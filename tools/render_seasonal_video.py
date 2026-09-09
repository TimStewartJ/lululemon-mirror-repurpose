#!/usr/bin/env python3
"""Offline renderer for the four-seasons cinematic background.

Deterministic, offline, fully self-contained renderer for a 48-second,
1080x1920, 24fps, seamless four-seasons tree animation aimed at a museum
projection / borderless-installation mood: luminous organic motion, layered
atmospheric depth, painterly light, flowing particles, and a meditative
sense of seasons dissolving into one another. Nothing here references or
reproduces any specific third-party artwork — only generic, original
qualities (bloom, parallax, watercolor-like light ribbons, soft particles)
are used, built from first principles with pycairo + NumPy + OpenCV.

Wind harmonics and foliage clusters are seeded once, then evaluated per frame.
Layered atmosphere, bloom, and camera drift preserve a dark widget quiet zone.
The output uses H.264 High Profile / Level 4.1, yuv420p, and no audio.

Run:
    python tools\\render_seasonal_video.py [n_frames]

    n_frames (optional): render only the first N frames for a quick smoke
    test instead of the full clip. Omit for the full deterministic 48s
    render used for the external background-video library.
    --contact-sheet: write 16 evenly spaced preview frames instead of a video.

Output:
    generated\\background-videos\\four-seasons-cinematic.mp4
    generated\\background-videos\\previews\\ (smoke clips and preview frames)
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import time

import cairo
import cv2
import numpy as np

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

WIDTH = 1080
HEIGHT = 1920
FPS = 24
DURATION_S = 48.0
N_FRAMES = int(round(FPS * DURATION_S))  # 1152

SEED = 20250903  # fixed -> deterministic/reproducible renders

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPT_DIR)
OUTPUT_PATH = os.path.join(
    REPO_ROOT, "generated", "background-videos", "four-seasons-cinematic.mp4",
)

# Keep uploads compact while retaining high-quality portrait detail.
TARGET_BITRATE_KBPS = 4800
MAXRATE_KBPS = 5400
BUFSIZE_KBPS = 10800

# Season indices
SPRING, SUMMER, AUTUMN, WINTER = 0, 1, 2, 3
SEASON_LEN = DURATION_S / 4.0  # 12s per season

# Upper-left "quiet zone" reserved for clock/date widgets: no strong tree
# mass, bright bloom, or saturated particles are allowed to rest here,
# though faint atmospheric drift (sky gradient, dust, ribbons) may pass
# behind it.
QUIET_X = 0.34 * WIDTH
QUIET_Y = 0.30 * HEIGHT

# Precise widget-legibility rectangle (art-director spec): mean luminance
# under this must stay below WIDGET_LUMA_MAX with no high-frequency detail.
# This is smaller/more precise than the geometry-avoidance QUIET_X/QUIET_Y
# box above, and is enforced numerically as a hard guarantee in
# ``enforce_widget_zone`` regardless of what atmosphere/background bleeds
# nearby, so the requirement is met even if upstream art direction changes.
WIDGET_X0, WIDGET_X1 = 0.02 * WIDTH, 0.40 * WIDTH
WIDGET_Y0, WIDGET_Y1 = 0.03 * HEIGHT, 0.24 * HEIGHT
WIDGET_LUMA_MAX = 15.0

TRUNK_BASE = (0.78 * WIDTH, 1.05 * HEIGHT)

GLOW_SCALE = 0.5
GLOW_W = int(WIDTH * GLOW_SCALE)
GLOW_H = int(HEIGHT * GLOW_SCALE)

# --------------------------------------------------------------------------
# Small deterministic math helpers
# --------------------------------------------------------------------------


def clamp(x, lo=0.0, hi=1.0):
    return lo if x < lo else (hi if x > hi else x)


def lerp(a, b, t):
    return a + (b - a) * t


def lerp_color(c0, c1, t):
    return tuple(lerp(c0[i], c1[i], t) for i in range(len(c0)))


def smoothstep(edge0, edge1, x):
    if edge0 == edge1:
        return 0.0 if x < edge0 else 1.0
    t = clamp((x - edge0) / (edge1 - edge0))
    return t * t * (3 - 2 * t)


def wrapped_delta(a, b, period):
    """Shortest signed distance from b to a on a circle of given period."""
    d = (a - b) % period
    if d > period / 2:
        d -= period
    return d


# --------------------------------------------------------------------------
# Periodic noise — precomputed harmonics, evaluated vectorized per frame.
#
# Build seeded harmonic/phase tables once and evaluate a whole population's
# wind/flutter with a single vectorized NumPy expression per frame.
# --------------------------------------------------------------------------

AMP_DECAY = 0.55
K_CHOICES = np.array([1, 2, 3, 4, 5], dtype=np.float64)


def octave_amps(n_octaves):
    return np.array([AMP_DECAY ** i for i in range(n_octaves)])


def make_harmonics(rng: np.random.RandomState, n, n_octaves=3):
    """Per-instance harmonic numbers + phases, built once at construction
    time. Returns (harmonics[n,n_octaves], phases[n,n_octaves])."""
    idx = rng.randint(0, len(K_CHOICES), size=(n, n_octaves))
    harmonics = K_CHOICES[idx]
    phases = rng.uniform(0, 2 * math.pi, size=(n, n_octaves))
    return harmonics, phases


def periodic_batch(t, harmonics, phases, amps, duration=DURATION_S):
    """Vectorized evaluation for an entire population at time t.
    Returns array of shape (n,), range approx [-1, 1]."""
    arg = (2 * math.pi * (t / duration)) * harmonics + phases
    vals = np.sin(arg) * amps[np.newaxis, :]
    return vals.sum(axis=1) / amps.sum()


def make_signal_params(seed, n_octaves=2):
    rng = np.random.RandomState(seed)
    harmonics, phases = make_harmonics(rng, 1, n_octaves)
    return harmonics[0], phases[0], octave_amps(n_octaves)


def eval_signal(t, params, duration=DURATION_S):
    harmonics, phases, amps = params
    val = 0.0
    for k, ph, a in zip(harmonics, phases, amps):
        val += a * math.sin(2 * math.pi * k * t / duration + ph)
    return val / amps.sum()


# A handful of named, art-directed global signals (deliberately simple,
# hand-tuned rather than randomized, since they are singular "camera" /
# "wind gust" effects rather than a population needing organic variety).
WIND_GUST_PARAMS = make_signal_params(SEED + 1, n_octaves=2)
RIBBON_DRIFT_PARAMS = make_signal_params(SEED + 3, n_octaves=2)


def global_wind_envelope(t):
    raw = eval_signal(t, WIND_GUST_PARAMS)
    return 0.55 + 0.45 * clamp(0.5 + 0.5 * raw, 0.0, 1.0)


def camera_transform(t):
    """Slow single-cycle breathing: always zooms *in* (scale >= 1) so no
    surface edge is ever revealed; small elliptical drift underneath."""
    breath = 0.5 + 0.5 * math.sin(2 * math.pi * 1 * t / DURATION_S)
    scale = 1.0 + 0.010 * breath
    dx = 5.0 * math.sin(2 * math.pi * 1 * t / DURATION_S + math.pi / 2.0)
    dy = 3.0 * math.sin(2 * math.pi * 1 * t / DURATION_S)
    return scale, dx, dy


def apply_camera(ctx, t):
    scale, dx, dy = camera_transform(t)
    cx, cy = WIDTH * 0.5, HEIGHT * 0.55
    ctx.translate(cx + dx, cy + dy)
    ctx.scale(scale, scale)
    ctx.translate(-cx, -cy)


# --------------------------------------------------------------------------
# Season weight schedule (periodic crossfade -> seamless winter->spring loop)
# --------------------------------------------------------------------------


def season_weights(t):
    """Returns a length-4 array of blend weights (sum to 1) for
    spring/summer/autumn/winter at time t. Smooth triangular crossfade,
    fully periodic in t over DURATION_S so the loop point is seamless."""
    pos = (t % DURATION_S) / SEASON_LEN  # 0..4 continuous
    raw = np.zeros(4)
    for i in range(4):
        delta = wrapped_delta(pos, i + 0.5, 4.0)
        raw[i] = clamp(1.6 - 1.6 * abs(delta), 0.0, 1.0)
    s = raw.sum()
    if s <= 1e-6:
        raw[:] = 0.25
        s = 1.0
    return raw / s


# --------------------------------------------------------------------------
# Cyclic keyframe curves for foliage "presence" (growth / shedding), decoupled
# from color so a leaf can be autumn-colored yet fading in the same breath.
# Positions are in season-units (0..4, periodic); each table's first and last
# entries match so the curve is exactly continuous across the loop point.
# --------------------------------------------------------------------------

# Morphological (not merely recolored) season arc:
#  spring (0-1):  sparse/twiggy, open sky, ramping from near-bare
#  summer (1-2):  heavy, closed, sagging canopy at full presence
#  autumn (2-3):  thins outside-in with real gaps (see OUTSIDE_PHASE_SHIFT)
#  winter (3-4):  genuinely bare (near-zero floor, not just dim)
# First/last entries match exactly so the loop wraps with zero discontinuity.
LEAF_PRESENCE_KEYFRAMES = [
    (0.0, 0.03), (0.30, 0.08), (0.65, 0.32), (1.0, 0.58),
    (1.25, 0.86), (1.55, 1.0), (2.0, 1.0),
    (2.35, 0.90), (2.65, 0.60), (2.9, 0.30), (3.15, 0.10),
    (3.4, 0.03), (4.0, 0.03),
]

BLOSSOM_PRESENCE_KEYFRAMES = [
    (0.0, 0.0), (0.12, 0.06), (0.32, 0.45), (0.5, 0.95), (0.68, 0.55),
    (0.88, 0.10), (1.0, 0.0), (3.0, 0.0), (3.9, 0.0), (4.0, 0.0),
]

# "Outsideness" phase-advance: clusters further from the trunk (higher
# t_local along their branch, deeper branch generation) are pushed slightly
# *ahead* in the yearly phase. Because the presence curve is cyclic, a single
# consistent phase-advance makes outer growth bud first in spring (a
# staggered wave) *and* shed first in autumn (thinning outside-in), from one
# unified mechanism rather than separate hacks.
OUTSIDE_PHASE_ADVANCE_S = 1.6


def cyclic_interp(pos, keyframes, period=4.0):
    pos = pos % period
    for i in range(len(keyframes) - 1):
        x0, v0 = keyframes[i]
        x1, v1 = keyframes[i + 1]
        if x0 <= pos <= x1:
            f = smoothstep(x0, x1, pos)
            return lerp(v0, v1, f)
    return keyframes[-1][1]


def leaf_presence(t, offset_s):
    pos = ((t - offset_s) % DURATION_S) / SEASON_LEN
    return cyclic_interp(pos, LEAF_PRESENCE_KEYFRAMES)


def blossom_presence(t, offset_s):
    pos = ((t - offset_s) % DURATION_S) / SEASON_LEN
    return cyclic_interp(pos, BLOSSOM_PRESENCE_KEYFRAMES)


# --------------------------------------------------------------------------
# Season palettes (dark, cinematic, mirror-friendly, gently desaturated)
# --------------------------------------------------------------------------

PALETTE = {
    SPRING: dict(
        sky_top=(6, 10, 22), sky_bot=(13, 23, 33),
        glow=(72, 112, 122), ground=(8, 14, 16),
        bark=(34, 26, 22), bark_hi=(60, 47, 36),
        bark_glow=(120, 150, 110),
        leaf=(92, 148, 86), leaf_hi=(142, 190, 112),
        blossom=(233, 200, 210), blossom_hi=(251, 236, 239),
        particle=(240, 205, 214), ribbon=(120, 170, 160),
    ),
    SUMMER: dict(
        sky_top=(4, 12, 16), sky_bot=(7, 21, 21),
        glow=(62, 132, 112), ground=(6, 16, 12),
        bark=(30, 24, 18), bark_hi=(54, 41, 29),
        bark_glow=(150, 190, 110),
        leaf=(38, 98, 56), leaf_hi=(86, 152, 80),
        blossom=(210, 210, 150), blossom_hi=(235, 235, 190),
        particle=(232, 212, 132), ribbon=(90, 180, 150),
    ),
    AUTUMN: dict(
        sky_top=(14, 8, 10), sky_bot=(29, 15, 10),
        glow=(152, 92, 50), ground=(16, 10, 8),
        bark=(36, 26, 20), bark_hi=(62, 43, 30),
        bark_glow=(200, 130, 60),
        leaf=(178, 98, 34), leaf_hi=(216, 152, 60),
        blossom=(198, 94, 40), blossom_hi=(226, 142, 60),
        particle=(202, 112, 46), ribbon=(190, 120, 60),
    ),
    WINTER: dict(
        sky_top=(6, 10, 20), sky_bot=(15, 21, 34),
        glow=(122, 152, 192), ground=(14, 18, 24),
        bark=(24, 22, 24), bark_hi=(72, 76, 84),
        bark_glow=(160, 190, 230),
        leaf=(212, 222, 232), leaf_hi=(246, 249, 253),
        blossom=(231, 236, 246), blossom_hi=(255, 255, 255),
        particle=(236, 241, 251), ribbon=(150, 190, 230),
    ),
}


def blended_palette(w):
    keys = PALETTE[0].keys()
    out = {}
    for k in keys:
        acc = [0.0, 0.0, 0.0]
        for s in range(4):
            c = PALETTE[s][k]
            for i in range(3):
                acc[i] += c[i] * w[s]
        out[k] = tuple(acc)
    return out


# Seasonal key-light: normalized (x, y) position (fraction of W/H) plus a
# rake angle (degrees from straight-down) used both to place the soft glow
# and to aim faint canopy-gap light shafts, so the light genuinely moves and
# changes character across the year instead of sitting as a static flare.
# Autumn = low warm rake; winter = high, diffuse, cool; spring/summer sit
# higher and softer.
KEY_LIGHT = {
    SPRING: dict(pos=(0.78, 0.22), angle=26, warmth=0.15),
    SUMMER: dict(pos=(0.72, 0.15), angle=16, warmth=0.0),
    AUTUMN: dict(pos=(0.92, 0.50), angle=58, warmth=1.0),
    WINTER: dict(pos=(0.62, 0.19), angle=8, warmth=-1.0),
}


def blended_key_light(w):
    px = sum(KEY_LIGHT[s]["pos"][0] * w[s] for s in range(4))
    py = sum(KEY_LIGHT[s]["pos"][1] * w[s] for s in range(4))
    ang = sum(KEY_LIGHT[s]["angle"] * w[s] for s in range(4))
    warmth = sum(KEY_LIGHT[s]["warmth"] * w[s] for s in range(4))
    return px, py, ang, warmth


# --------------------------------------------------------------------------
# Tree skeleton generation (built once, deterministic)
# --------------------------------------------------------------------------

MAX_DEPTH = 9
LEAF_MIN_DEPTH = 5


class Branch:
    __slots__ = (
        "id", "parent", "depth", "local_angle", "length", "thickness",
        "curvature", "curvature2", "wind_amp", "taper_wobble", "outsideness",
    )

    def __init__(self, id_, parent, depth, local_angle, length, thickness,
                 curvature, curvature2, wind_amp, taper_wobble):
        self.id = id_
        self.parent = parent
        self.depth = depth
        self.local_angle = local_angle
        self.length = length
        self.thickness = thickness
        self.curvature = curvature
        self.curvature2 = curvature2
        self.wind_amp = wind_amp
        self.taper_wobble = taper_wobble
        # 0 = trunk/interior, 1 = outermost twig; drives frost-creep order
        # (outer twigs frost first) and feeds cluster phase-advance.
        self.outsideness = clamp(depth / MAX_DEPTH)


class LeafCluster:
    __slots__ = (
        "node_id", "t_local", "is_blossom", "hue_shift", "offset_s",
        "outsideness", "anchors",
    )

    def __init__(self, node_id, t_local, is_blossom, hue_shift, offset_s, outsideness):
        self.node_id = node_id
        self.t_local = t_local
        self.is_blossom = is_blossom
        self.hue_shift = hue_shift
        self.offset_s = offset_s
        self.outsideness = outsideness
        # list of (along_off, perp_off, size, rot, variant, is_blossom,
        #          anchor_offset_s, hue_jitter, curl_seed, sag_seed)
        self.anchors = []


def rest_endpoint(base_xy, abs_angle_deg, length):
    rad = math.radians(abs_angle_deg)
    return (base_xy[0] + math.sin(rad) * length, base_xy[1] - math.cos(rad) * length)


# Build-time safety buffer around the true widget quiet-zone: bigger than
# QUIET_X/QUIET_Y so that after adding wind sway, cluster scatter radius, and
# camera breathing, animated foliage still never visually enters the zone
# used by draw_vignette/the widget overlay.
BUILD_QUIET_X = QUIET_X + 150
BUILD_QUIET_Y = QUIET_Y + 110


def _in_zone(x, y):
    return x < BUILD_QUIET_X and y < BUILD_QUIET_Y


def keep_out_of_quiet_zone(base_xy, parent_abs_angle, local_angle, length, max_iter=10):
    """Nudges local_angle (in +angle / rightward direction) until both the
    rest (sway=0) endpoint *and* the segment midpoint clear a buffered
    upper-left quiet zone, so a branch can never visually cut across the
    widget corner even once wind sway and cluster scatter are added."""
    angle = local_angle
    for _ in range(max_iter):
        aa = parent_abs_angle + angle
        ex, ey = rest_endpoint(base_xy, aa, length)
        mx, my = (base_xy[0] + ex) * 0.5, (base_xy[1] + ey) * 0.5
        if not (_in_zone(ex, ey) or _in_zone(mx, my)):
            return angle, aa, (ex, ey)
        angle += 7.0
    aa = parent_abs_angle + angle
    ex, ey = rest_endpoint(base_xy, aa, length)
    return angle, aa, (ex, ey)


def build_tree(rng: np.random.RandomState):
    branches: list[Branch] = []
    clusters: list[LeafCluster] = []
    next_id = [0]

    root_local_angle = 3.0
    root = Branch(
        id_=0, parent=-1, depth=0, local_angle=root_local_angle,
        length=0.315 * HEIGHT, thickness=0.021 * WIDTH,
        curvature=rng.uniform(-5, 5), curvature2=rng.uniform(-4, 4),
        wind_amp=1.0, taper_wobble=rng.uniform(0.94, 1.06),
    )
    branches.append(root)
    next_id[0] = 1
    root_abs_angle = root_local_angle
    root_end = rest_endpoint(TRUNK_BASE, root_abs_angle, root.length)

    def recurse(parent_id, depth, length, thickness, parent_abs_angle, parent_end_xy):
        if depth >= MAX_DEPTH or length < 15 or thickness < 1.05:
            return
        n = 3 if (depth <= 2 and rng.uniform() < 0.4) else 2
        for i in range(n):
            side = (-1, 0, 1)[i] if n == 3 else (-1, 1)[i]
            if side < 0:
                # graceful inward arcs -- modest range; kept away from the
                # quiet zone by keep_out_of_quiet_zone below, and given
                # gentler wind (see sway_mult) since they sit close to the
                # widget boundary.
                local_angle = rng.uniform(-24, -6) - depth * 0.55
                len_mult = rng.uniform(0.63, 0.74)
                sway_mult = 0.45
            elif side == 0:
                local_angle = rng.uniform(-7, 9)
                len_mult = rng.uniform(0.70, 0.80)
                sway_mult = 0.85
            else:
                local_angle = rng.uniform(18, 44) + depth * 0.45
                len_mult = rng.uniform(0.72, 0.84)
                sway_mult = 1.0

            local_angle, child_abs_angle, child_end = keep_out_of_quiet_zone(
                parent_end_xy, parent_abs_angle, local_angle, length * len_mult
            )

            child_len = length * len_mult
            child_thick = thickness * rng.uniform(0.63, 0.75)
            curvature = rng.uniform(-16, 16)
            curvature2 = rng.uniform(-10, 10)
            wind_amp = min(1.0 + depth * 1.15, 9.0) * sway_mult
            taper_wobble = rng.uniform(0.90, 1.10)

            child = Branch(
                id_=next_id[0], parent=parent_id, depth=depth + 1,
                local_angle=local_angle, length=child_len,
                thickness=child_thick, curvature=curvature,
                curvature2=curvature2, wind_amp=wind_amp,
                taper_wobble=taper_wobble,
            )
            branches.append(child)
            cid = next_id[0]
            next_id[0] += 1

            if depth + 1 >= LEAF_MIN_DEPTH:
                cluster_prob = clamp(0.30 + 0.11 * (depth + 1 - LEAF_MIN_DEPTH))
                if rng.uniform() < cluster_prob:
                    t_local = rng.uniform(0.5, 1.0)
                    is_blossom = rng.uniform() < 0.34
                    hue_shift = rng.uniform(-12, 12)
                    offset_s = rng.uniform(-2.2, 2.2)
                    outsideness = clamp(0.55 * child.outsideness + 0.45 * t_local)
                    cluster = LeafCluster(cid, t_local, is_blossom, hue_shift, offset_s, outsideness)
                    m = rng.randint(6, 13)
                    radius_scale = 15.0 + depth * 1.4
                    for _ in range(m):
                        r = min(abs(rng.normal(0, 1.0)) * radius_scale, radius_scale * 2.1)
                        theta = rng.uniform(0, 2 * math.pi)
                        along_off = math.cos(theta) * r
                        perp_off = math.sin(theta) * r
                        size = rng.uniform(11, 19) * (1.0 - 0.03 * depth)
                        rot = rng.uniform(0, 360)
                        variant = int(rng.randint(0, 3))
                        anchor_is_blossom = is_blossom
                        if rng.uniform() < 0.12:
                            anchor_is_blossom = not anchor_is_blossom
                        anchor_offset_s = rng.uniform(-0.7, 0.7)
                        hue_jitter = rng.uniform(-14, 14)
                        curl_seed = rng.uniform(0.0, 1.0)
                        sag_seed = rng.uniform(0.6, 1.4)
                        cluster.anchors.append(
                            (along_off, perp_off, max(size, 6.0), rot, variant,
                             anchor_is_blossom, anchor_offset_s, hue_jitter,
                             curl_seed, sag_seed)
                        )
                    clusters.append(cluster)

            recurse(cid, depth + 1, child_len, child_thick, child_abs_angle, child_end)

    recurse(0, 0, root.length, root.thickness, root_abs_angle, root_end)
    return branches, clusters


# --------------------------------------------------------------------------
# Particle systems (deterministic, periodic over DURATION_S)
# --------------------------------------------------------------------------


class ParticlePool:
    def __init__(self, rng, kind, count):
        self.kind = kind
        self.count = count
        self.x0 = rng.uniform(0.0, 1.0, count)
        self.phase = rng.uniform(0.0, DURATION_S, count)
        raw_period = rng.uniform(6.0, 15.0, count)
        # Snap each particle's private fall-loop period so DURATION_S is an
        # exact integer multiple of it -> guarantees perfectly seamless
        # per-particle motion (position AND rotation) at the master loop
        # boundary; both position and spin below must use this snapped
        # value, not the raw one, or the wrap would be discontinuous.
        n_loops = np.maximum(1, np.round(DURATION_S / raw_period))
        self.period = DURATION_S / n_loops
        self.sway_amp = rng.uniform(16, 66, count)
        self.size = rng.uniform(0.55, 1.35, count)
        self.rot0 = rng.uniform(0, 360, count)
        self.depth = rng.uniform(0.35, 1.0, count)  # parallax / blur factor
        self.sway_harm, self.sway_phase = make_harmonics(rng, count, n_octaves=2)
        self.sway_amps = octave_amps(2)
        # quiet-zone damping: particles whose spawn column sits in the
        # widget corner are drawn much fainter (faint atmosphere only).
        self.quiet_mask = (self.x0 * WIDTH < QUIET_X).astype(np.float64)


def make_particle_pools():
    pools = {}
    pools["petal"] = ParticlePool(np.random.RandomState(SEED + 11), "petal", 50)
    pools["pollen"] = ParticlePool(np.random.RandomState(SEED + 12), "pollen", 64)
    pools["leaf_fall"] = ParticlePool(np.random.RandomState(SEED + 13), "leaf_fall", 56)
    pools["snow"] = ParticlePool(np.random.RandomState(SEED + 14), "snow", 110)
    pools["dust"] = ParticlePool(np.random.RandomState(SEED + 15), "dust", 80)
    return pools


def particle_positions(pool: ParticlePool, t, drag=False):
    """Vectorized per-frame position/rotation/fade for a whole pool. Uses the
    pre-snapped ``pool.period`` (see ParticlePool.__init__) for both position
    and spin so the loop wrap is exact. ``drag`` (used for tumbling autumn
    leaves) warps the fall as a deterministic function of the already-
    periodic ``frac``, so it adds air-resistance flutter/hesitation without
    breaking the exact loop seam."""
    local_t = (t + pool.phase) % pool.period
    frac = local_t / pool.period
    sway_vals = periodic_batch(t, pool.sway_harm, pool.sway_phase, pool.sway_amps)
    sway = pool.sway_amp * sway_vals
    spin = 360.0 * (t / np.maximum(pool.period * 0.5, 0.5)) + pool.rot0
    if drag:
        flutter = 0.06 * np.sin(2 * np.pi * 3 * frac + np.radians(pool.rot0))
        frac_fall = np.clip(frac + flutter, 0.0, 1.0)
        sway = sway + pool.sway_amp * 0.55 * np.sin(2 * np.pi * 5 * frac)
        spin = spin + 45.0 * np.sin(2 * np.pi * 2 * frac + np.radians(pool.rot0))
    else:
        frac_fall = frac
    x = pool.x0 * WIDTH + sway
    y = frac_fall * HEIGHT
    fade = np.sin(np.clip(frac, 0, 1) * math.pi)
    return x, y, spin, np.clip(fade, 0.0, 1.0)


# --------------------------------------------------------------------------
# Drawing helpers (cairo) — organic bezier shapes, never plain circles
# --------------------------------------------------------------------------


def set_rgba(ctx, color, alpha):
    r, g, b = color
    ctx.set_source_rgba(r / 255.0, g / 255.0, b / 255.0, clamp(alpha))


def draw_leaf(ctx, cx, cy, size, angle_deg, color, alpha, variant=0, curl=0.0):
    """Three distinct silhouettes (rounded / slender-pointed / small
    three-lobe) instead of one uniform almond stamp; ``curl`` (0..1) skews
    the tip and narrows one side to read as a drying/curling autumn leaf."""
    if alpha <= 0.004:
        return
    ctx.save()
    ctx.translate(cx, cy)
    ctx.rotate(math.radians(angle_deg))
    curl = clamp(curl)
    tip_bend = size * 0.22 * curl  # tip hooks sideways as it dries/curls

    if variant == 0:
        w, h = size * 0.56, size * 0.92
        ctx.move_to(0, -h)
        ctx.curve_to(w, -h * 0.5, w * (0.86 - 0.3 * curl), h * 0.4, tip_bend, h)
        ctx.curve_to(-w * (0.86 - 0.55 * curl), h * 0.4, -w * (1.0 - 0.35 * curl), -h * 0.5, 0, -h)
    elif variant == 1:
        w, h = size * 0.36, size * 1.16
        ctx.move_to(0, -h)
        ctx.curve_to(w * 0.9, -h * 0.35, w * (0.58 - 0.3 * curl), h * 0.55, tip_bend, h)
        ctx.curve_to(-w * (0.58 - 0.55 * curl), h * 0.55, -w * 0.9, -h * 0.35, 0, -h)
    else:
        w, h = size * 0.62, size * 0.82
        ctx.move_to(0, -h)
        ctx.curve_to(w * 0.95, -h * 0.2, w * 0.58, h * 0.15, w * (0.6 - 0.25 * curl), h * 0.55)
        ctx.curve_to(w * 0.28, h * 0.85, -w * 0.28, h * 0.85, -w * (0.6 - 0.45 * curl), h * 0.55)
        ctx.curve_to(-w * 0.58, h * 0.15, -w * 0.95, -h * 0.2, 0, -h)
    ctx.close_path()
    set_rgba(ctx, color, alpha)
    ctx.fill_preserve()
    set_rgba(ctx, (max(color[0] - 30, 0), max(color[1] - 30, 0), max(color[2] - 30, 0)), alpha * 0.5)
    ctx.set_line_width(max(size * 0.04, 0.4))
    ctx.stroke()
    if curl < 0.55:
        set_rgba(ctx, (min(color[0] + 25, 255), min(color[1] + 25, 255), min(color[2] + 25, 255)), alpha * 0.35)
        ctx.set_line_width(max(size * 0.03, 0.3))
        ctx.move_to(0, -h * 0.85)
        ctx.curve_to(tip_bend * 0.3, 0, tip_bend * 0.6, h * 0.5, tip_bend, h * 0.85)
        ctx.stroke()
    ctx.restore()


def draw_leaf_glow(ctx, cx, cy, size, angle_deg, color, alpha):
    """Small additive highlight for the bloom pass — a soft core, not the
    full leaf silhouette."""
    if alpha <= 0.006:
        return
    grad = cairo.RadialGradient(cx, cy, 0, cx, cy, size * 0.7)
    r, g, b = color
    grad.add_color_stop_rgba(0, r / 255.0, g / 255.0, b / 255.0, alpha)
    grad.add_color_stop_rgba(1, r / 255.0, g / 255.0, b / 255.0, 0.0)
    ctx.set_source(grad)
    ctx.arc(cx, cy, size * 0.7, 0, 2 * math.pi)
    ctx.fill()


def draw_blossom(ctx, cx, cy, size, angle_deg, color, hi_color, alpha):
    if alpha <= 0.004 or size <= 0.3:
        return
    ctx.save()
    ctx.translate(cx, cy)
    ctx.rotate(math.radians(angle_deg))
    petal_len = size * 0.62
    petal_w = size * 0.42
    for i in range(5):
        ctx.save()
        ctx.rotate(math.radians(72 * i))
        ctx.move_to(0, 0)
        ctx.curve_to(petal_w, petal_len * 0.35, petal_w * 0.55, petal_len, 0, petal_len)
        ctx.curve_to(-petal_w * 0.55, petal_len, -petal_w, petal_len * 0.35, 0, 0)
        ctx.close_path()
        set_rgba(ctx, color, alpha)
        ctx.fill()
        ctx.restore()
    set_rgba(ctx, hi_color, alpha)
    ctx.arc(0, 0, size * 0.14, 0, 2 * math.pi)
    ctx.fill()
    ctx.restore()


def draw_snowflake(ctx, cx, cy, size, angle_deg, color, alpha):
    if alpha <= 0.004:
        return
    ctx.save()
    ctx.translate(cx, cy)
    ctx.rotate(math.radians(angle_deg))
    set_rgba(ctx, color, alpha)
    ctx.set_line_width(max(size * 0.16, 0.6))
    ctx.set_line_cap(cairo.LINE_CAP_ROUND)
    for i in range(3):
        ctx.save()
        ctx.rotate(math.radians(60 * i))
        ctx.move_to(0, -size)
        ctx.line_to(0, size)
        ctx.move_to(0, -size * 0.55)
        ctx.line_to(size * 0.28, -size * 0.8)
        ctx.move_to(0, -size * 0.55)
        ctx.line_to(-size * 0.28, -size * 0.8)
        ctx.stroke()
        ctx.restore()
    ctx.restore()


def draw_petal_particle(ctx, cx, cy, size, angle_deg, color, alpha):
    ctx.save()
    ctx.translate(cx, cy)
    ctx.rotate(math.radians(angle_deg))
    w = size * 3.2
    h = size * 5.5
    ctx.move_to(0, -h * 0.5)
    ctx.curve_to(w, -h * 0.2, w * 0.7, h * 0.35, 0, h * 0.5)
    ctx.curve_to(-w * 0.7, h * 0.35, -w, -h * 0.2, 0, -h * 0.5)
    ctx.close_path()
    set_rgba(ctx, color, alpha)
    ctx.fill()
    ctx.restore()


def draw_leaf_particle(ctx, cx, cy, size, angle_deg, color, alpha):
    draw_leaf(ctx, cx, cy, size * 5.5, angle_deg, color, alpha)


def draw_glow_dot(ctx, cx, cy, r, color, alpha):
    if alpha <= 0.004 or r <= 0.2:
        return
    grad = cairo.RadialGradient(cx, cy, 0, cx, cy, r)
    rr, gg, bb = color
    grad.add_color_stop_rgba(0, rr / 255.0, gg / 255.0, bb / 255.0, alpha)
    grad.add_color_stop_rgba(1, rr / 255.0, gg / 255.0, bb / 255.0, 0.0)
    ctx.set_source(grad)
    ctx.arc(cx, cy, r, 0, 2 * math.pi)
    ctx.fill()


def draw_ribbon(ctx, t, seed, color, base_x, base_y, width, height, drift_amp, alpha):
    """A soft, low-alpha watercolor-like current: a tall, gently curved band
    filled with a linear gradient that fades at both long edges."""
    drift = drift_amp * eval_signal(t, RIBBON_DRIFT_PARAMS)
    rng_local = np.random.RandomState(seed)
    bow1 = rng_local.uniform(-1, 1) * width * 0.9
    bow2 = rng_local.uniform(-1, 1) * width * 0.9
    x = base_x + drift
    ctx.save()
    ctx.move_to(x - width * 0.5, base_y)
    ctx.curve_to(x - width * 0.5 + bow1, base_y - height * 0.33,
                 x - width * 0.5 + bow2, base_y - height * 0.66,
                 x - width * 0.5, base_y - height)
    ctx.line_to(x + width * 0.5, base_y - height)
    ctx.curve_to(x + width * 0.5 + bow2, base_y - height * 0.66,
                 x + width * 0.5 + bow1, base_y - height * 0.33,
                 x + width * 0.5, base_y)
    ctx.close_path()
    grad = cairo.LinearGradient(x - width * 0.5, 0, x + width * 0.5, 0)
    r, g, b = color
    grad.add_color_stop_rgba(0.0, r / 255.0, g / 255.0, b / 255.0, 0.0)
    grad.add_color_stop_rgba(0.5, r / 255.0, g / 255.0, b / 255.0, alpha)
    grad.add_color_stop_rgba(1.0, r / 255.0, g / 255.0, b / 255.0, 0.0)
    ctx.set_source(grad)
    ctx.fill()
    ctx.restore()


# --------------------------------------------------------------------------
# Background field: 2-4 low-luminance, strongly defocused secondary
# tree/canopy forms at distinct depths, bleeding off the left/bottom edges,
# built once (deterministic) and heavily Gaussian-blurred so the composition
# reads as an atmospheric field rather than a single corner-slab silhouette.
# --------------------------------------------------------------------------

BG_SCALE = 0.28
BG_W = max(int(WIDTH * BG_SCALE), 2)
BG_H = max(int(HEIGHT * BG_SCALE), 2)


def build_organic_blob_path(ctx, cx, cy, rx, ry, rng, n=9, wobble=0.32):
    """A smooth closed wobbly blob (Catmull-Rom -> bezier) around an
    ellipse -- reads as an organic canopy mass, never a circle/ellipse."""
    pts = []
    for i in range(n):
        theta = 2 * math.pi * i / n
        rr = 1.0 + rng.uniform(-wobble, wobble)
        pts.append((cx + math.cos(theta) * rx * rr, cy + math.sin(theta) * ry * rr))
    n_pts = len(pts)
    ctx.move_to(*pts[0])
    for i in range(n_pts):
        p0 = pts[(i - 1) % n_pts]
        p1 = pts[i]
        p2 = pts[(i + 1) % n_pts]
        p3 = pts[(i + 2) % n_pts]
        c1 = (p1[0] + (p2[0] - p0[0]) / 6.0, p1[1] + (p2[1] - p0[1]) / 6.0)
        c2 = (p2[0] - (p3[0] - p1[0]) / 6.0, p2[1] - (p3[1] - p1[1]) / 6.0)
        ctx.curve_to(c1[0], c1[1], c2[0], c2[1], p2[0], p2[1])
    ctx.close_path()


class BgLayer:
    __slots__ = ("mask", "parallax", "alpha", "drift_amp", "drift_phase_s", "tint_mix")

    def __init__(self, mask, parallax, alpha, drift_amp, drift_phase_s, tint_mix):
        self.mask = mask  # (BG_H, BG_W) float32 in [0,1]
        self.parallax = parallax
        self.alpha = alpha
        self.drift_amp = drift_amp
        self.drift_phase_s = drift_phase_s
        self.tint_mix = tint_mix


# (cx_frac, cy_frac, rx, ry, n_blobs, trunk, alpha, parallax, drift_amp, drift_phase, tint_mix, seed)
BG_LAYER_SPECS = [
    (0.03, 0.34, 190, 150, 2, False, 0.13, 0.20, 5.0, 2.0, 0.20, SEED + 301),
    (-0.05, 0.66, 300, 250, 3, True, 0.25, 0.42, 9.0, 8.0, 0.32, SEED + 302),
    (0.16, 0.92, 260, 210, 2, True, 0.21, 0.60, 12.0, 14.0, 0.40, SEED + 303),
    (0.32, 0.80, 170, 150, 2, False, 0.13, 0.75, 14.0, 19.0, 0.28, SEED + 304),
]


def build_bg_layers():
    """Bakes each background form once into a heavily-blurred low-res alpha
    mask (deterministic, seeded). Kept well clear of the widget rectangle
    (all cy_frac anchors sit at/below y=0.34H) and mostly bleeding off the
    left/bottom edges per the art direction."""
    layers = []
    for (cxf, cyf, rx, ry, n_blobs, trunk, alpha, parallax, drift_amp,
         drift_phase, tint_mix, seed) in BG_LAYER_SPECS:
        rng = np.random.RandomState(seed)
        cx, cy = cxf * WIDTH, cyf * HEIGHT
        surf = cairo.ImageSurface(cairo.FORMAT_A8, BG_W, BG_H)
        bctx = cairo.Context(surf)
        bctx.scale(BG_SCALE, BG_SCALE)
        bctx.set_source_rgba(0, 0, 0, 1.0)
        if trunk:
            bctx.set_line_cap(cairo.LINE_CAP_ROUND)
            bctx.set_line_width(rx * 0.16)
            bctx.move_to(cx, cy + ry * 1.7)
            bctx.curve_to(cx + rx * 0.22, cy + ry * 0.9, cx - rx * 0.18, cy + ry * 0.35, cx, cy)
            bctx.stroke()
        for i in range(n_blobs):
            ang = rng.uniform(0, 2 * math.pi)
            bx = cx + math.cos(ang) * rx * 0.32 * i
            by = cy + math.sin(ang) * ry * 0.28 * i - ry * 0.15 * i
            build_organic_blob_path(bctx, bx, by, rx * rng.uniform(0.78, 1.05),
                                     ry * rng.uniform(0.78, 1.05), rng)
            bctx.fill()
        surf.flush()
        stride = surf.get_stride()
        raw = np.ndarray(shape=(BG_H, stride), dtype=np.uint8, buffer=surf.get_data())
        mask = raw[:, :BG_W].astype(np.float32) / 255.0
        sigma = max(BG_W, BG_H) * 0.045
        mask = cv2.GaussianBlur(mask, (0, 0), sigmaX=sigma)
        layers.append(BgLayer(mask, parallax, alpha, drift_amp, drift_phase, tint_mix))
    return layers


def draw_bg_layers(ctx, t, pal, bg_layers):
    """Composites the pre-baked, pre-blurred background forms as tinted
    soft masks with per-layer drift (distinct depths), well behind the ridge
    and hero tree. Drift/resize happens at the cheap low-res mask size; only
    the final full-res alpha buffer feed to cairo is upsampled."""
    base_tint = lerp_color(pal["ground"], pal["leaf"], 0.30)
    for layer in bg_layers:
        drift_px = layer.drift_amp * eval_signal(t + layer.drift_phase_s, RIBBON_DRIFT_PARAMS)
        tint = lerp_color(base_tint, pal["glow"], layer.tint_mix)
        tint = tuple(c * (0.45 + 0.15 * layer.parallax) for c in tint)

        M = np.array([[1, 0, drift_px * BG_SCALE], [0, 1, 0]], dtype=np.float32)
        shifted = cv2.warpAffine(layer.mask, M, (BG_W, BG_H), borderMode=cv2.BORDER_REPLICATE)
        full_mask = cv2.resize(shifted, (WIDTH, HEIGHT), interpolation=cv2.INTER_LINEAR)

        stride = cairo.ImageSurface.format_stride_for_width(cairo.FORMAT_A8, WIDTH)
        buf = np.zeros((HEIGHT, stride), dtype=np.uint8)
        buf[:, :WIDTH] = np.clip(full_mask * layer.alpha * 255.0, 0, 255).astype(np.uint8)
        surf = cairo.ImageSurface.create_for_data(buf, cairo.FORMAT_A8, WIDTH, HEIGHT, stride)

        ctx.save()
        set_rgba(ctx, tint, 1.0)
        ctx.mask_surface(surf, 0, 0)
        ctx.restore()


def draw_light_shafts(ctx, glow_x, glow_y, angle_deg, warmth, pal, strength):
    """Faint, intentional canopy-gap light shafts anchored to the seasonal
    key light -- a few soft tapered gradient beams, not a static flare."""
    if strength <= 0.01:
        return
    warm = clamp(0.5 + 0.5 * warmth)
    base_col = lerp_color((150, 190, 235), (255, 220, 170), warm)
    base_col = lerp_color(pal["glow"], base_col, 0.55)
    rad = math.radians(angle_deg)
    dirx, diry = -math.sin(rad), math.cos(rad)
    perp = (diry, -dirx)
    ctx.save()
    for off, length, width, a in (
        (-38, HEIGHT * 0.50, 58, 0.032),
        (26, HEIGHT * 0.60, 44, 0.026),
        (86, HEIGHT * 0.42, 34, 0.020),
    ):
        ox, oy = glow_x + perp[0] * off, glow_y + perp[1] * off
        ex, ey = ox + dirx * length, oy + diry * length
        grad = cairo.LinearGradient(ox, oy, ex, ey)
        r, g, b = base_col
        alpha = a * strength
        grad.add_color_stop_rgba(0.0, r / 255.0, g / 255.0, b / 255.0, alpha)
        grad.add_color_stop_rgba(1.0, r / 255.0, g / 255.0, b / 255.0, 0.0)
        ctx.move_to(ox - perp[0] * width * 0.5, oy - perp[1] * width * 0.5)
        ctx.line_to(ox + perp[0] * width * 0.5, oy + perp[1] * width * 0.5)
        ctx.line_to(ex + perp[0] * width * 0.12, ey + perp[1] * width * 0.12)
        ctx.line_to(ex - perp[0] * width * 0.12, ey - perp[1] * width * 0.12)
        ctx.close_path()
        ctx.set_source(grad)
        ctx.fill()
    ctx.restore()


def draw_foreground_fragments(ctx, t, pal):
    """A couple of very-low-alpha out-of-focus branch fragments bleeding
    from the frame edges -- a foreground depth cue, kept far from the
    widget rectangle (both fragments sit below y=0.24H, one on each side)."""
    drift = 6.0 * eval_signal(t + 23.0, RIBBON_DRIFT_PARAMS)
    frag_col = lerp_color(pal["bark"], (10, 12, 16), 0.15)
    specs = [
        (WIDTH * 1.05, HEIGHT * 0.46, WIDTH * 0.80, HEIGHT * 0.64, 46, 0.11),
        (WIDTH * -0.05, HEIGHT * 0.52, WIDTH * 0.16, HEIGHT * 0.70, 38, 0.095),
    ]
    ctx.save()
    ctx.set_line_cap(cairo.LINE_CAP_ROUND)
    for (sx, sy, ex, ey, w, a) in specs:
        mx, my = (sx + ex) * 0.5 + drift, (sy + ey) * 0.5
        set_rgba(ctx, frag_col, a)
        ctx.set_line_width(w * 0.075)
        ctx.move_to(sx, sy)
        ctx.curve_to(mx, my, mx, my, ex, ey)
        ctx.stroke()
    ctx.restore()


# --------------------------------------------------------------------------
# Frame composition
# --------------------------------------------------------------------------


def draw_background(ctx, t, pal, season_w, bg_layers):
    drift = 18.0 * eval_signal(t, RIBBON_DRIFT_PARAMS)
    grad = cairo.LinearGradient(0, 0, WIDTH * 0.25 + drift, HEIGHT)
    grad.add_color_stop_rgb(0, *(c / 255.0 for c in pal["sky_top"]))
    grad.add_color_stop_rgb(1, *(c / 255.0 for c in pal["sky_bot"]))
    ctx.set_source(grad)
    ctx.paint()

    # Seasonal key light: position + rake angle genuinely move (warm/low in
    # autumn, cool/diffuse/high in winter) instead of a static flare.
    lp_x, lp_y, light_angle, warmth = blended_key_light(season_w)
    glow_x = lp_x * WIDTH + 22 * eval_signal(t + 3.1, RIBBON_DRIFT_PARAMS)
    glow_y = lp_y * HEIGHT + 12 * eval_signal(t + 7.7, RIBBON_DRIFT_PARAMS)
    draw_glow_dot(ctx, glow_x, glow_y, WIDTH * 0.62, pal["glow"], 0.10)
    draw_glow_dot(ctx, glow_x, glow_y, WIDTH * 0.24, pal["glow"], 0.11)
    draw_glow_dot(ctx, glow_x, glow_y, WIDTH * 0.09, pal["glow"], 0.09)
    draw_light_shafts(ctx, glow_x, glow_y, light_angle, warmth, pal, 0.55 + 0.45 * season_w[AUTUMN])

    # Background field: low-luminance, strongly defocused secondary
    # tree/canopy forms at distinct depths, bleeding off left/bottom edges.
    draw_bg_layers(ctx, t, pal, bg_layers)

    ridge_drift = 8.0 * eval_signal(t + 11.0, RIBBON_DRIFT_PARAMS)
    ctx.save()
    pts = [0.0, 0.08, 0.15, 0.24, 0.32, 0.41, 0.50, 0.59, 0.68, 0.76, 0.83, 0.92, 1.0]
    heights = [0.90, 0.885, 0.87, 0.878, 0.885, 0.865, 0.86,
               0.868, 0.875, 0.858, 0.855, 0.87, 0.90]
    ridge_pts = [(px * WIDTH + ridge_drift, hy * HEIGHT) for px, hy in zip(pts, heights)]
    # smooth painterly horizon (Catmull-Rom through the ridge points) rather
    # than a jagged low-poly polyline.
    ctx.set_source_rgba(*(c / 255.0 for c in pal["ground"]), 0.85)
    ctx.move_to(0, HEIGHT * 0.90)
    ctx.line_to(*ridge_pts[0])
    n_r = len(ridge_pts)
    for i in range(n_r - 1):
        p0 = ridge_pts[max(i - 1, 0)]
        p1 = ridge_pts[i]
        p2 = ridge_pts[i + 1]
        p3 = ridge_pts[min(i + 2, n_r - 1)]
        c1 = (p1[0] + (p2[0] - p0[0]) / 6.0, p1[1] + (p2[1] - p0[1]) / 6.0)
        c2 = (p2[0] - (p3[0] - p1[0]) / 6.0, p2[1] - (p3[1] - p1[1]) / 6.0)
        ctx.curve_to(c1[0], c1[1], c2[0], c2[1], p2[0], p2[1])
    ctx.line_to(WIDTH, HEIGHT)
    ctx.line_to(0, HEIGHT)
    ctx.close_path()
    ctx.fill()
    # soft mist band along the horizon for a painterly (not hard-edged) join
    mist = cairo.LinearGradient(0, HEIGHT * 0.80, 0, HEIGHT * 0.90)
    mg = lerp_color(pal["ground"], pal["sky_bot"], 0.5)
    mist.add_color_stop_rgba(0.0, mg[0] / 255.0, mg[1] / 255.0, mg[2] / 255.0, 0.0)
    mist.add_color_stop_rgba(1.0, mg[0] / 255.0, mg[1] / 255.0, mg[2] / 255.0, 0.22)
    ctx.set_source(mist)
    ctx.rectangle(0, HEIGHT * 0.78, WIDTH, HEIGHT * 0.14)
    ctx.fill()
    ctx.restore()

    draw_foreground_fragments(ctx, t, pal)

    # Drifting volumetric light ribbons -- watercolor-like currents, kept
    # low-alpha and mostly right-of-quiet-zone.
    ribbon_specs = [
        (401, WIDTH * 0.62, HEIGHT * 1.05, WIDTH * 0.42, HEIGHT * 0.95, 26, 0.05),
        (402, WIDTH * 0.86, HEIGHT * 1.0, WIDTH * 0.30, HEIGHT * 1.05, 20, 0.045),
        (403, WIDTH * 0.42, HEIGHT * 1.1, WIDTH * 0.55, HEIGHT * 1.05, 34, 0.032),
    ]
    for seed, bx, by, w, h, drift_amp, alpha in ribbon_specs:
        draw_ribbon(ctx, t, seed, pal["ribbon"], bx, by, w, h, drift_amp, alpha)


def sample_bezier(sx, sy, c1x, c1y, c2x, c2y, ex, ey, n=6):
    """Samples a cubic bezier at n+1 points, returning (x, y, nx, ny, u) with
    (nx, ny) the unit normal to the *local tangent* (oriented "upward" where
    possible). Used to build offset ribbons that hug the true curve exactly
    -- never a floating straight-line segment across a curved branch."""
    pts = []
    for i in range(n + 1):
        u = i / n
        mu = 1.0 - u
        x = mu ** 3 * sx + 3 * mu * mu * u * c1x + 3 * mu * u * u * c2x + u ** 3 * ex
        y = mu ** 3 * sy + 3 * mu * mu * u * c1y + 3 * mu * u * u * c2y + u ** 3 * ey
        dx = 3 * mu * mu * (c1x - sx) + 6 * mu * u * (c2x - c1x) + 3 * u * u * (ex - c2x)
        dy = 3 * mu * mu * (c1y - sy) + 6 * mu * u * (c2y - c1y) + 3 * u * u * (ey - c2y)
        dlen = math.hypot(dx, dy) or 1.0
        tx, ty = dx / dlen, dy / dlen
        nx, ny = -ty, tx
        if ny > 0:
            nx, ny = -nx, -ny
        pts.append((x, y, nx, ny, u))
    return pts


def draw_curve_rim_solid(ctx, samples, half_width_fn, color, alpha):
    """Tapered ribbon hugging one side of ``samples`` at uniform alpha (bark
    rim-light): inner edge rides the curve itself, outer edge offsets by
    ``half_width_fn(u)`` along the local normal, so the highlight follows the
    bark's true bow/curvature instead of a straight chord."""
    if alpha <= 0.004:
        return
    outer = [(x + nx * half_width_fn(u), y + ny * half_width_fn(u)) for (x, y, nx, ny, u) in samples]
    ctx.move_to(*outer[0])
    for p in outer[1:]:
        ctx.line_to(*p)
    for (x, y, nx, ny, u) in reversed(samples):
        ctx.line_to(x, y)
    ctx.close_path()
    set_rgba(ctx, color, alpha)
    ctx.fill()


