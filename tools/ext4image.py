#!/usr/bin/env python3
"""Reads the ext4 file system in a raw or Android sparse image, without mounting it.

``install.py verify`` uses it to know what a unit's ``/system`` must show once
``system.img`` is written: every entry's inode, type, mode, owner, link count,
size, modification time, SELinux label, symlink target, and each regular
file's sha1. Only what make_ext4fs writes is read (extents, no 64-bit block
numbers, no inline data or encryption); an image using any other layout
feature is refused rather than read wrongly. Bytes a sparse image leaves
undefined (DONT_CARE chunks keep whatever the partition held) are never read
as data: anything that needs them is refused.

    python tools/ext4image.py system.img            # the entries, as JSON lines
"""

from __future__ import annotations

import bisect
import hashlib
import json
import pathlib
import stat
import struct
import sys
from typing import Iterator

SPARSE_MAGIC = 0xED26FF3A
CHUNK_RAW, CHUNK_FILL, CHUNK_DONT_CARE, CHUNK_CRC32 = 0xCAC1, 0xCAC2, 0xCAC3, 0xCAC4
EXT4_MAGIC = 0xEF53
EXTENT_MAGIC = 0xF30A
XATTR_MAGIC = 0xEA020000
ROOT_INODE = 2

# Features that change nothing about how this reader finds the bytes: compat ones never do.
INCOMPAT_READ = {0x2: "filetype", 0x40: "extents", 0x200: "flex_bg"}
RO_COMPAT_READ = {0x1: "sparse_super", 0x2: "large_file", 0x8: "huge_file", 0x10: "gdt_csum",
                  0x20: "dir_nlink", 0x40: "extra_isize"}
EXTENTS_FL, INLINE_DATA_FL, ENCRYPT_FL = 0x80000, 0x10000000, 0x800
MAX_DEPTH = 5
MAX_ENTRIES = 1_000_000
READ_PIECE = 1 << 20
SELINUX_INDEX, SELINUX_NAME = 6, b"selinux"


class Unknown(ValueError):
    """The image does not define these bytes (a sparse DONT_CARE chunk)."""


