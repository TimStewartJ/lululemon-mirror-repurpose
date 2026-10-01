package dev.mirror.repurpose;

import android.app.ActivityManager;
import android.app.KeyguardManager;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.res.Configuration;
import android.os.BatteryManager;
import android.os.PowerManager;
import android.os.SystemClock;
import android.provider.Settings;
import android.util.Log;

import org.json.JSONException;
import org.json.JSONObject;

import java.util.List;

/**
 * Keeps the dashboard on the glass. A Mirror has no touchscreen or keys, so
 * nobody can dismiss a screen that Android puts in front of Mirror Home, or
 * turn the display back on once Android has put it to sleep. The first has
 * happened after an update: while Mirror Home is being replaced Android may
 * start the other HOME app, the factory launcher, whose screen then ends up
 * on top. This notices either state and puts the dashboard back.
 *
 * <p>It acts only where nobody could do so by hand: Mirror Home is the HOME
 * app, the device has no input devices, and no computer is using its USB
 * port. A phone's owner, or someone working over ADB, is left alone.
 */
final class ForegroundKeeper {
    static final long CHECK_INTERVAL_MS = 5_000L;
    /** How long another screen may stay in front before the dashboard returns. */
    static final long COVERED_GRACE_MS = 10_000L;
    static final long WAKE_RETRY_MS = 10_000L;
    static final long RELAUNCH_RETRY_MS = 30_000L;

    private static final String TAG = "ForegroundKeeper";
    private static final long WAKE_HOLD_MS = 3_000L;
    private static final long NEVER = Long.MAX_VALUE;
    private static final int MAX_FRONT_LENGTH = 240;
    // UsbManager.ACTION_USB_STATE and its extras, which the SDK does not publish.
    private static final String USB_STATE = "android.hardware.usb.action.USB_STATE";
    private static final String USB_CONNECTED = "connected";
    private static final String USB_CONFIGURED = "configured";

    enum Action { NONE, WAKE, RELAUNCH }

    private static int wakeUps;
    private static int relaunches;
    private static long lastWakeElapsed = -1L;
    private static long lastRelaunchElapsed = -1L;
    private static long lastAt;
    private static String lastReason = "";
    private static String lastFront = "";

    private ForegroundKeeper() {
    }

    /**
     * What to do about a dashboard that is not in front.
     *
     * @param selectedHome whether Mirror Home is the HOME app Android would start
     * @param wasCreated whether this process has shown the dashboard; until it
     *         has, starting it is left to Android and to the service's start
     * @param attended whether someone could be operating the device: it has an
     *         input device, or a computer is using its USB port
     */
    static Action decide(
            boolean selectedHome,
            boolean wasCreated,
            boolean resumed,
            boolean interactive,
            boolean attended,
            long coveredForMs,
            long sinceWakeMs,
            long sinceRelaunchMs) {
        if (!selectedHome || !wasCreated || resumed || attended) {
            return Action.NONE;
        }
        if (!interactive) {
            // Nothing else will ever turn the display back on.
            return sinceWakeMs >= WAKE_RETRY_MS ? Action.WAKE : Action.NONE;
        }
        if (coveredForMs < COVERED_GRACE_MS || sinceRelaunchMs < RELAUNCH_RETRY_MS) {
            return Action.NONE;
        }
        return Action.RELAUNCH;
    }

    /**
     * Looks once and acts if needed. Call on the main thread. The service
     * looks every few seconds; the watchdog alarm looks too, because it still
     * fires when a sleeping Android has stopped the processor.
     */
    static synchronized void check(Context context) {
        try {
            restoreIfNeeded(context);
        } catch (RuntimeException error) {
            // A system service that is restarting; the next look will do.
            Log.w(TAG, "Unable to check whether the dashboard is in front", error);
        }
    }

    private static void restoreIfNeeded(Context context) {
        if (!ActivityDiagnostics.wasCreated() || ActivityDiagnostics.isResumed()) {
            return;
        }
        long elapsed = SystemClock.elapsedRealtime();
        PowerManager power = (PowerManager) context.getSystemService(Context.POWER_SERVICE);
        boolean interactive = power == null || power.isInteractive();
        Action action = decide(
                HomeSelection.isMirrorHomeSelected(context),
                true,
                false,
                interactive,
                attended(context),
                ActivityDiagnostics.coveredForMs(elapsed),
                since(lastWakeElapsed, elapsed),
                since(lastRelaunchElapsed, elapsed));
        if (action == Action.NONE) {
            return;
        }
        lastFront = front(context);
        lastAt = System.currentTimeMillis();
        if (action == Action.WAKE) {
            lastReason = "asleep";
            lastWakeElapsed = elapsed;
            wakeUps++;
            wake(power);
        } else {
            lastReason = "covered";
            lastRelaunchElapsed = elapsed;
            relaunches++;
        }
        Log.w(TAG, "Dashboard was " + lastReason + "; bringing it back. In front: " + lastFront);
        try {
            context.startActivity(dashboardIntent(context));
        } catch (RuntimeException error) {
            lastReason = lastReason + ", " + error.getClass().getSimpleName();
            Log.w(TAG, "Unable to bring the dashboard to the front", error);
        }
    }

