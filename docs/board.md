# The board

The board is where programs on the home network put things for the people in
front of the Mirror: notes, to-dos and reminders. A script, a home hub or an
AI agent sends plain facts about an item over HTTP. The Mirror decides how to
draw it, lists the items in a **Board** widget, and turns the pages by itself
when they do not all fit.

It is built for callers that have never seen this page. The Mirror describes
the board itself at `GET /api/v1/board/guide`, without a token, and every
refusal says what was wrong, names the field and points back at that guide.
This page says the same things at more length.

```text
GET    /api/v1/board/guide         how to use the board; needs no token
GET    /api/v1/board               what the glass lists now, with counts
GET    /api/v1/board/items         everything on the board, in pages
POST   /api/v1/board/items         add an item; the Mirror picks its id
GET    /api/v1/board/items/{id}    one item
PUT    /api/v1/board/items/{id}    create the item with this id, or replace it
PATCH  /api/v1/board/items/{id}    change some fields
DELETE /api/v1/board/items/{id}    remove one item
DELETE /api/v1/board/items?...     remove several
```

## Start here

The Mirror speaks plain HTTP on port `8787` of its address on the home
network. There is no HTTPS, so the board is for a network you trust.

1. **Get a token, once.** Every request but the guide needs the token of a
   paired device. Ask a person for the Mirror's six-digit pairing code: a
   paired phone shows one from the controls under **Settings > Paired devices
   > Show code**, and the Pairing code widget shows one on the glass. Then:

   ```bash
   curl -X POST http://MIRROR_IP:8787/api/v1/pair \
     -H "Content-Type: application/json" \
     -d '{"code":"123456","name":"Kitchen agent"}'
   ```

   Keep `token` from the reply. It lasts until someone revokes the device in
   the controls. The name is what the owner sees in the list of paired
   devices, and what your items say they came from.

2. **Post an item.** Only `title` is required.

   ```bash
   curl -X POST http://MIRROR_IP:8787/api/v1/board/items \
     -H "Authorization: Bearer MIRROR_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"title":"Dinner is in the oven","ttlSeconds":7200}'
   ```

3. **Look at what the glass lists.**

   ```bash
   curl http://MIRROR_IP:8787/api/v1/board -H "Authorization: Bearer MIRROR_TOKEN"
   ```

   If `glass.showsBoard` is `false`, nothing is drawing the board yet: a
   person turns the Board widget on in the controls under **Display**, or
   with **Show** beside the list on the **Home** tab. Replies to a write say
   so too, in `notice`. The items are kept either way.

A token is not limited to the board. It is the same credential the phone
controls use, so it can change anything they can. Pair only programs you
would hand the controls to, and revoke one from **Settings > Paired devices**
when it is no longer needed.

## An item

| Field | In a request | Meaning |
|---|---|---|
| `title` | required | The line the Mirror shows. One line, up to 120 characters. |
| `kind` | optional, default `note` | `note`, `todo` or `reminder`; see below. |
| `body` | optional | Detail shown smaller under the title. Up to 500 characters; line breaks are kept. |
| `due` | a time, or `null` | When the item is due. Required for a reminder, optional for a to-do, not allowed on a note. |
| `done` | `true` or `false` | Whether a to-do is finished or a reminder dismissed. |
| `priority` | optional, default `normal` | `low`, `normal` or `high`. High is listed first and drawn stronger; low is drawn fainter. |
| `expiresAt` | a time, or `null` | When the Mirror removes the item. `null`, or `"never"`, keeps it until it is deleted. |
| `ttlSeconds` | whole number | Another way to say `expiresAt`: seconds from now, on the Mirror's clock. Requests only. |
| `source` | optional | Who posted the item, up to 40 characters. Defaults to your paired name. |
| `id` | in the address | The item's name. `POST` picks one; with `PUT` you choose: up to 64 letters, digits, dots, dashes or underscores. |
| `state` | answers only | `open`, `soon`, `overdue` or `done`, worked out when you ask. |
| `showing` | answers only | Whether the Board widget lists the item now. |
| `dueIso`, `expiresAtIso` | answers only | `due` and `expiresAt` again as text, for reading. |
| `doneAt`, `createdAt`, `updatedAt` | answers only | When those things happened. |

