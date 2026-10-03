package dev.mirror.repurpose;

import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

import fi.iki.elonen.NanoHTTPD.Method;

public class ControlServerGlassTest {
    @Test
    public void whatChangesTheGlassIsShownAtOnce() {
        assertTrue(ControlServer.changesGlass(Method.PUT, "/api/v1/dashboard/layout", 200));
        assertTrue(ControlServer.changesGlass(Method.POST, "/api/v1/board", 201));
        assertTrue(ControlServer.changesGlass(Method.DELETE, "/api/v1/board/abc", 200));
        assertTrue(ControlServer.changesGlass(Method.PUT, "/api/v1/notes/main", 200));
        assertTrue(ControlServer.changesGlass(Method.PUT, "/api/v1/background-videos/active", 200));
        assertTrue(ControlServer.changesGlass(Method.PUT, "/api/v1/weather", 204));
    }

    @Test
    public void whatChangesNothingOnTheGlassLeavesItAlone() {
        // Reading, a change that was refused, and what the glass does not show.
        assertFalse(ControlServer.changesGlass(Method.GET, "/api/v1/dashboard/layout", 200));
        assertFalse(ControlServer.changesGlass(Method.PUT, "/api/v1/dashboard/layout", 400));
        assertFalse(ControlServer.changesGlass(Method.POST, "/api/v1/board", 401));
        assertFalse(ControlServer.changesGlass(Method.PUT, "/api/v1/voice", 200));
        assertFalse(ControlServer.changesGlass(Method.POST, "/api/v1/assistant/say", 200));
        assertFalse(ControlServer.changesGlass(Method.PUT, "/api/v1/background-videos/upload/abc/3", 200));
        assertFalse(ControlServer.changesGlass(Method.PUT, null, 200));
    }
}
