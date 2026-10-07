#!/usr/bin/env python3
"""Look in a release APK for code that Android 6 compiles wrongly on x86.

Android 6 compiles an app while installing it. Its compiler for x86 and
x86_64 has a fault in one instruction, ``filled-new-array/range`` with
objects: it gives up the register that holds the new array and then uses it
for something else, so the app is ended with a segmentation fault the moment
that code runs (``Mir2Lir::GenFilledNewArray`` in android-6.0.1_r1 frees
``ref_reg`` for x86 and marks the card through it afterwards). A Mirror has
an ARM processor and is not affected, but the emulator that every release is
tried on is, and so is anybody's Android 6 tablet with an Intel processor.

R8 writes that instruction for a list or array of more than five objects
made in one step, such as ``Arrays.asList("a", "b", "c", "d", "e", "f")`` or
``new String[] {...}`` with six names, in the release build only. A class's
static initializer is the one place where it does no harm, because Android 6
never compiles those: so the cure is to make such a list a constant.

    python tools/dex_guard.py APK [APK ...]

Fails if Mirror Home's own code has the instruction outside a static
initializer. What the libraries have is listed and not counted: it is not
ours to change, and the emulator suite has not run into it.
"""

from __future__ import annotations

import argparse
import pathlib
import re
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass

import android_emulator

OURS = "Ldev/mirror/"
PRIMITIVES = "ZBSCIJFD"
CLASS = re.compile(r"\s+Class descriptor\s+: '(.+)'")
METHOD = re.compile(r"\s+name\s+: '(.+)'")
INSTRUCTION = re.compile(r"filled-new-array/range \{([^}]*)\}, (\[+)(\S)")


class GuardError(RuntimeError):
    pass


@dataclass(frozen=True)
class Finding:
    where: str
    method: str
    elements: int

    @property
    def ours(self) -> bool:
        return self.where.startswith(OURS)

    @property
    def compiled(self) -> bool:
        """Android 6 compiles every method but a static initializer."""
        return self.method != "<clinit>"

    def __str__(self) -> str:
        return f"{self.where[1:-1].replace('/', '.')}.{self.method}: {self.elements} objects in one step"


def findings(disassembly: str) -> list[Finding]:
    """Where a disassembly, as ``dexdump -d`` prints it, makes an array of objects from a range."""
    found = []
    where = method = ""
    for line in disassembly.splitlines():
        match = CLASS.match(line)
        if match:
            where = match.group(1)
            continue
        match = METHOD.match(line)
        if match:
            method = match.group(1)
            continue
        match = INSTRUCTION.search(line)
        # An array of arrays holds objects too; only an array of numbers is filled without the fault.
        if match and (len(match.group(2)) > 1 or match.group(3) not in PRIMITIVES):
            found.append(Finding(where, method, elements(match.group(1))))
    return found


def elements(registers: str) -> int:
    """How many registers an instruction names: "v5, v6, v7" or, from older tools, "v5 .. v7"."""
    numbers = [int(number) for number in re.findall(r"v(\d+)", registers)]
    if ".." in registers and len(numbers) == 2:
        return numbers[1] - numbers[0] + 1
    return len(numbers)


def dexdump() -> pathlib.Path:
    tools = sorted((android_emulator.sdk_root() / "build-tools").glob("*/" + android_emulator.executable_name("dexdump")))
    if not tools:
        raise GuardError("dexdump was not found in the Android SDK's build-tools")
    return tools[-1]


def disassemble(apk: pathlib.Path) -> str:
    """The code of every dex file of an APK, as text."""
    tool = dexdump()
    text = []
    with zipfile.ZipFile(apk) as archive, tempfile.TemporaryDirectory() as directory:
        names = [name for name in archive.namelist() if re.fullmatch(r"classes\d*\.dex", name)]
        if not names:
            raise GuardError(f"{apk} holds no code")
        for name in names:
            path = pathlib.Path(archive.extract(name, directory))
            done = subprocess.run([str(tool), "-d", str(path)], capture_output=True)
            if done.returncode != 0:
                raise GuardError(f"dexdump could not read {name} of {apk}: {done.stderr.decode('utf-8', 'replace')[:300]}")
            text.append(done.stdout.decode("utf-8", "replace"))
    return "\n".join(text)


def judge(found: list[Finding]) -> tuple[list[Finding], list[Finding]]:
    """What must be changed, and what is only worth knowing."""
    faults = [finding for finding in found if finding.ours and finding.compiled]
    others = [finding for finding in found if not finding.ours and finding.compiled]
    return faults, others


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("apk", nargs="+", type=pathlib.Path)
    options = parser.parse_args(arguments)
    failed = False
    for apk in options.apk:
        try:
            faults, others = judge(findings(disassemble(apk)))
        except (GuardError, android_emulator.EmulatorError, OSError, zipfile.BadZipFile) as error:
            print(f"{apk}: {error}", file=sys.stderr)
            return 2
        for finding in others:
            print(f"{apk.name}: in a library, not counted: {finding}")
        for finding in faults:
            print(f"{apk.name}: {finding}", file=sys.stderr)
        if faults:
            failed = True
        else:
            print(f"{apk.name}: nothing of Mirror Home's own that Android 6 compiles wrongly on x86")
    if failed:
        print(
            "Android 6 on an x86 processor ends the app when that code runs. Make each list or array a\n"
            "constant of its class (static final), which is put together in the static initializer.",
            file=sys.stderr,
        )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
