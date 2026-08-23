package co.mirror.datacap;

import android.app.Service;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.ComponentName;
import android.content.pm.PackageManager;
import android.content.pm.ResolveInfo;
import android.os.Binder;
import android.os.IBinder;
import android.os.Parcel;
import android.provider.Settings;
import android.util.Log;

import org.json.JSONException;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.List;
import java.lang.reflect.Method;

public final class DataCapIntentService extends Service {
    static final String DESCRIPTOR = "dev.mirror.repurpose.ISystemHelper";
    static final int TRANSACTION_GET_CAPABILITIES = 1;
    static final int TRANSACTION_PREPARE_KIOSK = 2;
    static final int TRANSACTION_SET_STAY_AWAKE = 3;
    static final int TRANSACTION_SET_SCREEN_OFF_TIMEOUT = 4;
    static final int TRANSACTION_SET_MIRROR_HOME = 5;
    static final int TRANSACTION_RESTORE_STOCK_HOME = 6;

    private static final ComponentName MIRROR_HOME = new ComponentName(
            "dev.mirror.repurpose",
            "dev.mirror.repurpose.MainActivity");
    private static final ComponentName STOCK_HOME = new ComponentName(
            "com.mirror.launcher",
            "com.mirror.launcher.SplashActivity");

    private final IBinder helperBinder = new HelperBinder();
    private TrustedCaller trustedCaller;

    @Override
    public void onCreate() {
        super.onCreate();
        trustedCaller = new TrustedCaller(this);
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        return START_STICKY;
    }

    @Override
    public IBinder onBind(Intent intent) {
        return helperBinder;
    }

    private final class HelperBinder extends Binder {
        HelperBinder() {
            attachInterface(null, DESCRIPTOR);
        }

        @Override
        protected boolean onTransact(
                int code,
                Parcel request,
                Parcel response,
                int flags) {
            if (code == INTERFACE_TRANSACTION) {
                response.writeString(DESCRIPTOR);
                return true;
            }

            request.enforceInterface(DESCRIPTOR);
            if (!trustedCaller.isTrusted()) {
                Log.w("MirrorSystemHelper", "Rejected caller UID " + Binder.getCallingUid());
                response.writeException(new SecurityException("Caller is not trusted"));
                return true;
            }

            long identity = Binder.clearCallingIdentity();
            try {
                switch (code) {
                    case TRANSACTION_GET_CAPABILITIES:
                        response.writeNoException();
                        response.writeString(capabilities().toString());
                        return true;
                    case TRANSACTION_PREPARE_KIOSK:
                        response.writeNoException();
                        response.writeInt(prepareKiosk() ? 1 : 0);
                        return true;
                    case TRANSACTION_SET_STAY_AWAKE:
                        response.writeNoException();
                        response.writeInt(setStayAwake(request.readInt() != 0) ? 1 : 0);
                        return true;
                    case TRANSACTION_SET_SCREEN_OFF_TIMEOUT:
                        response.writeNoException();
                        response.writeInt(setScreenOffTimeout(request.readLong()) ? 1 : 0);
                        return true;
                    case TRANSACTION_SET_MIRROR_HOME:
                        response.writeNoException();
                        response.writeInt(setPreferredHome(MIRROR_HOME) ? 1 : 0);
                        return true;
                    case TRANSACTION_RESTORE_STOCK_HOME:
                        response.writeNoException();
                        response.writeInt(setPreferredHome(STOCK_HOME) ? 1 : 0);
                        return true;
                    default:
                        return super.onTransact(code, request, response, flags);
                }
            } catch (Exception error) {
                response.writeException(error);
                return true;
            } finally {
                Binder.restoreCallingIdentity(identity);
            }
        }
    }

    private JSONObject capabilities() throws JSONException {
        JSONObject result = new JSONObject();
        result.put("apiVersion", 1);
        result.put("uid", android.os.Process.myUid());
        result.put("writeSecureSettings", hasPermission("android.permission.WRITE_SECURE_SETTINGS"));
        result.put("reboot", hasPermission("android.permission.REBOOT"));
        result.put("recovery", hasPermission("android.permission.RECOVERY"));
        result.put("installPackages", hasPermission("android.permission.INSTALL_PACKAGES"));
        return result;
    }

    private boolean prepareKiosk() {
        boolean immersive = Settings.Secure.putString(
                getContentResolver(),
                "immersive_mode_confirmations",
                "confirmed");
        boolean awake = setStayAwake(true);
        boolean timeout = setScreenOffTimeout(Integer.MAX_VALUE);
        return immersive && awake && timeout;
    }

    private boolean setStayAwake(boolean enabled) {
        return Settings.Global.putInt(
                getContentResolver(),
                Settings.Global.STAY_ON_WHILE_PLUGGED_IN,
                enabled ? 7 : 0);
    }

    private boolean setScreenOffTimeout(long timeoutMillis) {
        long bounded = Math.max(15_000L, Math.min(timeoutMillis, Integer.MAX_VALUE));
        return Settings.System.putInt(
                getContentResolver(),
                Settings.System.SCREEN_OFF_TIMEOUT,
                (int) bounded);
    }

    private boolean hasPermission(String permission) {
        return checkSelfPermission(permission) == PackageManager.PERMISSION_GRANTED;
    }

    private boolean setPreferredHome(ComponentName target) {
        PackageManager packageManager = getPackageManager();
        Intent homeIntent = new Intent(Intent.ACTION_MAIN);
        homeIntent.addCategory(Intent.CATEGORY_HOME);
        List<ResolveInfo> candidates =
                packageManager.queryIntentActivities(homeIntent, PackageManager.MATCH_DEFAULT_ONLY);
        ArrayList<ComponentName> components = new ArrayList<>();
        boolean targetFound = false;
        for (ResolveInfo candidate : candidates) {
            ComponentName component = new ComponentName(
                    candidate.activityInfo.packageName,
                    candidate.activityInfo.name);
            components.add(component);
            if (target.equals(component)) {
                targetFound = true;
            }
        }
        if (!targetFound) {
            return false;
        }

        packageManager.clearPackagePreferredActivities(MIRROR_HOME.getPackageName());
        packageManager.clearPackagePreferredActivities(STOCK_HOME.getPackageName());

        IntentFilter filter = new IntentFilter(Intent.ACTION_MAIN);
        filter.addCategory(Intent.CATEGORY_HOME);
        filter.addCategory(Intent.CATEGORY_DEFAULT);
        ComponentName[] componentSet = components.toArray(new ComponentName[0]);
        try {
            Method replacePreferredActivity = packageManager.getClass().getMethod(
                    "replacePreferredActivity",
                    IntentFilter.class,
                    int.class,
                    ComponentName[].class,
                    ComponentName.class);
            replacePreferredActivity.invoke(
                    packageManager,
                    filter,
                    IntentFilter.MATCH_CATEGORY_EMPTY,
                    componentSet,
                    target);
        } catch (ReflectiveOperationException unavailable) {
            packageManager.addPreferredActivity(
                    filter,
                    IntentFilter.MATCH_CATEGORY_EMPTY,
                    componentSet,
                    target);
        }

        ResolveInfo resolved =
                packageManager.resolveActivity(homeIntent, PackageManager.MATCH_DEFAULT_ONLY);
        return resolved != null
                && target.getPackageName().equals(resolved.activityInfo.packageName)
                && target.getClassName().equals(resolved.activityInfo.name);
    }
}
