# Board profiles

**Silvering** is the project's planned rebuilt Android 6.0.1, with Mirror
Home built in. It is not built yet. It writes only `system` and `userdata`
and keeps the board's signed boot image and kernel.

A MIRROR keeps its own signed boot image and kernel whatever runs above them,
so anything installed in place of the factory Android must fit that kernel.
Each file in `boards/` describes one known combination of board and factory
kernel; Silvering images will be built for exactly one profile.

- `schema/board-profile.schema.json`: what a profile holds.
- `schema/device-report.schema.json`: what `tools/identify.py` writes about a unit.
- `boards/*.json`: the known combinations, named after the platform and the
  kernel's build hash.

## Evidence

`evidence` says how a profile is known:

- `observed`: seen on a unit this project works with.
- `reported`: read from an owner's report (linked in `sources`) and not seen
  here.

`install` stays `null` until a Silvering image has been built and accepted on
such a unit. The installer offers Silvering only for an `observed` profile
whose `install` is filled in; every profile today is `reported` with no
`install`, so nothing can be installed yet.

## Identify a unit

```powershell
python tools\identify.py --board-label RevP21 --output build\identify\report.json
```

`--board-label` is optional. **Today every profile is `reported` with no
`install`, so the report's value is identification: it cannot lead to an
installation yet.**

It reads the unit over ADB (or, in fastboot, what the bootloader answers to
`getvar all`) and changes nothing: no reboot, write, install or
authorization. A unit with Mirror Home on Wi-Fi can be read without USB:
`python tools\identify.py --mirror .secrets\mirror-background-video.json`
(any JSON file with `host`, `port` and a paired client's `token`). The board label is printed on the board and cannot be read by
software, so give it if you know it. The report names the profile the unit
matches by its kernel release, and holds no serial number, network name, key
or account, so it can be attached to an issue.

A unit whose kernel is in no profile, whose ADB is unauthorized, or that is
only in fastboot gets a report all the same: open a
[board report](https://github.com/TimStewartJ/lululemon-mirror-repurpose/issues/new?template=board-report.yml)
with it to add the combination. Devices that ADB lists as offline are passed
over; with more than one usable unit connected, choose one with `--serial`.

### From a program

`--json` prints only JSON on standard output, and no summary: the report
(`mirror-device-report/1`, schema above), or on failure
`{"schema": "mirror-identify-error/1", "exitCode": N, "error": "..."}`. Exit
codes: 0 a report was written (also when no unit was found: `state` is
`none`), 1 the unit could not be read, 2 neither adb nor fastboot is
installed. Read `match.installable` for whether Silvering can be installed,
and `match.notes` for what to tell the owner.

## Adding a profile

Copy an existing file, name it `msm8916-<kernel build hash>.json`, set
`evidence` to `reported` with the link to the report, and run
`python -m unittest tools.tests.test_identify`, which checks every profile
against its schema and that no two claim the same kernel.
