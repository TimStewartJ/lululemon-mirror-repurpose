package dev.mirror.repurpose;

import android.animation.LayoutTransition;
import android.animation.ValueAnimator;
import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.os.Handler;
import android.os.Looper;
import android.text.TextUtils;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.View;
import android.view.animation.DecelerateInterpolator;
import android.widget.LinearLayout;
import android.widget.TextView;

import dev.mirror.repurpose.MascotRig.Mood;

import java.util.List;
import java.util.Locale;

/**
 * Where the Mirror answers: a small panel low on the glass, in place of a
 * voice. Nobody wants a mirror to talk back.
 *
 * <p>It is read from across a room, in passing, so it shows one thing at a
 * time and says which: dots that breathe while the Mirror listens and that
 * run while it works; what it understood, small and in quotation marks,
 * which stays above the answer so that a mishearing can be seen for what it
 * is; the answer, large; and under an answer that has several parts, a row
 * for each with a label before it. If the owner chose a character, it
 * stands above the words in place of the dots and acts all of this out: it
 * listens, thinks, nods when it has understood, and says its answer.
 * Everything arrives and leaves by fading,
 * and the panel grows and shrinks around its words instead of jumping. Its
 * backing is black, which on the glass is plain mirror and keeps a film or
 * a widget behind the words from tangling with them.
 */
final class ConversationPanel extends LinearLayout {
    private static final int TEXT_COLOR = Color.rgb(245, 242, 236);
    private static final int HEARD_COLOR = Color.argb(158, 245, 242, 236);
    private static final int LABEL_COLOR = Color.argb(168, 245, 242, 236);
    private static final int ROW_COLOR = Color.argb(235, 245, 242, 236);
    private static final long APPEAR_MS = 260L;
    private static final long LEAVE_MS = 450L;
    private static final long SWAP_OUT_MS = 130L;
    private static final long SWAP_IN_MS = 240L;
    private static final long ROW_DELAY_MS = 110L;
    /** How long a caption stays when nothing says how long. */
    private static final long CAPTION_MS = 2_500L;
    /** A word or two is set large; a sentence that has to fit, smaller. */
    private static final int SHORT_TEXT = 32;
    private static final int LABEL_GAP_DP = 16;
    /** The box of a mascot, of which the mascot itself fills the middle two thirds. */
    private static final int MASCOT_DP = 112;

    private final Handler handler = new Handler(Looper.getMainLooper());
    private final MascotView mascot;
    private final TextView heard;
    private final LinearLayout status;
    private final Dots dots;
    private final TextView statusLabel;
    private final TextView main;
    private final LinearLayout rows;
    private final Runnable leave = this::fadeOut;
    private boolean showing;
    /** Counts what was shown, so that a fade that ends late leaves newer words alone. */
    private int generation;

