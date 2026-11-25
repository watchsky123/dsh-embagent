"""把 venv 的环境补丁变成**可复现、可关闭、可自检**的一件事。

## 为什么需要这个模块

P1 为了把后端启动从 76 秒压下来，在 venv 的 `site-packages/sitecustomize.py`
里放了一个 import hook。那解决了问题，但留下三个隐患：

1. **不可复现**：重装 venv 就没了，而"重新生成它"没有任何代码承载。
2. **不可见**：运行 `serve status` 看不出补丁在不在，于是"补丁丢了"
   会表现成"后端又变慢了"，而不是一条错误。
3. **不好关**：`DSH_EMBAGENT_EAGER_CLI=1` 藏在正经代码的注释里。

所以这里把补丁本身作为**本模块的一份常量**持有，并提供三个动作：

    python -m dsh_embagent.patch status   # 在不在、内容是否与预期一致
    python -m dsh_embagent.patch install  # 写入/刷新（幂等）
    python -m dsh_embagent.patch remove   # 删除，完全还原上游行为

`serve.ensure_venv()` 会在装完依赖后**自动调用 install**（幂等），
于是 venv 重建也不会丢。而 `serve status` 会带上 `patch` 字段。

补丁只影响**这个 venv**：系统 Python、其它项目、Velxio 官方镜像都不受影响。

@module dsh_embagent.patch
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# 补丁正文
# ---------------------------------------------------------------------------

#: 补丁要解决的问题、做法、开关，全部写在这里（生成的文件里也带着）。
PATCH_DOC = '''"""DSH 专用：让 Velxio 后端在本机**不用每次启动都等 76 秒**。

由 `dsh_embagent.patch` 生成，**不要手改**——改 `dsh_embagent/patch.py` 的
PATCH_BODY 后跑 `python -m dsh_embagent.patch install` 重新生成。

## 问题

`velxio-master/backend/app/api/routes/compile.py:35` 是**模块级**

    arduino_cli = ArduinoCLIService()

构造时同步执行 `_ensure_board_urls()`（config add × N + `core update-index`）
与 `_ensure_core_installed()`（`core list` / `core install`）。它们都会去拉
board-manager 索引，而本机连不上 GitHub release。

实测（同一台机器、同一份 core）：

    带那 3 个 board-manager URL → core list 76.0s
    清掉之后                    → core list  0.14s

## 做法

在 `app.api.routes.compile` 执行**之前**替换 `app.services.arduino_cli` 模块里
的 `ArduinoCLIService` 符号为惰性版，并在构造前清掉那三个 URL。

**为什么必须"执行前"**：第一版是在模块 exec 完成后替换 `module.arduino_cli`，
那时构造已经跑完，76 秒一分没省。日志里"已改为惰性构造"是真的，只是晚了一步。

## 开关

