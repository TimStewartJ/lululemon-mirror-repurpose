package dev.mirror.repurpose;

import android.content.Context;
import android.content.Intent;
import android.net.wifi.WifiInfo;
import android.net.wifi.WifiManager;
import android.os.SystemClock;
import android.util.Log;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.util.HashMap;
import java.util.Iterator;
import java.util.Map;

import fi.iki.elonen.NanoHTTPD;

public final class ControlServer extends NanoHTTPD {
    private static final String TAG = "ControlServer";
    private static final int MAX_BODY_BYTES = 64 * 1024;
    private static final int UPCOMING_OFFSET_CHANGES = 8;

    private final Context context;
    private final BackgroundVideoLibrary backgroundVideos;
    private final BackgroundVideoProvisioner backgroundVideoProvisioner;
    private final ConfigStore configStore;
    private final AutomationManager automation;
    private final MediaPlaybackManager media;
    private final MirrorBinderClient mirror;
    private final NoteStore notes;
    private final PairingManager pairing;
    private final PhotoLibrary photos;
    private final SystemHelperClient systemHelper;
    private final WeatherProvider weather;
    private final WifiProvisioner wifi;
    private final WifiDirectOnboarding wifiDirect;

    public ControlServer(Context context, int port) {
        super(port);
        this.context = context.getApplicationContext();
        backgroundVideos = BackgroundVideoLibrary.getInstance(context);
        configStore = new ConfigStore(context);
        automation = AutomationManager.getInstance(context);
        media = MediaPlaybackManager.getInstance(context);
        mirror = MirrorBinderClient.getInstance(context);
        notes = NoteStore.getInstance(context);
        pairing = PairingManager.getInstance(context);
        backgroundVideoProvisioner = new BackgroundVideoProvisioner(context, pairing);
        photos = new PhotoLibrary(context);
        systemHelper = SystemHelperClient.getInstance(context);
        weather = WeatherProvider.getInstance(context);
        wifi = new WifiProvisioner(context);
        wifiDirect = WifiDirectOnboarding.getInstance(context);
    }

