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

## What only a Mirror can show

- **The microphone.** The emulator has none. The factory software declares a
  microphone and the permission to record; what an app gets from it, at what
  distance, with how much noise, is unmeasured.
- **Speed.** The emulator runs on a desktop processor, where a command list
  takes 3 to 6 percent of real time. A Mirror's Cortex-A53 cores are many
  times slower; whether recognition keeps up with speech there has to be
  timed there.
- **Living beside Mirror Home.** A Mirror with Mirror Home 2.2.0 running
  reports about 400 MB of its 952 MB free. The recogniser needs about 120 MB
  of that, and its work must not cost the background video frames.

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
| `record --name NAME [--seconds N] [--source S] [--rate HZ] [--channels 1\|2] [--effects] [--tone HZ \| --play CLIP] [--volume STEP]` | Records the microphone, fetches the recording and prints its levels. `--source` is `MIC`, `VOICE_RECOGNITION`, `VOICE_COMMUNICATION`, `CAMCORDER` or `DEFAULT`. |
| `selftest --clips FOLDER [--commands commands.txt] [--volume STEP]` | Plays each clip on the device's speakers, records it with its microphone and recognises the recordings. Needs nobody in the room. |
| `decode [--clips FOLDER_ON_DEVICE] [--only PREFIX] [--commands commands.txt] [--gate]` | Recognises sound files on the device and times it. Without `--commands` it takes dictation. `--clips recordings` recognises what `record` recorded. |
| `listen [--seconds N] [--commands commands.txt] [--act] [--save NAME] [--no-gate] [--source S] [--input CLIP]` | Recognises the live microphone and reports how far recognition fell behind it. `--act` sends each command to Mirror Home, and puts its sleep schedule and brightness back when listening ends. `--save` keeps what the microphone heard and fetches it. `--input` hears a clip on the device at speaking pace in place of the microphone. |
| `tone [--hz N] [--seconds N] [--volume STEP]` | Plays a tone on the speakers. |
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
   picks up the Mirror's own speakers and whether that is understood.
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
