#!/usr/bin/env python3
"""Read what an Android screen shows from ``adb exec-out screencap``.

The raw framebuffer format needs no image library: a short header followed
by RGBA bytes. That is enough to tell a lit dashboard from a black panel and
to follow a fade, and frames are saved as PNG evidence with ``zlib`` alone.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
BYTES_PER_PIXEL = 4
# Lookup tables for bytes.translate: C-speed thresholding of one channel.
_AT_LEAST = {}


class ScreenCaptureError(RuntimeError):
    pass


@dataclass(frozen=True)
class Screenshot:
    width: int
    height: int
    pixels: bytes

    def rows(self, top: int, bottom: int, left: int, right: int) -> bytes:
        """The RGBA bytes of a box, row by row."""
        stride = self.width * BYTES_PER_PIXEL
        return b"".join(
            self.pixels[row * stride + left * BYTES_PER_PIXEL:
                        row * stride + right * BYTES_PER_PIXEL]
            for row in range(top, bottom)
        )


def parse_raw(data: bytes) -> Screenshot:
    """Decode ``screencap`` output: width, height and format, then RGBA.

    Android 6 writes a 12-byte header; later releases add a fourth field.
    """
    if len(data) < 12:
        raise ScreenCaptureError("Screen capture is empty")
    width, height, pixel_format = struct.unpack_from("<III", data, 0)
    if pixel_format != 1:
        raise ScreenCaptureError(f"Unsupported screen pixel format {pixel_format}")
    size = width * height * BYTES_PER_PIXEL
    if width == 0 or height == 0:
        raise ScreenCaptureError("Screen capture has no pixels")
    for header in (12, 16):
        if len(data) == header + size:
            return Screenshot(width, height, data[header:])
    raise ScreenCaptureError(
        f"Screen capture is {len(data)} bytes, not a {width}x{height} RGBA frame"
    )


def box_pixels(shot: Screenshot, box: tuple[float, float, float, float] | None) -> bytes:
    """Bytes of a box given as left, top, right, bottom fractions of the frame."""
    if box is None:
        return shot.pixels
    left, top, right, bottom = box
    if not (0 <= left < right <= 1 and 0 <= top < bottom <= 1):
        raise ValueError("A box is left, top, right, bottom within 0..1")
    return shot.rows(
        int(top * shot.height),
        max(int(top * shot.height) + 1, int(bottom * shot.height)),
        int(left * shot.width),
        max(int(left * shot.width) + 1, int(right * shot.width)),
    )


def mean_level(shot: Screenshot, box: tuple[float, float, float, float] | None = None) -> float:
    """Average of the red, green and blue values, 0 (black) to 255 (white)."""
    data = box_pixels(shot, box)
    count = len(data) // BYTES_PER_PIXEL
    if count == 0:
        return 0.0
    total = sum(data[0::4]) + sum(data[1::4]) + sum(data[2::4])
    return total / (3 * count)


def lit_fraction(
    shot: Screenshot,
    threshold: int = 96,
    box: tuple[float, float, float, float] | None = None,
) -> float:
    """Share of pixels whose green value reaches ``threshold``.

    The dashboard draws near-white marks on black, so one channel is enough
    to count the pixels that carry content.
    """
    if not 0 < threshold <= 255:
        raise ValueError("threshold is 1..255")
    table = _AT_LEAST.get(threshold)
    if table is None:
        table = bytes(1 if value >= threshold else 0 for value in range(256))
        _AT_LEAST[threshold] = table
    green = box_pixels(shot, box)[1::4]
    if not green:
        return 0.0
    return green.translate(table).count(1) / len(green)


def peak_level(shot: Screenshot, box: tuple[float, float, float, float] | None = None) -> int:
    """The brightest green value in the box, 0 to 255.

    A sparse dashboard barely moves the average of a mostly black frame, but
    its brightest stroke follows a fade exactly: full when awake, zero asleep.
    """
    green = box_pixels(shot, box)[1::4]
    return max(green) if green else 0


def to_png(shot: Screenshot) -> bytes:
    """An RGB PNG of the frame, for people to look at."""
    stride = shot.width * BYTES_PER_PIXEL
    rows = bytearray()
    for row in range(shot.height):
        line = shot.pixels[row * stride:(row + 1) * stride]
        rgb = bytearray(shot.width * 3)
        rgb[0::3] = line[0::4]
        rgb[1::3] = line[1::4]
        rgb[2::3] = line[2::4]
        rows.append(0)
        rows += rgb

    def chunk(kind: bytes, body: bytes) -> bytes:
        return (
            struct.pack(">I", len(body))
            + kind
            + body
            + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", shot.width, shot.height, 8, 2, 0, 0, 0)
    return (
        PNG_SIGNATURE
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(bytes(rows), 6))
        + chunk(b"IEND", b"")
    )
