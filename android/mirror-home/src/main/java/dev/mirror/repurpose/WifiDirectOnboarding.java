package dev.mirror.repurpose;

import android.annotation.SuppressLint;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.net.NetworkInfo;
import android.net.wifi.p2p.WifiP2pGroup;
import android.net.wifi.p2p.WifiP2pInfo;
import android.net.wifi.p2p.WifiP2pManager;
import android.os.Handler;
import android.os.Looper;
import android.util.Log;

public final class WifiDirectOnboarding {
    public static final class Snapshot {
        public final boolean active;
        public final boolean clientConnected;
        public final String networkName;
        public final String passphrase;
        public final String address;
        public final String state;

        private Snapshot(
                boolean active,
                boolean clientConnected,
                String networkName,
                String passphrase,
                String address,
                String state) {
            this.active = active;
            this.clientConnected = clientConnected;
            this.networkName = networkName;
            this.passphrase = passphrase;
            this.address = address;
            this.state = state;
        }
    }

    public static final String ACTION_STATE_CHANGED =
            "dev.mirror.repurpose.WIFI_DIRECT_STATE_CHANGED";
    private static final String TAG = "WifiDirectOnboarding";
    private static final long STARTUP_DELAY_MS = 5_000L;
    private static final long PROVISIONED_SHUTDOWN_DELAY_MS = 15_000L;
    private static volatile WifiDirectOnboarding instance;

    private final Context context;
    private final WifiP2pManager manager;
    private final WifiP2pManager.Channel channel;
    private final Handler handler = new Handler(Looper.getMainLooper());
    private boolean receiverRegistered;
    private boolean active;
    private boolean clientConnected;
    private String networkName = "";
    private String passphrase = "";
    private String address = "192.168.49.1";
    private String state = "inactive";

    private final BroadcastReceiver receiver = new BroadcastReceiver() {
        @Override
        public void onReceive(Context context, Intent intent) {
            String action = intent.getAction();
            if (WifiP2pManager.WIFI_P2P_CONNECTION_CHANGED_ACTION.equals(action)) {
                NetworkInfo networkInfo =
                        intent.getParcelableExtra(WifiP2pManager.EXTRA_NETWORK_INFO);
                clientConnected = networkInfo != null && networkInfo.isConnected();
                requestDetails();
            } else if (WifiP2pManager.WIFI_P2P_STATE_CHANGED_ACTION.equals(action)) {
                int wifiState = intent.getIntExtra(
                        WifiP2pManager.EXTRA_WIFI_STATE,
                        WifiP2pManager.WIFI_P2P_STATE_DISABLED);
                if (wifiState != WifiP2pManager.WIFI_P2P_STATE_ENABLED) {
                    setState("unavailable");
                }
            }
        }
    };

    private WifiDirectOnboarding(Context context) {
        this.context = context.getApplicationContext();
        manager = (WifiP2pManager) this.context.getSystemService(Context.WIFI_P2P_SERVICE);
        channel = manager == null
                ? null
                : manager.initialize(this.context, Looper.getMainLooper(), () -> {
                    synchronized (WifiDirectOnboarding.this) {
                        active = false;
                        state = "channel-lost";
                    }
                    broadcast();
                });
    }

    public static WifiDirectOnboarding getInstance(Context context) {
        if (instance == null) {
            synchronized (WifiDirectOnboarding.class) {
                if (instance == null) {
                    instance = new WifiDirectOnboarding(context);
                }
            }
        }
        return instance;
    }

    public void startIfNeeded() {
        handler.postDelayed(() -> {
            ConfigStore configStore = new ConfigStore(context);
            if (configStore.getManagedWifiSsid().isEmpty()
                    && LanAddress.current(context).isEmpty()) {
                start();
            }
        }, STARTUP_DELAY_MS);
    }

    @SuppressLint("MissingPermission")
    public synchronized void start() {
        if (manager == null || channel == null || active) {
            return;
        }
        registerReceiver();
        state = "starting";
        broadcast();
        manager.requestGroupInfo(channel, group -> {
            if (group != null && group.isGroupOwner()) {
                updateGroup(group);
            } else {
                createGroup();
            }
        });
    }

