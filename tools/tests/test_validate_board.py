import contextlib
import json
import pathlib
import sys
import tempfile
import unittest
from urllib.parse import parse_qsl

TOOLS = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
sys.path.insert(0, str(TOOLS / "tests"))

import validate
from test_validate import FakeAdb, FakeApi, fake_time, frame, healthy_report
from validate import CheckFailed, CheckSkipped, Reply

GUIDE = validate.BOARD_GUIDE
ITEMS = validate.BOARD_ITEMS
# 2026-10-02T17:00:00Z
START = 1_790_960_400_000


class FakeBoard(FakeApi):
    """The board as Mirror Home serves it, in as much detail as the checks
    look at, with switches that make it misbehave."""

    EXAMPLES = [
        {"does": "Leave a note", "method": "POST", "path": ITEMS, "body": {"title": "Dinner", "ttlSeconds": 7200}},
        {"does": "Add a to-do", "method": "POST", "path": ITEMS, "body": {"kind": "todo", "title": "Plants"}},
        {"does": "Set a reminder", "method": "PUT", "path": ITEMS + "/dentist",
         "body": {"kind": "reminder", "title": "Dentist", "due": "2026-10-03T09:00:00-07:00"}},
        {"does": "Mark something done", "method": "PATCH", "path": ITEMS + "/dentist", "body": {"done": True}},
        {"does": "Remove your own", "method": "DELETE", "path": ITEMS + "?source=YOUR-NAME"},
    ]

    def __init__(self, clock, *faults, debuggable=True):
        super().__init__()
        self.clock = clock
        self.faults = set(faults)
        self.debuggable = debuggable
        self.items = {}
        self.version = 0
        self.serial = 0
        self.layout = {"widgets": [
            {"id": "clock", "type": "clock", "visible": True},
            {"id": "board", "type": "board", "visible": False, "text": "", "size": "medium", "show": "all"},
        ]}
        self.layouts_saved = []

    def now(self):
        return START + int(self.clock.now * 1000)

    def refuse(self, status, message, field=None):
        body = {"error": message}
        if field:
            body["field"] = field
        if "no-guide-pointer" not in self.faults:
            body["guide"] = GUIDE
        return Reply(status, body, {})

    def expire(self):
        if "never-expires" in self.faults:
            return
        gone = [name for name, item in self.items.items() if item["expiresAt"] and item["expiresAt"] <= self.now()]
        for name in gone:
            del self.items[name]
        if gone and "silent-expiry" not in self.faults:
            self.version += 1

    def view(self, item):
        now = self.now()
        due = item["due"]
        state = "done" if item["done"] else "open" if due is None else (
            "overdue" if due <= now else "soon" if due - now <= 3_600_000 else "open"
        )
        return dict(item, state=state, showing=True, dueIso="2099-01-01T17:00:00Z" if due else None)

    def ordered(self):
        def rank(item):
            state = self.view(item)["state"]
            return {"overdue": 0, "soon": 1, "done": 4}.get(state, 3 if item["priority"] == "low" else 2)

        return [self.view(item) for item in sorted(self.items.values(), key=lambda item: (rank(item), item["order"]))]

    def save(self, name, body, replace):
        existing = self.items.get(name)
        if not replace and existing is None:
            return self.refuse(404, f'The board has no item "{name}"', "id")
        unknown = [key for key in body if key not in (
            "kind", "title", "body", "due", "done", "priority", "ttlSeconds", "expiresAt", "source"
        )]
        if unknown:
            return self.refuse(400, f'Unknown field "{unknown[0]}"', unknown[0])
        base = dict(existing) if existing and not replace else {
            "id": name, "kind": "note", "title": None, "body": "", "due": None, "done": False,
            "doneAt": None, "priority": "normal", "source": "Validation suite",
            "order": existing["order"] if existing else None,
        }
        base.update({key: value for key, value in body.items() if key != "ttlSeconds"})
        if base["kind"] == "reminder" and base["due"] is None:
            return self.refuse(400, "A reminder needs due", "due")
        if isinstance(base["due"], str):
            base["due"] = 4_070_970_000_000 if base["due"].startswith("2099") else START + 57_600_000
        base["doneAt"] = self.now() if base["done"] else None
        base["expiresAt"] = self.now() + body.get("ttlSeconds", 86_400) * 1000
        if base["order"] is None:
            self.serial += 1
            base["order"] = self.serial
        self.items[name] = base
        self.version += 1
        reply = {"item": self.view(base), "version": self.version, "created": existing is None}
        return Reply(201 if existing is None else 200, reply, {})

    def call(self, method, path, body=None, *, token=True, data=None, **_options):
        self.calls.append((method, path, body))
        path, _, query = path.partition("?")
        query = dict(parse_qsl(query))
        self.expire()
        if data is not None:
            # Bytes as a program sends them; Mirror Home reads them as UTF-8.
            try:
                body = json.loads(data.decode("ascii", "replace") if "ascii-only" in self.faults and method == "POST"
                                  else data.decode("utf-8"))
            except ValueError:
                body = None
        if (method, path) == ("GET", "/api/v1/health"):
            return Reply(200, dict(healthy_report(), debuggable=self.debuggable), {})
        if (method, path) == ("GET", "/api/v1/status"):
            return Reply(200, {"boardVersion": self.version}, {})
        if (method, path) == ("GET", "/api/v1/dashboard/layout"):
            return Reply(200, json.loads(json.dumps(self.layout)), {})
        if (method, path) == ("PUT", "/api/v1/dashboard/layout"):
            self.layout = json.loads(json.dumps(body))
            self.layouts_saved.append(self.layout)
            return Reply(200, body, {})
        if (method, path) == ("GET", GUIDE):
            examples = [dict(example) for example in self.EXAMPLES]
            if "example-fails" in self.faults:
                examples[1]["body"] = {"kind": "todo", "text": "Plants"}
            return Reply(200, {"start": ["Pair"], "item": [{"name": "title"}], "examples": examples}, {})
        # Over loopback the glass may read the summary without a token; nothing else.
        if token is None and (method, path) != ("GET", validate.BOARD) and "open-to-all" not in self.faults:
            return self.refuse(401, "The board needs a paired device's token")
        if (method, path) == ("GET", validate.BOARD):
            return Reply(200, {
                "version": self.version, "now": self.now(), "items": self.ordered(),
                "counts": {"total": len(self.items)}, "glass": {"showsBoard": True},
            }, {})
        if path == ITEMS:
            if method == "GET":
                matching = [item for item in self.ordered() if query.get("source") in (None, item["source"])]
                offset, limit = int(query.get("offset", 0)), int(query.get("limit", 50))
                if "pages-repeat" in self.faults:
                    offset = 0
                end = offset + limit
                return Reply(200, {
                    "items": matching[offset:end], "total": len(matching),
                    "nextOffset": end if end < len(matching) else None,
                }, {})
            if method == "DELETE":
                if not query:
                    return self.refuse(400, "Say which items to remove")
                names = [name for name, item in self.items.items()
                         if query.get("all") == "true" or item["source"] == query.get("source")]
                for name in names:
                    del self.items[name]
                self.version += bool(names)
                return Reply(200, {"deleted": len(names), "version": self.version}, {})
            if data is not None and body is None:
                return self.refuse(400, "The request body must be one JSON object")
            self.serial += 1
            return self.save(f"made-{self.serial}", body, True)
        name = path[len(ITEMS) + 1:]
        if method == "PUT":
            return self.save(name, body, True)
        if method == "PATCH":
            return self.save(name, body, False)
        if method == "POST":
            return self.refuse(405, "POST is not available here")
        if name not in self.items:
            return self.refuse(404, f'The board has no item "{name}"', "id")
        if method == "DELETE":
            del self.items[name]
            self.version += 1
            return Reply(200, {"deleted": 1, "version": self.version}, {})
        return Reply(200, {"item": self.view(self.items[name]), "version": self.version}, {})


