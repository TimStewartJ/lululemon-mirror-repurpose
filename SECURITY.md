# Security policy

This project is for hardware you own or are explicitly authorized to modify.

## Secrets

Never commit:

- Wi-Fi SSIDs or passphrases
- ADB private keys
- pairing tokens
- Android release keystores and their passwords
- device certificates or private keys
- proprietary APKs, firmware, or partition dumps

Local configuration belongs in ignored `.env`, `local.properties`,
`keystore.properties`, or `.secrets/` paths.

## Privileged helper

The optional helper is intentionally capability-based:

- no general shell endpoint
- no arbitrary file read/write endpoint
- no unauthenticated network listener
- explicit command allowlist
- device fingerprint and source-APK hash guards
- local backup and verified rollback before installation
- transient installation only; restore the factory APK before reboot

The companion and Mirror authenticate using a user-confirmed pairing code and a
random token. LAN control is denied until pairing succeeds.

The device API does not enable cross-origin browser access. The companion
proxies browser requests over the same origin. NanoHTTPD temporary storage is
redirected into the application's private cache, request bodies are bounded,
and the server does not use NanoHTTPD's general-purpose file handler.

## Reporting

Open a private security report before publishing a vulnerability that affects
devices beyond the discontinued MIRROR hardware profile.
