import contextlib
import io
import pathlib
import sys
import unittest
from unittest import mock

TOOLS = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))

import dex_guard
from dex_guard import Finding

# As `dexdump -d` prints a class: its descriptor, each method's name, and the code under it.
DISASSEMBLY = """
Class #1            -
  Class descriptor  : 'Ldev/mirror/repurpose/SystemLog;'
  Direct methods    -
    #0              : (in Ldev/mirror/repurpose/SystemLog;)
      name          : '<clinit>'
      type          : '()V'
0a1b2c:                                        |[0a1b2c] dev.mirror.repurpose.SystemLog.<clinit>:()V
0a1b3c: 2520 1f00 0000                         |0000: filled-new-array/range {v0 .. v31}, [Ljava/lang/String; // type@001f
    #1              : (in Ldev/mirror/repurpose/SystemLog;)
      name          : 'capture'
      type          : '(JLjava/lang/String;)Lorg/json/JSONObject;'
0a2b3c: 2506 1f00 0500                         |0010: filled-new-array/range {v5, v6, v7, v8, v9, v10}, [Ljava/lang/String; // type@001f
0a2b4c: 2403 1f00 1002                         |0020: filled-new-array {v0, v1, v2}, [Ljava/lang/String; // type@001f
    #2              : (in Ldev/mirror/repurpose/SystemLog;)
      name          : 'sizes'
      type          : '()[I'
0a3b3c: 2506 2000 0500                         |0010: filled-new-array/range {v5, v6, v7, v8, v9, v10}, [I // type@0020
0a3b4c: 2506 2100 0500                         |0020: filled-new-array/range {v1 .. v7}, [[I // type@0021
Class #2            -
  Class descriptor  : 'Landroidx/media3/common/Format;'
  Direct methods    -
    #0              : (in Landroidx/media3/common/Format;)
      name          : 'toBundle'
      type          : '()Landroid/os/Bundle;'
0b1b3c: 2508 3000 0000                         |0000: filled-new-array/range {v0 .. v7}, [Ljava/lang/Object; // type@0030
"""


class FindingsTest(unittest.TestCase):
    def test_every_array_of_objects_made_from_a_range_is_found_with_its_method(self):
        self.assertEqual(
            [
                Finding("Ldev/mirror/repurpose/SystemLog;", "<clinit>", 32),
                Finding("Ldev/mirror/repurpose/SystemLog;", "capture", 6),
                # An array of arrays of numbers holds objects: the arrays.
                Finding("Ldev/mirror/repurpose/SystemLog;", "sizes", 7),
                Finding("Landroidx/media3/common/Format;", "toBundle", 8),
            ],
            dex_guard.findings(DISASSEMBLY),
        )

    def test_only_mirror_homes_own_compiled_code_is_a_fault(self):
        faults, others = dex_guard.judge(dex_guard.findings(DISASSEMBLY))
        # Not the static initializer, which Android 6 does not compile; not the library.
        self.assertEqual(["capture", "sizes"], [finding.method for finding in faults])
        self.assertEqual(["toBundle"], [finding.method for finding in others])
        self.assertEqual(
            "dev.mirror.repurpose.SystemLog.capture: 6 objects in one step", str(faults[0])
        )

    def test_code_without_the_instruction_has_no_findings(self):
        self.assertEqual([], dex_guard.findings("      name          : 'run'\n0000: new-array v0, v1, [Ljava/lang/String;\n"))


class MainTest(unittest.TestCase):
    def run_guard(self, disassembly):
        printed, errors = io.StringIO(), io.StringIO()
        with mock.patch.object(dex_guard, "disassemble", lambda _apk: disassembly):
            with contextlib.redirect_stdout(printed), contextlib.redirect_stderr(errors):
                code = dex_guard.main(["mirror-home-release.apk"])
        return code, printed.getvalue(), errors.getvalue()

    def test_a_fault_fails_and_says_where_and_what_to_do(self):
        code, printed, errors = self.run_guard(DISASSEMBLY)
        self.assertEqual(1, code)
        self.assertIn("mirror-home-release.apk: dev.mirror.repurpose.SystemLog.capture: 6 objects in one step", errors)
        self.assertIn("static final", errors)
        self.assertIn("in a library, not counted: androidx.media3.common.Format.toBundle", printed)

    def test_a_build_with_such_lists_only_in_initializers_and_libraries_passes(self):
        clean = DISASSEMBLY.replace("'capture'", "'<clinit>'").replace("'sizes'", "'<clinit>'")
        code, printed, errors = self.run_guard(clean)
        self.assertEqual(0, code)
        self.assertEqual("", errors)
        self.assertIn("nothing of Mirror Home's own", printed)

    def test_an_apk_that_cannot_be_read_is_an_error_and_not_a_pass(self):
        errors = io.StringIO()
        with mock.patch.object(dex_guard, "disassemble", mock.Mock(side_effect=dex_guard.GuardError("holds no code"))):
            with contextlib.redirect_stderr(errors):
                self.assertEqual(2, dex_guard.main(["empty.apk"]))
        self.assertIn("holds no code", errors.getvalue())


if __name__ == "__main__":
    unittest.main()
