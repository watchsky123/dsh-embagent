"""``hw_velxio.bridge`` —— 插件的 Python 侧统一 JSON 入口。

契约（与 ``hwforge.blocks_cli`` 完全一致）：**一次调用、一行 JSON**。

    python -m hw_velxio.bridge status
    python -m hw_velxio.bridge catalog
    python -m hw_velxio.bridge run <project> <capability> <params-json>
    python -m hw_velxio.bridge velxio_health
    python -m hw_velxio.bridge velxio_boards
    python -m hw_velxio.bridge velxio_compile '<params-json>'

**三种状态**：``ok``（工作完成）/ ``not_ready``（前置缺失或后端不可达，属于顺序
/环境问题）/ ``failed``（跑了但出错）。

能力分两个引擎：

* ``engine: "hwforge"`` —— 直接复用 ``hwforge.blocks`` 的 14 个积木，跨层判定。
* ``engine: "velxio"``  —— 通过 HTTP 调运行中的 Velxio 后端，真实编译/芯片/库/电路。

实时仿真的串口流需要 WebSocket，由 **Node 宿主半**直接连 Velxio
（``/api/simulation/ws/{client_id}``），不经过这里——Python 侧只做同步 HTTP 能力，
避免在桥接层里塞一套 WS 客户端造成第二个事实源。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from . import __version__
from . import velxio_client

# ---------------------------------------------------------------------------
# 根定位：插件要能在任意 checkout 下工作，不能写死路径。
# ---------------------------------------------------------------------------

ROOT_MARKER = Path("hwforge") / "__init__.py"


def _module_dir() -> Path | None:
    try:
        here = Path(__file__).resolve().parent
        return here
    except Exception:
        return None


def hwforge_root() -> str:
    """按优先级解析 hwforge checkout 根，并用 marker 校验。"""
    candidates: list[str] = []
    env = os.environ.get("HWFORGE_ROOT", "").strip()
    if env:
        candidates.append(env)
    cfg = velxio_client.load_config().get("hwforge_root")
    if isinstance(cfg, str) and cfg.strip():
        candidates.append(cfg.strip())
    cwd = Path.cwd()
    candidates.append(str(cwd / "hwforge"))
    candidates.append(str(cwd))
    here = _module_dir()
    if here is not None:
        candidates.append(str(here.parent))  # hw_velxio/ -> 仓库根
    for cand in candidates:
        p = Path(cand)
        if (p / ROOT_MARKER).is_file():
            return str(p)
    # 都不合法时返回第一个候选，让调用方在错误里看到它，而不是空串。
    return candidates[0] if candidates else str(cwd)


def project_root() -> Path:
    """工程住的目录 —— **委托给 `design.store.project_root()`**。

    这里曾经自己算一份（`<插件目录>/project`），而工程在 `store.py` 迁到
    工作区之后没有跟着改：于是 `hw_block` 一直在**插件目录里的一个空文件夹**
    上跑 hwforge 积木，读不到任何 `scene.json`，而错误信息说的是"先跑会产出
    它们的块"——把路径错位伪装成了执行顺序问题。

    单一事实源：工作区只有 `design/workspace.py` 一处解析。
    """
    from .design.store import project_root as _design_project_root
    return _design_project_root()


# ---------------------------------------------------------------------------
# 统一能力目录（unified catalogue）
# ---------------------------------------------------------------------------

def _hwforge_catalogue() -> list[dict[str, Any]]:
    """复用 hwforge 自己的积木目录，绝不复制一份（避免第二事实源）。"""
    root = hwforge_root()
    if root not in sys.path:
        sys.path.insert(0, root)
    try:
        from hwforge.blocks.catalogue import catalogue  # type: ignore
        return [
            {"engine": "hwforge", **entry}
            for entry in catalogue()
        ]
    except Exception as exc:  # noqa: BLE001
        return [{
            "engine": "hwforge", "name": "__unavailable__",
            "summary": f"hwforge 不可导入：{exc}", "needs": [], "produces": [], "params": [],
        }]


# Velxio 侧能力：HTTP 可完成的同步能力。仿真 WS 流不在此列（Node 宿主半负责）。
VELXIO_CAPABILITIES: list[dict[str, Any]] = [
    {
        "engine": "velxio", "name": "velxio_compile",
        "summary": "真实编译：Arduino/ESP-IDF 源码 -> hex/bin（调 Velxio 后端）",
        "needs": [], "produces": ["velxio_compile.json"],
        "params": ["files", "board", "async"],
    },
    {
        "engine": "velxio", "name": "velxio_chip",
        "summary": "自定义芯片编译：C -> WebAssembly（调 Velxio /api/compile-chip）",
        "needs": [], "produces": ["velxio_chip.json"],
        "params": ["files"],
    },
    {
        "engine": "velxio", "name": "velxio_rom",
        "summary": "ROM 汇编：8080/Z80 -> 镜像（调 Velxio /api/compile-rom）",
        "needs": [], "produces": ["velxio_rom.json"],
        "params": ["files"],
    },
    {
        "engine": "velxio", "name": "velxio_libs",
        "summary": "Arduino / MicroPython 库搜索与安装（调 Velxio /api/libraries、/api/micropython-libs）",
        "needs": [], "produces": ["velxio_libs.json"],
        "params": ["query", "name", "version", "kind"],
    },
    {
        "engine": "velxio", "name": "velxio_circuit",
        "summary": "电路：创建/更新/导入/导出 Wokwi diagram.json（复用 Velxio 的 wokwi 纯计算）",
        "needs": [], "produces": ["circuit.json"],
        "params": ["action", "diagram", "files"],
    },
]


def catalog() -> dict[str, Any]:
    return {
        "version": __version__,
        "engines": ["hwforge", "velxio"],
        "hwforge_root": hwforge_root(),
        "velxio_url": velxio_client.velxio_url(),
        "capabilities": [*_hwforge_catalogue(), *VELXIO_CAPABILITIES],
    }


# ---------------------------------------------------------------------------
# 执行
# ---------------------------------------------------------------------------

def _emit(obj: dict[str, Any]) -> None:
    """唯一写 stdout 的地方：一行 JSON，前后不加任何噪音。"""
    sys.stdout.write(json.dumps(obj, ensure_ascii=False, default=str) + "\n")
    sys.stdout.flush()


def _run_hwforge(project: str, name: str, params: dict[str, Any]) -> dict[str, Any]:
    root = hwforge_root()
    if root not in sys.path:
        sys.path.insert(0, root)
    from hwforge.blocks import BlockError, BlockFailed, BlockNotReady, run_block  # type: ignore
    from hwforge.blocks.catalogue import BY_NAME  # type: ignore
    blk = BY_NAME.get(name)
    if blk is None:
        return {"status": "failed", "block": name, "findings": [f"未知 hwforge 积木：{name}"]}
    target = project_root() / project
    target.mkdir(parents=True, exist_ok=True)
    try:
        result = run_block(blk, target, params)
    except BlockNotReady as exc:
        return {"status": "not_ready", "block": name, "findings": [str(exc)]}
    except BlockError as exc:
        return {"status": "failed", "block": name, "findings": [str(exc)]}
    return {
        "status": "ok" if result.ok else "failed",
        "engine": "hwforge", "block": result.block,
        "wrote": list(result.wrote), "findings": list(result.findings),
        "data": result.data, "ms": result.ms,
    }


def _run_velxio(name: str, params: dict[str, Any]) -> dict[str, Any]:
    """执行一个 velxio 能力，把返回原样落盘为 JSON 产物。"""
    health = velxio_client.health()
    if not health.get("reachable"):
        return {
            "status": "not_ready", "engine": "velxio", "block": name,
            "findings": [f"Velxio 后端不可达（{health.get('url')}）：{health.get('error')}"],
        }
    try:
        if name == "velxio_compile":
            out = velxio_client.compile_start(
                params["files"], params.get("board", "arduino:avr:uno"))
        elif name == "velxio_chip":
            out = velxio_client.compile_chip(params["files"])
        elif name == "velxio_rom":
            out = velxio_client.compile_rom(params["files"])
        elif name == "velxio_libs":
            if params.get("query"):
                out = velxio_client.lib_search(params["query"])
            elif params.get("name"):
                out = velxio_client.lib_install(params["name"], params.get("version"))
            else:
                out = velxio_client.lib_list()
        elif name == "velxio_circuit":
            out = _circuit(params)
        else:
            return {"status": "failed", "engine": "velxio", "block": name,
                    "findings": [f"未知 velxio 能力：{name}"]}
    except Exception as exc:  # noqa: BLE001
        return {"status": "failed", "engine": "velxio", "block": name,
                "findings": [f"{type(exc).__name__}: {exc}"[:800]]}
    wrote = f"{name}.json"
    Path(project_root() / params.get("project", "_shared")).mkdir(parents=True, exist_ok=True)
    (project_root() / params.get("project", "_shared") / wrote).write_text(
        json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return {
        "status": "ok", "engine": "velxio", "block": name,
        "wrote": [wrote], "findings": [], "data": out,
    }


def _circuit(params: dict[str, Any]) -> dict[str, Any]:
    """电路 JSON ↔ Wokwi diagram.json。

    优先复用 Velxio 的 ``app.mcp.wokwi`` 纯计算（如果 velxio checkout 在
    ``HW_VELXIO_VELXIO_ROOT`` 上）；否则把 action 原样透传给一个可选的
    velxio 侧 helper，最后才如实报 not_ready——**不重实现 diagram 解析**。
    """
    velxio_root = os.environ.get("HW_VELXIO_VELXIO_ROOT", "").strip()
    if velxio_root and velxio_root not in sys.path:
        sys.path.insert(0, velxio_root)
    try:
        from app.mcp.wokwi import (  # type: ignore
            format_wokwi_diagram, generate_arduino_sketch, parse_wokwi_diagram,
        )
        action = params.get("action", "export")
        if action == "import" and params.get("diagram"):
            return {"action": action, "circuit": parse_wokwi_diagram(params["diagram"])}
        if action == "export" and params.get("diagram"):
            return {"action": action, "wokwi": format_wokwi_diagram(params["diagram"])}
        if action == "sketch" and params.get("diagram"):
            return {"action": action, "sketch": generate_arduino_sketch(params["diagram"])}
        return {"action": action, "error": "需要 action 与 diagram 参数"}
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "not_ready",
            "findings": [
                f"无法复用 Velxio 的 wokwi 计算（velxio_root={velxio_root or '未设置'}）：{exc}"],
        }


def _run(project: str, name: str, params: dict[str, Any]) -> dict[str, Any]:
    catalogue = [*_hwforge_catalogue(), *VELXIO_CAPABILITIES]
    entry = next((c for c in catalogue if c.get("name") == name), None)
    if entry is None:
        # **hwforge 不可用时要说清楚是"缺 hwforge"还是"名字写错了"**。
        # 两者都报"未知能力"会让人去查代码，而问题在环境。
        if any(c.get("name") == "__unavailable__" for c in catalogue):
            return {"status": "not_ready", "block": name,
                    "findings": [
                        f"hwforge 不可用，无法运行它的积木 {name}。",
                        "hw_velxio 自己的能力不受影响（design_cli 的全部命令都可用）；"
                        "hwforge 积木是**可选增强**，需要单独 clone hwforge 并设 "
                        "HWFORGE_ROOT。",
                    ]}
        return {"status": "failed", "block": name,
                "findings": [f"未知能力：{name}；用 catalog 查看可用能力"]}
    if entry.get("engine") == "hwforge":
        return _run_hwforge(project, name, params)
    return _run_velxio(name, {**params, "project": project})


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    if not args or args[0] in ("-h", "--help"):
        _emit({
            "usage": "status | catalog | run <project> <capability> [params-json] | "
                     "velxio_health | velxio_boards",
            "version": __version__,
        })
        return 0

    cmd = args[0]
    if cmd == "status":
        hf = _hwforge_catalogue()
        _emit({
            "status": "ok", "version": __version__,
            "hwforge_root": hwforge_root(),
            "hwforge_capabilities": len([c for c in hf if c.get("name") != "__unavailable__"]),
            "hwforge_available": not (len(hf) == 1 and hf[0].get("name") == "__unavailable__"),
            "velxio": velxio_client.health(),
            "projects": [
                {"id": p.name, "path": str(p)}
                for p in sorted(project_root().iterdir()) if p.is_dir()
            ] if project_root().is_dir() else [],
        })
        return 0
    if cmd == "catalog":
        _emit(catalog())
        return 0
    if cmd == "velxio_health":
        _emit(velxio_client.health())
        return 0
    if cmd == "velxio_boards":
        try:
            _emit({"status": "ok", "boards": velxio_client.boards()})
        except velxio_client.VelxioError as exc:
            _emit({"status": "not_ready", "findings": [str(exc)]})
            return 1
        return 0
    if cmd == "run":
        if len(args) < 3:
            _emit({"status": "failed", "findings": ["run 需要 <project> <capability>"]})
            return 1
        project, name = args[1], args[2]
        params = json.loads(args[3]) if len(args) > 3 and args[3].strip() else {}
        out = _run(project, name, params)
        _emit(out)
        return 0 if out.get("status") == "ok" else 1

    _emit({"status": "failed", "findings": [f"未知命令：{cmd}"]})
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
