package dev.mirror.repurpose.updater;

import android.app.Service;
import android.content.Intent;
import android.os.IBinder;
import android.util.Log;

import dev.mirror.repurpose.health.HoldLink;

/**
 * Lets Mirror Home hold on to the supervisor.
 *
 * <p>Android ends background services first when memory runs short, and
 * the supervisor is one. While Mirror Home, which is on the display, is
 * bound to this service, Android ranks the supervisor's process with what
 * the owner is looking at and leaves it alone. That the connection exists
 * is what it is for; over it Mirror Home can only ask how the kernel ranks
 * this process ({@link HoldLink}). Only an app signed with the supervisor's
 * own key may bind, which the manifest sees to.
 *
 * <p>The supervisor needs none of this to work: {@link OtaService} is
 * started with Android and by {@link OtaApplication}, held or not.
 */
public final class HoldService extends Service {
    private static final String TAG = "MirrorOtaHold";

    private static volatile boolean held;

    private final IBinder binder = HoldLink.serve();

    /** Whether Mirror Home is holding on to this process. */
    static boolean held() {
        return held;
    }

    @Override
    public IBinder onBind(Intent intent) {
        held = true;
        Log.i(TAG, "Held by Mirror Home");
        return binder;
    }

    @Override
    public boolean onUnbind(Intent intent) {
        held = false;
        Log.i(TAG, "No longer held by Mirror Home");
        // So that a later hold is told to onRebind and counted again.
        return true;
    }

    @Override
    public void onRebind(Intent intent) {
        held = true;
        Log.i(TAG, "Held by Mirror Home again");
    }

    @Override
    public void onDestroy() {
        held = false;
        super.onDestroy();
    }
}