def draw_frost_rim(ctx, samples, half_width_fn, color, alpha, coverage, soft=0.16):
    """Same tapered curve-following ribbon as ``draw_curve_rim_solid``, but
    with a directional coverage gradient (0=bare, 1=fully frosted) along the
    branch so frost visibly *creeps* from the tip toward the base rather than
    popping on/off uniformly."""
    if alpha <= 0.004 or coverage <= 0.004:
        return
    outer = [(x + nx * half_width_fn(u), y + ny * half_width_fn(u)) for (x, y, nx, ny, u) in samples]
    ctx.move_to(*outer[0])
    for p in outer[1:]:
        ctx.line_to(*p)
    for (x, y, nx, ny, u) in reversed(samples):
        ctx.line_to(x, y)
    ctx.close_path()

    edge = clamp(1.0 - coverage)  # frost creeps from the tip (u=1) toward base (u=0)
    lo = clamp(edge - soft)
    hi = clamp(edge + soft)
    x0, y0 = samples[0][0], samples[0][1]
    x1, y1 = samples[-1][0], samples[-1][1]
    grad = cairo.LinearGradient(x0, y0, x1, y1)
    r, g, b = color
    for s in sorted({0.0, lo, hi, 1.0}):
        a = 0.0 if s <= lo else (alpha if s >= hi else alpha * (s - lo) / max(hi - lo, 1e-6))
        grad.add_color_stop_rgba(s, r / 255.0, g / 255.0, b / 255.0, clamp(a))
    ctx.set_source(grad)
    ctx.fill()


