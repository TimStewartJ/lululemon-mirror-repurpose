package dev.mirror.repurpose.voicelab;

import org.json.JSONException;
import org.json.JSONObject;

/**
 * Turns a stream of 16 kHz sound into recognised sentences, the same way
 * whether the sound comes from the microphone or from a file. With the gate
 * on, the recogniser only works while someone seems to be speaking.
 */
final class Listener {
    /** The recogniser, reduced to what this class needs, so tests can stand in for it. */
    interface Engine {
        /** Takes sound; true when it considers a sentence finished. */
        boolean accept(short[] samples, int count);

        /** The finished sentence, as the recogniser's JSON. */
        String result();

        /** Whatever was heard so far, ending the sentence. */
        String finalResult();
    }

    interface Sink {
        void heard(JSONObject result) throws JSONException;
    }

    static final int FRAME_SAMPLES = Downsampler.TARGET_RATE * AudioStats.FRAME_MS / 1000;
    /** Sound kept from just before the gate opened, so a first syllable is not lost. */
    private static final int PRE_ROLL_FRAMES = 10;

    private final Engine engine;
    private final Sink sink;
    private final SpeechGate gate;
    private final short[] frame = new short[FRAME_SAMPLES];
    private final short[][] preRoll = new short[PRE_ROLL_FRAMES][FRAME_SAMPLES];
    private int frameFill;
    private int preRollNext;
    private int preRollCount;
    private boolean wasOpen;
    private long framesSeen;
    private long framesFed;

    Listener(Engine engine, boolean gated, Sink sink) {
        this.engine = engine;
        this.sink = sink;
        this.gate = gated ? new SpeechGate() : null;
    }

    void feed(short[] samples, int count) throws JSONException {
        int offset = 0;
        while (offset < count) {
            int take = Math.min(count - offset, FRAME_SAMPLES - frameFill);
            System.arraycopy(samples, offset, frame, frameFill, take);
            frameFill += take;
            offset += take;
            if (frameFill == FRAME_SAMPLES) {
                frameFill = 0;
                frame();
            }
        }
    }

    private void frame() throws JSONException {
        framesSeen++;
        if (gate == null) {
            give(frame);
            return;
        }
        boolean open = gate.accept(AudioStats.rms(frame, 0, FRAME_SAMPLES));
        if (open) {
            if (!wasOpen) {
                for (int index = 0; index < preRollCount; index++) {
                    int slot = (preRollNext - preRollCount + index + PRE_ROLL_FRAMES) % PRE_ROLL_FRAMES;
                    give(preRoll[slot]);
                }
                preRollCount = 0;
            }
            give(frame);
        } else {
            if (wasOpen) {
                emit(engine.finalResult());
            }
            System.arraycopy(frame, 0, preRoll[preRollNext], 0, FRAME_SAMPLES);
            preRollNext = (preRollNext + 1) % PRE_ROLL_FRAMES;
            preRollCount = Math.min(PRE_ROLL_FRAMES, preRollCount + 1);
        }
        wasOpen = open;
    }

    private void give(short[] samples) throws JSONException {
        framesFed++;
        if (engine.accept(samples, FRAME_SAMPLES)) {
            emit(engine.result());
        }
    }

    /** Ends the stream: whatever was being said is reported. */
    void finish() throws JSONException {
        if (gate == null || wasOpen) {
            emit(engine.finalResult());
        }
        wasOpen = false;
    }

    private void emit(String json) throws JSONException {
        JSONObject result = new JSONObject(json);
        if (!result.optString("text", "").trim().isEmpty()) {
            sink.heard(result);
        }
    }

    /** The share of the sound that reached the recogniser, from 0 to 1. */
    double fedShare() {
        return framesSeen == 0 ? 0 : framesFed / (double) framesSeen;
    }

    double secondsSeen() {
        return framesSeen * AudioStats.FRAME_MS / 1000.0;
    }

    double noiseFloorDb() {
        return gate == null ? Double.NaN : AudioStats.decibels(gate.noiseFloor());
    }
}
