package dev.mirror.repurpose;

import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.List;

/**
 * A pen that keeps what was drawn instead of drawing it: every shape as
 * points in the mascot's box, with the transformations worked out. The
 * tests measure a mascot with it, and the preview tool makes pictures from
 * it on a computer.
 */
final class MascotRecorder implements MascotPen {
    /** One shape: filled, or a line of a width through its points. */
    static final class Shape {
        final int rgb;
        final float alpha;
        /** 0 for a filled shape. */
        final float width;
        final boolean closed;
        final float[] points;

        Shape(int rgb, float alpha, float width, boolean closed, float[] points) {
            this.rgb = rgb;
            this.alpha = alpha;
            this.width = width;
            this.closed = closed;
            this.points = points;
        }
    }

    final List<Shape> shapes = new ArrayList<>();
    /** The box around everything drawn, line widths included. */
    float left = Float.MAX_VALUE;
    float top = Float.MAX_VALUE;
    float right = -Float.MAX_VALUE;
    float bottom = -Float.MAX_VALUE;

    /** x' = m0 x + m2 y + m4, y' = m1 x + m3 y + m5. */
    private float[] matrix = {1f, 0f, 0f, 1f, 0f, 0f};
    private final ArrayDeque<float[]> saved = new ArrayDeque<>();
    private int rgb;
    private float alpha;

    void clear() {
        shapes.clear();
        left = Float.MAX_VALUE;
        top = Float.MAX_VALUE;
        right = -Float.MAX_VALUE;
        bottom = -Float.MAX_VALUE;
    }

    /** Whether transformations were saved and restored in pairs. */
    boolean balanced() {
        return saved.isEmpty();
    }

    @Override
    public void save() {
        saved.push(matrix.clone());
    }

    @Override
    public void restore() {
        matrix = saved.pop();
    }

    @Override
    public void translate(float x, float y) {
        matrix[4] += matrix[0] * x + matrix[2] * y;
        matrix[5] += matrix[1] * x + matrix[3] * y;
    }

    @Override
    public void rotate(float degrees) {
        float cos = (float) Math.cos(Math.toRadians(degrees));
        float sin = (float) Math.sin(Math.toRadians(degrees));
        float a = matrix[0] * cos + matrix[2] * sin;
        float b = matrix[1] * cos + matrix[3] * sin;
        matrix[2] = -matrix[0] * sin + matrix[2] * cos;
        matrix[3] = -matrix[1] * sin + matrix[3] * cos;
        matrix[0] = a;
        matrix[1] = b;
    }

    @Override
    public void scale(float x, float y) {
        matrix[0] *= x;
        matrix[1] *= x;
        matrix[2] *= y;
        matrix[3] *= y;
    }

    @Override
    public void ink(int rgb, float alpha) {
        this.rgb = rgb;
        this.alpha = alpha;
    }

    @Override
    public void disc(float cx, float cy, float rx, float ry) {
        add(ellipse(cx, cy, rx, ry, 0f, 360f, 40, false), 40, true, 0f);
    }

    @Override
    public void ring(float cx, float cy, float rx, float ry, float width) {
        add(ellipse(cx, cy, rx, ry, 0f, 360f, 48, false), 48, true, width);
    }

    @Override
    public void line(float x1, float y1, float x2, float y2, float width) {
        add(new float[]{x1, y1, x2, y2}, 2, false, width);
    }

    @Override
    public void arc(float cx, float cy, float rx, float ry, float start, float sweep, float width) {
        int count = Math.max(6, Math.round(Math.abs(sweep) / 8f)) + 1;
        add(ellipse(cx, cy, rx, ry, start, sweep, count, true), count, false, width);
    }

    @Override
    public void shape(float[] points, int count, boolean closed, float width) {
        add(points, count, closed, width);
    }

    private static float[] ellipse(
            float cx, float cy, float rx, float ry, float start, float sweep, int count, boolean ends) {
        float[] points = new float[2 * count];
        for (int point = 0; point < count; point++) {
            double angle = Math.toRadians(start + sweep * point / (ends ? count - 1 : count));
            points[2 * point] = cx + rx * (float) Math.cos(angle);
            points[2 * point + 1] = cy + ry * (float) Math.sin(angle);
        }
        return points;
    }

    private void add(float[] points, int count, boolean closed, float width) {
        if (alpha <= 0.004f) {
            return;
        }
        float grown = width * (float) Math.sqrt(Math.abs(matrix[0] * matrix[3] - matrix[1] * matrix[2]));
        float[] placed = new float[2 * count];
        for (int point = 0; point < count; point++) {
            float x = points[2 * point];
            float y = points[2 * point + 1];
            float px = matrix[0] * x + matrix[2] * y + matrix[4];
            float py = matrix[1] * x + matrix[3] * y + matrix[5];
            placed[2 * point] = px;
            placed[2 * point + 1] = py;
            left = Math.min(left, px - grown / 2f);
            right = Math.max(right, px + grown / 2f);
            top = Math.min(top, py - grown / 2f);
            bottom = Math.max(bottom, py + grown / 2f);
        }
        shapes.add(new Shape(rgb, alpha, grown, closed, placed));
    }
}
