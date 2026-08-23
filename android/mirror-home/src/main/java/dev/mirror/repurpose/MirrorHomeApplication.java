package dev.mirror.repurpose;

import android.app.Application;
import android.content.Intent;

public final class MirrorHomeApplication extends Application {
    @Override
    public void onCreate() {
        super.onCreate();
        System.setProperty("java.io.tmpdir", getCacheDir().getAbsolutePath());
        MirrorBinderClient.getInstance(this).connect();
        SystemHelperClient.getInstance(this).connect();
        startService(new Intent(this, ControlServerService.class));
    }
}
