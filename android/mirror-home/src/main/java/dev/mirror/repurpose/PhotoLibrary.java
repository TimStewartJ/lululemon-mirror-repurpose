package dev.mirror.repurpose;

import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.Matrix;
import android.media.ExifInterface;

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
    public static final int THUMBNAIL_EDGE = 480;
    public static final int DISPLAY_EDGE = 1920;

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
        for (int edge : new int[]{THUMBNAIL_EDGE, DISPLAY_EDGE}) {
            File cached = new File(cacheRoot(), cacheName(photo, edge));
            if (cached.isFile() && !cached.delete()) {
                cached.deleteOnExit();
            }
        }
        return photo.delete();
    }

    /* Scaled JPEG variants, cached per edge and oriented from EXIF: the 480px
       thumbnail feeds the control application's grid and small frames, the
       1920px display variant feeds full-bleed backgrounds and large frames so
       the 2015 WebView never decodes a multi-megapixel original. */
    public synchronized File scaled(String encodedName, int edge) throws IOException {
        File photo = resolve(encodedName);
        if (photo == null || !photo.isFile()) {
            return null;
        }
        File root = cacheRoot();
        if (!root.isDirectory() && !root.mkdirs()) {
            throw new IOException("Unable to create photo cache");
        }
        File cached = new File(root, cacheName(photo, edge));
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
        while (Math.max(bounds.outWidth, bounds.outHeight) / (options.inSampleSize * 2) >= edge) {
            options.inSampleSize *= 2;
        }
        Bitmap decoded = BitmapFactory.decodeFile(photo.getPath(), options);
        if (decoded == null) {
            throw new IOException("Photo could not be decoded");
        }
        Bitmap oriented = decoded;
        try {
            float scale = Math.min(
                    1f,
                    edge / (float) Math.max(decoded.getWidth(), decoded.getHeight()));
            Matrix matrix = orientationMatrix(photo);
            if (scale < 1f) {
                matrix.postScale(scale, scale);
            }
            if (!matrix.isIdentity()) {
                oriented = Bitmap.createBitmap(
                        decoded,
                        0,
                        0,
                        decoded.getWidth(),
                        decoded.getHeight(),
                        matrix,
                        true);
            }
            File temporary = new File(root, cacheName(photo, edge) + ".tmp");
            try (OutputStream output = new FileOutputStream(temporary)) {
                if (!oriented.compress(Bitmap.CompressFormat.JPEG, edge > THUMBNAIL_EDGE ? 88 : 82, output)) {
                    throw new IOException("Photo variant could not be written");
                }
            }
            if (!temporary.renameTo(cached)) {
                if (cached.exists() && !cached.delete() || !temporary.renameTo(cached)) {
                    throw new IOException("Photo variant could not be stored");
                }
            }
            return cached;
        } finally {
            if (oriented != decoded) {
                oriented.recycle();
            }
            decoded.recycle();
        }
    }

    private static Matrix orientationMatrix(File photo) {
        Matrix matrix = new Matrix();
        if (!photo.getName().toLowerCase(Locale.US).matches(".*\\.jpe?g$")) {
            return matrix;
        }
        try {
            int orientation = new ExifInterface(photo.getPath()).getAttributeInt(
                    ExifInterface.TAG_ORIENTATION,
                    ExifInterface.ORIENTATION_NORMAL);
            switch (orientation) {
                case ExifInterface.ORIENTATION_ROTATE_90: matrix.postRotate(90); break;
                case ExifInterface.ORIENTATION_ROTATE_180: matrix.postRotate(180); break;
                case ExifInterface.ORIENTATION_ROTATE_270: matrix.postRotate(270); break;
                case ExifInterface.ORIENTATION_FLIP_HORIZONTAL: matrix.postScale(-1, 1); break;
                case ExifInterface.ORIENTATION_FLIP_VERTICAL: matrix.postScale(1, -1); break;
                case ExifInterface.ORIENTATION_TRANSPOSE:
                    matrix.postRotate(90);
                    matrix.postScale(-1, 1);
                    break;
                case ExifInterface.ORIENTATION_TRANSVERSE:
                    matrix.postRotate(270);
                    matrix.postScale(-1, 1);
                    break;
                default: break;
            }
        } catch (IOException ignored) {
            /* Unreadable EXIF simply means no rotation. */
        }
        return matrix;
    }

    private File cacheRoot() {
        return new File(root.getParentFile(), "photo-variants");
    }

    private static String cacheName(File photo, int edge) {
        return photo.getName() + "." + edge + ".jpg";
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
