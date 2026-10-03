# Automation

Mirror Home exposes a small authenticated REST API suitable for Home Assistant,
Node-RED, shell scripts, and other trusted LAN automation systems.

The normal UI is hosted directly by the Mirror. Automation systems call port
`8787` and send a paired client token as `Authorization: Bearer MIRROR_TOKEN`.

## Examples

Set brightness directly:

```bash
curl -X POST http://MIRROR_IP:8787/api/v1/control/brightness \
  -H "Authorization: Bearer MIRROR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"value":128}'
```

Load a dashboard:

```bash
curl -X PUT http://MIRROR_IP:8787/api/v1/dashboard \
  -H "Authorization: Bearer MIRROR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"url":"http://HOME_ASSISTANT_HOST:8123/dashboard-mirror"}'
```

Play a network media URL:

```bash
curl -X POST http://MIRROR_IP:8787/api/v1/media/play \
  -H "Authorization: Bearer MIRROR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"url":"http://MEDIA_HOST/movie.mp4","mimeType":"video/mp4"}'
```

Stop playback:

```bash
curl -X POST http://MIRROR_IP:8787/api/v1/media/stop \
  -H "Authorization: Bearer MIRROR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{}'
```

Never place the Mirror bearer token in a URL.

Put a reminder on the glass:

```bash
curl -X PUT http://MIRROR_IP:8787/api/v1/board/items/dentist \
  -H "Authorization: Bearer MIRROR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"kind":"reminder","title":"Leave for the dentist","due":"2026-10-03T09:00:00-07:00"}'
```

Notes, to-dos and reminders posted this way are listed by the Board widget
and clear themselves. [The board](board.md) covers the fields, the lifetime
of an item and how to read back what people marked done.

Configure a schedule:

```bash
curl -X PUT http://MIRROR_IP:8787/api/v1/automation \
  -H "Authorization: Bearer MIRROR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"enabled":true,"wakeTime":"07:00","sleepTime":"23:00","wakeBrightness":180,"ambientEnabled":false,"ambientMinimum":20,"ambientMaximum":220,"motionEnabled":true,"motionTimeoutSeconds":300,"motionSensitivity":6}'
```

`motionTimeoutSeconds` accepts 30 through 3600 seconds and
`motionSensitivity` accepts 1 through 10. Higher sensitivity reacts to smaller
changes. The schedule is a quiet-hours boundary: motion can wake the display
inside the wake window, or at any time when the schedule is disabled. Camera
failure is fail-open, so it does not leave the mirror unexpectedly black.

Sleep keeps Android and the camera monitor running but sets the physical panel
backlight to zero. This is distinct from suspending the Android device and is
what allows motion to restore the configured wake brightness immediately.
Transitions fade: three seconds to sleep, two seconds to wake. The API reports
`sleeping` as soon as sleep begins; media stops once the fade reaches black.

`GET /api/v1/automation` reports camera availability, permission, monitoring
state, low-resolution preview dimensions, current change score, and the last
motion time. It never exposes image data. Frames are processed only in memory
and immediately discarded; there is no recording, face recognition, or upload.
