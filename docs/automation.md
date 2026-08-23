# Automation

Mirror Home exposes a small authenticated REST API suitable for Home Assistant,
Node-RED, shell scripts, and other trusted LAN automation systems.

The normal UI is hosted directly by the Mirror. Automation systems call port
`8787` and send a paired client token as `Authorization: Bearer MIRROR_TOKEN`.
The optional companion proxy remains supported.

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

Never place either the companion access token or Mirror bearer token in a URL.

Configure a schedule:

```bash
curl -X PUT http://MIRROR_IP:8787/api/v1/automation \
  -H "Authorization: Bearer MIRROR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"enabled":true,"wakeTime":"07:00","sleepTime":"23:00","wakeBrightness":180,"ambientEnabled":false,"ambientMinimum":20,"ambientMaximum":220}'
```
