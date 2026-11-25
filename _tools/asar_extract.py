"""Extract selected entries from the DSH app.asar into a local reference tree.

Usage:
  python dsh_embagent/_tools/asar_extract.py <dest-dir> <substring> [<substring> ...]

Only entries whose path contains one of the substrings are written.
"""
import json
import os
import struct
import sys

ASAR = r"D:/AI_IDE/deepseek/resources/app.asar"


def read_header(f):
    d = f.read(16)
    payload = struct.unpack("<I", d[4:8])[0]
    jsz = struct.unpack("<I", d[12:16])[0]
    f.seek(16)
    return json.loads(f.read(jsz)), 8 + payload


def walk(node, prefix=""):
    for k, v in node.get("files", {}).items():
        if "files" in v:
            yield from walk(v, prefix + "/" + k)
        else:
            yield prefix + "/" + k, v


def main():
    dest = sys.argv[1]
    needles = sys.argv[2:]
    with open(ASAR, "rb") as f:
        header, data_base = read_header(f)
        entries = list(walk(header))
        written = 0
        for name, meta in entries:
            if not any(n in name for n in needles):
                continue
            off = meta.get("offset")
            if off is None:
                continue
            out = os.path.join(dest, name.lstrip("/").replace("/", os.sep))
            os.makedirs(os.path.dirname(out), exist_ok=True)
            f.seek(data_base + int(off))
            with open(out, "wb") as g:
                g.write(f.read(int(meta["size"])))
            written += 1
        print(f"wrote {written} files to {dest}")


if __name__ == "__main__":
    main()
