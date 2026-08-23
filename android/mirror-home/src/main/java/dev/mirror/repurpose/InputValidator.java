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

    public static boolean validTimeZone(String value) {
        if (value == null || value.isEmpty() || value.length() > 64) {
            return false;
        }
        if ("UTC".equals(value) || "GMT".equals(value)) {
            return true;
        }
        int separator = value.indexOf('/');
        if (separator < 1 || separator == value.length() - 1) {
            return false;
        }
        for (int index = 0; index < value.length(); index++) {
            char character = value.charAt(index);
            if (!(Character.isLetterOrDigit(character)
                    || character == '/'
                    || character == '_'
                    || character == '+'
                    || character == '-'
                    || character == '.')) {
                return false;
            }
        }
        return true;
    }

    public static int parseTimeMinutes(String value) {
        if (value == null || !value.matches("(?:[01][0-9]|2[0-3]):[0-5][0-9]")) {
            return -1;
        }
        return Integer.parseInt(value.substring(0, 2)) * 60
                + Integer.parseInt(value.substring(3, 5));
    }
}
