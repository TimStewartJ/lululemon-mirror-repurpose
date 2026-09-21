import importlib.util
import math
import pathlib
import sys
import unittest
from unittest import mock

TOOLS = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))

HAS_RENDER_DEPS = all(importlib.util.find_spec(name) for name in ("moderngl", "cv2", "numpy"))
if HAS_RENDER_DEPS:
    import numpy as np
    import artwork_gl
    import artwork_video as artwork
    import render_seasonal_video as video

    HAS_RENDER_DEPS = artwork_gl.gl_available()


@unittest.skipUnless(HAS_RENDER_DEPS, "The GPU artwork renderer needs moderngl, OpenCV and an OpenGL 4.3 device")
class SeasonalVideoTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.renderer = video.FrameRenderer()
        cls.tree = cls.renderer.tree
        cls.foliage = cls.renderer.foliage

    @classmethod
    def tearDownClass(cls):
        cls.renderer.stage.release()

    def frame(self, index):
        return self.renderer.render(index)[0]

    # -- the finished picture ------------------------------------------------

    def test_unstable_renderer_fails_and_releases_its_context(self):
        release_context = artwork_gl.Stage.release
        with mock.patch.object(artwork_gl, "settle", return_value=False) as settle, \
                mock.patch.object(artwork_gl.Stage, "release", autospec=True,
                                  side_effect=release_context) as release:
            with self.assertRaisesRegex(RuntimeError, "four-seasons renderer did not settle"):
                video.FrameRenderer()
        settle.assert_called_once()
        release.assert_called_once_with(settle.call_args.args[0].__self__.stage)

    def test_full_frame_loop_is_pixel_exact_and_deterministic(self):
        first = self.frame(0)
        np.testing.assert_array_equal(first, self.frame(video.N_FRAMES))
        other = video.FrameRenderer()
        try:
            np.testing.assert_array_equal(first, other.render(0)[0])
        finally:
            other.stage.release()
        np.testing.assert_array_equal(first, self.frame(0))
        before, after = self.frame(video.N_FRAMES - 1), self.frame(1)
        wrap = np.abs(first.astype(float) - before).mean()
        adjacent = np.abs(first.astype(float) - after).mean()
        self.assertLess(wrap, adjacent * 1.5 + 0.1)

    def test_representative_frames_keep_widget_zone_dark_and_smooth(self):
        for index in range(0, video.N_FRAMES, video.N_FRAMES // 12):
            frame, widget_luma = self.renderer.render(index)
            self.assertEqual(frame.shape, (video.HEIGHT, video.WIDTH, 3))
            x0, y0, x1, y1 = artwork.WIDGET_RECT
            luma = frame[y0:y1, x0:x1].astype(float) @ np.array([0.114, 0.587, 0.299])
            self.assertLessEqual(luma.mean(), artwork.WIDGET_LUMA_MAX, index)
            self.assertLessEqual(widget_luma, artwork.WIDGET_LUMA_MAX, index)
            self.assertLess(np.abs(np.diff(luma, axis=1)).mean(), 0.5, index)

    def test_stage_stays_a_mirror_while_every_season_glows(self):
        hues = []
        for season in range(4):
            index = int((season + 0.5) * video.SEASON_LEN * video.FPS)
            frame = self.frame(index)
            metrics = artwork.frame_metrics(frame)
            with self.subTest(season=season, **{k: round(v, 3) for k, v in metrics.items()}):
                self.assertGreaterEqual(metrics["near_black"], 0.55)
                self.assertLessEqual(metrics["fog"], 0.10)
                self.assertGreaterEqual(metrics["bright"], 0.08)
            lit = frame[artwork.luma(frame) > 60.0].astype(float).mean(axis=0)
            hues.append(lit / lit.sum())
        spring, summer, autumn, winter = hues                      # BGR shares of the lit picture
        self.assertGreater(spring[2], spring[1])                   # blossom: more red than green
        self.assertGreater(summer[1], summer[2])                   # leaves: green
        self.assertGreater(autumn[2], autumn[0] * 2.0)             # embers: red far above blue
        self.assertGreater(winter[0], autumn[0] * 1.5)             # frost: blue returns

    # -- the tree ------------------------------------------------------------------

    def test_tree_is_one_rooted_weeping_crown(self):
        tree = self.tree
        self.assertGreater(len(tree.pos), 5000)
        self.assertEqual(tree.parent[0], -1)
        self.assertTrue(np.all(tree.parent[1:] < np.arange(1, len(tree.pos))))      # parents come first
        self.assertTrue(np.all(tree.parent[1:] >= 0))
        strands = tree.kind == 1
        self.assertGreater(strands.sum(), tree.scaffold_count)
        self.assertGreater(tree.pos[:, 1].max(), 5.0)
        self.assertGreater(tree.pos[strands, 1].min(), video.WATER_Y)               # tips never touch the water
        self.assertLess(np.median(tree.pos[strands, 1]), np.median(tree.pos[~strands, 1]))   # strands hang
        # Pipe model: no limb is thicker than the one that carries it.
        node = np.arange(1, tree.scaffold_count)
        self.assertTrue(np.all(tree.radius[node] <= tree.radius[tree.parent[node]] * 1.001 + 1e-9))
        self.assertAlmostEqual(tree.radius[0], video.TRUNK_RADIUS * (1.0 + 0.55), places=6)
        # Wind carries the hanging tips furthest and leaves the trunk alone.
        self.assertEqual(tree.flex[0], 0.0)
        self.assertGreater(tree.flex[strands].mean(), 5.0 * tree.flex[~strands].mean())

    def test_tube_mesh_doubles_the_seam_so_the_bark_wraps_unbroken(self):
        verts, indices = video.tube_mesh(self.tree)
        self.assertEqual(verts.shape[1], 13)
        self.assertEqual(len(indices) % 3, 0)
        self.assertLess(indices.max(), len(verts))
        self.assertTrue(np.isfinite(verts).all())
        around = verts[:, 12]
        self.assertEqual(around.min(), 0.0)
        self.assertEqual(around.max(), 1.0)
        # Within every triangle the position around the ring moves by one step at most:
        # no triangle spans the jump from the end of the ring back to its start.
        tri = around[indices.reshape(-1, 3)]
        self.assertLessEqual((tri.max(axis=1) - tri.min(axis=1)).max(), 0.25 + 1e-6)
        # The doubled vertices coincide in space.
        first, last = verts[around == 0.0], verts[around == 1.0]
        self.assertEqual(len(first), len(last))
        np.testing.assert_allclose(first[:, 0:6], last[:, 0:6], atol=1e-5)
        normals = np.linalg.norm(verts[:, 6:9], axis=1)
        np.testing.assert_allclose(normals, 1.0, atol=1e-4)

    # -- time ----------------------------------------------------------------------

    def test_camera_makes_one_level_orbit_per_loop(self):
        self.assertAlmostEqual(video.orbit_angle(video.DURATION_S - 1e-9) - video.orbit_angle(0.0), 2 * math.pi, places=6)
        eye0, view0, proj0 = video.camera_matrices(0.0)
        eye1, view1, _ = video.camera_matrices(video.DURATION_S)
        np.testing.assert_allclose(eye0, eye1, atol=1e-9)
        np.testing.assert_allclose(view0, view1, atol=1e-9)
        angles = []
        for t in np.linspace(0.0, video.DURATION_S, 49)[:-1]:
            eye, view, _ = video.camera_matrices(t)
            self.assertAlmostEqual(np.hypot(eye[0], eye[2]), video.ORBIT_RADIUS, delta=0.6)
            self.assertAlmostEqual(view[1, 1], 1.0, places=9)          # level: verticals stay vertical
            angles.append(math.atan2(eye[0], eye[2]))
        steps = np.diff(np.unwrap(angles))
        self.assertTrue(np.all(steps > 0))
        np.testing.assert_allclose(steps, steps.mean(), rtol=1e-6)
        # Velocity is continuous across the seam.
        delta = 1e-3
        left = (eye0 - video.camera_matrices(video.DURATION_S - delta)[0]) / delta
        right = (video.camera_matrices(delta)[0] - eye0) / delta
        np.testing.assert_allclose(left, right, atol=1e-3)
        # The shifted lens puts the horizon below the middle of the glass.
        self.assertEqual(proj0[1, 2], -video.LENS_SHIFT_Y)

    def test_seasons_take_turns(self):
        self.assertEqual(video.SEASON_LEN, 30.0)
        for season in range(4):
            weights = video.season_weights((season + 0.5) * video.SEASON_LEN)
            self.assertEqual(weights[season], 1.0)
        for t in np.linspace(0.0, video.DURATION_S, 97):
            self.assertAlmostEqual(video.season_weights(t).sum(), 1.0, places=12)

    def test_wind_and_particles_close_the_loop(self):
        rest = self.tree.pos[::50]
        flex = self.tree.flex[::50]
        gust = np.full(len(rest), 1.0)
        np.testing.assert_allclose(video.sway_np(rest, flex, 0.0, gust),
                                   video.sway_np(rest, flex, video.DURATION_S, gust), atol=1e-9)
        self.assertEqual(np.abs(video.sway_np(rest[:1] * 0.0, np.zeros(1), 7.0, gust[:1])).max(), 0.0)
        np.testing.assert_allclose(video.gust_level(0.0), video.gust_level(video.DURATION_S), atol=1e-12)
        self.assertGreater(video.gust_level(25.5), 2.0 * video.gust_level(5.0))
        p = self.renderer.particles
        for cycles in (p.snow_cycles, p.snow_sway_cycles, p.fly_cycles, p.fly_cycles2, p.fly_blink,
                       p.mote_cycles, p.mote_twinkle, p.frost_cycles):
            np.testing.assert_array_equal(cycles, np.round(cycles))
            self.assertTrue(np.all(cycles >= 1))
        end = video.DURATION_S
        np.testing.assert_allclose(p.snow(0.0, 1.0)[0], p.snow(end, 1.0)[0], atol=1e-9)
        np.testing.assert_allclose(p.fireflies(0.0, 1.0), p.fireflies(end, 1.0), atol=1e-9)
        np.testing.assert_allclose(p.motes(0.0, (1.0, 1.0, 1.0), 1.0), p.motes(end, (1.0, 1.0, 1.0), 1.0), atol=1e-9)
        np.testing.assert_allclose(p.frost(0.0, 1.0, 1.0), p.frost(end, 1.0, 1.0), atol=1e-9)

    # -- foliage -------------------------------------------------------------------

    def test_every_blossom_and_leaf_carries_a_whole_year(self):
        petals, leaves = self.foliage.petals, self.foliage.leaves
        self.assertEqual(petals.shape[1], video.INSTANCE_FLOATS)
        self.assertEqual(leaves.shape[1], video.INSTANCE_FLOATS)
        self.assertEqual(len(petals) % 5, 0)                                  # five petals to a blossom
        self.assertGreater(len(petals), 10000)
        self.assertGreater(len(leaves), 5000)
        for data in (petals, leaves):
            self.assertTrue(np.isfinite(data).all())
            np.testing.assert_allclose(np.linalg.norm(data[:, 4:8], axis=1), 1.0, atol=1e-5)
            for column in (12, 13, 14):                                          # bud, open, release
                self.assertTrue(np.all((data[:, column] >= 0.0) & (data[:, column] < video.DURATION_S)))
            self.assertTrue(np.all(data[:, 16] > 0.0))                            # everything falls
        # Blossoms are torn away in the spring gusts, leaves in the autumn ones.
        spring = np.abs(video.cyclic(petals[:, 14] - 25.0)) < 16.0
        autumn = np.abs(video.cyclic(leaves[:, 14] - 85.0)) < 16.0
        self.assertGreater(spring.mean(), 0.97)
        self.assertGreater(autumn.mean(), 0.97)
        for peaks, data in ((video.PETAL_GUSTS, petals), (video.LEAF_GUSTS, leaves)):
            when = max(peaks, key=lambda peak: peak[1])[0]
            in_gust = (np.abs(video.cyclic(data[:, 14] - when)) < 2.0).mean()
            calm = (np.abs(video.cyclic(data[:, 14] - when + 5.5)) < 2.0).mean()
            self.assertGreater(in_gust, 1.5 * calm)
        # A leaf turns before it lets go.
        self.assertTrue(np.all(video.cyclic(leaves[:, 14] - leaves[:, 26]) >= 4.5 - 1e-3))

    def test_landings_ripple_where_and_when_foliage_meets_the_water(self):
        for data, offset in ((self.foliage.petals, 0.04), (self.foliage.leaves, 0.11)):
            xz, when = video.landings(data, offset)
            self.assertEqual(xz.shape, (len(data), 2))
            self.assertTrue(np.isfinite(xz).all())
            self.assertTrue(np.all((when >= 0.0) & (when < video.DURATION_S)))
            fall = video.cyclic(when - data[:, 14])
            self.assertTrue(np.all(fall > video.FALL_TAU))
            self.assertLess(fall.max(), 25.0)
        events = self.renderer.ripple_events
        self.assertGreater(len(events), 100)
        self.assertLess(np.hypot(events[:, 0], events[:, 1]).max(), 9.0)
        np.testing.assert_allclose(self.renderer.ripples(3.0), self.renderer.ripples(3.0 + video.DURATION_S), atol=1e-9)

    def test_every_ring_that_starts_gets_to_spread_and_fade(self):
        # Hundreds of petals land every second of the storm. The water shows a
        # bounded number of rings, none of which is pushed out by the next.
        events = self.renderer.ripple_events
        when = np.sort(events[:, 2])
        gaps = np.diff(np.concatenate([when, when[:1] + video.DURATION_S]))
        self.assertGreaterEqual(gaps.min(), video.RIPPLE_LIFE_S / video.RIPPLE_CAP - 1e-9)
        rows = np.array([[0.0, 0.0, 1.00, 1.0], [1.0, 0.0, 1.01, 1.0], [2.0, 0.0, 1.50, 1.0],
                         [3.0, 0.0, video.DURATION_S - 0.2, 1.0], [4.0, 0.0, 0.25, 1.0]])
        kept = video.thin_events(rows, 0.5)
        np.testing.assert_array_equal(kept[:, 0], [4.0, 0.0, 2.0])      # too close across the seam counts too
        p = self.renderer.particles
        most = 0
        for t in np.arange(0.0, video.DURATION_S, 0.25):
            _, xz, frac = p.snow(t, 1.0)
            rows = self.renderer.ripples(t, xz, frac, 1.0)
            most = max(most, len(rows))
            self.assertTrue(np.all(np.diff(rows[:, 2]) >= 0))
        self.assertGreater(most, 40)
        self.assertLess(most, video.MAX_RIPPLES)
        # A snowflake's ring has faded out by the time the flake sets out again.
        _, xz, frac = p.snow(100.0, 1.0)
        rows = self.renderer.ripples(100.0, xz, frac, 1.0)
        snow_rings = rows[rows[:, 3] <= 0.35 + 1e-9]
        self.assertGreater(len(snow_rings), 0)
        ending = (frac < video.SNOW_RING) & (frac > 0.97 * video.SNOW_RING)
        if ending.any():
            self.assertLess(snow_rings[:, 3].min(), 0.01)

    def test_output_and_encoding_envelope(self):
        self.assertEqual(video.OUTPUT_PATH.name, "four-seasons-spatial-120s.mp4")
        self.assertEqual(video.N_FRAMES, 120 * artwork.FPS)
        self.assertEqual(video.SPEC.duration_s, 120.0)
        self.assertLess(artwork.worst_case_upload_bytes(video.DURATION_S) * 1.01, artwork.MAX_VIDEO_BYTES)
        self.assertEqual(video.SPEC.n_frames, video.N_FRAMES)
        self.assertIs(video.SPEC.worker_frame, video.worker_frame)
        self.assertGreaterEqual(video.SUBFRAMES, 2)
        self.assertTrue(0.0 < video.SHUTTER <= 1.0)
        self.assertGreaterEqual(video.MARGIN, math.ceil(2.6 * max(video.DOF_NEAR_SIGMAS + video.DOF_FAR_SIGMAS)))


if __name__ == "__main__":
    unittest.main()
