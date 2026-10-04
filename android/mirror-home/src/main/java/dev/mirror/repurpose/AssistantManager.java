package dev.mirror.repurpose;

import android.content.Context;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.util.Log;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.ArrayDeque;
import java.util.UUID;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;

/**
 * The Mirror's side of its assistant. What is said to the Mirror and is no
 * command of its own goes to a companion on the household network, which
 * works out what was meant, changes the Mirror through the control API, and
 * answers with a line for the glass. See docs/assistant.md.
 *
 * <p>Nothing is sent anywhere until an owner has switched the assistant on
 * and said where the companion is.
 */
public final class AssistantManager {
    static final String KIND_THINKING = "thinking";
    static final String KIND_HEARD = "heard";
    static final String KIND_REPLY = "reply";
    static final String KIND_NOTICE = "notice";
    static final String KIND_CLEAR = "clear";
    /** What the glass shows at the place its answers were moved to. */
    static final String PLACE_NOTICE = "Answers appear here";

    private static final String TAG = "AssistantManager";
    private static final int CONNECT_TIMEOUT_MS = 4_000;
    /** The companion promises an answer within 40 seconds, whatever happens. */
    private static final int ANSWER_TIMEOUT_MS = 45_000;
    private static final int SIDE_TIMEOUT_MS = 5_000;
    private static final long HEALTH_INTERVAL_MS = 60_000L;
    private static final int MAX_ANSWER_BYTES = 64 * 1024;
    private static final int MAX_RECENT = 12;
    /** What was understood stays until the answer replaces it. */
    private static final long HEARD_MILLIS = 20_000L;
    private static final long THINKING_MILLIS = ANSWER_TIMEOUT_MS + 2_000L;
    /** A greeting is answered from what the companion already knows: at once, or not worth the wait. */
    private static final int SHORTCUT_TIMEOUT_MS = 8_000;

    private static volatile AssistantManager instance;

    private final Context context;
    private final ConfigStore configStore;
    private final Handler handler = new Handler(Looper.getMainLooper());
    /** One request at a time, in the order they were made. */
    private final ExecutorService exchanges = Executors.newSingleThreadExecutor();
    /** Health and events, which must not wait behind a request. */
    private final ExecutorService side = Executors.newSingleThreadExecutor();
    private final ArrayDeque<JSONObject> recent = new ArrayDeque<>();
    private final Runnable healthCheck = new Runnable() {
        @Override
        public void run() {
            if (available()) {
                side.execute(AssistantManager.this::checkHealth);
            }
            handler.postDelayed(this, HEALTH_INTERVAL_MS);
        }
    };

    private boolean started;
    private int waiting;
    private long lastAnswerAt;
    private long lastFailureAt;
    private String lastFailure = "";
    private boolean companionOk;
    private String companionDetail = "";
    private String companionModel = "";
    private long requests;
    private long ignored;
    private long failures;
    /** What is to happen once an answer has been read, such as going dark after "good night". */
    private Runnable afterShown;
    /** Counts what was said to the Mirror, so that a greeting's answer knows whether it is still wanted. */
    private final java.util.concurrent.atomic.AtomicInteger spoken = new java.util.concurrent.atomic.AtomicInteger();

    private AssistantManager(Context context) {
        this.context = context.getApplicationContext();
        configStore = new ConfigStore(this.context);
    }

    public static AssistantManager getInstance(Context context) {
        if (instance == null) {
            synchronized (AssistantManager.class) {
                if (instance == null) {
                    instance = new AssistantManager(context);
                }
            }
        }
        return instance;
    }

    /** Begins looking after the companion; called once when Mirror Home starts. */
    public synchronized void start() {
        if (started) {
            return;
        }
        started = true;
        handler.post(healthCheck);
        event("started", null);
    }

    /** Whether there is a companion to pass a request on to. */
    public boolean available() {
        return configStore.isAssistantEnabled()
                && !configStore.getAssistantAddress().isEmpty()
                && !configStore.getAssistantKey().isEmpty();
    }