    /**
     * Brings the one dashboard to the front wherever its task is. An explicit
     * component matters: Android 6 looks for an existing task of a HOME-type
     * start only among HOME tasks, and would otherwise create a second
     * dashboard beside one that Mirror Home started itself after an update.
     */
    static Intent dashboardIntent(Context context) {
        Intent launch = context.getPackageManager().getLaunchIntentForPackage(context.getPackageName());
        if (launch == null) {
            launch = new Intent(context, MainActivity.class)
                    .setAction(Intent.ACTION_MAIN)
                    .addCategory(Intent.CATEGORY_LAUNCHER);
        }
        return launch.addFlags(
                Intent.FLAG_ACTIVITY_NEW_TASK
                        | Intent.FLAG_ACTIVITY_CLEAR_TOP
                        | Intent.FLAG_ACTIVITY_RESET_TASK_IF_NEEDED);
    }

    static synchronized JSONObject snapshot(Context context) throws JSONException {
        return new JSONObject()
                .put("attended", attended(context))
                .put("wakeUps", wakeUps)
                .put("relaunches", relaunches)
                .put("lastAt", lastAt == 0L ? JSONObject.NULL : lastAt)
                .put("lastReason", lastReason)
                .put("lastFront", lastFront);
    }

    /** What Android's power management would do with an unattended display. */
    static JSONObject power(Context context) throws JSONException {
        PowerManager power = (PowerManager) context.getSystemService(Context.POWER_SERVICE);
        KeyguardManager keyguard =
                (KeyguardManager) context.getSystemService(Context.KEYGUARD_SERVICE);
        Intent battery = sticky(context, Intent.ACTION_BATTERY_CHANGED);
        Intent usb = sticky(context, USB_STATE);
        int timeout = Settings.System.getInt(
                context.getContentResolver(), Settings.System.SCREEN_OFF_TIMEOUT, -1);
        return new JSONObject()
                .put("interactive", power == null || power.isInteractive())
                .put("keyguardLocked", keyguard != null && keyguard.isKeyguardLocked())
                .put("screenOffTimeoutSeconds", timeout < 0 ? JSONObject.NULL : timeout / 1000)
                .put("stayOnWhilePluggedIn", Settings.Global.getInt(
                        context.getContentResolver(),
                        Settings.Global.STAY_ON_WHILE_PLUGGED_IN,
                        0))
                .put("plugged", battery == null
                        ? JSONObject.NULL
                        : battery.getIntExtra(BatteryManager.EXTRA_PLUGGED, 0))
                .put("usbConnected", usb != null && usb.getBooleanExtra(USB_CONNECTED, false))
                .put("usbConfigured", usb != null && usb.getBooleanExtra(USB_CONFIGURED, false));
    }

    /**
     * The tasks Android will tell an ordinary app about, front first: its own
     * and HOME apps'. Enough to see that another launcher took the display.
     */
    static String front(Context context) {
        try {
            ActivityManager manager =
                    (ActivityManager) context.getSystemService(Context.ACTIVITY_SERVICE);
            if (manager == null) {
                return "";
            }
            @SuppressWarnings("deprecation")
            List<ActivityManager.RunningTaskInfo> tasks = manager.getRunningTasks(3);
            StringBuilder names = new StringBuilder();
            for (ActivityManager.RunningTaskInfo task : tasks) {
                ComponentName top = task.topActivity;
                if (top != null) {
                    names.append(names.length() == 0 ? "" : ", ")
                            .append(top.flattenToShortString());
                }
            }
            return names.length() <= MAX_FRONT_LENGTH
                    ? names.toString()
                    : names.substring(0, MAX_FRONT_LENGTH);
        } catch (RuntimeException unavailable) {
            return "";
        }
    }

    private static boolean attended(Context context) {
        return hasInputDevices(context.getResources().getConfiguration())
                || usbHostPresent(context);
    }

    /** Whether Android sees anything a person could operate this device with. */
    static boolean hasInputDevices(Configuration configuration) {
        return hasInputDevices(
                configuration.touchscreen, configuration.keyboard, configuration.navigation);
    }

    /** As above, from the three fields of a {@link Configuration}; unknown counts as present. */
    static boolean hasInputDevices(int touchscreen, int keyboard, int navigation) {
        return touchscreen != Configuration.TOUCHSCREEN_NOTOUCH
                || keyboard != Configuration.KEYBOARD_NOKEYS
                || navigation != Configuration.NAVIGATION_NONAV;
    }

    /**
     * Whether a computer is using the USB port, as for ADB or scrcpy: the
     * port is connected and that host has configured it. A charger, or an
     * emulator's port that nothing is plugged into, is only "connected".
     */
    private static boolean usbHostPresent(Context context) {
        Intent state = sticky(context, USB_STATE);
        return state != null
                && state.getBooleanExtra(USB_CONNECTED, false)
                && state.getBooleanExtra(USB_CONFIGURED, false);
    }

    private static Intent sticky(Context context, String action) {
        try {
            return context.getApplicationContext().registerReceiver(null, new IntentFilter(action));
        } catch (RuntimeException unavailable) {
            return null;
        }
    }

    @SuppressWarnings("deprecation")
    private static void wake(PowerManager power) {
        if (power == null) {
            return;
        }
        try {
            // The only way an ordinary app can turn the display on from a service.
            power.newWakeLock(
                    PowerManager.SCREEN_BRIGHT_WAKE_LOCK
                            | PowerManager.ACQUIRE_CAUSES_WAKEUP
                            | PowerManager.ON_AFTER_RELEASE,
                    "MirrorHome:WakeDisplay").acquire(WAKE_HOLD_MS);
        } catch (RuntimeException error) {
            Log.w(TAG, "Unable to wake the display", error);
        }
    }

    private static long since(long thenElapsed, long nowElapsed) {
        return thenElapsed < 0 ? NEVER : Math.max(0L, nowElapsed - thenElapsed);
    }
}