def frost_hash(node_id):
    """Deterministic per-branch jitter in [0,1] (no RandomState reconstruction)."""
    return (node_id * 2246822519 % 1000) / 1000.0


def branch_frost_coverage(branch, winter_w):
    """Fraction (0..1) of a branch currently frosted. Outer twigs
    (higher ``outsideness``) start frosting at a lower winter weight than
    inner wood, and a small per-branch hash jitter keeps the wave organic
    rather than a synchronized on/off switch across the whole canopy."""
    jitter = (frost_hash(branch.id) - 0.5) * 0.16
    onset = (1.0 - branch.outsideness) * 0.62 + jitter
    span = 0.34
    return smoothstep(onset, onset + span, winter_w)


def compute_skeleton(branches, harmonics, phases, amps, t):
    """Returns dict node_id -> (start_xy, end_xy, abs_angle_deg)."""
    envelope = global_wind_envelope(t)
    sway_all = periodic_batch(t, harmonics, phases, amps) * envelope

    abs_angle = {}
    end_pt = {}
    out = {}
    for b in branches:
        sway = b.wind_amp * sway_all[b.id]
        if b.parent == -1:
            aa = b.local_angle + sway
            sx, sy = TRUNK_BASE
        else:
            aa = abs_angle[b.parent] + b.local_angle + sway
            sx, sy = end_pt[b.parent]
        rad = math.radians(aa)
        ex, ey = sx + math.sin(rad) * b.length, sy - math.cos(rad) * b.length
        abs_angle[b.id] = aa
        end_pt[b.id] = (ex, ey)
        out[b.id] = (sx, sy, ex, ey, aa)
    return out


