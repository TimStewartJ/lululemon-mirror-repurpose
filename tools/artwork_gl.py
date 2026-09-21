#!/usr/bin/env python3
"""GPU stage shared by the optional Mirror background artwork films.

The films are real 3D scenes of light on a true-black stage. This module owns
what they share on the GPU: a standalone moderngl context, linear-light HDR
targets with 8x multisampling, depth slabs that are blurred by their circle of
confusion and composited back to front (depth of field with soft occlusion),
analytic bokeh for points of light, a wide soft bloom, a hue-preserving display
transform that keeps black exactly black, and luminance-weighted grain.

It is not part of the Android build or a runtime dependency.
"""

from __future__ import annotations

import math

import moderngl
import numpy as np

GLSL_VERSION = "#version 430\n"

COMMON_GLSL = """
const float PI = 3.14159265358979;
const float TAU = 6.28318530717959;

float hash11(float p) {
    p = fract(p * 0.1031);
    p *= p + 33.33;
    p *= p + p;
    return fract(p);
}
float hash12(vec2 p) {
    vec3 p3 = fract(vec3(p.xyx) * 0.1031);
    p3 += dot(p3, p3.yzx + 33.33);
    return fract((p3.x + p3.y) * p3.z);
}
float hash13(vec3 p3) {
    p3 = fract(p3 * 0.1031);
    p3 += dot(p3, p3.zyx + 31.32);
    return fract((p3.x + p3.y) * p3.z);
}
float vnoise(vec2 p) {
    vec2 i = floor(p), f = fract(p);
    vec2 w = f * f * (3.0 - 2.0 * f);
    return mix(mix(hash12(i), hash12(i + vec2(1, 0)), w.x),
               mix(hash12(i + vec2(0, 1)), hash12(i + vec2(1, 1)), w.x), w.y);
}
float fbm(vec2 p) {
    float a = 0.5, s = 0.0;
    for (int i = 0; i < 4; ++i) { s += a * vnoise(p); p = p * 2.03 + 17.1; a *= 0.5; }
    return s;
}
vec3 quat_rotate(vec4 q, vec3 v) {
    return v + 2.0 * cross(q.xyz, cross(q.xyz, v) + q.w * v);
}
vec4 quat_mul(vec4 a, vec4 b) {
    return vec4(a.w * b.xyz + b.w * a.xyz + cross(a.xyz, b.xyz), a.w * b.w - dot(a.xyz, b.xyz));
}
vec4 quat_axis(vec3 axis, float angle) {
    return vec4(axis * sin(angle * 0.5), cos(angle * 0.5));
}
float smooth01(float x) { x = clamp(x, 0.0, 1.0); return x * x * (3.0 - 2.0 * x); }

// Depth layers. Layer j of n owns its own slice of the depth range, nearer
// slices for higher j: a later layer always draws over an earlier one, while
// depth still decides what is visible inside a layer. The slices stop a hair
// short of each other, so the far end of one never ties with the near end of
// the next. The remap is linear in clip space, so interpolation across
// triangles stays exact.
vec4 depth_layer(vec4 clip, float layer, float count) {
    clip.z = (clip.z * 0.999 + (2.0 * (count - 1.0 - layer) + 1.0 - count) * clip.w) / count;
    return clip;
}
"""

FULLSCREEN_VS = """
out vec2 v_uv;
void main() {
    vec2 p = vec2((gl_VertexID << 1) & 2, gl_VertexID & 2);
    v_uv = p;
    gl_Position = vec4(p * 2.0 - 1.0, 0.0, 1.0);
}
"""

COPY_FS = """
uniform sampler2D u_tex;
uniform float u_gain;
in vec2 v_uv;
out vec4 f_color;
void main() { f_color = texture(u_tex, v_uv) * u_gain; }
"""

