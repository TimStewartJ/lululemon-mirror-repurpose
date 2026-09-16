package dev.mirror.repurpose;

import android.content.Context;
import android.content.SharedPreferences;
import android.graphics.Bitmap;
import android.media.MediaCodecInfo;
import android.media.MediaCodecList;
import android.media.MediaExtractor;
import android.media.MediaFormat;
import android.media.MediaMetadataRetriever;
import android.util.Log;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.net.URLDecoder;
import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.Comparator;
import java.util.List;
import java.util.Locale;
import java.util.TimeZone;
import java.util.UUID;

public final class BackgroundVideoLibrary {
    public static final long MAX_VIDEO_BYTES = 256L * 1024L * 1024L;
    public static final long MAX_LIBRARY_BYTES = 768L * 1024L * 1024L;
    public static final long MIN_FREE_BYTES = 512L * 1024L * 1024L;
    public static final int MAX_VIDEOS = 12;

    private static final String TAG = "BackgroundVideos";
    private static final String PREFERENCES = "mirror_home_background_videos";
    private static final String KEY_ACTIVE = "active_id";
    private static final String KEY_PREVIOUS = "previous_id";
    private static final String KEY_SCHEDULE = "schedule_v1";
    private static final String KEY_HOLD_ID = "hold_id";
    private static final String KEY_HOLD_UNTIL = "hold_until_ms";
    private static final int MAX_METADATA_BYTES = 64 * 1024;
    private static final long MAX_DURATION_MS = 6L * 60L * 60L * 1000L;
    private static final int MAX_DIMENSION = 1920;
    private static final long MAX_PIXELS = 1920L * 1080L;
    private static final float MAX_FRAME_RATE = 30.5f;
    private static final int AVC_LEVEL_4_MAX_BIT_RATE = 20_000_000;
    private static final int MAX_BIT_RATE = AVC_LEVEL_4_MAX_BIT_RATE;
    private static final long AVC_LEVEL_4_MAX_FRAME_MACROBLOCKS = 8192L;
    private static final long AVC_LEVEL_4_MAX_MACROBLOCKS_PER_SECOND = 245_760L;
    private static final int POSTER_EDGE = 480;
    private static final int MAX_FRAME_SAMPLES = 1_000_000;

    static final class FrameRateStats {
        final float nominal;
        final float maximumSustained;

        FrameRateStats(float nominal, float maximumSustained) {
            this.nominal = nominal;
            this.maximumSustained = maximumSustained;
        }
    }

    static final class AvcSpec {
        final int profileIdc;
        final int levelIdc;
        final int codecProfile;
        final int codecLevel;

        AvcSpec(int profileIdc, int levelIdc, int codecProfile, int codecLevel) {
            this.profileIdc = profileIdc;
            this.levelIdc = levelIdc;
            this.codecProfile = codecProfile;
            this.codecLevel = codecLevel;
        }
    }

    public enum DeleteResult {
        DELETED,
        ACTIVE,
        SCHEDULED,
        NOT_FOUND
    }

    public static final class StoreResult {
        public final BackgroundVideoMetadata video;
        public final boolean duplicate;

        StoreResult(BackgroundVideoMetadata video, boolean duplicate) {
            this.video = video;
            this.duplicate = duplicate;
        }
    }

    private static volatile BackgroundVideoLibrary instance;

    private final SharedPreferences preferences;
    private final ConfigStore configStore;
    private final File root;
    private final File objects;
    private final File posters;
    private final File incoming;
    private final Object uploadLock = new Object();

