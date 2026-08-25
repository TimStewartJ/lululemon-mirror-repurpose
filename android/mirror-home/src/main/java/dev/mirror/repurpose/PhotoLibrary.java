package dev.mirror.repurpose;

import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.URLDecoder;
import java.util.Arrays;
import java.util.Comparator;
import java.util.Locale;

public final class PhotoLibrary {
    public static final long MAX_PHOTO_BYTES = 20L * 1024L * 1024L;
    private static final long MAX_LIBRARY_BYTES = 250L * 1024L * 1024L;
    private static final int MAX_PHOTOS = 250;
    private static final int THUMBNAIL_EDGE = 480;

    private final File root;

    public PhotoLibrary(Context context) {
        root = new File(context.getFilesDir(), "photos");
        if (!root.isDirectory() && !root.mkdirs()) {
            throw new IllegalStateException("Unable to create photo library");
        }
    }

    public synchronized JSONArray list() throws JSONException {
        File[] files = photoFiles();
        Arrays.sort(files, new Comparator<File>() {
            @Override
            public int compare(File first, File second) {
                return Long.compare(second.lastModified(), first.lastModified());
            }
        });
        JSONArray result = new JSONArray();
        for (File file : files) {
            result.put(new JSONObject()
                    .put("name", file.getName())
                    .put("sizeBytes", file.length())
                    .put("modifiedAt", file.lastModified()));
        }
        return result;
    }

    public synchronized File resolve(String encodedName) {
        String name = normalizeName(encodedName);
        if (name == null) {
            return null;
        }
        File candidate = new File(root, name);
        try {
            return root.getCanonicalFile().equals(candidate.getCanonicalFile().getParentFile())
                    ? candidate
                    : null;
        } catch (IOException error) {
            return null;
        }
    }

    public synchronized String store(String encodedName, File temporary, long contentLength)
            throws IOException {
        String requestedName = normalizeName(encodedName);
        if (requestedName == null || !supportedExtension(requestedName)) {
            throw new IOException("Photo must be JPEG, PNG, WebP, or GIF");
        }
        if (!temporary.isFile()
                || contentLength < 1
                || contentLength > MAX_PHOTO_BYTES
                || temporary.length() > MAX_PHOTO_BYTES) {
            throw new IOException("Photo exceeds the 20 MB limit");
        }
        File[] existing = photoFiles();
        long total = 0;
        for (File file : existing) {
            total += file.length();
        }
        if (existing.length >= MAX_PHOTOS || total + temporary.length() > MAX_LIBRARY_BYTES) {
            throw new IOException("Photo library limit reached");
        }

        String storedName = uniqueName(requestedName);
        File destination = new File(root, storedName);
        try (InputStream input = new FileInputStream(temporary);
                OutputStream output = new FileOutputStream(destination)) {
            byte[] buffer = new byte[8192];
            long copied = 0;
            int count;
            while ((count = input.read(buffer)) != -1) {
                copied += count;
                if (copied > MAX_PHOTO_BYTES) {
                    throw new IOException("Photo exceeds the 20 MB limit");
                }
                output.write(buffer, 0, count);
            }
        } catch (IOException error) {
            if (destination.exists() && !destination.delete()) {
                destination.deleteOnExit();
            }
            throw error;
        }
        return storedName;
    }

    public synchronized boolean delete(String encodedName) {
        File photo = resolve(encodedName);
        if (photo == null || !photo.isFile()) {
            return false;
        }
        File cached = new File(new File(root.getParentFile(), "photo-thumbnails"), photo.getName() + ".jpg");
        if (cached.isFile() && !cached.delete()) {
            cached.deleteOnExit();
        }
        return photo.delete();
    }

