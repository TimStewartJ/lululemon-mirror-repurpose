import contextlib
import io
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

TOOLS = pathlib.Path(__file__).resolve().parents[1]
REPO = TOOLS.parent
sys.path.insert(0, str(TOOLS))

import zone_offsets


CONTROL_CLOCK = (
    REPO / "android" / "mirror-home" / "src" / "main" / "assets" / "control" / "clock.js"
)
# Zones whose rules have been stable for years, so Node's ICU data, Python's
# IANA data and the committed table all describe them the same way.
STABLE_ZONES = (
    "America/Los_Angeles",
    "America/New_York",
    "Europe/Berlin",
    "Australia/Sydney",
    "Australia/Lord_Howe",
    "Asia/Kolkata",
    "Asia/Kathmandu",
    "America/Phoenix",
)


def step_offsets(steps):
    """An offset reader that switches to each (minute, offset) step in turn."""

    def offset_at(minute):
        offset = steps[0][1]
        for start, value in steps:
            if minute >= start:
                offset = value
        return offset

    return offset_at


def zoneinfo_available() -> bool:
    try:
        import zoneinfo

        zoneinfo.ZoneInfo("America/Los_Angeles")
    except Exception:
        return False
    return True


class ChangesTest(unittest.TestCase):
    def test_finds_each_change_at_its_first_minute(self):
        reader = step_offsets([(0, -420), (4_321, -480), (300_007, -420)])

        self.assertEqual(
            [[4_321, -480], [300_007, -420]],
            zone_offsets.changes(reader, 0, 400_000),
        )

    def test_window_is_exclusive_at_the_start_and_inclusive_at_the_end(self):
        reader = step_offsets([(0, 60), (1_000, 120), (5_000, 60)])

        self.assertEqual([[5_000, 60]], zone_offsets.changes(reader, 1_000, 5_000))
        self.assertEqual([], zone_offsets.changes(reader, 1_000, 4_999))

    def test_constant_offset_has_no_changes(self):
        self.assertEqual([], zone_offsets.changes(step_offsets([(0, 345)]), 0, 10_000_000))

    def test_rejects_a_nonpositive_step(self):
        with self.assertRaises(ValueError):
            zone_offsets.changes(step_offsets([(0, 0)]), 0, 10, step_minutes=0)


class BuildTableTest(unittest.TestCase):
    READERS = {
        "A/Summer": step_offsets([(0, -420), (5_000, -480)]),
        "B/Alias": step_offsets([(0, -420), (5_000, -480)]),
        "C/Fixed": step_offsets([(0, 330)]),
    }

    def build(self):
        return zone_offsets.build_table(
            self.READERS, 100, 20_000, reader=lambda name: self.READERS[name]
        )

    def test_zones_with_the_same_offsets_share_a_rule(self):
        table = self.build()

        self.assertEqual(2, len(table["rules"]))
        self.assertEqual(table["zones"]["A/Summer"], table["zones"]["B/Alias"])
        self.assertEqual(
            [-420, [[5_000, -480]]], table["rules"][table["zones"]["A/Summer"]]
        )
        self.assertEqual([330, []], table["rules"][table["zones"]["C/Fixed"]])

    def test_out_of_range_offsets_are_refused(self):
        with self.assertRaises(zone_offsets.ZoneOffsetError):
            zone_offsets.build_table(
                ["X/Wild"], 0, 10, reader=lambda name: step_offsets([(0, 900)])
            )

    def test_rendering_round_trips_and_ends_with_one_newline(self):
        table = self.build()
        text = zone_offsets.render(table, tzdata="2026d", start_minute=100, end_minute=20_000)
        parsed = json.loads(text)

        self.assertEqual(zone_offsets.FORMAT_VERSION, parsed["version"])
        self.assertEqual("2026d", parsed["tzdata"])
        self.assertEqual(100, parsed["from"])
        self.assertEqual(20_000, parsed["until"])
        self.assertEqual(table["rules"], parsed["rules"])
        self.assertEqual(table["zones"], parsed["zones"])
        self.assertTrue(text.endswith("}\n"))
        self.assertFalse(text.endswith("\n\n"))

    def test_differing_zones_names_changed_added_and_removed_zones(self):
        committed = self.build()
        fresh = zone_offsets.build_table(
            ["A/Summer", "C/Fixed", "D/New"],
            100,
            20_000,
            reader=lambda name: {
                "A/Summer": step_offsets([(0, -420)]),
                "C/Fixed": step_offsets([(0, 330)]),
                "D/New": step_offsets([(0, 0)]),
            }[name],
        )

        self.assertEqual(
            ["A/Summer", "B/Alias", "D/New"],
            zone_offsets.differing_zones(committed, fresh),
        )


