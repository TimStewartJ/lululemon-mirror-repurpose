package dev.mirror.repurpose;

import android.content.Context;
import android.content.SharedPreferences;

import org.json.JSONArray;
import org.json.JSONObject;

/* Persists the NoteBook and a version counter the dashboard runtime exposes so
   the glass and the control app only re-fetch notes when something changed. */
public final class NoteStore {
    private static final String PREFERENCES = "mirror_home_notes";
    private static final String KEY_NOTES = "notes";
    private static final String KEY_VERSION = "notes_version";

    private static volatile NoteStore instance;

    private final SharedPreferences preferences;
    private NoteBook book;

    private NoteStore(Context context) {
        preferences = context.getApplicationContext()
                .getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE);
        book = NoteBook.parse(preferences.getString(KEY_NOTES, ""));
    }

    public static NoteStore getInstance(Context context) {
        if (instance == null) {
            synchronized (NoteStore.class) {
                if (instance == null) {
                    instance = new NoteStore(context);
                }
            }
        }
        return instance;
    }

    public synchronized long version() {
        return preferences.getLong(KEY_VERSION, 0);
    }

    public synchronized JSONArray list() {
        return book.toJson();
    }

    public synchronized JSONObject find(String id) {
        return book.find(id);
    }

    public synchronized JSONObject add(String text) {
        JSONObject note = book.add(text, System.currentTimeMillis());
        persist();
        return note;
    }

    public synchronized JSONObject update(String id, String text) {
        JSONObject note = book.update(id, text, System.currentTimeMillis());
        if (note != null) {
            persist();
        }
        return note;
    }

    public synchronized boolean delete(String id) {
        boolean deleted = book.delete(id);
        if (deleted) {
            persist();
        }
        return deleted;
    }

    private void persist() {
        preferences.edit()
                .putString(KEY_NOTES, book.serialize())
                .putLong(KEY_VERSION, version() + 1)
                .apply();
    }
}
