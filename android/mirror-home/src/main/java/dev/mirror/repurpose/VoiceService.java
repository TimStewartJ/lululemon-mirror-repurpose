package dev.mirror.repurpose;

import android.Manifest;
import android.app.Service;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.media.AudioFormat;
import android.media.AudioRecord;
import android.media.MediaRecorder;
import android.os.Bundle;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import android.os.Message;
import android.os.Messenger;
import android.os.Process;
import android.os.RemoteException;
import android.os.SystemClock;
import android.util.Log;

import java.io.File;
import java.io.FileInputStream;
import java.io.IOException;

/**
 * Hears the microphone and turns speech into sentences, in a process of its
 * own. It only reports what it hears; {@link VoiceManager} in the dashboard's
 * process decides what that means. No sound is stored or leaves the device.
 *
 * <p>Measured on a Mirror: recognising everything the microphone hears takes
 * a third of one of its four cores, and the model about 120 MB.
 */
public final class VoiceService extends Service {
    static final String PROCESS_SUFFIX = ":voice";
    static final int SAMPLE_RATE = 16_000;

    /** To the service: a model folder and a list of sentences; replies go to {@code replyTo}. */
    static final int MSG_START = 1;
    static final int MSG_STOP = 2;
    /** To the service: a WAV file to hear in place of the microphone (debug builds). */
    static final int MSG_CLIP = 3;
    /** From the service. */
    static final int MSG_STATE = 10;
    static final int MSG_SENTENCE = 11;
    static final int MSG_STATS = 12;

    static final String KEY_MODEL = "model";
    static final String KEY_GRAMMAR = "grammar";
    static final String KEY_PATH = "path";
    static final String KEY_STATE = "state";
    static final String KEY_DETAIL = "detail";
    static final String KEY_PID = "pid";
    static final String KEY_LOAD_MS = "loadMs";
    static final String KEY_JSON = "json";
    static final String KEY_LEVEL_DB = "levelDb";
    static final String KEY_PEAK_DB = "peakDb";
    static final String KEY_SILENT = "silent";
    static final String KEY_CPU_SHARE = "cpuShare";
    static final String KEY_BEHIND_MS = "behindMs";
    static final String KEY_LISTENED_MS = "listenedMs";

    static final String STATE_LOADING = "loading";
    static final String STATE_LISTENING = "listening";
    static final String STATE_ERROR = "error";
    static final String STATE_STOPPED = "stopped";

    private static final String TAG = "VoiceService";
    private static final int CHUNK_SAMPLES = SAMPLE_RATE / 10;
    /** Room for sound that waits while the processor is busy, so that it is late rather than lost. */
    private static final int BUFFER_SECONDS = 10;
    private static final long STATS_INTERVAL_MS = 5_000L;
    private static final double SILENCE_DB = -120.0;
    /** How long the process waits to be wanted again before it ends. */
    private static final long EXIT_DELAY_MS = 2_000L;

    /** Services alive in this process; only its main thread counts them. */
    private static int alive;

    private final Handler handler = new Handler(Looper.getMainLooper(), message -> {
        handle(message);
        return true;
    });
    private final Messenger messenger = new Messenger(handler);
    private Listening listening;

    /** Whether this process is the recogniser's, which runs none of Mirror Home's other parts. */
    static boolean isVoiceProcess() {
        byte[] name = new byte[256];
        try (FileInputStream input = new FileInputStream("/proc/self/cmdline")) {
            int length = Math.max(0, input.read(name));
            int end = 0;
            while (end < length && name[end] != 0) {
                end++;
            }
            return new String(name, 0, end, java.nio.charset.StandardCharsets.UTF_8).endsWith(PROCESS_SUFFIX);
        } catch (IOException unreadable) {
            return false;
        }
    }

    @Override
    public void onCreate() {
        super.onCreate();
        alive++;
    }

    @Override
    public IBinder onBind(Intent intent) {
        return messenger.getBinder();
    }

    @Override
    public boolean onUnbind(Intent intent) {
        stopListening();
        return false;
    }

