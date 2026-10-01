package dev.mirror.repurpose.voicelab;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;

/**
 * A fixed list of things to say, each bound to one request to Mirror Home.
 * The recogniser is told to expect these sentences, which is what makes
 * recognition on a small device dependable and fast. It takes the list as a
 * small language of its own rather than as the only sentences possible:
 * other speech comes back as "[unk]", as parts of a command or as a mixture
 * of two, so only a sentence that is a command from its first word to its
 * last counts as one. File format, one command per line:
 *
 * <pre>mirror go to sleep | POST /api/v1/automation/sleep
 * mirror brighter | POST /api/v1/control/brightness {"value": 220}</pre>
 */
final class Commands {
    static final String UNKNOWN = "[unk]";

    static final class Request {
        final String method;
        final String path;
        final String body;

        Request(String method, String path, String body) {
            this.method = method;
            this.path = path;
            this.body = body;
        }
    }

    private final Map<String, Request> requests = new LinkedHashMap<>();

    static Commands parse(String text) {
        Commands commands = new Commands();
        for (String line : text.split("\\r?\\n")) {
            String trimmed = line.trim();
            if (trimmed.isEmpty() || trimmed.startsWith("#")) {
                continue;
            }
            String[] halves = trimmed.split("\\|", 2);
            String phrase = normalize(halves[0]);
            if (phrase.isEmpty()) {
                continue;
            }
            Request request = null;
            if (halves.length == 2 && !halves[1].trim().isEmpty()) {
                String[] parts = halves[1].trim().split("\\s+", 3);
                if (parts.length < 2) {
                    throw new IllegalArgumentException("A request needs a method and a path: " + trimmed);
                }
                request = new Request(parts[0], parts[1], parts.length == 3 ? parts[2] : "");
            }
            commands.requests.put(phrase, request);
        }
        return commands;
    }

    static String normalize(String phrase) {
        return phrase.trim().toLowerCase(Locale.ROOT).replaceAll("\\s+", " ");
    }

    List<String> phrases() {
        return new ArrayList<>(requests.keySet());
    }

    boolean knows(String phrase) {
        return requests.containsKey(normalize(phrase));
    }

    /** The request for what was heard, or null if it does nothing or is not a command. */
    Request requestFor(String heard) {
        return requests.get(normalize(heard));
    }

    /** What the recogniser is told to expect: every phrase, and "anything else". */
    String grammar() {
        JSONArray grammar = new JSONArray();
        for (String phrase : requests.keySet()) {
            grammar.put(phrase);
        }
        grammar.put(UNKNOWN);
        return grammar.toString();
    }

    /** The least certain word of a result, or NaN if the recogniser gave no words. */
    static double lowestConfidence(JSONObject result) throws JSONException {
        JSONArray words = result.optJSONArray("result");
        if (words == null || words.length() == 0) {
            return Double.NaN;
        }
        double lowest = 1.0;
        for (int index = 0; index < words.length(); index++) {
            lowest = Math.min(lowest, words.getJSONObject(index).optDouble("conf", 1.0));
        }
        return lowest;
    }

    /**
     * The command a recognition result amounts to, or null. Told to expect
     * only a few sentences, a recogniser fits other speech to the nearest
     * one, but with little confidence in its words; those are turned away.
     */
    String accepted(JSONObject result, double minimumConfidence) throws JSONException {
        String text = normalize(result.optString("text", ""));
        if (text.isEmpty() || text.contains(UNKNOWN) || !knows(text)) {
            return null;
        }
        double lowest = lowestConfidence(result);
        return !Double.isNaN(lowest) && lowest < minimumConfidence ? null : text;
    }
}