    /* Small JPEG previews for the control application so phones never have to
       download the full-size originals just to browse the library. */
    public synchronized File thumbnail(String encodedName) throws IOException {
        File photo = resolve(encodedName);
        if (photo == null || !photo.isFile()) {
            return null;
        }
        File cached = new File(thumbnailRoot(), photo.getName() + ".jpg");
        if (cached.isFile() && cached.lastModified() >= photo.lastModified()) {
            return cached;
        }
        BitmapFactory.Options bounds = new BitmapFactory.Options();
        bounds.inJustDecodeBounds = true;
        BitmapFactory.decodeFile(photo.getPath(), bounds);
        if (bounds.outWidth <= 0 || bounds.outHeight <= 0) {
            throw new IOException("Photo could not be decoded");
        }
        BitmapFactory.Options options = new BitmapFactory.Options();
        options.inSampleSize = 1;
        while (Math.max(bounds.outWidth, bounds.outHeight) / (options.inSampleSize * 2)
                >= THUMBNAIL_EDGE) {
            options.inSampleSize *= 2;
        }
        Bitmap decoded = BitmapFactory.decodeFile(photo.getPath(), options);
        if (decoded == null) {
            throw new IOException("Photo could not be decoded");
        }
        float scale = Math.min(
                1f,
                THUMBNAIL_EDGE / (float) Math.max(decoded.getWidth(), decoded.getHeight()));
        Bitmap scaled = scale < 1f
                ? Bitmap.createScaledBitmap(
                        decoded,
                        Math.max(1, Math.round(decoded.getWidth() * scale)),
                        Math.max(1, Math.round(decoded.getHeight() * scale)),
                        true)
                : decoded;
        File temporary = new File(thumbnailRoot(), photo.getName() + ".tmp");
        try (OutputStream output = new FileOutputStream(temporary)) {
            if (!scaled.compress(Bitmap.CompressFormat.JPEG, 82, output)) {
                throw new IOException("Thumbnail could not be written");
            }
        } finally {
            if (scaled != decoded) {
                scaled.recycle();
            }
            decoded.recycle();
        }
        if (!temporary.renameTo(cached)) {
            if (cached.exists() && !cached.delete() || !temporary.renameTo(cached)) {
                throw new IOException("Thumbnail could not be stored");
            }
        }
        return cached;
    }

    private File thumbnailRoot() throws IOException {
        File directory = new File(root.getParentFile(), "photo-thumbnails");
        if (!directory.isDirectory() && !directory.mkdirs()) {
            throw new IOException("Unable to create thumbnail cache");
        }
        return directory;
    }

    public static String mimeType(String name) {
        String lower = name.toLowerCase(Locale.US);
        if (lower.endsWith(".png")) {
            return "image/png";
        }
        if (lower.endsWith(".webp")) {
            return "image/webp";
        }
        if (lower.endsWith(".gif")) {
            return "image/gif";
        }
        return "image/jpeg";
    }

    private File[] photoFiles() {
        File[] files = root.listFiles(file -> file.isFile() && supportedExtension(file.getName()));
        return files == null ? new File[0] : files;
    }

    private String uniqueName(String requestedName) {
        File candidate = new File(root, requestedName);
        if (!candidate.exists()) {
            return requestedName;
        }
        int dot = requestedName.lastIndexOf('.');
        String base = requestedName.substring(0, dot);
        String extension = requestedName.substring(dot);
        return base + "-" + System.currentTimeMillis() + extension;
    }

    private static boolean supportedExtension(String name) {
        String lower = name.toLowerCase(Locale.US);
        return lower.endsWith(".jpg")
                || lower.endsWith(".jpeg")
                || lower.endsWith(".png")
                || lower.endsWith(".webp")
                || lower.endsWith(".gif");
    }

    private static String normalizeName(String encodedName) {
        if (encodedName == null || encodedName.isEmpty() || encodedName.length() > 180) {
            return null;
        }
        final String decoded;
        try {
            decoded = URLDecoder.decode(encodedName, "UTF-8").trim();
        } catch (Exception error) {
            return null;
        }
        if (decoded.isEmpty()
                || decoded.startsWith(".")
                || decoded.contains("..")
                || decoded.contains("/")
                || decoded.contains("\\")
                || decoded.indexOf('\0') >= 0
                || !decoded.matches("[A-Za-z0-9 _().-]+")) {
            return null;
        }
        return decoded;
    }
}
