package dev.mirror.repurpose;

import android.content.Context;
import android.content.Intent;
import android.net.wifi.WifiInfo;
import android.net.wifi.WifiManager;
import android.os.SystemClock;

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
    private static final int MAX_BODY_BYTES = 64 * 1024;

    private final Context context;
    private final ConfigStore configStore;
    private final AutomationManager automation;
    private final MediaPlaybackManager media;
    private final MirrorBinderClient mirror;
    private final PairingManager pairing;
    private final PhotoLibrary photos;
    private final SystemHelperClient systemHelper;
    private final WifiProvisioner wifi;
    private final WifiDirectOnboarding wifiDirect;

    public ControlServer(Context context, int port) {
        super(port);
        this.context = context.getApplicationContext();
        configStore = new ConfigStore(context);
        automation = AutomationManager.getInstance(context);
        media = MediaPlaybackManager.getInstance(context);
        mirror = MirrorBinderClient.getInstance(context);
        pairing = PairingManager.getInstance(context);
        photos = new PhotoLibrary(context);
        systemHelper = SystemHelperClient.getInstance(context);
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
                return servePhoto(uri.substring("/photos/".length()));
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
            if (Method.POST.equals(session.getMethod()) && "/api/v1/pair".equals(uri)) {
                return pair(readJson(session));
            }
            if (!authorized(session)) {
                return error(Response.Status.UNAUTHORIZED, "Authentication required");
            }
            if (Method.GET.equals(session.getMethod()) && "/api/v1/dashboard".equals(uri)) {
                return response(
                        Response.Status.OK,
                        new JSONObject().put("url", configStore.getDashboardUrl()));
            }
            if (Method.PUT.equals(session.getMethod()) && "/api/v1/dashboard".equals(uri)) {
                return updateDashboard(readJson(session));
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

        JSONObject result = new JSONObject();
        result.put("apiVersion", 1);
        result.put("appVersion", BuildConfig.VERSION_NAME);
        result.put("deviceUptimeSeconds", SystemClock.elapsedRealtime() / 1000L);
        result.put("paired", pairing.isPaired());
        result.put("displayName", configStore.getDisplayName());
        result.put("timeZone", configStore.getTimeZoneId());
        result.put("utcOffsetMinutes", configStore.getUtcOffsetMinutes());
        result.put("clock24Hour", configStore.isClock24Hour());
        result.put("mirrorBinderConnected", mirror.isConnected());
        result.put("systemHelperConnected", systemHelper.isConnected());
        Integer brightness = mirror.getBrightness();
        result.put("brightness", brightness == null ? JSONObject.NULL : brightness);
        result.put("wifi", wifiStatus);
        result.put("media", media.snapshot());
        result.put("bleProvisioning", BleProvisioningServer.lastKnownStatus());
        result.put("automation", automation.snapshot());
        return result;
    }

    private JSONObject bootstrap() throws JSONException {
        return new JSONObject()
                .put("apiVersion", 1)
                .put("appVersion", BuildConfig.VERSION_NAME)
                .put("displayName", configStore.getDisplayName())
                .put("paired", pairing.isPaired());
    }

    private Response pair(JSONObject body) throws JSONException {
        PairingManager.PairingResult result = pairing.pair(
                body.optString("code", null),
                body.optString("name", "Device"));
        if (result == null) {
            return error(Response.Status.UNAUTHORIZED, "Invalid or expired pairing code");
        }
        String timeZone = body.optString("timeZone", "");
        if (InputValidator.validTimeZone(timeZone)) {
            configStore.setTimeZoneId(timeZone);
            int offset = body.optInt("utcOffsetMinutes", 0);
            if (offset >= -14 * 60 && offset <= 14 * 60) {
                configStore.setUtcOffsetMinutes(offset);
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

    private JSONObject preferences() throws JSONException {
        return new JSONObject()
                .put("timeZone", configStore.getTimeZoneId())
                .put("utcOffsetMinutes", configStore.getUtcOffsetMinutes())
                .put("clock24Hour", configStore.isClock24Hour())
                .put("ambientLightAvailable", automation.hasAmbientLightSensor());
    }

    private Response updatePreferences(JSONObject body) throws JSONException {
        String timeZone = body.optString("timeZone", "");
        if (!InputValidator.validTimeZone(timeZone)) {
            return error(Response.Status.BAD_REQUEST, "Unknown IANA time zone");
        }
        configStore.setTimeZoneId(timeZone);
        int offset = body.optInt("utcOffsetMinutes", 0);
        if (offset < -14 * 60 || offset > 14 * 60) {
            return error(Response.Status.BAD_REQUEST, "Invalid UTC offset");
        }
        configStore.setUtcOffsetMinutes(offset);
        configStore.setClock24Hour(body.optBoolean("clock24Hour", false));
        notifyConfigurationChanged();
        return response(Response.Status.OK, preferences());
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
            case "/dashboard/aurora.html":
                assetName = "control/dashboard/aurora.html";
                mimeType = "text/html; charset=utf-8";
                document = true;
                break;
            case "/dashboard/aurora.css":
                assetName = "control/dashboard/aurora.css";
                mimeType = "text/css; charset=utf-8";
                break;
            case "/dashboard/aurora.js":
                assetName = "control/dashboard/aurora.js";
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
            case "/dashboard/gallery.html":
                assetName = "control/dashboard/gallery.html";
                mimeType = "text/html; charset=utf-8";
                document = true;
                break;
            case "/dashboard/gallery.css":
                assetName = "control/dashboard/gallery.css";
                mimeType = "text/css; charset=utf-8";
                break;
            case "/dashboard/gallery.js":
                assetName = "control/dashboard/gallery.js";
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
                        + "img-src 'self' data:; connect-src 'self'; "
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
