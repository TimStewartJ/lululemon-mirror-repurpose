# For agents

Instructions for coding agents working in this repository or on an owner's
MIRROR. People should start with the [README](README.md).

## What this repository is

Software that turns a discontinued lululemon MIRROR into a smart mirror:

- `android/mirror-home`: Mirror Home, the Android 6 HOME app, with a REST
  control API on port 8787 ([protocol](docs/protocol.md)).
- `android/ota-updater`: the optional OTA supervisor.
- `companion/`: the optional assistant server, which also offers the
  Mirror's tools to agents over MCP ([MCP guide](docs/mcp.md)).
- `profiles/`: known board and kernel combinations, and the schemas of the
  device report and board profile ([profiles](profiles/README.md)).
- `tools/`: command-line tools for owners and for checks.

## Acting on a real Mirror

A real Mirror is someone's appliance. Read before you change, change only
what you were asked to, and ask the owner before anything in the last column.

| Reads only | Changes the Mirror for a moment | Changes the Mirror: ask first |
|---|---|---|
| `python tools/identify.py --json` (works unauthorized and in fastboot) | `python tools/validate.py mirror --exercise` (sleeps and wakes it, sets brightness, puts things back) | `tools/mirrorctl.py install-home`, `rollback-home` |
| `python tools/identify.py --mirror CONFIG --json` (over Wi-Fi through Mirror Home) | | |
| `python tools/mirrorctl.py status` | `python tools/journal.py copy` (saves a copy of Android's log on the Mirror) | `tools/mirrorctl.py install-helper`, `restore-helper` (privileged) |
| `python tools/validate.py mirror` | | `tools/otactl.py provision`, `recover-token`, `deprovision`, `push`, `rollback`, `grant-permission`, `revoke-permission` |
| `python tools/otactl.py status`, `permissions` | | anything with `adb install`, `adb shell pm`, `settings put`, `fastboot flash` or `fastboot erase` |
| `python tools/journal.py`, `logs`, `save DIR` | | the REST API's `PUT`, `POST` and `DELETE`, except the board and moments when asked |

`tools/mirrorctl.py backup` reads the unit and writes only on this computer.
`tools/mirrorctl.py forward` sets up an ADB port forward and changes nothing
on the unit. `--help` of `mirrorctl.py` and `otactl.py` marks every command
"reads only", "this computer only" or "CHANGES THE UNIT". Never run a
`fastboot` command other than `devices` and `getvar` without the owner's
explicit word: no flash profile exists yet.

## Silvering

**Silvering** is the project's planned rebuilt Android 6.0.1 for MIRROR
units, with Mirror Home built in. (Named for the reflective layer behind a
mirror's glass.) It replaces only the `system` and `userdata`
partitions and keeps each board's own signed bootloader, boot image and
kernel, so secure boot does not stand in its way; the image must instead
match the board's kernel, which is what [board profiles](profiles/README.md)
record. **It does not exist yet**: no image has been built or accepted, so
no unit can install it today. [OS replacement](docs/os-replacement.md)
describes a different, blocked idea (a new kernel or Linux), not Silvering.
Its build recipe, in progress, is in [silvering/](silvering/README.md).
**MirrorOS** is the factory firmware a unit came with; `identify.py`
reports its version as `android.mirrorOsVersion`.

To control a Mirror from an agent rather than change this code, use the
companion's MCP server ([MCP guide](docs/mcp.md)); its tools say what they do.

## Identifying a unit

Never tell an owner what their unit is from the documentation: the docs
describe the units this project knows, not the one on the cable. Run this
first, without asking (it only reads), and report what it found:

```sh
python tools/identify.py --json [--serial SERIAL] [--board-label RevP21]
```

Standard output is one JSON document: a device report, or
`{"schema": "mirror-identify-error/1", "exitCode": N, "error": "..."}`. Exit
codes: 0 report (also when no unit was found), 1 unit not readable, 2 no
adb or fastboot. Tell the owner what `match.notes` says. `match.installable`
is false for every unit today. The report holds no serial number and may be
shared; the printed board label must come from the owner, since software
cannot read it.

## Changing the code

- Keep Android 6 (API 23) working: the Mirror's WebView is Chromium 44.
- Run `python tools/check.py` for every change, and
  `python tools/check.py --emulator` for changes to Mirror Home, its API or
  the dashboard ([validation](docs/validation.md)). The emulator keeps its
  disks under `build/emulator` (about 2.5 GB) and refuses to start when that
  drive is nearly full.
- A signed release build must also pass the signed-build suite in
  [validation](docs/validation.md): R8 has broken a release that the debug
  build passed.
- Update the guide a person uses for what you changed, and `CHANGELOG.md`.
- Never commit firmware, factory APKs, keys, `keystore.properties`, serial
  numbers, pairing tokens, Wi-Fi names, or anything under `build/`.
- Never commit Qualcomm vendor files. Silvering images will be built from
  files taken from the owner's own unit.