    /**
     * Changes the settings; a null leaves one as it is.
     *
     * @throws IllegalArgumentException with the reason, if the address or the key cannot be used
     */
    public void configure(Boolean enabled, String address, String key) {
        String normalized = address == null ? null : AssistantAddress.normalize(address);
        if (key != null && (key.length() > 256 || !key.matches("[\\x21-\\x7e]*"))) {
            throw new IllegalArgumentException(
                    "The companion's key is up to 256 letters, digits and signs, without spaces");
        }
        if (normalized != null) {
            configStore.setAssistantAddress(normalized);
        }
        if (key != null) {
            configStore.setAssistantKey(key);
        }
        if (enabled != null) {
            configStore.setAssistantEnabled(enabled);
        }
        synchronized (this) {
            companionOk = false;
            companionDetail = "";
            lastAnswerAt = 0;
            lastFailureAt = 0;
            lastFailure = "";
        }
        handler.removeCallbacks(healthCheck);
        handler.post(healthCheck);
    }

    /**
     * Someone said something to the Mirror that is for the assistant.
     *
     * @param from where the words lie in what the recogniser's process heard; -1 if unknown
     * @param injected the words themselves when a debug build was handed them in place of sound
     */
    void heardRequest(VoiceManager voice, long from, long to, String addressed, String injected) {
        AutomationManager automation = AutomationManager.getInstance(context);
        if (automation.isSleeping() && "inactivity".equals(automation.sleepReason())) {
            // Dark because nobody was there: whoever speaks is there now. A Mirror
            // that was told to sleep, or sleeps by its schedule, waits for the answer.
            automation.wakeForPresence();
        }
        show(KIND_THINKING, "", THINKING_MILLIS);
        synchronized (this) {
            waiting++;
        }
        if (from < 0 || to <= from) {
            if (BuildConfig.DEBUG && injected != null) {
                String text = injected;
                exchanges.execute(() -> finish("voice", voice, "", exchange(() -> postAsk(text, "test"))));
            } else {
                finish("voice", voice, "", Result.failed("Nothing was recorded of what was said"));
            }
            return;
        }
        String id = UUID.randomUUID().toString();
        File file = new File(context.getCacheDir(), "utterance-" + id + ".wav");
        voice.cut(from, to, file, samples -> {
            if (samples <= 0) {
                file.delete();
                finish("voice", voice, "", Result.failed("Nothing was recorded of what was said"));
                return;
            }
            exchanges.execute(() -> {
                Result result = exchange(() -> postUtterance(file, addressed, id));
                file.delete();
                finish("voice", voice, "", result);
            });
        });
    }

    /**
     * A greeting that the Mirror recognised itself, such as "good morning":
     * the companion answers it with where things stand. The Mirror has shown
     * the greeting already; the answer adds its rows.
     *
     * @param id the shortcut's name for the companion, such as good-morning
     * @param wording what was said
     * @param then what to do once the answer has been read, or at once if none comes; may be null
     */
    void shortcut(String id, String wording, Runnable then) {
        int mine = cancelAfterShown();
        synchronized (this) {
            waiting++;
        }
        // A thread of its own: a greeting waits neither behind a request that
        // the companion is still working on, nor behind a look at its health.
        new Thread(() -> {
            Result result = exchange(() -> postAsk(wording, "shortcut", id, SHORTCUT_TIMEOUT_MS));
            record("shortcut", wording, result);
            handler.post(() -> {
                if (spoken.get() != mine) {
                    // Something else was said since, and the glass is about that now.
                    return;
                }
                AssistantReply reply = result.reply;
                long shown = 1_200L;
                if (reply != null && !reply.ignored && !reply.reply.isEmpty()) {
                    shown = reply.millis();
                    GlassCaption.show(new GlassCaption.Caption(
                            KIND_NOTICE, reply.reply, "", reply.details, shown,
                            "good-night".equals(id) ? GlassCaption.MOOD_SLEEP : GlassCaption.MOOD_GREET));
                }
                if (then != null) {
                    afterShown = then;
                    handler.postDelayed(then, shown);
                }
            });
        }, "mirror-greeting").start();
    }