    private BackgroundVideoLibrary(Context context) {
        Context appContext = context.getApplicationContext();
        preferences = appContext.getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE);
        configStore = new ConfigStore(appContext);
        root = new File(appContext.getFilesDir(), "background-videos");
        objects = new File(root, "objects");
        posters = new File(root, "posters");
        incoming = new File(root, "incoming");
        requireDirectory(root);
        requireDirectory(objects);
        requireDirectory(posters);
        requireDirectory(incoming);
        cleanupIncoming();
        reconcileSelection();
    }

    public static BackgroundVideoLibrary getInstance(Context context) {
        if (instance == null) {
            synchronized (BackgroundVideoLibrary.class) {
                if (instance == null) {
                    instance = new BackgroundVideoLibrary(context);
                }
            }
        }
        return instance;
    }

    public StoreResult store(
            String encodedName,
            InputStream source,
            long contentLength) throws IOException {
        String name = normalizeName(encodedName);
        if (name == null) {
            throw new IOException("Background video name must end in .mp4");
        }
        if (source == null
                || contentLength < 1
                || contentLength > MAX_VIDEO_BYTES) {
            throw new IOException("Background video exceeds the 256 MiB limit or is incomplete");
        }

        synchronized (uploadLock) {
            if (root.getUsableSpace() - contentLength < MIN_FREE_BYTES) {
                throw new IOException("Not enough free space to keep a 512 MiB device reserve");
            }
            File staged = new File(incoming, UUID.randomUUID().toString() + ".part");
            boolean keepPoster = false;
            String stagedId = "";
            try {
                String id = writeIncoming(source, staged, contentLength);
                stagedId = id;
                synchronized (this) {
                    BackgroundVideoMetadata existing = readMetadata(id);
                    if (existing != null
                            && objectFile(id).isFile()
                            && objectFile(id).length() == existing.sizeBytes) {
                        keepPoster = true;
                        return new StoreResult(existing, true);
                    }
                    List<BackgroundVideoMetadata> current = records();
                    if (current.size() >= MAX_VIDEOS) {
                        throw new IOException(
                                "Background video library already contains 12 videos");
                    }
                    if (totalBytes(current) + contentLength > MAX_LIBRARY_BYTES) {
                        throw new IOException(
                                "Background video library exceeds the 768 MiB limit");
                    }
                }

                BackgroundVideoMetadata metadata =
                        inspect(staged, id, name, contentLength);
                writePoster(staged, id, metadata.durationMs);
                File destination;
                synchronized (this) {
                    destination = objectFile(id);
                    if (destination.exists() && !destination.delete()) {
                        throw new IOException("Unable to replace orphaned background video");
                    }
                    if (!staged.renameTo(destination)) {
                        throw new IOException("Unable to promote validated background video");
                    }
                    try {
                        writeMetadata(metadata);
                    } catch (IOException | JSONException error) {
                        deleteQuietly(destination);
                        deleteQuietly(metadataFile(id));
                        throw error instanceof IOException
                                ? (IOException) error
                                : new IOException(
                                        "Unable to persist background video metadata",
                                        error);
                    }
                }
                keepPoster = true;
                return new StoreResult(metadata, false);
            } finally {
                deleteQuietly(staged);
                if (!keepPoster && BackgroundVideoSelection.validId(stagedId)) {
                    deleteQuietly(posterFile(stagedId));
                }
            }
        }
    }

    public synchronized boolean activate(String id) throws IOException {
        BackgroundVideoMetadata metadata = readMetadata(id);
        if (metadata == null || !objectFile(id).isFile()) {
            return false;
        }
        BackgroundVideoSelection next = selection().activate(id);
        persistSelection(next);
        holdUntilNextChange(id);
        return true;
    }

    public synchronized boolean rollback() throws IOException {
        BackgroundVideoSelection current = selection();
        if (!current.canRollback()
                || readMetadata(current.previousId) == null
                || !objectFile(current.previousId).isFile()) {
            return false;
        }
        BackgroundVideoSelection next = current.rollback();
        persistSelection(next);
        holdUntilNextChange(next.activeId);
        return true;
    }

    public synchronized DeleteResult delete(String id) throws IOException {
        BackgroundVideoSelection current = selection();
        if (id != null && (id.equals(current.activeId) || id.equals(effectiveId()))) {
            return DeleteResult.ACTIVE;
        }
        if (id != null && schedule().videoIds().contains(id)) {
            return DeleteResult.SCHEDULED;
        }
        BackgroundVideoMetadata metadata = readMetadata(id);
        File object = objectFile(id);
        if (metadata == null && !object.isFile()) {
            return DeleteResult.NOT_FOUND;
        }
        if (!current.previousId.isEmpty() && current.previousId.equals(id)) {
            persistSelection(current.remove(id));
        }
        if (id.equals(holdId())) {
            persistHold("", 0L);
        }
        if (object.isFile() && !object.delete()) {
            throw new IOException("Unable to delete background video bytes");
        }
        deleteQuietly(metadataFile(id));
        deleteQuietly(posterFile(id));
        return DeleteResult.DELETED;
    }

    /** Replaces the timetable; any manual hold ends so the new schedule shows at once. */
    public synchronized void updateSchedule(BackgroundVideoSchedule schedule)
            throws IOException, JSONException {
        for (String id : schedule.videoIds()) {
            if (!available(id) || readMetadata(id) == null) {
                throw new IllegalArgumentException("A scheduled video is not in the library");
            }
        }
        SharedPreferences.Editor editor = preferences.edit()
                .putString(KEY_SCHEDULE, schedule.toJson().toString())
                .remove(KEY_HOLD_ID)
                .remove(KEY_HOLD_UNTIL);
        if (!editor.commit()) {
            throw new IOException("Unable to persist background video schedule");
        }
    }

    public synchronized void resumeSchedule() throws IOException {
        persistHold("", 0L);
    }

    /** The video the glass should show right now. */
    public synchronized String effectiveId() {
        String active = selection().activeId;
        String id = BackgroundVideoSchedule.effectiveId(
                schedule(),
                active,
                holdId(),
                holdUntil(),
                System.currentTimeMillis(),
                zone());
        if (available(id)) {
            return id;
        }
        return available(active) ? active : "";
    }

    public synchronized File effectiveFile() {
        String id = effectiveId();
        return id.isEmpty() ? null : objectFile(id);
    }

    /** Epoch milliseconds of the next scheduled change, or -1 without a running schedule. */
    public synchronized long nextScheduleChangeMillis() {
        BackgroundVideoSchedule schedule = schedule();
        return schedule.isActive()
                ? schedule.nextChangeMillis(System.currentTimeMillis(), zone())
                : -1L;
    }

    public synchronized File poster(String id) {
        if (!BackgroundVideoSelection.validId(id) || readMetadata(id) == null) {
            return null;
        }
        File poster = posterFile(id);
        return poster.isFile() ? poster : null;
    }

    /** "active" is what the glass shows now; "selectedId" is the manual choice. */
    public synchronized JSONObject selectionSnapshot() throws JSONException {
        BackgroundVideoSelection selection = selection();
        return new JSONObject()
                .put("active", metadataJson(effectiveId(), selection))
                .put("selectedId", selection.activeId)
                .put("previous", metadataJson(selection.previousId, selection))
                .put("canRollback", selection.canRollback())
                .put("schedule", scheduleJson(schedule()));
    }

    public synchronized JSONObject publicSelectionSnapshot() throws JSONException {
        BackgroundVideoSelection selection = selection();
        return new JSONObject()
                .put("active", !effectiveId().isEmpty())
                .put("previous", !selection.previousId.isEmpty())
                .put("canRollback", selection.canRollback())
                .put("scheduled", schedule().isActive());
    }

    public synchronized JSONObject document() throws JSONException {
        List<BackgroundVideoMetadata> videos = records();
        BackgroundVideoSelection selection = selection();
        BackgroundVideoSchedule schedule = schedule();
        String showing = effectiveId();
        JSONArray items = new JSONArray();
        for (BackgroundVideoMetadata video : videos) {
            JSONArray starts = new JSONArray();
            for (String start : schedule.startsFor(video.id)) {
                starts.put(start);
            }
            items.put(video.toJson(
                    video.id.equals(selection.activeId),
                    video.id.equals(selection.previousId),
                    posterFile(video.id).isFile())
                    .put("showing", video.id.equals(showing))
                    .put("scheduledStarts", starts));
        }
        return new JSONObject()
                .put("videos", items)
                .put("activeId", selection.activeId)
                .put("previousId", selection.previousId)
                .put("effectiveId", showing)
                .put("canRollback", selection.canRollback())
                .put("schedule", scheduleJson(schedule))
                .put("totalBytes", totalBytes(videos))
                .put("usableBytes", root.getUsableSpace())
                .put("maxVideoBytes", MAX_VIDEO_BYTES)
                .put("maxLibraryBytes", MAX_LIBRARY_BYTES)
                .put("minFreeBytes", MIN_FREE_BYTES)
                .put("maxVideos", MAX_VIDEOS);
    }

    private JSONObject scheduleJson(BackgroundVideoSchedule schedule) throws JSONException {
        long now = System.currentTimeMillis();
        TimeZone zone = zone();
        JSONObject result = schedule.toJson()
                .put("active", schedule.isActive())
                .put("maxSlots", BackgroundVideoSchedule.MAX_SLOTS)
                .put("utcOffsetMinutes", configStore.getUtcOffsetMinutes())
                .put("current", JSONObject.NULL)
                .put("next", JSONObject.NULL)
                .put("nextChangeAt", JSONObject.NULL)
                .put("hold", JSONObject.NULL);
        if (schedule.isActive()) {
            BackgroundVideoSchedule.Slot current = schedule.slotAt(now, zone);
            result.put("current", current.toJson())
                    .put("next", schedule.slotAfter(current).toJson())
                    .put("nextChangeAt", schedule.nextChangeMillis(now, zone));
            String hold = holdId();
            long until = holdUntil();
            if (BackgroundVideoSchedule.holdActive(hold, until, now) && available(hold)) {
                result.put("hold", new JSONObject().put("videoId", hold).put("until", until));
            }
        }
        return result;
    }

    /** While a schedule runs, a manual choice holds until the next scheduled change. */
    private void holdUntilNextChange(String id) throws IOException {
        BackgroundVideoSchedule schedule = schedule();
        long now = System.currentTimeMillis();
        TimeZone zone = zone();
        if (!schedule.isActive() || id.equals(schedule.slotAt(now, zone).videoId)) {
            persistHold("", 0L);
            return;
        }
        persistHold(id, schedule.nextChangeMillis(now, zone));
    }

    private BackgroundVideoSchedule schedule() {
        return BackgroundVideoSchedule.fromStorage(preferences.getString(KEY_SCHEDULE, ""));
    }

    private String holdId() {
        return preferences.getString(KEY_HOLD_ID, "");
    }

    private long holdUntil() {
        return preferences.getLong(KEY_HOLD_UNTIL, 0L);
    }

    private TimeZone zone() {
        return TimeZone.getTimeZone(configStore.getEffectiveTimeZoneId());
    }

    private boolean available(String id) {
        return BackgroundVideoSelection.validId(id) && objectFile(id).isFile();
    }

    private void persistHold(String id, long untilMillis) throws IOException {
        SharedPreferences.Editor editor = preferences.edit();
        if (BackgroundVideoSelection.validId(id) && untilMillis > 0) {
            editor.putString(KEY_HOLD_ID, id).putLong(KEY_HOLD_UNTIL, untilMillis);
        } else {
            editor.remove(KEY_HOLD_ID).remove(KEY_HOLD_UNTIL);
        }
        if (!editor.commit()) {
            throw new IOException("Unable to persist background video hold");
        }
    }

    private BackgroundVideoMetadata inspect(
            File file,
            String id,
            String name,
            long sizeBytes) throws IOException {
        MediaExtractor extractor = new MediaExtractor();
        try {
            extractor.setDataSource(file.getAbsolutePath());
            MediaFormat videoFormat = null;
            int videoTrack = -1;
            boolean hasAudio = false;
            for (int index = 0; index < extractor.getTrackCount(); index++) {
                MediaFormat format = extractor.getTrackFormat(index);
                String mime = format.getString(MediaFormat.KEY_MIME);
                if (mime != null && mime.startsWith("audio/")) {
                    hasAudio = true;
                } else if (mime != null && mime.startsWith("video/")) {
                    if (videoFormat != null) {
                        throw new IOException("Background video must contain exactly one video track");
                    }
                    videoFormat = format;
                    videoTrack = index;
                }
            }
            if (videoFormat == null) {
                throw new IOException("MP4 does not contain a video track");
            }
            String mimeType = videoFormat.getString(MediaFormat.KEY_MIME);
            if (!"video/avc".equals(mimeType)) {
                throw new IOException("Background video must use H.264/AVC");
            }
            int width = requiredInteger(videoFormat, MediaFormat.KEY_WIDTH, "width");
            int height = requiredInteger(videoFormat, MediaFormat.KEY_HEIGHT, "height");
            if (width > MAX_DIMENSION
                    || height > MAX_DIMENSION
                    || (long) width * height > MAX_PIXELS) {
                throw new IOException("Background video must be at most 1080p");
            }
            long durationMs = requiredLong(
                    videoFormat,
                    MediaFormat.KEY_DURATION,
                    "duration") / 1000L;
            if (durationMs < 1000L || durationMs > MAX_DURATION_MS) {
                throw new IOException("Background video duration must be between 1 second and 6 hours");
            }
            FrameRateStats measuredRate = inspectFrameRate(extractor, videoTrack);
            float frameRate = optionalFrameRate(videoFormat);
            if (frameRate <= 0) {
                frameRate = measuredRate.nominal;
            }
            if (frameRate <= 0) {
                throw new IOException("Background video frame rate is unavailable");
            }
            float maximumFrameRate = Math.max(frameRate, measuredRate.maximumSustained);
            if (maximumFrameRate > MAX_FRAME_RATE) {
                throw new IOException("Background video frame rate must not exceed 30 FPS");
            }
            int declaredBitRate =
                    optionalInteger(videoFormat, MediaFormat.KEY_BIT_RATE, 0);
            long averageFileBitRate = sizeBytes * 8_000L / durationMs;
            long effectiveBitRate = Math.max(declaredBitRate, averageFileBitRate);
            if (effectiveBitRate < 1 || effectiveBitRate > MAX_BIT_RATE) {
                throw new IOException("Background video bit rate must not exceed 20 Mbps");
            }
            AvcSpec avc = readAvcSpec(videoFormat);
            if (avc == null) {
                throw new IOException("Background video AVC profile or level is unavailable");
            }
            float capabilityFrameRate = Math.round(maximumFrameRate * 100f) / 100f;
            String decoderName =
                    findHardwareDecoder(
                            mimeType,
                            width,
                            height,
                            capabilityFrameRate,
                            (int) effectiveBitRate,
                            avc);
            return new BackgroundVideoMetadata(
                    id,
                    name,
                    sizeBytes,
                    System.currentTimeMillis(),
                    mimeType,
                    width,
                    height,
                    optionalInteger(videoFormat, MediaFormat.KEY_ROTATION, 0),
                    durationMs,
                    frameRate,
                    (int) effectiveBitRate,
                    decoderName,
                    avc.profileIdc,
                    avc.levelIdc,
                    hasAudio);
        } finally {
            extractor.release();
        }
    }

    private void writePoster(File video, String id, long durationMs) {
        MediaMetadataRetriever retriever = new MediaMetadataRetriever();
        Bitmap frame = null;
        Bitmap scaled = null;
        File temporary = new File(posters, id + ".tmp");
        try {
            retriever.setDataSource(video.getAbsolutePath());
            long timeUs = Math.min(2_000_000L, durationMs * 250L);
            frame = retriever.getFrameAtTime(timeUs, MediaMetadataRetriever.OPTION_CLOSEST_SYNC);
            if (frame == null) {
                Log.w(TAG, "No poster frame available for " + id);
                return;
            }
            float scale = Math.min(
                    1f,
                    POSTER_EDGE / (float) Math.max(frame.getWidth(), frame.getHeight()));
            scaled = scale < 1f
                    ? Bitmap.createScaledBitmap(
                            frame,
                            Math.max(1, Math.round(frame.getWidth() * scale)),
                            Math.max(1, Math.round(frame.getHeight() * scale)),
                            true)
                    : frame;
            try (FileOutputStream output = new FileOutputStream(temporary)) {
                if (!scaled.compress(Bitmap.CompressFormat.JPEG, 84, output)) {
                    throw new IOException("Unable to encode background video poster");
                }
                output.getFD().sync();
            }
            replaceFile(temporary, posterFile(id));
        } catch (IOException | RuntimeException error) {
            Log.w(TAG, "Unable to create background video poster", error);
            deleteQuietly(temporary);
        } finally {
            if (scaled != null && scaled != frame) {
                scaled.recycle();
            }
            if (frame != null) {
                frame.recycle();
            }
            try {
                retriever.release();
            } catch (IOException error) {
                Log.w(TAG, "Unable to release poster decoder", error);
            }
        }
    }

    private List<BackgroundVideoMetadata> records() {
        File[] files = objects.listFiles(
                file -> file.isFile() && file.getName().matches("[0-9a-f]{64}\\.json"));
        List<BackgroundVideoMetadata> result = new ArrayList<>();
        if (files == null) {
            return result;
        }
        for (File file : files) {
            String id = file.getName().substring(0, 64);
            BackgroundVideoMetadata metadata = readMetadata(id);
            File object = objectFile(id);
            if (metadata != null && object.isFile() && object.length() == metadata.sizeBytes) {
                result.add(metadata);
            } else {
                Log.e(TAG, "Ignoring invalid background video catalog entry " + id);
            }
        }
        Collections.sort(result, new Comparator<BackgroundVideoMetadata>() {
            @Override
            public int compare(
                    BackgroundVideoMetadata first,
                    BackgroundVideoMetadata second) {
                return Long.compare(second.addedAt, first.addedAt);
            }
        });
        return result;
    }

    private BackgroundVideoMetadata readMetadata(String id) {
        if (!BackgroundVideoSelection.validId(id)) {
            return null;
        }
        File file = metadataFile(id);
        if (!file.isFile() || file.length() < 2 || file.length() > MAX_METADATA_BYTES) {
            return null;
        }
        try (InputStream input = new FileInputStream(file);
                ByteArrayOutputStream output = new ByteArrayOutputStream()) {
            byte[] buffer = new byte[4096];
            int count;
            while ((count = input.read(buffer)) != -1) {
                output.write(buffer, 0, count);
                if (output.size() > MAX_METADATA_BYTES) {
                    return null;
                }
            }
            BackgroundVideoMetadata metadata = BackgroundVideoMetadata.parse(
                    new JSONObject(new String(output.toByteArray(), StandardCharsets.UTF_8)));
            return id.equals(metadata.id) ? metadata : null;
        } catch (IOException | JSONException | IllegalArgumentException error) {
            Log.e(TAG, "Unable to read background video metadata " + id, error);
            return null;
        }
    }

    private void writeMetadata(BackgroundVideoMetadata metadata)
            throws IOException, JSONException {
        File temporary = new File(objects, metadata.id + ".json.tmp");
        byte[] bytes = metadata.serialize().toString().getBytes(StandardCharsets.UTF_8);
        try (FileOutputStream output = new FileOutputStream(temporary)) {
            output.write(bytes);
            output.getFD().sync();
        }
        replaceFile(temporary, metadataFile(metadata.id));
    }

    private Object metadataJson(String id, BackgroundVideoSelection selection)
            throws JSONException {
        BackgroundVideoMetadata metadata = readMetadata(id);
        if (metadata == null || !objectFile(id).isFile()) {
            return JSONObject.NULL;
        }
        return metadata.toJson(
                id.equals(selection.activeId),
                id.equals(selection.previousId),
                posterFile(id).isFile());
    }

    private BackgroundVideoSelection selection() {
        return new BackgroundVideoSelection(
                preferences.getString(KEY_ACTIVE, ""),
                preferences.getString(KEY_PREVIOUS, ""));
    }

    private void reconcileSelection() {
        BackgroundVideoSelection current = selection();
        String active = readMetadata(current.activeId) != null ? current.activeId : "";
        String previous = readMetadata(current.previousId) != null ? current.previousId : "";
        if (!active.equals(current.activeId) || !previous.equals(current.previousId)) {
            try {
                persistSelection(new BackgroundVideoSelection(active, previous));
            } catch (IOException error) {
                throw new IllegalStateException("Unable to repair background video selection", error);
            }
        }
    }

    private void persistSelection(BackgroundVideoSelection selection) throws IOException {
        SharedPreferences.Editor editor = preferences.edit();
        if (selection.activeId.isEmpty()) {
            editor.remove(KEY_ACTIVE);
        } else {
            editor.putString(KEY_ACTIVE, selection.activeId);
        }
        if (selection.previousId.isEmpty()) {
            editor.remove(KEY_PREVIOUS);
        } else {
            editor.putString(KEY_PREVIOUS, selection.previousId);
        }
        if (!editor.commit()) {
            throw new IOException("Unable to persist background video selection");
        }
    }

    private void cleanupIncoming() {
        File[] files = incoming.listFiles(file -> file.isFile() && file.getName().endsWith(".part"));
        if (files == null) {
            return;
        }
        for (File file : files) {
            deleteQuietly(file);
        }
    }

    private File objectFile(String id) {
        return new File(objects, safeId(id) + ".mp4");
    }

    private File metadataFile(String id) {
        return new File(objects, safeId(id) + ".json");
    }

    private File posterFile(String id) {
        return new File(posters, safeId(id) + ".jpg");
    }

    private static String safeId(String id) {
        return BackgroundVideoSelection.validId(id) ? id : "invalid";
    }

    private static String normalizeName(String encodedName) {
        if (encodedName == null || encodedName.isEmpty() || encodedName.length() > 240) {
            return null;
        }
        final String decoded;
        try {
            decoded = URLDecoder.decode(encodedName, "UTF-8").trim();
        } catch (Exception error) {
            return null;
        }
        String lower = decoded.toLowerCase(Locale.US);
        if (decoded.isEmpty()
                || decoded.length() > 180
                || !lower.endsWith(".mp4")
                || decoded.startsWith(".")
                || decoded.contains("/")
                || decoded.contains("\\")
                || decoded.contains("..")
                || containsControlCharacter(decoded)) {
            return null;
        }
        return decoded;
    }

    private static boolean containsControlCharacter(String value) {
        for (int index = 0; index < value.length(); index++) {
            if (Character.isISOControl(value.charAt(index))) {
                return true;
            }
        }
        return false;
    }

    static String writeIncoming(
            InputStream source,
            File destination,
            long expectedLength) throws IOException {
        MessageDigest digest = sha256Digest();
        long copied = 0;
        try (FileOutputStream output = new FileOutputStream(destination)) {
            byte[] buffer = new byte[64 * 1024];
            while (copied < expectedLength) {
                int count = source.read(
                        buffer,
                        0,
                        (int) Math.min(buffer.length, expectedLength - copied));
                if (count < 0) {
                    throw new IOException("Background video upload ended early");
                }
                copied += count;
                digest.update(buffer, 0, count);
                output.write(buffer, 0, count);
            }
            output.getFD().sync();
        } catch (IOException error) {
            deleteQuietly(destination);
            throw error;
        }
        return hex(digest.digest());
    }

    private static MessageDigest sha256Digest() {
        try {
            return MessageDigest.getInstance("SHA-256");
        } catch (NoSuchAlgorithmException impossible) {
            throw new IllegalStateException("SHA-256 is unavailable", impossible);
        }
    }

    private static String hex(byte[] bytes) {
        StringBuilder result = new StringBuilder(bytes.length * 2);
        for (byte value : bytes) {
            result.append(String.format(Locale.US, "%02x", value & 0xff));
        }
        return result.toString();
    }

    private static int requiredInteger(MediaFormat format, String key, String label)
            throws IOException {
        if (!format.containsKey(key)) {
            throw new IOException("Background video " + label + " is unavailable");
        }
        return format.getInteger(key);
    }

    private static long requiredLong(MediaFormat format, String key, String label)
            throws IOException {
        if (!format.containsKey(key)) {
            throw new IOException("Background video " + label + " is unavailable");
        }
        return format.getLong(key);
    }

    private static int optionalInteger(MediaFormat format, String key, int fallback) {
        try {
            return format.containsKey(key) ? format.getInteger(key) : fallback;
        } catch (RuntimeException ignored) {
            return fallback;
        }
    }

    private static float optionalFrameRate(MediaFormat format) {
        if (!format.containsKey(MediaFormat.KEY_FRAME_RATE)) {
            return 0;
        }
        try {
            return format.getFloat(MediaFormat.KEY_FRAME_RATE);
        } catch (RuntimeException ignored) {
            try {
                return format.getInteger(MediaFormat.KEY_FRAME_RATE);
            } catch (RuntimeException second) {
                return 0;
            }
        }
    }

    private static FrameRateStats inspectFrameRate(
            MediaExtractor extractor,
            int trackIndex) throws IOException {
        extractor.selectTrack(trackIndex);
        long[] sampleTimes = new long[4096];
        int sampleCount = 0;
        try {
            while (true) {
                long timeUs = extractor.getSampleTime();
                if (timeUs < 0) {
                    break;
                }
                if (sampleCount >= MAX_FRAME_SAMPLES) {
                    throw new IOException("Background video contains too many frame samples");
                }
                if (sampleCount == sampleTimes.length) {
                    sampleTimes = Arrays.copyOf(
                            sampleTimes,
                            Math.min(MAX_FRAME_SAMPLES, sampleTimes.length * 2));
                }
                sampleTimes[sampleCount++] = timeUs;
                if (!extractor.advance()) {
                    break;
                }
            }
        } finally {
            extractor.unselectTrack(trackIndex);
        }
        return analyzeFrameRate(sampleTimes, sampleCount);
    }

    static float frameRateFromSampleTimes(List<Long> sampleTimes) {
        return analyzeFrameRate(sampleTimes).nominal;
    }

    static FrameRateStats analyzeFrameRate(List<Long> sampleTimes) {
        long[] values = new long[sampleTimes.size()];
        for (int index = 0; index < sampleTimes.size(); index++) {
            values[index] = sampleTimes.get(index);
        }
        return analyzeFrameRate(values, values.length);
    }

    private static FrameRateStats analyzeFrameRate(long[] sampleTimes, int sampleCount) {
        if (sampleCount < 2) {
            return new FrameRateStats(0, 0);
        }
        Arrays.sort(sampleTimes, 0, sampleCount);
        int uniqueCount = 1;
        for (int index = 1; index < sampleCount; index++) {
            if (sampleTimes[index] != sampleTimes[uniqueCount - 1]) {
                sampleTimes[uniqueCount++] = sampleTimes[index];
            }
        }
        long[] intervals = new long[uniqueCount - 1];
        int intervalCount = 0;
        for (int index = 1; index < uniqueCount; index++) {
            long interval = sampleTimes[index] - sampleTimes[index - 1];
            if (interval > 0) {
                intervals[intervalCount++] = interval;
            }
        }
        if (intervalCount == 0) {
            return new FrameRateStats(0, 0);
        }
        Arrays.sort(intervals, 0, intervalCount);
        long medianIntervalUs = intervals[intervalCount / 2];
        float nominal = medianIntervalUs > 0 ? 1_000_000f / medianIntervalUs : 0;
        int windowIntervals = Math.min(30, uniqueCount - 1);
        float maximumSustained = nominal;
        for (int start = 0; start + windowIntervals < uniqueCount; start++) {
            long elapsedUs =
                    sampleTimes[start + windowIntervals] - sampleTimes[start];
            if (elapsedUs > 0) {
                maximumSustained = Math.max(
                        maximumSustained,
                        1_000_000f * windowIntervals / elapsedUs);
            }
        }
        return new FrameRateStats(nominal, maximumSustained);
    }

    private static boolean isSoftwareDecoder(String name) {
        String normalized = name.toLowerCase(Locale.US);
        return normalized.contains("google")
                || normalized.contains("android")
                || normalized.contains("software")
                || normalized.contains("ffmpeg");
    }

    private static String findHardwareDecoder(
            String mimeType,
            int width,
            int height,
            float frameRate,
            int bitRate,
            AvcSpec avc) throws IOException {
        MediaCodecInfo[] codecs;
        try {
            codecs = new MediaCodecList(MediaCodecList.REGULAR_CODECS).getCodecInfos();
        } catch (RuntimeException error) {
            throw new IOException("Unable to enumerate media decoders", error);
        }
        StringBuilder rejected = new StringBuilder();
        for (MediaCodecInfo codec : codecs) {
            if (codec.isEncoder() || isSoftwareDecoder(codec.getName())) {
                continue;
            }
            boolean supportedType = false;
            for (String type : codec.getSupportedTypes()) {
                if (mimeType.equalsIgnoreCase(type)) {
                    supportedType = true;
                    break;
                }
            }
            if (!supportedType) {
                continue;
            }
            try {
                MediaCodecInfo.CodecCapabilities capabilities =
                        codec.getCapabilitiesForType(mimeType);
                MediaCodecInfo.VideoCapabilities video =
                        capabilities.getVideoCapabilities();
                boolean exactProfileLevel =
                        supportsProfileLevel(capabilities, avc, false);
                boolean level4Compatible =
                        fitsAvcLevel4Envelope(width, height, frameRate, bitRate)
                                && supportsProfileLevel(capabilities, avc, true);
                boolean profileLevel = exactProfileLevel || level4Compatible;
                boolean sizeRate = supports(video, width, height, frameRate, bitRate)
                        || supports(video, height, width, frameRate, bitRate);
                if (profileLevel && sizeRate) {
                    if (!exactProfileLevel) {
                        Log.w(
                                TAG,
                                codec.getName()
                                        + " under-reports AVC Level 4.1; accepting Level 4 envelope");
                    }
                    return codec.getName();
                }
                appendDecoderRejection(
                        rejected,
                        codec,
                        capabilities,
                        profileLevel,
                        sizeRate);
            } catch (RuntimeException error) {
                Log.w(TAG, "Unable to inspect decoder " + codec.getName(), error);
                if (rejected.length() > 0) {
                    rejected.append("; ");
                }
                rejected.append(codec.getName()).append(" inspection failed");
            }
        }
        throw new IOException(
                "No hardware H.264 decoder accepted profile "
                        + avc.profileIdc
                        + " level "
                        + avc.levelIdc
                        + " at "
                        + width
                        + "x"
                        + height
                        + " "
                        + frameRate
                        + " FPS"
                        + (rejected.length() > 0 ? " (" + rejected + ")" : ""));
    }

    private static void appendDecoderRejection(
            StringBuilder output,
            MediaCodecInfo codec,
            MediaCodecInfo.CodecCapabilities capabilities,
            boolean profileLevel,
            boolean sizeRate) {
        if (output.length() > 0) {
            output.append("; ");
        }
        output.append(codec.getName())
                .append(" profileLevel=")
                .append(profileLevel)
                .append(" sizeRate=")
                .append(sizeRate)
                .append(" advertised=");
        for (int index = 0; index < capabilities.profileLevels.length; index++) {
            if (index > 0) {
                output.append(",");
            }
            MediaCodecInfo.CodecProfileLevel value = capabilities.profileLevels[index];
            output.append(value.profile).append("@").append(value.level);
        }
    }

    private static boolean supportsProfileLevel(
            MediaCodecInfo.CodecCapabilities capabilities,
            AvcSpec avc,
            boolean allowLevel41OnLevel4) {
        for (MediaCodecInfo.CodecProfileLevel supported : capabilities.profileLevels) {
            if (supportsAvcProfileLevel(
                    supported.profile,
                    supported.level,
                    avc,
                    allowLevel41OnLevel4)) {
                return true;
            }
        }
        return false;
    }

    static boolean supportsAvcProfileLevel(
            int supportedProfile,
            int supportedLevel,
            AvcSpec requested) {
        return supportsAvcProfileLevel(
                supportedProfile,
                supportedLevel,
                requested,
                false);
    }

    static boolean supportsAvcProfileLevel(
            int supportedProfile,
            int supportedLevel,
            AvcSpec requested,
            boolean allowLevel41OnLevel4) {
        int supportedLevelRank = avcLevelRank(supportedLevel);
        int requestedLevelRank = avcLevelRank(requested.codecLevel);
        return supportedProfile == requested.codecProfile
                && requestedLevelRank > 0
                && (supportedLevelRank >= requestedLevelRank
                || (allowLevel41OnLevel4
                && supportedLevel
                        == MediaCodecInfo.CodecProfileLevel.AVCLevel4
                && requested.codecLevel
                        == MediaCodecInfo.CodecProfileLevel.AVCLevel41));
    }

    static boolean fitsAvcLevel4Envelope(
            int width,
            int height,
            float frameRate,
            int bitRate) {
        long frameMacroblocks =
                ((width + 15L) / 16L) * ((height + 15L) / 16L);
        return frameMacroblocks <= AVC_LEVEL_4_MAX_FRAME_MACROBLOCKS
                && frameMacroblocks * frameRate
                        <= AVC_LEVEL_4_MAX_MACROBLOCKS_PER_SECOND
                && bitRate <= AVC_LEVEL_4_MAX_BIT_RATE;
    }

    private static int avcLevelRank(int level) {
        if (level == MediaCodecInfo.CodecProfileLevel.AVCLevel1) {
            return 1;
        }
        if (level == MediaCodecInfo.CodecProfileLevel.AVCLevel1b) {
            return 2;
        }
        if (level == MediaCodecInfo.CodecProfileLevel.AVCLevel11) {
            return 3;
        }
        if (level == MediaCodecInfo.CodecProfileLevel.AVCLevel12) {
            return 4;
        }
        if (level == MediaCodecInfo.CodecProfileLevel.AVCLevel13) {
            return 5;
        }
        if (level == MediaCodecInfo.CodecProfileLevel.AVCLevel2) {
            return 6;
        }
        if (level == MediaCodecInfo.CodecProfileLevel.AVCLevel21) {
            return 7;
        }
        if (level == MediaCodecInfo.CodecProfileLevel.AVCLevel22) {
            return 8;
        }
        if (level == MediaCodecInfo.CodecProfileLevel.AVCLevel3) {
            return 9;
        }
        if (level == MediaCodecInfo.CodecProfileLevel.AVCLevel31) {
            return 10;
        }
        if (level == MediaCodecInfo.CodecProfileLevel.AVCLevel32) {
            return 11;
        }
        if (level == MediaCodecInfo.CodecProfileLevel.AVCLevel4) {
            return 12;
        }
        if (level == MediaCodecInfo.CodecProfileLevel.AVCLevel41) {
            return 13;
        }
        if (level == MediaCodecInfo.CodecProfileLevel.AVCLevel42) {
            return 14;
        }
        if (level == MediaCodecInfo.CodecProfileLevel.AVCLevel5) {
            return 15;
        }
        if (level == MediaCodecInfo.CodecProfileLevel.AVCLevel51) {
            return 16;
        }
        if (level == MediaCodecInfo.CodecProfileLevel.AVCLevel52) {
            return 17;
        }
        return 0;
    }

    private static AvcSpec readAvcSpec(MediaFormat format) {
        ByteBuffer buffer = format.getByteBuffer("csd-0");
        if (buffer == null) {
            return null;
        }
        ByteBuffer copy = buffer.duplicate();
        copy.rewind();
        byte[] bytes = new byte[copy.remaining()];
        copy.get(bytes);
        return parseAvcSpec(bytes);
    }

    static AvcSpec parseAvcSpec(byte[] bytes) {
        if (bytes == null || bytes.length < 4) {
            return null;
        }
        if ((bytes[0] & 0xff) == 1) {
            return avcSpec(bytes[1] & 0xff, bytes[2] & 0xff, bytes[3] & 0xff);
        }
        for (int index = 0; index < bytes.length - 3; index++) {
            int nalStart = -1;
            if (index + 3 < bytes.length
                    && bytes[index] == 0
                    && bytes[index + 1] == 0
                    && bytes[index + 2] == 1) {
                nalStart = index + 3;
            } else if (index + 4 < bytes.length
                    && bytes[index] == 0
                    && bytes[index + 1] == 0
                    && bytes[index + 2] == 0
                    && bytes[index + 3] == 1) {
                nalStart = index + 4;
            } else if (index == 0 && (bytes[0] & 0x1f) == 7) {
                nalStart = 0;
            }
            if (nalStart >= 0
                    && nalStart + 3 < bytes.length
                    && (bytes[nalStart] & 0x1f) == 7) {
                return avcSpec(
                        bytes[nalStart + 1] & 0xff,
                        bytes[nalStart + 2] & 0xff,
                        bytes[nalStart + 3] & 0xff);
            }
        }
        return null;
    }

    private static AvcSpec avcSpec(int profileIdc, int constraints, int levelIdc) {
        int profile;
        switch (profileIdc) {
            case 66:
                profile = MediaCodecInfo.CodecProfileLevel.AVCProfileBaseline;
                break;
            case 77:
                profile = MediaCodecInfo.CodecProfileLevel.AVCProfileMain;
                break;
            case 88:
                profile = MediaCodecInfo.CodecProfileLevel.AVCProfileExtended;
                break;
            case 100:
                profile = MediaCodecInfo.CodecProfileLevel.AVCProfileHigh;
                break;
            case 110:
                profile = MediaCodecInfo.CodecProfileLevel.AVCProfileHigh10;
                break;
            case 122:
                profile = MediaCodecInfo.CodecProfileLevel.AVCProfileHigh422;
                break;
            case 244:
                profile = MediaCodecInfo.CodecProfileLevel.AVCProfileHigh444;
                break;
            default:
                return null;
        }
        int level;
        switch (levelIdc) {
            case 10:
                level = MediaCodecInfo.CodecProfileLevel.AVCLevel1;
                break;
            case 11:
                level = (constraints & 0x10) != 0
                        ? MediaCodecInfo.CodecProfileLevel.AVCLevel1b
                        : MediaCodecInfo.CodecProfileLevel.AVCLevel11;
                break;
            case 12:
                level = MediaCodecInfo.CodecProfileLevel.AVCLevel12;
                break;
            case 13:
                level = MediaCodecInfo.CodecProfileLevel.AVCLevel13;
                break;
            case 20:
                level = MediaCodecInfo.CodecProfileLevel.AVCLevel2;
                break;
            case 21:
                level = MediaCodecInfo.CodecProfileLevel.AVCLevel21;
                break;
            case 22:
                level = MediaCodecInfo.CodecProfileLevel.AVCLevel22;
                break;
            case 30:
                level = MediaCodecInfo.CodecProfileLevel.AVCLevel3;
                break;
            case 31:
                level = MediaCodecInfo.CodecProfileLevel.AVCLevel31;
                break;
            case 32:
                level = MediaCodecInfo.CodecProfileLevel.AVCLevel32;
                break;
            case 40:
                level = MediaCodecInfo.CodecProfileLevel.AVCLevel4;
                break;
            case 41:
                level = MediaCodecInfo.CodecProfileLevel.AVCLevel41;
                break;
            case 42:
                level = MediaCodecInfo.CodecProfileLevel.AVCLevel42;
                break;
            case 50:
                level = MediaCodecInfo.CodecProfileLevel.AVCLevel5;
                break;
            case 51:
                level = MediaCodecInfo.CodecProfileLevel.AVCLevel51;
                break;
            case 52:
                level = MediaCodecInfo.CodecProfileLevel.AVCLevel52;
                break;
            default:
                return null;
        }
        return new AvcSpec(profileIdc, levelIdc, profile, level);
    }

    private static boolean supports(
            MediaCodecInfo.VideoCapabilities video,
            int width,
            int height,
            float frameRate,
            int bitRate) {
        if (video == null || !video.isSizeSupported(width, height)) {
            return false;
        }
        return (frameRate <= 0
                || video.areSizeAndRateSupported(width, height, frameRate))
                && bitRate <= video.getBitrateRange().getUpper();
    }

    private static long totalBytes(List<BackgroundVideoMetadata> videos) {
        long total = 0;
        for (BackgroundVideoMetadata video : videos) {
            total += video.sizeBytes;
        }
        return total;
    }

    private static void requireDirectory(File directory) {
        if (!directory.isDirectory() && !directory.mkdirs()) {
            throw new IllegalStateException("Unable to create " + directory.getName());
        }
    }

    private static void replaceFile(File source, File destination) throws IOException {
        if (destination.exists() && !destination.delete()) {
            throw new IOException("Unable to replace " + destination.getName());
        }
        if (!source.renameTo(destination)) {
            throw new IOException("Unable to install " + destination.getName());
        }
    }

    private static void deleteQuietly(File file) {
        if (file != null && file.exists() && !file.delete()) {
            Log.w(TAG, "Unable to delete " + file.getName());
        }
    }
}
