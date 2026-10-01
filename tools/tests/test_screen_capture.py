import pathlib
import struct
import sys
import unittest
import zlib

TOOLS = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))

import screen_capture
from screen_capture import ScreenCaptureError, Screenshot


BLACK = (0, 0, 0, 255)
WHITE = (255, 255, 255, 255)


def frame(width, height, pixel=BLACK, lit=None):
    """A frame filled with one pixel, with ``lit`` as {(x, y): pixel}."""
    pixels = bytearray(bytes(pixel) * (width * height))
    for (x, y), value in (lit or {}).items():
        offset = (y * width + x) * 4
        pixels[offset:offset + 4] = bytes(value)
    return Screenshot(width, height, bytes(pixels))


def raw(shot, header_fields=3):
    header = struct.pack("<III", shot.width, shot.height, 1)
    if header_fields == 4:
        header += struct.pack("<I", 0)
    return header + shot.pixels


def png_chunks(data):
    """(kind, body) pairs of a PNG, checking each chunk's CRC on the way."""
    if data[:8] != screen_capture.PNG_SIGNATURE:
        raise AssertionError("Not a PNG")
    position = 8
    chunks = []
    while position < len(data):
        (length,) = struct.unpack_from(">I", data, position)
        kind = data[position + 4:position + 8]
        body = data[position + 8:position + 8 + length]
        (crc,) = struct.unpack_from(">I", data, position + 8 + length)
        if crc != zlib.crc32(kind + body) & 0xFFFFFFFF:
            raise AssertionError(f"Bad CRC in {kind!r}")
        chunks.append((kind, body))
        position += 12 + length
    return chunks


class ParseRawTest(unittest.TestCase):
    def test_reads_the_android_6_header(self):
        shot = frame(3, 2, lit={(2, 1): WHITE})
        self.assertEqual(shot, screen_capture.parse_raw(raw(shot)))

    def test_reads_the_longer_header_of_later_releases(self):
        shot = frame(3, 2, lit={(0, 0): WHITE})
        self.assertEqual(shot, screen_capture.parse_raw(raw(shot, header_fields=4)))

    def test_rejects_an_empty_capture(self):
        with self.assertRaisesRegex(ScreenCaptureError, "empty"):
            screen_capture.parse_raw(b"")

    def test_rejects_other_pixel_formats(self):
        data = struct.pack("<III", 1, 1, 4) + b"\x00\x00"
        with self.assertRaisesRegex(ScreenCaptureError, "pixel format 4"):
            screen_capture.parse_raw(data)

    def test_rejects_a_frame_without_pixels(self):
        with self.assertRaisesRegex(ScreenCaptureError, "no pixels"):
            screen_capture.parse_raw(struct.pack("<III", 0, 1920, 1))

    def test_rejects_a_truncated_frame(self):
        data = raw(frame(4, 4))[:-4]
        with self.assertRaisesRegex(ScreenCaptureError, "not a 4x4 RGBA frame"):
            screen_capture.parse_raw(data)


