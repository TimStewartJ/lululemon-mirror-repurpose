package dev.mirror.repurpose;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNotEquals;
import static org.junit.Assert.assertSame;
import static org.junit.Assert.assertTrue;
import static org.junit.Assert.fail;

import org.json.JSONObject;
import org.junit.Test;

public class PanelPlaceTest {
    @Test
    public void aMirrorStartsWithItsAnswersLowAndInTheMiddle() throws Exception {
        assertEquals("bottom", PanelPlace.DEFAULT.height);
        assertEquals("center", PanelPlace.DEFAULT.side);
        assertTrue(PanelPlace.DEFAULT.fromBottom());
        assertTrue(PanelPlace.DEFAULT.outermost());
        assertFalse(PanelPlace.DEFAULT.fromTop());
        JSONObject json = PanelPlace.DEFAULT.toJson();
        assertEquals("bottom", json.getString("height"));
        assertEquals("center", json.getString("side"));
    }

    @Test
    public void whatWasStoredIsReadBackAndAnythingElseIsWhereAMirrorStarts() {
        for (String height : PanelPlace.HEIGHTS) {
            for (String side : PanelPlace.SIDES) {
                PanelPlace place = PanelPlace.of(height, side);
                assertEquals(place, PanelPlace.parse(place.stored()));
                assertEquals(place.hashCode(), PanelPlace.parse(place.stored()).hashCode());
            }
        }
        assertEquals("upper left", PanelPlace.of("upper", "left").stored());
        // Nothing stored yet, something from a build that knew other places, or rubbish.
        for (String stored : new String[]{null, "", "top", "ceiling left", "top middle", "top left corner"}) {
            assertSame(stored, PanelPlace.DEFAULT, PanelPlace.parse(stored));
        }
    }

    @Test
    public void oneOfTheTwoCanBeChangedAndTheOtherStays() {
        PanelPlace place = PanelPlace.DEFAULT.with("top", null);
        assertEquals("top center", place.stored());
        place = place.with(null, "right");
        assertEquals("top right", place.stored());
        assertEquals("middle left", place.with("middle", "left").stored());
        assertEquals(place, place.with(null, null));
        assertNotEquals(place, PanelPlace.DEFAULT);
    }

    @Test
    public void aPlaceThatThereIsNoneOfIsRefusedWithTheOnesThereAre() {
        try {
            PanelPlace.of("ceiling", "left");
            fail("A height that there is none of was taken");
        } catch (IllegalArgumentException refused) {
            assertEquals("place.height must be one of: top, upper, middle, lower, bottom", refused.getMessage());
        }
        try {
            PanelPlace.DEFAULT.with(null, "middle");
            fail("A side that there is none of was taken");
        } catch (IllegalArgumentException refused) {
            assertEquals("place.side must be one of: left, center, right", refused.getMessage());
        }
    }

    @Test
    public void theUpperPlacesHangFromTheTopAndTheLowerOnesStandOnTheBottom() {
        assertTrue(PanelPlace.of("top", "center").fromTop());
        assertTrue(PanelPlace.of("upper", "center").fromTop());
        assertTrue(PanelPlace.of("lower", "center").fromBottom());
        assertTrue(PanelPlace.of("bottom", "center").fromBottom());
        PanelPlace middle = PanelPlace.of("middle", "center");
        assertFalse(middle.fromTop() || middle.fromBottom());
        assertTrue(PanelPlace.of("top", "left").outermost());
        assertFalse(PanelPlace.of("upper", "left").outermost());
        assertFalse(PanelPlace.of("lower", "left").outermost());
        assertFalse(middle.outermost());
    }

    @Test
    public void theChoicesAreListedFromTheTopDownAndFromLeftToRight() throws Exception {
        JSONObject choices = PanelPlace.choices();
        assertEquals("[\"top\",\"upper\",\"middle\",\"lower\",\"bottom\"]", choices.getJSONArray("heights").toString());
        assertEquals("[\"left\",\"center\",\"right\"]", choices.getJSONArray("sides").toString());
    }
}
