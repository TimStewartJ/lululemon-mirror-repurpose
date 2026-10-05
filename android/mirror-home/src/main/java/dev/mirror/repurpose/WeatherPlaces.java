package dev.mirror.repurpose;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;

/**
 * Finds the place someone means when they name a town for the weather.
 *
 * <p>The service that knows places searches by a town's name alone, and
 * answers with the best known of that name first. "Springfield" is five
 * towns before it is the one in Oregon, and "Portland, Maine" is no town's
 * name at all. People say and type a town with its state or country, so a
 * search that finds nothing as it stands is tried again as a name and a
 * region: more towns of that name are asked for, and those in the region
 * are kept.
 */
final class WeatherPlaces {
    /** How many places an answer holds at most. */
    static final int LIMIT = 5;
    /** How many towns of one name are asked for when a region is to pick among them. */
    static final int WIDE = 50;

    /** One way of reading what was typed: a town's name, and the region it should lie in, or none. */
    static final class Reading {
        final String name;
        /** Empty when the words are taken as a name alone. */
        final String region;

        Reading(String name, String region) {
            this.name = name;
            this.region = region;
        }

        /** How many places to ask the service for. */
        int count() {
            return region.isEmpty() ? LIMIT : WIDE;
        }
    }

    /** What people write for a state, a province or a country, and what the service calls it. */
    private static final Map<String, String> SHORT = new HashMap<>();

    static {
        String[] pairs = {
                "al", "alabama", "ak", "alaska", "az", "arizona", "ar", "arkansas", "ca", "california",
                "co", "colorado", "ct", "connecticut", "de", "delaware", "dc", "district of columbia",
                "fl", "florida", "ga", "georgia", "hi", "hawaii", "id", "idaho", "il", "illinois",
                "in", "indiana", "ia", "iowa", "ks", "kansas", "ky", "kentucky", "la", "louisiana",
                "me", "maine", "md", "maryland", "ma", "massachusetts", "mi", "michigan", "mn", "minnesota",
                "ms", "mississippi", "mo", "missouri", "mt", "montana", "ne", "nebraska", "nv", "nevada",
                "nh", "new hampshire", "nj", "new jersey", "nm", "new mexico", "ny", "new york",
                "nc", "north carolina", "nd", "north dakota", "oh", "ohio", "ok", "oklahoma", "or", "oregon",
                "pa", "pennsylvania", "ri", "rhode island", "sc", "south carolina", "sd", "south dakota",
                "tn", "tennessee", "tx", "texas", "ut", "utah", "vt", "vermont", "va", "virginia",
                "wa", "washington", "wv", "west virginia", "wi", "wisconsin", "wy", "wyoming",
                "ab", "alberta", "bc", "british columbia", "mb", "manitoba", "nb", "new brunswick",
                "nl", "newfoundland and labrador", "ns", "nova scotia", "nt", "northwest territories",
                "nu", "nunavut", "on", "ontario", "pe", "prince edward island", "qc", "quebec",
                "sk", "saskatchewan", "yt", "yukon",
                "usa", "united states", "us", "united states", "uk", "united kingdom",
        };
        for (int index = 0; index < pairs.length; index += 2) {
            SHORT.put(pairs[index], pairs[index + 1]);
        }
    }

    private WeatherPlaces() {
    }

    /**
     * The ways to read what was typed, in the order to try them: as it
     * stands unless it has a comma, and then with its last words as a
     * region.
     */
    static List<Reading> readings(String query) {
        List<Reading> readings = new ArrayList<>();
        String text = query.trim().replaceAll("\\s+", " ");
        int comma = text.indexOf(',');
        if (comma >= 0) {
            String name = text.substring(0, comma).trim();
            String region = text.substring(comma + 1).replace(',', ' ').trim().replaceAll("\\s+", " ");
            if (name.length() >= 2 && !region.isEmpty()) {
                readings.add(new Reading(name, region));
                return readings;
            }
            text = text.replace(',', ' ').trim().replaceAll("\\s+", " ");
        }
        readings.add(new Reading(text, ""));
        String[] words = text.split(" ");
        // "Portland Maine", then "San Jose" in "Costa Rica": a region of one word, of two, of three.
        for (int regionWords = 1; regionWords <= 3 && regionWords < words.length; regionWords++) {
            String name = join(words, 0, words.length - regionWords);
            if (name.length() >= 2) {
                readings.add(new Reading(name, join(words, words.length - regionWords, words.length)));
            }
        }
        return readings;
    }

    /**
     * The places of an answer of the service that lie in the region, best
     * known first, as the control API gives them: a label, where it is, and
     * its time zone.
     *
     * @param found the service's "results"; null for none
     * @param region empty to keep them all
     */
    static JSONArray pick(JSONArray found, String region) throws JSONException {
        JSONArray results = new JSONArray();
        for (int index = 0; found != null && index < found.length() && results.length() < LIMIT; index++) {
            JSONObject item = found.optJSONObject(index);
            if (item == null || !lies(item, region)) {
                continue;
            }
            double latitude = item.optDouble("latitude", Double.NaN);
            double longitude = item.optDouble("longitude", Double.NaN);
            String name = item.optString("name", "").trim();
            if (Double.isNaN(latitude) || Double.isNaN(longitude)
                    || latitude < -90d || latitude > 90d || longitude < -180d || longitude > 180d
                    || name.isEmpty()) {
                continue;
            }
            String admin = item.optString("admin1", "").trim();
            String country = item.optString("country", "").trim();
            StringBuilder label = new StringBuilder(name);
            if (!admin.isEmpty() && !admin.equals(name)) {
                label.append(", ").append(admin);
            }
            if (!country.isEmpty()) {
                label.append(", ").append(country);
            }
            results.put(new JSONObject()
                    .put("label", label.length() > 80 ? label.substring(0, 80) : label.toString())
                    .put("latitude", latitude)
                    .put("longitude", longitude)
                    .put("timezone", item.optString("timezone", "")));
        }
        return results;
    }

    /** Whether a place of the service lies in what someone wrote for its state, province or country. */
    static boolean lies(JSONObject place, String region) {
        String wanted = plain(region);
        if (wanted.isEmpty()) {
            return true;
        }
        String code = place.optString("country_code", "").toLowerCase(Locale.US);
        String[] parts = {
                plain(place.optString("admin1", "")),
                plain(place.optString("admin2", "")),
                plain(place.optString("country", "")),
        };
        String spelled = SHORT.get(wanted);
        for (String part : parts) {
            if (part.isEmpty()) {
                continue;
            }
            // "Jersey" is not New Jersey, and "York" is not New York: the whole of it, as written or spelled out.
            if (part.equals(wanted) || part.equals(spelled)) {
                return true;
            }
        }
        if (wanted.equals(code)) {
            return true;
        }
        // A state and its country together: "Maine USA", "Ontario Canada".
        String[] words = wanted.split(" ");
        for (int split = 1; split < words.length; split++) {
            if (lies(place, join(words, 0, split)) && lies(place, join(words, split, words.length))) {
                return true;
            }
        }
        return false;
    }

    /** In lower case, without dots, and with single spaces: "D.C." is "dc". */
    private static String plain(String text) {
        return text.toLowerCase(Locale.US).replace(".", "").replaceAll("[^\\p{L}\\p{Nd}]+", " ").trim();
    }

    private static String join(String[] words, int from, int to) {
        StringBuilder joined = new StringBuilder();
        for (int index = from; index < to; index++) {
            joined.append(joined.length() == 0 ? "" : " ").append(words[index]);
        }
        return joined.toString();
    }
}
