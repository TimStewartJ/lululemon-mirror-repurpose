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
    import render_luminous_flowers as flowers

    HAS_RENDER_DEPS = artwork_gl.gl_available()


@unittest.skipUnless(HAS_RENDER_DEPS, "The GPU artwork renderer needs moderngl, OpenCV and an OpenGL 4.3 device")
class LuminousFlowersTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.renderer = flowers.FrameRenderer()
        cls.scene = cls.renderer.scene

    @classmethod
    def tearDownClass(cls):
        cls.renderer.stage.release()

    def frame(self, index):
        return self.renderer.render(index)[0]

    def stream(self, t):
        _, view, _ = flowers.camera_matrices(t)
        return self.renderer.stream(t, view)

    # -- the finished picture ------------------------------------------------

    def test_unstable_renderer_fails_and_releases_its_context(self):
        release_context = artwork_gl.Stage.release
        with mock.patch.object(artwork_gl, "settle", return_value=False) as settle, \
                mock.patch.object(artwork_gl.Stage, "release", autospec=True,
                                  side_effect=release_context) as release:
            with self.assertRaisesRegex(RuntimeError, "luminous-flowers renderer did not settle"):
                flowers.FrameRenderer()
        settle.assert_called_once()
        release.assert_called_once_with(settle.call_args.args[0].__self__.stage)

    def test_full_frame_loop_is_pixel_exact_and_deterministic(self):
        first = self.frame(0)
        np.testing.assert_array_equal(first, self.frame(flowers.N_FRAMES))
        np.testing.assert_array_equal(first, self.frame(0))
        other = flowers.FrameRenderer()
        try:
            np.testing.assert_array_equal(first, other.render(0)[0])
        finally:
            other.stage.release()
        np.testing.assert_array_equal(first, self.frame(0))
        before, after = self.frame(flowers.N_FRAMES - 1), self.frame(1)
        wrap = np.abs(first.astype(float) - before).mean()
        adjacent = np.abs(first.astype(float) - after).mean()
        self.assertLess(wrap, adjacent * 1.5 + 0.1)

    def test_representative_frames_keep_widget_zone_dark_and_smooth(self):
        for index in range(0, flowers.N_FRAMES, flowers.N_FRAMES // 12):
            frame, widget_luma = self.renderer.render(index)
            self.assertEqual(frame.shape, (flowers.HEIGHT, flowers.WIDTH, 3))
            x0, y0, x1, y1 = artwork.WIDGET_RECT
            luma = frame[y0:y1, x0:x1].astype(float) @ np.array([0.114, 0.587, 0.299])
            self.assertLessEqual(luma.mean(), artwork.WIDGET_LUMA_MAX, index)
            self.assertLessEqual(widget_luma, artwork.WIDGET_LUMA_MAX, index)
            self.assertLess(np.abs(np.diff(luma, axis=1)).mean(), 0.5, index)

    def test_stage_stays_a_mirror_while_flowers_glow_in_every_season(self):
        for season in range(4):
            index = int((season + 0.5) * flowers.SEASON_LEN * flowers.FPS)
            metrics = artwork.frame_metrics(self.frame(index))
            with self.subTest(season=season, **{k: round(v, 3) for k, v in metrics.items()}):
                self.assertGreaterEqual(metrics["near_black"], 0.55)
                self.assertLessEqual(metrics["fog"], 0.10)
                self.assertGreaterEqual(metrics["bright"], 0.08)

    def test_nothing_pops_where_it_used_to(self):
        # No patch of the picture may change far more in one step than in the
        # steps on either side of it. These two seconds once held the worst pops:
        # a released petal handing itself over to a layer of its own (it kept
        # hiding the petal behind it to the last, then let go of it at once), and
        # a blurred petal drifting in over the edge of the frame.
        for start_s in (118.5, 131.2):
            monitor = artwork.ContinuityMonitor(keep=3)
            start = int(start_s * flowers.FPS)
            for index in range(start, start + 30):
                monitor.push(index, self.frame(index))
            self.assertLess(monitor.report()["max_excess"], 5.0, monitor.report())

    # -- the scene -------------------------------------------------------------

    def test_slots_and_motes_close_the_loop(self):
        f = self.scene.flowers
        self.assertEqual(sum(len(slot["cycles"]) for slot in self.scene.slots), self.scene.n_flowers)
        for slot in self.scene.slots:
            loops = flowers.DURATION_S / slot["period"]
            self.assertAlmostEqual(loops, round(loops), places=9)
            self.assertEqual(len(slot["cycles"]), round(loops))
            births = np.sort(f["birth"][slot["cycles"]])
            gaps = np.diff(np.concatenate([births, births[:1] + flowers.DURATION_S]))
            np.testing.assert_allclose(gaps, slot["period"], atol=1e-9)
        s = self.scene
        for cycles in (s.mote_loops, s.mote_wander_cycles, s.mote_twinkle):
            np.testing.assert_array_equal(cycles, np.round(cycles))
        np.testing.assert_allclose(self.renderer.motes(0.0), self.renderer.motes(flowers.DURATION_S), atol=1e-9)

    def test_each_life_fits_its_slot_so_generations_never_collide(self):
        f, p = self.scene.flowers, self.scene.petals
        self.assertTrue(np.all(f["scatter_start"] + f["scatter_len"] + flowers.AFTERGLOW_S < f["period"] - 1.0))
        self.assertTrue(np.all(f["u_open"] + f["t_open"] < f["scatter_start"]))
        fi = p["flower"]
        self.assertTrue(np.all(p["release"] >= f["scatter_start"][fi] - 1e-9))
        self.assertTrue(np.all(p["release"] <= (f["scatter_start"] + f["scatter_len"])[fi] + 1e-9))
        self.assertTrue(np.all(p["release"] + p["life"] < flowers.DURATION_S))

    def test_petals_release_exactly_where_they_bloomed(self):
        p = self.scene.petals
        pi = np.arange(0, self.scene.n_petals, 7)
        release, eps = p["release"][pi], 1e-4
        rotation0, base0, shape0 = flowers.attached_pose(self.scene, pi, release - eps)
        rotation1, base1, shape1, s = flowers.released_pose(self.scene, pi, release + eps)
        self.assertLess(np.abs(base1 - base0).max(), 1e-3)
        self.assertLess(np.abs(rotation1 - rotation0).max(), 1e-3)
        for key in shape0:
            np.testing.assert_allclose(shape1[key], shape0[key], atol=1e-3)
        self.assertLess(s.max(), 1e-4)
        # ... and from rest: a moment later they have hardly moved.
        _, base2, _, _ = flowers.released_pose(self.scene, pi, release + 0.05)
        self.assertLess(np.abs(base2 - base1).max(), 0.05)

    def test_a_released_petal_brightens_from_rest_and_lifts_gradually(self):
        p = self.scene.petals
        index = int(np.argmax(p["release"] > 20.0))
        flower = int(p["flower"][index])
        birth, release = self.scene.flowers["birth"][flower], p["release"][index]
        levels, lifts = [], []
        for dt in (-0.05, 1e-4, 0.05, 0.6, flowers.LIFT_S + 0.1):
            t = (birth + release + dt) % flowers.DURATION_S
            _, view, _ = flowers.camera_matrices(t)
            rows, _, _, indices, lift = self.renderer.petal_instances(t, view)
            k = int(np.nonzero(indices == index)[0][0])
            levels.append(float(rows[k, 31]))
            lifts.append(float(lift[k]))
        self.assertAlmostEqual(levels[1] / levels[0], 1.0, delta=0.01)
        self.assertGreater(levels[3], levels[1] * 1.2)
        self.assertEqual(lifts[0], 0.0)
        self.assertLess(lifts[1], 1e-6)
        self.assertTrue(0.0 < lifts[2] < lifts[3] < 1.0)
        self.assertEqual(lifts[4], 1.0)

    def test_flowers_clear_the_widget_corner(self):
        f = self.scene.flowers
        pos, radius = f["bloom_pos"], f["radius"]
        dist = -pos[:, 2]
        sx = pos[:, 0] / (2.0 * flowers.TAN_HALF * flowers.ASPECT * dist) + 0.5
        sy = 0.5 - pos[:, 1] / (2.0 * flowers.TAN_HALF * dist)
        margin = radius / (2.0 * flowers.TAN_HALF * dist)
        inside = (sx - margin / flowers.ASPECT < flowers.QUIET_X) & (sy - margin < flowers.QUIET_Y)
        self.assertFalse(inside.any(), np.nonzero(inside)[0])

    def test_species_and_palettes_follow_the_season_of_birth(self):
        f = self.scene.flowers
        seen = set()
        for i in range(self.scene.n_flowers):
            season = int(f["season"][i])
            self.assertEqual(season, flowers.season_of(f["birth"][i] + 8.0))
            self.assertIn(flowers.SPECIES_NAMES[int(f["species"][i])], dict(flowers.SPECIES_BY_SEASON[season]))
            seen.add(season)
        self.assertEqual(seen, {0, 1, 2, 3})
        self.assertGreaterEqual(len(set(f["species"].astype(int))), 8)

    def test_depth_bands_layer_the_field_around_the_focal_plane(self):
        f = self.scene.flowers
        names = [band[0] for band in flowers.BANDS]
        self.assertEqual(names, ["far", "mid", "hero"])
        dist = -f["bloom_pos"][:, 2]
        far, mid, hero = (dist[f["band"] == k] for k in range(3))
        self.assertGreater(far.min(), mid.max())
        self.assertGreater(mid.min(), hero.max())
        self.assertGreaterEqual(hero.min(), flowers.FOCUS - 1e-9)
        self.assertLess(hero.max(), flowers.FOCUS + 2.0)

    # -- one ordered stream: the reason nothing pops ------------------------------

    def test_drawing_order_never_changes_between_frames(self):
        for t in (12.0, 61.3, 95.0, 118.9, 171.4):
            a, b = self.stream(t), self.stream(t + 1.0 / flowers.FPS)
            keys = []
            for s in (a, b):
                keys.append([(int(fl), int(pl), bool(fr)) for fl, pl, fr in zip(s["flower"], s["place"], s["free"])])
                self.assertEqual(len(set(keys[-1])), len(keys[-1]))
            common = set(keys[0]) & set(keys[1])
            self.assertGreater(len(common), 500)
            self.assertEqual([k for k in keys[0] if k in common], [k for k in keys[1] if k in common], t)

    def test_every_flower_owns_a_layer_and_free_petals_leave_it(self):
        s = self.stream(95.0)
        rows, flower, free = s["rows"], s["flower"], s["free"]
        layer = rows[:, 37]
        self.assertTrue(np.all(np.diff(layer) >= 0))
        self.assertEqual(int(layer[-1]) + 1, s["layers"])
        within = ~free
        for fl in np.unique(flower[within])[:40]:
            self.assertEqual(len(set(layer[within & (flower == fl)])), 1)
        # A free copy has a layer to itself, in front of the flower it left.
        free_layers = layer[free]
        self.assertEqual(len(set(free_layers)), len(free_layers))
        self.assertTrue(free.any())
        k = int(np.nonzero(free)[0][0])
        home = layer[within & (flower == flower[k])]
        if len(home):
            self.assertGreater(layer[k], home[0])
        # Depth is measured in a window around the owner of the layer.
        np.testing.assert_allclose(rows[within, 40], s["home"][within], rtol=1e-6)
        np.testing.assert_allclose(rows[free, 40], s["dist"][free], rtol=1e-6)

    def test_flowers_keep_one_depth_order_because_they_drift_together(self):
        f = self.scene.flowers

        def depth(t):
            age = (t - f["birth"]) % flowers.DURATION_S
            return f["bloom_pos"][:, 2] + flowers.DRIFT[2] * (age - f["u_mid"]), age

        (d0, age0), (d1, age1) = depth(40.0), depth(47.0)
        alive = (age0 < age1)                      # not reborn in between
        order0, order1 = np.argsort(d0[alive], kind="stable"), np.argsort(d1[alive], kind="stable")
        np.testing.assert_array_equal(order0, order1)

    def test_a_lifting_petal_hands_its_light_over_without_gain_or_loss(self):
        s = self.stream(95.0)
        weights, covered = self.renderer.slab_light(s)
        np.testing.assert_allclose(weights[~s["free"]].sum(axis=1), 1.0, atol=1e-9)
        np.testing.assert_allclose(weights[s["free"]].sum(axis=1), s["lift"][s["free"]], atol=1e-9)
        self.assertTrue(np.all((covered >= -1e-12) & (covered <= 1.0 + 1e-9)))
        lifting = (s["lift"] > 0.05) & (s["lift"] < 0.95)
        names = {}
        for k in np.nonzero(lifting)[0]:
            names.setdefault((int(s["flower"][k]), int(s["place"][k])), []).append(int(k))
        pairs = [v for v in names.values() if len(v) == 2]
        self.assertTrue(pairs)
        for a, b in pairs:
            within, free = (a, b) if not s["free"][a] else (b, a)
            lift = s["lift"][within]
            self.assertEqual(s["lift"][free], lift)
            # The copy left in the flower gives up its samples as the free copy gathers light.
            self.assertAlmostEqual(float(s["rows"][within, 43]), 1.0 - lift, places=5)
            self.assertAlmostEqual(float(s["rows"][free, 43]), 1.0, places=6)
            self.assertAlmostEqual(float(s["rows"][within, 43]) + weights[free].sum(), 1.0, places=5)
            # All of the free copy lies over the one in the flower, wherever both are drawn.
            self.assertGreaterEqual(covered[within].max() + 1e-9, lift * weights[within].max())
        # Everything in a flower's layer is drawn in the same slabs, so what hides
        # what never depends on which slab is looked at.
        for fl in np.unique(s["flower"][~s["free"]])[:40]:
            members = weights[(~s["free"]) & (s["flower"] == fl)]
            np.testing.assert_allclose(members, members[:1].repeat(len(members), axis=0), atol=1e-12)

    def test_shutter_opens_inside_the_frame_it_belongs_to(self):
        self.assertGreaterEqual(flowers.SUBFRAMES, 2)
        self.assertTrue(0.0 < flowers.SHUTTER <= 1.0)
        offsets = [((sub + 0.5) / flowers.SUBFRAMES - 0.5) * flowers.SHUTTER for sub in range(flowers.SUBFRAMES)]
        self.assertAlmostEqual(sum(offsets), 0.0, places=12)
        self.assertLess(max(offsets), 0.5)

    def test_output_and_encoding_budget(self):
        self.assertEqual(flowers.OUTPUT_PATH.name, "luminous-flowers-spatial-180s.mp4")
        self.assertEqual(flowers.N_FRAMES, 180 * artwork.FPS)
        self.assertEqual(flowers.SPEC.duration_s, 180.0)
        self.assertEqual(flowers.SPEC.n_frames, flowers.N_FRAMES)
        self.assertIs(flowers.SPEC.worker_frame, flowers.worker_frame)
        worst = artwork.worst_case_upload_bytes(flowers.DURATION_S, flowers.SPEC.maxrate_kbps,
                                                flowers.SPEC.bufsize_kbps)
        self.assertLess(worst * 1.01, artwork.MAX_VIDEO_BYTES)
        self.assertGreaterEqual(flowers.MARGIN, math.ceil(2.6 * max(flowers.DOF_NEAR_SIGMAS + flowers.DOF_FAR_SIGMAS)))


if __name__ == "__main__":
    unittest.main()
