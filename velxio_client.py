"""Velxio 后端的薄 HTTP 客户端。

只做三件事：拼 URL、发请求、把 JSON 原样返回。**不重试、不回退、不猜默认值**——
那些决定属于调用方（模型 / 工具层），不属于传输层。这与 ``hwforge.blocks_cli``
的薄原则一致。

后端地址解析优先级：

1. ``DSH_EMBAGENT_VELXIO_URL`` 环境变量（最高，便于从外部指向任意实例）。
2. ``~/.dsh/hw_velxio.json`` → ``{"velxio_url": "http://127.0.0.1:3080"}``。
3. 默认 ``http://127.0.0.1:3080``（Velxio Docker 的默认映射端口）。

Velxio 的 OSS 后端是**匿名、无鉴权**的（``backend/app/main.py`` 里没有鉴权路由），
所以这里不需要 token。私有部署若加了鉴权，可经 ``DSH_EMBAGENT_VELXIO_TOKEN`` 带
``Authorization`` 头——但桥接层不会因此假装已认证。
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

DEFAULT_URL = "http://127.0.0.1:3080"
CONFIG_PATH = Path(
    os.environ.get("USERPROFILE", os.environ.get("HOME", ""))
) / ".dsh" / "hw_velxio.json"


def config_path() -> Path:
    return CONFIG_PATH


def load_config() -> dict[str, Any]:
    """读 ``~/.dsh/hw_velxio.json``；不存在或坏了都返回空 dict，不抛。"""
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def velxio_url() -> str:
    """解析 Velxio 后端基址，去掉尾部斜杠。"""
    env = os.environ.get("DSH_EMBAGENT_VELXIO_URL", "").strip()
    if env:
        return env.rstrip("/")
    cfg = load_config().get("velxio_url")
    if isinstance(cfg, str) and cfg.strip():
        return cfg.strip().rstrip("/")
    return DEFAULT_URL


def velxio_token() -> str | None:
    t = os.environ.get("DSH_EMBAGENT_VELXIO_TOKEN", "").strip()
    return t or None


class VelxioError(Exception):
    """一次 Velxio HTTP 调用失败。携带 HTTP 状态与响应体，便于上层如实报告。"""

    def __init__(self, status: int, body: str, url: str):
        self.status = status
        self.body = body
        self.url = url
        super().__init__(f"Velxio HTTP {status} @ {url}: {body[:400]}")


def _request(
    method: str,
    path: str,
    *,
    body: dict[str, Any] | None = None,
    timeout: float = 180.0,
) -> Any:
    url = velxio_url() + path
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    token = velxio_token()
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise VelxioError(exc.code, raw, url) from exc
    except urllib.error.URLError as exc:
        raise VelxioError(0, f"无法连接 Velxio 后端：{exc.reason}", url) from exc
    if not raw.strip():
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"raw": raw}


# ---------------------------------------------------------------------------
# 只读 / 健康
# ---------------------------------------------------------------------------

def health() -> dict[str, Any]:
    """GET /health —— 后端是否可达。失败返回 {reachable: false, error}，不抛。"""
    try:
        payload = _request("GET", "/health", timeout=5.0)
        return {"reachable": True, "url": velxio_url(), "payload": payload}
    except VelxioError as exc:
        return {"reachable": False, "url": velxio_url(), "error": str(exc)}


def boards() -> dict[str, Any]:
    """GET /api/compile/boards —— 后端可用的板卡/核心清单。"""
    return _request("GET", "/api/compile/boards")


def setup_status() -> dict[str, Any]:
    """GET /api/compile/setup-status —— arduino-cli / ESP-IDF 是否就绪。"""
    return _request("GET", "/api/compile/setup-status")


def news_feed() -> dict[str, Any]:
    return _request("GET", "/api/news/feed", timeout=15.0)


# ---------------------------------------------------------------------------
# 编译 / 芯片 / ROM
# ---------------------------------------------------------------------------

def _compile_body(files: list[dict[str, str]], board: str, **extra: Any) -> dict[str, Any]:
    """构造编译请求体。

    **字段名必须是 `board_fqbn`，不是 `board`。**

    后端 ``CompileRequest``（``api/routes/compile.py:595``）声明的是
    ``board_fqbn: str = "arduino:avr:uno"``，而 pydantic v2 默认
    ``extra="ignore"`` —— 发 ``{"board": "esp32:esp32:esp32"}`` 不会报错，
    只会被**静默丢弃**，然后按默认的 uno 编译。那比直接报错更糟：用户会拿到
    一份"成功"的、给错板子编的固件。

    所以这里用 ``board_fqbn``，并且保留 ``board`` 作为一个**显式的错误**
    （见 ``compile_start`` 的断言）——两个名字都收会让下一个读代码的人
    以为哪个都行。
    """
    if "board" in extra:
        raise ValueError(
            "compile 的板卡字段是 board_fqbn，不是 board；"
            "传错名字后端会静默按 uno 编译（pydantic 默认 ignore 未知字段）")
    body: dict[str, Any] = {"files": files, "board_fqbn": board}
    body.update(extra)
    return body


def compile_sync(files: list[dict[str, str]], board: str, **extra: Any) -> dict[str, Any]:
    """POST /api/compile/ —— 同步编译。冷 ESP-IDF 可能超时，优先用 start/status。"""
    timeout = float(extra.pop("_timeout", 180.0))
    body = _compile_body(files, board, **extra)
    out = _request("POST", "/api/compile/", body=body, timeout=timeout)
    return _verify_board(out, board)


def compile_start(files: list[dict[str, str]], board: str, **extra: Any) -> dict[str, Any]:
    """POST /api/compile/start —— 异步编译，返回 job_id。"""
    body = _compile_body(files, board, **extra)
    return _request("POST", "/api/compile/start", body=body, timeout=60.0)


def _verify_board(out: dict[str, Any], board: str) -> dict[str, Any]:
    """回读响应里的板卡，和请求不一致时补一条显式告警。

    后端目前不回显 fqbn，所以这条检查**只在它回显时才生效**；重点是它不会
    假装成功——一旦将来后端加了回显字段，不匹配会立刻暴露，而不是继续静默。
    """
    echoed = out.get("board_fqbn") or out.get("board")
    if isinstance(echoed, str) and echoed and echoed != board:
        warnings = out.get("warnings")
        note = f"请求的板卡是 {board}，后端用的是 {echoed}"
        if isinstance(warnings, list):
            warnings.append(note)
        else:
            out["warnings"] = [note]
    return out


def compile_status(job_id: str) -> dict[str, Any]:
    """GET /api/compile/status/{job_id} —— 轮询异步编译结果。

    **结果在 `result` 子对象里**，不在顶层：顶层只有 `state`/`stage`/
    `progress` 这些作业进度字段，而 `success`/`hex_content`/`stderr` 全在
    `result` 下。第一版直接读顶层 `hex_content`，恒为 None——于是"编译成功
    但拿不到固件"，看起来像编译失败。
    """
    return _request("GET", f"/api/compile/status/{job_id}", timeout=30.0)


def compile_wait(job_id: str, timeout_s: float = 240.0, interval_s: float = 1.2) -> dict[str, Any]:
    """轮询到作业结束，返回**摊平**的结果。

    摊平的形状：顶层带 `state`/`success`/`hex_content`/`binary_content`/
    `stderr` 等，与 `/api/compile/`（同步路径）的响应同形。这样调用方不需要
    知道"异步作业有个 result 子对象"，两条路径的消费方式一致。

    超时**不抛**：返回 `state='timeout'` 与已观测到的进度，让上层如实上报。
    """
    import time as _time

    deadline = _time.time() + timeout_s
    last: dict[str, Any] = {}
    while _time.time() < deadline:
        last = compile_status(job_id)
        state = str(last.get("state") or "")
        if state in ("done", "error", "cancelled", "failed"):
            break
        _time.sleep(interval_s)

    result = last.get("result") if isinstance(last.get("result"), dict) else {}
    flat: dict[str, Any] = dict(result) if result else {}
    for key in ("state", "stage", "progress", "error", "job_id", "build_seconds"):
        if key in last and key not in flat:
            flat[key] = last[key]
    flat["job_id"] = job_id
    flat.setdefault("state", last.get("state", "unknown"))
    if flat.get("state") == "timeout":
        flat.setdefault("success", False)
    return flat


def compile_chip(files: list[dict[str, str]], **extra: Any) -> dict[str, Any]:
    """POST /api/compile-chip/ —— 自定义芯片（C → WebAssembly）。"""
    body = {"files": files, **extra}
    return _request("POST", "/api/compile-chip/", body=body, timeout=180.0)


def compile_rom(files: list[dict[str, str]], **extra: Any) -> dict[str, Any]:
    """POST /api/compile-rom/ —— ROM（8080/Z80 等）汇编。"""
    body = {"files": files, **extra}
    return _request("POST", "/api/compile-rom/", body=body, timeout=120.0)


def flash_upload(**extra: Any) -> dict[str, Any]:
    """POST /api/flash/upload —— 烧录到真实 USB 板（桌面侧）。"""
    return _request("POST", "/api/flash/upload", body=extra, timeout=120.0)


# ---------------------------------------------------------------------------
# 库管理
# ---------------------------------------------------------------------------

def lib_search(query: str) -> dict[str, Any]:
    return _request("GET", f"/api/libraries/search?q={urllib.parse.quote(query)}", timeout=30.0)


def lib_install(name: str, version: str | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"name": name}
    if version:
        body["version"] = version
    return _request("POST", "/api/libraries/install", body=body, timeout=120.0)


def lib_list() -> dict[str, Any]:
    return _request("GET", "/api/libraries/list", timeout=30.0)


def lib_uninstall(name: str) -> dict[str, Any]:
    return _request("DELETE", "/api/libraries/uninstall", body={"name": name}, timeout=60.0)


def micropython_search(query: str) -> dict[str, Any]:
    return _request(
        "GET", f"/api/micropython-libs/search?q={urllib.parse.quote(query)}", timeout=30.0
    )


def micropython_fetch(name: str, **extra: Any) -> dict[str, Any]:
    body = {"name": name, **extra}
    return _request("POST", "/api/micropython-libs/fetch", body=body, timeout=60.0)
