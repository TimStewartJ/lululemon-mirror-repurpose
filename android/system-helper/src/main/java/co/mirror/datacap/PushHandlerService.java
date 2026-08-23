package co.mirror.datacap;

import android.app.Service;
import android.content.Intent;
import android.os.IBinder;

public final class PushHandlerService extends Service {
    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }
}
