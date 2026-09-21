# Background videos

Mirror Home stores background videos separately from the APK so large media does
not inflate application updates.

For a stock device, complete [Getting started](getting-started.md) first.
Browser upload needs only installed Mirror Home and a paired browser; neither
the companion nor the optional artwork renderer is required.

## Storage

Files live in Mirror Home's private application data:

```text
/data/user/0/dev.mirror.repurpose/files/background-videos/
  objects/<sha256>.mp4
  objects/<sha256>.json
  posters/<sha256>.jpg
  incoming/*.part
```

The object name is the lowercase SHA-256 of the exact uploaded bytes. Metadata
contains only display and media properties, never filesystem paths. Active and
previous object IDs, the schedule, and any temporary hold are stored in private
Android preferences.

The library survives APK updates and APK rollback. Uninstalling Mirror Home or
clearing its application data removes it.

## Limits and validation

- 256 MiB per file
- 12 videos
- 768 MiB total library size
- 512 MiB free-space reserve after staging
- MP4 container with exactly one H.264/AVC video track
- at most 1920x1080 pixels in either portrait or landscape orientation
- at most 30 FPS
- at most 20 Mbps
- duration from 1 second through 6 hours
- the AVC profile and level must be supported by a hardware decoder on the
  Mirror

Audio tracks are accepted for convenient phone exports but disabled during
background playback. Uploads are streamed to private temporary storage, hashed,
validated on the device, flushed, and promoted to the content-addressed object
path only after every check passes.

## Control application

Open **Display > Background videos** to upload, activate, delete, or roll back a
video. **Upload MP4** uploads and then automatically activates the result;
activation clears a custom web dashboard URL and selects the built-in layout.
An upload that succeeds before activation fails can leave an inactive library
card; choose **Use** to retry. **Arrange the mirror > Background > Video**
selects video mode; **Fill**
crops to the panel and **Whole** letterboxes. Background dimming remains
available for text legibility.

The video on the glass cannot be deleted, and neither can a scheduled video.
Activating another video retains the old
selection as the rollback copy. Deleting the rollback copy is allowed only
after an explicit confirmation.

## Command line

Pair with the current six-digit code on the glass. Replace `MIRROR_IP` with
the Mirror's actual private IPv4 address and `PAIRING_CODE` with that code:

```powershell
.\tools\background-video.ps1 --host MIRROR_IP pair --code PAIRING_CODE --name "Video upload client"
```

Then manage the library:

```powershell
.\tools\background-video.ps1 status
.\tools\background-video.ps1 push 'C:\path\to\background.mp4'
.\tools\background-video.ps1 activate SHA256_ID
.\tools\background-video.ps1 rollback
.\tools\background-video.ps1 delete SHA256_ID
```

Replace the example file path and `SHA256_ID` with your file and a catalog ID.
`push` streams 1 MiB chunks without loading the video into memory, verifies the
returned content ID against a local streaming SHA-256, and activates the video
unless `--no-activate` is supplied. The revocable client credential is stored in
ignored `.secrets/mirror-background-video.json` and is never printed.

For unattended initial deployment, a random one-time bootstrap secret can be
hashed into a specific build with
`-PmirrorBackgroundVideoBootstrapTokenSha256=<sha256>`. The secret itself stays
in `.secrets/mirror-background-video-bootstrap.txt`. Provisioning saves the new
client credential before sending its authenticated confirmation; repeating it
after a lost response safely returns the same pending credential:

```powershell
.\tools\background-video.ps1 --host MIRROR_IP provision
```

After confirmation that bootstrap hash remains permanently marked consumed,
including across APK rollback; a later deployment uses a new random capability.
Normal builds leave the hash empty and disable provisioning entirely.

## Schedule

A schedule changes the background by time of day. It is a daily timetable of up
to eight start times, each with a video. A time shows its video until the next
time starts, wrapping past midnight, so exactly one video applies at any moment.
Times use the Mirror's saved local UTC offset, the same clock as the sleep
schedule. With a single time, that video shows all day.

Open **Display > Video schedule**, turn on **Change video by time of day**,
choose a video for each time, and select **Save video schedule**. Turning a
schedule on selects the built-in layout with a video background, like
activation. Turning it off keeps its times and shows the manually selected
video again.

While a schedule runs, choosing **Show now** on another video, activating it
through the API or CLI, or rolling back shows that video only until the next
scheduled change; **Resume schedule** ends the hold sooner. Saving a new
schedule also ends any hold. The glass fades the old video out through black and
the new one in when the background changes. A scheduled video cannot be deleted
until it is removed from the schedule.

```powershell
.\tools\background-video.ps1 schedule set 06:00=luminous-flowers-spatial-180s.mp4 19:00=546e5d02
.\tools\background-video.ps1 schedule show
.\tools\background-video.ps1 schedule off
.\tools\background-video.ps1 schedule on
.\tools\background-video.ps1 schedule resume
```

