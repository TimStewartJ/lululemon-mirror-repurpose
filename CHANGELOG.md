# Changelog

## Unreleased

- Bring the Mirror back to its Wi-Fi network when Android does not. One
  evening a Mirror stood for hours with its network a room away, showing
  its setup screen, until somebody looked: Mirror Home only asked Android to
  join the saved network while it started, and after that left it to
  Android, which normally finds it within seconds and that time did not.
  Why not is not known, since the restart erased Android's log; the Mirror's
  saved network carried the mark of having been set aside by Android after
  more than four failed attempts to join it in a row.
  Now, after a minute without a network, Mirror Home picks the saved network
  again as a person would, which makes Android take it back, then every two minutes and after half an hour every
  five; after ten minutes it switches Wi-Fi off and on, again after half an
  hour and then hourly; and Wi-Fi that is off while there is a network to go
  back to is switched on. A Mirror that was never on a network, and one
  whose setup network is in use, are left alone. `health.wifi.keeper` and
  **Settings > Health > Wi‑Fi** say whether the network was lost since
  Mirror Home started, for how long, and what brought it back. The emulator
  has no Wi-Fi to lose, so what the keeper does is tested against a stand-in
  for Android.
- Keep a journal that a restart does not erase. Android's log lives in
  memory, so switching a Mirror off and on, which is how one that misbehaves
  is brought back, erased the only account of what went wrong. Mirror Home
  now writes to its own storage when it starts and how the run before ended,
  when Wi-Fi is lost (with the signal just before, whether the display was
  on and how much memory was free), what Wi-Fi does while it searches, each
  time it asks Android to join again (with whether the network was in range
  and what Android holds against it), and when the network is back: about
  two thousand lines in half a megabyte, the oldest dropped first
  (`GET /api/v1/journal`). At the moment Wi-Fi is lost, when it first helps
  and when the network is back, it also copies Android's own log, which is
  where the reason stands and which on a Mirror reaches back only minutes
  (`POST /api/v1/journal/logs`, `GET /api/v1/journal/logs/{name}`; the newest
  twelve of each sort are kept). A copy holds Android's whole log once a person has given
  Mirror Home `READ_LOGS` from a computer (`adb shell pm grant
  dev.mirror.repurpose android.permission.READ_LOGS`, then start it again);
  until then it holds Mirror Home's own lines and says so.
  `tools/journal.ps1` reads the journal and fetches the copies, over the
  network or over USB. The emulator suite has a check for it, `journal`, and
  `reboot` checks that the journal reaches back across a restart.
