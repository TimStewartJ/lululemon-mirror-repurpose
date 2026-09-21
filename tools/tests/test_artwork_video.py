import importlib.util
import json
import math
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

TOOLS = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))

HAS_DEPS = all(importlib.util.find_spec(name) for name in ("cv2", "numpy"))
if HAS_DEPS:
    import numpy as np
    import artwork_video as artwork


_WORKER_OFFSET = 0


def _dummy_init(offset):
    global _WORKER_OFFSET
    _WORKER_OFFSET = offset


def _dummy_frame(index):
    return index * 10 + _WORKER_OFFSET


@unittest.skipUnless(HAS_DEPS, "Optional artwork dependencies are not installed")
class ArtworkVideoTest(unittest.TestCase):
    def test_widget_zone_guard_clamps_and_smooths(self):
        rng = np.random.RandomState(3)
        frame = rng.randint(0, 255, size=(artwork.HEIGHT, artwork.WIDTH, 3)).astype(np.uint8)
        guarded, mean_luma = artwork.enforce_widget_zone(frame)
        x0, y0, x1, y1 = artwork.WIDGET_RECT
        region = guarded[y0:y1, x0:x1].astype(float) @ np.array([0.114, 0.587, 0.299])
        self.assertLessEqual(region.mean(), artwork.WIDGET_LUMA_MAX + 0.5)
        self.assertLessEqual(mean_luma, artwork.WIDGET_LUMA_MAX + 1e-6)
        self.assertLess(np.abs(np.diff(region, axis=1)).mean(), 0.5)

    def test_frame_metrics_classify_black_fog_and_bright(self):
        frame = np.zeros((10, 10, 3), dtype=np.uint8)
        frame[:2] = 20
        frame[2:3] = 200
        metrics = artwork.frame_metrics(frame)
        self.assertAlmostEqual(metrics["near_black"], 0.7)
        self.assertAlmostEqual(metrics["fog"], 0.2)
        self.assertAlmostEqual(metrics["bright"], 0.1)

    def test_encoding_is_capped_crf_inside_hardware_and_upload_envelope(self):
        command = artwork.build_ffmpeg_cmd("test.mp4")
        for key, expected in (("-preset", "slow"), ("-crf", "16"), ("-maxrate", "16000k"),
                              ("-bufsize", "20000k"), ("-refs", "3"), ("-bf", "2"),
                              ("-level:v", "4.1"), ("-pix_fmt", "yuv420p"), ("-colorspace", "bt709")):
            self.assertEqual(command[command.index(key) + 1], expected)
        self.assertIn("-n", command)
        self.assertNotIn("-y", command)
        self.assertNotIn("-b:v", command)
        self.assertLess(artwork.worst_case_upload_bytes(120) * 1.01, artwork.MAX_VIDEO_BYTES)
        self.assertGreater(artwork.worst_case_upload_bytes(135), artwork.MAX_VIDEO_BYTES)
        macroblocks = math.ceil(artwork.WIDTH / 16) * math.ceil(artwork.HEIGHT / 16)
        self.assertLessEqual(macroblocks, artwork.LEVEL_41_FRAME_MACROBLOCKS)
        self.assertLessEqual(macroblocks * artwork.FPS, artwork.LEVEL_41_MACROBLOCKS_PER_SECOND)
        # Every frame is held for a whole number of the panel's 60 Hz refreshes,
        # and a keyframe comes every two seconds.
        self.assertEqual(60 % artwork.FPS, 0)
        self.assertEqual(command[command.index("-framerate") + 1], str(artwork.FPS))
        self.assertEqual(command[command.index("-g") + 1], str(2 * artwork.FPS))

    def test_validation_accepts_expected_and_rejects_bad_metadata(self):
        stream = dict(codec_type="video", codec_name="h264", profile="High", level=41,
                      pix_fmt="yuv420p", width=1080, height=1920, avg_frame_rate=f"{artwork.FPS}/1",
                      nb_frames=str(120 * artwork.FPS), color_space="bt709", color_range="tv")
        info = dict(streams=[stream], format=dict(duration="120", size="180000000"))
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "test.mp4"
            path.write_bytes(b"test")
            with mock.patch.object(artwork.subprocess, "run") as run:
                run.return_value.stdout = json.dumps(info)
                report = artwork.validate_output(str(path), n_frames=120 * artwork.FPS)
                self.assertEqual(
                    report["sha256"],
                    "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
                )
            for field, value in (("width", 1920), ("level", 50), ("nb_frames", "1152"),
                                 ("avg_frame_rate", "60/1"), ("bit_rate", "21000000"),
                                 ("color_range", "pc")):
                with self.subTest(field=field):
                    bad = dict(info, streams=[dict(stream, **{field: value})])
                    with mock.patch.object(artwork.subprocess, "run") as run:
                        run.return_value.stdout = json.dumps(bad)
                        with self.assertRaises(ValueError):
                            artwork.validate_output(str(path), n_frames=120 * artwork.FPS)
            for changes in (dict(duration="48"), dict(size=str(257 * 1024 * 1024))):
                with self.subTest(changes=changes):
                    with mock.patch.object(artwork.subprocess, "run") as run:
                        run.return_value.stdout = json.dumps(dict(info, format=dict(info["format"], **changes)))
                        with self.assertRaises(ValueError):
                            artwork.validate_output(str(path), n_frames=120 * artwork.FPS)
            with mock.patch.object(artwork.subprocess, "run") as run:
                run.return_value.stdout = json.dumps(dict(info, streams=[stream, dict(codec_type="audio")]))
                with self.assertRaises(ValueError):
                    artwork.validate_output(str(path), n_frames=120 * artwork.FPS)

    def test_frame_pipeline_preserves_order_serially_and_in_parallel(self):
        indices = [7, 3, 11, 0, 5, 9, 1]
        expected = [(i, i * 10 + 4) for i in indices]
        self.assertEqual(list(artwork.iter_frames(indices, _dummy_init, _dummy_frame, (4,), workers=1)), expected)
        self.assertEqual(list(artwork.iter_frames(indices, _dummy_init, _dummy_frame, (4,), workers=2)), expected)

    def test_encode_refuses_a_budget_that_cannot_guarantee_the_upload_limit(self):
        spec = artwork.ArtworkSpec(description="", slug="budget", output_path=pathlib.Path("x.mp4"),
                                   n_frames=artwork.FPS * 180, worker_init=_dummy_init, worker_frame=_dummy_frame)
        with mock.patch.object(artwork.subprocess, "Popen") as popen:
            with self.assertRaises(ValueError):
                artwork.encode(spec, [0], pathlib.Path("x.mp4"), workers=1, full_loop=False)
            popen.assert_not_called()

    def test_a_film_may_name_its_own_worker_count(self):
        def spec(**extra):
            return artwork.ArtworkSpec(description="", slug="workers", output_path=pathlib.Path("x.mp4"),
                                       n_frames=artwork.FPS, worker_init=_dummy_init, worker_frame=_dummy_frame,
                                       **extra)

        seen = {}

        def previews(_spec, _indices, workers):
            seen["workers"] = workers
            return []

        with mock.patch.object(artwork, "write_previews", previews):
            artwork.run_cli(spec(default_workers=3), ["--frames", "0"])
            self.assertEqual(seen["workers"], 3)
            artwork.run_cli(spec(default_workers=3), ["--frames", "0", "--workers", "2"])
            self.assertEqual(seen["workers"], 2)
            artwork.run_cli(spec(), ["--frames", "0"])
            self.assertEqual(seen["workers"], artwork.default_workers())
        self.assertGreaterEqual(artwork.default_workers(), 1)

    def test_continuity_monitor_finds_pops_and_flashes_but_not_motion(self):
        height, width = 480, 360

        def scene(step, *, pop_from=None, flash_at=None):
            frame = np.zeros((height, width, 3), dtype=np.uint8)
            x = 20 + 9 * step                                   # a bright slab in steady, fast motion
            frame[60:180, x:x + 100] = 200
            if pop_from is not None and step >= pop_from:        # something switches on and stays
                frame[300:360, 120:180] = 160
            if flash_at is not None and step == flash_at:        # one bad frame
                frame[300:360, 240:300] = 160
            return frame

        steady = artwork.ContinuityMonitor(block=60)
        for step in range(24):
            steady.push(step, scene(step))
        self.assertLess(steady.report()["max_excess"], 8.0)

        popping = artwork.ContinuityMonitor(block=60)
        for step in range(24):
            popping.push(step, scene(step, pop_from=11, flash_at=17))
        report = popping.report()
        self.assertGreater(report["max_excess"], 100.0)
        found = {(e["kind"], e["frame"], e["x"], e["y"]) for e in report["worst"]}
        self.assertIn(("pop", 11, 120, 300), found)
        self.assertIn(("flash", 17, 240, 300), found)
        self.assertLessEqual(len(report["worst"]), popping.keep)
        self.assertEqual(report["worst"], sorted(report["worst"], key=lambda e: -e["excess"]))
        self.assertEqual(artwork.ContinuityMonitor().report(), dict(max_excess=0.0, worst=[]))

    def test_contact_sheet_tiles_every_frame(self):
        frames = [np.full((96, 54, 3), value, dtype=np.uint8) for value in (0, 80, 160)]
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "sheet.jpg"
            artwork.write_contact_sheet(frames, ["a", "b", "c"], path, columns=2, thumb=(27, 48))
            import cv2
            sheet = cv2.imread(str(path))
            self.assertEqual(sheet.shape[:2], (96, 54))


if __name__ == "__main__":
    unittest.main()
