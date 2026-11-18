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
    except Exception as exc:  # noqa: BLE001
        # **hwforge 会抛非 BlockError 的异常**，而这里此前只接两种，于是
        # `hw_block(assemble, {"spec": {...}})`（spec 该是路径，给了 dict）
        # 直接把 `TypeError: expected str, bytes or os.PathLike object, not
        # dict` 的 traceback 甩到 stdout —— Node 侧只拿到一坨 Python 堆栈，
        # 既不是结构化结论，也没有"参数类型不对"的提示。
        #
        # 这里兜住全部：保留类型与首行消息（这是最有信息量的部分），
        # 再给一条**针对参数类型的**提示——因为绝大多数这类崩溃都是参数形状错。
        detail = f"{type(exc).__name__}: {exc}"
        findings = [detail[:800]]
        if isinstance(exc, (TypeError, AttributeError, KeyError, ValueError)):
            findings.append(
                "参数形状不对：hwforge 积木的参数由 catalog 声明，"
                "例如 assemble 的 spec 是**文件路径字符串**，不是 JSON 对象；"
                "用 python -m hw_velxio.bridge catalog 查每个积木的 params。")
        return {"status": "failed", "engine": "hwforge", "block": name,
                "findings": findings}
    return {
        "status": "ok" if result.ok else "failed",
        "engine": "hwforge", "block": result.block,
        "wrote": list(result.wrote), "findings": list(result.findings),
        "data": result.data, "ms": result.ms,
    }


def _run_velxio(name: str, params: dict[str, Any]) -> dict[str, Any]:
    """执行一个 velxio 能力。

    **纯计算能力排在健康检查之前**：`velxio_circuit` 只读 checkout 里的
    `app/mcp/wokwi.py`，不需要后端在线。把它也拦在 health 后面，等于
    因为"服务没起"而拒绝一个本来就不用服务的操作——缺失被当成了失败。
    """
    if name == "velxio_circuit":
        out = _circuit(params)
        if out.get("status") != "ok":
            out.setdefault("engine", "velxio")
            out.setdefault("block", name)
            return out
        return _save_velxio_artifact(name, params, out)

    health = velxio_client.health()
    if not health.get("reachable"):
        return {
            "status": "not_ready", "engine": "velxio", "block": name,
            "findings": [
                f"Velxio 后端不可达（{health.get('url')}）：{health.get('error')}",
                "用 hw_velxio action=start 启动本机后端；"
                "纯电路的 velxio_circuit 不需要后端。",
            ],
        }
    try:
        if name == "velxio_compile":
            board_fqbn = str(params.get("board") or params.get("board_fqbn")
                             or "arduino:avr:uno")
            started = velxio_client.compile_start(params["files"], board_fqbn)
            job_id = started.get("job_id") if isinstance(started, dict) else None
            # **异步作业必须等完**。`/compile/start` 只回一个 job_id，
            # 结果（success/hex_content/stderr）在 `/compile/status/{id}`
            # 的 `result` 子对象里。此前这里直接把 `{job_id: ...}` 当成
            # 编译结果返回，于是"编译成功但没有固件"——一个只完成了第一步
            # 却被当作完成了的动作。
            if isinstance(job_id, str) and job_id:
                out = velxio_client.compile_wait(
                    job_id, timeout_s=float(params.get("wait_s") or 300.0))
            else:
                out = started
        elif name == "velxio_chip":
            out = velxio_client.compile_chip(params["files"])
        elif name == "velxio_rom":
            out = velxio_client.compile_rom(params["files"])
        elif name == "velxio_libs":
            out = _libs(params)
        else:
            return {"status": "failed", "engine": "velxio", "block": name,
                    "findings": [f"未知 velxio 能力：{name}"]}
    except Exception as exc:  # noqa: BLE001
        return {"status": "failed", "engine": "velxio", "block": name,
                "findings": [f"{type(exc).__name__}: {exc}"[:800]]}
    return _save_velxio_artifact(name, params, out)


def _libs(params: dict[str, Any]) -> dict[str, Any]:
    """库能力：Arduino 与 MicroPython 双通道。

    `kind` 决定走哪一族接口（`arduino` 默认 / `micropython`）。此前
    `velxio_client` 里写好了 `micropython_search` / `micropython_fetch`，
    但 `bridge.py` 从不调用，等于那两条链是死代码；这里把它们接上。

    action 由参数推断，不额外加一个必填项：
      query → 搜索；name → 安装/获取；都没有 → 列已装。
    """
    kind = str(params.get("kind") or "arduino").strip().lower()
    if kind in ("micropython", "mpy", "python"):
        if params.get("query"):
            return velxio_client.micropython_search(str(params["query"]))
        if params.get("name"):
            return velxio_client.micropython_fetch(
                str(params["name"]), **({"version": params["version"]}
                                        if params.get("version") else {}))
        # MicroPython 没有"列出已装"的接口——如实说，不假装空列表就是全部。
        raise RuntimeError(
            "MicroPython 通道只支持 query（搜索）或 name（获取包）；"
            "没有 list 接口。要列 Arduino 库请用 kind=arduino。")
    if params.get("query"):
        return velxio_client.lib_search(str(params["query"]))
    if params.get("name"):
        return velxio_client.lib_install(str(params["name"]), params.get("version"))
    return velxio_client.lib_list()