    @Override
    public Response serve(IHTTPSession session) {
        if (Method.OPTIONS.equals(session.getMethod())) {
            return error(Response.Status.METHOD_NOT_ALLOWED, "Cross-origin requests are disabled");
        }

        String uri = session.getUri();
        try {
            if (Method.GET.equals(session.getMethod())
                    && "/api/v1/photos/slideshow".equals(uri)
                    && isLoopback(session)) {
                return response(
                        Response.Status.OK,
                        new JSONObject().put("photos", photos.list()));
            }
            if (Method.GET.equals(session.getMethod())
                    && uri.startsWith("/photos/")
                    && isLoopback(session)) {
                String rest = uri.substring("/photos/".length());
                if (rest.endsWith("/thumbnail")) {
                    return serveScaledPhoto(
                            rest.substring(0, rest.length() - "/thumbnail".length()),
                            PhotoLibrary.THUMBNAIL_EDGE);
                }
                if (rest.endsWith("/display")) {
                    return serveScaledPhoto(
                            rest.substring(0, rest.length() - "/display".length()),
                            PhotoLibrary.DISPLAY_EDGE);
                }
                return servePhoto(rest);
            }
            if (Method.GET.equals(session.getMethod())
                    && "/api/v1/dashboard/runtime".equals(uri)
                    && isLoopback(session)) {
                return response(Response.Status.OK, dashboardRuntime());
            }
            if (Method.GET.equals(session.getMethod())
                    && "/api/v1/dashboard/layout".equals(uri)
                    && isLoopback(session)) {
                return response(
                        Response.Status.OK,
                        configStore.getDashboardLayout().toJson());
            }
            if (Method.GET.equals(session.getMethod())
                    && "/api/v1/notes".equals(uri)
                    && isLoopback(session)) {
                return response(Response.Status.OK, notesDocument());
            }
            if (Method.GET.equals(session.getMethod())
                    && "/api/v1/dashboard/ambient-video".equals(uri)) {
                return response(
                        Response.Status.OK,
                        ambientVideoStatus(isLoopback(session) || authorized(session)));
            }
            if (Method.GET.equals(session.getMethod())
                    && "/api/v1/background-videos/bootstrap".equals(uri)) {
                return response(
                        Response.Status.OK,
                        new JSONObject().put(
                                "available",
                                backgroundVideoProvisioner.available()));
            }
            if (Method.POST.equals(session.getMethod())
                    && "/api/v1/background-videos/bootstrap".equals(uri)) {
                return provisionBackgroundVideoClient(session);
            }
            if (Method.GET.equals(session.getMethod())) {
                Response asset = controlAsset(uri);
                if (asset != null) {
                    return asset;
                }
            }
            if (Method.GET.equals(session.getMethod()) && "/api/v1/bootstrap".equals(uri)) {
                return response(Response.Status.OK, bootstrap());
            }
            if (Method.GET.equals(session.getMethod())
                    && "/api/v1/status".equals(uri)
                    && (isLoopback(session) || authorized(session))) {
                return response(Response.Status.OK, status());
            }
            if (Method.GET.equals(session.getMethod())
                    && "/api/v1/health".equals(uri)
                    && (isLoopback(session) || authorized(session))) {
                return response(
                        Response.Status.OK,
                        HealthReport.build(context, configStore, pairing, automation));
            }
            if (Method.POST.equals(session.getMethod()) && "/api/v1/pair".equals(uri)) {
                return pair(readJson(session));
            }
            if (!authorized(session)) {
                return error(Response.Status.UNAUTHORIZED, "Authentication required");
            }
            if (Method.POST.equals(session.getMethod())
                    && "/api/v1/background-videos/bootstrap/confirm".equals(uri)) {
                try {
                    boolean confirmed =
                            backgroundVideoProvisioner.confirm(bearerToken(session));
                    return confirmed
                            ? response(
                                    Response.Status.OK,
                                    new JSONObject().put("confirmed", true))
                            : error(
                                    Response.Status.FORBIDDEN,
                                    "Background video bootstrap confirmation failed");
                } catch (IllegalStateException error) {
                    return error(Response.Status.INTERNAL_ERROR, error.getMessage());
                }
            }
            if (Method.GET.equals(session.getMethod())
                    && "/api/v1/background-videos".equals(uri)) {
                return response(Response.Status.OK, backgroundVideos.document());
            }
            if (Method.PUT.equals(session.getMethod())
                    && uri.startsWith("/api/v1/background-videos/upload/")) {
                return uploadBackgroundVideo(
                        session,
                        uri.substring("/api/v1/background-videos/upload/".length()));
            }
            if (Method.POST.equals(session.getMethod())
                    && "/api/v1/background-videos/rollback".equals(uri)) {
                return rollbackBackgroundVideo();
            }
            if (Method.PUT.equals(session.getMethod())
                    && "/api/v1/background-videos/schedule".equals(uri)) {
                return updateBackgroundVideoSchedule(readJson(session));
            }
            if (Method.POST.equals(session.getMethod())
                    && "/api/v1/background-videos/schedule/resume".equals(uri)) {
                return resumeBackgroundVideoSchedule();
            }
            if (Method.POST.equals(session.getMethod())
                    && uri.startsWith("/api/v1/background-videos/")
                    && uri.endsWith("/activate")) {
                return activateBackgroundVideo(uri.substring(
                        "/api/v1/background-videos/".length(),
                        uri.length() - "/activate".length()));
            }
            if (Method.DELETE.equals(session.getMethod())
                    && uri.startsWith("/api/v1/background-videos/")) {
                return deleteBackgroundVideo(
                        uri.substring("/api/v1/background-videos/".length()));
            }
            if (Method.GET.equals(session.getMethod())
                    && uri.startsWith("/api/v1/background-videos/")
                    && uri.endsWith("/poster")) {
                return serveBackgroundVideoPoster(uri.substring(
                        "/api/v1/background-videos/".length(),
                        uri.length() - "/poster".length()));
            }
            if (Method.GET.equals(session.getMethod()) && "/api/v1/dashboard".equals(uri)) {
                return response(
                        Response.Status.OK,
                        new JSONObject().put("url", configStore.getDashboardUrl()));
            }
            if (Method.PUT.equals(session.getMethod()) && "/api/v1/dashboard".equals(uri)) {
                return updateDashboard(readJson(session));
            }
            if (Method.GET.equals(session.getMethod())
                    && "/api/v1/dashboard/layout".equals(uri)) {
                return response(
                        Response.Status.OK,
                        configStore.getDashboardLayout().toJson());
            }
            if (Method.PUT.equals(session.getMethod())
                    && "/api/v1/dashboard/layout".equals(uri)) {
                return updateDashboardLayout(readJson(session));
            }
            if (Method.POST.equals(session.getMethod())
                    && "/api/v1/dashboard/layout/validate".equals(uri)) {
                return response(
                        Response.Status.OK,
                        DashboardLayoutConfig.parse(readJson(session)).toJson());
            }
            if (Method.POST.equals(session.getMethod())
                    && "/api/v1/dashboard/layout/reset".equals(uri)) {
                configStore.resetDashboardLayout();
                notifyConfigurationChanged();
                return response(
                        Response.Status.OK,
                        configStore.getDashboardLayout().toJson());
            }
            if (Method.GET.equals(session.getMethod()) && "/api/v1/notes".equals(uri)) {
                return response(Response.Status.OK, notesDocument());
            }
            if (Method.POST.equals(session.getMethod()) && "/api/v1/notes".equals(uri)) {
                return addNote(readJson(session));
            }
            if (Method.PUT.equals(session.getMethod()) && uri.startsWith("/api/v1/notes/")) {
                return updateNote(uri.substring("/api/v1/notes/".length()), readJson(session));
            }
            if (Method.DELETE.equals(session.getMethod())
                    && uri.startsWith("/api/v1/notes/")) {
                boolean deleted = notes.delete(uri.substring("/api/v1/notes/".length()));
                return response(
                        deleted ? Response.Status.OK : Response.Status.NOT_FOUND,
                        new JSONObject().put("deleted", deleted).put("version", notes.version()));
            }
            if (Method.GET.equals(session.getMethod()) && "/api/v1/preferences".equals(uri)) {
                return response(Response.Status.OK, preferences());
            }
            if (Method.PUT.equals(session.getMethod()) && "/api/v1/preferences".equals(uri)) {
                return updatePreferences(readJson(session));
            }
            if (Method.GET.equals(session.getMethod()) && "/api/v1/automation".equals(uri)) {
                return response(Response.Status.OK, automation.snapshot());
            }
            if (Method.PUT.equals(session.getMethod()) && "/api/v1/automation".equals(uri)) {
                return updateAutomation(readJson(session));
            }
            if (Method.POST.equals(session.getMethod())
                    && "/api/v1/automation/sleep".equals(uri)) {
                automation.setManualSleeping(true);
                return response(Response.Status.OK, automation.snapshot());
            }
            if (Method.POST.equals(session.getMethod())
                    && "/api/v1/automation/wake".equals(uri)) {
                automation.setManualSleeping(false);
                return response(Response.Status.OK, automation.snapshot());
            }
            if (Method.GET.equals(session.getMethod()) && "/api/v1/weather".equals(uri)) {
                return response(Response.Status.OK, weather.snapshot(true));
            }
            if (Method.GET.equals(session.getMethod())
                    && "/api/v1/weather/locations".equals(uri)) {
                java.util.List<String> values = session.getParameters().get("q");
                String query = values == null || values.isEmpty() ? "" : values.get(0);
                return response(Response.Status.OK, weather.searchLocations(query));
            }
            if (Method.PUT.equals(session.getMethod()) && "/api/v1/weather".equals(uri)) {
                weather.update(readJson(session));
                notifyConfigurationChanged();
                return response(Response.Status.OK, weather.snapshot(true));
            }
            if (Method.POST.equals(session.getMethod())
                    && "/api/v1/weather/refresh".equals(uri)) {
                weather.refreshNow();
                return response(Response.Status.ACCEPTED, weather.snapshot(true));
            }
            if (Method.POST.equals(session.getMethod())
                    && "/api/v1/wifi/configure".equals(uri)) {
                return configureWifi(readJson(session));
            }
            if (Method.GET.equals(session.getMethod())
                    && "/api/v1/onboarding".equals(uri)) {
                return response(Response.Status.OK, onboardingStatus());
            }
            if (Method.POST.equals(session.getMethod())
                    && "/api/v1/onboarding/start".equals(uri)) {
                wifiDirect.start();
                return response(Response.Status.ACCEPTED, onboardingStatus());
            }
            if (Method.POST.equals(session.getMethod())
                    && "/api/v1/onboarding/stop".equals(uri)) {
                wifiDirect.stop();
                return response(Response.Status.OK, onboardingStatus());
            }
            if (Method.GET.equals(session.getMethod()) && "/api/v1/photos".equals(uri)) {
                return response(
                        Response.Status.OK,
                        new JSONObject().put("photos", photos.list()));
            }
            if (Method.GET.equals(session.getMethod())
                    && uri.startsWith("/api/v1/photos/")
                    && uri.endsWith("/thumbnail")) {
                return serveScaledPhoto(uri.substring(
                        "/api/v1/photos/".length(),
                        uri.length() - "/thumbnail".length()),
                        PhotoLibrary.THUMBNAIL_EDGE);
            }
            if (Method.GET.equals(session.getMethod())
                    && uri.startsWith("/api/v1/photos/")
                    && uri.endsWith("/display")) {
                return serveScaledPhoto(uri.substring(
                        "/api/v1/photos/".length(),
                        uri.length() - "/display".length()),
                        PhotoLibrary.DISPLAY_EDGE);
            }
            if (Method.GET.equals(session.getMethod()) && uri.startsWith("/api/v1/photos/")) {
                return servePhoto(uri.substring("/api/v1/photos/".length()));
            }
            if (Method.PUT.equals(session.getMethod()) && uri.startsWith("/api/v1/photos/")) {
                return uploadPhoto(session, uri.substring("/api/v1/photos/".length()));
            }
            if (Method.DELETE.equals(session.getMethod()) && uri.startsWith("/api/v1/photos/")) {
                boolean deleted = photos.delete(uri.substring("/api/v1/photos/".length()));
                return response(
                        deleted ? Response.Status.OK : Response.Status.NOT_FOUND,
                        new JSONObject().put("deleted", deleted));
            }
            if (Method.POST.equals(session.getMethod())
                    && "/api/v1/control/brightness".equals(uri)) {
                return updateBrightness(readJson(session));
            }
            if (Method.POST.equals(session.getMethod())
                    && "/api/v1/control/name".equals(uri)) {
                return updateName(readJson(session));
            }
            if (Method.GET.equals(session.getMethod()) && "/api/v1/media/status".equals(uri)) {
                return response(Response.Status.OK, media.snapshot());
            }
            if (Method.POST.equals(session.getMethod()) && "/api/v1/media/play".equals(uri)) {
                return playMedia(readJson(session));
            }
            if (Method.POST.equals(session.getMethod()) && "/api/v1/media/pause".equals(uri)) {
                media.pause();
                return response(Response.Status.ACCEPTED, media.snapshot());
            }
            if (Method.POST.equals(session.getMethod()) && "/api/v1/media/resume".equals(uri)) {
                media.resume();
                return response(Response.Status.ACCEPTED, media.snapshot());
            }
            if (Method.POST.equals(session.getMethod()) && "/api/v1/media/stop".equals(uri)) {
                media.stop();
                return response(Response.Status.ACCEPTED, media.snapshot());
            }
            if (Method.POST.equals(session.getMethod()) && "/api/v1/media/seek".equals(uri)) {
                return seekMedia(readJson(session));
            }
            if (Method.POST.equals(session.getMethod()) && "/api/v1/media/volume".equals(uri)) {
                return setMediaVolume(readJson(session));
            }
            if (Method.GET.equals(session.getMethod()) && "/api/v1/system".equals(uri)) {
                return systemStatus();
            }
            if (Method.POST.equals(session.getMethod())
                    && "/api/v1/system/prepare-kiosk".equals(uri)) {
                return prepareKiosk();
            }
            if (Method.POST.equals(session.getMethod())
                    && "/api/v1/system/home".equals(uri)) {
                return setHome(readJson(session));
            }
            if (Method.GET.equals(session.getMethod()) && "/api/v1/clients".equals(uri)) {
                return response(
                        Response.Status.OK,
                        new JSONObject().put("clients", pairing.clients()));
            }
            if (Method.POST.equals(session.getMethod())
                    && "/api/v1/clients/revoke".equals(uri)) {
                String clientId = readJson(session).optString("id", "");
                boolean revoked = pairing.revokeClient(clientId);
                return response(
                        revoked ? Response.Status.OK : Response.Status.NOT_FOUND,
                        new JSONObject().put("revoked", revoked));
            }
            if (Method.POST.equals(session.getMethod()) && "/api/v1/pair/window".equals(uri)) {
                return openPairingWindow();
            }
            if (Method.POST.equals(session.getMethod()) && "/api/v1/pair/revoke".equals(uri)) {
                boolean revoked = pairing.revokeToken(bearerToken(session));
                return response(
                        revoked ? Response.Status.OK : Response.Status.NOT_FOUND,
                        new JSONObject().put("revoked", revoked));
            }
            return error(Response.Status.NOT_FOUND, "Endpoint not found");
        } catch (JSONException error) {
            return error(Response.Status.BAD_REQUEST, "Invalid JSON request");
        } catch (IOException | ResponseException error) {
            return error(Response.Status.BAD_REQUEST, "Unable to read request");
        } catch (RuntimeException error) {
            // Answer instead of dropping the connection, and keep the evidence.
            Log.e(TAG, "Unhandled error serving " + uri, error);
            ApiDiagnostics.recordUnhandled(session.getMethod().name(), uri, error);
            return error(Response.Status.INTERNAL_ERROR, "Mirror Home could not complete the request");
        }
    }

