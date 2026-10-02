package dev.mirror.repurpose.updater;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;
import static org.junit.Assert.fail;

import android.app.admin.DevicePolicyManager;

import org.json.JSONArray;
import org.json.JSONObject;
import org.junit.Test;

import java.util.HashSet;
import java.util.Set;

public final class RuntimePermissionsTest {
    private static final String MICROPHONE = "android.permission.RECORD_AUDIO";

    private static JSONObject body(boolean granted) throws Exception {
        return new JSONObject()
                .put("packageName", OtaConstants.HOME_PACKAGE)
                .put("permission", MICROPHONE)
                .put("granted", granted)
                .put("confirm", RuntimePermissions.CONFIRMATION);
    }

    private static final class FakeBackend implements RuntimePermissions.Backend {
        final Set<String> declared = new HashSet<>(RuntimePermissions.ALLOWLIST);
        boolean granted;
        int state = DevicePolicyManager.PERMISSION_GRANT_STATE_DEFAULT;
        int calls;
        boolean accepted = true;
        boolean apply = true;
        String notReady;

        @Override
        public Set<String> prepare() throws OtaException {
            if (notReady != null) {
                throw new OtaException(notReady);
            }
            return declared;
        }

        @Override
        public boolean granted(String permission) {
            return granted;
        }

        @Override
        public int grantState(String permission) {
            return state;
        }

        @Override
        public boolean setGrantState(String permission, int requested) {
            calls++;
            if (accepted && apply) {
                state = requested;
                granted = requested == DevicePolicyManager.PERMISSION_GRANT_STATE_GRANTED;
            }
            return accepted;
        }
    }

    @Test
    public void microphoneGrantIsVerifiedAndReportsEveryAllowlistedPermission() throws Exception {
        FakeBackend backend = new FakeBackend();
        JSONObject result = new RuntimePermissions(backend)
                .change(RuntimePermissions.Request.parse(body(true)));
        assertTrue(backend.granted);
        assertEquals(DevicePolicyManager.PERMISSION_GRANT_STATE_GRANTED, backend.state);
        assertEquals(1, backend.calls);
        assertEquals(MICROPHONE, result.getString("changedPermission"));
        assertTrue(result.getBoolean("requestedGrant"));
        assertEquals(4, result.getJSONArray("permissions").length());
    }

    @Test
    public void revokeSetsDeniedRatherThanLeavingThePermissionGranted() throws Exception {
        FakeBackend backend = new FakeBackend();
        backend.granted = true;
        new RuntimePermissions(backend).change(RuntimePermissions.Request.parse(body(false)));
        assertFalse(backend.granted);
        assertEquals(DevicePolicyManager.PERMISSION_GRANT_STATE_DENIED, backend.state);
    }

    @Test
    public void undeclaredMicrophoneIsReportedWithoutInventingAGrant() throws Exception {
        FakeBackend backend = new FakeBackend();
        backend.declared.remove(MICROPHONE);
        JSONArray permissions = new RuntimePermissions(backend).snapshot().getJSONArray("permissions");
        JSONObject microphone = permissions.getJSONObject(0);
        assertEquals(MICROPHONE, microphone.getString("permission"));
        assertFalse(microphone.getBoolean("declared"));
        assertTrue(microphone.isNull("granted"));
        assertTrue(microphone.isNull("grantState"));
    }

    @Test
    public void undeclaredPermissionCannotBeChanged() throws Exception {
        FakeBackend backend = new FakeBackend();
        backend.declared.remove(MICROPHONE);
        try {
            new RuntimePermissions(backend).change(RuntimePermissions.Request.parse(body(true)));
            fail("Undeclared permission was accepted");
        } catch (OtaException expected) {
            assertTrue(expected.getMessage().contains("does not declare"));
            assertEquals(0, backend.calls);
        }
    }

    @Test
    public void readinessFailuresCannotReachTheGrantApi() throws Exception {
        for (String reason : new String[]{"not device owner", "untrusted Home", "OTA active"}) {
            FakeBackend backend = new FakeBackend();
            backend.notReady = reason;
            try {
                new RuntimePermissions(backend).change(RuntimePermissions.Request.parse(body(true)));
                fail("Readiness failure was ignored");
            } catch (OtaException expected) {
                assertEquals(reason, expected.getMessage());
                assertEquals(0, backend.calls);
            }
        }
    }

    @Test
    public void androidRefusalIsNotSuccess() throws Exception {
        FakeBackend backend = new FakeBackend();
        backend.accepted = false;
        try {
            new RuntimePermissions(backend).change(RuntimePermissions.Request.parse(body(true)));
            fail("Android refusal was ignored");
        } catch (OtaException expected) {
            assertTrue(expected.getMessage().contains("rejected"));
        }
    }

    @Test
    public void anUnconfirmedChangeIsNotSuccess() throws Exception {
        FakeBackend backend = new FakeBackend();
        backend.apply = false;
        try {
            new RuntimePermissions(backend).change(RuntimePermissions.Request.parse(body(true)));
            fail("Unconfirmed grant was accepted");
        } catch (OtaException expected) {
            assertTrue(expected.getMessage().contains("did not confirm"));
        }
    }

    @Test
    public void anotherPackageIsRejected() throws Exception {
        rejects(body(true).put("packageName", "dev.mirror.repurpose.voicelab"));
    }

    @Test
    public void unrelatedPermissionsAreRejected() throws Exception {
        for (String permission : new String[]{
                "android.permission.READ_CONTACTS",
                "android.permission.READ_CALENDAR",
                "android.permission.WRITE_SECURE_SETTINGS",
                "android.permission.INSTALL_PACKAGES",
                "android.permission.RECORD_AUDIO "}) {
            rejects(body(true).put("permission", permission));
        }
    }

    @Test
    public void confirmationAndBooleanAreRequired() throws Exception {
        JSONObject missingConfirmation = body(true);
        missingConfirmation.remove("confirm");
        rejects(missingConfirmation);
        rejects(body(true).put("confirm", "yes"));
        rejects(body(true).put("granted", "true"));
        rejects(body(true).put("granted", 1));
        rejects(body(true).put("granted", JSONObject.NULL));
    }

    @Test
    public void extraFieldsAreRejected() throws Exception {
        rejects(body(true).put("command", "anything"));
    }

    private static void rejects(JSONObject body) throws Exception {
        try {
            RuntimePermissions.Request.parse(body);
            fail("Invalid permission request was accepted: " + body);
        } catch (IllegalArgumentException expected) {
            assertTrue(expected.getMessage().length() > 0);
        }
    }
}
