#!/usr/bin/env python3
"""Pictures of the mascots, made on this computer without a Mirror.

The mascots are drawn by Mirror Home's own code (Mascot*.java), which knows
nothing of Android. This tool compiles that code with a JDK, has it play a
scene and write every frame down as shapes, and paints the shapes with
Pillow. What it shows is therefore what the glass shows, up to the last
pixel of anti-aliasing.

    python tools/mascots.py sheet    every mascot in every mood, as one picture
    python tools/mascots.py film     the mascots acting out a conversation, as a GIF

Both write into build/mascots/ unless --out says otherwise. Needs a JDK
(javac and java on the PATH, or JAVA_HOME) and Pillow.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import shutil
import subprocess
import sys

from PIL import Image, ImageDraw, ImageFont

REPO = pathlib.Path(__file__).resolve().parents[1]
SOURCES = REPO / "android" / "mirror-home" / "src"
MAIN = SOURCES / "main" / "java" / "dev" / "mirror" / "repurpose"
TEST = SOURCES / "test" / "java" / "dev" / "mirror" / "repurpose"
OUT = REPO / "build" / "mascots"
# Shapes are painted this much larger and scaled down, which smooths their edges.
OVERSAMPLE = 3
INK = (245, 242, 236)
DIM = (150, 148, 144)
BLUSH = (255, 158, 143)
MOODS = (
    "idle", "listening", "thinking", "speaking", "happy", "greeting",
    "curious", "confused", "sorry", "sleepy",
)
# A conversation: what happens, for how long, what the Mirror's panel says
# meanwhile, and what is going on, for whoever watches.
SCENE = (
    ("mood:hidden", 0.6, "", ""),
    ("mood:listening", 2.4, "Listening", "You say \u201cMirror\u201d"),
    ("mood:thinking", 1.8, "", "You ask for something"),
    ("nod", 1.2, "\u201cwhat\u2019s on my list?\u201d", "It understood"),
    ("speak:1.8:idle", 3.8, "Three things on your list", "It answers"),
    ("mood:hidden", 0.9, "", ""),
    ("mood:thinking", 1.6, "", "Another request"),
    ("speak:1.5:curious", 3.4, "Which list do you mean?", "It asks back, and waits"),
    ("mood:confused", 2.8, "Didn\u2019t catch that", "It did not follow"),
    ("mood:happy", 2.4, "Brighter", "A command, done"),
    ("mood:greeting", 3.0, "Good morning", "A greeting"),
    ("mood:sorry", 2.6, "The assistant isn\u2019t answering", "Something went wrong"),
    ("mood:sleepy", 3.4, "Good night", "Told good night"),
    ("mood:hidden", 1.0, "", ""),
)


class MascotError(Exception):
    """Something this tool needs is missing, said in a sentence."""


def jdk_tool(name: str) -> str:
    home = os.environ.get("JAVA_HOME")
    if home:
        candidate = pathlib.Path(home) / "bin" / (name + (".exe" if os.name == "nt" else ""))
        if candidate.is_file():
            return str(candidate)
    found = shutil.which(name)
    if not found:
        raise MascotError(f"{name} was not found. Install a JDK, or set JAVA_HOME to one.")
    return found


def compile_mascots(out: pathlib.Path) -> pathlib.Path:
    """Compiles the mascots and the scene player; returns the folder of classes."""
    classes = out / "classes"
    classes.mkdir(parents=True, exist_ok=True)
    sources = sorted(MAIN.glob("Mascot*.java")) + [TEST / "MascotRecorder.java", TEST / "MascotFilm.java"]
    # MascotView is the one that needs Android.
    sources = [source for source in sources if source.name != "MascotView.java"]
    done = subprocess.run(
        [jdk_tool("javac"), "-nowarn", "-d", str(classes), *map(str, sources)],
        capture_output=True, text=True,
    )
    if done.returncode != 0:
        raise MascotError("The mascots did not compile:\n" + done.stderr.strip())
    return classes


def play(steps: list[str], rate: int, out: pathlib.Path) -> dict:
    """Has every mascot act the steps out; returns the frames as MascotFilm wrote them."""
    classes = compile_mascots(out)
    frames = out / "frames.json"
    done = subprocess.run(
        [jdk_tool("java"), "-cp", str(classes), "dev.mirror.repurpose.MascotFilm", str(frames), str(rate), ",".join(steps)],
        capture_output=True, text=True,
    )
    if done.returncode != 0:
        raise MascotError("The scene could not be played:\n" + done.stderr.strip())
    film = json.loads(frames.read_text(encoding="utf-8"))
    frames.unlink()
    return film


def paint(shapes: list[list[float]], size: int) -> Image.Image:
    """One frame of one mascot, on the black that is plain mirror."""
    large = size * OVERSAMPLE
    image = Image.new("RGB", (large, large), (0, 0, 0))
    for rgb, alpha, width, closed, *flat in shapes:
        points = [((x + 1) / 2 * large, (y + 1) / 2 * large) for x, y in zip(flat[0::2], flat[1::2])]
        thick = width / 2 * large
        margin = thick / 2 + 2
        left = max(0, int(min(x for x, _ in points) - margin))
        top = max(0, int(min(y for _, y in points) - margin))
        right = min(large, int(max(x for x, _ in points) + margin) + 1)
        bottom = min(large, int(max(y for _, y in points) + margin) + 1)
        if right <= left or bottom <= top:
            continue
        mask = Image.new("L", (right - left, bottom - top), 0)
        draw = ImageDraw.Draw(mask)
        local = [(x - left, y - top) for x, y in points]
        if width == 0:
            draw.polygon(local, fill=255)
        else:
            path = local + local[:2] if closed else local
            draw.line(path, fill=255, width=max(1, round(thick)), joint="curve")
            for x, y in local if closed else (local[0], local[-1]):
                draw.ellipse((x - thick / 2, y - thick / 2, x + thick / 2, y + thick / 2), fill=255)
        color = ((int(rgb) >> 16) & 255, (int(rgb) >> 8) & 255, int(rgb) & 255)
        image.paste(color, (left, top), mask.point(lambda value: int(value * alpha)))
    return image.resize((size, size), Image.LANCZOS)


def font(size: int, italic: bool = False) -> ImageFont.ImageFont:
    """A light face, as the glass uses; whichever of them this computer has."""
    names = (
        ("Roboto-LightItalic.ttf", "segoeuili.ttf", "DejaVuSans-Oblique.ttf")
        if italic else ("Roboto-Light.ttf", "segoeuil.ttf", "HelveticaNeue.ttc", "DejaVuSans-ExtraLight.ttf", "DejaVuSans.ttf")
    )
    for name in names:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def palette() -> Image.Image:
    """The few colours a mascot has, in steps: one set for every frame keeps a GIF small."""
    colors: list[int] = []
    for step in range(24):
        colors += [round(channel * step / 23) for channel in INK]
    for step in range(1, 9):
        colors += [round(channel * step / 8) for channel in BLUSH]
    holder = Image.new("P", (1, 1))
    holder.putpalette(colors + [0] * (768 - len(colors)))
    return holder


def centered(draw: ImageDraw.ImageDraw, text: str, x: float, y: float, face, fill) -> None:
    draw.text((x - draw.textlength(text, font=face) / 2, y), text, font=face, fill=fill)


def sheet(out: pathlib.Path, tile: int) -> pathlib.Path:
    """Every mascot in every mood: a row for each mascot, a column for each mood."""
    steps = []
    for mood in MOODS:
        steps += [f"mood:{mood}", "run:0.9"]
    rate = 20
    film = play(["mood:idle", "run:1.5"] + steps, rate, out)
    moment = [round(1.5 * rate) + round(0.9 * rate) * (index + 1) - 1 for index in range(len(MOODS))]
    label, name = font(15), font(22)
    side = 110
    image = Image.new("RGB", (side + tile * len(MOODS), 34 + tile * len(film["mascots"])), (0, 0, 0))
    draw = ImageDraw.Draw(image)
    for column, mood in enumerate(MOODS):
        centered(draw, mood, side + tile * column + tile / 2, 10, label, DIM)
    for row, mascot in enumerate(film["mascots"]):
        draw.text((14, 34 + tile * row + tile / 2 - 14), mascot["name"], font=name, fill=INK)
        for column, frame in enumerate(moment):
            image.paste(paint(mascot["frames"][frame], tile), (side + tile * column, 34 + tile * row))
    path = out / "mascots-sheet.png"
    image.save(path)
    return path


def film(out: pathlib.Path, tile: int, rate: int, only: str | None) -> pathlib.Path:
    """The mascots acting out a conversation side by side, with what the panel says under them."""
    steps: list[str] = []
    captions: list[tuple[str, str]] = []
    for step, seconds, says, happening in SCENE:
        steps += [step, f"run:{seconds}"]
        captions += [(says, happening)] * round(seconds * rate)
    played = play(steps, rate, out)
    mascots = [mascot for mascot in played["mascots"] if only in (None, mascot["id"])]
    if not mascots:
        raise MascotError(f"There is no mascot called {only}.")
    columns = 1 if len(mascots) == 1 else 2
    rows = -(-len(mascots) // columns)
    width, strip = tile * columns, 96
    name, says_face, quote_face, small = font(18), font(30), font(24, italic=True), font(15)
    frames = []
    for index, (says, happening) in enumerate(captions):
        image = Image.new("RGB", (width, tile * rows + strip), (0, 0, 0))
        draw = ImageDraw.Draw(image)
        for place, mascot in enumerate(mascots):
            x, y = tile * (place % columns), tile * (place // columns)
            image.paste(paint(mascot["frames"][index], tile), (x, y))
            draw.text((x + 14, y + 10), mascot["name"], font=name, fill=DIM)
        quoted = says.startswith("\u201c")
        centered(draw, says, width / 2, tile * rows + 8, quote_face if quoted else says_face, DIM if quoted else INK)
        centered(draw, happening, width / 2, tile * rows + 62, small, DIM)
        frames.append(image)
    path = out / ("mascots-film.gif" if only is None else f"mascot-{only}.gif")
    colors = palette()
    frames = [frame.quantize(palette=colors, dither=Image.Dither.NONE) for frame in frames]
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=round(1000 / rate), loop=0)
    return path


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("what", choices=("sheet", "film"))
    parser.add_argument("--out", type=pathlib.Path, default=OUT, help="folder to write into (default: build/mascots)")
    parser.add_argument("--tile", type=int, default=0, help="pixels a mascot gets each way (default: 150 for the sheet, 240 for the film)")
    parser.add_argument("--fps", type=int, default=20, help="frames a second for the film; a GIF keeps time in hundredths, so 20 or 25")
    parser.add_argument("--only", help="the film of one mascot alone, by its id")
    options = parser.parse_args(arguments)
    options.out.mkdir(parents=True, exist_ok=True)
    try:
        if options.what == "sheet":
            path = sheet(options.out, options.tile or 150)
        else:
            path = film(options.out, options.tile or 240, options.fps, options.only)
    except MascotError as error:
        print(f"mascots: {error}", file=sys.stderr)
        return 1
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