def draw_tree(ctx, glow_ctx, branches, skel, pal, winter_w, t):
    for b in branches:
        sx, sy, ex, ey, aa = skel[b.id]
        rad = math.radians(aa)
        perp = (math.cos(rad), math.sin(rad))
        mx, my = (sx + ex) * 0.5, (sy + ey) * 0.5
        bow1 = b.curvature * 0.95
        bow2 = b.curvature2 * 0.95
        c1x, c1y = sx + (mx - sx) * 0.55 + perp[0] * bow1, sy + (my - sy) * 0.55 + perp[1] * bow1
        c2x, c2y = mx + (ex - mx) * 0.45 + perp[0] * bow2, my + (ey - my) * 0.45 + perp[1] * bow2

        t_frac = clamp(b.depth / MAX_DEPTH)
        thickness = max(b.thickness * b.taper_wobble, 0.9)
        bark_col = lerp_color(pal["bark"], pal["bark_hi"], 0.35 * (1 - t_frac))

        ctx.set_line_cap(cairo.LINE_CAP_ROUND)
        ctx.set_line_width(thickness)
        set_rgba(ctx, bark_col, 0.97)
        ctx.move_to(sx, sy)
        ctx.curve_to(c1x, c1y, c2x, c2y, ex, ey)
        ctx.stroke()

        needs_rim = b.depth <= 6
        needs_frost = winter_w > 0.05 and b.depth >= 2
        if needs_rim or needs_frost:
            samples = sample_bezier(sx, sy, c1x, c1y, c2x, c2y, ex, ey, n=6)

        # Subtle bark rim-light feeding the bloom pass -- follows the true
        # curve (not a straight chord) so it never drifts off the bark.
        if needs_rim:
            glow_alpha = (0.05 + 0.05 * winter_w) * (1.0 - t_frac * 0.4)
            rim_hw = thickness * 0.32
            draw_curve_rim_solid(glow_ctx, samples, lambda u: rim_hw, pal["bark_glow"], glow_alpha)

        # crystalline winter glints on the outer twigs (exact-period twinkle:
        # k=6 harmonics over the full DURATION_S loop, so it wraps cleanly)
        if winter_w > 0.2 and b.depth >= MAX_DEPTH - 2 and (b.id % 2 == 0):
            twinkle = 0.5 + 0.5 * math.sin(2 * math.pi * 6 * t / DURATION_S + glint_phase(b.id))
            spark_alpha = clamp((winter_w - 0.2) / 0.8) * 0.6 * twinkle
            draw_glow_dot(glow_ctx, ex, ey, thickness * 1.6 + 2.0, pal["bark_glow"], spark_alpha)

        # Frost/rim-of-snow that hugs the branch's own spline and creeps
        # directionally (tip -> base) as winter deepens, with a controlled
        # taper -- replaces the old floating straight-line snow cap that
        # could overshoot joints on curved branches.
        if needs_frost:
            coverage = branch_frost_coverage(b, winter_w)
            if coverage > 0.004:
                frost_alpha = 0.55 * clamp(winter_w / 0.6)
                frost_hw = thickness * 0.42
                draw_frost_rim(ctx, samples, lambda u: frost_hw, (250, 250, 255), frost_alpha, coverage)


