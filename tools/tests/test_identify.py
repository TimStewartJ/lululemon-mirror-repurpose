import contextlib
import io
import json
import pathlib
import sys
import unittest

TOOLS = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))

import identify  # noqa: E402

SERIAL = "bebf077"
KERNEL = "3.10.49-perf-gf46dad5260f"
GETPROP = f"""[ro.board.platform]: [msm8916]
[ro.build.fingerprint]: [mirror/mirror/msm8916_64:6.0.1/IFC-6309-2.0-MIR/329:user/release-keys]
[ro.build.id]: [IFC-6309-2.0-MIR]
[ro.build.type]: [user]
[ro.build.version.mirror]: [1.27.0]
[ro.build.version.release]: [6.0.1]
[ro.build.version.sdk]: [23]
[ro.hardware]: [qcom]
[ro.product.device]: [msm8916_64]
[ro.product.name]: [mirror]
[ro.serialno]: [{SERIAL}]
"""
BY_NAME = """lrwxrwxrwx root root 2019-06-25 21:40 system -> /dev/block/mmcblk0p21
lrwxrwxrwx root root 2019-06-25 21:40 userdata -> /dev/block/mmcblk0p29
lrwxrwxrwx root root 2019-06-25 21:40 boot -> /dev/block/mmcblk0p20
"""
PROC_PARTITIONS = """major minor  #blocks  name

 179        0    7634944 mmcblk0
 179       20      32768 mmcblk0p20
 179       21    1572864 mmcblk0p21
 179       29    5357551 mmcblk0p29
"""
GETVAR = f"""(bootloader) unlocked:no
(bootloader) secure:yes
(bootloader) version-bootloader:
(bootloader) product:QC_REFERENCE_PHONE
(bootloader) serialno:{SERIAL}
(bootloader) partition-size:system: 0x60000000
(bootloader) partition-type:system:ext4
(bootloader) partition-size:userdata: 0x146ffbe00
(bootloader) max-download-size: 0x1ff00000
all:
finished. total time: 0.051s
"""

# What the tool may ask of a unit: nothing here changes it.
READ_ONLY_SHELL = {"getprop", "uname", "cat", "ls", "pm", "dumpsys"}


class FakeUnit:
    def __init__(self, adb_devices="", fastboot_devices="", kernel=KERNEL, getprop=GETPROP):
        self.adb_devices = "List of devices attached\n" + adb_devices
        self.fastboot_devices = fastboot_devices
        self.kernel = kernel
        self.getprop = getprop
        self.commands: list[list[str]] = []

    def __call__(self, arguments: list[str]) -> identify.Result:
        self.commands.append(arguments)
        tool, rest = pathlib.Path(arguments[0]).name, arguments[1:]
        if tool == "fastboot":
            if rest == ["devices"]:
                return 0, self.fastboot_devices, ""
            if rest[-2:] == ["getvar", "all"]:
                return 0, "", GETVAR
        if rest == ["devices"]:
            return 0, self.adb_devices, ""
        command = rest[3:] if rest[:1] == ["-s"] and rest[2:3] == ["shell"] else []
        replies = {
            ("getprop",): self.getprop,
            ("uname", "-r"): self.kernel + "\n",
            ("cat", "/proc/version"): f"Linux version {self.kernel} (builder@host)\n",
            ("ls", "-l", "/dev/block/bootdevice/by-name/"): BY_NAME,
            ("cat", "/proc/partitions"): PROC_PARTITIONS,
            ("pm", "list", "packages"): "package:com.mirror.services\npackage:com.android.settings\n",
            ("dumpsys", "package", "com.mirror.services"): "    versionName=2.15.0\n",
        }
        return (0, replies[tuple(command)], "") if tuple(command) in replies else (1, "", "unknown")


def report_for(unit: FakeUnit, label=None, profiles=None) -> dict:
    tools = identify.Tools("adb", "fastboot", unit)
    return identify.identify(tools, None, label, identify.load_profiles() if profiles is None else profiles, "test")


