package dev.mirror.repurpose;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

/* How to use the board, as the Mirror itself tells it at
   GET /api/v1/board/guide. Served without a token, so a program that has
   only the Mirror's address can learn how to pair and what to send. Limits
   and choices are read from the rules in BoardItems, and BoardGuideTest
   sends every example through the real API, so this cannot describe a board
   other than the one that is running. */
public final class BoardGuide {
    private BoardGuide() {
    }

    public static JSONObject build() throws JSONException {
        return new JSONObject()
                .put("board", "The board is where programs on the home network put notes, to-dos "
                        + "and reminders for the people in front of the Mirror. You send plain "
                        + "facts about an item; the Mirror decides how to draw it, lists the items "
                        + "in a Board widget and turns the pages by itself when they do not fit.")
                .put("start", start())
                .put("requests", requests())
                .put("item", fields())
                .put("kinds", new JSONObject()
                        .put("note", "Something to read. Has no due time and cannot be done.")
                        .put("todo", "Something to do. May have a due time; done marks it finished.")
                        .put("reminder", "Something at a time. Needs due; done dismisses it."))
                .put("states", new JSONObject()
                        .put("open", "Nothing pressing.")
                        .put("soon", "Due within the next " + BoardItems.SOON_MS / 60_000L
                                + " minutes; the glass shows a countdown.")
                        .put("overdue", "Its due time has passed and it is not done.")
                        .put("done", "Finished or dismissed."))
                .put("lifetime", new JSONArray()
                        .put("Every item leaves by itself. Without expiresAt or ttlSeconds it is "
                                + "removed " + BoardItems.DEFAULT_LIFETIME_MS / 3_600_000L
                                + " hours after it was last written, or that long after it is due "
                                + "if that is later.")
                        .put("Writing the item again, with PUT or PATCH, starts those hours afresh. "
                                + "A program that keeps a list in step can rely on this: if it "
                                + "stops, its items clear within a day.")
                        .put("expiresAt or ttlSeconds sets the moment yourself. \"expiresAt\": null "
                                + "keeps the item until someone deletes it.")
                        .put("A done item stays on the glass, struck through, for "
                                + BoardItems.DONE_LINGER_MS / 60_000L + " minutes. After that it "
                                + "is off the glass but still listed here until it expires."))
                .put("order", "Items are listed, here and on the glass, in this order: overdue, "
                        + "then due soon, then the rest, then done. Within each, high priority "
                        + "before normal before low, earlier due times first, then the order they "
                        + "were posted in.")
                .put("glass", new JSONArray()
                        .put("The board appears where a person has placed a Board widget, in the "
                                + "controls under Display. GET " + BoardApi.ROOT + " says whether one "
                                + "is showing (glass.showsBoard); when none is, replies to a write "
                                + "carry a notice saying so.")
                        .put("Text is drawn as text: no HTML, Markdown or links. Keep a title to "
                                + "a few words that can be read from across a room, and put detail "
                                + "in body.")
                        .put("When the items do not fit the widget, the Mirror shows them a page "
                                + "at a time and moves on every few seconds. Nothing in a request "
                                + "controls pages."))
                .put("changes", "Every reply carries version, a counter that moves whenever the "
                        + "board's contents do; GET /api/v1/status carries it as boardVersion. Ask "
                        + "again when it has moved, for instance to learn that someone marked an "
                        + "item done.")
                .put("limits", new JSONObject()
                        .put("items", BoardItems.MAX_ITEMS)
                        .put("titleCharacters", BoardItems.MAX_TITLE_LENGTH)
                        .put("bodyCharacters", BoardItems.MAX_BODY_LENGTH)
                        .put("sourceCharacters", BoardItems.MAX_SOURCE_LENGTH)
                        .put("idCharacters", BoardItems.MAX_ID_LENGTH)
                        .put("ttlSeconds", BoardItems.MAX_TTL_SECONDS)
                        .put("requestBytes", 64 * 1024))
                .put("errors", "A refused request answers with a 4xx status and {\"error\": what "
                        + "was wrong and how to put it right, \"field\": the field at fault when "
                        + "there is one, \"guide\": this address}. 400 is a request the board "
                        + "cannot accept, 401 a missing or revoked token, 404 no such item, 405 "
                        + "a method the path does not take, 409 a full board.")
                .put("examples", examples());
    }

