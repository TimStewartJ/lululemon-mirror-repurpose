package dev.mirror.repurpose.updater;

import android.app.PendingIntent;
import android.app.admin.DevicePolicyManager;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageInfo;
import android.content.pm.PackageInstaller;
import android.content.pm.PackageManager;
import android.os.Build;
import android.os.PowerManager;
import android.util.Log;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.lang.reflect.Field;
import java.net.HttpURLConnection;
import java.net.URL;
import java.util.Arrays;
import java.util.HashSet;
import java.util.Locale;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.ScheduledFuture;
import java.util.concurrent.TimeUnit;

import dev.mirror.repurpose.health.ProcessHealth;

final class OtaManager {
    static final long MAX_APK_BYTES = 32L * 1024L * 1024L;

    private static final String TAG = "MirrorOtaManager";
    private static final String PREFERENCES = "ota_state";
    private static final String KEY_STATE = "state";
    private static final String KEY_MESSAGE = "message";
    private static final String KEY_TRANSACTION = "transaction";
    private static final String KEY_PENDING_KIND = "pending_kind";
    private static final String KEY_CANDIDATE_CODE = "candidate_code";
    private static final String KEY_CANDIDATE_NAME = "candidate_name";
    private static final String KEY_PREVIOUS_CODE = "previous_code";
    private static final String KEY_PREVIOUS_NAME = "previous_name";
    private static final String KEY_PRESERVE_BACKUP = "preserve_backup";
    private static final String KEY_UPDATED_AT = "updated_at";
    private static final String KIND_CANDIDATE = "candidate";
    private static final String KIND_ROLLBACK = "rollback";
    private static final int INSTALL_ALLOW_DOWNGRADE = 0x00000080;
    private static final long HEALTH_TIMEOUT_MS = 60_000L;
    private static final long RECOVERY_TIMEOUT_MS = 120_000L;

    private final Context context;
    private final SharedPreferences preferences;
    private final ApkInspector inspector;
    private final RuntimePermissions runtimePermissions;
    private final File root;
    private final File candidateApk;
    private final File backupApk;
    private final ScheduledExecutorService executor =
            Executors.newSingleThreadScheduledExecutor();
    private final PowerManager.WakeLock wakeLock;
    private ScheduledFuture<?> recoveryFuture;

