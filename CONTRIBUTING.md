# Contributing

## Principles

- Support only hardware profiles that can be verified on-device.
- Preserve a tested rollback path before adding privileged behavior.
- Never commit proprietary firmware, APKs, credentials, device serials, or
  pairing tokens.
- Keep the normal HOME app useful without the optional system helper.
- Prefer allowlisted capabilities over shell or arbitrary file APIs.
- Maintain Android 6/API 23 runtime compatibility.

## Validation

Run the complete local gate:

```powershell
python tools/check.py
```

For a focused loop:

```powershell
.\gradlew.bat :android:mirror-home:testDebugUnitTest :android:mirror-home:assembleDebug
.\gradlew.bat :android:ota-updater:testDebugUnitTest :android:ota-updater:lintDebug
npm --prefix companion test
npm --prefix companion run typecheck
```

Hardware-affecting changes should also document:

1. the exact device profile used,
2. the pre-change state,
3. the command or UI path exercised,
4. the observed result, and
5. the verified rollback.

Mirror Home release version codes must increase for OTA. Test-only
`mirrorOtaHealthFailureTest` artifacts must use a unique version code, remain
outside source control, and be followed by a verified automatic rollback.

## Commit scope

Use focused commits for independently reviewable behavior: device tooling,
privileged helper, dashboard/control plane, provisioning, or streaming.
