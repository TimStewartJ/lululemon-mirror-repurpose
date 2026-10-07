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
- A language model that can call tools, from wherever you have one: a
  provider you have an account or a key with (Anthropic, OpenAI, Google,
  OpenRouter and some forty others), or a server of your own such as Ollama.
  The companion is tied to none of them. See
  [Choosing a model](#choosing-a-model).
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
somewhere else. `config.example.json` shows every setting. No model is
chosen in it yet: that is the last step, under
[Choosing a model](#choosing-a-model).

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

## Choosing a model

The companion runs the model with [Pi](https://github.com/earendil-works/pi):
Pi's agent loop (`@earendil-works/pi-agent-core`) runs inside the companion
and is given the mirror's tools and no others. Pi also brings the
providers, which are the services a model can come from. Two settings
choose: `provider` says whose model, and `model` says which.

```bash
node src/cli.js providers          # where a model can come from, and which are signed in
node src/cli.js models anthropic   # the models a sign-in is offered
```

Nothing is chosen for you. Until both are set the companion runs, and
`health` says that a model is still to be chosen. A provider is one of two
kinds.

### A provider you have an account or a key with

```json
"provider": "anthropic",
"model": "claude-haiku-4-5",
```

`providers` lists them: Anthropic, OpenAI, Google, Mistral, Groq,
OpenRouter, Amazon Bedrock, GitHub Copilot and over thirty more. Sign in
once:

```bash
node src/cli.js login anthropic          # in the browser, where the provider has a sign-in there
node src/cli.js login openrouter --key   # or with a key, which is asked for and not shown
```

The sign-in is kept in `auth.json` beside the config, readable by your
account only; set `MIRROR_COMPANION_AUTH` to keep it elsewhere. `logout`
forgets it. A sign-in in the browser is renewed by the companion as it runs
out.

A key can instead be in the environment variable Pi reads for the provider,
such as `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY` or
`OPENROUTER_API_KEY`; [Pi's list](https://github.com/earendil-works/pi/tree/main/packages/ai#environment-variables)
has them all. For the service that means an `Environment=` line in the unit,
so `login` is the easier way. A stored sign-in comes before the environment.

### A server of your own

```json
"provider": "ollama",
"model": "qwen3:14b",
"endpoint": { "baseUrl": "http://localhost:11434/v1" },
```

For Ollama, LM Studio, vLLM, llama.cpp or a proxy, give `provider` any name
that is not in the list and say where the server is. `endpoint` takes:

| In `endpoint` | Meaning |
|---|---|
| `baseUrl` | The server's address, with the path its API is under. |
| `api` | The API it speaks: `openai-completions` (the default, which nearly all such servers have), `openai-responses` or `anthropic-messages`. |
| `apiKeyEnv` | The environment variable that holds its key. Leave it out for a server that asks for none. `login NAME` can store a key as well. |
| `images` | `true` if the model takes pictures. Without it the model cannot use `look`. |
| `reasoning` | `true` if the model thinks before it answers and takes `reasoningEffort`. |
| `contextWindow`, `maxTokens` | How much the model can read and write, in tokens: 32768 and 4096 unless you say otherwise. |
| `compat` | [Pi's compatibility settings](https://github.com/earendil-works/pi/tree/main/packages/ai#openai-compatibility-settings) for the API, for a server that needs one changed. |

### What the model has to manage

It must call tools reliably: everything the mirror does for a request goes
through one. The instructions and the tools together are about 7,500 tokens
and are sent with every request, so a context of 16,000 tokens is the least
that works. Small local models often answer in words where they should have
called a tool.

So far one model has been run for real, `gpt-6-luna`, and the wording of the
instructions was settled with it. Every provider goes through the same loop
of Pi's, and the others are covered by the tests with stand-ins only. Before
you rely on a model, let it answer the trial set:

```bash
node scripts/try-brain.mjs --provider anthropic --model claude-haiku-4-5
```

It says some sixty requests to the model against a fake mirror and reports
how many came out as expected and how long each took.

After a change of `provider`, `model` or `endpoint`, restart the companion.
`node src/cli.js health` then says whether the model answers, and if not,
why: nobody signed in and how to sign in, a model the sign-in is not offered
and which ones it is, or what the provider said to a first question.

### If you already use the GitHub Copilot CLI

One provider in the list needs no sign-in of its own: `copilot-cli` is
GitHub Copilot with the sign-in the
[Copilot CLI](https://docs.github.com/en/copilot/how-tos/copilot-cli) has on
the same account (`copilot`, then `/login`). `models copilot-cli` lists what
it is offered.

The companion reads the token the CLI keeps in `~/.copilot/config.json` (or
in the folder `COPILOT_HOME` names). The CLI writes it there on a machine
without a keychain, as most servers are; on a desktop it is in the keychain,
where the companion does not look, and a token in `COPILOT_GITHUB_TOKEN`
serves instead.

Know what this does before you rely on it. The requests go to GitHub with
the CLI's sign-in and name the CLI as the program they belong to, because
GitHub offers that sign-in its models only to requests that do. GitHub does
not document this for other programs, so it may stop working without notice,
and it draws on your Copilot allowance as the CLI would. `github-copilot`,
in the same list, is Copilot with a sign-in made for the purpose
(`login github-copilot`).

With `copilot-cli` the companion asks over a WebSocket that stays open, where
the model takes that, as the CLI does: a call comes back about 0.3 seconds
sooner and does not grow slower as a conversation grows. Set
`MIRROR_COMPANION_NO_WEBSOCKET=1` in the companion's environment to keep to
plain requests.

### Settings

| Setting | Meaning |
|---|---|
| `listen` | The address and port the companion listens on. |
| `secret` | Shared with the mirror. Every request must carry it. |
| `mirror` | The mirror's address, port and token. `pair` fills this in. |
| `provider` | Whose model answers: a provider from the list `node src/cli.js providers` prints, or the name of a server of your own. Empty until you choose. See [Choosing a model](#choosing-a-model). |
| `model` | The model, by the name its provider knows it by. `node src/cli.js models NAME` lists a provider's. Empty until you choose. |
| `reasoningEffort` | How hard the model thinks: `minimal`, `low`, `medium`, `high`, `xhigh` or `max`, cut to what the model can do. `low` is the default: with the one model tried it was measured to answer in about 2 seconds where the model's own default took 3.3. `default` and `none` ask for no thinking, which a model that cannot do without takes as its own default. |
| `endpoint` | Where a server of your own is; only with a `provider` that is not one of Pi's. |
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
| `show_moment`, `end_moment` | Puts something on the glass for a while, beside the answer, and takes it down: a countdown that runs, words written large, a list, a chart, a drawing. See [Moments](#moments). |
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

### Moments

A moment is something the model puts on the glass for a while as part of an
answer, and which the mirror takes away by itself when its time is up. There
are five kinds: `text` (words, as large as their box allows), `countdown`
(runs down on the glass to an instant, with a line that shortens),
`list` (up to eight rows), `chart` (two to twelve values as bars or a line)
and `drawing` (up to forty lines, circles, rectangles, paths and words on a
square of 100 by 100 units). Each may carry a small title, a colour and a
gentle motion (`pulse`, `float`, `spin`).

The model describes a moment; it does not program one. The mirror checks
every field (`Moments.java`) and draws the kind from them, so nothing a
model writes can run on the glass. Where a moment goes is decided by the
glass, which knows what is drawn where. Moments stand in one column in the
middle, in the order they came, and glide when one comes or goes. The column
keeps clear of where the answer appears while it can, lies over as few
widgets as it can, and is then as near as it can be to a little above the
middle; widgets under a moment fade out for as long as it is there and
return when it leaves. When the column is too tall, the older moments are
drawn smaller, oldest first, and when that is not enough the oldest leave
and the mirror no longer lists them. A list is as high as its rows need at
its size's writing, and a long row goes on under itself. Each moment stands
on a slight dark backing, for the film or photo behind it. A height or a
side is passed on only when a person asked for one.

A moment stays 45 seconds if nothing is said (a list 90, a chart 60), six
hours at most, and a countdown until 20 seconds after it has run out.
Showing the same `id` again replaces a moment where it stands. The glass
holds six at once. None is kept across a restart of Mirror Home: a timer is
therefore both a reminder on the board, which is announced when due and
survives, and a countdown to the same instant. The state the model reads
lists what is showing, so that "stop the timer" or "take that down" has
something to name.

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

For each request, the words and a summary of the mirror's state go to the
model service you chose: the mirror's local time and zone, whether the display is on,
the layout, the names of the films, the items on the board, and the weather
as the mirror has it, which includes the name of the place. So do the lines
it was asked to remember, and a picture of the glass when the model uses
`look`. The model's greeting and the hourly look send the same summary
without any words of yours. The briefing, the morning card, the card for
someone who was away and the reminder cards are made on this machine and
send nothing to the model. With a server of your own as the provider,
nothing leaves the house at all.

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
heard and answered. The secret and the mirror's token are never logged, and
neither is a sign-in to a model provider. Sign-ins made with `login` are in
`auth.json` beside the config.

## Test

```bash
node --test
```

runs the tests. They need no network, GPU, Python or sign-in: the
mirror, the model and speech-to-text are replaced by stand-ins in
`tests/fakes`. The model's stand-in is Pi's scripted provider, so the tests
run Pi's own agent loop.

Three scripts try the real parts:

```bash
node scripts/try-brain.mjs
node scripts/try-stt.mjs FOLDER_WITH_WAV_FILES
node scripts/fake-mirror.mjs
```

`try-brain` says a set of requests to the real model against a fake mirror
and checks what came of each, cards and briefings among them. It tries the
model in the companion's config unless `--provider` and `--model` name
another, and needs that provider's sign-in. `try-stt` runs the
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
| `src/brain.js` | The model: sessions, retries and time limits on Pi's agent loop. The only file that knows Pi's agent. |
| `src/providers.js` | Whose model: Pi's providers, a server of one's own, and the file of sign-ins. |
| `src/responses-socket.js` | Requests to the Responses API over a WebSocket that stays open, each going on from the one before, where the service takes them. |
| `src/prompt.js` | Every word the model is told. |
| `src/tools.js`, `src/tools/` | The tools, independent of the harness. |
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
