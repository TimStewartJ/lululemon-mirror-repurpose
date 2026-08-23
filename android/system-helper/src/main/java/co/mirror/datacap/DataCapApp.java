package co.mirror.datacap;

import android.app.Application;
import android.util.Log;

public final class DataCapApp extends Application {
    @Override
    public void onCreate() {
        super.onCreate();
        Log.i("MirrorSystemHelper", "System helper process started");
    }
}
