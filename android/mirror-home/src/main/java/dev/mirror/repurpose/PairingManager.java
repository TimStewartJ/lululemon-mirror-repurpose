package dev.mirror.repurpose;

import android.content.Context;
import android.content.SharedPreferences;
import android.os.SystemClock;
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

    /** What became of one attempt to pair with a code. */
    public static final class PairingAttempt {
        public enum Outcome { PAIRED, CLOSED, LOCKED, WRONG_CODE, FULL }

        public final Outcome outcome;
        public final PairingResult result;
        public final long retryAfterSeconds;

        private PairingAttempt(Outcome outcome, PairingResult result, long retryAfterSeconds) {
            this.outcome = outcome;
            this.result = result;
            this.retryAfterSeconds = retryAfterSeconds;
        }
    }

    /** A code a paired client may hand to a new device. */
    public static final class PairingWindow {
        public final String code;
        public final long expiresInSeconds;

        PairingWindow(String code, long expiresInSeconds) {
            this.code = code;
            this.expiresInSeconds = expiresInSeconds;
        }
    }

    private static volatile PairingManager instance;

    private static final String PREFERENCES = "mirror_home";
    private static final String KEY_LEGACY_TOKEN = "pairing_token";
    private static final String KEY_CLIENTS = "paired_clients_v2";
    private static final long CODE_LIFETIME_MS = 10 * 60 * 1000L;
    private static final long LAST_USED_WRITE_INTERVAL_MS = 60 * 60 * 1000L;
    private static final int MAX_CLIENTS = 32;

    private final SharedPreferences preferences;
    private final SecureRandom random = new SecureRandom();
    private final PairingGate gate = new PairingGate();
    private String code;
    private long codeExpiresAt;

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

    /**
     * The code for a surface that is showing it right now. Showing a code is
     * what opens pairing, so only something a person can read may call this.
     */
    public synchronized String displayCode() {
        long now = SystemClock.elapsedRealtime();
        gate.displayed(now);
        return currentCode(now);
    }

    /** The code if something is showing it, otherwise null. */
    public synchronized String codeOnDisplay() {
        long now = SystemClock.elapsedRealtime();
        return gate.onDisplay(now) ? currentCode(now) : null;
    }

    /** Whether a correct code would be accepted right now. */
    public synchronized boolean isOpen() {
        return gate.state(SystemClock.elapsedRealtime()) == PairingGate.State.OPEN;
    }

    /** A paired client asks for a fresh code to give to a new device. */
    public synchronized PairingWindow openWindow() {
        long now = SystemClock.elapsedRealtime();
        gate.reset();
        code = null;
        String fresh = currentCode(now);
        gate.displayedThrough(codeExpiresAt);
        return new PairingWindow(fresh, (codeExpiresAt - now) / 1000L);
    }

    public synchronized PairingAttempt pair(String candidate, String requestedName) {
        long now = SystemClock.elapsedRealtime();
        PairingGate.State state = gate.state(now);
        if (state == PairingGate.State.LOCKED) {
            return new PairingAttempt(
                    PairingAttempt.Outcome.LOCKED,
                    null,
                    (gate.lockedForMillis(now) + 999L) / 1000L);
        }
        if (state == PairingGate.State.CLOSED) {
            return new PairingAttempt(PairingAttempt.Outcome.CLOSED, null, 0L);
        }
        boolean matches = candidate != null && MessageDigest.isEqual(
                currentCode(now).getBytes(StandardCharsets.UTF_8),
                candidate.getBytes(StandardCharsets.UTF_8));
        if (!matches) {
            gate.recordWrongCode(now);
            return new PairingAttempt(PairingAttempt.Outcome.WRONG_CODE, null, 0L);
        }

        PairingResult result = issueTrustedClient(requestedName);
        if (result == null) {
            return new PairingAttempt(PairingAttempt.Outcome.FULL, null, 0L);
        }
        // Codes are single-use: a surface still showing one gets a new code.
        code = null;
        gate.reset();
        gate.close();
        return new PairingAttempt(PairingAttempt.Outcome.PAIRED, result, 0L);
    }

    /** Pairing attempts the gate has seen, for the health report. */
    public synchronized JSONObject securitySnapshot() throws JSONException {
        long now = SystemClock.elapsedRealtime();
        long lastWrongCodeAt = gate.lastWrongCodeAt();
        return new JSONObject()
                .put("open", gate.state(now) == PairingGate.State.OPEN)
                .put("lockedForSeconds", (gate.lockedForMillis(now) + 999L) / 1000L)
                .put("wrongCodes", gate.wrongCodes())
                .put(
                        "lastWrongCodeAgeSeconds",
                        lastWrongCodeAt < 0
                                ? JSONObject.NULL
                                : Long.valueOf((now - lastWrongCodeAt) / 1000L))
                .put("clients", readClients().length());
    }

    private String currentCode(long now) {
        if (code == null || now >= codeExpiresAt) {
            code = String.format(Locale.US, "%06d", random.nextInt(1_000_000));
            codeExpiresAt = now + CODE_LIFETIME_MS;
        }
        return code;
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
        return authenticatedClient(token) != null;
    }

    /** The paired device this token belongs to, by the name it gave; null when there is none. */
    public synchronized String clientName(String token) {
        JSONObject client = authenticatedClient(token);
        return client == null ? null : client.optString("name", "");
    }

    private JSONObject authenticatedClient(String token) {
        if (token == null) {
            return null;
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
            return client;
        }
        return null;
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
        gate.close();
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
