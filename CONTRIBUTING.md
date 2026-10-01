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

GitHub CI runs this same gate. SDK setup requests `platform-tools`, then
explicitly installs Android SDK Platform 35 and Build Tools 35.0.0. The legacy
SDK `tools` package is not needed and must not be part of the bootstrap request.
Optional artwork tests skip when their Python or GPU dependencies are absent;
validate renderer changes locally with those dependencies installed.

Changes to the dashboard, the control API or app start-up should also pass the
[Android 6 emulator suite](docs/validation.md), which runs the real app in the
Mirror's WebView generation. It needs the Android Emulator and one system
image, and CI runs it as a second job:

```powershell
python tools/validate.py emulator
python tools/check.py --emulator    # the gate and the suite together
```

Dashboard scripts (`mirror.js`, `custom.js`, `offline.js`) run in Chromium 44:
no arrow functions, `let`, `const` or `Object.assign`. The suite fails on any
script error the glass logs. When a change adds behaviour the suite could
observe, add a check for it to `tools/validate.py` and to the table in the
guide.

For a focused loop:

```powershell
.\gradlew.bat :android:mirror-home:testDebugUnitTest :android:mirror-home:assembleDebug
.\gradlew.bat :android:ota-updater:testDebugUnitTest :android:ota-updater:lintDebug
python -m unittest discover -s tools\tests
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

Before a release, refresh the bundled time-zone table from the newest IANA
data. `--check` instead lists zones whose rules changed since it was written:

```powershell
python -m pip install --upgrade tzdata
python tools/zone_offsets.py
```

The `tzdata` package can trail a new IANA release by some days. Compare the
release that `--check` names with <https://data.iana.org/time-zones/tzdb/version>;
if IANA is ahead, `python -m pip install git+https://github.com/python/tzdata`
installs the same package from its repository, which is updated first.

## Commit scope

Use focused commits for independently reviewable behavior: device tooling,
privileged helper, dashboard/control plane, provisioning, or streaming.
