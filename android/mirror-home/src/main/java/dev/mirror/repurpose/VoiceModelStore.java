package dev.mirror.repurpose;

import org.json.JSONException;
import org.json.JSONObject;

import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.nio.charset.StandardCharsets;

/**
 * The speech model on this device: one at a time, replaced as a whole. A
 * model being installed never stands half-written where the recogniser
 * looks for one.
 */
final class VoiceModelStore {
    private final File root;
    private final File model;
    private final File incoming;
    private final File previous;
    private final File record;
    /**
     * Held only while one model takes the place of another, so that asking
     * what is installed never waits for an archive to be unpacked.
     */
    private final Object swap = new Object();

    VoiceModelStore(File root) {
        this.root = root;
        model = new File(root, "model");
        incoming = new File(root, "model.incoming");
        previous = new File(root, "model.previous");
        record = new File(root, "model.json");
    }

    /** Puts right what an installation that was cut short left behind. */
    synchronized void recover() {
        VoiceModelArchive.delete(incoming);
        synchronized (swap) {
            if (!model.isDirectory() && previous.isDirectory()) {
                previous.renameTo(model);
            }
        }
        VoiceModelArchive.delete(previous);
    }

    File directory() {
        return model;
    }

    boolean installed() {
        synchronized (swap) {
            return new File(model, "am/final.mdl").isFile();
        }
    }

    /**
     * Replaces the model with the one in a zip archive.
     *
     * @return what is now installed, as {@link #describe()} gives it
     * @throws IOException with the reason, leaving the model that was there
     */
    synchronized JSONObject install(File archive, String sha256, long nowMs)
            throws IOException, JSONException {
        if (!root.isDirectory() && !root.mkdirs()) {
            throw new IOException("Unable to create the folder for the speech model");
        }
        VoiceModelArchive.delete(incoming);
        VoiceModelArchive.Contents contents;
        try {
            contents = VoiceModelArchive.unpack(archive, incoming);
        } catch (IOException error) {
            VoiceModelArchive.delete(incoming);
            throw error;
        }
        VoiceModelArchive.delete(previous);
        JSONObject description = new JSONObject()
                .put("name", contents.name)
                .put("bytes", contents.bytes)
                .put("files", contents.files)
                .put("sha256", sha256)
                .put("installedAt", nowMs);
        synchronized (swap) {
            if (model.exists() && !model.renameTo(previous)) {
                VoiceModelArchive.delete(incoming);
                throw new IOException("Unable to set the earlier speech model aside");
            }
            if (!incoming.renameTo(model)) {
                previous.renameTo(model);
                VoiceModelArchive.delete(incoming);
                throw new IOException("Unable to put the speech model in place");
            }
            try (FileOutputStream output = new FileOutputStream(record)) {
                output.write(description.toString().getBytes(StandardCharsets.UTF_8));
            }
        }
        VoiceModelArchive.delete(previous);
        return description;
    }

    synchronized boolean remove() {
        boolean had;
        synchronized (swap) {
            had = model.exists();
            VoiceModelArchive.delete(model);
            record.delete();
        }
        VoiceModelArchive.delete(incoming);
        VoiceModelArchive.delete(previous);
        return had;
    }

    /** What is installed, or null if nothing is. */
    JSONObject describe() {
        synchronized (swap) {
            return installed() ? readRecord() : null;
        }
    }

    private JSONObject readRecord() {
        try (FileInputStream input = new FileInputStream(record)) {
            byte[] bytes = new byte[(int) Math.min(record.length(), 4096)];
            int read = 0;
            while (read < bytes.length) {
                int count = input.read(bytes, read, bytes.length - read);
                if (count < 0) {
                    break;
                }
                read += count;
            }
            return new JSONObject(new String(bytes, 0, read, StandardCharsets.UTF_8));
        } catch (IOException | JSONException unreadable) {
            // A model put there by hand has no record; it still counts.
            return new JSONObject();
        }
    }
}
