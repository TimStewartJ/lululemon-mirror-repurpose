package dev.mirror.repurpose;

import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

import java.net.InetAddress;

public final class LanAddressTest {
    private static InetAddress address(int... octets) throws Exception {
        byte[] bytes = new byte[octets.length];
        for (int index = 0; index < octets.length; index++) {
            bytes[index] = (byte) octets[index];
        }
        return InetAddress.getByAddress(bytes);
    }

    @Test
    public void onlyEthernetInterfacesCountAsWired() {
        assertTrue(LanAddress.isWired("eth0"));
        assertTrue(LanAddress.isWired("eth1"));
        // Wi-Fi is asked directly; Wi-Fi Direct and mobile data are not the household LAN.
        assertFalse(LanAddress.isWired("wlan0"));
        assertFalse(LanAddress.isWired("p2p0"));
        assertFalse(LanAddress.isWired("p2p-wlan0-0"));
        assertFalse(LanAddress.isWired("rmnet0"));
        assertFalse(LanAddress.isWired("lo"));
        assertFalse(LanAddress.isWired(""));
        assertFalse(LanAddress.isWired(null));
    }

    @Test
    public void aRoutableIpv4AddressIsUsable() throws Exception {
        assertTrue(LanAddress.usable(address(10, 0, 2, 15)));
        assertTrue(LanAddress.usable(address(192, 168, 1, 40)));
        assertTrue(LanAddress.usable(address(172, 16, 0, 9)));
    }

    @Test
    public void addressesNobodyElseCanReachAreNotUsable() throws Exception {
        assertFalse(LanAddress.usable(address(127, 0, 0, 1)));
        assertFalse(LanAddress.usable(address(169, 254, 10, 20)));
        assertFalse(LanAddress.usable(address(0, 0, 0, 0)));
        assertFalse(LanAddress.usable(address(224, 0, 0, 251)));
        assertFalse(LanAddress.usable(null));
    }

    @Test
    public void ipv6IsLeftToWifi() throws Exception {
        assertFalse(LanAddress.usable(
                address(0xfe, 0x80, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1)));
        assertFalse(LanAddress.usable(
                address(0x20, 0x01, 0x0d, 0xb8, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1)));
    }
}