- Let the assistant put more on the glass than its line of words: moments. A
  moment is something that shows for a while and then leaves by itself: a
  countdown that runs ("set a timer for five minutes"), words written large
  ("write happy birthday Sam really big"), a list ("show me how to make
  pour-over coffee"), a small chart ("how does the temperature go over the
  next hours?") or a line drawing ("draw me a heart"). The companion's model
  has two tools for it, `show_moment` and `end_moment`, and describes a
  moment rather than programming one: Mirror Home checks every field and
  draws the five kinds itself (`GET`, `POST` and `DELETE /api/v1/moments`).
  The glass decides where moments go, since it alone knows what is drawn
  where: they stand in one column in the middle, in the order they came, on
  free room where there is some and otherwise over widgets, which fade out
  until the moment over them leaves. When the column is too tall the older
  moments are drawn smaller, and then the oldest leave. Each stands on a
  slight dark backing for the film or photo behind it. A moment arrives and leaves with a fade, stays about a minute
  unless told otherwise and six hours at most, and is replaced where it
  stands when it is sent again under its id. A timer is both a countdown and
  a reminder on the board, so that it is still announced if Mirror Home
  restarts. The emulator suite has a check for it, `moments`.
- Let the assistant change the Mirror's standing settings when asked, where
  it could only be asked about them. The companion's model has seven more
  tools: `set_clock` (12 or 24 hours, and the time zone: "use military
  time", "we moved to Denver"), `set_display_rules` (the hours the display
  is lit, whether it sleeps when it sees nobody, after how long and at what
  sensitivity), `set_weather` (the place, Fahrenheit or Celsius, on or off),
  `set_film_schedule` (which film from which time of day; stop, start and
  return to it), `set_text_color`, `set_name`, and `habits`, which tells and
  changes what the companion does unasked: the greeting, the morning
  briefing, the reminder cards, the tidying and the quiet hours. A change of
  habits takes effect at once and is written to the companion's config, so
  that it holds after a restart. `set_background` now also takes a plain
  colour, a gradient, a particular photo ("the next photo", "the second
  one") and how far to darken what is behind the widgets. The model is told
  what it cannot change (the Wi-Fi, pairing, whether it and the listening
  are on, and updates) and says so. Tried with the real model against a
  stand-in Mirror: 23 requests of 23 came out as asked, a typical one in 2.3
  seconds.
- Stop two things asked for in one breath from undoing one another. Asked to
  "go back to the film and hide the photo", the model calls two tools at
  once, and each read the Mirror's layout, changed its part and stored the
  whole: the one that stored last put back what the other had changed, and
  the answer said that both were done. The companion now runs the calls of
  one request one after the other. The same held for the display's rules
  (brightness and sleep settings), and it let a tidying run make two changes
  where it may make one. A tool call that is refused is now logged with what
  was asked for, which is how `set_background` was found to be refused for
  naming its kind along with a photo or a film, as the model does; it takes
  that now, and tells a darkening that is left in force.
- Find a town for the weather when it is given with its state or country.
  The place search passed what was typed to a service that searches by a
  town's name alone, so "Portland, Maine" found nothing and "Springfield"
  could only ever be the five best known ones. `GET
  /api/v1/weather/locations` now also reads the last words as a state,
  province or country, written out or abbreviated ("Portland ME", "San Jose
  Costa Rica"), asks for more towns of that name and keeps those that lie
  there. When none does, it answers with the towns of that name that there
  are (`elsewhere`), and the controls list them instead of "No matching
  locations".
- Let the place of the Mirror's answers be chosen. They stood low and in the
  middle of the glass for everyone; now they can stand at one of five
  heights (top, upper, middle, lower, bottom) and to the left, in the middle
  or to the right, under **Settings > Assistant > Answers appear** or with
  `place` in `PUT /api/v1/assistant`. A line on the glass shows the place
  when it changes, and a panel that was showing leaves where it was and
  arrives at its new place instead of travelling across the glass. High up
  it hangs from its upper edge and grows downwards; low down it stands on
  its lower edge, as before. With the assistant on, the Mirror can be asked
  ("Mirror, put your answers at the top", "answer further left", "a bit
  lower"): the companion's model is told where its answers stand and has a
  tool, `set_answer_place`, to move them. A Mirror on which nobody chooses
  is unchanged.
- Stop the memory growth that made a Mirror need a restart after about nine
  days, where its owner asks for it. The cause was found on one Mirror by
  stopping it and starting it again: Android 6 goes on scanning for other Wi-Fi
  networks while it is connected, and a daemon of the factory software,
  `lowi-server`, keeps a little more memory for every scan, about twenty
  megabytes a day, which the kernel may not take back. With those scans
  stopped for an hour the daemon neither grew nor used the processor; with
  them back it grew as before. **Settings > Wi-Fi > Look for networks only
  when disconnected** (`PUT /api/v1/wifi/scan-guard`) uses a switch of
  Android 6 for exactly this. Mirror Home sets it again after every start,
  looks every five minutes whether it still holds, says in `status` and the
  health report whether Android took it, and puts back what Android did
  before when it is turned off. Android's switch does not govern what
  happens without a connection, and so that nothing rests on that, the guard
  hands the switch back for as long as Wi-Fi has no network: a Mirror that
  loses its network looks for it with Android exactly as it came. What it
  gives up is moving to another saved network while the one it has still
  works. It is off unless turned on, and
  offered only on Android 6. A Mirror does not let an app look at the
  daemon, so Mirror Home counts the scans themselves as Android finishes
  them (`wifi.scanGuard.scans` in the health report): with the guard on,
  none arrive while connected, and `tools/validate.ps1 mirror` fails if they
  do. Memory that the daemon already holds comes back only with a restart.
- Keep the OTA supervisor from being among the first that the kernel ends.
  When memory ran short on that Mirror the supervisor, a background service
  like any other, was ended twelve times in five minutes, and for three
  hours nothing could have been installed or rolled back. Mirror Home now
  holds a connection to the supervisor for as long as it runs, so Android
  ranks the supervisor with what the display needs (58 where a background
  service has 294; the health report gives the number as
  `otaSupervisor.hold.oomScoreAdj`), as it ranks the recogniser of voice
  commands, which stayed. This needs **OTA supervisor 1.3.0**, which offers
  that connection to an app signed with its own key and to no other, and
  which, like every supervisor, is installed over USB. An earlier supervisor
  works as before and is reported as one that cannot be held. The
  supervisor still starts by itself and needs Mirror Home for nothing: it
  carries on while Mirror Home is replaced, rolled back or does not start.

- Give the Mirror a character to answer as, if its owner wants one: Blink
  (two eyes), Wisp (a ghost), Mochi (a cat) or Lune (a moon), chosen under
  **Settings > Assistant > Character** or with `mascot` in `PUT
  /api/v1/assistant`. It stands above the words in place of the dots and acts
  out what the Mirror does: it comes up when it hears its name, looks about
  while it thinks, nods when the words were understood, says its answer,
  tilts its head at what it did not follow, waves back at a greeting and
  sleeps at good night. Each part of its face is moved by a spring, so a new
  mood takes hold in the same frame and arrives with a little bounce. None is
  chosen unless an owner chooses, and a Mirror without one is unchanged.
  With the assistant on, the Mirror can be asked for one ("Mirror, be the
  cat", "next character", "no character"): the companion's model is told
  which character it is and has a tool, `set_character`, to become another.
  `tools/mascots.py` paints the characters on a computer, from the code that
  draws them on the glass.
- Say when the Mirror needs to be switched off and on. On one Mirror, nine
  days after its last restart and with voice commands on, a daemon of the
  factory software had grown to 217 MB and four fifths of the compressed swap
  were in use. The kernel then ended a background process every six seconds,
  among them the OTA supervisor, which could not install or roll back
  anything for hours, while Android went on reporting 300 MB as available.
  Only a restart gives that memory back, and an app cannot restart a Mirror.
  Mirror Home now reads what the kernel itself says of memory, looks for the
  supervisor every five minutes, and advises a restart when the supervisor
  has been silent for half an hour or three quarters of the swap are in use
  after three days or more. It says so in `status` and the health report
  (`restart`), under **Uptime** in the controls, and, with an assistant, as a
  row in the answer to a greeting. The health report also carries the
  kernel's figures and, of the processes Android lets an app see (on a
  Mirror, apps and not the factory daemons), the five that hold most, and
  `tools/validate.ps1 mirror` reports a Mirror that asks to be restarted.
- Answer greetings. "Mirror, good morning", "good afternoon", "good evening",
  "I'm home" and "good night" are commands of the Mirror's own: it wakes, or
  for "good night" goes dark, and the glass returns the greeting. With the
  assistant on, the Mirror also tells its companion which greeting it heard,
  and where things stand appears under the greeting in well under a second:
  the weather, what is due today, the reminders that came due and were not
  dismissed, and what is still to do. After "good night" the glass shows what
  tomorrow holds and goes dark once that has been read, unless someone speaks
  to the Mirror again. "Good night" and "good morning" were second wordings
  of "go to sleep" and "wake up" before; they still do that, and now say more.
- Show the assistant's answers in a panel of their own, low on the glass. It
  fades in and out, its dots breathe while the Mirror listens and run while it
  works, and what the companion understood stays above the answer in small
  italics, so that a mishearing can be told from a wrong answer. An answer in
  several parts is a heading with up to five labelled rows, which arrive one
  after the other. A companion sends rows as `details`, in an answer or with
  `POST /api/v1/assistant/say`, and may say for how many `seconds` an answer
  stays.
- Let nothing pop into being on the glass. A widget that is switched on fades
  in, one that is switched off fades out, one that moves glides, one that
  changes size goes dark for a moment and comes back, and a new item on the
  board fades in under the ones that were there. The glass also shows a change
  made through the API within a fraction of a second: Mirror Home now tells
  the dashboard when its layout, the board, a note, the weather or the
  background was changed, where the dashboard used to notice at its next look,
  up to seven seconds later. The layout editor in the controls is unchanged.
- Send the whole of a spoken request. The sound for the companion began half a
  second before the first word that the recogniser placed; it places the first
  word of speech it does not know late, and at a real Mirror "Can we get rid
  of..." arrived as "We get rid of...". It now begins a second before.
- The companion answers a greeting with a briefing that it builds itself from
  the Mirror's status and board, without the model. The model has two new
  tools: `briefing`, for a greeting in other words or "what did I miss?", and
  `present`, for an answer that is a list ("what's on my list?"). For three
  minutes after a briefing "dismiss those" marks the reminders it named as
  done. A reminder that falls due is shown as a small card, and one that fell
  due while the display was dark is told to whoever comes next ("While you
  were away"); the first person in the morning gets the morning's briefing
  unasked. A board that the assistant brings back is put where nothing else
  is, not on top of what took its place.
- The emulator suite has a check for greetings (`assistant-greets`), and the
  check of a typed request also sends a line with rows and reads them back
  from the glass.
- Stop a Mirror that is short of memory from restarting its dashboard over and
  over. After an update the factory launcher's process idles behind the
  dashboard until the Mirror is next restarted. With voice commands listening
  a Mirror has little memory left, and whenever the kernel ended that process,
  Mirror Home's ended with it and Android started both again; the launcher's
  new process was then the next to go. On one Mirror, eight days after its
  last restart and two hours after an update, this restarted the dashboard
  twenty-five times in eighteen minutes. Mirror Home now asks Android to end
  the launcher's idle process as soon as the dashboard is in front, and once
  a minute after that, which takes nothing else with it. It does so only on a
  device without touchscreen or keys where it is the HOME app, and needs the
  `KILL_BACKGROUND_PROCESSES` permission, which Android grants by itself. The
  emulator suite checks that the other HOME app's process does not stay under
  the dashboard. Restarting a Mirror after an update removes the launcher's
  process as well.
- Keep accents, signs and other scripts in what the controls send. A new note
  such as "Café at 72°" was stored with replacement characters in place of the
  é and the °, in every release so far: the web server read a posted request
  as ASCII unless its Content-Type named a charset, and browsers name none for
  JSON. Every JSON request is now read as UTF-8, and the emulator suite posts
  such a note the way the controls do and reads it back (`note-text`).
- Add the assistant. With it switched on, what is said after the Mirror's
  name and is not one of its own commands goes to a companion: a server on a
  computer of yours, which turns the sound into words, lets a language model
  work out what is meant, and carries it out through the Mirror's API. "Mirror,
  remind me to take out the trash at seven", "something calmer in the
  background" and "make the clock bigger" then work, and the Mirror answers
  in a line on the glass. While it takes a request down and waits, the glass
  shows three dots; then the words as they were understood; then the answer.
  A question from the assistant can be answered without the name. The Mirror
  decides where a request ends by the room going quiet, not by the
  recogniser's sentences, which end where they please for words it does not
  know. Its own commands stay on the Mirror and work without a network.
  New for a companion: `POST /api/v1/assistant/say` shows a line on the
  glass, and `GET /api/v1/screenshot` gives a picture of it. **Settings >
  Assistant** has the switch, the companion's address and key, a field for a
  typed request and what was asked lately. The assistant is off unless an
  owner turns it on; with it on, the sound of each such request leaves the
  Mirror for that computer. See [The assistant](docs/assistant.md).
- Add the companion, in `companion/`: a Node.js server for the assistant that
  transcribes with faster-whisper on the computer it runs on, asks a model,
  and gives it tools for the Mirror alone:
  to read its state and look at the glass, to sleep, wake and dim it, to
  choose the background, to arrange the dashboard, and to keep the board. It
  also greets whoever walks up and shows reminders when they fall due. See
  [its README](companion/README.md).
- The companion's model can be anyone's. It runs the model with
  [Pi](https://github.com/earendil-works/pi)'s agent loop in its own
  process and is tied to no provider: `provider` and `model` in its config
  choose among some forty (Anthropic, OpenAI, Google, OpenRouter, Amazon
  Bedrock, GitHub Copilot and more), or name a server of your own such as
  Ollama, given as `endpoint`. Nothing is chosen for you: until both are
  set, the companion runs and `health` says that a model is still to be
  chosen. `node src/cli.js providers`, `models`, `login` and `logout` list
  them and keep the sign-ins, in a file beside the config that only its
  owner can read. `health` says why a model does not answer: nobody signed
  in and how to sign in, a model the sign-in is not offered and which it is,
  or what the provider said. So far one model has been run for real. See
  [Choosing a model](companion/README.md#choosing-a-model).
- The emulator suite checks the assistant against a stand-in companion on the
  computer: three new checks cover its settings, a typed request and its
  answer on the glass, lines and pictures for the companion, a companion that
  fails or is gone, the rules about what is passed on, and a spoken request
  that must arrive as the very sound that was played.
- Add voice commands, recognised on the Mirror itself. Say "Mirror, go to
  sleep", "wake up", "brighter", "dimmer" or "next video" (some have a second
  wording, such as "brightness up"); the glass shows what it did, and the Mirror
  never speaks. The name may come up to six seconds before the command, and a
  command the recogniser has doubts about is dropped. The recogniser is also
  given the commonest words of spoken English, so that talk is taken for them
  and not for the Mirror's name. The Vosk recogniser runs in a process of its
  own, so the dashboard carries on if it stops, and nothing listens until an
  owner switches voice on. No sound is stored or leaves the Mirror. While
  Android installs an app, as in an update of Mirror Home, the recogniser
  stops and gives its memory back, because a Mirror has too little for both.
  The 39 MB speech model is too large for an update package and is sent once
  with `tools/voice.ps1 install-model`, which also switches voice on and off
  and reports what the Mirror heard. **Settings > Voice** in the controls has
  the same: the switch, what voice is waiting for, what can be said, what was
  said to the Mirror lately, and a way to install the model from a phone or
  computer without the tools. After an update over the network the microphone
  permission is granted through OTA supervisor 1.2.0. A spoken "brighter" or
  "dimmer" changes the wake brightness, so it lasts. The recogniser's native
  library makes the APK larger, 11.7 MB where 2.2.0 was 4.4 MB: it is there
  for a Mirror's ARM processor and for the x86_64 emulator that validates
  every build. See [Voice commands](docs/voice.md).
- The emulator suite checks voice end to end: nine new checks play recordings
  of synthetic speech to a debug build in place of a microphone, and cover a
  refused or unloadable model, the rules about the Mirror's name, a recogniser
  that stops, an installation that needs its memory, a missing microphone
  permission, restarts and a reboot. A release build is checked as far as it
  can be without speaking to it. CI downloads the speech model by checksum and
  caches it. When the dashboard logs a script error, the run now says which,
  even if the process that logged it was restarted since.
- Start the background video again when its player fails. A failed player used
  to stay stopped until the display next slept or woke. On a Mirror that left
  the dashboard without its film for fourteen minutes: Mirror Home had been
  restarted while the factory launcher's setup screen, which plays a video of
  its own, held the video decoder. The player now tries again after 5, 15 and
  60 seconds and then every five minutes, and the status counts the tries as
  `ambientVideo.retries`.
- Leave out the library code that nothing uses. Most of Mirror Home's compiled
  code belonged to its video player and other libraries, and Android 6
  compiles the whole of an app on the Mirror each time it installs it. Builds
  now drop what nothing calls, and rename nothing, which takes the code from
  10 MB to 2 MB. On a Mirror, Android compiled an update in about 7 seconds
  where it took about 27, with a quarter less memory, and the OTA supervisor,
  which Android used to stop for want of memory at that moment, kept running.
  Debug builds leave out the same code, so that the emulator suite fails when
  something that is needed has been dropped.
- OTA supervisor 1.2.0 adds authenticated, explicit runtime-permission
  grants and revocations for Mirror Home's microphone, camera and Wi-Fi
  location permissions. A same-certificate supervisor upgrade initially
  needs authorized ADB; later Home features can declare a permission in an
  OTA update and have it granted over the LAN without another USB visit.
  No permission is automatically granted. See
  [LAN OTA updates](docs/ota-updates.md#runtime-permissions-without-another-usb-visit).
- Keep the emulator suite dependable when the Android Emulator is not. Hours
  after 2.2.0 was tagged, `sdkmanager` began to install emulator 37.2.12, which
  exits when Android 6 reboots in about every second run. CI failed for the
  release commit although nothing in Mirror Home had changed, and a run that
  lost its emulator waited seven minutes and left no report. CI now installs
  an emulator build that is known to run the image, by build number and
  checksum. A run names its emulator release and warns about one that is
  known to fail or has not been tried. It notices an emulator that has frozen
  or exited, fails the remaining checks at once with that reason, and still
  writes its report. See
  [When the emulator stops answering](docs/validation.md#when-the-emulator-stops-answering).
- Give the virtual Mirror the real one's 128 MB app heap. The emulator had
  raised the setting to 256 MB for a panel this large; the suite now starts it
  with the limit and the `health` check verifies it.
- After a restart, a reboot or a covered dashboard, require the screen to
  show the dashboard, lit strokes on black, where any bright screen used to
  pass.
- Add a voice lab, an experiment that is not part of Mirror Home: a small
  app (`android/voice-lab`) and `tools/voice_lab.py` measure what a Mirror's
  microphone hears and whether spoken commands can be recognised on the
  device itself. On the Android 6 emulator the Vosk recogniser, with JNA held
  at 5.15.0, understood every command of a four-command list in noise and
  across an echoing room, in about 120 MB of memory, and took a command from
  other speech at most once in two hours. On a Mirror it recognises 3.7
  times faster than real time on one core, beside Mirror Home and its
  background video, and understood all 16 complete commands its owner spoke
  to it, some of them from across the room. See
  [Voice lab](docs/voice-lab.md).
- Add the board: notes, to-dos and reminders that programs on the home
  network post to the Mirror over its control API, for scripts, home hubs
  and AI agents. An item says what it is, when it is due, who sent it and
  when it should go; the Mirror decides how to draw it. Everything leaves
  by itself, a day after it was last written unless its sender says
  otherwise, so a program that posts and forgets leaves nothing stale on
  the glass. The Mirror serves a guide to the board without a credential at
  `GET /api/v1/board/guide`, and every refusal names the field at fault and
  points there. The new Board widget lists the items, overdue and soon-due
  first with a countdown, and turns pages when they do not fit. The controls
  list what was posted, with who posted it, and can mark items done, remove
  them or clear the board. The emulator suite gains `board-api` and
  `board-glass`, and the live exercise gains `board`. See
  [The board](docs/board.md).
- A saved layout that already holds 40 widgets stays readable when an update
  adds a widget type, instead of falling back to the default layout.

## 2.2.0 - 2026-10-01

- Add an Android 6 emulator suite. `tools/validate.py emulator` (or
  `.\tools\validate.ps1 emulator`) boots the SDK's API 23 image, whose
  Chromium 44 WebView is the Mirror's generation, as a virtual Mirror
  (1080x1920 at 240 dpi, 1 GB of memory, no touchscreen or keys, and a second
  HOME app standing in for the factory launcher), installs a debug build,
  and checks it end to end in about five minutes: the setup screen, pairing
  and its lockouts, the dashboard on true black with no script errors, the
  clock and sleep schedule across a daylight-saving change, the sleep fade,
  notes, the offline fallback, the control page, the health report, a
  dashboard that another screen covers or Android puts to sleep, a dark cold
  start, restarts and a reboot. Nothing but Android starts Mirror Home during
  a run, as on a Mirror nobody can touch. It reads the glass through WebView
  DevTools, which debug builds alone enable, and through screen captures,
  keeps screenshots and a JSON report, and refuses to run on a physical
  device. `--apk` checks a signed release build, and `--upgrade-from`
  rehearses an update over an earlier build and proves that pairing and
  settings survive it and that the dashboard ends up in front.
  `tools/check.py --emulator` and a second CI job run the suite. See
  [Validation](docs/validation.md).
- Check a live Mirror from a computer. `tools/validate.py mirror` reads a
  paired Mirror's status, health and OTA supervisor, changes nothing, and
  lists what needs attention. With `--exercise` it then test-drives the
  Mirror for half a minute and puts everything back: it pairs and revokes a
  temporary device, posts and deletes a note, sleeps and wakes the display,
  sets and reads back the brightness, checks that the background video stops
  and resumes, forces the offline fallback, and refreshes the weather.
- Keep `ota.ps1 push` waiting when the supervisor goes quiet. Android can
  stop the supervisor's process while an update is installed; the supervisor
  restarts and finishes the update, but `push` used to fail with a network
  error at the first dropped connection. It now waits, prints each step, and
  says to check `status` if contact is lost for good.
- Keep the dashboard in front. An update could leave the factory launcher's
  setup screen on the glass: while Android 6 replaces Mirror Home it needs a
  HOME app, and at the wrong instant it starts the other one, which then
  opens over the new dashboard. Nobody can dismiss that on a Mirror, and
  because Mirror Home kept answering its API the update counted as healthy.
  Mirror Home now brings its dashboard back when another screen has covered
  it for ten seconds, and wakes the display if Android has put it to sleep.
  It does so only where nobody could by hand: it is the HOME app, the device
  has no touchscreen or keys, and no computer is using its USB port. The
  health report adds what is in front (`activity.front`), what Mirror Home
  did about it (`activity.recovery`), whether it is the HOME app, and the
  display's power state (`device.power`).
- Show a wired address where there is no Wi-Fi. The setup screen, the Pairing
  code widget and the **Show code** hint use the Wi-Fi address or, failing
  that, a wired interface's, and `status` reports it as `address`. With a
  wired address the Wi-Fi Direct setup network is not started. Mirrors on
  Wi-Fi behave as before.
- Start black. Android draws the app theme's background while an app starts,
  and Mirror Home's was white, so the whole mirror could light up for a moment
  whenever Home started: at boot, after an update or after a crash. The
  starting window is now black.
- Report the Mirror's health. `GET /api/v1/health` and **Settings > Health**
  show what could not be seen without the glass or a USB cable: when Mirror
  Home started and whether the run before ended in an update, a reboot, a
  crash or Android stopping the app; the last uncaught exception with its
  stack trace, kept across restarts; whether the dashboard is in front or an
  Android prompt covers it, and for how long; script errors on the dashboard
  page; memory, storage, open files and threads; and whether the OTA
  supervisor accepts connections. Android 6 starts a HOME app twice while it
  updates it; the report counts the start it cut short instead of mistaking
  it for the previous run. OTA supervisor 1.1.0 records its own restarts and
  crashes and returns them in `ota.ps1 status`; installing it needs USB.
- Fix `GET /api/v1/status` failing while the display was awake and the stock
  Mirror services were not connected or could not report the brightness: the
  request was dropped, so the control page could not load. Any request that
  fails unexpectedly now answers 500 instead of dropping the connection, and
  is counted in the health report.
- Close pairing unless a code is on display. `POST /api/v1/pair` used to check
  any guess at any time, five every 30 seconds, and a new code every ten
  minutes forgave earlier misses. A code is now accepted only while the setup
  screen or the Pairing code widget shows one, or after a paired browser
  chooses **Settings > Paired devices > Show code** (`POST
  /api/v1/pair/window`). Five wrong codes lock pairing for 30 seconds,
  doubling with each lockout up to an hour, regardless of code rotation. The
  setup screen shows the code in every state so USB setup always works, and
  the pairing page says when no code is showing and why an attempt failed.
- Follow daylight-saving changes without help. The clock, the sleep schedule
  and the video schedule used the UTC offset a browser last reported, so they
  ran an hour off after each change until someone opened the controls. Mirror
  Home now ships the offset changes of every IANA zone for ten years
  (`zone-offsets.json`, written by `tools/zone_offsets.py` from IANA 2026e),
  and paired browsers send the changes they know of, which take precedence.
  The glass switches at the exact instant; a video-schedule start inside a
  skipped or repeated hour is handled; **Settings > Clock** shows the next
  change and rejects zone names the browser does not know. `preferences` and
  `pair` accept `utcOffsetChanges`; `status` adds `nextUtcOffsetChange`.
- Remove the desktop companion. Mirror Home serves its own controls, and USB
  setup, recovery and helper actions use `tools/mirror.ps1` and the API
  directly, so the Node.js service, its npm dependencies and its validation
  lanes are gone. `v2.1.0` is the last tag that contains it; Mirror Home still
  accepts that companion's media request header.
- Fade the display smoothly when it sleeps and wakes instead of switching
  instantly. One eased curve dims a black overlay and the backlight together
  (three seconds to sleep, two to wake), so video and the dashboard sink into
  the mirror evenly and a reversal mid-fade continues from the current level.
  Background video keeps playing, and sleep stops media and lowers the stored
  brightness, only once the panel is black. Waking with ambient brightness
  restores the last ambient level instead of staying at the sleep level.
- Add `tools/artwork_video.py`, a shared offline pipeline for the optional
  artwork films: parallel, ordered frame rendering in a bounded window,
  capped-CRF H.264 within the hardware and upload envelope, output validation,
  loop-seam checks, a numeric guard that keeps the widget zone dark and smooth,
  contact sheets, `--frames` previews, JSON render reports, and mirror metrics
  (near-black, fog and bright shares). Films run at 30 FPS: every frame is held
  for exactly two refreshes of the 60 Hz panel, so slow drifts do not judder the
  way 24 FPS does. Every render reports continuity, the largest one-step changes
  that stand apart from the motion around them, with their frame and place, so
  a pop can be found without watching the whole film.
- Add `tools/artwork_gl.py`, the shared GPU stage: multisampled HDR rendering,
  depth of field as cross-faded blur slabs, bokeh points of light, gaussian
  bloom, and a display transform with luminance-weighted grain that keeps black
  exact. Frames render with a margin and are cropped, so blur and bloom can
  gather light before it crosses the edge of the frame. Each depth layer owns
  a slice of the depth buffer, keeping translucent objects in a stable order.
  Renderers settle against provisional first-use shader results, and GL
  resources are freed only while their own context is current.
- Rebuild the optional four-seasons film as a real 3D GPU scene of light on
  true black. A weeping cherry grown by space colonization lives through a
  year while the camera makes one level orbit per loop. Blossoms and leaves
  animate on the GPU, release into gusts, land on still water that mirrors the
  tree, and ring where they touch down; snow, frost and fireflies close the
  year. Depth of field and an open shutter soften near particles and fast
  motion. Output is `generated/background-videos/four-seasons-spatial-120s.mp4`,
  a 120-second, native 1080x1920, 30 FPS loop. `moderngl` and OpenGL 4.3 replace
  `pycairo`; the earlier film is never overwritten.
- Add an optional luminous flowers film: ten species of curved, cupped, veined
  petal meshes with translucent shading around glowing stamens, drifting toward
  a lens with depth of field and parallax. Flowers unfurl from twisted buds,
  breathe, then release petals toward the viewer to tumble and dissolve into
  light. Each flower keeps a fixed depth layer and hands released petals over
  sample by sample, so neither draw order nor blur slabs cause one-frame pops.
  Output is `generated/background-videos/luminous-flowers-spatial-180s.mp4`,
  a 180-second, native 1080x1920, 30 FPS loop on true black. An 11 Mbps VBV cap
  keeps the longer film within the 256 MiB upload limit.
- Refuse to render either artwork film if repeated warm-up frames never agree.
  Report the failure explicitly and release the GPU context instead of silently
  returning nondeterministic frames.
- Encode artwork films in isolated temporary storage and publish the complete
  MP4 only after media validation, the widget guard and any loop-seam check
  pass. Publication is atomic and never replaces an existing file, including
  one created during rendering. Failed or interrupted runs do not leave an
  invalid film under its official name. Encoder errors retain their diagnostics.

## 2.1.0 - 2026-09-15

- Schedule background videos by time of day. **Display > Video schedule** holds
  up to eight start times, each showing its video until the next one and
  wrapping past midnight in the Mirror's local offset, so mornings and nights
  can use different films. Turning it on selects the built-in video background.
  While a schedule runs, **Show now**, API/CLI activation, and rollback hold a
  video until the next scheduled change; **Resume schedule** ends the hold.
  Scheduled videos cannot be deleted. Add `PUT /api/v1/background-videos/schedule`,
  `POST /api/v1/background-videos/schedule/resume`, catalog `effectiveId`,
  `showing`, `scheduledStarts`, and `schedule` fields, and
  `background-video.ps1 schedule show|set|on|off|resume`.
- Fade the glass through black whenever the background video changes instead of
  cutting while the decoder switches.

### Distribution maintenance - 2026-09-20

- Request `platform-tools` explicitly during hosted CI bootstrap, avoiding the
  unavailable legacy SDK `tools` package while keeping the full gate unchanged.
- Document project-signed APK verification, certificate compatibility and the
  owner-signed source-build path. Public Home builds leave test health failures
  and unattended background-video bootstrap provisioning disabled.
- Update the optional companion to Express 4.22.3, body-parser 1.20.8 and
  qs 6.16.0, resolving the known query-parser dependency advisories while
  retaining Express 4 compatibility. Mirror Home's Android code is unchanged.

## 2.0.0 - 2026-09-04

- Add a production background-video library stored separately from the APK.
  Authenticated clients can stream H.264 MP4 files up to 256 MiB into private,
  content-addressed storage; Mirror Home validates the exact SHA-256, duration,
  resolution, full-timeline frame rate, AVC profile/level, and on-device
  hardware decoder before atomically installing a video. The library supports
  12 videos / 768 MiB, preserves a 512 MiB free-space reserve, and retains
  active/previous selections for one-click rollback.
- Add **Display > Background videos** with upload progress, generated posters,
  metadata, Use/Delete/Rollback actions, Fill/Whole fitting, and background
  dimming. Add matching authenticated APIs and a streaming
  `tools/background-video.ps1` CLI with pairing and one-time build-scoped
  bootstrap provisioning.
- Keep exactly one Media3 player for background and presentation playback.
  Video backgrounds run beneath the transparent widget WebView, disable audio
  tracks and audio focus, pause with display sleep, release outside the
  foreground, and switch serially to casting/presentation media. Decoder,
  first-frame, loop, season, and dropped-frame telemetry remain available.
- Provide an optional deterministic offline seasonal-film renderer that writes to ignored
  `generated/background-videos/four-seasons-cinematic.mp4`; the resulting video
  can be uploaded as an ordinary first library item while the release APK stays
  small.
- Relaunch the HOME activity after Mirror Home replaces its own package so an
  OTA cannot leave the final frame of the previous dashboard frozen on-screen
  while only the background control service is running. Cold service starts
  do this only while Mirror Home is still Android's selected HOME, preserving
  an intentional return to the stock launcher.

## 1.9.1 - 2026-08-25

- Fix the note text areas in the control app: a one-line note no longer
  carries a blank second row (the `rows="2"` floor leaked into the autosize
  measurement), the autosize accounts for the border so the last line is not
  clipped, and long notes scroll inside the box instead of being cut off at a
  fixed height.

## 1.9.0 - 2026-08-25

- Add notes as content, separate from the layout. **Home > Leave a note on the
  mirror** posts up to 1,000 characters (line breaks kept) that reach the glass
  within a few seconds over `GET/POST /api/v1/notes` and
  `PUT/DELETE /api/v1/notes/{id}`; the Mirror keeps up to 50 notes, newest
  first, and a `notesVersion` field in status and runtime tells clients when
  to re-fetch. The list under the composer edits and deletes notes.
- Give the Note widget a source: newest note (the new default), rotate through
  notes every twenty seconds with a short fade, all notes stacked, one pinned
  note, or the classic fixed text. Add Fit / S / M / L sizing (fixed sizes
  clip instead of shrinking) and thin / light / regular / medium weights.
  Saved layouts without a source keep showing their own text.
- Raise the note text cap from 120 to 1,000 characters and honor line breaks
  on the glass; the cap stays at 120 for other widgets' unused text field.
- Posting a note when nothing on the glass shows notes turns on the canonical
  Note widget with "Newest note"; a visible fixed-text tagline is left alone.

## 1.8.2 - 2026-08-25

- Remove the Aurora dashboard source. The Mirror layout and a web page are
  the two remaining sources; a Mirror still pointed at Aurora falls back to
  the built-in dashboard.
- Remove the editor's "Face zone" reflection overlay and its toggle.

## 1.8.1 - 2026-08-25

- Fix photo frames stretching to the frame's aspect ratio for the length of
  each crossfade on the Mirror. The WebView's Chromium 44 hands an animating
  `<img>` straight to the GPU and ignores `object-fit` (crbug.com/369020,
  fixed in Chromium 48), so the fade now runs on a wrapper layer while the
  image itself is never composited on its own.
- Remove the full-screen Photos dashboard source. Photo widgets cover the same
  ground inside the Mirror layout, including a full-bleed rotating frame. A
  Mirror still pointed at the retired page falls back to the built-in
  dashboard instead of the offline screen.

## 1.8.0 - 2026-08-24

- Remove Bluetooth LE provisioning: the GATT advertising service, its
  Bluetooth permissions, the `bleProvisioning` status field, the Bluetooth
  dashboard widget, and the companion's Web Bluetooth flow. Wi-Fi setup is the
  on-glass QR + Wi-Fi Direct flow (or USB), which works from any phone camera
  and browser. Saved layouts that still contain a Bluetooth widget load with it
  dropped rather than resetting to defaults.

## 1.7.0 - 2026-08-24

- Add a Photo widget: a framed library photo, or a slow crossfading rotation
  through the whole library, placed and resized like any widget with Fill or
  Whole fitting, alignment-based crop focus, and duplication for collages.
- Prepare cached, EXIF-oriented 480px and 1920px photo variants on the Mirror
  and use them for frames, backgrounds, the gallery, and the control
  application so phone photos display upright and the display never decodes a
  multi-megapixel original.
- Place a library photo in a frame by tapping it in the control application.

## 1.6.0 - 2026-08-24

- Redesign every surface people see in the glass: a shared renderer draws the
  built-in dashboard with a hairline clock and quiet meridiem, stroke weather
  glyphs by WMO code, an hourly strip that hides meaningless rain chances, and
  stable fit-to-box typography that never clips or jitters.
- Select type weights through the Android family aliases because the 2015
  WebView ignores numeric `font-weight`; update the clock on the exact second
  and only repaint what changed.
- Rebuild Aurora as slow curtains of deep light, the photo gallery with
  long crossfades and a legibility scrim, and the offline fallback as a calm
  clock.
- Replace the native first-run screen with a hairline clock, rounded QR card,
  and a large grouped pairing code; remove on-glass debug telemetry.
- Rebuild the control application around Home, Display, Schedule, and
  Settings with a live miniature of the mirror, switches, segmented controls,
  immediate-apply brightness and source selection, a widget chip rail, and a
  canvas that shares the mirror's renderer.
- Add authenticated photo thumbnails for the library grid and a
  reflection-first default layout with warmer white text.
- Restyle the desktop companion to match.

## 1.5.2 - 2026-08-24

- Reject incomplete enabled weather coordinates before an empty field can
  coerce to the valid `0,0` location.
- Poll long-running weather refreshes to completion in the control UI, with the
  recurring status refresh as a backstop.

## 1.5.1 - 2026-08-24

- Add Android 6 TLS 1.2 support with a tracked public ISRG Root X1 trust anchor.
- Fix weather rendering scope, immediate city-search wiring, secure-context
  geolocation guidance, and rapid location-change refresh ordering.
- Preserve every canonical widget during v2 import and reject canonical type
  changes.

## 1.5.0 - 2026-08-24

- Add device-local Open-Meteo weather configuration, city search, current and
  hourly forecast widgets, bounded HTTPS fetching, atomic caching, stale status,
  retry scheduling, and offline fallback.
- Upgrade dashboard layouts to schema v2 with automatic v1 migration, duplicate
  widget instances, stable IDs, locks, and explicit layers.
- Add editor snap grids, edge/center alignment guides, reflection safe-zone,
  undo/redo, keyboard movement and resizing, layer controls, duplicate/delete,
  and validated JSON import/export.
- Replace the reset layout with a reflection-first weather-and-clock default;
  migrated layouts keep new weather widgets hidden until weather is enabled.

## 1.4.0 - 2026-08-24

- Add a separate boot-persistent Android device-owner OTA supervisor.
- Add HMAC-authenticated LAN update tooling with independent one-time bootstrap
  credentials, monotonic replay protection, and strict upload limits.
- Require the exact device fingerprint, Home package, release certificate,
  increasing version code, and streamed APK SHA-256 before installation.
- Add local known-good APK backup, silent PackageInstaller updates, loopback
  health checks, automatic data-preserving downgrade, and manual rollback.
- Add token recovery and explicit device-owner deprovisioning through
  ADB-forwarded loopback recovery.
- Add deterministic failure-injection builds and hardware-validate install,
  reboot persistence, health failure, automatic rollback, manual rollback, and
  repeat OTA upgrade.

## 1.2.3 - 2026-08-24

- Reconcile camera monitoring immediately when permission is granted externally
  through Android Settings.

## 1.2.2 - 2026-08-24

- Start the inactivity countdown only after camera frames are healthy, including
  after delayed acquisition or automatic camera recovery.

## 1.2.1 - 2026-08-24

- Turn the physical panel backlight completely off during sleep while keeping
  Android, camera monitoring, and network controls active.
- Detect stalled preview frames and camera-service errors, fail open, and retry
  camera acquisition automatically.
- Verify installed APK bytes when legacy ADB omits its success marker, avoiding
  false update and rollback failures.
- Stop without further package changes when an ADB failure leaves the installed
  APK state unverifiable.

## 1.2.0 - 2026-08-23

- Add private, low-resolution on-device camera motion sensing without recording,
  face recognition, or image upload.
- Add schedule-aware motion wake and configurable inactivity sleep with
  fail-open camera handling, manual-override precedence, and media protection.
- Add camera capability/live-state controls and an optional presence dashboard
  widget.

## 1.1.4 - 2026-08-23

- Use true black as the built-in dashboard default and reset background.

## 1.1.0 - 2026-08-23

- Freeform built-in mirror dashboard with movable and resizable widgets.
- Responsive visual layout editor in the Mirror-hosted control application.
- Custom solid, gradient, or gallery-photo backgrounds with adjustable dimming.
- Configurable widget visibility, opacity, alignment, and custom note.
- More subtle default layout with optional Wi-Fi, media, schedule, light, FCast,
  Bluetooth, uptime, and pairing metrics.

## 1.0.0 - 2026-08-23

First owner-controlled appliance release for the IFC6309 MIRROR profile.

- Initial IFC6309/Android 6 device profile and recovery documentation.
- Full-screen Android HOME dashboard with authenticated USB/LAN API.
- Encrypted BLE Wi-Fi provisioning and pairing-token handoff.
- Optional certificate-bound UID-1000 system helper with guarded installation.
- Media3 HTTP/HLS/DASH/RTSP playback.
- FCast v3 receiver and DNS-SD advertisement.
- Local companion web application with ADB, LAN, media hosting, and controls.
- Mirror-hosted responsive phone/desktop controls with QR pairing.
- Named per-client credentials with hashed storage and independent revocation.
- Wi-Fi Direct first-run/recovery onboarding and DNS-SD control discovery.
- Aurora, offline-fallback, and private local photo-gallery dashboards.
- Browser-derived local timezone, sleep/wake schedules, and brightness automation.
- Service watchdog plus signed transactional APK update and rollback tooling.
