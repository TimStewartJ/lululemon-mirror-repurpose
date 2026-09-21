#!/usr/bin/env python3
"""Offline renderer for the spatial four-seasons background film.

A deterministic 120-second, 1080x1920, 30 FPS seamless loop: one weeping cherry
living through a year, as a real 3D sculpture of light on a true-black stage.
The camera circles the tree exactly once per loop, so its grown (not drawn)
branches, hanging strands, blossoms and leaves slide past each other in true
perspective. Blossoms swell from buds, open and are torn away in gusts that
travel through the crown; leaves unfurl, shimmer, turn from the strand tips
upward and let go; petals and leaves tumble, land on still water, float and
fade; fireflies wander through summer; snow falls through the depth of field
and settles on the boughs while frost glints on the bare strands.

Black pixels keep the Mirror's glass reflective, so the tree appears to stand
in the room. It references no specific third-party artwork; generic qualities
(emissive colour on darkness, perpetual growth and decay) are built from first
principles with moderngl, NumPy and OpenCV.

Run:
    python tools\\render_seasonal_video.py 30                    # smoke clip
    python tools\\render_seasonal_video.py 240 --start-frame 660 # petal storm
    python tools\\render_seasonal_video.py --contact-sheet
    python tools\\render_seasonal_video.py --frames 300,1350,2475,3150
    python tools\\render_seasonal_video.py                       # full film

Output:
    generated\\background-videos\\four-seasons-spatial-120s.mp4
    generated\\background-videos\\previews\\ (smoke clips, previews, reports)
"""

from __future__ import annotations

import math

import numpy as np

import artwork_video as artwork

WIDTH = artwork.WIDTH
HEIGHT = artwork.HEIGHT
FPS = artwork.FPS
DURATION_S = 120.0
N_FRAMES = int(round(FPS * DURATION_S))
SEASON_LEN = DURATION_S / 4.0

SEED = 20260919
SLUG = "four-seasons-spatial"
OUTPUT_PATH = artwork.VIDEO_DIR / "four-seasons-spatial-120s.mp4"

SPRING, SUMMER, AUTUMN, WINTER = 0, 1, 2, 3

# Camera: a level orbit with a shifted lens, so verticals stay vertical, the
# whole crown stays inside the glass all the way round and its top clears the
# widget corner.
FOV_Y = 34.0
ORBIT_RADIUS = 21.0
EYE_HEIGHT = 2.4
ORBIT_START = math.radians(205.0)
LENS_SHIFT_X = 0.03          # tree axis a touch right of centre
LENS_SHIFT_Y = -0.27         # horizon at 63% of the height, water below
QUIET_FEATHER = 150.0        # how softly light fades toward the widget corner
NEAR, FAR = 0.5, 90.0
ASPECT = WIDTH / HEIGHT

DOF_SCALE = 105.0
DOF_SHARP_RANGE = 2.2        # the whole tree stays crisp
DOF_FAR_SIGMAS = (1.5, 3.0)
DOF_NEAR_SIGMAS = (1.5, 3.0, 5.0, 8.0, 12.0)
MARGIN = 48                  # px rendered around the frame: the reach of the widest blur

TRUNK_RADIUS = 0.235
STEP = 0.15


def smooth(x):
    x = np.clip(x, 0.0, 1.0)
    return x * x * (3.0 - 2.0 * x)


def season_weights(t):
    """Soft membership of loop time ``t`` in each season (sums to one)."""
    pos = (t % DURATION_S) / SEASON_LEN - 0.5
    w = np.zeros(4)
    lo = int(math.floor(pos)) % 4
    frac = float(smooth((pos - math.floor(pos) - 0.3) / 0.4))
    w[lo] += 1.0 - frac
    w[(lo + 1) % 4] += frac
    return w


# --------------------------------------------------------------------------
# Growing the tree
# --------------------------------------------------------------------------


class Tree:
    """Node arrays: position, parent, radius, flex, path length from the root."""

    def __init__(self):
        rng = np.random.RandomState(SEED)
        self.pos, self.parent, self.kind = [], [], []     # kind 0 scaffold, 1 strand
        self._trunk(rng)
        self._colonize(rng)
        self._relax()
        self.scaffold_count = len(self.pos)
        self._strands(rng)
        self.pos = np.asarray(self.pos, dtype=np.float64)
        self.parent = np.asarray(self.parent, dtype=np.int64)
        self.kind = np.asarray(self.kind, dtype=np.int64)
        self._measure()

    def _add(self, pos, parent, kind=0):
        self.pos.append(np.asarray(pos, dtype=np.float64))
        self.parent.append(parent)
        self.kind.append(kind)
        return len(self.pos) - 1

    def _trunk(self, rng):
        node = self._add((0.0, 0.0, 0.0), -1)
        y = 0.0
        while y < 2.5:
            y += STEP
            x = 0.20 * math.sin(y * 1.15 + 0.4) - 0.20 * math.sin(0.4)
            z = 0.14 * math.sin(y * 0.9 + 2.0) - 0.14 * math.sin(2.0)
            node = self._add((x, y, z), node)
        self.trunk_top = node

    def _colonize(self, rng):
        """Space colonization: branches grow toward a dome of attraction points,
        which gives the irregular, reaching limbs of a real crown."""
        n = 2300
        centre = np.array([0.0, 3.1, 0.0])
        direction = rng.normal(size=(n, 3))
        direction[:, 1] = np.abs(direction[:, 1]) * 0.9 + 0.05
        direction /= np.linalg.norm(direction, axis=1, keepdims=True)
        shell = rng.uniform(0.30, 1.0, n) ** 0.55
        lobes = 1.0 + 0.16 * np.sin(3.0 * np.arctan2(direction[:, 2], direction[:, 0]) + 1.0)
        points = centre + direction * shell[:, None] * np.array([2.15, 3.10, 2.15]) * lobes[:, None]
        points = points[points[:, 1] > 2.9]

        influence, kill = 1.5, 0.30
        nearest = np.full(len(points), -1)
        nearest_d = np.full(len(points), np.inf)

        def update(new_indices):
            new_pos = np.asarray([self.pos[i] for i in new_indices])
            d = np.linalg.norm(points[:, None, :] - new_pos[None, :, :], axis=2)
            best = d.argmin(axis=1)
            best_d = d[np.arange(len(points)), best]
            better = best_d < nearest_d
            nearest[better] = np.asarray(new_indices)[best[better]]
            nearest_d[better] = best_d[better]

        update(list(range(len(self.pos))))
        alive = np.ones(len(points), dtype=bool)
        for _ in range(260):
            active = alive & (nearest_d < influence)
            if not active.any():
                # Reach for the dome when nothing is close yet.
                if alive.any() and len(self.pos) < 40:
                    influence *= 1.3
                    continue
                break
            grow = {}
            for a in np.nonzero(active)[0]:
                node = nearest[a]
                pull = points[a] - self.pos[node]
                grow.setdefault(node, np.zeros(3))
                grow[node] += pull / max(np.linalg.norm(pull), 1e-9)
            new_nodes = []
            for node, pull in grow.items():
                pull = pull / max(np.linalg.norm(pull), 1e-9)
                if self.parent[node] >= 0:
                    # Keep some momentum so limbs sweep instead of zig-zagging.
                    along = self.pos[node] - self.pos[self.parent[node]]
                    pull = pull + 0.55 * along / max(np.linalg.norm(along), 1e-9)
                pull = pull + rng.normal(0.0, 0.10, 3)
                pull /= np.linalg.norm(pull)
                candidate = self.pos[node] + pull * STEP
                new_nodes.append(self._add(candidate, node))
            update(new_nodes)
            alive &= nearest_d > kill
            if len(self.pos) > 5200:
                break

    def _relax(self):
        """Laplacian smoothing along each limb removes growth jitter."""
        pos = np.asarray(self.pos)
        parent = np.asarray(self.parent)
        children = [[] for _ in pos]
        for i, p in enumerate(parent):
            if p >= 0:
                children[p].append(i)
        for _ in range(3):
            new = pos.copy()
            for i, p in enumerate(parent):
                if p < 0 or len(children[i]) != 1:
                    continue
                new[i] = 0.5 * pos[i] + 0.25 * (pos[p] + pos[children[i][0]])
            pos = new
        self.pos = [p for p in pos]

    def _strands(self, rng):
        """Weeping strands hang from the outer limbs like curtains."""
        pos = np.asarray(self.pos)
        parent = np.asarray(self.parent)
        has_child = np.zeros(len(pos), dtype=bool)
        has_child[parent[parent >= 0]] = True
        radial = np.hypot(pos[:, 0], pos[:, 2])
        outer = (pos[:, 1] > 3.3) & (radial > 0.7)
        tips = ~has_child & (pos[:, 1] > 3.0)
        chance = np.where(tips, 1.0, np.where(outer, 0.075, 0.0))
        self.strand_roots = []
        for node in np.nonzero(rng.uniform(size=len(pos)) < chance)[0]:
            away = np.array([pos[node, 0], 0.0, pos[node, 2]])
            away /= max(np.linalg.norm(away), 1e-6)
            along = pos[node] - pos[parent[node]]
            along /= max(np.linalg.norm(along), 1e-9)
            heading = away * 0.55 + along * 0.55 + rng.normal(0.0, 0.22, 3)
            heading[1] = min(heading[1], 0.25)
            floor = rng.uniform(0.7, 2.6) if rng.uniform() < 0.75 else rng.uniform(2.6, 3.6)
            length = rng.uniform(1.8, 5.0)
            self._hang(rng, node, heading, length, floor, droop=rng.uniform(0.20, 0.32), spawn=True)

    def _hang(self, rng, node, heading, length, floor, droop, spawn):
        heading = heading / np.linalg.norm(heading)
        step = 0.11
        travelled = 0.0
        chain = []
        while travelled < length:
            heading = heading + np.array([0.0, -droop, 0.0]) + rng.normal(0.0, 0.035, 3)
            heading /= np.linalg.norm(heading)
            nxt = self.pos[node] + heading * step
            if nxt[1] < floor:
                break
            node = self._add(nxt, node, kind=1)
            chain.append(node)
            travelled += step
        if spawn and len(chain) > 8:
            for _ in range(rng.randint(1, 4)):
                start = chain[rng.randint(2, max(3, int(len(chain) * 0.6)))]
                side = rng.normal(size=3)
                side[1] = 0.0
                side /= max(np.linalg.norm(side), 1e-9)
                self._hang(rng, start, side * 0.8 + np.array([0.0, -0.5, 0.0]), rng.uniform(0.5, 1.6),
                           floor, droop=rng.uniform(0.2, 0.3), spawn=False)

    def _measure(self):
        pos, parent = self.pos, self.parent
        n = len(pos)
        children = [[] for _ in range(n)]
        for i in range(1, n):
            children[parent[i]].append(i)
        self.children = children
        # Pipe model: a limb carries the cross-section of everything it feeds.
        radius = np.zeros(n)
        order = np.arange(n)[::-1]          # children always come after parents
        exponent = 2.35
        for i in order:
            if not children[i]:
                radius[i] = 0.0055 if self.kind[i] == 1 else 0.008
            else:
                radius[i] = (sum(radius[c] ** exponent for c in children[i])) ** (1.0 / exponent)
                if self.kind[i] == 1:
                    radius[i] = min(radius[i], 0.014)
        scaffold = self.kind == 0
        twig = 0.011
        share = np.clip((radius[scaffold] - twig) / (radius[0] - twig), 0.0, 1.0)
        radius[scaffold] = twig + (TRUNK_RADIUS - twig) * share ** 0.72
        flare = 1.0 + 0.55 * np.exp(-pos[:, 1] / 0.28)
        radius[scaffold] *= flare[scaffold]
        self.radius = radius

        path = np.zeros(n)
        for i in range(1, n):
            path[i] = path[parent[i]] + np.linalg.norm(pos[i] - pos[parent[i]])
        self.path = path

        # Flex: how far wind may carry a node. Strands swing like pendulums.
        flex = np.zeros(n)
        top_path = path[self.trunk_top]
        for i in range(1, n):
            if self.kind[i] == 0:
                reach = max(path[i] - top_path, 0.0)
                flex[i] = 0.10 * min(reach / 3.5, 1.0) ** 1.6
            else:
                step = np.linalg.norm(pos[i] - pos[parent[i]])
                flex[i] = flex[parent[i]] + 0.30 * step * (1.0 + 0.35 * (flex[parent[i]] > 0.12))
        self.flex = np.minimum(flex, 1.35)


