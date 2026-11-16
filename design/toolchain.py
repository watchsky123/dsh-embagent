"""外部工具探测与调用 —— **hw_velxio 自己的**，不依赖 `hwforge.downstream`。

## 它做什么

制造交接与固件仿真要靠外部工具（tscircuit / Renode）。本模块只做两件事：

1. **探测**：这些工具在不在。不在就如实报 `available=False` + 原因；
2. **调用**：找到就按它自己的命令跑，把输出写到自己的目录里。

## 两条不能破的纪律

1. **绝不自己合成制造文件。** 本模块不会写 Gerber / STEP / BOM ——
   那些必须由工具产出。自己拼一个"看起来像 Gerber"的文件是最坏的造假：
   它能通过文件名检查，然后被拿去投板。
2. **探测不到就说探测不到。** 把"没装"说成"生成失败"会让人去查代码，
   而问题在环境。

@module hw_velxio.design.toolchain
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


TSCIRCUIT_ENV = "HWV_TSCIRCUIT_CLI"
RENODE_ENV = "HWV_RENODE"


@dataclass
class ToolStatus:
    """一个外部工具的探测结果。"""

    available: bool
    #: 解析到的可执行文件（或 npx 形式的 argv）
    command: list[str] = field(default_factory=list)
    #: 不可用时**为什么**
    reason: str = ""
    #: 版本号（能拿到就给）
    version: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"available": self.available, "command": list(self.command),
                "reason": self.reason, "version": self.version}


def _which(name: str) -> str | None:
    return shutil.which(name)


def _version_of(argv: list[str]) -> str:
    """跑 `--version` 取版本号。拿不到返回空串（不猜）。"""
    try:
        proc = subprocess.run(argv + ["--version"], capture_output=True,
                              text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return ""
    text = (proc.stdout or proc.stderr or "").strip()
    return text.splitlines()[0][:80] if text else ""


def probe_tscircuit() -> ToolStatus:
    """tscircuit CLI 在不在。

    查两种拼法：全局安装的 `tsci`/`tscircuit`，以及 `npx tsci`
    （有 Node 但没全局安装的机器）。`HWV_TSCIRCUIT_CLI` 可覆盖。
    """
    pinned = os.environ.get(TSCIRCUIT_ENV, "").strip()
    if pinned:
        path = Path(pinned)
        if path.is_file() or _which(pinned):
            argv = [pinned]
            return ToolStatus(True, argv, "", _version_of(argv))
        return ToolStatus(False, [], f"{TSCIRCUIT_ENV}={pinned!r} 指向的文件不存在")

    for exe in ("tsci", "tscircuit"):
        found = _which(exe)
        if found:
            return ToolStatus(True, [found], "", _version_of([found]))

    npx = _which("npx")
    if npx:
        # `npx tsci` 只有在包能拉到/已缓存时才有用；这里不预先拉，
        # 只把命令交给调用方 —— 它跑失败时会自己报错。
        return ToolStatus(True, [npx, "tsci"],
                          "没有全局 tsci，走 npx（首次运行需要联网下载）")
    return ToolStatus(
        False, [],
        "找不到 tsci/tscircuit/npx：装 @tscircuit/cli，"
        f"或设 {TSCIRCUIT_ENV} 指向可执行文件")


def probe_renode() -> ToolStatus:
    """Renode 在不在。它按发行包安装，不按包管理器，所以查常见路径。"""
    pinned = os.environ.get(RENODE_ENV, "").strip()
    if pinned:
        path = Path(pinned)
        if path.is_file() or _which(pinned):
            argv = [pinned]
            return ToolStatus(True, argv, "", _version_of(argv))
        return ToolStatus(False, [], f"{RENODE_ENV}={pinned!r} 指向的文件不存在")

    for exe in ("renode", "renode-cli", "Renode.exe"):
        found = _which(exe)
        if found:
            return ToolStatus(True, [found], "", _version_of([found]))

    candidates = [
        Path("C:/Program Files/Renode/Renode.exe"),
        Path("C:/Program Files (x86)/Renode/Renode.exe"),
        Path.home() / "Renode" / "Renode.exe",
        Path("/opt/renode/renode"),
        Path("/usr/bin/renode"),
    ]
    for candidate in candidates:
        if candidate.is_file():
            return ToolStatus(True, [str(candidate)], "",
                              _version_of([str(candidate)]))

    return ToolStatus(
        False, [],
        "找不到 Renode：从 renode.io 下载发行包解压，"
        f"或设 {RENODE_ENV} 指向 Renode 可执行文件")


@dataclass
class FabResult:
    """一次制造导出的结果。"""

    ok: bool
    error: str = ""
    #: 实际写出的文件（相对 out_dir）
    files: list[str] = field(default_factory=list)
    #: 工具自己输出的日志尾巴（便于排查）
    log_tail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "error": self.error,
                "files": list(self.files), "log_tail": self.log_tail}


def export_fabrication(source: Path, out_dir: Path, *,
                       formats: tuple[str, ...] = ("gerbers", "bom", "step"),
                       timeout_s: int = 300) -> FabResult:
    """调 tscircuit 自己的 export，把产物写到 `out_dir`。

    **本函数不生成任何制造文件** —— 它只是调工具，然后数产物。
    """
    status = probe_tscircuit()
    if not status.available:
        return FabResult(ok=False, error=f"tscircuit 不可用：{status.reason}")

    source = Path(source)
    if not source.exists():
        return FabResult(ok=False, error=f"源工程不存在：{source}")

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    argv = [*status.command, "export", str(source),
            "--output", str(out_dir), *formats]
    try:
        proc = subprocess.run(argv, capture_output=True, text=True,
                              timeout=timeout_s)
    except subprocess.TimeoutExpired:
        return FabResult(ok=False,
                         error=f"tscircuit export 超时（>{timeout_s}s）")
    except OSError as exc:
        return FabResult(ok=False, error=f"tscircuit 调用失败：{exc}")

    log_tail = ((proc.stdout or "") + (proc.stderr or "")).strip()
    log_tail = log_tail[-1200:] if log_tail else ""
    if proc.returncode != 0:
        return FabResult(ok=False,
                         error=f"tscircuit export 返回 {proc.returncode}",
                         log_tail=log_tail)

    files = sorted(str(p.relative_to(out_dir)).replace("\\", "/")
                   for p in out_dir.rglob("*") if p.is_file())
    if not files:
        return FabResult(
            ok=False,
            error="tscircuit 返回成功但**没有产出任何文件** —— "
                  "不把「没报错」当成「做好了」",
            log_tail=log_tail)
    return FabResult(ok=True, files=files, log_tail=log_tail)


def discover() -> dict[str, Any]:
    """一次探测全部外部工具（给面板用）。"""
    tsc = probe_tscircuit()
    ren = probe_renode()
    return {
        "tools": {"tscircuit": tsc.to_dict(), "renode": ren.to_dict()},
        "available": tsc.available or ren.available,
        "note": "探测不到就是探测不到；本模块不合成任何制造文件",
    }