class Unpacked:
    """Random access to the bytes an image unpacks to.

    A sparse image's chunk headers are walked once (as install.py's plan does: a
    cut-off, padded or checksummed file is refused) into an index of pieces.
    """

    def __init__(self, stream, file_size: int):
        self.stream = stream
        stream.seek(0)
        head = stream.read(28)
        if len(head) == 28 and struct.unpack_from("<I", head)[0] == SPARSE_MAGIC:
            (_, major, _, file_header, chunk_header, block_size, total_blocks, total_chunks,
             checksum) = struct.unpack("<IHHHHIIII", head)
            if major != 1 or file_header < 28 or chunk_header < 12 or not block_size or block_size % 4:
                raise ValueError("its sparse header is not one this tool reads")
            if checksum:
                raise ValueError("it carries a sparse checksum, which this tool does not check")
            if total_chunks > total_blocks or total_chunks * chunk_header > file_size - file_header:
                raise ValueError("its header counts more chunks than it can hold")
            self.starts: list[int] = []
            self.pieces: list[tuple[int, int, int, int | bytes]] = []
            position, block = file_header, 0
            for _ in range(total_chunks):
                stream.seek(position)
                header = stream.read(chunk_header)
                if len(header) != chunk_header:
                    raise ValueError("it ends inside a chunk header")
                kind, _, blocks, total = struct.unpack_from("<HHII", header)
                data = {CHUNK_RAW: blocks * block_size, CHUNK_FILL: 4, CHUNK_DONT_CARE: 0}.get(kind)
                if kind == CHUNK_CRC32:
                    raise ValueError("it carries sparse checksums, which this tool does not check")
                if data is None or total - chunk_header != data or not blocks:
                    raise ValueError(f"a chunk at block {block} is not valid")
                start = position + chunk_header
                if start + data > file_size:
                    raise ValueError(f"it ends inside the chunk at block {block}")
                source: int | bytes = start
                if kind == CHUNK_FILL:
                    stream.seek(start)
                    source = stream.read(4)
                    if len(source) != 4:
                        raise ValueError("it changed while it was being read")
                self.starts.append(block * block_size)
                self.pieces.append((block * block_size, (block + blocks) * block_size, kind, source))
                position, block = start + data, block + blocks
            if block != total_blocks:
                raise ValueError(f"its chunks hold {block} blocks, its header says {total_blocks}")
            if position != file_size:
                raise ValueError("it has bytes after its last chunk")
            self.size, self.sparse = total_blocks * block_size, True
        else:
            self.size, self.sparse = file_size, False

    def read(self, offset: int, length: int) -> bytes:
        if offset < 0 or length < 0 or offset + length > self.size:
            raise ValueError(f"it refers to bytes {offset}-{offset + length} beyond its end ({self.size})")
        if not self.sparse:
            self.stream.seek(offset)
            data = self.stream.read(length)
            if len(data) != length:
                raise ValueError("it changed while it was being read")
            return data
        found = bytearray()
        position, end = offset, offset + length
        index = bisect.bisect_right(self.starts, offset) - 1
        while position < end:
            low, high, kind, source = self.pieces[index]
            take = min(high, end) - position
            if kind == CHUNK_DONT_CARE:
                raise Unknown(f"it refers to bytes at {position} that the sparse image leaves undefined")
            if kind == CHUNK_RAW:
                self.stream.seek(source + position - low)
                piece = self.stream.read(take)
                if len(piece) != take:
                    raise ValueError("it changed while it was being read")
                found += piece
            else:
                shift = (position - low) % 4
                found += (source[shift:] + source * (take // 4 + 2))[:take]
            position += take
            index += 1
        return bytes(found)


def signed32(value: int) -> int:
    return value - (1 << 32) if value & 0x80000000 else value


class Ext4:
    """The file system in an unpacked image."""

    def __init__(self, image: Unpacked):
        self.image = image
        sb = image.read(1024, 1024)
        if struct.unpack_from("<H", sb, 0x38)[0] != EXT4_MAGIC:
            raise ValueError("it is not an ext4 file system")
        (self.inodes_count, self.blocks_count, _, _, _, self.first_data_block, log_block_size, _,
         self.blocks_per_group, _, self.inodes_per_group) = struct.unpack_from("<11I", sb, 0)
        revision = struct.unpack_from("<I", sb, 0x4C)[0]
        compat, incompat, ro_compat = struct.unpack_from("<III", sb, 0x5C)
        unread = [f"incompat {bit:#x}" for bit in range(32) if incompat & (1 << bit) and (1 << bit) not in INCOMPAT_READ]
        unread += [f"ro_compat {bit:#x}" for bit in range(32)
                   if ro_compat & (1 << bit) and (1 << bit) not in RO_COMPAT_READ]
        if unread:
            raise ValueError(f"it uses ext4 features this tool does not read ({', '.join(unread)})")
        if log_block_size > 6:
            raise ValueError("its ext4 block size is not valid")
        self.block_size = 1024 << log_block_size
        self.inode_size = struct.unpack_from("<H", sb, 0x58)[0] if revision >= 1 else 128
        if self.inode_size < 128 or self.inode_size > self.block_size or self.inode_size & (self.inode_size - 1):
            raise ValueError("its inode size is not valid")
        if not 0 < self.blocks_per_group <= 8 * self.block_size or not 0 < self.inodes_per_group <= 8 * self.block_size:
            raise ValueError("its ext4 superblock is not consistent")
        if self.blocks_count * self.block_size > image.size:
            raise ValueError(f"its file system ({self.blocks_count * self.block_size} bytes) is larger than the "
                             f"image ({image.size})")
        self.groups = -(-(self.blocks_count - self.first_data_block) // self.blocks_per_group)
        if not self.groups or self.inodes_count != self.groups * self.inodes_per_group:
            raise ValueError("its ext4 superblock is not consistent")
        descriptors = image.read((self.first_data_block + 1) * self.block_size, 32 * self.groups)
        self.inode_tables = [struct.unpack_from("<I", descriptors, 32 * group + 8)[0] for group in range(self.groups)]
        if any(not 0 < table < self.blocks_count for table in self.inode_tables):
            raise ValueError("a group descriptor names an inode table outside the file system")

    def block(self, number: int, count: int = 1) -> bytes:
        if number + count > self.blocks_count or number < 0:
            raise ValueError(f"it refers to block {number + count - 1}, beyond its {self.blocks_count} blocks")
        return self.image.read(number * self.block_size, count * self.block_size)

    def inode(self, number: int) -> dict:
        if not 0 < number <= self.inodes_count:
            raise ValueError(f"it refers to inode {number}, which does not exist")
        group, index = divmod(number - 1, self.inodes_per_group)
        raw = self.image.read(self.inode_tables[group] * self.block_size + index * self.inode_size, self.inode_size)
        mode, uid_low, size_low = struct.unpack_from("<HHI", raw, 0)
        mtime = struct.unpack_from("<I", raw, 16)[0]
        gid_low, links = struct.unpack_from("<HH", raw, 24)
        flags = struct.unpack_from("<I", raw, 32)[0]
        file_acl, size_high = struct.unpack_from("<II", raw, 104)
        acl_high, uid_high, gid_high = struct.unpack_from("<HHH", raw, 118)
        extra = struct.unpack_from("<H", raw, 128)[0] if self.inode_size > 128 else 0
        if extra and (extra % 4 or 128 + extra > self.inode_size):
            raise ValueError(f"inode {number} has an invalid extra size")
        seconds = signed32(mtime)
        if extra >= 12:  # i_mtime_extra (inode bytes 136-139): two epoch bits above the 32-bit seconds
            seconds += (struct.unpack_from("<I", raw, 0x88)[0] & 3) << 32
        return {"number": number, "mode": mode, "uid": uid_low | uid_high << 16, "gid": gid_low | gid_high << 16,
                "links": links, "size": size_low | size_high << 32, "mtime": seconds, "flags": flags,
                "fileAcl": file_acl | acl_high << 32, "iblock": raw[40:100], "extra": extra, "raw": raw}

    # -- data ---------------------------------------------------------------

    def extents(self, inode: dict) -> list[tuple[int, int, int, bool]]:
        """(first logical block, length, first physical block, uninitialised) of every extent, in order."""
        found: list[tuple[int, int, int, bool]] = []

        def walk(node: bytes, level: int, expected_depth: int | None) -> None:
            magic, entries, maximum, depth = struct.unpack_from("<HHHH", node, 0)
            if magic != EXTENT_MAGIC or entries > maximum or 12 + 12 * maximum > len(node) or depth > MAX_DEPTH \
                    or (expected_depth is not None and depth != expected_depth):
                raise ValueError(f"inode {inode['number']} has an invalid extent tree")
            for entry in range(entries):
                offset = 12 + 12 * entry
                if depth:
                    child = struct.unpack_from("<I", node, offset + 4)[0] | struct.unpack_from("<H", node, offset + 8)[0] << 32
                    walk(self.block(child), level + 1, depth - 1)
                else:
                    logical, length, start_high, start_low = struct.unpack_from("<IHHI", node, offset)
                    uninitialised = length > 32768
                    length -= 32768 if uninitialised else 0
                    physical = start_low | start_high << 32
                    if not length or physical + length > self.blocks_count:
                        raise ValueError(f"inode {inode['number']} has an extent outside the file system")
                    found.append((logical, length, physical, uninitialised))

        walk(inode["iblock"], 0, None)
        for before, after in zip(found, found[1:]):
            if before[0] + before[1] > after[0]:
                raise ValueError(f"inode {inode['number']} has extents out of order or overlapping")
        return found

    def data(self, inode: dict) -> Iterator[bytes]:
        """The file's bytes, in pieces; holes and uninitialised extents read as zeros."""
        if inode["flags"] & (INLINE_DATA_FL | ENCRYPT_FL):
            raise ValueError(f"inode {inode['number']} keeps its data inline or encrypted, which this tool does not read")
        if not inode["flags"] & EXTENTS_FL:
            if inode["size"] == 0:
                return
            raise ValueError(f"inode {inode['number']} uses block maps, which this tool does not read")
        remaining, position = inode["size"], 0
        for logical, length, physical, uninitialised in self.extents(inode):
            if not remaining:
                break
            start = logical * self.block_size
            if start > position:
                gap = min(start - position, remaining)
                for piece in range(0, gap, READ_PIECE):
                    yield bytes(min(READ_PIECE, gap - piece))
                remaining -= gap
                position += gap
            extent_bytes = min(length * self.block_size, remaining)
            for piece in range(0, extent_bytes, READ_PIECE):
                take = min(READ_PIECE, extent_bytes - piece)
                yield bytes(take) if uninitialised else self.image.read(physical * self.block_size + piece, take)
            remaining -= extent_bytes
            position += extent_bytes
        for piece in range(0, remaining, READ_PIECE):
            yield bytes(min(READ_PIECE, remaining - piece))

    def symlink_target(self, inode: dict) -> bytes:
        if not inode["flags"] & EXTENTS_FL and inode["size"] < 60:
            return inode["iblock"][:inode["size"]]
        return b"".join(self.data(inode))

    def directory(self, inode: dict) -> list[tuple[bytes, int]]:
        """(name, inode) of every entry but . and .."""
        content = b"".join(self.data(inode))
        names: list[tuple[bytes, int]] = []
        for block_start in range(0, len(content), self.block_size):
            block = content[block_start:block_start + self.block_size]
            offset = 0
            while offset < len(block):
                if offset + 8 > len(block):
                    raise ValueError(f"directory inode {inode['number']} has a cut-off entry")
                number, record, name_length = struct.unpack_from("<IHB", block, offset)
                if record < 8 + name_length or record % 4 or offset + record > len(block):
                    raise ValueError(f"directory inode {inode['number']} has an invalid entry")
                name = block[offset + 8:offset + 8 + name_length]
                if number and name not in (b".", b".."):
                    if not name or b"/" in name or b"\0" in name:
                        raise ValueError(f"directory inode {inode['number']} holds an invalid name")
                    names.append((name, number))
                offset += record
        return names

    def attributes(self, inode: dict) -> dict[tuple[int, bytes], bytes]:
        """Extended attributes, from the inode's spare room and its attribute block."""
        found: dict[tuple[int, bytes], bytes] = {}

        def entries(area: bytes, first: int, base: int) -> None:
            offset = first
            while offset + 4 <= len(area) and struct.unpack_from("<I", area, offset)[0]:
                if offset + 16 > len(area):
                    raise ValueError(f"inode {inode['number']} has a cut-off attribute")
                name_length, index, value_offset, value_inode, value_size = struct.unpack_from("<BBHII", area, offset)
                name = area[offset + 16:offset + 16 + name_length]
                start = base + value_offset
                if value_inode or len(name) != name_length or start + value_size > len(area):
                    raise ValueError(f"inode {inode['number']} has an invalid attribute")
                if (index, name) in found:
                    raise ValueError(f"inode {inode['number']} has an attribute twice")
                found[(index, name)] = area[start:start + value_size]
                offset += (16 + name_length + 3) & ~3

        if inode["extra"]:
            body = inode["raw"][128 + inode["extra"]:]
            if len(body) >= 4 and struct.unpack_from("<I", body, 0)[0] == XATTR_MAGIC:
                entries(body, 4, 4)
        if inode["fileAcl"]:
            block = self.block(inode["fileAcl"])
            if struct.unpack_from("<I", block, 0)[0] != XATTR_MAGIC or struct.unpack_from("<I", block, 8)[0] != 1:
                raise ValueError(f"inode {inode['number']} names an attribute block that is not one")
            entries(block, 32, 0)
        return found

    def label(self, inode: dict) -> str | None:
        value = self.attributes(inode).get((SELINUX_INDEX, SELINUX_NAME))
        if value is None:
            return None
        return value.rstrip(b"\0").decode("utf-8", "surrogateescape")

    # -- the tree -------------------------------------------------------------

    def entry(self, path: str, inode: dict, hash_files: bool) -> dict:
        mode = inode["mode"]
        found = {"path": path, "inode": inode["number"], "mode": mode, "uid": inode["uid"], "gid": inode["gid"],
                 "links": inode["links"], "size": inode["size"], "mtime": inode["mtime"], "label": self.label(inode)}
        if stat.S_ISLNK(mode):
            found["target"] = self.symlink_target(inode).decode("utf-8", "surrogateescape")
        elif stat.S_ISREG(mode):
            if hash_files:
                digest = hashlib.sha1()
                for piece in self.data(inode):
                    digest.update(piece)
                found["sha1"] = digest.hexdigest()
        elif stat.S_IFMT(mode) not in (stat.S_IFDIR, stat.S_IFCHR, stat.S_IFBLK, stat.S_IFIFO, stat.S_IFSOCK):
            raise ValueError(f"{path} has an invalid file type")
        return found

    def walk(self, mount: str = "/", hash_files: bool = True) -> list[dict]:
        """Every entry under the root, as the mounted file system shows it at ``mount``, sorted by path."""
        root = self.inode(ROOT_INODE)
        if not stat.S_ISDIR(root["mode"]):
            raise ValueError("its root is not a directory")
        entries = [self.entry(mount, root, hash_files)]
        seen = {ROOT_INODE}
        pending = [(mount.rstrip("/"), root)]
        while pending:
            folder, inode = pending.pop()
            for name, number in self.directory(inode):
                child = self.inode(number)
                path = f"{folder}/{name.decode('utf-8', 'surrogateescape')}"
                if stat.S_ISDIR(child["mode"]):
                    if number in seen:
                        raise ValueError(f"{path} is a second name for a folder")
                    seen.add(number)
                    pending.append((path, child))
                entries.append(self.entry(path, child, hash_files))
                if len(entries) > MAX_ENTRIES:
                    raise ValueError("it holds more entries than this tool reads")
        return sorted(entries, key=lambda entry: entry["path"])


def read_entries(stream, file_size: int, mount: str = "/", hash_files: bool = True) -> list[dict]:
    return Ext4(Unpacked(stream, file_size)).walk(mount, hash_files)


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if len(arguments) != 1:
        print("usage: ext4image.py IMAGE", file=sys.stderr)
        return 2
    path = pathlib.Path(arguments[0])
    try:
        with path.open("rb") as stream:
            entries = read_entries(stream, path.stat().st_size)
    except (OSError, ValueError) as error:
        print(f"ext4image: {path.name}: {error}", file=sys.stderr)
        return 1
    for entry in entries:
        print(json.dumps(entry, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
