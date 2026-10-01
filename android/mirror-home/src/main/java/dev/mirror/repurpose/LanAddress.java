package dev.mirror.repurpose;

import android.content.Context;
import android.net.wifi.WifiInfo;
import android.net.wifi.WifiManager;

import java.net.Inet4Address;
import java.net.InetAddress;
import java.net.NetworkInterface;
import java.net.SocketException;
import java.util.Collections;
import java.util.List;

/**
 * The address another device on the household network uses to reach the
 * Mirror: its Wi-Fi address, or failing that a wired one. The Wi-Fi Direct
 * setup group is deliberately not counted, since it is not the household LAN.
 */
final class LanAddress {
    private LanAddress() {
    }

    /** Empty when the Mirror is on no household network. */
    static String current(Context context) {
        WifiManager manager = (WifiManager) context.getApplicationContext()
                .getSystemService(Context.WIFI_SERVICE);
        WifiInfo info = manager == null ? null : manager.getConnectionInfo();
        if (info != null && info.getNetworkId() >= 0) {
            String address = WifiProvisioner.ipAddress(info.getIpAddress());
            if (!address.isEmpty()) {
                return address;
            }
        }
        return wired();
    }

    private static String wired() {
        try {
            List<NetworkInterface> interfaces =
                    Collections.list(NetworkInterface.getNetworkInterfaces());
            for (NetworkInterface candidate : interfaces) {
                if (!isWired(candidate.getName()) || !candidate.isUp()) {
                    continue;
                }
                for (InetAddress address : Collections.list(candidate.getInetAddresses())) {
                    if (usable(address)) {
                        return address.getHostAddress();
                    }
                }
            }
        } catch (SocketException | RuntimeException unavailable) {
            // No interface list means no wired address.
        }
        return "";
    }

    static boolean isWired(String interfaceName) {
        return interfaceName != null && interfaceName.startsWith("eth");
    }

    static boolean usable(InetAddress address) {
        return address instanceof Inet4Address
                && !address.isLoopbackAddress()
                && !address.isLinkLocalAddress()
                && !address.isAnyLocalAddress()
                && !address.isMulticastAddress();
    }
}
