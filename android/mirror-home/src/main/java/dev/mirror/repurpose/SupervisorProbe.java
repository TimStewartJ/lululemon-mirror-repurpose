package dev.mirror.repurpose;

import android.content.Context;
import android.content.pm.PackageInfo;
import android.content.pm.PackageManager;

import org.json.JSONException;
import org.json.JSONObject;

import java.io.IOException;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.Socket;

/**
 * Checks from the outside that the optional OTA supervisor is accepting
 * connections. The supervisor is a separate app that only answers signed
 * requests, so this is the one sign of its health an ordinary paired client
 * can see. Each check blocks briefly and must not run on the main thread.
 */
final class SupervisorProbe {
    static final String PACKAGE_NAME = "dev.mirror.repurpose.updater";
    static final int PORT = 8791;

    private static final int CONNECT_TIMEOUT_MS = 500;

    private static long checks;
    private static long failures;
    private static long lastCheckedAt;
    private static long lastListeningAt;
    private static long unreachableSince;

    private SupervisorProbe() {
    }

    static synchronized JSONObject check(Context context) throws JSONException {
        JSONObject result = new JSONObject();
        PackageInfo info;
        try {
            info = context.getPackageManager().getPackageInfo(PACKAGE_NAME, 0);
        } catch (PackageManager.NameNotFoundException absent) {
            // A supervisor that was removed is not one that has fallen silent.
            unreachableSince = 0L;
            return result.put("installed", false);
        }
        long now = System.currentTimeMillis();
        boolean listening = accepting();
        checks++;
        lastCheckedAt = now;
        if (listening) {
            lastListeningAt = now;
            unreachableSince = 0L;
        } else {
            failures++;
            if (unreachableSince == 0L) {
                unreachableSince = now;
            }
        }
        return result
                .put("installed", true)
                .put("versionName", info.versionName == null ? "" : info.versionName)
                .put("hold", SupervisorHold.getInstance(context).snapshot())
                .put("listening", listening)
                .put("checks", checks)
                .put("failures", failures)
                .put("lastCheckedAt", lastCheckedAt)
                .put("lastListeningAt", lastListeningAt == 0L ? JSONObject.NULL : lastListeningAt)
                .put(
                        "unreachableSince",
                        unreachableSince == 0L ? JSONObject.NULL : unreachableSince);
    }

    /**
     * For how long an installed supervisor has not been answering, as of the
     * last look; 0 if it answered then, is not installed, or was never looked for.
     */
    static synchronized long silentForMs(long now) {
        return unreachableSince == 0L ? 0L : Math.max(0L, now - unreachableSince);
    }

    private static boolean accepting() {
        try (Socket socket = new Socket()) {
            socket.connect(
                    new InetSocketAddress(InetAddress.getLoopbackAddress(), PORT),
                    CONNECT_TIMEOUT_MS);
            return true;
        } catch (IOException | RuntimeException error) {
            return false;
        }
    }
}
