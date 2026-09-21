#!/usr/bin/env python3
"""Shared offline pipeline for the optional Mirror background artwork films.

Renderers supply deterministic per-frame drawing. This module supplies what
every film needs: a bounded, ordered multi-process frame pipeline, hardware
friendly H.264 encoding, output validation, loop-seam and continuity checks, the
widget legibility guard, contact sheets and mirror metrics. It is not part of
the Android build or a runtime dependency.
"""

from __future__ import annotations

import argparse
from collections import deque
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import subprocess
import time
from typing import Callable, Iterable, Iterator

import cv2
import numpy as np

from background_video import MAX_VIDEO_BYTES, sha256_file

WIDTH = 1080
HEIGHT = 1920
# Half the panel's 60 Hz: every frame is held for exactly two refreshes, so slow
# drifts stay even. 24 FPS would alternate between two and three (pulldown
# judder), which shows most on exactly this kind of calm, continuous motion. It
# is also the most that H.264 level 4.1 carries at this size.
FPS = 30

REPO_ROOT = Path(__file__).resolve().parents[1]
VIDEO_DIR = REPO_ROOT / "generated" / "background-videos"
PREVIEW_DIR = VIDEO_DIR / "previews"

MAX_UPLOAD_BITRATE = 20_000_000
LEVEL_41_FRAME_MACROBLOCKS = 8192
LEVEL_41_MACROBLOCKS_PER_SECOND = 245_760

# Capped CRF keeps quality constant, so mostly-black films stay small, while
# the VBV ceiling bounds peaks for the hardware decoder and keeps the worst
# case (maxrate * duration + buffer) below the 256 MiB upload limit.
CRF = 16
MAXRATE_KBPS = 16_000
BUFSIZE_KBPS = 20_000

# Widget-legibility rectangle (x 2-40%, y 3-24%): mean luminance stays below
# WIDGET_LUMA_MAX and fine detail is smoothed away, whatever the art does.
WIDGET_RECT = (int(0.02 * WIDTH), int(0.03 * HEIGHT), int(0.40 * WIDTH), int(0.24 * HEIGHT))
WIDGET_LUMA_MAX = 15.0

LUMA_BGR = np.array([0.114, 0.587, 0.299], dtype=np.float32)
NEAR_BLACK_LUMA = 12.0
FOG_LUMA = 40.0
BRIGHT_LUMA = 60.0


def luma(bgr: np.ndarray) -> np.ndarray:
    return bgr.astype(np.float32, copy=False) @ LUMA_BGR


def enforce_widget_zone(bgr, rect=WIDGET_RECT, luma_max=WIDGET_LUMA_MAX, sigma=9.0):
    """Hard numeric widget guarantee. Returns (frame, region_mean_luma)."""
    x0, y0, x1, y1 = rect
    region = cv2.GaussianBlur(bgr[y0:y1, x0:x1].astype(np.float32), (0, 0), sigmaX=sigma)
    mean_luma = float(luma(region).mean())
    if mean_luma > luma_max:
        region *= luma_max / mean_luma
        mean_luma = float(luma(region).mean())
    bgr[y0:y1, x0:x1] = np.clip(region, 0, 255).astype(np.uint8)
    return bgr, mean_luma


def frame_metrics(bgr) -> dict:
    """How much of the glass stays a mirror (near black), how much is a dim
    veil (fog), and how much actually glows (bright), on full-range luma."""
    y = luma(bgr)
    return {
        "near_black": float((y < NEAR_BLACK_LUMA).mean()),
        "fog": float(((y >= NEAR_BLACK_LUMA) & (y < FOG_LUMA)).mean()),
        "bright": float((y > BRIGHT_LUMA).mean()),
        "mean_luma": float(y.mean()),
    }


