package dev.mirror.repurpose;

import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.content.ServiceConnection;
import android.content.pm.PackageManager;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import android.os.Parcel;
import android.os.SystemClock;
import android.util.Log;

import org.json.JSONException;
import org.json.JSONObject;

import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.ThreadFactory;
import java.util.concurrent.TimeUnit;

/**
 * Turns off Qualcomm smartphone display post-processing ({@code CABL} and {@code PP})
 * so the Mirror's MDP5 hardware pipeline maps framebuffer pixels linearly 1:1.
 *
 * <p>Generic CAF {@code msm8916_64} builds ship {@code /system/bin/mm-pp-daemon} with
 * {@code ro.qualcomm.cabl=2}. Because the Mirror's Toshiba {@code TC358775} DSI-to-LVDS
 * bridge reports {@code mipi dsi video panel} rather than HDMI, {@code CABLService}
 * starts Content-Adaptive Backlight ({@code cabl:on}) at boot. On a 97%-black mirror
 * dashboard, CABL hits its minimum-backlight floor and programs
 * {@code CABL_LUT_PIXEL_BOOSTING} with soft-clipping in the MDP hardware LUTs, stretching
 * 8-bit anti-aliased glyph edges into visible contour stripes and amplifying panel grain.
 *
 * <p>{@code com.qualcomm.cabl/.CABLService} and {@code com.qualcomm.display/.PPService}
 * run as {@code android.uid.system} with {@code /dev/socket/pps} access and expose
 * unguarded Binder interfaces. Both spawn their socket connector thread asynchronously
 * in {@code onBind} and initialize their local {@code On} flag to {@code false} (making
 * {@code stop} a no-op until {@code start} has synced the flag once the socket connects).
 * Sending {@code start} until the socket answers, then immediately {@code stop}, writes
 * {@code cabl:off} and {@code pp:off} to {@code mm-pp-daemon} and restores linear
 * {@code directmap_LUT} in hardware.
 */
final class QualcommDisplayPostProcDisabler {
    static final String STATE_PENDING = "pending";
    static final String STATE_DISABLED = "disabled";
    static final String STATE_ABSENT = "absent";
    static final String STATE_ERROR = "error";

    static final String CABL_PACKAGE = "com.qualcomm.cabl";
    static final String CABL_CLASS = "com.qualcomm.cabl.CABLService";
    static final String CABL_ACTION = "org.codeaurora.cabl.ICABLService";
    static final String CABL_DESCRIPTOR = "com.android.cabl.ICABLService";
    static final int TRANSACTION_CABL_CONTROL = IBinder.FIRST_CALL_TRANSACTION;
    static final int CABL_CONTROL_STOP = 0;
    static final int CABL_CONTROL_START = 1;

    static final String PP_PACKAGE = "com.qualcomm.display";
    static final String PP_CLASS = "com.qualcomm.display.PPService";
    static final String PP_ACTION = "com.android.display.IPPService";
    static final String PP_DESCRIPTOR = "com.android.display.IPPService";
    static final int TRANSACTION_PP_START = IBinder.FIRST_CALL_TRANSACTION;
    static final int TRANSACTION_PP_STOP = IBinder.FIRST_CALL_TRANSACTION + 1;
    static final int TRANSACTION_PP_STATUS = IBinder.FIRST_CALL_TRANSACTION + 3;

    private static final String TAG = "MirrorDisplayPP";
    private static final long BIND_TIMEOUT_MS = 3_000L;
    private static final long SOCKET_RETRY_DELAY_MS = 120L;
    private static final int SOCKET_MAX_ATTEMPTS = 22;
    private static final long BOOT_WINDOW_MS = 90_000L;
    private static final long POST_BOOT_REASSERT_DELAY_MS = 25_000L;

    interface Session {
        boolean transactBoolean(String descriptor, int code, Integer arg) throws Exception;

        void close();
    }

    interface Transport {
        boolean hasService(String packageName, String className);

        Session bind(String action, String packageName, String className, long timeoutMs)
                throws Exception;

        void sleepMs(long millis) throws InterruptedException;

        long elapsedRealtimeMs();

        long currentTimeMs();
    }

    private static QualcommDisplayPostProcDisabler instance;

    private final Transport transport;
    private final ExecutorService worker;
    private final Handler mainHandler;

    private String state = STATE_PENDING;
    private String cablState = STATE_PENDING;
    private String ppState = STATE_PENDING;
    private String detail = "";
    private int attempts;
    private long appliedAt;
    private boolean queued;

    QualcommDisplayPostProcDisabler(Transport transport, ExecutorService worker, Handler mainHandler) {
        this.transport = transport;
        this.worker = worker;
        this.mainHandler = mainHandler;
    }

