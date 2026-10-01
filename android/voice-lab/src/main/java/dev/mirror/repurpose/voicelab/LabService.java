package dev.mirror.repurpose.voicelab;

import android.app.ActivityManager;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.media.AudioFormat;
import android.media.AudioManager;
import android.media.AudioRecord;
import android.media.AudioTrack;
import android.media.audiofx.AcousticEchoCanceler;
import android.media.audiofx.AutomaticGainControl;
import android.media.audiofx.NoiseSuppressor;
import android.os.Debug;
import android.os.IBinder;
import android.os.PowerManager;
import android.os.Process;
import android.os.SystemClock;
import android.util.Log;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.Arrays;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * Runs one experiment at a time, started from a computer:
 *
 * <pre>adb shell am startservice -n dev.mirror.repurpose.voicelab/.LabService \
 *     --es cmd record --es seconds 5 --es out first</pre>
 *
 * Every extra is a string. Files live in the app's external files folder,
 * where adb can put models and sound clips and fetch results:
 * {@code results/NAME.json}, {@code recordings/NAME.wav}.
 *
 * <ul>
 * <li>{@code info}: what the device offers for hearing and speaking.</li>
 * <li>{@code record}: record the microphone and describe the recording,
 *     optionally while the speakers play a tone or a clip.</li>
 * <li>{@code decode}: recognise sound clips with the speech model on the
 *     device, timing it.</li>
 * <li>{@code listen}: recognise the live microphone, or a clip as if live,
 *     optionally send what was understood to Mirror Home, and optionally
 *     keep what the microphone heard.</li>
 * <li>{@code tone}: play a tone, to test the speakers.</li>
 * <li>{@code stop}: end a recording or listening early.</li>
 * </ul>
 */
public final class LabService extends Service {
    static final String TAG = "VoiceLab";
    private static final int CHUNK_MS = 100;
    private static final long LONGEST_EXPERIMENT_MS = 12 * 60 * 60 * 1000L;
    private static final int MICROPHONE_BUFFER_SECONDS = 10;