class IdentifyTest(unittest.TestCase):
    def assertFollowsSchema(self, report: dict) -> None:
        self.assertEqual(identify.schema_errors(report, identify.load_schema("device-report")), [])

    def assertReadOnly(self, unit: FakeUnit) -> None:
        for command in unit.commands:
            tool, rest = pathlib.Path(command[0]).name, command[1:]
            if tool == "fastboot":
                self.assertIn(rest[-2:] if len(rest) > 1 else rest, (["devices"], ["getvar", "all"]), command)
            elif rest != ["devices"]:
                self.assertEqual(rest[2], "shell", command)
                self.assertIn(rest[3], READ_ONLY_SHELL, command)

    def test_authorized_unit_is_read_and_matched_by_its_kernel(self):
        unit = FakeUnit(adb_devices=f"{SERIAL}\tdevice\n")
        report = report_for(unit, label="RevP21")
        self.assertFollowsSchema(report)
        self.assertReadOnly(unit)
        self.assertEqual(report["state"], "android")
        self.assertEqual(report["android"]["mirrorOsVersion"], "1.27.0")
        self.assertEqual(report["kernel"]["release"], KERNEL)
        self.assertEqual(
            report["partitions"],
            [
                {"name": "boot", "sizeBytes": 32768 * 1024},
                {"name": "system", "sizeBytes": 1572864 * 1024},
                {"name": "userdata", "sizeBytes": 5357551 * 1024},
            ],
        )
        self.assertEqual(report["packages"], [{"name": "com.mirror.services", "versionName": "2.15.0"}])
        self.assertEqual(report["match"]["profile"], "msm8916-gf46dad5260f")
        self.assertEqual(report["match"]["evidence"], "reported")
        # A reported profile without an accepted image is never offered for installation.
        self.assertFalse(report["match"]["installable"])
        self.assertNotIn(SERIAL, json.dumps(report))

    def test_board_label_that_differs_from_the_profile_is_noted(self):
        report = report_for(FakeUnit(adb_devices=f"{SERIAL}\tdevice\n"), label="RevP18")
        self.assertTrue(any("RevP18 differs" in note for note in report["match"]["notes"]))

    def test_unknown_kernel_asks_for_a_report(self):
        report = report_for(FakeUnit(adb_devices=f"{SERIAL}\tdevice\n", kernel="3.10.49-perf-gdeadbeef00"))
        self.assertFollowsSchema(report)
        self.assertIsNone(report["match"]["profile"])
        self.assertTrue(any("not in profiles/boards" in note for note in report["match"]["notes"]))

    def test_other_platform_is_not_matched(self):
        getprop = GETPROP.replace("[msm8916]", "[imx8mq]")
        report = report_for(FakeUnit(adb_devices=f"{SERIAL}\tdevice\n", getprop=getprop))
        self.assertIsNone(report["match"]["profile"])
        self.assertTrue(any("imx8mq" in note for note in report["match"]["notes"]))

    def test_installable_needs_an_observed_profile_with_an_install(self):
        profile = json.loads((identify.PROFILES / "msm8916-gf46dad5260f.json").read_text(encoding="utf-8"))
        unit = FakeUnit(adb_devices=f"{SERIAL}\tdevice\n")
        self.assertFalse(report_for(unit, profiles=[{**profile, "install": {"image": "x"}}])["match"]["installable"])
        observed = {**profile, "evidence": "observed", "install": {"image": "x"}}
        self.assertTrue(report_for(unit, profiles=[observed])["match"]["installable"])

    def test_fastboot_unit_reports_the_bootloader_without_its_serial(self):
        unit = FakeUnit(fastboot_devices=f"{SERIAL}\tfastboot\n")
        report = report_for(unit)
        self.assertFollowsSchema(report)
        self.assertReadOnly(unit)
        self.assertEqual(report["state"], "fastboot")
        self.assertEqual(report["bootloader"]["secure"], "yes")
        self.assertEqual(report["bootloader"]["unlocked"], "no")
        self.assertEqual(report["bootloader"]["version-bootloader"], "")
        self.assertNotIn("serialno", report["bootloader"])
        self.assertEqual(report["bootloader"]["partition-type:system"], "ext4")
        self.assertEqual(report["bootloader"]["max-download-size"], "0x1ff00000")
        self.assertIn({"name": "system", "sizeBytes": 0x60000000}, report["partitions"])
        self.assertIsNone(report["match"]["profile"])
        self.assertNotIn(SERIAL, json.dumps(report))

    def test_unauthorized_unit_is_reported_without_reading_it(self):
        unit = FakeUnit(adb_devices=f"{SERIAL}\tunauthorized\n")
        report = report_for(unit)
        self.assertFollowsSchema(report)
        self.assertEqual(report["state"], "android-unauthorized")
        self.assertEqual([command[1:] for command in unit.commands], [["devices"], ["devices"]])

    def test_nothing_connected(self):
        report = report_for(FakeUnit())
        self.assertFollowsSchema(report)
        self.assertEqual(report["state"], "none")

    def test_two_units_need_a_serial(self):
        unit = FakeUnit(adb_devices=f"{SERIAL}\tdevice\n", fastboot_devices="other\tfastboot\n")
        with self.assertRaisesRegex(RuntimeError, "--serial"):
            report_for(unit)
        tools = identify.Tools("adb", "fastboot", unit)
        chosen = identify.identify(tools, "other", None, identify.load_profiles(), "test")
        self.assertEqual(chosen["state"], "fastboot")

    def test_an_offline_device_does_not_stand_in_the_way(self):
        alone = report_for(FakeUnit(adb_devices="emulator-5562\toffline\n"))
        self.assertEqual(alone["state"], "none")
        beside = report_for(FakeUnit(adb_devices=f"emulator-5562\toffline\n{SERIAL}\tdevice\n"))
        self.assertEqual(beside["state"], "android")
        tools = identify.Tools("adb", "fastboot", FakeUnit(adb_devices="emulator-5562\toffline\n"))
        with self.assertRaisesRegex(RuntimeError, "offline"):
            identify.identify(tools, "emulator-5562", None, identify.load_profiles(), "test")


