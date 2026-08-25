package dev.mirror.repurpose;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.security.SecureRandom;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;

/* The notes people leave for the mirror, newest first. Pure Java so the rules
   are unit-testable; NoteStore handles persistence. Notes are content, not
   layout: note widgets refer to them by source (latest, rotate, list, pinned)
   so posting a note never requires re-saving the layout. */
public final class NoteBook {
    public static final int MAX_TEXT_LENGTH = 1000;
    public static final int MAX_NOTES = 50;
    private static final String ID_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789";
    private static final int ID_LENGTH = 8;

    private final List<JSONObject> notes = new ArrayList<>();
    private final SecureRandom random = new SecureRandom();

    public NoteBook() {
    }

    public static NoteBook parse(String serialized) {
        NoteBook book = new NoteBook();
        if (serialized == null || serialized.isEmpty()) {
            return book;
        }
        try {
            JSONArray array = new JSONArray(serialized);
            for (int index = 0; index < array.length(); index++) {
                JSONObject note = array.optJSONObject(index);
                if (note == null || !validId(note.optString("id", ""))) {
                    continue;
                }
                String text = note.optString("text", "");
                if (!validText(text)) {
                    continue;
                }
                book.notes.add(new JSONObject()
                        .put("id", note.getString("id"))
                        .put("text", text)
                        .put("createdAt", note.optLong("createdAt", 0))
                        .put("updatedAt", note.optLong("updatedAt", note.optLong("createdAt", 0))));
                if (book.notes.size() >= MAX_NOTES) {
                    break;
                }
            }
        } catch (JSONException ignored) {
            // A corrupt store yields an empty book rather than a crash.
        }
        return book;
    }

    public String serialize() {
        return toJson().toString();
    }

    public JSONArray toJson() {
        JSONArray array = new JSONArray();
        for (JSONObject note : notes) {
            array.put(copy(note));
        }
        return array;
    }

    public int size() {
        return notes.size();
    }

    public JSONObject find(String id) {
        int index = indexOf(id);
        return index < 0 ? null : copy(notes.get(index));
    }

    public JSONObject add(String text, long now) {
        String normalized = normalizeText(text);
        if (notes.size() >= MAX_NOTES) {
            throw new IllegalArgumentException(
                    "The mirror already holds " + MAX_NOTES + " notes");
        }
        try {
            JSONObject note = new JSONObject()
                    .put("id", newId())
                    .put("text", normalized)
                    .put("createdAt", now)
                    .put("updatedAt", now);
            notes.add(0, note);
            return copy(note);
        } catch (JSONException impossible) {
            throw new IllegalStateException("Unable to create note", impossible);
        }
    }

    public JSONObject update(String id, String text, long now) {
        String normalized = normalizeText(text);
        int index = indexOf(id);
        if (index < 0) {
            return null;
        }
        try {
            JSONObject note = notes.get(index);
            note.put("text", normalized);
            note.put("updatedAt", now);
            return copy(note);
        } catch (JSONException impossible) {
            throw new IllegalStateException("Unable to update note", impossible);
        }
    }

    public boolean delete(String id) {
        int index = indexOf(id);
        if (index < 0) {
            return false;
        }
        notes.remove(index);
        return true;
    }

    /* Trims outer whitespace, normalizes line endings, and rejects anything the
       mirror cannot show: empty notes, control characters, or text past the cap. */
    public static String normalizeText(String text) {
        if (text == null) {
            throw new IllegalArgumentException("A note needs some text");
        }
        String normalized = text.replace("\r\n", "\n").replace('\r', '\n').trim();
        if (normalized.isEmpty()) {
            throw new IllegalArgumentException("A note needs some text");
        }
        if (normalized.length() > MAX_TEXT_LENGTH) {
            throw new IllegalArgumentException(
                    "Notes can hold up to " + MAX_TEXT_LENGTH + " characters");
        }
        if (containsControlCharacter(normalized)) {
            throw new IllegalArgumentException("Notes cannot contain control characters");
        }
        return normalized;
    }

    public static boolean validText(String text) {
        return text != null
                && !text.trim().isEmpty()
                && text.length() <= MAX_TEXT_LENGTH
                && !containsControlCharacter(text);
    }

    public static boolean validId(String id) {
        return id != null && id.matches("[a-z0-9][a-z0-9-]{0,39}");
    }

    static boolean containsControlCharacter(String value) {
        for (int index = 0; index < value.length(); index++) {
            char character = value.charAt(index);
            if (Character.isISOControl(character) && character != '\n') {
                return true;
            }
        }
        return false;
    }

    private int indexOf(String id) {
        if (id == null) {
            return -1;
        }
        for (int index = 0; index < notes.size(); index++) {
            if (id.equals(notes.get(index).optString("id"))) {
                return index;
            }
        }
        return -1;
    }

    private String newId() {
        while (true) {
            StringBuilder builder = new StringBuilder(ID_LENGTH);
            for (int index = 0; index < ID_LENGTH; index++) {
                builder.append(ID_ALPHABET.charAt(random.nextInt(ID_ALPHABET.length())));
            }
            String candidate = builder.toString().toLowerCase(Locale.US);
            if (indexOf(candidate) < 0) {
                return candidate;
            }
        }
    }

    private static JSONObject copy(JSONObject note) {
        try {
            return new JSONObject(note.toString());
        } catch (JSONException impossible) {
            throw new IllegalStateException("Stored note is invalid", impossible);
        }
    }
}
