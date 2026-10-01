import os
import pathlib
import re
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

TOOLS = pathlib.Path(__file__).resolve().parents[1]
REPO = TOOLS.parent
sys.path.insert(0, str(TOOLS))

import android_emulator
from android_emulator import EmulatorError


def settings(config):
    return dict(line.split("=", 1) for line in config.splitlines())


class InstalledReleaseTest(unittest.TestCase):
    def read(self, properties):
        with tempfile.TemporaryDirectory() as directory:
            sdk = pathlib.Path(directory)
            if properties is not None:
                (sdk / "emulator").mkdir()
                (sdk / "emulator" / "source.properties").write_text(properties, encoding="utf-8")
            return android_emulator.installed_release(sdk)

    def test_reads_the_release_and_build_from_the_package_file(self):
        self.assertEqual(
            ("37.1.11", "15917651"),
            self.read(
                "Pkg.UserSrc=false\nPkg.Revision=37.1.11\nPkg.Path=emulator\n"
                "Pkg.Desc=Android Emulator\nPkg.BuildId=15917651\n"
            ),
        )

    def test_a_package_file_without_a_build_number_still_names_the_release(self):
        self.assertEqual(("30.0.5", ""), self.read("Pkg.Revision = 30.0.5\r\nPkg.Path=emulator\r\n"))

    def test_an_sdk_without_the_emulator_or_its_package_file_gives_nothing(self):
        self.assertIsNone(self.read(None))
        self.assertIsNone(self.read("Pkg.Path=emulator\n"))


class ReleaseAdviceTest(unittest.TestCase):
    def test_a_release_that_ran_the_suite_needs_no_advice(self):
        for release in android_emulator.WORKING_RELEASES:
            self.assertEqual("", android_emulator.release_advice(release))
        self.assertEqual("", android_emulator.release_advice(None))

    def test_a_release_known_to_fail_is_named_with_where_to_read_on(self):
        for release in android_emulator.FAILING_RELEASES:
            advice = android_emulator.release_advice(release)
            self.assertIn(f"Android Emulator {release} is known to freeze or exit", advice)
            self.assertIn("When the emulator stops answering", advice)

    def test_an_untried_release_is_said_to_be_untried(self):
        self.assertIn("has not been run on Android Emulator 99.1.2", android_emulator.release_advice("99.1.2"))

    def test_no_release_is_listed_as_both(self):
        self.assertFalse(set(android_emulator.WORKING_RELEASES) & set(android_emulator.FAILING_RELEASES))

    def test_the_guide_names_every_release_in_either_list(self):
        guide = (REPO / "docs" / "validation.md").read_text(encoding="utf-8")
        for release in android_emulator.WORKING_RELEASES + android_emulator.FAILING_RELEASES:
            self.assertIn(release, guide, f"docs/validation.md does not mention emulator {release}")

    def test_ci_installs_a_release_that_ran_the_suite(self):
        workflow = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        pinned = re.search(r'EMULATOR_BUILD: "(\d+)" # (\S+)', workflow)
        self.assertIsNotNone(pinned, "the CI workflow does not pin an emulator build")
        self.assertIn(pinned.group(2), android_emulator.WORKING_RELEASES)
        self.assertRegex(workflow, r"EMULATOR_SHA256: [0-9a-f]{64}\b")