TUBE_SUBDIVISIONS = 3


def tube_mesh(tree):
    """Tapered tubes swept along Catmull-Rom splines through the nodes, so limbs
    and strands bend as curves instead of showing their growth steps. Rings are
    parallel-transported; vertex rows hold the centre, the offset from it, the
    normal, flex, path length, radius and the position around the ring."""
    pos, parent, radius = tree.pos, tree.parent, tree.radius
    n = len(pos)
    main_child = np.full(n, -1)
    for i in range(n):
        if tree.children[i]:
            main_child[i] = max(tree.children[i], key=lambda c: radius[c])
    node = np.arange(1, n)
    p = parent[node]
    continues = (main_child[p] == node) & (parent[p] >= 0)
    onward = main_child[node]
    p1, p2 = pos[p], pos[node]
    # A limb that carries on through a node bends smoothly; a side shoot starts straight.
    p0 = np.where(continues[:, None], pos[np.maximum(parent[p], 0)], 2.0 * p1 - p2)
    p3 = np.where((onward >= 0)[:, None], pos[np.maximum(onward, 0)], 2.0 * p2 - p1)
    s = np.linspace(0.0, 1.0, TUBE_SUBDIVISIONS + 1)
    s1, s2, s3 = s[None, :, None], (s ** 2)[None, :, None], (s ** 3)[None, :, None]
    a, b, c = (p2 - p0)[:, None], (2 * p0 - 5 * p1 + 4 * p2 - p3)[:, None], (-p0 + 3 * p1 - 3 * p2 + p3)[:, None]
    centre = 0.5 * (2.0 * p1[:, None] + a * s1 + b * s2 + c * s3)
    tangent = a + 2.0 * b * s1 + 3.0 * c * s2
    tangent /= np.maximum(np.linalg.norm(tangent, axis=2, keepdims=True), 1e-9)

    # Parallel transport one side vector from the root outward (parents come first).
    side = np.empty((n - 1, len(s), 3))
    carried = np.zeros((n, 3))
    carried[0] = (1.0, 0.0, 0.0)
    for j in range(n - 1):
        v = carried[p[j]]
        for k in range(len(s)):
            axis = tangent[j, k]
            v = v - axis * (v @ axis)
            length = math.sqrt(v @ v)
            if length < 1e-6:
                v = np.cross(axis, (0.0, 0.0, 1.0))
                length = math.sqrt(v @ v)
            v = v / length
            side[j, k] = v
        carried[node[j]] = v
    other = np.cross(tangent, side)

    r_start = np.where(main_child[p] == node, radius[p], np.minimum(radius[node] * 1.15, radius[p]))
    ring_sides = np.where(r_start > 0.09, 12, np.where(r_start > 0.035, 8, np.where(r_start > 0.012, 5, 4)))
    blend = s[None, :]
    ring_radius = r_start[:, None] + (radius[node] - r_start)[:, None] * blend
    ring_flex = tree.flex[p][:, None] + (tree.flex[node] - tree.flex[p])[:, None] * blend
    ring_path = tree.path[p][:, None] + (tree.path[node] - tree.path[p])[:, None] * blend

    rows, indices, base = [], [], 0
    for count in (12, 8, 5, 4):
        sel = np.nonzero(ring_sides == count)[0]
        if not len(sel):
            continue
        # The seam vertex is doubled so the position around the ring runs 0..1 unbroken.
        stride = count + 1
        angle = 2 * math.pi * np.arange(stride) / count
        normal = (side[sel][:, :, None, :] * np.cos(angle)[None, None, :, None]
                  + other[sel][:, :, None, :] * np.sin(angle)[None, None, :, None])
        block = np.empty((len(sel), len(s), stride, 13), dtype=np.float32)
        block[..., 0:3] = centre[sel][:, :, None, :]
        block[..., 3:6] = normal * ring_radius[sel][:, :, None, None]
        block[..., 6:9] = normal
        block[..., 9] = ring_flex[sel][:, :, None]
        block[..., 10] = ring_path[sel][:, :, None]
        block[..., 11] = ring_radius[sel][:, :, None]
        block[..., 12] = (np.arange(stride) / count)[None, None, :]
        rows.append(block.reshape(-1, 13))
        seg = np.arange(len(sel))[:, None, None]
        ring = np.arange(len(s) - 1)[None, :, None]
        k = np.arange(count)[None, None, :]
        q00 = base + (seg * len(s) + ring) * stride + k
        q01, q10, q11 = q00 + 1, q00 + stride, q00 + stride + 1
        indices.append(np.stack([q00, q01, q10, q01, q11, q10], axis=-1).reshape(-1))
        base += len(sel) * len(s) * stride
    return np.concatenate(rows).astype(np.float32), np.concatenate(indices).astype(np.int32)


# --------------------------------------------------------------------------
# Camera
# --------------------------------------------------------------------------


def orbit_angle(t):
    return ORBIT_START + 2 * math.pi * (t % DURATION_S) / DURATION_S


def camera_matrices(t):
    import artwork_gl as agl
    angle = orbit_angle(t)
    phase = 2 * math.pi * (t % DURATION_S) / DURATION_S
    radius = ORBIT_RADIUS + 0.5 * math.sin(2 * phase + 0.7)
    height = EYE_HEIGHT + 0.22 * math.sin(3 * phase + 1.9)
    eye = np.array([radius * math.sin(angle), height, radius * math.cos(angle)])
    target = np.array([0.0, height, 0.0])
    view = agl.look_at(eye, target)
    proj = agl.perspective(FOV_Y, ASPECT, NEAR, FAR)
    proj[0, 2] = -LENS_SHIFT_X
    proj[1, 2] = -LENS_SHIFT_Y
    return eye, view, proj


# --------------------------------------------------------------------------
# Wind: gusts arrive at chosen moments; everything is periodic over the loop
# --------------------------------------------------------------------------

# (time, strength, width in seconds)
GUSTS = ((19.0, 1.0, 2.2), (25.5, 1.35, 2.4), (31.0, 0.9, 2.0), (47.0, 0.45, 3.0),
         (76.0, 0.9, 2.2), (84.0, 1.35, 2.5), (92.0, 1.0, 2.2), (108.0, 0.4, 3.0))
PETAL_GUSTS = ((19.0, 0.30), (25.5, 0.45), (31.0, 0.25))
LEAF_GUSTS = ((76.0, 0.28), (84.0, 0.42), (92.0, 0.30))
WIND_FRONT_SPEED = 2.4

WATER_Y = 0.0
FALL_TAU = 0.7
WIND_TAU = 1.2


def cyclic(dt):
    """Signed shortest time difference on the loop."""
    return (np.asarray(dt) + DURATION_S / 2.0) % DURATION_S - DURATION_S / 2.0


def gust_level(t):
    t = np.asarray(t, dtype=np.float64)
    level = np.full(t.shape, 0.55)
    for when, strength, width in GUSTS:
        level = level + strength * np.exp(-(cyclic(t - when) / width) ** 2)
    return level