class LevelsTest(unittest.TestCase):
    def test_mean_level_spans_black_to_white(self):
        self.assertEqual(0.0, screen_capture.mean_level(frame(4, 4)))
        self.assertEqual(255.0, screen_capture.mean_level(frame(4, 4, WHITE)))

    def test_mean_level_ignores_alpha(self):
        self.assertEqual(0.0, screen_capture.mean_level(frame(2, 2, (0, 0, 0, 255))))
        self.assertEqual(255.0, screen_capture.mean_level(frame(2, 2, (255, 255, 255, 0))))

    def test_mean_level_averages_the_three_colours(self):
        self.assertAlmostEqual(20.0, screen_capture.mean_level(frame(2, 2, (10, 20, 30, 255))))

    def test_mean_level_of_a_box(self):
        shot = frame(4, 4, lit={(x, y): WHITE for x in range(2) for y in range(2)})
        self.assertEqual(63.75, screen_capture.mean_level(shot))
        self.assertEqual(255.0, screen_capture.mean_level(shot, (0, 0, 0.5, 0.5)))
        self.assertEqual(0.0, screen_capture.mean_level(shot, (0.5, 0.5, 1, 1)))

    def test_lit_fraction_counts_pixels_at_or_above_the_threshold(self):
        shot = frame(4, 1, lit={(0, 0): (0, 95, 0, 255), (1, 0): (0, 96, 0, 255), (2, 0): WHITE})
        self.assertEqual(0.5, screen_capture.lit_fraction(shot))
        self.assertEqual(0.75, screen_capture.lit_fraction(shot, 95))
        self.assertEqual(0.25, screen_capture.lit_fraction(shot, 255))

    def test_lit_fraction_of_a_box(self):
        shot = frame(10, 10, lit={(9, 9): WHITE})
        self.assertEqual(0.01, screen_capture.lit_fraction(shot))
        self.assertEqual(0.0, screen_capture.lit_fraction(shot, 96, (0, 0, 0.5, 0.5)))
        self.assertEqual(0.04, screen_capture.lit_fraction(shot, 96, (0.5, 0.5, 1, 1)))

    def test_lit_fraction_rejects_a_threshold_everything_meets(self):
        for threshold in (0, 256, -1):
            with self.assertRaises(ValueError):
                screen_capture.lit_fraction(frame(1, 1), threshold)

    def test_peak_level_finds_one_lit_pixel_in_a_black_frame(self):
        # The case the mean misses: a hairline dashboard on a black panel.
        shot = frame(100, 100, lit={(63, 41): (200, 180, 160, 255)})
        self.assertLess(screen_capture.mean_level(shot), 0.1)
        self.assertEqual(180, screen_capture.peak_level(shot))

    def test_peak_level_of_a_box_ignores_pixels_outside_it(self):
        shot = frame(10, 10, lit={(9, 9): WHITE, (1, 1): (0, 40, 0, 255)})
        self.assertEqual(40, screen_capture.peak_level(shot, (0, 0, 0.5, 0.5)))
        self.assertEqual(255, screen_capture.peak_level(shot, (0.5, 0.5, 1, 1)))

    def test_black_frame_has_no_peak(self):
        self.assertEqual(0, screen_capture.peak_level(frame(8, 8)))


class BoxTest(unittest.TestCase):
    def test_rows_returns_the_bytes_of_a_box(self):
        shot = frame(3, 3, lit={(1, 1): (1, 2, 3, 4), (2, 1): (5, 6, 7, 8)})
        self.assertEqual(bytes([1, 2, 3, 4, 5, 6, 7, 8]), shot.rows(1, 2, 1, 3))

    def test_a_thin_box_still_covers_one_pixel(self):
        shot = frame(4, 4, lit={(0, 0): WHITE})
        self.assertEqual(bytes(WHITE), screen_capture.box_pixels(shot, (0, 0, 0.01, 0.01)))

    def test_rejects_a_box_outside_the_frame(self):
        shot = frame(2, 2)
        for box in ((0.5, 0, 0.5, 1), (0, 0, 1.5, 1), (-0.1, 0, 1, 1), (0, 0.9, 1, 0.1)):
            with self.assertRaises(ValueError):
                screen_capture.box_pixels(shot, box)


class PngTest(unittest.TestCase):
    def test_writes_an_rgb_png_of_the_frame(self):
        shot = frame(3, 2, lit={(0, 0): (10, 20, 30, 40), (2, 1): (250, 240, 230, 220)})
        chunks = png_chunks(screen_capture.to_png(shot))
        self.assertEqual([b"IHDR", b"IDAT", b"IEND"], [kind for kind, _ in chunks])
        self.assertEqual(
            (3, 2, 8, 2, 0, 0, 0),
            struct.unpack(">IIBBBBB", chunks[0][1]),
        )
        rows = zlib.decompress(chunks[1][1])
        self.assertEqual(
            bytes([0, 10, 20, 30, 0, 0, 0, 0, 0, 0])
            + bytes([0, 0, 0, 0, 0, 0, 0, 250, 240, 230]),
            rows,
        )


if __name__ == "__main__":
    unittest.main()
