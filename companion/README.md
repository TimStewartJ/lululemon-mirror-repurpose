# Mirror companion

The companion is a small server for a home network. It makes a MIRROR that
runs Mirror Home into something you can talk to. Mirror Home has five fixed
voice commands of its own. Anything else that is said after "Mirror" is sent
to the companion, which works out the words, lets a language model act on
the mirror through the mirror's own control API, and sends back what the
glass is to show: one short line, or a card, which is a headline with up to
five rows under it. The mirror never speaks.

A greeting such as "good morning" or "good night" is answered with a
briefing: a card with the weather, what is due, what was missed and what is
still to do. The companion builds it from the mirror's state without asking
the model, so it is there at once.

The companion also does three things by itself. It greets someone who walks
up after the display was dark for a while: in the morning with the briefing,
after a reminder went unseen with what was missed, and otherwise with a line
from the model. It shows a card when a reminder on the board falls due. And
once an hour it looks over the display and may tidy one small thing.

## What it needs

- A Linux machine on the same network as the mirror. It is built for and
  tried on Ubuntu 24.04 with an NVIDIA GPU of 4 GB. Without a GPU it works
  on the CPU, more slowly.
- Node.js 24 and Python 3.10 or later.
- A GitHub Copilot login for the account it runs under. Log in once with the
  Copilot CLI as that user. The companion uses that login and the model
  `gpt-6-luna`.
- Mirror Home 2.3.0 or later on the mirror, with the companion's address and
  secret entered in its controls.

## Install

From this folder:

```bash
bash deploy/install.sh
```

This installs the Node packages, creates a Python environment in
`~/.local/state/mirror-companion/venv`, installs the speech-to-text packages
into it, and fetches the speech model. The first run downloads about 2 GB
and fills about 3 GB of disk. It is safe to run again. `--no-gpu` leaves out the NVIDIA libraries, `--state
DIR` uses another state folder, and `--model NAME` fetches another Whisper
model.

## Configure and pair

```bash
node src/cli.js init
```

writes `~/.config/mirror-companion/config.json` with a fresh secret. Only
your account can read the file. Set `MIRROR_COMPANION_CONFIG` to keep it
somewhere else. `config.example.json` shows every setting.

Next, pair with the mirror. On a paired phone, open the mirror's controls,
go to **Settings > Paired devices**, and choose **Show code**. Then:

```bash
node src/cli.js pair --host MIRROR_ADDRESS --code 123456
```

The mirror gives the companion a token, which is stored in the config. The
mirror lists the companion as "Mirror companion" among its paired devices,
and revoking it there ends its access.

Last, give the mirror the companion's address and its secret. In the
mirror's controls, under **Settings > Assistant**, enter the address as
`http://THIS_MACHINE:8790` and, as the key, what this prints:

```bash
node src/cli.js secret
```

Choose **Save companion** and switch **Assistant** on. The line under the
switch reads **Connected** once the mirror has reached the companion.

### Settings

| Setting | Meaning |
|---|---|
| `listen` | The address and port the companion listens on. |
| `secret` | Shared with the mirror. Every request must carry it. |
| `mirror` | The mirror's address, port and token. `pair` fills this in. |
| `model` | The Copilot model. |
| `reasoningEffort` | How hard the model thinks: `low` by default, which was measured to answer in about 2 seconds where the model's own default took 3.3. `default` leaves the choice to the model. |
| `stt.model`, `stt.device` | The Whisper model, and `auto`, `cuda` or `cpu`. With `auto` the GPU is used if it works. |
| `stt.python` | The Python that has the speech packages. Leave it empty for the one `install.sh` made. |
| `proactive.greet`, `.reminders`, `.tend` | Switch each of the three things it does by itself. `greet` is the model's greeting; with `reminders` off there is no card for what was missed either. |
| `proactive.morningBriefing` | Whether the first person of the morning gets the briefing. Switched off, the model greets in the morning as at any other hour, if `greet` is on. |
| `proactive.tendMinutes` | How often it looks over the display. |
| `proactive.quietHours` | From when to when, on the mirror's clock, it shows nothing by itself and changes nothing. `null` for never. |
| `keepUtterances` | How many recordings to keep. 0 keeps none. |
| `stateDir` | Where the companion keeps what it stores. |

