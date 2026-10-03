# Speech clips for the validation suite

Four short recordings of synthetic speech, 16 kHz mono, that the Android 6
emulator suite plays to Mirror Home in place of a microphone (an emulator has
none worth the name):

| File | Says | Voice |
| --- | --- | --- |
| `mirror-go-to-sleep.wav` | "Mirror, go to sleep." | `en_US-ljspeech-medium` |
| `mirror-wake-up.wav` | "Mirror, wake up." | `en_US-norman-medium` |
| `talk-of-sleep.wav` | "I am going to sleep early tonight." | `en_US-norman-medium` |
| `mirror-what-is-the-weather.wav` | "Mirror, what is the weather like today?" | `en_US-ljspeech-medium` |

The first two are commands. The third is talk that holds a command's words
without the Mirror's name, and must do nothing. The fourth is a request
that is no command: with the assistant switched on, its sound must reach
the companion from before its first word to after its last.

## Where they come from

They were spoken by [Piper](https://github.com/OHF-Voice/piper1-gpl) 1.8.0
(`length_scale` 1.05), resampled from 22.05 kHz to 16 kHz, trimmed, and set
to half of full scale with 0.3 s of silence before and 0.4 s after. Both
voices were trained from nothing on public-domain recordings: LJ Speech, and
readings from LibriVox. No person's recording is in these files, and they
are offered under this repository's licence.

Clips of people in a household, such as the ones `tools/voice_lab.py` makes,
never belong here.
