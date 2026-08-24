package dev.mirror.repurpose.updater;

import android.app.admin.DeviceAdminReceiver;
import android.content.Context;
import android.content.Intent;

public final class OtaDeviceAdminReceiver extends DeviceAdminReceiver {
    @Override
    public void onEnabled(Context context, Intent intent) {
        context.startService(new Intent(context, OtaService.class));
    }
}
