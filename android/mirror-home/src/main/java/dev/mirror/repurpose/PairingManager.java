package dev.mirror.repurpose;

import android.content.Context;
import android.content.SharedPreferences;
import android.util.Base64;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.security.SecureRandom;
import java.util.Locale;

public final class PairingManager {
    public static final class PairingResult {
        public final String clientId;
        public final String clientName;
        public final String token;

        PairingResult(String clientId, String clientName, String token) {
            this.clientId = clientId;
            this.clientName = clientName;
            this.token = token;
        }
    }

    private static volatile PairingManager instance;

    private static final String PREFERENCES = "mirror_home";
    private static final String KEY_LEGACY_TOKEN = "pairing_token";
    private static final String KEY_CLIENTS = "paired_clients_v2";
    private static final long CODE_LIFETIME_MS = 10 * 60 * 1000L;
    private static final long FAILURE_LOCKOUT_MS = 30 * 1000L;
    private static final long LAST_USED_WRITE_INTERVAL_MS = 60 * 60 * 1000L;
    private static final int MAX_FAILURES = 5;
    private static final int MAX_CLIENTS = 32;

    private final SharedPreferences preferences;
    private final SecureRandom random = new SecureRandom();
    private String code;
    private long codeExpiresAt;
    private int failures;
    private long lockedUntil;