# One bad sample must never be smeared across the frame by a blur.
SANITIZE_FS = """
uniform sampler2D u_tex;
uniform float u_gain;
in vec2 v_uv;
out vec4 f_color;
void main() {
    vec4 c = texelFetch(u_tex, ivec2(gl_FragCoord.xy), 0);
    bool bad = any(isnan(c)) || any(isinf(c));
    f_color = bad ? vec4(0.0) : clamp(c, 0.0, 64.0) * u_gain;
}
"""

BLUR_FS = """
uniform sampler2D u_tex;
uniform vec2 u_step;
uniform float u_sigma;
in vec2 v_uv;
out vec4 f_color;
void main() {
    int radius = int(ceil(u_sigma * 2.6));
    float inv = 1.0 / (2.0 * u_sigma * u_sigma);
    vec4 sum = texture(u_tex, v_uv);
    float total = 1.0;
    for (int i = 1; i <= radius; i += 2) {
        float w0 = exp(-float(i * i) * inv);
        float w1 = (i + 1 <= radius) ? exp(-float((i + 1) * (i + 1)) * inv) : 0.0;
        float w = w0 + w1;
        float o = (float(i) * w0 + float(i + 1) * w1) / w;
        sum += (texture(u_tex, v_uv + u_step * o) + texture(u_tex, v_uv - u_step * o)) * w;
        total += 2.0 * w;
    }
    f_color = sum / total;
}
"""

BLOOM_PREFILTER_FS = """
uniform sampler2D u_tex;
uniform float u_threshold;
uniform float u_knee;
in vec2 v_uv;
out vec4 f_color;
void main() {
    vec2 px = 1.0 / vec2(textureSize(u_tex, 0));
    vec3 c = (texture(u_tex, v_uv + px * vec2(-0.5, -0.5)).rgb + texture(u_tex, v_uv + px * vec2(0.5, -0.5)).rgb
            + texture(u_tex, v_uv + px * vec2(-0.5, 0.5)).rgb + texture(u_tex, v_uv + px * vec2(0.5, 0.5)).rgb) * 0.25;
    float peak = max(c.r, max(c.g, c.b));
    float soft = clamp(peak - u_threshold + u_knee, 0.0, 2.0 * u_knee);
    soft = soft * soft / (4.0 * u_knee + 1e-5);
    float gain = max(soft, peak - u_threshold) / max(peak, 1e-5);
    f_color = vec4(c * gain, 1.0);
}
"""

BLOOM_DOWN_FS = """
uniform sampler2D u_tex;
in vec2 v_uv;
out vec4 f_color;
void main() {
    vec2 px = 1.0 / vec2(textureSize(u_tex, 0));
    vec3 a = texture(u_tex, v_uv + px * vec2(-2, -2)).rgb, b = texture(u_tex, v_uv + px * vec2(0, -2)).rgb;
    vec3 c = texture(u_tex, v_uv + px * vec2(2, -2)).rgb, d = texture(u_tex, v_uv + px * vec2(-2, 0)).rgb;
    vec3 e = texture(u_tex, v_uv).rgb, f = texture(u_tex, v_uv + px * vec2(2, 0)).rgb;
    vec3 g = texture(u_tex, v_uv + px * vec2(-2, 2)).rgb, h = texture(u_tex, v_uv + px * vec2(0, 2)).rgb;
    vec3 i = texture(u_tex, v_uv + px * vec2(2, 2)).rgb, j = texture(u_tex, v_uv + px * vec2(-1, -1)).rgb;
    vec3 k = texture(u_tex, v_uv + px * vec2(1, -1)).rgb, l = texture(u_tex, v_uv + px * vec2(-1, 1)).rgb;
    vec3 m = texture(u_tex, v_uv + px * vec2(1, 1)).rgb;
    vec3 sum = e * 0.125 + (a + c + g + i) * 0.03125 + (b + d + f + h) * 0.0625 + (j + k + l + m) * 0.125;
    f_color = vec4(sum, 1.0);
}
"""

