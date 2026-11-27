import argparse
import contextlib
import hashlib
import io
import json
import os
import pathlib
import shlex
import shutil
import signal
import stat
import struct
import subprocess
import sys
import tempfile
import unittest
import unittest.mock

TOOLS = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))

import identify  # noqa: E402
import install  # noqa: E402

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import test_ext4image  # noqa: E402

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


PROBE = {"qcom,sensor-id": "00000005", "qcom,poll-ms": "000000fa", "qcom,limit-temp": "0000003c",
         "qcom,temp-hysteresis": "0000000a", "qcom,freq-step": "00000002"}
THROTTLING = {**PROBE, "qcom,freq-control-mask": "0000000f"}
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
        self.assertEqual(verdict(thermal({**PROBE, "qcom,therm-reset-temp": "00000078"})), install.REVIEW)

    def test_the_driver_must_have_what_its_probe_needs(self):
        for name in PROBE:
            with self.subTest(missing=name):
                without = {key: value for key, value in THROTTLING.items() if key != name}
                self.assertEqual(verdict(thermal({**without, "qcom,therm-reset-temp": "00000078"})), install.STOP)

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
        broken = {"schema": "mirror-thermal/1", "deviceTreeNodes": ["bad"], "zones": {"x": 1}, "unreadable": "x"}
        self.assertEqual(verdict(broken), install.STOP)
        self.assertEqual(install.exported(broken)["deviceTreeNodes"], {})
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
                  "qcom,sensor-id\nqcom,temp-hysteresis\n"
                  "qcom,wlan-addr\nqcom,vdd-dig-rstr\n",
            "/sys/class/thermal": "cooling_device0\nthermal_zone0\n",
            "/sys/class/thermal/thermal_zone0": "mode\ntemp\ntrip_point_0_temp\ntrip_point_0_type\ntype\n",
            **(listings_extra or {}),
        }
        dumps = {"compatible": " ".join(COMPATIBLE[i:i + 2] for i in range(0, len(COMPATIBLE), 2)),
                 "qcom,limit-temp": "00 00 00 3c", "qcom,freq-step": "00 00 00 02",
                 "qcom,freq-control-mask": "00 00 00 0f", "qcom,poll-ms": "00 00 00 fa",
                 "qcom,sensor-id": "00 00 00 05", "qcom,temp-hysteresis": "00 00 00 0a",
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

    def test_each_listed_line_is_one_name(self):
        tools, _ = self.unit(listings_extra={"/proc/device-tree/soc": "qcom,msm-thermal\nqcom,msm-thermal \n"})
        read, log = install.read_thermal_over_adb(tools, SERIAL)
        self.assertEqual(install.evaluate_thermal(read, log)["verdict"], install.STOP)
        tools, _ = self.unit(listings_extra={"/proc/device-tree/soc": "qcom,msm-thermal\r\nqcom,other\r\n"})
        read, log = install.read_thermal_over_adb(tools, SERIAL)
        self.assertEqual(install.evaluate_thermal(read, log)["verdict"], install.REVIEW)
        tools, asked = self.unit(listings_extra={"/proc/device-tree/soc": "qcom,msm-thermal\n"
                                                                          "qcom,msm-thermal alternate\n"})
        read, log = install.read_thermal_over_adb(tools, SERIAL)
        self.assertIn("/proc/device-tree/soc", [entry["path"] for entry in read["unreadable"]])
        self.assertEqual(install.evaluate_thermal(read, log)["verdict"], install.STOP)

    def test_address_shaped_names_are_never_shown(self):
        tools, _ = self.unit(listings_extra={"/proc/device-tree/soc": "qcom,msm-thermal\n"
                                                                      "qcom,msm-thermal@0211.2233.4455\n"})
        read, log = install.read_thermal_over_adb(tools, SERIAL)
        result = install.evaluate_thermal(read, log)
        self.assertEqual(result["verdict"], install.STOP)
        self.assertNotIn("0211.2233.4455", repr(result) + repr(install.exported(read)))
        mac_compatible = thermal({**THROTTLING, "compatible": b"qcom,msm-thermal\0peer.0211.2233.4455\0".hex()})
        self.assertNotIn(b"0211.2233.4455".hex(), repr(install.exported(mac_compatible)))
        for text in ("qcom,msm-thermal_0211.2233.4455", "x_02:11:22:33:44:55", "id_0x021122334455"):
            self.assertNotEqual(install.redact(text), text, text)
        underscore = thermal(nodes={"soc/qcom,msm-thermal_0211.2233.4455": {"properties": {
            "compatible": COMPATIBLE, **THROTTLING}, "listed": True}})
        self.assertEqual(verdict(underscore), install.STOP)
        self.assertNotIn("0211.2233.4455", repr(install.exported(underscore)))
        numbers = thermal({**THROTTLING, "compatible": 7, "qcom,poll-ms": 250})
        self.assertEqual(verdict(numbers), install.STOP)
        install.exported(numbers)

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

    def test_a_missing_probe_input_does_not_blame_poll_ms(self):
        without = {key: value for key, value in THROTTLING.items() if key != "qcom,sensor-id"}
        stops = install.evaluate_thermal(thermal(without), LOG)["stops"]
        self.assertTrue(any("qcom,sensor-id" in stop for stop in stops), stops)
        self.assertFalse(any("poll-ms is" in stop for stop in stops), stops)
        slow = install.evaluate_thermal(thermal({**THROTTLING, "qcom,poll-ms": "00002710"}), LOG)["stops"]
        self.assertTrue(any("poll-ms is 10000" in stop for stop in slow), slow)


BLOCK = 4096
# 1 GiB, as CAF's bootloader sends it (hex without 0x), and the file system userdata.sh makes for it.
USERDATA_PARTITION = 1 << 30
USERDATA_BLOCKS = (USERDATA_PARTITION - install.CRYPT_FOOTER) // BLOCK


def superblock(blocks, magic=install.EXT4_MAGIC, inodes_per_group=16, blocks_per_group=8 * BLOCK, inodes=None):
    """An ext4 superblock with 4096-byte blocks and a geometry a kernel would mount."""
    groups = -(-blocks // blocks_per_group) if blocks_per_group else 0
    block = bytearray(1024)
    for offset, value in ((0x00, groups * inodes_per_group if inodes is None else inodes), (0x04, blocks),
                          (0x18, 2), (0x20, blocks_per_group), (0x28, inodes_per_group), (0x4C, 1)):
        struct.pack_into("<I", block, offset, value)
    struct.pack_into("<H", block, 0x38, magic)
    struct.pack_into("<H", block, 0x58, 256)
    return bytes(block)


def sparse(blocks, file_system_blocks=None, magic=install.EXT4_MAGIC, header_blocks=None, cut=0, extra=b"",
           tail=None, first=None, crc=False, checksum=0):
    """A sparse ext4 image of ``blocks`` blocks; ``tail`` is the first block after a smaller file system."""
    file_system_blocks = blocks if file_system_blocks is None else file_system_blocks
    first = bytes(1024) + superblock(file_system_blocks, magic) if first is None else first
    chunks = [struct.pack("<HHII", install.CHUNK_RAW, 0, 1, 12 + BLOCK) + first + bytes(BLOCK - len(first)),
              struct.pack("<HHII", install.CHUNK_FILL, 0, 2, 16) + b"\xff\0\0\0"]
    if crc:
        chunks.append(struct.pack("<HHII", install.CHUNK_CRC32, 0, 0, 16) + bytes(4))
    if tail is None:
        chunks.append(struct.pack("<HHII", install.CHUNK_DONT_CARE, 0, blocks - 3, 12))
    else:
        chunks += [struct.pack("<HHII", install.CHUNK_DONT_CARE, 0, file_system_blocks - 3, 12),
                   struct.pack("<HHII", install.CHUNK_RAW, 0, 1, 12 + BLOCK) + tail + bytes(BLOCK - len(tail)),
                   struct.pack("<HHII", install.CHUNK_DONT_CARE, 0, blocks - file_system_blocks - 1, 12)]
    header = struct.pack("<IHHHHIIII", install.SPARSE_MAGIC, 1, 0, 28, 12, BLOCK,
                         blocks if header_blocks is None else header_blocks, len(chunks), checksum)
    image = header + b"".join(chunks) + extra
    return image[:len(image) - cut]


def encode_sparse(raw, block_size):
    """``raw`` as a sparse image of one chunk per block: a fill chunk where the block repeats one word."""
    chunks = []
    for offset in range(0, len(raw), block_size):
        data = raw[offset:offset + block_size]
        if data == data[:4] * (block_size // 4):
            chunks.append(struct.pack("<HHII", install.CHUNK_FILL, 0, 1, 16) + data[:4])
        else:
            chunks.append(struct.pack("<HHII", install.CHUNK_RAW, 0, 1, 12 + block_size) + data)
    header = struct.pack("<IHHHHIIII", install.SPARSE_MAGIC, 1, 0, 28, 12, block_size, len(raw) // block_size,
                         len(chunks), 0)
    return header + b"".join(chunks)


SYSTEM = sparse(32)
USERDATA = sparse(USERDATA_BLOCKS)
VERITY = struct.pack("<II", install.VERITY_MAGIC, 0)


class Plan(unittest.TestCase):
    def folder(self, files, hashes=True):
        directory = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, directory)
        for name, data in files.items():
            (directory / name).write_bytes(data)
            if hashes and name.endswith(".img"):
                (directory / f"{name}.sha256").write_text(f"{hashlib.sha256(data).hexdigest()}  {name}\n")
        return directory

    def unit(self, sizes=None, version=install.FASTBOOT_VERSION, adb="", fastboot=f"{SERIAL}\tfastboot\n"):
        sizes = {"system": "0x0000000000040000", "userdata": f"{USERDATA_PARTITION:x}"} if sizes is None else sizes
        asked = []

        def runner(arguments):
            asked.append(arguments)
            tool, *rest = arguments
            if tool == "adb":
                self.assertEqual(rest, ["devices"])
                return 0, "List of devices attached\n" + adb, ""
            self.assertTrue(tool.endswith(("fastboot", "fastboot.exe")), tool)
            if rest == ["--version"]:
                return 0, f"fastboot version {version}\nInstalled as /opt/platform-tools/fastboot\n", ""
            if rest == ["devices"]:
                return 0, fastboot, ""
            self.assertEqual(rest[:3], ["-s", SERIAL, "getvar"])
            self.assertEqual(len(rest), 4)
            partition = rest[3].removeprefix("partition-size:")
            if partition not in sizes:
                return 1, "", f"getvar:{rest[3]} FAILED (remote: 'GetVar Variable Not found')\nFinished.\n"
            return 0, "", f"{rest[3]}: {sizes[partition]}\nFinished. Total time: 0.001s\n"

        return identify.Tools("adb", "fastboot", runner), asked

    def plan(self, files=None, **unit):
        tools, asked = self.unit(**unit)
        folder = self.folder({"system.img": SYSTEM, "userdata.img": USERDATA} if files is None else files)
        result = install.make_plan(tools, folder, None)
        for arguments in asked:
            # Nothing but questions reaches the unit.
            self.assertTrue(arguments[1:] in (["devices"], ["--version"]) or arguments[3] == "getvar", arguments)
        return result

    def test_ready_lists_only_system_then_userdata(self):
        result = self.plan()
        self.assertEqual(result["verdict"], install.READY, result)
        self.assertEqual(result["writes"], [["fastboot", "-s", "<serial>", "flash", "system", "system.img"],
                                            ["fastboot", "-s", "<serial>", "flash", "userdata", "userdata.img"]])
        self.assertEqual(result["images"]["system"]["unpackedBytes"], 32 * BLOCK)
        self.assertEqual(result["images"]["system"]["sha256"], hashlib.sha256(SYSTEM).hexdigest())
        self.assertEqual(result["partitionBytes"], {"system": 0x40000, "userdata": USERDATA_PARTITION})
        self.assertNotIn(SERIAL, json.dumps(result))
        self.assertEqual(install.EXIT_CODES[result["verdict"]], 0)

    def test_no_other_partition_or_image_can_be_written(self):
        for partition in ("boot", "aboot", "recovery", "modem", "System", "system ", ""):
            with self.subTest(partition=partition), self.assertRaises(ValueError):
                install.flash_arguments(SERIAL, partition, f"{partition}.img")
        for image in ("boot.img", "system.img", "../userdata.img", "userdata.img.sha256"):
            with self.subTest(image=image), self.assertRaises(ValueError):
                install.flash_arguments(SERIAL, "userdata", image)
        self.assertEqual(install.flash_arguments(SERIAL, "system", "system.img"),
                         ["-s", SERIAL, "flash", "system", "system.img"])

    def test_another_fastboot_is_a_stop(self):
        result = self.plan(version="34.0.5-10900879")
        self.assertEqual(result["verdict"], install.STOP)
        self.assertEqual(result["writes"], [])

    def test_anything_else_in_the_folder_is_a_stop(self):
        result = self.plan({"system.img": SYSTEM, "userdata.img": USERDATA, "boot.img": b"ANDROID!"})
        self.assertEqual(result["verdict"], install.STOP)
        self.assertIn("boot.img", result["stops"][0])

    def test_each_image_must_match_its_hash(self):
        tools, _ = self.unit()
        folder = self.folder({"system.img": SYSTEM, "userdata.img": USERDATA})
        for text in (f"{'0' * 64}  system.img\n", f"{hashlib.sha256(SYSTEM).hexdigest()}  other.img\n",
                     f"{hashlib.sha256(SYSTEM).hexdigest()}  system.img\n{'0' * 64}  system.img\n"):
            with self.subTest(text=text):
                (folder / "system.img.sha256").write_text(text)
                self.assertEqual(install.make_plan(tools, folder, None)["verdict"], install.STOP)
        (folder / "system.img.sha256").unlink()
        self.assertEqual(install.make_plan(tools, folder, None)["verdict"], install.STOP)

    def test_only_whole_ext4_images_are_read(self):
        bad = {"an Android boot image": b"ANDROID!" + bytes(4096),
               "a cut-off sparse image": sparse(32, cut=2),
               "a sparse image with bytes after its last chunk": sparse(32, extra=b"\0"),
               "a header that disagrees with its chunks": sparse(32, header_blocks=33),
               "a file system other than ext4": sparse(32, magic=0x1234)}
        folder = self.folder({}, hashes=False)
        for what, data in bad.items():
            with self.subTest(what):
                (folder / "x.img").write_bytes(data)
                with self.assertRaises(ValueError):
                    install.image_layout(folder / "x.img")
        result = self.plan({"system.img": sparse(32, file_system_blocks=40), "userdata.img": USERDATA})
        self.assertEqual(result["verdict"], install.STOP)
        raw = bytes(1024) + superblock(4) + bytes(4 * BLOCK - 2048)
        (folder / "raw.img").write_bytes(raw)
        self.assertEqual(install.image_layout(folder / "raw.img"),
                         {"format": "raw", "unpackedBytes": 4 * BLOCK, "fileSystemBytes": 4 * BLOCK,
                          "afterFileSystem": None})

    def test_only_dm_verity_may_follow_the_system_file_system(self):
        result = self.plan({"system.img": sparse(32, file_system_blocks=30, tail=VERITY), "userdata.img": USERDATA})
        self.assertEqual(result["verdict"], install.READY, result)
        self.assertEqual(result["images"]["system"]["afterFileSystem"], "verity")
        self.assertTrue(any(f"{2 * BLOCK} bytes of dm-verity" in note for note in result["notes"]), result)
        for tail in (b"ANDROID!", None):
            with self.subTest(tail=tail):
                image = sparse(32, file_system_blocks=30, tail=tail) if tail else sparse(32, file_system_blocks=30)
                result = self.plan({"system.img": image, "userdata.img": USERDATA})
                self.assertEqual(result["verdict"], install.STOP, result)
        result = self.plan({"system.img": SYSTEM,
                            "userdata.img": sparse(USERDATA_BLOCKS + 2, USERDATA_BLOCKS, tail=VERITY)})
        self.assertEqual(result["verdict"], install.STOP)
        self.assertIn("userdata.sh never writes", result["stops"][0])

    def test_system_must_fit_its_partition(self):
        result = self.plan(sizes={"system": "0x1f000", "userdata": f"{USERDATA_PARTITION:x}"})
        self.assertEqual(result["verdict"], install.STOP)
        self.assertIn("system partition", result["stops"][0])

    def test_userdata_is_built_for_this_partition_with_the_footer_left_free(self):
        result = self.plan(sizes={"system": "0x40000", "userdata": f"{2 * USERDATA_PARTITION:#x}"})
        self.assertEqual(result["verdict"], install.STOP)
        self.assertIn("another partition", result["stops"][0])
        whole = self.plan({"system.img": SYSTEM, "userdata.img": sparse(USERDATA_PARTITION // BLOCK)})
        self.assertEqual(whole["verdict"], install.STOP)

    def test_without_userdata_it_gives_the_size_to_build_it_in_hex(self):
        result = self.plan({"system.img": SYSTEM})
        self.assertEqual(result["verdict"], install.INCOMPLETE)
        self.assertEqual(result["writes"], [])
        self.assertIn("./userdata.sh 0x40000000 adbkey.pub userdata.img", result["userdataCommand"])

    def test_without_the_unit_in_fastboot_only_the_images_are_checked(self):
        for unit in ({"fastboot": ""}, {"fastboot": "", "adb": f"{SERIAL}\tdevice\n"}):
            with self.subTest(unit=unit):
                result = self.plan(**unit)
                self.assertEqual(result["verdict"], install.INCOMPLETE, result)
                self.assertEqual(result["writes"], [])
                self.assertEqual(set(result["images"]), {"system", "userdata"})

    def test_a_size_the_bootloader_does_not_give_is_a_stop(self):
        result = self.plan(sizes={"system": "0x40000"})
        self.assertEqual(result["verdict"], install.STOP)
        self.assertEqual(result["writes"], [])

    def test_only_one_whole_size_line_is_read(self):
        for answer in ("\n40000", "0x40000\npartition-size:system: 0x80000", "0x40000 0x80000"):
            with self.subTest(answer=answer):
                result = self.plan(sizes={"system": answer, "userdata": f"{USERDATA_PARTITION:x}"})
                self.assertEqual(result["verdict"], install.STOP, result)

    def test_superblocks_no_kernel_would_mount_are_refused(self):
        bad = {"no inodes": superblock(32, inodes_per_group=0, inodes=0),
               "no blocks per group": superblock(32, blocks_per_group=0),
               "inodes that do not add up": superblock(32, inodes=17)}
        for what, block in bad.items():
            with self.subTest(what):
                result = self.plan({"system.img": sparse(32, first=bytes(1024) + block), "userdata.img": USERDATA})
                self.assertEqual(result["verdict"], install.STOP, result)
                self.assertIn("not consistent", result["stops"][0])
        sixty_four = bytearray(superblock(32))
        struct.pack_into("<I", sixty_four, 0x60, 0x80)
        result = self.plan({"system.img": sparse(32, first=bytes(1024) + bytes(sixty_four)), "userdata.img": USERDATA})
        self.assertIn("64-bit", result["stops"][0])

    def test_sparse_checksums_are_refused_not_skipped(self):
        for image in (sparse(32, crc=True), sparse(32, checksum=0x1B3463E3)):
            result = self.plan({"system.img": image, "userdata.img": USERDATA})
            self.assertEqual(result["verdict"], install.STOP, result)
            self.assertIn("checksum", result["stops"][0])

    def test_a_superblock_across_chunks_and_fills_is_read(self):
        raw = bytes(1024) + superblock(4) + bytes(4 * BLOCK - 2048)
        folder = self.folder({}, hashes=False)
        (folder / "small-blocks.img").write_bytes(encode_sparse(raw, 512))
        self.assertEqual(install.image_layout(folder / "small-blocks.img"),
                         {"format": "sparse", "unpackedBytes": 4 * BLOCK, "fileSystemBytes": 4 * BLOCK,
                          "afterFileSystem": None})

    def test_the_writes_name_the_fastboot_whose_version_was_checked(self):
        tools, _ = self.unit()
        tools.fastboot_path = r"C:\Program Files\platform-tools\fastboot.exe"
        folder = self.folder({"system.img": SYSTEM, "userdata.img": USERDATA})
        result = install.make_plan(tools, folder, None)
        self.assertEqual(result["verdict"], install.READY, result)
        self.assertEqual(result["writes"][0], [tools.fastboot_path, "-s", "<serial>", "flash", "system", "system.img"])
        self.assertTrue(pathlib.Path(install.absolute_tool(r"platform-tools\fastboot.exe")).is_absolute())

    def test_the_writes_are_rendered_for_the_shell_they_are_run_in(self):
        write = ["/opt/platform tools/fastboot", "-s", "<serial>", "flash", "system", "system.img"]
        self.assertEqual(shlex.split(install.render(write, "posix")),
                         ["/opt/platform tools/fastboot", "-s", "$SERIAL", "flash", "system", "system.img"])
        rendered = install.render([r"C:\Program Files\it's\fastboot.exe", *write[1:]], "powershell")
        self.assertEqual(rendered, "& 'C:\\Program Files\\it''s\\fastboot.exe' -s $serial flash system system.img")
        powershell = shutil.which("pwsh") or shutil.which("powershell")
        if not powershell:
            self.skipTest("no PowerShell to parse the command with")
        # PowerShell parses the line and compares what it would run with the path given (exit 0 when equal).
        script = ("$e = $null; $t = $null; $a = [System.Management.Automation.Language.Parser]::ParseInput("
                  "$env:RENDERED, [ref]$t, [ref]$e); if ($e.Count) { exit 3 }; "
                  "$c = $a.EndBlock.Statements[0].PipelineElements[0]; $x = $c.CommandElements; "
                  "if ($c.InvocationOperator -ne 'Ampersand' -or $x.Count -ne 6 -or $x[0].Value -cne $env:EXPECTED "
                  "-or (($x[1..5] | ForEach-Object { $_.Extent.Text }) -join ' ') -cne "
                  "'-s $serial flash system system.img') { exit 4 }")
        for path in (r"C:\Program Files\it's\fastboot.exe", "C:\\Users\\O\u2019Brien \u2018x\u201b\\fastboot.exe"):
            with self.subTest(path=path):
                rendered = install.render([path, *write[1:]], "powershell")
                parsed = subprocess.run([powershell, "-NoProfile", "-NonInteractive", "-Command", script], text=True,
                                        capture_output=True, env={**os.environ, "RENDERED": rendered, "EXPECTED": path},
                                        timeout=60)
                self.assertEqual(parsed.returncode, 0, parsed.stderr)

    def test_a_folder_that_changes_before_ready_is_a_stop(self):
        tools, _ = self.unit()
        folder = self.folder({"system.img": SYSTEM, "userdata.img": USERDATA})
        answer = tools.runner

        def runner(arguments):
            if arguments[3:4] == ["getvar"]:
                (folder / "boot.img").write_bytes(b"ANDROID!")
            return answer(arguments)

        tools.runner = runner
        result = install.make_plan(tools, folder, None)
        self.assertEqual(result["verdict"], install.STOP, result)
        self.assertIn("changed", result["stops"][0])
        self.assertEqual(result["writes"], [])

    def test_two_units_are_refused_without_naming_them(self):
        tools, _ = self.unit(fastboot=f"{SERIAL}\tfastboot\nother42\tfastboot\n")
        with self.assertRaises(RuntimeError) as raised:
            install.make_plan(tools, self.folder({"system.img": SYSTEM}), None)
        self.assertNotIn(SERIAL, str(raised.exception))
        self.assertNotIn("other42", str(raised.exception))

    def test_a_hash_file_that_is_not_one_line_is_not_read_whole(self):
        result = self.plan({"system.img": SYSTEM, "userdata.img": USERDATA,
                            "system.img.sha256": hashlib.sha256(SYSTEM).hexdigest().encode() + b"  system.img"
                            + b" " * 2000 + b"\n"})
        self.assertEqual(result["verdict"], install.STOP, result)

    def test_an_image_that_changes_while_it_is_checked_is_refused(self):
        folder = self.folder({"system.img": SYSTEM})
        original = install.read_layout

        def changing(stream, size):
            with (folder / "system.img").open("ab") as image:
                image.write(b"\0")
            return original(stream, size)

        install.read_layout = changing
        self.addCleanup(setattr, install, "read_layout", original)
        with self.assertRaises(ValueError) as raised:
            install.check_image(folder / "system.img", folder / "system.img.sha256")
        self.assertIn("changed", str(raised.exception))


def step(message, answer):
    """One step as fastboot 35.0.1 prints it: the message padded to 50 columns, then its answer."""
    return f"{message:<50} {answer}\n"


FINISHED = "Finished. Total time: 53.004s\n"
COMMAND_FAILED = "fastboot: error: Command failed\n"
SYSTEM_IN_PIECES = "".join(step(f"Sending sparse 'system' {piece}/2 (524284 KB)", "OKAY [ 16.512s]")
                           + step("Writing 'system'", "OKAY [ 10.201s]") for piece in (1, 2)) + FINISHED
USERDATA_WHOLE = step("Sending 'userdata' (10652 KB)", "OKAY [  0.402s]") + step("Writing 'userdata'",
                                                                               "OKAY [  1.020s]") + FINISHED
REFUSED = (step("Sending sparse 'system' 1/2 (524284 KB)", "OKAY [ 16.512s]")
           + step("Writing 'system'", "FAILED (remote: 'Partition flashing is not allowed')") + COMMAND_FAILED)
CUT_OFF = (step("Sending sparse 'system' 1/2 (524284 KB)", "OKAY [ 16.512s]") + step("Writing 'system'", "OKAY [ 10.201s]")
           + step("Sending sparse 'system' 2/2 (524284 KB)", "FAILED (Write to device failed (No such device))")
           + COMMAND_FAILED)
RESTARTED = step("Rebooting", "OKAY [  0.003s]") + "Finished. Total time: 0.054s\n"


class Flash(unittest.TestCase):
    folder = Plan.folder
    unit = Plan.unit
    STAY = "Keep the unit in fastboot"

    def setUp(self):
        # Nothing here may touch the real per-unit journal in the home folder.
        self.default_journal = install.default_journal
        install.SILENCED.clear()
        patcher = unittest.mock.patch.object(install, "default_journal",
                                             side_effect=AssertionError("the home journal was used"))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.running = False
        self.fastboot_running = install.fastboot_running
        running = unittest.mock.patch.object(install, "fastboot_running", side_effect=lambda: self.running)
        running.start()
        self.addCleanup(running.stop)
        self.images = self.folder({"system.img": SYSTEM, "userdata.img": USERDATA})
        self.journal = self.images.parent / f"{self.images.name}-journal.jsonl"
        self.addCleanup(lambda: [path.unlink(missing_ok=True)
                                 for path in self.images.parent.glob(f"{self.images.name}-journal*")])
        self.tools, _ = self.unit()
        self.listing = f"{SERIAL}\t fastboot\n"
        answer = self.tools.runner
        self.tools.runner = lambda arguments: (0, self.listing, "") \
            if arguments[0] == "fastboot" and arguments[1:] == ["devices"] else answer(arguments)
        self.sent, self.folders = [], []
        self.answers = {"system": (0, SYSTEM_IN_PIECES, ""), "userdata": (0, USERDATA_WHOLE, ""),
                        "reboot": (0, RESTARTED, "")}
        self.during_write = {}

    def writer(self, arguments, folder):
        self.sent.append(arguments)
        self.folders.append(folder)
        # fastboot writes from private copies of the checked images, never from the images folder.
        self.assertNotEqual(folder, self.images.resolve())
        self.assertEqual(sorted(entry.name for entry in folder.iterdir()), ["system.img", "userdata.img"])
        self.assertEqual((folder / "system.img").read_bytes(), SYSTEM)
        name = arguments[4] if arguments[3] == "flash" else arguments[3]
        self.during_write.pop(name, lambda: None)()
        return self.answers[name]

    def flash(self, typed=install.CONFIRMATION, interactive=True):
        prompts = []

        def confirm(prompt):
            prompts.append(prompt)
            if isinstance(typed, BaseException):
                raise typed
            return typed() if callable(typed) else typed

        printed = io.StringIO()
        with contextlib.redirect_stdout(printed), contextlib.redirect_stderr(printed):
            code = install.flash(self.tools, self.writer, self.images, SERIAL, self.journal, confirm, interactive)
        return code, printed.getvalue(), prompts

    def entries(self):
        return [json.loads(line) for line in self.journal.read_text(encoding="utf-8").splitlines()] \
            if self.journal.exists() else []

    def fresh_journal(self):
        """A journal of a unit never flashed, so a scenario is judged on its own run."""
        self.journal = self.images.parent / f"{self.images.name}-journal-{len(self.entries())}-{id(object())}.jsonl"
        self.sent.clear()

    def replay(self, text):
        path = self.images.parent / f"{self.images.name}-journal-replay.jsonl"
        path.write_text(text, encoding="utf-8")
        journal = install.Journal(path, SERIAL)
        journal.release()
        return journal.state.partitions, journal.state.restarted, journal.damaged

    def interrupt_when_recording(self, event, partition=None):
        """Ctrl-C right after the journal records ``event``."""
        record = install.Journal.record

        def recording_then_interrupted(journal, name, **fields):
            record(journal, name, **fields)
            if name == event and fields.get("partition") == partition:
                signal.raise_signal(signal.SIGINT)

        patcher = unittest.mock.patch.object(install.Journal, "record", recording_then_interrupted)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_system_then_userdata_then_a_restart_after_the_typed_words(self):
        code, printed, prompts = self.flash()
        self.assertEqual(code, 0, printed)
        self.assertEqual(self.sent, [["fastboot", "-s", SERIAL, "flash", "system", "system.img"],
                                     ["fastboot", "-s", SERIAL, "flash", "userdata", "userdata.img"],
                                     ["fastboot", "-s", SERIAL, "reboot"]])
        self.assertIn(hashlib.sha256(SYSTEM).hexdigest(), prompts[0])
        self.assertIn("thermal", prompts[0])
        events = [entry["event"] for entry in self.entries() if entry["event"] != "read"]
        self.assertEqual(events, ["flash stage", "plan", "confirmed", "write", "answer", "write", "answer", "restart",
                                  "answer"])
        journal = self.journal.read_text(encoding="utf-8")
        self.assertNotIn(SERIAL, journal)
        self.assertIn("Writing 'userdata'", journal)
        reads = [entry["command"][1:] for entry in self.entries() if entry["event"] == "read"]
        self.assertIn(["-s", "<unit>", "getvar", "partition-size:userdata"], reads)
        self.assertIn("<unit> fastboot", journal)
        self.assertFalse(self.folders[0].exists(), "the private copies are removed afterwards")
        self.assertEqual(sorted(entry.name for entry in self.images.iterdir()),
                         ["system.img", "system.img.sha256", "userdata.img", "userdata.img.sha256"])
        self.assertIs(signal.getsignal(signal.SIGINT), signal.default_int_handler)

    def test_nothing_is_written_without_the_exact_words(self):
        for typed in ("", "yes", "write system", "WRITE SYSTEM AND USERDATA", f" {install.CONFIRMATION}",
                      f"{install.CONFIRMATION}  ", EOFError(), KeyboardInterrupt()):
            with self.subTest(typed=typed):
                code, printed, prompts = self.flash(typed)
                self.assertEqual(code, install.EXIT_CODES[install.STOP])
                self.assertEqual(len(prompts), 1)
                self.assertEqual(self.sent, [])
                self.assertEqual(self.entries()[-1]["event"], "not confirmed")
                self.assertNotIn("write", [entry["event"] for entry in self.entries()])

    def test_nothing_is_asked_or_written_unless_the_plan_is_ready(self):
        (self.images / "userdata.img").unlink()
        (self.images / "userdata.img.sha256").unlink()
        code, printed, prompts = self.flash()
        self.assertEqual(code, install.EXIT_CODES[install.STOP])
        self.assertEqual((prompts, self.sent), ([], []))
        self.assertNotIn("flash system", printed)

    def test_without_fastboot_it_stops_and_still_says_how_to_restart(self):
        self.tools.fastboot_path = None
        code, printed, prompts = self.flash()
        self.assertEqual(code, install.EXIT_CODES[install.STOP], printed)
        self.assertEqual((prompts, self.sent), ([], []))

    def test_another_fastboot_unit_stops_it_before_the_question_and_before_each_write(self):
        self.listing = f"{SERIAL}\t fastboot\nother42\t fastboot\n"
        code, printed, prompts = self.flash()
        self.assertEqual(code, install.EXIT_CODES[install.STOP])
        self.assertEqual((prompts, self.sent), ([], []))
        self.assertNotIn("other42", self.journal.read_text(encoding="utf-8"))

        self.fresh_journal()
        self.listing = f"{SERIAL}\t fastboot\n"
        self.during_write["system"] = lambda: setattr(self, "listing", f"{SERIAL}\t fastboot\nother42\t fastboot\n")
        code, printed, _ = self.flash()
        self.assertEqual(code, install.WRITE_FAILED, printed)
        self.assertEqual([arguments[4] for arguments in self.sent], ["system"])

    def test_any_other_row_in_fastboots_list_counts_as_another_device(self):
        for extra in ("no permissions (missing udev rules? user is in the plugdev group); see "
                      "[http://developer.android.com/tools/device.html]\t fastboot",
                      "other42\t Android Bootloader Interface"):
            with self.subTest(extra=extra):
                self.listing = f"{SERIAL}\t fastboot\n{extra}\n"
                code, printed, prompts = self.flash()
                self.assertEqual(code, install.EXIT_CODES[install.STOP], printed)
                self.assertEqual((prompts, self.sent), ([], []))

    def test_a_refused_first_write_leaves_the_unit_unchanged_and_is_not_restarted(self):
        self.answers["system"] = (1, REFUSED, "")
        code, printed, _ = self.flash()
        self.assertEqual(code, install.EXIT_CODES[install.STOP])
        self.assertEqual(len(self.sent), 1)
        self.assertIn("Nothing was written", printed)
        self.assertIn("Partition flashing is not allowed", printed)
        self.assertIn("restart it with", printed)
        self.assertEqual(self.entries()[-1]["outcome"], install.UNCHANGED)

    def test_a_system_write_cut_off_after_a_piece_stays_in_fastboot(self):
        self.answers["system"] = (1, CUT_OFF, "")
        code, printed, _ = self.flash()
        self.assertEqual(code, install.WRITE_FAILED)
        self.assertEqual([arguments[3:5] for arguments in self.sent], [["flash", "system"]])
        self.assertIn(self.STAY, printed)
        self.assertNotIn("restart it with", printed)

    def test_a_failed_userdata_write_is_not_followed_by_a_restart(self):
        for answer in ((1, step("Sending 'userdata' (10652 KB)", "OKAY [  0.402s]")
                        + step("Writing 'userdata'", "FAILED (remote: 'flash write failure')") + COMMAND_FAILED, ""),
                       (1, step("Sending 'userdata' (10652 KB)", "FAILED (remote: 'data too large')")
                        + COMMAND_FAILED, "")):
            with self.subTest(answer=answer[1]):
                self.fresh_journal()
                self.answers["userdata"] = answer
                code, printed, _ = self.flash()
                self.assertEqual(code, install.WRITE_FAILED, printed)
                self.assertEqual([arguments[4] for arguments in self.sent], ["system", "userdata"])
                self.assertIn(self.STAY, printed)

    def test_after_an_unfinished_write_a_later_refusal_does_not_say_restart(self):
        self.answers["system"] = (1, CUT_OFF, "")
        self.assertEqual(self.flash()[0], install.WRITE_FAILED)
        self.answers["system"] = (1, REFUSED, "")
        code, printed, _ = self.flash()
        self.assertEqual(code, install.WRITE_FAILED, printed)
        self.assertIn(self.STAY, printed)
        self.assertNotIn("restart it with", printed)

    def test_a_retry_from_another_folder_or_without_a_terminal_still_knows_the_unfinished_write(self):
        self.answers["system"] = (1, CUT_OFF, "")
        self.assertEqual(self.flash()[0], install.WRITE_FAILED)
        self.images = self.folder({"system.img": SYSTEM, "userdata.img": USERDATA})
        self.answers["system"] = (1, REFUSED, "")
        code, printed, _ = self.flash()
        self.assertEqual(code, install.WRITE_FAILED, printed)
        self.assertNotIn("restart it with", printed)
        (self.images / "userdata.img").unlink()
        (self.images / "userdata.img.sha256").unlink()
        code, printed, _ = self.flash()
        self.assertEqual(code, install.WRITE_FAILED, printed)
        self.assertIn(self.STAY, printed)
        code, printed, prompts = self.flash(interactive=False)
        self.assertEqual(code, install.WRITE_FAILED, printed)
        self.assertEqual(prompts, [])
        self.assertIn("terminal", printed)

    def test_the_userdata_of_an_earlier_installation_does_not_count_for_a_new_one(self):
        self.assertEqual(self.flash()[0], 0)
        self.answers["userdata"] = (1, step("Sending 'userdata' (10652 KB)", "FAILED (remote: 'data too large')")
                                    + COMMAND_FAILED, "")
        code, printed, _ = self.flash()
        self.assertEqual(code, install.WRITE_FAILED, printed)
        self.assertIn("userdata is not", printed)

    def test_a_retry_that_changes_nothing_keeps_what_the_last_installation_did(self):
        self.assertEqual(self.flash()[0], 0)
        self.answers["system"] = (1, REFUSED, "")
        code, printed, _ = self.flash()
        self.assertEqual(code, install.EXIT_CODES[install.STOP], printed)
        self.assertIn("restarted the unit", printed)
        code, printed, _ = self.flash("no")
        self.assertEqual(code, install.EXIT_CODES[install.STOP], printed)
        self.assertIn("restarted the unit", printed)

    def test_the_bytes_written_are_the_bytes_checked(self):
        def change_after_the_check():
            (self.images / "userdata.img").write_bytes(USERDATA + b"\0")
            return install.CONFIRMATION

        code, printed, _ = self.flash(change_after_the_check)
        self.assertEqual(code, install.EXIT_CODES[install.STOP], printed)
        self.assertEqual(self.sent, [])

        (self.images / "userdata.img").write_bytes(USERDATA)
        self.during_write["system"] = lambda: (self.images / "userdata.img").write_bytes(b"replaced")
        code, printed, _ = self.flash()
        self.assertEqual(code, 0, printed)  # the images folder no longer matters: the copies were checked

        (self.images / "userdata.img").write_bytes(USERDATA)

        def change_the_copy():
            copy = self.folders[-1] / "userdata.img"
            copy.chmod(0o600)
            copy.write_bytes(b"replaced")

        self.fresh_journal()
        self.during_write["system"] = change_the_copy
        code, printed, _ = self.flash()
        self.assertEqual(code, install.WRITE_FAILED, printed)
        self.assertEqual([arguments[4] for arguments in self.sent], ["system"])

    def test_a_copy_that_does_not_read_back_as_written_is_never_sent(self):
        chmod = pathlib.Path.chmod

        def corrupting(path, mode, **options):
            if path.name == "system.img" and path.parent != self.images:
                with path.open("r+b") as image:
                    image.write(b"\xff")
            return chmod(path, mode, **options)

        with unittest.mock.patch.object(pathlib.Path, "chmod", corrupting):
            code, printed, prompts = self.flash()
        self.assertEqual(code, install.EXIT_CODES[install.STOP], printed)
        self.assertEqual(self.sent, [])
        self.assertIn("read back", printed)
        self.assertEqual(list(self.images.parent.glob(f".{self.images.name}-flashing-*")), [])

    def test_ctrl_c_lets_the_step_under_way_finish_and_stops_before_the_next(self):
        self.during_write["system"] = lambda: signal.raise_signal(signal.SIGINT)
        code, printed, _ = self.flash()
        self.assertEqual(code, install.WRITE_FAILED, printed)
        self.assertEqual([arguments[4] for arguments in self.sent], ["system"])
        self.assertIn("system is written and userdata is not (Ctrl-C)", printed)

        self.fresh_journal()
        self.answers["system"] = (1, REFUSED, "")
        self.during_write["system"] = lambda: signal.raise_signal(signal.SIGINT)
        code, printed, _ = self.flash()
        self.assertEqual(code, install.EXIT_CODES[install.STOP], printed)  # a whole refusal stays a refusal

        self.fresh_journal()
        self.answers["system"] = (0, SYSTEM_IN_PIECES, "")
        self.during_write["userdata"] = lambda: signal.raise_signal(signal.SIGINT)
        code, printed, _ = self.flash()
        self.assertEqual(code, install.NOT_RESTARTED, printed)
        self.assertEqual([arguments[4] for arguments in self.sent], ["system", "userdata"])
        self.assertIs(signal.getsignal(signal.SIGINT), signal.default_int_handler)

    def test_ctrl_c_once_a_command_is_announced_lets_it_run_and_stops_after_it(self):
        self.interrupt_when_recording("write", "userdata")
        code, printed, _ = self.flash()
        self.assertEqual(code, install.NOT_RESTARTED, printed)
        self.assertEqual([arguments[3:5] for arguments in self.sent], [["flash", "system"], ["flash", "userdata"]])

    def test_fastboot_never_sees_ctrl_c(self):
        popen = unittest.mock.MagicMock()
        popen.return_value.stdout.read1.return_value = b""
        popen.return_value.wait.return_value = 0
        with unittest.mock.patch.object(install.subprocess, "Popen", popen):
            install.stream_tool(["fastboot", "devices"], self.images)
        options = popen.call_args.kwargs
        if os.name == "nt":
            self.assertTrue(options["creationflags"] & subprocess.CREATE_NEW_PROCESS_GROUP)
        else:
            self.assertTrue(options["start_new_session"])

    def test_whatever_goes_wrong_the_advice_follows_what_is_known(self):
        def fail():
            raise OSError("pipe broke")

        cases = [("system", install.WRITE_FAILED), ("userdata", install.WRITE_FAILED),
                 ("reboot", install.NOT_RESTARTED)]
        for broken, expected in cases:
            with self.subTest(broken=broken):
                self.fresh_journal()
                self.during_write = {broken: fail}
                code, printed, _ = self.flash()
                self.assertEqual(code, expected, printed)
                self.assertNotIn("Nothing was written", printed)

    def test_a_failed_restart_says_both_partitions_are_written(self):
        self.answers["reboot"] = (1, step("Rebooting", "FAILED (Write to device failed)") + COMMAND_FAILED, "")
        code, printed, _ = self.flash()
        self.assertEqual(code, install.NOT_RESTARTED)
        self.assertIn("written", printed)

    def test_an_acknowledged_restart_is_checked_before_any_power_action(self):
        self.answers["reboot"] = (1, step("Rebooting", "OKAY [  0.003s]") + "interrupted\n", "")
        code, printed, _ = self.flash()
        self.assertEqual(code, install.NOT_RESTARTED, printed)
        self.assertIn("accepted the restart", printed)
        self.assertIn("adb devices", printed)
        self.assertEqual(install.restart_outcome(*self.answers["reboot"][:2]), install.ACKNOWLEDGED)

    def test_a_unit_gone_from_fastboot_is_not_called_restarted(self):
        self.during_write["userdata"] = lambda: setattr(self, "listing", "")
        code, printed, _ = self.flash()
        self.assertEqual(code, install.NOT_RESTARTED, printed)
        self.assertEqual([arguments[3] for arguments in self.sent], ["flash", "flash"])
        self.assertIn("Check the cable", printed)
        self.assertNotIn("is already starting", printed)

    def test_another_run_on_the_same_unit_is_refused(self):
        held = install.Journal(self.journal, SERIAL)
        try:
            code, printed, prompts = self.flash()
        finally:
            held.release()
        self.assertEqual(code, install.UNIT_HELD, printed)
        self.assertEqual((prompts, self.sent), ([], []))
        self.assertEqual(self.flash()[0], 0)

    def test_the_journal_never_goes_in_the_images_folder_or_on_an_input(self):
        kept = self.journal
        os.link(self.images / "system.img.sha256", kept)
        before = (self.images / "system.img.sha256").read_bytes()
        for journal in (self.images / "journal.jsonl", self.images, kept):
            with self.subTest(journal=journal):
                self.journal = journal
                code, printed, prompts = self.flash()
                self.assertEqual(code, install.UNIT_HELD)
                self.assertEqual((prompts, self.sent), ([], []))
                self.assertFalse((self.images / "journal.jsonl").exists())
        self.assertEqual((self.images / "system.img.sha256").read_bytes(), before)
        self.journal = kept

    def test_a_journal_cut_off_mid_line_still_shows_the_unfinished_write(self):
        self.journal.write_text(json.dumps({"event": "write", "partition": "system"}) + '\n{"event": "ans',
                                encoding="utf-8")
        self.answers["system"] = (1, REFUSED, "")
        code, printed, _ = self.flash()
        self.assertEqual(code, install.WRITE_FAILED, printed)
        self.assertIn("could not be read", printed)
        lines = self.journal.read_text(encoding="utf-8").splitlines()
        self.assertEqual(lines[1], '{"event": "ans')
        self.assertTrue(all(json.loads(line) for line in lines[2:]))

    def test_what_earlier_runs_left_is_read_from_the_journal(self):
        def lines(*events):
            return "\n".join(json.dumps(event) for event in events) + "\n"

        write = {"event": "write", "partition": "system"}
        written = [write, {"event": "answer", "partition": "system", "outcome": "written"},
                   {"event": "write", "partition": "userdata"},
                   {"event": "answer", "partition": "userdata", "outcome": "written"}]
        restarted = [{"event": "restart"}, {"event": "answer", "outcome": "restarted"}]
        cases = [
            (lines(), {"system": None, "userdata": None}, False),
            (lines(write), {"system": install.UNKNOWN, "userdata": None}, False),
            (lines(write, {"event": "answer", "partition": "system", "outcome": "unchanged"}),
             {"system": None, "userdata": None}, False),
            (lines(write, {"event": "answer", "partition": "system", "outcome": "unknown"}, write,
                   {"event": "answer", "partition": "system", "outcome": "unchanged"}),
             {"system": install.UNKNOWN, "userdata": None}, False),
            (lines(*written, *restarted), {"system": install.WRITTEN, "userdata": install.WRITTEN}, True),
            (lines(*written, *restarted, write, {"event": "answer", "partition": "system", "outcome": "unchanged"}),
             {"system": install.WRITTEN, "userdata": install.WRITTEN}, True),
            (lines(*written, *restarted, write, {"event": "answer", "partition": "system", "outcome": "written"}),
             {"system": install.WRITTEN, "userdata": None}, False),
            (lines(*written, {"event": "restart"}, {"event": "answer", "outcome": "acknowledged"}),
             {"system": install.WRITTEN, "userdata": install.WRITTEN}, False),
        ]
        for text, states, was_restarted in cases:
            with self.subTest(text=text):
                self.assertEqual(self.replay(text), (states, was_restarted, 0))
        self.assertEqual(self.replay("[1, 2]\n" + lines(write))[1:], (False, 1))
        # Damage voids what came before it, and a later complete installation supersedes it.
        self.assertEqual(self.replay(lines(*written) + "torn\n")[0],
                         {"system": install.UNKNOWN, "userdata": None})
        self.assertEqual(self.replay(lines(*written, *restarted) + "torn\n")[:2],
                         ({"system": install.UNKNOWN, "userdata": None}, False))
        self.assertEqual(self.replay("torn\n" + lines(*written, *restarted)),
                         ({"system": install.WRITTEN, "userdata": install.WRITTEN}, True, 1))

    def test_only_a_lister_that_ran_and_found_none_says_no_fastboot_is_running(self):
        def run(result):
            def fake(command, **options):
                if isinstance(result, BaseException):
                    raise result
                return subprocess.CompletedProcess(command, result[0], result[1], "")
            return unittest.mock.patch.object(install.subprocess, "run", fake)

        cases = [(True, FileNotFoundError("pgrep"), None), (True, subprocess.TimeoutExpired("pgrep", 60), None),
                 (True, (2, ""), None), (True, (1, ""), False), (True, (0, "4242\n"), True),
                 (False, OSError("tasklist"), None), (False, (1, ""), None),
                 (False, (0, "INFO: No tasks are running which match the specified criteria.\n"), False),
                 (False, (0, '"fastboot.exe","4242","Console","1","9,000 K"\n'), True)]
        for posix, result, expected in cases:
            with self.subTest(posix=posix, result=result), run(result):
                self.assertIs(self.fastboot_running(posix), expected)

    def test_the_default_journal_is_one_per_unit_and_does_not_show_its_serial(self):
        path = self.default_journal(SERIAL)
        self.assertNotIn(SERIAL, str(path))
        self.assertEqual(path, self.default_journal(SERIAL))
        self.assertNotEqual(path, self.default_journal("other42"))

    def test_without_a_terminal_the_stage_never_asks(self):
        flash = unittest.mock.MagicMock(return_value=3)
        arguments = argparse.Namespace(images=str(self.images), serial=SERIAL)
        with unittest.mock.patch.object(install, "flash", flash), \
                unittest.mock.patch.object(install, "default_journal", return_value=self.journal), \
                unittest.mock.patch("sys.stdin", io.StringIO("")):
            self.assertEqual(install.command_flash(arguments), 3)
        self.assertFalse(flash.call_args.kwargs["interactive"])

    def test_the_advice_is_given_even_when_the_window_cannot_show_it(self):
        self.answers["system"] = (1, CUT_OFF, "")

        class Closing(io.StringIO):
            closed_now = False

            def write(self, text):
                if self.closed_now:
                    raise BrokenPipeError("closed")
                return super().write(text)

        window, fallback = Closing(), io.StringIO()
        self.during_write["system"] = lambda: setattr(window, "closed_now", True)
        with contextlib.redirect_stdout(window), contextlib.redirect_stderr(fallback):
            code = install.flash(self.tools, self.writer, self.images, SERIAL, self.journal,
                                 lambda prompt: install.CONFIRMATION)
        self.assertEqual(code, install.WRITE_FAILED)
        self.assertIn(self.STAY, fallback.getvalue())

    def test_a_closed_output_pipe_does_not_change_the_exit_code(self):
        script = ("import sys, time; sys.path.insert(0, sys.argv[1]); import install; time.sleep(0.5); "
                  "install.say('x' * 100000); install.say('advice'); sys.exit(6)")
        child = subprocess.Popen([sys.executable, "-c", script, str(TOOLS)], stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE)
        child.stdout.close()
        error = child.stderr.read()
        child.stderr.close()
        self.assertEqual(child.wait(timeout=60), 6, error)
        self.assertIn(b"advice", error)

    def test_a_journal_that_cannot_record_the_last_answer_is_not_a_clean_success(self):
        fsync = os.fsync

        def failing(descriptor):
            if self.entries() and self.entries()[-1].get("outcome") == install.RESTARTED:
                raise OSError("disk full")
            return fsync(descriptor)

        with unittest.mock.patch.object(install.os, "fsync", failing):
            code, printed, _ = self.flash()
        self.assertEqual(code, install.JOURNAL_INCOMPLETE, printed)
        self.assertIn("could not be completed", printed)

    def test_what_each_flash_did_is_read_from_fastboots_own_words(self):
        whole = step("Sending 'system' (4 KB)", "OKAY [  0.001s]") + step("Writing 'system'", "OKAY [  0.002s]")
        with_info = (step("Sending 'system' (4 KB)", "OKAY [  0.001s]")
                     + f"{'Writing ' + repr('system'):<50} (bootloader) Erasing...\nOKAY [  0.002s]\n" + FINISHED)
        cases = [
            ((0, SYSTEM_IN_PIECES), install.WRITTEN),
            ((0, SYSTEM_IN_PIECES.replace("\n", "\r\n")), install.WRITTEN),
            ((0, whole + FINISHED), install.WRITTEN),
            ((0, with_info), install.WRITTEN),
            ((0, "(bootloader) Previous update: FAILED\n" + whole + FINISHED), install.WRITTEN),
            ((0, whole), install.UNKNOWN),  # no Finished
            ((0, SYSTEM_IN_PIECES.replace("2/2", "3/3")), install.UNKNOWN),
            ((0, step("Sending 'userdata' (4 KB)", "OKAY [  0.001s]") + step("Writing 'userdata'", "OKAY [  0.002s]")
              + FINISHED), install.UNKNOWN),
            ((0, step("Sending 'system' (4 KB)", "OKAY [  0.001s]") + FINISHED), install.UNKNOWN),
            ((0, "OKAY [  0.001s]\n" + whole + FINISHED), install.UNKNOWN),
            ((1, "fastboot: error: cannot load 'system.img': No such file or directory\n"), install.UNCHANGED),
            ((1, step("Sending sparse 'system' 1/2 (524284 KB)", "FAILED (remote: 'data too large')")
              + COMMAND_FAILED), install.UNCHANGED),
            ((1, REFUSED), install.UNCHANGED),
            ((1, REFUSED.replace("not allowed", "failed")), install.UNKNOWN),
            ((1, REFUSED.replace("Partition flashing is not allowed", "write failed; retry not allowed")),
             install.UNKNOWN),
            ((1, REFUSED.replace("Partition flashing", "Critical partition flashing")), install.UNCHANGED),
            ((1, REFUSED.replace("remote: 'Partition flashing is not allowed'",
                                 "Device sent unknown status code: not allowed")), install.UNKNOWN),
            ((1, CUT_OFF), install.UNKNOWN),
            ((1, step("Sending sparse 'system' 1/2 (524284 KB)", "OKAY [ 16.512s]")
              + step("Writing 'system'", "OKAY [  1.000s]")
              + step("Sending sparse 'system' 2/2 (524284 KB)", "OKAY [ 16.512s]")
              + step("Writing 'system'", "FAILED (remote: 'Partition flashing is not allowed')") + COMMAND_FAILED),
             install.UNKNOWN),
            ((130, step("Sending sparse 'system' 1/2 (524284 KB)", "OKAY [ 16.512s]") + "Writing 'system' "),
             install.UNKNOWN),
        ]
        for (code, output), expected in cases:
            with self.subTest(output=output):
                self.assertEqual(install.classify_write("system", code, output)[0], expected)
        self.assertEqual(install.restart_outcome(0, RESTARTED), install.RESTARTED)
        self.assertEqual(install.restart_outcome(0, "(bootloader) last boot: FAILED\n" + RESTARTED), install.RESTARTED)
        self.assertEqual(install.restart_outcome(0, step("Rebooting", "OKAY [  0.003s]")), install.ACKNOWLEDGED)
        self.assertEqual(install.restart_outcome(0, "Finished. Total time: 0.001s\n"), install.NOT_RESTARTED_YET)
        self.assertEqual(install.restart_outcome(1, step("Rebooting", "FAILED (Write to device failed)")
                                                 + COMMAND_FAILED), install.NOT_RESTARTED_YET)

    def test_fastboot_runs_from_the_given_folder_and_what_it_prints_is_shown_and_kept(self):
        script = ("import os, sys, time; sys.stderr.write('Writing ' + repr(os.getcwd()) + ' '); sys.stderr.flush(); "
                  "time.sleep(0.2); sys.stderr.write('OKAY [  0.200s]\\n'); sys.exit(3)")
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed):
            code, output, note = install.stream_tool([sys.executable, "-c", script], self.images)
        self.assertEqual(code, 3)
        self.assertIn(repr(str(self.images)), output)
        self.assertTrue(output.endswith("OKAY [  0.200s]\n") or output.endswith("OKAY [  0.200s]\r\n"), output)
        self.assertEqual(printed.getvalue(), output)

    def test_fastboot_is_waited_for_even_when_its_words_cannot_be_shown(self):
        script = "import sys, time; sys.stderr.write('Sending '); sys.stderr.flush(); time.sleep(0.5); sys.exit(4)"

        class Closed(io.StringIO):
            def write(self, text):
                raise BrokenPipeError("closed")

        with contextlib.redirect_stdout(Closed()):
            code, output, _ = install.stream_tool([sys.executable, "-c", script], self.images)
        self.assertEqual((code, output), (4, "Sending "))

    def test_hiding_the_serial_never_changes_what_the_replay_reads(self):
        for serial in ("e", "system", "unknown"):
            with self.subTest(serial=serial):
                path = self.images.parent / f"{self.images.name}-journal-serial-{serial}.jsonl"
                journal = install.Journal(path, serial)
                journal.record("write", partition="system", command=["fastboot", "-s", serial, "flash", "system"])
                journal.record("answer", partition="system", outcome=install.UNKNOWN, output=f"{serial} failed")
                journal.release()
                self.assertEqual(journal.state.partitions["system"], install.UNKNOWN)
                self.assertEqual(self.replay(path.read_text(encoding="utf-8"))[0]["system"], install.UNKNOWN)
                self.assertNotIn(f'"-s", "{serial}"', path.read_text(encoding="utf-8"))

    def test_an_answer_whose_write_line_was_lost_still_counts(self):
        unknown = json.dumps({"event": "answer", "partition": "system", "outcome": "unknown"})
        self.assertEqual(self.replay('{"event": "wri\n' + unknown + "\n")[0]["system"], install.UNKNOWN)
        self.journal.write_text('{"event": "wri\n' + unknown + "\n", encoding="utf-8")
        self.answers["system"] = (1, REFUSED, "")
        code, printed, _ = self.flash()
        self.assertEqual(code, install.WRITE_FAILED, printed)

    def test_a_damaged_journal_never_calls_the_unit_unchanged(self):
        self.journal.write_text("not a journal line\n", encoding="utf-8")
        self.answers["system"] = (1, REFUSED, "")
        code, printed, _ = self.flash()
        self.assertEqual(code, install.WRITE_FAILED, printed)
        self.assertIn("could not be read", printed)
        self.assertIn("system may hold part of an image", printed)
        self.assertNotIn("restart it with", printed)
        self.assertNotIn("move it aside", printed)

    def test_another_fastboot_program_keeps_the_stage_from_starting_or_going_on(self):
        for running in (True, None):
            with self.subTest(running=running):
                self.fresh_journal()
                self.running = running
                code, printed, prompts = self.flash()
                self.assertEqual(code, install.EXIT_CODES[install.STOP], printed)
                self.assertEqual((prompts, self.sent), ([], []))
                self.assertIn("fastboot program", printed)
        self.fresh_journal()
        self.running = False
        self.during_write["system"] = lambda: setattr(self, "running", True)
        code, printed, _ = self.flash()
        self.assertEqual(code, install.WRITE_FAILED, printed)
        self.assertEqual([arguments[4] for arguments in self.sent], ["system"])

    def test_ctrl_c_during_the_checks_before_a_step_keeps_it_from_being_sent(self):
        answer = self.tools.runner
        armed = []

        def runner(arguments):
            if armed and arguments[1:] == ["devices"]:
                armed.clear()
                signal.raise_signal(signal.SIGINT)
            return answer(arguments)

        self.tools.runner = runner
        code, printed, _ = self.flash(lambda: armed.append(True) or install.CONFIRMATION)
        self.assertEqual(code, install.EXIT_CODES[install.STOP], printed)
        self.assertEqual(self.sent, [])

    def test_a_new_journal_is_synced_into_its_folder(self):
        folder = self.images.parent / f"{self.images.name}-journal-new"
        self.addCleanup(shutil.rmtree, folder, True)
        with unittest.mock.patch.object(install, "sync_folder") as sync:
            install.Journal(folder / "unit.jsonl", SERIAL).release()
        self.assertEqual([call.args[0] for call in sync.call_args_list], [folder, folder.parent])
        with unittest.mock.patch.object(install, "sync_folder") as sync:
            install.Journal(folder / "unit.jsonl", SERIAL).release()
        self.assertEqual(sync.call_args_list, [])

    def test_under_sudo_the_journal_is_the_invoking_persons(self):
        owner = unittest.mock.Mock(pw_dir="/home/owner")
        self.assertEqual(install.invoking_home({"SUDO_USER": "owner"}, 0, lambda name: owner),
                         pathlib.Path("/home/owner"))
        self.assertEqual(install.invoking_home({"SUDO_USER": "owner"}, 1000, lambda name: owner), pathlib.Path.home())
        self.assertEqual(install.invoking_home({}, 0, lambda name: owner), pathlib.Path.home())


class Verify(unittest.TestCase):
    """The unit is a stand-in that answers each pass as the API 23 emulator's toybox, toolbox and mksh do
    through `adb exec-out`."""

    def setUp(self):
        image, _ = test_ext4image.sample()
        self.use_image(image.image())
        self.denied, self.unopened, self.unhashed = set(), set(), set()
        self.reasons: dict[str, str] = {}
        self.read_failures: set[str] = set()
        self.statuses: dict[str, int] = {}
        self.kernel_history = ["[    1.02] EXT4-fs (vda): mounted filesystem with ordered data mode. Opts: (null)"]
        self.kernel_new: list[str] = []
        self.kernel_status = 0
        self.drop_errors: set[str] = set()
        self.per_reading: dict[int, dict] = {}
        self.state, self.readings, self.calls = "device", 0, []
        self.journal_path = self.folder / "journal.jsonl"
        patcher = unittest.mock.patch.object(install, "default_journal", return_value=self.journal_path)
        patcher.start()
        self.addCleanup(patcher.stop)

    def use_image(self, raw: bytes) -> None:
        if not hasattr(self, "folder"):
            self.folder = pathlib.Path(tempfile.mkdtemp())
            self.addCleanup(shutil.rmtree, self.folder)
        (self.folder / "system.img").write_bytes(raw)
        self.image_hash = hashlib.sha256(raw).hexdigest()
        (self.folder / "system.img.sha256").write_text(f"{self.image_hash}  system.img\n")
        self.expected = test_ext4image.entries(raw)
        self.unit = {entry["path"]: dict(entry) for entry in self.expected}

    def visible(self, path: str) -> bool:
        ancestors = [path[:index] for index in range(1, len(path)) if path[index] == "/"]
        return path not in self.denied and not any(a in self.denied or a in self.unopened for a in ancestors)

    def answer(self, name: str) -> list[str]:
        if name == "names":
            self.readings += 1
        unit = {path: {**entry, **self.per_reading.get(self.readings, {}).get(path, {})}
                for path, entry in self.unit.items()}
        shown = sorted(path for path in unit if self.visible(path))
        if name == "names":
            return shown
        if name == "errors":
            return [f"find: {path}: {self.reasons.get(path, 'Permission denied')}" for path in sorted(self.denied)
                    if path in unit and self.visible(path.rpartition("/")[0]) and path not in self.drop_errors] + \
                [f"find: No {path}: {self.reasons.get(path, 'Permission denied')}" for path in sorted(self.unopened)]
        if name == "listing":
            lines = []
            for folder in shown:
                if stat.S_ISDIR(unit[folder]["mode"]):
                    lines.append(f"@@ {folder}")
                    if folder in self.unopened:
                        lines.append(f"opendir failed, {self.reasons.get(folder, 'Permission denied')}")
                    else:
                        lines += sorted(path.rpartition("/")[2] for path in unit if path.rpartition("/")[0] == folder)
            return lines
        if name == "stat":
            return [f"{e['inode']} {e['mode']:x} {e['uid']} {e['gid']} {e['links']} {e['size']} "
                    f"{e['mtime'] % (1 << 64)} {path}" for path, e in ((path, unit[path]) for path in shown)]
        if name == "hashes":
            return [f"{unit[path]['sha1']}  {path}" for path in shown
                    if stat.S_ISREG(unit[path]["mode"]) and path not in self.unhashed]
        if name == "kernel":
            return ["fd00h", "major minor  #blocks  name", "", " 253        0    1572864 vda", "@@",
                    *self.kernel_history, *self.kernel_new]

        def line(path: str, name: str) -> str:
            entry = unit[path]
            target = entry.get("target")
            if target is not None and len(target) > 255:
                target = target[:252] + "..."
            return (f"{stat.filemode(entry['mode'])} root     root              {entry['label']} {name}"
                    + (f" -> {target}" if target is not None else ""))

        lines = ["@@ /", line(install.SYSTEM_MOUNT, "system")]
        for folder in shown:
            if stat.S_ISDIR(unit[folder]["mode"]) and folder not in self.unopened:
                lines.append(f"@@ {folder}")
                lines += [line(path, path.rpartition("/")[2]) for path in shown
                          if path.rpartition("/")[0] == folder]
        return lines

    def status(self, name: str) -> int:
        if name == "kernel":
            return self.kernel_status
        return self.statuses.get(name, 1 if self.denied or self.unopened else 0)

    def runner(self, arguments):
        self.calls.append(arguments)
        tool, *rest = arguments
        if rest == ["devices"]:
            if tool == "fastboot":
                return 0, f"{SERIAL}\tfastboot\n" if self.state == "fastboot" else "", ""
            listed = f"{SERIAL}\t{self.state}\n" if self.state in ("device", "unauthorized") else ""
            return 0, "List of devices attached\n" + listed, ""
        self.assertEqual(rest[:3], ["-s", SERIAL, "exec-out"])
        command, end = rest[3], install.VERIFY_MARKER
        if command == "getprop ro.build.fingerprint":
            return 0, "test/silvering:6.0.1/MMB29M/1:userdebug/dev-keys\n", ""
        if command == install.KERNEL_BEFORE:
            return 0, "".join(f"{line}\n" for line in self.kernel_history) + f"{end}{self.kernel_status}\n", ""
        if command.startswith("for f in "):
            paths = shlex.split(command[len("for f in "):command.index("; do ")])
            lines = []
            for path in paths:
                if "sha1sum \"$f\" 2>&1" in command:
                    lines.append(f"sha1sum: {path}: {self.reasons.get(path, 'Permission denied')}")
                else:
                    # toybox cat stops on a read error without a word; sha1sum hashes what it got.
                    failed = path in self.read_failures
                    digest = {**self.unit[path], **self.per_reading.get(self.readings, {}).get(path, {})}["sha1"]
                    lines += [f"@@ {path}", f"{'d' * 40 if failed else digest}  -", f"## {1 if failed else 0}"]
            return 0, "".join(f"{line}\n" for line in lines) + f"{end}0\n", ""
        for name, text in install.VERIFY_PASSES.items():
            if command == text:
                if name == "names":
                    body = "".join(f"{path}\0" for path in self.answer(name))
                else:
                    body = "".join(f"{line}\n" for line in self.answer(name))
                return 0, f"{body}{end}{self.status(name)}\n", ""
        self.fail(f"not a read this stage makes: {command}")

    def verify(self):
        return install.verify(identify.Tools("adb", "fastboot", self.runner), self.folder, None)

    def passes(self) -> int:
        return sum(1 for arguments in self.calls if arguments[1:2] == ["-s"] and "getprop" not in arguments[-1])

    def write_journal(self, *attempts) -> None:
        lines = []
        for attempt in attempts:
            lines.append({"event": "flash stage"})
            if attempt is not None:
                lines.append({"event": "plan", "images": {name: {"sha256": digest} for name, digest in attempt.items()}})
            for partition in ("system", "userdata"):
                lines += [{"event": "write", "partition": partition},
                          {"event": "answer", "partition": partition, "outcome": install.WRITTEN}]
        self.journal_path.write_text("".join(json.dumps(line) + "\n" for line in lines))

    def test_every_entry_as_written_is_verified_apart_from_what_no_tool_reads(self):
        result, code = self.verify()
        self.assertEqual((result["verdict"], code), (install.REVIEW, 5), result)
        self.assertEqual(result["differences"], [])
        # The only entry not read in full carries a file capability, which no tool on the unit prints, and
        # so, for every entry, attributes stay a limit of the check.
        self.assertEqual(result["unread"], [{"path": "/system/other", "label": test_ext4image.SYSTEM_LABEL,
                                             "size": 6, "reason": "security.capability not read"}])
        self.assertEqual(result["limits"], [install.ATTRIBUTES_LIMIT])
        self.assertEqual(result["fullyChecked"], len(self.expected) - 1)
        self.assertEqual(result["hashedBytes"], result["fileBytes"])
        self.assertEqual(result["fingerprint"], result["unitFingerprint"])
        self.assertEqual(self.passes(), len(install.VERIFY_PASSES) + 1)
        self.assertNotIn(SERIAL, json.dumps(result))
        self.assertNotIn("evidence", result)
        seen = install.read_system_over_adb(identify.Tools("adb", "fastboot", self.runner), SERIAL, {})
        plain = [{key: value for key, value in entry.items() if key != "attributes"} for entry in self.expected]
        # A limit alone keeps the verdict at review; with nothing left unread anywhere, verified.
        self.assertEqual(install.compare_system(plain, seen)["verdict"], install.REVIEW)
        seen["limits"] = []
        self.assertEqual(install.compare_system(plain, seen)["verdict"], install.VERIFIED)

    def test_entries_the_shell_may_not_read_are_listed_for_review(self):
        self.denied, self.unopened = {"/system/build.prop"}, {"/system/bin", "/system/lost+found"}
        self.unhashed = {"/system/holey"}
        result, code = self.verify()
        self.assertEqual((result["verdict"], code), (install.REVIEW, 5), result)
        self.assertEqual(result["differences"], [])
        reasons = {item["path"]: item["reason"] for item in result["unread"]}
        self.assertEqual(reasons, {"/system/build.prop": "find: Permission denied",
                                   "/system/bin": "its listing (Permission denied) not read",
                                   "/system/bin/ls": "in a folder adb's shell may not open",
                                   "/system/bin/sh": "in a folder adb's shell may not open",
                                   # An empty folder still counts as unread when it cannot be listed.
                                   "/system/lost+found": "its listing (Permission denied) not read",
                                   "/system/holey": "content (Permission denied) not read",
                                   "/system/other": "security.capability not read"})
        self.assertLess(result["hashedBytes"], result["fileBytes"])

    def test_a_folder_the_shell_may_not_lstat_hides_its_entries_without_losing_them(self):
        self.denied = {"/system/bin"}
        result, code = self.verify()
        self.assertEqual((result["differences"], code), ([], 5), result)
        reasons = {item["path"]: item["reason"] for item in result["unread"]}
        self.assertEqual({path: reasons[path] for path in ("/system/bin", "/system/bin/sh", "/system/bin/ls")},
                         {"/system/bin": "find: Permission denied",
                          "/system/bin/sh": "in a folder adb's shell may not open",
                          "/system/bin/ls": "in a folder adb's shell may not open"})

    def test_a_read_that_fails_because_the_file_system_is_damaged_is_a_difference(self):
        self.unopened, self.unhashed = {"/system/bin"}, {"/system/build.prop"}
        self.reasons = {"/system/bin": "Structure needs cleaning", "/system/build.prop": "I/O error"}
        result, code = self.verify()
        self.assertEqual((result["verdict"], code), (install.STOP, 3), result)
        self.assertEqual({item["path"]: item["what"] for item in result["differences"]},
                         {"/system/bin": "find could not open it: Structure needs cleaning",
                          "/system/build.prop": "sha1sum could not read it: I/O error"})
        self.assertEqual({item["path"]: item["reason"] for item in result["unread"]
                          if item["path"].startswith("/system/bin/")},
                         {"/system/bin/ls": "in a folder find could not read (Structure needs cleaning)",
                          "/system/bin/sh": "in a folder find could not read (Structure needs cleaning)"})
        # A file whose sha1 differs is read to its end again through cat: when cat stops on a read error, the
        # difference is a read that stops, and alone it does not call for a rewrite.
        self.unopened, self.unhashed, self.reasons = set(), set(), {}
        self.unit["/system/holey"]["sha1"] = "c" * 40
        self.read_failures = {"/system/holey"}
        self.write_journal({"system": self.image_hash, "userdata": "1" * 64})
        (self.folder / "userdata.img.sha256").write_text(f"{'1' * 64}  userdata.img\n")
        result, code = self.verify()
        self.assertEqual(code, 3, result)
        self.assertEqual(result["differences"], [{"path": "/system/holey", "what": "reading it stops with a read "
                                                  "error toybox cat does not name"}])
        self.assertIn("storage", " ".join(result["advice"]))
        self.assertNotIn("reboot bootloader", " ".join(result["advice"]))
        # bionic's EBADMSG names damage too.
        self.read_failures, self.unhashed = set(), {"/system/build.prop"}
        self.unit["/system/holey"]["sha1"] = self.expected[5]["sha1"]
        self.reasons = {"/system/build.prop": "Not a data message"}
        result, code = self.verify()
        self.assertEqual(code, 3, result)

    def test_a_failure_that_says_nothing_about_the_partition_concludes_nothing(self):
        for setting in ({"unhashed": {"/system/build.prop"}, "reasons": {"/system/build.prop": "Too many open files"}},
                        {"denied": {"/system/holey"}, "reasons": {"/system/holey": "Out of memory"}}):
            with self.subTest(setting):
                self.denied, self.unhashed = setting.get("denied", set()), setting.get("unhashed", set())
                self.reasons = setting.get("reasons", {})
                result, code = self.verify()
                self.assertEqual(code, 1, result)
                self.assertIn("says nothing about the partition", result["error"])
        # A file that gives another sha1 when read again in full concludes nothing either.
        self.denied, self.unhashed, self.reasons = set(), set(), {}
        self.unit["/system/holey"]["sha1"] = "c" * 40
        original = self.runner
        self.runner = lambda arguments: (lambda answer: (answer[0], answer[1].replace("c" * 40 + "  -", "e" * 40 + "  -"),
                                                         answer[2]))(original(arguments))
        result, code = self.verify()
        self.runner = original
        self.assertEqual(code, 1, result)
        self.assertIn("two different sha1s", result["error"])

    def test_a_difference_seen_in_two_readings_stops(self):
        self.unit["/system/build.prop"]["sha1"] = "f" * 40
        self.unit["/system/bin/sh"]["label"] = "u:object_r:system_file:s0"
        self.unit["/system/bin/ls"]["target"] = "toybox"
        self.unit["/system/lost+found"]["mode"] = 0o40755
        self.unit["/system/extra"] = {**self.unit["/system/build.prop"], "path": "/system/extra"}
        del self.unit["/system/holey"]
        result, code = self.verify()
        self.assertEqual((result["verdict"], code), (install.STOP, 3), result)
        self.assertEqual(sorted(item["path"] for item in result["differences"]),
                         ["/system/bin/ls", "/system/bin/sh", "/system/build.prop", "/system/extra", "/system/holey",
                          "/system/lost+found"])
        self.assertIn("the file read to its end", str(result["differences"]))
        self.assertIn("missing on the unit", str(result["differences"]))
        # Every pass and the kernel log before them, and the file whose sha1 differed read once more, twice.
        self.assertEqual(self.passes(), 2 * (len(install.VERIFY_PASSES) + 2))
        # No record that this folder was written to the unit: never advise another write.
        self.assertIn("Write nothing yet", " ".join(result["advice"]))
        self.assertEqual(result["journal"], {"installation": None, "state": "no journal"})

    def test_the_kernel_log_shows_read_errors_that_tools_take_for_the_end(self):
        fault = "EXT4-fs error (device vda): ext4_readdir:211: inode #11: comm {}: directory contains a hole at offset 0"
        self.kernel_new = [f"[   40.1] {fault.format('ls')}",
                           "[   41.0] EXT4-fs error (device vdb): ext4_lookup: deleted inode referenced"]
        self.kernel_history.append("[    2.0] booted")
        # The same fault met again by the second reading, at another time and by another program, is the same.
        original_answer = self.answer
        self.answer = lambda name: original_answer(name) + (
            [f"[   90.3] {fault.format('find')}"] if name == "kernel" and self.readings == 2 else [])
        result, code = self.verify()
        self.answer = original_answer
        self.assertEqual(code, 3, result)
        self.assertEqual([item["path"] for item in result["differences"]], ["/system"])
        self.assertIn("ext4_readdir", result["differences"][0]["what"])
        self.assertNotIn("vdb", result["differences"][0]["what"])
        # An error logged before the check began is shown for review, not taken for a difference.
        self.kernel_history, self.kernel_new = [f"[    5.0] {fault.format('init')}", "[    6.0] booted"], []
        result, code = self.verify()
        self.assertEqual((result["differences"], code), ([], 5), result)
        self.assertEqual(result["kernelHistory"], [f"[    5.0] {fault.format('init')}"])
        # A log that turned over during the walks, or cannot be read, is a limit.
        original_answer = self.answer
        self.answer = lambda name: original_answer(name)[:5] + ["[   99.0] later"] if name == "kernel" \
            else original_answer(name)
        result, code = self.verify()
        self.answer = original_answer
        self.assertEqual(code, 5, result)
        self.assertIn("turned over", result["limits"][1])
        self.kernel_history, self.kernel_status = ["dmesg: klogctl: Operation not permitted"], 1
        result, code = self.verify()
        self.assertEqual(code, 5, result)
        self.assertEqual(len(result["limits"]), 2)
        self.assertIn("kernel log", result["limits"][1])

    def test_a_stop_advises_another_write_only_for_both_images_the_journal_binds(self):
        self.unit["/system/build.prop"]["sha1"] = "f" * 40
        userdata = "1" * 64
        (self.folder / "userdata.img.sha256").write_text(f"{userdata}  userdata.img\n")
        this = {"system": self.image_hash, "userdata": userdata}
        for attempts, advice in (
                ([this], "run `install.py flash` again"),
                ([{**this, "userdata": "2" * 64}], "another userdata.img"),
                ([{**this, "system": "0" * 64}], "another system.img"),
                # A later attempt without a readable plan is not credited to the earlier one.
                ([this, None], "cannot be fully read")):
            with self.subTest(advice):
                self.write_journal(*attempts)
                result, code = self.verify()
                self.assertEqual(code, 3)
                self.assertIn(advice, " ".join(result["advice"]))
                self.assertEqual("-s <serial>" in " ".join(result["advice"]), attempts == [this])
        # Answers must match the write they answer: a later write of system that is answered for userdata,
        # or answers with no write before them, make the journal unusable.
        for lines in ([{"event": "flash stage"}, {"event": "plan", "images": {"system": {"sha256": "a" * 64},
                                                                              "userdata": {"sha256": "b" * 64}}},
                       {"event": "write", "partition": "system"},
                       {"event": "answer", "partition": "userdata", "outcome": install.UNCHANGED}],
                      [{"event": "answer", "partition": "system", "outcome": install.WRITTEN},
                       {"event": "answer", "partition": "userdata", "outcome": install.WRITTEN}],
                      [None]):
            with self.subTest(lines=lines):
                self.write_journal(this)
                with self.journal_path.open("a") as stream:
                    stream.write("".join(json.dumps(line) + "\n" for line in lines))
                self.assertEqual(install.journal_installation(SERIAL), (None, "damaged"))

    def test_a_name_that_only_looks_the_same_is_a_difference(self):
        for odd in ("/system/build.prop\r", "/system/build prop", "/system/build.prop -> x"):
            with self.subTest(name=odd):
                self.unit = {entry["path"]: dict(entry) for entry in self.expected}
                self.unit[odd] = {**self.unit.pop("/system/build.prop"), "path": odd}
                result, code = self.verify()
                self.assertEqual(code, 3, result)
                self.assertIn({"path": "/system/build.prop", "what": "missing on the unit"}, result["differences"])
                self.assertIn({"path": install.shown_text(odd), "what": "on the unit, not in system.img"},
                              result["differences"])
        self.unit = {entry["path"]: dict(entry) for entry in self.expected}
        self.unit["/system/build.prop\n"] = {**self.unit.pop("/system/build.prop"), "path": "/system/build.prop\n"}
        result, code = self.verify()
        self.assertEqual(code, 3, result)
        self.assertEqual([item["path"] for item in result["differences"]], ["/system/build.prop\\n"])

    def test_bytes_the_unit_prints_are_kept_as_they_are(self):
        code, out, _ = install.verify_runner([sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'a\\xffb')"])
        self.assertEqual((code, out), (0, b"a\xffb".decode("utf-8", "surrogateescape")))

    def test_a_difference_seen_in_one_reading_concludes_nothing(self):
        cases = {"the unit's sha1": {1: {"/system/build.prop": {"sha1": "0" * 40}}},
                 # Two different targets whose shown form (cut at 200 characters) is the same.
                 "the unit's target": {1: {"/system/long-link": {"target": "/" + "x" * 240 + "a"}},
                                       2: {"/system/long-link": {"target": "/" + "x" * 240 + "b"}}}}
        for name, per_reading in cases.items():
            with self.subTest(name):
                self.per_reading, self.readings = per_reading, 0
                result, code = self.verify()
                self.assertEqual(code, 1, result)
                self.assertIn("same evidence", result["error"])
        # The same when this computer reads the image differently the first time.
        self.per_reading, self.calls = {}, []
        first = test_ext4image.entries((self.folder / "system.img").read_bytes())
        first[4] = {**first[4], "sha1": "e" * 40}
        clean = install.expected_system(self.folder)
        with unittest.mock.patch.object(install, "expected_system", side_effect=[(first, *clean[1:]), clean]):
            result, code = self.verify()
        self.assertEqual(code, 1, result)
        # And when the folder holds another image the second time.
        self.unit["/system/build.prop"]["sha1"] = "f" * 40
        with unittest.mock.patch.object(install, "expected_system", side_effect=[clean, (clean[0], "9" * 64, clean[2])]):
            result, code = self.verify()
        self.assertEqual(code, 1, result)

    def test_an_image_that_does_not_match_its_hash_is_never_compared(self):
        with (self.folder / "system.img").open("r+b") as stream:
            stream.seek(9 * test_ext4image.BS)
            stream.write(b"\xff")
        result, code = self.verify()
        self.assertEqual(code, 1, result)
        self.assertEqual(self.calls, [])

    def test_answers_it_cannot_account_for_conclude_nothing(self):
        original_runner = self.runner

        def edit(pass_name, change):
            return lambda arguments, answer: (answer[0], change(answer[1]), answer[2]) \
                if arguments[-1] == install.VERIFY_PASSES[pass_name] else answer

        cases = {
            "a pass cut off": ({}, edit("hashes", lambda out: out.replace(install.VERIFY_MARKER, ""))),
            "find killed": ({"errors": 137}, None),
            "passes walking differently": ({"hashes": 1}, None),
            "a strange line": ({}, edit("stat", lambda out: out.replace(
                install.VERIFY_MARKER, "stat: '/system/x': Permission denied\n" + install.VERIFY_MARKER, 1))),
            "a row missing": ({}, edit("stat", lambda out: "\n".join(
                line for line in out.split("\n") if not line.endswith(" /system/bin/ls")))),
            "a name ls lists and find does not": ({}, edit("listing", lambda out: out.replace(
                "@@ /system/bin\n", "@@ /system/bin\nghost\n"))),
            "a folder ls could not open and find could": ({}, edit("listing", lambda out: out.replace(
                "@@ /system/bin\n", "@@ /system/bin\nopendir failed, Permission denied\n"))),
            "no /system": ({}, edit("names", lambda out: out.replace("/system\0", "", 1))),
            "adb's own error": ({}, lambda arguments, answer: (answer[0], answer[1], "error: device offline")
                                if arguments[-1] == install.VERIFY_PASSES["hashes"] else answer),
        }
        for name, (statuses, change) in cases.items():
            with self.subTest(name):
                self.calls, self.statuses = [], statuses
                self.runner = original_runner if change is None else (
                    lambda arguments, change=change: change(arguments, original_runner(arguments)))
                result, code = self.verify()
                self.runner = original_runner
                self.assertEqual(code, 1, result)
                self.assertNotIn(SERIAL, result["error"])

    def test_a_missing_error_row_for_an_entry_the_image_lacks_concludes_nothing(self):
        self.unit["/system/ghost"] = {**self.unit["/system/build.prop"], "path": "/system/ghost"}
        self.denied, self.drop_errors = {"/system/ghost", "/system/holey"}, {"/system/ghost"}
        result, code = self.verify()
        self.assertEqual(code, 1, result)
        self.assertIn("unaccounted", result["error"])

    def test_without_android_and_adb_nothing_is_read(self):
        for state, advice in (("unauthorized", "adbkey.pub"), ("fastboot", "Do not restart it"), ("none", "cable")):
            with self.subTest(state):
                self.state, self.calls = state, []
                result, code = self.verify()
                self.assertEqual((result["verdict"], code), (install.INCOMPLETE, 4))
                self.assertIn("Nothing was compared", result["advice"][0])
                self.assertIn(advice, result["advice"][0])
                self.assertNotIn("fastboot reboot", result["advice"][0])
                self.assertEqual(self.passes(), 0)

    def test_each_field_counts_on_its_own(self):
        entry = {"path": "/system/link", "inode": 20, "mode": 0o120777, "uid": 0, "gid": 0, "links": 1, "size": 300,
                 "mtime": 0, "label": test_ext4image.SYSTEM_LABEL, "target": "/system/" + "y" * 292}
        seen = {"names": {"/system/link"}, "broken": [], "lstatErrors": {}, "openErrors": {},
                "stat": {"/system/link": {key: entry[key] for key in install.STAT_FIELDS}},
                "labels": {"/system/link": {"label": entry["label"], "target": entry["target"][:252] + "..."}},
                "hashes": {}, "hashErrors": {}, "readErrors": {}, "kernelErrors": [], "kernelHistory": [], "limits": []}
        result = install.compare_system([entry], seen)
        self.assertEqual(result["verdict"], install.REVIEW)
        self.assertEqual(result["unread"][0]["reason"], "the link target's end not read")
        seen["labels"]["/system/link"]["target"] = entry["target"][:251] + "z..."
        self.assertEqual(install.compare_system([entry], seen)["verdict"], install.STOP)
        # A target with a space is compared like any other, and so is the owner beside it.
        spaced = {**entry, "target": "/system/a b", "size": 11}
        seen["labels"]["/system/link"]["target"] = "/system/a c"
        seen["stat"]["/system/link"] = {**{key: spaced[key] for key in install.STAT_FIELDS}, "uid": 7}
        what = install.compare_system([spaced], seen)["differences"][0]["what"]
        self.assertIn("uid 0 in the image, 7 on the unit", what)
        self.assertIn("'/system/a c' on the unit", what)
        # toolbox ls cuts a target at its 252nd byte, which may fall inside a character.
        wide = {**entry, "target": "/" + "é" * 128, "size": 257}
        cut = ("/" + "é" * 128).encode("utf-8")[:252] + b"..."
        seen["stat"]["/system/link"] = {key: wide[key] for key in install.STAT_FIELDS}
        seen["labels"]["/system/link"]["target"] = cut.decode("utf-8", "surrogateescape")
        result = install.compare_system([wide], seen)
        self.assertEqual((result["verdict"], result["unread"][0]["reason"]),
                         (install.REVIEW, "the link target's end not read"))

    def test_a_file_the_unit_shows_as_another_type_stops(self):
        self.unit["/system/build.prop"] = {**self.unit["/system/build.prop"], "mode": 0o120777, "target": "x"}
        del self.unit["/system/build.prop"]["sha1"]
        result, code = self.verify()
        self.assertEqual(code, 3, result)
        self.assertIn("mode 33188 in the image, 41471 on the unit", str(result["differences"]))

    def test_entries_inside_a_folder_find_could_not_enter_conclude_nothing(self):
        self.statuses = {name: 1 for name in install.WALKS}
        original = self.runner
        self.runner = lambda arguments: (lambda answer: (answer[0], answer[1].replace(
            install.VERIFY_MARKER, "find: No /system/bin: Permission denied\n" + install.VERIFY_MARKER, 1)
            if arguments[-1] == install.VERIFY_PASSES["errors"] else answer[1], answer[2]))(original(arguments))
        result, code = self.verify()
        self.runner = original
        self.assertEqual(code, 1, result)
        self.assertIn("could not enter", result["error"])

    def test_a_time_before_1970_reads_the_same(self):
        image, _ = test_ext4image.sample()
        image.file(15, test_ext4image.PROPERTIES, mtime=-5)
        self.use_image(image.image())
        self.assertEqual(self.unit["/system/build.prop"]["mtime"], -5)
        result, code = self.verify()
        self.assertEqual((result["differences"], code), ([], 5), result)

    def test_the_passes_avoid_what_the_units_toybox_gets_wrong(self):
        for name, command in install.VERIFY_PASSES.items():
            with self.subTest(name):
                # Its find never runs `-exec ... {} +`; its stat %N prints link targets unterminated.
                self.assertNotIn("{} +", command)
                self.assertNotIn("%N", command)
                self.assertTrue(command.endswith(f"echo {install.VERIFY_MARKER}$?")
                                or command.endswith(f"echo {install.VERIFY_MARKER}${{PIPESTATUS[0]}}"))


if __name__ == "__main__":
    unittest.main()