class AvdConfigTest(unittest.TestCase):
    def test_describes_the_mirror_panel(self):
        config = settings(android_emulator.avd_config("mirror-android6"))
        self.assertEqual("mirror-android6", config["AvdId"])
        self.assertEqual("1080", config["hw.lcd.width"])
        self.assertEqual("1920", config["hw.lcd.height"])
        self.assertEqual(str(android_emulator.DEFAULT_DENSITY), config["hw.lcd.density"])
        self.assertEqual("portrait", config["hw.initialOrientation"])
        self.assertEqual("1080x1920", config["skin.name"])
        self.assertEqual("x86_64", config["abi.type"])

    def test_has_no_navigation_bar_or_phone_hardware(self):
        config = settings(android_emulator.avd_config("mirror"))
        # Without hardware keys Android 6 draws a navigation bar over the panel.
        self.assertEqual("yes", config["hw.mainKeys"])
        for name in ("hw.battery", "hw.gps", "hw.accelerometer", "hw.audioOutput", "hw.sdCard"):
            self.assertEqual("no", config[name], name)
        self.assertEqual("none", config["hw.camera.back"])
        self.assertEqual("false", config["PlayStore.enabled"])

    def test_has_no_input_devices(self):
        # Android then reports no touchscreen, keyboard or navigation keys, as
        # on a Mirror, where nobody can dismiss a dialog or wake the display.
        config = settings(android_emulator.avd_config("mirror"))
        self.assertEqual("no-touch", config["hw.screen"])
        for name in ("hw.touchScreen", "hw.keyboard", "hw.keyboard.lid", "hw.dPad", "hw.trackBall"):
            self.assertEqual("no", config[name], name)

    def test_points_at_the_android_6_image_inside_the_sdk(self):
        image = settings(android_emulator.avd_config("mirror"))["image.sysdir.1"]
        self.assertEqual(
            ["system-images", "android-23", "default", "x86_64", ""],
            image.split(os.sep),
        )

    def test_takes_another_panel(self):
        config = settings(android_emulator.avd_config("mirror", width=720, height=1280, density=320))
        self.assertEqual(
            ("720", "1280", "320", "720x1280"),
            (config["hw.lcd.width"], config["hw.lcd.height"], config["hw.lcd.density"], config["skin.name"]),
        )

    def test_is_stable_so_rewriting_changes_nothing(self):
        config = android_emulator.avd_config("mirror")
        self.assertEqual(config, android_emulator.avd_config("mirror"))
        names = [line.split("=", 1)[0] for line in config.splitlines()]
        self.assertEqual(sorted(names), names)
        self.assertTrue(config.endswith("\n"))

    def test_writes_both_ini_files(self):
        with tempfile.TemporaryDirectory() as directory:
            home = pathlib.Path(directory) / "avd"
            created = android_emulator.ensure_avd(home, "mirror-android6", density=213)
            self.assertEqual(home / "mirror-android6.avd", created)
            pointer = settings((home / "mirror-android6.ini").read_text(encoding="ascii"))
            self.assertEqual(str(created), pointer["path"])
            self.assertEqual("android-23", pointer["target"])
            config = settings((created / "config.ini").read_text(encoding="ascii"))
            self.assertEqual("213", config["hw.lcd.density"])
            # A second run with other settings replaces them in place.
            android_emulator.ensure_avd(home, "mirror-android6", density=160)
            config = settings((created / "config.ini").read_text(encoding="ascii"))
            self.assertEqual("160", config["hw.lcd.density"])


class ArgumentsTest(unittest.TestCase):
    def test_runs_headless_on_a_clean_disk_by_default(self):
        arguments = android_emulator.emulator_arguments(
            "mirror-android6", 5580, wipe_data=True, window=False
        )
        self.assertEqual(["-avd", "mirror-android6", "-port", "5580"], arguments[:4])
        self.assertIn("-no-window", arguments)
        self.assertIn("-wipe-data", arguments)
        self.assertIn("-no-snapshot", arguments)
        self.assertIn("-no-audio", arguments)
        self.assertEqual("swiftshader_indirect", arguments[arguments.index("-gpu") + 1])
        self.assertEqual("-QuickbootFileBacked", arguments[arguments.index("-feature") + 1])

    def test_limits_each_app_to_the_mirrors_heap(self):
        # The AVD's own heap setting is raised by the emulator for a large panel.
        arguments = android_emulator.emulator_arguments(
            "mirror-android6", 5580, wipe_data=True, window=False
        )
        self.assertEqual("dalvik.vm.heapgrowthlimit=128m", arguments[arguments.index("-prop") + 1])
        self.assertEqual(128, android_emulator.HEAP_MB)

    def test_can_show_its_window_and_keep_its_data(self):
        arguments = android_emulator.emulator_arguments(
            "mirror-android6", 5582, wipe_data=False, window=True
        )
        self.assertNotIn("-no-window", arguments)
        self.assertNotIn("-wipe-data", arguments)


class SerialTest(unittest.TestCase):
    def test_recognizes_emulator_serials(self):
        for serial in ("emulator-5554", "emulator-5580"):
            self.assertTrue(android_emulator.is_emulator_serial(serial), serial)

    def test_rejects_everything_a_physical_device_can_be_called(self):
        for serial in (
            "10.0.0.196:5555",
            "R58M12ABCDE",
            "emulator-",
            "emulator-55a4",
            "emulator-5554:5555",
            "my-emulator-5554",
            "",
        ):
            self.assertFalse(android_emulator.is_emulator_serial(serial), serial)