    @Override
    public void onDestroy() {
        stopListening();
        alive--;
        // Switched off means gone: Android would otherwise keep the process, and
        // whatever the recogniser's libraries still hold, for as long as it pleases.
        handler.postDelayed(() -> {
            if (alive == 0) {
                Process.killProcess(Process.myPid());
            }
        }, EXIT_DELAY_MS);
        super.onDestroy();
    }

    private void handle(Message message) {
        Bundle data = message.getData();
        switch (message.what) {
            case MSG_START:
                stopListening();
                listening = new Listening(
                        new File(data.getString(KEY_MODEL, "")),
                        data.getString(KEY_GRAMMAR, "[]"),
                        message.replyTo);
                listening.start();
                break;
            case MSG_STOP:
                stopListening();
                break;
            case MSG_CLIP:
                if (BuildConfig.DEBUG && listening != null) {
                    listening.hear(new File(data.getString(KEY_PATH, "")));
                }
                break;
            default:
                break;
        }
    }

    private void stopListening() {
        Listening previous = listening;
        listening = null;
        if (previous == null) {
            return;
        }
        previous.stopRequested = true;
        try {
            // The microphone has to be free before it is opened again.
            previous.join(3_000L);
        } catch (InterruptedException interrupted) {
            Thread.currentThread().interrupt();
        }
    }

    /** One stretch of listening, from loading the model to letting go of the microphone. */
    private final class Listening extends Thread {
        private final File model;
        private final String grammar;
        private final Messenger client;
        private final Object clipLock = new Object();
        private short[] clip;
        private int clipOffset;
        volatile boolean stopRequested;

        Listening(File model, String grammar, Messenger client) {
            super("voice-listening");
            this.model = model;
            this.grammar = grammar;
            this.client = client;
        }

        void hear(File wav) {
            try {
                short[] samples = VoiceClip.read(wav);
                synchronized (clipLock) {
                    clip = samples;
                    clipOffset = 0;
                }
            } catch (IOException error) {
                Log.w(TAG, "Unable to read the clip to hear", error);
            } finally {
                wav.delete();
            }
        }

        @Override
        public void run() {
            VoskRecognizer recognizer = null;
            AudioRecord microphone = null;
            long began = SystemClock.elapsedRealtime();
            long loadMs = 0;
            try {
                report(STATE_LOADING, "", 0);
                recognizer = new VoskRecognizer(model, grammar);
                loadMs = SystemClock.elapsedRealtime() - began;
                microphone = openMicrophone();
                report(STATE_LISTENING, "", loadMs);
                listen(recognizer, microphone);
                report(STATE_STOPPED, "", loadMs);
            } catch (Throwable error) {
                // Also a library that this device cannot load: the dashboard carries on without voice.
                Log.e(TAG, "Listening ended", error);
                report(STATE_ERROR, explain(error), loadMs);
            } finally {
                if (microphone != null) {
                    if (microphone.getRecordingState() == AudioRecord.RECORDSTATE_RECORDING) {
                        microphone.stop();
                    }
                    microphone.release();
                }
                if (recognizer != null) {
                    recognizer.close();
                }
            }
        }

        private AudioRecord openMicrophone() throws IOException {
            if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
                throw new SecurityException("The microphone permission has not been granted");
            }
            int minimum = AudioRecord.getMinBufferSize(
                    SAMPLE_RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT);
            if (minimum <= 0) {
                throw new IOException("This device offers no 16 kHz microphone");
            }
            AudioRecord microphone = new AudioRecord(
                    MediaRecorder.AudioSource.VOICE_RECOGNITION,
                    SAMPLE_RATE,
                    AudioFormat.CHANNEL_IN_MONO,
                    AudioFormat.ENCODING_PCM_16BIT,
                    Math.max(minimum * 4, SAMPLE_RATE * 2 * BUFFER_SECONDS));
            if (microphone.getState() != AudioRecord.STATE_INITIALIZED) {
                microphone.release();
                throw new IOException("The microphone could not be opened");
            }
            microphone.startRecording();
            if (microphone.getRecordingState() != AudioRecord.RECORDSTATE_RECORDING) {
                microphone.release();
                throw new IOException("Recording did not start; something else may hold the microphone");
            }
            return microphone;
        }

