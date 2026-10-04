package dev.mirror.repurpose;

import android.content.BroadcastReceiver;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.ServiceConnection;
import android.content.pm.PackageManager;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import android.util.Log;

import org.json.JSONException;
import org.json.JSONObject;

import dev.mirror.repurpose.health.HoldLink;

/**
 * Keeps the OTA supervisor running while the dashboard is in front.
 *
 * <p>Left to itself the supervisor is a background service like any other,
 * and among the first that the kernel ends when memory runs short: on one
 * Mirror twelve times in five minutes, so that for three hours nothing
 * could be installed or rolled back. Android ranks a process by who needs
 * it. While Mirror Home, which is on the display, holds a connection to the
 * supervisor, the supervisor counts as needed by what the owner is looking
 * at, as the recogniser of voice commands does, and that one stayed.
 *
 * <p>The supervisor does not depend on this. It starts by itself with
 * Android and carries on while Mirror Home is being replaced or does not
 * start at all; it is then ranked as before. A supervisor older than 1.3.0
 * has no such connection to offer and is left as it is.
 */
final class SupervisorHold {
    static final String STATE_ABSENT = "absent";
    static final String STATE_UNSUPPORTED = "unsupported";
    static final String STATE_REFUSED = "refused";
    static final String STATE_WAITING = "waiting";
    static final String STATE_HELD = "held";

    /** A supervisor that was ended is started again by Android within this, or the hold starts over. */
    static final long RECONNECT_MS = 20_000L;

    private static final String TAG = "SupervisorHold";
    private static final String SERVICE = SupervisorProbe.PACKAGE_NAME + ".HoldService";

    private static SupervisorHold instance;

    private final Context context;
    private final Handler handler = new Handler(Looper.getMainLooper());
    private final Runnable look = new Runnable() {
        @Override
        public void run() {
            check();
        }
    };
    private final ServiceConnection connection = new ServiceConnection() {
        @Override
        public void onServiceConnected(ComponentName name, IBinder service) {
            synchronized (SupervisorHold.this) {
                link = service;
                heldSince = System.currentTimeMillis();
                state = STATE_HELD;
                detail = "";
            }
            Log.i(TAG, "Holding the OTA supervisor");
        }

        @Override
        public void onServiceDisconnected(ComponentName name) {
            synchronized (SupervisorHold.this) {
                link = null;
                heldSince = 0L;
                losses++;
                state = STATE_WAITING;
                // So that a supervisor that was replaced is not waited for as one that was ended.
                boundAt = System.currentTimeMillis();
            }
            Log.w(TAG, "The OTA supervisor went away");
            handler.removeCallbacks(look);
            handler.postDelayed(look, RECONNECT_MS + 1_000L);
        }
    };

    private boolean bound;
    private IBinder link;
    private long boundAt;
    private long heldSince;
    private int binds;
    private int losses;
    private String state = STATE_ABSENT;
    private String detail = "";

    private SupervisorHold(Context context) {
        this.context = context.getApplicationContext();
    }

    static synchronized SupervisorHold getInstance(Context context) {
        if (instance == null) {
            instance = new SupervisorHold(context);
        }
        return instance;
    }

    /** Takes hold of the supervisor, now and whenever it is installed or replaced. */
    void start() {
        IntentFilter packages = new IntentFilter();
        packages.addAction(Intent.ACTION_PACKAGE_ADDED);
        packages.addAction(Intent.ACTION_PACKAGE_REPLACED);
        packages.addAction(Intent.ACTION_PACKAGE_REMOVED);
        packages.addDataScheme("package");
        packages.addDataSchemeSpecificPart(SupervisorProbe.PACKAGE_NAME, 0);
        context.registerReceiver(new BroadcastReceiver() {
            @Override
            public void onReceive(Context context, Intent intent) {
                synchronized (SupervisorHold.this) {
                    // What Android held of the old supervisor is gone with it.
                    release();
                }
                handler.removeCallbacks(look);
                handler.postDelayed(look, 2_000L);
            }
        }, packages);
        check();
    }

    /** Looks whether the supervisor is held and takes hold of it if not. */
    synchronized void check() {
        long now = System.currentTimeMillis();
        if (!installed()) {
            release();
            state = STATE_ABSENT;
            detail = "";
            return;
        }
        if (bound) {
            if (link != null && link.isBinderAlive()) {
                state = STATE_HELD;
                return;
            }
            if (now - boundAt < RECONNECT_MS) {
                state = STATE_WAITING;
                return;
            }
            release();
        }
        Intent service = new Intent().setComponent(
                new ComponentName(SupervisorProbe.PACKAGE_NAME, SERVICE));
        try {
            binds++;
            boundAt = now;
            if (context.bindService(service, connection, Context.BIND_AUTO_CREATE)) {
                bound = true;
                state = STATE_WAITING;
                detail = "";
                return;
            }
            // Android asks for this even when it found nothing to bind to.
            unbind();
            state = STATE_UNSUPPORTED;
            detail = "This OTA supervisor is older than 1.3.0 and cannot be held";
        } catch (SecurityException refused) {
            unbind();
            state = STATE_REFUSED;
            detail = "The OTA supervisor only lets a Mirror Home signed with its own key hold it";
        }
    }

    private void release() {
        if (bound) {
            unbind();
        }
        bound = false;
        link = null;
        heldSince = 0L;
    }

    private void unbind() {
        try {
            context.unbindService(connection);
        } catch (IllegalArgumentException notBound) {
            // Android had already let go.
        }
    }

    private boolean installed() {
        try {
            context.getPackageManager().getPackageInfo(SupervisorProbe.PACKAGE_NAME, 0);
            return true;
        } catch (PackageManager.NameNotFoundException absent) {
            return false;
        }
    }

    /**
     * Where the hold stands and, while it holds, the supervisor's process
     * with how readily the kernel would end it: 58 as for the recogniser,
     * where an unheld supervisor has 294 or more.
     */
    JSONObject snapshot() throws JSONException {
        IBinder asked;
        JSONObject result;
        synchronized (this) {
            asked = STATE_HELD.equals(state) ? link : null;
            result = new JSONObject()
                    .put("state", state)
                    .put("detail", detail)
                    .put("since", heldSince == 0L ? JSONObject.NULL : heldSince)
                    .put("binds", binds)
                    .put("losses", losses);
        }
        // Outside the lock: the supervisor answers this, in its own time.
        int[] process = asked == null ? null : HoldLink.describe(asked);
        return result
                .put("pid", process == null ? JSONObject.NULL : process[0])
                .put("oomScoreAdj", process == null || process[1] == HoldLink.UNKNOWN
                        ? JSONObject.NULL
                        : (Object) process[1]);
    }
}
