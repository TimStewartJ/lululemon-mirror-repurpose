package dev.mirror.repurpose;

import android.app.Application;
import android.content.Intent;
import android.os.Handler;
import android.os.Looper;

import dev.mirror.repurpose.health.ProcessHealth;

public final class MirrorHomeApplication extends Application {
    private static final int WIFI_RETRY_COUNT = 24;
    private static final long WIFI_RETRY_DELAY_MS = 5000;

    private final Handler handler = new Handler(Looper.getMainLooper());
    private WifiProvisioner wifiProvisioner;
    private int wifiAttempts;

    @Override
    public void onCreate() {
        super.onCreate();
        System.setProperty("java.io.tmpdir", getCacheDir().getAbsolutePath());
        if (VoiceService.isVoiceProcess()) {
            // The recogniser's process runs none of Mirror Home's own services,
            // and its runs are not the dashboard's.
            return;
        }
        ProcessHealth.start(this, BuildConfig.VERSION_NAME, BuildConfig.VERSION_CODE);
        if (BuildConfig.OTA_HEALTH_FAILURE_TEST) {
            return;
        }
        wifiProvisioner = new WifiProvisioner(this);
        ensureWifiConnection();
        MirrorBinderClient.getInstance(this).connect();
        SystemHelperClient.getInstance(this).connect();
        MediaPlaybackManager.getInstance(this);
        AutomationManager.getInstance(this);
        WeatherProvider.getInstance(this);
        VoiceManager.getInstance(this).start();
        startService(new Intent(this, ControlServerService.class));
        WatchdogReceiver.schedule(this);
    }

    @Override
    public void onTrimMemory(int level) {
        super.onTrimMemory(level);
        ProcessHealth recorder = ProcessHealth.get();
        if (recorder != null) {
            recorder.recordTrimMemory(level);
        }
    }

    private void ensureWifiConnection() {
        if (wifiProvisioner.isConnected() || wifiAttempts >= WIFI_RETRY_COUNT) {
            return;
        }
        wifiAttempts++;
        wifiProvisioner.ensureConnection();
        handler.postDelayed(new Runnable() {
            @Override
            public void run() {
                ensureWifiConnection();
            }
        }, WIFI_RETRY_DELAY_MS);
    }
}
