package dev.mirror.repurpose;

import android.content.Context;
import android.content.SharedPreferences;
import android.util.Base64;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.util.Locale;

public final class PairingManager {
    private static volatile PairingManager instance;

    private static final String PREFERENCES = "mirror_home";
    private static final String KEY_TOKEN = "pairing_token";
    private static final long CODE_LIFETIME_MS = 10 * 60 * 1000L;
    private static final long FAILURE_LOCKOUT_MS = 30 * 1000L;
    private static final int MAX_FAILURES = 5;

    private final SharedPreferences preferences;
    private final SecureRandom random = new SecureRandom();
    private String code;
    private long codeExpiresAt;
    private int failures;
    private long lockedUntil;

    private PairingManager(Context context) {
        preferences = context.getApplicationContext()
                .getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE);
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

        byte[] tokenBytes = new byte[32];
        random.nextBytes(tokenBytes);
        String token = Base64.encodeToString(
                tokenBytes,
                Base64.NO_WRAP | Base64.NO_PADDING | Base64.URL_SAFE);
        preferences.edit().putString(KEY_TOKEN, token).apply();
        code = null;
        return token;
    }

    public boolean authenticate(String token) {
        String expected = preferences.getString(KEY_TOKEN, null);
        return expected != null
                && token != null
                && MessageDigest.isEqual(
                        expected.getBytes(StandardCharsets.UTF_8),
                        token.getBytes(StandardCharsets.UTF_8));
    }

    public boolean isPaired() {
        return preferences.contains(KEY_TOKEN);
    }

    public void revoke() {
        preferences.edit().remove(KEY_TOKEN).apply();
        synchronized (this) {
            code = null;
        }
    }
}