    OtaManager(Context context) {
        this.context = context.getApplicationContext();
        preferences = this.context.getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE);
        try {
            inspector = new ApkInspector(this.context);
        } catch (ApkInspector.InspectionException error) {
            throw new IllegalStateException("Unable to initialize APK validation", error);
        }
        runtimePermissions = new RuntimePermissions(new RuntimePermissions.Backend() {
            @Override
            public Set<String> prepare() throws OtaException {
                requireDeviceOwner();
                requireIdle();
                try {
                    inspector.requireTrustedHome(inspector.installedHome());
                    PackageInfo home = OtaManager.this.context.getPackageManager()
                            .getPackageInfo(OtaConstants.HOME_PACKAGE, PackageManager.GET_PERMISSIONS);
                    if (home.applicationInfo.targetSdkVersion < 23) {
                        throw new OtaException("Mirror Home must use runtime permissions");
                    }
                    return home.requestedPermissions == null
                            ? new HashSet<String>()
                            : new HashSet<>(Arrays.asList(home.requestedPermissions));
                } catch (ApkInspector.InspectionException | PackageManager.NameNotFoundException error) {
                    throw new OtaException("Unable to validate installed Mirror Home", error);
                }
            }

            @Override
            public boolean granted(String permission) {
                return OtaManager.this.context.getPackageManager()
                        .checkPermission(permission, OtaConstants.HOME_PACKAGE)
                        == PackageManager.PERMISSION_GRANTED;
            }

            @Override
            public int grantState(String permission) throws OtaException {
                return permissionPolicyManager().getPermissionGrantState(
                        deviceAdmin(), OtaConstants.HOME_PACKAGE, permission);
            }

            @Override
            public boolean setGrantState(String permission, int state) throws OtaException {
                return permissionPolicyManager().setPermissionGrantState(
                        deviceAdmin(), OtaConstants.HOME_PACKAGE, permission, state);
            }
        });
        root = new File(this.context.getFilesDir(), "updates");
        if (!root.isDirectory() && !root.mkdirs()) {
            throw new IllegalStateException("Unable to create OTA storage");
        }
        candidateApk = new File(root, "candidate.apk");
        backupApk = new File(root, "known-good.apk");
        PowerManager powerManager =
                (PowerManager) this.context.getSystemService(Context.POWER_SERVICE);
        if (powerManager == null) {
            throw new IllegalStateException("Power manager is unavailable");
        }
        wakeLock = powerManager.newWakeLock(
                PowerManager.PARTIAL_WAKE_LOCK,
                "MirrorRepurpose:OtaTransaction");
        wakeLock.setReferenceCounted(false);
        scheduleInterruptedTransactionRecovery();
    }

    synchronized JSONObject submitUpdate(File source, String expectedSha256)
            throws OtaException {
        requireDeviceOwner();
        requireIdle();
        String previousState = state();
        boolean preserveBackup = shouldPreserveBackup(
                previousState,
                preferences.getBoolean(KEY_PRESERVE_BACKUP, false));
        if (preserveBackup && !backupApk.isFile()) {
            throw new OtaException(
                    "Recovery requires the existing known-good APK backup");
        }
        if (expectedSha256 == null
                || !expectedSha256.toLowerCase(Locale.US).matches("[0-9a-f]{64}")) {
            throw new OtaException("A valid APK SHA-256 is required");
        }
        acquireWakeLock();
        try {
            File temporaryCandidate = new File(root, "candidate.apk.tmp");
            copyFile(source, temporaryCandidate, MAX_APK_BYTES);
            String actualSha256 = ApkInspector.sha256(temporaryCandidate);
            if (!actualSha256.equals(expectedSha256.toLowerCase(Locale.US))) {
                throw new OtaException("Uploaded APK SHA-256 does not match");
            }

            if (!BuildConfig.SUPPORTED_FINGERPRINT.equals(Build.FINGERPRINT)) {
                throw new OtaException("OTA supervisor does not support this firmware");
            }
            ApkInspector.Metadata candidate = inspector.inspectArchive(temporaryCandidate);
            inspector.requireTrustedHome(candidate);
            ApkInspector.Metadata current = inspector.installedHome();
            inspector.requireTrustedHome(current);
            if (candidate.versionCode <= current.versionCode) {
                throw new OtaException(
                        "Update version code must be newer than installed Mirror Home");
            }

            if (preserveBackup) {
                ApkInspector.Metadata backup = inspector.inspectArchive(backupApk);
                inspector.requireTrustedHome(backup);
            } else {
                File temporaryBackup = new File(root, "known-good.apk.tmp");
                copyFile(current.source, temporaryBackup, MAX_APK_BYTES);
                ApkInspector.Metadata backup = inspector.inspectArchive(temporaryBackup);
                inspector.requireTrustedHome(backup);
                if (backup.versionCode != current.versionCode) {
                    throw new OtaException("Known-good APK backup verification failed");
                }
                replaceFile(temporaryBackup, backupApk);
            }

            replaceFile(temporaryCandidate, candidateApk);
            String transaction = UUID.randomUUID().toString();
            SharedPreferences.Editor transactionState = preferences.edit()
                    .putString(KEY_TRANSACTION, transaction)
                    .putString(KEY_PENDING_KIND, KIND_CANDIDATE)
                    .putInt(KEY_CANDIDATE_CODE, candidate.versionCode)
                    .putString(KEY_CANDIDATE_NAME, candidate.versionName)
                    .putString(KEY_STATE, OtaConstants.STATE_VALIDATING)
                    .putString(KEY_MESSAGE, "Validated signed Mirror Home update")
                    .putBoolean(KEY_PRESERVE_BACKUP, preserveBackup)
                    .putLong(KEY_UPDATED_AT, System.currentTimeMillis());
            if (!preserveBackup) {
                transactionState
                        .putInt(KEY_PREVIOUS_CODE, current.versionCode)
                        .putString(KEY_PREVIOUS_NAME, current.versionName);
            }
            if (!transactionState.commit()) {
                throw new OtaException("Unable to persist OTA transaction");
            }
            executor.execute(new Runnable() {
                @Override
                public void run() {
                    installCandidate(transaction);
                }
            });
            return snapshot();
        } catch (ApkInspector.InspectionException | IOException error) {
            validationFailed(error.getMessage(), preserveBackup);
            throw new OtaException(error.getMessage(), error);
        } catch (OtaException error) {
            validationFailed(error.getMessage(), preserveBackup);
            throw error;
        }
    }

    synchronized JSONObject submitRollback() throws OtaException {
        requireDeviceOwner();
        requireIdle();
        if (!backupApk.isFile()) {
            throw new OtaException("No known-good APK is available");
        }
        try {
            ApkInspector.Metadata backup = inspector.inspectArchive(backupApk);
            inspector.requireTrustedHome(backup);
            ApkInspector.Metadata current = inspector.installedHome();
            inspector.requireTrustedHome(current);
            if (backup.versionCode == current.versionCode) {
                throw new OtaException("Known-good APK is already installed");
            }
            String transaction = UUID.randomUUID().toString();
            if (!preferences.edit()
                    .putString(KEY_TRANSACTION, transaction)
                    .putString(KEY_PENDING_KIND, KIND_ROLLBACK)
                    .putInt(KEY_PREVIOUS_CODE, backup.versionCode)
                    .putString(KEY_PREVIOUS_NAME, backup.versionName)
                    .putString(KEY_STATE, OtaConstants.STATE_ROLLING_BACK)
                    .putString(KEY_MESSAGE, "Manual rollback requested")
                    .putLong(KEY_UPDATED_AT, System.currentTimeMillis())
                    .commit()) {
                throw new OtaException("Unable to persist rollback transaction");
            }
            acquireWakeLock();
            executor.execute(new Runnable() {
                @Override
                public void run() {
                    installRollback(transaction, "Manual rollback requested");
                }
            });
            return snapshot();
        } catch (ApkInspector.InspectionException error) {
            throw new OtaException(error.getMessage(), error);
        }
    }

    synchronized JSONObject snapshot() {
        JSONObject result = new JSONObject();
        try {
            result.put("apiVersion", 1);
            result.put("updaterVersion", BuildConfig.VERSION_NAME);
            result.put("deviceOwner", isDeviceOwner());
            result.put("heldByHome", HoldService.held());
            result.put("runtimePermissionControl", new JSONObject()
                    .put("packageName", OtaConstants.HOME_PACKAGE)
                    .put("allowlist", new JSONArray(RuntimePermissions.ALLOWLIST)));
            result.put("deviceFingerprint", Build.FINGERPRINT);
            result.put("supportedFingerprint", BuildConfig.SUPPORTED_FINGERPRINT);
            result.put("state", state());
            result.put("active", isActive(state()));
            result.put("message", preferences.getString(KEY_MESSAGE, ""));
            result.put("transactionId", nullable(
                    preferences.getString(KEY_TRANSACTION, "")));
            result.put("candidateVersionCode", nullableInt(KEY_CANDIDATE_CODE));
            result.put("candidateVersionName", nullable(
                    preferences.getString(KEY_CANDIDATE_NAME, "")));
            result.put("knownGoodVersionCode", backupApk.isFile()
                    ? preferences.getInt(KEY_PREVIOUS_CODE, -1)
                    : JSONObject.NULL);
            result.put("knownGoodVersionName", backupApk.isFile()
                    ? nullable(preferences.getString(KEY_PREVIOUS_NAME, ""))
                    : JSONObject.NULL);
            result.put("updatedAt", preferences.getLong(KEY_UPDATED_AT, 0L));
            result.put("backupAvailable", backupApk.isFile());
            result.put(
                    "preservingKnownGood",
                    preferences.getBoolean(KEY_PRESERVE_BACKUP, false));
            try {
                ApkInspector.Metadata current = inspector.installedHome();
                result.put("homeVersionCode", current.versionCode);
                result.put("homeVersionName", current.versionName);
            } catch (ApkInspector.InspectionException error) {
                result.put("homeVersionCode", JSONObject.NULL);
                result.put("homeVersionName", JSONObject.NULL);
            }
            ProcessHealth recorder = ProcessHealth.get();
            result.put("health", recorder == null ? JSONObject.NULL : recorder.snapshot());
        } catch (JSONException impossible) {
            throw new IllegalStateException("Unable to build OTA status", impossible);
        }
        return result;
    }

    void handleInstallResult(Intent intent) {
        String transaction = intent.getStringExtra("transactionId");
        String kind = intent.getStringExtra("installKind");
        int status = intent.getIntExtra(
                PackageInstaller.EXTRA_STATUS,
                PackageInstaller.STATUS_FAILURE);
        String message = intent.getStringExtra(PackageInstaller.EXTRA_STATUS_MESSAGE);
        if (!currentTransaction(transaction, kind)) {
            Log.w(TAG, "Ignoring stale package installer result");
            return;
        }
        cancelRecovery();
        if (status == PackageInstaller.STATUS_SUCCESS) {
            executor.execute(new Runnable() {
                @Override
                public void run() {
                    verifyInstalledPackage(kind, transaction);
                }
            });
        } else if (KIND_CANDIDATE.equals(kind)) {
            failIfCurrent(
                    transaction,
                    kind,
                    "Update installation failed: " + safeMessage(status, message));
        } else {
            recoveryRequiredIfCurrent(
                    transaction,
                    kind,
                    "Rollback installation failed: " + safeMessage(status, message));
        }
    }

    synchronized boolean isDeviceOwner() {
        DevicePolicyManager manager =
                (DevicePolicyManager) context.getSystemService(Context.DEVICE_POLICY_SERVICE);
        return manager != null && manager.isDeviceOwnerApp(context.getPackageName());
    }

    synchronized JSONObject permissionsSnapshot() throws OtaException, JSONException {
        try {
            return runtimePermissions.snapshot();
        } catch (SecurityException | IllegalArgumentException error) {
            Log.w(TAG, "Unable to read runtime permissions", error);
            throw new OtaException("Android rejected reading runtime permissions", error);
        }
    }

    synchronized JSONObject changePermission(RuntimePermissions.Request request)
            throws OtaException, JSONException {
        try {
            JSONObject result = runtimePermissions.change(request);
            Log.i(TAG, "Runtime permission " + request.permission
                    + (request.granted ? " granted" : " denied") + " for " + OtaConstants.HOME_PACKAGE);
            return result;
        } catch (SecurityException | IllegalArgumentException error) {
            Log.w(TAG, "Unable to change runtime permission " + request.permission, error);
            throw new OtaException("Android rejected the runtime permission change", error);
        }
    }

    private DevicePolicyManager permissionPolicyManager() throws OtaException {
        DevicePolicyManager manager =
                (DevicePolicyManager) context.getSystemService(Context.DEVICE_POLICY_SERVICE);
        if (manager == null) {
            throw new OtaException("Device policy manager is unavailable");
        }
        return manager;
    }

    private ComponentName deviceAdmin() {
        return new ComponentName(context, OtaDeviceAdminReceiver.class);
    }

    synchronized void clearDeviceOwner() throws OtaException {
        if (isActive(state())) {
            throw new OtaException("Cannot clear device owner during an OTA transaction");
        }
        DevicePolicyManager manager =
                (DevicePolicyManager) context.getSystemService(Context.DEVICE_POLICY_SERVICE);
        if (manager == null || !manager.isDeviceOwnerApp(context.getPackageName())) {
            throw new OtaException("OTA supervisor is not the device owner");
        }
        manager.clearDeviceOwnerApp(context.getPackageName());
    }

    void close() {
        executor.shutdownNow();
        releaseWakeLock();
    }

    private void installCandidate(String transaction) {
        if (!currentActiveTransaction(transaction, KIND_CANDIDATE)) {
            return;
        }
        try {
            ApkInspector.Metadata candidate = inspector.inspectArchive(candidateApk);
            installApk(
                    candidateApk,
                    KIND_CANDIDATE,
                    candidate.versionCode,
                    false,
                    transaction);
        } catch (ApkInspector.InspectionException | OtaException error) {
            failIfCurrent(
                    transaction,
                    KIND_CANDIDATE,
                    "Unable to start update installation: " + error.getMessage());
        }
    }

    private void installRollback(String transaction, String reason) {
        if (!currentActiveTransaction(transaction, KIND_ROLLBACK)
                && !currentActiveTransaction(transaction, KIND_CANDIDATE)) {
            return;
        }
        try {
            ApkInspector.Metadata backup = inspector.inspectArchive(backupApk);
            if (!setRollbackStateIfCurrent(transaction, reason)) {
                return;
            }
            installApk(
                    backupApk,
                    KIND_ROLLBACK,
                    backup.versionCode,
                    true,
                    transaction);
        } catch (ApkInspector.InspectionException | OtaException error) {
            recoveryRequiredIfCurrent(
                    transaction,
                    KIND_ROLLBACK,
                    "Unable to start rollback: " + error.getMessage());
        }
    }

    private void installApk(
            File apk,
            String kind,
            int expectedVersionCode,
            boolean allowDowngrade,
            String transaction) throws OtaException {
        if (!currentActiveTransaction(transaction, kind)) {
            return;
        }
        PackageInstaller installer = context.getPackageManager().getPackageInstaller();
        PackageInstaller.SessionParams parameters =
                new PackageInstaller.SessionParams(
                        PackageInstaller.SessionParams.MODE_FULL_INSTALL);
        parameters.setAppPackageName(OtaConstants.HOME_PACKAGE);
        parameters.setSize(apk.length());
        if (allowDowngrade) {
            enableDowngrade(parameters);
        }
        int sessionId = -1;
        try {
            sessionId = installer.createSession(parameters);
            try (PackageInstaller.Session session = installer.openSession(sessionId)) {
                try (InputStream input = new FileInputStream(apk);
                        OutputStream output =
                                session.openWrite("base.apk", 0, apk.length())) {
                    byte[] buffer = new byte[64 * 1024];
                    int count;
                    while ((count = input.read(buffer)) != -1) {
                        output.write(buffer, 0, count);
                    }
                    session.fsync(output);
                }
                synchronized (this) {
                    if (!currentActiveTransaction(transaction, kind)) {
                        installer.abandonSession(sessionId);
                        return;
                    }
                    String nextState = KIND_CANDIDATE.equals(kind)
                            ? OtaConstants.STATE_INSTALLING
                            : OtaConstants.STATE_ROLLING_BACK;
                    if (!preferences.edit()
                            .putString(KEY_STATE, nextState)
                            .putString(KEY_PENDING_KIND, kind)
                            .putString(
                                    KEY_MESSAGE,
                                    KIND_CANDIDATE.equals(kind)
                                            ? "Installing signed Mirror Home update"
                                            : "Restoring known-good Mirror Home")
                            .putLong(KEY_UPDATED_AT, System.currentTimeMillis())
                            .commit()) {
                        throw new OtaException("Unable to persist package installer state");
                    }
                }
                Intent result = new Intent(context, OtaService.class)
                        .setAction(OtaConstants.ACTION_INSTALL_RESULT)
                        .putExtra("transactionId", transaction)
                        .putExtra("installKind", kind)
                        .putExtra("expectedVersionCode", expectedVersionCode);
                PendingIntent pendingIntent = PendingIntent.getService(
                        context,
                        sessionId,
                        result,
                        PendingIntent.FLAG_UPDATE_CURRENT);
                session.commit(pendingIntent.getIntentSender());
            }
        } catch (OtaException error) {
            if (sessionId >= 0) {
                try {
                    installer.abandonSession(sessionId);
                } catch (RuntimeException ignored) {
                }
            }
            throw error;
        } catch (IOException | RuntimeException error) {
            if (sessionId >= 0) {
                try {
                    installer.abandonSession(sessionId);
                } catch (RuntimeException ignored) {
                }
            }
            throw new OtaException("PackageInstaller rejected the transaction", error);
        }
    }

    private void verifyInstalledPackage(String kind, String transaction) {
        if (!currentActiveTransaction(transaction, kind)) {
            return;
        }
        int expectedCode = KIND_CANDIDATE.equals(kind)
                ? preferences.getInt(KEY_CANDIDATE_CODE, -1)
                : preferences.getInt(KEY_PREVIOUS_CODE, -1);
        String expectedName = KIND_CANDIDATE.equals(kind)
                ? preferences.getString(KEY_CANDIDATE_NAME, "")
                : preferences.getString(KEY_PREVIOUS_NAME, "");
        if (!setHealthCheckStateIfCurrent(transaction, kind)) {
            return;
        }
        boolean healthy = awaitHealthyHome(expectedCode, expectedName, HEALTH_TIMEOUT_MS);
        if (Thread.currentThread().isInterrupted()) {
            return;
        }
        if (!currentActiveTransaction(transaction, kind)) {
            return;
        }
        if (healthy) {
            if (KIND_CANDIDATE.equals(kind)) {
                setStateIfCurrent(
                        transaction,
                        kind,
                        OtaConstants.STATE_SUCCEEDED,
                        "Mirror Home " + expectedName + " passed its health check");
            } else {
                setStateIfCurrent(
                        transaction,
                        kind,
                        OtaConstants.STATE_ROLLED_BACK,
                        "Known-good Mirror Home " + expectedName + " was restored");
            }
            releaseWakeLock();
        } else if (KIND_CANDIDATE.equals(kind)) {
            installRollback(transaction, "Updated Mirror Home failed its health check");
        } else {
            recoveryRequiredIfCurrent(
                    transaction,
                    kind,
                    "Restored Mirror Home failed its health check");
        }
    }

    private boolean awaitHealthyHome(int expectedCode, String expectedName, long timeoutMs) {
        long deadline = android.os.SystemClock.elapsedRealtime() + timeoutMs;
        long lastLaunch = 0;
        while (android.os.SystemClock.elapsedRealtime() < deadline
                && !Thread.currentThread().isInterrupted()) {
            long now = android.os.SystemClock.elapsedRealtime();
            if (now - lastLaunch >= 5000L) {
                launchHome();
                lastLaunch = now;
            }
            if (installedVersionCode() == expectedCode && homeApiHealthy(expectedName)) {
                return true;
            }
            try {
                Thread.sleep(2000L);
            } catch (InterruptedException interrupted) {
                Thread.currentThread().interrupt();
                return false;
            }
        }
        return false;
    }

    private void launchHome() {
        try {
            Intent intent = new Intent()
                    .setComponent(new ComponentName(
                            OtaConstants.HOME_PACKAGE,
                            OtaConstants.HOME_ACTIVITY))
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_CLEAR_TOP);
            context.startActivity(intent);
        } catch (RuntimeException error) {
            Log.w(TAG, "Unable to launch Mirror Home during health check", error);
        }
    }

    private boolean homeApiHealthy(String expectedVersionName) {
        HttpURLConnection connection = null;
        try {
            connection = (HttpURLConnection) new URL(
                    "http://127.0.0.1:8787/api/v1/status").openConnection();
            connection.setConnectTimeout(2000);
            connection.setReadTimeout(2000);
            connection.setUseCaches(false);
            if (connection.getResponseCode() != 200) {
                return false;
            }
            try (InputStream input = connection.getInputStream();
                    java.io.ByteArrayOutputStream output =
                            new java.io.ByteArrayOutputStream()) {
                byte[] buffer = new byte[4096];
                int count;
                while ((count = input.read(buffer)) != -1) {
                    output.write(buffer, 0, count);
                    if (output.size() > 64 * 1024) {
                        return false;
                    }
                }
                JSONObject status = new JSONObject(
                        new String(
                                output.toByteArray(),
                                java.nio.charset.StandardCharsets.UTF_8));
                return status.optInt("apiVersion", -1) == 1
                        && expectedVersionName.equals(status.optString("appVersion", ""));
            }
        } catch (Exception error) {
            return false;
        } finally {
            if (connection != null) {
                connection.disconnect();
            }
        }
    }

    private int installedVersionCode() {
        try {
            PackageInfo info = context.getPackageManager().getPackageInfo(
                    OtaConstants.HOME_PACKAGE,
                    0);
            return info.versionCode;
        } catch (PackageManager.NameNotFoundException error) {
            return -1;
        }
    }

    private synchronized void scheduleInterruptedTransactionRecovery() {
        if (!isActive(state())) {
            return;
        }
        acquireWakeLock();
        String transaction = preferences.getString(KEY_TRANSACTION, "");
        String kind = preferences.getString(KEY_PENDING_KIND, "");
        recoveryFuture = executor.schedule(new Runnable() {
            @Override
            public void run() {
                reconcileInterruptedTransaction(transaction, kind);
            }
        }, 10, TimeUnit.SECONDS);
    }

    private void reconcileInterruptedTransaction(String transaction, String kind) {
        if (!currentActiveTransaction(transaction, kind)) {
            return;
        }
        if (OtaConstants.STATE_VALIDATING.equals(state())
                && KIND_CANDIDATE.equals(kind)) {
            installCandidate(transaction);
            return;
        }
        int expectedCode = KIND_CANDIDATE.equals(kind)
                ? preferences.getInt(KEY_CANDIDATE_CODE, -1)
                : preferences.getInt(KEY_PREVIOUS_CODE, -1);
        long deadline = android.os.SystemClock.elapsedRealtime() + RECOVERY_TIMEOUT_MS;
        while (android.os.SystemClock.elapsedRealtime() < deadline
                && !Thread.currentThread().isInterrupted()
                && currentActiveTransaction(transaction, kind)) {
            if (installedVersionCode() == expectedCode) {
                verifyInstalledPackage(kind, transaction);
                return;
            }
            try {
                Thread.sleep(3000L);
            } catch (InterruptedException interrupted) {
                Thread.currentThread().interrupt();
                return;
            }
        }
        recoveryRequiredIfCurrent(
                transaction,
                kind,
                "Interrupted OTA transaction could not be reconciled");
    }

    private static void enableDowngrade(PackageInstaller.SessionParams parameters)
            throws OtaException {
        try {
            Field installFlags = PackageInstaller.SessionParams.class.getField("installFlags");
            installFlags.setInt(
                    parameters,
                    installFlags.getInt(parameters) | INSTALL_ALLOW_DOWNGRADE);
        } catch (ReflectiveOperationException error) {
            throw new OtaException("Android does not expose rollback installation support", error);
        }
    }

    private synchronized void requireDeviceOwner() throws OtaException {
        if (!isDeviceOwner()) {
            throw new OtaException("OTA supervisor is not the Android device owner");
        }
    }

    private synchronized void requireIdle() throws OtaException {
        if (isActive(state())) {
            throw new OtaException("Another OTA transaction is active");
        }
    }

    private synchronized boolean currentTransaction(String transaction, String kind) {
        return transaction != null
                && transaction.equals(preferences.getString(KEY_TRANSACTION, ""))
                && kind != null
                && kind.equals(preferences.getString(KEY_PENDING_KIND, ""));
    }

    private synchronized boolean currentActiveTransaction(String transaction, String kind) {
        return currentTransaction(transaction, kind) && isActive(state());
    }

    private synchronized void cancelRecovery() {
        if (recoveryFuture != null) {
            recoveryFuture.cancel(true);
            recoveryFuture = null;
        }
    }

    private synchronized boolean setRollbackStateIfCurrent(
            String transaction,
            String message) {
        if (transaction == null
                || !transaction.equals(preferences.getString(KEY_TRANSACTION, ""))
                || !isActive(state())) {
            return false;
        }
        return preferences.edit()
                .putString(KEY_PENDING_KIND, KIND_ROLLBACK)
                .putString(KEY_STATE, OtaConstants.STATE_ROLLING_BACK)
                .putString(KEY_MESSAGE, message)
                .putLong(KEY_UPDATED_AT, System.currentTimeMillis())
                .commit();
    }

    private synchronized boolean setHealthCheckStateIfCurrent(
            String transaction,
            String kind) {
        if (!currentActiveTransaction(transaction, kind)) {
            return false;
        }
        String message = KIND_CANDIDATE.equals(kind)
                ? "Checking updated Mirror Home"
                : "Checking restored Mirror Home";
        return preferences.edit()
                .putString(KEY_STATE, OtaConstants.STATE_HEALTH_CHECK)
                .putString(KEY_MESSAGE, message)
                .putLong(KEY_UPDATED_AT, System.currentTimeMillis())
                .commit();
    }

    private synchronized boolean setStateIfCurrent(
            String transaction,
            String kind,
            String nextState,
            String message) {
        if (!currentTransaction(transaction, kind)) {
            return false;
        }
        if (!isActive(state())) {
            return false;
        }
        SharedPreferences.Editor editor = preferences.edit()
                .putString(KEY_STATE, nextState)
                .putString(KEY_MESSAGE, message)
                .putLong(KEY_UPDATED_AT, System.currentTimeMillis());
        if (OtaConstants.STATE_SUCCEEDED.equals(nextState)
                || OtaConstants.STATE_ROLLED_BACK.equals(nextState)) {
            editor.putBoolean(KEY_PRESERVE_BACKUP, false);
        }
        return editor.commit();
    }

    private synchronized String state() {
        return preferences.getString(KEY_STATE, OtaConstants.STATE_IDLE);
    }

    private synchronized void setState(String state, String message) {
        if (!preferences.edit()
                .putString(KEY_STATE, state)
                .putString(KEY_MESSAGE, message)
                .putLong(KEY_UPDATED_AT, System.currentTimeMillis())
                .commit()) {
            Log.e(TAG, "Unable to persist OTA state " + state);
        }
    }

    private void fail(String message) {
        setState(OtaConstants.STATE_FAILED, message);
        releaseWakeLock();
    }

    private void validationFailed(String message, boolean preserveBackup) {
        if (preserveBackup) {
            recoveryRequired("Forward recovery validation failed: " + message);
        } else {
            fail("Update validation failed: " + message);
        }
    }

    private void failIfCurrent(String transaction, String kind, String message) {
        boolean preserve = preferences.getBoolean(KEY_PRESERVE_BACKUP, false);
        String targetState = candidateFailureState(preserve);
        if (setStateIfCurrent(
                transaction,
                kind,
                targetState,
                message)) {
            releaseWakeLock();
        }
    }

    private void recoveryRequired(String message) {
        preferences.edit().putBoolean(KEY_PRESERVE_BACKUP, true).commit();
        setState(OtaConstants.STATE_RECOVERY_REQUIRED, message);
        releaseWakeLock();
    }

    private void recoveryRequiredIfCurrent(
            String transaction,
            String kind,
            String message) {
        synchronized (this) {
            if (!currentTransaction(transaction, kind)) {
                return;
            }
            preferences.edit().putBoolean(KEY_PRESERVE_BACKUP, true).commit();
        }
        if (setStateIfCurrent(
                    transaction,
                    kind,
                    OtaConstants.STATE_RECOVERY_REQUIRED,
                    message)) {
            releaseWakeLock();
        }
    }

    private void acquireWakeLock() {
        if (!wakeLock.isHeld()) {
            wakeLock.acquire(10 * 60 * 1000L);
        }
    }

    private void releaseWakeLock() {
        if (wakeLock.isHeld()) {
            wakeLock.release();
        }
    }

    private static boolean isActive(String state) {
        return OtaConstants.STATE_VALIDATING.equals(state)
                || OtaConstants.STATE_INSTALLING.equals(state)
                || OtaConstants.STATE_HEALTH_CHECK.equals(state)
                || OtaConstants.STATE_ROLLING_BACK.equals(state);
    }

    static boolean shouldPreserveBackup(String state, boolean preservationLatched) {
        return preservationLatched
                || OtaConstants.STATE_RECOVERY_REQUIRED.equals(state);
    }

    static String candidateFailureState(boolean preservationLatched) {
        return preservationLatched
                ? OtaConstants.STATE_RECOVERY_REQUIRED
                : OtaConstants.STATE_FAILED;
    }

    private static Object nullable(String value) {
        return value == null || value.isEmpty() ? JSONObject.NULL : value;
    }

    private Object nullableInt(String key) {
        int value = preferences.getInt(key, -1);
        return value < 0 ? JSONObject.NULL : value;
    }

    private static String safeMessage(int status, String message) {
        return message == null || message.isEmpty()
                ? "PackageInstaller status " + status
                : message;
    }

    private static void copyFile(File source, File destination, long maximumBytes)
            throws IOException, OtaException {
        if (source == null || !source.isFile() || source.length() < 1) {
            throw new OtaException("Source APK is missing");
        }
        if (source.length() > maximumBytes) {
            throw new OtaException("APK exceeds the 32 MB limit");
        }
        if (destination.exists() && !destination.delete()) {
            throw new IOException("Unable to replace temporary APK");
        }
        long copied = 0;
        try (InputStream input = new FileInputStream(source);
                FileOutputStream output = new FileOutputStream(destination)) {
            byte[] buffer = new byte[64 * 1024];
            int count;
            while ((count = input.read(buffer)) != -1) {
                copied += count;
                if (copied > maximumBytes) {
                    throw new OtaException("APK exceeds the 32 MB limit");
                }
                output.write(buffer, 0, count);
            }
            output.getFD().sync();
        } catch (IOException | OtaException error) {
            if (destination.exists() && !destination.delete()) {
                Log.w(TAG, "Unable to delete partial OTA file " + destination);
            }
            throw error;
        }
    }

    private static void replaceFile(File source, File destination) throws IOException {
        if (destination.exists() && !destination.delete()) {
            throw new IOException("Unable to replace OTA file");
        }
        if (!source.renameTo(destination)) {
            throw new IOException("Unable to finalize OTA file");
        }
    }
}
