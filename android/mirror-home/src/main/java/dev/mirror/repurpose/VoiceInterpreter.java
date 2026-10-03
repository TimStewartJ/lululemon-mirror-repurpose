package dev.mirror.repurpose;

/**
 * Decides what a recognised sentence means. A command counts only when it is
 * addressed to the Mirror: said after the wake word in the same sentence, or
 * within a few seconds of the wake word said alone. The sentence must be the
 * command from its first word to its last, because the recogniser also puts
 * out parts of commands for other speech.
 *
 * <p>These rules come from a trial on a Mirror: a pause after "mirror" made
 * two sentences of one command, and "mirror, mirror go to sleep" was no
 * command at all.
 *
 * <p>With an assistant to pass speech on to, whatever is addressed to the
 * Mirror and is no command of its own is a request for the assistant. The
 * recogniser knows only the commands' words and makes little sense of such
 * a request, so all that is asked of it is to have heard the name for sure.
 */
final class VoiceInterpreter {
    /** How long after the wake word alone a command may follow. */
    static final long WINDOW_MS = 6_000L;
    /**
     * The least the recogniser must be sure of its least certain word. Real
     * commands arrive at 1.0; the commands it made of four hours of other
     * people's reading came with 0.6 to 0.7.
     */
    static final double MIN_CONFIDENCE = 0.8;
    /** How long an answer may take to begin after the assistant asked a question. */
    static final long ANSWER_MS = 10_000L;

    /** How a request for the assistant was addressed to the Mirror. */
    static final String BY_NAME = "name";
    static final String AFTER_NAME = "window";
    static final String ANSWER = "follow-up";

    enum Kind {
        /** A command to carry out. */
        COMMAND,
        /** The wake word alone: a command may follow. */
        WAKE,
        /** Addressed to the Mirror, but not a command it knows: it may be said again. */
        NOT_UNDERSTOOD,
        /** A command or the wake word that the recogniser was not sure of. */
        UNSURE,
        /** Addressed to the Mirror and no command of its own: for the assistant. */
        ASK,
        /** Speech that is not for the Mirror. */
        OTHER
    }

    static final class Outcome {
        final Kind kind;
        final VoiceCommands.Command command;
        /** For {@link Kind#ASK}: {@link #BY_NAME}, {@link #AFTER_NAME} or {@link #ANSWER}. */
        final String addressed;

        private Outcome(Kind kind, VoiceCommands.Command command) {
            this(kind, command, null);
        }

        private Outcome(Kind kind, VoiceCommands.Command command, String addressed) {
            this.kind = kind;
            this.command = command;
            this.addressed = addressed;
        }
    }

    private static final Outcome OTHER = new Outcome(Kind.OTHER, null);

    private long windowUntilMs = -1;
    private long answerUntilMs = -1;

    /**
     * Takes one recognised sentence.
     *
     * @param text what the recogniser heard
     * @param lowestConfidence its least certain word, from 0 to 1; NaN if it gave none
     * @param startMs when the sentence began, on the clock that {@code endMs} uses
     * @param endMs when it ended
     */
    Outcome heard(String text, double lowestConfidence, long startMs, long endMs) {
        return heard(text, lowestConfidence, Double.NaN, startMs, endMs, false);
    }

    /**
     * Takes one recognised sentence.
     *
     * @param nameConfidence how sure the recogniser is of the name the sentence
     *     begins with; NaN if it gave none
     * @param assistant whether there is an assistant to pass requests on to
     */
    Outcome heard(
            String text,
            double lowestConfidence,
            double nameConfidence,
            long startMs,
            long endMs,
            boolean assistant) {
        String normalized = VoiceCommands.normalize(text);
        if (normalized.isEmpty()) {
            return OTHER;
        }
        String[] words = normalized.split(" ");
        int wakeWords = 0;
        while (wakeWords < words.length && words[wakeWords].equals(VoiceCommands.WAKE_WORD)) {
            // Said twice, the wake word is still one.
            wakeWords++;
        }
        StringBuilder rest = new StringBuilder();
        for (int index = wakeWords; index < words.length; index++) {
            rest.append(rest.length() == 0 ? "" : " ").append(words[index]);
        }
        boolean sure = Double.isNaN(lowestConfidence) || lowestConfidence >= MIN_CONFIDENCE;
        boolean nameSure = Double.isNaN(nameConfidence)
                ? sure
                : nameConfidence >= MIN_CONFIDENCE;
        boolean windowOpen = windowOpen(startMs);
        boolean answerAwaited = assistant && answerUntilMs >= 0 && startMs <= answerUntilMs;
        if (wakeWords > 0 && rest.length() == 0) {
            if (!sure) {
                return new Outcome(Kind.UNSURE, null);
            }
            windowUntilMs = endMs + WINDOW_MS;
            return new Outcome(Kind.WAKE, null);
        }
        VoiceCommands.Command command = VoiceCommands.forWording(rest.toString());
        if (command != null && (wakeWords > 0 || windowOpen) && sure) {
            windowUntilMs = -1;
            answerUntilMs = -1;
            return new Outcome(Kind.COMMAND, command);
        }
        if (assistant) {
            // A command in doubt goes the same way: the assistant hears the sound itself.
            String addressed = wakeWords > 0
                    ? (nameSure ? BY_NAME : null)
                    : (answerAwaited ? ANSWER : (windowOpen ? AFTER_NAME : null));
            if (addressed != null) {
                windowUntilMs = -1;
                answerUntilMs = -1;
                return new Outcome(Kind.ASK, null, addressed);
            }
        }
        if (command != null && (wakeWords > 0 || windowOpen)) {
            return new Outcome(Kind.UNSURE, command);
        }
        if (wakeWords > 0 && sure && !assistant) {
            windowUntilMs = endMs + WINDOW_MS;
            return new Outcome(Kind.NOT_UNDERSTOOD, null);
        }
        return OTHER;
    }

    /** The assistant asked something: what is said next is its answer, name or no name. */
    void awaitAnswer(long nowMs) {
        answerUntilMs = nowMs + ANSWER_MS;
    }

    /** Whether a command without the wake word would count at this time. */
    boolean windowOpen(long nowMs) {
        return windowUntilMs >= 0 && nowMs <= windowUntilMs;
    }

    /** Forgets the wake word, as when listening starts afresh. */
    void reset() {
        windowUntilMs = -1;
        answerUntilMs = -1;
    }
}
