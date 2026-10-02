# Voice lab

The voice lab is an experiment, not a feature. Before voice control is
designed for Mirror Home, two things have to be known about a real Mirror:

1. how well its microphone hears a person in the room, and
2. whether spoken commands can be recognised on the Mirror itself, on
   Android 6 with a 2015 processor and about 1 GB of memory.

The lab is a small separate app, `android/voice-lab`, and a script,
`tools/voice_lab.py`, that drives it over adb. It is installed beside Mirror
Home for a trial and removed afterwards. Nothing of it is part of a Mirror
Home release, and Mirror Home does not use the microphone.

## How it works

The app has no screen. Its one service is started over adb with a command and
writes each result as JSON into its own folder on the device, where the script
reads it. It can:

- report what the device offers: processor, memory, microphone settings that
  open, the device's own noise and echo processing, speech engines;
- record the microphone and measure the recording: peak, the room's noise
  level, how far speech stands above it, clipping;
- recognise sound files on the device and time it;
- recognise the live microphone, and send what it understood to Mirror Home;
- play a tone or a clip on the speakers, also while recording.

Speech is recognised by [Vosk](https://alphacephei.com/vosk/) 0.3.75 with its
small English model, `vosk-model-small-en-us-0.15` (Apache 2.0, a 41 MB
download, 71 MB unpacked). The model is copied to the device; it is not in the
repository or in the app.

### The command list

A text file binds each sentence to one request to Mirror Home's
[control API](protocol.md):

```text
# what to say | what Mirror Home is asked to do
mirror go to sleep | POST /api/v1/automation/sleep
mirror wake up | POST /api/v1/automation/wake
mirror brighter | POST /api/v1/control/brightness {"value": 255}
mirror dimmer | POST /api/v1/control/brightness {"value": 60}
```

The recogniser is told to expect these sentences. That is what makes
recognition dependable and cheap on a small device: with a list it does about
an eighth of the work of open dictation, and it keeps understanding in noise
that defeats dictation. It treats the list as a small language rather than as
the only sentences possible, so other speech comes back as `[unk]`, as part
of a command (`wake up [unk]`) or as a mixture of two. The lab therefore
accepts a command only when the whole sentence is one, from its first word to
its last, and the recogniser's confidence in its least certain word is at
least 0.5 (`--confidence`).

### The gate

Recognition is the expensive part. With the gate on, sound reaches the
recogniser only while a frame stands clearly above the room's noise level,
including the third of a second before, so a quiet room costs next to
nothing.

## What the Android 6 emulator showed

Measured on 2026-10-01 on the [validation suite's](validation.md) virtual
Mirror (Android 6, API 23, 1 GB of memory), with Mirror Home 2.2.0 installed
beside the lab.

**It runs on Android 6, with one change.** Vosk 0.3.75 asks for JNA 5.18.1,
which Android 6 cannot load: from JNA 5.16.0 its `Structure` class uses
`java.util.function`, which Android only has from version 7. The lab pins
JNA 5.15.0, the last release without it.

**Memory.** The lab's process grows from about 10 MB to 113-125 MB with the
model loaded and recognising, well below the 300 MB the model's page
suggests. Loading takes 1.5 to 4.5 seconds on the emulator.

**Commands are understood.** Each command was spoken by two synthetic voices
at three paces and then degraded: with noise 20, 10 and 5 dB below the voice,
30 dB quieter, and as heard across an echoing room (0.6 s of reverberation,
noise 15 dB below the voice). Synthetic voices are easier to recognise than
people, so these are upper bounds.

| Command list | Understood |
| --- | --- |
| `mirror ...`, 4 commands | 144 of 144 |
| `hey mirror ...`, 4 commands | 144 of 144 |
| `mirror mirror ...`, 4 commands | 144 of 144 |
| `mirror ...`, 12 commands | 429 of 432 |

Two of the three misses were slow speech in the loudest noise, where the
pause after "mirror" ended the sentence early. Without a list, as open
dictation, the same model understood 23 of 24 clean commands but only 11 of
24 with noise 10 dB below the voice and 8 of 24 across the room, at eight
times the processing cost.

**Other speech rarely sets a command off.** Two hours of people reading
stories aloud (five LibriVox readers, public domain) went through the
recogniser as recorded and as heard across the room, with and without the
gate:

| Command list | As recorded | As recorded, gated | Across the room | Across the room, gated |
| --- | --- | --- | --- | --- |
| `mirror ...`, 4 commands | 0 | 0 | 1 | 1 |
| `hey mirror ...`, 4 commands | 0 | 0 | 1 | 0 |
| `mirror mirror ...`, 4 commands | not run | not run | 0 | 0 |
| `mirror ...`, 12 commands | not run | not run | 1 | 1 |

That is at most one wrongly accepted command in two hours of continuous
speech beside the device. Raising the confidence needed does not remove them
all: two of the five had full confidence. A longer wake phrase helps, and a
command set off by mistake should be one that is cheap to undo.

**It answers in about a second.** A command took effect 0.8 to 1.2 seconds
after its last word, which is the pause the recogniser waits for before it
calls a sentence finished.

**End to end.** Fed a stream at speaking pace (room noise, commands, other
talk), the lab put Mirror Home to sleep and woke it through its API on each
command and ignored the rest. Mirror Home stayed the same process, with
about 640 MB of the emulator's memory still free.

## What a Mirror showed

The emulator has no microphone and a desktop processor, so three things
could only be measured on a Mirror. Measured on 2026-10-02 on one (four
Cortex-A53 cores at up to 1.2 GHz, 929 MB of memory, Android 6.0.1) that was
running Mirror Home 2.2.0 with a background video:

**Its processor keeps up.** With the four-command list, 91 seconds of clips
were recognised in 24.5 seconds: a real-time factor of 0.27 on one of the
four cores, with the same answers as on the emulator. The model loads in 4 to
5 seconds. Recognising everything the live microphone heard took 34 percent
of one core, and recognition never fell more than 0.6 seconds behind the
microphone.

**It fits beside Mirror Home.** The lab's process is about 125 MB with the
model loaded and gives the memory back afterwards. The Mirror has about
390 MB free with Mirror Home alone and kept at least 279 MB free, above the
216 MB at which Android calls memory low. Mirror Home stayed the same
process, and its background video dropped no frame in five minutes of
listening. The warmest sensor rose from 41 to 49 degrees Celsius.

**The microphone works for an ordinary app.** All five Android sound sources
open, from 8 to 48 kHz, mono and stereo. As `VOICE_RECOGNITION` at 16 kHz a
quiet room reads about 60 dB below full scale, and a voice 15 to 20 dB below
it: about 45 dB above the room, without clipping.
`VOICE_COMMUNICATION` is about 25 dB quieter than the other sources. A
stereo recording's second channel is a weaker, different signal with mains
hum; nothing yet shows a second usable microphone. The device offers an echo
canceller and a noise suppressor but no gain control, and has neither a
speech recogniser nor a speech synthesiser installed.

**Spoken commands work.** In five minutes at the Mirror its owner spoke 16
complete commands. All 16 were understood, each with full confidence, and
Mirror Home carried each out about a second after its last word. Some two
minutes of ordinary conversation beside the Mirror in that time set off
none. Three other tries failed, each in a way a real feature can meet:

| Said | Heard | A feature should |
| --- | --- | --- |
| "mirror", a pause, "go to sleep" | two sentences, `mirror` and `go to sleep` | let the wake word open a few seconds in which the command may follow |
| "mirror, mirror go to sleep" | `mirror mirror go to sleep` | take a repeated wake word as one |
| "mirror, brightness down" | `mirror [unk]` | know more than one wording for a command, or hand free sentences to a larger model |

The first two rules would have understood 18 of 18, and add no wrong command
to the eight hours of read speech measured on the emulator.

**It hears across a room, by its owner's account.** Some of the 16 commands
were spoken from across the room and worked as well as those from arm's
length. The recording cannot say which. The Mirror evens out loudness before
an app gets the sound: nearly every command arrived with its peaks 3 to 7 dB
below full scale, 40 to 51 dB above the room. How far that reaches has not
been measured.

**The gate saves little in a lived-in room.** It was open 69 to 84 percent of
the time: the microphone is sensitive, and the gate opens just above the
level of a silent room. Recognising everything costs a third of one core, so
a feature can do without the gate or give it a higher threshold.

**A Mirror does not understand its own speakers.** Ten clips played at
volume step 9 of 15 reached its microphone near full scale, and none of the
eight commands in them was recognised. Step 9 is also loud in a room, so the
lab plays its sounds at step 4 unless told otherwise.

### Still open

- **An ordinary day.** How often a household's own talk sets off a command
  over hours, rather than minutes, has not been counted on a Mirror.

## Set up

Build the app and fetch the model:

```powershell
.\gradlew.bat :android:voice-lab:assembleDebug
New-Item -ItemType Directory -Force build\voice-lab | Out-Null
Invoke-WebRequest https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip -OutFile build\voice-lab\model.zip
Expand-Archive build\voice-lab\model.zip build\voice-lab\models
```

Save the [command list](#the-command-list) as `build\voice-lab\commands.txt`.

Connect the Mirror by USB with
[authorized ADB](provisioning.md#usb-access-and-recovery), find its serial
with `adb devices`, and install:

```powershell
python tools\voice_lab.py --serial SERIAL setup `
    --model build\voice-lab\models\vosk-model-small-en-us-0.15 `
    --commands build\voice-lab\commands.txt
```

The app is installed with `adb install -g`, which grants the microphone
permission. Nobody can answer a permission dialog on a Mirror, which is also
why this cannot be done over the air.

## Commands

Every command needs `--serial`. Results are kept under `build/voice-lab/`.

| Command | What it does |
| --- | --- |
| `setup [--model FOLDER] [--clips FOLDER] [--commands FILE]` | Installs the app and copies the model, sound clips and command list to it. |
| `info` | Processor, memory, the microphone settings that open, the device's own sound processing, speech engines. |
| `record --name NAME [--seconds N] [--source S] [--rate HZ] [--channels 1\|2] [--effects] [--tone HZ \| --play CLIP] [--volume STEP]` | Records the microphone, fetches the recording and prints its levels. `--source` is `MIC`, `VOICE_RECOGNITION`, `VOICE_COMMUNICATION`, `CAMCORDER` or `DEFAULT`. A tone or clip plays at volume step 4 of 15 unless `--volume` gives another. |
| `selftest --clips FOLDER [--commands commands.txt] [--volume STEP]` | Plays each clip on the device's speakers, at volume step 4 of 15 unless `--volume` gives another, records it with its microphone and recognises the recordings. Needs nobody in the room, and is heard by everybody in it. |
| `decode [--clips FOLDER_ON_DEVICE] [--only PREFIX] [--commands commands.txt] [--gate]` | Recognises sound files on the device and times it. Without `--commands` it takes dictation. `--clips recordings` recognises what `record` recorded. |
| `listen [--seconds N] [--commands commands.txt] [--act] [--save NAME] [--no-gate] [--source S] [--input CLIP]` | Recognises the live microphone and reports how far recognition fell behind it. `--act` sends each command to Mirror Home, and puts its sleep schedule and brightness back when listening ends. `--save` keeps what the microphone heard and fetches it. `--input` hears a clip on the device at speaking pace in place of the microphone. |
| `tone [--hz N] [--seconds N] [--volume STEP]` | Plays a tone on the speakers, at volume step 4 of 15 unless `--volume` gives another. |
| `pair` | Pairs the lab with Mirror Home as "Voice lab (temporary)", using the credential in `.secrets/mirror-background-video.json`, so that `--act` can command it. |
| `remove` | Revokes that pairing, uninstalls the app and deletes its folder with every recording. |

Sound clips are 16-bit WAV files at 16 or 48 kHz. On Windows a sentence can be
spoken into one:

```powershell
Add-Type -AssemblyName System.Speech
$voice = New-Object System.Speech.Synthesis.SpeechSynthesizer
$format = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000, 'Sixteen', 'Mono')
New-Item -ItemType Directory -Force build\voice-lab\clips | Out-Null
$voice.SetOutputToWaveFile("$PWD\build\voice-lab\clips\sleep.wav", $format)
$voice.Speak("mirror, go to sleep"); $voice.Dispose()
```

## A trial at the Mirror

1. `setup`, then `info`: the processor, the memory free, and which
   microphone settings open.
2. **Speed.** `decode --commands commands.txt` on a few minutes of clips. A
   real-time factor below 1 means the Mirror recognises faster than people
   speak. Watch Mirror Home's `GET /api/v1/health` meanwhile for its memory
   and its process.
3. **The microphone, with nobody there.** `record --name room --seconds 10`
   for the room's noise, then `selftest` to hear whether the microphone
   picks up the Mirror's own speakers and whether that is understood. Tell
   the household first: the Mirror speaks.
4. **Live, with a person.** `pair`, then
   `listen --commands commands.txt --act --save live --seconds 180`. Say each
   command at arm's length, from the middle of the room and from its far
   side, and watch the Mirror obey. The saved recording shows afterwards how
   loud the voice was at each distance, and
   `decode --clips recordings --only live` recognises it again with other
   settings. Repeat with another `--source` if the first hears poorly.
5. **An ordinary hour.** `listen --commands commands.txt --seconds 3600`
   with the room in normal use, to count commands nobody gave. With a
   command list the recogniser can only put out the list's words, so this
   keeps no record of what was said in the room.
6. `remove`.

## Privacy and removal

Recordings are sound from the room. The lab records only when `record`,
`selftest` or `listen --save` asks it to. Recordings are written to the lab's
folder on the Mirror and copied to `build/voice-lab/recordings/` on the
computer, which git ignores; nothing is sent anywhere else. `remove` deletes
the folder on the Mirror.

The lab's service is exported so that adb can start it, which means any app
on the device could ask it to record, and its Mirror Home credential lies in
its folder as a file. Both are acceptable for a trial on a Mirror that runs
nothing else, and both are reasons to run `remove` when the trial ends.