def glint_phase(node_id):
    """Deterministic per-branch phase offset (no RandomState reconstruction;
    a cheap integer hash keeps winter glints staggered across the canopy)."""
    return (node_id * 2654435761 % 1000) / 1000.0 * 2 * math.pi


def draw_foliage(ctx, glow_ctx, clusters, skel, pal, season_w, t):
    leaf_col_base = lerp_color(pal["leaf"], pal["leaf_hi"], 0.18)
    autumn_w = season_w[AUTUMN]
    summer_w = season_w[SUMMER]
    for cl in clusters:
        if cl.node_id not in skel:
            continue
        sx, sy, ex, ey, aa = skel[cl.node_id]
        bx, by = sx + (ex - sx) * cl.t_local, sy + (ey - sy) * cl.t_local
        rad = math.radians(aa)
        tangent = (math.sin(rad), -math.cos(rad))
        perp = (math.cos(rad), math.sin(rad))

        # Outside clusters are phase-advanced: they bud first in spring and
        # shed first in autumn -- one mechanism drives both the staggered
        # spring wave and the outside-in autumn thinning-with-gaps.
        phase_off = cl.offset_s - cl.outsideness * OUTSIDE_PHASE_ADVANCE_S

        hue = cl.hue_shift
        blossom_col = tuple(clamp(c + hue * 0.5, 0, 255) for c in pal["blossom"])

        cluster_leaf_p = leaf_presence(t, phase_off)
        cluster_blossom_p = blossom_presence(t, phase_off)
        if cluster_leaf_p <= 0.004 and cluster_blossom_p <= 0.004:
            continue

        sag = summer_w * 10.0  # heavy, closed canopy sags downward in summer

        for (along_off, perp_off, size, rot, variant, is_blossom,
             anchor_off_s, hue_jitter, curl_seed, sag_seed) in cl.anchors:
            leaf_p = leaf_presence(t, phase_off + anchor_off_s)
            blossom_p = blossom_presence(t, phase_off + anchor_off_s)
            if leaf_p <= 0.006 and blossom_p <= 0.006:
                continue

            px = bx + tangent[0] * along_off + perp[0] * perp_off
            py = by + tangent[1] * along_off + perp[1] * perp_off + sag * sag_seed
            angle = aa + rot * 0.12

            if is_blossom:
                if blossom_p <= 0.006:
                    continue
                grow = clamp(blossom_p / 0.5)
                draw_blossom(ctx, px, py, size * (0.35 + 0.65 * grow), angle,
                             blossom_col, pal["blossom_hi"], blossom_p)
                draw_leaf_glow(glow_ctx, px, py, size * 0.9, angle, pal["blossom_hi"], blossom_p * 0.4)
            else:
                if leaf_p <= 0.006:
                    continue
                grow = clamp(leaf_p / 0.5)
                # Autumn broadens per-anchor hue variance (less flat/yellow)
                # and introduces a curling/drying silhouette per leaf.
                leaf_col = tuple(
                    clamp(c + hue + hue_jitter * (0.3 + 0.7 * autumn_w), 0, 255)
                    for c in leaf_col_base
                )
                curl = clamp(autumn_w * (0.3 + 0.7 * curl_seed))
                draw_leaf(ctx, px, py, size * (0.55 + 0.45 * grow), angle,
                          leaf_col, leaf_p, variant, curl)
                if leaf_p > 0.5:
                    draw_leaf_glow(glow_ctx, px, py, size * 0.7, angle, pal["leaf_hi"], (leaf_p - 0.5) * 0.5)


