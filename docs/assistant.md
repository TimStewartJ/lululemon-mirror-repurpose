# The assistant

From Mirror Home 2.3.0 a Mirror can be asked for more than its five
[voice commands](voice.md). Say its name and then what you want:

> "Mirror, remind me to take out the trash at seven tomorrow."
>
> "Mirror, something calmer in the background."
>
> "Mirror, make the clock bigger and put the weather under it."

The Mirror does not work this out by itself. It passes the request to a
**companion**: a small server on a computer of yours, on the same network.
The companion turns the sound into words, lets a language model decide what
to do, and does it through the Mirror's own API: it reads what the glass
shows, changes the layout, the background and the brightness, keeps the
[board](board.md), and answers with one line on the glass. It also acts
unasked: it can greet whoever walks up, and it shows a reminder when it
falls due.

The assistant is off until you switch it on, and a Mirror without a
companion works as before. The companion in this repository is in
[`companion/`](../companion/README.md), which says how to install it and
what it can do.

## What it is like

The Mirror never speaks. All of it happens in a panel low on the glass,
which fades in when there is something to say and out again afterwards:

| The glass shows | Meaning |
|---|---|
| Three dots breathing, and **Listening** | The Mirror heard its name and waits for what follows. |
| Three dots running one after the other | The Mirror heard a request, and is taking it down or waiting for the answer. |
| Your words, small and in quotation marks | What the companion understood you to say. They stay above the answer, so that a mishearing can be seen for what it is. |
| A sentence | The answer. It stays for about as long as it takes to read, at most fourteen seconds. |
| A heading with rows under it | An answer in several parts, such as where the day stands: each row has a label (**WEATHER**, **MISSED**, **TODAY**) and a line or two of words. It stays longer, up to thirty seconds. |
| A sentence that ends in a question mark | The assistant needs to know more. Answer within ten seconds, without the name. |
| **The assistant isn't answering** | The companion could not be reached or failed. The controls say why. |

The Mirror's own commands never go to the companion: "Mirror, go to sleep"
works at once, and without a network. Everything else that follows the name
does. You can also pause after "Mirror", wait for **Listening**, and then
ask.

### Greetings

"Mirror, good morning", "good afternoon", "good evening", "I'm home" and
"good night" are the Mirror's own as well: it wakes, or for "good night"
goes dark, whether or not a companion answers. With the assistant on, the
Mirror also tells the companion which greeting it heard, and the companion
answers with where things stand. That answer is put together from what the
companion already knows, without the model and without the sound of what
was said, so it is there in well under a second:

| Said | The rows under the greeting |
|---|---|
| "good morning" | The weather now and what the day brings, what is due today, reminders that came due and nobody dismissed, and what is still to do. |
| "good afternoon", "good evening" | The same, for what is left of the day. |
| "I'm home" | What came due while you were out, and what is next. |
| "good night" | What tomorrow holds, and what was left open today. The display goes dark when it has been read. |

