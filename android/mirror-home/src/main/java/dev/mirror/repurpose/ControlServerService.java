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
    private ControlServer server;

    @Override
    public void onCreate() {
        super.onCreate();
        try {
            server = new ControlServer(this, PORT);
            server.start();
            Log.i(TAG, "Control server listening on port " + PORT);
        } catch (IOException error) {
            Log.e(TAG, "Unable to start control server", error);
            stopSelf();
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
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }
}