    /**
     * Whoever speaks again has not left: what was to follow the last answer
     * is called off, and a greeting still on its way is no longer shown.
     *
     * @return the count of what was said, for a greeting to know itself by
     */
    int cancelAfterShown() {
        int now = spoken.incrementAndGet();
        handler.post(() -> {
            if (afterShown != null) {
                handler.removeCallbacks(afterShown);
                afterShown = null;
            }
        });
        return now;
    }

    /**
     * A request typed in the controls. Waits for the companion's answer.
     *
     * @return the companion's answer as it sent it
     * @throws IOException with the reason, if the companion gave none
     */
    public JSONObject ask(String text) throws IOException {
        if (!available()) {
            throw new IOException(configStore.isAssistantEnabled()
                    ? "The assistant has no companion to ask yet"
                    : "The assistant is switched off");
        }
        cancelAfterShown();
        show(KIND_THINKING, "", THINKING_MILLIS);
        synchronized (this) {
            waiting++;
        }
        Future<Result> answer = exchanges.submit(() -> exchange(() -> postAsk(text, "controls")));
        Result result;
        try {
            result = answer.get(ANSWER_TIMEOUT_MS + 10_000L, TimeUnit.MILLISECONDS);
        } catch (InterruptedException | ExecutionException | TimeoutException error) {
            result = Result.failed("The companion did not answer in time");
        }
        finish("controls", null, text, result);
        if (result.reply == null) {
            throw new IOException(result.failure);
        }
        try {
            return new JSONObject(result.raw);
        } catch (JSONException impossible) {
            throw new IOException("The companion's answer could not be read");
        }
    }

    /**
     * Shows a line on the glass for the companion.
     *
     * @param millis how long; 0 for as long as its length needs
     * @return whether the glass shows it: a dark Mirror shows nothing
     */
    public boolean say(String text, String kind, long millis, java.util.List<GlassCaption.Row> details) {
        if (AutomationManager.getInstance(context).isSleeping()) {
            return false;
        }
        if (KIND_HEARD.equals(kind)) {
            show(KIND_HEARD, text, HEARD_MILLIS);
        } else {
            GlassCaption.show(new GlassCaption.Caption(
                    KIND_NOTICE.equals(kind) ? KIND_NOTICE : KIND_REPLY,
                    text,
                    "",
                    details,
                    millis > 0 ? millis : AssistantReply.showMillis(text, details)));
        }
        return true;
    }

    /** Someone came before a Mirror that was dark because nobody was there. */
    void presence(long asleepSeconds) {
        try {
            event("presence", new JSONObject().put("asleepSeconds", asleepSeconds));
        } catch (JSONException impossible) {
            // A number always fits.
        }
    }

    /** Where the assistant stands, in brief: for the status, which the controls ask for every few seconds. */
    /**
     * Chooses the character that the Mirror answers as, and has a new one
     * say hello on the glass, so that whoever chose it sees what they chose.
     *
     * @param id a mascot's id, or {@link Mascot#NONE}
     * @throws IllegalArgumentException if there is no such mascot
     */
    public void chooseMascot(String id) {
        if (!Mascot.known(id)) {
            StringBuilder known = new StringBuilder(Mascot.NONE);
            for (Mascot mascot : Mascot.all()) {
                known.append(", ").append(mascot.id);
            }
            throw new IllegalArgumentException("mascot must be one of: " + known);
        }
        boolean changed = !id.equals(configStore.getMascot());
        configStore.setMascot(id);
        Mascot chosen = Mascot.byId(id);
        if (changed && chosen != null && !AutomationManager.getInstance(context).isSleeping()) {
            GlassCaption.show(new GlassCaption.Caption(
                    KIND_NOTICE, chosen.name, "", null, 4_500L, GlassCaption.MOOD_GREET));
        }
    }

    /**
     * Moves the Mirror's answers to another place on the glass, and shows a
     * line there, so that whoever moved them sees where they went.
     */
    public void choosePlace(PanelPlace place) {
        boolean changed = !place.equals(configStore.getPanelPlace());
        configStore.setPanelPlace(place);
        if (changed && !AutomationManager.getInstance(context).isSleeping()) {
            GlassCaption.show(new GlassCaption.Caption(KIND_NOTICE, PLACE_NOTICE, "", null, 4_000L));
        }
    }

