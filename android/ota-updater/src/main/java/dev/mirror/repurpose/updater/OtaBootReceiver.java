package dev.mirror.repurpose.updater;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

public final class OtaBootReceiver extends BroadcastReceiver {
    @Override
    public void onReceive(Context context, Intent intent) {
        context.startService(new Intent(context, OtaService.class));
    }
}
