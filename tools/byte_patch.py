#!/usr/bin/env python3
"""Create and apply safe, hash-locked, same-length binary deltas.

NOT a PS1 text codec; does not fix CD-sector EDC/ECC, pointers, or rendering.
This command never modifies source files and never overwrites an output.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile

BLOCK = 1024 * 1024
MAX_BYTES = 65536
MAX_SPANS = 8192
HEX64 = re.compile(r"[0-9a-f]{64}\Z")


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(BLOCK), b""):
            digest.update(block)
    return digest.hexdigest()


def validate(data):
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ValueError("Unsupported patch schema")
    n = data.get("size_bytes")
    if type(n) is not int or n <= 0:
        raise ValueError("Invalid image size")
    for key in ("source_sha256", "target_sha256"):
        if not isinstance(data.get(key), str) or not HEX64.fullmatch(data[key]):
            raise ValueError("Invalid " + key)
    items = data.get("spans")
    if not isinstance(items, list) or not 1 <= len(items) <= MAX_SPANS:
        raise ValueError("Invalid span list")
    end, total = 0, 0
    for i, entry in enumerate(items):
        if not isinstance(entry, dict):
            raise ValueError("Invalid span")
        offset, before, after = (entry.get(x) for x in ("offset", "before_sha256", "after_hex"))
        if type(offset) is not int or offset < end or offset < 0:
            raise ValueError(f"Overlap or invalid offset at span {i}")
        if not isinstance(before, str) or not HEX64.fullmatch(before):
            raise ValueError("Invalid before_sha256")
        if not isinstance(after, str) or not after or not re.fullmatch(r"(?:[0-9a-f]{2})+", after):
            raise ValueError("Invalid after_hex")
        end = offset + len(after) // 2
        if end > n:
            raise ValueError("Span exceeds image")
        total += len(after) // 2
    if total > MAX_BYTES or data.get("changed_bytes") != total:
        raise ValueError("Changed byte budget/count invalid")
    return data


def make_patch(original, edited, max_bytes=MAX_BYTES):
    original, edited = Path(original), Path(edited)
    if original.resolve() == edited.resolve():
        raise ValueError("Two different files required")
    size = original.stat().st_size
    if size <= 0 or size != edited.stat().st_size:
        raise ValueError("Source and edited image must have equal nonzero lengths")
    if type(max_bytes) is not int or not 0 < max_bytes <= MAX_BYTES:
        raise ValueError("Invalid changed-byte limit")
    spans = []
    h_old, h_new = hashlib.sha256(), hashlib.sha256()
    start = None
    before, after = bytearray(), bytearray()
    changed, pos = 0, 0

    def flush():
        nonlocal start
        if start is None:
            return
        spans.append({"offset": start, "before_sha256": sha256_bytes(before),
                      "after_hex": after.hex()})
        if len(spans) > MAX_SPANS:
            raise ValueError("Too many changed spans")
        start = None
        before.clear()
        after.clear()

    with original.open("rb") as left, edited.open("rb") as right:
        while True:
            a, b = left.read(BLOCK), right.read(BLOCK)
            if not a and not b:
                break
            if len(a) != len(b):
                raise ValueError("Files changed while comparing")
            h_old.update(a)
            h_new.update(b)
            if a == b:
                flush()
            else:
                for i, (x, y) in enumerate(zip(a, b)):
                    if x == y:
                        flush()
                    else:
                        if start is None:
                            start = pos + i
                        before.append(x)
                        after.append(y)
                        changed += 1
                        if changed > max_bytes:
                            raise ValueError("Changed-byte safety limit exceeded")
            pos += len(a)
    flush()
    if original.stat().st_size != pos or edited.stat().st_size != pos:
        raise ValueError("Files changed while comparing")
    if not spans:
        raise ValueError("No changes to create a patch")
    return validate({"schema_version": 1, "size_bytes": pos,
                     "source_sha256": h_old.hexdigest(), "target_sha256": h_new.hexdigest(),
                     "changed_bytes": changed, "spans": spans})


def apply_patch(patch, original, output):
    data = validate(patch)
    original, output = Path(original), Path(output)
    if original.resolve() == output.resolve():
        raise ValueError("Refusing to overwrite input")
    if output.exists():
        raise FileExistsError("Output already exists")
    if original.stat().st_size != data["size_bytes"] or sha256_file(original) != data["source_sha256"]:
        raise ValueError("Source image SHA-256/size mismatch")
    output.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w+b", prefix=".dw3-", dir=output.parent, delete=False) as tmp:
            temp_path = Path(tmp.name)
            with original.open("rb") as source:
                shutil.copyfileobj(source, tmp, length=BLOCK)
            for span in data["spans"]:
                replacement = bytes.fromhex(span["after_hex"])
                tmp.seek(span["offset"])
                previous = tmp.read(len(replacement))
                if sha256_bytes(previous) != span["before_sha256"]:
                    raise ValueError(f"Byte precondition mismatch: {span['offset']}")
                tmp.seek(span["offset"])
                tmp.write(replacement)
            tmp.flush()
            os.fsync(tmp.fileno())
        if temp_path.stat().st_size != data["size_bytes"] or sha256_file(temp_path) != data["target_sha256"]:
            raise ValueError("Target SHA-256 mismatch; output not published")
        if output.exists():
            raise FileExistsError("Output already exists")
        os.replace(temp_path, output)
        temp_path = None
        return {"output": str(output), "sha256": data["target_sha256"],
                "changed_bytes": data["changed_bytes"], "gameplay_verified": False}
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    p_make = sub.add_parser("make")
    for field in ("original", "edited", "patch"):
        p_make.add_argument(field, type=Path)
    p_apply = sub.add_parser("apply")
    for field in ("patch", "original", "output"):
        p_apply.add_argument(field, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.action == "make":
            if args.patch.exists():
                raise FileExistsError("Patch destination exists")
            data = make_patch(args.original, args.edited)
            with args.patch.open("x", encoding="utf-8") as file:
                json.dump(data, file, indent=2)
                file.write("\n")
            print(json.dumps({"status": "created_not_gameplay_verified", "changed_bytes": data["changed_bytes"],
                              "spans": len(data["spans"])}))
        else:
            print(json.dumps(apply_patch(json.loads(args.patch.read_text(encoding="utf-8")),
                                         args.original, args.output)))
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
