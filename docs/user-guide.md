# User guide

This guide assumes Mirror Home is installed, selected as default HOME, and
verified after reboot. For a stock unit, use
[Getting started: stock Mirror to working appliance](getting-started.md)
first. A QR code is Wi-Fi/browser onboarding, not a stock-device installer.

## First run after installation

When Mirror Home has neither an app-managed SSID nor an active Wi-Fi
connection, it attempts a private WPA2 Wi-Fi Direct group and, if ready,
displays two QR codes:

1. scan **Join Mirror Setup Wi-Fi**,
2. scan **Open setup**,
3. enter the six-digit pairing code shown on the Mirror,
4. in **Settings > Wi-Fi**, type the household SSID and password and select
   **Connect**; name the Mirror in **Settings**.

The form configures a WPA-PSK personal network; it has no network scan picker.
Keep USB recovery available until the household SSID/IP and LAN controls work.
See [Provisioning](provisioning.md#browser-wi-fi-configuration) if the setup
group or connection fails.

The browser transfers its new credential to the Mirror's LAN address in a URL
fragment. Fragments are not sent in HTTP requests and are removed from browser
history after the destination page loads. A phone on setup Wi-Fi must rejoin
the household network to reach that destination.

If Wi-Fi is already configured, scan the single control QR code. The control
application is hosted by the Mirror itself on port `8787`; nothing is
installed on the phone or computer.

## Pairing additional devices

If the native setup screen is visible, scan its control QR and enter the code.
Otherwise, in a paired browser open **Settings > Paired devices** and choose
**Show code**, then open the Mirror's LAN control address on the new device
and enter that code. The **Pairing code** widget in **Display > Arrange the
mirror** does the same on the glass for as long as it is shown.
Every browser receives an independent random credential.
Use **Settings > Paired devices** to review or revoke one device without
signing out the others.

The Mirror accepts a code only while one is on display: on the setup screen,
in the Pairing code widget, or after **Show code**. At any other time pairing
is closed and attempts are refused without being checked. Codes expire after
ten minutes and work once. Five wrong codes lock pairing for 30 seconds and
each further lockout doubles, up to an hour; a correct code or **Show code**
clears the lock.

## The control application

The control application has four sections:

- **Home**: a live miniature of what the mirror is showing right now, the
  brightness slider (applies when released), wake/sleep, what is playing, and a
  way to play a media link.
- **Display**: what the mirror shows, the layout editor, weather, and photos.
  It also manages private background videos stored separately from the app.
- **Schedule**: sleep/wake times, wake brightness, and presence sensing.
- **Settings**: name, voice commands, clock, Wi-Fi, the recovery setup
  network, paired devices, version/address details, and the Mirror's health.

## Dashboards

**Display > What the mirror shows** offers two sources; selecting one applies
it immediately:

- **Mirror**: the built-in layout with a hairline clock, weather, and any
  widgets you arrange. Photos live here too: place one or more Photo widgets,
  up to a full-bleed frame that rotates through the library.
- **Web page**: any HTTP(S) page reachable by the Mirror, such as a Home
  Assistant dashboard.

The built-in surfaces are designed for two-way mirror glass: true black
backgrounds, thin white type, and stroke weather glyphs that stay legible
through the reflection.

The built-in dashboard can show current weather, today’s high/low and rain
chance, plus an optional hourly strip. Search for a city/postal code or enter
coordinates manually. The coordinates are stored privately and sent over HTTPS
to Open-Meteo to request forecasts every 30 minutes. Forecast results are cached
privately on the Mirror; cached values remain available during an Internet
outage and are labeled stale after 90 minutes. Browser-based location detection
is available only from a secure HTTPS or localhost control origin; normal LAN
HTTP users should use city search.

The visual editor supports a configurable snap grid, edge/center guides,
undo/redo, widget locks, layers, keyboard movement,
duplicate instances, and validated JSON import/export. Canonical widgets remain
available and can be hidden; duplicated instances can be deleted.

The **Photo** widget places a framed photo anywhere in the layout. Choose one
library photo or let the frame rotate through the whole library every twenty
seconds with a slow crossfade; choose **Fill** to crop to the frame or
**Whole** to letterbox; alignment sets the crop focus. Duplicate frames for a
collage. Tapping a photo in the library places it in a frame. Photo frames,
backgrounds, and the control application's library grid use scaled,
EXIF-oriented variants prepared on the Mirror so phone pictures display upright
and the display never decodes a multi-megapixel original.

### Notes

**Home > Leave a note on the mirror** posts a note (up to 1,000 characters,
line breaks kept) that appears on the glass within a few seconds without
touching the layout. The list beneath the composer shows every note on the
Mirror with edit and delete controls; the Mirror keeps up to 50 notes, newest
first. Notes are content rather than layout, so any paired phone can post one,
and other paired browsers see the change at their next status refresh.

The **Note** widget decides how notes appear. Its **Shows** menu offers:

- **Newest note** — the most recent note (the default),
- **Rotate through notes** — cycle through all notes every twenty seconds with
  a short fade,
- **All notes** — stack every note in the widget,
- **Always: …** — pin one specific note,
- **Fixed text** — the classic inline tagline typed into the widget itself.

**Size** chooses **Fit** (shrink the text until it fits the box) or a fixed
small, medium, or large size that clips from the top instead of shrinking;
**Weight** picks thin, light, regular, or medium type. Duplicate the widget to
show, for example, a pinned house rule beside the rotating family notes. If
nothing on the glass is showing notes when you post one, the Mirror turns on
the canonical Note widget with **Newest note** so the note is seen; a visible
fixed-text tagline is left alone.

### The board

Notes are what people type. The board is for programs: a script, a home hub
or an AI agent on your network can post notes, to-dos and reminders to the
Mirror, each with a due time and a moment at which it clears itself.
[The board](board.md) describes how a program does that.

The **Board** widget lists what was posted. It is hidden until you turn it
on in **Display**. It shows overdue items first, then what is due within the
hour with a countdown, then the rest, and strikes through what is done. When
there is more than fits, it shows a page at a time and moves on every ten
seconds. Its **Heading** is a title of your own, **Shows** narrows it to
to-dos, reminders or notes, and **Size** picks small, medium or large type.
An empty board takes no room on the glass.

**Home > On the board** lists every item with who posted it. Mark a to-do or
reminder **Done**, remove an item, or **Clear** the board. If the widget is
hidden while there are items, **Show** turns it on. A program gets its access
by pairing, like a phone: **Settings > Paired devices > Show code** gives it
a code, and revoking it there ends its access. Its token can do everything
the controls can, so pair only programs you trust.

Remote dashboards automatically fall back to a local clock when their main
page returns an error or becomes unreachable. Mirror Home retries every minute.

### Visual layout editor

Open **Display > Arrange the mirror** from any paired phone or desktop. The
canvas uses exactly the same renderer as the mirror, so what you see is what the
glass shows:

- drag a widget to move it; drag its corner handle to resize it,
- tap a widget chip to select it, or its eye to show or hide it,
- change visibility, lock, alignment, opacity, layer, and exact geometry,
- choose a solid color, gradient, or library photo background,
- choose an uploaded H.264 video background, Fill/Whole fitting, and dimming,
- adjust photo dimming and the text/detail colors,
- choose what each Note widget shows and how large and heavy its type is,
- give the Board widget a heading, a size, and the kind of item it lists,
- select **Save & show on mirror** to apply the layout immediately.

The **…** menu exports or imports a layout file and offers **Reset to
default**, which restores the reflection-first composition without changing
Wi-Fi, pairings, photos, or schedules. With no custom web URL selected, unpaired or disconnected states use the native
setup screen instead of the saved layout. A configured custom URL takes
precedence and uses its offline fallback on load failure.

The photo library accepts JPEG, PNG, WebP, and GIF images up to 20 MB each,
with a 250 MB total library limit. Photo bytes are served only to the Mirror's
loopback interface; remote browsers can list, upload, and delete them only with
an authenticated API request.

### Background videos

**Display > Background videos** accepts H.264 MP4 files up to 256 MiB. Uploads
are validated against the Mirror's hardware decoder and stored privately outside
the APK, so app updates remain small. The library keeps up to 12 videos and
768 MiB while reserving at least 512 MiB of free device storage.

**Upload MP4** automatically activates a successfully uploaded video. Choose
**Use** on an existing library card or select **Video** in the background editor.
Activation switches a custom web dashboard back to the built-in Mirror layout.
The previous active video remains available through **Use previous video**.
Active videos cannot be deleted. Audio tracks are ignored, display sleep pauses
the loop, and full-screen casting temporarily takes over the same decoder before
the background resumes.

**Display > Video schedule** changes the background by time of day, for example
a bright film from 06:00 and a calmer one from 19:00. Each time shows its video
until the next time, wrapping past midnight, on the Mirror's local clock.
Turning the schedule on selects the built-in layout with a video
background. While it runs, **Show now** (or **Use previous video**) shows another
video only until the next scheduled change; **Resume schedule** ends that sooner.
Videos fade through black when the background changes. Scheduled videos cannot
be deleted until they are removed from the schedule.

See [Background videos](background-videos.md) for storage, CLI, validation, and
recovery details.

## Clock and schedules

Pairing from a browser copies its IANA time-zone name to the Mirror, and
**Settings > Clock** changes it. The clock, the sleep schedule and the video
schedule follow daylight-saving changes on their own.

The vendor firmware has no usable time-zone rules, so Mirror Home carries its
own. Each release includes the UTC-offset changes of every IANA zone for the
following ten years, and every time a paired browser opens the controls it
sends the changes it knows of, which take precedence. A browser is therefore
needed only if a government changes its rules after your Mirror Home release
was built. **Settings > Clock** shows the next change the Mirror expects.

The **Schedule** tab can:

- set local sleep and wake times,
- set wake brightness,
- sleep or wake immediately with a four-hour manual override,
- use an ambient-light sensor when one exists,
- wake when the camera sees movement and sleep after a configurable period
  without movement.

The schedule acts as quiet hours for motion sensing: movement wakes the display
only inside the configured wake window. With the schedule disabled, motion
sensing operates all day. Manual sleep/wake overrides remain authoritative for
four hours, and active media playback is never stopped merely because the room
is still.

The IFC6309 MIRROR reports no ambient-light sensor, but its built-in camera can
run a 160x120 local motion monitor. It compares luminance changes, immediately
discards every frame, and performs no recording, face recognition, or network
upload. If camera permission or the camera itself is unavailable, presence
automation fails open and leaves the display awake inside its schedule.

Sleep fades the display to black over three seconds, dimming the picture and
the backlight together, then stops media entered by schedule or manual action
and turns the physical panel backlight completely off while Android, the
camera monitor, and network controls remain active. Wake fades back in to the
selected brightness over two seconds. A change of mind mid-fade reverses
smoothly from the current level.

## Voice commands

Say "Mirror, go to sleep", "Mirror, wake up", "Mirror, brighter", "Mirror,
dimmer" or "Mirror, next video". The Mirror recognises this by itself: no
sound is stored or leaves it, and it answers on the glass, never out loud.
Nothing listens until you switch it on. It also knows greetings: "Mirror,
good morning", "good afternoon", "good evening", "I'm home" and "good
night", which wake it or, for the last, send it to sleep.

**Settings > Voice** has the switch, and under it where voice stands:
**Listening**, or what it is waiting for. Two things are needed once:

- a speech model on the Mirror. **Install** takes the model's zip file from
  this device; from a computer, `.\tools\voice.ps1 install-model` downloads
  and sends it in one step;
- Android's permission to use the microphone, which an installation over USB
  grants and an update over the network does not. The page then says how to
  grant it from a computer.

**What to say** lists every command with its wordings. **Heard
lately** lists what was said to the Mirror since it last started and what it
did, which is where to look if it ever acts on its own. See
[Voice commands](voice.md) for how it decides, what it costs the Mirror and
what it keeps.

## The assistant

With an assistant, the Mirror can be asked for more than its own commands:
"Mirror, remind me to call the dentist at nine", "Mirror, something calmer
in the background", "Mirror, put the weather under the clock". A companion
program on a computer of yours works out what is meant and does it, and the
Mirror answers low on the glass, with what it understood you to say above
the answer. It needs voice commands to be set up and a companion to be
installed; [The assistant](assistant.md) says how.

With an assistant a greeting is answered too. "Mirror, good morning" brings
the weather, what is due today and the reminders that came due while nobody
was looking; "Mirror, I'm home" brings what came up while you were out; and
"Mirror, good night" shows what tomorrow holds before the display goes dark.

**Settings > Assistant** has the switch, the companion's address and key,
and under the switch where it stands: **Connected**, or what is missing.
**Character** gives the Mirror a small figure that stands above its answers
and listens, thinks and answers along; there are four, and none unless you
choose one. The field below takes a typed request, which is the quickest way
to try it, and **Asked lately** lists what was asked and answered since the Mirror
last started.

While the assistant is on, the sound of what you ask it leaves the Mirror
for that computer. The Mirror's own commands and greetings stay on the
Mirror, and nothing else that is said in the room is sent.

## Media

FCast v3 senders discover the Mirror automatically on TCP `46899`. Direct
HTTP(S), HLS, DASH, and RTSP URLs can also be played from **Home**.
Direct-UI playback starts muted by default.

See [Streaming](streaming.md) and [Casting roadmap](casting-roadmap.md).

## Software updates

Mirror Home can be updated over USB with the same release key using
`.\tools\mirror.ps1 install-home --variant release`. This does not require
the helper or device-owner enrollment. See
[installation and signing](getting-started.md#4-sign-and-install-mirror-home).

For optional LAN updates, complete the separate
[OTA supervisor enrollment](ota-updates.md#one-time-provisioning) first:

```powershell
.\tools\ota.ps1 status
.\tools\ota.ps1 push PATH_TO_SIGNED_MIRROR_HOME_APK
```

Only a newer release signed by the configured Mirror release certificate is
accepted. The supervisor saves the current APK, installs the candidate, and
restores the saved release automatically if the Home API does not become
healthy. See [LAN OTA updates](ota-updates.md).

After an update the factory launcher's setup screen can appear on the glass.
Android starts that launcher while it replaces Mirror Home. Mirror Home takes
the display back by itself after about fifteen seconds; from 2.2.0 on, there
is nothing to do.

## Health

**Settings > Health** says how the Mirror itself is doing, so a problem can be
found without standing in front of it:

- **Running since** and **Last start**: when Mirror Home last started and
  why: after an update, after the Mirror restarted, after a crash, or after
  Android stopped the app.
- **Crashes**: how many have been recorded, and when and what the last was.
- **Dashboard**: whether the glass is showing the dashboard, is asleep, or is
  covered by another screen, such as the factory launcher's, and for how
  long. Mirror Home brings a covered dashboard back by itself, so that should
  last seconds; **brought back** says how often it has had to since it
  started. Script errors on the dashboard page are counted here.
- **Memory** and **Storage**: what Mirror Home uses and what is left.
- Under **Uptime**, a line appears when the Mirror asks to be restarted. A
  Mirror has less than a gigabyte of memory, and its factory software leaks
  some of it every day. After a week or more, sooner with voice commands on,
  memory runs short: the Mirror gets slower and Android keeps stopping
  background programs, the updater among them. Nothing but a restart gives
  that memory back, and the Mirror cannot restart by itself, so it says so
  here, and with an assistant also when you greet it. Switch its power off
  and on; everything comes back by itself within a minute or two.
  [Staying up for weeks](#staying-up-for-weeks) stops most of that leak.
- **Updater**: whether the optional OTA supervisor is ready. **Kept running
  by Mirror Home** means Android will not end it when memory is short, which
  needs supervisor 1.3.0. An earlier one is restarted by Android now and
  then, so **Not answering right now** for a moment is normal with it; **Not
  answering since** a time is not.

**Full report** holds every detail and is what to attach to a bug report. It
contains no credential, pairing code or Wi-Fi name, but it does include the
address of a web dashboard if you use one.

From a computer, `.\tools\validate.ps1 mirror` reads the same report and lists
anything that needs attention. See [Validation](validation.md).

## Staying up for weeks

Left alone, a Mirror runs short of memory after about nine days, sooner with
voice commands on, and then needs its power switched off and on. Most of
that is one leak: Android keeps looking for other Wi-Fi networks while it is
connected to yours, every few minutes, and a program of the Mirror's factory
software keeps a little more memory with every look and never returns it.

**Settings > Wi-Fi > Look for networks only when disconnected** stops those
looks. It is off until you turn it on. What changes:

- The Mirror stays on the network it has. If you have saved several, it no
  longer moves to a better one while the first still works.
- A Mirror that loses its network looks for it and joins it again, as before.
  For as long as it has none, Mirror Home hands the switch back to Android,
  and the line under it says that Android is looking for a network.
- Setting up Wi-Fi from the controls works as before.

The line under the switch says whether Android took it. Android forgets the
switch when it restarts, so Mirror Home sets it again by itself every time.
Memory that was already lost comes back only with one restart; from then on
it should not be lost again. `.\tools\validate.ps1 mirror` prints how many
looks have still arrived while connected (`scanGuard.scans.sinceApplied`),
which should stay at none, and fails if there are more than two.

If you use [LAN updates](ota-updates.md), install OTA supervisor 1.3.0 as
well, once, over USB. Mirror Home then keeps the supervisor running even
when memory is short, so an update or a rollback is never refused for that
reason. See [Updating the supervisor](ota-updates.md#updating-the-supervisor).

## Recovery

If normal Wi-Fi is unavailable, use **Settings > Setup network > Start** in
the paired page while connected through USB. Automatic group creation at
startup requires both no managed SSID and no active Wi-Fi connection; reboot
alone does not start it for an unreachable saved network. QR codes also
depend on the native setup screen being visible. See
[Provisioning](provisioning.md#qr-and-wi-fi-direct) for limitations.

The stock launcher and factory system APKs remain installed. See
[Recovery](recovery.md) before using the transient privileged helper.
