# Automation

Mirror Home exposes a small authenticated REST API suitable for Home Assistant,
Node-RED, shell scripts, and other trusted LAN automation systems.

Use the companion as the browser-facing API. When the companion is exposed on
the LAN, configure `COMPANION_ACCESS_TOKEN` and send it in the
`X-Companion-Token` header.

## Examples

Set brightness:

```bash
curl -X POST http://COMPANION_HOST:4317/api/device/control/brightness \
  -H "X-Companion-Token: COMPANION_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"value":128}'
```

Load a dashboard:

```bash
curl -X PUT http://COMPANION_HOST:4317/api/device/dashboard \
  -H "X-Companion-Token: COMPANION_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"url":"http://HOME_ASSISTANT_HOST:8123/dashboard-mirror"}'
```

Play a network media URL:

```bash
curl -X POST http://COMPANION_HOST:4317/api/device/media/play \
  -H "X-Companion-Token: COMPANION_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"url":"http://MEDIA_HOST/movie.mp4","mimeType":"video/mp4"}'
```

Stop playback:

```bash
curl -X POST http://COMPANION_HOST:4317/api/device/media/stop \
  -H "X-Companion-Token: COMPANION_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{}'
```

Never place either the companion access token or Mirror bearer token in a URL.
