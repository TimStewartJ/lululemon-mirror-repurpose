package dev.mirror.repurpose;

import android.os.Handler;
import android.os.Looper;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

/**
 * The way to the panel on the glass, for whatever in Mirror Home has
 * something to show there. It stays within the process: a broadcast would
 * wait its turn behind the system's own, which after an installation or a
 * change of network can take seconds, and a caption that comes seconds late
 * is an answer to nothing.
 */
final class GlassCaption {
    /** The most rows an answer may have under it. */
    static final int MAX_ROWS = 5;
    static final int MAX_LABEL = 14;
    static final int MAX_ROW_TEXT = 90;

    /** One part of an answer that has several: what it is, and the words. */
    static final class Row {
        final String label;
        final String text;

        Row(String label, String text) {
            this.label = label;
            this.text = text;
        }
    }

    /** One thing for the panel to show. */
    static final class Caption {
        final String kind;
        /** The words; for an answer with rows, its headline. */
        final String text;
        /** What was understood of the request this answers; empty to leave that as it is. */
        final String heard;
        final List<Row> details;
        /** For how long; 0 for as long as its kind is shown. */
        final long millis;

        Caption(String kind, String text, String heard, List<Row> details, long millis) {
            this.kind = kind;
            this.text = text == null ? "" : text;
            this.heard = heard == null ? "" : heard;
            this.details = details == null ? Collections.<Row>emptyList() : details;
            this.millis = millis;
        }
    }

    /** Shows a caption; runs on the main thread. */
    interface Glass {
        void show(Caption caption);
    }

    private static final Handler MAIN = new Handler(Looper.getMainLooper());
    private static volatile Glass glass;
    private static volatile Runnable refresh;

    private GlassCaption() {
    }

    static void attach(Glass shown) {
        glass = shown;
    }

    static void detach(Glass shown) {
        if (glass == shown) {
            glass = null;
        }
    }

    /** Names what makes the dashboard look again at what it shows; null for nothing. */
    static void attachRefresh(Runnable look) {
        refresh = look;
    }

    static void detachRefresh(Runnable look) {
        if (refresh == look) {
            refresh = null;
        }
    }

    /**
     * Something that the glass shows was changed: its layout, the board, a
     * note. The dashboard looks every few seconds anyway, but whoever asked
     * for the change is standing there, and seconds are long then. Any thread.
     */
    static void changed() {
        MAIN.post(() -> {
            Runnable look = refresh;
            if (look != null) {
                look.run();
            }
        });
    }

    /**
     * Shows a line, or does what its kind says. Any thread. Without a
     * dashboard there is no glass to show it on, and it is dropped.
     *
     * @param millis for how long; 0 for as long as its kind is shown
     */
    static void show(String kind, String text, long millis) {
        show(new Caption(kind, text, "", null, millis));
    }

    static void show(Caption caption) {
        MAIN.post(() -> {
            Glass current = glass;
            if (current != null) {
                current.show(caption);
            }
        });
    }

    /**
     * Reads the rows of an answer: a list of {label, text}, as a companion sends it.
     *
     * @param strict whether rows that are too many or too long are refused; otherwise they are cut
     * @return the rows; none for null
     * @throws IllegalArgumentException with the reason, if strict and the rows break the limits
     */
    static List<Row> rows(JSONArray sent, boolean strict) {
        List<Row> rows = new ArrayList<>();
        if (sent == null) {
            return rows;
        }
        if (strict && sent.length() > MAX_ROWS) {
            throw new IllegalArgumentException("details holds at most " + MAX_ROWS + " rows");
        }
        for (int index = 0; index < sent.length() && rows.size() < MAX_ROWS; index++) {
            JSONObject row = sent.optJSONObject(index);
            Object label = row == null ? null : row.opt("label");
            Object text = row == null ? null : row.opt("text");
            if (label == null || label == JSONObject.NULL) {
                label = "";
            }
            boolean wellFormed = label instanceof String && text instanceof String
                    && !((String) text).trim().isEmpty()
                    && oneLine((String) label) && oneLine((String) text)
                    && ((String) label).length() <= MAX_LABEL
                    && ((String) text).length() <= MAX_ROW_TEXT;
            if (wellFormed) {
                rows.add(new Row(((String) label).trim(), ((String) text).trim()));
            } else if (strict) {
                throw new IllegalArgumentException(
                        "each row of details has a label of up to " + MAX_LABEL
                                + " characters and a text of 1 to " + MAX_ROW_TEXT + ", each on one line");
            } else if (text instanceof String && !((String) text).trim().isEmpty()) {
                String cutLabel = label instanceof String ? AssistantReply.oneLine((String) label) : "";
                String cutText = AssistantReply.oneLine((String) text);
                rows.add(new Row(
                        cutLabel.length() <= MAX_LABEL ? cutLabel : cutLabel.substring(0, MAX_LABEL).trim(),
                        cutText.length() <= MAX_ROW_TEXT
                                ? cutText
                                : cutText.substring(0, MAX_ROW_TEXT - 1).trim() + "\u2026"));
            }
        }
        return rows;
    }

    private static boolean oneLine(String text) {
        return text.indexOf('\n') < 0 && text.indexOf('\r') < 0;
    }
}
