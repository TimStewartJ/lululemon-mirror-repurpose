package dev.mirror.repurpose;

import android.content.Context;
import android.content.Intent;
import android.net.Uri;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;

import androidx.annotation.OptIn;
import androidx.media3.common.AudioAttributes;
import androidx.media3.common.C;
import androidx.media3.common.Format;
import androidx.media3.common.MediaItem;
import androidx.media3.common.PlaybackException;
import androidx.media3.common.PlaybackParameters;
import androidx.media3.common.Player;
import androidx.media3.common.VideoSize;
import androidx.media3.common.util.UnstableApi;
import androidx.media3.datasource.DefaultDataSource;
import androidx.media3.datasource.DefaultHttpDataSource;
import androidx.media3.exoplayer.DecoderCounters;
import androidx.media3.exoplayer.DecoderReuseEvaluation;
import androidx.media3.exoplayer.ExoPlayer;
import androidx.media3.exoplayer.analytics.AnalyticsListener;
import androidx.media3.exoplayer.source.DefaultMediaSourceFactory;
import androidx.media3.exoplayer.source.MediaSource;
import androidx.media3.ui.PlayerView;

import org.json.JSONException;
import org.json.JSONObject;

import java.util.Collections;
import java.io.File;
import java.util.HashMap;
import java.util.Locale;
import java.util.Map;
import java.util.Objects;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;

@OptIn(markerClass = UnstableApi.class)
public final class MediaPlaybackManager implements Player.Listener, AnalyticsListener {
    public static final String ACTION_MEDIA_STATE_CHANGED =
            "dev.mirror.repurpose.MEDIA_STATE_CHANGED";

    public static final class PlayRequest {
        public final String url;
        public final String mimeType;
        public final String title;
        public final Map<String, String> headers;
        public final double startSeconds;
        public final double volume;
        public final double speed;

        public PlayRequest(
                String url,
                String mimeType,
                String title,
                Map<String, String> headers,
                double startSeconds,
                double volume,
                double speed) {
            this.url = url;
            this.mimeType = mimeType;
            this.title = title;
            this.headers = headers == null
                    ? Collections.emptyMap()
                    : Collections.unmodifiableMap(new HashMap<>(headers));
            this.startSeconds = startSeconds;
            this.volume = volume;
            this.speed = speed;
        }
    }

    private enum Mode {
        NONE,
        AMBIENT,
        PRESENTATION
    }

    private static volatile MediaPlaybackManager instance;

    private final Context context;
    private final Handler mainHandler = new Handler(Looper.getMainLooper());
    private final AudioAttributes mediaAudioAttributes =
            new AudioAttributes.Builder()
                    .setUsage(C.USAGE_MEDIA)
                    .setContentType(C.AUDIO_CONTENT_TYPE_MOVIE)
                    .build();
    private final ExoPlayer player;
    private volatile String currentUrl;
    private volatile String currentMimeType;
    private volatile String currentTitle;
    private volatile String lastError;
    private Mode mode = Mode.NONE;
    private PlayerView ambientView;
    private PlayerView presentationView;
    private boolean ambientEnabled;
    private boolean ambientRequestedPlaying;
    private boolean ambientPrepared;
    private File ambientSource;
    private String ambientSourceId = "";
    private boolean ambientFirstFrameRendered;
    private long ambientFirstFrameAtMs;
    private int ambientLoopCount;
    private int ambientReportedDroppedFrames;
    private String ambientDecoderName = "";
    private String ambientMimeType = "";
    private String ambientCodecs = "";
    private float ambientFrameRate;
    private int ambientVideoWidth;
    private int ambientVideoHeight;
    private String ambientLastError;
    private int ambientRetries;
    private final AmbientVideoRetry ambientRetry = new AmbientVideoRetry();
    private final Runnable retryAmbient = new Runnable() {
        @Override
        public void run() {
            // It may have been started again since, or be wanted no longer.
            if (mode == Mode.AMBIENT && ambientEnabled && !ambientPrepared) {
                ambientRetries++;
                applyAmbientState();
            }
        }
    };
    private DecoderCounters ambientDecoderCounters;
    private boolean videoRendererEnabled;

    private MediaPlaybackManager(Context context) {
        this.context = context.getApplicationContext();
        player = new ExoPlayer.Builder(this.context).build();
        player.setAudioAttributes(mediaAudioAttributes, false);
        player.addListener(this);
        player.addAnalyticsListener(this);
    }

    public static MediaPlaybackManager getInstance(Context context) {
        if (instance == null) {
            synchronized (MediaPlaybackManager.class) {
                if (instance == null) {
                    instance = new MediaPlaybackManager(context);
                }
            }
        }
        return instance;
    }

