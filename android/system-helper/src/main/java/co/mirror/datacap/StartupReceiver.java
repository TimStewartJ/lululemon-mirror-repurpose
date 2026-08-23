package co.mirror.datacap;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

public final class StartupReceiver extends BroadcastReceiver {
    @Override
    public void onReceive(Context context, Intent intent) {
        // The helper is deliberately transient and must be removed before reboot.
    }
}