    ConversationPanel(Context context) {
        super(context);
        setOrientation(VERTICAL);
        setGravity(Gravity.CENTER_HORIZONTAL);
        setPadding(dp(30), dp(16), dp(30), dp(18));
        GradientDrawable backing = new GradientDrawable();
        // Wholly black: a clock's large figures show through anything less.
        backing.setColor(Color.BLACK);
        backing.setCornerRadius(dp(30));
        setBackground(backing);
        LayoutTransition growing = new LayoutTransition();
        growing.enableTransitionType(LayoutTransition.CHANGING);
        growing.disableTransitionType(LayoutTransition.APPEARING);
        growing.disableTransitionType(LayoutTransition.DISAPPEARING);
        growing.setDuration(SWAP_IN_MS);
        // Only what is inside: the panel's own place is seen to below.
        growing.setAnimateParentHierarchy(false);
        setLayoutTransition(growing);

        mascot = new MascotView(context);
        mascot.setVisibility(GONE);
        LayoutParams mascotLayout = new LayoutParams(dp(MASCOT_DP), dp(MASCOT_DP));
        mascotLayout.gravity = Gravity.CENTER_HORIZONTAL;
        addView(mascot, mascotLayout);

        heard = text(context, 20, "sans-serif-light", HEARD_COLOR);
        heard.setTypeface(Typeface.create("sans-serif-light", Typeface.ITALIC));
        heard.setMaxLines(2);
        heard.setEllipsize(TextUtils.TruncateAt.END);
        heard.setVisibility(GONE);
        addView(heard, wrapped(0, 0, 0, dp(9)));

        status = new LinearLayout(context);
        status.setOrientation(HORIZONTAL);
        status.setGravity(Gravity.CENTER_VERTICAL);
        dots = new Dots(context);
        status.addView(dots, new LayoutParams(dp(58), dp(34)));
        statusLabel = text(context, 26, "sans-serif-light", TEXT_COLOR);
        statusLabel.setLetterSpacing(0.04f);
        statusLabel.setVisibility(GONE);
        LayoutParams labelLayout = wrapped(dp(12), 0, 0, 0);
        status.addView(statusLabel, labelLayout);
        status.setVisibility(GONE);
        addView(status, wrapped(0, 0, 0, 0));

        main = text(context, 32, "sans-serif-light", TEXT_COLOR);
        main.setMaxLines(4);
        main.setEllipsize(TextUtils.TruncateAt.END);
        main.setLineSpacing(0f, 1.12f);
        main.setVisibility(GONE);
        addView(main, wrapped(0, 0, 0, 0));

        rows = new LinearLayout(context);
        rows.setOrientation(VERTICAL);
        rows.setVisibility(GONE);
        addView(rows, wrapped(0, dp(14), 0, dp(2)));

        // The panel stands on its lower edge, so more words push its upper edge
        // up at once. Carried back to where they were and let rise from there,
        // the words that stay glide to their new place instead of jumping.
        addOnLayoutChangeListener((view, left, top, right, bottom, oldLeft, oldTop, oldRight, oldBottom) -> {
            if (showing && getAlpha() > 0f && oldBottom > oldTop && top != oldTop) {
                setTranslationY(getTranslationY() + oldTop - top);
                animate().translationY(0f).setDuration(SWAP_IN_MS + 80L)
                        .setInterpolator(new DecelerateInterpolator());
            }
        });

        setAlpha(0f);
        setVisibility(GONE);
    }

    /** Chooses the character that stands above the words, by its id; none for an id that names none. */
    void setMascot(String id) {
        Mascot chosen = Mascot.byId(id);
        if (chosen == mascot.chosen()) {
            return;
        }
        mascot.choose(chosen);
        mascot.setVisibility(chosen == null ? GONE : VISIBLE);
    }

    /** Has the mascot, if there is one, act a caption out. */
    private void act(String kind, GlassCaption.Caption caption, String text) {
        if (mascot.chosen() == null) {
            return;
        }
        if (VoiceManager.KIND_LISTENING.equals(kind)) {
            mascot.show(Mood.LISTENING);
        } else if (AssistantManager.KIND_THINKING.equals(kind)) {
            mascot.show(Mood.THINKING);
        } else if (AssistantManager.KIND_HEARD.equals(kind)) {
            mascot.show(Mood.THINKING);
            mascot.nod();
        } else if (VoiceManager.KIND_NOT_UNDERSTOOD.equals(kind)) {
            mascot.show(Mood.CONFUSED);
        } else if (GlassCaption.MOOD_SLEEP.equals(caption.mood)) {
            mascot.show(Mood.SLEEPY);
        } else if (GlassCaption.MOOD_SORRY.equals(caption.mood)) {
            mascot.show(Mood.SORRY);
        } else if (GlassCaption.MOOD_GREET.equals(caption.mood)) {
            // The greeting came first and its answer follows: it goes on waving through both.
            if (mascot.mood() != Mood.GREETING) {
                mascot.show(Mood.GREETING);
            }
        } else if (VoiceManager.KIND_COMMAND.equals(kind)) {
            mascot.show(Mood.HAPPY);
        } else {
            // It says its answer for about as long as the first words take to read.
            float seconds = Math.min(2.4f, 0.9f + 0.012f * text.length());
            boolean asks = GlassCaption.MOOD_CURIOUS.equals(caption.mood) || text.endsWith("?");
            mascot.speak(seconds, asks ? Mood.CURIOUS : Mood.IDLE);
        }
    }

