package dev.mirror.repurpose;

import android.content.Context;
import android.net.wifi.WifiConfiguration;
import android.net.wifi.WifiManager;
import android.os.Build;

import java.util.List;
import java.util.Locale;

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

    public WifiProvisioner(Context context) {
        wifiManager = (WifiManager) context.getApplicationContext()
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

        if (!wifiManager.isWifiEnabled() && !wifiManager.setWifiEnabled(true)) {
            return new Result(false, "Android rejected enabling Wi-Fi");
        }

        String quotedSsid = quote(ssid);
        List<WifiConfiguration> configured = wifiManager.getConfiguredNetworks();
        if (configured != null) {
            for (WifiConfiguration existing : configured) {
                if (quotedSsid.equals(existing.SSID)) {
                    wifiManager.removeNetwork(existing.networkId);
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
        return new Result(
                enabled && reconnected,
                enabled && reconnected
                        ? "Wi-Fi connection requested"
                        : "Network saved, but reconnect was rejected");
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