def season_pos(t):
    return (t % DURATION_S) / SEASON_LEN


def emitter_gate(pos, center, half_width=0.42, power=1.6):
    """Narrow, mutually-exclusive activity window for a seasonal particle
    emitter (steeper falloff than the broad palette crossfade). With
    half_width=0.42 and season centers 1.0 apart, adjacent emitters cannot
    overlap -- petal/leaf_fall/snow can never be simultaneously active, so
    inappropriate emitters (snow in spring, snow while leaves still falling)
    fully die out before the next one begins."""
    d = abs(wrapped_delta(pos, center, 4.0))
    g = clamp(1.0 - d / half_width)
    return g ** power


def draw_particles(ctx, glow_ctx, t, pools, season_w, pal):
    pos = season_pos(t)
    kind_weight = {
        "petal": emitter_gate(pos, 0.5),
        "pollen": emitter_gate(pos, 1.15, half_width=0.62, power=1.2),
        "leaf_fall": emitter_gate(pos, 2.5),
        "snow": emitter_gate(pos, 3.5),
        "dust": 1.0,
    }
    kind_base_alpha = {
        "petal": 0.85, "pollen": 0.5, "leaf_fall": 0.9, "snow": 0.85, "dust": 0.22,
    }
    for kind, pool in pools.items():
        wgt = clamp(kind_weight[kind], 0.0, 1.0)
        if wgt < 0.015:
            continue
        drag = kind == "leaf_fall"
        xs, ys, spins, fades = particle_positions(pool, t, drag=drag)
        base_alpha = kind_base_alpha[kind]
        for i in range(pool.count):
            depth_scale = 0.6 + 0.7 * pool.depth[i]
            size = pool.size[i] * depth_scale
            quiet_damp = lerp(1.0, 0.22, pool.quiet_mask[i])
            alpha = wgt * fades[i] * quiet_damp
            if alpha < 0.01:
                continue
            x, y, spin = xs[i], ys[i], spins[i]
            far = pool.depth[i] < 0.55  # depth-band: far particles read soft/out-of-focus
            if kind == "petal":
                if far:
                    draw_glow_dot(glow_ctx, x, y, size * 3.0 + 1.5, pal["particle"], alpha * base_alpha * 0.8)
                else:
                    draw_petal_particle(ctx, x, y, size, spin, pal["particle"], alpha * base_alpha)
            elif kind == "pollen":
                draw_glow_dot(glow_ctx, x, y, 3.4 * size + 2, pal["particle"], alpha * base_alpha)
            elif kind == "leaf_fall":
                if far:
                    draw_glow_dot(glow_ctx, x, y, size * 3.4 + 1.5, pal["particle"], alpha * base_alpha * 0.75)
                else:
                    draw_leaf_particle(ctx, x, y, size, spin, pal["particle"], alpha * base_alpha)
            elif kind == "snow":
                if far:
                    draw_glow_dot(glow_ctx, x, y, size * 2.8 + 1.5, (255, 255, 255), alpha * 0.30)
                else:
                    draw_snowflake(ctx, x, y, 3.4 * size + 1.4, spin, pal["particle"], alpha * base_alpha)
                    draw_glow_dot(glow_ctx, x, y, size * 2.4 + 1.0, (255, 255, 255), alpha * 0.18)
            elif kind == "dust":
                draw_glow_dot(glow_ctx, x, y, 1.6 * size + 0.8, pal["particle"], alpha * base_alpha)