class CommandLineTest(unittest.TestCase):
    def run_main(self, unit: FakeUnit | None, *argv: str) -> tuple[int, str, str]:
        tools = identify.Tools("adb", "fastboot", unit) if unit else identify.Tools(None, None)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = identify.main(list(argv), tools)
        return code, out.getvalue(), err.getvalue()

    def test_json_mode_prints_only_the_report(self):
        code, out, err = self.run_main(FakeUnit(adb_devices=f"{SERIAL}\tdevice\n"), "--json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["match"]["profile"], "msm8916-gf46dad5260f")
        self.assertEqual(err, "")

    def test_json_mode_reports_errors_as_json(self):
        two = FakeUnit(adb_devices=f"{SERIAL}\tdevice\n", fastboot_devices="other\tfastboot\n")
        code, out, _ = self.run_main(two, "--json")
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out)["schema"], identify.ERROR_SCHEMA)
        self.assertIn("--serial", json.loads(out)["error"])
        code, out, _ = self.run_main(None, "--json")
        self.assertEqual((code, json.loads(out)["exitCode"]), (2, 2))

    def test_without_json_errors_go_to_standard_error(self):
        code, out, err = self.run_main(None)
        self.assertEqual((code, out), (2, ""))
        self.assertIn("platform-tools", err)


class ProfilesTest(unittest.TestCase):
    def test_every_profile_follows_its_schema(self):
        schema = identify.load_schema("board-profile")
        paths = sorted(identify.PROFILES.glob("*.json"))
        self.assertTrue(paths)
        for path in paths:
            profile = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(identify.schema_errors(profile, schema), [], path.name)
            self.assertEqual(profile["id"], path.stem)
        kernels = [json.loads(path.read_text(encoding="utf-8"))["match"]["kernelRelease"] for path in paths]
        self.assertEqual(len(kernels), len(set(kernels)), "two profiles claim one kernel")

    def test_schema_check_catches_what_matters(self):
        schema = identify.load_schema("board-profile")
        profile = identify.load_profiles()[0]
        self.assertTrue(identify.schema_errors({**profile, "evidence": "rumour"}, schema))
        self.assertTrue(identify.schema_errors({**profile, "extra": 1}, schema))
        missing = dict(profile)
        del missing["install"]
        self.assertTrue(identify.schema_errors(missing, schema))


if __name__ == "__main__":
    unittest.main()
