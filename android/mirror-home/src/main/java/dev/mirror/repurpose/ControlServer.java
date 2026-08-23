package dev.mirror.repurpose;

import android.content.Context;
import android.content.Intent;
import android.net.wifi.WifiInfo;
import android.net.wifi.WifiManager;

import org.json.JSONException;
import org.json.JSONObject;

import java.io.IOException;
import java.util.HashMap;
import java.util.Map;

import fi.iki.elonen.NanoHTTPD;

public final class ControlServer extends NanoHTTPD {
    private static final int MAX_BODY_BYTES = 64 * 1024;

    private final Context context;
    private final ConfigStore configStore;
    private final MediaPlaybackManager media;
    private final MirrorBinderClient mirror;
    private final PairingManager pairing;
    private final SystemHelperClient systemHelper;
    private final WifiProvisioner wifi;

    public ControlServer(Context context, int port) {
        super(port);
        this.context = context.getApplicationContext();
        configStore = new ConfigStore(context);
        media = MediaPlaybackManager.getInstance(context);
        mirror = MirrorBinderClient.getInstance(context);
        pairing = PairingManager.getInstance(context);
        systemHelper = SystemHelperClient.getInstance(context);
        wifi = new WifiProvisioner(context);
    }

    @Override
    public Response serve(IHTTPSession session) {
        if (Method.OPTIONS.equals(session.getMethod())) {
            return error(Response.Status.METHOD_NOT_ALLOWED, "Cross-origin requests are disabled");
        }

        String uri = session.getUri();
        try {
            if (Method.GET.equals(session.getMethod()) && "/api/v1/status".equals(uri)) {
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
            if (Method.POST.equals(session.getMethod())
                    && "/api/v1/wifi/configure".equals(uri)) {
                return configureWifi(readJson(session));
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
            if (Method.POST.equals(session.getMethod()) && "/api/v1/pair/revoke".equals(uri)) {
                pairing.revoke();
                return response(Response.Status.OK, new JSONObject().put("revoked", true));
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
        result.put("paired", pairing.isPaired());
        result.put("displayName", configStore.getDisplayName());
        result.put("mirrorBinderConnected", mirror.isConnected());
        result.put("systemHelperConnected", systemHelper.isConnected());
        Integer brightness = mirror.getBrightness();
        result.put("brightness", brightness == null ? JSONObject.NULL : brightness);
        result.put("wifi", wifiStatus);
        result.put("media", media.snapshot());
        result.put("bleProvisioning", BleProvisioningServer.lastKnownStatus());
        return result;
    }

    private Response pair(JSONObject body) throws JSONException {
        String token = pairing.pair(body.optString("code", null));
        if (token == null) {
            return error(Response.Status.UNAUTHORIZED, "Invalid or expired pairing code");
        }
        return response(Response.Status.OK, new JSONObject().put("token", token));
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

    private Response configureWifi(JSONObject body) throws JSONException {
        String ssid = body.optString("ssid", null);
        String passphrase = body.optString("passphrase", null);
        boolean hidden = body.optBoolean("hidden", false);
        WifiProvisioner.Result result = wifi.configure(ssid, passphrase, hidden);
        return response(
                result.success ? Response.Status.ACCEPTED : Response.Status.BAD_REQUEST,
                new JSONObject()
                        .put("accepted", result.success)
                        .put("message", result.message));
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

    private Response playMedia(JSONObject body) throws JSONException {
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
        if (body == null || body.isEmpty()) {
            return new JSONObject();
        }
        return new JSONObject(body);
    }

    private boolean authorized(IHTTPSession session) {
        String authorization = session.getHeaders().get("authorization");
        if (authorization == null || !authorization.startsWith("Bearer ")) {
            return false;
        }
        return pairing.authenticate(authorization.substring("Bearer ".length()));
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
