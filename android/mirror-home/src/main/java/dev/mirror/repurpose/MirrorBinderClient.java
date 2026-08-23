package dev.mirror.repurpose;

import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.os.Bundle;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import android.os.Message;
import android.os.Messenger;
import android.os.Parcel;
import android.util.Log;

public final class MirrorBinderClient {
    private static final String TAG = "MirrorBinderClient";
    private static final String ACTION_SERVICE_ACCESS =
            "com.mirror.framework.ACTION_MIRROR_SERVICE_ACCESS";
    private static final String EXTRA_SERVICE_NAME =
            "com.mirror.framework.EXTRA_SERVICE_NAME";
    private static final String EXTRA_RESPONSE_MESSENGER =
            "com.mirror.framework.EXTRA_SERVICE_RESP_MESSENGER";
    private static final String KEY_SERVICE_BINDER =
            "com.mirror.framework.SERVICE_BINDER";
    private static final String DESCRIPTOR =
            "com.mirror.framework.ICommunicationService";
    private static final ComponentName LOOKUP_RECEIVER = new ComponentName(
            "com.mirror.services",
            "com.mirror.services.ServiceLookupReceiver");

    private static final int TRANSACTION_SET_NAME = 4;
    private static final int TRANSACTION_GET_NAME = 7;
    private static final int TRANSACTION_GET_BRIGHTNESS = 23;
    private static final int TRANSACTION_SET_BRIGHTNESS = 22;

    private static volatile MirrorBinderClient instance;

    private final Context context;
    private final Messenger responseMessenger;
    private volatile IBinder binder;

    private MirrorBinderClient(Context context) {
        this.context = context.getApplicationContext();
        responseMessenger = new Messenger(
                new Handler(Looper.getMainLooper(), new Handler.Callback() {
                    @Override
                    public boolean handleMessage(Message message) {
                        if (message.obj instanceof Bundle) {
                            binder = ((Bundle) message.obj).getBinder(KEY_SERVICE_BINDER);
                            Log.i(TAG, binder == null ? "Binder response was empty" : "Binder connected");
                        }
                        return true;
                    }
                }));
    }

    public static MirrorBinderClient getInstance(Context context) {
        if (instance == null) {
            synchronized (MirrorBinderClient.class) {
                if (instance == null) {
                    instance = new MirrorBinderClient(context);
                }
            }
        }
        return instance;
    }

    public void connect() {
        Intent request = new Intent(ACTION_SERVICE_ACCESS);
        request.setComponent(LOOKUP_RECEIVER);
        request.putExtra(EXTRA_SERVICE_NAME, DESCRIPTOR);
        request.putExtra(EXTRA_RESPONSE_MESSENGER, responseMessenger);
        context.sendBroadcast(request);
    }

    public boolean isConnected() {
        return binder != null && binder.isBinderAlive();
    }

    public Integer getBrightness() {
        IBinder current = binder;
        if (current == null) {
            return null;
        }
        try {
            return transactInt(current, TRANSACTION_GET_BRIGHTNESS);
        } catch (Exception error) {
            Log.e(TAG, "Unable to read brightness", error);
            return null;
        }
    }

    public boolean setBrightness(int brightness) {
        IBinder current = binder;
        if (current == null || brightness < 1 || brightness > 255) {
            return false;
        }
        try {
            transactVoidInt(current, TRANSACTION_SET_BRIGHTNESS, brightness);
            return true;
        } catch (Exception error) {
            Log.e(TAG, "Unable to set brightness", error);
            return false;
        }
    }

    public String getName() {
        IBinder current = binder;
        if (current == null) {
            return null;
        }
        try {
            return transactString(current, TRANSACTION_GET_NAME);
        } catch (Exception error) {
            Log.e(TAG, "Unable to read name", error);
            return null;
        }
    }

    public boolean setName(String name) {
        IBinder current = binder;
        if (current == null || name == null || name.length() > 64) {
            return false;
        }
        try {
            transactVoidString(current, TRANSACTION_SET_NAME, name);
            return true;
        } catch (Exception error) {
            Log.e(TAG, "Unable to set name", error);
            return false;
        }
    }

    private static String transactString(IBinder binder, int code) throws Exception {
        Parcel request = Parcel.obtain();
        Parcel response = Parcel.obtain();
        try {
            request.writeInterfaceToken(DESCRIPTOR);
            requireTransaction(binder, code, request, response);
            return response.readString();
        } finally {
            response.recycle();
            request.recycle();
        }
    }

    private static int transactInt(IBinder binder, int code) throws Exception {
        Parcel request = Parcel.obtain();
        Parcel response = Parcel.obtain();
        try {
            request.writeInterfaceToken(DESCRIPTOR);
            requireTransaction(binder, code, request, response);
            return response.readInt();
        } finally {
            response.recycle();
            request.recycle();
        }
    }

    private static void transactVoidInt(IBinder binder, int code, int value)
            throws Exception {
        Parcel request = Parcel.obtain();
        Parcel response = Parcel.obtain();
        try {
            request.writeInterfaceToken(DESCRIPTOR);
            request.writeInt(value);
            requireTransaction(binder, code, request, response);
        } finally {
            response.recycle();
            request.recycle();
        }
    }

    private static void transactVoidString(IBinder binder, int code, String value)
            throws Exception {
        Parcel request = Parcel.obtain();
        Parcel response = Parcel.obtain();
        try {
            request.writeInterfaceToken(DESCRIPTOR);
            request.writeString(value);
            requireTransaction(binder, code, request, response);
        } finally {
            response.recycle();
            request.recycle();
        }
    }

    private static void requireTransaction(
            IBinder binder,
            int code,
            Parcel request,
            Parcel response) throws Exception {
        if (!binder.transact(code, request, response, 0)) {
            throw new IllegalStateException("Binder transaction " + code + " was rejected");
        }
        response.readException();
    }
}
