package dev.mirror.repurpose;

import android.os.SystemClock;
import android.util.Log;

import org.vosk.LibVosk;
import org.vosk.LogLevel;
import org.vosk.Model;
import org.vosk.Recognizer;

import java.io.Closeable;
import java.io.File;
import java.io.IOException;

/** The Vosk recogniser with a speech model from this device and a list of sentences to expect. */
final class VoskRecognizer implements Closeable {
    private static final String TAG = "VoskRecognizer";
    /**
     * Vosk keeps what it worked out about the sound for ten minutes at a
     * time, which on a Mirror grew its process from 123 MB to 157 MB. A
     * recogniser made anew between two sentences every two minutes forgets
     * it sooner; the model stays loaded.
     */
    private static final long RENEW_AFTER_SAMPLES = 120L * VoiceService.SAMPLE_RATE;

    private final Model model;
    private final String grammar;
    private Recognizer recognizer;
    private long samplesHeard;

    /**
     * Loads the model, which takes some seconds and about 120 MB.
     *
     * @param grammar the sentences to expect, as a JSON list
     */
    VoskRecognizer(File modelDirectory, String grammar) throws IOException {
        LibVosk.setLogLevel(LogLevel.WARNINGS);
        this.grammar = grammar;
        try {
            model = new Model(modelDirectory.getAbsolutePath());
        } catch (IOException unusable) {
            // The library says no more than that it failed; its reasons are in Android's log.
            throw new IOException("The speech model could not be loaded");
        }
        try {
            recognizer = newRecognizer();
        } catch (IOException | RuntimeException error) {
            model.close();
            throw error;
        }
    }

    private Recognizer newRecognizer() throws IOException {
        Recognizer made;
        try {
            made = new Recognizer(model, VoiceService.SAMPLE_RATE, grammar);
        } catch (IOException unusable) {
            throw new IOException("The speech model does not take the list of commands");
        }
        made.setWords(true);
        return made;
    }

    /** Takes sound; true when a sentence has ended and {@link #result()} holds it. */
    boolean accept(short[] samples, int count) {
        samplesHeard += count;
        return recognizer.acceptWaveForm(samples, count);
    }

    /**
     * How many samples the recogniser in use has been given. The times it
     * reports for words count from its first sample; zero right after
     * {@link #result()} means that a new one has taken over.
     */
    long position() {
        return samplesHeard;
    }

    /** Whether the recogniser holds words of a sentence that it has not ended yet. */
    boolean wordsUnderWay() {
        try {
            return !new org.json.JSONObject(recognizer.getPartialResult()).optString("partial", "").isEmpty();
        } catch (org.json.JSONException unreadable) {
            return false;
        }
    }

    /** The sentence that has just ended. To be asked for once, after {@link #accept} said so. */
    String result() throws IOException {
        String result = recognizer.getResult();
        if (samplesHeard >= RENEW_AFTER_SAMPLES) {
            // Between two sentences nothing is lost: sound that arrives meanwhile waits its turn.
            long began = SystemClock.elapsedRealtime();
            recognizer.close();
            // Not to be closed twice if the new one cannot be made.
            recognizer = null;
            recognizer = newRecognizer();
            samplesHeard = 0;
            Log.d(TAG, "Recogniser made anew in " + (SystemClock.elapsedRealtime() - began) + " ms");
        }
        return result;
    }

    @Override
    public void close() {
        if (recognizer != null) {
            recognizer.close();
        }
        model.close();
    }
}