def build_ffmpeg_cmd(output_path, *, width=WIDTH, height=HEIGHT, fps=FPS, crf=CRF,
                     maxrate_kbps=MAXRATE_KBPS, bufsize_kbps=BUFSIZE_KBPS):
    return [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostats", "-n",
        "-f", "rawvideo",
        "-pixel_format", "bgr24",
        "-video_size", f"{width}x{height}",
        "-framerate", str(fps),
        "-i", "-",
        "-an",
        "-c:v", "libx264",
        "-profile:v", "high",
        "-level:v", "4.1",
        "-pix_fmt", "yuv420p",
        "-preset", "slow",
        "-vf", "scale=in_range=full:out_range=tv:out_color_matrix=bt709",
        "-color_range", "tv",
        "-colorspace", "bt709",
        "-color_primaries", "bt709",
        "-color_trc", "bt709",
        "-x264-params", "aq-mode=3:aq-strength=0.85",
        "-crf", str(crf),
        "-maxrate", f"{maxrate_kbps}k",
        "-bufsize", f"{bufsize_kbps}k",
        "-g", str(fps * 2),
        "-keyint_min", str(fps),
        "-bf", "2",
        "-refs", "3",
        "-sc_threshold", "0",
        "-movflags", "+faststart",
        str(output_path),
    ]


def worst_case_upload_bytes(duration_s, maxrate_kbps=MAXRATE_KBPS, bufsize_kbps=BUFSIZE_KBPS):
    return (maxrate_kbps * duration_s + bufsize_kbps) * 1000 / 8


def validate_output(path, *, n_frames, width=WIDTH, height=HEIGHT, fps=FPS,
                    render_elapsed=0.0, total_elapsed=0.0):
    cmd = [
        "ffprobe", "-hide_banner", "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    info = json.loads(result.stdout)
    if len(info["streams"]) != 1 or info["streams"][0]["codec_type"] != "video":
        raise ValueError("Expected exactly one video stream and no other tracks")
    stream = info["streams"][0]
    fmt = info["format"]

    duration = float(fmt.get("duration", stream.get("duration", 0)))
    size_bytes = int(fmt.get("size", os.path.getsize(path)))
    fps_num, fps_den = stream.get("avg_frame_rate", "0/1").split("/")
    fps_val = float(fps_num) / float(fps_den) if float(fps_den) else 0.0

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
        "has_audio": False,
        "bitrate": max(int(stream.get("bit_rate", 0)), size_bytes * 8 / max(duration, 1e-9)),
        "frames": int(stream.get("nb_frames", 0)),
        "color_space": stream.get("color_space"),
        "color_range": stream.get("color_range"),
        "render_time_s": round(render_elapsed, 2),
        "total_time_s": round(total_elapsed, 2),
    }

    print("\n===== VALIDATION REPORT =====")
    for key, value in report.items():
        print(f"{key:>14}: {value}")
    print("==============================")

    frame_macroblocks = math.ceil(width / 16) * math.ceil(height / 16)
    checks = {
        "dimensions": report["width"] == width and report["height"] == height,
        "frame rate": abs(fps_val - fps) < 0.001,
        "pixel format": report["pix_fmt"] == "yuv420p",
        "codec/profile/level": (report["codec_name"], report["profile"], report["level"]) == ("h264", "High", 41),
        "frame count": report["frames"] == n_frames,
        "duration": duration > 0 and abs(duration - n_frames / fps) < 0.01,
        "file size": 0 < size_bytes <= MAX_VIDEO_BYTES,
        "bitrate": 0 < report["bitrate"] <= MAX_UPLOAD_BITRATE,
        "Level 4.1 macroblock envelope": frame_macroblocks <= LEVEL_41_FRAME_MACROBLOCKS
        and frame_macroblocks * fps <= LEVEL_41_MACROBLOCKS_PER_SECOND,
        "color signaling": report["color_space"] == "bt709" and report["color_range"] == "tv",
    }
    for name, valid in checks.items():
        if not valid:
            raise ValueError(f"Video validation failed: {name}")
    report["sha256"] = sha256_file(Path(path))
    print(f"sha256: {report['sha256']}")
    return report


