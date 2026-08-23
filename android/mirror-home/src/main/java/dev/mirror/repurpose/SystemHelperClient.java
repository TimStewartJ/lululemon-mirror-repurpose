package dev.mirror.repurpose;

import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.content.ServiceConnection;
import android.os.IBinder;
import android.os.Handler;
import android.os.Looper;
import android.os.Parcel;
import android.util.Log;

import org.json.JSONObject;

public final class SystemHelperClient {
    private static final String TAG = "SystemHelperClient";
    private static final String DESCRIPTOR = "dev.mirror.repurpose.ISystemHelper";
    private static final ComponentName SERVICE = new ComponentName(
            "co.mirror.datacap",
            "co.mirror.datacap.DataCapIntentService");
    private static final int TRANSACTION_GET_CAPABILITIES = 1;
    private static final int TRANSACTION_PREPARE_KIOSK = 2;

    private static volatile SystemHelperClient instance;

    private final Context context;
    private final Handler handler = new Handler(Looper.getMainLooper());
    private final ServiceConnection connection = new ServiceConnection() {
        @Override
        public void onServiceConnected(ComponentName name, IBinder service) {
            binder = service;
            bound = true;
            binding = false;
            Log.i(TAG, "System helper connected");
        }

        @Override
        public void onServiceDisconnected(ComponentName name) {
            binder = null;
            bound = false;
            binding = false;
        }
    };

    private volatile IBinder binder;
    private volatile boolean bound;
    private volatile boolean binding;

    private SystemHelperClient(Context context) {
        this.context = context.getApplicationContext();
    }

    public static SystemHelperClient getInstance(Context context) {
        if (instance == null) {
            synchronized (SystemHelperClient.class) {
                if (instance == null) {
                    instance = new SystemHelperClient(context);
                }
            }
        }
        return instance;
    }

    public synchronized void connect() {
        if (isConnected() || binding) {
            return;
        }
        binding = true;
        Intent intent = new Intent();
        intent.setComponent(SERVICE);
        bound = context.bindService(intent, connection, Context.BIND_AUTO_CREATE);
        if (!bound) {
            binding = false;
            return;
        }
        handler.postDelayed(new Runnable() {
            @Override
            public void run() {
                synchronized (SystemHelperClient.this) {
                    if (binding && binder == null) {
                        try {
                            context.unbindService(connection);
                        } catch (IllegalArgumentException ignored) {
                            // The stock service returned a null Binder and may already be unbound.
                        }
                        bound = false;
                        binding = false;
                    }
                }
            }
        }, 3000);
    }

    public boolean isConnected() {
        return binder != null && binder.isBinderAlive();
    }

    public JSONObject capabilities() {
        IBinder current = binder;
        if (current == null) {
            return null;
        }
        Parcel request = Parcel.obtain();
        Parcel response = Parcel.obtain();
        try {
            request.writeInterfaceToken(DESCRIPTOR);
            if (!current.transact(TRANSACTION_GET_CAPABILITIES, request, response, 0)) {
                return null;
            }
            response.readException();
            return new JSONObject(response.readString());
        } catch (Exception error) {
            Log.e(TAG, "Unable to read system helper capabilities", error);
            return null;
        } finally {
            response.recycle();
            request.recycle();
        }
    }

    public boolean prepareKiosk() {
        IBinder current = binder;
        if (current == null) {
            return false;
        }
        Parcel request = Parcel.obtain();
        Parcel response = Parcel.obtain();
        try {
            request.writeInterfaceToken(DESCRIPTOR);
            if (!current.transact(TRANSACTION_PREPARE_KIOSK, request, response, 0)) {
                return false;
            }
            response.readException();
            return response.readInt() != 0;
        } catch (Exception error) {
            Log.e(TAG, "Unable to prepare kiosk settings", error);
            return false;
        } finally {
            response.recycle();
            request.recycle();
        }
    }
}
