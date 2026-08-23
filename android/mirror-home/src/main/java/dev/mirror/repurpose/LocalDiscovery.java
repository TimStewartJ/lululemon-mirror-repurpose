package dev.mirror.repurpose;

import android.content.Context;
import android.net.nsd.NsdManager;
import android.net.nsd.NsdServiceInfo;
import android.util.Log;

import java.util.ArrayList;
import java.util.List;

public final class LocalDiscovery {
    private static final String TAG = "LocalDiscovery";

    private final Context context;
    private final ConfigStore configStore;
    private final List<NsdManager.RegistrationListener> listeners = new ArrayList<>();

    public LocalDiscovery(Context context) {
        this.context = context.getApplicationContext();
        configStore = new ConfigStore(this.context);
    }

    public void start(int port) {
        register("_http._tcp.", port);
        register("_mirror-home._tcp.", port);
    }

    public void stop() {
        NsdManager manager = (NsdManager) context.getSystemService(Context.NSD_SERVICE);
        if (manager == null) {
            listeners.clear();
            return;
        }
        for (NsdManager.RegistrationListener listener : listeners) {
            try {
                manager.unregisterService(listener);
            } catch (RuntimeException error) {
                Log.w(TAG, "Unable to unregister discovery service", error);
            }
        }
        listeners.clear();
    }

    private void register(String type, int port) {
        NsdManager manager = (NsdManager) context.getSystemService(Context.NSD_SERVICE);
        if (manager == null) {
            return;
        }
        NsdServiceInfo info = new NsdServiceInfo();
        info.setServiceName(configStore.getDisplayName() + " Control");
        info.setServiceType(type);
        info.setPort(port);
        info.setAttribute("path", "/");
        info.setAttribute("version", BuildConfig.VERSION_NAME);

        NsdManager.RegistrationListener listener = new NsdManager.RegistrationListener() {
            @Override
            public void onServiceRegistered(NsdServiceInfo serviceInfo) {
                Log.i(TAG, "Advertised " + serviceInfo.getServiceType());
            }

            @Override
            public void onRegistrationFailed(NsdServiceInfo serviceInfo, int errorCode) {
                Log.w(TAG, "Discovery registration failed: " + errorCode);
            }

            @Override
            public void onServiceUnregistered(NsdServiceInfo serviceInfo) {
            }

            @Override
            public void onUnregistrationFailed(NsdServiceInfo serviceInfo, int errorCode) {
                Log.w(TAG, "Discovery unregistration failed: " + errorCode);
            }
        };
        try {
            manager.registerService(info, NsdManager.PROTOCOL_DNS_SD, listener);
            listeners.add(listener);
        } catch (RuntimeException error) {
            Log.w(TAG, "Unable to advertise " + type, error);
        }
    }
}
