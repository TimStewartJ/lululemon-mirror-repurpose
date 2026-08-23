package dev.mirror.repurpose;

import android.Manifest;
import android.annotation.SuppressLint;
import android.content.Context;
import android.content.pm.PackageManager;
import android.net.wifi.WifiConfiguration;
import android.net.wifi.WifiInfo;
import android.net.wifi.WifiManager;
import android.os.Build;
import android.os.SystemClock;

import java.util.List;
import java.util.Locale;

@SuppressLint("MissingPermission")
public final class WifiProvisioner {
    public static final class Result {
        public final boolean success;
        public final String message;

        private Result(boolean success, String message) {
            this.success = success;
            this.message = message;
        }
    }

    private final WifiManager wifiManager;
    private final ConfigStore configStore;
    private final Context context;

    public WifiProvisioner(Context context) {
        this.context = context.getApplicationContext();
        configStore = new ConfigStore(this.context);
        wifiManager = (WifiManager) this.context
                .getSystemService(Context.WIFI_SERVICE);
    }

    public Result configure(String ssid, String passphrase, boolean hidden) {
        if (Build.VERSION.SDK_INT >= 29) {
            return new Result(
                    false,
                    "This provisioning path is limited to the supported Android 6 firmware");
        }
        if (!InputValidator.validSsid(ssid)) {
            return new Result(false, "SSID must contain 1-32 UTF-8 bytes");
        }
        if (!InputValidator.validWpaPassphrase(passphrase)) {
            return new Result(false, "WPA passphrase must contain 8-63 bytes or 64 hex digits");
        }
        if (wifiManager == null) {
            return new Result(false, "Wi-Fi service is unavailable");
        }
        if (Build.VERSION.SDK_INT >= 23
                && context.checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION)
                        != PackageManager.PERMISSION_GRANTED) {
            return new Result(false, "Location permission is required for Wi-Fi provisioning");
        }

        if (!enableWifi()) {
            return new Result(false, "Android rejected enabling Wi-Fi");
        }

        String quotedSsid = quote(ssid);
        List<WifiConfiguration> configured = wifiManager.getConfiguredNetworks();
        if (configured != null) {
            for (WifiConfiguration existing : configured) {
                if (quotedSsid.equals(existing.SSID)) {
                    WifiInfo current = wifiManager.getConnectionInfo();
                    if (current != null && current.getNetworkId() == existing.networkId) {
                        configStore.setManagedWifiSsid(ssid);
                        return new Result(true, "Already connected to this Wi-Fi network");
                    }
                    if (!wifiManager.removeNetwork(existing.networkId)) {
                        boolean enabled = wifiManager.enableNetwork(existing.networkId, true);
                        boolean reconnected = wifiManager.reconnect();
                        if (enabled && reconnected) {
                            configStore.setManagedWifiSsid(ssid);
                        }
                        return new Result(
                                enabled && reconnected,
                                enabled && reconnected
                                        ? "Existing Wi-Fi connection requested"
                                        : "Existing network could not be activated");
                    }
                }
            }
        }

        WifiConfiguration configuration = new WifiConfiguration();
        configuration.SSID = quotedSsid;
        configuration.hiddenSSID = hidden;
        configuration.status = WifiConfiguration.Status.ENABLED;
        if (passphrase.matches("[0-9a-fA-F]{64}")) {
            configuration.preSharedKey = passphrase;
        } else {
            configuration.preSharedKey = quote(passphrase);
        }
        configuration.allowedKeyManagement.set(WifiConfiguration.KeyMgmt.WPA_PSK);

        int networkId = wifiManager.addNetwork(configuration);
        if (networkId < 0) {
            return new Result(false, "Android rejected the Wi-Fi configuration");
        }
        wifiManager.saveConfiguration();
        wifiManager.disconnect();
        boolean enabled = wifiManager.enableNetwork(networkId, true);
        boolean reconnected = wifiManager.reconnect();
        if (enabled && reconnected) {
            configStore.setManagedWifiSsid(ssid);
        }
        return new Result(
                enabled && reconnected,
                enabled && reconnected
                        ? "Wi-Fi connection requested"
                        : "Network saved, but reconnect was rejected");
    }

    public boolean ensureConnection() {
        String ssid = configStore.getManagedWifiSsid();
        if (ssid.isEmpty() || wifiManager == null || Build.VERSION.SDK_INT >= 29) {
            return false;
        }
        if (!enableWifi()) {
            return false;
        }
        List<WifiConfiguration> configured = wifiManager.getConfiguredNetworks();
        if (configured == null) {
            return false;
        }
        String quotedSsid = quote(ssid);
        for (WifiConfiguration network : configured) {
            if (quotedSsid.equals(network.SSID)) {
                return wifiManager.enableNetwork(network.networkId, true)
                        && wifiManager.reconnect();
            }
        }
        return false;
    }

    public boolean isConnected() {
        if (wifiManager == null || !wifiManager.isWifiEnabled()) {
            return false;
        }
        WifiInfo info = wifiManager.getConnectionInfo();
        return info != null && info.getNetworkId() >= 0 && info.getIpAddress() != 0;
    }

    private boolean enableWifi() {
        if (wifiManager.isWifiEnabled()) {
            return true;
        }
        if (!wifiManager.setWifiEnabled(true)) {
            return false;
        }
        long deadline = SystemClock.elapsedRealtime() + 10_000L;
        while (!wifiManager.isWifiEnabled() && SystemClock.elapsedRealtime() < deadline) {
            SystemClock.sleep(250L);
        }
        return wifiManager.isWifiEnabled();
    }

    public static String cleanSsid(String ssid) {
        if (ssid == null) {
            return "";
        }
        if (ssid.length() >= 2 && ssid.startsWith("\"") && ssid.endsWith("\"")) {
            return ssid.substring(1, ssid.length() - 1);
        }
        return ssid;
    }

    public static String ipAddress(int value) {
        if (value == 0) {
            return "";
        }
        return String.format(
                Locale.US,
                "%d.%d.%d.%d",
                value & 0xff,
                value >> 8 & 0xff,
                value >> 16 & 0xff,
                value >> 24 & 0xff);
    }

    private static String quote(String value) {
        return "\"" + value.replace("\\", "\\\\").replace("\"", "\\\"") + "\"";
    }
}