def sway_np(rest, flex, t, gust):
    """NumPy twin of the shader's ``sway`` (same constants)."""
    w = 2 * math.pi / DURATION_S
    x, y, z = rest[:, 0], rest[:, 1], rest[:, 2]
    a = np.sin(w * 17.0 * t + 0.55 * x + 0.35 * z + y * 0.30)
    b = np.sin(w * 29.0 * t - 0.40 * x + 0.75 * z + y * 0.55 + 1.7)
    c = np.sin(w * 46.0 * t + 0.95 * x - 0.60 * z + y * 0.90 + 0.4)
    push = (np.array([0.62, 0.0, 0.34])[None] * (a * 0.26 + 0.12)[:, None]
            + np.array([-0.30, 0.0, 0.58])[None] * (b * 0.13)[:, None]
            + np.array([0.45, 0.0, -0.50])[None] * (c * 0.06)[:, None])
    f = flex * (0.55 + 0.45 * flex)
    d = push * (gust * f)[:, None]
    d[:, 1] = -(d[:, 0] ** 2 + d[:, 2] ** 2) * 0.55 / np.maximum(flex * 2.2, 0.3)
    return d


def wind_direction(t):
    """Gusts blow across the frame and a little toward the lens, wherever the
    orbiting camera happens to be when they arrive."""
    angle = ORBIT_START + 2 * math.pi * ((np.asarray(t) + 3.0) % DURATION_S) / DURATION_S
    toward = np.stack([np.sin(angle), np.zeros_like(angle), np.cos(angle)], axis=-1)
    right = np.stack([np.cos(angle), np.zeros_like(angle), -np.sin(angle)], axis=-1)
    return right * 0.92 + toward * 0.34


# --------------------------------------------------------------------------
# Foliage: every petal and leaf carries its whole year as constants
# --------------------------------------------------------------------------


def matrix_to_quat(m):
    """(n, 3, 3) rotation matrices to (n, 4) quaternions x, y, z, w."""
    q = np.empty((len(m), 4))
    trace = m[:, 0, 0] + m[:, 1, 1] + m[:, 2, 2]
    q[:, 3] = np.sqrt(np.maximum(1.0 + trace, 1e-12)) * 0.5
    q[:, 0] = np.sqrt(np.maximum(1.0 + m[:, 0, 0] - m[:, 1, 1] - m[:, 2, 2], 1e-12)) * 0.5
    q[:, 1] = np.sqrt(np.maximum(1.0 - m[:, 0, 0] + m[:, 1, 1] - m[:, 2, 2], 1e-12)) * 0.5
    q[:, 2] = np.sqrt(np.maximum(1.0 - m[:, 0, 0] - m[:, 1, 1] + m[:, 2, 2], 1e-12)) * 0.5
    q[:, 0] = np.copysign(q[:, 0], m[:, 2, 1] - m[:, 1, 2])
    q[:, 1] = np.copysign(q[:, 1], m[:, 0, 2] - m[:, 2, 0])
    q[:, 2] = np.copysign(q[:, 2], m[:, 1, 0] - m[:, 0, 1])
    return q / np.linalg.norm(q, axis=1, keepdims=True)


def frames_from_axis(rng, axis):
    """Random right-handed frames (columns x, y, z) whose z column is ``axis``."""
    axis = axis / np.linalg.norm(axis, axis=1, keepdims=True)
    helper = rng.normal(size=axis.shape)
    x = np.cross(helper, axis)
    x /= np.maximum(np.linalg.norm(x, axis=1, keepdims=True), 1e-9)
    y = np.cross(axis, x)
    return np.stack([x, y, axis], axis=2)


def mixture_times(rng, n, lo, hi, peaks, width, background):
    """Release moments: a thin steady fall plus bursts when the gusts arrive."""
    out = rng.uniform(lo, hi, n)
    weights = np.array([w for _, w in peaks])
    pick = rng.choice(len(peaks), n, p=weights / weights.sum())
    burst = np.array([peaks[i][0] for i in pick]) + rng.normal(0.0, width, n)
    use = rng.uniform(size=n) > background
    out[use] = burst[use]
    return out


PETAL_OPEN_ELEV = math.radians(16.0)
INSTANCE_FLOATS = 28


class Foliage:
    def __init__(self, tree):
        rng = np.random.RandomState(SEED + 11)
        hosts = np.nonzero((tree.kind == 1) | ((tree.radius < 0.02) & (tree.pos[:, 1] > 3.0)))[0]
        self.crown_top = tree.pos[hosts, 1].max()
        self.crown_low = tree.pos[hosts, 1].min()
        self.petals = self._blossoms(tree, rng, hosts)
        self.leaves = self._leaves(tree, rng, hosts)

    @staticmethod
    def _outward(pos):
        out = pos * np.array([1.0, 0.0, 1.0])
        return out / np.maximum(np.linalg.norm(out, axis=1, keepdims=True), 1e-6)

    def _exposure(self, rng, pos):
        radial = np.hypot(pos[:, 0], pos[:, 2])
        shell = np.clip(radial / 2.3, 0.0, 1.0) ** 1.5
        return (0.30 + 0.70 * shell) * rng.uniform(0.55, 1.20, len(pos)) ** 1.3

    def _fall(self, rng, release, base_speed, wind_gain, flutter, tumble):
        n = len(release)
        gust = gust_level(release)
        wind = wind_direction(release) + rng.normal(0.0, 0.16, (n, 3))
        fall = np.stack([rng.uniform(*base_speed, n), gust * rng.uniform(0.45, 1.4, n) * wind_gain,
                         rng.uniform(*flutter, n), rng.uniform(0.5, 1.1, n)], axis=1)
        fall2 = np.stack([wind[:, 0], wind[:, 2], rng.uniform(*tumble, n) * rng.choice((-1.0, 1.0), n), gust], axis=1)
        return fall, fall2

    def _blossoms(self, tree, rng, hosts):
        sites = hosts[rng.uniform(size=len(hosts)) < 0.50]
        per_site = rng.choice((2, 3, 4, 5), len(sites), p=(0.25, 0.35, 0.25, 0.15))
        node = np.repeat(sites, per_site)
        n = len(node)
        stalk = rng.normal(size=(n, 3)) + np.array([0.0, -0.7, 0.0])
        stalk /= np.linalg.norm(stalk, axis=1, keepdims=True)
        centre = tree.pos[node] + stalk * rng.uniform(0.02, 0.09, (n, 1))
        axis = self._outward(centre) * 0.5 + np.array([0.0, -0.4, 0.0]) + rng.normal(0.0, 0.75, (n, 3))
        frame = frames_from_axis(rng, axis)
        size = rng.uniform(0.036, 0.058, n)
        tint = rng.uniform(0.0, 1.0, n) ** 1.2
        exposure = self._exposure(rng, centre)

        # Blossoms open in a wave that runs from the crown down the strands.
        depth = np.clip((self.crown_top - centre[:, 1]) / (self.crown_top - self.crown_low), 0.0, 1.0)
        t_open = (117.0 + 12.5 * depth + rng.normal(0.0, 1.2, n)) % DURATION_S
        t_bud = (103.0 + 9.0 * depth + rng.normal(0.0, 1.5, n)) % DURATION_S

        petals = 5
        k = np.tile(np.arange(petals), n)
        b = np.repeat(np.arange(n), petals)
        m = len(b)
        psi = k * 2 * math.pi / petals + rng.normal(0.0, 0.05, m)
        radial = np.stack([np.cos(psi), np.sin(psi), np.zeros(m)], axis=1)
        zed = np.array([0.0, 0.0, 1.0])
        e = PETAL_OPEN_ELEV + rng.normal(0.0, 0.06, m)
        y_p = radial * np.cos(e)[:, None] + zed * np.sin(e)[:, None]
        z_p = -radial * np.sin(e)[:, None] + zed * np.cos(e)[:, None]
        x_p = np.cross(y_p, z_p)
        local = np.stack([x_p, y_p, z_p], axis=2)
        world = frame[b] @ local
        anchor = centre[b] + np.einsum("nij,nj->ni", frame[b], radial) * (0.10 * size[b])[:, None]

        release = mixture_times(rng, m, 15.0, 35.0, PETAL_GUSTS, 1.3, background=0.22)
        front = np.einsum("ni,ni->n", anchor, wind_direction(release)) / WIND_FRONT_SPEED
        release = (release + front) % DURATION_S
        fall, fall2 = self._fall(rng, release, (0.55, 0.95), 1.0, (0.10, 0.28), (1.5, 4.0))

        data = np.empty((m, INSTANCE_FLOATS), dtype=np.float32)
        data[:, 0:3], data[:, 3] = anchor, tree.flex[node][b]
        data[:, 4:8] = matrix_to_quat(world)
        data[:, 8], data[:, 9], data[:, 10], data[:, 11] = psi, size[b] * rng.uniform(0.92, 1.06, m), exposure[b], rng.uniform(0, 1, m)
        data[:, 12], data[:, 13], data[:, 14], data[:, 15] = t_bud[b], t_open[b], release, rng.uniform(7.0, 13.0, m)
        data[:, 16:20], data[:, 20:24] = fall, fall2
        data[:, 24], data[:, 25], data[:, 26], data[:, 27] = tint[b], 0.0, 0.0, tree.path[node][b]
        return data

    def _leaves(self, tree, rng, hosts):
        node = np.concatenate([hosts[rng.uniform(size=len(hosts)) < 0.62], hosts[rng.uniform(size=len(hosts)) < 0.40]])
        n = len(node)
        parent = tree.parent[node]
        along = tree.pos[node] - tree.pos[parent]
        along /= np.maximum(np.linalg.norm(along, axis=1, keepdims=True), 1e-9)
        # Leaves hang: their length follows the strand and gravity.
        hang = along * 0.55 + np.array([0.0, -0.75, 0.0]) + rng.normal(0.0, 0.42, (n, 3))
        hang /= np.linalg.norm(hang, axis=1, keepdims=True)
        helper = rng.normal(size=(n, 3))
        x = np.cross(hang, helper)
        x /= np.maximum(np.linalg.norm(x, axis=1, keepdims=True), 1e-9)
        z = np.cross(x, hang)
        world = np.stack([x, hang, z], axis=2)
        anchor = tree.pos[node] + x * rng.uniform(-0.02, 0.02, (n, 1))
        size = rng.uniform(0.12, 0.21, n)
        exposure = self._exposure(rng, anchor)

        tipward = np.clip(tree.flex[node] / 1.2, 0.0, 1.0)
        t_bud = (24.0 + 10.0 * rng.uniform(size=n) + 2.0 * tipward) % DURATION_S
        t_open = (t_bud + 1.2) % DURATION_S
        t_turn = 60.5 + 15.0 * (1.0 - tipward) + rng.normal(0.0, 1.6, n)
        release = mixture_times(rng, n, 71.0, 99.0, LEAF_GUSTS, 1.5, background=0.30)
        front = np.einsum("ni,ni->n", anchor, wind_direction(release)) / WIND_FRONT_SPEED
        release = np.maximum(release + front, t_turn + 4.5)
        fall, fall2 = self._fall(rng, release, (0.80, 1.35), 0.72, (0.15, 0.40), (1.0, 2.6))

        data = np.empty((n, INSTANCE_FLOATS), dtype=np.float32)
        data[:, 0:3], data[:, 3] = anchor, tree.flex[node]
        data[:, 4:8] = matrix_to_quat(world)
        data[:, 8], data[:, 9], data[:, 10], data[:, 11] = rng.uniform(0, 2 * math.pi, n), size, exposure, rng.uniform(0, 1, n)
        data[:, 12], data[:, 13], data[:, 14], data[:, 15] = t_bud, t_open, release % DURATION_S, rng.uniform(9.0, 15.0, n)
        data[:, 16:20], data[:, 20:24] = fall, fall2
        data[:, 24], data[:, 25], data[:, 26], data[:, 27] = rng.uniform(0, 1, n), rng.uniform(0, 1, n) ** 0.8, t_turn % DURATION_S, tree.path[node]
        return data


