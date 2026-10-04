# Security policy

This project is for hardware you own or are explicitly authorized to modify.

## Secrets

Never commit:

- Wi-Fi SSIDs or passphrases
- ADB private keys
- pairing tokens
- OTA active and bootstrap tokens
- background-video client and bootstrap tokens
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

The Mirror issues an independent random token for every named browser/client
after a user-confirmed pairing code. Only SHA-256 token hashes are persisted,
and clients can be revoked independently. LAN control is denied until pairing
succeeds. Pairing itself is closed unless a code is on display, and wrong
codes lock it for a period that doubles with each lockout, so a six-digit code
cannot be found by guessing over the network.

## OTA supervisor

The device-owner OTA supervisor exposes no shell and accepts only the exact
Mirror Home package signed by its own release certificate. Upload requests are
HMAC-SHA256 authenticated over method, path, content hash, monotonic counter,
and nonce. Replayed and unauthenticated requests are rejected before request
bodies are parsed.

From 1.3.0 the supervisor has one component that another app can reach: a
service that Mirror Home binds to so that Android does not end the
supervisor when memory runs short. It is guarded by a permission of
protection level `signature`, so only an app signed with the supervisor's
own key can bind, and the connection answers one read-only question, the
supervisor's process and its kernel ranking. It carries no command.

Initial token issuance requires an independent random bootstrap capability whose
SHA-256 hash is injected at build time. Recovery token rotation additionally
requires an ADB-forwarded loopback request. The supervisor preserves a private,
re-validated known-good APK and does not overwrite it while recovering from an
unhealthy installed candidate.

The device API does not enable cross-origin browser access; browsers load the
controls from the Mirror itself. NanoHTTPD temporary storage is
redirected into the application's private cache, request bodies are bounded,
and the server does not use NanoHTTPD's general-purpose file handler.

Background-video uploads require a normal revocable bearer credential, are
bounded before parsing, and remain inside app-private storage. A deployment may
inject only the SHA-256 of a random one-time bootstrap capability; the secret
stays in `.secrets`, provisions one revocable client, and is consumed only after
that client authenticates a confirmation. Retries before confirmation return
the same pending credential rather than creating clients or stranding access.
Consumed hashes are retained independently so rolling back an APK cannot reopen
an older bootstrap.
Media filenames never become filesystem paths: stored objects use validated
lowercase SHA-256 identifiers.

## Local transport limitations

The stock Android 6 image cannot provide a browser-trusted local TLS identity
without installing a private CA or using an external HTTPS reverse proxy.
Mirror Home therefore serves its direct controls over authenticated HTTP on the
trusted LAN. Bearer tokens never appear in query strings; first-run origin
handoff uses a URL fragment, which is not transmitted to the server and is
removed immediately.

Do not expose ports `8787`, `8791`, or `46899` to the public Internet or an untrusted
wireless network. FCast v3 is plaintext and has no standard sender
authentication. The owner-controlled OS plan moves controls and FCast v4 to
TLS-capable current components.

## Reporting

Open a private security report before publishing a vulnerability that affects
devices beyond the discontinued MIRROR hardware profile.
