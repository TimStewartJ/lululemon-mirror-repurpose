# Validation

Two commands check Mirror Home without anyone watching the glass:

```powershell
.\tools\validate.ps1 emulator            # the full suite on an Android 6 emulator
.\tools\validate.ps1 mirror              # a read-only health check of your Mirror
.\tools\validate.ps1 mirror --exercise   # the same, then a short test drive of it
```

On macOS or Linux run `python3 tools/validate.py` with the same arguments.

## The Android 6 emulator suite

The MIRROR runs Android 6.0.1 (API 23) and draws its dashboard in the
Chromium 44 WebView that shipped with it. The Android SDK's
`system-images;android-23;default;x86_64` image has the same API level and the
same WebView generation (44.0.2403), so it reproduces what most often breaks
on the Mirror and nowhere else: JavaScript and CSS that a 2015 browser engine
does not understand, and Android 6 platform behaviour.

The suite creates its own virtual device shaped like the Mirror: a 1080x1920
portrait panel at 240 dpi with no navigation bar, no touchscreen and no keys,
1 GB of memory and a 128 MB app heap. It boots it without a window, installs
a debug build of Mirror Home on empty storage, and makes it the preferred
HOME app in Android's own chooser, as an owner does. The emulator's launcher
stays installed as the second HOME app, the part a Mirror's factory launcher
plays. The suite then uses Mirror Home the way an owner would. It pairs with
the code on the glass, changes settings through the control API, reads the
dashboard page through WebView DevTools, and looks at the screen itself.

Nothing but Android starts Mirror Home during a run. Where a check stops it,
reboots, or installs an update, the dashboard has to come back by itself, as
it must on a Mirror that nobody can touch.

### One-time setup

