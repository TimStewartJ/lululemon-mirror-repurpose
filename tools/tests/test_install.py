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

    def setUp(self):
        self.images = self.folder({"system.img": SYSTEM, "userdata.img": USERDATA})
        self.journal = self.images.parent / f"{self.images.name}-journal.jsonl"
        self.addCleanup(lambda: self.journal.unlink(missing_ok=True))
        self.tools, _ = self.unit()
        self.listing = f"{SERIAL}\tfastboot\n"
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
        self.during_write.get(name, lambda: None)()
        return self.answers[name]

    def flash(self, typed=install.CONFIRMATION):
        prompts = []

        def confirm(prompt):
            prompts.append(prompt)
            if isinstance(typed, BaseException):
                raise typed
            return typed() if callable(typed) else typed

        printed = io.StringIO()
        with contextlib.redirect_stdout(printed), contextlib.redirect_stderr(printed):
            code = install.flash(self.tools, self.writer, self.images, SERIAL, self.journal, confirm)
        return code, printed.getvalue(), prompts

    def entries(self):
        return [json.loads(line) for line in self.journal.read_text(encoding="utf-8").splitlines()] \
            if self.journal.exists() else []

    def fresh_journal(self):
        """A journal without earlier writes, so a scenario is judged on its own run."""
        self.journal = self.images.parent / f"{self.images.name}-journal-{len(self.sent)}-{id(object())}.jsonl"
        journal = self.journal
        self.addCleanup(lambda: journal.unlink(missing_ok=True))
        self.sent.clear()

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

    def test_another_fastboot_unit_stops_it_before_the_question_and_before_each_write(self):
        self.listing = f"{SERIAL}\tfastboot\nother42\tfastboot\n"
        code, printed, prompts = self.flash()
        self.assertEqual(code, install.EXIT_CODES[install.STOP])
        self.assertEqual((prompts, self.sent), ([], []))
        self.assertNotIn("other42", self.journal.read_text(encoding="utf-8"))

        self.listing = f"{SERIAL}\tfastboot\n"
        self.during_write["system"] = lambda: setattr(self, "listing", f"{SERIAL}\tfastboot\nother42\tfastboot\n")
        code, printed, _ = self.flash()
        self.assertEqual(code, install.WRITE_FAILED, printed)
        self.assertEqual([arguments[4] for arguments in self.sent], ["system"])

    def test_a_refused_first_write_leaves_the_unit_unchanged_and_is_not_restarted(self):
        self.answers["system"] = (1, REFUSED, "")
        code, printed, _ = self.flash()
        self.assertEqual(code, install.EXIT_CODES[install.STOP])
        self.assertEqual(len(self.sent), 1)
        self.assertIn("Nothing was written", printed)
        self.assertIn("Partition flashing is not allowed", printed)
        self.assertIn("reboot", printed)
        self.assertEqual(self.entries()[-1]["outcome"], install.UNCHANGED)

    def test_after_an_unfinished_write_a_later_refusal_does_not_say_restart(self):
        self.answers["system"] = (1, CUT_OFF, "")
        self.assertEqual(self.flash()[0], install.WRITE_FAILED)
        self.answers["system"] = (1, REFUSED, "")
        code, printed, _ = self.flash()
        self.assertEqual(code, install.WRITE_FAILED, printed)
        self.assertIn("do not restart", printed)
        self.assertNotIn("restart it with", printed)

    def test_a_system_write_cut_off_after_a_piece_stays_in_fastboot(self):
        self.answers["system"] = (1, CUT_OFF, "")
        code, printed, _ = self.flash()
        self.assertEqual(code, install.WRITE_FAILED)
        self.assertEqual([arguments[3:5] for arguments in self.sent], [["flash", "system"]])
        self.assertIn("do not restart", printed)

    def test_a_failed_userdata_write_is_not_followed_by_a_restart(self):
        for answer in ((1, step("Sending 'userdata' (10652 KB)", "OKAY [  0.402s]")
                        + step("Writing 'userdata'", "FAILED (remote: 'flash write failure')") + COMMAND_FAILED, ""),
                       (1, step("Sending 'userdata' (10652 KB)", "FAILED (remote: 'data too large')")
                        + COMMAND_FAILED, "")):
            with self.subTest(answer=answer[1]):
                self.sent.clear()
                self.answers["userdata"] = answer
                code, printed, _ = self.flash()
                self.assertEqual(code, install.WRITE_FAILED, printed)
                self.assertEqual([arguments[4] for arguments in self.sent], ["system", "userdata"])
                self.assertIn("do not restart", printed)

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

    def test_ctrl_c_during_a_write_stops_before_the_next_step(self):
        self.during_write["system"] = lambda: signal.raise_signal(signal.SIGINT)
        code, printed, _ = self.flash()
        self.assertEqual(code, install.WRITE_FAILED, printed)
        self.assertEqual([arguments[4] for arguments in self.sent], ["system"])

        self.fresh_journal()
        self.answers["system"] = (1, step("Sending sparse 'system' 1/2 (524284 KB)", "FAILED (interrupted)")
                                  + COMMAND_FAILED, "")
        code, printed, _ = self.flash()
        self.assertEqual(code, install.WRITE_FAILED, printed)  # never "unchanged" once interrupted
        self.assertEqual(self.entries()[-1]["outcome"], install.UNKNOWN)

        self.fresh_journal()
        self.answers["system"] = (0, SYSTEM_IN_PIECES, "")
        self.during_write = {"userdata": lambda: signal.raise_signal(signal.SIGINT)}
        code, printed, _ = self.flash()
        self.assertEqual(code, install.NOT_RESTARTED, printed)
        self.assertEqual([arguments[4] for arguments in self.sent], ["system", "userdata"])
        self.assertIs(signal.getsignal(signal.SIGINT), signal.default_int_handler)

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

    def test_the_journal_never_goes_in_the_images_folder(self):
        kept = self.journal
        for journal in (self.images / "journal.jsonl", self.images):
            with self.subTest(journal=journal):
                self.journal = journal
                code, printed, prompts = self.flash()
                self.assertEqual(code, install.EXIT_CODES[install.STOP])
                self.assertEqual((prompts, self.sent), ([], []))
                self.assertFalse((self.images / "journal.jsonl").exists())
        self.journal = kept

    def test_without_a_terminal_the_stage_refuses_to_run(self):
        arguments = argparse.Namespace(images=str(self.images), serial=SERIAL, journal=None)
        with contextlib.redirect_stderr(io.StringIO()), unittest.mock.patch("sys.stdin", io.StringIO("")):
            self.assertEqual(install.command_flash(arguments), install.EXIT_CODES[install.STOP])
        self.assertEqual(self.sent, [])

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

    def interrupt_when_recording(self, event, partition=None):
        """Ctrl-C right after the journal records ``event``, before the command could be sent."""
        record = install.Journal.record

        def recording_then_interrupted(journal, name, **fields):
            record(journal, name, **fields)
            if name == event and fields.get("partition") == partition:
                signal.raise_signal(signal.SIGINT)

        patcher = unittest.mock.patch.object(install.Journal, "record", recording_then_interrupted)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_ctrl_c_after_a_command_is_recorded_keeps_it_from_being_sent(self):
        self.interrupt_when_recording("write", "userdata")
        code, printed, _ = self.flash()
        self.assertEqual(code, install.WRITE_FAILED, printed)
        self.assertEqual([arguments[4] for arguments in self.sent], ["system"])
        self.assertEqual(install.unit_history(self.journal.read_text(encoding="utf-8"))[0],
                         {"system": install.WRITTEN, "userdata": None})

    def test_ctrl_c_after_the_restart_is_recorded_keeps_it_from_being_sent(self):
        self.interrupt_when_recording("restart")
        code, printed, _ = self.flash()
        self.assertEqual(code, install.NOT_RESTARTED, printed)
        self.assertEqual([arguments[3] for arguments in self.sent], ["flash", "flash"])

    def test_a_retry_from_another_folder_still_knows_the_unfinished_write(self):
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
        self.assertIn("Stay in fastboot", printed)

    def test_a_complete_earlier_run_is_not_mistaken_for_an_unfinished_one(self):
        self.assertEqual(self.flash()[0], 0)
        (self.images / "userdata.img").unlink()
        (self.images / "userdata.img.sha256").unlink()
        code, printed, _ = self.flash()
        self.assertEqual(code, install.EXIT_CODES[install.STOP], printed)

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
        self.assertIn("Stay in fastboot", fallback.getvalue())

    def test_any_other_row_in_fastboots_list_counts_as_another_device(self):
        for extra in ("no permissions (missing udev rules? user is in the plugdev group); see "
                      "[http://developer.android.com/tools/device.html]\t fastboot",
                      "other42\t Android Bootloader Interface"):
            with self.subTest(extra=extra):
                self.listing = f"{SERIAL}\t fastboot\n{extra}\n"
                code, printed, prompts = self.flash()
                self.assertEqual(code, install.EXIT_CODES[install.STOP], printed)
                self.assertEqual((prompts, self.sent), ([], []))

    def test_an_acknowledged_restart_is_not_called_a_failure(self):
        self.answers["reboot"] = (1, step("Rebooting", "OKAY [  0.003s]") + "interrupted\n", "")
        code, printed, _ = self.flash()
        self.assertEqual(code, install.NOT_RESTARTED, printed)
        self.assertIn("fastboot devices", printed)
        self.assertNotIn("power button", printed)
        self.assertEqual(install.restart_outcome(*self.answers["reboot"][:2]), install.ACKNOWLEDGED)

    def test_a_journal_that_cannot_record_the_last_answer_is_not_a_clean_success(self):
        record = install.Journal.record

        def failing(journal, name, **fields):
            if name == "answer" and fields.get("outcome") == install.RESTARTED:
                journal.failed = True
                raise OSError("disk full")
            record(journal, name, **fields)

        with unittest.mock.patch.object(install.Journal, "record", failing):
            code, printed, _ = self.flash()
        self.assertEqual(code, install.JOURNAL_INCOMPLETE, printed)
        self.assertIn("could not be completed", printed)

    def test_a_journal_that_is_not_one_is_never_appended_to(self):
        os.link(self.images / "system.img.sha256", self.journal)
        before = (self.images / "system.img.sha256").read_bytes()
        code, printed, prompts = self.flash()
        self.assertEqual(code, install.EXIT_CODES[install.STOP], printed)
        self.assertEqual((prompts, self.sent), ([], []))
        self.assertEqual((self.images / "system.img.sha256").read_bytes(), before)
        self.journal.unlink()
        self.journal.write_text("not a journal\n")
        self.assertEqual(self.flash()[0], install.EXIT_CODES[install.STOP])
        self.assertEqual(self.journal.read_text(), "not a journal\n")

    def test_what_earlier_runs_left_is_read_from_the_journal(self):
        def lines(*events):
            return "\n".join(json.dumps(event) for event in events)

        write = {"event": "write", "partition": "system"}
        cases = [
            (lines(), {"system": None, "userdata": None}, False),
            (lines(write), {"system": install.UNKNOWN, "userdata": None}, False),
            (lines(write, {"event": "not sent", "partition": "system"}), {"system": None, "userdata": None}, False),
            (lines(write, {"event": "answer", "partition": "system", "outcome": "unchanged"}),
             {"system": None, "userdata": None}, False),
            (lines(write, {"event": "answer", "partition": "system", "outcome": "unknown"}, write,
                   {"event": "answer", "partition": "system", "outcome": "unchanged"}),
             {"system": install.UNKNOWN, "userdata": None}, False),
            (lines(write, {"event": "answer", "partition": "system", "outcome": "written"},
                   {"event": "write", "partition": "userdata"},
                   {"event": "answer", "partition": "userdata", "outcome": "written"},
                   {"event": "restart"}, {"event": "answer", "outcome": "restarted"}),
             {"system": install.WRITTEN, "userdata": install.WRITTEN}, True),
        ]
        for text, states, restarted in cases:
            with self.subTest(text=text):
                self.assertEqual(install.unit_history(text), (states, restarted))
        with self.assertRaises(ValueError):
            install.unit_history("[1, 2]\n")

    def test_the_default_journal_is_one_per_unit_and_does_not_show_its_serial(self):
        path = install.default_journal(SERIAL)
        self.assertNotIn(SERIAL, str(path))
        self.assertEqual(path, install.default_journal(SERIAL))
        self.assertNotEqual(path, install.default_journal("other42"))


if __name__ == "__main__":
    unittest.main()
