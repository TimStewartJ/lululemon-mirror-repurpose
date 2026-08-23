package com.amazonaws.mobileconnectors.s3.transferutility;

import android.app.Service;
import android.content.Intent;
import android.os.IBinder;

public final class TransferService extends Service {
    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }
}
