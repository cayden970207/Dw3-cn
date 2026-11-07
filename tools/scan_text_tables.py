#!/usr/bin/env python3
"""Read-only DW2003 text table inventory from a user-owned disc image or ZIP.

No game text or binary bytes are printed by default. --private-dump writes raw
entry bytes locally for later codec work; NEVER commit that output. The tables,
file IDs and executable offsets are *candidates* from the EU recomp reference,
not verified against the user's modified Chinese edition until scanned.

This tool does not write or modify a game image or prove gameplay compatibility.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import sys
import tempfile
import zipfile

SECTOR_USER_BYTES = 2048
MAX_IMAGE_BYTES = 2_000_000_000
MAX_DIR_BYTES = 8_388_608
MAX_RESOURCE_BYTES = 33_554_432
FILE_COUNT = 2382
EXE_LOAD_OFFSET = 0x80010000 - 0x800
FILETABLE_LBA_OFFSET = 0x80044F6C - EXE_LOAD_OFFSET
FILETABLE_SECTORS_OFFSET = 0x800474A4 - EXE_LOAD_OFFSET
LANGUAGE_OFFSET = 2  # 0=JPN, 2=ENG in the EU reference build
TEXT_IDS = tuple(1 + 7 * n + LANGUAGE_OFFSET for n in range(50))
CONTAINER_FIRST = 0x112 + LANGUAGE_OFFSET
MAX_ENTRIES = 4999


class DiscError(ValueError):
    """A structural ambiguity or invalid disc layout; no write is attempted."""


def u16(data: bytes, off: int) -> int:
    if off < 0 or off + 2 > len(data):
        raise DiscError("u16 outside buffer")
    return struct.unpack_from("<H", data, off)[0]


def u32(data: bytes, off: int) -> int:
    if off < 0 or off + 4 > len(data):
        raise DiscError("u32 outside buffer")
    return struct.unpack_from("<I", data, off)[0]


def hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fp:
        for chunk in iter(lambda: fp.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


@contextmanager
def local_image(path: Path, member: str | None = None):
    """Yield a local raw-image path. ZIP members are streamed into an ephemeral,
    size-capped file, deleted even on error; never extract by archive pathname."""
    path = Path(path)
    if not zipfile.is_zipfile(path):
        if member:
            raise DiscError("--member is only valid for ZIP archives")
        if not 0 < path.stat().st_size <= MAX_IMAGE_BYTES:
            raise DiscError("Image empty or exceeds size cap")
        yield path
        return
    with zipfile.ZipFile(path) as archive:
        choices = [x for x in archive.infolist() if not x.is_dir() and
                   Path(x.filename).suffix.lower() in (".iso", ".bin", ".img")]
        if member:
            choices = [x for x in choices if x.filename == member]
        if len(choices) != 1:
            raise DiscError("ZIP requires exactly one selected ISO/BIN/IMG member; use --member")
        chosen = choices[0]
        if chosen.flag_bits & 1 or not 0 < chosen.file_size <= MAX_IMAGE_BYTES:
            raise DiscError("Encrypted, empty or oversized ZIP image is unsupported")
        with tempfile.TemporaryDirectory(prefix="dw3-local-scan-") as folder:
            dst = Path(folder) / "user-disc.img"
            total = 0
            with archive.open(chosen) as source, dst.open("xb") as out:
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_IMAGE_BYTES:
                        raise DiscError("ZIP image exceeds size cap during decoding")
                    out.write(chunk)
            if total != chosen.file_size:
                raise DiscError("ZIP image uncompressed size mismatch")
            yield dst


class Disc:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.size = self.path.stat().st_size
        self.sector_bytes, self.user_offset = self._detect()

    def _detect(self):
        with self.path.open("rb") as fp:
            for raw, offset in ((2352, 24), (2352, 16), (2048, 0)):
                if self.size % raw:
                    continue
                fp.seek(16 * raw + offset)
                if fp.read(7) == b"\x01CD001\x01":
                    return raw, offset
        raise DiscError("ISO9660 primary volume descriptor not found at LBA 16")

    @property
    def sector_count(self) -> int:
        return self.size // self.sector_bytes

    def read_user(self, lba: int, size: int) -> bytes:
        if type(lba) is not int or type(size) is not int or lba < 0 or size < 0:
            raise DiscError("Invalid LBA or size")
        if size > MAX_RESOURCE_BYTES:
            raise DiscError("Requested file exceeds scan cap")
        count = (size + SECTOR_USER_BYTES - 1) // SECTOR_USER_BYTES
        if lba + count > self.sector_count:
            raise DiscError("File extent outside image")
        out = bytearray()
        with self.path.open("rb") as fp:
            for n in range(count):
                fp.seek((lba + n) * self.sector_bytes + self.user_offset)
                block = fp.read(SECTOR_USER_BYTES)
                if len(block) != SECTOR_USER_BYTES:
                    raise DiscError("Truncated sector")
                out.extend(block)
        return bytes(out[:size])


def iso9660_files(disc: Disc) -> dict[int, dict]:
    """Read directory path table so even a hidden AAA/ folder is traversed."""
    pvd = disc.read_user(16, SECTOR_USER_BYTES)
    if pvd[:7] != b"\x01CD001\x01":
        raise DiscError("Not ISO9660 PVD")
    pt_size, pt_lba = u32(pvd, 132), u32(pvd, 140)
    if pt_size < 10 or pt_size > 1024 * 1024:
        raise DiscError("Invalid ISO path table size")
    pt = disc.read_user(pt_lba, pt_size)
    dirs = []
    cursor = 0
    while cursor < len(pt):
        if cursor + 8 > len(pt):
            raise DiscError("Truncated ISO path table")
        n = pt[cursor]
        if n == 0 or cursor + 8 + n > len(pt):
            raise DiscError("Invalid ISO path table name length")
        extent = u32(pt, cursor + 2)
        parent = u16(pt, cursor + 6)
        raw_name = pt[cursor + 8:cursor + 8 + n]
        if not dirs:
            if raw_name != b"\x00" or parent != 1:
                raise DiscError("Path table must start with root")
            name = ""
        else:
            if not 1 <= parent <= len(dirs):
                raise DiscError("Invalid path table parent")
            if b"/" in raw_name or b"\\" in raw_name or raw_name in (b".", b".."):
                raise DiscError("Unsafe ISO path name")
            name = (dirs[parent - 1][0] + "/" + raw_name.decode("latin1")).lstrip("/")
        dirs.append((name, extent))
        if len(dirs) > 4096:
            raise DiscError("Too many ISO directories")
        cursor += 8 + n + (n & 1)
    found: dict[int, dict] = {}
    for folder, extent in dirs:
        header = disc.read_user(extent, SECTOR_USER_BYTES)
        rec_len = header[0]
        if rec_len < 34:
            raise DiscError("Directory has no valid dot record")
        size = u32(header, 10)
        if size < SECTOR_USER_BYTES or size > MAX_DIR_BYTES:
            raise DiscError("Invalid directory extent size")
        raw = disc.read_user(extent, size)
        pos = 0
        while pos < len(raw):
            length = raw[pos]
            if length == 0:
                pos = (pos // 2048 + 1) * 2048
                continue
            if length < 34 or pos + length > len(raw):
                raise DiscError("Malformed directory record")
            record = raw[pos:pos + length]
            name_len = record[32]
            if 33 + name_len > length:
                raise DiscError("Malformed directory file name")
            name = record[33:33 + name_len]
            if name not in (b"\x00", b"\x01") and not (record[25] & 2):
                if b"/" in name or b"\\" in name:
                    raise DiscError("Unsafe file name in directory record")
                location = u32(record, 2)
                file_size = u32(record, 10)
                if location in found:
                    raise DiscError("Duplicate file extent in ISO")
                found[location] = {
                    "path": (folder + "/" if folder else "") +
                            name.decode("latin1").split(";", 1)[0],
                    "size_bytes": file_size,
                }
            pos += length
    if not found:
        raise DiscError("No ISO files discovered")
    return found


def get_filetable(disc: Disc, iso_files: dict[int, dict]) -> dict:
    exes = [(lba, f) for lba, f in iso_files.items()
            if f["path"].upper().split("/")[-1] == "SLES_039.36"]
    if len(exes) != 1:
        raise DiscError("Expected exactly one SLES_039.36 executable")
    lba, item = exes[0]
    if item["size_bytes"] > MAX_RESOURCE_BYTES:
        raise DiscError("Executable exceeds size cap")
    data = disc.read_user(lba, item["size_bytes"])
    if not data.startswith(b"PS-X EXE"):
        raise DiscError("PS1 executable header missing")
    if len(data) < FILETABLE_SECTORS_OFFSET + 2 * FILE_COUNT:
        raise DiscError("PS1 executable too short for EU reference filetable offsets")
    lbas = struct.unpack_from(f"<{FILE_COUNT}I", data, FILETABLE_LBA_OFFSET)
    sectors = struct.unpack_from(f"<{FILE_COUNT}H", data, FILETABLE_SECTORS_OFFSET)
    valid = [(i, x, sectors[i]) for i, x in enumerate(lbas) if x and sectors[i]]
    matches = sum(1 for _, x, count in valid if x in iso_files and
                  (iso_files[x]["size_bytes"] + 2047) // 2048 == count)
    ratio = matches / len(valid) if valid else 0
    return {
        "exe_sha256": hashlib.sha256(data).hexdigest(),
        "exe_path": item["path"],
        "filetable_candidates": len(valid),
        "filetable_matches": matches,
        "filetable_match_ratio": round(ratio, 6),
        "reference_offsets_supported": len(valid) > 2000 and ratio >= 0.97,
        "lbas": lbas,
        "sectors": sectors,
    }


def table_entries(data: bytes, base: int = 0, end: int | None = None):
    """Validate a counted string table and return byte ranges including NUL.
    Never assume ASCII, UTF-8, or vanilla EU font on a Chinese-modified image."""
    end = len(data) if end is None else end
    if base < 0 or end > len(data) or base + 8 > end:
        raise DiscError("String table outside subfile")
    count = u32(data, base)
    if not 0 < count <= MAX_ENTRIES or base + 4 + count * 4 > end:
        raise DiscError("Invalid string table count")
    offsets = [u32(data, base + 4 + i * 4) for i in range(count)]
    if offsets[0] != 4 + count * 4:
        raise DiscError("First string offset differs from table header size")
    if any(x <= 0 or base + x >= end for x in offsets):
        raise DiscError("String offsets outside table")
    if any(a >= b for a, b in zip(offsets, offsets[1:])):
        raise DiscError("String offsets are not strictly increasing")
    result = []
    for i, off in enumerate(offsets):
        begin = base + off
        limit = base + offsets[i + 1] if i + 1 < count else end
        terminal = data.find(b"\x00", begin, limit)
        if terminal < 0:
            raise DiscError(f"String {i} is not terminated before next entry")
        result.append((begin, terminal + 1))
    return result


def read_text_file(data: bytes, is_container: bool):
    if not is_container:
        return [(None, table_entries(data))]
    if len(data) < 8:
        raise DiscError("Container header too short")
    header_bytes = u32(data, 0)
    if header_bytes % 4 or not 4 <= header_bytes <= 4 * 1024:
        raise DiscError("Bad container header size")
    if header_bytes > len(data):
        raise DiscError("Truncated container")
    count = header_bytes // 4
    offsets = [u32(data, 4 * i) for i in range(count)]
    used = [x for x in offsets if x]
    if not used or used[0] != header_bytes:
        raise DiscError("Invalid container subfile header")
    if any(x < header_bytes or x >= len(data) for x in used):
        raise DiscError("Subfile offset outside container")
    if any(a >= b for a, b in zip(used, used[1:])):
        raise DiscError("Subfile offsets not increasing")
    tables = []
    for i, off in enumerate(offsets):
        if not off:
            continue
        following = [x for x in used if x > off]
        limit = following[0] if following else len(data)
        tables.append((i, table_entries(data, off, limit)))
    return tables


def scan(path: Path, member: str | None = None, private: bool = False) -> dict:
    """Emit metadata only by default; only local --private-dump includes bytes."""
    with local_image(Path(path), member) as image:
        disc = Disc(image)
        files = iso9660_files(disc)
        exe = get_filetable(disc, files)
        report = {
            "schema_version": 1,
            "gameplay_verified": False,
            "image_sha256": hash_file(image),
            "image_size_bytes": disc.size,
            "sector_bytes": disc.sector_bytes,
            "user_data_offset": disc.user_offset,
            "boot": {
                "path": exe["exe_path"],
                "exe_sha256": exe["exe_sha256"],
                "filetable_candidates": exe["filetable_candidates"],
                "filetable_matches": exe["filetable_matches"],
                "filetable_match_ratio": exe["filetable_match_ratio"],
                "reference_offsets_supported": exe["reference_offsets_supported"],
            },
            "issues": [],
            "text_files": [],
            "note": "EU ID/offset heuristic. Does NOT decode CN glyphs, apply patches, prove translation or run the game.",
        }
        if not exe["reference_offsets_supported"]:
            report["issues"].append("Reference executable filetable did not reach 97% match; verify ID map manually")
        for fid in TEXT_IDS:
            lba = exe["lbas"][fid]
            sectors = exe["sectors"][fid]
            info = files.get(lba)
            if not info:
                report["issues"].append(f"ID {fid:#x}: LBA absent from ISO")
                continue
            parts = info["path"].upper().split("/")
            # English normally lives under COUNTRY/ENG and is named ES*.BIN
            if len(parts) < 3 or parts[-3:-1] != ["COUNTRY", "ENG"] or not (
                    parts[-1].startswith("ES") and parts[-1].endswith(".BIN")):
                report["issues"].append(f"ID {fid:#x}: unexpected EU path {info['path']}")
                continue
            expected_secs = (info["size_bytes"] + 2047) // 2048
            if expected_secs != sectors:
                report["issues"].append(f"ID {fid:#x}: sector size mismatch")
                continue
            if not 0 < info["size_bytes"] <= MAX_RESOURCE_BYTES:
                report["issues"].append(f"ID {fid:#x}: resource exceeds size cap")
                continue
            data = disc.read_user(lba, info["size_bytes"])
            try:
                tables = read_text_file(data, fid >= CONTAINER_FIRST)
            except DiscError as error:
                report["issues"].append(f"ID {fid:#x}: {error}")
                continue
            records = []
            for subfile, rows in tables:
                for index, (start, end) in enumerate(rows):
                    raw = data[start:end]
                    entry = {
                        "table": subfile,
                        "index": index,
                        "raw_sha256": hashlib.sha256(raw).hexdigest(),
                        "encoded_bytes": len(raw),
                    }
                    if private:
                        entry["raw_hex"] = raw.hex()
                    records.append(entry)
            report["text_files"].append({
                "file_id": fid,
                "path": info["path"],
                "file_sha256": hashlib.sha256(data).hexdigest(),
                "file_size_bytes": len(data),
                "container": fid >= CONTAINER_FIRST,
                "table_count": len(tables),
                "entry_count": len(records),
                "entries": records,
            })
        report["total_text_files"] = len(report["text_files"])
        report["total_entries"] = sum(x["entry_count"] for x in report["text_files"])
        report["inventory_complete"] = (len(report["text_files"]) == 50 and
                                        not report["issues"] and
                                        exe["reference_offsets_supported"])
        return report


def write_private(path: Path, data: dict):
    """Create local unencrypted raw-byte dump with restrictive mode; no overwrite."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fp:
        json.dump(data, fp, ensure_ascii=False, indent=2)
        fp.write("\n")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path, help="Uncompressed ISO/BIN/IMG or ZIP with one game image")
    parser.add_argument("--member", help="Exact ZIP member when more than one image is present")
    parser.add_argument("--private-dump", type=Path,
                        help="Write original string bytes to a local, ignored file; NEVER commit it")
    parser.add_argument("--expect", help="Expected SHA-256 of the uncompressed image")
    args = parser.parse_args(argv)
    try:
        report = scan(args.image, args.member, private=args.private_dump is not None)
        if args.expect is not None:
            expect = args.expect.lower()
            if len(expect) != 64 or any(c not in "0123456789abcdef" for c in expect):
                parser.error("--expect must be a 64-char hex SHA-256")
            if report["image_sha256"] != expect:
                raise DiscError("Image SHA-256 mismatch")
        if args.private_dump:
            write_private(args.private_dump, report)
        for item in report["text_files"]:
            item.pop("entries")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["inventory_complete"] else 1
    except (OSError, DiscError, zipfile.BadZipFile, EOFError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
