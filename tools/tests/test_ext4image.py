import hashlib
import io
import pathlib
import stat
import struct
import sys
import unittest

TOOLS = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))

import ext4image  # noqa: E402

BS = 4096
INODES = 32
INODE_SIZE = 256
EXTRA = 32
TABLE = 4  # inode table: blocks 4-5
SYSTEM_LABEL = "u:object_r:system_file:s0"
SHELL_LABEL = "u:object_r:shell_exec:s0"


def xattr_entries(attributes: dict, value_base: int, room: int) -> tuple[bytes, bytes]:
    """Entries (ending in four zero bytes) and the values packed at the end of ``room`` bytes, offsets
    counted from ``value_base`` bytes before the entries' start."""
    entries, values = bytearray(), bytearray()
    for (index, name), value in attributes.items():
        values = bytearray(value) + values
        offset = value_base + room - len(values)
        entry = struct.pack("<BBHIII", len(name), index, offset, 0, len(value), 0) + name
        entries += entry + bytes(-len(entry) % 4)
    return bytes(entries) + bytes(4), bytes(values)


class Builder:
    """A small ext4 file system as make_ext4fs lays it out: 4096-byte blocks, one group, extents."""

    def __init__(self, blocks=64, incompat=0x42, ro_compat=0x3):
        self.data = bytearray(blocks * BS)
        self.next_block = 8
        struct.pack_into("<11I", self.data, 1024, INODES, blocks, 0, 0, 0, 0, 2, 2, 32768, 32768, INODES)
        struct.pack_into("<H", self.data, 1024 + 0x38, ext4image.EXT4_MAGIC)
        struct.pack_into("<I", self.data, 1024 + 0x4C, 1)
        struct.pack_into("<H", self.data, 1024 + 0x58, INODE_SIZE)
        struct.pack_into("<III", self.data, 1024 + 0x5C, 0x38, incompat, ro_compat)
        struct.pack_into("<I", self.data, BS + 8, TABLE)

    def allocate(self, data: bytes) -> tuple[int, int]:
        count = max(1, -(-len(data) // BS))
        first = self.next_block
        self.data[first * BS:first * BS + len(data)] = data
        self.next_block += count
        return first, count

    @staticmethod
    def leaves(extents) -> bytes:
        return b"".join(struct.pack("<IHHI", logical, length + (32768 if uninitialised else 0), physical >> 32,
                                    physical & 0xFFFFFFFF) for logical, length, physical, uninitialised in extents)

    def extent_root(self, extents, depth=0) -> bytes:
        if depth == 0:
            return struct.pack("<HHHHI", ext4image.EXTENT_MAGIC, len(extents), 4, 0, 0) + self.leaves(extents)
        leaf = struct.pack("<HHHHI", ext4image.EXTENT_MAGIC, len(extents), (BS - 12) // 12, 0, 0) + self.leaves(extents)
        block, _ = self.allocate(leaf)
        return struct.pack("<HHHHI", ext4image.EXTENT_MAGIC, 1, 4, 1, 0) + struct.pack("<IIHH", 0, block, 0, 0)

    def inode(self, number, mode, size=0, iblock=b"", flags=ext4image.EXTENTS_FL, uid=0, gid=0, links=1,
              mtime=1700000000, epoch=0, label=SYSTEM_LABEL, block_label=False):
        raw = bytearray(INODE_SIZE)
        struct.pack_into("<HHI", raw, 0, mode, uid & 0xFFFF, size & 0xFFFFFFFF)
        struct.pack_into("<I", raw, 16, mtime & 0xFFFFFFFF)
        struct.pack_into("<HH", raw, 24, gid & 0xFFFF, links)
        struct.pack_into("<I", raw, 32, flags)
        raw[40:40 + len(iblock)] = iblock
        struct.pack_into("<I", raw, 108, size >> 32)
        struct.pack_into("<HH", raw, 120, uid >> 16, gid >> 16)
        struct.pack_into("<H", raw, 128, EXTRA)
        struct.pack_into("<I", raw, 0x88, epoch)
        attributes = {} if label is None else {(ext4image.SELINUX_INDEX, ext4image.SELINUX_NAME): label.encode() + b"\0"}
        if block_label:
            block = bytearray(BS)
            struct.pack_into("<III", block, 0, ext4image.XATTR_MAGIC, 1, 1)
            entries, values = xattr_entries(attributes, 0, BS)
            block[32:32 + len(entries)] = entries
            block[BS - len(values):] = values
            struct.pack_into("<I", raw, 104, self.allocate(bytes(block))[0])
        elif attributes:
            room = INODE_SIZE - 128 - EXTRA - 4
            entries, values = xattr_entries(attributes, 0, room)
            body = struct.pack("<I", ext4image.XATTR_MAGIC) + entries
            raw[128 + EXTRA:128 + EXTRA + len(body)] = body
            raw[INODE_SIZE - len(values):] = values
        at = TABLE * BS + (number - 1) * INODE_SIZE
        self.data[at:at + INODE_SIZE] = raw

    def file(self, number, content: bytes, mode=0o100644, **options):
        first, count = self.allocate(content)
        self.inode(number, mode, len(content), self.extent_root([(0, count, first, False)]), **options)

    def directory(self, number, own, parent, children, mode=0o40755, **options):
        block = bytearray()
        names = [(b".", own), (b"..", parent)] + [(name.encode(), child) for name, child in children]
        for position, (name, child) in enumerate(names):
            record = 8 + len(name) + (-(8 + len(name)) % 4)
            if position == len(names) - 1:
                record = BS - len(block)
            block += struct.pack("<IHBB", child, record, len(name), 0) + name + bytes(record - 8 - len(name))
        first, _ = self.allocate(bytes(block))
        self.inode(number, mode, BS, self.extent_root([(0, 1, first, False)]), **options)

    def image(self) -> bytes:
        return bytes(self.data)


SH = bytes(range(256)) * 20  # 5120 bytes: two blocks, the second partly used
PROPERTIES = b"ro.build.fingerprint=test/silvering:6.0.1/MMB29M/1:userdebug/dev-keys\nro.other=1\n"
LONG_TARGET = "/system/" + "x" * 90


def sample(depth=0) -> tuple[Builder, dict]:
    """A tree with what make_ext4fs writes, and the bytes of the file with holes."""
    image = Builder()
    image.directory(2, 2, 2, [("bin", 12), ("build.prop", 15), ("holey", 16), ("long-link", 17),
                              ("lost+found", 11), ("other", 18)], mtime=0)
    image.directory(11, 11, 2, [], mode=0o40700)
    image.directory(12, 12, 2, [("sh", 13), ("ls", 14)], gid=2000, links=2)
    image.file(13, SH, mode=0o100755, gid=2000, label=SHELL_LABEL)
    image.inode(14, 0o120755, 7, b"toolbox", flags=0, gid=2000)
    image.file(15, PROPERTIES)
    data, _ = image.allocate(b"D" * BS)
    holey_size = 4 * BS + 10
    image.inode(16, 0o100644, holey_size,
                image.extent_root([(1, 1, data, False), (2, 1, data, True)], depth=depth))
    first, _ = image.allocate(LONG_TARGET.encode())
    image.inode(17, 0o120777, len(LONG_TARGET), image.extent_root([(0, 1, first, False)]))
    image.file(18, b"other\n", uid=70000, gid=70001, epoch=1, mtime=-5, block_label=True)
    holey = bytes(BS) + b"D" * BS + bytes(2 * BS + 10)
    return image, {"holey": holey}


def entries(raw: bytes, mount="/system", hash_files=True) -> list[dict]:
    return ext4image.read_entries(io.BytesIO(raw), len(raw), mount, hash_files)


def sha1(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def to_sparse(raw: bytes, kinds: dict[int, int]) -> bytes:
    """``raw`` as an Android sparse image; ``kinds`` maps a block to FILL or DONT_CARE (default RAW)."""
    blocks = len(raw) // BS
    chunks: list[tuple[int, int, int]] = []
    for block in range(blocks):
        kind = kinds.get(block, ext4image.CHUNK_RAW)
        if chunks and chunks[-1][0] == kind and kind != ext4image.CHUNK_FILL:
            chunks[-1] = (kind, chunks[-1][1], chunks[-1][2] + 1)
        else:
            chunks.append((kind, block, 1))
    body = bytearray()
    for kind, first, count in chunks:
        data = {ext4image.CHUNK_RAW: raw[first * BS:(first + count) * BS],
                ext4image.CHUNK_FILL: raw[first * BS:first * BS + 4], ext4image.CHUNK_DONT_CARE: b""}[kind]
        body += struct.pack("<HHII", kind, 0, count, 12 + len(data)) + data
    return struct.pack("<IHHHHIIII", ext4image.SPARSE_MAGIC, 1, 0, 28, 12, BS, blocks, len(chunks), 0) + body


class Reader(unittest.TestCase):
    def test_every_entry_as_a_mounted_file_system_shows_it(self):
        image, contents = sample()
        found = {entry["path"]: entry for entry in entries(image.image())}
        self.assertEqual(sorted(found), ["/system", "/system/bin", "/system/bin/ls", "/system/bin/sh",
                                         "/system/build.prop", "/system/holey", "/system/long-link",
                                         "/system/lost+found", "/system/other"])
        self.assertEqual(found["/system"], {"path": "/system", "inode": 2, "mode": 0o40755, "uid": 0, "gid": 0,
                                            "links": 1, "size": BS, "mtime": 0, "label": SYSTEM_LABEL})
        self.assertEqual(found["/system/bin/sh"]["sha1"], sha1(SH))
        self.assertEqual((found["/system/bin/sh"]["size"], found["/system/bin/sh"]["label"]), (len(SH), SHELL_LABEL))
        self.assertEqual(found["/system/bin/ls"]["target"], "toolbox")
        self.assertEqual(found["/system/long-link"]["target"], LONG_TARGET)
        # A hole and an uninitialised extent read as zeros, and so does the end past the last extent.
        self.assertEqual(found["/system/holey"]["sha1"], sha1(contents["holey"]))
        other = found["/system/other"]
        self.assertEqual((other["uid"], other["gid"], other["mtime"], other["label"]),
                         (70000, 70001, (1 << 32) - 5, SYSTEM_LABEL))
        self.assertEqual(found["/system/lost+found"]["mode"], 0o40700)
        self.assertNotIn("sha1", entries(image.image(), hash_files=False)[3])

    def test_an_index_node_leads_to_the_same_bytes(self):
        image, contents = sample(depth=1)
        holey = [entry for entry in entries(image.image()) if entry["path"] == "/system/holey"][0]
        self.assertEqual(holey["sha1"], sha1(contents["holey"]))

    def test_a_sparse_image_reads_the_same_and_its_undefined_blocks_never_as_data(self):
        image, _ = sample()
        raw = image.image()
        used = image.next_block
        free = {block: ext4image.CHUNK_DONT_CARE for block in range(used, len(raw) // BS)}
        zero_blocks = {2: ext4image.CHUNK_FILL, 3: ext4image.CHUNK_FILL}  # bitmaps: all zero here
        self.assertEqual(entries(to_sparse(raw, {**free, **zero_blocks})), entries(raw))
        sh_block = [block for block in range(8, used) if raw[block * BS:block * BS + 256] == SH[:256]][0]
        with self.assertRaises(ext4image.Unknown):
            entries(to_sparse(raw, {**free, sh_block: ext4image.CHUNK_DONT_CARE}))

    def test_layouts_it_does_not_read_are_refused(self):
        cases = {}
        for name, incompat, ro_compat in (("64bit", 0x42 | 0x80, 0x3), ("inline data", 0x42 | 0x8000, 0x3),
                                          ("metadata_csum", 0x42, 0x3 | 0x400), ("bigalloc", 0x42, 0x3 | 0x200)):
            image = Builder(incompat=incompat, ro_compat=ro_compat)
            image.directory(2, 2, 2, [])
            cases[name] = image
        image, _ = sample()
        image.inode(16, 0o100644, 2 * BS, image.extent_root([(0, 2, 20, False), (1, 1, 30, False)]))
        cases["overlapping extents"] = image
        image, _ = sample()
        image.inode(16, 0o100644, 10, struct.pack("<I", 20), flags=0)
        cases["a block map"] = image
        image, _ = sample()
        image.inode(16, 0o100644, BS, image.extent_root([(0, 1, 63, False), (1, 5, 62, False)]))
        cases["an extent past the end"] = image
        image, _ = sample()
        image.directory(12, 12, 2, [("sh", 13), ("again", 2)], gid=2000)
        cases["a second name for a folder"] = image
        image, _ = sample()
        image.data[TABLE * BS + 15 * INODE_SIZE:TABLE * BS + 15 * INODE_SIZE + 2] = struct.pack("<H", 0o170000)
        cases["an invalid type"] = image
        for name, image in cases.items():
            with self.subTest(name), self.assertRaises(ValueError):
                entries(image.image())

    def test_a_cut_off_sparse_image_is_refused(self):
        image, _ = sample()
        sparse = to_sparse(image.image(), {})
        for cut in (sparse[:-1], sparse + b"\0"):
            with self.subTest(length=len(cut)), self.assertRaises(ValueError):
                entries(cut)


if __name__ == "__main__":
    unittest.main()
