package dev.mirror.repurpose.voicelab;

import org.vosk.LibVosk;
import org.vosk.LogLevel;
import org.vosk.Model;
import org.vosk.Recognizer;

import java.io.Closeable;
import java.io.File;
import java.io.IOException;

/** The Vosk recogniser, loaded from a model folder on the device. */
final class VoskEngine implements Listener.Engine, Closeable {
    private final Model model;
    private Recognizer recognizer;

    VoskEngine(File modelDirectory) throws IOException {
        if (!new File(modelDirectory, "am").isDirectory()) {
            throw new IOException("No speech model in " + modelDirectory);
        }
        LibVosk.setLogLevel(LogLevel.WARNINGS);
        model = new Model(modelDirectory.getAbsolutePath());
    }

    /**
     * Starts recognising afresh.
     *
     * @param grammar the sentences to expect as a JSON list, or null for any speech
     */
    void start(String grammar) throws IOException {
        if (recognizer != null) {
            recognizer.close();
        }
        recognizer = grammar == null
                ? new Recognizer(model, Downsampler.TARGET_RATE)
                : new Recognizer(model, Downsampler.TARGET_RATE, grammar);
        recognizer.setWords(true);
    }

    @Override
    public boolean accept(short[] samples, int count) {
        return recognizer.acceptWaveForm(samples, count);
    }

    @Override
    public String result() {
        return recognizer.getResult();
    }

    @Override
    public String finalResult() {
        return recognizer.getFinalResult();
    }

    @Override
    public void close() {
        if (recognizer != null) {
            recognizer.close();
            recognizer = null;
        }
        model.close();
    }
}