RIPPLE_LIFE_S = 6.5
RIPPLE_CAP = 96              # rings alive at once where petals and leaves land
SNOW_RING = 0.035            # share of a snowflake's fall for which its ring lasts
MAX_RIPPLES = 128            # with the snow's; the size of the shader's array


def thin_events(events, min_gap):
    """Rows of ``events`` (x, z, when, strength) at least ``min_gap`` seconds
    apart around the loop. In a storm hundreds of petals land every second; the
    water shows a bounded number of rings, and every ring that starts also gets
    to spread and fade instead of being pushed out by the next."""
    order = np.argsort(events[:, 2], kind="stable")
    kept, last = [], -math.inf
    for i in order:
        if events[i, 2] - last >= min_gap:
            kept.append(i)
            last = events[i, 2]
    while len(kept) > 1 and events[kept[0], 2] + DURATION_S - events[kept[-1], 2] < min_gap:
        kept.pop()
    return events[kept]


def landings(data, centre_offset):
    """Where and when released foliage meets the water (CPU twin of the shader)."""
    anchor, flex = data[:, 0:3].astype(np.float64), data[:, 3].astype(np.float64)
    release = data[:, 14].astype(np.float64)
    p0 = anchor + sway_np(anchor, flex, release, data[:, 23].astype(np.float64))
    height = np.maximum(p0[:, 1] + centre_offset - WATER_Y, 0.05)
    a_land = height / data[:, 16] + FALL_TAU
    travel = a_land - WIND_TAU * (1.0 - np.exp(-a_land / WIND_TAU))
    xz = p0[:, [0, 2]] + data[:, 20:22] * (data[:, 17] * travel)[:, None]
    return xz, (release + a_land) % DURATION_S


# --------------------------------------------------------------------------
# GPU programs
# --------------------------------------------------------------------------

TREE_COMMON = """
uniform float u_time;
uniform float u_duration;

float since(float t, float t0) { return mod(t - t0 + u_duration, u_duration); }

// Wind is a sum of travelling waves with whole cycles per loop, so every
// branch returns exactly to where it started.
vec3 sway(vec3 rest, float flex, float t, float gust) {
    float w = TAU / u_duration;
    vec2 p = rest.xz;
    float a = sin(w * 17.0 * t + dot(p, vec2(0.55, 0.35)) + rest.y * 0.30);
    float b = sin(w * 29.0 * t + dot(p, vec2(-0.40, 0.75)) + rest.y * 0.55 + 1.7);
    float c = sin(w * 46.0 * t + dot(p, vec2(0.95, -0.60)) + rest.y * 0.90 + 0.4);
    vec3 push = vec3(0.62, 0.0, 0.34) * (a * 0.26 + 0.12) + vec3(-0.30, 0.0, 0.58) * b * 0.13
              + vec3(0.45, 0.0, -0.50) * c * 0.06;
    float f = flex * (0.55 + 0.45 * flex);
    vec3 d = push * gust * f;
    d.y = -dot(d.xz, d.xz) * 0.55 / max(flex * 2.2, 0.3);
    return d;
}
"""

BRANCH_VS = TREE_COMMON + """
uniform mat4 u_view;
uniform mat4 u_proj;
uniform vec2 u_jitter;
uniform vec2 u_size;
uniform float u_mirror;
uniform float u_gust;
in vec3 in_center;
in vec3 in_offset;
in vec3 in_normal;
in vec4 in_data;      // flex, path, radius, around
out vec3 v_wpos;
out vec3 v_normal;
out vec4 v_data;
out float v_thin;
void main() {
    vec3 centre = in_center + sway(in_center, in_data.x, u_time, u_gust);
    vec4 vc = u_view * vec4(centre * vec3(1.0, u_mirror, 1.0), 1.0);
    // Hair-thin strands keep a minimum on-screen width and give up brightness instead.
    float px = in_data.z * u_proj[1][1] * u_size.y * 0.5 / max(-vc.z, 1e-3);
    float widen = max(1.0, 0.60 / max(px, 1e-4));
    vec3 wp = centre + in_offset * widen;
    wp.y *= u_mirror;
    v_thin = 1.0 / widen;
    v_wpos = wp;
    v_normal = in_normal * vec3(1.0, u_mirror, 1.0);
    v_data = in_data;
    vec4 clip = u_proj * u_view * vec4(wp, 1.0);
    clip.xy += u_jitter * clip.w;
    gl_Position = clip;
}
"""

BRANCH_FS = """
uniform vec3 u_eye;
uniform vec3 u_tint;
uniform float u_time;
uniform float u_duration;
uniform float u_snow;
uniform float u_level;
uniform float u_axis_dist;
in vec3 v_wpos;
in vec3 v_normal;
in vec4 v_data;
in float v_thin;
out vec4 f_color;

// Value noise that closes around the limb, so the grain has no seam.
float ring_noise(vec2 p, float period) {
    vec2 i = floor(p), f = fract(p);
    vec2 w = f * f * (3.0 - 2.0 * f);
    float x0 = mod(i.x, period), x1 = mod(i.x + 1.0, period);
    return mix(mix(hash12(vec2(x0, i.y)), hash12(vec2(x1, i.y)), w.x),
               mix(hash12(vec2(x0, i.y + 1.0)), hash12(vec2(x1, i.y + 1.0)), w.x), w.y);
}

const float STROKES = 30.0;

void main() {
    vec3 N = normalize(v_normal);
    vec3 V = normalize(u_eye - v_wpos);
    float ndv = clamp(abs(dot(N, V)), 0.0, 1.0);
    float rim = pow(1.0 - ndv, 2.6);
    float path = v_data.y, around = v_data.w, radius = v_data.z;
    float thick = smoothstep(0.012, 0.06, radius);
    // The wood is drawn rather than lit: long brush strokes of light follow the
    // grain over a dark body, wander, break off and gather toward the silhouette.
    float grain = ring_noise(vec2(around * 4.0 + path * 0.15, path * 0.55), 4.0);
    float wander = 2.6 * (grain - 0.5) + 1.0 * (ring_noise(vec2(around * 9.0, path * 1.7 + 5.0), 9.0) - 0.5);
    float x = around * STROKES + wander;
    float id = mod(floor(x), STROKES);
    float tone = 0.12 + 0.88 * pow(hash12(vec2(id, 3.0)), 1.6);
    float dash = smoothstep(0.30, 0.62, vnoise(vec2(path * 0.85 + id * 13.0, id * 7.0)));
    float crowd = max(fwidth(x), 1e-4);
    float d = abs(fract(x) - 0.5) / crowd;
    float ink = ((1.0 - smoothstep(0.40, 1.25, d)) + 0.20 * exp(-d * d * 0.09)) * tone * dash;
    ink = mix(ink, min(crowd * 0.55, 0.30), smoothstep(0.20, 0.48, crowd));
    float fine = ring_noise(vec2(around * 40.0, path * 5.0), 40.0);
    // Pulses of light climb from the roots to the strand tips.
    float w = TAU / u_duration;
    float pulse = pow(0.5 + 0.5 * sin(path * 1.10 - w * 15.0 * u_time), 8.0);
    float slow = 0.5 + 0.5 * sin(path * 0.33 - w * 4.0 * u_time + 1.0);
    float limb = 0.004 + ink * (0.22 + 0.16 * slow + 1.10 * pulse)
               + rim * (0.22 + 0.50 * grain) * (1.0 + 0.7 * pulse);
    float strand = 0.16 + 0.30 * rim * (0.45 + 0.8 * grain) + 0.45 * pulse + 0.05 * slow;
    vec3 col = u_tint * mix(strand, limb, thick);
    // Snow settles on whatever faces the sky.
    float cap = smoothstep(0.20, 0.80, N.y + 0.3 * (fine - 0.5)) * u_snow;
    float sparkle = pow(hash13(floor(v_wpos * 70.0)), 50.0) * 4.0;
    col = mix(col, vec3(0.74, 0.86, 1.0) * (0.50 + 0.30 * fine + sparkle), cap * mix(0.75, 1.0, thick));
    // The far side of the crown sinks into the dark.
    float depth = length((u_eye - v_wpos).xz) - u_axis_dist;
    col *= mix(1.20, 0.30, smooth01(depth / 4.4 + 0.5));
    col *= u_level * mix(v_thin, 1.0, 0.2);
    f_color = vec4(col, 1.0);
}
"""

