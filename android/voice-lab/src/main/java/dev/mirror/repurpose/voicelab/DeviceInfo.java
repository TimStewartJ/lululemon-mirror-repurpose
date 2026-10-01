package dev.mirror.repurpose.voicelab;

import android.Manifest;
import android.app.ActivityManager;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.content.pm.ResolveInfo;
import android.media.AudioDeviceInfo;
import android.media.AudioFormat;
import android.media.AudioManager;
import android.media.AudioRecord;
import android.media.MediaRecorder;
import android.media.audiofx.AcousticEchoCanceler;
import android.media.audiofx.AutomaticGainControl;
import android.media.audiofx.NoiseSuppressor;
import android.os.Build;
import android.speech.RecognitionService;
import android.speech.SpeechRecognizer;
import android.speech.tts.TextToSpeech;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.File;
import java.io.FileReader;
import java.io.IOException;
import java.util.LinkedHashMap;
import java.util.Map;

/** What this device offers for hearing and speaking, without recording anything. */
final class DeviceInfo {
    static final Map<String, Integer> SOURCES = new LinkedHashMap<>();

    static {
        SOURCES.put("DEFAULT", MediaRecorder.AudioSource.DEFAULT);
        SOURCES.put("MIC", MediaRecorder.AudioSource.MIC);
        SOURCES.put("VOICE_RECOGNITION", MediaRecorder.AudioSource.VOICE_RECOGNITION);
        SOURCES.put("VOICE_COMMUNICATION", MediaRecorder.AudioSource.VOICE_COMMUNICATION);
        SOURCES.put("CAMCORDER", MediaRecorder.AudioSource.CAMCORDER);
    }

    private static final int[] RATES = {16_000, 48_000, 44_100, 8_000};

    private DeviceInfo() {
    }

    static boolean mayRecord(Context context) {
        return context.checkSelfPermission(Manifest.permission.RECORD_AUDIO)
                == PackageManager.PERMISSION_GRANTED;
    }

    /**
     * Opens the microphone for 16-bit sound. Nobody can answer a permission
     * dialog on a Mirror, so the permission is granted at installation
     * ("adb install -g") or not at all.
     */
    static AudioRecord open(Context context, int source, int rate, int mask, int bufferBytes) {
        if (context.checkSelfPermission(Manifest.permission.RECORD_AUDIO)
                != PackageManager.PERMISSION_GRANTED) {
            throw new SecurityException("The microphone permission has not been granted");
        }
        return new AudioRecord(source, rate, mask, AudioFormat.ENCODING_PCM_16BIT, bufferBytes);
    }

