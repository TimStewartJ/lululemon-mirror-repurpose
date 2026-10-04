package dev.mirror.repurpose;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.util.Arrays;
import java.util.List;

/**
 * Where on the glass the Mirror's answers stand: how high, and to which
 * side. A Mirror hangs at one height and is read by people of several, from
 * a doorway or from right in front of it, so the place is the owner's to
 * choose. Low and in the middle is where a Mirror starts.
 */
final class PanelPlace {
    /** From the upper edge of the glass down to the lower one. */
    static final List<String> HEIGHTS = Arrays.asList("top", "upper", "middle", "lower", "bottom");
    static final List<String> SIDES = Arrays.asList("left", "center", "right");
    static final PanelPlace DEFAULT = new PanelPlace("bottom", "center");

    final String height;
    final String side;

    private PanelPlace(String height, String side) {
        this.height = height;
        this.side = side;
    }

    /**
     * @throws IllegalArgumentException if either is not one of the known names, with the names
     */
    static PanelPlace of(String height, String side) {
        if (!HEIGHTS.contains(height)) {
            throw new IllegalArgumentException("place.height must be one of: " + join(HEIGHTS));
        }
        if (!SIDES.contains(side)) {
            throw new IllegalArgumentException("place.side must be one of: " + join(SIDES));
        }
        return new PanelPlace(height, side);
    }

    /** The place that {@link #stored} wrote; where a Mirror starts for anything else. */
    static PanelPlace parse(String stored) {
        String[] parts = stored == null ? new String[0] : stored.split(" ");
        if (parts.length != 2 || !HEIGHTS.contains(parts[0]) || !SIDES.contains(parts[1])) {
            return DEFAULT;
        }
        return new PanelPlace(parts[0], parts[1]);
    }

    String stored() {
        return height + " " + side;
    }

    /**
     * This place with another height, another side or both; null leaves one as it is.
     *
     * @throws IllegalArgumentException as {@link #of}
     */
    PanelPlace with(String newHeight, String newSide) {
        return of(newHeight == null ? height : newHeight, newSide == null ? side : newSide);
    }

    /** Whether the panel hangs from the upper edge of the glass and grows downwards. */
    boolean fromTop() {
        return HEIGHTS.indexOf(height) < 2;
    }

    /** Whether it stands on the lower edge and grows upwards, as it does where a Mirror starts. */
    boolean fromBottom() {
        return HEIGHTS.indexOf(height) > 2;
    }

    /** Whether it is as near its edge as it goes, and not a quarter of the way in. */
    boolean outermost() {
        return "top".equals(height) || "bottom".equals(height);
    }

    JSONObject toJson() throws JSONException {
        return new JSONObject().put("height", height).put("side", side);
    }

    /** Every height and every side, for whoever offers the choice. */
    static JSONObject choices() throws JSONException {
        return new JSONObject().put("heights", new JSONArray(HEIGHTS)).put("sides", new JSONArray(SIDES));
    }

    @Override
    public boolean equals(Object other) {
        return other instanceof PanelPlace
                && height.equals(((PanelPlace) other).height)
                && side.equals(((PanelPlace) other).side);
    }

    @Override
    public int hashCode() {
        return stored().hashCode();
    }

    @Override
    public String toString() {
        return stored();
    }

    private static String join(List<String> names) {
        StringBuilder joined = new StringBuilder();
        for (String name : names) {
            joined.append(joined.length() == 0 ? "" : ", ").append(name);
        }
        return joined.toString();
    }
}
