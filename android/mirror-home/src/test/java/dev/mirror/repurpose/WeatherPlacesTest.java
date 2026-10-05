package dev.mirror.repurpose;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.json.JSONArray;
import org.json.JSONObject;
import org.junit.Test;

import java.util.ArrayList;
import java.util.List;

public class WeatherPlacesTest {
    /** Towns as the service lists them for "Springfield" and for "Portland", best known first. */
    private static JSONArray towns() throws Exception {
        return new JSONArray()
                .put(town("Springfield", "Missouri", "Greene", "United States", "US", 37.21533, -93.29824, "America/Chicago"))
                .put(town("Springfield", "Illinois", "Sangamon", "United States", "US", 39.80172, -89.64371, "America/Chicago"))
                .put(town("Springfield", "Massachusetts", "Hampden", "United States", "US", 42.10148, -72.58981, "America/New_York"))
                .put(town("Springfield", "Ohio", "Clark", "United States", "US", 39.92423, -83.80882, "America/New_York"))
                .put(town("Springfield", "Tennessee", "Robertson", "United States", "US", 36.50921, -86.885, "America/Chicago"))
                .put(town("Springfield", "Minnesota", "Brown", "United States", "US", 44.23885, -94.97582, "America/Chicago"))
                .put(town("Springfield", "Oregon", "Lane", "United States", "US", 44.04624, -123.02203, "America/Los_Angeles"))
                .put(town("Springfield", "New Jersey", "Union", "United States", "US", 40.70491, -74.31723, "America/New_York"))
                .put(town("Springfield", "Ontario", "Elgin", "Canada", "CA", 42.83339, -80.93305, "America/Toronto"));
    }

    private static JSONObject town(
            String name, String admin1, String admin2, String country, String code,
            double latitude, double longitude, String zone) throws Exception {
        return new JSONObject()
                .put("name", name).put("admin1", admin1).put("admin2", admin2)
                .put("country", country).put("country_code", code)
                .put("latitude", latitude).put("longitude", longitude).put("timezone", zone);
    }

    private static List<String> read(String query) {
        List<String> seen = new ArrayList<>();
        for (WeatherPlaces.Reading reading : WeatherPlaces.readings(query)) {
            seen.add(reading.name + "|" + reading.region + "|" + reading.count());
        }
        return seen;
    }

    private static List<String> labels(JSONArray results) throws Exception {
        List<String> labels = new ArrayList<>();
        for (int index = 0; index < results.length(); index++) {
            labels.add(results.getJSONObject(index).getString("label"));
        }
        return labels;
    }

    @Test
    public void aNameAloneIsSearchedAsItStands() {
        assertEquals("[Seattle||5]", read("Seattle").toString());
        assertEquals("[Seattle||5]", read("  Seattle  ").toString());
    }

    @Test
    public void severalWordsAreFirstANameAndThenANameWithItsRegion() {
        // "New York" is a town; only if nothing is called that is "York" taken for where "New" lies.
        assertEquals("[New York||5, New|York|50]", read("New York").toString());
        assertEquals("[Portland Maine||5, Portland|Maine|50]", read("Portland   Maine").toString());
        assertEquals(
                "[San Jose Costa Rica||5, San Jose Costa|Rica|50, San Jose|Costa Rica|50, San|Jose Costa Rica|50]",
                read("San Jose Costa Rica").toString());
        // A name of one letter is no name.
        assertEquals("[A Coruna Spain||5, A Coruna|Spain|50]", read("A Coruna Spain").toString());
    }

    @Test
    public void aCommaSaysWhereTheNameEnds() {
        assertEquals("[Portland|Maine|50]", read("Portland, Maine").toString());
        assertEquals("[Portland|ME|50]", read("Portland,ME").toString());
        assertEquals("[Springfield|Oregon USA|50]", read("Springfield, Oregon, USA").toString());
        assertEquals("[San Jose|Costa Rica|50]", read("San Jose, Costa Rica").toString());
        // A comma with nothing on one side of it is a slip of the finger.
        assertEquals("[Seattle||5]", read("Seattle,").toString());
        assertEquals("[Seattle||5]", read(", Seattle").toString());
    }

