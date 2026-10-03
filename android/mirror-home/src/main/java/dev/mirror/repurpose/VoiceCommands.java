package dev.mirror.repurpose;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;

/**
 * What can be said to a Mirror, and in which words. The recogniser is told to
 * expect exactly these sentences, which is what makes recognition on a 2015
 * processor dependable: on one core a Mirror recognises unbroken speech in a
 * third of the time it lasts, and still understands in noise that defeats
 * open dictation.
 *
 * <p>Wordings of different commands must not sound alike. "Turn on" and
 * "turn off" were tried and taken for each other in noise, which for sleep
 * and wake is the worst mistake there is.
 */
final class VoiceCommands {
    /** Said first, so that talk in the room is not taken for a command. */
    static final String WAKE_WORD = "mirror";
    /** What the recogniser puts out for speech that is not on the list. */
    static final String UNKNOWN = "[unk]";
    /**
     * Words that belong to no command. A recogniser that knows only the
     * commands' words makes them of whatever it hears: on a Mirror in a room
     * with sound in it, it heard the name alone about eight times an hour.
     * With the commonest words of spoken English to choose from as well, it
     * has something better to take talk for, and Mirror Home drops them
     * unread. In four hours of people reading aloud this cut what was taken
     * for speech to the Mirror (its name alone, its name with other words, or
     * a command in doubt) from 23 stretches to 3, and cost two commands in a
     * hundred across a room and none nearer.
     *
     * <p>No word here may sound like the name or like a command's word:
     * "nearer", "dinner" and their like were tried too, and cost one command
     * in five across a room. Every word must be in the speech model's
     * vocabulary, or the recogniser complains about it each time it starts.
     */
    private static final String OTHER_WORDS =
            "be of and a in that have i it for not on with he as you do at this but his by from "
            + "they we say her she or an will my one all would there their what so out if about who "
            + "get which me when make can like time no just him know take people into year your "
            + "some could them see other than then now look only come its over think also back "
            + "after use two how our work first well way even new want because any these give day "
            + "most us is was are were been has had did said got going really yeah okay oh right "
            + "very much more here where why too little thing things something anything nothing man "
            + "woman house home water today tomorrow yes please thank thanks off again still never "
            + "always every many long great old big small put tell told ask asked need feel left am "
            + "being does done doing came went gone goes made makes taking took taken says saying "
            + "seen saw sees gave given gets getting let lets may must shall should cannot can't "
            + "don't didn't doesn't won't wouldn't couldn't isn't aren't wasn't i'm i'll i've it's "
            + "that's there's what's he's she's we're they're you're you've we'll they'll let's "
            + "mister missus miss sir lady boy girl child children father mother brother sister "
            + "friend friends family world life hand hands eyes face head heart mind body door room "
            + "place city country school money food name word words question answer story book "
            + "books number part side end kind lot bit week month years days hour hours minute "
            + "minutes moment times once twice three four five six seven eight nine ten hundred "
            + "thousand last few both each such own same another those through between under before "
            + "since while until against without within around across along away together upon "
            + "above below behind beside toward almost quite rather ever often sometimes already "
            + "yet soon perhaps maybe sure true best better less least enough high low young open "
            + "close start stop run ran walk talk speak call called found find leave live lived "
            + "love help show turn turned move bring brought buy pay play read hear heard believe "
            + "remember understand seem seemed mean happy sorry hard easy early late full black red "
            + "green blue cold hot dead poor rich whole real";

    enum Command {
        SLEEP("sleep", "Sleeping", "go to sleep"),
        WAKE("wake", "Awake", "wake up"),
        BRIGHTER("brighter", "Brighter", "brighter", "brightness up"),
        DIMMER("dimmer", "Dimmer", "dimmer", "brightness down"),
        NEXT_VIDEO("next-video", "Next video", "next video", "change the video"),
        // Greetings. Each wakes or darkens the Mirror as its hour suggests, and
        // with an assistant it is answered with where things stand.
        GOOD_MORNING("good-morning", "Good morning", "good morning"),
        GOOD_AFTERNOON("good-afternoon", "Good afternoon", "good afternoon"),
        GOOD_EVENING("good-evening", "Good evening", "good evening"),
        GOOD_NIGHT("good-night", "Good night", "good night"),
        HOME("home", "Welcome home", "i'm home", "i'm back");