    /** Where the assistant stands, in brief: for the status, which the controls ask for every few seconds. */
    public JSONObject summary() throws JSONException {
        return new JSONObject().put("enabled", configStore.isAssistantEnabled()).put("state", state());
    }

    public synchronized JSONObject snapshot() throws JSONException {
        String state = state();
        return new JSONObject()
                .put("enabled", configStore.isAssistantEnabled())
                .put("address", configStore.getAssistantAddress())
                .put("keySet", !configStore.getAssistantKey().isEmpty())
                .put("state", state)
                .put("detail", describe(state))
                .put("model", companionModel)
                .put("mascot", configStore.getMascot())
                .put("mascots", mascots())
                .put("place", configStore.getPanelPlace().toJson())
                .put("places", PanelPlace.choices())
                .put("busy", waiting > 0)
                .put("lastAnswerAt", lastAnswerAt == 0 ? JSONObject.NULL : lastAnswerAt)
                .put("counts", new JSONObject()
                        .put("requests", requests)
                        .put("ignored", ignored)
                        .put("failures", failures))
                .put("recent", new JSONArray(recent));
    }

    /** The same for the health report, without what was said: that report is passed around. */
    private static JSONArray mascots() throws JSONException {
        JSONArray list = new JSONArray();
        for (Mascot mascot : Mascot.all()) {
            list.put(new JSONObject().put("id", mascot.id).put("name", mascot.name));
        }
        return list;
    }

    public JSONObject diagnostics() throws JSONException {
        JSONObject report = snapshot();
        report.remove("recent");
        return report;
    }

    /** One word for where the assistant stands. */
    private synchronized String state() {
        if (!configStore.isAssistantEnabled()) {
            return "off";
        }
        if (configStore.getAssistantAddress().isEmpty() || configStore.getAssistantKey().isEmpty()) {
            return "unconfigured";
        }
        if (lastAnswerAt == 0 && lastFailureAt == 0) {
            return "connecting";
        }
        if (lastFailureAt > lastAnswerAt) {
            return "unreachable";
        }
        return companionOk ? "connected" : "trouble";
    }

    private String describe(String state) {
        switch (state) {
            case "off":
                return "The assistant is switched off";
            case "unconfigured":
                return "No companion has been set";
            case "connecting":
                return "Looking for the companion";
            case "unreachable":
                return lastFailure.isEmpty() ? "The companion does not answer" : lastFailure;
            case "trouble":
                return companionDetail.isEmpty() ? "The companion reports a problem" : companionDetail;
            default:
                return "Connected";
        }
    }

    // ---- one exchange with the companion

    private interface Request {
        String send() throws IOException;
    }

    private static final class Result {
        final AssistantReply reply;
        final String raw;
        final String failure;
        final long millis;

        private Result(AssistantReply reply, String raw, String failure, long millis) {
            this.reply = reply;
            this.raw = raw;
            this.failure = failure;
            this.millis = millis;
        }

        static Result failed(String failure) {
            return new Result(null, null, failure, 0);
        }
    }

    private Result exchange(Request request) {
        long began = SystemClock.elapsedRealtime();
        try {
            String raw = request.send();
            return new Result(AssistantReply.parse(raw), raw, null, SystemClock.elapsedRealtime() - began);
        } catch (IOException error) {
            Log.w(TAG, "The companion gave no answer: " + error.getMessage());
            return Result.failed(reason(error));
        } catch (JSONException unreadable) {
            return Result.failed("The companion's answer could not be read");
        }
    }