def draw_vignette(ctx):
    grad = cairo.RadialGradient(
        WIDTH * 0.30, HEIGHT * 0.28, HEIGHT * 0.15,
        WIDTH * 0.30, HEIGHT * 0.28, HEIGHT * 0.95,
    )
    grad.add_color_stop_rgba(0, 0, 0, 0, 0.0)
    grad.add_color_stop_rgba(1, 0, 0, 0, 0.42)
    ctx.set_source(grad)
    ctx.paint()

    corner = cairo.RadialGradient(
        WIDTH * 0.06, HEIGHT * 0.06, 0,
        WIDTH * 0.06, HEIGHT * 0.06, WIDTH * 0.9,
    )
    corner.add_color_stop_rgba(0, 0, 0, 0, 0.55)
    corner.add_color_stop_rgba(1, 0, 0, 0, 0.0)
    ctx.set_source(corner)
    ctx.paint()


def apply_grain(arr, frame_idx):
    rng = np.random.RandomState(SEED + 90000 + frame_idx)
    noise = rng.normal(0, 2.2, size=(arr.shape[0], arr.shape[1], 1)).astype(np.float32)
    out = arr.astype(np.float32) + noise
    np.clip(out, 0, 255, out=out)
    return out.astype(np.uint8)


_WX0, _WX1 = int(WIDGET_X0), int(WIDGET_X1)
_WY0, _WY1 = int(WIDGET_Y0), int(WIDGET_Y1)


def enforce_widget_zone(bgr):
    """Hard numeric guarantee for the widget-legibility rectangle
    (x 2-40%, y 3-24%): mean luminance stays under WIDGET_LUMA_MAX and any
    high-frequency detail is smoothed away, regardless of what atmosphere or
    background layers bleed nearby. Returns (frame, region_mean_luma)."""
    region = bgr[_WY0:_WY1, _WX0:_WX1].astype(np.float32)
    # Kill high-frequency detail first (branch fragments/foliage texture),
    # then hard-clamp brightness to guarantee the luminance ceiling.
    region = cv2.GaussianBlur(region, (0, 0), sigmaX=9.0)
    luma = region[:, :, 0] * 0.114 + region[:, :, 1] * 0.587 + region[:, :, 2] * 0.299
    mean_luma = float(luma.mean())
    if mean_luma > WIDGET_LUMA_MAX:
        region *= (WIDGET_LUMA_MAX / mean_luma)
        luma = region[:, :, 0] * 0.114 + region[:, :, 1] * 0.587 + region[:, :, 2] * 0.299
        mean_luma = float(luma.mean())
    bgr[_WY0:_WY1, _WX0:_WX1] = np.clip(region, 0, 255).astype(np.uint8)
    return bgr, mean_luma