    static synchronized QualcommDisplayPostProcDisabler getInstance(Context context) {
        if (instance == null) {
            final Context appContext = context.getApplicationContext();
            ExecutorService executor = Executors.newSingleThreadExecutor(new ThreadFactory() {
                @Override
                public Thread newThread(Runnable runnable) {
                    Thread thread = new Thread(runnable, "display-pp-guard");
                    thread.setDaemon(true);
                    return thread;
                }
            });
            instance = new QualcommDisplayPostProcDisabler(
                    new AndroidTransport(appContext),
                    executor,
                    new Handler(Looper.getMainLooper()));
        }
        return instance;
    }

    void start() {
        ensureLinearSoon();
        if (transport.elapsedRealtimeMs() < BOOT_WINDOW_MS && mainHandler != null) {
            // CABLReceiver also listens to BOOT_COMPLETED; re-assert once boot settles
            // in case CABLService started CABL a few seconds after Mirror Home launched.
            mainHandler.postDelayed(new Runnable() {
                @Override
                public void run() {
                    forceEnsureLinearSoon();
                }
            }, POST_BOOT_REASSERT_DELAY_MS);
        }
    }

    void ensureLinearSoon() {
        schedule(false);
    }

    void forceEnsureLinearSoon() {
        schedule(true);
    }

    private void schedule(final boolean force) {
        synchronized (this) {
            if (!force && settled()) {
                return;
            }
            if (!force && queued) {
                return;
            }
            queued = true;
        }
        worker.execute(new Runnable() {
            @Override
            public void run() {
                synchronized (QualcommDisplayPostProcDisabler.this) {
                    queued = false;
                }
                ensureLinearNow(force);
            }
        });
    }

    private boolean settled() {
        return STATE_DISABLED.equals(state) || STATE_ABSENT.equals(state);
    }

    /** Synchronous execution entry point, exposed package-privately for deterministic unit tests. */
    void ensureLinearNow(boolean force) {
        synchronized (this) {
            if (!force && settled()) {
                return;
            }
            attempts++;
        }

        boolean hasCabl = transport.hasService(CABL_PACKAGE, CABL_CLASS);
        boolean hasPp = transport.hasService(PP_PACKAGE, PP_CLASS);
        if (!hasCabl && !hasPp) {
            synchronized (this) {
                cablState = STATE_ABSENT;
                ppState = STATE_ABSENT;
                state = STATE_ABSENT;
                detail = "";
            }
            return;
        }

        StringBuilder notes = new StringBuilder();
        // Stop PP before CABL: PPService.startPP() re-arms cabl:on inside mm-pp-daemon
        // while syncing its local state flag, so CABL must be stopped second.
        String nextPp = hasPp ? disablePp(notes) : STATE_ABSENT;
        String nextCabl = hasCabl ? disableCabl(notes) : STATE_ABSENT;

        boolean ok = !STATE_ERROR.equals(nextCabl) && !STATE_ERROR.equals(nextPp);
        String nextState = ok ? STATE_DISABLED : STATE_ERROR;
        String nextDetail = notes.toString();
        long now = transport.currentTimeMs();

        synchronized (this) {
            cablState = nextCabl;
            ppState = nextPp;
            state = nextState;
            detail = nextDetail;
            if (ok) {
                appliedAt = now;
            }
        }

        if (ok) {
            Log.i(TAG, "Linear MDP display LUT active (cabl=" + nextCabl + ", pp=" + nextPp + ")");
        } else {
            Log.w(TAG, "Unable to fully disable Qualcomm display post-proc (cabl="
                    + nextCabl + ", pp=" + nextPp + "): " + nextDetail);
        }
    }

    private String disableCabl(StringBuilder notes) {
        Session session = null;
        try {
            session = transport.bind(CABL_ACTION, CABL_PACKAGE, CABL_CLASS, BIND_TIMEOUT_MS);
            if (session == null) {
                appendNote(notes, "cabl: bind unavailable");
                return STATE_ERROR;
            }
            boolean synced = false;
            for (int attempt = 0; attempt < SOCKET_MAX_ATTEMPTS; attempt++) {
                if (session.transactBoolean(
                        CABL_DESCRIPTOR, TRANSACTION_CABL_CONTROL, CABL_CONTROL_START)) {
                    synced = true;
                    break;
                }
                transport.sleepMs(SOCKET_RETRY_DELAY_MS);
            }
            if (!synced) {
                appendNote(notes, "cabl: daemon socket timeout");
                return STATE_ERROR;
            }
            if (!session.transactBoolean(
                    CABL_DESCRIPTOR, TRANSACTION_CABL_CONTROL, CABL_CONTROL_STOP)) {
                appendNote(notes, "cabl: stop rejected");
                return STATE_ERROR;
            }
            return STATE_DISABLED;
        } catch (InterruptedException interrupted) {
            Thread.currentThread().interrupt();
            appendNote(notes, "cabl: interrupted");
            return STATE_ERROR;
        } catch (Exception error) {
            appendNote(notes, "cabl: " + error.getClass().getSimpleName());
            return STATE_ERROR;
        } finally {
            if (session != null) {
                session.close();
            }
        }
    }