    public void attach(PlayerView view) {
        presentationView = view;
        configureView(view, true);
        updateOutputView();
    }

    public void detach(PlayerView view) {
        if (presentationView == view) {
            if (view.getPlayer() == player) {
                view.setPlayer(null);
            }
            presentationView = null;
        }
    }

    public void attachAmbient(PlayerView view) {
        ambientView = view;
        configureView(view, false);
        updateOutputView();
    }

    public void detachAmbient(PlayerView view) {
        if (ambientView == view) {
            if (view.getPlayer() == player) {
                view.setPlayer(null);
            }
            ambientView = null;
        }
    }

    public void setAmbientState(
            File source,
            String sourceId,
            boolean enabled,
            boolean playing) {
        runOnMain(() -> {
            String normalizedId = sourceId == null ? "" : sourceId;
            boolean sourceChanged = !Objects.equals(ambientSourceId, normalizedId)
                    || !sameFile(ambientSource, source);
            ambientSource = source;
            ambientSourceId = normalizedId;
            ambientEnabled = enabled && source != null && source.isFile();
            ambientRequestedPlaying = ambientEnabled && playing;
            if (sourceChanged && mode == Mode.AMBIENT) {
                player.stop();
                player.clearMediaItems();
                mode = Mode.NONE;
                ambientPrepared = false;
                ambientFirstFrameRendered = false;
                updateOutputView();
            }
            if (mode != Mode.PRESENTATION) {
                applyAmbientState();
            }
        });
    }

    public boolean play(PlayRequest request) {
        if (!InputValidator.validMediaUrl(request.url)) {
            return false;
        }
        runOnMain(() -> startPresentation(request));
        return true;
    }

    public void pause() {
        runOnMain(() -> {
            if (mode == Mode.PRESENTATION) {
                player.pause();
            }
        });
    }

    public void resume() {
        runOnMain(() -> {
            if (mode == Mode.PRESENTATION) {
                player.play();
            }
        });
    }

    public void stop() {
        runOnMain(() -> stopPresentation(null));
    }

    public void seek(double seconds) {
        runOnMain(() -> {
            if (mode == Mode.PRESENTATION) {
                player.seekTo(Math.max(0L, secondsToMillis(seconds)));
            }
        });
    }

    public void setVolume(double volume) {
        runOnMain(() -> {
            if (mode == Mode.PRESENTATION) {
                player.setVolume(clamp((float) volume, 0f, 1f));
            }
        });
    }

    public void setSpeed(double speed) {
        runOnMain(() -> {
            if (mode == Mode.PRESENTATION) {
                player.setPlaybackParameters(
                        new PlaybackParameters(clamp((float) speed, 0.25f, 4f)));
            }
        });
    }

    public boolean isPresentationActive() {
        return currentUrl != null && lastError == null;
    }

    public JSONObject snapshot() {
        return snapshotOnMainThread(false);
    }

    public JSONObject ambientSnapshot() {
        return snapshotOnMainThread(true);
    }

    @Override
    public void onPlaybackStateChanged(int playbackState) {
        if (mode == Mode.PRESENTATION && playbackState == Player.STATE_ENDED) {
            stopPresentation(null);
            return;
        }
        if (mode == Mode.PRESENTATION) {
            notifyStateChanged();
        }
    }

    @Override
    public void onIsPlayingChanged(boolean isPlaying) {
        if (mode == Mode.PRESENTATION) {
            notifyStateChanged();
        }
    }

    @Override
    public void onPlayerError(PlaybackException error) {
        if (mode == Mode.PRESENTATION) {
            stopPresentation(error.getMessage());
        } else if (mode == Mode.AMBIENT) {
            ambientLastError = error.getMessage();
            ambientPrepared = false;
            mainHandler.removeCallbacks(retryAmbient);
            mainHandler.postDelayed(retryAmbient, ambientRetry.nextDelayMs());
        }
    }

    @Override
    public void onPositionDiscontinuity(
            Player.PositionInfo oldPosition,
            Player.PositionInfo newPosition,
            int reason) {
        if (mode == Mode.AMBIENT && reason == Player.DISCONTINUITY_REASON_AUTO_TRANSITION) {
            ambientLoopCount++;
        }
    }

    @Override
    public void onVideoEnabled(EventTime eventTime, DecoderCounters counters) {
        videoRendererEnabled = true;
        if (mode == Mode.AMBIENT) {
            ambientDecoderCounters = counters;
        }
    }