    private PairingManager(Context context) {
        preferences = context.getApplicationContext()
                .getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE);
        migrateLegacyToken();
    }

    public static PairingManager getInstance(Context context) {
        if (instance == null) {
            synchronized (PairingManager.class) {
                if (instance == null) {
                    instance = new PairingManager(context);
                }
            }
        }
        return instance;
    }

    public synchronized String currentCode() {
        long now = System.currentTimeMillis();
        if (code == null || now >= codeExpiresAt) {
            code = String.format(Locale.US, "%06d", random.nextInt(1_000_000));
            codeExpiresAt = now + CODE_LIFETIME_MS;
            failures = 0;
            lockedUntil = 0;
        }
        return code;
    }

    public synchronized String pair(String candidate) {
        PairingResult result = pair(candidate, "Companion");
        return result == null ? null : result.token;
    }

    public synchronized PairingResult pair(String candidate, String requestedName) {
        long now = System.currentTimeMillis();
        if (now < lockedUntil || candidate == null) {
            return null;
        }
        boolean matches = MessageDigest.isEqual(
                currentCode().getBytes(StandardCharsets.UTF_8),
                candidate.getBytes(StandardCharsets.UTF_8));
        if (!matches) {
            failures++;
            if (failures >= MAX_FAILURES) {
                failures = 0;
                lockedUntil = now + FAILURE_LOCKOUT_MS;
            }
            return null;
        }

        PairingResult result = issueTrustedClient(requestedName);
        if (result == null) {
            return null;
        }
        code = null;
        return result;
    }

    synchronized PairingResult issueTrustedClient(String requestedName) {
        JSONArray clients = readClients();
        if (clients.length() >= MAX_CLIENTS) {
            return null;
        }
        long now = System.currentTimeMillis();
        String token = randomValue(32);
        String clientId = randomValue(12);
        String clientName = normalizeClientName(requestedName);
        JSONObject client = new JSONObject();
        try {
            client.put("id", clientId);
            client.put("name", clientName);
            client.put("tokenHash", sha256(token));
            client.put("createdAt", now);
            client.put("lastUsedAt", now);
            clients.put(client);
        } catch (JSONException impossible) {
            throw new IllegalStateException("Unable to create paired client", impossible);
        }
        writeClients(clients);
        return new PairingResult(clientId, clientName, token);
    }

    public synchronized boolean authenticate(String token) {
        if (token == null) {
            return false;
        }
        String candidateHash = sha256(token);
        JSONArray clients = readClients();
        long now = System.currentTimeMillis();
        for (int index = 0; index < clients.length(); index++) {
            JSONObject client = clients.optJSONObject(index);
            if (client == null
                    || !constantTimeEquals(client.optString("tokenHash"), candidateHash)) {
                continue;
            }
            if (now - client.optLong("lastUsedAt", 0) >= LAST_USED_WRITE_INTERVAL_MS) {
                try {
                    client.put("lastUsedAt", now);
                    writeClients(clients);
                } catch (JSONException ignored) {
                    // Existing client records contain only JSON-safe values.
                }
            }
            return true;
        }
        return false;
    }

    public synchronized JSONArray clients() {
        JSONArray stored = readClients();
        JSONArray visible = new JSONArray();
        for (int index = 0; index < stored.length(); index++) {
            JSONObject client = stored.optJSONObject(index);
            if (client == null) {
                continue;
            }
            JSONObject item = new JSONObject();
            try {
                item.put("id", client.optString("id"));
                item.put("name", client.optString("name"));
                item.put("createdAt", client.optLong("createdAt"));
                item.put("lastUsedAt", client.optLong("lastUsedAt"));
                visible.put(item);
            } catch (JSONException ignored) {
                // Existing client records contain only JSON-safe values.
            }
        }
        return visible;
    }

    public synchronized boolean revokeClient(String clientId) {
        if (clientId == null || clientId.isEmpty()) {
            return false;
        }
        JSONArray stored = readClients();
        JSONArray retained = new JSONArray();
        boolean removed = false;
        for (int index = 0; index < stored.length(); index++) {
            JSONObject client = stored.optJSONObject(index);
            if (client != null && clientId.equals(client.optString("id"))) {
                removed = true;
            } else if (client != null) {
                retained.put(client);
            }
        }
        if (removed) {
            writeClients(retained);
        }
        return removed;
    }

    public synchronized boolean revokeToken(String token) {
        if (token == null) {
            return false;
        }
        String tokenHash = sha256(token);
        JSONArray stored = readClients();
        JSONArray retained = new JSONArray();
        boolean removed = false;
        for (int index = 0; index < stored.length(); index++) {
            JSONObject client = stored.optJSONObject(index);
            if (client != null
                    && constantTimeEquals(client.optString("tokenHash"), tokenHash)) {
                removed = true;
            } else if (client != null) {
                retained.put(client);
            }
        }
        if (removed) {
            writeClients(retained);
        }
        return removed;
    }

    public synchronized boolean isPaired() {
        return readClients().length() > 0;
    }

    public synchronized void revoke() {
        preferences.edit()
                .remove(KEY_CLIENTS)
                .remove(KEY_LEGACY_TOKEN)
                .apply();
        code = null;
    }

    static String normalizeClientName(String requestedName) {
        if (requestedName == null) {
            return "Device";
        }
        String cleaned = requestedName.trim().replaceAll("[\\p{Cntrl}]", "");
        if (cleaned.isEmpty()) {
            return "Device";
        }
        return cleaned.length() > 64 ? cleaned.substring(0, 64) : cleaned;
    }

    private void migrateLegacyToken() {
        if (preferences.contains(KEY_CLIENTS)) {
            return;
        }
        String legacyToken = preferences.getString(KEY_LEGACY_TOKEN, null);
        if (legacyToken == null || legacyToken.isEmpty()) {
            return;
        }
        long now = System.currentTimeMillis();
        JSONObject client = new JSONObject();
        JSONArray clients = new JSONArray();
        try {
            client.put("id", randomValue(12));
            client.put("name", "Legacy companion");
            client.put("tokenHash", sha256(legacyToken));
            client.put("createdAt", now);
            client.put("lastUsedAt", now);
            clients.put(client);
            preferences.edit()
                    .putString(KEY_CLIENTS, clients.toString())
                    .remove(KEY_LEGACY_TOKEN)
                    .apply();
        } catch (JSONException impossible) {
            throw new IllegalStateException("Unable to migrate pairing token", impossible);
        }
    }

    private JSONArray readClients() {
        String serialized = preferences.getString(KEY_CLIENTS, "[]");
        try {
            return new JSONArray(serialized);
        } catch (JSONException error) {
            return new JSONArray();
        }
    }

    private void writeClients(JSONArray clients) {
        if (!preferences.edit().putString(KEY_CLIENTS, clients.toString()).commit()) {
            throw new IllegalStateException("Unable to persist paired clients");
        }
    }

    private String randomValue(int byteCount) {
        byte[] bytes = new byte[byteCount];
        random.nextBytes(bytes);
        return Base64.encodeToString(
                bytes,
                Base64.NO_WRAP | Base64.NO_PADDING | Base64.URL_SAFE);
    }

    private static boolean constantTimeEquals(String first, String second) {
        return MessageDigest.isEqual(
                first.getBytes(StandardCharsets.UTF_8),
                second.getBytes(StandardCharsets.UTF_8));
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