FOLIAGE_VS = TREE_COMMON + """
uniform mat4 u_view;
uniform mat4 u_proj;
uniform vec2 u_jitter;
uniform vec3 u_eye;
uniform float u_mirror;
uniform float u_gust;
uniform float u_kind;          // 0 petal, 1 leaf
uniform vec3 u_slab;           // previous, own, next blur coordinate
uniform vec2 u_slab_range;
uniform vec4 u_lens;           // focus, scale, sharp range, near fade
in vec2 in_uv;
in vec4 i_anchor;   // rest xyz, flex
in vec4 i_quat;     // frame at full bloom
in vec4 i_params;   // azimuth or phase, size, exposure, seed
in vec4 i_times;    // bud, open, release, float duration
in vec4 i_fall;     // terminal speed, wind travel, flutter amplitude, flutter hz
in vec4 i_fall2;    // wind x, wind z, tumble rate, gust at release
in vec4 i_color;    // tint, autumn target, turn time, path
out vec2 v_uv;
out vec3 v_wpos;
out vec3 v_normal;
flat out vec4 v_tone;     // rgb, brightness
flat out vec4 v_state;    // alpha, kind, seed, ember

const float FALL_TAU = 0.7;
const float WIND_TAU = 1.2;

float profile(float u, float p, float q) {
    float norm = pow(p / (p + q), p) * pow(q / (p + q), q);
    return pow(max(u, 1e-5), p) * pow(max(1.0 - u, 0.0), q) / norm;
}

vec4 leaf_flutter(float t, float gust, float seed, float phase) {
    float w = TAU / u_duration;
    float n1 = 58.0 + floor(seed * 40.0), n2 = 37.0 + floor(seed * 23.0);
    float twist = (0.30 * sin(w * n1 * t + phase) + 0.12 * sin(w * (n1 + 31.0) * t + 2.0 * phase)) * gust;
    float swing = 0.22 * sin(w * n2 * t + 1.7 * phase) * gust;
    return quat_mul(quat_axis(vec3(0.0, 1.0, 0.0), twist), quat_axis(vec3(1.0, 0.0, 0.0), swing));
}

vec3 leaf_colour(float tint, float target, float turned, float young) {
    vec3 emerald = vec3(0.012, 0.30, 0.085), jade = vec3(0.010, 0.27, 0.17), lime = vec3(0.17, 0.46, 0.035);
    vec3 green = tint < 0.5 ? mix(emerald, jade, tint * 2.0) : mix(jade, lime, (tint - 0.5) * 2.0);
    vec3 bronze = vec3(0.55, 0.36, 0.08);
    vec3 col = mix(bronze, green, young);
    vec3 gold = vec3(1.0, 0.62, 0.05), amber = vec3(1.0, 0.27, 0.02), crimson = vec3(0.72, 0.030, 0.035);
    vec3 final = target < 0.5 ? mix(gold, amber, target * 2.0) : mix(amber, crimson, (target - 0.5) * 2.0);
    vec3 autumn = turned < 0.5 ? mix(col, gold, smooth01(turned * 2.0)) : mix(gold, final, smooth01(turned * 2.0 - 1.0));
    return turned > 0.0 ? autumn : col;
}

void main() {
    float kind = u_kind;
    float seed = i_params.w;
    float size = i_params.y;
    float span = since(i_times.z, i_times.x);            // bud to release
    float a_bud = since(u_time, i_times.x);
    float a_rel = since(u_time, i_times.z);
    bool attached = a_bud < span;

    float width = kind < 0.5 ? 0.84 : 0.40;
    float p = kind < 0.5 ? 1.10 : 0.85, q = kind < 0.5 ? 0.42 : 1.05;
    float cup = kind < 0.5 ? 0.28 : 0.0;
    float fold = kind < 0.5 ? 0.0 : 0.16;
    float curl = kind < 0.5 ? 0.10 : 0.22;

    float a_open = a_bud - since(i_times.y, i_times.x);
    float open_s = kind < 0.5 ? 5.5 : 7.5;
    float open = attached ? (a_open > 0.0 ? smooth01(a_open / open_s) : 0.0) : 1.0;
    float grow = attached ? smooth01(a_bud / 5.0) : 1.0;
    float scale = (kind < 0.5 ? mix(0.36, 1.0, open) : mix(0.10, 1.0, open)) * grow;
    fold = mix(1.1, fold, open);
    float tuck = kind < 0.5 ? mix(radians(64.0), 0.0, open) : mix(radians(28.0), 0.0, open);

    // Local surface.
    float u = in_uv.x, v = in_uv.y;
    float L = size * scale, W = L * width;
    float hw = profile(u, p, q);
    float x = v * hw * W * 0.5;
    float z = cup * x * x / max(W * 0.5, 1e-5) + fold * abs(x) - curl * L * u * u;
    vec3 lp = vec3(x, u * L, z);
    vec3 ln = normalize(vec3(-(2.0 * cup * x / max(W * 0.5, 1e-5) + fold * sign(x)), 2.0 * curl * u, 1.0));
    float ct = cos(tuck), st = sin(tuck);
    lp = vec3(lp.x, lp.y * ct - lp.z * st, lp.y * st + lp.z * ct);
    ln = vec3(ln.x, ln.y * ct - ln.z * st, ln.y * st + ln.z * ct);

    vec4 q_rest = i_quat;
    vec3 wp, wn;
    float alpha = 1.0, ember = 0.0, flash = 1.0;
    vec3 rest = i_anchor.xyz;
    if (attached) {
        vec4 ql = kind < 0.5 ? q_rest : quat_mul(q_rest, leaf_flutter(u_time, u_gust, seed, i_params.x));
        wp = rest + sway(rest, i_anchor.w, u_time, u_gust) + quat_rotate(ql, lp);
        wn = quat_rotate(ql, ln);
    } else {
        float gust0 = i_fall2.w;
        vec4 q0 = kind < 0.5 ? q_rest : quat_mul(q_rest, leaf_flutter(i_times.z, gust0, seed, i_params.x));
        vec3 mid = vec3(0.0, 0.5 * size, 0.0);
        vec3 c0 = rest + sway(rest, i_anchor.w, i_times.z, gust0) + quat_rotate(q0, mid);
        float height = max(c0.y - """ + repr(WATER_Y) + """, 0.05);
        float a_land = height / i_fall.x + FALL_TAU;
        float a = min(a_rel, a_land);
        float fallen = (a - FALL_TAU * (1.0 - exp(-a / FALL_TAU))) / (a_land - FALL_TAU * (1.0 - exp(-a_land / FALL_TAU)));
        float travel = a - WIND_TAU * (1.0 - exp(-a / WIND_TAU));
        float after = max(a_rel - a_land, 0.0);
        float h1 = hash11(seed * 91.7 + 3.1), h2 = hash11(seed * 47.3 + 9.2), h3 = hash11(seed * 13.9 + 5.5);
        vec2 wind = i_fall2.xy;
        float phase = TAU * h1;
        float env = (1.0 - exp(-a / 0.8)) * (1.0 - smooth01((a - a_land + 0.6) / 0.6));
        vec3 flutter = i_fall.z * env * vec3(cos(phase + TAU * i_fall.w * a) - cos(phase),
                                             0.22 * sin(2.0 * (phase + TAU * i_fall.w * a)) - 0.22 * sin(2.0 * phase),
                                             sin(phase + TAU * i_fall.w * a) - sin(phase));
        vec3 c = c0;
        c.xz += wind * i_fall.y * travel + vec2(wind.y, -wind.x) * (h2 - 0.5) * 0.6 * travel;
        c.y = mix(c0.y, """ + repr(WATER_Y) + """ + 0.012, fallen);
        c += flutter * vec3(1.0, fallen < 1.0 ? 1.0 : 0.0, 1.0);
        // Afloat: a slow current and the faintest bob.
        c.xz += (wind * 0.045 + vec2(h2 - 0.5, h3 - 0.5) * 0.05) * after;

        vec3 axis = normalize(vec3(1.0, 0.6 * (h2 - 0.5), 0.9 * (h3 - 0.5)));
        float spun = i_fall2.z * (a - FALL_TAU * (1.0 - exp(-a / FALL_TAU)));
        vec4 qf = quat_mul(q0, quat_axis(axis, spun));
        vec4 flat_q = quat_mul(quat_axis(vec3(0.0, 1.0, 0.0), TAU * h1 + 0.05 * after),
                               quat_axis(vec3(1.0, 0.0, 0.0), h3 < 0.5 ? -0.5 * PI : 0.5 * PI));
        if (dot(qf, flat_q) < 0.0) flat_q = -flat_q;
        vec4 qn = normalize(mix(qf, flat_q, smooth01((a_rel - a_land + 0.9) / 0.9)));
        wp = c + quat_rotate(qn, lp - mid);
        wn = quat_rotate(qn, ln);
        float float_s = i_times.w;
        ember = smooth01(after / (0.5 * float_s));
        alpha = 1.0 - smooth01((after - 0.40 * float_s) / (0.60 * float_s));
        // Wet petals rock on the water and catch the light for an instant.
        float rock = TAU / u_duration * (44.0 + floor(h2 * 60.0)) * u_time + TAU * h3;
        float glint = pow(0.5 + 0.5 * sin(rock), 40.0) * smooth01(after / 0.8);
        flash = 1.25 + (kind < 0.5 ? 12.0 : 2.5) * glint;
        if (a_rel > a_land + float_s) alpha = 0.0;
        // What is about to touch the water joins its own reflection: the mirrored
        // copy fades over the last of the fall instead of vanishing at the surface.
        if (u_mirror < 0.0) alpha *= 1.0 - smooth01((a_rel - a_land + 0.9) / 0.6);
    }

    // Colour over the year.
    vec3 col;
    if (kind < 0.5) {
        vec3 pale = vec3(1.0, 0.80, 0.86), rose = vec3(1.0, 0.30, 0.52), bud = vec3(0.90, 0.10, 0.34);
        col = mix(bud, mix(pale, rose, i_color.x), smooth01(open * 1.2));
        col = mix(col, col * vec3(0.95, 0.55, 0.70), 0.6 * ember);
    } else {
        float a_turn = since(u_time, i_color.z);
        float turned = a_turn < since(i_times.x, i_color.z) ? clamp(a_turn / 9.0, 0.0, 1.0) : 0.0;
        float young = smooth01(max(a_open, 0.0) / 16.0);
        col = leaf_colour(i_color.x, i_color.y, attached || turned > 0.0 ? turned : 1.0, young);
        col = mix(col, vec3(0.55, 0.05, 0.01), 0.75 * ember);
    }

    // Light travels out from the trunk through the crown.
    float w = TAU / u_duration;
    float wave = 0.5 + 0.5 * sin(i_color.w * 0.33 - w * 4.0 * u_time + 1.0);
    float brightness = i_params.z * (0.78 + 0.40 * wave) * flash * (1.0 - 0.55 * ember);

    wp.y *= u_mirror;
    wn.y *= u_mirror;
    vec4 vp = u_view * vec4(wp, 1.0);
    float dist = max(-vp.z, 1e-3);
    float s = (1.0 / dist - 1.0 / u_lens.x) * u_lens.y;
    s = sign(s) * max(abs(s) - u_lens.z, 0.0);
    s = clamp(s, u_slab_range.x, u_slab_range.y);
    float weight = s <= u_slab.y ? (u_slab.y - u_slab.x > 1e-6 ? (s - u_slab.x) / (u_slab.y - u_slab.x) : 1.0)
                                 : (u_slab.z - u_slab.y > 1e-6 ? (u_slab.z - s) / (u_slab.z - u_slab.y) : 1.0);
    alpha *= clamp(weight, 0.0, 1.0) * smoothstep(u_lens.w * 0.5, u_lens.w, dist);

    v_uv = vec2(u, v);
    v_wpos = wp;
    v_normal = wn;
    v_tone = vec4(col, brightness);
    v_state = vec4(alpha, kind, seed, ember);
    vec4 clip = u_proj * vp;
    clip.xy += u_jitter * clip.w;
    gl_Position = alpha > 0.002 && scale > 0.001 ? clip : vec4(2.0, 2.0, 2.0, 1.0);
}
"""