class CommittedTableTest(unittest.TestCase):
    def setUp(self):
        self.table = json.loads(zone_offsets.DEFAULT_OUTPUT.read_text(encoding="utf-8"))

    def test_structure_is_what_mirror_home_parses(self):
        table = self.table

        self.assertEqual(zone_offsets.FORMAT_VERSION, table["version"])
        self.assertLess(table["from"], table["until"])
        self.assertGreater(len(table["zones"]), 400)
        for name, rule in table["zones"].items():
            self.assertTrue(0 <= rule < len(table["rules"]), name)
        for base, changes in table["rules"]:
            self.assertLessEqual(abs(base), zone_offsets.MAX_OFFSET_MINUTES)
            previous_minute, previous_offset = table["from"], base
            for minute, offset in changes:
                self.assertGreater(minute, previous_minute)
                self.assertLessEqual(minute, table["until"])
                self.assertNotEqual(offset, previous_offset)
                self.assertLessEqual(abs(offset), zone_offsets.MAX_OFFSET_MINUTES)
                previous_minute, previous_offset = minute, offset

    def test_file_is_exactly_what_the_generator_renders(self):
        rendered = zone_offsets.render(
            {"rules": self.table["rules"], "zones": self.table["zones"]},
            tzdata=self.table["tzdata"],
            start_minute=self.table["from"],
            end_minute=self.table["until"],
        )

        self.assertEqual(
            rendered,
            zone_offsets.DEFAULT_OUTPUT.read_bytes().decode("utf-8").replace("\r\n", "\n"),
        )

    def test_check_mode_reports_a_table_that_no_longer_matches(self):
        if not zoneinfo_available():
            self.skipTest("IANA time-zone data is not installed")
        # One day and an offset no zone uses keep this quick and certain to differ.
        stale = {
            "version": zone_offsets.FORMAT_VERSION,
            "tzdata": "stale",
            "from": self.table["from"],
            "until": self.table["from"] + zone_offsets.MINUTES_PER_DAY,
            "rules": [[1, []]],
            "zones": {name: 0 for name in self.table["zones"]},
        }
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "zone-offsets.json"
            path.write_text(json.dumps(stale), encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(1, zone_offsets.main(["--check", "--output", str(path)]))
            self.assertIn("differ", output.getvalue())


@unittest.skipUnless(zoneinfo_available(), "IANA time-zone data is not installed")
class StableZonesTest(unittest.TestCase):
    def setUp(self):
        self.table = json.loads(zone_offsets.DEFAULT_OUTPUT.read_text(encoding="utf-8"))

    def test_committed_table_agrees_with_local_iana_data(self):
        fresh = zone_offsets.build_table(
            STABLE_ZONES, self.table["from"], self.table["until"]
        )

        for name in STABLE_ZONES:
            self.assertEqual(
                fresh["rules"][fresh["zones"][name]],
                self.table["rules"][self.table["zones"][name]],
                name,
            )

    @unittest.skipUnless(shutil.which("node"), "Node.js is not installed")
    def test_control_page_computes_the_same_changes_as_the_table(self):
        start_ms = self.table["from"] * 60_000
        script = (
            "const clock = require(process.argv[1]);"
            "const result = {};"
            "for (const zone of JSON.parse(process.argv[3])) {"
            "  result[zone] = clock.describe(zone, Number(process.argv[2]));"
            "}"
            "process.stdout.write(JSON.stringify(result));"
        )
        completed = subprocess.run(
            [
                shutil.which("node"),
                "-e",
                script,
                str(CONTROL_CLOCK),
                str(start_ms),
                json.dumps(STABLE_ZONES),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        described = json.loads(completed.stdout)

        for name in STABLE_ZONES:
            base, changes = self.table["rules"][self.table["zones"][name]]
            self.assertEqual(base, described[name]["utcOffsetMinutes"], name)
            self.assertEqual(
                [[minute * 60_000, offset] for minute, offset in changes],
                [
                    [change["at"], change["utcOffsetMinutes"]]
                    for change in described[name]["utcOffsetChanges"]
                ],
                name,
            )


if __name__ == "__main__":
    unittest.main()