**Times.** A request gives a time as milliseconds since 1970, like the rest
of the control API, or as ISO 8601 with its offset from UTC, such as
`2026-10-03T09:00:00-07:00` or `2026-10-03T16:00:00Z`. A time without an
offset is refused, because the Mirror would have to guess. Answers give
milliseconds, and `due` and `expiresAt` also as text in UTC. A listing carries
the Mirror's own clock as `now` and `nowIso`.

**Kinds.** The kind says what the item is, and so which fields make sense:

| Kind | What it is | `due` | `done` |
|---|---|---|---|
| `note` | Something to read. | not allowed | not allowed |
| `todo` | Something to do. | optional | marks it finished |
| `reminder` | Something at a time. | required | dismisses it |

**States.** `soon` means due within the next 60 minutes; the glass then shows
a countdown. `overdue` means the time has passed and the item is not done.

Fields the Mirror sends back are accepted and ignored in a request, so an
item that was read can be written again as it is. Any other unknown field is
refused by name: a misspelt `tittle` is an error, not a silent blank.

A request body is one JSON object in UTF-8. The board reads it as that
whatever `Content-Type` says, so accents and other scripts arrive intact and
a `curl -d` without a header works.

## How long things stay

Everything on the board leaves by itself, so a program that posts and then
forgets leaves nothing stale on the glass.

- Without `expiresAt` or `ttlSeconds`, an item is removed 24 hours after it
  was last written, or 24 hours after it is due if that is later.
- Writing the item again, with `PUT` or `PATCH`, starts those hours afresh.
  A program that keeps a list in step can rely on this: if it stops, its
  items clear within a day.
- `expiresAt` or `ttlSeconds` sets the moment yourself, and then writing the
  item again does not move it.
- `"expiresAt": null` keeps the item until someone deletes it.
- A done item stays on the glass, struck through, for ten minutes. After
  that it is off the glass but still on the board, where a program can read
  that it was done, until it expires.

The board holds up to 100 items. A full board answers `409` and says how to
make room.

## What the glass does

The board appears where a person has placed a **Board** widget. The widget
has a heading, a size, and a choice of what it lists: everything, or only
to-dos, reminders or notes, so that two widgets can show two lists.

Items are listed in the same order everywhere, in the API and on the glass:
overdue first, then due soon, then the rest, then done. Within each of
those, high priority comes before normal and low, earlier due times come
first, and otherwise the order is the one things were posted in.

Each item is a mark for its kind, its title, when it is due, and its body:
"In 25 min" inside the hour before, "3:30 PM", "Tomorrow 9:00 AM" or "Sat
9:00 AM" further out, and "5 min ago" or "Overdue · 3:30 PM" after. A done
item is struck through. The countdown runs on the glass without the board
being asked again.

When the items do not fit the widget, the Mirror shows them a page at a
time and moves on every ten seconds, with a row of dots for the pages.
Nothing in a request controls pages: the Mirror measures what fits in the
widget at the size its owner chose. An empty board draws nothing at all.

Text is drawn as text. There is no HTML, Markdown or link, and no way to
send markup. Keep a title to a few words that can be read from across a
room, and put detail in `body`.

## Requests

**Add an item** with `POST /api/v1/board/items`. The answer is `201` with the
item, its new `id` and the board's `version`.

**Keep something of your own up to date** by choosing its id and sending
`PUT /api/v1/board/items/{id}` each time. The same request creates the item
(`201`) and later replaces it (`200`), so repeating it never makes a second
copy. A replacement is whole: a field you leave out goes back to its
default, `done` included.

```bash
curl -X PUT http://MIRROR_IP:8787/api/v1/board/items/dentist \
  -H "Authorization: Bearer MIRROR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"kind":"reminder","title":"Leave for the dentist","body":"Bring the insurance card","due":"2026-10-03T09:00:00-07:00","priority":"high"}'
```

**Change some fields** with `PATCH`, which keeps the rest:

```bash
curl -X PATCH http://MIRROR_IP:8787/api/v1/board/items/dentist \
  -H "Authorization: Bearer MIRROR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"done":true}'
```

**List** with `GET /api/v1/board/items`. It returns everything on the
board, including done items that have left the glass. Narrow it with
`?kind=`, `?source=` and `?done=true` or `false`. It answers in pages:
`?limit=` (1 to 100, default 50) and `?offset=`, with `total` and
`nextOffset` in the answer. Follow `nextOffset` until it is `null`.

```json
{
  "items": [
    {
      "id": "dentist",
      "kind": "reminder",
      "title": "Leave for the dentist",
      "body": "Bring the insurance card",
      "due": 1791043200000,
      "dueIso": "2026-10-03T16:00:00Z",
      "done": false,
      "doneAt": null,
      "priority": "high",
      "expiresAt": 1791129600000,
      "expiresAtIso": "2026-10-04T16:00:00Z",
      "source": "Kitchen agent",
      "createdAt": 1790987172714,
      "updatedAt": 1790987172714,
      "state": "open",
      "showing": true
    }
  ],
  "total": 1,
  "offset": 0,
  "limit": 50,
  "nextOffset": null,
  "version": 12,
  "now": 1790987173098,
  "nowIso": "2026-10-03T00:26:13Z"
}
```

`GET /api/v1/board` is the short form for "what is on the glass": only the
items the widget lists now, with `counts` by state and `glass.showsBoard`.

**Remove** one item with `DELETE /api/v1/board/items/{id}`, or several with
`DELETE /api/v1/board/items` and a filter: `?source=NAME` for everything one
sender posted, `?kind=`, `?done=true`, a combination, or `?all=true` for the
whole board. Without any of these the request is refused.

## Noticing changes

Every answer carries `version`, a counter that moves whenever the board's
contents do: an item posted, changed, removed or expired. `GET
/api/v1/status` carries the same number as `boardVersion`. A program that
wants to know that someone ticked a to-do off in the phone controls asks for
the status now and then and reads the board again when the number has moved.

## When a request is refused

```json
{
  "error": "A reminder needs due, the moment it is about: milliseconds since 1970, or ISO 8601 with an offset such as 2026-10-03T09:00:00-07:00",
  "field": "due",
  "guide": "/api/v1/board/guide"
}
```

| Status | Meaning |
|---|---|
| `400` | The board cannot accept the request as it is; `error` says what to change. |
| `401` | No token, or one that was revoked. |
| `404` | No such item. It may have expired. |
| `405` | The path does not take that method; `error` lists the ones it takes. |
| `409` | The board is full. |

## In the controls

The **Home** tab lists what is on the board under **On the board**, with who
posted each item. A to-do or reminder can be marked done or undone there,
any item can be removed, and **Clear** empties the board. The list appears
when the board has something on it.

**Display > Arrange the mirror** has the Board widget with the others. It is
hidden until someone turns it on. While the board is empty, the editor draws
a few sample items in it so that it can be placed and sized.

## Notes and the board

The Mirror has had [notes](user-guide.md#notes) for longer: plain text that
people type in the controls and the Note widget shows. They are still there
and work as before. The board is the one for programs: its items say what
they are, when they are due, who sent them and when they should go.

## For agents that speak MCP

An agent that takes its tools over the Model Context Protocol need not call
these routes itself. The [companion](../companion/README.md) offers the board
as the tools `board_add`, `board_update` and `board_remove`, beside the
rest of the Mirror; see [MCP](mcp.md).

## What it does not do yet

- A token cannot be limited to the board.
- The Mirror does not call anyone. A program asks for `boardVersion` to
  learn of a change.
- Nothing at the glass changes an item: the Mirror has no touchscreen. A
  person marks things done in the phone controls.
- There is one board. A widget can list one kind of item, but items cannot
  be grouped into named lists.

`python tools\validate.py emulator` checks the board on Android 6 with the
checks `board-api` and `board-glass`, and `python tools\validate.py mirror
--exercise` tries it on a Mirror; see [Validation](validation.md).