    @Test
    public void withoutARegionTheBestKnownFiveAreKept() throws Exception {
        JSONArray results = WeatherPlaces.pick(towns(), "");
        assertEquals(
                "[Springfield, Missouri, United States, Springfield, Illinois, United States, "
                        + "Springfield, Massachusetts, United States, Springfield, Ohio, United States, "
                        + "Springfield, Tennessee, United States]",
                labels(results).toString());
        JSONObject first = results.getJSONObject(0);
        assertEquals(37.21533, first.getDouble("latitude"), 1e-9);
        assertEquals(-93.29824, first.getDouble("longitude"), 1e-9);
        assertEquals("America/Chicago", first.getString("timezone"));
        assertEquals(0, WeatherPlaces.pick(null, "").length());
        assertEquals(0, WeatherPlaces.pick(new JSONArray(), "Oregon").length());
    }

    @Test
    public void aRegionPicksTheTownThatLiesInIt() throws Exception {
        assertEquals("[Springfield, Oregon, United States]", labels(WeatherPlaces.pick(towns(), "Oregon")).toString());
        assertEquals("[Springfield, Oregon, United States]", labels(WeatherPlaces.pick(towns(), "OR")).toString());
        assertEquals("[Springfield, Oregon, United States]", labels(WeatherPlaces.pick(towns(), "oregon usa")).toString());
        assertEquals("[Springfield, New Jersey, United States]", labels(WeatherPlaces.pick(towns(), "N.J.")).toString());
        assertEquals("[Springfield, Ontario, Canada]", labels(WeatherPlaces.pick(towns(), "Canada")).toString());
        assertEquals("[Springfield, Ontario, Canada]", labels(WeatherPlaces.pick(towns(), "ON")).toString());
        // A county is a region too.
        assertEquals("[Springfield, Oregon, United States]", labels(WeatherPlaces.pick(towns(), "Lane")).toString());
        // A country keeps the best known five of it.
        assertEquals(5, WeatherPlaces.pick(towns(), "United States").length());
        assertEquals(5, WeatherPlaces.pick(towns(), "US").length());
        assertEquals(0, WeatherPlaces.pick(towns(), "France").length());
    }

    @Test
    public void partOfARegionsNameIsNotThatRegion() throws Exception {
        JSONObject newJersey = towns().getJSONObject(7);
        assertTrue(WeatherPlaces.lies(newJersey, "New Jersey"));
        assertTrue(WeatherPlaces.lies(newJersey, "new jersey, usa"));
        assertFalse(WeatherPlaces.lies(newJersey, "Jersey"));
        assertFalse(WeatherPlaces.lies(newJersey, "New"));
        assertFalse(WeatherPlaces.lies(newJersey, "New York"));
        // The capital's own way of writing where it lies.
        JSONObject capital = town("Washington D.C.", "District of Columbia", "", "United States", "US", 38.9, -77.04, "America/New_York");
        assertTrue(WeatherPlaces.lies(capital, "D.C."));
        assertTrue(WeatherPlaces.lies(capital, "DC"));
        assertFalse(WeatherPlaces.lies(capital, "Washington"));
    }

    @Test
    public void whatIsNoPlaceIsLeftOut() throws Exception {
        JSONArray found = new JSONArray()
                .put("not an object")
                .put(new JSONObject().put("name", "Nowhere").put("latitude", 95).put("longitude", 10))
                .put(new JSONObject().put("name", "").put("latitude", 10).put("longitude", 10))
                .put(new JSONObject().put("name", "Halfway").put("latitude", 10))
                .put(new JSONObject().put("name", "Luxembourg").put("admin1", "Luxembourg").put("country", "Luxembourg")
                        .put("latitude", 49.61).put("longitude", 6.13));
        JSONArray results = WeatherPlaces.pick(found, "");
        // A town named as its region is not named twice.
        assertEquals("[Luxembourg, Luxembourg]", labels(results).toString());
        assertEquals("", results.getJSONObject(0).getString("timezone"));
    }
}
