package dev.mirror.repurpose.updater;

import android.app.Service;
import android.content.Intent;
import android.os.IBinder;
import android.util.Log;

import java.io.IOException;

public final class OtaService extends Service {
    private static final String TAG = "MirrorOtaService";

    private OtaManager manager;
    private OtaServer server;

    @Override
    public void onCreate() {
        super.onCreate();
        manager = new OtaManager(this);
        try {
            server = new OtaServer(this, manager);
            server.start();
            Log.i(TAG, "OTA supervisor listening on port " + OtaConstants.PORT);
        } catch (IOException error) {
            server = null;
            Log.e(TAG, "Unable to start OTA supervisor", error);
            stopSelf();
        }
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent != null && OtaConstants.ACTION_INSTALL_RESULT.equals(intent.getAction())) {
            manager.handleInstallResult(intent);
        }
        return START_STICKY;
    }

    @Override
    public void onDestroy() {
        if (server != null) {
            server.stop();
            server = null;
        }
        if (manager != null) {
            manager.close();
            manager = null;
        }
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }
}
