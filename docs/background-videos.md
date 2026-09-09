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
previous object IDs are stored in private Android preferences.

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

The active video cannot be deleted. Activating another video retains the old
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

## Optional seasonal artwork

`tools\render_seasonal_video.py` renders the original four-seasons film offline;
it is not part of the Android build or a runtime dependency. It requires Python
with `pycairo`, `numpy`, and `opencv-python`, plus `ffmpeg` and `ffprobe` on PATH.

```powershell
python .\tools\render_seasonal_video.py 24
python .\tools\render_seasonal_video.py --contact-sheet
python .\tools\render_seasonal_video.py
```

The first command makes a one-second smoke clip; `--contact-sheet` writes 16
evenly spaced preview PNGs. Both go into ignored
`generated\background-videos\previews`. The full render writes
`generated\background-videos\four-seasons-cinematic.mp4`: 1080x1920, 24 FPS,
48 seconds, H.264 High@4.1, and no audio. Upload that file with the CLI or control
application like any other background video. No generated media belongs in
Android resources or Git.

## Playback

One Media3 `ExoPlayer` is shared between the ambient background and full-screen
presentation/casting media, so two hardware decoders are never allocated at
once. The background is muted, requests no audio focus, and disables audio track
selection. It pauses during display sleep and releases its decoder when the HOME
activity leaves the foreground.

`GET /api/v1/dashboard/ambient-video` reports the active content ID, decoder,
resolution, frame rate, rendered/dropped frames, loop count, and dashboard
readiness. LAN callers receive only redacted dashboard diagnostics unless they
authenticate.

The endpoint name and its authenticated `video.season` field are retained for
compatibility. `season` labels the four quarters of the playback timeline
spring/summer/autumn/winter; it is not metadata about arbitrary uploaded clips.
