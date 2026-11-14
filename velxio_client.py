"""Velxio 后端的薄 HTTP 客户端。

只做三件事：拼 URL、发请求、把 JSON 原样返回。**不重试、不回退、不猜默认值**——
那些决定属于调用方（模型 / 工具层），不属于传输层。这与 ``hwforge.blocks_cli``
的薄原则一致。

后端地址解析优先级：

1. ``HW_VELXIO_VELXIO_URL`` 环境变量（最高，便于从外部指向任意实例）。
2. ``~/.dsh/hw_velxio.json`` → ``{"velxio_url": "http://127.0.0.1:3080"}``。
3. 默认 ``http://127.0.0.1:3080``（Velxio Docker 的默认映射端口）。

Velxio 的 OSS 后端是**匿名、无鉴权**的（``backend/app/main.py`` 里没有鉴权路由），
所以这里不需要 token。私有部署若加了鉴权，可经 ``HW_VELXIO_VELXIO_TOKEN`` 带
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
    env = os.environ.get("HW_VELXIO_VELXIO_URL", "").strip()
    if env:
        return env.rstrip("/")
    cfg = load_config().get("velxio_url")
    if isinstance(cfg, str) and cfg.strip():
        return cfg.strip().rstrip("/")
    return DEFAULT_URL


def velxio_token() -> str | None:
    t = os.environ.get("HW_VELXIO_VELXIO_TOKEN", "").strip()
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

def compile_sync(files: list[dict[str, str]], board: str, **extra: Any) -> dict[str, Any]:
    """POST /api/compile/ —— 同步编译。冷 ESP-IDF 可能超时，优先用 start/status。"""
    body = {"files": files, "board": board, **extra}
    return _request("POST", "/api/compile/", body=body, timeout=extra.pop("_timeout", 180.0))


def compile_start(files: list[dict[str, str]], board: str, **extra: Any) -> dict[str, Any]:
    """POST /api/compile/start —— 异步编译，返回 job_id。"""
    body = {"files": files, "board": board, **extra}
    return _request("POST", "/api/compile/start", body=body, timeout=60.0)


def compile_status(job_id: str) -> dict[str, Any]:
    """GET /api/compile/status/{job_id} —— 轮询异步编译结果。"""
    return _request("GET", f"/api/compile/status/{job_id}", timeout=30.0)


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
