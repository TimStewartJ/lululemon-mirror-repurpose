package dev.mirror.repurpose;

import android.app.Service;
import android.content.Intent;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import android.util.Log;

import java.io.IOException;

public final class ControlServerService extends Service {
    public static final int PORT = 8787;
    public static final String ACTION_CONFIGURATION_CHANGED =
            "dev.mirror.repurpose.CONFIGURATION_CHANGED";

    private static final String TAG = "ControlServerService";
    private static final int HOME_LAUNCH_ATTEMPTS = 4;
    private static final long HOME_LAUNCH_RETRY_MS = 2500L;

    private final Handler handler = new Handler(Looper.getMainLooper());
    private final Runnable ensureHomeActivity = new Runnable() {
        @Override
        public void run() {
            if (!HomeSelection.isMirrorHomeSelected(ControlServerService.this)) {
                return;
            }
            if (DashboardDiagnostics.activityCreated()) {
                return;
            }
            if (homeLaunchAttempts >= HOME_LAUNCH_ATTEMPTS) {
                Log.e(TAG, "Unable to relaunch HOME activity after package update");
                return;
            }
            homeLaunchAttempts++;
            try {
                Intent launch = getPackageManager().getLaunchIntentForPackage(getPackageName());
                if (launch == null) {
                    launch = new Intent(ControlServerService.this, MainActivity.class)
                            .setAction(Intent.ACTION_MAIN)
                            .addCategory(Intent.CATEGORY_LAUNCHER);
                }
                launch.addFlags(
                        Intent.FLAG_ACTIVITY_NEW_TASK
                                | Intent.FLAG_ACTIVITY_CLEAR_TOP
                                | Intent.FLAG_ACTIVITY_RESET_TASK_IF_NEEDED);
                DashboardDiagnostics.recordLaunchAttempt(homeLaunchAttempts, "requesting");
                startActivity(launch);
                DashboardDiagnostics.recordLaunchAttempt(homeLaunchAttempts, "requested");
            } catch (RuntimeException error) {
                DashboardDiagnostics.recordLaunchAttempt(
                        homeLaunchAttempts,
                        error.getClass().getSimpleName() + ": " + error.getMessage());
                Log.w(TAG, "HOME activity relaunch attempt failed", error);
            }
            handler.postDelayed(this, HOME_LAUNCH_RETRY_MS);
        }
    };

    private ControlServer server;
    private FCastServer fcastServer;
    private LocalDiscovery localDiscovery;
    private WifiDirectOnboarding wifiDirectOnboarding;
    private int homeLaunchAttempts;

    @Override
    public void onCreate() {
        super.onCreate();
        if (BuildConfig.OTA_HEALTH_FAILURE_TEST) {
            Log.e(TAG, "OTA health-failure test build is intentionally not starting services");
            stopSelf();
            return;
        }
        try {
            server = new ControlServer(this, PORT);
            server.start();
            Log.i(TAG, "Control server listening on port " + PORT);
            localDiscovery = new LocalDiscovery(this);
            localDiscovery.start(PORT);
            if (HomeSelection.isMirrorHomeSelected(this)) {
                handler.post(ensureHomeActivity);
            }
        } catch (IOException error) {
            Log.e(TAG, "Unable to start control server", error);
            stopSelf();
            return;
        }

        wifiDirectOnboarding = WifiDirectOnboarding.getInstance(this);
        wifiDirectOnboarding.startIfNeeded();

        try {
            fcastServer = new FCastServer(
                    this,
                    MediaPlaybackManager.getInstance(this),
                    new ConfigStore(this));
            fcastServer.start();
        } catch (IOException error) {
            fcastServer = null;
            Log.e(TAG, "Unable to start FCast receiver", error);
        }
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        return START_STICKY;
    }

    @Override
    public void onDestroy() {
        handler.removeCallbacks(ensureHomeActivity);
        if (server != null) {
            server.stop();
            server = null;
        }
        if (localDiscovery != null) {
            localDiscovery.stop();
            localDiscovery = null;
        }
        if (wifiDirectOnboarding != null) {
            wifiDirectOnboarding.stop();
            wifiDirectOnboarding = null;
        }
        if (fcastServer != null) {
            fcastServer.stop();
            fcastServer = null;
        }
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

}