    /** Shows what a caption holds, in the way of its kind. Call on the main thread. */
    void show(GlassCaption.Caption caption) {
        String kind = caption.kind == null ? "" : caption.kind;
        if (AssistantManager.KIND_CLEAR.equals(kind)) {
            handler.removeCallbacks(leave);
            fadeOut();
            return;
        }
        generation++;
        long showFor = caption.millis > 0 ? caption.millis : CAPTION_MS;
        if (VoiceManager.KIND_LISTENING.equals(kind)) {
            setHeard("");
            setStatus(Dots.BREATHING, "Listening");
            setMain("");
            setRows(null);
            showFor = VoiceInterpreter.WINDOW_MS;
        } else if (AssistantManager.KIND_THINKING.equals(kind)) {
            // A new request: what was understood of the one before is gone.
            setHeard("");
            setStatus(Dots.RUNNING, "");
            setMain("");
            setRows(null);
        } else if (AssistantManager.KIND_HEARD.equals(kind)) {
            setHeard(caption.text);
            setStatus(Dots.RUNNING, "");
            setMain("");
            setRows(null);
        } else {
            String text = VoiceManager.KIND_NOT_UNDERSTOOD.equals(kind) ? "Didn\u2019t catch that" : caption.text;
            if (VoiceManager.KIND_NOT_UNDERSTOOD.equals(kind)) {
                showFor = VoiceInterpreter.WINDOW_MS;
            }
            boolean answers = AssistantManager.KIND_REPLY.equals(kind);
            if (!caption.heard.isEmpty()) {
                setHeard(caption.heard);
            } else if (!answers) {
                // An answer keeps what the companion showed it had understood.
                setHeard("");
            }
            setStatus(Dots.STILL, "");
            setMain(text);
            setRows(caption.details);
        }
        act(kind, caption, caption.text);
        appear();
        handler.removeCallbacks(leave);
        handler.postDelayed(leave, showFor);
    }

    /** Stops everything; for a dashboard that is being taken down. */
    void cancel() {
        handler.removeCallbacksAndMessages(null);
        dots.set(Dots.STILL);
        animate().cancel();
        main.animate().cancel();
        heard.animate().cancel();
    }

    private void appear() {
        if (showing && getAlpha() == 1f) {
            return;
        }
        boolean fromNothing = getVisibility() != VISIBLE;
        showing = true;
        setVisibility(VISIBLE);
        animate().cancel();
        if (fromNothing) {
            setAlpha(0f);
            setTranslationY(dp(14));
        }
        animate().alpha(1f).translationY(0f).setDuration(APPEAR_MS)
                .setInterpolator(new DecelerateInterpolator()).withEndAction(null);
    }

    private void fadeOut() {
        if (!showing) {
            return;
        }
        showing = false;
        mascot.show(Mood.HIDDEN);
        int leaving = generation;
        animate().cancel();
        animate().alpha(0f).translationY(dp(6)).setDuration(LEAVE_MS).withEndAction(() -> {
            if (showing || leaving != generation) {
                return;
            }
            setVisibility(GONE);
            dots.set(Dots.STILL);
            // Emptied, so that the next words do not arrive beside these.
            heard.setText("");
            heard.setVisibility(GONE);
            main.setText("");
            main.setVisibility(GONE);
            status.setVisibility(GONE);
            rows.removeAllViews();
            rows.setVisibility(GONE);
        });
    }

    private void setHeard(String words) {
        String text = words == null || words.isEmpty() ? "" : "\u201c" + words + "\u201d";
        if (text.contentEquals(heard.getText()) && (text.isEmpty() == (heard.getVisibility() == GONE))) {
            return;
        }
        heard.animate().cancel();
        if (text.isEmpty()) {
            heard.setText("");
            heard.setVisibility(GONE);
            return;
        }
        heard.setText(text);
        heard.setVisibility(VISIBLE);
        heard.setAlpha(0f);
        heard.animate().alpha(1f).setDuration(SWAP_IN_MS).withEndAction(null);
    }