Besides the [build prerequisites](getting-started.md#1-confirm-scope-and-prepare-the-computer),
install the emulator and the Android 6 image, about 4.5 GB on disk:

```powershell
sdkmanager "emulator" "system-images;android-23;default;x86_64"
```

`sdkmanager` installs the newest Android Emulator, and not every release
runs an image from 2015 dependably. A run names the release it is using and
warns about one that is known to fail; see
[When the emulator stops answering](#when-the-emulator-stops-answering).

The emulator needs hardware virtualization: Windows Hypervisor Platform on
Windows, or KVM on Linux. Nothing else is installed; the suite uses only the
Python standard library. Its virtual device lives in `build/emulator/` and
takes about 2 GB while a run is in progress; the disk images are deleted when
the run ends.

### Running it

```powershell
.\tools\validate.ps1 emulator
```

This builds the debug APK, boots the emulator, runs every check, shuts the
emulator down, and exits non-zero if any check failed. A run takes about
five minutes. `python tools/check.py --emulator` runs it after the other
build, test and lint lanes.

| Option | Effect |
|---|---|
| `--quick` | Skip the check that reboots the emulator. |
| `--only NAMES` | Run only these checks, comma-separated. `install` and `first-pairing` always run, because the others need the pairing they make. |
| `--skip-build` | Install the debug APK that is already built. |
| `--apk PATH` | Install this APK instead. It may be a release build: `clock-switch`, `pairing-widget` and `notes` are then skipped, because only debug builds let their page be read, and the other checks judge the screen and the API. |
| `--window` | Show the emulator's screen while it runs. |
| `--keep-running` | Leave the emulator running afterwards, to look around. |
| `--serial emulator-NNNN` | Use an emulator that is already running. Mirror Home is reinstalled on it and made its HOME app. One with a touchscreen fails `health`, because Mirror Home leaves an attended device alone. |
| `--upgrade-from PATH` | Rehearse an update from this earlier APK instead of running the suite; see below. |
| `--density DPI` | Screen density of the virtual panel (default 240, the Mirror's). |
| `--output DIR` | Where to write the report and screenshots. |

The suite installs a debug build and resets Mirror Home's data, so it refuses
to run on anything but an emulator: the ADB serial must be `emulator-NNNN`,
the kernel must report itself as the emulator's, and the build fingerprint
must not be a Mirror's. It checks all three before it changes anything.

### What it checks

| Check | What must hold |
|---|---|
| `install` | Mirror Home installs and its API answers with the installed version. |
| `setup-screen` | Unpaired, the glass shows the native setup screen: a QR card on black and a six-digit pairing code. |
| `first-pairing` | The code on the glass pairs a client. A wrong code is refused, a used code does not work twice, and the client list needs a credential. |
| `dashboard` | The built-in dashboard renders in the Android 6 WebView with a clock, a date and no script errors, on a screen that is otherwise true black. |
| `clock-bundled` | A zone follows the bundled daylight-saving table, an offset the zone never uses stays fixed, a client's own changes take precedence, and out-of-order changes are rejected without altering anything. |
| `clock-switch` | The clock on the glass changes at the instant of an announced UTC-offset change, without waiting for its next refresh. |
| `schedule-switch` | The sleep schedule follows the clock across an offset change, the screen goes fully black, and it lights again when the schedule is turned off. |
| `sleep-wake` | Sleep fades through intermediate levels to a screen with no lit pixel, and wake restores the dashboard. |
| `pairing-closed` | With no code on display, pairing attempts are refused without being checked or counted, and the dashboard is not handed a code. |
| `pairing-window` | **Show code** needs a credential, pairs exactly one more device, and a revoked credential stops working. |
| `pairing-lockout` | Five wrong codes lock pairing, even for the correct code, with a `Retry-After`; an owner's new code clears the lock. |
| `pairing-widget` | The Pairing code widget shows the current code and opens pairing only while it is on the glass. |
| `notes` | A note posted through the API appears on the glass and leaves it when deleted. |
| `offline-fallback` | An unreachable web dashboard falls back to the offline clock, and clearing the address brings the built-in dashboard back. |
| `control-page` | The control page, everything it references and the dashboard's files are served, with a content security policy and no framing; the bundled zone table is not served. |
| `health` | The health report describes this device (Android 6, WebView 44, the whole 1080x1920 panel, a 128 MB app heap, no input devices, Mirror Home as the HOME app) and shows no crash, unhandled API error or covered dashboard. Its count of open files agrees with the kernel's own list. |
| `returns-to-front` | The other HOME app is started over the dashboard. Mirror Home leaves it for ten seconds, then takes the display back without creating a second dashboard. This runs twice: from the arrangement a boot leaves, and from the one an update can leave (see [Rehearsing an update](#rehearsing-an-update)). |
| `wakes-display` | Android itself is put to sleep, which turns the panel off. Mirror Home wakes it and the dashboard is back within seconds. |
| `cold-start` | Starting Mirror Home never lights the whole screen, since on mirror glass a white starting window is a bright flash, and the dashboard then fades in. |
| `restart` | A stopped process is recorded as the previous run, Android starts its HOME app again, Home comes back paired, and the dashboard returns to the glass. |
| `quick-restart` | A process stopped within seconds of starting, as Android 6 does while it replaces a HOME app, is counted as an early stop and not reported as the previous run. |
| `reboot` | After a reboot the dashboard comes to the front by itself, and pairing and the clock survive. |
| `script-errors` | The dashboard logged no script error and the API hit no unhandled error during the whole run. |

### Evidence

Each run writes to `build/validation/emulator-<time>/`, which Git ignores:

- `report.json`: every check's outcome, duration and measurements, such as
  the clock's switching delay, the fade's brightness samples and memory use,
  and the release of the Android Emulator that ran them;
- a PNG of the screen at each visual check, plus the frame that failed one;
- `logcat.txt`, `emulator.log`, and `crashes.txt`, which holds what Android
  recorded about any app that crashed or stopped answering.

The stock image's own apps, such as its carrier settings, sometimes crash on
a device with this little memory. Android 6 shows no "has stopped" dialog on
a device without input devices, so neither a Mirror nor the suite's virtual
device gets one. On an emulator with a touchscreen the dialog would cover the
screen; the suite closes it before the next check and lists it in the report
as `systemDialogsClosed`. It never closes a dialog about Mirror Home.

To see why a check failed, open its screenshot first, then read
`logcat.txt` around the time it failed; Android logs a crash under
`AndroidRuntime`. To look around by hand, run with
`--keep-running --window`, forward the control port with
`adb -s emulator-NNNN forward tcp:8787 tcp:8787`, and open
`http://127.0.0.1:8787`. Chrome's `chrome://inspect` can attach to the
dashboard page of a debug build; release builds never enable WebView
debugging.

On Windows the emulator can report an access violation as it shuts down and
leave one crash dump in the temporary directory. That happens after the last
check, and the suite judges the checks, not the emulator's exit code.

### When the emulator stops answering

The Android 6 image never changes, but the Android Emulator that runs it is
whatever release the SDK holds, and a new release can break on an image this
old. These have been tried:

| Release | Build | Result |
|---|---|---|
| 34.2.13 | 11772612 | Runs the suite (Windows). |
| 37.1.11 | 15917651 | Runs the suite (Linux and Windows). CI uses this build. |
| 37.2.12 | 16428233 | Fails about every second run, on Linux and on Windows: the emulator exits when Android reboots, and once it froze after an app was stopped. |

When the emulator freezes or exits, the check that was running fails, usually
with a time-out, `Connection refused`, or "The emulator exited when Android
rebooted". The suite then stops waiting for it: every later check fails at
once with "The emulator froze or exited during this run", the run says that
the emulator has stopped answering, and `report.json` holds
`"emulatorStoppedAnswering": true`. No `logcat.txt` is written, because the
log went with the emulator. None of this is a fault in Mirror Home. Checks
that passed before that point still count.

Run the suite again, or install a release from the table. `sdkmanager` only
offers the newest, so download the build by its number and put it in the
place of the SDK's `emulator` folder:

```powershell
curl.exe -L -o emulator.zip https://dl.google.com/android/repository/emulator-windows_x64-15917651.zip
Remove-Item -Recurse "$env:LOCALAPPDATA\Android\Sdk\emulator"
Expand-Archive emulator.zip "$env:LOCALAPPDATA\Android\Sdk"
```

On Linux the archive is `emulator-linux_x64-15917651.zip`. Android Studio and
`sdkmanager --update` will offer to replace it with the newest again.

`.github/workflows/ci.yml` installs its build the same way and checks the
archive's SHA-256. Before moving CI to a newer release, run the suite on it
several times, then add it to `WORKING_RELEASES` in
`tools/android_emulator.py` and to the table above.

### What the emulator cannot show

The emulator has none of the Mirror's hardware or vendor software, so these
still need the real unit:

- brightness and backlight control through the stock `com.mirror.services`
  Binder;
- background video on the hardware decoder, and playback smoothness;
- camera presence sensing;
- Wi-Fi, the Wi-Fi Direct setup network and its QR codes (the emulator has
  only a wired network, and Mirror Home shows that address instead);
- the OTA supervisor, which accepts only the Mirror's firmware fingerprint;
- the system helper;
- the factory launcher itself. The emulator's launcher takes its place as
  the second HOME app, but does nothing of its own accord, where the factory
  launcher opens its setup screen.

The virtual device's density, memory, heap size, lack of input devices and
power settings were read from a Mirror's health report (`device.display`,
`device.input`, `device.power`, `memory.systemTotalKb` and
`memory.javaHeapMaxKb`). A Mirror tells Android it has no mains power, so
Android's "stay awake while charging" never applies to it: its display stays
on only while a window asks for that, and from the factory turns off ten
minutes after. The emulator raises the heap size of a virtual device with a
panel this large to 256 MB, so the suite also starts it with a 128 MB limit
for each app, and `health` checks that Mirror Home got it.

`wakes-display` holds a kernel wake lock for its few seconds. A sleeping
emulator otherwise stops its processor and with it ADB, where a Mirror's
keeps running for the camera that senses presence.

### Rehearsing an update

A fresh install says nothing about a Mirror that already holds its owner's
settings. Before updating one, install the new build over the old one here
first:

```powershell
.\tools\validate.ps1 emulator --upgrade-from OLD.apk --apk NEW.apk
```

| Check | What must hold |
|---|---|
| `earlier-build` | The earlier build installs on empty storage and answers. |
| `owner-settings` | It is paired with the code it shows, and given a time zone, a sleep schedule, a note and a changed layout, as earlier controls saved them. |
| `update` | The build under test installs over it, as an OTA or USB update does, with a higher version code. Nothing starts it afterwards, and Home comes back without a crash. |
| `kept-pairing` | The credential issued before the update still works. |
| `kept-settings` | The clock zone, schedule, note and layout are unchanged, and the saved zone now follows the bundled daylight-saving table with the right next change. |
| `dashboard-after` | The dashboard is back on true black, in front, with no script or API errors, and Mirror Home is still the HOME app. |
| `pairing-after` | With no code on display, the updated Home refuses pairing attempts. |
| `returns-to-front` | A screen that covers the updated dashboard does not stay in front; the same check as in the suite. |

The last check is there because of what an update did to a Mirror. While
Android 6 replaces a HOME app it needs another one, and if it asks at the
wrong instant it starts the factory launcher. The new Mirror Home then starts
its dashboard itself, the launcher moves on to its setup screen, and that
screen stays on top. Mirror Home kept answering its API underneath, so the
update was reported as healthy. Builds before 2.2.0 fail this check: the
other screen stays in front until the check gives up.

Both APKs must be signed with the same key, or Android refuses the update.
Mirror Home builds before 2.2.0 cannot serve as the earlier build as they
are: without the stock Mirror services their `status` fails, so they cannot
be paired on an emulator. Build the earlier source with the one-line status
fix from 2.2.0 instead; what it stores is unchanged.

## Checking a live Mirror

```powershell
.\tools\validate.ps1 mirror
```

This reads `bootstrap`, `status` and `health` from a paired Mirror and says
what needs attention. It sends only `GET` requests and changes nothing. It
uses the credential that `tools\background-video.ps1 pair` saves in
`.secrets/mirror-background-video.json`; pass `--config` for another file
with `host`, `port` and `token`.

| Check | Fails when |
|---|---|
| `reachable` | The Mirror does not answer on the network. |
| `status` | Wi-Fi is disconnected, the background video reports an error or dropped 1% or more of its frames, presence sensing is on but not monitoring, or the weather shown is stale. |
| `health` | A crash is recorded, Home was stopped more than twice while starting, the dashboard stays covered by another screen for 30 seconds, Mirror Home is not Android's HOME app, Android has put the display to sleep, the dashboard logged script errors, the API hit an unhandled error, Android reports low memory, less than 256 MiB of storage is free, the OTA supervisor is installed but silent for 30 seconds, the clock keeps a fixed offset, or pairing is locked. |
| `updater` | The OTA supervisor does not answer a signed request, is not the device owner, does not support the firmware, needs recovery, disagrees with Home about the installed version, or reports a crash of its own. Skipped when this computer has no `.secrets/mirror-ota.json`. |

Mirror Home releases before 2.2.0 have no health report; `health` is then
skipped rather than failed. The run prints what it read and writes it to
`build/validation/mirror-<time>/report.json`. That includes which screens
Android has in front (`front`), and how often Mirror Home had to wake the
display or take it back (`recovery`).

### Exercising it

```powershell
.\tools\validate.ps1 mirror --exercise
```

After the checks above, this uses the Mirror for about half a minute, the way
an owner would, and puts everything back. Run it after an update. People near
the Mirror will see the display go dark and come back, the brightness change
once, and the offline clock replace the dashboard for a few seconds.

| Check | What it does and expects |
|---|---|
| `pairing` | A guess is refused unread while no code is on display. A code requested as a paired device pairs one temporary device, once; that device is then revoked and its credential stops working. |
| `notes` | A note is posted, listed and announced to the glass, then deleted. |
| `display` | The display is put to sleep, and the background video stops once it is dark. It is woken, the Mirror's own services report the wake brightness, a different brightness is set and read back, and the video plays again without dropping frames. The sleep schedule is then saved again as it was. |
| `offline-fallback` | The dashboard is pointed at a page that cannot load, the offline clock takes its place, and the original dashboard is restored and loads. |
| `weather` | The weather is refreshed from the network. Skipped when weather is off. |
| `health-after` | Mirror Home is still the same process, with no crash, script error or unhandled API error. |

Each check restores what it changed even when it fails. The display check is
skipped while something is playing, because sleep would stop it. If the
display was asleep because nobody was near, it wakes for the test and goes
back to sleep after the usual time without movement. The exercise needs
Mirror Home 2.2.0 or newer.

## The health report

Mirror Home records enough about itself to be judged from another room.
**Settings > Health** in the controls shows the summary and the full report;
`GET /api/v1/health` returns it to a paired client. See
[Control protocol](protocol.md#health) for its fields.

It answers questions that `status` cannot:

- **Did it restart, and why?** Each run records how the previous one ended:
  an update, a reboot of the Mirror, a crash, or Android stopping the app.
- **Did it crash?** The last uncaught exception is kept with its stack trace
  across restarts.
- **Is the dashboard really showing?** Mirror Home keeps answering its API
  while another screen covers it. The report says whether the dashboard is
  in front, for how long it has not been, which HOME app is on top, and how
  often Mirror Home had to take the display back or wake it.
- **Is the page healthy?** Script errors on the glass are counted, with the
  most recent ones kept.
- **Is it running out of anything?** Memory in use, free memory and storage,
  open files and threads.
- **Is the updater alive?** Whether the optional OTA supervisor accepts
  connections, which is otherwise invisible until an update fails.

The OTA supervisor, from version 1.1.0, records its own restarts and crashes
the same way and includes them in `.\tools\ota.ps1 status` under `health`.
