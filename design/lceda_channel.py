"""立创通道 —— CLI 与 Bridge 两条腿，**同一套调用形状**。

【为什么需要两条腿】

我们平时用的是 `lceda-pro.exe invoke --session <id>`（CLI 通道）。它有一个
致命的边界：**窗口必须存在**。而 Bridge 通道（Run API Gateway 扩展 + 
`bridge-server.mjs`）是"立在用户那扇窗里跑"，两者的差别是：

| | CLI (`invoke --session`) | Bridge (`POST /execute`) |
|---|---|---|
| 谁开窗口 | 我们（`open`） | 用户自己开着的那个 |
| 登录态 | **新窗口可能没有**（实测 `createProject` 抛 `username undefined`，AGENTS §3.21） | 用户窗口的登录态 |
| 单次上限 | `--timeout`（可到 900s） | **30 秒硬上限**，超时≠失败（手册 §17.5/§17.7） |
| 前置条件 | 装 lceda-pro | 还要装 `run-api-gateway` 扩展 + 勾「允许外部交互」+ 连上 |

所以：**长任务走 CLI，需要登录态/用户窗口的任务走 Bridge**，两者都要能报
``not_ready`` 并点名缺哪一步 —— 而不是"连不上就崩"。

【这个模块只做通道，不做判定】

和 `lceda.py` 同一条纪律：通道只回答"能不能连、这次调用跑没跑通"。
「板子对不对」的判定仍然只有一份（`ladder` / `handoff` / `pcb_geo`）。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Iterable

from . import lceda

__all__ = ["BridgeChannel", "ChannelError", "probe", "open_channel"]

#: Bridge 的端口区间（与扩展、skill 一致）。
PORT_RANGE = tuple(range(49620, 49630))

#: Bridge 的 `/execute` 是**30 秒硬上限**（源码常量，不可配置）。
BRIDGE_TIMEOUT_MS = 30_000


class ChannelError(RuntimeError):
    """通道层面的失败（连不上 / 业务信封 ok:false / 超时）。"""


@dataclass
class BridgeChannel:
    """通过 Bridge Server 在**用户那扇窗**里执行代码。"""

    port: int = 0
    window_id: str = ""
    kind: str = "bridge"
    exe: str = ""
    version: str = ""
    session_id: str = ""
    project_uuid: str = ""
    project_path: str = ""
    capabilities: dict[str, Any] = field(default_factory=dict)
    headless: bool = False

    # -- 与 `LcedaSession` 对齐的最小接口 ---------------------------------
    @property
    def ready(self) -> bool:
        return bool(self.port)

    def invoke(self, code: str, *, require_ok: bool = True,
               expect: Iterable[str] = (), check: Any = None,
               timeout_ms: int = BRIDGE_TIMEOUT_MS, label: str = "") -> Any:
        """执行一段 JS 并返回 `result`。

        Bridge 的返回信封是 ``{success, result, windowId}``；`require_ok`
        在这里的含义和 CLI 通道一致：`success === false` 就抛。
        """
        if not self.port:
            raise ChannelError(f"{label or 'invoke'}：Bridge 没连上")
        body = json.dumps({"code": code, "windowId": self.window_id},
                          ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/execute", data=body,
            headers={"Content-Type": "application/json"}, method="POST")
        where = label or "invoke"
        try:
            with urllib.request.urlopen(request,
                                        timeout=max(1.0, timeout_ms / 1000.0)) as resp:
                payload = json.loads(resp.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as exc:  # 4xx/5xx
            # **把 body 读出来**：Bridge 的 500 往往带着真正原因（比如它自己的
            # 30 秒超时），只报 "HTTP 500" 等于什么都没说（第一版就这样）。
            detail = ""
            try:
                detail = exc.read().decode("utf-8", "replace")[:300]
            except Exception:  # noqa: BLE001
                detail = ""
            raise ChannelError(
                f"{where}：HTTP {exc.code}"
                + (f" {detail}" if detail else "")) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            # **超时 ≠ 失败**（手册 §17.7）：EDA 侧可能已经做完了。
            raise ChannelError(
                f"{where}：超时/断连（{exc}）—— EDA 侧**可能已执行**，"
                "先读回状态再决定重做") from exc
        if not isinstance(payload, dict):
            raise ChannelError(f"{where}：Bridge 返回不是对象")
        if require_ok and payload.get("success") is False:
            raise ChannelError(f"{where}：业务失败 {payload.get('error')}")
        result = payload.get("result")
        blob = json.dumps(result, ensure_ascii=False, default=str)
        for token in expect:
            if token not in blob:
                raise ChannelError(f"{where}：返回值里没有 {token!r}")
        if callable(check):
            verdict = check(result)
            if verdict is not True:
                raise ChannelError(f"{where}：{verdict if isinstance(verdict, str) else '自定义检查未通过'}")
        return result

    # -- 与 `LcedaSession` 对齐的工程读接口 -------------------------------
    #
    # `emit` 的 `_run_project` 会调 `wait_project_ready()`；Bridge 通道第一版
    # 没实现它 → 整条发射在第一步就 `AttributeError`（已踩）。
    # 这里复用 CLI 通道那两个脚本，**只换执行通道，不另写一份判定**。

    def wait_project_ready(self, timeout_ms: int = 15_000) -> dict[str, Any]:
        """等工程可读，返回 `{projectUuid, friendlyName, boards}`。"""
        tries = max(1, int(timeout_ms / 250))
        script = lceda._JS_READY.replace("__TRIES__", str(tries))  # noqa: SLF001
        value = self.invoke(script, expect=("projectUuid", "boards"),
                            timeout_ms=min(timeout_ms + 5_000, BRIDGE_TIMEOUT_MS),
                            label="ready")
        self.project_uuid = str((value or {}).get("projectUuid") or "")
        return value or {}

    def current_project(self) -> dict[str, Any]:
        """读当前工程结构（板/原理图/页/PCB）。"""
        return self.invoke(lceda._JS_CURRENT, timeout_ms=BRIDGE_TIMEOUT_MS,  # noqa: SLF001
                           label="current") or {}

    def projects_root(self) -> str:
        """工程根目录。Bridge 通道里 `getProjectsPaths` 一样可能被权限拦住。"""
        try:
            value = self.invoke(
                "const roots = await eda.sys_FileSystem.getProjectsPaths();"
                "return {ok: true, roots: roots || []};",
                expect=("roots",), timeout_ms=BRIDGE_TIMEOUT_MS, label="root")
        except ChannelError:
            return ""
        roots = (value or {}).get("roots") or []
        return str(roots[0]) if roots else ""

    def open(self, path: str = "", *, headless: bool = True) -> "BridgeChannel":
        """Bridge 通道不自己开窗：它跟着**用户那扇窗**走。传 path 视为无效。"""
        return self

    def close(self) -> dict[str, Any]:
        """**不动用户的窗口**：只释放本地句柄。"""
        self.port = 0
        return {"ok": True, "note": "Bridge 通道只释放句柄，不关用户的窗口"}

    def probe_capabilities(self, jobs: Iterable[str] = ()) -> dict[str, Any]:
        """Bridge 通道不做逐方法探测（它在用户窗口里，方法集就是那台机器的）。"""
        return {"ok": True, "checked": 0, "missing": [], "jobs": list(jobs),
                "note": "Bridge 通道不探测方法集：运行时就在用户窗口里"}


def _health(port: int, *, timeout: float = 0.6) -> dict[str, Any] | None:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health",
                                    timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8") or "{}")
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return None
    return payload if payload.get("service") == "easyeda-bridge" else None


def _windows(port: int) -> dict[str, Any] | None:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/eda-windows",
                                    timeout=1.0) as resp:
            return json.loads(resp.read().decode("utf-8") or "{}")
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return None


def probe(*, exe: str = "") -> dict[str, Any]:
    """两条腿各自的状态。**只读**，不启动任何东西。"""
    exe_path = exe or lceda.find_exe()
    cli = {"available": bool(exe_path) and bool(exe_path),
           "exe": exe_path,
           "version": lceda.version_of(exe_path) if exe_path else "",
           "sessions": []}
    if exe_path:
        try:
            cli["sessions"] = [
                {"sessionId": s.get("sessionId"), "origin": s.get("origin"),
                 "headless": s.get("headless"), "status": s.get("status")}
                for s in lceda.LcedaSession.list_sessions(exe_path)]
        except Exception as exc:  # noqa: BLE001 - 探测失败不该抛
            cli["error"] = f"{type(exc).__name__}: {exc}"
    bridge: dict[str, Any] = {"running": False, "port": 0, "edaConnected": False,
                              "windows": [], "missing": []}
    for port in PORT_RANGE:
        health = _health(port)
        if not health:
            continue
        bridge.update({"running": True, "port": port,
                       "edaConnected": bool(health.get("edaConnected")),
                       "edaWindowCount": health.get("edaWindowCount"),
                       "activeWindowId": health.get("activeWindowId")})
        windows = _windows(port) or {}
        bridge["windows"] = [{"windowId": w.get("windowId"),
                              "active": bool(w.get("active"))}
                             for w in (windows.get("windows") or [])]
        break
    if not bridge["running"]:
        bridge["missing"].append(
            "Bridge 服务没在跑：`node <技能目录>/scripts/bridge-server.mjs`")
    elif not bridge["edaConnected"]:
        bridge["missing"].extend([
            "立创里没连上 Bridge：装 `run-api-gateway` 扩展并勾「允许外部交互」",
            "扩展的端口扫描**只重试 5 次**（手册 §17.3）：顶部菜单 "
            "API Gateway → Reconnect 点一次",
        ])
    recommended = ("bridge" if bridge["edaConnected"]
                   else ("cli" if cli["available"] else ""))
    return {"cli": cli, "bridge": bridge, "recommended": recommended}


def open_channel(prefer: str = "auto", *, exe: str = "",
                 project_path: str = "", headless: bool = True) -> Any:
    """按 `prefer` 打开一条通道。

    * ``cli``    —— 一定用 CLI（要装 lceda-pro）
    * ``bridge`` —— 一定用 Bridge（连不上就抛 `ChannelError`，**不偷偷降级**）
    * ``auto``   —— 先看 Bridge 是否 `edaConnected`，否则 CLI，并在返回对象上
      记 `fallback_from="bridge"`，让调用方能如实报告"用的是哪条腿、为什么"
    """
    exe_path = exe or lceda.find_exe()
    if prefer not in ("auto", "cli", "bridge"):
        raise ChannelError(f"未知通道 prefer={prefer!r}（可用 auto/cli/bridge）")
    want_bridge = prefer == "bridge"
    if prefer in ("auto", "bridge"):
        state = probe(exe=exe_path)
        if state["bridge"]["edaConnected"]:
            port = int(state["bridge"]["port"])
            window = state["bridge"].get("activeWindowId") or ""
            channel = BridgeChannel(port=port, window_id=str(window or ""),
                                    exe=exe_path,
                                    version=lceda.version_of(exe_path) if exe_path else "")
            channel.capabilities = state
            return channel
        if want_bridge:
            raise ChannelError(
                "Bridge 通道不可用：" + "；".join(state["bridge"]["missing"] or ["未连接"]))
    if not exe_path:
        raise ChannelError(
            f"没有 lceda-pro 可执行文件：设环境变量 {lceda.EXE_ENV} 或安装立创EDA专业版")
    session = lceda.LcedaSession(exe=exe_path)
    session.open(path=project_path, headless=headless)
    if prefer == "auto":
        session.fallback_from = "bridge"   # type: ignore[attr-defined]
        session.channel_probe = probe(exe=exe_path)  # type: ignore[attr-defined]
    return session