    private static JSONArray start() {
        return new JSONArray()
                .put("Get a token, once. Ask a person for the Mirror's six-digit pairing code: "
                        + "it is in the Pairing code widget on the glass, or a paired phone shows "
                        + "one from the controls at http://MIRROR:8787/ under Settings > Paired "
                        + "devices > Show code. Then POST /api/v1/pair with {\"code\": \"123456\", "
                        + "\"name\": \"what to call you\"} and keep \"token\" from the reply. It "
                        + "lasts until someone revokes it.")
                .put("Send the token with every request as the header \"Authorization: Bearer "
                        + "TOKEN\". The Mirror speaks plain HTTP on the home network; there is no "
                        + "HTTPS. A request body is one JSON object in UTF-8; the Content-Type "
                        + "header is not examined.")
                .put("Post an item: POST " + BoardApi.ITEMS_PATH + " with {\"title\": \"Water the "
                        + "plants\"}. Only title is required.")
                .put("To keep something of your own up to date, choose its id and PUT "
                        + BoardApi.ITEMS_PATH + "/{id} each time: the same request creates it and "
                        + "later replaces it, so repeating it never makes a second copy.")
                .put("Read " + BoardApi.ROOT + " to see what the glass lists, and "
                        + BoardApi.ITEMS_PATH + " for everything on the board.");
    }

    private static JSONArray requests() throws JSONException {
        String items = BoardApi.ITEMS_PATH;
        return new JSONArray()
                .put(request("GET", BoardApi.GUIDE_PATH, false, "This guide."))
                .put(request("GET", BoardApi.ROOT, true,
                        "What the glass lists now, in order, with counts by state and whether a "
                                + "Board widget is showing."))
                .put(request("GET", items, true,
                        "Everything on the board, including done items that have left the glass. "
                                + "Narrow it with ?kind=, ?source= and ?done=true|false; page "
                                + "through it with ?limit= (1 to " + BoardItems.MAX_ITEMS
                                + ", default " + BoardApi.DEFAULT_LIMIT + ") and ?offset=, "
                                + "following nextOffset until it is null."))
                .put(request("POST", items, true,
                        "Adds an item under an id the Mirror picks. Answers 201 with the item."))
                .put(request("GET", items + "/{id}", true, "One item."))
                .put(request("PUT", items + "/{id}", true,
                        "Creates the item with the id you chose, or replaces it whole: a field "
                                + "you leave out goes back to its default, done included. Answers "
                                + "201 when it created, 200 when it replaced."))
                .put(request("PATCH", items + "/{id}", true,
                        "Changes the fields you send and keeps the rest. {\"done\": true} marks "
                                + "an item done."))
                .put(request("DELETE", items + "/{id}", true, "Removes one item."))
                .put(request("DELETE", items, true,
                        "Removes several: ?source=NAME for everything one sender posted, "
                                + "?kind=, ?done=true, a combination, or ?all=true for the whole "
                                + "board. Refused without any of these."));
    }

    private static JSONObject request(String method, String path, boolean token, String does)
            throws JSONException {
        return new JSONObject()
                .put("method", method)
                .put("path", path)
                .put("needsToken", token)
                .put("does", does);
    }