    private JSONObject status() throws JSONException {
        WifiManager manager =
                (WifiManager) context.getSystemService(Context.WIFI_SERVICE);
        WifiInfo info = manager == null ? null : manager.getConnectionInfo();
        JSONObject wifiStatus = new JSONObject();
        if (info != null && info.getNetworkId() >= 0) {
            wifiStatus.put("connected", true);
            wifiStatus.put("ssid", WifiProvisioner.cleanSsid(info.getSSID()));
            wifiStatus.put("ipAddress", WifiProvisioner.ipAddress(info.getIpAddress()));
        } else {
            wifiStatus.put("connected", false);
        }

        long now = System.currentTimeMillis();
        UtcOffsetTimeline clock = configStore.getUtcOffsetTimeline();
        JSONObject result = new JSONObject();
        result.put("apiVersion", 1);
        result.put("appVersion", BuildConfig.VERSION_NAME);
        result.put("deviceUptimeSeconds", SystemClock.elapsedRealtime() / 1000L);
        result.put("paired", pairing.isPaired());
        result.put("displayName", configStore.getDisplayName());
        result.put("timeZone", configStore.getTimeZoneId());
        result.put("utcOffsetMinutes", clock.offsetMinutesAt(now));
        result.put("nextUtcOffsetChange", nextUtcOffsetChange(clock, now));
        result.put("clock24Hour", configStore.isClock24Hour());
        result.put("mirrorBinderConnected", mirror.isConnected());
        result.put("systemHelperConnected", systemHelper.isConnected());
        Integer brightness = automation.isSleeping()
                ? Integer.valueOf(0)
                : mirror.getBrightness();
        result.put("brightness", brightness == null ? JSONObject.NULL : brightness);
        result.put("wifi", wifiStatus);
        result.put("media", media.snapshot());
        result.put("ambientVideo", media.ambientSnapshot());
        result.put("backgroundVideos", backgroundVideos.selectionSnapshot());
        result.put("automation", automation.snapshot());
        result.put("weather", weather.snapshot(false));
        result.put("notesVersion", notes.version());
        return result;
    }

