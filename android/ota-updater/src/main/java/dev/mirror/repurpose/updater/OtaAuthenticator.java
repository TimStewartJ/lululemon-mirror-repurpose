package dev.mirror.repurpose.updater;

import android.content.Context;
import android.content.SharedPreferences;
import android.util.Base64;

import java.nio.charset.StandardCharsets;
import java.security.GeneralSecurityException;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.util.HashSet;
import java.util.Locale;
import java.util.Map;
import java.util.Set;

import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;

final class OtaAuthenticator {
    static final String EMPTY_SHA256 =
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855";

    private static final String PREFERENCES = "ota_auth";
    private static final String KEY_TOKEN = "token";
    private static final String KEY_CONFIRMED = "confirmed";
    private static final String KEY_LAST_COUNTER = "last_counter";
    private static final String KEY_COUNTERS = "recent_counters";
    private static final String KEY_COUNTER_SET_INITIALIZED = "counter_set_initialized";
    private static final long COUNTER_REORDER_WINDOW = 5L * 60L * 1_000_000_000L;
    private static final int MAX_RECENT_COUNTERS = 512;

    private final SharedPreferences preferences;
    private final SecureRandom random = new SecureRandom();

    OtaAuthenticator(Context context) {
        preferences = context.getApplicationContext()
                .getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE);
    }

    synchronized boolean isProvisioned() {
        return preferences.getBoolean(KEY_CONFIRMED, false)
                && !preferences.getString(KEY_TOKEN, "").isEmpty();
    }

    synchronized String issueProvisioningToken(String bootstrapToken) throws AuthException {
        if (isProvisioned()) {
            throw new AuthException("OTA authentication is already provisioned");
        }
        requireBootstrapToken(bootstrapToken);
        String token = preferences.getString(KEY_TOKEN, "");
        if (token.isEmpty()) {
            byte[] value = new byte[32];
            random.nextBytes(value);
            token = Base64.encodeToString(
                    value,
                    Base64.URL_SAFE | Base64.NO_WRAP | Base64.NO_PADDING);
            if (!preferences.edit().putString(KEY_TOKEN, token).commit()) {
                throw new AuthException("Unable to persist OTA authentication");
            }
        }
        return token;
    }

    synchronized String recoverProvisioningToken(String bootstrapToken) throws AuthException {
        requireBootstrapToken(bootstrapToken);
        if (isProvisioned()) {
            if (!preferences.edit()
                    .putBoolean(KEY_CONFIRMED, false)
                    .remove(KEY_TOKEN)
                    .remove(KEY_LAST_COUNTER)
                    .remove(KEY_COUNTERS)
                    .remove(KEY_COUNTER_SET_INITIALIZED)
                    .commit()) {
                throw new AuthException("Unable to reset OTA authentication");
            }
        }
        return issueProvisioningToken(bootstrapToken);
    }

    private static void requireBootstrapToken(String token) throws AuthException {
        String expected = BuildConfig.BOOTSTRAP_TOKEN_SHA256;
        if (!bootstrapTokenMatches(token, expected)) {
            throw new AuthException("OTA bootstrap authentication is unavailable");
        }
    }

    static boolean bootstrapTokenMatches(String token, String expected) throws AuthException {
        if (expected == null
                || !expected.matches("[0-9a-f]{64}")
                || token == null
                || token.length() > 128) {
            return false;
        }
        byte[] actual = sha256(token.getBytes(StandardCharsets.UTF_8));
        byte[] configured;
        try {
            configured = decodeHex(expected);
        } catch (IllegalArgumentException error) {
            return false;
        }
        return MessageDigest.isEqual(actual, configured);
    }

    synchronized void confirm(
            String method,
            String path,
            Map<String, String> headers) throws AuthException {
        authorizeInternal(method, path, headers, EMPTY_SHA256, true);
        if (!preferences.edit().putBoolean(KEY_CONFIRMED, true).commit()) {
            throw new AuthException("Unable to confirm OTA authentication");
        }
    }

    synchronized void authorize(
            String method,
            String path,
            Map<String, String> headers,
            String bodySha256) throws AuthException {
        authorizeInternal(method, path, headers, bodySha256, false);
    }

    private void authorizeInternal(
            String method,
            String path,
            Map<String, String> headers,
            String bodySha256,
            boolean allowUnconfirmed) throws AuthException {
        String token = preferences.getString(KEY_TOKEN, "");
        if (token.isEmpty()
                || (!allowUnconfirmed && !preferences.getBoolean(KEY_CONFIRMED, false))) {
            throw new AuthException("OTA authentication is not provisioned");
        }
        String counterValue = header(headers, "x-ota-counter");
        String nonce = header(headers, "x-ota-nonce").toLowerCase(Locale.US);
        String suppliedBodyHash = header(headers, "x-ota-content-sha256")
                .toLowerCase(Locale.US);
        String authorization = header(headers, "authorization");
        if (!nonce.matches("[0-9a-f]{32,64}")
                || !suppliedBodyHash.matches("[0-9a-f]{64}")
                || !suppliedBodyHash.equals(bodySha256.toLowerCase(Locale.US))
                || !authorization.startsWith("MirrorOTA ")) {
            throw new AuthException("Invalid OTA authentication headers");
        }

        long counter;
        try {
            counter = Long.parseLong(counterValue);
        } catch (NumberFormatException error) {
            throw new AuthException("Invalid OTA request counter");
        }
        if (counter <= 0) {
            throw new AuthException("Invalid OTA request counter");
        }

        String canonical = canonicalRequest(
                method,
                path,
                counterValue,
                nonce,
                suppliedBodyHash);
        byte[] expected;
        byte[] supplied;
        try {
            expected = decodeHex(hmacHex(token, canonical));
            supplied = decodeHex(authorization.substring("MirrorOTA ".length()));
        } catch (IllegalArgumentException | GeneralSecurityException error) {
            throw new AuthException("Invalid OTA request signature");
        }
        if (!MessageDigest.isEqual(expected, supplied)) {
            throw new AuthException("Invalid OTA request signature");
        }
        rememberCounter(counter);
    }

    private void rememberCounter(long counter) throws AuthException {
        long maximum = preferences.getLong(KEY_LAST_COUNTER, 0L);
        boolean initialized = preferences.getBoolean(KEY_COUNTER_SET_INITIALIZED, false);
        Set<String> stored = preferences.getStringSet(KEY_COUNTERS, new HashSet<String>());
        CounterState state = advanceCounter(counter, maximum, stored, initialized);
        if (!preferences.edit()
                .putLong(KEY_LAST_COUNTER, state.maximum)
                .putStringSet(KEY_COUNTERS, state.counters)
                .putBoolean(KEY_COUNTER_SET_INITIALIZED, true)
                .commit()) {
            throw new AuthException("Unable to persist OTA replay protection");
        }
    }

    static CounterState advanceCounter(
            long counter,
            long maximum,
            Set<String> stored,
            boolean initialized) throws AuthException {
        Set<String> updated = new HashSet<>();
        long nextMaximum = Math.max(maximum, counter);
        long minimum = nextMaximum > COUNTER_REORDER_WINDOW
                ? nextMaximum - COUNTER_REORDER_WINDOW
                : 0L;

        if (!initialized && counter <= maximum) {
            throw new AuthException("OTA request counter was already used");
        }
        for (String value : stored) {
            try {
                long seen = Long.parseLong(value);
                if (seen >= minimum) {
                    if (seen == counter) {
                        throw new AuthException("OTA request counter was already used");
                    }
                    updated.add(value);
                }
            } catch (NumberFormatException ignored) {
            }
        }
        if (initialized && counter < minimum) {
            throw new AuthException("OTA request counter is too old");
        }
        if (updated.size() >= MAX_RECENT_COUNTERS) {
            throw new AuthException("Too many OTA requests are active");
        }
        updated.add(Long.toString(counter));
        return new CounterState(nextMaximum, updated);
    }

    static String canonicalRequest(
            String method,
            String path,
            String counter,
            String nonce,
            String bodySha256) {
        return method.toUpperCase(Locale.US)
                + "\n"
                + path
                + "\n"
                + counter
                + "\n"
                + nonce.toLowerCase(Locale.US)
                + "\n"
                + bodySha256.toLowerCase(Locale.US);
    }

    static String hmacHex(String token, String canonical) throws GeneralSecurityException {
        Mac mac = Mac.getInstance("HmacSHA256");
        mac.init(new SecretKeySpec(
                token.getBytes(StandardCharsets.UTF_8),
                "HmacSHA256"));
        return encodeHex(mac.doFinal(canonical.getBytes(StandardCharsets.UTF_8)));
    }

    private static byte[] sha256(byte[] value) throws AuthException {
        try {
            return MessageDigest.getInstance("SHA-256").digest(value);
        } catch (GeneralSecurityException impossible) {
            throw new AuthException("SHA-256 is unavailable");
        }
    }

    private static String header(Map<String, String> headers, String name) {
        String value = headers.get(name);
        return value == null ? "" : value.trim();
    }

    private static String encodeHex(byte[] value) {
        StringBuilder result = new StringBuilder(value.length * 2);
        for (byte item : value) {
            result.append(String.format(Locale.US, "%02x", item & 0xff));
        }
        return result.toString();
    }

    private static byte[] decodeHex(String value) {
        if (value.length() != 64 || !value.matches("[0-9a-fA-F]{64}")) {
            throw new IllegalArgumentException("Expected a SHA-256 hex value");
        }
        byte[] result = new byte[value.length() / 2];
        for (int index = 0; index < value.length(); index += 2) {
            result[index / 2] =
                    (byte) Integer.parseInt(value.substring(index, index + 2), 16);
        }
        return result;
    }

    static final class AuthException extends Exception {
        AuthException(String message) {
            super(message);
        }
    }

    static final class CounterState {
        final long maximum;
        final Set<String> counters;

        CounterState(long maximum, Set<String> counters) {
            this.maximum = maximum;
            this.counters = counters;
        }
    }
}
