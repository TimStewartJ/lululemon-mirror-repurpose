package dev.mirror.repurpose;

import android.content.Context;
import android.content.SharedPreferences;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.Locale;

final class BackgroundVideoProvisioner {
    private static final String PREFERENCES = "mirror_home_background_videos";
    private static final String KEY_LEGACY_CONSUMED_HASH = "bootstrap_consumed_hash";
    private static final String KEY_PENDING_HASH = "bootstrap_pending_hash";
    private static final String KEY_PENDING_TOKEN = "bootstrap_pending_token";
    private static final String KEY_PENDING_CLIENT_ID = "bootstrap_pending_client_id";
    private static final String KEY_PENDING_CLIENT_NAME = "bootstrap_pending_client_name";

    private final SharedPreferences preferences;
    private final PairingManager pairing;

    BackgroundVideoProvisioner(Context context, PairingManager pairing) {
        preferences = context.getApplicationContext()
                .getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE);
        this.pairing = pairing;
    }

    synchronized boolean available() {
        String configured = BuildConfig.BACKGROUND_VIDEO_BOOTSTRAP_TOKEN_SHA256;
        return !configured.isEmpty()
                && !isConsumed(
                        configured,
                        preferences.getBoolean(consumedKey(configured), false),
                        preferences.getString(KEY_LEGACY_CONSUMED_HASH, ""));
    }

    synchronized PairingManager.PairingResult provision(String candidate) {
        String configured = BuildConfig.BACKGROUND_VIDEO_BOOTSTRAP_TOKEN_SHA256;
        if (!available() || !matches(configured, candidate)) {
            return null;
        }
        if (configured.equals(preferences.getString(KEY_PENDING_HASH, ""))) {
            String token = preferences.getString(KEY_PENDING_TOKEN, "");
            String clientId = preferences.getString(KEY_PENDING_CLIENT_ID, "");
            String clientName = preferences.getString(KEY_PENDING_CLIENT_NAME, "");
            if (!token.isEmpty()
                    && !clientId.isEmpty()
                    && pairing.authenticate(token)) {
                return new PairingManager.PairingResult(clientId, clientName, token);
            }
            clearStalePending();
        } else {
            clearStalePending();
        }
        PairingManager.PairingResult result =
                pairing.issueTrustedClient("Background video CLI");
        if (result == null) {
            return null;
        }
        if (!preferences.edit()
                .putString(KEY_PENDING_HASH, configured)
                .putString(KEY_PENDING_TOKEN, result.token)
                .putString(KEY_PENDING_CLIENT_ID, result.clientId)
                .putString(KEY_PENDING_CLIENT_NAME, result.clientName)
                .commit()) {
            pairing.revokeClient(result.clientId);
            throw new IllegalStateException("Unable to persist background video bootstrap");
        }
        return result;
    }

    synchronized boolean confirm(String token) {
        String pending = preferences.getString(KEY_PENDING_TOKEN, "");
        String configured = BuildConfig.BACKGROUND_VIDEO_BOOTSTRAP_TOKEN_SHA256;
        String pendingHash = preferences.getString(KEY_PENDING_HASH, "");
        String tokenHash = token == null ? "" : sha256(token);
        if (configured.isEmpty()) {
            return false;
        }
        if (preferences.getBoolean(consumedKey(configured), false)) {
            boolean confirmed = constantTimeEquals(
                    preferences.getString(confirmedTokenKey(configured), ""),
                    tokenHash);
            if (confirmed) {
                persistLegacyConsumedHash(configured);
            }
            return confirmed;
        }
        if (configured.equals(
                preferences.getString(KEY_LEGACY_CONSUMED_HASH, ""))) {
            if (token == null || !pairing.authenticate(token)) {
                return false;
            }
            if (!preferences.edit()
                    .putBoolean(consumedKey(configured), true)
                    .putString(confirmedTokenKey(configured), tokenHash)
                    .remove(KEY_PENDING_HASH)
                    .remove(KEY_PENDING_TOKEN)
                    .remove(KEY_PENDING_CLIENT_ID)
                    .remove(KEY_PENDING_CLIENT_NAME)
                    .commit()) {
                throw new IllegalStateException(
                        "Unable to migrate background video bootstrap state");
            }
            return true;
        }
        if (pending.isEmpty()
                || !configured.equals(pendingHash)
                || !constantTimeEquals(pending, token)) {
            return false;
        }
        SharedPreferences.Editor editor = preferences.edit()
                .putBoolean(consumedKey(configured), true)
                .putString(confirmedTokenKey(configured), tokenHash)
                .remove(KEY_PENDING_HASH)
                .remove(KEY_PENDING_TOKEN)
                .remove(KEY_PENDING_CLIENT_ID)
                .remove(KEY_PENDING_CLIENT_NAME);
        String legacyConsumed =
                preferences.getString(KEY_LEGACY_CONSUMED_HASH, "");
        if (legacyConsumed.isEmpty()) {
            editor.putString(KEY_LEGACY_CONSUMED_HASH, configured);
        } else if (!legacyConsumed.equals(configured)
                && legacyConsumed.matches("[0-9a-f]{64}")) {
            editor.putBoolean(consumedKey(legacyConsumed), true);
        }
        boolean committed = editor.commit();
        if (!committed) {
            throw new IllegalStateException("Unable to confirm background video bootstrap");
        }
        return true;
    }

    static boolean isConsumed(
            String configured,
            boolean perHashConsumed,
            String legacyConsumedHash) {
        return perHashConsumed
                || (configured != null && configured.equals(legacyConsumedHash));
    }

    static boolean matches(String expectedSha256, String candidate) {
        if (expectedSha256 == null
                || !expectedSha256.matches("[0-9a-f]{64}")
                || candidate == null
                || candidate.length() < 32
                || candidate.length() > 256) {
            return false;
        }
        return MessageDigest.isEqual(
                expectedSha256.getBytes(StandardCharsets.UTF_8),
                sha256(candidate).getBytes(StandardCharsets.UTF_8));
    }

    private void clearStalePending() {
        String clientId = preferences.getString(KEY_PENDING_CLIENT_ID, "");
        if (!clientId.isEmpty()) {
            pairing.revokeClient(clientId);
        }
        if (!preferences.edit()
                .remove(KEY_PENDING_HASH)
                .remove(KEY_PENDING_TOKEN)
                .remove(KEY_PENDING_CLIENT_ID)
                .remove(KEY_PENDING_CLIENT_NAME)
                .commit()) {
            throw new IllegalStateException("Unable to clear stale background video bootstrap");
        }
    }

    private static boolean constantTimeEquals(String first, String second) {
        return first != null
                && second != null
                && MessageDigest.isEqual(
                        first.getBytes(StandardCharsets.UTF_8),
                        second.getBytes(StandardCharsets.UTF_8));
    }

    private static String consumedKey(String hash) {
        return "bootstrap_consumed_" + hash;
    }

    private static String confirmedTokenKey(String hash) {
        return "bootstrap_confirmed_token_" + hash;
    }

    private void persistLegacyConsumedHash(String configured) {
        String legacyConsumed =
                preferences.getString(KEY_LEGACY_CONSUMED_HASH, "");
        if (!legacyConsumed.isEmpty()) {
            return;
        }
        if (!preferences.edit()
                .putString(KEY_LEGACY_CONSUMED_HASH, configured)
                .commit()) {
            throw new IllegalStateException(
                    "Unable to persist background video bootstrap compatibility state");
        }
    }

    private static String sha256(String value) {
        try {
            byte[] digest = MessageDigest.getInstance("SHA-256")
                    .digest(value.getBytes(StandardCharsets.UTF_8));
            StringBuilder result = new StringBuilder(digest.length * 2);
            for (byte item : digest) {
                result.append(String.format(Locale.US, "%02x", item & 0xff));
            }
            return result.toString();
        } catch (NoSuchAlgorithmException impossible) {
            throw new IllegalStateException("SHA-256 is unavailable", impossible);
        }
    }
}
