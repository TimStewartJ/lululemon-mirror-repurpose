package dev.mirror.repurpose;

import org.json.JSONException;
import org.json.JSONObject;

final class DashboardDiagnostics {
    private static volatile String phase = "not-created";
    private static volatile String url = "";
    private static volatile String detail = "";
    private static volatile String pageProbe = "";
    private static volatile boolean pageComplete;
    private static volatile boolean rendererPresent;
    private static volatile String lastFailurePhase = "";
    private static volatile String lastFailureUrl = "";
    private static volatile String lastFailureDetail = "";
    private static volatile int launchAttempts;
    private static volatile String launchDetail = "";

    private DashboardDiagnostics() {
    }

    static void record(String nextPhase, String nextUrl, String nextDetail) {
        phase = nextPhase;
        url = nextUrl == null ? "" : nextUrl;
        detail = nextDetail == null ? "" : nextDetail;
        pageProbe = "";
        pageComplete = false;
        rendererPresent = false;
        if ("loading".equals(nextPhase) || "retrying".equals(nextPhase)) {
            lastFailurePhase = "";
            lastFailureUrl = "";
            lastFailureDetail = "";
        }
    }

    static void recordFailure(String nextPhase, String nextUrl, String nextDetail) {
        record(nextPhase, nextUrl, nextDetail);
        lastFailurePhase = phase;
        lastFailureUrl = url;
        lastFailureDetail = detail;
    }

    static void recordPageProbe(String value) {
        pageProbe = value == null ? "" : value;
        pageComplete = pageProbe.contains("complete|");
        rendererPresent = pageProbe.endsWith("|object\"");
    }

    static boolean activityCreated() {
        return !"not-created".equals(phase);
    }

    static void recordLaunchAttempt(int attempts, String nextDetail) {
        launchAttempts = attempts;
        launchDetail = nextDetail == null ? "" : nextDetail;
    }

    static JSONObject snapshot() throws JSONException {
        return new JSONObject()
                .put("phase", phase)
                .put("url", url)
                .put("detail", detail)
                .put("pageProbe", pageProbe)
                .put("pageComplete", pageComplete)
                .put("rendererPresent", rendererPresent)
                .put("lastFailurePhase", lastFailurePhase)
                .put("lastFailureUrl", lastFailureUrl)
                .put("lastFailureDetail", lastFailureDetail)
                .put("launchAttempts", launchAttempts)
                .put("launchDetail", launchDetail);
    }

    static JSONObject publicSnapshot() throws JSONException {
        return new JSONObject()
                .put("phase", phase)
                .put("pageComplete", pageComplete)
                .put("rendererPresent", rendererPresent)
                .put("hasLoadFailure", !lastFailurePhase.isEmpty())
                .put("launchAttempts", launchAttempts);
    }
}