BLOOM_UP_FS = """
uniform sampler2D u_tex;
uniform float u_radius;
uniform float u_gain;
in vec2 v_uv;
out vec4 f_color;
void main() {
    vec2 px = u_radius / vec2(textureSize(u_tex, 0));
    vec3 sum = texture(u_tex, v_uv).rgb * 4.0;
    sum += (texture(u_tex, v_uv + px * vec2(-1, 0)).rgb + texture(u_tex, v_uv + px * vec2(1, 0)).rgb
          + texture(u_tex, v_uv + px * vec2(0, -1)).rgb + texture(u_tex, v_uv + px * vec2(0, 1)).rgb) * 2.0;
    sum += texture(u_tex, v_uv + px * vec2(-1, -1)).rgb + texture(u_tex, v_uv + px * vec2(1, -1)).rgb
         + texture(u_tex, v_uv + px * vec2(-1, 1)).rgb + texture(u_tex, v_uv + px * vec2(1, 1)).rgb;
    f_color = vec4(sum * (u_gain / 16.0), 1.0);
}
"""

DISPLAY_FS = """
uniform sampler2D u_hdr;
uniform sampler2D u_bloom;
uniform float u_exposure;
uniform float u_bloom_gain;
uniform float u_knee;
uniform float u_whiten;
uniform float u_black;
uniform float u_grain;
uniform float u_frame;
uniform vec4 u_quiet;        // x0, y0, x1, y1 in top-down pixels
uniform float u_quiet_feather;
uniform float u_quiet_floor;
uniform vec2 u_size;         // of the frame
uniform vec2 u_margin;       // rendered around the frame, and left behind here
in vec2 v_uv;
out vec4 f_color;

float quiet_mask(vec2 p) {
    vec2 d = max(max(u_quiet.xy - p, p - u_quiet.zw), 0.0);
    float outside = length(d) / u_quiet_feather;
    return mix(u_quiet_floor, 1.0, smooth01(outside));
}

void main() {
    vec2 uv = (gl_FragCoord.xy + u_margin) / (u_size + 2.0 * u_margin);
    vec3 c = texture(u_hdr, uv).rgb * u_exposure + texture(u_bloom, uv).rgb * u_bloom_gain;
    vec2 p = vec2(gl_FragCoord.x, u_size.y - gl_FragCoord.y);
    c *= quiet_mask(p);
    float peak = max(c.r, max(c.g, c.b));
    if (peak > u_knee) {
        float span = 1.0 - u_knee;
        float mapped = u_knee + span * (1.0 - exp(-(peak - u_knee) / span));
        vec3 kept = c * (mapped / peak);
        float heat = clamp((peak - 1.0) / 4.0, 0.0, 1.0) * u_whiten;
        c = mix(kept, vec3(mapped), heat);
    }
    c = max(c - u_black, 0.0) / (1.0 - u_black);
    c = pow(c, vec3(1.0 / 2.2)) * 255.0;
    // Luminance-weighted grain and dither: exact black stays exact black.
    float luma = dot(c, vec3(0.299, 0.587, 0.114));
    float weight = clamp((luma - 1.5) / 20.0, 0.0, 1.0);
    vec3 seed = vec3(gl_FragCoord.xy, u_frame);
    float n = hash13(seed) + hash13(seed + 17.17) + hash13(seed + 41.3) + hash13(seed + 73.9) - 2.0;
    c += n * u_grain * 1.732 * weight;
    f_color = vec4(clamp(floor(c + 0.5), 0.0, 255.0).bgr / 255.0, 1.0);
}
"""

