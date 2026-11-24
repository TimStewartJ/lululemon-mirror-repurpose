import pathlib
import sys
import unittest

TOOLS = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))

import identify  # noqa: E402
import install  # noqa: E402

SERIAL = "bebf077"


def thermal(properties=None, enabled="Y", cpus="0-3", unreadable=()):
    return {
        "schema": "mirror-thermal/1",
        "cpusPossible": cpus,
        "msmThermalParameters": {"enabled": enabled},
        "deviceTreeNodes": {"soc/qcom,msm-thermal": {"properties": properties or {}, "children": {}}},
        "zones": [{"name": "thermal_zone0", "type": "tsens_tz_sensor0", "temp": "41", "mode": "enabled",
                   "trips": [{"index": 0, "type": "critical", "temp": "120"}]}],
        "unreadable": list(unreadable),
    }


THROTTLING = {"qcom,limit-temp": "0000003c", "qcom,freq-step": "00000002",
              "qcom,freq-control-mask": "0000000f"}
LOG = ["[    1.2] msm_thermal:msm_thermal_dev_init Registered limits", "[    1.3] tsens: probe ok"]


class ThermalGate(unittest.TestCase):
    def test_throttling_of_every_cpu_with_a_clean_log_passes(self):
        result = install.evaluate_thermal(thermal(THROTTLING), LOG)
        self.assertEqual(result["verdict"], install.PASS, result)
        self.assertEqual(result["values"]["qcom,limit-temp"], 60)

    def test_a_chip_reset_alone_passes(self):
        result = install.evaluate_thermal(thermal({"qcom,therm-reset-temp": "00000078"}), LOG)
        self.assertEqual(result["verdict"], install.PASS, result)

    def test_over_wifi_the_answer_is_at_best_incomplete(self):
        result = install.evaluate_thermal(thermal(THROTTLING), None)
        self.assertEqual(result["verdict"], install.INCOMPLETE)

    def test_a_mask_missing_a_cpu_is_a_stop(self):
        result = install.evaluate_thermal(thermal({**THROTTLING, "qcom,freq-control-mask": "00000007"}), LOG)
        self.assertEqual(result["verdict"], install.STOP)

    def test_throttling_without_a_step_is_a_stop(self):
        properties = {**THROTTLING, "qcom,freq-step": "00000000"}
        self.assertEqual(install.evaluate_thermal(thermal(properties), LOG)["verdict"], install.STOP)

    def test_driver_not_enabled_is_a_stop(self):
        result = install.evaluate_thermal(thermal(THROTTLING, enabled="N"), LOG)
        self.assertEqual(result["verdict"], install.STOP)

    def test_an_unreadable_device_tree_property_is_a_stop_and_a_zone_is_not(self):
        zone = {"path": "/sys/class/thermal/thermal_zone3/temp", "reason": "EACCES"}
        self.assertEqual(install.evaluate_thermal(thermal(THROTTLING, unreadable=[zone]), LOG)["verdict"],
                         install.PASS)
        node = {"path": "/proc/device-tree/soc/qcom,msm-thermal/qcom,limit-temp", "reason": "EACCES"}
        result = install.evaluate_thermal(thermal(THROTTLING, unreadable=[node]), LOG)
        self.assertEqual(result["verdict"], install.STOP)
        self.assertIn("EACCES", " ".join(result["stops"]))

    def test_kernel_log_failure_or_silence_is_a_stop(self):
        failed = LOG + ["[    1.4] msm_thermal: Failed to register freq limits"]
        self.assertEqual(install.evaluate_thermal(thermal(THROTTLING), failed)["verdict"], install.STOP)
        self.assertEqual(install.evaluate_thermal(thermal(THROTTLING), ["[ 1.0] unrelated"])["verdict"],
                         install.STOP)

    def test_a_property_that_is_not_one_cell_is_a_stop(self):
        result = install.evaluate_thermal(thermal({**THROTTLING, "qcom,limit-temp": "0000003c00000001"}), LOG)
        self.assertEqual(result["verdict"], install.STOP)

    def test_no_report_and_no_node_are_stops(self):
        self.assertEqual(install.evaluate_thermal(None, LOG)["verdict"], install.STOP)
        missing = thermal(THROTTLING)
        missing["deviceTreeNodes"] = {}
        self.assertEqual(install.evaluate_thermal(missing, LOG)["verdict"], install.STOP)

    def test_cpu_lists(self):
        self.assertEqual(install.parse_cpu_list("0-3"), {0, 1, 2, 3})
        self.assertEqual(install.parse_cpu_list("0,2-3\n"), {0, 2, 3})
        self.assertIsNone(install.parse_cpu_list(""))


