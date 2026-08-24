package dev.mirror.repurpose;

import android.Manifest;
import android.content.Context;
import android.content.pm.PackageManager;
import android.graphics.ImageFormat;
import android.graphics.SurfaceTexture;
import android.hardware.Camera;
import android.os.Handler;
import android.os.HandlerThread;
import android.os.Looper;
import android.os.SystemClock;
import android.util.Log;

import org.json.JSONException;
import org.json.JSONObject;

import java.io.IOException;
import java.util.List;

@SuppressWarnings("deprecation")
final class MotionDetectionManager {
    interface Listener {
        void onMotionDetected(long elapsedRealtime);

        void onMotionStateChanged();
    }

    private static final String TAG = "MotionDetection";
    private static final long FRAME_INTERVAL_MS = 500L;
    private static final long HEALTH_INTERVAL_MS = 5_000L;
    private static final long FRAME_STALL_TIMEOUT_MS = 15_000L;
    private static final long RETRY_INTERVAL_MS = 30_000L;

    private final Context context;
    private final ConfigStore configStore;
    private final Listener listener;
    private final Handler mainHandler = new Handler(Looper.getMainLooper());
    private final HandlerThread cameraThread = new HandlerThread("mirror-motion-camera");
    private final MotionFrameAnalyzer analyzer = new MotionFrameAnalyzer();
    private final int cameraId;
    private final Runnable retry = new Runnable() {
        @Override
        public void run() {
            reconcile();
        }
    };
    private final Runnable healthCheck = new Runnable() {
        @Override
        public void run() {
            if (camera == null) {
                return;
            }
            long now = SystemClock.elapsedRealtime();
            long newestFrame = lastFrameElapsed;
            long reference = newestFrame == 0 ? cameraStartedElapsed : newestFrame;
            if (reference > 0 && now - reference > FRAME_STALL_TIMEOUT_MS) {
                handleFailure("Camera preview stopped delivering frames", null);
                return;
            }
            cameraHandler.postDelayed(this, HEALTH_INTERVAL_MS);
        }
    };

    private Handler cameraHandler;
    private Camera camera;
    private SurfaceTexture previewTexture;
    private volatile String state = "disabled";
    private volatile String error = "";
    private volatile boolean monitoring;
    private volatile int sensitivity = 6;
    private volatile int previewWidth;
    private volatile int previewHeight;
    private volatile double lastScore;
    private volatile long lastFrameAt;
    private volatile long lastFrameElapsed;
    private volatile long lastMotionAt;
    private long cameraStartedElapsed;
    private long lastProcessedElapsed;

    MotionDetectionManager(Context context, Listener listener) {
        this.context = context.getApplicationContext();
        this.listener = listener;
        configStore = new ConfigStore(this.context);
        cameraId = findCameraId();
        cameraThread.start();
        cameraHandler = new Handler(cameraThread.getLooper());
    }

    void refresh() {
        cameraHandler.post(new Runnable() {
            @Override
            public void run() {
                reconcile();
            }
        });
    }

    boolean isMonitoring() {
        long frameElapsed = lastFrameElapsed;
        return monitoring
                && frameElapsed > 0
                && SystemClock.elapsedRealtime() - frameElapsed <= FRAME_STALL_TIMEOUT_MS;
    }

    boolean hasCamera() {
        return cameraId >= 0;
    }

    boolean hasPermission() {
        return android.os.Build.VERSION.SDK_INT < 23
                || context.checkSelfPermission(Manifest.permission.CAMERA)
                == PackageManager.PERMISSION_GRANTED;
    }

