package dev.mirror.repurpose;

import android.content.Context;
import android.content.Intent;
import android.os.Handler;
import android.os.Looper;

import androidx.media3.common.AudioAttributes;
import androidx.media3.common.C;
import androidx.media3.common.MediaItem;
import androidx.media3.common.PlaybackException;
import androidx.media3.common.PlaybackParameters;
import androidx.media3.common.Player;
import androidx.media3.exoplayer.ExoPlayer;
import androidx.media3.ui.PlayerView;

import org.json.JSONException;
import org.json.JSONObject;

import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;

public final class MediaPlaybackManager implements Player.Listener {
    public static final String ACTION_MEDIA_STATE_CHANGED =
            "dev.mirror.repurpose.MEDIA_STATE_CHANGED";

    public static final class PlayRequest {
        public final String url;
        public final String mimeType;
        public final String title;
        public final double startSeconds;
        public final double volume;
        public final double speed;

        public PlayRequest(
                String url,
                String mimeType,
                String title,
                double startSeconds,
                double volume,
                double speed) {
            this.url = url;
            this.mimeType = mimeType;
            this.title = title;
            this.startSeconds = startSeconds;
            this.volume = volume;
            this.speed = speed;
        }
    }

    private static volatile MediaPlaybackManager instance;

    private final Context context;
    private final Handler mainHandler = new Handler(Looper.getMainLooper());
    private final ExoPlayer player;
    private volatile String currentUrl;
    private volatile String currentMimeType;
    private volatile String currentTitle;
    private volatile String lastError;

    private MediaPlaybackManager(Context context) {
        this.context = context.getApplicationContext();
        player = new ExoPlayer.Builder(this.context).build();
        player.setAudioAttributes(
                new AudioAttributes.Builder()
                        .setUsage(C.USAGE_MEDIA)
                        .setContentType(C.AUDIO_CONTENT_TYPE_MOVIE)
                        .build(),
                true);
        player.addListener(this);
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
        view.setPlayer(player);
        view.setUseController(false);
        view.setKeepScreenOn(true);
    }

    public void detach(PlayerView view) {
        if (view.getPlayer() == player) {
            view.setPlayer(null);
        }
    }

    public boolean play(PlayRequest request) {
        if (!InputValidator.validMediaUrl(request.url)) {
            return false;
        }
        mainHandler.post(new Runnable() {
            @Override
            public void run() {
                MediaItem.Builder item = new MediaItem.Builder().setUri(request.url);
                if (request.mimeType != null && !request.mimeType.isEmpty()) {
                    item.setMimeType(request.mimeType);
                }
                currentUrl = request.url;
                currentMimeType = request.mimeType;
                currentTitle = request.title;
                lastError = null;
                player.setMediaItem(item.build(), Math.max(0L, secondsToMillis(request.startSeconds)));
                player.setVolume(clamp((float) request.volume, 0f, 1f));
                player.setPlaybackParameters(
                        new PlaybackParameters(clamp((float) request.speed, 0.25f, 4f)));
                player.prepare();
                player.play();
                notifyStateChanged();
            }
        });
        return true;
    }

    public void pause() {
        mainHandler.post(player::pause);
    }

    public void resume() {
        mainHandler.post(player::play);
    }

    public void stop() {
        mainHandler.post(new Runnable() {
            @Override
            public void run() {
                player.stop();
                player.clearMediaItems();
                currentUrl = null;
                currentMimeType = null;
                currentTitle = null;
                lastError = null;
                notifyStateChanged();
            }
        });
    }

    public void seek(double seconds) {
        mainHandler.post(() -> player.seekTo(Math.max(0L, secondsToMillis(seconds))));
    }

    public void setVolume(double volume) {
        mainHandler.post(() -> player.setVolume(clamp((float) volume, 0f, 1f)));
    }

    public void setSpeed(double speed) {
        mainHandler.post(
                () -> player.setPlaybackParameters(
                        new PlaybackParameters(clamp((float) speed, 0.25f, 4f))));
    }

    public boolean isPresentationActive() {
        return currentUrl != null && lastError == null;
    }

    public JSONObject snapshot() {
        if (Looper.myLooper() == Looper.getMainLooper()) {
            return snapshotOnMain();
        }
        AtomicReference<JSONObject> result = new AtomicReference<>();
        CountDownLatch latch = new CountDownLatch(1);
        mainHandler.post(new Runnable() {
            @Override
            public void run() {
                result.set(snapshotOnMain());
                latch.countDown();
            }
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

    @Override
    public void onPlaybackStateChanged(int playbackState) {
        if (playbackState == Player.STATE_ENDED) {
            currentUrl = null;
        }
        notifyStateChanged();
    }

    @Override
    public void onIsPlayingChanged(boolean isPlaying) {
        notifyStateChanged();
    }

    @Override
    public void onPlayerError(PlaybackException error) {
        lastError = error.getMessage();
        notifyStateChanged();
    }

    private JSONObject snapshotOnMain() {
        JSONObject result = new JSONObject();
        try {
            result.put("state", stateName());
            result.put("url", currentUrl == null ? JSONObject.NULL : currentUrl);
            result.put("mimeType", currentMimeType == null ? JSONObject.NULL : currentMimeType);
            result.put("title", currentTitle == null ? JSONObject.NULL : currentTitle);
            result.put("positionSeconds", player.getCurrentPosition() / 1000.0);
            long duration = player.getDuration();
            result.put(
                    "durationSeconds",
                    duration == C.TIME_UNSET ? JSONObject.NULL : duration / 1000.0);
            result.put("volume", player.getVolume());
            result.put("speed", player.getPlaybackParameters().speed);
            result.put("error", lastError == null ? JSONObject.NULL : lastError);
        } catch (JSONException impossible) {
            return errorSnapshot(impossible.toString());
        }
        return result;
    }

    private JSONObject errorSnapshot(String message) {
        JSONObject result = new JSONObject();
        try {
            result.put("state", "error");
            result.put("error", message);
        } catch (JSONException ignored) {
            // Both keys and values are valid JSON strings.
        }
        return result;
    }

    private String stateName() {
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

    private void notifyStateChanged() {
        Intent intent = new Intent(ACTION_MEDIA_STATE_CHANGED);
        intent.setPackage(context.getPackageName());
        context.sendBroadcast(intent);
    }

    private static long secondsToMillis(double seconds) {
        return (long) (seconds * 1000.0);
    }

    private static float clamp(float value, float minimum, float maximum) {
        return Math.max(minimum, Math.min(maximum, value));
    }
}
