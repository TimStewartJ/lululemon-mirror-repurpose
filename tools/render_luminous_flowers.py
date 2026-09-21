#!/usr/bin/env python3
"""Offline renderer for the spatial luminous flowers background film.

A deterministic 180-second, 1080x1920, 30 FPS seamless loop: a slow drift
through a dark space of original, procedurally grown flowers made of light.
Everything is real 3D. Each petal is a curved, cupped, veined surface; petals
unfurl from a spiralled bud ring by ring, breathe in full bloom and then let go
one at a time, tumbling toward and past the lens as soft discs of colour. A
perspective camera sways around a focal plane, so near and far flowers slide
against each other, and depth of field leaves only the flowers at the focal
plane crisp.

Flowers gather along a slowly undulating ribbon instead of being sprinkled
evenly, bloom and scatter in waves that travel along it, and follow the seasons:
cherry, camellia and peony; lotus, lily and cosmos; chrysanthemum and aster;
then plum, narcissus and white camellia.

Black pixels keep the Mirror's glass reflective, so the flowers appear to bloom
in the room. It references no specific third-party artwork; generic qualities
(emissive colour on darkness, perpetual birth and scattering) are built from
first principles with moderngl, NumPy and OpenCV.

Run:
    python tools\\render_luminous_flowers.py 30                    # smoke clip
    python tools\\render_luminous_flowers.py 180 --start-frame 2850 # a scatter
    python tools\\render_luminous_flowers.py --contact-sheet
    python tools\\render_luminous_flowers.py --frames 0,1350,2700,4050
    python tools\\render_luminous_flowers.py                       # full film

Output:
    generated\\background-videos\\luminous-flowers-spatial-180s.mp4
    generated\\background-videos\\previews\\ (smoke clips, previews, reports)
"""

from __future__ import annotations

import math

import numpy as np

import artwork_video as artwork

WIDTH = artwork.WIDTH
HEIGHT = artwork.HEIGHT
FPS = artwork.FPS
DURATION_S = 180.0
N_FRAMES = int(round(FPS * DURATION_S))
SEASON_LEN = DURATION_S / 4.0

SEED = 20260918
SLUG = "luminous-flowers-spatial"
OUTPUT_PATH = artwork.VIDEO_DIR / "luminous-flowers-spatial-180s.mp4"

# Mostly-black flowers encode well below the cap, so the longer loop spends
# that headroom on variety: an 11 Mbps VBV cap keeps 180 s under the upload limit.
MAXRATE_KBPS = 11_000
BUFSIZE_KBPS = 16_000

SPRING, SUMMER, AUTUMN, WINTER = 0, 1, 2, 3

# Camera and lens. World units are arbitrary; the focal plane is 12 away.
FOV_Y = 38.0
FOCUS = 12.0
NEAR, FAR = 2.0, 80.0
DOF_SCALE = 105.0
DOF_FAR_SIGMAS = (1.4, 2.8, 4.2, 5.8)
DOF_NEAR_SIGMAS = (1.4, 2.8, 4.6, 7.0, 10.5, 15.0)
MARGIN = 48                  # px rendered around the frame: the reach of the widest blur
SUBFRAMES = 4                # moments drawn per frame while the shutter is open
SHUTTER = 0.5                # share of the frame time the shutter stays open
TAN_HALF = math.tan(math.radians(FOV_Y) * 0.5)
ASPECT = WIDTH / HEIGHT

# Everything rides one slow current toward the lens, like walking through it.
DRIFT = np.array([0.0, 0.0, 0.2])
BREEZE = np.array([0.09, 0.05, 0.42])

# Widget corner: flowers are never planted there and the display transform
# fades the art smoothly to black around it.
QUIET_X = 0.46
QUIET_Y = 0.30
QUIET_FEATHER_PX = 230.0
QUIET_FLOOR = 0.03

EXPOSURE = 1.0
GRAIN = 0.75
KEY_LIGHT = tuple(np.array([-0.45, 0.62, 0.64]) / np.linalg.norm([-0.45, 0.62, 0.64]))

# --------------------------------------------------------------------------
# Species: rings of petals, outermost first
# --------------------------------------------------------------------------

SHAPES = {
    "round": (0.85, 0.40),
    "obovate": (1.10, 0.42),
    "broad": (1.00, 0.33),
    "pointed": (0.75, 0.95),
    "lance": (0.60, 1.10),
    "ray": (0.50, 0.60),
}


def ring(count, length, width, shape, elev, k1, k2=0.0, cup=0.25, ruffle=0.0, roll=8.0,
         rb=0.05, lift=0.0, notch=0.0, teeth=0.0, stagger=0.0, veins=9.0):
    p, q = SHAPES[shape]
    return dict(count=count, length=length, width=width, p=p, q=q, elev=math.radians(elev), k1=k1, k2=k2,
                cup=cup, ruffle=ruffle, roll=math.radians(roll), rb=rb, lift=lift, notch=notch,
                teeth=teeth, stagger=stagger, veins=veins)


# stamens: (count, length, spread degrees); disc: (florets, radius)
SPECIES = {
    "cherry": dict(
        rings=(ring(5, 1.0, 0.80, "obovate", 12, -0.30, cup=0.22, ruffle=0.03, roll=11, notch=0.17, veins=11),),
        stamens=(26, 0.40, 34), disc=None, core=0.07),
    "plum": dict(
        rings=(ring(5, 1.0, 0.94, "round", 15, -0.20, cup=0.30, roll=13, veins=9),),
        stamens=(34, 0.46, 40), disc=None, core=0.08),
    "camellia": dict(
        rings=(ring(6, 1.0, 0.88, "round", 17, -0.40, cup=0.30, roll=14, notch=0.05),
               ring(6, 0.80, 0.86, "round", 38, -0.15, cup=0.36, roll=12, stagger=0.5, lift=0.03),
               ring(5, 0.58, 0.80, "round", 58, 0.25, cup=0.42, roll=10, stagger=0.25, lift=0.06)),
        stamens=(44, 0.30, 16), disc=None, core=0.10),
    "peony": dict(
        rings=(ring(6, 1.0, 0.82, "round", 14, -0.35, cup=0.30, ruffle=0.07, roll=13, notch=0.06),
               ring(7, 0.88, 0.80, "round", 31, -0.10, cup=0.34, ruffle=0.10, roll=12, stagger=0.5, lift=0.02),
               ring(7, 0.74, 0.78, "round", 47, 0.20, cup=0.38, ruffle=0.12, roll=10, stagger=0.2, lift=0.05),
               ring(6, 0.58, 0.74, "round", 61, 0.45, cup=0.42, ruffle=0.12, roll=9, stagger=0.7, lift=0.08),
               ring(5, 0.42, 0.70, "round", 73, 0.75, cup=0.46, ruffle=0.10, roll=8, stagger=0.4, lift=0.10)),
        stamens=(18, 0.20, 14), disc=None, core=0.08),
    "lotus": dict(
        rings=(ring(8, 1.0, 0.44, "pointed", 20, 0.40, cup=0.46, roll=6, veins=13),
               ring(8, 0.88, 0.44, "pointed", 41, 0.45, cup=0.48, roll=6, stagger=0.5, lift=0.03, veins=13),
               ring(6, 0.68, 0.42, "pointed", 61, 0.55, cup=0.50, roll=5, stagger=0.25, lift=0.06, veins=11)),
        stamens=(36, 0.24, 30), disc=(40, 0.13), core=0.09),
    "lily": dict(
        rings=(ring(3, 1.0, 0.36, "pointed", 38, -0.55, k2=-1.15, cup=0.30, ruffle=0.04, roll=4, veins=7),
               ring(3, 0.96, 0.31, "pointed", 40, -0.50, k2=-1.10, cup=0.28, ruffle=0.04, roll=4, stagger=0.5,
                    veins=7)),
        stamens=(6, 0.78, 22), disc=None, core=0.06),
    "cosmos": dict(
        rings=(ring(8, 1.0, 0.56, "broad", 8, -0.18, cup=0.12, ruffle=0.02, roll=9, teeth=0.8, veins=9),),
        stamens=None, disc=(110, 0.17), core=0.05),
    "chrysanthemum": dict(
        rings=(ring(21, 1.0, 0.135, "ray", 7, -0.95, cup=0.55, roll=3, veins=3),
               ring(21, 0.90, 0.135, "ray", 21, -0.50, cup=0.55, roll=3, stagger=0.5, lift=0.02, veins=3),
               ring(13, 0.74, 0.150, "ray", 39, 0.05, cup=0.55, roll=3, stagger=0.3, lift=0.04, veins=3),
               ring(13, 0.57, 0.160, "ray", 57, 0.75, cup=0.55, roll=3, stagger=0.8, lift=0.06, veins=3),
               ring(8, 0.41, 0.175, "ray", 71, 1.25, cup=0.55, roll=3, stagger=0.1, lift=0.08, veins=3)),
        stamens=None, disc=None, core=0.06),
    "aster": dict(
        rings=(ring(21, 1.0, 0.17, "ray", 6, -0.22, cup=0.30, roll=4, veins=3),
               ring(13, 0.82, 0.18, "ray", 15, -0.10, cup=0.30, roll=4, stagger=0.5, lift=0.02, veins=3)),
        stamens=None, disc=(130, 0.20), core=0.05),
    "narcissus": dict(
        rings=(ring(6, 1.0, 0.46, "pointed", 9, -0.22, cup=0.18, roll=7, veins=9),
               ring(6, 0.36, 1.25, "broad", 76, 0.05, cup=0.55, ruffle=0.10, roll=0, stagger=0.5, lift=0.02,
                    rb=0.10, veins=7)),
        stamens=(6, 0.24, 10), disc=None, core=0.07),
}