# Points of light with an analytic circle of confusion. Each instance is
# (xyz, size_world, rgb, intensity). Energy is conserved as the disc grows, so
# defocused motes turn into large faint bokeh instead of bright blobs.
SPRITE_VS = """
uniform mat4 u_view;
uniform mat4 u_proj;
uniform vec2 u_size;
uniform vec2 u_jitter;
uniform float u_focus;
uniform float u_coc_scale;
uniform float u_coc_max;
uniform float u_coc_sharp;
uniform float u_min_px;
uniform float u_near_fade;
in vec2 in_corner;
in vec3 i_pos;
in float i_radius;
in vec3 i_color;
in float i_gain;
out vec2 v_corner;
out vec3 v_color;
out float v_soft;
void main() {
    vec4 vp = u_view * vec4(i_pos, 1.0);
    float dist = max(-vp.z, 1e-3);
    float px_per_unit = u_proj[1][1] * u_size.y * 0.5 / dist;
    float sharp = max(i_radius * px_per_unit, u_min_px);
    float coc = min(max(abs(1.0 / dist - 1.0 / u_focus) * u_coc_scale - u_coc_sharp, 0.0), u_coc_max);
    float radius = sqrt(sharp * sharp + coc * coc);
    float energy = (i_radius * px_per_unit) * (i_radius * px_per_unit) / (radius * radius);
    float fade = smoothstep(u_near_fade * 0.5, u_near_fade, dist);
    v_color = i_color * i_gain * max(energy, 0.0) * fade;
    v_soft = clamp(coc / radius, 0.0, 1.0);
    // The quad is a little larger than the disc so its soft edge is not cut.
    v_corner = in_corner * (radius + 1.5) / radius;
    vec4 clip = u_proj * vp;
    clip.xy += in_corner * (radius + 1.5) * 2.0 / u_size * clip.w;
    clip.xy += u_jitter * clip.w;
    gl_Position = (-vp.z > 0.05 && i_gain > 0.0) ? clip : vec4(2.0, 2.0, 2.0, 1.0);
}
"""

SPRITE_FS = """
in vec2 v_corner;
in vec3 v_color;
in float v_soft;
out vec4 f_color;
void main() {
    float r = length(v_corner);
    if (r > 1.0) discard;
    // In focus: a soft gaussian point. Defocused: a flat disc with a slightly
    // brighter rim. Both integrate to the same energy over the unit disc.
    float point = exp(-r * r * 4.0) * 4.07;
    float disc = smoothstep(1.0, 0.86, r) * (0.82 + 0.3 * r * r) / 0.82;
    f_color = vec4(v_color * mix(point, disc, v_soft), 0.0);
}
"""


def perspective(fov_y_deg, aspect, near, far):
    f = 1.0 / math.tan(math.radians(fov_y_deg) * 0.5)
    m = np.zeros((4, 4), dtype=np.float64)
    m[0, 0] = f / aspect
    m[1, 1] = f
    m[2, 2] = (far + near) / (near - far)
    m[2, 3] = 2.0 * far * near / (near - far)
    m[3, 2] = -1.0
    return m


def look_at(eye, target, up=(0.0, 1.0, 0.0)):
    eye = np.asarray(eye, dtype=np.float64)
    forward = np.asarray(target, dtype=np.float64) - eye
    forward /= np.linalg.norm(forward)
    side = np.cross(forward, np.asarray(up, dtype=np.float64))
    side /= np.linalg.norm(side)
    true_up = np.cross(side, forward)
    m = np.eye(4)
    m[0, :3], m[1, :3], m[2, :3] = side, true_up, -forward
    m[:3, 3] = -m[:3, :3] @ eye
    return m


def mat_bytes(m):
    """Column-major float32 bytes of a row-major math matrix."""
    return np.ascontiguousarray(np.asarray(m, dtype=np.float32).T).tobytes()


def grid_mesh(nu, nv):
    """(u, v) lattice with u in [0, 1] and v in [-1, 1], plus triangle indices."""
    u, v = np.meshgrid(np.linspace(0.0, 1.0, nu + 1), np.linspace(-1.0, 1.0, nv + 1), indexing="ij")
    verts = np.stack([u.ravel(), v.ravel()], axis=1).astype(np.float32)
    idx = []
    for i in range(nu):
        for j in range(nv):
            a, b = i * (nv + 1) + j, (i + 1) * (nv + 1) + j
            idx += [a, b, a + 1, a + 1, b, b + 1]
    return verts, np.asarray(idx, dtype=np.int32)