    /**
     * Keeps what came of a request for the controls, and shows it. Any thread.
     *
     * @param typed the request's words where the Mirror knows them itself; else empty
     */
    private void finish(String source, VoiceManager voice, String typed, Result result) {
        AssistantReply reply = result.reply;
        // At once: whoever asked through the controls reads the state next.
        record(source, typed, result);
        handler.post(() -> {
            if (reply == null) {
                GlassCaption.show(
                        KIND_NOTICE, "The assistant isn\u2019t answering", 5_000L, GlassCaption.MOOD_SORRY);
                return;
            }
            if (reply.ignored || reply.reply.isEmpty()) {
                show(KIND_CLEAR, "", 0);
                return;
            }
            AutomationManager automation = AutomationManager.getInstance(context);
            if (automation.isSleeping() && !reply.acted.contains("set_power")) {
                // Someone asked and was answered: the answer has to be seen.
                automation.wakeForPresence();
            }
            // With what was understood above it, so that a mishearing can be seen for what it is.
            GlassCaption.show(new GlassCaption.Caption(
                    KIND_REPLY,
                    reply.reply,
                    reply.heard.isEmpty() ? typed : reply.heard,
                    reply.details,
                    reply.millis(),
                    reply.listen ? GlassCaption.MOOD_CURIOUS : ""));
            if (reply.listen && voice != null) {
                voice.awaitAnswer();
            }
        });
    }

    /** Counts a request and keeps what came of it for the controls. Any thread. */
    private void record(String source, String typed, Result result) {
        long now = System.currentTimeMillis();
        AssistantReply reply = result.reply;
        synchronized (this) {
            waiting = Math.max(0, waiting - 1);
            requests++;
            if (reply == null) {
                failures++;
                lastFailureAt = now;
                lastFailure = result.failure;
            } else {
                lastAnswerAt = now;
                companionOk = true;
                if (reply.ignored) {
                    ignored++;
                }
            }
            try {
                recent.addLast(new JSONObject()
                        .put("at", now)
                        .put("source", source)
                        .put("heard", reply == null || reply.heard.isEmpty() ? typed : reply.heard)
                        .put("reply", reply == null ? "" : reply.reply)
                        .put("rows", reply == null ? 0 : reply.details.size())
                        .put("ignored", reply != null && reply.ignored)
                        .put("did", reply == null ? "" : reply.acted)
                        .put("millis", result.millis)
                        .put("error", reply == null ? result.failure : JSONObject.NULL));
            } catch (JSONException impossible) {
                // Strings and numbers always fit.
            }
            while (recent.size() > MAX_RECENT) {
                recent.removeFirst();
            }
        }
    }

    private void show(String kind, String text, long millis) {
        GlassCaption.show(kind, text, millis);
    }

    private void event(String type, JSONObject more) {
        if (!available()) {
            return;
        }
        side.execute(() -> {
            try {
                JSONObject body = more == null ? new JSONObject() : more;
                body.put("type", type).put("at", System.currentTimeMillis());
                send("POST", "/v1/event", "application/json",
                        body.toString().getBytes(StandardCharsets.UTF_8), null, null, SIDE_TIMEOUT_MS);
            } catch (IOException | JSONException error) {
                Log.i(TAG, "The companion was not told of " + type + ": " + error.getMessage());
            }
        });
    }

    private void checkHealth() {
        try {
            JSONObject health = new JSONObject(send("GET", "/v1/health", null, null, null, null, SIDE_TIMEOUT_MS));
            boolean ok = health.optBoolean("ok", false);
            String detail = "";
            for (String part : new String[]{"brain", "stt", "mirror"}) {
                JSONObject report = health.optJSONObject(part);
                if (report != null && !report.optBoolean(part.equals("mirror") ? "reachable" : "ready", true)) {
                    detail = "The companion\u2019s " + label(part) + " is not ready"
                            + (report.optString("detail", "").isEmpty()
                                    ? "" : ": " + AssistantReply.oneLine(report.optString("detail")));
                    break;
                }
            }
            synchronized (this) {
                lastAnswerAt = System.currentTimeMillis();
                companionOk = ok;
                companionDetail = detail;
                companionModel = AssistantReply.oneLine(health.optString("model", ""));
            }
        } catch (IOException | JSONException error) {
            synchronized (this) {
                lastFailureAt = System.currentTimeMillis();
                lastFailure = error instanceof IOException
                        ? reason((IOException) error)
                        : "What answers at that address is not a companion";
            }
        }
    }

    private static String label(String part) {
        switch (part) {
            case "brain":
                return "model";
            case "stt":
                return "speech recognition";
            default:
                return "way to this Mirror";
        }
    }