    JSONObject snapshot() throws JSONException {
        long motionAt = lastMotionAt;
        long frameAt = lastFrameAt;
        long frameElapsed = lastFrameElapsed;
        boolean healthy = isMonitoring();
        return new JSONObject()
                .put("available", hasCamera())
                .put("permissionGranted", hasPermission())
                .put("monitoring", healthy)
                .put("state", state)
                .put("error", error.isEmpty() ? JSONObject.NULL : error)
                .put("cameraId", cameraId < 0 ? JSONObject.NULL : cameraId)
                .put("previewWidth", previewWidth == 0 ? JSONObject.NULL : previewWidth)
                .put("previewHeight", previewHeight == 0 ? JSONObject.NULL : previewHeight)
                .put("lastFrameAt", frameAt == 0 ? JSONObject.NULL : frameAt)
                .put(
                        "lastFrameAgeSeconds",
                        frameElapsed == 0
                                ? JSONObject.NULL
                                : Math.max(
                                        0L,
                                        (SystemClock.elapsedRealtime() - frameElapsed) / 1000L))
                .put("lastMotionAt", motionAt == 0 ? JSONObject.NULL : motionAt)
                .put(
                        "lastMotionAgeSeconds",
                        motionAt == 0
                                ? JSONObject.NULL
                                : Math.max(0L, (System.currentTimeMillis() - motionAt) / 1000L))
                .put("score", Math.round(lastScore * 10d) / 10d);
    }

    private void reconcile() {
        cameraHandler.removeCallbacks(retry);
        sensitivity = configStore.getMotionSensitivity();
        if (!configStore.isMotionEnabled()) {
            stopCamera();
            publishState("disabled", "", false);
            return;
        }
        if (!hasCamera()) {
            stopCamera();
            publishState("unavailable", "No camera was detected", false);
            return;
        }
        if (!hasPermission()) {
            stopCamera();
            publishState("permission_required", "Camera permission is required", false);
            return;
        }
        if (camera != null) {
            return;
        }
        startCamera();
    }

    private void startCamera() {
        publishState("starting", "", false);
        try {
            Camera opened = Camera.open(cameraId);
            camera = opened;
            opened.setErrorCallback(new Camera.ErrorCallback() {
                @Override
                public void onError(int errorCode, Camera source) {
                    if (source == camera) {
                        handleFailure("Camera reported error " + errorCode, null);
                    }
                }
            });
            Camera.Parameters parameters = opened.getParameters();
            Camera.Size selectedSize = smallestPreviewSize(parameters.getSupportedPreviewSizes());
            if (selectedSize != null) {
                parameters.setPreviewSize(selectedSize.width, selectedSize.height);
            }
            List<Integer> formats = parameters.getSupportedPreviewFormats();
            if (formats != null && formats.contains(ImageFormat.NV21)) {
                parameters.setPreviewFormat(ImageFormat.NV21);
            }
            Integer frameRate = lowestFrameRate(parameters.getSupportedPreviewFrameRates());
            if (frameRate != null) {
                parameters.setPreviewFrameRate(frameRate);
            }
            opened.setParameters(parameters);

            Camera.Parameters applied = opened.getParameters();
            Camera.Size appliedSize = applied.getPreviewSize();
            int format = applied.getPreviewFormat();
            int bitsPerPixel = ImageFormat.getBitsPerPixel(format);
            if (appliedSize == null || bitsPerPixel <= 0) {
                handleFailure("Camera returned an unsupported preview format", null);
                return;
            }

            SurfaceTexture texture = new SurfaceTexture(0);
            texture.setDefaultBufferSize(appliedSize.width, appliedSize.height);
            opened.setPreviewTexture(texture);

            previewTexture = texture;
            previewWidth = appliedSize.width;
            previewHeight = appliedSize.height;
            lastProcessedElapsed = 0;
            lastFrameElapsed = 0;
            cameraStartedElapsed = SystemClock.elapsedRealtime();
            analyzer.reset();

            int bufferBytes =
                    (appliedSize.width * appliedSize.height * bitsPerPixel + 7) / 8 + 1;
            opened.setPreviewCallbackWithBuffer(new Camera.PreviewCallback() {
                @Override
                public void onPreviewFrame(byte[] data, Camera source) {
                    processFrame(data, source);
                }
            });
            opened.addCallbackBuffer(new byte[bufferBytes]);
            opened.addCallbackBuffer(new byte[bufferBytes]);
            opened.startPreview();
            cameraHandler.removeCallbacks(healthCheck);
            cameraHandler.postDelayed(healthCheck, HEALTH_INTERVAL_MS);
            Log.i(
                    TAG,
                    "Motion monitoring started at "
                            + appliedSize.width
                            + "x"
                            + appliedSize.height
                            + " @ "
                            + (frameRate == null ? "default" : frameRate)
                            + " fps");
        } catch (IOException cameraError) {
            handleFailure("Unable to attach the camera preview", cameraError);
        } catch (RuntimeException cameraError) {
            handleFailure("Unable to start the camera", cameraError);
        }
    }

