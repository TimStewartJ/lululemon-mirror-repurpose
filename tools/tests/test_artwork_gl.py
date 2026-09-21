import importlib.util
import math
import pathlib
import sys
import unittest

TOOLS = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))

HAS_GL = all(importlib.util.find_spec(name) for name in ("moderngl", "numpy"))
if HAS_GL:
    import numpy as np
    import artwork_gl as agl

    HAS_GL = agl.gl_available()

LAYER_VS = """
uniform float u_layer;
uniform float u_layers;
uniform float u_depth;
void main() {
    vec2 p = vec2((gl_VertexID << 1) & 2, gl_VertexID & 2);
    gl_Position = depth_layer(vec4(p * 2.0 - 1.0, u_depth, 1.0), u_layer, u_layers);
}
"""

LAYER_FS = """
uniform vec3 u_color;
out vec4 f_color;
void main() { f_color = vec4(u_color, 1.0); }
"""


@unittest.skipUnless(HAS_GL, "The GPU stage needs moderngl and an OpenGL 4.3 device")
class ArtworkGlTest(unittest.TestCase):
    def test_camera_matrices_put_the_target_in_the_middle(self):
        view = agl.look_at((0.0, 0.0, 0.0), (0.0, 0.0, -12.0))
        proj = agl.perspective(38.0, 1080 / 1920, 2.0, 80.0)

        def ndc(point):
            clip = proj @ view @ np.append(np.asarray(point, dtype=float), 1.0)
            return clip[:3] / clip[3]

        np.testing.assert_allclose(ndc((0.0, 0.0, -12.0))[:2], 0.0, atol=1e-12)
        self.assertGreater(ndc((1.0, 0.0, -12.0))[0], 0.0)                     # +x is to the right
        self.assertGreater(ndc((0.0, 1.0, -12.0))[1], 0.0)                     # +y is up
        self.assertAlmostEqual(ndc((0.0, 0.0, -2.0))[2], -1.0, places=9)       # near plane
        self.assertAlmostEqual(ndc((0.0, 0.0, -80.0))[2], 1.0, places=9)       # far plane
        top = 12.0 * math.tan(math.radians(19.0))
        self.assertAlmostEqual(ndc((0.0, top, -12.0))[1], 1.0, places=9)
        self.assertEqual(agl.mat_bytes(proj), np.ascontiguousarray(proj.T, dtype=np.float32).tobytes())

    def test_depth_of_field_cross_fades_between_neighbouring_slabs(self):
        dof = agl.DepthOfField(12.0, 105.0, (1.4, 2.8, 4.2), (1.4, 2.8, 4.6, 7.0))
        self.assertEqual(dof.sigmas, (4.2, 2.8, 1.4, 0.0, 1.4, 2.8, 4.6, 7.0))   # back to front
        self.assertEqual(dof.focus_index, 3)
        dist = np.linspace(2.5, 60.0, 4000)
        weights = dof.slab_weights(dist)
        np.testing.assert_allclose(weights.sum(axis=1), 1.0, atol=1e-12)
        self.assertLessEqual((weights > 0).sum(axis=1).max(), 2)
        used = np.nonzero(weights > 0)
        for row in range(0, len(dist), 97):
            columns = used[1][used[0] == row]
            self.assertLessEqual(columns.max() - columns.min(), 1)               # always neighbours
        self.assertLess(np.abs(np.diff(weights, axis=0)).max(), 0.06)            # no jumps along the way
        np.testing.assert_allclose(dof.slab_weights(np.array([12.0]))[0], np.eye(8)[3], atol=1e-12)
        self.assertEqual(int(np.argmax(dof.slab_weights(np.array([3.0]))[0])), 7)    # at the lens: widest near blur
        self.assertEqual(int(np.argmax(dof.slab_weights(np.array([500.0]))[0])), 0)
        # A subject with real depth can be held sharp through and through.
        deep = agl.DepthOfField(21.0, 105.0, (1.5,), (1.5,), sharp=2.2)
        np.testing.assert_allclose(deep.slab_weights(np.array([17.0, 21.0, 27.0]))[:, 1], 1.0, atol=1e-12)

    def test_grid_mesh_covers_a_petal_sheet(self):
        verts, indices = agl.grid_mesh(6, 4)
        verts = np.asarray(verts).reshape(-1, 2)
        self.assertEqual(verts[:, 0].min(), 0.0)
        self.assertEqual(verts[:, 0].max(), 1.0)
        self.assertEqual(verts[:, 1].min(), -1.0)
        self.assertEqual(verts[:, 1].max(), 1.0)
        self.assertEqual(len(indices) % 3, 0)
        self.assertEqual(int(np.max(indices)), len(verts) - 1)
        self.assertEqual(len(np.unique(indices)), len(verts))

    def test_jitter_stays_inside_the_pixel_and_never_repeats(self):
        self.assertEqual(agl.jitter_offset(0, 1), (0.0, 0.0))
        offsets = [agl.jitter_offset(k, 6) for k in range(6)]
        self.assertEqual(len(set(offsets)), 6)
        self.assertTrue(all(-0.5 <= v <= 0.5 for offset in offsets for v in offset))

    def test_margin_is_rendered_around_the_frame_and_left_behind(self):
        plain = agl.Stage(96, 160)
        padded = agl.Stage(96, 160, margin=16)
        try:
            self.assertEqual((padded.width, padded.height), (128, 192))
            self.assertEqual((padded.frame_width, padded.frame_height, padded.margin), (96, 160, 16))
            proj = agl.perspective(40.0, 96 / 160, 0.5, 50.0)
            wide = padded.expand(proj)
            np.testing.assert_allclose(wide[0], proj[0] * 96 / 128)
            np.testing.assert_allclose(wide[1], proj[1] * 160 / 192)
            np.testing.assert_allclose(wide[2:], proj[2:])
            np.testing.assert_allclose(plain.expand(proj), proj)

            view = agl.look_at((0.0, 0.0, 0.0), (0.0, 0.0, -10.0))
            spot = 10.0 * math.tan(math.radians(20.0)) * 0.5         # a quarter of the way up from the middle

            def shoot(stage, x, y):
                light = np.array([[x, y, -10.0, 0.25, 1.0, 0.8, 0.6, 4.0]])
                with stage.ctx:
                    stage.begin()
                    stage.draw_sprites("test", light, view, stage.expand(proj), None)
                    return stage.finish(0, grain=0.0, bloom_gain=0.0).astype(float).sum(axis=2)

            for stage in (plain, padded):
                frame = shoot(stage, 0.0, spot)
                self.assertEqual(frame.shape, (160, 96))
                total = frame.sum()
                cy, cx = (frame * np.arange(160)[:, None]).sum() / total, (frame * np.arange(96)[None, :]).sum() / total
                self.assertAlmostEqual(cx, 47.5, delta=0.6)
                self.assertAlmostEqual(cy, 79.5 - 40.0, delta=0.6)   # rows run top-down: up is a smaller row
            # Light just outside the frame is drawn in the margin and cropped away...
            outside = 10.0 * math.tan(math.radians(20.0)) * (96 / 160) * (1.0 + 10 / 48)
            self.assertEqual(shoot(padded, outside, 0.0).max(), 0.0)
            self.assertEqual(shoot(plain, outside, 0.0).max(), 0.0)
            # ... where blur and bloom can still find it: its glow reaches into the frame.
            light = np.array([[outside, 0.0, -10.0, 0.25, 1.0, 0.8, 0.6, 40.0]])
            glow = []
            for stage in (plain, padded):
                with stage.ctx:
                    stage.begin()
                    stage.draw_sprites("test", light, view, stage.expand(proj), None)
                    frame = stage.finish(0, grain=0.0, bloom_threshold=0.1, bloom_gain=2.0).astype(float).sum(axis=2)
                glow.append(frame[:, -8:].sum())
            self.assertEqual(glow[0], 0.0)
            self.assertGreater(glow[1], 100.0)
        finally:
            plain.release()
            padded.release()

    def test_empty_stage_is_exactly_black_whatever_the_grain(self):
        stage = agl.Stage(64, 96, margin=8)
        try:
            with stage.ctx:
                stage.begin()
                frame = stage.finish(7, grain=3.0)
            self.assertEqual(frame.shape, (96, 64, 3))
            self.assertEqual(frame.dtype, np.uint8)
            self.assertEqual(int(frame.max()), 0)
        finally:
            stage.release()

    def test_a_later_depth_layer_always_draws_over_an_earlier_one(self):
        import moderngl
        stage = agl.Stage(16, 16)
        try:
            with stage.ctx:
                ctx = stage.ctx
                prog = stage.program(LAYER_VS, LAYER_FS)
                vao = ctx.vertex_array(prog, [])
                target = ctx.framebuffer([ctx.texture((16, 16), 4)], ctx.depth_renderbuffer((16, 16)))

                def draw(*quads, layers=3.0):
                    target.use()
                    target.clear(0.0, 0.0, 0.0, 0.0, depth=1.0)
                    ctx.enable(moderngl.DEPTH_TEST)
                    ctx.depth_func = "<="
                    prog["u_layers"].value = layers
                    for layer, depth, color in quads:
                        prog["u_layer"].value = float(layer)
                        prog["u_depth"].value = depth
                        prog["u_color"].value = color
                        vao.render(moderngl.TRIANGLES, vertices=3)
                    pixel = np.frombuffer(target.read(components=3), dtype=np.uint8)[:3]
                    return tuple(int(v) for v in pixel)

                red, green, blue = (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)
                # Inside a layer depth decides, whatever the order of drawing.
                self.assertEqual(draw((1, -0.5, red), (1, 0.5, green)), (255, 0, 0))
                self.assertEqual(draw((1, 0.5, green), (1, -0.5, red)), (255, 0, 0))
                # Between layers the later layer wins, even from the far end of its
                # range against the near end of the earlier one...
                self.assertEqual(draw((0, -1.0, red), (1, 1.0, green)), (0, 255, 0))
                self.assertEqual(draw((1, 1.0, green), (0, -1.0, red)), (0, 255, 0))
                self.assertEqual(draw((2, 0.9, blue), (0, -0.9, red), (1, 0.0, green)), (0, 0, 255))
                # ... however many layers share the depth buffer.
                self.assertEqual(draw((382, 1.0, green), (383, 1.0, blue), (381, -1.0, red), layers=400.0), (0, 0, 255))
        finally:
            stage.release()

    def test_a_renderer_settles_before_it_hands_out_frames(self):
        # A provisional first frame (a driver warming up a new shader) is rendered
        # again until two renders in a row agree.
        calls = []

        def render(index, provisional=2):
            calls.append(index)
            value = 0 if len(calls) > provisional else len(calls)
            return np.full((2, 2, 3), value, dtype=np.uint8), 0.0

        self.assertTrue(agl.settle(render, index=5))
        self.assertEqual(calls, [5, 5, 5, 5])
        calls.clear()
        self.assertFalse(agl.settle(lambda index: render(index, provisional=99), tries=3))
        self.assertEqual(len(calls), 4)

    def test_stages_in_one_process_keep_to_their_own_context(self):
        view = agl.look_at((0.0, 0.0, 0.0), (0.0, 0.0, -10.0))
        proj = agl.perspective(40.0, 1.0, 0.5, 50.0)

        def shoot(stage, color):
            light = np.array([[0.0, 0.0, -10.0, 0.6, *color, 3.0]])
            with stage.ctx:
                stage.begin()
                stage.draw_sprites("test", light, view, stage.expand(proj), None)
                return stage.finish(0, grain=0.0)

        first = agl.Stage(48, 48, margin=8)
        reference = shoot(first, (1.0, 0.2, 0.1))
        second = agl.Stage(48, 48, margin=8)            # making a context also makes it current
        third = agl.Stage(32, 32)
        try:
            np.testing.assert_array_equal(shoot(first, (1.0, 0.2, 0.1)), reference)
            other = shoot(second, (0.1, 0.2, 1.0))
            self.assertGreater(int(other[24, 24, 0]), int(other[24, 24, 2]))            # BGR: a blue light
            self.assertGreater(int(reference[24, 24, 2]), int(reference[24, 24, 0]))    # and a red one
            third.release()
            del third
            np.testing.assert_array_equal(shoot(first, (1.0, 0.2, 0.1)), reference)
            np.testing.assert_array_equal(shoot(second, (0.1, 0.2, 1.0)), other)
        finally:
            first.release()
            second.release()


if __name__ == "__main__":
    unittest.main()