`DSH_EMBAGENT_EAGER_CLI=1` → 完全走上游原始行为（慢，但零介入）。
删掉本文件 → 同样完全还原。
"""

from __future__ import annotations

import os


def _install() -> None:
    if os.environ.get("DSH_EMBAGENT_EAGER_CLI", "").strip() in ("1", "true", "yes"):
        return

    import importlib.abc
    import sys

    TARGET = "app.api.routes.compile"
    SERVICE_NAME = "ArduinoCLIService"

    class _LazyService:
        """惰性构造上游 `ArduinoCLIService`，属性访问转发。"""

        __slots__ = ("_real", "_factory")

        def __init__(self, factory):
            object.__setattr__(self, "_factory", factory)
            object.__setattr__(self, "_real", None)

        def _unwrap(self):
            real = object.__getattribute__(self, "_real")
            if real is None:
                factory = object.__getattribute__(self, "_factory")
                real = factory()
                object.__setattr__(self, "_real", real)
            return real

        def __getattr__(self, name):
            return getattr(self._unwrap(), name)

        def __setattr__(self, name, value):
            setattr(self._unwrap(), name, value)

        def __repr__(self):
            real = object.__getattribute__(self, "_real")
            return "<LazyArduinoCLIService %s>" % (
                "已构造" if real is not None else "未构造")

    def _strip_dead_board_urls() -> None:
        """清掉 arduino-cli 配置里指向不可达域名的索引 URL。

        只动 arduino-cli 自己的 yaml，不碰 Velxio 源码。代价是 RP2040/ESP32
        的扩展板族可能不在列表里——本机本来也装不了那些 core。
        """
        try:
            import shutil
            import subprocess
            cli = shutil.which("arduino-cli")
            if not cli:
                return
            subprocess.run(
                [cli, "config", "delete", "board_manager.additional_urls"],
                capture_output=True, text=True, timeout=30)
        except Exception:
            pass

    _patched = {"done": False}

    def patch_service_module() -> None:
        if _patched["done"]:
            return
        try:
            import app.services.arduino_cli as svc
        except Exception:
            return
        real_cls = getattr(svc, SERVICE_NAME, None)
        if real_cls is None:
            return
        if isinstance(real_cls, type) and getattr(real_cls, "_hwv_lazy", False):
            return

        _strip_dead_board_urls()

        def factory():
            return real_cls()

        class LazyArduinoCLIService:
            _hwv_lazy = True

            def __new__(cls):
                return _LazyService(factory)

        svc.ArduinoCLIService = LazyArduinoCLIService
        _patched["done"] = True
        print("[dsh_embagent] ArduinoCLIService 已替换为惰性版"
              "（DSH_EMBAGENT_EAGER_CLI=1 可关闭）")

    class _Finder(importlib.abc.MetaPathFinder):
        """在本模块 exec **之前**包一层 loader。"""

        def find_spec(self, fullname, path=None, target=None):
            if fullname != TARGET:
                return None
            for finder in sys.meta_path:
                if finder is self:
                    continue
                find = getattr(finder, "find_spec", None)
                if find is None:
                    continue
                spec = find(fullname, path, target)
                if spec is None or spec.loader is None:
                    continue
                original_exec = getattr(spec.loader, "exec_module", None)
                if original_exec is None:
                    return spec

                def exec_module(module, _orig=original_exec):
                    patch_service_module()
                    _orig(module)

                spec.loader.exec_module = exec_module
                return spec
            return None

    sys.meta_path.insert(0, _Finder())


try:
    _install()
except Exception as exc:
    import sys as _sys
    print("[dsh_embagent] sitecustomize 未生效：%s: %s"
          % (type(exc).__name__, exc), file=_sys.stderr)
'''

PATCH_BODY = PATCH_DOC


def _home() -> Path:
    return Path(os.environ.get("USERPROFILE") or os.environ.get("HOME") or Path.home())


def sitecustomize_path() -> Path:
    """venv 的 sitecustomize 落点。"""
    return _home() / ".dsh" / "dsh_embagent" / "venv" / "Lib" / "site-packages" / "sitecustomize.py"


def _expected_digest() -> str:
    return hashlib.sha256(PATCH_BODY.encode("utf-8")).hexdigest()[:16]


def status() -> dict[str, Any]:
    """补丁在不在、是不是当前版本。**只读**。"""
    target = sitecustomize_path()
    exists = target.is_file()
    digest = ""
    matches = False
    if exists:
        try:
            blob = target.read_text(encoding="utf-8")
        except OSError as exc:
            return {"installed": False, "path": str(target),
                    "findings": [f"文件在但读不出来：{exc}"]}
        digest = hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]
        matches = digest == _expected_digest()
    return {
        "installed": exists,
        "path": str(target),
        "digest": digest,
        "expected": _expected_digest(),
        "up_to_date": matches,
        "eager": os.environ.get("DSH_EMBAGENT_EAGER_CLI", "").strip() in ("1", "true", "yes"),
        "note": ("补丁存在且是当前版本" if matches
                 else "补丁缺失或是旧版本，用 patch install 刷新" if exists
                 else "补丁未安装；后端启动会慢约 76 秒（详见 dsh_embagent/patch.py）"),
    }


def install() -> dict[str, Any]:
    """写入/刷新补丁（幂等）。"""
    target = sitecustomize_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        # 二进制写：避免 Windows 的换行转换污染 Python 源码（\r\n 在
        # 字符串字面量里无害，但会让 digest 对不上，从而"每次都判定过期"）。
        target.write_bytes(PATCH_BODY.encode("utf-8"))
    except OSError as exc:
        return {"status": "failed", "path": str(target),
                "findings": [f"写不进去：{exc}",
                             "venv 还没建？先跑 serve prepare。"]}
    return {"status": "ok", "path": str(target), "digest": _expected_digest(),
            "bytes": len(PATCH_BODY.encode("utf-8"))}


def remove() -> dict[str, Any]:
    """删除补丁，完全还原上游行为。"""
    target = sitecustomize_path()
    if not target.is_file():
        return {"status": "ok", "removed": False, "note": "本来就没有"}
    try:
        target.unlink()
    except OSError as exc:
        return {"status": "failed", "findings": [f"删不掉：{exc}"]}
    return {"status": "ok", "removed": True, "path": str(target)}


def ensure() -> dict[str, Any]:
    """幂等：缺失/过期就装。`serve.ensure_venv()` 装完依赖后调它。"""
    current = status()
    if current.get("installed") and current.get("up_to_date"):
        return {"status": "ok", **current, "written": False}
    out = install()
    return {"status": out["status"], **status(), "written": out["status"] == "ok"}


def main(argv: list[str] | None = None) -> int:
    import json
    import sys

    args = list(sys.argv[1:] if argv is None else argv)
    cmd = args[0] if args else "status"
    if cmd == "status":
        out = status()
    elif cmd == "install":
        out = install()
        out.update(status())
    elif cmd == "remove":
        out = remove()
    elif cmd == "ensure":
        out = ensure()
    else:
        out = {"status": "failed", "findings": [f"未知动作 {cmd}（status/install/remove/ensure）"]}
    blob = json.dumps(out, ensure_ascii=False, default=str).encode("utf-8") + b"\n"
    sys.stdout.buffer.write(blob)
    sys.stdout.buffer.flush()
    return 0 if str(out.get("status", "ok")) in ("ok", "not_ready") else 1


if __name__ == "__main__":
    raise SystemExit(main())