    private void setStatus(int motion, String label) {
        if (mascot.chosen() != null) {
            // The mascot shows that the Mirror listens or works; the dots would say it twice.
            dots.set(Dots.STILL);
            dots.setVisibility(GONE);
            if (label.isEmpty()) {
                status.setVisibility(GONE);
                return;
            }
        } else {
            dots.setVisibility(VISIBLE);
            dots.set(motion);
            if (motion == Dots.STILL) {
                status.setVisibility(GONE);
                return;
            }
        }
        statusLabel.setText(label);
        statusLabel.setVisibility(label.isEmpty() ? GONE : VISIBLE);
        // Beside the dots the label keeps its distance; alone it stands in the middle.
        ((LayoutParams) statusLabel.getLayoutParams()).leftMargin = mascot.chosen() == null ? dp(12) : 0;
        if (status.getVisibility() != VISIBLE) {
            status.setVisibility(VISIBLE);
            status.setAlpha(0f);
            status.animate().alpha(1f).setDuration(SWAP_IN_MS).withEndAction(null);
        }
    }

    private void setMain(String words) {
        String text = words == null ? "" : words;
        main.animate().cancel();
        if (text.isEmpty()) {
            main.setText("");
            main.setVisibility(GONE);
            return;
        }
        if (text.contentEquals(main.getText()) && main.getVisibility() == VISIBLE) {
            // The same words, as when a greeting gains its rows: they stay as they are.
            main.setAlpha(1f);
            return;
        }
        boolean replaces = main.getVisibility() == VISIBLE && getVisibility() == VISIBLE;
        Runnable arrive = () -> {
            main.setTextSize(TypedValue.COMPLEX_UNIT_SP, text.length() <= SHORT_TEXT ? 32 : 26);
            main.setText(text);
            main.setVisibility(VISIBLE);
            main.setAlpha(0f);
            main.animate().alpha(1f).setDuration(SWAP_IN_MS).withEndAction(null);
        };
        if (replaces) {
            main.animate().alpha(0f).setDuration(SWAP_OUT_MS).withEndAction(arrive);
        } else {
            arrive.run();
        }
    }

    /** The rows under an answer: a label in small capitals, then what it labels. */
    private void setRows(List<GlassCaption.Row> details) {
        rows.removeAllViews();
        if (details == null || details.isEmpty()) {
            rows.setVisibility(GONE);
            setColumn(-1);
            return;
        }
        Paint measure = new Paint();
        TextView sample = label("");
        measure.setTypeface(sample.getTypeface());
        measure.setTextSize(sample.getTextSize());
        measure.setLetterSpacing(sample.getLetterSpacing());
        float widest = 0f;
        for (GlassCaption.Row row : details) {
            widest = Math.max(widest, measure.measureText(row.label.toUpperCase(Locale.US)));
        }
        int labelWidth = widest == 0f ? 0 : Math.round(widest) + dp(4);
        setColumn(labelWidth == 0 ? 0 : labelWidth + LABEL_GAP_DP * getResources().getDisplayMetrics().density);
        int index = 0;
        for (GlassCaption.Row row : details) {
            LinearLayout line = new LinearLayout(getContext());
            line.setOrientation(HORIZONTAL);
            if (labelWidth > 0) {
                TextView label = label(row.label.toUpperCase(Locale.US));
                label.setGravity(Gravity.END);
                // A row lines its label up with the first line of the larger words beside it.
                LayoutParams labelLayout = new LayoutParams(labelWidth, LayoutParams.WRAP_CONTENT);
                labelLayout.rightMargin = dp(LABEL_GAP_DP);
                line.addView(label, labelLayout);
            }
            TextView words = text(getContext(), 24, "sans-serif-light", ROW_COLOR);
            words.setGravity(Gravity.START);
            words.setMaxLines(2);
            words.setEllipsize(TextUtils.TruncateAt.END);
            words.setLineSpacing(0f, 1.1f);
            words.setText(row.text);
            line.addView(words, new LayoutParams(LayoutParams.WRAP_CONTENT, LayoutParams.WRAP_CONTENT));
            LayoutParams lineLayout = new LayoutParams(LayoutParams.WRAP_CONTENT, LayoutParams.WRAP_CONTENT);
            lineLayout.topMargin = index == 0 ? 0 : dp(9);
            lineLayout.gravity = Gravity.START;
            rows.addView(line, lineLayout);
            // One after another, so that the eye is led down the list.
            line.setAlpha(0f);
            line.setTranslationY(dp(8));
            line.animate().alpha(1f).translationY(0f)
                    .setStartDelay(SWAP_OUT_MS + ROW_DELAY_MS * index).setDuration(SWAP_IN_MS + 80L)
                    .setInterpolator(new DecelerateInterpolator());
            index++;
        }
        rows.setVisibility(VISIBLE);
    }

