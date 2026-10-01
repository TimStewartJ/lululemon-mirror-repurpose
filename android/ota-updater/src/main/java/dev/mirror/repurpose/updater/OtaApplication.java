package dev.mirror.repurpose.updater;

import android.app.Application;
import android.content.Intent;

import dev.mirror.repurpose.health.ProcessHealth;

public final class OtaApplication extends Application {
    @Override
    public void onCreate() {
        super.onCreate();
        // First, so a failure while the service starts is on record afterwards.
        ProcessHealth.start(this, BuildConfig.VERSION_NAME, BuildConfig.VERSION_CODE);
        System.setProperty("java.io.tmpdir", getCacheDir().getAbsolutePath());
        startService(new Intent(this, OtaService.class));
    }
}