    private JSONObject bootstrap() throws JSONException {
        return new JSONObject()
                .put("apiVersion", 1)
                .put("appVersion", BuildConfig.VERSION_NAME)
                .put("displayName", configStore.getDisplayName())
                .put("paired", pairing.isPaired())
                .put("pairingOpen", pairing.isOpen());
    }

    private JSONObject dashboardRuntime() throws JSONException {
        JSONObject runtime = status();
        // The built-in dashboard polls this to draw its pairing widget, which
        // is what puts the code on the glass and so opens pairing.
        boolean widgetShown = configStore.getDashboardUrl().isEmpty()
                && configStore.getDashboardLayout().showsWidget("pairing");
        String code = widgetShown ? pairing.displayCode() : pairing.codeOnDisplay();
        if (code != null) {
            runtime.put("pairingCode", code);
        }
        runtime.put("controlUrl", controlUrl());
        return runtime;
    }

    private Response openPairingWindow() throws JSONException {
        PairingManager.PairingWindow window = pairing.openWindow();
        return response(
                Response.Status.OK,
                new JSONObject()
                        .put("code", window.code)
                        .put("expiresInSeconds", window.expiresInSeconds)
                        .put(
                                "expiresAt",
                                System.currentTimeMillis() + window.expiresInSeconds * 1000L));
    }

    private static Response pairingError(
            Response.Status status,
            String reason,
            String message,
            long retryAfterSeconds) throws JSONException {
        JSONObject body = new JSONObject().put("error", message).put("reason", reason);
        if (retryAfterSeconds > 0) {
            body.put("retryAfterSeconds", retryAfterSeconds);
        }
        Response result = response(status, body);
        if (retryAfterSeconds > 0) {
            result.addHeader("Retry-After", Long.toString(retryAfterSeconds));
        }
        return result;
    }

    static String waitDescription(long seconds) {
        if (seconds < 90) {
            return seconds + (seconds == 1 ? " second" : " seconds");
        }
        long minutes = (seconds + 59) / 60;
        return minutes + " minutes";
    }

    private JSONObject ambientVideoStatus(boolean fullDiagnostics) throws JSONException {
        JSONObject video = media.ambientSnapshot();
        if (!fullDiagnostics) {
            video = new JSONObject()
                    .put("enabled", video.optBoolean("enabled"))
                    .put("state", video.optString("state"))
                    .put("playing", video.optBoolean("playing"))
                    .put("firstFrameRendered", video.optBoolean("firstFrameRendered"))
                    .put("width", video.optInt("width"))
                    .put("height", video.optInt("height"))
                    .put("frameRate", video.optDouble("frameRate"))
                    .put("droppedFrames", video.optInt("droppedFrames"))
                    .put("droppedFramePercent", video.optDouble("droppedFramePercent"));
        }
        return new JSONObject()
                .put("video", video)
                .put(
                        "selection",
                        fullDiagnostics
                                ? backgroundVideos.selectionSnapshot()
                                : backgroundVideos.publicSelectionSnapshot())
                .put(
                        "dashboard",
                        fullDiagnostics
                                ? DashboardDiagnostics.snapshot()
                                : DashboardDiagnostics.publicSnapshot());
    }

    private Response provisionBackgroundVideoClient(IHTTPSession session)
            throws JSONException {
        if (!backgroundVideoProvisioner.available()) {
            return error(
                    Response.Status.CONFLICT,
                    "Background video bootstrap is unavailable or already used");
        }
        PairingManager.PairingResult result;
        try {
            result = backgroundVideoProvisioner.provision(
                    session.getHeaders().get("x-background-video-bootstrap"));
        } catch (IllegalStateException error) {
            return error(Response.Status.INTERNAL_ERROR, error.getMessage());
        }
        if (result == null) {
            return error(
                    Response.Status.UNAUTHORIZED,
                    "Invalid background video bootstrap token");
        }
        return response(
                Response.Status.OK,
                new JSONObject()
                        .put("token", result.token)
                        .put("clientId", result.clientId)
                        .put("clientName", result.clientName));
    }

    private Response pair(JSONObject body) throws JSONException {
        PairingManager.PairingAttempt attempt = pairing.pair(
                body.optString("code", null),
                body.optString("name", "Device"));
        switch (attempt.outcome) {
            case CLOSED:
                return pairingError(
                        Response.Status.FORBIDDEN,
                        "closed",
                        "The mirror is not showing a pairing code. On a paired device, "
                                + "open Settings > Paired devices and choose Show code.",
                        0L);
            case LOCKED:
                return pairingError(
                        Response.Status.TOO_MANY_REQUESTS,
                        "locked",
                        "Too many wrong codes. Try again in "
                                + waitDescription(attempt.retryAfterSeconds) + ".",
                        attempt.retryAfterSeconds);
            case WRONG_CODE:
                return pairingError(
                        Response.Status.UNAUTHORIZED,
                        "wrong-code",
                        "That code is not right. Check the code and try again.",
                        0L);
            case FULL:
                return pairingError(
                        Response.Status.CONFLICT,
                        "full",
                        "This mirror has as many paired devices as it allows. "
                                + "Revoke one in Settings > Paired devices first.",
                        0L);
            default:
                break;
        }
        PairingManager.PairingResult result = attempt.result;
        String timeZone = body.optString("timeZone", "");
        if (InputValidator.validTimeZone(timeZone)) {
            try {
                saveClock(timeZone, body.optInt("utcOffsetMinutes", 0), body);
            } catch (IllegalArgumentException ignored) {
                // Pairing already succeeded; a malformed clock keeps the saved one.
            }
        }
        notifyConfigurationChanged();
        return response(
                Response.Status.OK,
                new JSONObject()
                        .put("token", result.token)
                        .put("clientId", result.clientId)
                        .put("clientName", result.clientName));
    }