    private String disablePp(StringBuilder notes) {
        Session session = null;
        try {
            session = transport.bind(PP_ACTION, PP_PACKAGE, PP_CLASS, BIND_TIMEOUT_MS);
            if (session == null) {
                appendNote(notes, "pp: bind unavailable");
                return STATE_ERROR;
            }
            boolean synced = false;
            for (int attempt = 0; attempt < SOCKET_MAX_ATTEMPTS; attempt++) {
                if (session.transactBoolean(PP_DESCRIPTOR, TRANSACTION_PP_START, null)) {
                    synced = true;
                    break;
                }
                transport.sleepMs(SOCKET_RETRY_DELAY_MS);
            }
            if (!synced) {
                appendNote(notes, "pp: daemon socket timeout");
                return STATE_ERROR;
            }
            if (!session.transactBoolean(PP_DESCRIPTOR, TRANSACTION_PP_STOP, null)) {
                appendNote(notes, "pp: stop rejected");
                return STATE_ERROR;
            }
            if (session.transactBoolean(PP_DESCRIPTOR, TRANSACTION_PP_STATUS, null)) {
                appendNote(notes, "pp: still running after stop");
                return STATE_ERROR;
            }
            return STATE_DISABLED;
        } catch (InterruptedException interrupted) {
            Thread.currentThread().interrupt();
            appendNote(notes, "pp: interrupted");
            return STATE_ERROR;
        } catch (Exception error) {
            appendNote(notes, "pp: " + error.getClass().getSimpleName());
            return STATE_ERROR;
        } finally {
            if (session != null) {
                session.close();
            }
        }
    }

    private static void appendNote(StringBuilder notes, String message) {
        if (notes.length() > 0) {
            notes.append("; ");
        }
        notes.append(message);
    }

    synchronized JSONObject snapshot() throws JSONException {
        return new JSONObject()
                .put("state", state)
                .put("cabl", cablState)
                .put("pp", ppState)
                .put("attempts", attempts)
                .put("appliedAt", appliedAt == 0L ? JSONObject.NULL : appliedAt)
                .put("detail", detail);
    }

    private static final class AndroidTransport implements Transport {
        private final Context context;

        AndroidTransport(Context context) {
            this.context = context;
        }

        @Override
        public boolean hasService(String packageName, String className) {
            Intent intent = new Intent().setClassName(packageName, className);
            PackageManager pm = context.getPackageManager();
            return pm != null && pm.resolveService(intent, 0) != null;
        }

        @Override
        public Session bind(String action, String packageName, String className, long timeoutMs)
                throws Exception {
            final CountDownLatch connected = new CountDownLatch(1);
            final IBinder[] holder = new IBinder[1];
            final ServiceConnection connection = new ServiceConnection() {
                @Override
                public void onServiceConnected(ComponentName name, IBinder service) {
                    holder[0] = service;
                    connected.countDown();
                }

                @Override
                public void onServiceDisconnected(ComponentName name) {
                }
            };
            Intent intent = new Intent(action).setClassName(packageName, className);
            boolean bound = false;
            try {
                bound = context.bindService(intent, connection, Context.BIND_AUTO_CREATE);
                if (!bound) {
                    safeUnbind(connection);
                    return null;
                }
                if (!connected.await(timeoutMs, TimeUnit.MILLISECONDS) || holder[0] == null) {
                    safeUnbind(connection);
                    return null;
                }
                final IBinder binder = holder[0];
                return new Session() {
                    @Override
                    public boolean transactBoolean(String descriptor, int code, Integer arg)
                            throws Exception {
                        Parcel data = Parcel.obtain();
                        Parcel reply = Parcel.obtain();
                        try {
                            data.writeInterfaceToken(descriptor);
                            if (arg != null) {
                                data.writeInt(arg);
                            }
                            if (!binder.transact(code, data, reply, 0)) {
                                return false;
                            }
                            reply.readException();
                            return reply.readInt() != 0;
                        } finally {
                            reply.recycle();
                            data.recycle();
                        }
                    }

                    @Override
                    public void close() {
                        safeUnbind(connection);
                    }
                };
            } catch (Exception error) {
                if (bound) {
                    safeUnbind(connection);
                }
                throw error;
            }
        }

        private void safeUnbind(ServiceConnection connection) {
            try {
                context.unbindService(connection);
            } catch (RuntimeException ignored) {
            }
        }

        @Override
        public void sleepMs(long millis) throws InterruptedException {
            Thread.sleep(millis);
        }

        @Override
        public long elapsedRealtimeMs() {
            return SystemClock.elapsedRealtime();
        }

        @Override
        public long currentTimeMs() {
            return System.currentTimeMillis();
        }
    }
}
