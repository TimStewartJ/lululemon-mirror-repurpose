import pathlib
import shlex
import sys
import unittest

TOOLS = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))

import identify  # noqa: E402
import install  # noqa: E402

SERIAL = "bebf077"
COMPATIBLE = b"qcom,msm-thermal\0".hex()


def thermal(properties=None, enabled="Y", cpus="0-3", unreadable=(), listed=True, nodes=None):
    node = {"properties": {"compatible": COMPATIBLE, **(properties or {})}, "children": {}, "listed": True}
    return {
        "schema": "mirror-thermal/1",
        "cpusPossible": cpus,
        "deviceTreeListed": listed,
        "msmThermalParameters": {"enabled": enabled},
        "deviceTreeNodes": nodes if nodes is not None else {"soc/qcom,msm-thermal": node},
        "zones": [{"name": "thermal_zone0", "type": "tsens_tz_sensor0", "temp": "41", "mode": "enabled",
                   "trips": [{"index": 0, "type": "critical", "temp": "120"}]}],
        "unreadable": list(unreadable),
    }


THROTTLING = {"qcom,limit-temp": "0000003c", "qcom,freq-step": "00000002",
              "qcom,freq-control-mask": "0000000f", "qcom,poll-ms": "000000fa"}
LOG = ["[    1.2] msm_thermal:msm_thermal_dev_init Registered limits", "[    1.3] tsens: probe ok"]


def verdict(section, log=LOG):
    return install.evaluate_thermal(section, log)["verdict"]


