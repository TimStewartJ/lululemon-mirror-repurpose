package dev.mirror.repurpose;

import android.content.Context;
import android.content.SharedPreferences;

import java.util.Map;

/* Keeps the board across restarts and lets one request at a time at it.
   The rules live in BoardItems and the HTTP shape in BoardApi; this only
   saves what they changed. */
public final class BoardStore {
    private static final String PREFERENCES = "mirror_home_board";
    private static final String KEY_BOARD = "board";

    private static volatile BoardStore instance;

    private final SharedPreferences preferences;
    private final BoardItems items;
    private final BoardApi api;

    private BoardStore(Context context) {
        preferences = context.getApplicationContext()
                .getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE);
        items = BoardItems.parse(preferences.getString(KEY_BOARD, ""));
        api = new BoardApi(items);
    }

    public static BoardStore getInstance(Context context) {
        if (instance == null) {
            synchronized (BoardStore.class) {
                if (instance == null) {
                    instance = new BoardStore(context);
                }
            }
        }
        return instance;
    }

    /** The counter the glass and the controls watch; expiring an item moves it too. */
    public synchronized long version() {
        items.expire(System.currentTimeMillis());
        save();
        return items.version();
    }

    public synchronized BoardApi.Reply handle(
            String method,
            String uri,
            Map<String, String> query,
            BoardApi.BodyReader body,
            BoardApi.Caller caller,
            String glassNotice) {
        try {
            return api.handle(
                    method, uri, query, body, caller, glassNotice, System.currentTimeMillis());
        } finally {
            save();
        }
    }

    private void save() {
        if (items.takeUnsaved()) {
            preferences.edit().putString(KEY_BOARD, items.serialize()).apply();
        }
    }
}
