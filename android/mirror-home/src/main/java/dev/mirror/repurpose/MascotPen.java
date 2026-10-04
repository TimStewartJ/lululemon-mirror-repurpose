package dev.mirror.repurpose;

/**
 * What a mascot is drawn with. A mascot knows nothing of Android: on the
 * glass this is a Canvas, and in the tests and the preview tool it is a list
 * of shapes, so that the same drawing can be looked at without a Mirror.
 *
 * <p>A mascot's box runs from -1 to 1 both ways, with y pointing down.
 * Angles are in degrees and turn clockwise from three o'clock, as on a
 * screen.
 */
interface MascotPen {
    void save();

    void restore();

    void translate(float x, float y);

    void rotate(float degrees);

    void scale(float x, float y);

    /** The colour of what is drawn next, as 0xRRGGBB, and how strong it is, from 0 to 1. */
    void ink(int rgb, float alpha);

    /** A filled ellipse. */
    void disc(float cx, float cy, float rx, float ry);

    /** The outline of an ellipse. */
    void ring(float cx, float cy, float rx, float ry, float width);

    /** A line with round ends. */
    void line(float x1, float y1, float x2, float y2, float width);

    /** A part of an ellipse's outline, with round ends. */
    void arc(float cx, float cy, float rx, float ry, float start, float sweep, float width);

    /**
     * A shape through points given as x0, y0, x1, y1 and so on.
     *
     * @param count how many points
     * @param closed whether the last point joins the first
     * @param width the width of its outline; 0 fills it instead
     */
    void shape(float[] points, int count, boolean closed, float width);
}
