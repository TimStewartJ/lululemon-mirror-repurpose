package dev.mirror.repurpose.updater;

import android.content.Context;

import org.json.JSONException;
import org.json.JSONObject;

import java.io.File;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.HashMap;
import java.util.Locale;
import java.util.Map;

import fi.iki.elonen.NanoHTTPD;

final class OtaServer extends NanoHTTPD {
    private final OtaAuthenticator authenticator;
    private final OtaManager manager;

    OtaServer(Context context, OtaManager manager) {
        super(OtaConstants.PORT);
        this.manager = manager;
        authenticator = new OtaAuthenticator(context);
    }

    @Override
    public Response serve(IHTTPSession session) {
        if (Method.OPTIONS.equals(session.getMethod())) {
            return error(Response.Status.METHOD_NOT_ALLOWED, "Cross-origin requests are disabled");
        }
        String path = session.getUri();
        try {
            if (Method.GET.equals(session.getMethod())
                    && "/api/v1/bootstrap".equals(path)
                    && isLoopback(session)) {
                return response(
                        Response.Status.OK,
                        new JSONObject()
                                .put("apiVersion", 1)
                                .put("updaterVersion", BuildConfig.VERSION_NAME)
                                .put("deviceOwner", manager.isDeviceOwner())
                                .put("provisioned", authenticator.isProvisioned()));
            }
            if (Method.POST.equals(session.getMethod())
                    && "/api/v1/provision".equals(path)
                    && isLoopback(session)) {
                if (!manager.isDeviceOwner()) {
                    return error(
                            Response.Status.CONFLICT,
                            "OTA supervisor must be device owner before provisioning");
                }
                return response(
                        Response.Status.OK,
                        new JSONObject()
                                .put(
                                        "token",
                                        authenticator.issueProvisioningToken(
                                                session.getHeaders().get(
                                                        "x-ota-bootstrap-token")))
                                .put("port", OtaConstants.PORT));
            }
            if (Method.POST.equals(session.getMethod())
                    && "/api/v1/provision/confirm".equals(path)
                    && isLoopback(session)) {
                authenticator.confirm(
                        session.getMethod().name(),
                        path,
                        session.getHeaders());
                return response(
                        Response.Status.OK,
                        new JSONObject().put("provisioned", true));
            }
            if (Method.POST.equals(session.getMethod())
                    && "/api/v1/provision/recover".equals(path)
                    && isLoopback(session)) {
                if (!manager.isDeviceOwner()) {
                    return error(
                            Response.Status.CONFLICT,
                            "OTA supervisor is not the Android device owner");
                }
                return response(
                        Response.Status.OK,
                        new JSONObject()
                                .put(
                                        "token",
                                        authenticator.recoverProvisioningToken(
                                                session.getHeaders().get(
                                                        "x-ota-bootstrap-token")))
                                .put("port", OtaConstants.PORT));
            }

            String bodySha256 = requestBodySha256(session);
            authenticator.authorize(
                    session.getMethod().name(),
                    path,
                    session.getHeaders(),
                    bodySha256);

            if (Method.GET.equals(session.getMethod()) && "/api/v1/status".equals(path)) {
                return response(Response.Status.OK, manager.snapshot());
            }
            if (Method.GET.equals(session.getMethod()) && "/api/v1/permissions".equals(path)) {
                return response(Response.Status.OK, manager.permissionsSnapshot());
            }
            if (Method.POST.equals(session.getMethod()) && "/api/v1/permissions".equals(path)) {
                return changePermission(session, bodySha256);
            }
            if (Method.PUT.equals(session.getMethod()) && "/api/v1/update".equals(path)) {
                return uploadUpdate(session, bodySha256);
            }
            if (Method.POST.equals(session.getMethod()) && "/api/v1/rollback".equals(path)) {
                return response(Response.Status.ACCEPTED, manager.submitRollback());
            }
            if (Method.POST.equals(session.getMethod())
                    && "/api/v1/deprovision".equals(path)
                    && isLoopback(session)) {
                if (!"CLEAR_DEVICE_OWNER".equals(
                        session.getHeaders().get("x-ota-confirm"))) {
                    return error(
                            Response.Status.BAD_REQUEST,
                            "Explicit device-owner confirmation is required");
                }
                manager.clearDeviceOwner();
                return response(
                        Response.Status.OK,
                        new JSONObject().put("deviceOwner", false));
            }
            return error(Response.Status.NOT_FOUND, "Endpoint not found");
        } catch (OtaAuthenticator.AuthException error) {
            return error(Response.Status.UNAUTHORIZED, error.getMessage());
        } catch (OtaException error) {
            return error(Response.Status.CONFLICT, error.getMessage());
        } catch (JSONException error) {
            return error(Response.Status.INTERNAL_ERROR, "Unable to encode OTA response");
        } catch (IOException | ResponseException error) {
            return error(Response.Status.BAD_REQUEST, "Unable to read OTA request");
        }
    }

