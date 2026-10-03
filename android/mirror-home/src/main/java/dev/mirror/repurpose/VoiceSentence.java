package dev.mirror.repurpose;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

/** One sentence as the recogniser reports it, with what the Mirror needs of it. */
final class VoiceSentence {
    final String text;
    /** The recogniser's confidence in its least certain word; NaN if it named none. */
    final double lowestConfidence;
    /**
     * The recogniser's confidence in the Mirror's name where the sentence
     * begins with it; NaN if it does not, or if no confidence was given.
     * Free speech after the name is made of words the recogniser is unsure
     * of by nature, so whether the Mirror was addressed is judged by its
     * name alone.
     */
    final double nameConfidence;
    /** Milliseconds from the recogniser's start to the first and after the last word. */
    final long startMs;
    final long endMs;

    VoiceSentence(String text, double lowestConfidence, long startMs, long endMs) {
        this(text, lowestConfidence, Double.NaN, startMs, endMs);
    }

    VoiceSentence(String text, double lowestConfidence, double nameConfidence, long startMs, long endMs) {
        this.text = text;
        this.lowestConfidence = lowestConfidence;
        this.nameConfidence = nameConfidence;
        this.startMs = startMs;
        this.endMs = endMs;
    }

    /**
     * Reads the recogniser's JSON, for example
     * {@code {"result": [{"word": "mirror", "conf": 1, "start": 0.5, "end": 0.9}], "text": "mirror"}}.
     *
     * @return the sentence, or null if nothing was said
     */
    static VoiceSentence parse(String json) throws JSONException {
        JSONObject result = new JSONObject(json);
        String text = VoiceCommands.normalize(result.optString("text", ""));
        if (text.isEmpty()) {
            return null;
        }
        JSONArray words = result.optJSONArray("result");
        if (words == null || words.length() == 0) {
            return new VoiceSentence(text, Double.NaN, 0, 0);
        }
        double lowest = Double.MAX_VALUE;
        double name = Double.NaN;
        boolean leading = true;
        for (int index = 0; index < words.length(); index++) {
            JSONObject word = words.getJSONObject(index);
            double confidence = word.optDouble("conf", 1.0);
            lowest = Math.min(lowest, confidence);
            leading = leading && VoiceCommands.WAKE_WORD.equals(word.optString("word"));
            if (leading) {
                name = Double.isNaN(name) ? confidence : Math.min(name, confidence);
            }
        }
        long start = Math.round(words.getJSONObject(0).optDouble("start", 0) * 1000);
        long end = Math.round(words.getJSONObject(words.length() - 1).optDouble("end", 0) * 1000);
        return new VoiceSentence(text, lowest, name, start, Math.max(start, end));
    }
}