    @SuppressLint("MissingPermission")
    private void createGroup() {
        manager.createGroup(channel, new WifiP2pManager.ActionListener() {
            @Override
            public void onSuccess() {
                synchronized (WifiDirectOnboarding.this) {
                    active = true;
                    state = "ready";
                }
                requestDetails();
            }

            @Override
            public void onFailure(int reason) {
                synchronized (WifiDirectOnboarding.this) {
                    active = false;
                    state = "failed-" + reason;
                }
                broadcast();
            }
        });
    }

    @SuppressLint("MissingPermission")
    public synchronized void stop() {
        handler.removeCallbacksAndMessages(null);
        if (manager != null && channel != null && active) {
            manager.removeGroup(channel, new WifiP2pManager.ActionListener() {
                @Override
                public void onSuccess() {
                }

                @Override
                public void onFailure(int reason) {
                    Log.w(TAG, "Unable to remove Wi-Fi Direct group: " + reason);
                }
            });
        }
        active = false;
        clientConnected = false;
        networkName = "";
        passphrase = "";
        state = "inactive";
        unregisterReceiver();
        broadcast();
    }

    public void onProvisioned() {
        handler.postDelayed(this::stop, PROVISIONED_SHUTDOWN_DELAY_MS);
    }

    public synchronized Snapshot snapshot() {
        return new Snapshot(
                active,
                clientConnected,
                networkName,
                passphrase,
                address,
                state);
    }

    public static String wifiQrPayload(String ssid, String password) {
        return "WIFI:T:WPA;S:" + escapeQr(ssid) + ";P:" + escapeQr(password) + ";;";
    }

    private static String escapeQr(String value) {
        return value
                .replace("\\", "\\\\")
                .replace(";", "\\;")
                .replace(",", "\\,")
                .replace(":", "\\:")
                .replace("\"", "\\\"");
    }

    @SuppressLint("MissingPermission")
    private void requestDetails() {
        if (manager == null || channel == null) {
            return;
        }
        manager.requestGroupInfo(channel, this::updateGroup);
        manager.requestConnectionInfo(channel, this::updateConnection);
    }

    private synchronized void updateGroup(WifiP2pGroup group) {
        if (group != null && group.isGroupOwner()) {
            active = true;
            networkName = group.getNetworkName() == null ? "" : group.getNetworkName();
            passphrase = group.getPassphrase() == null ? "" : group.getPassphrase();
            clientConnected = !group.getClientList().isEmpty();
            state = networkName.isEmpty() || passphrase.isEmpty() ? "starting" : "ready";
        }
        broadcast();
    }

    private synchronized void updateConnection(WifiP2pInfo info) {
        if (info != null && info.groupOwnerAddress != null) {
            address = info.groupOwnerAddress.getHostAddress();
        }
        broadcast();
    }

    @SuppressLint("UnspecifiedRegisterReceiverFlag")
    private synchronized void registerReceiver() {
        if (receiverRegistered) {
            return;
        }
        IntentFilter filter = new IntentFilter();
        filter.addAction(WifiP2pManager.WIFI_P2P_STATE_CHANGED_ACTION);
        filter.addAction(WifiP2pManager.WIFI_P2P_CONNECTION_CHANGED_ACTION);
        context.registerReceiver(receiver, filter);
        receiverRegistered = true;
    }

    private synchronized void unregisterReceiver() {
        if (!receiverRegistered) {
            return;
        }
        try {
            context.unregisterReceiver(receiver);
        } catch (IllegalArgumentException ignored) {
            // Receiver was already removed during process teardown.
        }
        receiverRegistered = false;
    }

    private synchronized void setState(String nextState) {
        state = nextState;
        broadcast();
    }

    private void broadcast() {
        Intent intent = new Intent(ACTION_STATE_CHANGED);
        intent.setPackage(context.getPackageName());
        context.sendBroadcast(intent);
    }
}
