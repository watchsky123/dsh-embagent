"""Search the DSH app.asar for a byte pattern and print matching file names + snippets.

Usage: python hw_velxio/_tools/asar_find.py <pattern> [--max-files N] [--snippet N]
"""
import json
import struct
import sys

ASAR = r"D:/AI_IDE/deepseek/resources/app.asar"


def read_header(f):
    """Return (header json, byte offset where file data begins)."""
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
    pattern = sys.argv[1].encode()
    max_files = 20
    snippet = 1200
    args = sys.argv[2:]
    for i, a in enumerate(args):
        if a == "--max-files":
            max_files = int(args[i + 1])
        if a == "--snippet":
            snippet = int(args[i + 1])
    with open(ASAR, "rb") as f:
        header, data_base = read_header(f)
        entries = list(walk(header))
        shown = 0
        for name, meta in entries:
            off = meta.get("offset")
            if off is None:
                continue
            size = int(meta["size"])
            if size == 0 or size > 60_000_000:
                continue
            f.seek(data_base + int(off))
            buf = f.read(size)
            if pattern not in buf:
                continue
            shown += 1
            print(f"\n===== {name}  size={size}  hits={buf.count(pattern)}")
            hit = buf.find(pattern)
            lo = max(0, hit - snippet // 3)
            print(buf[lo:lo + snippet].decode("utf-8", "replace"))
            if shown >= max_files:
                print(f"\n[stopped after {shown} files]")
                return


if __name__ == "__main__":
    main()
