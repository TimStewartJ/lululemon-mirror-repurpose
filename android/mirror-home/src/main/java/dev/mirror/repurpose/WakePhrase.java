package dev.mirror.repurpose;

/** A complete leading phrase, never a name found partway through talk. */
final class WakePhrase {
    static final int MAX_LENGTH = 64;

    private WakePhrase() {
    }

    static String normalize(String phrase) {
        String normalized = VoiceCommands.normalize(phrase);
        if (normalized.length() > MAX_LENGTH
                || !normalized.matches("[a-z]+('[a-z]+)?( [a-z]+('[a-z]+)?){0,3}")) {
            throw new IllegalArgumentException(
                    "wakePhrase must be 1 to 4 English words, at most 64 characters");
        }
        return normalized;
    }

    static String defaultFor(String mascotName) {
        return "hey " + VoiceCommands.normalize(
                mascotName == null || mascotName.isEmpty() ? "mirror" : mascotName);
    }

    /** Number of leading words occupied by one or more complete repetitions. */
    static int leadingWords(String text, String phrase) {
        String[] words = VoiceCommands.normalize(text).split(" ");
        String[] wake = phrase.split(" ");
        int end = 0;
        while (end + wake.length <= words.length) {
            int index = 0;
            while (index < wake.length && wake[index].equals(words[end + index])) {
                index++;
            }
            if (index != wake.length) {
                break;
            }
            end += wake.length;
            // A custom phrase may also be the first word of a command ("wake wake up").
            if (VoiceCommands.forWording(join(words, end)) != null) {
                break;
            }
        }
        return end;
    }

    static String withoutLeading(String text, String phrase) {
        String normalized = VoiceCommands.normalize(text);
        int count = leadingWords(normalized, phrase);
        return join(normalized.split(" "), count);
    }

    private static String join(String[] words, int from) {
        StringBuilder rest = new StringBuilder();
        for (int index = from; index < words.length; index++) {
            rest.append(rest.length() == 0 ? "" : " ").append(words[index]);
        }
        return rest.toString();
    }
}