    private Response changePermission(IHTTPSession session, String expectedSha256)
            throws IOException, ResponseException, OtaException, OtaAuthenticator.AuthException,
            JSONException {
        long length = contentLength(session);
        if (length < 1 || length > 1024) {
            return error(Response.Status.BAD_REQUEST, "Permission request must be 1-1024 bytes");
        }
        String contentType = session.getHeaders().get("content-type");
        if (contentType == null
                || !"application/json".equalsIgnoreCase(contentType.split(";", 2)[0].trim())) {
            return error(Response.Status.UNSUPPORTED_MEDIA_TYPE, "JSON Content-Type is required");
        }
        byte[] body = new byte[(int) length];
        int offset = 0;
        while (offset < body.length) {
            int count = session.getInputStream().read(body, offset, body.length - offset);
            if (count < 0) {
                return error(Response.Status.BAD_REQUEST, "Permission request body is incomplete");
            }
            offset += count;
        }
        OtaAuthenticator.verifyBodySha256(body, expectedSha256);
        RuntimePermissions.Request request;
        try {
            request = RuntimePermissions.Request.parse(
                    new JSONObject(new String(body, StandardCharsets.UTF_8)));
        } catch (JSONException | IllegalArgumentException error) {
            return error(Response.Status.BAD_REQUEST, error.getMessage());
        }
        return response(Response.Status.OK, manager.changePermission(request));
    }

    private Response uploadUpdate(IHTTPSession session, String expectedSha256)
            throws IOException, ResponseException, OtaException {
        long contentLength = contentLength(session);
        if (contentLength < 1 || contentLength > OtaManager.MAX_APK_BYTES) {
            return error(Response.Status.BAD_REQUEST, "APK exceeds the 32 MB limit");
        }
        String contentType = session.getHeaders().get("content-type");
        if (contentType == null
                || !contentType.toLowerCase(Locale.US)
                        .startsWith("application/vnd.android.package-archive")) {
            return error(
                    Response.Status.UNSUPPORTED_MEDIA_TYPE,
                    "Android package Content-Type is required");
        }
        Map<String, String> files = new HashMap<>();
        session.parseBody(files);
        String temporaryPath = files.get("content");
        if (temporaryPath == null) {
            return error(Response.Status.BAD_REQUEST, "APK body is missing");
        }
        File temporary = new File(temporaryPath);
        if (!temporary.isFile() || temporary.length() != contentLength) {
            return error(Response.Status.BAD_REQUEST, "APK upload is incomplete");
        }
        return response(
                Response.Status.ACCEPTED,
                manager.submitUpdate(temporary, expectedSha256));
    }

    private static String requestBodySha256(IHTTPSession session) {
        String value = session.getHeaders().get("x-ota-content-sha256");
        if (value == null || value.isEmpty()) {
            return OtaAuthenticator.EMPTY_SHA256;
        }
        return value.toLowerCase(Locale.US);
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
                    "Invalid Content-Length");
        }
    }

    private static boolean isLoopback(IHTTPSession session) {
        String address = session.getRemoteIpAddress();
        if (address == null) {
            return false;
        }
        String normalized = address.toLowerCase(Locale.US);
        return normalized.startsWith("127.")
                || "::1".equals(normalized)
                || normalized.startsWith("0:0:0:0:0:0:0:1")
                || normalized.startsWith("::ffff:127.");
    }

    private static Response response(Response.Status status, JSONObject body) {
        Response response = newFixedLengthResponse(
                status,
                "application/json; charset=utf-8",
                body.toString());
        response.addHeader("Cache-Control", "no-store");
        response.addHeader("X-Content-Type-Options", "nosniff");
        response.addHeader("X-Frame-Options", "DENY");
        return response;
    }

    private static Response error(Response.Status status, String message) {
        try {
            return response(status, new JSONObject().put("error", message));
        } catch (JSONException impossible) {
            return newFixedLengthResponse(
                    Response.Status.INTERNAL_ERROR,
                    "text/plain; charset=utf-8",
                    "Internal OTA error");
        }
    }
}
