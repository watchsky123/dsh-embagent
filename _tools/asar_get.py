"""从 app.asar 里按路径取文件（不整包解压）。"""
import json
import struct
import sys
import pathlib

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


def extract(wanted: str, out: str) -> int:
    with open(ASAR, "rb") as f:
        info, base = read_header(f)
        for path, meta in walk(info):
            if path.lstrip("/") != wanted.lstrip("/"):
                continue
            f.seek(base + int(meta["offset"]))
            blob = f.read(int(meta["size"]))
            pathlib.Path(out).write_bytes(blob)
            print(f"写出 {out}（{len(blob)} B）")
            return 0
    print("没找到:", wanted)
    return 1


def listdir(prefix: str) -> int:
    with open(ASAR, "rb") as f:
        info, _ = read_header(f)
        for path, meta in walk(info):
            if path.lstrip("/").startswith(prefix.lstrip("/")):
                print(f"{int(meta['size']):>9}  {path.lstrip('/')}")
    return 0


if __name__ == "__main__":
    if sys.argv[1] == "ls":
        raise SystemExit(listdir(sys.argv[2]))
    raise SystemExit(extract(sys.argv[1], sys.argv[2]))