class FakeGlass:
    """The dashboard page: lists the board four rows at a time and turns a
    page every ten seconds, as mirror.js does."""

    def __init__(self, board, *faults):
        self.board = board
        self.faults = set(faults)

    def evaluate(self, script):
        widget = next(widget for widget in self.board.layout["widgets"] if widget["id"] == "board")
        items = self.board.ordered()
        if "=== null" in script:
            return not (widget["visible"] and items)
        if not widget["visible"] or not items:
            return "null"
        pages = [items[start:start + 4] for start in range(0, len(items), 4)]
        current = 0 if "stuck" in self.faults else int(self.board.clock.now // 10) % len(pages)
        now = self.board.now()

        def when(item):
            if item["done"] or item["due"] is None:
                return ""
            minutes = (item["due"] - now) / 60_000
            return f"In {int(-(-minutes // 1))} min" if minutes > 0 else f"{int(-minutes)} min ago"

        return json.dumps({
            "heading": widget["text"], "dots": len(pages) if len(pages) > 1 else 0, "fading": False,
            "inner": 400 if "cut-off" in self.faults else 300, "box": 333,
            "rows": [{
                "title": item["title"], "when": when(item),
                "state": f"mr-board-row mr-board-{item['state']} mr-board-{item['priority']}",
            } for item in pages[current]],
        })


class BoardCheckCase(unittest.TestCase):
    def context(self, *faults, glass=(), debuggable=True):
        clock = fake_time(self)
        board = FakeBoard(clock, *faults, debuggable=debuggable)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        ctx = validate.Context(FakeAdb([frame(255)]), board, pathlib.Path(directory.name))

        @contextlib.contextmanager
        def page(*_arguments, **_options):
            ctx.require_inspectable()
            yield FakeGlass(board, *glass)

        ctx.page = page
        return ctx, board


class BoardApiCheckTest(BoardCheckCase):
    def test_a_board_that_behaves_passes_and_is_left_empty(self):
        ctx, board = self.context()
        validate.check_board_api(ctx)
        self.assertEqual({}, board.items)
        self.assertEqual(5, ctx.details["boardExamples"])
        self.assertIn(("PATCH", ITEMS + "/plants", {"done": True}), board.calls)

    def test_it_begins_without_a_token_as_a_new_program_would(self):
        ctx, board = self.context()
        validate.check_board_api(ctx)
        self.assertEqual(("GET", GUIDE, None), board.calls[0])

    def test_an_example_in_the_guide_that_does_not_work_is_named(self):
        ctx, board = self.context("example-fails")
        with self.assertRaisesRegex(CheckFailed, "The guide's example 'Add a to-do' answered 400"):
            validate.check_board_api(ctx)
        self.assertEqual({}, board.items)

    def test_a_board_open_to_anyone_fails(self):
        ctx, _ = self.context("open-to-all")
        with self.assertRaisesRegex(CheckFailed, "A post without a token answered 201, expected 401"):
            validate.check_board_api(ctx)

    def test_a_refusal_that_does_not_point_at_the_guide_fails(self):
        ctx, _ = self.context("no-guide-pointer")
        with self.assertRaisesRegex(CheckFailed, "refused without pointing at the guide"):
            validate.check_board_api(ctx)

    def test_text_that_arrives_as_question_marks_fails(self):
        ctx, board = self.context("ascii-only")
        with self.assertRaisesRegex(CheckFailed, "POST as application/json did not keep its text"):
            validate.check_board_api(ctx)
        self.assertEqual({}, board.items)

    def test_pages_that_do_not_add_up_fail(self):
        ctx, board = self.context("pages-repeat")
        with self.assertRaisesRegex(CheckFailed, "The listing's pages never end"):
            validate.check_board_api(ctx)
        self.assertEqual({}, board.items)

    def test_an_item_that_outlives_its_time_fails(self):
        ctx, board = self.context("never-expires")
        with self.assertRaisesRegex(CheckFailed, "waiting for an item to leave when its time is up"):
            validate.check_board_api(ctx)
        self.assertEqual({}, board.items)

    def test_an_expiry_the_glass_would_not_hear_of_fails(self):
        ctx, _ = self.context("silent-expiry")
        with self.assertRaisesRegex(CheckFailed, "An expired item did not move boardVersion"):
            validate.check_board_api(ctx)


class BoardGlassCheckTest(BoardCheckCase):
    def test_every_item_coming_round_passes_and_puts_the_layout_back(self):
        ctx, board = self.context()
        original = json.loads(json.dumps(board.layout))
        validate.check_board_glass(ctx)
        self.assertEqual(2, ctx.details["boardPages"])
        self.assertEqual(original, board.layout)
        self.assertEqual({}, board.items)
        shown = next(widget for widget in board.layouts_saved[0]["widgets"] if widget["id"] == "board")
        self.assertTrue(shown["visible"])
        self.assertTrue((ctx.output / "board.png").is_file())

    def test_a_release_build_is_skipped_before_anything_changes(self):
        ctx, board = self.context(debuggable=False)
        with self.assertRaises(CheckSkipped):
            validate.check_board_glass(ctx)
        self.assertEqual({("GET", "/api/v1/health")}, {call[:2] for call in board.calls})

    def test_a_board_that_never_turns_its_page_fails(self):
        ctx, board = self.context(glass=("stuck",))
        original = json.loads(json.dumps(board.layout))
        with self.assertRaisesRegex(CheckFailed, "waiting for every item to come round"):
            validate.check_board_glass(ctx)
        self.assertEqual(original, board.layout)
        self.assertEqual({}, board.items)

    def test_a_page_taller_than_its_widget_fails(self):
        ctx, _ = self.context(glass=("cut-off",))
        with self.assertRaisesRegex(CheckFailed, "taller than its widget"):
            validate.check_board_glass(ctx)


class BoardChecksAreListedTest(unittest.TestCase):
    def test_the_suite_runs_the_board_after_the_notes(self):
        names = [name for name, _, _, _ in validate.EMULATOR_CHECKS]
        self.assertEqual(
            ["notes", "board-api", "board-glass"], names[names.index("notes"):names.index("notes") + 3]
        )
        self.assertIn("board", [name for name, _, _, _ in validate.EXERCISE_CHECKS])


class MomentsCheckTest(unittest.TestCase):
    def test_the_suite_looks_at_moments_after_the_board(self):
        names = [name for name, _, _, _ in validate.EMULATOR_CHECKS]
        self.assertEqual("moments", names[names.index("board-glass") + 1])

    def test_boxes_that_only_touch_do_not_lie_over_one_another(self):
        # Each box is left, top, right, bottom, as the glass reports them.
        self.assertTrue(validate.boxes_share([0, 0, 100, 100], [50, 50, 150, 150]))
        self.assertTrue(validate.boxes_share([0, 0, 100, 100], [20, 20, 30, 30]))
        self.assertFalse(validate.boxes_share([0, 0, 100, 100], [100, 0, 200, 100]))
        self.assertFalse(validate.boxes_share([0, 0, 100, 100], [0, 101, 100, 200]))


if __name__ == "__main__":
    unittest.main()
