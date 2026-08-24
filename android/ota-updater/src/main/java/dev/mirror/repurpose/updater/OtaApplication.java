package dev.mirror.repurpose.updater;

import android.app.Application;
import android.content.Intent;

public final class OtaApplication extends Application {
    @Override
    public void onCreate() {
        super.onCreate();
        System.setProperty("java.io.tmpdir", getCacheDir().getAbsolutePath());
        startService(new Intent(this, OtaService.class));
    }
}