FOLIAGE_FS = """
uniform vec3 u_eye;
uniform vec3 u_key;
uniform float u_axis_dist;
in vec2 v_uv;
in vec3 v_wpos;
in vec3 v_normal;
flat in vec4 v_tone;
flat in vec4 v_state;
out vec4 f_color;

float profile(float u, float p, float q) {
    float norm = pow(p / (p + q), p) * pow(q / (p + q), q);
    return pow(max(u, 1e-5), p) * pow(max(1.0 - u, 0.0), q) / norm;
}

void main() {
    // Edge-on petals are thinner than a pixel; keep extrapolated samples inside the surface.
    float u = clamp(v_uv.x, 0.0, 1.0), v = clamp(v_uv.y, -1.0, 1.0);
    float kind = v_state.y, seed = v_state.z;
    float cover = 1.0;
    vec3 col = v_tone.rgb;
    float detail = 1.0;
    if (kind < 0.5) {
        // Cherry petals are notched at the tip and deepen toward the heart.
        float xn = v * profile(u, 1.10, 0.42);
        float cut = 1.0 - 0.17 * exp(-(xn * xn) / 0.05);
        float aa = max(fwidth(cut - u), 1e-4);
        cover = smoothstep(-aa, aa, cut - u);
        col = mix(col * vec3(0.95, 0.42, 0.55), col, smooth01(u * 1.7 + 0.1));
        col += vec3(1.0, 0.75, 0.30) * 0.55 * exp(-u * 11.0);
        detail = 0.88 + 0.24 * fbm(vec2(xn * 9.0 + seed * 40.0, u * 3.0));
    } else {
        // Leaves: a bright midrib, pinnate veins and a paler serrated margin.
        float side = abs(v);
        float rib = exp(-v * v / 0.004);
        float veins = abs(fract((u * 1.1 - side * 0.55) * 7.0 + seed) - 0.5) * 2.0;
        float fw = fwidth((u * 1.1 - side * 0.55) * 7.0);
        float vein = (1.0 - smoothstep(0.10, 0.10 + max(2.0 * fw, 0.08), veins)) * clamp(1.0 - fw * 1.5, 0.0, 1.0);
        float margin = smoothstep(0.70, 1.0, side);
        detail = 0.80 + 0.40 * rib + 0.20 * vein * (1.0 - margin) + 0.12 * margin * margin
               + 0.30 * (fbm(vec2(v * 5.0 + seed * 30.0, u * 4.0)) - 0.5);
    }
    if (cover <= 0.002) discard;

    vec3 V = normalize(u_eye - v_wpos);
    vec3 N = normalize(v_normal);
    float facing = dot(N, V);
    vec3 Nf = facing < 0.0 ? -N : N;
    float fres = pow(clamp(1.0 - abs(facing), 0.0, 1.0), 2.2);
    float wrap = smooth01(dot(Nf, u_key) * 0.5 + 0.5);
    float through = pow(clamp(0.5 - 0.5 * dot(Nf, u_key), 0.0, 1.0), 2.0);
    float gloss = kind < 0.5 ? 0.0 : pow(clamp(dot(Nf, normalize(u_key + V)), 0.0, 1.0), 18.0) * 1.1;
    float light = (0.34 + 0.70 * wrap + 0.50 * fres + 0.45 * through) * detail + gloss;
    if (facing < 0.0) light *= 0.78;

    // The far side of the crown sinks into the dark; the near side glows.
    float depth = length((u_eye - v_wpos).xz) - u_axis_dist;
    light *= mix(1.30, 0.26, smooth01(depth / 4.4 + 0.5));

    float alpha = v_state.x * cover;
    f_color = vec4(col * light * v_tone.a * alpha, alpha);
}
"""

WATER_VS = """
uniform mat4 u_view;
uniform mat4 u_proj;
in vec2 in_pos;
out vec3 v_wpos;
void main() {
    v_wpos = vec3(in_pos.x, """ + repr(WATER_Y) + """, in_pos.y);
    gl_Position = u_proj * u_view * vec4(v_wpos, 1.0);
}
"""

WATER_FS = """
uniform sampler2D u_reflection;
uniform vec2 u_size;
uniform vec3 u_eye;
uniform float u_time;
uniform float u_duration;
uniform float u_level;
uniform int u_ripple_count;
uniform vec4 u_ripples[""" + str(MAX_RIPPLES) + """];      // x, z, age, strength
uniform vec3 u_ripple_tint;
in vec3 v_wpos;
out vec4 f_color;

// A barely breathing surface: a few crossing swells, whole cycles per loop.
vec2 swell(vec2 p) {
    float w = TAU / u_duration * u_time;
    return 0.010 * vec2(cos(p.x * 2.1 + p.y * 0.7 + w * 23.0), cos(p.y * 2.6 - p.x * 0.9 + w * 31.0))
         + 0.006 * vec2(cos(p.x * 5.3 - p.y * 1.9 - w * 41.0), cos(p.y * 4.7 + p.x * 2.3 + w * 37.0))
         + 0.004 * vec2(cos(p.x * 8.9 + p.y * 6.1 + w * 53.0), cos(p.y * 9.7 - p.x * 5.3 - w * 47.0));
}

void main() {
    vec2 p = v_wpos.xz;
    // Rings spread where petals, leaves and snow touch down.
    vec2 slope = vec2(0.0);
    float crest = 0.0;
    for (int i = 0; i < u_ripple_count; ++i) {
        vec4 r = u_ripples[i];
        vec2 d = p - r.xy;
        float dist = length(d);
        float front = 0.12 + 0.36 * r.z;
        float fade = r.w * exp(-r.z * 0.70) * smoothstep(0.0, 0.25, r.z)
                   * (1.0 - smoothstep(""" + repr(RIPPLE_LIFE_S - 1.5) + """, """ + repr(RIPPLE_LIFE_S) + """, r.z));
        float x = (dist - front) * 14.0;
        float ring = fade * exp(-x * x * 0.12) * step(dist, front + 1.2);
        slope += (d / max(dist, 1e-3)) * cos(x) * ring * 0.10;
        crest += ring * pow(0.5 + 0.5 * cos(x), 6.0) * smoothstep(0.15, 0.9, r.z);
    }
    float range = length(p);
    float view_dist = length(u_eye - v_wpos);
    // At this grazing angle one pixel covers a long stretch of water, so the
    // reflection is gathered along the line of sight: it smears downward into
    // soft streaks the way lights do on a night harbour.
    vec2 screen = gl_FragCoord.xy / u_size;
    vec2 along = normalize(p - u_eye.xz);
    float footprint = 0.030 * view_dist;
    vec3 refl = vec3(0.0);
    float total = 0.0;
    for (int k = -8; k <= 8; ++k) {
        float f = float(k) / 8.0;
        float weight = 1.0 - 0.6 * f * f;
        vec2 tilt = slope + swell(p + along * f * footprint);
        vec2 uv = screen + tilt * vec2(0.8, 2.2) * 6.0 / view_dist;
        refl += texture(u_reflection, uv).rgb * weight;
        total += weight;
    }
    refl /= total;
    // The pool has no shore: it thins out around the tree and toward the lower edge of the glass.
    float reach = (1.0 - smoothstep(3.5, 9.5, range)) * smoothstep(0.0, 0.075, screen.y);
    vec3 col = refl * 0.42 * reach + u_ripple_tint * crest * 0.13 * (1.0 - smoothstep(5.0, 11.0, range)) * smoothstep(0.0, 0.05, screen.y);
    f_color = vec4(col * u_level, 0.0);
}
"""