    static JSONObject collect(Context context) throws JSONException {
        PackageManager packages = context.getPackageManager();
        AudioManager audio = (AudioManager) context.getSystemService(Context.AUDIO_SERVICE);
        ActivityManager activity = (ActivityManager) context.getSystemService(Context.ACTIVITY_SERVICE);
        ActivityManager.MemoryInfo memory = new ActivityManager.MemoryInfo();
        activity.getMemoryInfo(memory);

        JSONObject result = new JSONObject()
                .put("fingerprint", Build.FINGERPRINT)
                .put("sdk", Build.VERSION.SDK_INT)
                .put("abis", new JSONArray(Build.SUPPORTED_ABIS))
                .put("hardware", Build.HARDWARE)
                .put("processors", Runtime.getRuntime().availableProcessors())
                .put("cpuMaxKhz", firstLine("/sys/devices/system/cpu/cpu0/cpufreq/cpuinfo_max_freq"))
                .put("cpuHardware", cpuField("Hardware"))
                // 0xd03 is a Cortex-A53.
                .put("cpuPart", cpuField("CPU part"))
                .put("cpuFeatures", cpuField("Features").isEmpty() ? cpuField("flags") : cpuField("Features"))
                .put("memoryTotalMb", memory.totalMem / (1024 * 1024))
                .put("memoryAvailableMb", memory.availMem / (1024 * 1024))
                .put("memoryLowThresholdMb", memory.threshold / (1024 * 1024))
                .put("javaHeapMaxMb", Runtime.getRuntime().maxMemory() / (1024 * 1024))
                .put("microphoneFeature", packages.hasSystemFeature(PackageManager.FEATURE_MICROPHONE))
                .put("mayRecord", mayRecord(context))
                .put("outputSampleRate", audio.getProperty(AudioManager.PROPERTY_OUTPUT_SAMPLE_RATE))
                .put("mediaVolume", audio.getStreamVolume(AudioManager.STREAM_MUSIC))
                .put("mediaVolumeMax", audio.getStreamMaxVolume(AudioManager.STREAM_MUSIC))
                .put("echoCanceler", AcousticEchoCanceler.isAvailable())
                .put("noiseSuppressor", NoiseSuppressor.isAvailable())
                .put("gainControl", AutomaticGainControl.isAvailable())
                .put("builtInRecognition", SpeechRecognizer.isRecognitionAvailable(context))
                .put("recognitionServices", services(packages, RecognitionService.SERVICE_INTERFACE))
                .put("speechEngines", services(packages, TextToSpeech.Engine.INTENT_ACTION_TTS_SERVICE));

        JSONArray inputs = new JSONArray();
        for (AudioDeviceInfo device : audio.getDevices(AudioManager.GET_DEVICES_INPUTS)) {
            inputs.put(new JSONObject()
                    .put("type", device.getType())
                    .put("name", String.valueOf(device.getProductName()))
                    .put("channelCounts", new JSONArray(device.getChannelCounts()))
                    .put("sampleRates", new JSONArray(device.getSampleRates())));
        }
        result.put("inputDevices", inputs);

        // Whether each way of asking for the microphone can be opened at all.
        JSONArray configurations = new JSONArray();
        for (Map.Entry<String, Integer> source : SOURCES.entrySet()) {
            for (int rate : RATES) {
                for (int channels = 1; channels <= 2; channels++) {
                    int mask = channels == 1
                            ? AudioFormat.CHANNEL_IN_MONO
                            : AudioFormat.CHANNEL_IN_STEREO;
                    int minimum = AudioRecord.getMinBufferSize(rate, mask, AudioFormat.ENCODING_PCM_16BIT);
                    JSONObject entry = new JSONObject()
                            .put("source", source.getKey())
                            .put("rate", rate)
                            .put("channels", channels)
                            .put("minBufferBytes", minimum)
                            .put("opens", false);
                    if (minimum > 0 && mayRecord(context)) {
                        AudioRecord record = null;
                        try {
                            record = open(context, source.getValue(), rate, mask, minimum * 2);
                            entry.put("opens", record.getState() == AudioRecord.STATE_INITIALIZED);
                        } catch (RuntimeException error) {
                            entry.put("error", error.toString());
                        } finally {
                            if (record != null) {
                                record.release();
                            }
                        }
                    }
                    configurations.put(entry);
                }
            }
        }
        return result.put("configurations", configurations);
    }

    private static JSONArray services(PackageManager packages, String action) {
        JSONArray names = new JSONArray();
        for (ResolveInfo info : packages.queryIntentServices(new Intent(action), 0)) {
            names.put(info.serviceInfo.packageName + "/" + info.serviceInfo.name);
        }
        return names;
    }

    private static String firstLine(String path) {
        try (BufferedReader reader = new BufferedReader(new FileReader(new File(path)))) {
            String line = reader.readLine();
            return line == null ? "" : line.trim();
        } catch (IOException unavailable) {
            return "";
        }
    }

    private static String cpuField(String name) {
        try (BufferedReader reader = new BufferedReader(new FileReader("/proc/cpuinfo"))) {
            String line;
            while ((line = reader.readLine()) != null) {
                if (line.toLowerCase(java.util.Locale.ROOT).startsWith(name.toLowerCase(java.util.Locale.ROOT))) {
                    int colon = line.indexOf(':');
                    return colon < 0 ? line.trim() : line.substring(colon + 1).trim();
                }
            }
        } catch (IOException unavailable) {
            // Reported as unknown below.
        }
        return "";
    }
}