Each `HH:MM=VIDEO` uses a 24-hour time and a video file name, full ID, or unique
ID prefix of at least six characters.

`PUT /api/v1/background-videos/schedule` takes
`{"enabled": true, "slots": [{"start": "06:00", "videoId": "SHA256_ID"}]}` and
returns the catalog. `POST /api/v1/background-videos/schedule/resume` ends a hold.
The catalog reports `effectiveId` (what the glass shows), keeps `activeId` as the
manual selection, marks each video with `showing` and `scheduledStarts`, and
includes `schedule` with the saved times, `current` and `next` slots,
`nextChangeAt`, and any `hold`.

## Optional artwork films

The artwork renderers make background films offline; they are not part of the
Android build or a runtime dependency. They render real 3D scenes on the GPU and
require Python with `moderngl`, `numpy` and `opencv-python`, a graphics device
with OpenGL 4.3, plus `ffmpeg` and `ffprobe` on PATH. No window is opened.

Both films are designed for a two-way mirror: light on a true-black stage.
Black pixels leave the glass reflective, so the imagery appears to float in the
room instead of lighting the whole panel. Nothing references or reproduces a
specific third-party artwork.

### Four seasons

`tools\render_seasonal_video.py` renders one weeping cherry living through a
year while the camera makes a single level orbit around it, so the crown turns
in true perspective and its far side really passes behind the near side:

- the tree is grown, not drawn: space colonization reaches a crown of limbs
  toward a dome of attraction points, hanging strands are dropped from them,
  and the pipe model gives every limb the cross-section of what it feeds. Limbs
  are swept as tapered spline tubes whose bark is lit as ink strokes of light
  that follow the grain
- blossoms swell from buds in a wave that runs down the strands, open, and are
  torn away in three gusts that cross the crown as a front; leaves bud under
  the falling petals, deepen, turn gold, orange and crimson from the tips
  inward, and let go in the autumn gusts
- everything that falls lands on still water that mirrors the tree, rings where
  it touches down, carries the petals and leaves as a glowing carpet, and lets
  them fade
- fireflies wander through summer; in winter snow falls through the depth of
  field, settles along the limbs, and frost glints at the strand tips

Every petal and leaf carries its whole year as constants and is animated on the
GPU. A released leaf starts exactly at its live position on the swaying strand,
so nothing jumps. The whole tree stays in focus while whatever flies between it
and the lens blurs with distance, and a shutter that stays open for part of
every frame smears the storm instead of strobing it.

```powershell
python .\tools\render_seasonal_video.py 30
python .\tools\render_seasonal_video.py 240 --start-frame 660
python .\tools\render_seasonal_video.py --contact-sheet
python .\tools\render_seasonal_video.py --frames 300,1350,2475,3150
python .\tools\render_seasonal_video.py
```

The first command makes a one-second smoke clip and the second samples the
petal storm. `--contact-sheet` writes 16 evenly spaced preview PNGs; `--frames`
previews chosen frames. Previews also produce an overview JPEG and per-frame
metrics. The full render writes
`generated\background-videos\four-seasons-spatial-120s.mp4`: native 1080x1920,
30 FPS, 120 seconds (30 seconds per season), H.264 High@4.1, yuv420p, BT.709
limited-range output, and no audio. The earlier `four-seasons-cinematic.mp4`
is never overwritten.

### Luminous flowers

`tools\render_luminous_flowers.py` renders a garden of original, procedurally
generated flowers drifting toward the viewer through a lens with real depth of
field:

- every petal is a curved, cupped, ruffled surface with veins, painterly streaks
  and translucent shading: a soft key light models its form and light from
  behind shines through it
- ten forms (among them cosmos, camellia, lily, narcissus, chrysanthemum and
  lotus) are built from rings of such petals around glowing stamens or a disc of
  florets
- each flower is born as a point of light, unfurls from a twisted bud, breathes
  in full bloom, then lets its petals go one by one: they tumble toward the
  lens on a slow breeze and dissolve into light from the tips inward
- far flowers are dim, cool and soft, hero flowers bloom on the focal plane, and
  released petals pass the lens as large, soft shapes; the camera sways slowly,
  so everything moves with true parallax
- species and colors follow the season in which a flower is born: blush and
  coral cherry, peony and camellia; violet, blue and mint lotus, lily and
  cosmos; ember chrysanthemums and asters; then ice-white plum and narcissus
  with a few crimson camellias

Every flower slot lives a whole number of cycles per loop, and each cycle is a
different seeded flower at a different position, so the garden keeps changing
while the frame after the last still equals frame 0.

Translucent things must be drawn in order, and an order that is sorted again
for every frame swaps, which shows as a pop. Here the order never changes: all
flowers drift at one speed, so they keep one far-to-near order for life; each
flower owns a slice of the depth buffer in which depth sorts its petals; and a
released petal is handed over to a slice of its own, giving up its samples in
the flower one by one while its free copy gathers light.

