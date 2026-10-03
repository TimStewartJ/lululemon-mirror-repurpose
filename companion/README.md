# Mirror companion

The companion is a small server for a home network. It makes a MIRROR that
runs Mirror Home into something you can talk to. Mirror Home has five fixed
voice commands of its own. Anything else that is said after "Mirror" is sent
to the companion, which works out the words, lets a language model act on
the mirror through the mirror's own control API, and sends back one short
line for the glass to show. The mirror never speaks.

The companion also does three things by itself. It greets someone who walks
up after the display was dark for a while. It shows a caption when a reminder
on the board falls due. And once an hour it looks over the display and may
tidy one small thing.

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
| `proactive.greet`, `.reminders`, `.tend` | Switch each of the three things it does by itself. |
| `proactive.tendMinutes` | How often it looks over the display. |
| `proactive.quietHours` | From when to when, on the mirror's clock, it shows nothing by itself and changes nothing. `null` for never. |
| `keepUtterances` | How many recordings to keep. 0 keeps none. |
| `stateDir` | Where the companion keeps what it stores. |

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
node src/cli.js say-wav recording.wav
```

`health` names what is not ready and what to do about it. A recording for
`say-wav` is a WAV file at 16 kHz, mono, 16-bit.

## What it can do

The model has these tools and no others. It has no shell, no files and no web
access.

| Tool | Does |
|---|---|
| `get_state` | Reads the mirror: time, display, widgets, background, board, weather. |
| `look` | Takes a picture of what the glass shows. |
| `set_power` | Puts the display to sleep or wakes it. |
| `set_brightness` | Sets how bright the display is when awake. |
| `set_background` | Chooses a film, or a black or photo background. |
| `arrange_widgets` | Shows, hides, moves and resizes widgets. |
| `board_add`, `board_update`, `board_remove` | Notes, to-dos and reminders on the board. A timer is a reminder. |
| `say` | Shows a line when it greets. In a conversation its answer is the line, and it has no other. |
| `remember`, `forget` | Keeps or drops a line about the household. |
| `ignore` | Decides that the words were not meant for the mirror. |

A request within two minutes of the last one continues the same
conversation, so "make it bigger" works after "show the clock".

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
`look`. The greeting and the hourly look send the same summary without any
words of yours.

In the state folder, `~/.local/state/mirror-companion` unless you chose
another, the companion keeps:

- `utterances/`: the last 20 recordings, so that a mishearing can be listened
  to. Set `keepUtterances` to 0 to keep none.
- `activity.jsonl`: the last 200 exchanges, with what was heard and answered.
  `GET /v1/activity` shows them.
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
and checks what came of each. It needs the Copilot login. `try-stt` runs the
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
| `src/proactive.js` | The greeting, the reminders and the hourly look. |
| `src/brain.js` | The model: sessions, retries and time limits on the Copilot SDK. |
| `src/prompt.js` | Every word the model is told. |
| `src/tools.js`, `src/tools/` | The tools, independent of the SDK. |
| `src/layout.js` | The rules of the mirror's layout. |
| `src/state.js` | The summary of the mirror that the model reads. |
| `src/mirror.js` | The client for the mirror's API. |
| `src/stt.js`, `src/stt_worker.py` | Speech-to-text: the Python worker and what keeps it running. |
| `src/queue.js` | One agent run at a time. |
| `src/config.js`, `src/log.js`, `src/memory.js`, `src/activity.js`, `src/recordings.js`, `src/health.js`, `src/time.js`, `src/wav.js`, `src/reply.js`, `src/clock.js` | One small job each, named by the file. |

The wire formats between the mirror and the companion are in
[The assistant](../docs/assistant.md#protocol). The routes are
`POST /v1/utterance`, `POST /v1/ask`, `POST /v1/event`, `GET /v1/health` and
`GET /v1/activity`, which lists the latest exchanges.