The `proactive` settings can also be changed by asking the mirror ("stop
greeting me", "quiet from nine at night until seven"); see
[Settings changed by asking](#settings-changed-by-asking).

## Run

```bash
node src/cli.js serve
```

To run it as a service that starts with the machine, use the unit template:

```bash
mkdir -p ~/.config/systemd/user
sed "s|@COMPANION_DIR@|$PWD|g" deploy/mirror-companion.service > ~/.config/systemd/user/mirror-companion.service
systemctl --user daemon-reload
systemctl --user enable --now mirror-companion
loginctl enable-linger "$USER"
journalctl --user -u mirror-companion -f
```

The template expects Node at `~/.nvm/versions/node/v24.14.0/bin/node`. Edit
the two paths in the unit if yours is elsewhere. The last but one command
lets the service run while you are not logged in.

To see how it is doing, and to try it without the mirror:

```bash
node src/cli.js health
node src/cli.js ask "what is on my list?"
node src/cli.js ask "good morning" --shortcut good-morning
node src/cli.js say-wav recording.wav
```

`health` names what is not ready and what to do about it. `ask` prints the
reply and, under it, the rows of a card, one to a line. With `--shortcut`
the words are sent as a greeting the mirror recognised itself:
`good-morning`, `good-afternoon`, `good-evening`, `good-night` or `home`. A
recording for `say-wav` is a WAV file at 16 kHz, mono, 16-bit.

## What it can do

The model has these tools and no others. It has no shell, no files and no web
access.

| Tool | Does |
|---|---|
| `get_state` | Reads the mirror: time, display, widgets, background, board, weather. |
| `look` | Takes a picture of what the glass shows. |
| `set_power` | Puts the display to sleep or wakes it. |
| `set_brightness` | Sets how bright the display is when awake. |
| `set_background` | Chooses a film, plain black, a colour or a gradient, or a photo of the mirror's library ("the next photo", "the second one"), and darkens what is behind the widgets so that they are easier to read. |
| `set_character` | Chooses the character the mirror answers as, or none: "be the cat", "next character", "just the words". |
| `set_answer_place` | Moves where on the glass the answers appear: "put your answers at the top", "further left", "a bit lower". |
| `arrange_widgets` | Shows, hides, moves and resizes widgets. |
| `set_clock` | Makes the clock read 12 or 24 hours, and moves the mirror to another time zone: "use military time", "we moved to Denver". |
| `set_display_rules` | Sets when the display is dark by itself: the hours it is lit each day, or none; whether it sleeps when it sees nobody, after how long, and how small a movement counts; whether its brightness follows the room. |
| `set_weather` | Sets the place the weather is for, Fahrenheit or Celsius, and whether there is weather at all: "show the weather for Portland, Maine", "use Celsius". |
| `set_film_schedule` | Sets which film plays from which time of day, stops and starts that timetable, and returns to it from a film chosen by hand. |
| `set_text_color` | Colours the clock, the date, notes and the board, or the weather and the other small widgets, and gives them back their own soft white. |
| `set_name` | Gives the mirror the name it shows on the glass and in the controls. The word that wakes it stays "Mirror". |
| `habits` | Tells and changes what the companion does unasked: the greeting, the morning briefing, the reminder cards, the tidying and how often, and the quiet hours. "Stop greeting me", "nothing by yourself after nine at night". |
| `board_add`, `board_update`, `board_remove` | Notes, to-dos and reminders on the board. A timer is a reminder. A hidden board is shown for a new item, at a free place if another widget has taken its own. |
| `present` | Answers with a card when the answer is a list or has several parts: "what's on my list?", "what's the forecast?". |
| `briefing` | Answers a greeting or "what did I miss?" with the briefing. |
| `say` | Shows a line when it greets. In a conversation its answer is the line or the card, and it has no other. |
| `remember`, `forget` | Keeps or drops a line about the household. |
| `ignore` | Decides that the words were not meant for the mirror. |

A request within two minutes of the last one continues the same
conversation, so "make it bigger" works after "show the clock".

Four things the model cannot change, and says so when asked: the Wi-Fi,
which phones are paired, whether the assistant and the listening are
switched on, and software updates. A request can lock people out of the
mirror through each of them, or switch off the thing that takes requests,
and words that were misheard across a room should not be able to. Two more
need something only a person has: a web page as the dashboard and media to
play both take an address.

### Settings changed by asking

A change made through `habits` takes effect at once and is written to the
`proactive` part of the config file the companion was started from, so that
it holds after a restart; the rest of the file is left as it was written. If
the file cannot be written, the change holds until the companion is next
restarted, and the answer says so.

For the weather the mirror looks the place up itself, by the town's name with
its state or country, and the companion takes the best known match and names
the others in its answer. A town that is not where it was said to be is not
replaced by one of the same name elsewhere: the model is told which there
are and asks. When the place lies in another time zone than the mirror's
clock, the answer says so and the clock is left alone until someone asks.

A new time zone is given to the mirror by its IANA name with the offset in
force there, which the companion works out; the mirror then follows the
changes its own table has for that zone. The same zone is sent back exactly
as it was read, which the mirror takes as "leave the clock alone".

### Cards

An answer is one line, or a card: a headline of at most 60 characters with up
to five rows, each a label of at most 14 characters beside one line of at
most 90. One fact or one confirmation stays a line. The model answers with a
card, through `present`, when the answer is a list or has several parts. A
card that breaks those limits is handed back to the model once to be written
again; whatever leaves the companion is cut to fit, between words. A Mirror
Home that does not know cards yet shows the headline alone.

### Greetings and the briefing

Mirror Home recognises "good morning", "good afternoon", "good evening",
"good night" and a homecoming by itself and sends them as shortcuts, without
a recording. The companion answers a shortcut with the briefing, which it
builds from the mirror's status and board. No model and no speech-to-text
are involved, and a shortcut does not wait behind a request that is being
served. If the mirror cannot be read, the answer is the greeting alone.

| Greeting | Headline | Rows |
|---|---|---|
| Morning | Good morning | Weather now and today, what is due today, what was missed, the open to-dos. |
| Afternoon | Good afternoon | Weather now and for the rest of the day, what is due later, what was missed, the open to-dos. |
| Evening | Good evening | Weather now, tonight and tomorrow, what is due tonight, what was missed, what is due tomorrow. |
| Night | Good night | Tomorrow's weather, what is due first tomorrow, what is still open. |
| Home | Welcome home | Weather now, what is due later, what was missed, the open to-dos. |

A row with nothing to say is left out, and so is weather that the mirror
could not refresh. A mirror whose status says that it should be restarted
(`restart.advised`) gets a last row, "Mirror: Short of memory after 9 days.
Please switch me off and on.", in every briefing but the one at night. A list names three items and counts the rest. Times are
written as the mirror's clock shows them. An item is missed when its time has
passed and it is not done, and it is named in every briefing until it is
marked done or leaves the board.

A greeting in other words, such as "morning, mirror", "hey, I'm back",
"catch me up" or "what did I miss?", goes to the model, which answers with
the same briefing through its `briefing` tool. For three minutes after a
briefing the model is told what it said, so that "dismiss those" or "got it"
marks the missed items done.

### What it does by itself

When a reminder falls due and the display is on, the glass shows a small
card for 20 seconds: the reminder's title, and under it the time it was due.

When someone comes before a mirror that was dark, the companion shows at most
one thing, and nothing in the quiet hours:

1. The morning briefing, to the first person between 5 and 11 in the morning
   after the display was dark for ten minutes or more. Once a day, and no
   model is involved.
2. Otherwise, if reminders fell due while the display was dark, in a quiet
   hour, or while the mirror could not be reached: a card "While you were
   away" with what was missed and what is due in the next three hours. The
   display must have been dark for a minute or more.
3. Otherwise, the model's greeting: after ten dark minutes, at most every 45
   minutes, and only if the model finds something worth reading.

### What it listens to

The mirror sends whatever follows its name, and now and then it takes other
talk for its name. Words that begin with the name, and words typed in the
controls, are always answered. Others, which followed the name after a pause
or lost it in transcription, are left to the model, which may drop them as
talk between people; before it does, it is asked to weigh them a second
time, because asked once it dropped about one real request in four.

## What leaves the house, and what is stored

The sound stays on this machine. It is turned into words here, by Whisper.

For each request, the words and a summary of the mirror's state go to GitHub
Copilot's model: the mirror's local time and zone, whether the display is on,
the layout, the names of the films, the items on the board, and the weather
as the mirror has it, which includes the name of the place. So do the lines
it was asked to remember, and a picture of the glass when the model uses
`look`. The model's greeting and the hourly look send the same summary
without any words of yours. The briefing, the morning card, the card for
someone who was away and the reminder cards are made on this machine and
send nothing to the model.

In the state folder, `~/.local/state/mirror-companion` unless you chose
another, the companion keeps:

- `utterances/`: the last 20 recordings, so that a mishearing can be listened
  to. Set `keepUtterances` to 0 to keep none.
- `activity.jsonl`: the last 200 exchanges, with what was heard and answered,
  the rows of a card included. `GET /v1/activity` shows them.
- `memory.txt`: what it was asked to remember, one line each. You can read
  and edit it.
- `venv/` and `models/`: the Python environment and the speech model.

The log goes to standard output, one JSON line per event, and holds what was
heard and answered. The secret and the mirror's token are never logged.

## Test

```bash
node --test
```

runs the tests. They need no network, GPU, Python or Copilot login: the
mirror, the model and speech-to-text are replaced by stand-ins in
`tests/fakes`.

Three scripts try the real parts:

```bash
node scripts/try-brain.mjs
node scripts/try-stt.mjs FOLDER_WITH_WAV_FILES
node scripts/fake-mirror.mjs
```

`try-brain` says a set of requests to the real model against a fake mirror
and checks what came of each, cards and briefings among them. It needs the Copilot login. `try-stt` runs the
real speech-to-text worker on recordings and prints the transcripts and
times. `fake-mirror` runs the fake mirror by itself, so that `pair`, `serve`
and `say-wav` can be tried from end to end without a mirror.

## How it is put together

| File | Job |
|---|---|
| `src/cli.js` | The command line. |
| `src/serve.js` | Puts the parts together and starts them. |
| `src/server.js` | The HTTP routes the mirror calls, with their checks and limits. |
| `src/assistant.js` | One exchange: recording or typed words in, answer out. |
| `src/proactive.js` | The greeting, the morning card, the reminders and the hourly look. |
| `src/briefing.js` | The briefing and the other cards that code builds, without the model. |
| `src/brain.js` | The model: sessions, retries and time limits on the Copilot SDK. |
| `src/prompt.js` | Every word the model is told. |
| `src/tools.js`, `src/tools/` | The tools, independent of the SDK. |
| `src/layout.js` | The rules of the mirror's layout, and where a widget can go without covering another. |
| `src/state.js` | The summary of the mirror that the model reads. |
| `src/mirror.js` | The client for the mirror's API. |
| `src/stt.js`, `src/stt_worker.py` | Speech-to-text: the Python worker and what keeps it running. |
| `src/queue.js` | One agent run at a time. |
| `src/config.js`, `src/log.js`, `src/memory.js`, `src/activity.js`, `src/recordings.js`, `src/health.js`, `src/time.js`, `src/wav.js`, `src/reply.js`, `src/clock.js` | One small job each, named by the file. |

The wire formats between the mirror and the companion are in
[The assistant](../docs/assistant.md#protocol). The routes are
`POST /v1/utterance`, `POST /v1/ask`, `POST /v1/event`, `GET /v1/health` and
`GET /v1/activity`, which lists the latest exchanges. An answer always has
`details`, the rows of a card, empty for one line, and may have `seconds`,
how long the glass should show it. A shortcut is `POST /v1/ask` with
`"source": "shortcut"` and `"shortcut"` set to its name.