```powershell
python .\tools\render_luminous_flowers.py 30
python .\tools\render_luminous_flowers.py 180 --start-frame 2850
python .\tools\render_luminous_flowers.py --contact-sheet
python .\tools\render_luminous_flowers.py
```

The full render writes
`generated\background-videos\luminous-flowers-spatial-180s.mp4`: native
1080x1920, 30 FPS, 180 seconds (45 seconds per season), with the same codec,
color and hardware settings. Mostly-black flowers encode well below the cap, so
this film spends that headroom on a longer loop and caps VBV at 11 Mbps with a
16 Mbit buffer, which keeps the worst case below 256 MiB.

### Shared pipeline

`tools\artwork_video.py` holds what the films share. Frames render in parallel
worker processes (`--workers N`; each film names a default that suits its mix of
CPU and GPU work), each building its scene once; an ordered, bounded window feeds
`ffmpeg`, so memory stays flat. Every frame is an exact function of loop time,
so the frame after the last one equals frame 0 and each loop is seamless by
construction.

`tools\artwork_gl.py` is the GPU stage: multisampled HDR targets, depth of field
as blur slabs that are cross-faded and laid over one another back to front,
points of light with an analytic circle of confusion, gaussian bloom,
and the display transform (hue-preserving knee, luminance-weighted grain that
keeps exact black exact, the fade toward the widget corner). The picture is
rendered with a margin around the frame and cropped, because blur and bloom can
only gather what has been drawn: without it, whatever drifts in over the edge
would appear all at once. A renderer settles before it hands out frames, since a
driver may run a freshly compiled shader through a provisional build the first
time it is used. If repeated renders never agree, construction fails with an
explicit error and releases the GPU context instead of returning unstable frames.

Films run at 30 FPS, half the panel's 60 Hz, so every frame is held for exactly
two refreshes and slow drifts stay even; 24 FPS would alternate between two and
three. It is also the most that Level 4.1 carries at this size.

The slow x264 preset uses capped CRF 16. By default VBV is capped at 16 Mbps with a
20 Mbit buffer: quality stays constant and mostly-black films stay small, while the
worst case (maximum rate for the whole film plus buffer) stays below 256 MiB. A
film may choose a lower cap for a longer loop; rendering refuses any budget that
cannot guarantee the upload limit.
Three reference frames and two B-frames keep the hardware-friendly decoder
settings. The widget zone (x 2-40%, y 3-24%) is numerically kept dark and
smooth.

Smoke and full renders validate codec, dimensions, cadence, frame count,
duration, color signaling, upload size/bitrate and the Level 4.1 macroblock
envelope, refuse to overwrite existing outputs (use `--output PATH`), print
SHA-256, and record mirror metrics: the share of near-black (mirror), dim veil
(fog) and bright pixels. Full renders also check decoded loop-seam differences.
A JSON report is written next to the previews.

Every render also reports continuity: the largest one-step changes in any patch
of the picture that stand apart from the motion around them, with the frame and
place of each. Motion, however fast, changes a patch by similar amounts on
successive steps; something switching state in a single frame does not. Draw
order swapping or a blur gathering what had just crossed the edge of the frame
scored 12 to 25 among the flowers, whose honest motion stays below 8, while a
gust full of tumbling leaves reaches 13 by itself: a high score is a frame worth
looking at (`--frames`), not a verdict.

Focused optional tests skip if the artwork-only Python dependencies or a
suitable graphics device are absent:

```powershell
python -m unittest discover -s tools\tests -p "test_artwork_video.py" -v
python -m unittest discover -s tools\tests -p "test_artwork_gl.py" -v
python -m unittest discover -s tools\tests -p "test_render_seasonal_video.py" -v
python -m unittest discover -s tools\tests -p "test_render_luminous_flowers.py" -v
```

Upload a new film with the CLI or control application like any other background
video. Activating it keeps the previous selection for rollback; do not delete
that copy until playback has been verified. No generated media belongs in
Android resources or Git.

## Playback

One Media3 `ExoPlayer` is shared between the ambient background and full-screen
presentation/casting media, so two hardware decoders are never allocated at
once. The background is muted, requests no audio focus, and disables audio track
selection. It pauses during display sleep and releases its decoder when the HOME
activity leaves the foreground.

`GET /api/v1/dashboard/ambient-video` reports the playing content ID, decoder,
resolution, frame rate, rendered/dropped frames, loop count, and dashboard
readiness. Authenticated callers also get the selection, with `active` as the
video on the glass, `selectedId` as the manual choice, and the schedule. LAN
callers receive only redacted dashboard diagnostics unless they
authenticate.

A background change fades through black: a black curtain between the video and
the widgets closes over roughly one second, the player switches source, and the
curtain opens again. While asleep or hidden, the source simply switches.

The endpoint name and its authenticated `video.season` field are retained for
compatibility. `season` labels the four quarters of the playback timeline
spring/summer/autumn/winter; it is not metadata about arbitrary uploaded clips.