class PortTest(unittest.TestCase):
    def test_a_bound_port_is_not_free(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as held:
            held.bind(("127.0.0.1", 0))
            held.listen(1)
            self.assertFalse(android_emulator.port_is_free(held.getsockname()[1]))
            port = held.getsockname()[1]
        self.assertTrue(android_emulator.port_is_free(port))

    def test_chooses_the_first_even_port_whose_neighbour_is_free(self):
        first = android_emulator.FIRST_CONSOLE_PORT
        # 5580 is free but its ADB port is not; 5582 is taken; 5584 and 5585 are free.
        busy = {first + 1, first + 2}
        with mock.patch.object(android_emulator, "port_is_free", lambda port: port not in busy):
            self.assertEqual(first + 4, android_emulator.free_console_port())

    def test_reports_when_every_port_is_taken(self):
        with mock.patch.object(android_emulator, "port_is_free", lambda port: False):
            with self.assertRaisesRegex(EmulatorError, "No free emulator console port"):
                android_emulator.free_console_port()

    def test_console_ports_are_even(self):
        self.assertEqual(0, android_emulator.FIRST_CONSOLE_PORT % 2)
        with mock.patch.object(android_emulator, "port_is_free", lambda port: True):
            self.assertEqual(android_emulator.FIRST_CONSOLE_PORT, android_emulator.free_console_port())


class SdkTest(unittest.TestCase):
    def test_uses_the_sdk_the_environment_names(self):
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.dict(os.environ, {"ANDROID_SDK_ROOT": directory}):
                self.assertEqual(pathlib.Path(directory), android_emulator.sdk_root())

    def test_falls_back_to_android_home(self):
        with tempfile.TemporaryDirectory() as directory:
            environment = {"ANDROID_SDK_ROOT": str(pathlib.Path(directory) / "missing"), "ANDROID_HOME": directory}
            with mock.patch.dict(os.environ, environment):
                self.assertEqual(pathlib.Path(directory), android_emulator.sdk_root())

    def test_reports_a_missing_sdk(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = str(pathlib.Path(directory) / "missing")
            with mock.patch.dict(os.environ, {"ANDROID_SDK_ROOT": missing, "ANDROID_HOME": missing, "LOCALAPPDATA": missing}), \
                    mock.patch.object(pathlib.Path, "home", return_value=pathlib.Path(missing)):
                with self.assertRaisesRegex(EmulatorError, "ANDROID_SDK_ROOT"):
                    android_emulator.sdk_root()

    def test_names_the_package_to_install_when_the_image_is_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(EmulatorError, 'sdkmanager "system-images;android-23;default;x86_64"'):
                android_emulator.require_system_image(pathlib.Path(directory))

    def test_finds_an_installed_image(self):
        with tempfile.TemporaryDirectory() as directory:
            image = pathlib.Path(directory) / "system-images" / "android-23" / "default" / "x86_64"
            image.mkdir(parents=True)
            (image / "system.img").write_bytes(b"image")
            found = android_emulator.require_system_image(pathlib.Path(directory))
            self.assertTrue(found.samefile(image))

    def test_names_the_package_to_install_when_the_emulator_is_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(EmulatorError, 'sdkmanager "emulator"'):
                android_emulator.emulator_path(pathlib.Path(directory))

    def test_prefers_adb_on_the_path_then_the_sdk(self):
        with tempfile.TemporaryDirectory() as directory:
            sdk = pathlib.Path(directory)
            with mock.patch.object(android_emulator.shutil, "which", return_value=None):
                with self.assertRaisesRegex(EmulatorError, "platform-tools"):
                    android_emulator.adb_path(sdk)
                bundled = sdk / "platform-tools" / android_emulator.executable_name("adb")
                bundled.parent.mkdir()
                bundled.write_bytes(b"")
                self.assertEqual(bundled, android_emulator.adb_path(sdk))
            with mock.patch.object(android_emulator.shutil, "which", return_value=str(sdk / "elsewhere-adb")):
                self.assertEqual(sdk / "elsewhere-adb", android_emulator.adb_path(sdk))


class BootTest(unittest.TestCase):
    def completed(self, returncode, stdout):
        return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")

    def test_booted_when_the_property_reads_one(self):
        with mock.patch.object(android_emulator.subprocess, "run", return_value=self.completed(0, "1\r\n")) as run:
            self.assertTrue(android_emulator.boot_completed(pathlib.Path("adb"), "emulator-5580"))
        self.assertEqual(
            ["adb", "-s", "emulator-5580", "shell", "getprop", "sys.boot_completed"],
            run.call_args.args[0],
        )

    def test_not_booted_while_the_property_is_empty_or_adb_fails(self):
        for outcome in (self.completed(0, "\n"), self.completed(0, "0\n"), self.completed(1, "1\n")):
            with mock.patch.object(android_emulator.subprocess, "run", return_value=outcome):
                self.assertFalse(android_emulator.boot_completed(pathlib.Path("adb"), "emulator-5580"))

    def test_not_booted_when_adb_hangs_or_is_missing(self):
        for error in (subprocess.TimeoutExpired("adb", 20), FileNotFoundError("adb")):
            with mock.patch.object(android_emulator.subprocess, "run", side_effect=error):
                self.assertFalse(android_emulator.boot_completed(pathlib.Path("adb"), "emulator-5580"))


class DiscardDisksTest(unittest.TestCase):
    def filled(self, directory):
        avd = pathlib.Path(directory) / "mirror-android6.avd"
        (avd / "snapshots" / "default_boot").mkdir(parents=True)
        (avd / "config.ini").write_text("hw.lcd.density=160\n", encoding="ascii")
        (avd / "userdata-qemu.img").write_bytes(b"data")
        (avd / "cache.img.qcow2").write_bytes(b"cache")
        (avd / "snapshots" / "default_boot" / "ram.img").write_bytes(b"ram")
        return avd

    def test_keeps_only_the_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            avd = self.filled(directory)
            android_emulator.discard_disks(avd)
            self.assertEqual(["config.ini"], sorted(entry.name for entry in avd.iterdir()))

    def test_a_missing_avd_is_not_an_error(self):
        with tempfile.TemporaryDirectory() as directory:
            android_emulator.discard_disks(pathlib.Path(directory) / "never-created.avd")

    def test_a_file_that_cannot_be_deleted_is_left_behind(self):
        with tempfile.TemporaryDirectory() as directory:
            avd = self.filled(directory)
            real_unlink = pathlib.Path.unlink

            def unlink(path, *arguments, **options):
                if path.name == "userdata-qemu.img":
                    raise PermissionError("in use")
                return real_unlink(path, *arguments, **options)

            with mock.patch.object(pathlib.Path, "unlink", unlink):
                android_emulator.discard_disks(avd)
            self.assertEqual(
                ["config.ini", "userdata-qemu.img"],
                sorted(entry.name for entry in avd.iterdir()),
            )


class EmulatorLifecycleTest(unittest.TestCase):
    def emulator(self, directory):
        sdk = pathlib.Path(directory) / "sdk"
        image = sdk / "system-images" / "android-23" / "default" / "x86_64"
        image.mkdir(parents=True)
        (image / "system.img").write_bytes(b"image")
        launcher = sdk / "emulator" / android_emulator.executable_name("emulator")
        launcher.parent.mkdir()
        launcher.write_bytes(b"")
        with mock.patch.object(android_emulator, "sdk_root", return_value=sdk), \
                mock.patch.object(android_emulator, "adb_path", return_value=pathlib.Path("adb")):
            return android_emulator.Emulator(
                avd_home=pathlib.Path(directory) / "avd",
                log_path=pathlib.Path(directory) / "logs" / "emulator.log",
            ), launcher

    def test_starts_the_private_avd_on_a_free_port(self):
        with tempfile.TemporaryDirectory() as directory:
            emulator, launcher = self.emulator(directory)
            process = mock.Mock()
            with mock.patch.object(android_emulator, "free_console_port", return_value=5590), \
                    mock.patch.object(android_emulator.subprocess, "Popen", return_value=process) as popen:
                emulator.start()
            try:
                self.assertEqual("emulator-5590", emulator.serial)
                command = popen.call_args.args[0]
                self.assertEqual(str(launcher), command[0])
                self.assertEqual(["-avd", "mirror-android6", "-port", "5590"], command[1:5])
                environment = popen.call_args.kwargs["env"]
                self.assertEqual(str(pathlib.Path(directory) / "avd"), environment["ANDROID_AVD_HOME"])
                self.assertTrue((pathlib.Path(directory) / "avd" / "mirror-android6.avd" / "config.ini").is_file())
            finally:
                with mock.patch.object(android_emulator.subprocess, "run"):
                    emulator.stop()

    def test_stop_asks_the_emulator_to_shut_down_and_ignores_its_exit_code(self):
        with tempfile.TemporaryDirectory() as directory:
            emulator, _ = self.emulator(directory)
            process = mock.Mock()
            process.wait.return_value = 0xC0000005
            with mock.patch.object(android_emulator, "free_console_port", return_value=5590), \
                    mock.patch.object(android_emulator.subprocess, "Popen", return_value=process):
                emulator.start()
            with mock.patch.object(android_emulator.subprocess, "run") as run, \
                    mock.patch.object(android_emulator, "terminate_tree") as terminate:
                disk = pathlib.Path(directory) / "avd" / "mirror-android6.avd" / "userdata-qemu.img"
                disk.write_bytes(b"data")
                emulator.stop()
            self.assertEqual(["adb", "-s", "emulator-5590", "emu", "kill"], run.call_args.args[0])
            terminate.assert_not_called()
            self.assertIsNone(emulator.process)
            # The next run starts from empty storage, so nothing large is kept.
            self.assertFalse(disk.exists())
            self.assertTrue(disk.with_name("config.ini").is_file())
            # Stopping twice is harmless.
            emulator.stop()

    def test_stop_keeps_the_disks_of_an_emulator_that_keeps_its_data(self):
        with tempfile.TemporaryDirectory() as directory:
            emulator, _ = self.emulator(directory)
            emulator.wipe_data = False
            with mock.patch.object(android_emulator, "free_console_port", return_value=5590), \
                    mock.patch.object(android_emulator.subprocess, "Popen", return_value=mock.Mock()):
                emulator.start()
            disk = pathlib.Path(directory) / "avd" / "mirror-android6.avd" / "userdata-qemu.img"
            disk.write_bytes(b"data")
            with mock.patch.object(android_emulator.subprocess, "run"):
                emulator.stop()
            self.assertTrue(disk.is_file())

    def test_stop_ends_the_process_tree_when_the_emulator_does_not_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            emulator, _ = self.emulator(directory)
            process = mock.Mock()
            process.wait.side_effect = [subprocess.TimeoutExpired("emulator", 60), 1]
            with mock.patch.object(android_emulator, "free_console_port", return_value=5590), \
                    mock.patch.object(android_emulator.subprocess, "Popen", return_value=process):
                emulator.start()
            with mock.patch.object(android_emulator.subprocess, "run"), \
                    mock.patch.object(android_emulator, "terminate_tree") as terminate:
                emulator.stop()
            terminate.assert_called_once_with(process)

    def test_waiting_reports_an_emulator_that_exits_before_booting(self):
        with tempfile.TemporaryDirectory() as directory:
            emulator, _ = self.emulator(directory)
            process = mock.Mock()
            process.poll.return_value = 1
            process.returncode = 1
            emulator.process = process
            emulator.port = 5590
            with self.assertRaisesRegex(EmulatorError, "exited with code 1 before booting"):
                emulator.wait_for_boot(timeout=5)
            emulator.process = None

    def test_waiting_gives_up_after_the_timeout(self):
        with tempfile.TemporaryDirectory() as directory:
            emulator, _ = self.emulator(directory)
            process = mock.Mock()
            process.poll.return_value = None
            emulator.process = process
            emulator.port = 5590
            ticks = iter([0, 1])
            with mock.patch.object(android_emulator, "boot_completed", return_value=False), \
                    mock.patch.object(android_emulator.time, "sleep"), \
                    mock.patch.object(android_emulator.time, "monotonic", side_effect=lambda: next(ticks, 500)):
                with self.assertRaisesRegex(EmulatorError, "did not finish booting within 420 s"):
                    emulator.wait_for_boot()
            emulator.process = None


if __name__ == "__main__":
    unittest.main()