def _save_velxio_artifact(name: str, params: dict[str, Any],
                          out: dict[str, Any]) -> dict[str, Any]:
    """把能力输出原样落盘成 `<project>/<name>.json`。

    工程目录不存在时**报 failed 而不是静默建**：`_run_velxio` 拿到的 project
    来自 `hw_block` 的参数，写错名字应当被看见，而不是在工作区里多一个空目录。
    """
    project = str(params.get("project") or "").strip()
    if not project:
        return {"status": "ok", "engine": "velxio", "block": name,
                "wrote": [], "findings": ["没有 project 参数，结果只在本次返回里，未落盘"],
                "data": out}
    target_dir = project_root() / project
    wrote = f"{name}.json"
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        (target_dir / wrote).write_text(
            json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    except OSError as exc:
        return {"status": "failed", "engine": "velxio", "block": name,
                "findings": [f"结果写不进 {target_dir}：{exc}"], "data": out}
    return {
        "status": "ok", "engine": "velxio", "block": name,
        "wrote": [wrote], "findings": [], "data": out,
    }


def _velxio_package_root() -> str:
    """Velxio 的**包根**（`import app` 能成功的那一层）。

    这里曾经把 checkout 根塞进 `sys.path`，但真正的包在 `backend/` 下：
    `<root>/backend/app/mcp/wokwi.py`。于是 `import app.mcp.wokwi` 在根目录
    必然 ModuleNotFoundError，而这个失败被 catch 成一句"velxio_root 未设置"，
    把路径错位伪装成了环境变量没配。

    两级候选，`backend/` 优先；两级都不对时返回空串，让调用方如实报 not_ready。
    """
    from .serve import velxio_root  # 单一事实源：根只由 serve 解析

    root = velxio_root()
    if not root:
        return ""
    for candidate in (Path(root) / "backend", Path(root)):
        if (candidate / "app" / "__init__.py").is_file() or \
                (candidate / "app" / "mcp" / "wokwi.py").is_file():
            return str(candidate)
    return ""


def _circuit(params: dict[str, Any]) -> dict[str, Any]:
    """电路 JSON ↔ Wokwi diagram.json。

    这是**纯计算**（不需要 Velxio 后端在线），只依赖 checkout 里的
    ``app/mcp/wokwi.py`` 三个纯函数。因此它在 `_run` 里排在健康检查**之前**
    ——"后端没起"不该让一个本地 JSON 转换失败。

    **不重实现 diagram 解析**：格式会漂移，复制一份就是第二个事实源。
    """
    package_root = _velxio_package_root()
    if not package_root:
        return {
            "status": "not_ready",
            "findings": [
                "找不到 Velxio checkout 的 app 包（需要 <root>/backend/app/mcp/wokwi.py）。",
                "设 HW_VELXIO_VELXIO_ROOT 指向 velxio 仓库根，或配置 velxio_root。",
                "注意：电路转换不需要后端在线，只需要源码在盘上。",
            ],
        }
    if package_root not in sys.path:
        sys.path.insert(0, package_root)
    try:
        from app.mcp.wokwi import (  # type: ignore
            format_wokwi_diagram, generate_arduino_sketch, parse_wokwi_diagram,
        )
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "not_ready",
            "findings": [
                f"能定位到 {package_root} 但 import app.mcp.wokwi 失败："
                f"{type(exc).__name__}: {exc}"[:600],
            ],
        }
    action = params.get("action", "export")
    diagram = params.get("diagram")
    if not isinstance(diagram, dict) or not diagram:
        return {"status": "failed", "findings": [
            f"action={action} 需要 diagram（一个 Wokwi diagram JSON 对象）"]}
    try:
        if action == "import":
            return {"status": "ok", "action": action,
                    "circuit": parse_wokwi_diagram(diagram)}
        if action == "export":
            return {"status": "ok", "action": action,
                    "wokwi": format_wokwi_diagram(diagram)}
        if action == "sketch":
            return {"status": "ok", "action": action,
                    "sketch": generate_arduino_sketch(diagram)}
        return {"status": "failed", "findings": [
            f"未知 action：{action}；可用 import / export / sketch"]}
    except Exception as exc:  # noqa: BLE001
        return {"status": "failed", "findings": [
            f"wokwi 计算失败：{type(exc).__name__}: {exc}"[:800]]}


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