    private Response updateDashboard(JSONObject body) throws JSONException {
        String url = body.optString("url", "");
        if (!InputValidator.validDashboardUrl(url)) {
            return error(Response.Status.BAD_REQUEST, "Dashboard URL must use HTTP or HTTPS");
        }
        configStore.setDashboardUrl(url);
        notifyConfigurationChanged();
        return response(Response.Status.OK, new JSONObject().put("url", url));
    }

    private Response updateDashboardLayout(JSONObject body) throws JSONException {
        DashboardLayoutConfig layout = DashboardLayoutConfig.parse(body);
        configStore.setDashboardLayout(layout);
        notifyConfigurationChanged();
        return response(Response.Status.OK, layout.toJson());
    }

    private JSONObject notesDocument() throws JSONException {
        return new JSONObject()
                .put("notes", notes.list())
                .put("version", notes.version())
                .put("maxLength", NoteBook.MAX_TEXT_LENGTH)
                .put("maxNotes", NoteBook.MAX_NOTES);
    }

    private Response addNote(JSONObject body) throws JSONException {
        try {
            JSONObject note = notes.add(noteText(body));
            return response(
                    Response.Status.CREATED,
                    new JSONObject().put("note", note).put("version", notes.version()));
        } catch (IllegalArgumentException error) {
            return error(Response.Status.BAD_REQUEST, error.getMessage());
        }
    }

    private Response updateNote(String id, JSONObject body) throws JSONException {
        try {
            JSONObject note = notes.update(id, noteText(body));
            if (note == null) {
                return error(Response.Status.NOT_FOUND, "Note not found");
            }
            return response(
                    Response.Status.OK,
                    new JSONObject().put("note", note).put("version", notes.version()));
        } catch (IllegalArgumentException error) {
            return error(Response.Status.BAD_REQUEST, error.getMessage());
        }
    }

    private static String noteText(JSONObject body) {
        return body.isNull("text") ? null : body.optString("text");
    }

    private String controlUrl() {
        WifiManager manager =
                (WifiManager) context.getSystemService(Context.WIFI_SERVICE);
        WifiInfo info = manager == null ? null : manager.getConnectionInfo();
        String address = info == null ? "" : WifiProvisioner.ipAddress(info.getIpAddress());
        return address.isEmpty()
                ? "http://127.0.0.1:" + ControlServerService.PORT + "/"
                : "http://" + address + ":" + ControlServerService.PORT + "/";
    }

    private JSONObject preferences() throws JSONException {
        long now = System.currentTimeMillis();
        UtcOffsetTimeline clock = configStore.getUtcOffsetTimeline();
        return new JSONObject()
                .put("timeZone", configStore.getTimeZoneId())
                .put("utcOffsetMinutes", clock.offsetMinutesAt(now))
                .put("utcOffsetChanges", clock.changesJson(now, UPCOMING_OFFSET_CHANGES))
                .put("clockSource", configStore.getClockSource())
                .put("clock24Hour", configStore.isClock24Hour())
                .put("ambientLightAvailable", automation.hasAmbientLightSensor());
    }

    private Response updatePreferences(JSONObject body) throws JSONException {
        String timeZone = body.optString("timeZone", "");
        if (!InputValidator.validTimeZone(timeZone)) {
            return error(Response.Status.BAD_REQUEST, "Unknown IANA time zone");
        }
        try {
            saveClock(timeZone, body.optInt("utcOffsetMinutes", 0), body);
        } catch (IllegalArgumentException error) {
            return error(Response.Status.BAD_REQUEST, error.getMessage());
        }
        configStore.setClock24Hour(body.optBoolean("clock24Hour", false));
        notifyConfigurationChanged();
        return response(Response.Status.OK, preferences());
    }

    /**
     * Saves a client's zone and offset. A client that also lists the upcoming
     * offset changes is followed exactly; one that knows only the offset in
     * force leaves known changes alone when it agrees with them.
     */
    private void saveClock(String timeZone, int offsetMinutes, JSONObject body) {
        if (!UtcOffsetTimeline.validOffset(offsetMinutes)) {
            throw new IllegalArgumentException("Invalid UTC offset");
        }
        if (body.has("utcOffsetChanges") && !body.isNull("utcOffsetChanges")) {
            JSONArray changes = body.optJSONArray("utcOffsetChanges");
            if (changes == null) {
                throw new IllegalArgumentException("UTC offset changes must be a list");
            }
            configStore.setClock(
                    timeZone,
                    offsetMinutes,
                    UtcOffsetTimeline.parse(offsetMinutes, changes));
            return;
        }
        if (timeZone.equals(configStore.getTimeZoneId())
                && configStore.getUtcOffsetMinutes() == offsetMinutes) {
            return;
        }
        configStore.setClock(timeZone, offsetMinutes, null);
    }

    private static Object nextUtcOffsetChange(UtcOffsetTimeline clock, long now)
            throws JSONException {
        JSONArray next = clock.changesJson(now, 1);
        return next.length() == 0 ? JSONObject.NULL : next.get(0);
    }

    private Response updateAutomation(JSONObject body) throws JSONException {
        if (!automation.update(body)) {
            return error(Response.Status.BAD_REQUEST, "Invalid automation settings");
        }
        return response(Response.Status.OK, automation.snapshot());
    }

    private Response configureWifi(JSONObject body) throws JSONException {
        String ssid = body.optString("ssid", null);
        String passphrase = body.optString("passphrase", null);
        boolean hidden = body.optBoolean("hidden", false);
        WifiProvisioner.Result result = wifi.configure(ssid, passphrase, hidden);
        String ipAddress = result.success ? wifi.awaitIpAddress(15_000L) : "";
        if (result.success) {
            wifiDirect.onProvisioned();
        }
        return response(
                result.success ? Response.Status.ACCEPTED : Response.Status.BAD_REQUEST,
                new JSONObject()
                        .put("accepted", result.success)
                        .put("message", result.message)
                        .put(
                                "ipAddress",
                                ipAddress.isEmpty() ? JSONObject.NULL : ipAddress)
                        .put("apiPort", ControlServerService.PORT));
    }