    /**
     * Sets what is above the rows over the column of their words: an answer
     * with rows reads as a list with a heading, and a heading centred over
     * labels and words together would sit over neither.
     *
     * @param indent where the words begin, in pixels; negative for no rows, when everything is centred
     */
    private void setColumn(float indent) {
        boolean listed = indent >= 0;
        // The mascot stands over the first word of a list, as the one who says it.
        LayoutParams stand = (LayoutParams) mascot.getLayoutParams();
        stand.gravity = listed ? Gravity.START : Gravity.CENTER_HORIZONTAL;
        stand.leftMargin = listed ? Math.round(indent) - dp(MASCOT_DP) / 6 : 0;
        mascot.setLayoutParams(stand);
        for (TextView line : new TextView[]{heard, main}) {
            LayoutParams layout = (LayoutParams) line.getLayoutParams();
            layout.gravity = listed ? Gravity.START : Gravity.CENTER_HORIZONTAL;
            layout.leftMargin = listed ? Math.round(indent) : 0;
            line.setLayoutParams(layout);
            line.setGravity(listed ? Gravity.START : Gravity.CENTER);
        }
    }

    private TextView label(String words) {
        TextView label = text(getContext(), 13, "sans-serif-medium", LABEL_COLOR);
        label.setLetterSpacing(0.14f);
        label.setSingleLine(true);
        label.setText(words);
        return label;
    }

    private static TextView text(Context context, int sp, String family, int color) {
        TextView view = new TextView(context);
        view.setTextColor(color);
        view.setTextSize(TypedValue.COMPLEX_UNIT_SP, sp);
        view.setTypeface(Typeface.create(family, Typeface.NORMAL));
        view.setGravity(Gravity.CENTER);
        view.setIncludeFontPadding(false);
        return view;
    }

    private static LayoutParams wrapped(int left, int top, int right, int bottom) {
        LayoutParams layout = new LayoutParams(LayoutParams.WRAP_CONTENT, LayoutParams.WRAP_CONTENT);
        layout.setMargins(left, top, right, bottom);
        return layout;
    }

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }

    /**
     * Three dots. Breathing together, the Mirror listens; running one after
     * the other, it works on what it heard.
     */
    private static final class Dots extends View {
        static final int STILL = 0;
        static final int BREATHING = 1;
        static final int RUNNING = 2;

        private final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);
        private final ValueAnimator clock = ValueAnimator.ofFloat(0f, 1f);
        private int motion = STILL;

        Dots(Context context) {
            super(context);
            paint.setColor(TEXT_COLOR);
            clock.setRepeatCount(ValueAnimator.INFINITE);
            clock.setInterpolator(null);
            clock.addUpdateListener(animation -> invalidate());
        }

        void set(int wanted) {
            if (wanted == motion) {
                return;
            }
            motion = wanted;
            clock.cancel();
            if (wanted != STILL) {
                clock.setDuration(wanted == BREATHING ? 2_400L : 1_250L);
                clock.start();
            }
            invalidate();
        }

        @Override
        protected void onDetachedFromWindow() {
            clock.cancel();
            super.onDetachedFromWindow();
        }

        @Override
        protected void onDraw(Canvas canvas) {
            float density = getResources().getDisplayMetrics().density;
            float radius = 4f * density;
            float gap = 17f * density;
            float middle = getWidth() / 2f;
            float line = getHeight() / 2f;
            float time = motion == STILL ? 0f : (Float) clock.getAnimatedValue();
            for (int dot = 0; dot < 3; dot++) {
                // Each dot swells a moment after the one before it; breathing, all at once.
                float phase = motion == RUNNING ? time - dot * 0.16f : time;
                float swell = (float) (0.5 - 0.5 * Math.cos(2 * Math.PI * phase));
                float strength = motion == RUNNING ? (float) Math.pow(swell, 1.6) : swell;
                paint.setAlpha(Math.round(255 * (0.34f + 0.66f * strength)));
                float lift = motion == RUNNING ? -3f * density * strength : 0f;
                canvas.drawCircle(
                        middle + (dot - 1) * gap, line + lift, radius * (0.82f + 0.3f * strength), paint);
            }
        }
    }
}
