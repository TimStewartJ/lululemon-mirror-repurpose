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

    enum Kind {
        /** A command to carry out. */
        COMMAND,
        /** The wake word alone: a command may follow. */
        WAKE,
        /** Addressed to the Mirror, but not a command it knows: it may be said again. */
        NOT_UNDERSTOOD,
        /** A command or the wake word that the recogniser was not sure of. */
        UNSURE,
        /** Speech that is not for the Mirror. */
        OTHER
    }

    static final class Outcome {
        final Kind kind;
        final VoiceCommands.Command command;

        private Outcome(Kind kind, VoiceCommands.Command command) {
            this.kind = kind;
            this.command = command;
        }
    }

    private static final Outcome OTHER = new Outcome(Kind.OTHER, null);

    private long windowUntilMs = -1;

    /**
     * Takes one recognised sentence.
     *
     * @param text what the recogniser heard
     * @param lowestConfidence its least certain word, from 0 to 1; NaN if it gave none
     * @param startMs when the sentence began, on the clock that {@code endMs} uses
     * @param endMs when it ended
     */
    Outcome heard(String text, double lowestConfidence, long startMs, long endMs) {
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
        boolean windowOpen = windowOpen(startMs);
        if (wakeWords > 0 && rest.length() == 0) {
            if (!sure) {
                return new Outcome(Kind.UNSURE, null);
            }
            windowUntilMs = endMs + WINDOW_MS;
            return new Outcome(Kind.WAKE, null);
        }
        VoiceCommands.Command command = VoiceCommands.forWording(rest.toString());
        if (command != null && (wakeWords > 0 || windowOpen)) {
            if (!sure) {
                return new Outcome(Kind.UNSURE, command);
            }
            windowUntilMs = -1;
            return new Outcome(Kind.COMMAND, command);
        }
        if (wakeWords > 0 && sure) {
            windowUntilMs = endMs + WINDOW_MS;
            return new Outcome(Kind.NOT_UNDERSTOOD, null);
        }
        return OTHER;
    }

    /** Whether a command without the wake word would count at this time. */
    boolean windowOpen(long nowMs) {
        return windowUntilMs >= 0 && nowMs <= windowUntilMs;
    }

    /** Forgets the wake word, as when listening starts afresh. */
    void reset() {
        windowUntilMs = -1;
    }
}