def surface_to_bgr(surface):
    stride = surface.get_stride()
    buf = surface.get_data()
    arr = np.ndarray(shape=(surface.get_height(), stride // 4, 4), dtype=np.uint8, buffer=buf)
    return np.ascontiguousarray(arr[:, :surface.get_width(), :3])


def clear_surface(ctx, w, h):
    ctx.save()
    ctx.identity_matrix()
    ctx.set_operator(cairo.OPERATOR_SOURCE)
    ctx.set_source_rgba(0, 0, 0, 0)
    ctx.rectangle(0, 0, w, h)
    ctx.fill()
    ctx.set_operator(cairo.OPERATOR_OVER)
    ctx.restore()


# --------------------------------------------------------------------------
# Main render loop
# --------------------------------------------------------------------------


def render_frame(ctx, glow_ctx, frame_idx, branches, clusters, pools,
                  branch_harm, branch_phase, branch_amps, bg_layers):
    t = frame_idx / FPS
    w = season_weights(t)
    pal = blended_palette(w)

    ctx.save()
    apply_camera(ctx, t)
    draw_background(ctx, t, pal, w, bg_layers)
    skel = compute_skeleton(branches, branch_harm, branch_phase, branch_amps, t)

    glow_ctx.save()
    apply_camera(glow_ctx, t)

    draw_tree(ctx, glow_ctx, branches, skel, pal, w[WINTER], t)
    draw_foliage(ctx, glow_ctx, clusters, skel, pal, w, t)
    draw_particles(ctx, glow_ctx, t, pools, w, pal)

    glow_ctx.restore()
    ctx.restore()

    draw_vignette(ctx)


def composite_bloom(base_bgr, glow_surface):
    glow_bgr = surface_to_bgr(glow_surface).astype(np.float32)
    tight = cv2.GaussianBlur(glow_bgr, (0, 0), sigmaX=3.2)
    wide = cv2.GaussianBlur(glow_bgr, (0, 0), sigmaX=11.0)
    bloom_half = tight * 0.65 + wide * 0.85
    bloom = cv2.resize(bloom_half, (WIDTH, HEIGHT), interpolation=cv2.INTER_LINEAR)
    out = base_bgr.astype(np.float32) + bloom * 0.9
    np.clip(out, 0, 255, out=out)
    return out.astype(np.uint8)


def build_ffmpeg_cmd(output_path):
    return [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostats", "-y",
        "-f", "rawvideo",
        "-pixel_format", "bgr24",
        "-video_size", f"{WIDTH}x{HEIGHT}",
        "-framerate", str(FPS),
        "-i", "-",
        "-an",
        "-c:v", "libx264",
        "-profile:v", "high",
        "-level:v", "4.1",
        "-pix_fmt", "yuv420p",
        "-preset", "medium",
        "-b:v", f"{TARGET_BITRATE_KBPS}k",
        "-maxrate", f"{MAXRATE_KBPS}k",
        "-bufsize", f"{BUFSIZE_KBPS}k",
        "-g", str(FPS * 2),
        "-keyint_min", str(FPS),
        "-bf", "2",
        "-refs", "3",
        "-sc_threshold", "0",
        "-movflags", "+faststart",
        output_path,
    ]


def main():
    n_frames = N_FRAMES
    contact_sheet = "--contact-sheet" in sys.argv
    pos_args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if pos_args:
        n_frames = int(pos_args[0])

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)

    t_start = time.time()
    rng = np.random.RandomState(SEED)
    branches, clusters = build_tree(rng)
    branch_harm, branch_phase = make_harmonics(rng, len(branches), n_octaves=3)
    branch_amps = octave_amps(3)
    pools = make_particle_pools()
    bg_layers = build_bg_layers()
    n_anchors = sum(len(c.anchors) for c in clusters)
    print(f"[render] tree: {len(branches)} branches, {len(clusters)} clusters, "
          f"{n_anchors} leaf/blossom anchors, {len(bg_layers)} bg layers")

    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, WIDTH, HEIGHT)
    ctx = cairo.Context(surface)
    glow_surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, GLOW_W, GLOW_H)
    glow_ctx = cairo.Context(glow_surface)
    glow_ctx.scale(GLOW_SCALE, GLOW_SCALE)

    if contact_sheet:
        out_dir = os.path.join(os.path.dirname(OUTPUT_PATH), "previews")
        os.makedirs(out_dir, exist_ok=True)
        n_shots = 16
        indices = [int(i * N_FRAMES / n_shots) for i in range(n_shots)]
        for k, frame_idx in enumerate(indices):
            clear_surface(glow_ctx, GLOW_W, GLOW_H)
            render_frame(ctx, glow_ctx, frame_idx, branches, clusters, pools,
                         branch_harm, branch_phase, branch_amps, bg_layers)
            base_bgr = surface_to_bgr(surface)
            final = composite_bloom(base_bgr, glow_surface)
            final = apply_grain(final, frame_idx)
            final, wz_luma = enforce_widget_zone(final)
            t = frame_idx / FPS
            png_path = os.path.join(out_dir, f"preview_{k:02d}_f{frame_idx:04d}_t{t:05.1f}s.png")
            cv2.imwrite(png_path, final)
            print(f"[preview] frame {frame_idx} t={t:.1f}s widget_luma={wz_luma:.2f} -> {png_path}")
        print(f"[preview] {n_shots} frames written to {out_dir}")
        return

    if n_frames == N_FRAMES:
        output_path = OUTPUT_PATH
    else:
        smoke_dir = os.path.join(os.path.dirname(OUTPUT_PATH), "previews")
        os.makedirs(smoke_dir, exist_ok=True)
        output_path = os.path.join(
            smoke_dir, f"four-seasons-cinematic-smoke-{n_frames}.mp4")
    cmd = build_ffmpeg_cmd(output_path)
    print("[render] ffmpeg cmd:", " ".join(cmd))
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                             stderr=subprocess.PIPE)

    render_t0 = time.time()
    wz_min, wz_max, wz_sum = 255.0, 0.0, 0.0
    try:
        for frame_idx in range(n_frames):
            clear_surface(glow_ctx, GLOW_W, GLOW_H)
            render_frame(ctx, glow_ctx, frame_idx, branches, clusters, pools,
                         branch_harm, branch_phase, branch_amps, bg_layers)

            base_bgr = surface_to_bgr(surface)
            final = composite_bloom(base_bgr, glow_surface)
            final = apply_grain(final, frame_idx)
            final, wz_luma = enforce_widget_zone(final)
            wz_min = min(wz_min, wz_luma)
            wz_max = max(wz_max, wz_luma)
            wz_sum += wz_luma
            proc.stdin.write(final.tobytes())

            if frame_idx % 96 == 0:
                elapsed = time.time() - render_t0
                print(f"[render] frame {frame_idx}/{n_frames} ({elapsed:.1f}s elapsed)")
    finally:
        proc.stdin.close()
        stderr_out = proc.stderr.read().decode("utf-8", "ignore")
        ret = proc.wait()
    render_t1 = time.time()

    if ret != 0:
        print(stderr_out[-4000:])
        raise RuntimeError(f"ffmpeg exited with code {ret}")

    total_elapsed = time.time() - t_start
    render_elapsed = render_t1 - render_t0
    file_size = os.path.getsize(output_path)
    wz_mean = wz_sum / n_frames if n_frames else 0.0
    print(f"[render] done. frames={render_elapsed:.1f}s total={total_elapsed:.1f}s "
          f"size={file_size/1_000_000:.2f}MB -> {output_path}")
    print(f"[render] widget-zone luminance: min={wz_min:.2f} mean={wz_mean:.2f} "
          f"max={wz_max:.2f} (ceiling={WIDGET_LUMA_MAX})")

    if n_frames == N_FRAMES:
        validate_output(output_path, render_elapsed, total_elapsed)
        report_seam_metrics(output_path)
    else:
        print(f"[render] smoke test ({n_frames} frames) — skipping full validation.")


def validate_output(path, render_elapsed, total_elapsed):
    cmd = [
        "ffprobe", "-hide_banner", "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    info = json.loads(result.stdout)
    stream = next(s for s in info["streams"] if s["codec_type"] == "video")
    fmt = info["format"]

    duration = float(fmt.get("duration", stream.get("duration", 0)))
    size_bytes = int(fmt.get("size", os.path.getsize(path)))
    fps_num, fps_den = (stream.get("r_frame_rate", "0/1")).split("/")
    fps_val = float(fps_num) / float(fps_den) if float(fps_den) else 0.0
    has_audio = any(s["codec_type"] == "audio" for s in info["streams"])

    report = {
        "codec_name": stream.get("codec_name"),
        "profile": stream.get("profile"),
        "level": stream.get("level"),
        "pix_fmt": stream.get("pix_fmt"),
        "width": stream.get("width"),
        "height": stream.get("height"),
        "fps": round(fps_val, 3),
        "duration_s": round(duration, 3),
        "size_bytes": size_bytes,
        "size_mb": round(size_bytes / 1_000_000, 2),
        "has_audio": has_audio,
        "render_time_s": round(render_elapsed, 2),
        "total_time_s": round(total_elapsed, 2),
    }

    print("\n===== VALIDATION REPORT =====")
    for k, v in report.items():
        print(f"{k:>14}: {v}")
    print("==============================")

    assert report["width"] == WIDTH and report["height"] == HEIGHT, "dimension mismatch"
    assert abs(report["fps"] - FPS) < 0.01, "fps mismatch"
    assert report["pix_fmt"] == "yuv420p", "pix_fmt mismatch"
    assert report["codec_name"] == "h264", "codec mismatch"
    assert not report["has_audio"], "unexpected audio stream"
    assert 26.0 <= report["size_mb"] <= 28.8, f"size out of target range: {report['size_mb']}MB"
    assert 47.0 <= report["duration_s"] <= 49.0, "duration out of expected range"

    return report


def report_seam_metrics(path):
    """Numerically confirms the loop seam is no more jarring than any other
    adjacent-frame cut, by decoding and diffing frames near the wrap point."""
    cap = cv2.VideoCapture(path)
    wanted = {0, 1, N_FRAMES - 2, N_FRAMES - 1}
    frames = {}
    i = 0
    while True:
        ok, f = cap.read()
        if not ok or i > max(wanted):
            break
        if i in wanted:
            frames[i] = f
        i += 1
    cap.release()

    if not all(k in frames for k in wanted):
        print("[seam] could not decode all required frames for seam check")
        return

    def mad(a, b):
        return float(np.mean(np.abs(a.astype(np.int16) - b.astype(np.int16))))

    d_start = mad(frames[0], frames[1])
    d_end = mad(frames[N_FRAMES - 2], frames[N_FRAMES - 1])
    d_wrap = mad(frames[N_FRAMES - 1], frames[0])

    print("\n===== SEAM METRICS (mean abs pixel diff) =====")
    print(f"  frame0 -> frame1 (normal adjacent):        {d_start:.3f}")
    print(f"  frame{N_FRAMES-2} -> frame{N_FRAMES-1} (normal adjacent): {d_end:.3f}")
    print(f"  frame{N_FRAMES-1} -> frame0 (loop wrap):    {d_wrap:.3f}")
    ratio = d_wrap / max((d_start + d_end) / 2.0, 1e-6)
    print(f"  wrap/adjacent ratio: {ratio:.2f}x")
    print("===============================================")


if __name__ == "__main__":
    main()
