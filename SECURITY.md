# Security policy

This project is for hardware you own or are explicitly authorized to modify.

## Secrets

Never commit:

- Wi-Fi SSIDs or passphrases
- ADB private keys
- pairing tokens
- device certificates or private keys
- proprietary APKs, firmware, or partition dumps

Local configuration belongs in ignored `.env` or `local.properties` files.

## Privileged helper

The optional helper is intentionally capability-based:

- no general shell endpoint
- no arbitrary file read/write endpoint
- no unauthenticated network listener
- explicit command allowlist
- device fingerprint and source-APK hash guards
- local backup and verified rollback before installation

The companion and Mirror authenticate using a user-confirmed pairing code and a
random token. LAN control is denied until pairing succeeds.

## Reporting

Open a private security report before publishing a vulnerability that affects
devices beyond the discontinued MIRROR hardware profile.
