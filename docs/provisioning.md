# Provisioning

## QR and Wi-Fi Direct

The normal first-run path does not require ADB:

1. With no managed Wi-Fi, Mirror Home creates a private Wi-Fi Direct group.
2. Scan the first QR code to join it.
3. Scan the second QR code to open `http://192.168.49.1:8787/`.
4. Pair with the six-digit code and submit household Wi-Fi credentials.
5. The browser transfers its credential to the new LAN origin in a URL fragment
   and removes the fragment immediately after loading.

When Wi-Fi is already connected, scan the single control QR code.

## USB recovery

USB is the most reliable first-run path:

1. Authorize ADB on the owned Mirror.
2. Install Mirror Home with `tools/mirror.ps1 install-home`.
3. Forward the control API with `tools/mirror.ps1 forward`.
4. Open `http://127.0.0.1:18787/` directly, or use the optional companion.

The browser speaks only to the same-origin companion. The companion attaches
the device token server-side.

## Bluetooth LE

Mirror Home advertises an encrypted GATT provisioning service:

```text
Service:  7d7a0001-6d69-7272-6f72-726570757270
Write:    7d7a0002-6d69-7272-6f72-726570757270
Response: 7d7a0003-6d69-7272-6f72-726570757270
```

The client must bond with the Mirror before reading or writing characteristics.
Desktop Chrome/Edge performs this through its Web Bluetooth permission and
operating-system pairing flow.

Requests and responses are compact UTF-8 JSON terminated by a newline. Clients
split writes into 18-byte chunks unless they know a larger negotiated MTU.
Mirror Home limits assembled requests to 2 KiB and sends response chunks only
after Android confirms the previous notification.

Provision request:

```json
{
  "type": "provision",
  "code": "123456",
  "ssid": "example",
  "passphrase": "not-a-real-password",
  "hidden": false
}
```

Success is returned only after Android obtains an IP address:

```json
{
  "ok": true,
  "token": "<bearer-token>",
  "ipAddress": "192.168.1.50",
  "apiPort": 8787,
  "message": "Wi-Fi connection requested"
}
```

The passphrase is never persisted by the companion. Android stores the network
through its native Wi-Fi configuration service.

## LAN and discovery

After provisioning, the companion switches to the returned private LAN address
and uses the same bearer token. LAN-exposed companion deployments require their
own `COMPANION_ACCESS_TOKEN`; loopback-only companion instances remain
frictionless. The Mirror-hosted control page needs no companion token and uses
named per-browser device credentials.
