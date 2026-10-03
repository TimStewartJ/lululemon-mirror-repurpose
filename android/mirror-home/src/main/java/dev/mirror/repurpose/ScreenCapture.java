package dev.mirror.repurpose;

import android.graphics.Bitmap;
import android.os.Handler;
import android.os.Looper;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;

/**
 * A picture of what the glass shows, for the assistant to look at. The
 * dashboard draws itself into it; Android 6 gives an app no way to copy the
 * screen, and a video layer cannot be drawn at all, so a background film
 * appears as its still poster.
 */
final class ScreenCapture {
    static final int MIN_WIDTH = 180;
    static final int MAX_WIDTH = 1080;
    static final int DEFAULT_WIDTH = 540;

    /** Draws the glass on the main thread; null while there is nothing to see. */
    interface Source {
        Bitmap draw(int width);
    }

    private static final long WAIT_MS = 4_000L;
    private static volatile Source source;

    private ScreenCapture() {
    }

    static void attach(Source drawing) {
        source = drawing;
    }

    static void detach(Source drawing) {
        if (source == drawing) {
            source = null;
        }
    }

    /**
     * The glass as a JPEG. Any thread but the main one.
     *
     * @return the file's bytes, or null while the display is dark or the dashboard is not showing
     * @throws IOException if the picture could not be made in time
     */
    static byte[] jpeg(int width) throws IOException {
        Source drawing = source;
        if (drawing == null) {
            return null;
        }
        AtomicReference<Bitmap> picture = new AtomicReference<>();
        AtomicReference<RuntimeException> failure = new AtomicReference<>();
        CountDownLatch drawn = new CountDownLatch(1);
        new Handler(Looper.getMainLooper()).post(() -> {
            try {
                picture.set(drawing.draw(width));
            } catch (RuntimeException | OutOfMemoryError error) {
                failure.set(new IllegalStateException(error.toString()));
            } finally {
                drawn.countDown();
            }
        });
        try {
            if (!drawn.await(WAIT_MS, TimeUnit.MILLISECONDS)) {
                throw new IOException("The glass could not be drawn in time");
            }
        } catch (InterruptedException interrupted) {
            Thread.currentThread().interrupt();
            throw new IOException("Interrupted while the glass was drawn");
        }
        if (failure.get() != null) {
            throw new IOException("The glass could not be drawn: " + failure.get().getMessage());
        }
        Bitmap bitmap = picture.get();
        if (bitmap == null) {
            return null;
        }
        try {
            ByteArrayOutputStream file = new ByteArrayOutputStream(96 * 1024);
            bitmap.compress(Bitmap.CompressFormat.JPEG, 82, file);
            return file.toByteArray();
        } finally {
            bitmap.recycle();
        }
    }

    /** The width to draw for a request: what was asked for, within what is offered. */
    static int width(String asked) {
        try {
            return asked == null
                    ? DEFAULT_WIDTH
                    : Math.max(MIN_WIDTH, Math.min(MAX_WIDTH, Integer.parseInt(asked.trim())));
        } catch (NumberFormatException notANumber) {
            return DEFAULT_WIDTH;
        }
    }
}