    private final ExecutorService worker = Executors.newSingleThreadExecutor();
    private volatile boolean stopRequested;

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent == null) {
            return START_NOT_STICKY;
        }
        String command = intent.getStringExtra("cmd");
        if ("stop".equals(command)) {
            stopRequested = true;
        } else {
            worker.execute(() -> run(command == null ? "" : command, intent));
        }
        return START_NOT_STICKY;
    }

    @Override
    public void onDestroy() {
        stopRequested = true;
        worker.shutdownNow();
        super.onDestroy();
    }

    private void run(String command, Intent intent) {
        String name = text(intent, "out", command + "-" + System.currentTimeMillis());
        JSONObject result = new JSONObject();
        PowerManager.WakeLock wakeLock = ((PowerManager) getSystemService(Context.POWER_SERVICE))
                .newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "VoiceLab:experiment");
        wakeLock.setReferenceCounted(false);
        // Released when the experiment ends; the limit is for one that never does.
        wakeLock.acquire(LONGEST_EXPERIMENT_MS);
        try {
            stopRequested = false;
            Log.i(TAG, "start " + command + " as " + name + " in " + getExternalFilesDir(null));
            result.put("command", command).put("startedAt", System.currentTimeMillis());
            switch (command) {
                case "info":
                    result.put("info", DeviceInfo.collect(this));
                    break;
                case "record":
                    record(intent, name, result);
                    break;
                case "decode":
                    decode(intent, result);
                    break;
                case "listen":
                    listen(intent, result);
                    break;
                case "tone":
                    tone(intent, result);
                    break;
                default:
                    throw new IllegalArgumentException("Unknown command: " + command);
            }
            result.put("ok", true);
        } catch (Exception | LinkageError | OutOfMemoryError error) {
            // A library Android 6 cannot load is a finding too, so it is recorded.
            Log.e(TAG, command + " failed", error);
            try {
                result.put("ok", false).put("error", error.toString());
            } catch (JSONException impossible) {
                // The result stays without its error.
            }
        } finally {
            wakeLock.release();
            write(name, result);
            Log.i(TAG, "done " + name);
        }
    }

    // ---------------------------------------------------------------------
    // record
    // ---------------------------------------------------------------------

    private void record(Intent intent, String name, JSONObject result) throws Exception {
        String sourceName = text(intent, "source", "MIC");
        Integer source = DeviceInfo.SOURCES.get(sourceName);
        if (source == null) {
            throw new IllegalArgumentException("Unknown source " + sourceName);
        }
        int rate = number(intent, "rate", Downsampler.TARGET_RATE);
        int channels = number(intent, "channels", 1);
        boolean effects = flag(intent, "effects", false);
        int toneHz = number(intent, "tone", 0);
        // A clip played on the speakers lets a Mirror test its own hearing with nobody there.
        String playName = text(intent, "play", "");
        Wav played = playName.isEmpty() ? null : Wav.read(new File(base(), playName));
        int seconds = number(intent, "seconds", played == null ? 5 : (int) Math.ceil(played.seconds()) + 1);
        int volume = number(intent, "volume", -1);
        int mask = channels == 2 ? AudioFormat.CHANNEL_IN_STEREO : AudioFormat.CHANNEL_IN_MONO;
        int minimum = AudioRecord.getMinBufferSize(rate, mask, AudioFormat.ENCODING_PCM_16BIT);
        if (minimum <= 0) {
            throw new IOException("This device does not offer " + rate + " Hz with " + channels + " channel(s)");
        }
        AudioRecord recorder = DeviceInfo.open(
                this, source, rate, mask, Math.max(minimum * 4, rate * channels * 2));
        JSONArray applied = new JSONArray();
        AcousticEchoCanceler echo = null;
        NoiseSuppressor noise = null;
        AutomaticGainControl gain = null;
        AudioTrack tone = null;
        short[] samples = new short[rate * channels * seconds];
        int filled = 0;
        int readError = 0;
        long started = SystemClock.elapsedRealtime();
        AudioManager audio = (AudioManager) getSystemService(Context.AUDIO_SERVICE);
        int volumeBefore = audio.getStreamVolume(AudioManager.STREAM_MUSIC);
        try {
            if (recorder.getState() != AudioRecord.STATE_INITIALIZED) {
                throw new IOException("The microphone could not be opened as " + sourceName);
            }
            if (effects) {
                int session = recorder.getAudioSessionId();
                if (AcousticEchoCanceler.isAvailable() && (echo = AcousticEchoCanceler.create(session)) != null) {
                    echo.setEnabled(true);
                    applied.put("echoCanceler");
                }
                if (NoiseSuppressor.isAvailable() && (noise = NoiseSuppressor.create(session)) != null) {
                    noise.setEnabled(true);
                    applied.put("noiseSuppressor");
                }
                if (AutomaticGainControl.isAvailable() && (gain = AutomaticGainControl.create(session)) != null) {
                    gain.setEnabled(true);
                    applied.put("gainControl");
                }
            }
            recorder.startRecording();
            if (recorder.getRecordingState() != AudioRecord.RECORDSTATE_RECORDING) {
                throw new IOException("Recording did not start; something else may hold the microphone");
            }
            if (volume >= 0 && (toneHz > 0 || played != null)) {
                audio.setStreamVolume(AudioManager.STREAM_MUSIC, volume, 0);
            }
            if (played != null) {
                tone = play(played.sampleRate, played.channel(0));
            } else if (toneHz > 0) {
                tone = playTone(toneHz, seconds);
            }
            int chunk = rate * channels * CHUNK_MS / 1000;
            while (filled < samples.length && !stopRequested) {
                int read = recorder.read(samples, filled, Math.min(chunk, samples.length - filled));
                if (read < 0) {
                    readError = read;
                    break;
                }
                filled += read;
            }
        } finally {
            if (tone != null) {
                tone.release();
            }
            audio.setStreamVolume(AudioManager.STREAM_MUSIC, volumeBefore, 0);
            if (recorder.getRecordingState() == AudioRecord.RECORDSTATE_RECORDING) {
                recorder.stop();
            }
            release(echo, noise, gain);
            recorder.release();
        }
        File file = new File(folder("recordings"), name + ".wav");
        Wav.write(file, rate, channels, samples, filled);
        Wav wav = new Wav(rate, channels, Arrays.copyOf(samples, filled));
        JSONArray perChannel = new JSONArray();
        for (int channel = 0; channel < channels; channel++) {
            perChannel.put(AudioStats.describe(wav.channel(channel), rate));
        }
        result.put("file", "recordings/" + file.getName())
                .put("source", sourceName)
                .put("rate", rate)
                .put("channels", channels)
                .put("effects", applied)
                .put("toneHz", toneHz)
                .put("played", playName.isEmpty() ? JSONObject.NULL : playName)
                .put("mediaVolume", volume >= 0 ? volume : volumeBefore)
                .put("readError", readError)
                .put("wallMs", SystemClock.elapsedRealtime() - started)
                .put("stats", perChannel);
        if (channels == 2) {
            // Two identical channels mean one microphone presented twice.
            short[] left = wav.channel(0);
            short[] right = wav.channel(1);
            short[] difference = new short[left.length];
            for (int index = 0; index < left.length; index++) {
                difference[index] = (short) Math.max(-32768, Math.min(32767, left[index] - right[index]));
            }
            result.put("channelDifferenceDb", AudioStats.round(
                    AudioStats.decibels(AudioStats.rms(difference, 0, difference.length))));
        }
    }

    private static void release(AcousticEchoCanceler echo, NoiseSuppressor noise, AutomaticGainControl gain) {
        if (echo != null) {
            echo.release();
        }
        if (noise != null) {
            noise.release();
        }
        if (gain != null) {
            gain.release();
        }
    }

    // ---------------------------------------------------------------------
    // tone
    // ---------------------------------------------------------------------

    private void tone(Intent intent, JSONObject result) throws Exception {
        int hertz = number(intent, "hz", 440);
        int seconds = number(intent, "seconds", 2);
        int volume = number(intent, "volume", -1);
        AudioManager audio = (AudioManager) getSystemService(Context.AUDIO_SERVICE);
        int before = audio.getStreamVolume(AudioManager.STREAM_MUSIC);
        if (volume >= 0) {
            audio.setStreamVolume(AudioManager.STREAM_MUSIC, volume, 0);
        }
        AudioTrack track = playTone(hertz, seconds);
        try {
            SystemClock.sleep(seconds * 1000L);
        } finally {
            track.release();
            if (volume >= 0) {
                audio.setStreamVolume(AudioManager.STREAM_MUSIC, before, 0);
            }
        }
        result.put("hz", hertz)
                .put("seconds", seconds)
                .put("volumeBefore", before)
                .put("volumeUsed", volume >= 0 ? volume : before)
                .put("volumeMax", audio.getStreamMaxVolume(AudioManager.STREAM_MUSIC));
    }

    private static AudioTrack playTone(int hertz, int seconds) throws IOException {
        int rate = 48_000;
        short[] samples = new short[rate * seconds];
        for (int index = 0; index < samples.length; index++) {
            // A quarter of full scale: audible without being a shock.
            samples[index] = (short) (8192 * Math.sin(2 * Math.PI * hertz * index / rate));
        }
        return play(rate, samples);
    }

    /** Starts playing mono sound on the speakers; the caller releases the track. */
    @SuppressWarnings("deprecation")
    private static AudioTrack play(int rate, short[] samples) throws IOException {
        AudioTrack track = new AudioTrack(
                AudioManager.STREAM_MUSIC, rate, AudioFormat.CHANNEL_OUT_MONO,
                AudioFormat.ENCODING_PCM_16BIT, samples.length * 2, AudioTrack.MODE_STATIC);
        if (track.getState() == AudioTrack.STATE_UNINITIALIZED) {
            track.release();
            throw new IOException("The speakers could not be opened at " + rate + " Hz");
        }
        track.write(samples, 0, samples.length);
        track.play();
        return track;
    }

    // ---------------------------------------------------------------------
    // decode
    // ---------------------------------------------------------------------

    private void decode(Intent intent, JSONObject result) throws Exception {
        Commands commands = commands(intent);
        boolean gated = flag(intent, "gate", false);
        double confidence = Double.parseDouble(text(intent, "confidence", "0.5"));
        File clipFolder = new File(base(), text(intent, "clips", "clips"));
        String only = text(intent, "only", "");
        File[] clips = clipFolder.listFiles((folder, file) -> file.endsWith(".wav") && file.startsWith(only));
        if (clips == null || clips.length == 0) {
            throw new IOException("No .wav files" + (only.isEmpty() ? "" : " named " + only + "*") + " in " + clipFolder);
        }
        Arrays.sort(clips);

        result.put("memoryBefore", memory());
        long loadStarted = SystemClock.elapsedRealtime();
        long loadCpu = Process.getElapsedCpuTime();
        try (VoskEngine engine = new VoskEngine(new File(base(), text(intent, "model", "model")))) {
            result.put("modelLoadMs", SystemClock.elapsedRealtime() - loadStarted)
                    .put("modelLoadCpuMs", Process.getElapsedCpuTime() - loadCpu)
                    .put("memoryLoaded", memory())
                    .put("gated", gated)
                    .put("phrases", commands == null ? JSONObject.NULL : commands.phrases().size());
            JSONArray reports = new JSONArray();
            double audioSeconds = 0;
            long wallTotal = 0;
            long cpuTotal = 0;
            for (File clip : clips) {
                if (stopRequested) {
                    break;
                }
                short[] sound = Wav.read(clip).mono16k();
                JSONArray heard = new JSONArray();
                engine.start(commands == null ? null : commands.grammar());
                Listener listener = new Listener(engine, gated, sentence ->
                        heard.put(describe(sentence, commands, confidence)));
                long wall = SystemClock.elapsedRealtime();
                long cpu = Process.getElapsedCpuTime();
                feed(listener, sound, false);
                listener.finish();
                wall = SystemClock.elapsedRealtime() - wall;
                cpu = Process.getElapsedCpuTime() - cpu;
                double seconds = sound.length / (double) Downsampler.TARGET_RATE;
                audioSeconds += seconds;
                wallTotal += wall;
                cpuTotal += cpu;
                reports.put(new JSONObject()
                        .put("clip", clip.getName())
                        .put("seconds", AudioStats.round(seconds))
                        .put("wallMs", wall)
                        .put("cpuMs", cpu)
                        .put("fedShare", AudioStats.round(listener.fedShare()))
                        .put("heard", heard));
            }
            result.put("clips", reports)
                    .put("audioSeconds", AudioStats.round(audioSeconds))
                    .put("wallMs", wallTotal)
                    .put("cpuMs", cpuTotal)
                    // Below 1 the device recognises faster than people speak.
                    .put("realTimeFactor", AudioStats.round(wallTotal / 1000.0 / Math.max(0.001, audioSeconds)))
                    .put("memoryAfter", memory());
        }
    }

    private static JSONObject describe(JSONObject sentence, Commands commands, double confidence)
            throws JSONException {
        double lowest = Commands.lowestConfidence(sentence);
        String command = commands == null ? null : commands.accepted(sentence, confidence);
        return new JSONObject()
                .put("text", sentence.optString("text", ""))
                .put("lowestConfidence", Double.isNaN(lowest) ? JSONObject.NULL : AudioStats.round(lowest))
                .put("command", command == null ? JSONObject.NULL : command);
    }

    /**
     * Hands sound to the listener in the pieces a microphone would deliver.
     *
     * @return at speaking pace, the most milliseconds recognition fell behind the sound
     */
    private long feed(Listener listener, short[] sound, boolean atSpeakingPace) throws JSONException {
        int chunk = Downsampler.TARGET_RATE * CHUNK_MS / 1000;
        short[] piece = new short[chunk];
        long began = SystemClock.elapsedRealtime();
        long behind = 0;
        for (int offset = 0; offset < sound.length && !stopRequested; offset += chunk) {
            int count = Math.min(chunk, sound.length - offset);
            System.arraycopy(sound, offset, piece, 0, count);
            if (atSpeakingPace) {
                // A microphone hands over each piece once it has been said, however
                // long the piece before it took to recognise.
                long early = began + (offset + count) * 1000L / Downsampler.TARGET_RATE
                        - SystemClock.elapsedRealtime();
                if (early > 0) {
                    SystemClock.sleep(early);
                } else {
                    behind = Math.max(behind, -early);
                }
            }
            listener.feed(piece, count);
        }
        return behind;
    }

    // ---------------------------------------------------------------------
    // listen
    // ---------------------------------------------------------------------

    private void listen(Intent intent, JSONObject result) throws Exception {
        Commands commands = commands(intent);
        boolean gated = flag(intent, "gate", true);
        boolean act = flag(intent, "act", false);
        double confidence = Double.parseDouble(text(intent, "confidence", "0.5"));
        int seconds = number(intent, "seconds", 60);
        String input = text(intent, "input", "");
        MirrorClient mirror = null;
        if (act) {
            File token = new File(base(), "token.txt");
            if (!token.isFile()) {
                throw new IOException("No token.txt beside the model, so nothing can be sent to Mirror Home");
            }
            mirror = new MirrorClient(read(token).trim());
        }
        MirrorClient client = mirror;

        result.put("memoryBefore", memory());
        long loadStarted = SystemClock.elapsedRealtime();
        try (VoskEngine engine = new VoskEngine(new File(base(), text(intent, "model", "model")))) {
            result.put("modelLoadMs", SystemClock.elapsedRealtime() - loadStarted)
                    .put("memoryLoaded", memory())
                    .put("gated", gated);
            engine.start(commands == null ? null : commands.grammar());
            // A file is read before the clock starts; a microphone's sound needs no reading.
            short[] sound = input.isEmpty() ? null : Wav.read(new File(base(), input)).mono16k();
            JSONArray events = new JSONArray();
            long started = SystemClock.elapsedRealtime();
            Listener listener = new Listener(engine, gated, sentence -> {
                JSONObject event = describe(sentence, commands, confidence)
                        .put("atMs", SystemClock.elapsedRealtime() - started);
                Commands.Request request = commands == null || event.isNull("command")
                        ? null
                        : commands.requestFor(event.getString("command"));
                if (client != null && request != null) {
                    try {
                        event.put("mirrorAnswered", client.send(request));
                    } catch (IOException error) {
                        event.put("mirrorError", error.toString());
                    }
                }
                Log.i(TAG, "heard " + event);
                events.put(event);
            });
            long cpu = Process.getElapsedCpuTime();
            if (sound == null) {
                String save = text(intent, "save", "");
                File kept = save.isEmpty() ? null : new File(folder("recordings"), save + ".wav");
                result.put("behindMs", microphone(intent, listener, seconds, kept));
                if (kept != null) {
                    result.put("file", "recordings/" + kept.getName());
                }
            } else {
                result.put("behindMs", feed(listener, sound, flag(intent, "realtime", true)));
            }
            listener.finish();
            long wall = SystemClock.elapsedRealtime() - started;
            cpu = Process.getElapsedCpuTime() - cpu;
            result.put("events", events)
                    .put("listenedSeconds", AudioStats.round(listener.secondsSeen()))
                    .put("wallMs", wall)
                    .put("cpuMs", cpu)
                    // The share of one processor core this took while listening.
                    .put("cpuShare", AudioStats.round(cpu / (double) Math.max(1, wall)))
                    .put("fedShare", AudioStats.round(listener.fedShare()))
                    .put("noiseFloorDb", gated ? AudioStats.round(listener.noiseFloorDb()) : JSONObject.NULL)
                    .put("memoryAfter", memory());
        }
    }

    /**
     * Hears the microphone for a time, keeping the sound in a file if one is given.
     *
     * @return the most milliseconds recognition fell behind the microphone
     */
    private long microphone(Intent intent, Listener listener, int seconds, File kept) throws Exception {
        String sourceName = text(intent, "source", "VOICE_RECOGNITION");
        Integer source = DeviceInfo.SOURCES.get(sourceName);
        if (source == null) {
            throw new IllegalArgumentException("Unknown source " + sourceName);
        }
        int rate = number(intent, "rate", Downsampler.TARGET_RATE);
        Downsampler downsampler = new Downsampler(rate);
        int minimum = AudioRecord.getMinBufferSize(
                rate, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT);
        if (minimum <= 0) {
            throw new IOException("This device does not offer " + rate + " Hz mono");
        }
        // Room for ten seconds of sound, so that a processor slower than speech
        // answers late rather than losing part of what was said.
        AudioRecord recorder = DeviceInfo.open(
                this, source, rate, AudioFormat.CHANNEL_IN_MONO,
                Math.max(minimum * 4, rate * 2 * MICROPHONE_BUFFER_SECONDS));
        Wav.Writer writer = null;
        long behind = 0;
        try {
            if (recorder.getState() != AudioRecord.STATE_INITIALIZED) {
                throw new IOException("The microphone could not be opened as " + sourceName);
            }
            if (kept != null) {
                writer = new Wav.Writer(kept, Downsampler.TARGET_RATE, 1);
            }
            recorder.startRecording();
            if (recorder.getRecordingState() != AudioRecord.RECORDSTATE_RECORDING) {
                throw new IOException("Recording did not start; something else may hold the microphone");
            }
            short[] chunk = new short[rate * CHUNK_MS / 1000];
            long begun = SystemClock.elapsedRealtime();
            long deadline = seconds <= 0 ? Long.MAX_VALUE : begun + seconds * 1000L;
            long nextReport = begun + 10_000;
            long cpu = Process.getElapsedCpuTime();
            long wall = begun;
            long samplesRead = 0;
            long startDelay = -1;
            while (!stopRequested && SystemClock.elapsedRealtime() < deadline) {
                int read = recorder.read(chunk, 0, chunk.length);
                if (read < 0) {
                    throw new IOException("Reading the microphone failed with code " + read);
                }
                samplesRead += read;
                // Sound that waited to be read arrives later than it was made.
                long late = SystemClock.elapsedRealtime() - begun - samplesRead * 1000 / rate;
                if (startDelay < 0) {
                    startDelay = late;
                }
                behind = Math.max(behind, late - startDelay);
                int count = downsampler.process(chunk, read);
                if (writer != null) {
                    writer.write(chunk, count);
                }
                listener.feed(chunk, count);
                if (SystemClock.elapsedRealtime() >= nextReport) {
                    long now = SystemClock.elapsedRealtime();
                    long used = Process.getElapsedCpuTime();
                    Log.i(TAG, "listening: cpuShare "
                            + AudioStats.round((used - cpu) / (double) Math.max(1, now - wall))
                            + " fedShare " + AudioStats.round(listener.fedShare())
                            + " noiseFloorDb " + AudioStats.round(listener.noiseFloorDb())
                            + " behindMs " + behind);
                    cpu = used;
                    wall = now;
                    nextReport = now + 10_000;
                }
            }
        } finally {
            if (recorder.getRecordingState() == AudioRecord.RECORDSTATE_RECORDING) {
                recorder.stop();
            }
            recorder.release();
            if (writer != null) {
                writer.close();
            }
        }
        return behind;
    }

    // ---------------------------------------------------------------------
    // shared
    // ---------------------------------------------------------------------

    private Commands commands(Intent intent) throws IOException {
        String file = text(intent, "commands", "");
        return file.isEmpty() ? null : Commands.parse(read(new File(base(), file)));
    }

    private JSONObject memory() throws JSONException {
        Debug.MemoryInfo process = new Debug.MemoryInfo();
        Debug.getMemoryInfo(process);
        ActivityManager.MemoryInfo system = new ActivityManager.MemoryInfo();
        ((ActivityManager) getSystemService(Context.ACTIVITY_SERVICE)).getMemoryInfo(system);
        return new JSONObject()
                .put("pssKb", process.getTotalPss())
                .put("nativeHeapKb", Debug.getNativeHeapAllocatedSize() / 1024)
                .put("systemAvailableKb", system.availMem / 1024)
                .put("systemLow", system.lowMemory);
    }

    private File base() throws IOException {
        File base = getExternalFilesDir(null);
        if (base == null) {
            throw new IOException("External storage is not available");
        }
        return base;
    }

    private File folder(String name) throws IOException {
        File folder = new File(base(), name);
        if (!folder.isDirectory() && !folder.mkdirs()) {
            throw new IOException("Unable to create " + folder);
        }
        return folder;
    }

    private void write(String name, JSONObject result) {
        try {
            File target = new File(folder("results"), name + ".json");
            File partial = new File(target.getPath() + ".part");
            try (FileOutputStream output = new FileOutputStream(partial)) {
                output.write(result.toString(1).getBytes(StandardCharsets.UTF_8));
            }
            // Appears under its name only once it is complete.
            if (!partial.renameTo(target)) {
                throw new IOException("Unable to rename " + partial);
            }
        } catch (IOException | JSONException error) {
            Log.e(TAG, "Unable to write the result " + name, error);
        }
    }

    private static String read(File file) throws IOException {
        byte[] bytes = new byte[(int) file.length()];
        try (FileInputStream input = new FileInputStream(file)) {
            int read = 0;
            while (read < bytes.length) {
                int count = input.read(bytes, read, bytes.length - read);
                if (count < 0) {
                    break;
                }
                read += count;
            }
            return new String(bytes, 0, read, StandardCharsets.UTF_8);
        }
    }

    private static String text(Intent intent, String key, String fallback) {
        String value = intent.getStringExtra(key);
        return value == null ? fallback : value;
    }

    private static int number(Intent intent, String key, int fallback) {
        String value = intent.getStringExtra(key);
        return value == null ? fallback : Integer.parseInt(value.trim());
    }

    private static boolean flag(Intent intent, String key, boolean fallback) {
        String value = intent.getStringExtra(key);
        return value == null ? fallback : value.trim().equals("1") || value.trim().equalsIgnoreCase("true");
    }
}
