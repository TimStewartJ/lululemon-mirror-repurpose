package dev.mirror.repurpose;

import android.content.ComponentName;
import android.content.ContentResolver;
import android.content.Context;
import android.content.pm.PackageManager;
import android.provider.Settings;
import android.util.Log;

import org.json.JSONException;
import org.json.JSONObject;

/**
 * How Mirror Home sets the backlight and keeps the Mirror's name on whatever
 * Android it runs on. On a factory system the Mirror's own service does both.
 * Without it (a rebuilt Android, a unit whose service does not answer, the
 * emulator), Android's brightness setting is used where Home may write it,
 * and otherwise Home's window holds the level while it is in front; the name
 * is then kept by Home alone.
 */
final class DeviceControls {
    private static final String TAG = "DeviceControls";

    /** The Mirror's factory service. */
    static final String VENDOR = "vendor";
    /** Android's own brightness setting. */
    static final String SYSTEM = "system";
    /** Mirror Home's window, while it is in front. */
    static final String WINDOW = "window";
    /** Kept by Mirror Home only. */
    static final String LOCAL = "local";

    static final String VENDOR_CONNECTED = "connected";
    static final String VENDOR_UNANSWERED = "unanswered";
    static final String VENDOR_ABSENT = "absent";

    private static final ComponentName VENDOR_RECEIVER = new ComponentName(
            "com.mirror.services",
            "com.mirror.services.ServiceLookupReceiver");

    private static volatile DeviceControls instance;

    private final Context context;
    private final MirrorBinderClient vendor;
    private volatile int windowLevel = -1;

    private DeviceControls(Context context) {
        this.context = context.getApplicationContext();
        vendor = MirrorBinderClient.getInstance(this.context);
    }

    static DeviceControls getInstance(Context context) {
        if (instance == null) {
            synchronized (DeviceControls.class) {
                if (instance == null) {
                    instance = new DeviceControls(context);
                }
            }
        }
        return instance;
    }

    /** The methods to try, best first, for what this unit offers now. */
    static String[] brightnessMethods(boolean vendorConnected, boolean canWriteSettings) {
        if (vendorConnected && canWriteSettings) {
            return new String[]{VENDOR, SYSTEM, WINDOW};
        }
        if (vendorConnected) {
            return new String[]{VENDOR, WINDOW};
        }
        if (canWriteSettings) {
            return new String[]{SYSTEM, WINDOW};
        }
        return new String[]{WINDOW};
    }

    /** Whether a level set this way stays on the panel without Home's window holding it. */
    static boolean storesLevel(String method) {
        return VENDOR.equals(method) || SYSTEM.equals(method);
    }

    String brightnessMethod() {
        return brightnessMethods(vendor.isConnected(), canWriteSettings())[0];
    }

    /**
     * Sets the backlight (1 to 255).
     *
     * @return the method that set it, or null for a level out of range
     */
    String setBrightness(int level) {
        if (level < 1 || level > 255) {
            return null;
        }
        for (String method : brightnessMethods(vendor.isConnected(), canWriteSettings())) {
            if (VENDOR.equals(method) && vendor.setBrightness(level)) {
                windowLevel = -1;
                return VENDOR;
            }
            if (SYSTEM.equals(method) && writeSystemBrightness(level)) {
                windowLevel = -1;
                return SYSTEM;
            }
            if (WINDOW.equals(method)) {
                windowLevel = level;
                return WINDOW;
            }
        }
        return null;
    }

    /** The level last set, as far as it can be read; null if none is known. */
    Integer getBrightness() {
        String method = brightnessMethod();
        if (VENDOR.equals(method)) {
            Integer level = vendor.getBrightness();
            if (level != null) {
                return level;
            }
        }
        if (windowLevel > 0) {
            return windowLevel;
        }
        if (SYSTEM.equals(method)) {
            try {
                return Settings.System.getInt(
                        context.getContentResolver(), Settings.System.SCREEN_BRIGHTNESS);
            } catch (Settings.SettingNotFoundException error) {
                return null;
            }
        }
        return null;
    }

    /** The level Home's window holds while no stored level does; -1 when none. */
    int windowLevel() {
        return windowLevel;
    }

    /**
     * Gives the Mirror a name: to the factory service where it answers.
     *
     * @return who keeps the name besides Mirror Home: {@link #VENDOR} or {@link #LOCAL}
     */
    String setName(String name) {
        return vendor.isConnected() && vendor.setName(name) ? VENDOR : LOCAL;
    }

    String vendorState() {
        if (vendor.isConnected()) {
            return VENDOR_CONNECTED;
        }
        try {
            context.getPackageManager().getReceiverInfo(VENDOR_RECEIVER, 0);
            return VENDOR_UNANSWERED;
        } catch (PackageManager.NameNotFoundException error) {
            return VENDOR_ABSENT;
        }
    }

    JSONObject capabilities() throws JSONException {
        return new JSONObject()
                .put("vendorService", vendorState())
                .put("brightness", brightnessMethod())
                .put("name", vendor.isConnected() ? VENDOR : LOCAL);
    }

    private boolean canWriteSettings() {
        try {
            return Settings.System.canWrite(context);
        } catch (RuntimeException error) {
            return false;
        }
    }

    private boolean writeSystemBrightness(int level) {
        ContentResolver resolver = context.getContentResolver();
        try {
            Settings.System.putInt(resolver, Settings.System.SCREEN_BRIGHTNESS_MODE,
                    Settings.System.SCREEN_BRIGHTNESS_MODE_MANUAL);
            return Settings.System.putInt(resolver, Settings.System.SCREEN_BRIGHTNESS, level);
        } catch (RuntimeException error) {
            Log.w(TAG, "Android's brightness setting could not be written", error);
            return false;
        }
    }
}
