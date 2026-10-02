package dev.mirror.repurpose.updater;

import android.app.admin.DevicePolicyManager;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.util.Arrays;
import java.util.Collections;
import java.util.Iterator;
import java.util.List;
import java.util.Set;

final class RuntimePermissions {
    static final String CONFIRMATION = "CHANGE_RUNTIME_PERMISSION";
    static final List<String> ALLOWLIST = Collections.unmodifiableList(Arrays.asList(
            "android.permission.RECORD_AUDIO",
            "android.permission.CAMERA",
            "android.permission.ACCESS_FINE_LOCATION",
            "android.permission.ACCESS_COARSE_LOCATION"));

    interface Backend {
        Set<String> prepare() throws OtaException;
        boolean granted(String permission) throws OtaException;
        int grantState(String permission) throws OtaException;
        boolean setGrantState(String permission, int state) throws OtaException;
    }

    private final Backend backend;

    RuntimePermissions(Backend backend) {
        this.backend = backend;
    }

    JSONObject snapshot() throws OtaException, JSONException {
        Set<String> declared = backend.prepare();
        JSONArray permissions = new JSONArray();
        for (String permission : ALLOWLIST) {
            boolean requested = declared.contains(permission);
            permissions.put(new JSONObject()
                    .put("permission", permission)
                    .put("declared", requested)
                    .put("granted", requested ? backend.granted(permission) : JSONObject.NULL)
                    .put("grantState", requested
                            ? stateName(backend.grantState(permission)) : JSONObject.NULL));
        }
        return new JSONObject()
                .put("packageName", OtaConstants.HOME_PACKAGE)
                .put("permissions", permissions);
    }

    JSONObject change(Request request) throws OtaException, JSONException {
        Set<String> declared = backend.prepare();
        if (!declared.contains(request.permission)) {
            throw new OtaException(
                    "Mirror Home does not declare " + request.permission
                            + "; install a signed Home build that declares it first");
        }
        int state = request.granted
                ? DevicePolicyManager.PERMISSION_GRANT_STATE_GRANTED
                : DevicePolicyManager.PERMISSION_GRANT_STATE_DENIED;
        if (!backend.setGrantState(request.permission, state)) {
            throw new OtaException("Android rejected the runtime permission change");
        }
        if (backend.grantState(request.permission) != state
                || backend.granted(request.permission) != request.granted) {
            throw new OtaException(
                    "Android did not confirm the requested runtime permission state");
        }
        // Android 6 can also change related permissions in the same group.
        return snapshot()
                .put("changedPermission", request.permission)
                .put("requestedGrant", request.granted);
    }

    private static String stateName(int state) throws OtaException {
        switch (state) {
            case DevicePolicyManager.PERMISSION_GRANT_STATE_DEFAULT:
                return "default";
            case DevicePolicyManager.PERMISSION_GRANT_STATE_GRANTED:
                return "granted";
            case DevicePolicyManager.PERMISSION_GRANT_STATE_DENIED:
                return "denied";
            default:
                throw new OtaException("Android returned an unknown permission grant state");
        }
    }

    static final class Request {
        final String permission;
        final boolean granted;

        private Request(String permission, boolean granted) {
            this.permission = permission;
            this.granted = granted;
        }

        static Request parse(JSONObject body) throws JSONException {
            List<String> fields = Arrays.asList("packageName", "permission", "granted", "confirm");
            Iterator<String> keys = body.keys();
            while (keys.hasNext()) {
                if (!fields.contains(keys.next())) {
                    throw new IllegalArgumentException("Unknown permission request field");
                }
            }
            if (!OtaConstants.HOME_PACKAGE.equals(body.opt("packageName"))) {
                throw new IllegalArgumentException("Only Mirror Home permissions may be changed");
            }
            Object permission = body.opt("permission");
            if (!(permission instanceof String) || !ALLOWLIST.contains(permission)) {
                throw new IllegalArgumentException("Runtime permission is not allowlisted");
            }
            Object granted = body.opt("granted");
            if (!(granted instanceof Boolean)) {
                throw new IllegalArgumentException("granted must be a boolean");
            }
            if (!CONFIRMATION.equals(body.opt("confirm"))) {
                throw new IllegalArgumentException("Explicit permission-change confirmation is required");
            }
            return new Request((String) permission, (Boolean) granted);
        }
    }
}
