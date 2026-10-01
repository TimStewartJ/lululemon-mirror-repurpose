package dev.mirror.repurpose;

import android.app.AlarmManager;
import android.app.PendingIntent;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.os.SystemClock;

public final class WatchdogReceiver extends BroadcastReceiver {
    private static final long INTERVAL_MS = 15 * 60 * 1000L;
    private static final int REQUEST_CODE = 4708;

    @Override
    public void onReceive(Context context, Intent intent) {
        context.startService(new Intent(context, ControlServerService.class));
        MirrorBinderClient.getInstance(context).connect();
        AutomationManager.getInstance(context).refresh();
        WeatherProvider.getInstance(context).refreshIfDue();
        // Keeps a history of the OTA supervisor's reachability between reports.
        Context application = context.getApplicationContext();
        new Thread(() -> {
            try {
                SupervisorProbe.check(application);
            } catch (org.json.JSONException ignored) {
                // The probe's own bookkeeping is all that matters here.
            }
        }, "supervisor-probe").start();
    }

    public static void schedule(Context context) {
        AlarmManager manager =
                (AlarmManager) context.getSystemService(Context.ALARM_SERVICE);
        if (manager == null) {
            return;
        }
        Intent intent = new Intent(context, WatchdogReceiver.class);
        PendingIntent pending = PendingIntent.getBroadcast(
                context,
                REQUEST_CODE,
                intent,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        manager.setInexactRepeating(
                AlarmManager.ELAPSED_REALTIME_WAKEUP,
                SystemClock.elapsedRealtime() + INTERVAL_MS,
                INTERVAL_MS,
                pending);
    }
}
