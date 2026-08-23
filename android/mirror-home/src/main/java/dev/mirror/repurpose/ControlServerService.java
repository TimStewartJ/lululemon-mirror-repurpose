package dev.mirror.repurpose;

import android.app.Service;
import android.content.Intent;
import android.os.IBinder;
import android.util.Log;

import java.io.IOException;

public final class ControlServerService extends Service {
    public static final int PORT = 8787;
    public static final String ACTION_CONFIGURATION_CHANGED =
            "dev.mirror.repurpose.CONFIGURATION_CHANGED";

    private static final String TAG = "ControlServerService";
    private BleProvisioningServer bleServer;
    private ControlServer server;
    private FCastServer fcastServer;
    private LocalDiscovery localDiscovery;
    private WifiDirectOnboarding wifiDirectOnboarding;

    @Override
    public void onCreate() {
        super.onCreate();
        try {
            server = new ControlServer(this, PORT);
            server.start();
            Log.i(TAG, "Control server listening on port " + PORT);
            localDiscovery = new LocalDiscovery(this);
            localDiscovery.start(PORT);
        } catch (IOException error) {
            Log.e(TAG, "Unable to start control server", error);
            stopSelf();
            return;
        }

        bleServer = new BleProvisioningServer(
                this,
                PairingManager.getInstance(this),
                new WifiProvisioner(this));
        bleServer.start();
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
        if (bleServer != null) {
            bleServer.stop();
            bleServer = null;
        }
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }
}
