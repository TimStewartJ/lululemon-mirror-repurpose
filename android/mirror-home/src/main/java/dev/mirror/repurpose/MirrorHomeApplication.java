package dev.mirror.repurpose;

import android.app.Application;
import android.content.Intent;
import android.os.Handler;
import android.os.Looper;

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
        wifiProvisioner = new WifiProvisioner(this);
        ensureWifiConnection();
        MirrorBinderClient.getInstance(this).connect();
        SystemHelperClient.getInstance(this).connect();
        MediaPlaybackManager.getInstance(this);
        AutomationManager.getInstance(this);
        startService(new Intent(this, ControlServerService.class));
        WatchdogReceiver.schedule(this);
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