SPECIES_NAMES = tuple(SPECIES)

SPECIES_BY_SEASON = {
    SPRING: (("cherry", 0.40), ("peony", 0.25), ("camellia", 0.20), ("plum", 0.15)),
    SUMMER: (("lotus", 0.34), ("lily", 0.26), ("cosmos", 0.24), ("aster", 0.16)),
    AUTUMN: (("chrysanthemum", 0.50), ("aster", 0.22), ("cosmos", 0.18), ("camellia", 0.10)),
    WINTER: (("plum", 0.34), ("narcissus", 0.30), ("camellia", 0.20), ("chrysanthemum", 0.16)),
}

# (throat, body, tip, stamen) in display sRGB. A ring may carry its own palette
# (the narcissus corona).
PALETTES = {
    SPRING: (((214, 40, 110), (255, 138, 186), (255, 214, 230), (255, 226, 140)),
             ((226, 50, 70), (255, 120, 110), (255, 200, 176), (255, 236, 170)),
             ((255, 110, 160), (255, 214, 228), (255, 244, 248), (255, 214, 120)),
             ((150, 20, 120), (236, 60, 160), (255, 160, 214), (255, 232, 160))),
    SUMMER: (((255, 236, 170), (255, 170, 210), (250, 70, 150), (255, 214, 96)),
             ((60, 30, 190), (124, 90, 255), (120, 200, 255), (255, 240, 200)),
             ((20, 40, 200), (60, 110, 255), (170, 220, 255), (255, 236, 180)),
             ((120, 20, 170), (250, 70, 200), (190, 130, 255), (255, 226, 150)),
             ((0, 120, 130), (40, 210, 185), (180, 255, 228), (255, 244, 190))),
    AUTUMN: (((230, 100, 10), (255, 176, 40), (255, 232, 130), (255, 236, 170)),
             ((190, 20, 20), (255, 84, 36), (255, 170, 70), (255, 230, 150)),
             ((120, 8, 40), (220, 30, 64), (255, 120, 96), (255, 214, 130)),
             ((110, 20, 90), (196, 60, 130), (255, 150, 126), (255, 220, 150))),
    WINTER: (((120, 170, 255), (214, 232, 255), (255, 255, 255), (255, 222, 130)),
             ((40, 90, 220), (130, 186, 255), (226, 242, 255), (255, 236, 180)),
             ((90, 60, 210), (170, 144, 255), (236, 226, 255), (255, 230, 170)),
             ((130, 6, 30), (232, 34, 56), (255, 100, 100), (255, 214, 110))),
}
NARCISSUS_PETALS = ((255, 236, 170), (255, 250, 232), (255, 255, 255), (255, 214, 110))
NARCISSUS_CORONA = ((255, 120, 10), (255, 180, 30), (255, 220, 100))
WINTER_ACCENT_WEIGHTS = (0.34, 0.26, 0.26, 0.14)

MOTE_COLORS = {
    SPRING: (255, 214, 190), SUMMER: (190, 236, 255), AUTUMN: (255, 196, 110), WINTER: (226, 238, 255),
}

# (name, slots, world radius, bloom distance, brightness, ribbon spread, tilt spread degrees)
BANDS = (
    ("far", 26, (0.55, 0.95), (27.0, 40.0), 0.55, 0.30, 40.0),
    ("mid", 12, (0.50, 0.80), (16.5, 22.0), 0.85, 0.15, 34.0),
    ("hero", 7, (0.58, 0.88), (12.0, 13.6), 1.00, 0.085, 28.0),
)

BIRTH_FADE_S = 2.5
LIFT_S = 1.5                 # a released petal's hand-over from its flower's depth layer to its own
RING_BIAS, PETAL_BIAS = 0.10, 0.003   # depth nudges, in flower radii, per ring and per petal
DISSOLVE_FROM = 0.48
AFTERGLOW_S = 3.5


def srgb_to_linear(rgb):
    return np.power(np.asarray(rgb, dtype=np.float64) / 255.0, 2.2)


def smooth(x):
    x = np.clip(x, 0.0, 1.0)
    return x * x * (3.0 - 2.0 * x)