def report_seam_metrics(path, n_frames):
    """Confirms the loop seam is no more jarring than an ordinary frame step."""
    cap = cv2.VideoCapture(str(path))
    wanted = {0, 1, n_frames - 2, n_frames - 1}
    frames = {}
    index = 0
    while index <= max(wanted):
        ok, frame = cap.read()
        if not ok:
            break
        if index in wanted:
            frames[index] = frame
        index += 1
    cap.release()
    if not all(k in frames for k in wanted):
        raise ValueError("Could not decode all required frames for seam check")

    def mad(a, b):
        return float(np.mean(np.abs(a.astype(np.int16) - b.astype(np.int16))))

    d_start = mad(frames[0], frames[1])
    d_end = mad(frames[n_frames - 2], frames[n_frames - 1])
    d_wrap = mad(frames[n_frames - 1], frames[0])
    ratio = d_wrap / max((d_start + d_end) / 2.0, 1e-6)
    print("\n===== SEAM METRICS (mean abs pixel diff) =====")
    print(f"  frame 0 -> 1 (adjacent):             {d_start:.3f}")
    print(f"  frame {n_frames - 2} -> {n_frames - 1} (adjacent): {d_end:.3f}")
    print(f"  frame {n_frames - 1} -> 0 (loop wrap):   {d_wrap:.3f}")
    print(f"  wrap/adjacent ratio: {ratio:.2f}x")
    print("===============================================")
    if d_wrap > max(d_start, d_end) * 1.5 + 0.1:
        raise ValueError("Loop seam exceeds adjacent-frame tolerance")
    return {"start": d_start, "end": d_end, "wrap": d_wrap, "ratio": ratio}


def default_workers():
    return max(1, min(18, (os.cpu_count() or 2) - 2))


