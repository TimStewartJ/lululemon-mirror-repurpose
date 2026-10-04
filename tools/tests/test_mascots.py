import pathlib
import shutil
import sys
import tempfile
import unittest
from unittest import mock

TOOLS = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))

import mascots
from PIL import Image

INK = 0xF5F2EC


def lit(image):
    """How many pixels of a picture are not black."""
    return sum(image.convert("L").histogram()[41:])


class PaintTest(unittest.TestCase):
    def test_a_frame_without_shapes_is_plain_mirror(self):
        image = mascots.paint([], 60)
        self.assertEqual((60, 60), image.size)
        self.assertEqual(0, lit(image))

    def test_a_filled_shape_is_painted_where_its_points_are(self):
        # The right half of the box.
        image = mascots.paint([[INK, 1, 0, 1, 0, -1, 1, -1, 1, 1, 0, 1]], 60)
        self.assertEqual((0, 0, 0), image.getpixel((10, 30)))
        self.assertEqual((245, 242, 236), image.getpixel((45, 30)))

    def test_a_line_has_its_width_and_round_ends(self):
        image = mascots.paint([[INK, 1, 0.2, 0, -0.5, 0, 0.5, 0]], 100)
        self.assertGreater(image.getpixel((50, 50))[0], 200)
        self.assertGreater(image.getpixel((50, 47))[0], 200)
        self.assertEqual(0, image.getpixel((50, 30))[0])
        # Past its end by less than half its width: the round end.
        self.assertGreater(image.getpixel((79, 50))[0], 100)
        self.assertEqual(0, image.getpixel((90, 50))[0])

    def test_a_closed_line_joins_its_last_point_to_its_first(self):
        triangle = [INK, 1, 0.1, 1, -0.6, 0.6, 0.6, 0.6, 0, -0.6]
        closed = mascots.paint([triangle], 100)
        opened = mascots.paint([triangle[:3] + [0] + triangle[4:]], 100)
        self.assertGreater(lit(closed), lit(opened) * 1.2)
        self.assertEqual(0, closed.getpixel((50, 55))[0])

    def test_a_faint_shape_is_painted_faintly(self):
        image = mascots.paint([[INK, 0.2, 0, 1, -1, -1, 1, -1, 1, 1, -1, 1]], 40)
        self.assertAlmostEqual(49, image.getpixel((20, 20))[0], delta=3)

    def test_a_shape_that_reaches_past_the_box_is_cut_off_and_harms_nothing(self):
        image = mascots.paint([[INK, 1, 0.1, 0, 0.5, 0, 3, 0], [INK, 1, 0, 1, 2, 2, 3, 2, 3, 3]], 50)
        self.assertGreater(image.getpixel((48, 25))[0], 200)

    def test_the_colours_of_a_film_run_from_black_to_the_ink_and_to_the_blush(self):
        colors = mascots.palette().getpalette()
        self.assertEqual([0, 0, 0], colors[:3])
        self.assertIn(list(mascots.INK), [colors[index:index + 3] for index in range(0, 96, 3)])
        self.assertIn(list(mascots.BLUSH), [colors[index:index + 3] for index in range(0, 96, 3)])


class SceneTest(unittest.TestCase):
    def test_the_scene_is_made_of_steps_the_player_knows(self):
        for step, seconds, says, happening in mascots.SCENE:
            self.assertRegex(step, r"^(mood:[a-z]+|speak:[0-9.]+:[a-z]+|nod)$")
            self.assertGreater(seconds, 0)
            if step.startswith("mood:"):
                self.assertIn(step[5:], mascots.MOODS + ("hidden",))
            self.assertEqual(not says and not happening, step == "mood:hidden")
        # A GIF keeps time in hundredths of a second, so every length is whole frames at 20 a second.
        self.assertTrue(all(abs(seconds * 20 - round(seconds * 20)) < 1e-6 for _, seconds, _, _ in mascots.SCENE))

    def test_without_a_jdk_it_says_what_is_missing(self):
        with mock.patch.dict(mascots.os.environ, {"JAVA_HOME": ""}), mock.patch.object(mascots.shutil, "which", return_value=None):
            with self.assertRaisesRegex(mascots.MascotError, "javac was not found. Install a JDK"):
                mascots.jdk_tool("javac")
            with tempfile.TemporaryDirectory() as directory, mock.patch("sys.stderr"):
                self.assertEqual(1, mascots.main(["sheet", "--out", directory]))


@unittest.skipUnless(shutil.which("javac") and shutil.which("java"), "needs a JDK")
class PicturesTest(unittest.TestCase):
    """The whole way: the mascots' own code, compiled and played, and painted."""

    def test_every_mascot_acts_out_what_it_is_told(self):
        with tempfile.TemporaryDirectory() as directory:
            out = pathlib.Path(directory)
            film = mascots.play(["mood:listening", "run:0.5", "speak:0.4:idle", "nod", "run:0.5"], 10, out)
            self.assertEqual(10, film["fps"])
            self.assertGreaterEqual(len(film["mascots"]), 3)
            for mascot in film["mascots"]:
                self.assertEqual(10, len(mascot["frames"]), mascot["id"])
                self.assertRegex(mascot["id"], "^[a-z]+$")
                self.assertGreater(lit(mascots.paint(mascot["frames"][-1], 80)), 60, mascot["id"])
            self.assertFalse((out / "frames.json").exists())
            with self.assertRaisesRegex(mascots.MascotError, "could not be played"):
                mascots.play(["dance:1"], 10, out)

    def test_the_sheet_has_a_row_for_each_mascot_and_a_column_for_each_mood(self):
        with tempfile.TemporaryDirectory() as directory:
            path = mascots.sheet(pathlib.Path(directory), 40)
            with Image.open(path) as image:
                self.assertEqual(110 + 40 * len(mascots.MOODS), image.width)
                self.assertEqual(0, (image.height - 34) % 40)
                self.assertGreater(lit(image), 500)


if __name__ == "__main__":
    unittest.main()