def season_of(t):
    return int((t % DURATION_S) // SEASON_LEN) % 4


def season_weights(t):
    """Soft membership of loop time ``t`` in each season (sums to one)."""
    pos = (t % DURATION_S) / SEASON_LEN - 0.5
    w = np.zeros(4)
    lo = int(math.floor(pos)) % 4
    frac = smooth((pos - math.floor(pos) - 0.35) / 0.3)
    w[lo] += 1.0 - frac
    w[(lo + 1) % 4] += frac
    return w


# --------------------------------------------------------------------------
# Camera
# --------------------------------------------------------------------------

CAMERA_PHASES = np.random.RandomState(SEED + 7).uniform(0, 2 * math.pi, 4)


def camera_eye(t):
    """Slow periodic sway around the focal point: pure parallax, no cuts."""
    phase = 2 * math.pi * (t % DURATION_S) / DURATION_S
    x = 0.95 * math.sin(2 * phase + CAMERA_PHASES[0]) + 0.25 * math.sin(3 * phase + CAMERA_PHASES[1])
    y = 0.50 * math.sin(phase + CAMERA_PHASES[2]) + 0.22 * math.sin(4 * phase + CAMERA_PHASES[3])
    return np.array([x, y, 0.0])


def camera_matrices(t):
    import artwork_gl as agl
    eye = camera_eye(t)
    view = agl.look_at(eye, (0.0, 0.0, -FOCUS))
    proj = agl.perspective(FOV_Y, ASPECT, NEAR, FAR)
    return eye, view, proj


def screen_to_world(sx, sy, dist):
    """World point at ``dist`` in front of the resting camera for normalized
    screen coordinates (0..1, top-left origin)."""
    return np.array([(sx - 0.5) * 2.0 * TAN_HALF * ASPECT * dist, (0.5 - sy) * 2.0 * TAN_HALF * dist, -dist])


def ribbon_x(s, birth):
    """The undulating ribbon the flowers gather along; it travels over the loop."""
    return 0.53 + 0.30 * math.sin(2 * math.pi * (0.85 * s + birth / DURATION_S) + 0.6)


# --------------------------------------------------------------------------
# Rotation helpers (vectorized; quaternions are x, y, z, w)
# --------------------------------------------------------------------------


def axis_angle_matrix(axis, angle):
    axis = axis / np.maximum(np.linalg.norm(axis, axis=-1, keepdims=True), 1e-12)
    x, y, z = axis[..., 0], axis[..., 1], axis[..., 2]
    c, s = np.cos(angle), np.sin(angle)
    C = 1.0 - c
    m = np.empty(axis.shape[:-1] + (3, 3))
    m[..., 0, 0], m[..., 0, 1], m[..., 0, 2] = c + x * x * C, x * y * C - z * s, x * z * C + y * s
    m[..., 1, 0], m[..., 1, 1], m[..., 1, 2] = y * x * C + z * s, c + y * y * C, y * z * C - x * s
    m[..., 2, 0], m[..., 2, 1], m[..., 2, 2] = z * x * C - y * s, z * y * C + x * s, c + z * z * C
    return m


def facing_matrix(rng, tilt_sigma_deg):
    """Random orientation whose +Z axis leans toward the viewer."""
    tilt = min(abs(rng.normal(0.0, math.radians(tilt_sigma_deg))), math.radians(72.0))
    around = rng.uniform(0, 2 * math.pi)
    axis = np.array([-math.sin(around), math.cos(around), 0.0])
    lean = axis_angle_matrix(axis[None], np.array([tilt]))[0]
    spin = axis_angle_matrix(np.array([[0.0, 0.0, 1.0]]), np.array([rng.uniform(0, 2 * math.pi)]))[0]
    return lean @ spin


# --------------------------------------------------------------------------
# Scene: flower slots live whole cycles per loop; every cycle is a new flower
# --------------------------------------------------------------------------


def weighted_choice(rng, options):
    names = [n for n, _ in options]
    weights = np.array([w for _, w in options], dtype=np.float64)
    return names[int(rng.choice(len(names), p=weights / weights.sum()))]


PETAL_FIELDS = ("flower", "ring", "psi", "length", "width", "p", "q", "notch", "teeth", "elev", "k1", "k2",
                "cup", "ruffle", "roll", "rb", "lift", "veins", "release", "life", "d_out", "flutter_amp",
                "flutter_hz", "flutter_phase", "tumble_rate", "seed")
PETAL_VECTORS = ("breeze", "flutter_dir", "tumble_axis", "throat", "body", "tip")
FLOWER_FIELDS = ("birth", "period", "band", "radius", "u_mid", "rock_amp", "rock_period", "rock_phase",
                 "spin", "u_open", "t_open", "scatter_start", "scatter_len", "twist", "breathe", "level",
                 "season", "species", "sway_phase", "core")
LIGHT_FIELDS = ("flower", "kind", "polar", "around", "reach", "size", "gain", "twinkle")

KIND_CORE, KIND_INNER, KIND_ANTHER, KIND_FLORET = 0, 1, 2, 3


class Scene:
    def __init__(self):
        rng = np.random.RandomState(SEED)
        flowers = {k: [] for k in FLOWER_FIELDS}
        flowers.update(bloom_pos=[], base=[], rock_axis=[], stamen_color=[])
        petals = {k: [] for k in PETAL_FIELDS + PETAL_VECTORS}
        lights = {k: [] for k in LIGHT_FIELDS}
        lights.update(color=[])
        self.slots = []
        for band_index, (band, count, radius_range, dist_range, level, spread, tilt) in enumerate(BANDS):
            for slot_index in range(count):
                k = int(rng.choice((3, 4)))
                period = DURATION_S / k
                home = (slot_index + rng.uniform(0.15, 0.85)) / count
                # Bloom and scatter travel along the ribbon as a wave.
                phase = (home * period * 1.0 + rng.uniform(-5.0, 5.0)) % period
                cycles = []
                for c in range(k):
                    birth = (c * period - phase) % DURATION_S
                    cycles.append(len(flowers["birth"]))
                    self._add_flower(rng, flowers, petals, lights, birth, period, band_index, band, home,
                                     radius_range, dist_range, level, spread, tilt, slot_index)
                self.slots.append(dict(band=band, period=period, phase=phase, cycles=cycles, home=home))

        self.flowers = {k: np.asarray(v, dtype=np.float64) for k, v in flowers.items()}
        self.petals = {k: np.asarray(v, dtype=np.float64) for k, v in petals.items()}
        self.lights = {k: np.asarray(v, dtype=np.float64) for k, v in lights.items()}
        for table in (self.petals, self.lights):
            table["flower"] = table["flower"].astype(np.int64)
        self.n_flowers = len(self.flowers["birth"])
        self.n_petals = len(self.petals["flower"])
        self.n_lights = len(self.lights["flower"])
        # Each light's place among the lights of its flower (they are stored flower by flower).
        first = np.searchsorted(self.lights["flower"], np.arange(self.n_flowers))
        self.lights["slot"] = np.arange(self.n_lights) - first[self.lights["flower"]]
        self.lights["count"] = np.bincount(self.lights["flower"], minlength=self.n_flowers)[self.lights["flower"]]
        # Petals that lie almost in one plane (neighbours in a ring, ring upon ring)
        # must not fight over who is in front as the flower moves. Each petal is
        # nudged toward the viewer by its place in the flower, so later petals and
        # inner rings settle on top, while depth still decides between petals that
        # are truly apart, such as the near and far walls of a cupped flower.
        first = np.searchsorted(self.petals["flower"], np.arange(self.n_flowers))
        place = np.arange(self.n_petals) - first[self.petals["flower"]]
        self.petals["bias"] = (RING_BIAS * self.petals["ring"] + PETAL_BIAS * place) * self.flowers["radius"][self.petals["flower"]]
        self._build_motes()

    def _add_flower(self, rng, flowers, petals, lights, birth, period, band_index, band, home,
                    radius_range, dist_range, level, spread, tilt, slot_index):
        index = len(flowers["birth"])
        season = season_of(birth + 8.0)
        species = weighted_choice(rng, SPECIES_BY_SEASON[season])
        spec = SPECIES[species]
        palettes = PALETTES[season]
        if season == WINTER:
            palette = palettes[int(rng.choice(len(palettes), p=WINTER_ACCENT_WEIGHTS))]
        else:
            palette = palettes[int(rng.randint(len(palettes)))]
        if species == "narcissus":
            palette = NARCISSUS_PETALS
        radius = rng.uniform(*radius_range)
        dist = rng.uniform(*dist_range)

        u_open = rng.uniform(2.0, 5.0)
        t_open = rng.uniform(8.0, 12.0)
        scatter_len = rng.uniform(5.5, 8.5)
        scatter_start = period - scatter_len - AFTERGLOW_S - rng.uniform(2.0, 5.0)
        u_mid = 0.5 * (u_open + t_open + scatter_start)

        for _ in range(400):
            s = np.clip(0.04 + 0.98 * home + rng.normal(0.0, 0.045), 0.02, 1.04)
            sx = ribbon_x(s, birth) + rng.normal(0.0, spread)
            sy = s + rng.normal(0.0, spread * 0.35)
            if not -0.08 < sx < 1.08:
                continue
            margin = radius / (2.0 * TAN_HALF * dist)
            if sx - margin / ASPECT < QUIET_X and sy - margin < QUIET_Y:
                continue
            break
        else:
            sx, sy = 0.8, 0.7
        bloom_pos = screen_to_world(sx, sy, dist)

        flowers["birth"].append(birth)
        flowers["period"].append(period)
        flowers["band"].append(band_index)
        flowers["radius"].append(radius)
        flowers["u_mid"].append(u_mid)
        flowers["bloom_pos"].append(bloom_pos)
        flowers["base"].append(facing_matrix(rng, tilt))
        axis = rng.normal(size=3)
        axis[2] *= 0.3
        flowers["rock_axis"].append(axis / np.linalg.norm(axis))
        flowers["rock_amp"].append(math.radians(rng.uniform(10.0, 24.0)))
        flowers["rock_period"].append(rng.uniform(17.0, 29.0))
        flowers["rock_phase"].append(rng.uniform(0, 2 * math.pi))
        flowers["spin"].append(math.radians(rng.uniform(1.0, 3.2)) * rng.choice((-1.0, 1.0)))
        flowers["u_open"].append(u_open)
        flowers["t_open"].append(t_open)
        flowers["scatter_start"].append(scatter_start)
        flowers["scatter_len"].append(scatter_len)
        flowers["twist"].append(rng.uniform(0.7, 1.3) * rng.choice((-1.0, 1.0)))
        flowers["breathe"].append(rng.uniform(0, 2 * math.pi))
        flowers["level"].append(level * rng.uniform(0.85, 1.1))
        flowers["season"].append(season)
        flowers["species"].append(SPECIES_NAMES.index(species))
        flowers["sway_phase"].append(rng.uniform(0, 2 * math.pi))
        flowers["core"].append(spec["core"])
        flowers["stamen_color"].append(srgb_to_linear(palette[3]))

        rings = spec["rings"]
        total = sum(r["count"] for r in rings)
        order = rng.permutation(total)
        slot = 0
        for ring_index, r in enumerate(rings):
            colors = NARCISSUS_CORONA if species == "narcissus" and ring_index == 1 else palette[:3]
            throat, body, tip = (srgb_to_linear(c) for c in colors)
            start = rng.uniform(0, 2 * math.pi)
            for i in range(r["count"]):
                jitter = rng.normal(0.0, 0.035)
                petals["flower"].append(index)
                petals["ring"].append(ring_index)
                petals["psi"].append(start + (i + r["stagger"]) * 2 * math.pi / r["count"] + jitter)
                petals["length"].append(radius * r["length"] * rng.uniform(0.93, 1.05))
                petals["width"].append(r["width"] * rng.uniform(0.94, 1.06))
                for key in ("p", "q", "notch", "teeth", "k1", "k2", "cup", "ruffle", "roll", "rb", "lift", "veins"):
                    petals[key].append(r[key])
                petals["elev"].append(r["elev"] + rng.normal(0.0, 0.04))
                # Outer rings tend to let go first; every petal has its own moment.
                rank = (0.65 * order[slot] / max(total - 1, 1)
                        + 0.35 * ring_index / max(len(rings) - 1, 1) if len(rings) > 1
                        else order[slot] / max(total - 1, 1))
                slot += 1
                petals["release"].append(scatter_start + scatter_len * rank)
                petals["life"].append(rng.uniform(9.0, 14.0))
                petals["d_out"].append(rng.uniform(0.35, 1.0) * radius)
                petals["breeze"].append(BREEZE * rng.uniform(0.6, 1.35) + rng.normal(0.0, 0.07, 3))
                direction = rng.normal(size=3)
                petals["flutter_dir"].append(direction / np.linalg.norm(direction))
                petals["flutter_amp"].append(rng.uniform(0.05, 0.16) * (0.6 + radius))
                petals["flutter_hz"].append(rng.uniform(0.22, 0.48))
                petals["flutter_phase"].append(rng.uniform(0, 2 * math.pi))
                axis = rng.normal(size=3) * np.array([1.0, 0.45, 0.45])
                petals["tumble_axis"].append(axis / np.linalg.norm(axis))
                petals["tumble_rate"].append(rng.uniform(0.55, 1.5) * rng.choice((-1.0, 1.0)))
                petals["seed"].append(rng.uniform(0.0, 1.0))
                petals["throat"].append(throat)
                petals["body"].append(body)
                petals["tip"].append(tip)

        stamen_color = srgb_to_linear(palette[3])

        def light(kind, polar, around, reach, size, gain, color, twinkle=0.0):
            lights["flower"].append(index)
            lights["kind"].append(kind)
            lights["polar"].append(polar)
            lights["around"].append(around)
            lights["reach"].append(reach)
            lights["size"].append(size)
            lights["gain"].append(gain)
            lights["twinkle"].append(twinkle)
            lights["color"].append(color)

        light(KIND_CORE, 0.0, 0.0, 0.03, spec["core"] * 0.8, 1.3, stamen_color)
        if spec["stamens"]:
            count, length, spread_deg = spec["stamens"]
            for i in range(count):
                polar = math.radians(spread_deg) * math.sqrt((i + 0.5) / count) * rng.uniform(0.85, 1.1)
                light(KIND_ANTHER, polar, i * 2.39996 + rng.normal(0, 0.1), length * rng.uniform(0.82, 1.08),
                      0.020 if species != "lily" else 0.034, 2.4, stamen_color, rng.uniform(0, 2 * math.pi))
        if spec["disc"]:
            count, disc_radius = spec["disc"]
            for i in range(count):
                frac = math.sqrt((i + 0.5) / count)
                color = stamen_color * (1.0 - 0.45 * frac) + srgb_to_linear((255, 120, 20)) * 0.45 * frac
                light(KIND_FLORET, frac, i * 2.39996, disc_radius, 0.013 + 0.006 * frac, 1.5, color,
                      rng.uniform(0, 2 * math.pi))

    def _build_motes(self):
        rng = np.random.RandomState(SEED + 3)
        n = 260
        self.mote_depth_span = 40.0
        self.mote_xy = np.stack([rng.uniform(-10.0, 10.0, n), rng.uniform(-15.0, 15.0, n)], axis=1)
        self.mote_z0 = rng.uniform(0.0, self.mote_depth_span, n)
        self.mote_loops = rng.choice((1, 2), n, p=(0.7, 0.3)).astype(np.float64)
        self.mote_wander = rng.uniform(0.15, 0.6, (n, 2))
        self.mote_wander_cycles = np.round(rng.uniform(2.0, 7.0, (n, 2)))
        self.mote_phase = rng.uniform(0, 2 * math.pi, (n, 3))
        self.mote_twinkle = np.round(rng.uniform(12, 40, n))
        self.mote_radius = rng.uniform(0.008, 0.022, n) * np.where(rng.uniform(size=n) < 0.07, 2.0, 1.0)
        self.mote_gain = rng.uniform(0.15, 1.0, n) ** 1.6
        self.mote_tint = rng.uniform(0.0, 1.0, (n, 1))


# --------------------------------------------------------------------------
# Motion: every pose is a pure function of loop time
# --------------------------------------------------------------------------


def flower_pose(scene, index, u):
    """World rotation (n, 3, 3) and position (n, 3) of flowers ``index`` at age ``u``."""
    f = scene.flowers
    rock = f["rock_amp"][index] * np.sin(2 * math.pi * u / f["rock_period"][index] + f["rock_phase"][index])
    rock_m = axis_angle_matrix(f["rock_axis"][index], rock)
    spin_axis = np.broadcast_to(np.array([0.0, 0.0, 1.0]), (len(index), 3))
    spin_m = axis_angle_matrix(spin_axis, f["spin"][index] * u)
    rotation = rock_m @ f["base"][index] @ spin_m
    sway = np.stack([0.10 * np.sin(2 * math.pi * u / 13.0 + f["sway_phase"][index]),
                     0.08 * np.sin(2 * math.pi * u / 17.0 + 1.7 * f["sway_phase"][index]),
                     np.zeros_like(u)], axis=1)
    position = f["bloom_pos"][index] + DRIFT * (u - f["u_mid"][index])[:, None] + sway
    return rotation, position


def openness(scene, pi, u):
    f, p = scene.flowers, scene.petals
    fi = p["flower"][pi]
    return smooth((u - f["u_open"][fi] - 1.3 * p["ring"][pi]) / f["t_open"][fi])


def attached_pose(scene, pi, u):
    """Rotation, base position and live shape of petals ``pi`` at flower age ``u``."""
    f, p = scene.flowers, scene.petals
    fi = p["flower"][pi]
    rotation, position = flower_pose(scene, fi, u)
    o = openness(scene, pi, u)
    grow = smooth(u / (f["u_open"][fi] + 0.7 * f["t_open"][fi]))
    breathe = 0.035 * np.sin(2 * math.pi * u / 6.5 + f["breathe"][fi] + 0.6 * p["ring"][pi]) * o
    elev_bud = math.radians(86.0) - math.radians(3.0) * p["ring"][pi]
    elev = elev_bud + (p["elev"][pi] - elev_bud) * o + breathe
    psi = p["psi"][pi] + (1.0 - o) * f["twist"][fi] * (1.0 + 0.35 * p["ring"][pi])
    radial = np.stack([np.cos(psi), np.sin(psi), np.zeros_like(psi)], axis=1)
    axis = np.array([0.0, 0.0, 1.0])
    y_l = radial * np.cos(elev)[:, None] + axis * np.sin(elev)[:, None]
    z_l = -radial * np.sin(elev)[:, None] + axis * np.cos(elev)[:, None]
    x_l = np.cross(y_l, z_l)
    roll = p["roll"][pi] * (0.4 + 0.6 * o)
    x_r = x_l * np.cos(roll)[:, None] - z_l * np.sin(roll)[:, None]
    z_r = x_l * np.sin(roll)[:, None] + z_l * np.cos(roll)[:, None]
    local = np.stack([x_r, y_l, z_r], axis=2)
    radius = f["radius"][fi]
    offset = radial * (radius * p["rb"][pi] * (0.35 + 0.65 * o))[:, None] + axis * (radius * p["lift"][pi])[:, None]
    shape = dict(
        length=p["length"][pi] * (0.24 + 0.76 * grow),
        width=p["width"][pi] * (0.55 + 0.45 * o),
        k1=1.05 + (p["k1"][pi] - 1.05) * o,
        k2=0.35 + (p["k2"][pi] - 0.35) * o,
        cup=0.85 + (p["cup"][pi] - 0.85) * o,
    )
    return rotation @ local, position + np.einsum("nij,nj->ni", rotation, offset), shape


def ease_from_rest(a, tau):
    """Distance travelled with zero starting speed, tending to unit speed."""
    return a * a / (a + tau)


def released_pose(scene, pi, u):
    """Rigid pose of released petals: they start exactly where they bloomed,
    with no jump in position, orientation or speed."""
    p = scene.petals
    release = p["release"][pi]
    a = u - release
    rotation0, base0, shape = attached_pose(scene, pi, release)
    half = np.stack([np.zeros_like(a), 0.5 * shape["length"], np.zeros_like(a)], axis=1)
    centre0 = base0 + np.einsum("nij,nj->ni", rotation0, half)
    outward = rotation0[:, :, 1]
    flutter = (p["flutter_amp"][pi] * smooth(a / 3.0)
               * (np.sin(2 * math.pi * p["flutter_hz"][pi] * a + p["flutter_phase"][pi])
                  - np.sin(p["flutter_phase"][pi]) * np.exp(-a)))
    centre = (centre0 + DRIFT * a[:, None]
              + outward * (p["d_out"][pi] * (1.0 - np.exp(-ease_from_rest(a, 1.2) / 2.5)))[:, None]
              + p["breeze"][pi] * ease_from_rest(a, 2.2)[:, None]
              + p["flutter_dir"][pi] * flutter[:, None])
    angle = p["tumble_rate"][pi] * ease_from_rest(a, 1.6) + 0.5 * flutter
    axis_world = np.einsum("nij,nj->ni", rotation0, p["tumble_axis"][pi])
    rotation = axis_angle_matrix(axis_world, angle) @ rotation0
    base = centre - np.einsum("nij,nj->ni", rotation, half)
    s = np.clip(a / p["life"][pi], 0.0, 1.0)
    return rotation, base, shape, s


# --------------------------------------------------------------------------
# GPU programs
# --------------------------------------------------------------------------

PETAL_VS = """
uniform mat4 u_view;
uniform mat4 u_proj;
uniform vec2 u_jitter;
uniform vec2 u_size;
uniform float u_layers;
in vec2 in_uv;
in vec4 i_m0; in vec4 i_m1; in vec4 i_m2;
in vec4 i_shape;    // length, width fraction, p, q
in vec4 i_shape2;   // notch, cup, k1, k2
in vec4 i_shape3;   // ruffle, teeth, veins, seed
in vec4 i_throat;   // rgb, alpha
in vec4 i_body;     // rgb, level
in vec4 i_tip;      // rgb, slab weight
in vec4 i_extra;    // dissolve, depth layer, share of this petal drawn over this copy, kind
in vec4 i_depth;    // view distance and half depth of the layer's depth window, nudge toward the viewer,
                    // share of the samples this copy covers
// The flower's lights travel in the same stream, after its petals. For them:
// i_m*.w = position (a filament's root), i_m0.xyz = a filament's far end,
// i_shape = world size, tail gain, least pixel size; i_body.rgb = light;
// i_extra.z = the light's place among the lights of its flower.
out vec2 v_uv;
out vec3 v_wpos;
out vec3 v_normal;
out vec3 v_light;
flat out vec4 v_shape; flat out vec4 v_shape2; flat out vec4 v_shape3;
flat out vec4 v_throat; flat out vec4 v_body; flat out vec4 v_tip; flat out vec4 v_extra;

float half_width(float u) {
    float p = i_shape.z, q = i_shape.w;
    float norm = pow(p / (p + q), p) * pow(q / (p + q), q);
    return pow(max(u, 1e-5), p) * pow(max(1.0 - u, 0.0), q) / norm;
}

vec3 petal_point(float u, float v) {
    float L = i_shape.x, W = i_shape.y * i_shape.x;
    float k1 = i_shape2.z, k2 = i_shape2.w;
    vec2 acc = vec2(0.0);
    const int STEPS = 10;
    float ds = u / float(STEPS);
    for (int i = 0; i < STEPS; ++i) {
        float s = (float(i) + 0.5) * ds;
        float th = k1 * s + k2 * s * s * s;
        acc += vec2(cos(th), sin(th)) * ds;
    }
    float theta = k1 * u + k2 * u * u * u;
    vec3 n = vec3(0.0, -sin(theta), cos(theta));
    float hw = half_width(u);
    float xn = v * hw;
    float x = xn * W * 0.5;
    float ruffle = i_shape3.x * pow(abs(xn), 1.5) * sin(TAU * (2.6 * u + i_shape3.w)) * smoothstep(0.15, 0.6, u);
    float z = (i_shape2.y * xn * xn + ruffle) * W * 0.5 + 0.10 * (i_shape3.w - 0.5) * x * u;
    return vec3(x, acc.x * L, acc.y * L) + n * z;
}

// Points and filaments of light: camera-facing quads that keep a least pixel
// size and give up brightness instead, so far stamens shimmer without aliasing.
vec4 light_vertex(float kind) {
    vec3 p0 = vec3(i_m0.w, i_m1.w, i_m2.w);
    vec2 corner = vec2(in_uv.x * 2.0 - 1.0, in_uv.y);
    vec3 light = i_body.rgb * i_tip.a;
    vec4 clip;
    bool seen;
    if (kind < 1.5) {
        vec4 vp = u_view * vec4(p0, 1.0);
        float true_px = i_shape.x * u_proj[1][1] * u_size.y * 0.5 / max(-vp.z, 1e-3);
        float radius = max(true_px, i_shape.z);
        v_light = light * true_px * true_px / (radius * radius);
        v_uv = corner * (radius + 1.5) / radius;
        clip = u_proj * vp;
        clip.xy += corner * (radius + 1.5) * 2.0 / u_size * clip.w;
        seen = -vp.z > 0.05;
    } else {
        mat4 vp = u_proj * u_view;
        vec4 a = vp * vec4(p0, 1.0), b = vp * vec4(i_m0.xyz, 1.0);
        clip = mix(a, b, in_uv.x);
        vec2 sa = a.xy / max(a.w, 1e-3) * u_size * 0.5, sb = b.xy / max(b.w, 1e-3) * u_size * 0.5;
        vec2 dir = sb - sa;
        dir /= max(length(dir), 1e-4);
        float true_px = i_shape.x * u_proj[1][1] * u_size.y * 0.5 / max(clip.w, 1e-3);
        float px = max(true_px, i_shape.z);
        float half_px = px * 0.5 + 1.0;
        clip.xy += vec2(-dir.y, dir.x) * corner.y * half_px * 2.0 / u_size * clip.w;
        v_uv = vec2(0.0, corner.y * half_px / (px * 0.5));
        v_light = light * min(true_px / px, 1.0) * mix(1.0, i_shape.y, in_uv.x);
        seen = a.w > 0.05 && b.w > 0.05;
    }
    // Lights glow over their own flower: each sits at the front of the flower's
    // depth layer, a hair nearer than the one before, so none hides another.
    clip.z = (-1.0 + (256.0 - i_extra.z) * 6e-7 * u_layers) * clip.w;
    clip = depth_layer(clip, i_extra.y, u_layers);
    return seen ? clip : vec4(2.0, 2.0, 2.0, 1.0);
}

void main() {
    v_shape = i_shape; v_shape2 = i_shape2; v_shape3 = i_shape3;
    v_throat = i_throat; v_body = i_body; v_tip = i_tip;
    v_extra = vec4(i_extra.x, i_depth.w, i_extra.zw);      // the fragment stage needs the cover, not the layer
    vec4 clip;
    if (i_extra.w > 0.5) {
        v_wpos = vec3(0.0);
        v_normal = vec3(0.0, 0.0, 1.0);
        clip = light_vertex(i_extra.w);
    } else {
        // Cluster samples toward the base and tip, where the outline turns fastest.
        float u = in_uv.x - 0.6 * sin(TAU * in_uv.x) / TAU;
        float v = in_uv.y;
        vec3 p = petal_point(u, v);
        vec3 du = petal_point(min(u + 0.01, 1.0), v) - petal_point(max(u - 0.01, 0.0), v);
        vec3 dv = petal_point(u, min(v + 0.03, 1.0)) - petal_point(u, max(v - 0.03, -1.0));
        vec3 n = cross(dv, du);
        n = length(n) > 1e-9 ? normalize(n) : vec3(0.0, 0.0, 1.0);
        vec3 wp = vec3(dot(i_m0.xyz, p), dot(i_m1.xyz, p), dot(i_m2.xyz, p)) + vec3(i_m0.w, i_m1.w, i_m2.w);
        v_normal = vec3(dot(i_m0.xyz, n), dot(i_m1.xyz, n), dot(i_m2.xyz, n));
        v_wpos = wp;
        v_uv = vec2(u, v);
        v_light = vec3(0.0);
        // Each flower owns a depth layer: depth sorts the petals within a flower,
        // while whole flowers keep one fixed order and can never swap places.
        // Inside a layer, depth runs linearly through a window around its owner.
        vec4 vpos = u_view * vec4(wp, 1.0);
        clip = u_proj * vpos;
        clip.z = clamp((-vpos.z - i_depth.z - i_depth.x) / i_depth.y, -1.0, 1.0) * clip.w;
        clip = depth_layer(clip, i_extra.y, u_layers);
    }
    clip.xy += u_jitter * clip.w;
    gl_Position = clip;
}
"""

PETAL_FS = """
uniform vec3 u_eye;
uniform vec3 u_key;
in vec2 v_uv;
in vec3 v_wpos;
in vec3 v_normal;
in vec3 v_light;
flat in vec4 v_shape; flat in vec4 v_shape2; flat in vec4 v_shape3;
flat in vec4 v_throat; flat in vec4 v_body; flat in vec4 v_tip;
flat in vec4 v_extra;   // dissolve, share of the samples covered, share of this petal drawn over this copy, kind
uniform float u_sub;
out vec4 f_color;

float half_width(float u) {
    float p = v_shape.z, q = v_shape.w;
    float norm = pow(p / (p + q), p) * pow(q / (p + q), q);
    return pow(max(u, 1e-5), p) * pow(max(1.0 - u, 0.0), q) / norm;
}

// A petal that is handing itself over to a layer of its own leaves its flower's
// layer sample by sample. What it hid comes back exactly as gradually as the
// petal goes, where a fading copy that still wrote depth would hide it to the
// last and then let go of it in a single frame.
int samples_covered(float share) {
    int count = max(gl_NumSamples, 1);
    if (share >= 1.0) return (1 << count) - 1;
    vec2 cell = floor(gl_FragCoord.xy) + 5.588238 * u_sub;
    float dither = fract(52.9829189 * fract(dot(cell, vec2(0.06711056, 0.00583715))));
    int turn = int(hash13(vec3(cell, 3.0)) * float(count));
    int mask = 0;
    for (int s = 0; s < count; ++s)
        if (share * float(count) > float(s) + dither) mask |= 1 << ((s + turn) % count);
    return mask;
}

void main() {
    gl_SampleMask[0] = samples_covered(v_extra.w > 0.5 ? 1.0 : v_extra.y);
    if (v_extra.w > 0.5) {
        // A point of light is a soft gaussian; a filament is a soft line.
        float r2 = v_extra.w < 1.5 ? dot(v_uv, v_uv) : 0.0;
        if (r2 > 1.0) discard;
        float shape = v_extra.w < 1.5 ? exp(-r2 * 4.0) * 4.07 : exp(-v_uv.y * v_uv.y * 2.2);
        f_color = vec4(v_light * shape, 0.0);
        return;
    }
    // Edge-on petals are thinner than a pixel; keep extrapolated samples inside the surface.
    float u = clamp(v_uv.x, 0.0, 1.0), v = clamp(v_uv.y, -1.0, 1.0);
    float seed = v_shape3.w;
    float xn = v * half_width(u);

    // Notched or toothed tips are cut from the outline in petal space; the lobes
    // of a toothed tip are uneven, as they are on a real cosmos.
    float lobes = 0.62 * (0.5 - 0.5 * cos(TAU * xn * 2.3 + 1.3 * seed))
                + 0.38 * (0.5 - 0.5 * cos(TAU * xn * 3.9 + 4.1 * seed + 1.0));
    float cut = 1.0 - v_shape2.x * exp(-(xn * xn) / 0.05) - v_shape3.y * 0.085 * lobes;
    float aa = max(fwidth(cut - u), 1e-4);
    float cover = smoothstep(-aa, aa, cut - u);

    // At the end of its flight a petal does not dim: it dissolves into light,
    // eroding from the tip and edges inward behind a bright front.
    float ember = 0.0;
    if (v_extra.x > 0.0) {
        float field = 0.58 * fbm(vec2(xn * 7.0 + seed * 13.0, u * 5.5 - seed * 9.0))
                    + 0.42 * (1.0 - max(u, v * v));
        float front = field - v_extra.x * 1.06;
        float fa = max(fwidth(front), 1e-4);
        cover *= smoothstep(-fa, fa, front);
        ember = 1.0 - smoothstep(0.0, 0.07, front);
    }
    if (cover <= 0.002) discard;

    // Painterly streaks run along the petal and pull the colour ramp with them.
    float streak = fbm(vec2(xn * 4.5 + seed * 31.0, u * 2.0 + seed * 7.0));
    float fine = fbm(vec2(xn * 26.0 + seed * 11.0, u * 4.0 + seed * 3.0));
    float uu = clamp(u + (streak - 0.5) * 0.40, 0.0, 1.0);
    vec3 col = uu < 0.34 ? mix(v_throat.rgb, v_body.rgb, smooth01(uu / 0.34))
                         : mix(v_body.rgb, v_tip.rgb, smooth01((uu - 0.34) / 0.66));

    // Veins fan from the base and converge at the tip: strong primaries with
    // fainter secondaries between them, broken up along their length.
    float count = v_shape3.z;
    float vv = v + 0.030 * sin(u * 7.0 + seed * 40.0) + 0.02 * (fine - 0.5);
    float lines = vv * count;
    float fw = fwidth(lines);
    float cell = fract(lines * 0.5 + 0.25);
    float primary = 1.0 - smoothstep(0.035, 0.035 + max(fw * 0.75, 0.03), abs(cell - 0.5));
    float secondary = 1.0 - smoothstep(0.02, 0.02 + max(fw * 0.75, 0.025), min(cell, 1.0 - cell));
    float breakup = 0.35 + 1.1 * fbm(vec2(lines * 0.7 + seed * 5.0, u * 3.0));
    float vein = (primary + 0.45 * secondary) * breakup;
    vein *= clamp(1.0 - fw * 1.6, 0.0, 1.0) * smoothstep(0.03, 0.25, u) * (1.0 - smoothstep(0.55, 0.96, u));
    float midrib = exp(-xn * xn / 0.0016) * (1.0 - smoothstep(0.45, 0.9, u));

    vec3 V = normalize(u_eye - v_wpos);
    vec3 N = normalize(v_normal);
    float facing = dot(N, V);
    vec3 Nf = facing < 0.0 ? -N : N;
    float fres = pow(clamp(1.0 - abs(facing), 0.0, 1.0), 2.4);

    // A soft key light models the form; light from behind shines through.
    float wrap = smooth01(dot(Nf, u_key) * 0.5 + 0.5);
    float through = pow(clamp(0.5 - 0.5 * dot(Nf, u_key), 0.0, 1.0), 2.0);
    float depth = mix(0.46, 1.0, smooth01(u * 1.25));          // deep throat, bright tip
    float edge = smoothstep(0.58, 1.0, abs(v));
    edge = edge * edge + smoothstep(0.86, 1.0, u / max(cut, 0.5)) * 0.6;
    float body = depth * (0.74 + 0.52 * (streak - 0.5)) * (0.90 + 0.22 * fine) * mix(0.60, 1.10, wrap);
    float light = body + 0.16 * vein + 0.12 * midrib + 0.30 * edge + 0.55 * fres + 0.22 * through * depth
                + 1.6 * ember;
    if (facing < 0.0) {
        light *= 0.82;
        col = mix(col, v_body.rgb, 0.3);
    }
    // Edges and veins carry paler light than the petal body.
    vec3 pale = mix(col, vec3(dot(col, vec3(0.3, 0.55, 0.15))) * 0.6 + 0.7 * col + 0.08, 0.5);
    col = mix(col, pale, clamp(0.55 * edge + 0.5 * vein + 0.6 * fres + ember, 0.0, 1.0));

    float alpha = v_throat.a * clamp(0.84 + 0.13 * fres + 0.10 * edge, 0.0, 0.985);
    // A petal between two blur slabs is drawn in both. The nearer copy covers
    // the farther one, so the farther copy is drawn a little stronger: the petal
    // keeps its light and its opacity on the way through.
    float weight = v_tip.a * cover / (1.0 - 0.75 * v_extra.z * alpha);
    f_color = vec4(col * light * v_body.a * weight, alpha * weight);
}
"""

INSTANCE_FLOATS = 44
INSTANCE_FORMAT = "4f 4f 4f 4f 4f 4f 4f 4f 4f 4f 4f/i"
INSTANCE_NAMES = ("i_m0", "i_m1", "i_m2", "i_shape", "i_shape2", "i_shape3", "i_throat", "i_body", "i_tip",
                  "i_extra", "i_depth")
KIND_POINT, KIND_FILAMENT = 1.0, 2.0      # what a stream row is, after 0.0 for a petal
PETAL_GRID = (26, 10)


class FrameRenderer:
    def __init__(self):
        import moderngl
        import artwork_gl as agl
        self.agl, self.moderngl = agl, moderngl
        self.scene = Scene()
        self.stage = agl.Stage(WIDTH, HEIGHT, margin=MARGIN)
        self.dof = agl.DepthOfField(FOCUS, DOF_SCALE, DOF_FAR_SIGMAS, DOF_NEAR_SIGMAS)
        ctx = self.stage.ctx
        self.prog = self.stage.program(PETAL_VS, PETAL_FS)
        verts, idx = agl.grid_mesh(*PETAL_GRID)
        self.capacity = 2 * (self.scene.n_petals + self.scene.n_lights)
        self.instances = ctx.buffer(reserve=self.capacity * INSTANCE_FLOATS * 4, dynamic=True)
        self.vao = ctx.vertex_array(self.prog, [
            (ctx.buffer(verts.tobytes()), "2f", "in_uv"),
            (self.instances, INSTANCE_FORMAT, *INSTANCE_NAMES),
        ], index_buffer=ctx.buffer(idx.tobytes()), skip_errors=True)
        if not agl.settle(self.render):
            self.stage.release()
            raise RuntimeError("The luminous-flowers renderer did not settle into deterministic frames")

    # -- per-frame state -----------------------------------------------------

    def petal_instances(self, t, view):
        """Instance rows of every visible petal at loop time ``t``, with their
        view distances, flowers, petal indices and how far each has lifted out
        of its flower's depth layer (0 attached .. 1 free)."""
        scene = self.scene
        f, p = scene.flowers, scene.petals
        age = (t - f["birth"]) % DURATION_S
        u_all = age[p["flower"]]
        attached = u_all < p["release"]
        released = (~attached) & (u_all < p["release"] + p["life"])
        rows = []
        for mask, is_released in ((attached, False), (released, True)):
            pi = np.nonzero(mask)[0]
            if not len(pi):
                continue
            u = u_all[pi]
            fi = p["flower"][pi]
            if is_released:
                rotation, base, shape, s = released_pose(scene, pi, u)
                fade = np.ones_like(u)
                dissolve = smooth((s - DISSOLVE_FROM) / (1.0 - DISSOLVE_FROM))
                # A released petal brightens for a moment, rising from rest so nothing pops.
                since = (u - p["release"][pi]) / 0.6
                glow = 1.0 + 0.35 * since * np.exp(1.0 - since)
                lift = smooth((u - p["release"][pi]) / LIFT_S)
            else:
                rotation, base, shape = attached_pose(scene, pi, u)
                fade = smooth(u / BIRTH_FADE_S)
                dissolve = np.zeros_like(u)
                glow = np.ones_like(u)
                lift = np.zeros_like(u)
            length = shape["length"]
            data = np.zeros((len(pi), INSTANCE_FLOATS), dtype=np.float32)
            data[:, 0:3], data[:, 3] = rotation[:, 0, :], base[:, 0]
            data[:, 4:7], data[:, 7] = rotation[:, 1, :], base[:, 1]
            data[:, 8:11], data[:, 11] = rotation[:, 2, :], base[:, 2]
            data[:, 12], data[:, 13], data[:, 14], data[:, 15] = length, shape["width"], p["p"][pi], p["q"][pi]
            data[:, 16], data[:, 17], data[:, 18], data[:, 19] = p["notch"][pi], shape["cup"], shape["k1"], shape["k2"]
            data[:, 20], data[:, 21], data[:, 22], data[:, 23] = p["ruffle"][pi], p["teeth"][pi], p["veins"][pi], p["seed"][pi]
            centre = base + rotation[:, :, 1] * (0.5 * length)[:, None]
            view_pos = centre @ view[:3, :3].T + view[:3, 3]
            dist = np.maximum(-view_pos[:, 2], 1e-3)
            level = f["level"][fi] * glow * depth_level(dist)
            fade = fade * near_fade(dist)
            data[:, 24:27], data[:, 27] = depth_tint(p["throat"][pi], dist), 0.86 * fade
            data[:, 28:31], data[:, 31] = depth_tint(p["body"][pi], dist), level * fade
            data[:, 32:35] = depth_tint(p["tip"][pi], dist)
            data[:, 36], data[:, 42] = dissolve, p["bias"][pi]
            # A petal that has faded to nothing must not hide anything either.
            seen = fade > 1e-4
            rows.append((data[seen], dist[seen], fi[seen], pi[seen], lift[seen]))
        if not rows:
            empty = np.empty(0, dtype=np.int64)
            return np.empty((0, INSTANCE_FLOATS), dtype=np.float32), np.empty(0), empty, empty, np.empty(0)
        return tuple(np.concatenate([r[k] for r in rows]) for k in range(5))

    def stream(self, t, view):
        """Everything that is drawn at loop time ``t``, as one ordered stream.

        Returns the instance ``rows`` in drawing order and, row by row: ``home``
        and ``dist``, the view distances of the row's flower and of the row
        itself; ``lift``, how far its petal has left the flower's layer (0
        attached .. 1 free); ``free``, whether the row is the free copy of its
        petal; ``flower`` and ``place``, which name the row (petals count from 0,
        lights follow them); and ``layers``, the number of depth layers.

        The order never changes while two things are on stage together, which
        is what keeps the film free of pops:

        * Flowers are layered far to near. Every flower drifts toward the viewer
          at the same speed, so two living flowers never change places.
        * A flower's layer holds its attached petals in the order they were made
          (outer rings first), sorted among themselves by true depth, then its
          lights, which glow over them.
        * A released petal lifts out into a layer of its own in front of its
          flower, cross-fading over ``LIFT_S`` seconds so the hand-over is never
          seen: the copy in the flower gives up its samples one by one while the
          free copy gathers light. Free petals are never sorted against anything:
          they are simply drawn in the order they were made.
        * Depth of field does the rest: nearer blur slabs are laid over farther
          ones, so a petal flying at the lens still passes in front of the
          flowers it overtakes, as a soft cross-fade instead of a swap.
        """
        scene = self.scene
        f = scene.flowers
        petals, petal_dist, petal_flower, petal_index, petal_lift = self.petal_instances(t, view)
        lights, light_dist, light_flower, light_order = self.flower_lights(t, view)
        within = petal_lift < 1.0 - 1e-4    # rows in the flower's own layer
        free = petal_lift > 1e-4            # rows in a layer of their own
        rows = np.concatenate([petals[within], lights, petals[free]])
        dist = np.concatenate([petal_dist[within], light_dist, petal_dist[free]])
        flower = np.concatenate([petal_flower[within], light_flower, petal_flower[free]])
        place = np.concatenate([petal_index[within], light_order, petal_index[free]])
        lift = np.concatenate([petal_lift[within], np.zeros(len(lights)), petal_lift[free]])
        own_layer = np.concatenate([np.zeros(int(within.sum()) + len(lights), dtype=bool),
                                    np.ones(int(free.sum()), dtype=bool)])
        if not len(rows):
            return dict(rows=rows, home=dist, dist=dist, lift=lift, free=own_layer, flower=flower, place=place,
                        layers=1)

        age = (t - f["birth"]) % DURATION_S
        flower_depth = f["bloom_pos"][:, 2] + DRIFT[2] * (age - f["u_mid"])
        order = np.lexsort((place, own_layer, flower, flower_depth[flower]))
        rows, dist, flower, place, lift, own_layer = (v[order] for v in (rows, dist, flower, place, lift, own_layer))
        starts = np.ones(len(rows), dtype=bool)
        starts[1:] = (flower[1:] != flower[:-1]) | own_layer[1:]
        layer = np.cumsum(starts) - 1
        rows[:, 37] = layer
        # Depth inside a layer is measured in a window around its owner, so the
        # depth buffer stays precise however many layers share it.
        _, position = flower_pose(scene, np.arange(scene.n_flowers), age)
        home = np.maximum(-(position @ view[2, :3] + view[2, 3]), 1e-3)[flower]
        rows[:, 40] = np.where(own_layer, dist, home)
        rows[:, 41] = np.where(own_layer, 1.6, 2.6)
        rows[:, 43] = np.where(own_layer, 1.0, 1.0 - lift)
        return dict(rows=rows, home=home, dist=dist, lift=lift, free=own_layer, flower=flower, place=place,
                    layers=int(layer[-1]) + 1)

    def flower_lights(self, t, view):
        """Stream rows for the points of light and stamen filaments of every
        living flower, with their view distances, flowers and drawing order."""
        scene = self.scene
        f, l = scene.flowers, scene.lights
        age = (t - f["birth"]) % DURATION_S
        fi_all = l["flower"]
        u_all = age[fi_all]
        end = f["scatter_start"][fi_all] + f["scatter_len"][fi_all]
        alive = u_all < end + AFTERGLOW_S
        li = np.nonzero(alive)[0]
        if not len(li):
            empty = np.empty(0, dtype=np.int64)
            return np.empty((0, INSTANCE_FLOATS), dtype=np.float32), np.empty(0), empty, empty
        fi, u = fi_all[li], u_all[li]
        rotation, position = flower_pose(scene, fi, u)
        radius = f["radius"][fi]
        o = smooth((u - f["u_open"][fi]) / f["t_open"][fi])
        fade_out = 1.0 - smooth((u - end[li]) / AFTERGLOW_S)
        fade_in = smooth(u / BIRTH_FADE_S)
        kind = l["kind"][li]
        spread = 0.22 + 0.78 * o
        polar = np.where(kind == KIND_ANTHER, l["polar"][li] * spread, 0.0)
        around = l["around"][li]
        local = np.stack([np.sin(polar) * np.cos(around), np.sin(polar) * np.sin(around), np.cos(polar)], axis=1)
        reach = l["reach"][li] * radius * np.where(kind == KIND_ANTHER, 0.35 + 0.65 * o, 1.0)
        # Disc florets sit on a shallow dome in golden-angle order.
        floret = kind == KIND_FLORET
        disc_r = l["polar"][li] * l["reach"][li] * radius
        local = np.where(floret[:, None],
                         np.stack([disc_r * np.cos(around), disc_r * np.sin(around),
                                   0.07 * radius * (1.0 - l["polar"][li] ** 2) + 0.02 * radius], axis=1),
                         local * reach[:, None])
        world = position + np.einsum("nij,nj->ni", rotation, local)
        view_pos = world @ view[:3, :3].T + view[:3, 3]
        dist = np.maximum(-view_pos[:, 2], 1e-3)
        twinkle = 1.0 + 0.25 * np.sin(2 * math.pi * u / 2.3 + l["twinkle"][li])
        birth_flare = 1.0 + 2.2 * np.exp(-((u - 1.0) / 0.8) ** 2)
        visible = np.where(kind == KIND_CORE, (0.35 + 0.65 * o) * birth_flare,
                           np.where(kind == KIND_INNER, o, smooth(o * 1.5 - 0.25))) * twinkle
        gain = l["gain"][li] * visible * fade_in * fade_out * f["level"][fi] * depth_level(dist)
        anther = kind == KIND_ANTHER
        n, m = len(li), int(anther.sum())
        rows = np.zeros((n + m, INSTANCE_FLOATS), dtype=np.float32)
        points, filaments = rows[:n], rows[n:]
        points[:, 3], points[:, 7], points[:, 11] = world[:, 0], world[:, 1], world[:, 2]
        points[:, 12], points[:, 13], points[:, 14] = l["size"][li] * radius, 1.0, 0.9
        points[:, 28:31] = l["color"][li] * gain[:, None]
        points[:, 38], points[:, 39] = l["slot"][li], KIND_POINT
        root = position[anther] + rotation[anther][:, :, 2] * (0.02 * radius[anther])[:, None]
        filaments[:, 3], filaments[:, 7], filaments[:, 11] = root[:, 0], root[:, 1], root[:, 2]
        filaments[:, 0:3] = world[anther]
        filaments[:, 12], filaments[:, 13], filaments[:, 14] = 0.007 * radius[anther], 1.6, 1.1
        filaments[:, 28:31] = l["color"][li][anther] * (gain[anther] * 0.30)[:, None]
        filaments[:, 38], filaments[:, 39] = (l["count"][li] + l["slot"][li])[anther], KIND_FILAMENT
        shown = np.concatenate([gain, gain[anther]]) > 0.0
        order = scene.n_petals + np.concatenate([li, scene.n_lights + li[anther]])
        return (rows[shown], np.concatenate([dist, dist[anther]])[shown],
                np.concatenate([fi, fi[anther]])[shown], order[shown])

    def motes(self, t):
        scene = self.scene
        span = scene.mote_depth_span
        phase = 2 * math.pi * (t % DURATION_S) / DURATION_S
        travel = (scene.mote_z0 + scene.mote_loops * span * (t % DURATION_S) / DURATION_S) % span
        z = -2.0 - (span - travel)
        wander = scene.mote_wander * np.sin(scene.mote_wander_cycles * phase + scene.mote_phase[:, :2])
        xy = scene.mote_xy + wander
        edge = np.minimum(travel, span - travel) / 4.0
        twinkle = 0.6 + 0.4 * np.sin(scene.mote_twinkle * phase + scene.mote_phase[:, 2])
        weights = season_weights(t)
        tint = sum(weights[s] * srgb_to_linear(MOTE_COLORS[s]) for s in range(4))
        gold = srgb_to_linear((255, 206, 120))
        color = tint[None, :] * (1.0 - 0.5 * scene.mote_tint) + gold[None, :] * 0.5 * scene.mote_tint
        data = np.empty((len(z), 8))
        data[:, 0:2], data[:, 2], data[:, 3] = xy, z, scene.mote_radius
        data[:, 4:7] = color
        data[:, 7] = scene.mote_gain * twinkle * np.clip(edge, 0.0, 1.0) * 1.1
        return data

    # -- drawing -------------------------------------------------------------

    def render(self, frame_idx):
        """The finished frame (BGR) and the mean luma left in its widget zone."""
        with self.stage.ctx:        # a process may hold several renderers, each with a context of its own
            return self._render(frame_idx)

    def slab_light(self, stream):
        """Per row and blur slab: the light the row carries there, and how much
        of the same petal is laid over it."""
        home, dist, lift, free = (stream[k] for k in ("home", "dist", "lift", "free"))
        # Everything in a flower's layer takes the flower's place in the depth of
        # field, so petals that hide one another are always in the same slabs. A
        # free petal has a place of its own, and gathers its light as it lifts.
        home_weights, own_weights = self.dof.slab_weights(home), self.dof.slab_weights(dist)
        weights = np.where(free[:, None], own_weights * lift[:, None], home_weights)
        # Laid over each copy of a petal: its copies in the nearer slabs, and the
        # free copy over the one still in the flower.
        covered = (1.0 - lift)[:, None] * nearer_sum(home_weights) + lift[:, None] * (
            nearer_sum(own_weights) + np.where(free[:, None], 0.0, own_weights))
        return weights, covered

    def moment(self, t):
        """What one instant of the open shutter draws: the camera, the number of
        depth layers and, slab by slab, the instance rows (or None)."""
        eye, view, proj = camera_matrices(t)
        stream = self.stream(t, view)
        rows = stream["rows"]
        weights, covered = self.slab_light(stream)
        is_petal = rows[:, 39] < 0.5
        slabs = []
        for k in range(len(self.dof.sigmas)):
            chosen = np.nonzero(weights[:, k] > 1e-4)[0]
            slab = rows[chosen].copy()
            slab[:, 35] = weights[chosen, k]
            slab[:, 38] = np.where(is_petal[chosen], covered[chosen, k], slab[:, 38])
            slabs.append(slab if len(chosen) else None)
        return dict(eye=eye, view=view, proj=self.stage.expand(proj), layers=stream["layers"], slabs=slabs)

    def _render(self, frame_idx):
        agl, mgl = self.agl, self.moderngl
        stage, ctx = self.stage, self.stage.ctx
        t = (frame_idx % N_FRAMES) / FPS
        # The shutter stays open for a part of every frame. Each sub-frame draws
        # the garden a moment later, so whatever moves fast is smeared by its own
        # motion instead of strobing from frame to frame.
        moments = [self.moment((t + ((sub + 0.5) / SUBFRAMES - 0.5) * SHUTTER / FPS) % DURATION_S)
                   for sub in range(SUBFRAMES)]
        self.prog["u_key"].value = KEY_LIGHT
        self.prog["u_size"].value = (stage.width, stage.height)

        stage.begin()
        for k, sigma in enumerate(self.dof.sigmas):
            if all(m["slabs"][k] is None for m in moments):
                continue

            def draw(sub, jitter, k=k):
                now = moments[sub]
                slab = now["slabs"][k]
                if slab is None:
                    return
                self.instances.write(slab.tobytes())
                self.prog["u_view"].write(agl.mat_bytes(now["view"]))
                self.prog["u_proj"].write(agl.mat_bytes(now["proj"]))
                self.prog["u_eye"].value = tuple(float(v) for v in now["eye"])
                self.prog["u_layers"].value = float(now["layers"])
                self.prog["u_sub"].value = float(sub)
                self.prog["u_jitter"].value = (jitter[0] * 2.0 / stage.width, jitter[1] * 2.0 / stage.height)
                ctx.disable(mgl.CULL_FACE)
                ctx.enable(mgl.BLEND | mgl.DEPTH_TEST)
                ctx.depth_func = "<="
                ctx.blend_func = mgl.ONE, mgl.ONE_MINUS_SRC_ALPHA
                self.vao.render(mgl.TRIANGLES, instances=len(slab))

            stage.render_slab(sigma, draw, subframes=SUBFRAMES)

        _, view, proj = camera_matrices(t)
        stage.draw_sprites("motes", self.motes(t), view, stage.expand(proj), self.dof,
                           coc_max=46.0, min_px=0.8, near_fade=2.5)
        frame = stage.finish(
            frame_idx % N_FRAMES, exposure=EXPOSURE, bloom_threshold=0.62, bloom_knee=0.30, bloom_gain=0.42,
            grain=GRAIN, quiet_rect=artwork.WIDGET_RECT, quiet_feather=QUIET_FEATHER_PX, quiet_floor=QUIET_FLOOR)
        return artwork.enforce_widget_zone(frame)


def nearer_sum(weights):
    """Per slab, the summed weight of all nearer slabs."""
    return np.cumsum(weights[:, ::-1], axis=1)[:, ::-1] - weights


def depth_level(dist):
    """Distant flowers dim into the dark."""
    return 1.0 - 0.55 * smooth((dist - FOCUS - 2.0) / 26.0)


def near_fade(dist):
    """Anything about to touch the lens dissolves instead of filling the frame."""
    return smooth((dist - 3.0) / 4.5)


def depth_tint(color, dist):
    """A cool aerial tint for distant flowers."""
    amount = (0.30 * smooth((dist - FOCUS - 3.0) / 24.0))[:, None]
    cool = color * np.array([0.72, 0.86, 1.18])
    return color * (1.0 - amount) + cool * amount


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
    maxrate_kbps=MAXRATE_KBPS,
    bufsize_kbps=BUFSIZE_KBPS,
    default_workers=5,          # each worker spends as long laying out its moments as the GPU spends drawing them
)


def main(argv=None):
    return artwork.run_cli(SPEC, argv)


if __name__ == "__main__":
    main()