    @Override
    public void onVideoDisabled(EventTime eventTime, DecoderCounters counters) {
        videoRendererEnabled = false;
    }

    @Override
    public void onVideoDecoderInitialized(
            EventTime eventTime,
            String decoderName,
            long initializedTimestampMs,
            long initializationDurationMs) {
        if (mode == Mode.AMBIENT) {
            ambientDecoderName = decoderName == null ? "" : decoderName;
        }
    }

    @Override
    public void onVideoInputFormatChanged(
            EventTime eventTime,
            Format format,
            DecoderReuseEvaluation decoderReuseEvaluation) {
        if (mode == Mode.AMBIENT) {
            ambientMimeType = format.sampleMimeType == null ? "" : format.sampleMimeType;
            ambientCodecs = format.codecs == null ? "" : format.codecs;
            ambientFrameRate = format.frameRate == Format.NO_VALUE ? 0f : format.frameRate;
        }
    }

    @Override
    public void onDroppedVideoFrames(EventTime eventTime, int droppedFrames, long elapsedMs) {
        if (mode == Mode.AMBIENT) {
            ambientReportedDroppedFrames += droppedFrames;
        }
    }

    @Override
    public void onRenderedFirstFrame(EventTime eventTime, Object output, long renderTimeMs) {
        if (mode == Mode.AMBIENT) {
            ambientFirstFrameRendered = true;
            ambientFirstFrameAtMs = SystemClock.elapsedRealtime();
            ambientRetry.succeeded();
        }
    }

    @Override
    public void onVideoSizeChanged(EventTime eventTime, VideoSize videoSize) {
        if (mode == Mode.AMBIENT) {
            ambientVideoWidth = videoSize.width;
            ambientVideoHeight = videoSize.height;
        }
    }

    private void startPresentation(PlayRequest request) {
        mode = Mode.PRESENTATION;
        ambientPrepared = false;
        currentUrl = request.url;
        currentMimeType = request.mimeType;
        currentTitle = request.title;
        lastError = null;
        player.stop();
        player.clearMediaItems();
        player.setAudioAttributes(mediaAudioAttributes, true);
        player.setTrackSelectionParameters(player.getTrackSelectionParameters()
                .buildUpon()
                .setTrackTypeDisabled(C.TRACK_TYPE_AUDIO, false)
                .build());
        player.setRepeatMode(Player.REPEAT_MODE_OFF);
        updateOutputView();

        MediaItem.Builder item = new MediaItem.Builder().setUri(request.url);
        if (request.mimeType != null && !request.mimeType.isEmpty()) {
            item.setMimeType(request.mimeType);
        }
        DefaultHttpDataSource.Factory httpDataSource =
                new DefaultHttpDataSource.Factory()
                        .setAllowCrossProtocolRedirects(false)
                        .setDefaultRequestProperties(request.headers);
        DefaultDataSource.Factory dataSource =
                new DefaultDataSource.Factory(context, httpDataSource);
        MediaSource source =
                new DefaultMediaSourceFactory(dataSource)
                        .createMediaSource(item.build());
        player.setMediaSource(
                source,
                Math.max(0L, secondsToMillis(request.startSeconds)));
        player.setVolume(clamp((float) request.volume, 0f, 1f));
        player.setPlaybackParameters(
                new PlaybackParameters(clamp((float) request.speed, 0.25f, 4f)));
        player.prepare();
        player.play();
        notifyStateChanged();
    }

    private void stopPresentation(String terminalError) {
        if (mode != Mode.PRESENTATION && currentUrl == null) {
            return;
        }
        player.stop();
        player.clearMediaItems();
        mode = Mode.NONE;
        currentUrl = null;
        currentMimeType = null;
        currentTitle = null;
        lastError = terminalError;
        updateOutputView();
        notifyStateChanged();
        applyAmbientState();
    }

    private void applyAmbientState() {
        if (!ambientEnabled) {
            if (mode == Mode.AMBIENT) {
                player.stop();
                player.clearMediaItems();
                mode = Mode.NONE;
                ambientPrepared = false;
                ambientFirstFrameRendered = false;
                updateOutputView();
            }
            return;
        }
        if (mode != Mode.AMBIENT || !ambientPrepared) {
            player.stop();
            player.clearMediaItems();
            mode = Mode.AMBIENT;
            resetAmbientTelemetry();
            player.setAudioAttributes(mediaAudioAttributes, false);
            player.setTrackSelectionParameters(player.getTrackSelectionParameters()
                    .buildUpon()
                    .setTrackTypeDisabled(C.TRACK_TYPE_AUDIO, true)
                    .build());
            player.setRepeatMode(Player.REPEAT_MODE_ONE);
            player.setVolume(0f);
            player.setPlaybackParameters(new PlaybackParameters(1f));
            updateOutputView();
            player.setMediaItem(MediaItem.fromUri(Uri.fromFile(ambientSource)));
            player.prepare();
            ambientPrepared = true;
        }
        if (ambientRequestedPlaying) {
            player.play();
        } else {
            player.pause();
        }
    }