BRANCH_TINTS = ((1.00, 0.78, 0.72), (0.80, 0.92, 0.62), (1.00, 0.68, 0.36), (0.70, 0.85, 1.00))
BRANCH_LEVELS = (0.55, 0.45, 0.75, 1.05)
RIPPLE_TINTS = ((1.00, 0.72, 0.82), (0.75, 1.00, 0.80), (1.00, 0.66, 0.30), (0.80, 0.90, 1.00))
KEY_LIGHT_LOCAL = np.array([-0.40, 0.62, 0.68]) / np.linalg.norm([-0.40, 0.62, 0.68])
SUBFRAMES = 6
SHUTTER = 0.55
FOLIAGE_GRID = (5, 2)


class Particles:
    """Snow, fireflies, drifting motes and frost: closed paths of light."""

    def __init__(self, tree):
        rng = np.random.RandomState(SEED + 23)
        n = 1500
        self.snow_xz = rng.uniform(-1.0, 1.0, (n, 2)) * np.array([9.0, 9.0])
        self.snow_top = 9.5
        self.snow_cycles = np.round(rng.uniform(5.0, 10.0, n))
        self.snow_phase = rng.uniform(0.0, 1.0, n)
        self.snow_sway = rng.uniform(0.10, 0.45, (n, 2))
        self.snow_sway_cycles = np.round(rng.uniform(9.0, 26.0, (n, 2)))
        self.snow_sway_phase = rng.uniform(0, 2 * math.pi, (n, 2))
        self.snow_radius = rng.uniform(0.006, 0.016, n) * np.where(rng.uniform(size=n) < 0.10, 2.2, 1.0)
        self.snow_gain = rng.uniform(0.25, 1.0, n) ** 1.5

        f = 46
        self.fly_centre = np.stack([rng.uniform(-3.4, 3.4, f), rng.uniform(0.35, 3.4, f), rng.uniform(-3.4, 3.4, f)], axis=1)
        self.fly_amp = rng.uniform(0.35, 1.3, (f, 3)) * np.array([1.0, 0.45, 1.0])
        self.fly_cycles = np.round(rng.uniform(2.0, 6.0, (f, 3)))
        self.fly_cycles2 = np.round(rng.uniform(7.0, 13.0, (f, 3)))
        self.fly_phase = rng.uniform(0, 2 * math.pi, (f, 6))
        self.fly_blink = np.round(rng.uniform(34.0, 62.0, f))
        self.fly_blink_phase = rng.uniform(0, 2 * math.pi, f)

        m = 110
        self.mote_centre = np.stack([rng.uniform(-5.0, 5.0, m), rng.uniform(0.2, 7.5, m), rng.uniform(-5.0, 5.0, m)], axis=1)
        self.mote_amp = rng.uniform(0.2, 0.9, (m, 3))
        self.mote_cycles = np.round(rng.uniform(1.0, 5.0, (m, 3)))
        self.mote_phase = rng.uniform(0, 2 * math.pi, (m, 3))
        self.mote_twinkle = np.round(rng.uniform(15.0, 50.0, m))
        self.mote_radius = rng.uniform(0.007, 0.018, m)
        self.mote_gain = rng.uniform(0.2, 1.0, m) ** 1.5

        tips = np.nonzero(tree.kind == 1)[0]
        tips = tips[rng.uniform(size=len(tips)) < 0.055]
        self.frost_pos = tree.pos[tips]
        self.frost_flex = tree.flex[tips]
        self.frost_cycles = np.round(rng.uniform(20.0, 70.0, len(tips)))
        self.frost_phase = rng.uniform(0, 2 * math.pi, len(tips))

    def snow(self, t, amount):
        frac = (self.snow_phase + self.snow_cycles * t / DURATION_S) % 1.0
        y = self.snow_top * (1.0 - frac)
        phase = 2 * math.pi * t / DURATION_S
        xz = self.snow_xz + self.snow_sway * np.sin(self.snow_sway_cycles * phase + self.snow_sway_phase)
        edge = np.clip(np.minimum(frac, 1.0 - frac) / 0.06, 0.0, 1.0)
        data = np.empty((len(y), 8))
        data[:, 0], data[:, 1], data[:, 2], data[:, 3] = xz[:, 0], y, xz[:, 1], self.snow_radius
        data[:, 4:7] = (0.80, 0.90, 1.0)
        data[:, 7] = self.snow_gain * edge * amount * 1.5
        return data, xz, frac

    def fireflies(self, t, amount):
        phase = 2 * math.pi * t / DURATION_S
        pos = (self.fly_centre + self.fly_amp * np.sin(self.fly_cycles * phase + self.fly_phase[:, :3])
               + 0.3 * self.fly_amp * np.sin(self.fly_cycles2 * phase + self.fly_phase[:, 3:]))
        pos[:, 1] = np.maximum(pos[:, 1], 0.12)
        blink = np.clip(np.sin(self.fly_blink * phase + self.fly_blink_phase) * 1.6 - 0.2, 0.0, 1.0) ** 2
        data = np.empty((len(pos), 8))
        data[:, 0:3], data[:, 3] = pos, 0.030
        data[:, 4:7] = (0.62, 1.0, 0.22)
        data[:, 7] = blink * amount * 3.2
        return data

    def motes(self, t, tint, amount):
        phase = 2 * math.pi * t / DURATION_S
        pos = self.mote_centre + self.mote_amp * np.sin(self.mote_cycles * phase + self.mote_phase)
        twinkle = 0.55 + 0.45 * np.sin(self.mote_twinkle * phase + self.mote_phase[:, 0])
        data = np.empty((len(pos), 8))
        data[:, 0:3], data[:, 3] = pos, self.mote_radius
        data[:, 4:7] = tint
        data[:, 7] = self.mote_gain * twinkle * amount
        return data

    def frost(self, t, gust, amount):
        phase = 2 * math.pi * t / DURATION_S
        pos = self.frost_pos + sway_np(self.frost_pos, self.frost_flex, t, np.full(len(self.frost_pos), gust))
        glint = np.clip(np.sin(self.frost_cycles * phase + self.frost_phase), 0.0, 1.0) ** 12
        data = np.empty((len(pos), 8))
        data[:, 0:3], data[:, 3] = pos, 0.016
        data[:, 4:7] = (0.80, 0.92, 1.0)
        data[:, 7] = glint * amount * 5.0
        return data