    private JSONObject onboardingStatus() throws JSONException {
        WifiDirectOnboarding.Snapshot snapshot = wifiDirect.snapshot();
        return new JSONObject()
                .put("state", snapshot.state)
                .put("active", snapshot.active)
                .put("clientConnected", snapshot.clientConnected);
    }

    private Response uploadPhoto(IHTTPSession session, String encodedName)
            throws IOException, ResponseException, JSONException {
        long contentLength;
        try {
            contentLength = Long.parseLong(session.getHeaders().get("content-length"));
        } catch (Exception error) {
            return error(Response.Status.LENGTH_REQUIRED, "Content-Length is required");
        }
        if (contentLength < 1 || contentLength > PhotoLibrary.MAX_PHOTO_BYTES) {
            return error(Response.Status.BAD_REQUEST, "Photo exceeds the 20 MB limit");
        }
        String contentType = session.getHeaders().get("content-type");
        if (contentType == null || !contentType.toLowerCase(java.util.Locale.US).startsWith("image/")) {
            return error(Response.Status.UNSUPPORTED_MEDIA_TYPE, "An image Content-Type is required");
        }
        Map<String, String> files = new HashMap<>();
        session.parseBody(files);
        String temporaryPath = files.get("content");
        if (temporaryPath == null) {
            return error(Response.Status.BAD_REQUEST, "Photo body is missing");
        }
        String storedName = photos.store(encodedName, new File(temporaryPath), contentLength);
        return response(
                Response.Status.CREATED,
                new JSONObject().put("name", storedName));
    }

    private Response uploadBackgroundVideo(IHTTPSession session, String encodedName)
            throws IOException, ResponseException, JSONException {
        long contentLength = contentLength(session);
        if (contentLength < 1 || contentLength > BackgroundVideoLibrary.MAX_VIDEO_BYTES) {
            return error(
                    Response.Status.BAD_REQUEST,
                    "Background video exceeds the 256 MiB limit");
        }
        String contentType = session.getHeaders().get("content-type");
        if (contentType == null
                || !contentType.toLowerCase(java.util.Locale.US).startsWith("video/mp4")) {
            return error(
                    Response.Status.UNSUPPORTED_MEDIA_TYPE,
                    "An MP4 video Content-Type is required");
        }
        try {
            BackgroundVideoLibrary.StoreResult stored = backgroundVideos.store(
                    encodedName,
                    session.getInputStream(),
                    contentLength);
            boolean posterAvailable = backgroundVideos.poster(stored.video.id) != null;
            return response(
                    stored.duplicate ? Response.Status.OK : Response.Status.CREATED,
                    new JSONObject()
                            .put(
                                    "video",
                                    stored.video.toJson(false, false, posterAvailable))
                            .put("duplicate", stored.duplicate));
        } catch (IOException error) {
            return error(Response.Status.BAD_REQUEST, error.getMessage());
        }
    }

    private Response activateBackgroundVideo(String id) throws JSONException {
        try {
            if (!backgroundVideos.activate(id)) {
                return error(Response.Status.NOT_FOUND, "Background video not found");
            }
            configStore.setDashboardLayout(
                    configStore.getDashboardLayout().withBackgroundMode("video"));
            configStore.setDashboardUrl("");
            notifyConfigurationChanged();
            return response(Response.Status.OK, backgroundVideos.document());
        } catch (IOException | IllegalArgumentException error) {
            return error(Response.Status.CONFLICT, error.getMessage());
        }
    }

    private Response rollbackBackgroundVideo() throws JSONException {
        try {
            if (!backgroundVideos.rollback()) {
                return error(
                        Response.Status.CONFLICT,
                        "No previous background video is available");
            }
            configStore.setDashboardLayout(
                    configStore.getDashboardLayout().withBackgroundMode("video"));
            configStore.setDashboardUrl("");
            notifyConfigurationChanged();
            return response(Response.Status.OK, backgroundVideos.document());
        } catch (IOException | IllegalArgumentException error) {
            return error(Response.Status.CONFLICT, error.getMessage());
        }
    }

    private Response updateBackgroundVideoSchedule(JSONObject body) throws JSONException {
        try {
            BackgroundVideoSchedule schedule = BackgroundVideoSchedule.parse(body);
            backgroundVideos.updateSchedule(schedule);
            if (schedule.isActive()) {
                configStore.setDashboardLayout(
                        configStore.getDashboardLayout().withBackgroundMode("video"));
                configStore.setDashboardUrl("");
            }
            notifyConfigurationChanged();
            return response(Response.Status.OK, backgroundVideos.document());
        } catch (IllegalArgumentException error) {
            return error(Response.Status.BAD_REQUEST, error.getMessage());
        } catch (IOException error) {
            return error(Response.Status.CONFLICT, error.getMessage());
        }
    }

    private Response resumeBackgroundVideoSchedule() throws JSONException {
        try {
            backgroundVideos.resumeSchedule();
            notifyConfigurationChanged();
            return response(Response.Status.OK, backgroundVideos.document());
        } catch (IOException error) {
            return error(Response.Status.CONFLICT, error.getMessage());
        }
    }

    private Response deleteBackgroundVideo(String id) throws JSONException {
        try {
            BackgroundVideoLibrary.DeleteResult result = backgroundVideos.delete(id);
            switch (result) {
                case ACTIVE:
                    return error(
                            Response.Status.CONFLICT,
                            "Activate another background before deleting this one");
                case SCHEDULED:
                    return error(
                            Response.Status.CONFLICT,
                            "Remove this video from the schedule before deleting it");
                case NOT_FOUND:
                    return error(Response.Status.NOT_FOUND, "Background video not found");
                default:
                    notifyConfigurationChanged();
                    return response(
                            Response.Status.OK,
                            new JSONObject().put("deleted", true).put("id", id));
            }
        } catch (IOException error) {
            return error(Response.Status.CONFLICT, error.getMessage());
        }
    }

    private Response serveBackgroundVideoPoster(String id) {
        File poster = backgroundVideos.poster(id);
        if (poster == null) {
            return error(Response.Status.NOT_FOUND, "Background video poster not found");
        }
        try {
            Response result = newFixedLengthResponse(
                    Response.Status.OK,
                    "image/jpeg",
                    new FileInputStream(poster),
                    poster.length());
            result.addHeader("Cache-Control", "private, max-age=86400");
            result.addHeader("X-Content-Type-Options", "nosniff");
            return result;
        } catch (IOException error) {
            return error(Response.Status.NOT_FOUND, "Background video poster not found");
        }
    }