class ThermalGate(unittest.TestCase):
    def test_throttling_of_every_cpu_with_a_clean_log_needs_a_person_to_read_the_log(self):
        result = install.evaluate_thermal(thermal(THROTTLING), LOG)
        self.assertEqual(result["verdict"], install.REVIEW, result)
        self.assertEqual(result["values"]["qcom,limit-temp"], 60)
        self.assertEqual(result["kernelLines"], [line.strip() for line in LOG])

    def test_a_chip_reset_alone_is_enough_for_review(self):
        self.assertEqual(verdict(thermal({"qcom,therm-reset-temp": "00000078", "qcom,poll-ms": "000000fa"})), install.REVIEW)

    def test_over_wifi_the_answer_is_at_best_incomplete(self):
        self.assertEqual(verdict(thermal(THROTTLING), None), install.INCOMPLETE)

    def test_a_tree_read_by_name_cannot_rule_out_other_nodes(self):
        refused = [{"path": "/proc/device-tree", "reason": "folder cannot be listed"},
                   {"path": "/proc/device-tree/soc/qcom,msm-thermal",
                    "reason": "folder cannot be listed; known properties read by name"}]
        section = thermal(THROTTLING, listed=False, unreadable=refused)
        section["deviceTreeNodes"]["soc/qcom,msm-thermal"]["listed"] = False
        result = install.evaluate_thermal(section, LOG)
        self.assertEqual(result["verdict"], install.INCOMPLETE, result)
        self.assertEqual(verdict(thermal(THROTTLING, listed=False)), install.INCOMPLETE)
        unlisted = thermal(THROTTLING)
        unlisted["deviceTreeNodes"]["soc/qcom,msm-thermal"]["listed"] = False
        self.assertEqual(verdict(unlisted), install.INCOMPLETE)

    def test_polling_must_be_frequent_enough(self):
        self.assertEqual(verdict(thermal({**THROTTLING, "qcom,poll-ms": "05265c00"})), install.STOP)
        no_poll = {key: value for key, value in THROTTLING.items() if key != "qcom,poll-ms"}
        self.assertEqual(verdict(thermal(no_poll)), install.STOP)
        self.assertEqual(verdict(thermal({"qcom,therm-reset-temp": "00000078"})), install.STOP)

    def test_a_status_that_is_not_one_string_is_a_stop(self):
        self.assertEqual(verdict(thermal({**THROTTLING, "status": b"okay\0\0".hex()})), install.STOP)
        self.assertEqual(verdict(thermal({**THROTTLING, "status": b"okay\0".hex()})), install.REVIEW)

    def test_a_mask_missing_a_cpu_is_a_stop(self):
        self.assertEqual(verdict(thermal({**THROTTLING, "qcom,freq-control-mask": "00000007"})), install.STOP)

    def test_throttling_without_a_step_is_a_stop(self):
        self.assertEqual(verdict(thermal({**THROTTLING, "qcom,freq-step": "00000000"})), install.STOP)

    def test_temperatures_that_could_never_act_are_not_credited(self):
        self.assertEqual(verdict(thermal({"qcom,therm-reset-temp": "0000ea60", "qcom,poll-ms": "000000fa"})), install.STOP)
        self.assertEqual(verdict(thermal({**THROTTLING, "qcom,limit-temp": "0000ea60"})), install.STOP)

    def test_driver_not_enabled_is_a_stop(self):
        self.assertEqual(verdict(thermal(THROTTLING, enabled="N")), install.STOP)

    def test_an_unreadable_device_tree_property_is_a_stop_and_a_zone_is_not(self):
        zone = {"path": "/sys/class/thermal/thermal_zone3/temp", "reason": "EACCES"}
        self.assertEqual(verdict(thermal(THROTTLING, unreadable=[zone])), install.REVIEW)
        node = {"path": "/proc/device-tree/soc/qcom,msm-thermal/qcom,limit-temp", "reason": "EACCES"}
        result = install.evaluate_thermal(thermal(THROTTLING, unreadable=[node]), LOG)
        self.assertEqual(result["verdict"], install.STOP)
        self.assertIn("EACCES", " ".join(result["stops"]))

    def test_the_kernel_log_must_name_the_driver_and_show_no_trouble(self):
        for log in (
            ["[ 1.0] unrelated"],
            ["[ 1.3] tsens: probe ok"],
            LOG + ["[ 1.4] msm_thermal: could not register frequency limits"],
            LOG + ["[ 1.4] msm-thermal: Failed to get regulator"],
            LOG + ["[ 2.0] msm_thermal:interrupt_mode_init Interrupt mode init"],
        ):
            with self.subTest(log=log):
                self.assertEqual(verdict(thermal(THROTTLING), log), install.STOP)

    def test_a_kernel_line_that_may_identify_the_unit_is_left_out(self):
        log = LOG + ["[ 0.0] msm_thermal: cmdline androidboot.serialno=bebf077"]
        result = install.evaluate_thermal(thermal(THROTTLING), log)
        self.assertEqual(result["verdict"], install.STOP)
        self.assertNotIn(SERIAL, repr(result))
        shown = install.evaluate_thermal(thermal(THROTTLING), LOG + ["[ 2.0] msm_thermal: peer 02:11:22:33:44:55",
                                                                     "[ 2.1] msm_thermal: id 0x021122334455"])
        self.assertNotIn("02:11:22:33:44:55", repr(shown))
        self.assertNotIn("021122334455", repr(shown))

    def test_evidence_is_judged_before_it_is_trimmed_for_the_report(self):
        long_status = thermal({**THROTTLING, "status": (b"disabled\0" + b"x" * 1023 + b"\0").hex()})
        self.assertEqual(verdict(long_status), install.STOP)
        long_name = "soc/qcom,msm-thermal" + "x" * 117
        twice = thermal(THROTTLING)
        twice["deviceTreeNodes"][long_name] = twice["deviceTreeNodes"]["soc/qcom,msm-thermal"]
        result = install.evaluate_thermal(twice, LOG)
        self.assertEqual(result["verdict"], install.STOP)
        self.assertNotIn("x" * 117, repr(result))

    def test_a_failed_property_read_counts_whatever_its_reason_says(self):
        unlisted = thermal(THROTTLING, unreadable=[
            {"path": "/proc/device-tree/soc/qcom,msm-thermal", "reason": "folder cannot be listed; known "
                                                                          "properties read by name"},
            {"path": "/proc/device-tree/soc/qcom,msm-thermal/status",
             "reason": "Permission denied; known properties read by name"}])
        unlisted["deviceTreeNodes"]["soc/qcom,msm-thermal"]["listed"] = False
        self.assertEqual(verdict(unlisted), install.STOP)

    def test_every_candidate_must_have_a_record_and_the_ledger_must_be_a_list(self):
        extra = thermal(THROTTLING)
        extra["deviceTreeNodes"]["soc/qcom,msm-thermal@1"] = None
        self.assertEqual(verdict(extra), install.STOP)
        no_ledger = thermal(THROTTLING)
        no_ledger["unreadable"] = None
        self.assertEqual(verdict(no_ledger), install.STOP)

    def test_node_names_and_values_that_may_identify_the_unit_are_not_shown(self):
        named = thermal(nodes={"soc/qcom,msm-thermal-serial-UNIT-7K4M": {"properties": {
            "compatible": COMPATIBLE, **THROTTLING}, "listed": True}})
        shown = repr(install.evaluate_thermal(named, LOG)) + repr(install.exported(named))
        self.assertNotIn("UNIT-7K4M", shown)
        self.assertEqual(verdict(named), install.STOP)
        odd = thermal({**THROTTLING, "qcom,sensor-id": "021122334455"})
        self.assertNotIn("021122334455", repr(install.exported(odd)))
        self.assertEqual(verdict(odd), install.STOP)
        dotted = install.evaluate_thermal(thermal(THROTTLING), LOG + ["[ 2.0] msm_thermal: peer 0211.2233.4455"])
        self.assertNotIn("0211.2233.4455", repr(dotted))

    def test_labelled_identifiers_in_diagnostics_are_withheld(self):
        section = thermal(THROTTLING, unreadable=[{"path": "/sys/module/msm_thermal/parameters/enabled",
                                                   "reason": "serial=UNIT-7K4M"}])
        shown = repr(install.exported(section)) + repr(install.evaluate_thermal(section, LOG))
        self.assertNotIn("UNIT-7K4M", shown)
        self.assertEqual(install.evaluate_thermal({"schema": "mirror-thermal/1", "unreadable": [
            {"path": None, "reason": None}]}, LOG)["verdict"], install.STOP)

    def test_a_property_that_is_not_one_cell_is_a_stop(self):
        self.assertEqual(verdict(thermal({**THROTTLING, "qcom,limit-temp": "0000003c00000001"})), install.STOP)

    def test_the_node_must_be_the_drivers_and_the_only_one(self):
        self.assertEqual(verdict(None), install.STOP)
        self.assertEqual(verdict(thermal(THROTTLING, nodes={})), install.STOP)
        no_compatible = {"soc/qcom,msm-thermal": {"properties": {"qcom,therm-reset-temp": "00000078"}}}
        self.assertEqual(verdict(thermal(nodes=no_compatible)), install.STOP)
        disabled = {"soc/qcom,msm-thermal": {"properties": {
            "compatible": COMPATIBLE, "status": b"disabled\0".hex(), "qcom,therm-reset-temp": "00000078"}}}
        self.assertEqual(verdict(thermal(nodes=disabled)), install.STOP)
        enabled = {"properties": {"compatible": COMPATIBLE, "qcom,therm-reset-temp": "00000078"}}
        two = {"soc/qcom,msm-thermal": enabled, "soc/qcom,msm-thermal@0": enabled}
        self.assertEqual(verdict(thermal(nodes=two)), install.STOP)
        shouting = {"properties": {"compatible": b"QCOM,MSM-THERMAL\0".hex(), "qcom,therm-reset-temp": "00000078"}}
        with_case = thermal(THROTTLING)
        with_case["deviceTreeNodes"]["qcom,msm-thermal@1"] = shouting
        self.assertEqual(verdict(with_case), install.STOP)

    def test_what_leaves_this_computer_is_only_what_the_gate_reads(self):
        old = thermal({**THROTTLING, "qcom,wlan-addr": "021122334455"})
        old["msmThermalParameters"]["sensor_serial"] = SERIAL
        old["unreadable"] = [{"path": "/sys/x", "reason": "peer 02:11:22:33:44:55 id 0x021122334455"}]
        shown = repr(install.exported(old)) + repr(install.evaluate_thermal(old, LOG))
        for secret in (SERIAL, "021122334455", "02:11:22:33:44:55"):
            self.assertNotIn(secret, shown)
        self.assertEqual(install.exported(old)["msmThermalParameters"], {"enabled": "Y"})

    def test_cpu_lists(self):
        self.assertEqual(install.parse_cpu_list("0-3"), {0, 1, 2, 3})
        self.assertEqual(install.parse_cpu_list("0,2-3\n"), {0, 2, 3})
        for bad in ("", "0,3-1", "0-40", "0-"):
            self.assertIsNone(install.parse_cpu_list(bad), bad)
        self.assertEqual(verdict(thermal({**THROTTLING, "qcom,freq-control-mask": "00000001"}, cpus="0,3-1")),
                         install.STOP)


