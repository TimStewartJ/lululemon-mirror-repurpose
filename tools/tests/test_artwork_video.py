import importlib.util
import io
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


@unittest.skipUnless(HAS_DEPS, "Optional artwork dependencies are not installed")
class ArtworkEncodingTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = pathlib.Path(directory.name)
        self.output = self.root / "film.mp4"
        self.previews = self.root / "previews"
        self.spec = artwork.ArtworkSpec(
            description="", slug="test", output_path=self.output, n_frames=30,
            worker_init=_dummy_init, worker_frame=_dummy_frame, width=64, height=64,
        )
        self.indices = list(range(self.spec.n_frames))
        frame = np.zeros((64, 64, 3), dtype=np.uint8)
        self.events = []
        self.encoder_code = 0
        self.broken_pipe = False
        self.popen = self.patch(artwork.subprocess, "Popen", side_effect=self.start_encoder)
        self.frames = self.patch(artwork, "iter_frames", return_value=[
            (index, (frame, 0.0)) for index in self.indices
        ])
        self.validate = self.patch(artwork, "validate_output", side_effect=self.validate_partial)
        self.seam = self.patch(artwork, "report_seam_metrics", side_effect=self.check_seam)
        self.patch(artwork, "PREVIEW_DIR", self.previews)

    def patch(self, target, name, *args, **kwargs):
        patcher = mock.patch.object(target, name, *args, **kwargs)
        result = patcher.start()
        self.addCleanup(patcher.stop)
        return result

    def start_encoder(self, command, **kwargs):
        self.partial = pathlib.Path(command[-1])
        self.assertNotEqual(self.partial, self.output)
        self.partial.write_bytes(b"encoded video")
        kwargs["stderr"].write(b"encoder diagnostic\n")
        self.process = mock.Mock()
        self.process.stdin = io.BytesIO()
        if self.broken_pipe:
            self.process.stdin = mock.Mock()
            self.process.stdin.write.side_effect = BrokenPipeError("encoder pipe closed")
            self.process.stdin.close.side_effect = BrokenPipeError("encoder pipe closed")

        def finish():
            self.events.append("encoder finished")
            return self.encoder_code

        self.process.wait.side_effect = finish
        return self.process

    def validate_partial(self, path, **kwargs):
        self.assertTrue(path.is_file())
        self.assertTrue(path.name.endswith(".partial.mp4"))
        self.assertFalse(self.output.exists())
        self.assertEqual(kwargs["n_frames"], len(self.indices))
        self.events.append("validation")
        return {"validated": True}

    def check_seam(self, path, n_frames):
        self.assertEqual(path, self.partial)
        self.assertEqual(n_frames, self.spec.n_frames)
        self.assertFalse(self.output.exists())
        self.events.append("seam")
        return {"wrap": 0.0}

    def encode(self, full_loop=False):
        return artwork.encode(self.spec, self.indices, self.output, workers=1, full_loop=full_loop)

    def assert_no_staging(self):
        self.assertEqual(list(self.root.glob(".film-*")), [])

    def assert_unpublished(self):
        self.assertFalse(self.output.exists())
        self.assertFalse(self.previews.exists())
        self.assert_no_staging()

    def test_publishes_only_after_validation_and_records_the_final_path(self):
        report = self.encode(full_loop=True)
        self.assertEqual(self.events, ["encoder finished", "validation", "seam"])
        self.assertEqual(self.output.read_bytes(), b"encoded video")
        self.assertEqual(report["path"], str(self.output))
        stored = json.loads((self.previews / "film-report.json").read_text())
        self.assertEqual(stored, report)
        self.assertTrue(self.process.stdin.closed)
        self.assert_no_staging()

    def test_renderer_failure_does_not_publish(self):
        self.frames.side_effect = RuntimeError("renderer failed")
        with self.assertRaisesRegex(RuntimeError, "renderer failed"):
            self.encode()
        self.process.wait.assert_called_once()
        self.assertTrue(self.process.stdin.closed)
        self.assert_unpublished()

    def test_interrupted_render_does_not_publish(self):
        def interrupted(*_args, **_kwargs):
            yield self.frames.return_value[0]
            raise KeyboardInterrupt()

        self.frames.side_effect = interrupted
        with self.assertRaises(KeyboardInterrupt):
            self.encode()
        self.process.wait.assert_called_once()
        self.assertTrue(self.process.stdin.closed)
        self.assert_unpublished()

    def test_failed_process_start_cleans_staging(self):
        self.popen.side_effect = OSError("ffmpeg unavailable")
        with self.assertRaisesRegex(OSError, "ffmpeg unavailable"):
            self.encode()
        self.assert_unpublished()

    def test_failed_encoder_retains_diagnostics(self):
        self.encoder_code = 1
        with self.assertRaisesRegex(RuntimeError, "ffmpeg exited with code 1") as raised:
            self.encode()
        self.assertIn("encoder diagnostic", str(raised.exception))
        self.validate.assert_not_called()
        self.assert_unpublished()

    def test_broken_pipe_retains_encoder_diagnostics_and_reaps_process(self):
        self.broken_pipe = True
        self.encoder_code = 2
        with self.assertRaisesRegex(RuntimeError, "ffmpeg exited with code 2") as raised:
            self.encode()
        self.assertIn("encoder diagnostic", str(raised.exception))
        self.assertIsInstance(raised.exception.__cause__, BrokenPipeError)
        self.process.wait.assert_called_once()
        self.assert_unpublished()

    def test_broken_pipe_is_not_ignored_even_after_successful_encoder_exit(self):
        self.broken_pipe = True
        with self.assertRaises(BrokenPipeError):
            self.encode()
        self.validate.assert_not_called()
        self.assert_unpublished()

    def test_widget_ceiling_failure_does_not_publish(self):
        self.frames.return_value = [
            (index, (frame, artwork.WIDGET_LUMA_MAX + 1.0))
            for index, (frame, _) in self.frames.return_value
        ]
        with self.assertRaisesRegex(ValueError, "Widget zone exceeded"):
            self.encode()
        self.validate.assert_not_called()
        self.assert_unpublished()

    def test_metadata_validation_failure_does_not_publish(self):
        self.validate.side_effect = ValueError("invalid metadata")
        with self.assertRaisesRegex(ValueError, "invalid metadata"):
            self.encode()
        self.seam.assert_not_called()
        self.assert_unpublished()

    def test_seam_failure_does_not_publish(self):
        self.seam.side_effect = ValueError("invalid seam")
        with self.assertRaisesRegex(ValueError, "invalid seam"):
            self.encode(full_loop=True)
        self.assert_unpublished()

    def test_existing_output_is_never_overwritten(self):
        self.output.write_bytes(b"original")
        with self.assertRaises(FileExistsError):
            self.encode()
        self.assertEqual(self.output.read_bytes(), b"original")
        self.popen.assert_not_called()
        self.assert_no_staging()

    def test_output_created_during_rendering_is_never_overwritten(self):
        def racing_destination(path, **kwargs):
            report = self.validate_partial(path, **kwargs)
            self.output.write_bytes(b"another render won")
            return report

        self.validate.side_effect = racing_destination
        with self.assertRaises(FileExistsError):
            self.encode()
        self.assertEqual(self.output.read_bytes(), b"another render won")
        self.assertFalse(self.previews.exists())
        self.assert_no_staging()

    def test_publication_failure_is_explicit_and_does_not_publish(self):
        with mock.patch.object(artwork.os, "link", side_effect=OSError("hard links unsupported")):
            with self.assertRaisesRegex(OSError, "hard links unsupported"):
                self.encode()
        self.assert_unpublished()

    def test_failed_render_does_not_block_retry(self):
        self.validate.side_effect = ValueError("invalid metadata")
        with self.assertRaises(ValueError):
            self.encode()
        self.assert_unpublished()
        self.validate.side_effect = self.validate_partial
        self.encode()
        self.assertEqual(self.output.read_bytes(), b"encoded video")
        self.assert_no_staging()


if __name__ == "__main__":
    unittest.main()
