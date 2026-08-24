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
        assertEquals(1, value.getInt("version"));
        assertEquals("solid", value.getJSONObject("background").getString("mode"));
        assertEquals("#000000", value.getJSONObject("background").getString("primary"));
        assertEquals(13, value.getJSONArray("widgets").length());
        assertEquals(0, value.getJSONObject("background").getInt("dim"));
        assertEquals(13, DashboardLayoutConfig.parse(value).toJson()
                .getJSONArray("widgets").length());
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
}