    private static JSONArray fields() throws JSONException {
        String time = "A time: " + BoardTime.formats() + ". Answers give milliseconds, and the "
                + "same moment as text in a field ending in Iso; nowIso in a listing is the "
                + "Mirror's clock.";
        return new JSONArray()
                .put(field("title", "text", true, null,
                        "The line the Mirror shows. Required; one line of up to "
                                + BoardItems.MAX_TITLE_LENGTH + " characters."))
                .put(field("kind", "text", true, BoardItems.KIND_NOTE,
                        "What the item is: " + BoardItems.joined(BoardItems.KINDS)
                                + ". See kinds."))
                .put(field("body", "text", true, "",
                        "Detail shown smaller under the title. Up to " + BoardItems.MAX_BODY_LENGTH
                                + " characters; line breaks are kept."))
                .put(field("due", "time or null", true, JSONObject.NULL,
                        "When the item is due. " + time + " Required for a reminder, optional "
                                + "for a todo, not allowed on a note."))
                .put(field("done", "true or false", true, Boolean.FALSE,
                        "Whether a todo is finished or a reminder dismissed."))
                .put(field("priority", "text", true, "normal",
                        "One of " + BoardItems.joined(BoardItems.PRIORITIES) + ". High is listed "
                                + "first and drawn stronger; low is drawn fainter."))
                .put(field("expiresAt", "time or null", true, "see lifetime",
                        "When the Mirror removes the item. " + time + " null, or \"never\", keeps "
                                + "it until it is deleted."))
                .put(field("ttlSeconds", "whole number", true, null,
                        "Another way to say expiresAt: seconds from now, 1 to "
                                + BoardItems.MAX_TTL_SECONDS + ". Accepted in requests only; "
                                + "answers give expiresAt. Counted on the Mirror's clock, so it "
                                + "works even if yours disagrees."))
                .put(field("source", "text", true, "your paired name",
                        "Who posted the item, up to " + BoardItems.MAX_SOURCE_LENGTH
                                + " characters. Lets you list or remove just your own items."))
                .put(field("id", "text", false, null,
                        "The item's name in its address. POST picks one; with PUT you choose: 1 "
                                + "to " + BoardItems.MAX_ID_LENGTH + " letters, digits, dots, "
                                + "dashes or underscores."))
                .put(field("state", "text", false, null,
                        "One of " + BoardItems.joined(BoardItems.STATES) + ", worked out when "
                                + "you ask. See states."))
                .put(field("showing", "true or false", false, null,
                        "Whether the Board widget lists the item now."))
                .put(field("dueIso", "text or null", false, null,
                        "due again, as ISO 8601 in UTC, for reading."))
                .put(field("expiresAtIso", "text or null", false, null,
                        "expiresAt again, as ISO 8601 in UTC, for reading."))
                .put(field("doneAt", "time or null", false, null, "When the item became done."))
                .put(field("createdAt", "time", false, null, "When the item was first posted."))
                .put(field("updatedAt", "time", false, null, "When the item was last written."));
    }

    private static JSONObject field(
            String name, String type, boolean writable, Object fallback, String meaning)
            throws JSONException {
        JSONObject field = new JSONObject()
                .put("name", name)
                .put("type", type)
                .put("writable", writable)
                .put("meaning", meaning);
        if (fallback != null) {
            field.put("default", fallback);
        }
        return field;
    }

    private static JSONArray examples() throws JSONException {
        String items = BoardApi.ITEMS_PATH;
        return new JSONArray()
                .put(example("Leave a note for two hours", "POST", items, new JSONObject()
                        .put("title", "Dinner is in the oven")
                        .put("ttlSeconds", 7200)))
                .put(example("Add a to-do that stays until it is done or deleted", "POST", items,
                        new JSONObject()
                                .put("kind", "todo")
                                .put("title", "Water the plants")
                                .put("expiresAt", JSONObject.NULL)))
                .put(example("Set a reminder; repeating this request changes it in place", "PUT",
                        items + "/dentist", new JSONObject()
                                .put("kind", "reminder")
                                .put("title", "Leave for the dentist")
                                .put("body", "Bring the insurance card")
                                .put("due", BoardTime.EXAMPLE)
                                .put("priority", "high")))
                .put(example("Mark something done", "PATCH", items + "/dentist",
                        new JSONObject().put("done", true)))
                .put(example("See which to-dos are still open", "GET",
                        items + "?kind=todo&done=false", null))
                .put(example("Remove everything you posted", "DELETE",
                        items + "?source=YOUR-NAME", null));
    }

    private static JSONObject example(String does, String method, String path, JSONObject body)
            throws JSONException {
        JSONObject example = new JSONObject()
                .put("does", does)
                .put("method", method)
                .put("path", path);
        if (body != null) {
            example.put("body", body);
        }
        return example;
    }
}