A Mirror that is running short of memory and wants to be switched off and
on adds a row saying so, labelled **MIRROR**, to every such answer except
the one to "good night"; see [Health](user-guide.md#health).

A reminder counts as missed from the moment it was due until it is marked
done or dismissed, or until the board drops it a day later. After a
greeting, "Mirror, dismiss those" marks the ones it has just listed as done.

### A character

The Mirror can answer as a small character that stands above its words.
Choose one under **Settings > Assistant > Character** in the controls; it
says hello on the glass when you do. With **None**, which is how a Mirror
starts, the panel shows its dots as before.

With the assistant on you can also ask for one: "Mirror, be the cat",
"Mirror, can you be a ghost?", "Mirror, next character", "Mirror, no
character", or "Mirror, which characters do you have?". The assistant knows
which one it is at the moment, and the choice stays until it is changed.

| Character | What it is |
|---|---|
| **Blink** | Two eyes and nothing else; the one that reads from farthest away. |
| **Wisp** | The small ghost that lives in the mirror. It floats, and waves with an arm it otherwise keeps to itself. |
| **Mochi** | A cat's head. Its ears go up when it listens and flat when it is sorry. |
| **Lune** | A moon. Full when it attends, a sliver when it sleeps, and its phases pass over it while it thinks. |

All four act the same things out, each with the body it has:

| When | The character |
|---|---|
| The Mirror heard its name | comes up, all eyes, and sound reaches it in small arcs. |
| It works on a request | looks up and about, and three dots rise from it. |
| The companion understood the words | nods. |
| The answer arrives | says it, and then stays beside it, blinking now and then. |
| The answer is a question | tilts its head and waits. |
| A command was carried out | smiles, with a small hop. |
| You greeted it | waves. |
| It did not follow | tilts its head the other way, under a question mark. |
| The companion did not answer | draws its brows together. |
| Good night | shuts its eyes and sleeps. |

### Where the answers appear

The Mirror's answers stand low and in the middle of the glass unless you
choose otherwise. Under **Settings > Assistant > Answers appear** they can
be set at one of five heights, from the top of the glass to the bottom, and
to the left, in the middle or to the right. A line on the glass shows the
place when you change it. The character, if there is one, comes along, and
so does what the Mirror's own voice commands show.

With the assistant on you can also ask: "Mirror, put your answers at the
top", "Mirror, answer further to the left", "Mirror, a bit lower", "Mirror,
back to the bottom". The answers are not a widget: they lie over whatever is
behind them, on a black backing, so high up they cover the clock for as long
as they show.

A character takes up a new part in the frame in which the Mirror learns of
it, whatever it was in the middle of, and leaves with the panel. It is drawn
only while the panel shows, so it costs nothing the rest of the time. It
also stands above what the Mirror's own voice commands show, with or
without an assistant.

What changes on the glass changes gently. A widget that the assistant adds
fades in, one it removes fades out, one it moves glides to its place, and
one whose size changes goes dark for a moment and comes back. A new item on
the board fades in under the ones that were there. The glass also shows a
change within a fraction of a second of its being made, where it used to
look for changes every few seconds.

A request that someone made is answered where they can see it: a Mirror
that is dark wakes for the answer, unless the answer was to go dark.

### More than a line: moments

The assistant can also put something on the glass for a while, beside its
answer, and the Mirror takes it away again by itself:

| Say | What appears |
|---|---|
| "Mirror, set a timer for five minutes", "how long until seven?" | A countdown that runs on the glass, with a line under it that shortens. A timer is also a reminder on the board, so it is announced when due. |
| "Mirror, write happy birthday Sam really big", "leave a note for Sam: back at six" | Words, as large as there is room for. |
| "Mirror, show me how to make pour-over coffee, step by step", "what should I pack for the gym?" | A list of up to eight rows. |
| "Mirror, show me how the temperature goes over the next hours" | A small chart, as bars or as a line. |
| "Mirror, draw me a heart", "draw a sun" | A line drawing. |
| "Mirror, make it red and put it at the top", "bigger" | The same moment, changed where it stands. |
| "Mirror, stop the timer", "take that down", "take all of those down" | It leaves at once. |

It may also show one unasked, where seeing serves better than a sentence.
A plain answer, such as the time or a confirmation, gets none.

A moment arrives and leaves with a fade, as a widget does, and stands on a
slight dark backing so that it can be read over a film or a photo.

Moments stand in one column in the middle of the glass, in the order they
came, and glide to make room when one comes or goes. Where there is free
room the column takes that; where there is not, it lies over the clock, the
weather or whatever else is there, over as few as it can, and those fade
out until the moment over them leaves. A moment is there for a while, and
what you have just asked for should not be the thing that is made small:
when the column grows too tall for the glass, the older moments are drawn
smaller, oldest first, and when that is not enough the oldest leave. The
column keeps clear of where the answer appears while it can. Ask for a
place ("at the top", "on the left") to have a moment there instead. It stays for about a minute unless told how long, six
hours at most; a countdown stays until it has run out and twenty seconds
more, with its digits pulsing at the end. The glass holds six at a time.

Moments are not kept. If Mirror Home restarts, they are gone; the timer's
reminder on the board is not.

### Settings you can ask for

Most of what **Settings** and **Display** hold in the phone controls can be
asked for as well, in your own words:

| Say | What changes |
|---|---|
| "Mirror, use military time", "back to AM and PM" | How the clock reads. |
| "Mirror, we moved to Denver, fix the clock" | The time zone. The Mirror follows that zone's daylight saving by itself. |
| "Mirror, turn off at eleven at night and come back on at seven", "stay on all night" | The hours the display is lit each day. |
| "Mirror, don't go dark when the room is empty", "wait ten minutes before you sleep", "you keep missing me, be more sensitive" | Whether the display sleeps when it sees nobody, after how long, and how small a movement counts as someone. |
| "Mirror, show the weather for Portland, Maine", "use Celsius", "no weather" | The place the weather is for, its units, and whether there is weather. |
| "Mirror, play the flowers film from six in the morning and the water one from seven at night", "also the forest one from noon", "stop changing the film by the clock", "go back to the schedule" | Which film plays from which time of day. |
| "Mirror, make the background a deep blue", "a gradient from navy to purple", "the next photo", "the second photo", "it's hard to read, darken it a bit" | A plain colour, a gradient, a particular photo, and how far the background is darkened behind the widgets. |
| "Mirror, make the text a warm amber", "put the text back to normal" | The colour of what is written on the glass. |
| "Mirror, call yourself Hallway" | The name on the glass and in the controls. You still say "Mirror" to be heard: the word that wakes it is part of the voice model. |
| "Mirror, stop greeting me", "no morning briefing", "nothing by yourself after nine at night until seven", "leave the films alone", "what do you do on your own?" | What the assistant does unasked. A change holds after the companion restarts. |

A town is best said with its state or country. Of several towns of one name
the Mirror takes the best known and says which, so that "no, the one in
Maine" can follow; one that is not where you said it was is not swapped for
another, and the Mirror asks which you mean. Moving the weather does not
move the clock: when the place is in another time zone the answer says so.

Hours that leave the present moment outside them make the display go dark
at once, which is what was asked for; say "Mirror, wake up" to have it for
now. Dark text cannot be seen on a mirror, which is black where nothing is
drawn, and the Mirror says so when asked for a dark colour.

Four things stay with the phone controls, and the Mirror says so when asked:
the Wi-Fi, which phones are paired, whether the assistant and the listening
are on, and software updates. Each can lock people out of the Mirror or
switch off the thing that takes requests, and a sentence misheard from
across a room should not be able to. A web page as the dashboard and media
to play need an address, which nobody dictates.

## How a request travels

1. The recogniser on the Mirror hears its name, followed by something that
   is not one of its commands. It must be sure of the name; what follows it
   may be anything.
2. The glass shows the dots. The Mirror waits until you have stopped
   talking: until the room is as quiet as before and the recogniser has no
   more words under way, for at most twelve seconds.
3. That stretch of sound, from a second before the name, is cut out of
   the half minute that the recogniser's process keeps in memory, and sent
   to the companion.
4. The companion transcribes it, shows the words on the glass, and gives
   them to the model together with tools for the Mirror. The model may use
   several of them, and may look at a picture of the glass.
5. The companion's answer comes back and is shown. If it is a question, the
   next thing said within ten seconds goes to the companion as well, name
   or no name.

From the last word to the answer takes about five seconds with the
companion in this repository: one for the Mirror to be sure that you have
finished, one or two to transcribe, and three or more for the model.

## What leaves the Mirror

With the assistant switched off, nothing does: voice commands are recognised
on the Mirror, and no sound is stored or sent, as before.

With it switched on:

- **The sound of each request** goes to the companion, over your own
  network and nowhere else: from a second before the name to half a
  second after you stop. Talk that does not begin with the Mirror's name is
  not sent. The recogniser can take other talk for its name, about as often
  as it would otherwise have shown **Didn't catch that**; that stretch is
  then sent too, and the companion is told to drop what was not meant for
  the Mirror.
- **What the companion does with it** is the companion's business, and the
  one in this repository says so in its [README](../companion/README.md):
  the sound is transcribed on your computer, which keeps the last twenty
  recordings so that a mishearing can be listened to, or none if you say so;
  the words, and what the Mirror shows, go to the model service that you
  gave it.
- **A picture of the glass** can be fetched by any paired device, which the
  companion is one of. It shows what the dashboard shows, such as notes.
- **The Mirror keeps** the last twelve requests in memory until it
  restarts: what was understood, what was answered, and how long it took.
  Paired devices can read them. The [health report](validation.md#the-health-report)
  holds only how many there were.

The companion is given two secrets: its own pairing with the Mirror, like a
phone's, and a key that the Mirror sends with every request. The Mirror
stores the key in its settings and never shows it again.

## Setting it up

1. Get [voice commands](voice.md#setting-it-up) working first. The
   assistant uses the same recogniser and the same microphone permission.
2. Install the companion on a computer that is always on, and pair it with
   the Mirror: see [`companion/README.md`](../companion/README.md). It
   prints its address and its key.
3. In the controls, open **Settings > Assistant**, enter the address and
   the key, choose **Save companion** and switch **Assistant** on. From a
   computer the same is one request:

   ```powershell
   Invoke-RestMethod -Method Put -Uri http://MIRROR:8787/api/v1/assistant `
     -Headers @{ Authorization = "Bearer $token" } -ContentType application/json `
     -Body '{"enabled": true, "address": "http://192.168.1.20:8790", "key": "..."}'
   ```

The line under the switch then reads **Connected**, with the model that
answers. Type a request into the field below to try it without speaking;
**Asked lately** lists what was asked and answered since the Mirror last
started.

| State | Meaning | What to do |
|---|---|---|
| Off | The assistant is switched off. The Mirror answers only its own commands. | Switch it on. |
| Waiting for a companion | Switched on, but the address or the key is missing. | Enter both. |
| Looking for the companion | The Mirror has not had an answer yet. | Wait a few seconds. |
| Connected | The companion answers and reports that it is ready. | |
| A problem that the companion reports | The companion answers, but its model, its speech recognition or its way back to the Mirror is not ready. | Look at the companion. |
| The companion does not answer | It cannot be reached at its address, did not answer in time, or does not accept the key. The Mirror asks again every minute. | Check that it runs, that the address is right, and that the key is the one it printed. |

Spoken requests also need voice to be **Listening**. With voice switched
off, typed requests still work.

## What it does unasked

The Mirror tells the companion when Mirror Home has started, and when
someone came before a Mirror that had gone dark because nobody was there,
with how long it had been dark. What the companion makes of that is up to
it; the one in this repository greets, at most now and then, and not at
night. Reminders that fall due are shown by the companion as well, through
the same route by which it shows anything: a line on the glass.

Each of these can be switched off, and the hours it keeps quiet changed, by
asking: "Mirror, stop greeting me", "Mirror, nothing by yourself after nine
at night until seven". "Mirror, what do you do on your own?" tells how they
are set.

## Limits

- The Mirror must recognise its name. From across a room that fails more
  often than up close; say it again, or say "Mirror", wait for
  **Listening**, and then ask.
- One request is one breath of at most twelve seconds. In a room that is
  never quiet, with music or a television, the Mirror cannot hear where a
  request ends and sends more than the request; the companion makes of it
  what it can.
- A background film cannot be pictured: in a picture of the glass it
  appears as its still poster.
- It answers in writing only: one line of at most 200 characters, or a
  heading with up to five rows, with a moment beside it if it shows one.
- A drawing is what a language model can describe in a few dozen lines and
  curves: a heart, a sun, a house. It is no picture.
- This glass draws no emoji; the assistant is told to use none.

## Protocol

Two directions, both plain HTTP on the home network.

### The Mirror asks the companion

The Mirror sends `Authorization: Bearer KEY` with every request, to the
address in its settings. Anything that answers these four routes can be a
companion.

`POST /v1/utterance` carries a spoken request: a WAV file (`audio/wav`,
16 kHz, one channel, 16 bits) of twelve seconds at most, with two headers.
`X-Mirror-Addressed` is `name` (the request began with the name, which is
in the sound), `window` (it followed the name after a pause; the name is
not in the sound) or `follow-up` (it answers the companion's question).
`X-Mirror-Utterance` is an id for the request. `POST /v1/ask` carries a
typed one: `{"text": "...", "source": "controls"}`. A greeting that the
Mirror recognised itself arrives the same way, as
`{"text": "good morning", "source": "shortcut", "shortcut": "good-morning"}`;
the other shortcuts are `good-afternoon`, `good-evening`, `good-night` and
`home`. For a shortcut the Mirror waits eight seconds, not 45, and a
companion that does not know shortcuts may answer it as the typed request it
also is. All are answered with:

```json
{
  "heard": "remind me to take out the trash at seven tomorrow",
  "reply": "I'll remind you tomorrow at 7.",
  "ignored": false,
  "listen": false,
  "acted": ["board_add"],
  "details": [],
  "seconds": 6
}
```

| Field | Contents |
|---|---|
| `heard` | What was understood, for **Asked lately**. |
| `reply` | The line to show, at most 200 characters; longer is cut. Empty shows nothing. |
| `ignored` | True if the request was not meant for the Mirror or held nothing. The dots go away and nothing is shown. |
| `listen` | True if `reply` is a question: what is said next, within ten seconds, is sent as a `follow-up`. |
| `acted` | The names of what the companion did. If `set_power` is among them, a dark Mirror is not woken for the answer. |
| `details` | Optional: up to five rows to show under `reply`, which is then their heading. Each is `{"label": "Weather", "text": "Clear, 31° later"}`: a label of at most 14 characters, which may be empty or left out, and a text of 1 to 90. More rows are dropped and longer ones cut. |
| `seconds` | Optional: how long to show the answer, 2 to 30. Without it the Mirror goes by the length of what there is to read. |

The Mirror waits 45 seconds. Any status but `200` counts as no answer;
`401` is reported as a key that is not accepted.

`POST /v1/event` tells the companion what the Mirror noticed:
`{"type": "started", "at": 1790990000000}`, or `"presence"` with
`asleepSeconds`. It is not answered with anything the Mirror reads.

`GET /v1/health` is asked once a minute, and at once when the settings
change. `{"ok": true, "model": "..."}` makes the Mirror **Connected**. With
`ok` false, the first of `brain.ready`, `stt.ready` and `mirror.reachable`
that is false is named in the controls, with its `detail`.

### The companion asks the Mirror

The companion is a paired device and uses the [control API](protocol.md)
like any other: the dashboard, its layout, the board, the background
videos, sleep and wake. Two routes exist for it:

`POST /api/v1/assistant/say` shows a line on the glass:
`{"text": "Rain from three.", "kind": "notice", "seconds": 8}`. `text` is
one line of 1 to 200 characters. `kind` is `reply` (the default), `notice`
or `heard`, which is shown as a quotation of what someone said and stays
until the answer replaces it. `seconds`, 2 to 30, is optional; without it a
line stays as long as its length needs. `details` is optional too: rows to
show under the line, as in an answer, except that rows which break the
limits are refused with `400` and not cut. Mirror Home before 2.3.0-dev.10
ignores `details` and shows the line alone. The answer is `{"shown": true}`,
or `{"shown": false, "reason": "sleeping"}` from a dark Mirror, which shows
nothing and is not woken by this.

`GET /api/v1/screenshot` answers with a JPEG of what the glass shows, 540
pixels wide, or as wide as `?width=` says, from 180 to 1080. A dark Mirror,
or one whose dashboard is not in front, answers `409`.

The Mirror's side is `GET` and `PUT /api/v1/assistant` and `POST
/api/v1/assistant/ask`; see [Control protocol](protocol.md#assistant).

## For developers

The characters are `Mascot*.java`. They know nothing of Android: each
draws itself with a pen (`MascotPen`) from a pose that `MascotRig` works
out, which pulls every part of a face toward where its mood wants it with a
spring. `MascotView` is the pen on the glass. On a computer,
`python tools/mascots.py sheet` paints every character in every mood and
`python tools/mascots.py film` has them act out a conversation as a GIF,
from the same code and without a Mirror; it needs a JDK and Pillow. A new
character is a class beside the four and a line in `Mascot.ALL`.

`AssistantManager.java` keeps the settings, talks to the companion and
decides what the glass shows; `ConversationPanel.java` is the panel that
shows it, and `GlassCaption.java` the way to it from anywhere in Mirror
Home. The dashboard's transitions are in `dashboard/mirror.js` and
`mirror.css`, and `ControlServer.changesGlass` decides which requests make
the dashboard look again at once. Whether a sentence is a request is decided in
`VoiceInterpreter.java`; where a request ends, in `RequestEnd.java`; both
have unit tests. `SoundRing.java` is the half minute of sound, which exists
only in the recogniser's process and only in memory.

The [emulator suite](validation.md) runs a stand-in companion on the
computer (`tools/fake_companion.py`) and checks the whole path against it:
`assistant-off`, `assistant-asks` and `assistant-voice`.
