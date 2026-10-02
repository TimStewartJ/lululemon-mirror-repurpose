# Voice commands

From Mirror Home 2.3.0 a Mirror can be told what to do. Say its name and
then a command:

> "Mirror, go to sleep."

Speech is recognised on the Mirror itself, by the open-source
[Vosk](https://alphacephei.com/vosk/) recogniser. No sound leaves the
Mirror, none is stored, and nothing listens until you switch voice on.

## What you can say

| Say "Mirror, ..." | What happens |
|---|---|
| "go to sleep" or "good night" | The display goes dark and stays dark, as with **Sleep** in the controls: for four hours, or until it is woken. Walking past does not wake it. |
| "wake up" or "good morning" | The display comes on. After that it follows its schedule and presence sensing again. |
| "brighter" or "brightness up" | The display gets a step brighter, now and whenever it wakes. Where a light sensor sets the brightness, the Mirror says so and changes nothing. |
| "dimmer" or "brightness down" | A step dimmer, likewise. |
| "next video" or "change the video" | The next background video, if the dashboard shows one. |

Anything but "go to sleep" also wakes a dark Mirror, and counts as someone
being there, like being seen by the camera.

You can pause after "Mirror". The glass then shows **Listening**, and a
command counts for the next six seconds without the name. If the Mirror
heard its name but not a command it knows, it shows **Didn't catch that**
and waits another six seconds.

The Mirror never speaks. It shows what it did in a line near the bottom of
the glass: **Sleeping**, **Awake**, **Brighter**, **Next video**.

## Setting it up

Voice needs three things besides Mirror Home 2.3.0 or later: a speech model
on the Mirror, Android's permission to use the microphone, and the switch.
All three are done from the computer that is paired with the Mirror for
[background videos](background-videos.md); none needs the USB cable if the
[OTA supervisor](ota-updates.md) is installed.

1. **The speech model.** It is 39 MB, too large to travel inside an update,
   so it is sent by itself:

   ```powershell
   .\tools\voice.ps1 install-model
   ```

   This downloads Vosk's small model for US English from its makers, checks
   it against the checksum kept in `tools/voice.py`, and sends it to the
   Mirror, which unpacks and checks it. It stays through updates.

2. **The microphone permission.** A Mirror has nothing to tap "Allow" on.
   An installation over USB grants it. After an update over the network,
   grant it through the OTA supervisor (version 1.2.0 or later):

   ```powershell
   .\tools\ota.ps1 grant-permission microphone --confirm
   ```

   With the USB cable instead:
   `adb shell pm grant dev.mirror.repurpose android.permission.RECORD_AUDIO`.

3. **The switch.** In the controls under **Voice**, or:

   ```powershell
   .\tools\voice.ps1 on
   ```

The order does not matter. Mirror Home starts listening within a quarter of
a minute of the last of the three, and again by itself after every restart.

```powershell
.\tools\voice.ps1 status
```

says where it stands, what is missing, how loud the room is to the
microphone, and what was said to the Mirror lately. `.\tools\voice.ps1 off`
switches voice off, and `remove-model` takes the model off the Mirror.

## What it takes to be heard

A command counts only if all of this holds:

- It is addressed to the Mirror: the name comes first, in the same breath
  or up to six seconds before. "Mirror, mirror, go to sleep" is fine.
- The sentence is the command and nothing else. "Mirror, go to sleep" in
  the middle of a longer sentence is talk.
- The recogniser is sure of every word. It reports how sure it is, and
  commands it has doubts about are dropped and counted as `unsure`.

The wordings were chosen so that no two commands sound alike. "Turn on" and
"turn off" were tried and taken for each other in noise, which is why sleep
and wake have the words they have.

The recogniser is told which sentences to expect, which is what lets a
processor from 2015 keep up. Told only the commands, it makes commands'
words of whatever it hears. It is therefore also given about 400 of the
commonest words of spoken English, so that talk has something better to be
taken for than the Mirror's name. Mirror Home drops those words unread.

In the trial on a Mirror ([Voice lab](voice-lab.md)), with a shorter list,
all 16 complete commands its owner spoke were understood, some of them from
across the room, and two minutes of conversation in the room set off
nothing. On a computer, with the same model and the list and rules above,
four hours of people reading aloud, half of them made to sound as if across
a room, set off no command. Three times in those four hours the recogniser
heard the Mirror's name followed by other words, which shows **Didn't catch
that** and does nothing else. Without the common words it was 23 times,
counting the name alone and commands in doubt.

What does not work:

- Speech from the Mirror's own speakers. Its microphones hear them near
  full scale and the words do not survive.
- Languages other than English. The command list is English, and the usual
  model is for US English.
- A whisper, or a command spoken while someone else talks louder.

## What it costs the Mirror

The recogniser runs in a process of its own, apart from the dashboard. On a
first-generation Mirror it loads its model in about five seconds, holds
about 125 MB of the 930 MB of memory, and uses about half of one of the
four processor cores for as long as it listens: a third in a quiet room,
more while people talk. Unbroken speech takes it a third as long to
recognise as it lasts, so it catches up: it mostly keeps within a quarter
of a second of the microphone, and fell up to two seconds behind during
talk. The dashboard and its background video were measured beside it and
lost no frame.

If the recogniser stops, Android starts it again and the dashboard does not
notice. If it stops five times in ten minutes, Mirror Home leaves it alone
for ten minutes. Switched off, its process ends.

While Android installs an app, the recogniser stops and gives its memory
back, and it starts again when the installation has ended. This is for
updates of Mirror Home. Android 6 compiles an app as it installs it, and a
Mirror with voice listening has too little memory left for that: an update
ran one so short that Android stopped the factory launcher and the OTA
supervisor, and restarted Mirror Home four times, in the 44 seconds before
the update went in. With the recogniser out of the way, and with the
smaller app that 2.3.0 gives Android to compile, an update on the same
Mirror was in place in ten seconds and stopped nothing else.

## What the Mirror keeps

No sound. Of what it recognises, Mirror Home keeps, in memory until it
restarts:

- how many stretches of speech the recogniser heard, and how many of them
  were commands, the name alone, not understood, or too uncertain;
- the last twenty sentences that began with the Mirror's name, with every
  word that is not a command's word replaced by "unknown". Speech that was
  not addressed to it is counted and dropped.

Paired devices can read this, and the [health report](validation.md#the-health-report)
includes it.

## When it does not listen

`.\tools\voice.ps1 status` and the controls name one of these:

| State | Meaning | What to do |
|---|---|---|
| Off | Voice is switched off. Nothing listens. | Switch it on. |
| No speech model | Switched on, but no model is installed. | `.\tools\voice.ps1 install-model` |
| No permission | Android has not allowed Mirror Home the microphone. | Grant it; see above. |
| Paused | Android is installing an app, usually an update of Mirror Home, and the recogniser has stopped to leave it the memory. It starts again when the installation ends, and after three minutes at the latest. | Wait. |
| Starting, loading | The recogniser's process is starting or reading the model. | Wait a few seconds. |
| Listening | It works. If it adds that the microphone is silent, Android delivers no sound at all. | Restart the Mirror. |
| Error | The recogniser could not start, with the reason. "The speech model could not be loaded" means the installed model is damaged or not a Vosk model; install it again. Mirror Home tries again every half minute. | |

## Another speech model

`.\tools\voice.ps1 install-model --file MODEL.zip` sends any Vosk model
archive of up to 96 MB instead. It must be one of the small models, whose
graph comes in two parts (`graph/HCLr.fst` and `graph/Gr.fst`); only those
can be given a list of sentences to expect. Every word of the commands must
be in its vocabulary, so it must be an English model.

## For developers

The command list is `VoiceCommands.java`, the rules about the name are
`VoiceInterpreter.java`, and both have unit tests. The API is described in
[Control protocol](protocol.md#voice). The
[emulator suite](validation.md#how-the-suite-speaks) plays recordings to a
debug build in place of a microphone.
