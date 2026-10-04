package dev.mirror.repurpose;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Paint;
import android.graphics.Path;
import android.graphics.RectF;
import android.view.View;
import android.view.animation.AnimationUtils;

/**
 * The mascot on the glass: a view that draws one, and keeps drawing while
 * it moves. A mascot that is away and still costs nothing; the view asks for
 * its next frame only while there is one to show.
 */
final class MascotView extends View {
    private final MascotRig rig = new MascotRig();
    private final Pen pen = new Pen();
    private Mascot mascot;
    /** When the last frame was drawn, in animation time; 0 before the first of a run. */
    private long drawnAt;

    MascotView(Context context) {
        super(context);
        // Drawn by the processor, not the graphics chip: Android 6 draws a path
        // on a scaled canvas from a picture made at its unscaled size, and a
        // mascot is all of two units across. A view this small costs nothing.
        setLayerType(LAYER_TYPE_SOFTWARE, null);
    }

    /** Which mascot to draw; null for none. It takes over in whatever mood the last one was. */
    void choose(Mascot chosen) {
        if (chosen == mascot) {
            return;
        }
        mascot = chosen;
        // For whoever cannot see it, and for the validation suite, which reads the glass this way.
        setContentDescription(chosen == null ? null : chosen.name);
        invalidate();
    }

    Mascot chosen() {
        return mascot;
    }

    MascotRig.Mood mood() {
        return rig.mood();
    }

    void show(MascotRig.Mood mood) {
        rig.show(mood);
        invalidate();
    }

    void speak(float seconds, MascotRig.Mood then) {
        rig.speak(seconds, then);
        invalidate();
    }

    void nod() {
        rig.nod();
        invalidate();
    }

    @Override
    protected void onDraw(Canvas canvas) {
        if (mascot == null) {
            return;
        }
        long now = AnimationUtils.currentAnimationTimeMillis();
        rig.step(drawnAt == 0L ? 1f / 60f : (now - drawnAt) / 1000f);
        drawnAt = now;
        float half = Math.min(getWidth(), getHeight()) / 2f;
        canvas.save();
        canvas.translate(getWidth() / 2f, getHeight() / 2f);
        canvas.scale(half, half);
        pen.canvas = canvas;
        mascot.render(pen, rig.pose(), rig.clock());
        pen.canvas = null;
        canvas.restore();
        if (rig.resting()) {
            drawnAt = 0L;
        } else {
            postInvalidateOnAnimation();
        }
    }

    /** A mascot's pen on a Canvas that was moved and scaled to the mascot's box. */
    private static final class Pen implements MascotPen {
        private final Paint fill = new Paint(Paint.ANTI_ALIAS_FLAG);
        private final Paint stroke = new Paint(Paint.ANTI_ALIAS_FLAG);
        private final Path path = new Path();
        private final RectF box = new RectF();
        Canvas canvas;

        Pen() {
            fill.setStyle(Paint.Style.FILL);
            stroke.setStyle(Paint.Style.STROKE);
            stroke.setStrokeCap(Paint.Cap.ROUND);
            stroke.setStrokeJoin(Paint.Join.ROUND);
        }

        @Override
        public void save() {
            canvas.save();
        }

        @Override
        public void restore() {
            canvas.restore();
        }

        @Override
        public void translate(float x, float y) {
            canvas.translate(x, y);
        }

        @Override
        public void rotate(float degrees) {
            canvas.rotate(degrees);
        }

        @Override
        public void scale(float x, float y) {
            canvas.scale(x, y);
        }

        @Override
        public void ink(int rgb, float alpha) {
            int color = (Math.round(Math.max(0f, Math.min(1f, alpha)) * 255f) << 24) | (rgb & 0xFFFFFF);
            fill.setColor(color);
            stroke.setColor(color);
        }

        @Override
        public void disc(float cx, float cy, float rx, float ry) {
            box.set(cx - rx, cy - ry, cx + rx, cy + ry);
            canvas.drawOval(box, fill);
        }

        @Override
        public void ring(float cx, float cy, float rx, float ry, float width) {
            box.set(cx - rx, cy - ry, cx + rx, cy + ry);
            stroke.setStrokeWidth(width);
            canvas.drawOval(box, stroke);
        }

        @Override
        public void line(float x1, float y1, float x2, float y2, float width) {
            stroke.setStrokeWidth(width);
            canvas.drawLine(x1, y1, x2, y2, stroke);
        }

        @Override
        public void arc(float cx, float cy, float rx, float ry, float start, float sweep, float width) {
            box.set(cx - rx, cy - ry, cx + rx, cy + ry);
            stroke.setStrokeWidth(width);
            canvas.drawArc(box, start, sweep, false, stroke);
        }

        @Override
        public void shape(float[] points, int count, boolean closed, float width) {
            path.rewind();
            path.moveTo(points[0], points[1]);
            for (int point = 1; point < count; point++) {
                path.lineTo(points[2 * point], points[2 * point + 1]);
            }
            if (closed) {
                path.close();
            }
            if (width > 0f) {
                stroke.setStrokeWidth(width);
                canvas.drawPath(path, stroke);
            } else {
                canvas.drawPath(path, fill);
            }
        }
    }
}