    private Response servePhoto(String encodedName) throws IOException {
        File photo = photos.resolve(encodedName);
        if (photo == null || !photo.isFile()) {
            return error(Response.Status.NOT_FOUND, "Photo not found");
        }
        Response result = newFixedLengthResponse(
                Response.Status.OK,
                PhotoLibrary.mimeType(photo.getName()),
                new FileInputStream(photo),
                photo.length());
        result.addHeader("Cache-Control", "private, max-age=3600");
        result.addHeader("X-Content-Type-Options", "nosniff");
        return result;
    }

    private Response serveScaledPhoto(String encodedName, int edge) {
        File variant;
        try {
            variant = photos.scaled(encodedName, edge);
        } catch (IOException error) {
            return error(Response.Status.UNSUPPORTED_MEDIA_TYPE, "Photo could not be decoded");
        }
        if (variant == null) {
            return error(Response.Status.NOT_FOUND, "Photo not found");
        }
        Response result;
        try {
            result = newFixedLengthResponse(
                    Response.Status.OK,
                    "image/jpeg",
                    new FileInputStream(variant),
                    variant.length());
        } catch (IOException error) {
            return error(Response.Status.NOT_FOUND, "Photo not found");
        }
        result.addHeader("Cache-Control", "private, max-age=86400");
        result.addHeader("X-Content-Type-Options", "nosniff");
        return result;
    }

    private static boolean isLoopback(IHTTPSession session) {
        String address = session.getRemoteIpAddress();
        return "127.0.0.1".equals(address)
                || "0:0:0:0:0:0:0:1".equals(address)
                || "::1".equals(address);
    }

    private Response updateBrightness(JSONObject body) throws JSONException {
        int value = body.optInt("value", -1);
        if (value < 1 || value > 255) {
            return error(Response.Status.BAD_REQUEST, "Brightness must be between 1 and 255");
        }
        boolean changed = mirror.setBrightness(value);
        return response(
                changed ? Response.Status.OK : Response.Status.SERVICE_UNAVAILABLE,
                new JSONObject().put("changed", changed).put("value", value));
    }

    private Response updateName(JSONObject body) throws JSONException {
        String name = body.optString("name", null);
        if (name == null || name.trim().isEmpty() || name.length() > 64) {
            return error(Response.Status.BAD_REQUEST, "Name must contain 1-64 characters");
        }
        boolean changed = mirror.setName(name);
        if (changed) {
            configStore.setDisplayName(name);
        }
        return response(
                changed ? Response.Status.OK : Response.Status.SERVICE_UNAVAILABLE,
                new JSONObject().put("changed", changed).put("name", name));
    }

    private Response systemStatus() throws JSONException {
        systemHelper.connect();
        JSONObject capabilities = systemHelper.capabilities();
        JSONObject result = new JSONObject();
        result.put("connected", systemHelper.isConnected());
        result.put("capabilities", capabilities == null ? JSONObject.NULL : capabilities);
        return response(Response.Status.OK, result);
    }

    private Response prepareKiosk() throws JSONException {
        systemHelper.connect();
        boolean prepared = systemHelper.prepareKiosk();
        return response(
                prepared ? Response.Status.OK : Response.Status.SERVICE_UNAVAILABLE,
                new JSONObject().put("prepared", prepared));
    }

    private Response setHome(JSONObject body) throws JSONException {
        systemHelper.connect();
        boolean enabled = body.optBoolean("enabled", true);
        boolean changed = systemHelper.setMirrorHome(enabled);
        return response(
                changed ? Response.Status.OK : Response.Status.SERVICE_UNAVAILABLE,
                new JSONObject().put("changed", changed).put("enabled", enabled));
    }

    private Response playMedia(JSONObject body) throws JSONException {
        if (automation.isSleeping()) {
            return error(Response.Status.CONFLICT, "Mirror is sleeping");
        }
        String url = body.optString("url", null);
        if (!InputValidator.validMediaUrl(url)) {
            return error(
                    Response.Status.BAD_REQUEST,
                    "Media URL must use HTTP, HTTPS, or RTSP");
        }
        MediaPlaybackManager.PlayRequest request = new MediaPlaybackManager.PlayRequest(
                url,
                body.optString("mimeType", null),
                body.optString("title", null),
                parseRequestHeaders(body.optJSONObject("headers")),
                body.optDouble("time", 0),
                body.has("volume") ? body.optDouble("volume", 1) : 1,
                body.has("speed") ? body.optDouble("speed", 1) : 1);
        boolean accepted = media.play(request);
        return response(
                accepted ? Response.Status.ACCEPTED : Response.Status.BAD_REQUEST,
                new JSONObject().put("accepted", accepted));
    }

    private Response seekMedia(JSONObject body) throws JSONException {
        double seconds = body.optDouble("time", Double.NaN);
        if (Double.isNaN(seconds) || seconds < 0) {
            return error(Response.Status.BAD_REQUEST, "Seek time must be non-negative");
        }
        media.seek(seconds);
        return response(Response.Status.ACCEPTED, new JSONObject().put("time", seconds));
    }

    private Response setMediaVolume(JSONObject body) throws JSONException {
        double volume = body.optDouble("volume", Double.NaN);
        if (Double.isNaN(volume) || volume < 0 || volume > 1) {
            return error(Response.Status.BAD_REQUEST, "Volume must be between 0 and 1");
        }
        media.setVolume(volume);
        return response(Response.Status.ACCEPTED, new JSONObject().put("volume", volume));
    }

    static Map<String, String> parseRequestHeaders(JSONObject headers)
            throws JSONException {
        Map<String, String> result = new HashMap<>();
        if (headers == null) {
            return result;
        }
        if (headers.length() > 1) {
            throw new JSONException("Only the companion media header is supported");
        }
        int totalLength = 0;
        Iterator<String> names = headers.keys();
        while (names.hasNext()) {
            String name = names.next();
            String value = headers.getString(name);
            String lowerName = name.toLowerCase(java.util.Locale.US);
            if (!"x-companion-token".equals(lowerName)
            || value.isEmpty()
            || value.length() > 256
            || value.contains("\r")
            || value.contains("\n")) {
        throw new JSONException("Invalid media request header");
            }
            totalLength += name.length() + value.length();
            if (totalLength > 8192) {
        throw new JSONException("Media request headers are too large");
            }
            result.put(name, value);
        }
        return result;
    }

