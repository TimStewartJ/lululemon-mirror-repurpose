package dev.mirror.repurpose;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;

import org.json.JSONException;
import org.json.JSONObject;
import org.junit.Test;

public final class DashboardLayoutConfigTest {
    @Test
    public void defaultsAreSubtleAndComplete() throws JSONException {
        JSONObject value = DashboardLayoutConfig.defaults().toJson();
        assertEquals(2, value.getInt("version"));
        assertEquals("solid", value.getJSONObject("background").getString("mode"));
        assertEquals("#000000", value.getJSONObject("background").getString("primary"));
        assertEquals(16, value.getJSONArray("widgets").length());
        assertEquals(0, value.getJSONObject("background").getInt("dim"));
        assertEquals(16, DashboardLayoutConfig.parse(value).toJson()
                .getJSONArray("widgets").length());
        assertTrue(value.getJSONArray("widgets").getJSONObject(3).getBoolean("visible"));
    }

    @Test(expected = JSONException.class)
    public void rejectsWidgetsOutsideCanvas() throws JSONException {
        JSONObject value = DashboardLayoutConfig.defaults().toJson();
        value.getJSONArray("widgets").getJSONObject(0).put("x", 900);
        value.getJSONArray("widgets").getJSONObject(0).put("w", 300);
        DashboardLayoutConfig.parse(value);
    }

    @Test(expected = JSONException.class)
    public void rejectsUnknownWidgets() throws JSONException {
        JSONObject value = DashboardLayoutConfig.defaults().toJson();
        value.getJSONArray("widgets")
                .put(new JSONObject().put("id", "shell").put("type", "shell"));
        DashboardLayoutConfig.parse(value);
    }

    @Test
    public void migratesVersionOneWithoutEnablingNewWeatherWidgets() throws JSONException {
        JSONObject value = DashboardLayoutConfig.defaults().toJson();
        value.put("version", 1);
        value.getJSONArray("widgets").remove(4);
        value.getJSONArray("widgets").remove(3);

        JSONObject migrated = DashboardLayoutConfig.parse(value).toJson();

        assertEquals(2, migrated.getInt("version"));
        assertEquals(16, migrated.getJSONArray("widgets").length());
        assertTrue(!find(migrated, "weather").getBoolean("visible"));
        assertTrue(!find(migrated, "forecast").getBoolean("visible"));
    }

    @Test
    public void supportsDuplicateWidgetTypesWithLocksAndLayers() throws JSONException {
        JSONObject value = DashboardLayoutConfig.defaults().toJson();
        JSONObject duplicate = new JSONObject(
                value.getJSONArray("widgets").getJSONObject(0).toString())
                .put("id", "clock-2")
                .put("locked", true)
                .put("layer", 42);
        value.getJSONArray("widgets").put(duplicate);

        JSONObject parsed = DashboardLayoutConfig.parse(value).toJson();

        assertEquals(17, parsed.getJSONArray("widgets").length());
        assertTrue(find(parsed, "clock-2").getBoolean("locked"));
        assertEquals(42, find(parsed, "clock-2").getInt("layer"));
    }

    @Test(expected = JSONException.class)
    public void rejectsDuplicateWidgetIds() throws JSONException {
        JSONObject value = DashboardLayoutConfig.defaults().toJson();
        value.getJSONArray("widgets").put(new JSONObject(
                value.getJSONArray("widgets").getJSONObject(0).toString()));
        DashboardLayoutConfig.parse(value);
    }

    @Test
    public void versionTwoRestoresMissingCanonicalWidgetsHidden() throws JSONException {
        JSONObject value = DashboardLayoutConfig.defaults().toJson();
        value.put("widgets", new org.json.JSONArray()
                .put(new JSONObject(value.getJSONArray("widgets")
                        .getJSONObject(0).toString())));

        JSONObject normalized = DashboardLayoutConfig.parse(value).toJson();

        assertEquals(16, normalized.getJSONArray("widgets").length());
        assertTrue(!find(normalized, "weather").getBoolean("visible"));
        assertTrue(!find(normalized, "date").getBoolean("visible"));
    }

    @Test(expected = JSONException.class)
    public void rejectsCanonicalWidgetTypeChanges() throws JSONException {
        JSONObject value = DashboardLayoutConfig.defaults().toJson();
        value.getJSONArray("widgets").getJSONObject(0).put("type", "note");
        DashboardLayoutConfig.parse(value);
    }

    @Test
    public void photoWidgetKeepsValidatedPhotoAndFit() throws JSONException {
        JSONObject value = DashboardLayoutConfig.defaults().toJson();
        JSONObject photo = find(value, "photo");
        assertTrue(!photo.getBoolean("visible"));
        assertEquals("", photo.getString("photo"));
        assertEquals("cover", photo.getString("fit"));
        photo.put("photo", "Family (2024).jpg").put("fit", "contain").put("visible", true);

        JSONObject parsed = DashboardLayoutConfig.parse(value).toJson();

        assertEquals("Family (2024).jpg", find(parsed, "photo").getString("photo"));
        assertEquals("contain", find(parsed, "photo").getString("fit"));
        assertTrue(!find(parsed, "clock").has("photo"));
    }

    @Test(expected = JSONException.class)
    public void rejectsPhotoWidgetPathTraversal() throws JSONException {
        JSONObject value = DashboardLayoutConfig.defaults().toJson();
        find(value, "photo").put("photo", "../secrets.jpg");
        DashboardLayoutConfig.parse(value);
    }

    @Test(expected = JSONException.class)
    public void rejectsUnknownPhotoFit() throws JSONException {
        JSONObject value = DashboardLayoutConfig.defaults().toJson();
        find(value, "photo").put("fit", "stretch");
        DashboardLayoutConfig.parse(value);
    }

    private static JSONObject find(JSONObject layout, String id) throws JSONException {
        for (int index = 0; index < layout.getJSONArray("widgets").length(); index++) {
            JSONObject widget = layout.getJSONArray("widgets").getJSONObject(index);
            if (id.equals(widget.getString("id"))) {
                return widget;
            }
        }
        throw new JSONException("Widget not found: " + id);
    }
}