class DepthOfField:
    """Blur as a function of distance, and the depth slabs that realize it.

    ``sigma(d) = scale * |1/d - 1/focus|`` pixels. Slabs are listed back to
    front by their blur; an object is cross-faded between the two slabs that
    bracket its own blur, so it never pops as it travels through depth.
    """

    def __init__(self, focus, scale, far_sigmas, near_sigmas, sharp=0.0):
        self.focus = float(focus)
        self.scale = float(scale)
        # Blur below ``sharp`` pixels is treated as in focus, so a subject with
        # real depth (a whole tree) stays crisp and never looks like a miniature.
        self.sharp = float(sharp)
        self.sigmas = tuple(sorted(far_sigmas, reverse=True)) + (0.0,) + tuple(sorted(near_sigmas))
        self.focus_index = len(far_sigmas)
        # Signed blur coordinate: negative behind the focal plane, positive in front.
        self.coords = np.array([-s for s in sorted(far_sigmas, reverse=True)] + [0.0] + sorted(near_sigmas))

    def signed_sigma(self, dist):
        dist = np.maximum(np.asarray(dist, dtype=np.float64), 1e-3)
        s = (1.0 / dist - 1.0 / self.focus) * self.scale
        return np.sign(s) * np.maximum(np.abs(s) - self.sharp, 0.0)

    def slab_uniform(self, k):
        """(previous, own, next) blur coordinates of slab ``k`` for shaders."""
        c = self.coords
        return float(c[max(k - 1, 0)]), float(c[k]), float(c[min(k + 1, len(c) - 1)])

    def slab_weights(self, dist):
        """(n, slabs) cross-fade weights that sum to one."""
        s = np.clip(self.signed_sigma(dist), self.coords[0], self.coords[-1])
        pos = np.interp(s, self.coords, np.arange(len(self.coords)))
        k = np.arange(len(self.coords))[None, :]
        return np.clip(1.0 - np.abs(pos[:, None] - k), 0.0, 1.0)