    private JSONObject readJson(IHTTPSession session)
            throws IOException, ResponseException, JSONException {
        String lengthValue = session.getHeaders().get("content-length");
        if (lengthValue == null) {
            throw new ResponseException(
                    Response.Status.BAD_REQUEST,
                    "Content-Length is required");
        }
        try {
            if (Integer.parseInt(lengthValue) > MAX_BODY_BYTES) {
                throw new ResponseException(
                        Response.Status.BAD_REQUEST,
                        "Request body is too large");
            }
        } catch (NumberFormatException error) {
            throw new ResponseException(
                    Response.Status.BAD_REQUEST,
                    "Invalid Content-Length header");
        }
        Map<String, String> files = new HashMap<>();
        session.parseBody(files);
        String body = files.get("postData");
        if (body == null && files.get("content") != null) {
            body = readTemporaryBody(files.get("content"));
        }
        if (body == null || body.isEmpty()) {
            return new JSONObject();
        }
        return new JSONObject(body);
    }

    private static String readTemporaryBody(String path) throws IOException {
        File source = new File(path);
        if (!source.isFile() || source.length() > MAX_BODY_BYTES) {
            throw new IOException("Invalid temporary request body");
        }
        try (FileInputStream input = new FileInputStream(source);
                ByteArrayOutputStream output = new ByteArrayOutputStream()) {
            byte[] buffer = new byte[8192];
            int count;
            while ((count = input.read(buffer)) != -1) {
                output.write(buffer, 0, count);
                if (output.size() > MAX_BODY_BYTES) {
                    throw new IOException("Request body is too large");
                }
            }
            return new String(output.toByteArray(), java.nio.charset.StandardCharsets.UTF_8);
        }
    }

    private static long contentLength(IHTTPSession session) throws ResponseException {
        String value = session.getHeaders().get("content-length");
        if (value == null) {
            throw new ResponseException(
                    Response.Status.LENGTH_REQUIRED,
                    "Content-Length is required");
        }
        try {
            return Long.parseLong(value);
        } catch (NumberFormatException error) {
            throw new ResponseException(
                    Response.Status.BAD_REQUEST,
                    "Invalid Content-Length header");
        }
    }

    private boolean authorized(IHTTPSession session) {
        return pairing.authenticate(bearerToken(session));
    }

    private static String bearerToken(IHTTPSession session) {
        String authorization = session.getHeaders().get("authorization");
        return authorization != null && authorization.startsWith("Bearer ")
                ? authorization.substring("Bearer ".length())
                : null;
    }

    private Response controlAsset(String uri) throws IOException {
        String assetName;
        String mimeType;
        boolean document = false;
        switch (uri) {
            case "/":
            case "/index.html":
                assetName = "control/index.html";
                mimeType = "text/html; charset=utf-8";
                document = true;
                break;
            case "/app.js":
                assetName = "control/app.js";
                mimeType = "application/javascript; charset=utf-8";
                break;
            case "/clock.js":
                assetName = "control/clock.js";
                mimeType = "application/javascript; charset=utf-8";
                break;
            case "/styles.css":
                assetName = "control/styles.css";
                mimeType = "text/css; charset=utf-8";
                break;
            case "/manifest.webmanifest":
                assetName = "control/manifest.webmanifest";
                mimeType = "application/manifest+json; charset=utf-8";
                break;
            case "/icon.svg":
                assetName = "control/icon.svg";
                mimeType = "image/svg+xml";
                break;
            case "/dashboard/mirror.css":
                assetName = "control/dashboard/mirror.css";
                mimeType = "text/css; charset=utf-8";
                break;
            case "/dashboard/mirror.js":
                assetName = "control/dashboard/mirror.js";
                mimeType = "application/javascript; charset=utf-8";
                break;
            case "/dashboard/offline.html":
                assetName = "control/dashboard/offline.html";
                mimeType = "text/html; charset=utf-8";
                document = true;
                break;
            case "/dashboard/offline.css":
                assetName = "control/dashboard/offline.css";
                mimeType = "text/css; charset=utf-8";
                break;
            case "/dashboard/offline.js":
                assetName = "control/dashboard/offline.js";
                mimeType = "application/javascript; charset=utf-8";
                break;
            case "/dashboard/custom.html":
                assetName = "control/dashboard/custom.html";
                mimeType = "text/html; charset=utf-8";
                document = true;
                break;
            case "/dashboard/custom.css":
                assetName = "control/dashboard/custom.css";
                mimeType = "text/css; charset=utf-8";
                break;
            case "/dashboard/custom.js":
                assetName = "control/dashboard/custom.js";
                mimeType = "application/javascript; charset=utf-8";
                break;
            default:
                return null;
        }

        byte[] contents;
        try (InputStream input = context.getAssets().open(assetName);
                ByteArrayOutputStream output = new ByteArrayOutputStream()) {
            byte[] buffer = new byte[8192];
            int count;
            while ((count = input.read(buffer)) != -1) {
                output.write(buffer, 0, count);
            }
            contents = output.toByteArray();
        }
        Response result = newFixedLengthResponse(
                Response.Status.OK,
                mimeType,
                new ByteArrayInputStream(contents),
                contents.length);
        result.addHeader("Cache-Control", document ? "no-store" : "no-cache");
        result.addHeader(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self'; style-src 'self'; "
                        + "img-src 'self' data: blob:; connect-src 'self'; "
                        + "frame-ancestors 'none'; base-uri 'none'; form-action 'self'");
        result.addHeader("X-Content-Type-Options", "nosniff");
        result.addHeader("X-Frame-Options", "DENY");
        result.addHeader("Referrer-Policy", "no-referrer");
        return result;
    }

    private void notifyConfigurationChanged() {
        Intent intent = new Intent(ControlServerService.ACTION_CONFIGURATION_CHANGED);
        intent.setPackage(context.getPackageName());
        context.sendBroadcast(intent);
    }

    private static Response error(Response.Status status, String message) {
        try {
            return response(status, new JSONObject().put("error", message));
        } catch (JSONException impossible) {
            return newFixedLengthResponse(status, MIME_PLAINTEXT, message);
        }
    }

    private static Response response(Response.Status status, JSONObject body) {
        Response response = newFixedLengthResponse(
                status,
                "application/json; charset=utf-8",
                body.toString());
        response.addHeader("Cache-Control", "no-store");
        return response;
    }
}