        /** Name in the API and the health report. */
        final String id;
        /** Shown on the glass when the command is carried out. */
        final String caption;
        private final String[] wordings;

        Command(String id, String caption, String... wordings) {
            this.id = id;
            this.caption = caption;
            this.wordings = wordings;
        }

        List<String> wordings() {
            return Collections.unmodifiableList(java.util.Arrays.asList(wordings));
        }

        /** Whether this is a greeting, which an assistant answers with where things stand. */
        boolean greets() {
            return this == GOOD_MORNING || this == GOOD_AFTERNOON || this == GOOD_EVENING
                    || this == GOOD_NIGHT || this == HOME;
        }
    }

    private static final Map<String, Command> BY_WORDING = new LinkedHashMap<>();
    private static final List<String> COMMAND_WORDS = new ArrayList<>();

    static {
        COMMAND_WORDS.add(WAKE_WORD);
        for (Command command : Command.values()) {
            for (String wording : command.wordings) {
                if (BY_WORDING.put(wording, command) != null) {
                    throw new IllegalStateException("Two commands share the wording " + wording);
                }
                for (String word : wording.split(" ")) {
                    if (!COMMAND_WORDS.contains(word)) {
                        COMMAND_WORDS.add(word);
                    }
                }
            }
        }
    }

    private VoiceCommands() {
    }

    /** The command a wording stands for, without the wake word; null if none. */
    static Command forWording(String wording) {
        return BY_WORDING.get(normalize(wording));
    }

    static String normalize(String text) {
        return text == null ? "" : text.trim().toLowerCase(Locale.ROOT).replaceAll("\\s+", " ");
    }

    /**
     * The sentences the recogniser is to expect, as the JSON list it takes:
     * each wording after the wake word, each wording alone (for a command that
     * follows the wake word after a pause), the wake word alone, the words
     * that talk is better taken for, and anything else.
     */
    static String grammar() {
        JSONArray sentences = new JSONArray();
        for (String wording : BY_WORDING.keySet()) {
            sentences.put(WAKE_WORD + " " + wording);
        }
        for (String wording : BY_WORDING.keySet()) {
            sentences.put(wording);
        }
        sentences.put(WAKE_WORD);
        for (String word : otherWords()) {
            sentences.put(word);
        }
        sentences.put(UNKNOWN);
        return sentences.toString();
    }

    /** The wake word and every word of the commands, wake word first. */
    static List<String> words() {
        return Collections.unmodifiableList(COMMAND_WORDS);
    }

    /** The words that are there so that talk is not taken for a command. */
    static List<String> otherWords() {
        List<String> others = new ArrayList<>();
        for (String word : OTHER_WORDS.split(" ")) {
            // A word of a command is no longer something else to take talk for.
            if (!COMMAND_WORDS.contains(word)) {
                others.add(word);
            }
        }
        return Collections.unmodifiableList(others);
    }

    /**
     * A sentence with every word that is no part of the commands made
     * unknown, which is all of it that Mirror Home ever keeps.
     */
    static String withoutOtherWords(String sentence) {
        StringBuilder kept = new StringBuilder();
        boolean unknownBefore = false;
        for (String word : normalize(sentence).split(" ")) {
            boolean unknown = !COMMAND_WORDS.contains(word);
            if (word.isEmpty() || (unknown && unknownBefore)) {
                continue;
            }
            kept.append(kept.length() == 0 ? "" : " ").append(unknown ? UNKNOWN : word);
            unknownBefore = unknown;
        }
        return kept.toString();
    }

    /** The list for the controls: what each command is called and how to say it. */
    static JSONArray describe() throws JSONException {
        JSONArray commands = new JSONArray();
        for (Command command : Command.values()) {
            JSONArray say = new JSONArray();
            for (String wording : command.wordings) {
                say.put(WAKE_WORD + " " + wording);
            }
            commands.put(new JSONObject()
                    .put("id", command.id)
                    .put("caption", command.caption)
                    .put("say", say));
        }
        return commands;
    }
}