class ContinuityMonitor:
    """Watches the rendered stream for pops and flashes.

    Motion, however fast, changes a patch of the picture by similar amounts on
    successive steps. A pop (something switching state in one frame) or a flash
    (a single bad frame) changes a patch far more than the steps on either side
    of it. The monitor keeps the worst such events so they can be inspected.

    ``excess`` is in 8-bit levels, averaged over a block. Something small, bright
    and fast that crosses a block in two steps reads as a faint flash too, so a
    score is a frame worth looking at, not a verdict. The pops this was written
    to find (draw order swapping, a blur gathering what had just crossed the
    edge of the frame) scored 12 to 25 among the flowers, whose honest motion
    stays below 8; a gust full of tumbling leaves reaches 13 by itself.
    """

    def __init__(self, block=60, keep=5):
        self.block = block
        self.keep = keep
        self.previous = None
        self.steps: deque = deque(maxlen=4)      # (frame index, block differences)
        self.events: list[dict] = []

    def push(self, index, frame):
        if self.previous is not None:
            # Block means of the absolute change, in two area-averaged steps.
            diff = cv2.absdiff(frame, self.previous)
            quarter = cv2.resize(diff, (frame.shape[1] // 4, frame.shape[0] // 4), interpolation=cv2.INTER_AREA)
            size = (max(1, frame.shape[1] // self.block), max(1, frame.shape[0] // self.block))
            blocks = cv2.resize(quarter.astype(np.float32), size, interpolation=cv2.INTER_AREA).mean(axis=2)
            self.steps.append((index, blocks))
            if len(self.steps) == 4:
                (_, a), (at, b), (_, c), (_, d) = self.steps
                pop = b - np.maximum(a, c)                       # one step stands alone
                flash = np.minimum(b, c) - np.maximum(a, d)      # there and back again
                for kind, field in (("pop", pop), ("flash", flash)):
                    y, x = np.unravel_index(int(np.argmax(field)), field.shape)
                    self._record(kind, float(field[y, x]), at, int(x * self.block), int(y * self.block))
        self.previous = frame

    def _record(self, kind, excess, frame, x, y):
        if excess <= 0.0:
            return
        self.events.append(dict(kind=kind, excess=round(excess, 2), frame=int(frame), x=x, y=y))
        self.events.sort(key=lambda e: -e["excess"])
        del self.events[self.keep:]

    def report(self):
        return dict(max_excess=self.events[0]["excess"] if self.events else 0.0, worst=list(self.events))


def iter_frames(indices: Iterable[int], worker_init: Callable, worker_frame: Callable,
                init_args=(), workers=1) -> Iterator[tuple[int, object]]:
    """Yields ``(index, worker_frame(index))`` in order.

    Each worker process builds its scene once in ``worker_init``. At most a
    small window of frames is outstanding, so memory stays bounded even when
    the encoder is slower than the renderers.
    """
    indices = list(indices)
    if workers <= 1:
        worker_init(*init_args)
        for index in indices:
            yield index, worker_frame(index)
        return
    window = max(4, workers * 2)
    with ProcessPoolExecutor(max_workers=workers, initializer=worker_init, initargs=init_args) as pool:
        pending: deque = deque()
        for index in indices:
            pending.append((index, pool.submit(worker_frame, index)))
            if len(pending) >= window:
                done_index, future = pending.popleft()
                yield done_index, future.result()
        while pending:
            done_index, future = pending.popleft()
            yield done_index, future.result()


def write_contact_sheet(frames, labels, path, columns=8, thumb=(270, 480)):
    tiles = []
    for frame, label in zip(frames, labels):
        tile = cv2.resize(frame, thumb, interpolation=cv2.INTER_AREA)
        cv2.putText(tile, label, (8, thumb[1] - 12), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                    (150, 150, 150), 1, cv2.LINE_AA)
        tiles.append(tile)
    rows = math.ceil(len(tiles) / columns)
    tiles += [np.zeros_like(tiles[0])] * (rows * columns - len(tiles))
    grid = np.vstack([np.hstack(tiles[r * columns:(r + 1) * columns]) for r in range(rows)])
    cv2.imwrite(str(path), grid, [cv2.IMWRITE_JPEG_QUALITY, 92])
    return path


def summarize_metrics(samples):
    keys = ("near_black", "fog", "bright", "mean_luma")
    return {k: round(float(np.mean([s[k] for s in samples])), 4) for k in keys} if samples else {}


@dataclass(frozen=True)
class ArtworkSpec:
    description: str
    slug: str
    output_path: Path
    n_frames: int
    worker_init: Callable
    worker_frame: Callable
    fps: int = FPS
    width: int = WIDTH
    height: int = HEIGHT
    contact_shots: int = 16
    crf: int = CRF
    maxrate_kbps: int = MAXRATE_KBPS
    bufsize_kbps: int = BUFSIZE_KBPS
    # GPU films share one device, so a few processes saturate it.
    default_workers: int | None = None

    @property
    def duration_s(self):
        return self.n_frames / self.fps


def run_cli(spec: ArtworkSpec, argv=None):
    parser = argparse.ArgumentParser(description=spec.description,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("n_frames", type=int, nargs="?", default=spec.n_frames,
                        help="render only N frames for a smoke clip")
    parser.add_argument("--start-frame", type=int, default=0)
    parser.add_argument("--contact-sheet", action="store_true",
                        help="write evenly spaced preview PNGs and an overview JPEG")
    parser.add_argument("--frames", help="comma-separated frame indices to preview")
    parser.add_argument("--output", help="MP4 path; existing videos are never overwritten")
    parser.add_argument("--workers", type=int, default=spec.default_workers or default_workers())
    args = parser.parse_args(argv)
    if not 1 <= args.n_frames <= spec.n_frames:
        parser.error(f"n_frames must be between 1 and {spec.n_frames}")
    if not 0 <= args.start_frame < spec.n_frames:
        parser.error(f"start-frame must be between 0 and {spec.n_frames - 1}")
    workers = max(1, args.workers)

    if args.contact_sheet or args.frames:
        if args.frames:
            indices = [int(v) % spec.n_frames for v in args.frames.split(",") if v.strip()]
        else:
            indices = [int(i * spec.n_frames / spec.contact_shots) for i in range(spec.contact_shots)]
        return write_previews(spec, indices, workers)

    output_path = Path(args.output) if args.output else (
        spec.output_path if args.n_frames == spec.n_frames
        else PREVIEW_DIR / f"{spec.slug}-smoke-{args.start_frame}-{args.n_frames}.mp4"
    )
    if output_path.exists():
        parser.error(f"Output already exists (choose --output to preserve it): {output_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    indices = [(args.start_frame + i) % spec.n_frames for i in range(args.n_frames)]
    return encode(spec, indices, output_path, workers,
                  full_loop=args.n_frames == spec.n_frames and args.start_frame == 0)


def write_previews(spec: ArtworkSpec, indices, workers):
    PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
    frames, labels, report = [], [], []
    for index, (frame, widget_luma) in iter_frames(indices, spec.worker_init, spec.worker_frame,
                                                   workers=min(workers, len(indices))):
        t = index / spec.fps
        png = PREVIEW_DIR / f"{spec.slug}_f{index:04d}_t{t:05.1f}s.png"
        cv2.imwrite(str(png), frame)
        metrics = frame_metrics(frame)
        report.append(dict(frame=index, t=t, widget_luma=round(widget_luma, 2),
                           **{k: round(v, 4) for k, v in metrics.items()}))
        print(f"[preview] t={t:5.1f}s widget={widget_luma:5.2f} black={metrics['near_black']:.3f} "
              f"fog={metrics['fog']:.3f} bright={metrics['bright']:.3f} -> {png.name}", flush=True)
        frames.append(frame)
        labels.append(f"{t:.1f}s")
    sheet = write_contact_sheet(frames, labels, PREVIEW_DIR / f"{spec.slug}-overview.jpg",
                                columns=min(8, len(frames)))
    (PREVIEW_DIR / f"{spec.slug}-preview-metrics.json").write_text(
        json.dumps(dict(frames=report, mean=summarize_metrics(report)), indent=2))
    print(f"[preview] overview -> {sheet}; mean {summarize_metrics(report)}")
    return report


def encode(spec: ArtworkSpec, indices, output_path, workers, full_loop):
    if worst_case_upload_bytes(spec.duration_s, spec.maxrate_kbps, spec.bufsize_kbps) * 1.01 >= MAX_VIDEO_BYTES:
        raise ValueError("Encoding budget cannot guarantee the upload size limit for this duration")
    t_start = time.time()
    cmd = build_ffmpeg_cmd(output_path, width=spec.width, height=spec.height, fps=spec.fps, crf=spec.crf,
                           maxrate_kbps=spec.maxrate_kbps, bufsize_kbps=spec.bufsize_kbps)
    print("[render] ffmpeg:", " ".join(cmd), flush=True)
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    widget_lumas, samples = [], []
    monitor = ContinuityMonitor()
    try:
        for count, (index, (frame, widget_luma)) in enumerate(
                iter_frames(indices, spec.worker_init, spec.worker_frame, workers=workers)):
            proc.stdin.write(frame.tobytes())
            widget_lumas.append(widget_luma)
            monitor.push(index, frame)
            if count % spec.fps == 0:
                samples.append(frame_metrics(frame))
            if count % (spec.fps * 4) == 0:
                print(f"[render] frame {count}/{len(indices)} ({time.time() - t_start:.1f}s)", flush=True)
    finally:
        proc.stdin.close()
        stderr = proc.stderr.read().decode("utf-8", "ignore")
        code = proc.wait()
    if code != 0:
        print(stderr[-4000:])
        raise RuntimeError(f"ffmpeg exited with code {code}")
    render_elapsed = time.time() - t_start
    print(f"[render] widget luma min={min(widget_lumas):.2f} mean={np.mean(widget_lumas):.2f} "
          f"max={max(widget_lumas):.2f} (ceiling {WIDGET_LUMA_MAX})")
    if max(widget_lumas) > WIDGET_LUMA_MAX + 1e-6:
        raise ValueError("Widget zone exceeded its luminance ceiling")
    report = validate_output(output_path, n_frames=len(indices), width=spec.width, height=spec.height,
                             fps=spec.fps, render_elapsed=render_elapsed,
                             total_elapsed=time.time() - t_start)
    report["mirror_metrics"] = summarize_metrics(samples)
    report["widget_luma_max"] = round(max(widget_lumas), 3)
    report["continuity"] = monitor.report()
    print("[render] continuity (largest one-step changes that stand apart from the motion around them):")
    for event in report["continuity"]["worst"]:
        print(f"[render]   {event['kind']:5s} +{event['excess']:6.2f} entering frame {event['frame']} "
              f"near x={event['x']} y={event['y']}")
    if full_loop:
        report["seam"] = report_seam_metrics(output_path, spec.n_frames)
    report["path"] = str(output_path)
    print(f"[render] mirror metrics {report['mirror_metrics']}")
    report_path = PREVIEW_DIR / f"{output_path.stem}-report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2))
    print(f"[render] report -> {report_path}")
    return report