class FrameRenderer:
    def __init__(self):
        import moderngl
        import artwork_gl as agl
        self.agl, self.moderngl = agl, moderngl
        self.tree = Tree()
        self.foliage = Foliage(self.tree)
        self.particles = Particles(self.tree)
        self.stage = agl.Stage(WIDTH, HEIGHT, margin=MARGIN)
        self.dof = agl.DepthOfField(ORBIT_RADIUS, DOF_SCALE, DOF_FAR_SIGMAS, DOF_NEAR_SIGMAS, sharp=DOF_SHARP_RANGE)
        ctx = self.stage.ctx
        verts, idx = tube_mesh(self.tree)
        self.branch_prog = self.stage.program(BRANCH_VS, BRANCH_FS)
        self.branch_vao = ctx.vertex_array(self.branch_prog, [
            (ctx.buffer(verts.tobytes()), "3f 3f 3f 4f", "in_center", "in_offset", "in_normal", "in_data"),
        ], index_buffer=ctx.buffer(idx.tobytes()), skip_errors=True)

        self.foliage_prog = self.stage.program(FOLIAGE_VS, FOLIAGE_FS)
        grid, grid_idx = agl.grid_mesh(*FOLIAGE_GRID)
        grid_vbo, grid_ibo = ctx.buffer(grid.tobytes()), ctx.buffer(grid_idx.tobytes())
        names = ("i_anchor", "i_quat", "i_params", "i_times", "i_fall", "i_fall2", "i_color")
        self.sets = []
        for kind, data in ((0.0, self.foliage.petals), (1.0, self.foliage.leaves)):
            vao = ctx.vertex_array(self.foliage_prog, [
                (grid_vbo, "2f", "in_uv"),
                (ctx.buffer(np.ascontiguousarray(data).tobytes()), "4f 4f 4f 4f 4f 4f 4f/i", *names),
            ], index_buffer=grid_ibo, skip_errors=True)
            self.sets.append((kind, vao, len(data)))

        self.water_prog = self.stage.program(WATER_VS, WATER_FS)
        quad = np.array([[-60, -60], [60, -60], [-60, 60], [60, 60]], dtype=np.float32)
        self.water_vao = ctx.vertex_array(self.water_prog, [(ctx.buffer(quad.tobytes()), "2f", "in_pos")])
        self.reflection_tex, self.reflection = self.stage.depth_target((self.stage.width // 2, self.stage.height // 2))

        # Ripples: a sample of the real landings, each where and when it happens.
        rng = np.random.RandomState(SEED + 31)
        events = []
        for data, offset, share in ((self.foliage.petals, 0.04, 0.035), (self.foliage.leaves, 0.11, 0.10)):
            xz, when = landings(data, offset)
            keep = (rng.uniform(size=len(when)) < share) & (np.hypot(xz[:, 0], xz[:, 1]) < 9.0)
            events.append(np.column_stack([xz[keep], when[keep], rng.uniform(0.5, 1.0, keep.sum())]))
        self.ripple_events = thin_events(np.concatenate(events), RIPPLE_LIFE_S / RIPPLE_CAP)
        agl.settle(self.render)

    # -- per-frame state ---------------------------------------------------------

    def ripples(self, t, snow_xz=None, snow_frac=None, snow_amount=0.0):
        ev = self.ripple_events
        age = (t - ev[:, 2]) % DURATION_S
        live = age < RIPPLE_LIFE_S
        rows = np.column_stack([ev[live, 0], ev[live, 1], age[live], ev[live, 3]])
        if snow_amount > 0.0 and snow_xz is not None:
            # Every third snowflake rings as it touches down: tiny, short-lived
            # rings that are gone again before the flake sets out anew.
            landed = np.nonzero((snow_frac < SNOW_RING) & (np.arange(len(snow_frac)) % 3 == 0)
                                & (np.hypot(snow_xz[:, 0], snow_xz[:, 1]) < 7.0))[0]
            period = DURATION_S / self.particles.snow_cycles[landed]
            spent = snow_frac[landed] / SNOW_RING
            snow_rows = np.column_stack([snow_xz[landed], snow_frac[landed] * period,
                                         0.35 * snow_amount * (1.0 - smooth((spent - 0.5) / 0.5))])
            rows = np.concatenate([rows, snow_rows])
        return rows[np.argsort(rows[:, 2], kind="stable")][:MAX_RIPPLES]

    def _foliage_uniforms(self, prog, t, view, proj, eye, gust, mirror, jitter, slab):
        agl = self.agl
        prog["u_view"].write(agl.mat_bytes(view))
        prog["u_proj"].write(agl.mat_bytes(proj))
        prog["u_eye"].value = tuple(float(v) for v in eye)
        prog["u_time"].value = t
        prog["u_duration"].value = DURATION_S
        prog["u_gust"].value = gust
        prog["u_mirror"].value = mirror
        prog["u_jitter"].value = (jitter[0] * 2.0 / self.stage.width, jitter[1] * 2.0 / self.stage.height)
        prog["u_slab"].value = slab
        prog["u_slab_range"].value = (float(self.dof.coords[0]), float(self.dof.coords[-1]))
        prog["u_lens"].value = (self.dof.focus, self.dof.scale, self.dof.sharp, 2.6)

    def draw_tree(self, t, view, proj, eye, state, *, mirror=1.0, jitter=(0.0, 0.0), slab=None, branches=True):
        mgl, ctx = self.moderngl, self.stage.ctx
        ctx.enable(mgl.DEPTH_TEST)
        ctx.disable(mgl.CULL_FACE)
        ctx.depth_func = "<="
        axis_dist = float(np.hypot(eye[0], eye[2]))
        if branches:
            prog = self.branch_prog
            ctx.disable(mgl.BLEND)
            prog["u_view"].write(self.agl.mat_bytes(view))
            prog["u_proj"].write(self.agl.mat_bytes(proj))
            prog["u_eye"].value = tuple(float(v) for v in eye)
            prog["u_time"].value = t
            prog["u_duration"].value = DURATION_S
            prog["u_size"].value = (self.stage.width, self.stage.height)
            prog["u_gust"].value = state["gust"]
            prog["u_tint"].value = state["branch_tint"]
            prog["u_snow"].value = state["snow_cap"]
            prog["u_level"].value = state["branch_level"]
            prog["u_mirror"].value = mirror
            prog["u_axis_dist"].value = axis_dist
            prog["u_jitter"].value = (jitter[0] * 2.0 / self.stage.width, jitter[1] * 2.0 / self.stage.height)
            self.branch_vao.render(mgl.TRIANGLES)
        prog = self.foliage_prog
        ctx.enable(mgl.BLEND)
        ctx.blend_func = mgl.ONE, mgl.ONE_MINUS_SRC_ALPHA
        focus = self.dof.slab_uniform(self.dof.focus_index)
        self._foliage_uniforms(prog, t, view, proj, eye, state["gust"], mirror, jitter, slab or focus)
        prog["u_key"].value = state["key"]
        prog["u_axis_dist"].value = axis_dist
        for kind, vao, count in self.sets:
            prog["u_kind"].value = kind
            vao.render(mgl.TRIANGLES, instances=count)
        ctx.disable(mgl.BLEND)

    def render(self, frame_idx):
        """The finished frame (BGR) and the mean luma left in its widget zone."""
        with self.stage.ctx:        # a process may hold several renderers, each with a context of its own
            return self._render(frame_idx)

    def _render(self, frame_idx):
        agl, mgl = self.agl, self.moderngl
        stage, ctx = self.stage, self.stage.ctx
        t = (frame_idx % N_FRAMES) / FPS
        eye, view, proj = camera_matrices(t)
        proj = stage.expand(proj)
        weights = season_weights(t)
        gust = float(gust_level(t))
        winter_t = cyclic(t - 105.0)
        snow_fall = float(smooth((winter_t + 13.5) / 5.0) * (1.0 - smooth((winter_t - 9.0) / 5.0)))
        snow_cap = float(smooth((winter_t + 9.0) / 9.0) * (1.0 - smooth((winter_t - 7.0) / 7.0)))
        summer_t = cyclic(t - 49.0)
        fireflies = float(smooth((summer_t + 14.0) / 6.0) * (1.0 - smooth((summer_t - 9.0) / 6.0)))
        # The key light rides with the camera so form reads the same all the way round.
        right, up, back = view[0, :3], view[1, :3], view[2, :3]
        key = KEY_LIGHT_LOCAL[0] * right + KEY_LIGHT_LOCAL[1] * up + KEY_LIGHT_LOCAL[2] * back
        state = dict(
            gust=gust, snow_cap=snow_cap, key=tuple(float(v) for v in key),
            branch_tint=tuple(float(sum(weights[s] * BRANCH_TINTS[s][c] for s in range(4))) for c in range(3)),
            branch_level=float(sum(weights[s] * BRANCH_LEVELS[s] for s in range(4))),
        )
        snow, snow_xz, snow_frac = self.particles.snow(t, snow_fall)
        if snow_fall > 0.0:
            # Falling snow is smeared across the open shutter like everything else.
            smear = [self.particles.snow((t + dt * SHUTTER / FPS) % DURATION_S, snow_fall / 3.0)[0] for dt in (-0.5, 0.0, 0.5)]
            snow = np.concatenate(smear)
        flies = self.particles.fireflies(t, fireflies)

        # 1. The mirrored tree, for the water.
        mirror_view = view @ np.diag([1.0, -1.0, 1.0, 1.0])
        self.reflection.use()
        self.reflection.clear(0.0, 0.0, 0.0, 0.0, depth=1.0)
        ctx.viewport = (0, 0, stage.width // 2, stage.height // 2)
        self.draw_tree(t, view, proj, eye, state, mirror=-1.0)
        stage.draw_sprites("flies-mirror", flies, mirror_view, proj, self.dof, target=self.reflection, min_px=0.6)
        ctx.viewport = (0, 0, stage.width, stage.height)
        reflection = stage.blur(self.reflection_tex, 1.6)

        # 2. Still water under everything.
        stage.begin()
        ripples = self.ripples(t, snow_xz, snow_frac, snow_fall)
        prog = self.water_prog
        prog["u_view"].write(agl.mat_bytes(view))
        prog["u_proj"].write(agl.mat_bytes(proj))
        prog["u_size"].value = (stage.width, stage.height)
        prog["u_eye"].value = tuple(float(v) for v in eye)
        prog["u_time"].value = t
        prog["u_duration"].value = DURATION_S
        prog["u_level"].value = 1.0
        prog["u_ripple_count"].value = len(ripples)
        packed = np.zeros((MAX_RIPPLES, 4), dtype=np.float32)
        packed[:len(ripples)] = ripples
        prog["u_ripples"].write(packed.tobytes())
        prog["u_ripple_tint"].value = tuple(float(sum(weights[s] * RIPPLE_TINTS[s][c] for s in range(4))) for c in range(3))
        reflection.use(location=0)
        prog["u_reflection"].value = 0
        stage.hdr.use()
        ctx.disable(mgl.DEPTH_TEST | mgl.BLEND)
        self.water_vao.render(mgl.TRIANGLE_STRIP)

        # 3. The tree in focus, then whatever flies between it and the lens.
        for k, sigma in enumerate(self.dof.sigmas):
            if k < self.dof.focus_index:
                continue
            slab = self.dof.slab_uniform(k)
            is_focus = k == self.dof.focus_index

            def draw(sub, jitter, slab=slab, is_focus=is_focus):
                ts = (t + (sub / SUBFRAMES - 0.5) * SHUTTER / FPS) % DURATION_S
                self.draw_tree(ts, view, proj, eye, state, jitter=jitter, slab=slab, branches=is_focus)

            stage.render_slab(sigma, draw, subframes=SUBFRAMES)

        # 4. Points of light, each with its own circle of confusion.
        mote_tint = sum(weights[s] * np.array(c) for s, c in enumerate(
            ((1.0, 0.72, 0.62), (0.80, 1.0, 0.55), (1.0, 0.60, 0.22), (0.78, 0.88, 1.0))))
        lights = np.concatenate([
            snow, flies, self.particles.motes(t, mote_tint, 0.8 * (1.0 - 0.6 * snow_fall)),
            self.particles.frost(t, gust, snow_cap),
        ])
        stage.draw_sprites("lights", lights, view, proj, self.dof, coc_max=42.0, min_px=0.75, near_fade=2.5)
        frame = stage.finish(frame_idx % N_FRAMES, bloom_threshold=0.62, bloom_knee=0.30, bloom_gain=0.42,
                             quiet_rect=artwork.WIDGET_RECT, quiet_feather=QUIET_FEATHER, quiet_floor=0.03)
        return artwork.enforce_widget_zone(frame)


_RENDERER = None


def worker_init():
    global _RENDERER
    _RENDERER = FrameRenderer()


def worker_frame(frame_idx):
    return _RENDERER.render(frame_idx)


SPEC = artwork.ArtworkSpec(
    description=__doc__,
    slug=SLUG,
    output_path=OUTPUT_PATH,
    n_frames=N_FRAMES,
    worker_init=worker_init,
    worker_frame=worker_frame,
    default_workers=3,
)


def main(argv=None):
    return artwork.run_cli(SPEC, argv)


if __name__ == "__main__":
    main()
