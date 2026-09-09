package dev.mirror.repurpose;

import org.json.JSONException;
import org.json.JSONObject;

final class BackgroundVideoMetadata {
    final String id;
    final String name;
    final long sizeBytes;
    final long addedAt;
    final String mimeType;
    final int width;
    final int height;
    final int rotation;
    final long durationMs;
    final float frameRate;
    final int bitrate;
    final String decoderName;
    final int avcProfile;
    final int avcLevel;
    final boolean hasAudio;

    BackgroundVideoMetadata(
            String id,
            String name,
            long sizeBytes,
            long addedAt,
            String mimeType,
            int width,
            int height,
            int rotation,
            long durationMs,
            float frameRate,
            int bitrate,
            String decoderName,
            int avcProfile,
            int avcLevel,
            boolean hasAudio) {
        if (!BackgroundVideoSelection.validId(id)
                || name == null
                || name.isEmpty()
                || name.length() > 180
                || sizeBytes < 1
                || addedAt < 1
                || mimeType == null
                || mimeType.isEmpty()
                || width < 1
                || height < 1
                || durationMs < 1
                || frameRate < 0
                || bitrate < 0
                || decoderName == null
                || decoderName.isEmpty()
                || avcProfile < 1
                || avcLevel < 1) {
            throw new IllegalArgumentException("Invalid background video metadata");
        }
        this.id = id;
        this.name = name;
        this.sizeBytes = sizeBytes;
        this.addedAt = addedAt;
        this.mimeType = mimeType;
        this.width = width;
        this.height = height;
        this.rotation = rotation;
        this.durationMs = durationMs;
        this.frameRate = frameRate;
        this.bitrate = bitrate;
        this.decoderName = decoderName;
        this.avcProfile = avcProfile;
        this.avcLevel = avcLevel;
        this.hasAudio = hasAudio;
    }

    JSONObject toJson(boolean active, boolean previous, boolean posterAvailable)
            throws JSONException {
        return new JSONObject()
                .put("id", id)
                .put("name", name)
                .put("sizeBytes", sizeBytes)
                .put("addedAt", addedAt)
                .put("mimeType", mimeType)
                .put("width", width)
                .put("height", height)
                .put("rotation", rotation)
                .put("durationMs", durationMs)
                .put("frameRate", frameRate)
                .put("bitrate", bitrate)
                .put("decoderName", decoderName)
                .put("avcProfile", avcProfile)
                .put("avcLevel", avcLevel)
                .put("hasAudio", hasAudio)
                .put("posterAvailable", posterAvailable)
                .put("active", active)
                .put("previous", previous);
    }

    JSONObject serialize() throws JSONException {
        return toJson(false, false, false);
    }

    static BackgroundVideoMetadata parse(JSONObject value) throws JSONException {
        try {
            return new BackgroundVideoMetadata(
                    value.getString("id"),
                    value.getString("name"),
                    value.getLong("sizeBytes"),
                    value.getLong("addedAt"),
                    value.getString("mimeType"),
                    value.getInt("width"),
                    value.getInt("height"),
                    value.optInt("rotation", 0),
                    value.getLong("durationMs"),
                    (float) value.optDouble("frameRate", 0),
                    value.optInt("bitrate", 0),
                    value.getString("decoderName"),
                    value.getInt("avcProfile"),
                    value.getInt("avcLevel"),
                    value.optBoolean("hasAudio", false));
        } catch (IllegalArgumentException error) {
            throw new JSONException(error.getMessage());
        }
    }
}