        private void listen(VoskRecognizer recognizer, AudioRecord microphone) throws IOException {
            short[] chunk = new short[CHUNK_SAMPLES];
            long began = SystemClock.elapsedRealtime();
            long samplesRead = 0;
            long startDelay = -1;
            long behindMs = 0;
            long periodBegan = began;
            long periodCpu = Process.getElapsedCpuTime();
            double sumSquares = 0;
            long measured = 0;
            int peak = 0;
            while (!stopRequested) {
                int read = microphone.read(chunk, 0, chunk.length);
                if (read < 0) {
                    throw new IOException("Reading the microphone failed with code " + read);
                }
                if (read == 0) {
                    continue;
                }
                samplesRead += read;
                long now = SystemClock.elapsedRealtime();
                // Sound that waited to be read arrives later than it was made.
                long late = now - began - samplesRead * 1000 / SAMPLE_RATE;
                if (startDelay < 0) {
                    startDelay = late;
                }
                behindMs = Math.max(behindMs, late - startDelay);
                replaceWithClip(chunk, read);
                for (int index = 0; index < read; index++) {
                    int sample = chunk[index];
                    sumSquares += (double) sample * sample;
                    peak = Math.max(peak, Math.abs(sample));
                }
                measured += read;
                if (recognizer.accept(chunk, read)) {
                    Bundle sentence = new Bundle();
                    sentence.putString(KEY_JSON, recognizer.result());
                    send(MSG_SENTENCE, sentence);
                }
                if (now - periodBegan >= STATS_INTERVAL_MS) {
                    long cpu = Process.getElapsedCpuTime();
                    Bundle stats = new Bundle();
                    stats.putDouble(KEY_LEVEL_DB, decibels(Math.sqrt(sumSquares / Math.max(1, measured))));
                    stats.putDouble(KEY_PEAK_DB, decibels(peak));
                    stats.putBoolean(KEY_SILENT, peak == 0);
                    stats.putDouble(KEY_CPU_SHARE, (cpu - periodCpu) / (double) Math.max(1, now - periodBegan));
                    stats.putLong(KEY_BEHIND_MS, behindMs);
                    stats.putLong(KEY_LISTENED_MS, now - began);
                    send(MSG_STATS, stats);
                    periodBegan = now;
                    periodCpu = cpu;
                    sumSquares = 0;
                    measured = 0;
                    peak = 0;
                    behindMs = 0;
                }
            }
        }

        /** While a clip is being heard, its sound takes the place of the microphone's. */
        private void replaceWithClip(short[] chunk, int count) {
            synchronized (clipLock) {
                if (clip == null) {
                    return;
                }
                int available = Math.min(count, clip.length - clipOffset);
                System.arraycopy(clip, clipOffset, chunk, 0, available);
                java.util.Arrays.fill(chunk, available, count, (short) 0);
                clipOffset += available;
                if (clipOffset >= clip.length) {
                    clip = null;
                }
            }
        }

        private void report(String state, String detail, long loadMs) {
            Bundle data = new Bundle();
            data.putString(KEY_STATE, state);
            data.putString(KEY_DETAIL, detail);
            data.putInt(KEY_PID, Process.myPid());
            data.putLong(KEY_LOAD_MS, loadMs);
            send(MSG_STATE, data);
        }

        private void send(int what, Bundle data) {
            Message message = Message.obtain(null, what);
            message.setData(data);
            try {
                client.send(message);
            } catch (RemoteException gone) {
                // The dashboard's process has ended; there is nobody to listen for.
                stopRequested = true;
            }
        }
    }

    /** Why listening ended, in words for the controls. */
    static String explain(Throwable error) {
        String message = error.getMessage();
        if (error instanceof LinkageError) {
            return "The speech recogniser does not run on this device";
        }
        if ((error instanceof IOException || error instanceof SecurityException)
                && message != null && !message.isEmpty()) {
            return message;
        }
        return error.getClass().getSimpleName() + (message == null ? "" : ": " + message);
    }

    static double decibels(double level) {
        return level < 1 ? SILENCE_DB : Math.round(200.0 * Math.log10(level / 32768.0)) / 10.0;
    }
}