class Stage:
    """The shared render targets and passes of a film.

    The picture is rendered with ``margin`` pixels to spare on every side, and
    only the frame in the middle is kept. Blur and bloom reach past the edges
    of the frame, and they can only gather what has been drawn: without the
    margin, whatever drifts in from outside would appear all at once, already
    blurred, the moment its first pixel crossed the edge. ``width`` and
    ``height`` are the rendered size; draw with ``expand(proj)``.
    """

    def __init__(self, width, height, *, samples=8, margin=0):
        self.frame_width, self.frame_height, self.margin = width, height, int(margin)
        self.width, self.height = width + 2 * self.margin, height + 2 * self.margin
        self.ctx = ctx = moderngl.create_context(standalone=True, require=430)
        # Dropped GL objects wait for ``finish`` to free them, when this context
        # is sure to be the current one: a process may hold several stages, and
        # a name freed in the wrong context would destroy a living object there.
        ctx.gc_mode = "context_gc"
        size = (self.width, self.height)
        self.msaa_color = ctx.renderbuffer(size, 4, samples=samples, dtype="f2")
        self.msaa_depth = ctx.depth_renderbuffer(size, samples=samples)
        self.msaa = ctx.framebuffer([self.msaa_color], self.msaa_depth)
        self.resolve_tex, self.resolve = self._target(size)
        self.accum_tex, self.accum = self._target(size)
        self.hdr_tex, self.hdr = self._target(size)
        self.blur_targets = {}
        self.bloom_levels = []
        w, h = self.width // 2, self.height // 2
        for _ in range(6):
            self.bloom_levels.append(self._target((max(w, 2), max(h, 2))) + self._target((max(w, 2), max(h, 2))))
            w, h = w // 2, h // 2
        self.out_tex = ctx.texture((self.frame_width, self.frame_height), 4, dtype="f1")
        self.out = ctx.framebuffer([self.out_tex])

        self.copy_prog = self.program(FULLSCREEN_VS, COPY_FS)
        self.sanitize_prog = self.program(FULLSCREEN_VS, SANITIZE_FS)
        self.blur_prog = self.program(FULLSCREEN_VS, BLUR_FS)
        self.prefilter_prog = self.program(FULLSCREEN_VS, BLOOM_PREFILTER_FS)
        self.down_prog = self.program(FULLSCREEN_VS, BLOOM_DOWN_FS)
        self.up_prog = self.program(FULLSCREEN_VS, BLOOM_UP_FS)
        self.display_prog = self.program(FULLSCREEN_VS, DISPLAY_FS)
        self._fullscreen = {}
        self.sprite_prog = self.program(SPRITE_VS, SPRITE_FS)
        corners = np.array([[-1, -1], [1, -1], [-1, 1], [1, 1]], dtype=np.float32)
        self.corner_vbo = ctx.buffer(corners.tobytes())
        self._sprite_sets = {}

    # -- resources ---------------------------------------------------------

    def _target(self, size):
        tex = self.ctx.texture(size, 4, dtype="f2")
        tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
        tex.repeat_x = tex.repeat_y = False
        return tex, self.ctx.framebuffer([tex])

    def depth_target(self, size):
        """An HDR colour texture with its own depth buffer (reflections)."""
        tex = self.ctx.texture(size, 4, dtype="f2")
        tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
        tex.repeat_x = tex.repeat_y = False
        return tex, self.ctx.framebuffer([tex], self.ctx.depth_renderbuffer(size))

    def program(self, vertex, fragment):
        return self.ctx.program(vertex_shader=GLSL_VERSION + COMMON_GLSL + vertex,
                                fragment_shader=GLSL_VERSION + COMMON_GLSL + fragment)

    def _pass(self, prog, target, **uniforms):
        vao = self._fullscreen.get(id(prog))
        if vao is None:
            vao = self._fullscreen[id(prog)] = self.ctx.vertex_array(prog, [])
        unit = 0
        for name, value in uniforms.items():
            if isinstance(value, moderngl.Texture):
                value.use(location=unit)
                prog[name].value = unit
                unit += 1
            else:
                prog[name].value = value
        target.use()
        vao.render(moderngl.TRIANGLES, vertices=3)

    def expand(self, proj):
        """The projection that shows ``proj``'s picture in the frame and carries
        on past its edges into the margin."""
        out = np.array(proj, dtype=np.float64)
        out[0, :] *= self.frame_width / self.width
        out[1, :] *= self.frame_height / self.height
        return out

    # -- frame -------------------------------------------------------------

    def begin(self):
        self.ctx.disable(moderngl.BLEND | moderngl.DEPTH_TEST | moderngl.CULL_FACE)
        self.hdr.clear(0.0, 0.0, 0.0, 0.0)

    def render_slab(self, sigma, draw, *, subframes=1):
        """Draws one depth slab (premultiplied RGBA), blurs it by ``sigma``
        pixels and composites it over everything behind it. ``draw(s, jitter)``
        renders sub-frame ``s`` into the bound multisampled target."""
        ctx = self.ctx
        self.accum.clear(0.0, 0.0, 0.0, 0.0)
        for s in range(subframes):
            self.msaa.use()
            self.msaa.clear(0.0, 0.0, 0.0, 0.0, depth=1.0)
            draw(s, jitter_offset(s, subframes))
            ctx.copy_framebuffer(self.resolve, self.msaa)
            ctx.disable(moderngl.DEPTH_TEST)
            ctx.enable(moderngl.BLEND)
            ctx.blend_func = moderngl.ONE, moderngl.ONE
            self._pass(self.sanitize_prog, self.accum, u_tex=self.resolve_tex, u_gain=1.0 / subframes)
            ctx.disable(moderngl.BLEND)
        layer = self.accum_tex
        if sigma > 0.05:
            layer = self.blur(layer, sigma)
        ctx.enable(moderngl.BLEND)
        ctx.blend_func = moderngl.ONE, moderngl.ONE_MINUS_SRC_ALPHA
        self._pass(self.copy_prog, self.hdr, u_tex=layer, u_gain=1.0)
        ctx.disable(moderngl.BLEND)

    def _blur_pair(self, scale):
        if scale not in self.blur_targets:
            size = (self.width // scale, self.height // scale)
            self.blur_targets[scale] = self._target(size) + self._target(size)
        return self.blur_targets[scale]

    def blur(self, tex, sigma):
        """Separable gaussian; wide blurs run at reduced resolution, reached by
        exact 2x2 bilinear halvings so the downsample stays alias free."""
        scale = 1 if sigma < 5.0 else (2 if sigma < 12.0 else 4)
        source = tex
        for step in (2, 4):
            if scale >= step:
                _, _, half_tex, half_fbo = self._blur_pair(step)
                self._pass(self.copy_prog, half_fbo, u_tex=source, u_gain=1.0)
                source = half_tex
        a_tex, a_fbo, b_tex, b_fbo = self._blur_pair(scale)
        size = a_tex.size
        s = sigma / scale
        self._pass(self.blur_prog, a_fbo, u_tex=source, u_step=(1.0 / size[0], 0.0), u_sigma=s)
        self._pass(self.blur_prog, b_fbo, u_tex=a_tex, u_step=(0.0, 1.0 / size[1]), u_sigma=s)
        return b_tex

    def _sprite_set(self, name, capacity):
        """A reusable instance buffer of sprites (8 floats each)."""
        entry = self._sprite_sets.get(name)
        if entry is None or entry[2] < capacity:
            capacity = max(int(capacity * 1.5), 64)
            buf = self.ctx.buffer(reserve=capacity * 8 * 4, dynamic=True)
            vao = self.ctx.vertex_array(self.sprite_prog, [
                (self.corner_vbo, "2f", "in_corner"),
                (buf, "3f 1f 3f 1f/i", "i_pos", "i_radius", "i_color", "i_gain"),
            ])
            entry = self._sprite_sets[name] = (buf, vao, capacity)
        return entry

    def draw_sprites(self, name, data, view, proj, dof: DepthOfField | None, *, target=None,
                     jitter=(0.0, 0.0), coc_max=60.0, min_px=0.7, near_fade=1.5):
        """Additive points of light. ``data`` is (n, 8): xyz, world radius, rgb,
        gain. With ``dof`` each point gets an analytic circle of confusion;
        without it the point stays sharp (for slabs that are blurred later)."""
        data = np.ascontiguousarray(data, dtype=np.float32)
        if not len(data):
            return
        buf, vao, _ = self._sprite_set(name, len(data))
        buf.write(data.tobytes())
        prog = self.sprite_prog
        prog["u_view"].write(mat_bytes(view))
        prog["u_proj"].write(mat_bytes(proj))
        prog["u_size"].value = (self.width, self.height)
        prog["u_jitter"].value = (jitter[0] * 2.0 / self.width, jitter[1] * 2.0 / self.height)
        prog["u_focus"].value = dof.focus if dof else 1.0
        prog["u_coc_scale"].value = dof.scale if dof else 0.0
        prog["u_coc_max"].value = coc_max
        prog["u_coc_sharp"].value = dof.sharp if dof else 0.0
        prog["u_min_px"].value = min_px
        prog["u_near_fade"].value = near_fade
        # Light adds: it never hides anything.
        (self.hdr if target is None else target).use()
        self.ctx.disable(moderngl.DEPTH_TEST)
        self.ctx.enable(moderngl.BLEND)
        self.ctx.blend_func = moderngl.ONE, moderngl.ONE
        vao.render(moderngl.TRIANGLE_STRIP, instances=len(data))
        self.ctx.disable(moderngl.BLEND)

    def finish(self, frame_idx, *, exposure=1.0, bloom_threshold=0.55, bloom_knee=0.35, bloom_gain=0.5,
               bloom_radius=1.0, bloom_sigma=1.7, level_gains=(1.0, 0.8, 0.55, 0.35, 0.2, 0.12), knee=0.72, whiten=0.35,
               black=0.0035, grain=0.8, quiet_rect=(0, 0, 0, 0), quiet_feather=1.0, quiet_floor=1.0):
        """Bloom, display transform and readback. Returns a BGR uint8 frame."""
        ctx = self.ctx
        levels = self.bloom_levels
        level_gains = [g / sum(level_gains) for g in level_gains]
        self._pass(self.prefilter_prog, levels[0][1], u_tex=self.hdr_tex,
                   u_threshold=bloom_threshold, u_knee=bloom_knee)
        for i in range(1, len(levels)):
            self._pass(self.down_prog, levels[i][1], u_tex=levels[i - 1][0])
        # Each level becomes a true gaussian: tent filters alone leave a faint
        # hard-edged disc around every isolated point of light.
        if bloom_sigma > 0.05:
            for down_tex, down_fbo, up_tex, up_fbo in levels:
                size = down_tex.size
                self._pass(self.blur_prog, up_fbo, u_tex=down_tex, u_step=(1.0 / size[0], 0.0), u_sigma=bloom_sigma)
                self._pass(self.blur_prog, down_fbo, u_tex=up_tex, u_step=(0.0, 1.0 / size[1]), u_sigma=bloom_sigma)
        # Walk back up, adding each wider level onto the next sharper one.
        previous = levels[-1][0]
        for i in range(len(levels) - 2, -1, -1):
            down_tex, _, up_tex, up_fbo = levels[i]
            self._pass(self.copy_prog, up_fbo, u_tex=down_tex, u_gain=level_gains[i])
            ctx.enable(moderngl.BLEND)
            ctx.blend_func = moderngl.ONE, moderngl.ONE
            self._pass(self.up_prog, up_fbo, u_tex=previous, u_radius=bloom_radius,
                       u_gain=level_gains[i + 1] if i == len(levels) - 2 else 1.0)
            ctx.disable(moderngl.BLEND)
            previous = up_tex
        self._pass(self.display_prog, self.out, u_hdr=self.hdr_tex, u_bloom=previous,
                   u_exposure=exposure, u_bloom_gain=bloom_gain, u_knee=knee, u_whiten=whiten,
                   u_black=black, u_grain=grain, u_frame=float(frame_idx),
                   u_quiet=tuple(float(v) for v in quiet_rect), u_quiet_feather=float(quiet_feather),
                   u_quiet_floor=float(quiet_floor), u_size=(float(self.frame_width), float(self.frame_height)),
                   u_margin=(float(self.margin), float(self.margin)))
        raw = self.out.read(components=3, alignment=1)
        ctx.gc()
        frame = np.frombuffer(raw, dtype=np.uint8).reshape(self.frame_height, self.frame_width, 3)
        return np.ascontiguousarray(frame[::-1])

    def release(self):
        with self.ctx:
            self.ctx.gc()
        self.ctx.release()


def halton(index, base):
    f, r = 1.0, 0.0
    while index > 0:
        f /= base
        r += f * (index % base)
        index //= base
    return r


def jitter_offset(index, count):
    """Sub-pixel sample position in [-0.5, 0.5]^2 (Halton 2, 3)."""
    if count <= 1:
        return 0.0, 0.0
    return halton(index + 1, 2) - 0.5, halton(index + 1, 3) - 0.5


def settle(render, index=0, tries=5):
    """Renders frame ``index`` until two renders in a row agree, and discards
    them. True once they do.

    The first time a freshly compiled shader runs, a driver may put it through
    a provisional build (seen on AMD whenever its shader cache is cold, which is
    after every edit to a shader): that first frame then differs from every later
    render of it in a few hundred pixels. A film must be a pure function of loop
    time, so a renderer settles before it hands out its first frame.
    """
    previous = render(index)[0]
    for _ in range(tries):
        current = render(index)[0]
        if np.array_equal(previous, current):
            return True
        previous = current
    return False


def gl_available():
    try:
        ctx = moderngl.create_context(standalone=True, require=430)
    except Exception:
        return False
    ctx.release()
    return True