class OverAdb(unittest.TestCase):
    NODE = "/proc/device-tree/soc/qcom,msm-thermal"

    def unit(self, overrides=None, listings_extra=None):
        node = self.NODE
        files = {
            "/sys/module/msm_thermal/parameters/enabled": "Y\n",
            "/sys/devices/system/cpu/possible": "0-3\n",
            "/sys/class/thermal/thermal_zone0/type": "tsens_tz_sensor0\n",
            "/sys/class/thermal/thermal_zone0/temp": "40\n",
            "/sys/class/thermal/thermal_zone0/mode": "enabled\n",
            "/sys/class/thermal/thermal_zone0/trip_point_0_temp": "120\n",
            "/sys/class/thermal/thermal_zone0/trip_point_0_type": "critical\n",
        }
        folders = {"/sys/module/msm_thermal/parameters"}
        listings = {
            "/proc/device-tree": "#address-cells\n#size-cells\nsoc\n",
            "/proc/device-tree/soc": "#address-cells\n#size-cells\nqcom,msm-thermal\nqcom,other\n",
            node: "compatible\nqcom,limit-temp\nqcom,freq-step\nqcom,freq-control-mask\nqcom,poll-ms\n"
                  "qcom,wlan-addr\nqcom,vdd-dig-rstr\n",
            "/sys/class/thermal": "cooling_device0\nthermal_zone0\n",
            "/sys/class/thermal/thermal_zone0": "mode\ntemp\ntrip_point_0_temp\ntrip_point_0_type\ntype\n",
            **(listings_extra or {}),
        }
        dumps = {"compatible": " ".join(COMPATIBLE[i:i + 2] for i in range(0, len(COMPATIBLE), 2)),
                 "qcom,limit-temp": "00 00 00 3c", "qcom,freq-step": "00 00 00 02",
                 "qcom,freq-control-mask": "00 00 00 0f", "qcom,poll-ms": "00 00 00 fa",
                 "qcom,wlan-addr": "02 11 22 33 44 55"}
        overrides = overrides or {}
        asked = []
        ok, failed = f"{install.STATUS_MARKER}0\n", f"{install.STATUS_MARKER}1\n"

        def runner(arguments):
            self.assertEqual(arguments[:4], ["adb", "-s", SERIAL, "shell"])
            command, _, tail = arguments[4].rpartition(" 2>&1; echo ")
            self.assertEqual(tail, f"{install.STATUS_MARKER}$?")
            asked.append(command)
            if command in overrides:
                return overrides[command]
            words = shlex.split(command)
            if words[0] == "cat":
                return (0, files[words[1]] + ok, "") if words[1] in files else \
                    (0, f"{words[1]}: No such file or directory\n" + failed, "")
            if words[0] == "[":
                there = words[2] in files or words[2] in folders or words[2] in listings
                return 0, ok if there else failed, ""
            if words[0] == "ls":
                self.assertTrue(words[1].endswith("/"), "a folder is listed with a trailing slash")
                return 0, listings[words[1].rstrip("/")] + ok, ""
            if words[:3] == ["od", "-An", "-tx1"]:
                return 0, " " + dumps[words[3].rsplit("/", 1)[1]] + "\n" + ok, ""
            if words == ["dmesg"]:
                return 0, "\n".join(LOG) + "\n" + ok, ""
            self.fail(f"unexpected command {command}")

        return identify.Tools("adb", "fastboot", runner), asked

    def test_reads_the_same_shape_mirror_home_reports(self):
        tools, asked = self.unit()
        read, log = install.read_thermal_over_adb(tools, SERIAL)
        node = read["deviceTreeNodes"]["soc/qcom,msm-thermal"]
        self.assertEqual(node["properties"]["qcom,limit-temp"], "0000003c")
        self.assertEqual(install.strings(node["properties"]["compatible"]), ["qcom,msm-thermal"])
        self.assertNotIn("qcom,wlan-addr", node["properties"])
        self.assertNotIn("021122334455", repr(read))
        self.assertTrue(node["listed"])
        self.assertTrue(read["deviceTreeListed"])
        self.assertEqual(read["msmThermalParameters"], {"enabled": "Y"})
        self.assertEqual([zone["name"] for zone in read["zones"]], ["thermal_zone0"])
        self.assertEqual(read["zones"][0]["trips"], [{"index": 0, "type": "critical", "temp": "120"}])
        self.assertEqual(read["unreadable"], [])
        self.assertFalse(any(command.startswith(("echo", "rm", "setprop", "reboot")) for command in asked))
        self.assertEqual(install.evaluate_thermal(read, log)["verdict"], install.REVIEW)

    def test_a_failure_adb_does_not_report_is_still_caught(self):
        tools, _ = self.unit({"ls /proc/device-tree/": (0, f"ls: {SERIAL}: Input/output error\n"
                                                          f"{install.STATUS_MARKER}1\n", "")})
        read, log = install.read_thermal_over_adb(tools, SERIAL)
        self.assertFalse(read["deviceTreeListed"])
        self.assertNotIn(SERIAL, repr(read))
        self.assertIn("<unit>", read["unreadable"][0]["reason"])
        self.assertNotEqual(install.evaluate_thermal(read, log)["verdict"], install.REVIEW)

    def test_a_command_without_its_status_counts_as_failed(self):
        tools, _ = self.unit({"cat /sys/module/msm_thermal/parameters/enabled": (0, "Y\n", "")})
        read, log = install.read_thermal_over_adb(tools, SERIAL)
        self.assertEqual(read["msmThermalParameters"], {})
        self.assertEqual(install.evaluate_thermal(read, log)["verdict"], install.STOP)

    def test_a_trip_point_that_cannot_be_read_is_recorded(self):
        path = "/sys/class/thermal/thermal_zone0/trip_point_0_temp"
        tools, _ = self.unit({f"cat {path}": (0, f"cat: {path}: Permission denied 02:11:22:33:44:55\n"
                                                 f"{install.STATUS_MARKER}1\n", "")})
        read, _ = install.read_thermal_over_adb(tools, SERIAL)
        self.assertEqual(read["zones"][0]["trips"][0]["temp"], None)
        self.assertIn(path, [entry["path"] for entry in read["unreadable"]])
        self.assertNotIn("02:11:22:33:44:55", repr(read))

    def test_names_that_are_not_plain_are_never_put_in_a_command(self):
        tools, asked = self.unit(listings_extra={"/proc/device-tree/soc": "qcom,msm-thermal'; reboot; '\n"
                                                                          "qcom,msm-thermal\n"})
        read, _ = install.read_thermal_over_adb(tools, SERIAL)
        self.assertFalse(any("reboot" in command for command in asked))
        self.assertIn("/proc/device-tree/soc", [entry["path"] for entry in read["unreadable"]])

    def test_a_kernel_log_without_its_status_is_refused_without_repeating_it(self):
        tools, _ = self.unit({"dmesg": (0, "[ 0.0] androidboot.serialno=UNIT-7K4M\n", "")})
        with self.assertRaises(RuntimeError) as raised:
            install.read_thermal_over_adb(tools, SERIAL)
        self.assertNotIn("UNIT-7K4M", str(raised.exception))

    def test_od_output_that_is_not_byte_pairs_is_refused(self):
        for dump in (" 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00\n*\n", " 0 0000078\n"):
            with self.subTest(dump=dump):
                tools, _ = self.unit({f"od -An -tx1 {self.NODE}/qcom,limit-temp":
                                      (0, dump + f"{install.STATUS_MARKER}0\n", "")})
                read, _ = install.read_thermal_over_adb(tools, SERIAL)
                self.assertNotIn("qcom,limit-temp", read["deviceTreeNodes"]["soc/qcom,msm-thermal"]["properties"])
                self.assertIn(f"{self.NODE}/qcom,limit-temp", [e["path"] for e in read["unreadable"]])


if __name__ == "__main__":
    unittest.main()