    private void updateOutputView() {
        PlayerView target = mode == Mode.AMBIENT ? ambientView
                : mode == Mode.PRESENTATION ? presentationView : null;
        if (ambientView != null && ambientView != target && ambientView.getPlayer() == player) {
            ambientView.setPlayer(null);
        }
        if (presentationView != null
                && presentationView != target
                && presentationView.getPlayer() == player) {
            presentationView.setPlayer(null);
        }
        if (target != null && target.getPlayer() != player) {
            target.setPlayer(player);
        }
    }

    private void resetAmbientTelemetry() {
        ambientLoopCount = 0;
        ambientReportedDroppedFrames = 0;
        ambientDecoderCounters = null;
        ambientDecoderName = "";
        ambientMimeType = "";
        ambientCodecs = "";
        ambientFrameRate = 0f;
        ambientVideoWidth = 0;
        ambientVideoHeight = 0;
        ambientLastError = null;
        ambientFirstFrameRendered = false;
    }

    private void configureView(PlayerView view, boolean keepScreenOn) {
        view.setUseController(false);
        view.setKeepScreenOn(keepScreenOn);
    }

    private JSONObject snapshotOnMainThread(boolean ambient) {
        if (Looper.myLooper() == Looper.getMainLooper()) {
            return ambient ? ambientSnapshotOnMain() : presentationSnapshotOnMain();
        }
        AtomicReference<JSONObject> result = new AtomicReference<>();
        CountDownLatch latch = new CountDownLatch(1);
        mainHandler.post(() -> {
            result.set(ambient ? ambientSnapshotOnMain() : presentationSnapshotOnMain());
            latch.countDown();
        });
        try {
            if (!latch.await(1, TimeUnit.SECONDS)) {
                return errorSnapshot("Timed out reading player state");
            }
        } catch (InterruptedException error) {
            Thread.currentThread().interrupt();
            return errorSnapshot("Interrupted while reading player state");
        }
        return result.get();
    }

    private JSONObject presentationSnapshotOnMain() {
        JSONObject result = new JSONObject();
        try {
            result.put("state", presentationStateName());
            result.put("url", currentUrl == null ? JSONObject.NULL : currentUrl);
            result.put("mimeType", currentMimeType == null ? JSONObject.NULL : currentMimeType);
            result.put("title", currentTitle == null ? JSONObject.NULL : currentTitle);
            result.put(
                    "positionSeconds",
                    mode == Mode.PRESENTATION ? player.getCurrentPosition() / 1000.0 : 0);
            long duration = mode == Mode.PRESENTATION ? player.getDuration() : C.TIME_UNSET;
            result.put(
                    "durationSeconds",
                    duration == C.TIME_UNSET ? JSONObject.NULL : duration / 1000.0);
            result.put("volume", mode == Mode.PRESENTATION ? player.getVolume() : 0);
            result.put(
                    "speed",
                    mode == Mode.PRESENTATION ? player.getPlaybackParameters().speed : 1);
            result.put("error", lastError == null ? JSONObject.NULL : lastError);
        } catch (JSONException impossible) {
            return errorSnapshot(impossible.toString());
        }
        return result;
    }

