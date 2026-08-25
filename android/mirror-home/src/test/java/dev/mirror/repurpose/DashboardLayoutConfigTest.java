package dev.mirror.repurpose;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
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
        assertEquals(15, value.getJSONArray("widgets").length());
        assertEquals(0, value.getJSONObject("background").getInt("dim"));
        assertEquals(15, DashboardLayoutConfig.parse(value).toJson()
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
        assertEquals(15, migrated.getJSONArray("widgets").length());
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

        assertEquals(16, parsed.getJSONArray("widgets").length());
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

        assertEquals(15, normalized.getJSONArray("widgets").length());
        assertTrue(!find(normalized, "weather").getBoolean("visible"));
        assertTrue(!find(normalized, "date").getBoolean("visible"));
    }

    @Test
    public void dropsRetiredBluetoothWidgetFromSavedVersionTwoLayouts() throws JSONException {
        JSONObject value = DashboardLayoutConfig.defaults().toJson();
        value.getJSONArray("widgets").getJSONObject(0).put("x", 120);
        value.getJSONArray("widgets").put(new JSONObject()
                .put("id", "ble")
                .put("type", "ble")
                .put("x", 50)
                .put("y", 950)
                .put("w", 180)
                .put("h", 32)
                .put("visible", true)
                .put("opacity", 48)
                .put("align", "start")
                .put("text", "")
                .put("locked", false)
                .put("layer", 25));

        JSONObject parsed = DashboardLayoutConfig.parse(value).toJson();

        assertEquals(15, parsed.getJSONArray("widgets").length());
        assertEquals(120, find(parsed, "clock").getInt("x"));
        assertFalse(contains(parsed, "ble"));
    }

    @Test
    public void dropsRetiredBluetoothWidgetFromSavedVersionOneLayouts() throws JSONException {
        JSONObject value = DashboardLayoutConfig.defaults().toJson();
        value.put("version", 1);
        value.getJSONArray("widgets").put(new JSONObject()
                .put("id", "ble")
                .put("x", 50)
                .put("y", 950)
                .put("w", 180)
                .put("h", 32)
                .put("visible", true));

        JSONObject migrated = DashboardLayoutConfig.parse(value).toJson();

        assertEquals(2, migrated.getInt("version"));
        assertEquals(15, migrated.getJSONArray("widgets").length());
        assertFalse(contains(migrated, "ble"));
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

    @Test
    public void defaultNoteShowsTheNewestNoteWithDefaultTypography() throws JSONException {
        JSONObject note = find(DashboardLayoutConfig.defaults().toJson(), "note");
        assertEquals("latest", note.getString("source"));
        assertEquals("", note.getString("note"));
        assertEquals("auto", note.getString("size"));
        assertEquals("light", note.getString("weight"));
        assertTrue(!find(DashboardLayoutConfig.defaults().toJson(), "clock").has("source"));
    }

    @Test
    public void savedNotesWithoutSourceKeepShowingTheirOwnText() throws JSONException {
        JSONObject value = DashboardLayoutConfig.defaults().toJson();
        JSONObject note = find(value, "note");
        note.remove("source");
        note.remove("size");
        note.remove("weight");
        note.put("text", "Line one\nLine two");

        JSONObject parsed = find(DashboardLayoutConfig.parse(value).toJson(), "note");

        assertEquals("text", parsed.getString("source"));
        assertEquals("Line one\nLine two", parsed.getString("text"));
        assertEquals("auto", parsed.getString("size"));
        assertEquals("light", parsed.getString("weight"));
    }

    @Test
    public void restoredDefaultNoteInheritsTheDefaultSource() throws JSONException {
        JSONObject value = DashboardLayoutConfig.defaults().toJson();
        org.json.JSONArray widgets = value.getJSONArray("widgets");
        for (int index = widgets.length() - 1; index >= 0; index--) {
            if ("note".equals(widgets.getJSONObject(index).getString("id"))) {
                widgets.remove(index);
            }
        }

        JSONObject restored = find(DashboardLayoutConfig.parse(value).toJson(), "note");

        assertEquals("latest", restored.getString("source"));
        assertFalse(restored.getBoolean("visible"));
    }

    @Test
    public void noteWidgetsKeepValidatedSourceAndTypography() throws JSONException {
        JSONObject value = DashboardLayoutConfig.defaults().toJson();
        find(value, "note")
                .put("source", "pinned")
                .put("note", "abc123")
                .put("size", "large")
                .put("weight", "thin");
        StringBuilder text = new StringBuilder();
        for (int index = 0; index < NoteBook.MAX_TEXT_LENGTH; index++) {
            text.append('n');
        }
        find(value, "note").put("text", text.toString());

        JSONObject parsed = find(DashboardLayoutConfig.parse(value).toJson(), "note");

        assertEquals("pinned", parsed.getString("source"));
        assertEquals("abc123", parsed.getString("note"));
        assertEquals("large", parsed.getString("size"));
        assertEquals("thin", parsed.getString("weight"));
        assertEquals(NoteBook.MAX_TEXT_LENGTH, parsed.getString("text").length());
    }

    @Test(expected = JSONException.class)
    public void rejectsOversizedNoteText() throws JSONException {
        JSONObject value = DashboardLayoutConfig.defaults().toJson();
        StringBuilder text = new StringBuilder();
        for (int index = 0; index <= NoteBook.MAX_TEXT_LENGTH; index++) {
            text.append('n');
        }
        find(value, "note").put("text", text.toString());
        DashboardLayoutConfig.parse(value);
    }

    @Test(expected = JSONException.class)
    public void keepsTheShortTextCapForOtherWidgets() throws JSONException {
        JSONObject value = DashboardLayoutConfig.defaults().toJson();
        StringBuilder text = new StringBuilder();
        for (int index = 0; index < 121; index++) {
            text.append('c');
        }
        find(value, "clock").put("text", text.toString());
        DashboardLayoutConfig.parse(value);
    }

    @Test(expected = JSONException.class)
    public void rejectsUnknownNoteSource() throws JSONException {
        JSONObject value = DashboardLayoutConfig.defaults().toJson();
        find(value, "note").put("source", "rss");
        DashboardLayoutConfig.parse(value);
    }

    @Test(expected = JSONException.class)
    public void rejectsMalformedNoteReference() throws JSONException {
        JSONObject value = DashboardLayoutConfig.defaults().toJson();
        find(value, "note").put("source", "pinned").put("note", "../x");
        DashboardLayoutConfig.parse(value);
    }

    @Test(expected = JSONException.class)
    public void rejectsUnknownNoteWeight() throws JSONException {
        JSONObject value = DashboardLayoutConfig.defaults().toJson();
        find(value, "note").put("weight", "bold");
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

    private static boolean contains(JSONObject layout, String id) throws JSONException {
        for (int index = 0; index < layout.getJSONArray("widgets").length(); index++) {
            if (id.equals(layout.getJSONArray("widgets").getJSONObject(index).getString("id"))) {
                return true;
            }
        }
        return false;
    }
}