class OverAdb(unittest.TestCase):
    def test_reads_the_same_shape_mirror_home_reports(self):
        node = "/proc/device-tree/soc/qcom,msm-thermal"
        files = {
            "/sys/module/msm_thermal/parameters/enabled": "Y\n",
            "/sys/devices/system/cpu/possible": "0-3\n",
            "/sys/class/thermal/thermal_zone0/type": "tsens_tz_sensor0\n",
            "/sys/class/thermal/thermal_zone0/temp": "40\n",
            "/sys/class/thermal/thermal_zone0/mode": "enabled\n",
            "/sys/class/thermal/thermal_zone0/trip_point_0_temp": "120\n",
            "/sys/class/thermal/thermal_zone0/trip_point_0_type": "critical\n",
        }
        listings = {
            "/sys/module/msm_thermal/parameters": "enabled\n",
            "/proc/device-tree": "soc\n",
            "/proc/device-tree/soc": "qcom,msm-thermal\nqcom,other\n",
            "/sys/class/thermal": "cooling_device0\nthermal_zone0\n",
        }
        hexdumps = {f"{node}/qcom,limit-temp": " 00 00 00 3c\n", f"{node}/qcom,freq-step": " 00 00 00 02\n",
                    f"{node}/qcom,freq-control-mask": " 00 00 00 0f\n"}
        asked = []

        def runner(arguments):
            self.assertEqual(arguments[:4], ["adb", "-s", SERIAL, "shell"])
            command = arguments[4]
            asked.append(command)
            if command.startswith("cat '"):
                path = command[5:-1]
                return 0, files.get(path, f"cat: {path}: No such file or directory\n"), ""
            if command.startswith("ls '"):
                path = command[4:-1]
                return 0, listings.get(path, f"{path}: No such file or directory\n"), ""
            if command.startswith("for f in"):
                return 0, "qcom,limit-temp\nqcom,freq-step\nqcom,freq-control-mask\n", ""
            if command.startswith("od -An -tx1 '"):
                return 0, hexdumps[command[13:-1]], ""
            if command == "dmesg":
                return 0, "\n".join(LOG) + "\n", ""
            self.fail(f"unexpected command {command}")

        tools = identify.Tools("adb", "fastboot", runner)
        read, log = install.read_thermal_over_adb(tools, SERIAL)
        self.assertEqual(read["deviceTreeNodes"]["soc/qcom,msm-thermal"]["properties"]["qcom,limit-temp"],
                         "0000003c")
        self.assertEqual([zone["name"] for zone in read["zones"]], ["thermal_zone0"])
        self.assertEqual(read["zones"][0]["trips"], [{"index": 0, "type": "critical", "temp": "120"}])
        self.assertEqual(read["unreadable"], [])
        self.assertFalse(any(command.startswith(("echo", "rm", "setprop", "reboot")) for command in asked))
        self.assertEqual(install.evaluate_thermal(read, log)["verdict"], install.PASS)

    def test_od_folding_is_refused(self):
        def runner(arguments):
            command = arguments[4]
            if command.startswith("od"):
                return 0, " 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00\n*\n", ""
            if command.startswith("ls '/proc/device-tree/soc'"):
                return 0, "qcom,msm-thermal\n", ""
            if command.startswith("for f in"):
                return 0, "qcom,big\n", ""
            return 0, "", ""

        read, _ = install.read_thermal_over_adb(identify.Tools("adb", "fastboot", runner), SERIAL)
        self.assertNotIn("qcom,big", read["deviceTreeNodes"]["soc/qcom,msm-thermal"]["properties"])
        self.assertIn("/proc/device-tree/soc/qcom,msm-thermal/qcom,big", [e["path"] for e in read["unreadable"]])


if __name__ == "__main__":
    unittest.main()