    private String postUtterance(File wav, String addressed, String id) throws IOException {
        byte[] sound;
        try (FileInputStream input = new FileInputStream(wav)) {
            sound = readAll(input, VoiceService.SAMPLE_RATE * 2 * VoiceService.RING_SECONDS + 1024);
        }
        return send("POST", "/v1/utterance", "audio/wav", sound, addressed, id, ANSWER_TIMEOUT_MS);
    }

    private String postAsk(String text, String source) throws IOException {
        return postAsk(text, source, null, ANSWER_TIMEOUT_MS);
    }

    private String postAsk(String text, String source, String shortcut, int timeoutMs) throws IOException {
        try {
            JSONObject request = new JSONObject().put("text", text).put("source", source);
            if (shortcut != null) {
                request.put("shortcut", shortcut);
            }
            byte[] body = request.toString().getBytes(StandardCharsets.UTF_8);
            return send("POST", "/v1/ask", "application/json", body, null, null, timeoutMs);
        } catch (JSONException impossible) {
            throw new IOException("The request could not be written");
        }
    }

    /** One request to the companion; a companion that cannot be reached at first is tried once more. */
    private String send(
            String method,
            String path,
            String contentType,
            byte[] body,
            String addressed,
            String id,
            int answerTimeoutMs) throws IOException {
        String address = configStore.getAssistantAddress();
        String key = configStore.getAssistantKey();
        if (address.isEmpty() || key.isEmpty()) {
            throw new IOException("No companion has been set");
        }
        IOException last = null;
        for (int attempt = 0; attempt < 2; attempt++) {
            HttpURLConnection connection = (HttpURLConnection) new URL(address + path).openConnection();
            try {
                connection.setConnectTimeout(CONNECT_TIMEOUT_MS);
                connection.setReadTimeout(answerTimeoutMs);
                connection.setRequestMethod(method);
                connection.setInstanceFollowRedirects(false);
                connection.setRequestProperty("Authorization", "Bearer " + key);
                connection.setRequestProperty("Accept", "application/json");
                if (addressed != null) {
                    connection.setRequestProperty("X-Mirror-Addressed", addressed);
                }
                if (id != null) {
                    connection.setRequestProperty("X-Mirror-Utterance", id);
                }
                if (body != null) {
                    connection.setDoOutput(true);
                    connection.setRequestProperty("Content-Type", contentType);
                    connection.setFixedLengthStreamingMode(body.length);
                }
                try {
                    connection.connect();
                } catch (IOException unreachable) {
                    // Nothing has been sent, so asking again asks only once.
                    last = unreachable;
                    continue;
                }
                if (body != null) {
                    try (OutputStream output = connection.getOutputStream()) {
                        output.write(body);
                    }
                }
                int status = connection.getResponseCode();
                InputStream stream = status >= 400 ? connection.getErrorStream() : connection.getInputStream();
                String answer = stream == null
                        ? ""
                        : new String(readAll(stream, MAX_ANSWER_BYTES), StandardCharsets.UTF_8);
                if (status == 401) {
                    throw new IOException("The companion does not accept this Mirror\u2019s key");
                }
                if (status == 429) {
                    throw new IOException("The companion is busy");
                }
                if (status < 200 || status >= 300) {
                    throw new IOException("The companion answered " + status);
                }
                return answer;
            } finally {
                connection.disconnect();
            }
        }
        throw last;
    }

    private static byte[] readAll(InputStream input, int limit) throws IOException {
        ByteArrayOutputStream output = new ByteArrayOutputStream();
        byte[] buffer = new byte[8192];
        int count;
        while ((count = input.read(buffer)) != -1) {
            output.write(buffer, 0, count);
            if (output.size() > limit) {
                throw new IOException("More was sent than is taken");
            }
        }
        return output.toByteArray();
    }

    /** Why the companion gave no answer, in words for the controls. */
    static String reason(IOException error) {
        if (error instanceof java.net.SocketTimeoutException) {
            return "The companion did not answer in time";
        }
        if (error instanceof java.net.ConnectException
                || error instanceof java.net.NoRouteToHostException
                || error instanceof java.net.UnknownHostException) {
            return "The companion cannot be reached at its address";
        }
        String message = error.getMessage();
        return message != null && message.startsWith("The companion")
                ? message
                : "The connection to the companion failed";
    }
}
