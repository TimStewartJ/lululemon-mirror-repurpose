package dev.mirror.repurpose;

import java.net.URI;
import java.nio.charset.StandardCharsets;

public final class InputValidator {
    private InputValidator() {
    }

    public static boolean validSsid(String ssid) {
        if (ssid == null) {
            return false;
        }
        int length = ssid.getBytes(StandardCharsets.UTF_8).length;
        return length >= 1 && length <= 32;
    }

    public static boolean validWpaPassphrase(String passphrase) {
        if (passphrase == null) {
            return false;
        }
        if (passphrase.matches("[0-9a-fA-F]{64}")) {
            return true;
        }
        int length = passphrase.getBytes(StandardCharsets.UTF_8).length;
        return length >= 8 && length <= 63;
    }

    public static boolean validDashboardUrl(String value) {
        if (value == null || value.isEmpty()) {
            return true;
        }
        try {
            URI uri = URI.create(value);
            String scheme = uri.getScheme();
            return uri.getHost() != null
                    && ("http".equalsIgnoreCase(scheme) || "https".equalsIgnoreCase(scheme));
        } catch (IllegalArgumentException ignored) {
            return false;
        }
    }

    public static boolean validMediaUrl(String value) {
        if (value == null || value.isEmpty()) {
            return false;
        }
        try {
            URI uri = URI.create(value);
            String scheme = uri.getScheme();
            return uri.getHost() != null
                    && ("http".equalsIgnoreCase(scheme)
                    || "https".equalsIgnoreCase(scheme)
                    || "rtsp".equalsIgnoreCase(scheme));
        } catch (IllegalArgumentException ignored) {
            return false;
        }
    }
}
