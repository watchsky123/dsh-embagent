"""`doctor` —— 一条命令说清"这台机器能做什么、缺什么"。

## 为什么需要它

此前每遇到环境问题都要逐个试：立创装没装？Bridge 连没连？MuJoCo 在不在
（它装在项目内 `vendor/pylib`，不在全局）？arduino-cli / gcc / ffmpeg 有没有？
`HWV_PARTS_OFFLINE` 是不是被谁打开了？—— 这些问题的答案散在六七个模块里。

`doctor` 只做一件事：**逐项问一遍，三项结论（ok / not_ready / failed）**。
它**不修任何东西**，也不装任何东西。

## 判据

| 项 | required | 说明 |
|---|---|---|
| `workspace` | ✅ | 工作区能写（不能写什么都做不了）|
| `lceda` | ✅ | 立创专业版可执行文件在位 |
| `bridge` | ❌ | Bridge 通道（连上 = 发射可以走用户窗口）|
| `lcsc` | ❌ | 目录缓存（没有也能跑，只是第一次联网慢）|
| `mujoco` | ❌ | 机械/仿真 |
| `arduino_cli` / `gcc` / `ffmpeg` | ❌ | 编译/视频 |

**required 全 ok 才是 `ok`**；required 有一项 `failed` → `failed`；只有可选项
缺失 → `not_ready`（并写清缺了会影响什么）。
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

__all__ = ["run", "describe"]

#: 缺了它就什么都做不了。
REQUIRED = ("workspace", "lceda")


def _row(name: str, status: str, detail: str, *, required: bool | None = None,
         impact: str = "", extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """**`required` 只由 `REQUIRED` 这张表决定**（§1.1：一个事实源）。

    第一版在 `_check_bin` 里写死 `required=False`、在 workspace/lceda 里写死
    `True` —— 于是把某项提成必需时**不生效**（表改了，行还挂旧值）。
    """
    row = {"name": name, "status": status, "detail": detail,
           "required": (name in REQUIRED) if required is None else required,
           "impact": impact}
    if extra:
        row.update(extra)
    return row


def _check_workspace() -> dict[str, Any]:
    from .store import project_root

    root = project_root()
    try:
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=str(root), prefix=".hwv_probe_",
                                         delete=True) as handle:
            handle.write(b"ok")
            handle.flush()
        return _row("workspace", "ok", str(root))
    except OSError as exc:
        return _row("workspace", "failed", f"{root}：{exc}",
                    impact="写不了工作区，所有产物都落不了盘")


def _check_lceda() -> dict[str, Any]:
    from . import lceda as lceda_mod

    try:
        exe = lceda_mod.find_exe()
    except lceda_mod.CliError as exc:
        return _row("lceda", "not_ready", str(exc),
                    impact="发射（emit）与所有立创侧证据都拿不到")
    version = ""
    try:
        version = lceda_mod.version_of(exe)
    except Exception:  # noqa: BLE001 - 版本拿不到不影响"在位"
        version = "（版本未知）"
    return _row("lceda", "ok", f"{exe} {version}".strip())


def _check_bridge() -> dict[str, Any]:
    from . import lceda_channel as channel_mod

    try:
        probe = channel_mod.probe()
    except Exception as exc:  # noqa: BLE001
        return _row("bridge", "not_ready", f"探测失败：{type(exc).__name__}: {exc}",
                    impact="Bridge 通道不可用；发射仍可走 CLI")
    bridge = probe.get("bridge") or {}
    if bridge.get("edaConnected"):
        return _row("bridge", "ok",
                    f"port {bridge.get('port')}，窗口 {len(bridge.get('windows') or [])} 个，已连",
                    impact="长任务仍需 CLI（单次 30 秒上限）")
    missing = "；".join(str(x) for x in (bridge.get("missing") or []))
    return _row("bridge", "not_ready",
                f"running={bridge.get('running')} "
                f"edaConnected={bridge.get('edaConnected')}"
                + (f"；{missing}" if missing else ""),
                impact="Bridge 未连：发射走 CLI 即可；要用 Bridge 需扩展里点 Reconnect")


def _check_lcsc() -> dict[str, Any]:
    if str(os.environ.get("HWV_PARTS_OFFLINE", "")).strip().lower() in ("1", "true", "yes"):
        return _row("lcsc", "not_ready", "HWV_PARTS_OFFLINE 打开着（不发网络请求）",
                    impact="选料/尺寸只能用手上缓存")
    cache = Path.home() / ".dsh" / "hw_velxio_parts_cache.json"
    if cache.is_file():
        return _row("lcsc", "ok", f"本地缓存 {cache}（{cache.stat().st_size} 字节）",
                    impact="不联网也能查已缓存的编号")
    return _row("lcsc", "not_ready", "没有本地缓存（第一次查询会联网）",
                impact="离线时 parts-search/enrich 会 not_ready")


def _check_mujoco() -> dict[str, Any]:
    from . import mujoco_backend

    ok, why = mujoco_backend.available()
    return _row("mujoco", "ok" if ok else "not_ready", why,
                impact="没有它：mjcf 只说'没验证过'，ladder V7 也跑不了")


def _check_bin(name: str, impact: str) -> dict[str, Any]:
    path = shutil.which(name)
    if path:
        return _row(name, "ok", path, impact="")
    return _row(name, "not_ready", f"PATH 里没有 {name}", impact=impact)


#: 仓库内工具链的相对位置（**不装到系统里**，删目录即卸载）。
LOCAL_TOOLCHAINS = {
    "arm_gcc": ("_tools/arm-gcc", "arm-none-eabi-gcc.exe",
                "STM32 固件真编译做不了（只能验纯 C 逻辑）"),
}


def _check_local_toolchain(name: str, root: Path) -> dict[str, Any]:
    """先在**仓库内**找，再看 PATH。**只探测，不装。**"""
    rel, exe_name, impact = LOCAL_TOOLCHAINS[name]
    base = root / rel
    if base.is_dir():
        found = sorted(base.glob(f"*/bin/{exe_name}"), reverse=True)
        if found:
            exe = found[0]
            version = ""
            try:
                import subprocess  # noqa: PLC0415

                proc = subprocess.run([str(exe), "--version"],
                                      capture_output=True, text=True, timeout=30)
                first = (proc.stdout or "").splitlines()
                version = first[0].strip() if first else ""
            except Exception as exc:  # noqa: BLE001 - 探测失败不该炸整体
                version = f"(版本读不出：{type(exc).__name__})"
            return _row(name, "ok", f"{exe}；{version}".strip(),
                        impact="", extra={"scope": "repo", "rel": rel})
        return _row(name, "not_ready", f"{rel} 在，但里面没有 {exe_name}",
                    impact=impact, extra={"scope": "repo"})
    path = shutil.which(exe_name)
    if path:
        return _row(name, "ok", path, impact="", extra={"scope": "path"})
    return _row(name, "not_ready",
                f"仓库内没有 {rel}，PATH 里也没有 {exe_name}",
                impact=impact, extra={"scope": "none"})


def run() -> dict[str, Any]:
    """逐项探测。**不装、不修、不联网**（LCSC 那一项只读本地缓存）。"""
    rows: list[dict[str, Any]] = []
    for probe in (_check_workspace, _check_lceda, _check_bridge, _check_lcsc,
                  _check_mujoco):
        name = probe.__name__.removeprefix("_check_").replace("_", "-")
        try:
            rows.append(probe())
        except Exception as exc:  # noqa: BLE001
            # **fallback 也要带 required**：否则"探测本身炸了"会被当成
            # "可选项缺失"，整体变 not_ready —— 而它可能是必需项（§0.1 形状）。
            rows.append(_row(name, "not_ready", f"{type(exc).__name__}: {exc}",
                             required=name in REQUIRED))
    # 仓库内工具链：**位置是事实的一部分**（装哪、能不能被卸载干净）
    repo_root = Path(__file__).resolve().parents[1]
    rows.append(_check_local_toolchain("arm_gcc", repo_root))
    rows.append(_check_bin("arduino-cli", "Arduino 编译（真 hex）做不了"))
    rows.append(_check_bin("gcc", "C 源码重编做不了"))
    rows.append(_check_bin("ffmpeg", "contact_sheet 拼缩略图做不了"))

    hard = [r for r in rows if r["required"] and r["status"] != "ok"]
    missing = [r for r in rows if not r["required"] and r["status"] != "ok"]
    status = ("failed" if any(r["status"] == "failed" for r in rows)
              else "not_ready" if hard else "ok")
    return {
        "status": status,
        "checks": rows,
        "counts": {"total": len(rows),
                   "ok": sum(1 for r in rows if r["status"] == "ok"),
                   "missing": len(missing), "required_missing": len(hard)},
        "note": ("必需项齐全" if not hard else
                 "必需项缺：" + "、".join(r["name"] for r in hard))
                + (f"；可选缺 {len(missing)} 项（{ '、'.join(r['name'] for r in missing) }）"
                   if missing else ""),
    }


def describe() -> dict[str, Any]:
    return {
        "module": "hw_velxio.design.doctor",
        "required": list(REQUIRED),
        "checks": ["workspace", "lceda", "bridge", "lcsc", "mujoco",
                   "arduino-cli", "gcc", "ffmpeg"],
        "discipline": "只探测，不安装、不修复、不联网；缺可选项 = not_ready 并写影响",
    }