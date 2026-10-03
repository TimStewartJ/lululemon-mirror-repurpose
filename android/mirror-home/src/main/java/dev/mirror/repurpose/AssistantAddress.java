package dev.mirror.repurpose;

import java.net.URI;
import java.net.URISyntaxException;
import java.util.Locale;

/** Where the companion is: an http address on the household network. */
final class AssistantAddress {
    private AssistantAddress() {
    }

    /**
     * The address as it is kept: scheme, host and port, nothing after.
     *
     * @param entered what an owner typed, such as {@code 10.0.0.90:8790}
     * @return the address, or an empty string for no companion
     * @throws IllegalArgumentException with the reason, if it is no usable address
     */
    static String normalize(String entered) {
        String text = entered == null ? "" : entered.trim();
        if (text.isEmpty()) {
            return "";
        }
        if (text.length() > 200) {
            throw new IllegalArgumentException("The companion's address is too long");
        }
        if (!text.contains("://")) {
            text = "http://" + text;
        }
        URI address;
        try {
            address = new URI(text);
        } catch (URISyntaxException malformed) {
            throw new IllegalArgumentException("The companion's address is not a web address");
        }
        String scheme = address.getScheme() == null ? "" : address.getScheme().toLowerCase(Locale.ROOT);
        if (!scheme.equals("http") && !scheme.equals("https")) {
            throw new IllegalArgumentException("The companion's address must begin with http:// or https://");
        }
        String host = address.getHost();
        if (host == null || host.isEmpty() || address.getUserInfo() != null) {
            throw new IllegalArgumentException("The companion's address needs a host, such as 10.0.0.90:8790");
        }
        String path = address.getRawPath();
        if ((path != null && !path.isEmpty() && !path.equals("/"))
                || address.getRawQuery() != null
                || address.getRawFragment() != null) {
            throw new IllegalArgumentException("The companion's address ends after its port");
        }
        int port = address.getPort();
        if (port == 0 || port > 65535) {
            throw new IllegalArgumentException("The companion's port must be between 1 and 65535");
        }
        String bracketed = host.contains(":") && !host.startsWith("[") ? "[" + host + "]" : host;
        return scheme + "://" + bracketed.toLowerCase(Locale.ROOT) + (port < 0 ? "" : ":" + port);
    }
}