    private void processFrame(byte[] data, Camera source) {
        if (source != camera) {
            return;
        }
        try {
            long now = SystemClock.elapsedRealtime();
            if (now - lastProcessedElapsed >= FRAME_INTERVAL_MS) {
                lastProcessedElapsed = now;
                MotionFrameAnalyzer.Result result =
                        analyzer.analyze(data, previewWidth, previewHeight, sensitivity);
                lastFrameElapsed = now;
                lastScore = result.score;
                lastFrameAt = System.currentTimeMillis();
                if (!monitoring) {
                    publishState("monitoring", "", true);
                }
                if (result.calibrated && result.motion) {
                    lastMotionAt = lastFrameAt;
                    mainHandler.post(new Runnable() {
                        @Override
                        public void run() {
                            listener.onMotionDetected(now);
                        }
                    });
                }
            }
        } catch (IllegalArgumentException frameError) {
            handleFailure("Camera returned an invalid preview frame", frameError);
        } finally {
            if (source == camera) {
                source.addCallbackBuffer(data);
            }
        }
    }

    private void handleFailure(String message, Exception cause) {
        if (cause == null) {
            Log.e(TAG, message);
        } else {
            Log.e(TAG, message, cause);
        }
        stopCamera();
        publishState("error", message, false);
        if (configStore.isMotionEnabled()) {
            cameraHandler.postDelayed(retry, RETRY_INTERVAL_MS);
        }
    }

    private void stopCamera() {
        cameraHandler.removeCallbacks(healthCheck);
        Camera current = camera;
        camera = null;
        if (current != null) {
            try {
                current.setPreviewCallbackWithBuffer(null);
                current.stopPreview();
            } catch (RuntimeException stopError) {
                Log.w(TAG, "Camera preview was already stopped", stopError);
            }
            try {
                current.release();
            } catch (RuntimeException releaseError) {
                Log.w(TAG, "Unable to release camera cleanly", releaseError);
            }
        }
        SurfaceTexture texture = previewTexture;
        previewTexture = null;
        if (texture != null) {
            texture.release();
        }
        monitoring = false;
        previewWidth = 0;
        previewHeight = 0;
        lastProcessedElapsed = 0;
        lastFrameElapsed = 0;
        cameraStartedElapsed = 0;
        lastScore = 0;
        analyzer.reset();
    }

    private void publishState(String nextState, String nextError, boolean nextMonitoring) {
        boolean changed = !state.equals(nextState)
                || !error.equals(nextError)
                || monitoring != nextMonitoring;
        state = nextState;
        error = nextError;
        monitoring = nextMonitoring;
        if (changed) {
            mainHandler.post(new Runnable() {
                @Override
                public void run() {
                    listener.onMotionStateChanged();
                }
            });
        }
    }

    private static int findCameraId() {
        try {
            int count = Camera.getNumberOfCameras();
            int firstCamera = count == 0 ? -1 : 0;
            Camera.CameraInfo info = new Camera.CameraInfo();
            for (int id = 0; id < count; id++) {
                Camera.getCameraInfo(id, info);
                if (info.facing == Camera.CameraInfo.CAMERA_FACING_FRONT) {
                    return id;
                }
            }
            return firstCamera;
        } catch (RuntimeException cameraError) {
            Log.e(TAG, "Unable to enumerate cameras", cameraError);
            return -1;
        }
    }

    private static Camera.Size smallestPreviewSize(List<Camera.Size> sizes) {
        Camera.Size selected = null;
        if (sizes == null) {
            return null;
        }
        for (Camera.Size size : sizes) {
            if (selected == null || size.width * size.height < selected.width * selected.height) {
                selected = size;
            }
        }
        return selected;
    }

    private static Integer lowestFrameRate(List<Integer> frameRates) {
        Integer selected = null;
        if (frameRates == null) {
            return null;
        }
        for (Integer frameRate : frameRates) {
            if (frameRate != null
                    && frameRate > 0
                    && (selected == null || frameRate < selected)) {
                selected = frameRate;
            }
        }
        return selected;
    }
}