    private JSONObject ambientSnapshotOnMain() {
        int renderedFrames = 0;
        int decoderDroppedFrames = 0;
        int maxConsecutiveDroppedFrames = 0;
        if (ambientDecoderCounters != null) {
            ambientDecoderCounters.ensureUpdated();
            renderedFrames = ambientDecoderCounters.renderedOutputBufferCount;
            decoderDroppedFrames = ambientDecoderCounters.droppedBufferCount;
            maxConsecutiveDroppedFrames =
                    ambientDecoderCounters.maxConsecutiveDroppedBufferCount;
        }
        long position = mode == Mode.AMBIENT ? Math.max(0L, player.getCurrentPosition()) : 0;
        long duration = mode == Mode.AMBIENT ? player.getDuration() : C.TIME_UNSET;
        int droppedFrames = Math.max(ambientReportedDroppedFrames, decoderDroppedFrames);
        int completedFrames = renderedFrames + droppedFrames;
        JSONObject result = new JSONObject();
        try {
            result.put("enabled", ambientEnabled);
            result.put("sourceId", ambientSourceId);
            result.put("requestedPlaying", ambientRequestedPlaying);
            result.put("prepared", ambientPrepared);
            result.put("decoderActive", mode == Mode.AMBIENT && videoRendererEnabled);
            result.put("attached", ambientView != null && ambientView.getPlayer() == player);
            result.put("state", ambientStateName());
            result.put("playing", mode == Mode.AMBIENT && player.isPlaying());
            result.put("firstFrameRendered", ambientFirstFrameRendered);
            result.put(
                    "firstFrameAgeMs",
                    ambientFirstFrameRendered
                            ? SystemClock.elapsedRealtime() - ambientFirstFrameAtMs
                            : JSONObject.NULL);
            result.put("positionMs", position);
            result.put("durationMs", duration == C.TIME_UNSET ? JSONObject.NULL : duration);
            result.put("season", seasonName(position, duration));
            result.put("loopCount", ambientLoopCount);
            result.put("decoderName", ambientDecoderName);
            result.put("hardwareDecoder", isLikelyHardwareDecoder(ambientDecoderName));
            result.put("mimeType", ambientMimeType);
            result.put("codecs", ambientCodecs);
            result.put("frameRate", ambientFrameRate);
            result.put("width", ambientVideoWidth);
            result.put("height", ambientVideoHeight);
            result.put("renderedFrames", renderedFrames);
            result.put("droppedFrames", droppedFrames);
            result.put(
                    "droppedFramePercent",
                    completedFrames == 0 ? 0 : droppedFrames * 100.0 / completedFrames);
            result.put("maxConsecutiveDroppedFrames", maxConsecutiveDroppedFrames);
            result.put(
                    "error",
                    ambientLastError == null ? JSONObject.NULL : ambientLastError);
            result.put("retries", ambientRetries);
        } catch (JSONException impossible) {
            return errorSnapshot(impossible.toString());
        }
        return result;
    }

    private String presentationStateName() {
        if (lastError != null) {
            return "error";
        }
        if (currentUrl == null) {
            return "idle";
        }
        if (player.isPlaying()) {
            return "playing";
        }
        if (player.getPlaybackState() == Player.STATE_BUFFERING) {
            return "buffering";
        }
        return "paused";
    }

    private String ambientStateName() {
        if (ambientLastError != null) {
            return "error";
        }
        if (!ambientEnabled) {
            return "disabled";
        }
        if (mode != Mode.AMBIENT) {
            return "waiting";
        }
        switch (player.getPlaybackState()) {
            case Player.STATE_BUFFERING:
                return "buffering";
            case Player.STATE_READY:
                return "ready";
            case Player.STATE_ENDED:
                return "ended";
            default:
                return "idle";
        }
    }

    private void notifyStateChanged() {
        Intent intent = new Intent(ACTION_MEDIA_STATE_CHANGED);
        intent.setPackage(context.getPackageName());
        context.sendBroadcast(intent);
    }

    private void runOnMain(Runnable action) {
        if (Looper.myLooper() == Looper.getMainLooper()) {
            action.run();
        } else {
            mainHandler.post(action);
        }
    }

    private static String seasonName(long positionMs, long durationMs) {
        if (positionMs < 0 || durationMs <= 0 || durationMs == C.TIME_UNSET) {
            return "";
        }
        String[] seasons = {"spring", "summer", "autumn", "winter"};
        int index = (int) Math.min(3, positionMs * seasons.length / durationMs);
        return seasons[index];
    }

    private static boolean isLikelyHardwareDecoder(String name) {
        if (name == null || name.isEmpty()) {
            return false;
        }
        String normalized = name.toLowerCase(Locale.US);
        return !normalized.contains("google")
                && !normalized.contains("android")
                && !normalized.contains("software")
                && !normalized.contains("ffmpeg");
    }

    private static boolean sameFile(File first, File second) {
        if (first == null || second == null) {
            return first == second;
        }
        return first.getAbsolutePath().equals(second.getAbsolutePath());
    }

    private static JSONObject errorSnapshot(String message) {
        JSONObject result = new JSONObject();
        try {
            result.put("state", "error");
            result.put("error", message);
        } catch (JSONException ignored) {
        }
        return result;
    }

    private static long secondsToMillis(double seconds) {
        return (long) (seconds * 1000.0);
    }

    private static float clamp(float value, float minimum, float maximum) {
        return Math.max(minimum, Math.min(maximum, value));
    }
}
