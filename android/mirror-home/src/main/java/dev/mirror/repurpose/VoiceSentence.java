package dev.mirror.repurpose;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

/** One sentence as the recogniser reports it, with what the Mirror needs of it. */
final class VoiceSentence {
    final String text;
    /** The recogniser's confidence in its least certain word; NaN if it named none. */
    final double lowestConfidence;
    /** Milliseconds from the start of listening to the first and after the last word. */
    final long startMs;
    final long endMs;

    VoiceSentence(String text, double lowestConfidence, long startMs, long endMs) {
        this.text = text;
        this.lowestConfidence = lowestConfidence;
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
        for (int index = 0; index < words.length(); index++) {
            lowest = Math.min(lowest, words.getJSONObject(index).optDouble("conf", 1.0));
        }
        long start = Math.round(words.getJSONObject(0).optDouble("start", 0) * 1000);
        long end = Math.round(words.getJSONObject(words.length() - 1).optDouble("end", 0) * 1000);
        return new VoiceSentence(text, lowest, start, Math.max(start, end));
    }
}
