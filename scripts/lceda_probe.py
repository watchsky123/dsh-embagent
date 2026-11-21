"""一次性探测：立创EDA 的 CLI / API 面 / 版本 —— **运维用，不是判定**。

    python -m hw_velxio.scripts.lceda_probe              # 能力快照
    python -m hw_velxio.scripts.lceda_probe --raw P      # 把完整 API 清单落盘

为什么单独一个脚本：`doc api` 有 525 个类型、`doc format` 还有几十个，
全量拉一次要几分钟。发射层只需要用到的那 22 条（`lceda.CAPABILITIES`），
所以默认只核对这 22 条；要看全量再用 `--raw`。

产物写到 ``<工作区>/lceda-probe/``：
  * ``capabilities.json`` —— exe / 版本 / 22 条能力 / 单位换算
  * ``classes.txt``       —— `--raw` 时才有：525 个类型名（ASCII，按名排序）
  * ``methods.json``      —— `--raw` 时才有：类型 → 方法名列表
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

if __package__ in (None, ""):  # 允许直接 python scripts/lceda_probe.py
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from hw_velxio.design import lceda  # noqa: E402


def _out_dir() -> Path:
    root = Path(__file__).resolve().parents[2]
    target = root / "lceda-probe"
    target.mkdir(parents=True, exist_ok=True)
    return target


def dump_capabilities() -> dict[str, Any]:
    started = time.time()
    found = lceda.report()
    found["elapsed_s"] = round(time.time() - started, 1)
    path = _out_dir() / "capabilities.json"
    path.write_text(json.dumps(found, ensure_ascii=False, indent=1), encoding="utf-8")
    found["written"] = str(path)
    return found


def dump_raw() -> dict[str, Any]:
    """把 ``doc api`` 全量拉一遍。慢（几分钟），只给人做对照用。"""
    exe = lceda.find_exe()
    if not exe:
        return {"status": "not_ready", "reason": "没有 lceda-pro"}
    session = lceda.LcedaSession(exe=exe).open(headless=True)
    try:
        surface = session.doc("api")
        types = surface.get("types") or []
        names = sorted(str(t.get("name")) for t in types if t.get("name"))
        out = _out_dir()
        (out / "classes.txt").write_text("\n".join(names), encoding="utf-8")
        methods: dict[str, list[str]] = {}
        for name in names:
            try:
                info = session.doc("api", name)
            except lceda.CliError:
                methods[name] = []
                continue
            methods[name] = [str(m.get("name")) for m in (info.get("methods") or [])]
        (out / "methods.json").write_text(
            json.dumps(methods, ensure_ascii=False, indent=1), encoding="utf-8")
        return {"status": "ok", "types": len(names),
                "written": [str(out / "classes.txt"), str(out / "methods.json")]}
    finally:
        session.close()


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "--raw":
        payload = dump_raw()
    else:
        payload = dump_capabilities()
    blob = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8") + b"\n"
    sys.stdout.buffer.write(blob)
    sys.stdout.buffer.flush()
    return 0 if payload.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())