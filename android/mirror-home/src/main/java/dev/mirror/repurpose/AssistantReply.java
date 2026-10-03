package dev.mirror.repurpose;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

/** What the companion answered to a request; see docs/assistant.md. */
final class AssistantReply {
    /** The longest line the glass is asked to show. */
    static final int MAX_TEXT = 200;

    /** What the companion understood the person to say. */
    final String heard;
    /** What the glass should show; empty for nothing. */
    final String reply;
    /** The companion took the words for talk that was not meant for the Mirror. */
    final boolean ignored;
    final String reason;
    /** The reply is a question: what is said next is its answer. */
    final boolean listen;
    /** What the companion did, as the names of its tools. */
    final String acted;
    /** The parts of an answer that has several; the reply is then their headline. */
    final java.util.List<GlassCaption.Row> details;
    /** How long the companion wants the answer shown, in milliseconds; 0 for as long as it needs. */
    final long showMillis;

    private AssistantReply(
            String heard,
            String reply,
            boolean ignored,
            String reason,
            boolean listen,
            String acted,
            java.util.List<GlassCaption.Row> details,
            long showMillis) {
        this.heard = heard;
        this.reply = reply;
        this.ignored = ignored;
        this.reason = reason;
        this.listen = listen;
        this.acted = acted;
        this.details = details;
        this.showMillis = showMillis;
    }

    static AssistantReply parse(String json) throws JSONException {
        JSONObject body = new JSONObject(json);
        boolean ignored = body.optBoolean("ignored", false);
        String reply = ignored ? "" : oneLine(body.optString("reply", ""));
        StringBuilder acted = new StringBuilder();
        JSONArray tools = body.optJSONArray("acted");
        for (int index = 0; tools != null && index < tools.length() && index < 12; index++) {
            acted.append(acted.length() == 0 ? "" : ", ").append(oneLine(tools.optString(index)));
        }
        java.util.List<GlassCaption.Row> details = ignored || reply.isEmpty()
                ? java.util.Collections.<GlassCaption.Row>emptyList()
                : GlassCaption.rows(body.optJSONArray("details"), false);
        double seconds = body.optDouble("seconds", 0);
        return new AssistantReply(
                oneLine(body.optString("heard", "")),
                reply,
                ignored,
                oneLine(body.optString("reason", "")),
                !reply.isEmpty() && body.optBoolean("listen", false),
                acted.toString(),
                details,
                seconds >= 2 && seconds <= 30 ? Math.round(seconds * 1000) : 0L);
    }

    /** How long this answer stays on the glass: as long as the companion said, or as its length needs. */
    long millis() {
        return showMillis > 0 ? showMillis : showMillis(reply, details);
    }

    /** Text as one line of at most {@link #MAX_TEXT} characters, cut between words. */
    static String oneLine(String text) {
        String line = text == null ? "" : text.replaceAll("\\s+", " ").trim();
        if (line.length() <= MAX_TEXT) {
            return line;
        }
        int cut = line.lastIndexOf(' ', MAX_TEXT - 1);
        return line.substring(0, cut < MAX_TEXT / 2 ? MAX_TEXT - 1 : cut).trim() + "\u2026";
    }

    /**
     * How long a line stays on the glass when nobody said how long: time to
     * notice it and to read it, from across a room.
     */
    static long showMillis(String text) {
        return Math.max(3_500L, Math.min(14_000L, 2_000L + 70L * text.length()));
    }

    /** The same for an answer with rows under it, each of which wants reading too. */
    static long showMillis(String text, java.util.List<GlassCaption.Row> details) {
        long rows = 0;
        for (GlassCaption.Row row : details) {
            rows += 1_200L + 55L * row.text.length();
        }
        return Math.min(24_000L, showMillis(text) + rows);
    }
}
