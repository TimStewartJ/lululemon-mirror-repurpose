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

## LAN and discovery

After Wi-Fi provisioning, the browser moves to the Mirror's private LAN address
with the same bearer token. The companion can also connect to a Mirror that is
already on the LAN through `POST /api/companion/devices/lan/connect` with that
address and an existing pairing token; the address must be a private
(RFC1918) or link-local IPv4 literal. LAN-exposed companion deployments require
their own `COMPANION_ACCESS_TOKEN`; loopback-only companion instances remain
frictionless. The Mirror-hosted control page needs no companion token and uses
named per-browser device credentials.
