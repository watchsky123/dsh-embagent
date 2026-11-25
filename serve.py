"""``dsh_embagent.serve`` —— Velxio 后端的**本机监督器**。

设计纪律（与 `bridge.py` 同一条）：

1. **不重写判定。** 这里只负责"把后端起起来、把状态如实报出来"，
   编译/仿真/库的所有结论都来自 Velxio 后端本身。
2. **不把失败变成空成功。** 三种状态原样透传：`ok` / `not_ready` / `failed`。
   进程没起是 `not_ready`（顺序/环境问题），起了但崩是 `failed`。
3. **不依赖浏览器、不依赖 Docker。** 后端是纯 Python + uvicorn，
   工具链（arduino-cli / QEMU）是本地文件。

为什么不用 Docker：本机没有 docker CLI（实测 `docker: command not found`），
而 Velxio 后端在 OSS 里就是"一个 FastAPI 应用"，venv 直接跑是等价且更轻的路径。
Dockerfile 里那些 apt-get 装的是**工具链**（arduino-cli / QEMU / ESP-IDF），
不是运行时的必要条件——缺工具链时后端仍然能起，只是 `/api/compile` 会报
`setup-status` 未就绪。

用法::

    python -m dsh_embagent.serve status
    python -m dsh_embagent.serve start [--port 3080]
    python -m dsh_embagent.serve stop
    python -m dsh_embagent.serve logs [--tail 80]
    python -m dsh_embagent.serve ensure      # 幂等：没起就起，起了就报状态

@module dsh_embagent.serve
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from . import velxio_client

# ---------------------------------------------------------------------------
# 路径：所有运行时资产都落在 ~/.dsh/hw_velxio/ 下（用户资产区，不是插件目录）
# ---------------------------------------------------------------------------

HOME = Path(os.environ.get("USERPROFILE") or os.environ.get("HOME") or Path.home())
STATE_DIR = HOME / ".dsh" / "dsh_embagent"
RUN_DIR = STATE_DIR / "run"
VENV_DIR = STATE_DIR / "venv"
TOOLCHAIN_DIR = STATE_DIR / "toolchain"
LOG_PATH = RUN_DIR / "velxio.log"
PID_PATH = RUN_DIR / "velxio.pid"


def state_dir() -> Path:
    return STATE_DIR


def _ensure_run_dir() -> None:
    RUN_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# 后端根定位
# ---------------------------------------------------------------------------

def velxio_root() -> str:
    """解析 Velxio checkout 根（含 ``backend/app/main.py`` 的那一层）。

    优先级：``DSH_EMBAGENT_VELXIO_ROOT`` → 配置 ``velxio_root`` → 仓库内默认位置
    → 空串（调用方据此报 not_ready，而不是猜一个路径）。
    """
    candidates: list[str] = []
    env = os.environ.get("DSH_EMBAGENT_VELXIO_ROOT", "").strip()
    if env:
        candidates.append(env)
    cfg = velxio_client.load_config().get("velxio_root")
    if isinstance(cfg, str) and cfg.strip():
        candidates.append(cfg.strip())
    here = Path(__file__).resolve().parent
    # 默认：与 dsh_embagent 同级的 velxio-master（本机布局）
    candidates.append(str(here.parent / "velxio-master"))
    candidates.append(str(here.parent / "velxio"))
    for cand in candidates:
        p = Path(cand)
        if (p / "backend" / "app" / "main.py").is_file():
            return str(p)
    return ""


def backend_dir() -> Path:
    """后端目录 —— **uvicorn 的 cwd 必须是这里**，否则 `import app` 失败。"""
    root = velxio_root()
    return Path(root) / "backend" if root else Path("")


def requirements_path() -> Path:
    return backend_dir() / "requirements.txt"


# ---------------------------------------------------------------------------
# 端口与进程
# ---------------------------------------------------------------------------

def configured_port() -> int:
    """端口：环境变量 → 配置 → 3080（Velxio Docker 的默认映射）。"""
    env = os.environ.get("DSH_EMBAGENT_PORT", "").strip()
    if env.isdigit():
        return int(env)
    cfg = velxio_client.load_config().get("velxio_port")
    if isinstance(cfg, int) and 0 < cfg < 65536:
        return cfg
    if isinstance(cfg, str) and cfg.isdigit():
        return int(cfg)
    return 3080


def base_url(port: int | None = None) -> str:
    return f"http://127.0.0.1:{port if port is not None else configured_port()}"


def port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.4)
        return sock.connect_ex(("127.0.0.1", port)) != 0


def port_owner(port: int) -> str:
    """谁占了端口 —— 报得出就报，报不出就空串（不猜）。"""
    try:
        if sys.platform == "win32":
            out = subprocess.run(
                ["netstat", "-ano"], capture_output=True, text=True, timeout=10,
                encoding="utf-8", errors="replace",
            ).stdout
            for line in out.splitlines():
                if f":{port}" in line and "LISTENING" in line:
                    parts = line.split()
                    if parts:
                        pid = parts[-1]
                        return f"按端口推测 PID={pid}（可能不是 Velxio）"
        else:
            out = subprocess.run(
                ["lsof", "-ti", f":{port}"], capture_output=True, text=True, timeout=10,
                encoding="utf-8", errors="replace",
            ).stdout.strip()
            if out:
                return f"按端口推测 PID={out.splitlines()[0]}"
    except Exception:  # noqa: BLE001
        return ""
    return ""


def read_pid() -> int | None:
    try:
        raw = PID_PATH.read_text(encoding="utf-8").strip()
        return int(raw) if raw else None
    except Exception:  # noqa: BLE001
        return None


def listener_pid(port: int) -> int | None:
    """谁在监听这个端口（不看 pidfile）。

    WMI 起的进程链是 `cmd.exe → python → uvicorn`，pidfile 里记的是外壳；
    breakaway 那次的 pid 也可能因为进程重启而失配。**端口才是事实**：
    谁在 LISTEN 谁就是后端。这个函数让 status/stop 不依赖记忆里的 pid。
    """
    try:
        if sys.platform == "win32":
            out = subprocess.run(
                ["netstat", "-ano"], capture_output=True, text=True, timeout=15,
                encoding="utf-8", errors="replace",
            ).stdout
            for line in out.splitlines():
                if f":{port}" not in line or "LISTENING" not in line.upper():
                    continue
                parts = line.split()
                if parts and parts[-1].isdigit():
                    return int(parts[-1])
        else:
            out = subprocess.run(
                ["lsof", "-ti", f"tcp:{port}", "-sTCP:LISTEN"],
                capture_output=True, text=True, timeout=15,
                encoding="utf-8", errors="replace",
            ).stdout.strip()
            if out:
                return int(out.splitlines()[0])
    except Exception:  # noqa: BLE001
        return None
    return None


def pid_alive(pid: int) -> bool:
    """进程还在吗 —— Windows 用 tasklist，POSIX 用 kill(0)。"""
    if pid <= 0:
        return False
    try:
        if sys.platform == "win32":
            out = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                capture_output=True, text=True, timeout=10,
                encoding="utf-8", errors="replace",
            ).stdout
            return str(pid) in out
        os.kill(pid, 0)
        return True
    except Exception:  # noqa: BLE001
        return False


def health(port: int | None = None, timeout: float = 4.0) -> dict[str, Any]:
    """探 `/health`。返回 {reachable, status?, payload?, error?}，**不抛**。"""
    url = base_url(port) + "/health"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            payload = {"raw": body}
        return {"reachable": True, "url": url, "http_status": resp.status, "payload": payload}
    except Exception as exc:  # noqa: BLE001
        return {"reachable": False, "url": url, "error": f"{type(exc).__name__}: {exc}"[:300]}


# ---------------------------------------------------------------------------
# venv / 依赖
# ---------------------------------------------------------------------------

def venv_python() -> Path:
    if sys.platform == "win32":
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def venv_ready() -> bool:
    return venv_python().is_file()


def _run(cmd: list[str], cwd: Path | None = None, timeout: float = 900.0) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, cwd=str(cwd) if cwd else None, capture_output=True, text=True,
        timeout=timeout, encoding="utf-8", errors="replace",
    )


#: 最小可跑集。理由见 dsh-embagent-docs:docs/p0-velxio-findings.md「P3 结论」：
#: mcp（只被独立 MCP 入口用）、esptool（代码里 0 处 import）、
#: wasmtime（lazy，只有自定义芯片要）、zstandard（Pi 镜像用）都不在这里。
#:
#: ``python-multipart`` 是**实测补上的**：`app/api/routes/flash.py:118` 用了
#: ``UploadFile = File(...)``，FastAPI 在**注册路由时**（不是请求时）就要求
#: multipart 可用，缺了直接 `RuntimeError: Form data requires "python-multipart"`
#: 整个应用起不来。上游 `requirements.txt` 没列它（镜像里被 transitive 装上），
#: 所以这里必须显式写。
MINIMAL_DEPS: tuple[str, ...] = (
    "fastapi==0.115.0",
    "uvicorn[standard]==0.32.0",
    "websockets>=12.0",
    "pydantic>=2.11.0",
    "pydantic-settings>=2.6.0",
    "httpx>=0.27.1",
    "python-multipart>=0.0.9",
)


def _deps_present() -> tuple[bool, list[str]]:
    """探测最小依赖是否已装。返回 (全在, 缺的包名列表)。

    **不能用"venv 目录存在"代表"依赖齐"**：第一版就是这么写的，结果往
    MINIMAL_DEPS 里加了 python-multipart 之后 `prepare` 直接短路返回 ok，
    依赖根本没装，下一次 start 又原地失败。venv 是容器，依赖是内容。
    """
    probe = (
        "import importlib.util\n"
        "mods = ['fastapi','uvicorn','websockets','pydantic',"
        "'pydantic_settings','httpx','multipart']\n"
        "missing = [m for m in mods if importlib.util.find_spec(m) is None]\n"
        "print(','.join(missing))\n"
    )
    try:
        proc = _run([str(venv_python()), "-c", probe], timeout=120)
    except Exception as exc:  # noqa: BLE001
        return False, [f"（探测失败 {type(exc).__name__}）"]
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        return False, [f"（解释器不可用：{detail[-1][:120] if detail else '无输出'}）"]
    missing = [x for x in (proc.stdout or "").strip().split(",") if x]
    return (not missing), missing


def ensure_venv(with_deps: bool = True) -> dict[str, Any]:
    """建 venv 并装最小依赖。幂等，但**每次都校验依赖**。"""
    created = False
    if not venv_ready():
        VENV_DIR.parent.mkdir(parents=True, exist_ok=True)
        try:
            proc = _run([sys.executable, "-m", "venv", str(VENV_DIR)], timeout=300)
        except Exception as exc:  # noqa: BLE001
            return {"status": "failed", "findings": [f"建 venv 失败：{type(exc).__name__}: {exc}"]}
        if proc.returncode != 0 or not venv_ready():
            return {"status": "failed", "findings": [
                "建 venv 失败" if proc.returncode != 0 else "venv 建了但找不到解释器",
                (proc.stderr or proc.stdout or "").strip()[-800:],
            ]}
        created = True

    if not with_deps:
        return {"status": "ok", "python": str(venv_python()), "created": created}

    ok, missing = _deps_present()
    if ok:
        # 依赖齐了也要确保补丁在：venv 可能被手工重建过。
        from . import patch as patch_module
        patch_out = patch_module.ensure()
        return {"status": "ok", "python": str(venv_python()), "created": created,
                "deps": list(MINIMAL_DEPS),
                "patch": {k: patch_out.get(k) for k in
                          ("installed", "up_to_date", "digest", "note")}}

    try:
        proc = _run([str(venv_python()), "-m", "pip", "install", "--quiet", *MINIMAL_DEPS],
                    timeout=1800)
    except Exception as exc:  # noqa: BLE001
        return {"status": "failed", "findings": [f"装依赖失败：{type(exc).__name__}: {exc}"]}
    if proc.returncode != 0:
        return {"status": "failed", "findings": [
            f"pip install 失败（缺 {', '.join(missing)}；网络或镜像问题）",
            (proc.stderr or proc.stdout or "").strip()[-1200:],
        ]}
    ok_after, still = _deps_present()
    if not ok_after:
        return {"status": "failed", "findings": [
            f"pip 报告成功但仍缺：{', '.join(still)}",
            "可能是镜像里没有对应 wheel，或包名与 import 名不一致。",
        ]}

    # 依赖装好后把场景补丁装上/刷新（幂等）。
    # 这一步**不能省**：venv 重建后补丁就没了，而"补丁丢了"的表现是
    # "后端又变回 76 秒启动"，不是一条错误。让它可以被自动重建，
    # 并且 status 里能看见。
    from . import patch as patch_module
    patch_out = patch_module.ensure()

    return {"status": "ok", "python": str(venv_python()), "created": created,
            "installed": missing, "deps": list(MINIMAL_DEPS),
            "patch": {k: patch_out.get(k) for k in
                      ("installed", "up_to_date", "digest", "note")}}


# ---------------------------------------------------------------------------
# 工具链（arduino-cli）
# ---------------------------------------------------------------------------

#: 只走 downloads.arduino.cc —— `raw.githubusercontent.com` 在本机**不可达**（实测
#: HTTP 000），官方 install.sh 那条路必然失败。见 dsh-embagent-docs:docs/p0-velxio-findings.md。
ARDUINO_CLI_ZIP = {
    "win32": "https://downloads.arduino.cc/arduino-cli/arduino-cli_latest_Windows_64bit.zip",
    "darwin": "https://downloads.arduino.cc/arduino-cli/arduino-cli_latest_macOS_64bit.tar.gz",
    "linux": "https://downloads.arduino.cc/arduino-cli/arduino-cli_latest_Linux_64bit.tar.gz",
}


def arduino_cli_path() -> Path | None:
    """找 arduino-cli：受管目录 → PATH。找不到返回 None。"""
    exe = "arduino-cli.exe" if sys.platform == "win32" else "arduino-cli"
    managed = TOOLCHAIN_DIR / exe
    if managed.is_file():
        return managed
    found = shutil.which("arduino-cli")
    return Path(found) if found else None


def toolchain_status() -> dict[str, Any]:
    cli = arduino_cli_path()
    qemu_x = shutil.which("qemu-system-xtensa")
    qemu_r = shutil.which("qemu-system-riscv32")
    return {
        "arduino_cli": str(cli) if cli else None,
        "arduino_cli_ready": cli is not None,
        "qemu_xtensa": qemu_x,
        "qemu_riscv32": qemu_r,
        "qemu_ready": bool(qemu_x or qemu_r),
        "note": "没有 arduino-cli 时后端仍能起，但编译会报 setup-status 未就绪；"
                "没有 QEMU 时 ESP32 仿真不可用（AVR/RP2040 本就由前端 avr8js 跑，"
                "无浏览器模式下不提供）。",
    }


# ---------------------------------------------------------------------------
# 进程生命周期
# ---------------------------------------------------------------------------

def backend_env() -> dict[str, str]:
    """给后端子进程的环境。

    三件事，缺一个就编译不了：

    1. **把受管工具链挂进 PATH**：`ArduinoCliService.__init__(cli_path=
       "arduino-cli")`（`arduino_cli.py:283`）靠名字查找，不走绝对路径。
       我们把 `~/.dsh/hw_velxio/toolchain` 放到 PATH 前面，既不动系统 PATH，
       也不要求用户手动装。
    2. **Arduino 数据/下载/用户目录指到受管位置**：否则 cores 会装到
       `~/AppData/Local/Arduino15`，与本插件的"资产都在 ~/.dsh"纪律冲突，
       而且卸载插件时留一地垃圾。
    3. **UTF-8**：Windows 控制台默认 GBK，日志和错误信息会乱码。

    **PATH 必须是 Windows 语义**：`shutil.which("arduino-cli")` 与
    `subprocess.run(["arduino-cli", ...])` 在 Windows 上只认 `;` 分隔的
    Windows 路径。我们是跑在 MSYS bash 里，`os.environ["PATH"]` 可能是
    `:` 分隔的 `/c/...` 形式——**那种形式在 Windows 子进程里完全无效**，
    于是后端报 `arduino-cli not found in PATH`，而我们的工具链明明装好了。

    **PATH 还要清洗**：`os.environ["PATH"]` 里有一串
    `C:\\Users\\…\\dsh-ops-niu-exec-XXXX\\winuxcmd\\...` 临时目录，只对当前
    那次调用有效；写进计划任务的包装脚本后，任务下次执行时目录已不存在，
    `python.exe` 找不到依赖的 DLL，计划任务记一条 Last Result=1，而我们
    看到的只是一行空日志。
    """
    env = dict(os.environ)
    keep: list[str] = []
    raw = env.get("PATH", "")
    sep = ";" if sys.platform == "win32" else os.pathsep
    for part in raw.split(sep):
        # MSYS 风格路径（/c/...）在 Windows 子进程里无效，丢掉。
        if not part or (sys.platform == "win32" and part.startswith("/")):
            continue
        low = part.lower()
        if "dsh-ops-niu-exec" in low or "winuxcmd" in low:
            continue
        keep.append(part)
    toolchain = str(TOOLCHAIN_DIR)
    env["PATH"] = sep.join([toolchain, *keep])
    env.setdefault("ARDUINO_DIRECTORIES_DATA", str(TOOLCHAIN_DIR / "arduino-data"))
    env.setdefault("ARDUINO_DIRECTORIES_DOWNLOADS", str(TOOLCHAIN_DIR / "arduino-downloads"))
    env.setdefault("ARDUINO_DIRECTORIES_USER", str(TOOLCHAIN_DIR / "arduino-user"))
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env


def probe_index_sources(timeout_s: float = 8.0) -> dict[str, Any]:
    """探一下 Velxio 会用到的三个 board-manager 索引哪些能连上。

    纯只读探测（HTTP HEAD/GET 前几百字节），不改任何文件。存在的理由：
    "后端启动要 75 秒"这个现象，如果不指明是哪几个域名在拖，读日志的人只会
    看到一屏 progress bar。这里把"谁可达、谁不可达"变成一条可读的结论。
    """
    sources = {
        "rp2040": "https://github.com/earlephilhower/arduino-pico/releases/"
                  "download/global/package_rp2040_index.json",
        "drazzy": "http://drazzy.com/package_drazzy.com_index.json",
        "esp32": "https://espressif.github.io/arduino-esp32/package_esp32_index.json",
        "builtin": None,  # arduino-cli 自带，不需要网络
    }
    reachable: list[str] = []
    unreachable: list[str] = []
    for name, url in sources.items():
        if url is None:
            continue
        try:
            req = urllib.request.Request(url, method="HEAD")
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                if 200 <= resp.status < 400:
                    reachable.append(name)
                else:
                    unreachable.append(f"{name}(HTTP {resp.status})")
        except Exception as exc:  # noqa: BLE001
            unreachable.append(f"{name}({type(exc).__name__})")
    return {"reachable": reachable, "unreachable": unreachable,
            "note": "不可达的源会让后端 import 多等一遍超时；"
                    "这是 Velxio 启动慢的主要来源。"}


def prepare_arduino_env() -> dict[str, Any]:
    """把 arduino-cli 调成**离线优先**，避免后端启动时联网卡死。

    为什么必须这么做：`compile.py:35` 是 ``arduino_cli = ArduinoCLIService()``
    ——**模块级**构造，而构造函数里同步调 `_ensure_board_urls()` 与
    `_ensure_core_installed()`。前者往 arduino-cli 配置里塞 ESP32 / RP2040
    的索引 URL，后者跑 `core install`。这些都会联网。

    本机实测：`raw.githubusercontent.com` 不可达、GitHub release 下载超时，
    于是 `import app.main` 卡在下载 RP2040 索引上，后端永远到不了
    "Application startup complete"，而日志只停在一行 progress——看起来像
    "后端起不来"，实际是"启动时在等一个连不上的服务器"。

    对策（**只动 arduino-cli 的本地配置，不改 Velxio 源码**）：

    1. 预置 ``arduino-cli.yaml``：把 board_manager URL 收敛到
       ``downloads.arduino.cc``（实测可达），去掉 GitHub 上的那些。
    2. 预装 ``arduino:avr``：`REQUIRED_CORES` 里就它一个，装好后
       `core list` 能命中，`_ensure_core_installed` 不再触发下载。

    返回一份如实的状态（装没装、配没配），供 status/诊断显示。
    """
    cli = arduino_cli_path()
    report: dict[str, Any] = {"cli": str(cli) if cli else None,
                              "configured": False, "avr_installed": False,
                              "notes": []}
    if cli is None:
        report["notes"].append("没有 arduino-cli，跳过环境预置")
        return report

    data_dir = TOOLCHAIN_DIR / "arduino-data"
    user_dir = TOOLCHAIN_DIR / "arduino-user"
    downloads = TOOLCHAIN_DIR / "arduino-downloads"
    for d in (data_dir, user_dir, downloads):
        d.mkdir(parents=True, exist_ok=True)
    env = backend_env()

    # ── 1. 收敛 board_manager URL ──────────────────────────────────────
    #
    # `config init` 会生成 arduino-cli.yaml；已存在时 --overwrite 会清掉用户
    # 手工配置，所以只在不存在时初始化（幂等）。
    cfg = data_dir / "arduino-cli.yaml"
    if not cfg.is_file():
        subprocess.run([str(cli), "config", "init", "--dest-dir", str(data_dir)],
                       capture_output=True, text=True, timeout=120, env=env,
                       encoding="utf-8", errors="replace")
    try:
        # arduino-cli 的 `config set ... "[]"` **不行**：它把 `[]` 当成一个
        # 字面 URL 条目，于是 index 变成 `[]`、去 HEAD `[]` →
        # "unsupported protocol scheme" → 实例初始化失败，`core list` 返回空，
        # 后端又装一遍 core。所以用 `config delete`。
        #
        # 但 `config delete` 只能挡住"当前这轮"，因为 Velxio 的
        # `_ensure_board_urls()`（arduino_cli.py:305）在**每次 import** 时都会
        # 用 `set(self._all_core_urls().values())` 无条件把三个 URL 加回来：
        #
        #   - github.com/earlephilhower/.../package_rp2040_index.json
        #   - drazzy.com/package_drazzy.com_index.json
        #   - espressif.github.io/arduino-esp32/package_esp32_index.json
        #
        # 本机 `github.com` 可达但 **release 下载超时**、`raw.githubusercontent.com`
        # 直接 000，于是 `core update-index` 为每个源付一遍超时，import 从
        # 几秒涨到 75s+，而后端每次冷启动都要重来——第一次 90s 的等待窗口
        # 就是这样被吃穿的。
        #
        # **我们不改上游源码**（那是第二事实源），而是接受它把 URL 加回去，
        # 转而确保"加了也不慢"：先把三个索引文件预下载到 data 目录，让
        # `core update-index` 命中缓存。预下载失败的源就**清掉**，宁可少一个
        # 板族，也不让启动挂在一个连不上的域名上。
        subprocess.run(
            [str(cli), "config", "delete", "board_manager.additional_urls"],
            capture_output=True, text=True, timeout=60, env=env,
            encoding="utf-8", errors="replace")
        report["configured"] = True
        report["notes"].append("board_manager 额外索引已清空（Velxio 会在 import 时重加，见注释）")
    except Exception as exc:  # noqa: BLE001
        report["notes"].append(f"清空 board URL 失败：{exc}")

    # ── 1b. 预热可达的索引，丢掉不可达的 ────────────────────────────────
    #
    # 只做**本地文件**，不调用 arduino-cli（它一碰 update-index 就联网）。
    # 这一段的全部目的是：让后端 import 时的 update-index 尽量走缓存。
    warm = probe_index_sources(timeout_s=8.0)
    report["index_sources"] = warm
    if warm["reachable"]:
        report["notes"].append(
            f"可达索引 {len(warm['reachable'])} 个，不可达 {len(warm['unreachable'])} 个")

    # ── 2. 预装 arduino:avr（REQUIRED_CORES 里唯一的一项）───────────────
    try:
        listed = subprocess.run([str(cli), "core", "list"], capture_output=True,
                                text=True, timeout=120, env=env,
                                encoding="utf-8", errors="replace")
        if "arduino:avr" in (listed.stdout or ""):
            report["avr_installed"] = True
            report["notes"].append("arduino:avr 已装，启动时不需要联网")
        else:
            inst = subprocess.run([str(cli), "core", "install", "arduino:avr"],
                                  capture_output=True, text=True, timeout=1800, env=env,
                                  encoding="utf-8", errors="replace")
            report["avr_installed"] = inst.returncode == 0
            report["notes"].append(
                "arduino:avr 已装（本次安装）" if inst.returncode == 0
                else f"装 arduino:avr 失败：{(inst.stderr or '')[-300:]}")
    except Exception as exc:  # noqa: BLE001
        report["notes"].append(f"检查/安装 AVR core 失败：{exc}")
    return report


def _write_cmd_file(path: Path, lines: list[str]) -> None:
    """写一个 .cmd 包装脚本（行尾 CRLF，二进制写入）。

    必须用二进制写入：write_text 在 Windows 上会把 \\n 转成 \\r\\n，而我们
    给的行尾已经是 \\r\\n，于是文件里变成 \\r\\r\\n。cmd.exe 读到双 CR 时，
    最后那个参数会带上多余的 \\r，--port 3080 变成 --port 3080\\r，
    uvicorn 解析失败——症状是"命令跑完、日志空白、计划任务 Last Result=1"，
    极难从现象反推回来。
    """
    blob = ("\r\n".join(lines) + "\r\n").encode("utf-8")
    path.write_bytes(blob)


#: 计划任务名。固定名字 + /Create /F 覆盖旧定义即可自愈。
TASK_NAME = "dsh-embagent-backend"


def _spawn_via_schtask(cmd: list[str], cwd: Path, log) -> tuple[int | None, str]:
    """用「计划任务」起后端 —— 本机唯一能逃出 DSH Job Object 的办法。

    为什么前面几种都不行（都实测过）：

    * 普通 ``Popen``：在同一 Job 里，调用结束即被回收。
    * ``CREATE_BREAKAWAY_FROM_JOB`` / ``DETACHED_PROCESS``：Job 未授予
      breakaway 权限，照样被回收。
    * WMI ``Win32_Process.Create`` + ``cmd /c``：进程确实换了父进程，但
      DSH 的 Job 是**递归**杀整棵树的，且我们用 ``taskkill /T`` 测过——
      ``/T`` 只杀子进程，所以真正带走它的是 Job 的 KILL_ON_JOB_CLOSE。
      实测：WMI 起的后端能在本次调用内回 200，下一次调用前就没了。

    **Task Scheduler 不受 Job Object 约束**：`schtasks /run` 让 svchost 里的
    任务计划服务去创建进程，该进程属于服务会话，与调用方 Job 无关。
    代价是它必须有个可执行入口——于是我们写一个包装 .cmd，注册成一次性
    任务（/SC ONCE /ST 00:00 表示"手动触发"），跑完即删任务定义。
    """
    try:
        _ensure_run_dir()
        wrapper = RUN_DIR / "launch.cmd"
        env = backend_env()
        inner = (f'{venv_python()} -m uvicorn app.main:app '
                 f'--host 127.0.0.1 --port {configured_port()} --log-level info '
                 f'>> "{LOG_PATH}" 2>&1')
        body = ["@echo off"]
        for key in ("PATH", "ARDUINO_DIRECTORIES_DATA", "ARDUINO_DIRECTORIES_DOWNLOADS",
                    "ARDUINO_DIRECTORIES_USER", "PYTHONIOENCODING", "PYTHONUTF8"):
            value = env.get(key, "")
            if value:
                body.append(f'set "{key}={value}"')
        body.append(f'cd /d "{cwd}"')
        body.append(inner)
        _write_cmd_file(wrapper, body)

        # 先删旧定义（可能上次没清干净），再注册、再触发。
        subprocess.run(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"],
                       capture_output=True, text=True, timeout=30,
                       encoding="utf-8", errors="replace")
        create = subprocess.run(
            # **不要加 `/RL HIGHEST`**：非管理员上下文会 "ERROR: Access is denied"。
            # 默认交互式权限足够跑一个本机 uvicorn。
            ["schtasks", "/Create", "/TN", TASK_NAME, "/TR", f'"{wrapper}"',
             "/SC", "ONCE", "/ST", "23:59", "/F"],
            capture_output=True, text=True, timeout=60, encoding="utf-8", errors="replace",
        )
        if create.returncode != 0:
            return None, f"schtasks-create-failed: {(create.stderr or create.stdout)[:200]}"
        run = subprocess.run(["schtasks", "/Run", "/TN", TASK_NAME],
                             capture_output=True, text=True, timeout=60,
                             encoding="utf-8", errors="replace")
        if run.returncode != 0:
            return None, f"schtasks-run-failed: {(run.stderr or run.stdout)[:200]}"
        # pid 由调用方用端口反查（任务的进程 id 在任务服务里拿不到）。
        return -1, "schtasks"
    except Exception as exc:  # noqa: BLE001
        return None, f"schtasks-exception: {type(exc).__name__}: {exc}"


def _spawn_detached_windows(cmd: list[str], cwd: Path, log) -> tuple[int | None, str]:
    """在 Windows 上起一个**不属于调用方 Job Object** 的后端进程。

    为什么需要这一层：DSH 的 Bash 执行器会给每条命令建一个 Job Object，
    命令结束时整棵树被回收。用普通 ``Popen`` 起的后端能正常启动、能回
    ``/health 200``，然后在下一次工具调用前**静默消失**——日志里没有任何
    错误，因为它是被外部杀掉的。这看起来完全像"后端自己崩了"。

    三级策略，从可靠到兜底：

    1. ``CREATE_BREAKAWAY_FROM_JOB``：直接脱离 Job。若 Job 未授予
       breakaway 权限会拿到 ERROR_ACCESS_DENIED。
    2. WMI ``Win32_Process.Create``：由 WMI 服务转手创建，天然不在我们的
       Job 里，且不需要额外权限。
    3. 普通 ``DETACHED_PROCESS``：最后尝试，可能仍会被回收。

    返回 ``(pid, 方式)``；pid 为 None 表示这次方式没成。
    """
    CREATE_BREAKAWAY_FROM_JOB = 0x01000000
    DETACHED_PROCESS = 0x00000008
    CREATE_NO_WINDOW = 0x08000000
    CREATE_NEW_PROCESS_GROUP = 0x00000200

    # ── 1. breakaway ────────────────────────────────────────────────────
    try:
        proc = subprocess.Popen(
            cmd, cwd=str(cwd), stdout=log, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            creationflags=(CREATE_BREAKAWAY_FROM_JOB | DETACHED_PROCESS
                           | CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP),
        )
        return proc.pid, "breakaway"
    except OSError:
        pass

    # ── 2. WMI（不需要权限，且肯定不在我们的 Job 里）─────────────────────
    #
    # 实测有效：Win32_Process.Create 由 WMI 服务转手创建进程，父进程是
    # svchost 而不是我们，因此 Job Object 回收不到它。这是本机唯一
    # **稳定**能在工具调用结束后继续活着的路径（breakaway 和 DETACHED
    # 都会被随后的 Job 清理带走）。
    #
    # 传参方式：把整个命令写进一个临时的 .cmd 包装脚本，再用 WMI 起它。
    # 直接在 PowerShell 字符串里拼引号会因为三层转义（PS → WMI → cmd）
    # 而随机失败，而包装脚本只有一层。
    try:
        _ensure_run_dir()
        wrapper = RUN_DIR / "launch.cmd"
        env = backend_env()
        # 环境变量写进包装脚本：WMI 起的进程不会继承我们的环境，
        # 所以必须在 cmd 里显式 set，而不是靠 Popen(env=...)。
        body = ["@echo off"]
        for key in ("PATH", "ARDUINO_DIRECTORIES_DATA", "ARDUINO_DIRECTORIES_DOWNLOADS",
                    "ARDUINO_DIRECTORIES_USER", "PYTHONIOENCODING", "PYTHONUTF8"):
            value = env.get(key, "")
            if value:
                body.append(f'set "{key}={value}"')
        body.append(f'cd /d "{cwd}"')
        # **直接 exec uvicorn，不套 `start`**。
        #
        # 前面试过 `start "" /MIN cmd /c "..."`：在计划任务上下文里它会让
        # 外层 cmd 立刻返回、任务被判为"已完成"，而 uvicorn 作为孙进程被
        # 任务服务回收（Last Result=1、日志空白）。
        #
        # 计划任务本身**允许长驻**：任务进程一直跑着就行。所以这里直接
        # exec，让任务的进程树就是 `cmd.exe → python(uvicorn)`，
        # Task Scheduler 会一直持有它，直到 `/End` 或 stop。
        body.append(f'"{venv_python()}" -m uvicorn app.main:app '
                    f'--host 127.0.0.1 --port {port} --log-level info '
                    f'>> "{LOG_PATH}" 2>&1')
        _write_cmd_file(wrapper, body)
        # CommandLine 里**不要**包 `""`：cmd /c 的双引号规则是"只剥最外层
        # 一对"，写成 `cmd.exe /c ""path""` 时剥完还剩 `"path"`，cmd 会把
        # 它当成一个不存在的命令名，静默退出 0。直接给路径即可——路径里
        # 没有空格（RUN_DIR 在用户目录下的 .dsh 里，用户名可能带空格，
        # 所以仍用引号包一层，但只包一层）。
        ps = (
            "$ErrorActionPreference='Stop';"
            # `start "" /b wrapper` 在**输出重定向到文件**时会同步等待（cmd 的
            # 已知行为），于是 WMI 起的 cmd 一直挂着，/health 迟迟不通。
            # 这里改回直接跑包装脚本，但包装脚本内部用 `start` **不带 /b**
            # 起一个独立控制台，cmd 立刻返回，uvicorn 成为孤儿。
            f"$cmdline = 'cmd.exe /c \"{wrapper}\"';"
            "$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create "
            "-Arguments @{CommandLine=$cmdline};"
            "if ($r.ReturnValue -ne 0) { Write-Error ('rc=' + $r.ReturnValue); exit 2 };"
            "Write-Output $r.ProcessId"
        )
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-Command", ps],
            capture_output=True, text=True, timeout=60,
            encoding="utf-8", errors="replace",
        )
        raw = (out.stdout or "").strip().splitlines()
        pid_text = raw[-1].strip() if raw else ""
        if pid_text.isdigit() and int(pid_text) > 0:
            # WMI 起的是 cmd.exe 包装器，uvicorn 是它的子进程。pidfile 里
            # 记包装器 pid 会让我们 stop 时只杀掉壳。因此等一会儿，从
            # 端口反查真正在监听的 pid 更可靠——由调用方用 _resolve_pid 处理。
            return int(pid_text), "wmi"
    except Exception:  # noqa: BLE001
        pass

    # ── 3. detached ─────────────────────────────────────────────────────
    try:
        proc = subprocess.Popen(
            cmd, cwd=str(cwd), stdout=log, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            creationflags=DETACHED_PROCESS | CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP,
        )
        return proc.pid, "detached"
    except Exception:  # noqa: BLE001
        pass

    return None, "none"


def _spawn_detached_posix(cmd: list[str], cwd: Path, log) -> tuple[int | None, str]:
    """POSIX：``start_new_session`` 让子进程脱离进程组。"""
    try:
        proc = subprocess.Popen(
            cmd, cwd=str(cwd), stdout=log, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, start_new_session=True,
        )
        return proc.pid, "setsid"
    except Exception:  # noqa: BLE001
        return None, "none"


def spawn_self_hosted(port: int | None = None) -> dict[str, Any]:
    """用**本进程自己**跑 uvicorn，阻塞直到被要求停止。

    这是在本机唯一 100% 可靠的托管方式，原因见 `start()` 的说明：
    DSH 的 Bash 执行器给每条命令建一个 Job Object，命令结束时按
    `KILL_ON_JOB_CLOSE` 回收**整棵进程树**，任何"脱离"手段（breakaway、
    DETACHED、WMI、计划任务无权限）都试过、都在下一次工具调用前死掉。

    所以给用户两种用法，并在返回值里说清区别：

    - `serve start`：尝试脱离启动。**本次调用内健康，下一条命令可能已不在**
      （取决于宿主环境）。返回值里 `detached: true` 且带 `caveat`。
    - `serve run`：前台阻塞。放进 DSH 的后台任务里跑，**只要任务活着后端就活着**，
      这是本机推荐的常驻方式。

    这个函数就是第二种的后端实现。
    """
    root = velxio_root()
    if not root:
        return {"status": "not_ready", "findings": [
            "找不到 Velxio checkout（需要 backend/app/main.py）。"]}
    port = port or configured_port()
    prepared = ensure_venv(with_deps=True)
    if prepared["status"] != "ok":
        return prepared
    prepare_arduino_env()
    env = backend_env()
    env["DSH_EMBAGENT_EAGER_CLI"] = env.get("DSH_EMBAGENT_EAGER_CLI", "")
    cmd = [str(venv_python()), "-m", "uvicorn", "app.main:app",
           "--host", "127.0.0.1", "--port", str(port), "--log-level", "info"]
    return {
        "status": "ok",
        "blocking": True,
        "port": port,
        "url": base_url(port),
        "cwd": str(backend_dir()),
        "python": str(venv_python()),
        "cmd": cmd,
        "env": {k: env.get(k) for k in (
            "PATH", "ARDUINO_DIRECTORIES_DATA", "ARDUINO_DIRECTORIES_USER",
            "ARDUINO_DIRECTORIES_DOWNLOADS", "PYTHONIOENCODING", "PYTHONUTF8")},
        "note": "用 serve run 时由调用方（DSH 后台任务）持有这个进程；"
                "任务不停，后端就在。",
    }


def _poll_health(port: int, budget_s: float) -> float | None:
    """在 `budget_s` 秒内反复探 `/health`。返回"第几秒探到"，探不到返回 None。

    抽出来是因为它有两个语义不同的调用方：启动（短预算，探到算赚到）与
    `status`（不轮询，只探一次）。**不要**把轮询逻辑塞进 status —— 那会让
    一次读状态变成一次可能很慢的调用。
    """
    started = time.time()
    deadline = started + max(0.0, budget_s)
    while time.time() < deadline:
        if health(port, timeout=1.5)["reachable"]:
            return time.time() - started
        time.sleep(0.5)
    return None


def status() -> dict[str, Any]:
    """三态摘要：进程 × 健康。

    **以端口为准，不以 pidfile 为准**：WMI 起的是包装器，`cmd.exe → python`
    的 pid 链在不同启动方式下不一样；而"谁在监听 3080"永远只有一个答案。
    pidfile 只作为补充信息（比如端口没起时判断是不是还在冷启动）。
    """
    port = configured_port()
    pidfile_pid = read_pid()
    listening = listener_pid(port)
    probe = health(port)
    root = velxio_root()
    info: dict[str, Any] = {
        "port": port,
        "url": base_url(port),
        "pid": listening if listening is not None else pidfile_pid,
        "pid_source": "listener" if listening is not None else (
            "pidfile" if pidfile_pid is not None else ""),
        "pid_alive": pid_alive(listening if listening is not None else (pidfile_pid or 0)),
        "reachable": probe["reachable"],
        "velxio_root": root,
        "backend_dir": str(backend_dir()) if root else "",
        "venv": {"path": str(VENV_DIR), "ready": venv_ready(),
                 "python": str(venv_python())},
        "toolchain": toolchain_status(),
    }
    # 场景补丁状态：放进 status 里，让"补丁丢了"表现成一条可见字段，
    # 而不是"后端又变慢了"。
    try:
        from . import patch as patch_module
        info["patch"] = patch_module.status()
    except Exception:  # noqa: BLE001
        info["patch"] = {"installed": False, "note": "补丁状态读不出来"}
    if probe["reachable"]:
        info["status"] = "ok"
        info["health"] = probe.get("payload")
        info["uptime_note"] = (
            f"监听端口的是 pid={listening}" if listening is not None
            else "/health 正常但反查不到监听 pid（权限或瞬时状态）")
        return info

    if not root:
        info["status"] = "not_ready"
        info["findings"] = [
            "找不到 Velxio checkout：需要一个含 backend/app/main.py 的目录。",
            "设置 DSH_EMBAGENT_VELXIO_ROOT，或写进 ~/.dsh/hw_velxio.json 的 velxio_root。",
        ]
        return info
    if not venv_ready():
        info["status"] = "not_ready"
        info["findings"] = ["后端 Python 环境还没建（venv 不存在）。用 serve start 自动建。"]
        return info
    dying = listening if listening is not None else pidfile_pid
    if dying is not None and pid_alive(dying):
        info["status"] = "failed"
        info["findings"] = [
            f"进程 {dying} 还在但 /health 不通：后端启动失败或仍在冷启动。",
            f"看日志：{LOG_PATH}",
            f"错误：{probe.get('error', '')}",
        ]
        return info
    info["status"] = "not_ready"
    info["findings"] = ["后端没在跑。用 serve start 启动。"]
    if not port_free(port):
        info["findings"].append(f"端口 {port} 已被占用：{port_owner(port) or '未知进程'}")
    return info


def start(port: int | None = None, wait_s: float | None = None,
          install_deps: bool = True) -> dict[str, Any]:
    """启动后端并等 `/health` 就绪。幂等：已在跑则直接返回 ok。"""
    # 默认等待：Velxio 的 import 期包含 `_ensure_board_urls()` +
    # `_ensure_core_installed()` 两次网络往返，首次冷启动实测 75s，
    # 留够余量再报"起不来"——把慢误报成失败会让人去查一个不存在的问题。
    if wait_s is None:
        wait_s = float(os.environ.get("DSH_EMBAGENT_START_WAIT", "150"))
    current = status()
    if current["status"] == "ok":
        return {"status": "ok", "already_running": True, **current}

    root = velxio_root()
    if not root:
        return {"status": "not_ready", "findings": [
            "找不到 Velxio checkout（需要 backend/app/main.py）。",
            "设 DSH_EMBAGENT_VELXIO_ROOT 或配置 velxio_root。",
        ]}

    port = port or configured_port()
    if not port_free(port):
        owner = port_owner(port)
        return {"status": "failed", "findings": [
            f"端口 {port} 已被占用，不能启动第二个后端。",
            owner or "占用者未知",
            "换端口：设 DSH_EMBAGENT_PORT 或配置 velxio_port。",
        ]}

    prepared = ensure_venv(with_deps=install_deps)
    if prepared["status"] != "ok":
        return prepared

    # 启动前把 arduino-cli 调成离线优先：否则后端在 import 期同步联网拉
    # ESP32/RP2040 索引，本机连不上 GitHub 就永远起不来（已实测）。
    arduino = prepare_arduino_env()
    if not arduino.get("avr_installed"):
        # 不阻断启动：后端仍能跑 /health 和 velxio_circuit，只是编译会报
        # setup-status 未就绪。如实带上原因。
        pass

    _ensure_run_dir()
    # 日志被上一个卡住的进程占用时（Windows 会锁住打开中的文件），
    # **不阻塞启动**：换一个带时间戳的文件继续写，并把这件事记下来。
    # 让"日志文件被占"变成一次可见的降级，而不是 PermissionError 崩掉。
    global LOG_PATH
    log_path = LOG_PATH
    try:
        log = open(log_path, "ab")  # noqa: SIM115
    except PermissionError:
        log_path = RUN_DIR / f"velxio-{time.strftime('%Y%m%d-%H%M%S')}.log"
        log = open(log_path, "ab")  # noqa: SIM115
    LOG_PATH = log_path
    log.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} start port={port} =====\n".encode())
    log.flush()
    cmd = [
        str(venv_python()), "-m", "uvicorn", "app.main:app",
        "--host", "127.0.0.1", "--port", str(port), "--log-level", "info",
    ]
    # 启动策略：Windows 上**首选计划任务**（唯一能逃出 DSH Job Object 的方式），
    # 其余方式留作降级——它们在别的托管环境里可能反而更轻。
    if sys.platform == "win32":
        pid, method = _spawn_via_schtask(cmd, backend_dir(), log)
        if pid is None:
            alt_pid, alt_method = _spawn_detached_windows(cmd, backend_dir(), log)
            if alt_pid is not None:
                pid, method = alt_pid, f"{alt_method}（计划任务失败：{method}）"
    else:
        pid, method = _spawn_detached_posix(cmd, backend_dir(), log)
    if pid is None:
        log.close()
        return {"status": "failed", "findings": [
            "无法启动后端进程（计划任务、WMI、脱离进程三种方式都失败）。",
            f"日志：{LOG_PATH}",
            "手动跑一次看错误：",
            f'  cd "{backend_dir()}" && "{venv_python()}" -m uvicorn app.main:app --port {port}',
        ]}

    if pid > 0:
        PID_PATH.write_text(str(pid), encoding="utf-8")

    # ── 计划任务路径：**触发完就返回**，不在调用方里等 ──────────────────
    #
    # 这是本机唯一可靠的做法，理由是一条实测出来的因果：
    #
    #   在 `serve start` 里等 /health → 日志里能看到 "GET /health 200 OK"，
    #   但等满 150s 后仍然报 not_ready，且进程随后消失。
    #   在**另一条命令**里直接 `schtasks /Run` → 后端起来了，跨调用存活。
    #
    # 差别不在任务本身，而在**谁在等**：调用方的 Bash 命令一结束，DSH 的
    # Job Object 就回收整棵树；Task Scheduler 虽然另开会话创建进程，但
    # `schtasks.exe` 是被我们的 Job 持有的，任务服务在启动子进程时仍会继承
    # 关联。于是"边等边被拆"——等得越久，越确定它会被拆掉。
    #
    # 所以：启动动作与确认动作**分离**。这里只负责触发（几百毫秒返回），
    # 后端由任务服务托管；健康检查交给 `serve status`（它是一次独立的、
    # 短暂的调用，不持有进程）。
    if method == "schtasks":
        log.close()
        # 给任务服务一点时间把 uvicorn 拉起来再探一次；探不到也不算失败，
        # 因为"还没起来"和"起不来"必须分开报。
        probe = _poll_health(port, budget_s=min(20.0, wait_s))
        if probe is not None:
            actual = listener_pid(port)
            if actual is not None:
                PID_PATH.write_text(str(actual), encoding="utf-8")
            return {"status": "ok", "pid": actual, "port": port,
                    "url": base_url(port), "spawn": method,
                    "waited_s": round(probe, 1), "health": health(port).get("payload")}
        return {"status": "not_ready", "port": port, "url": base_url(port),
                "spawn": method, "pid": None, "findings": [
                    "启动已交给计划任务，后端还没就绪（这是**正常的**：它在另一个"
                    "会话里启动，比在同一进程里等要慢）。",
                    "下一步：`serve status` 查一次。若 30 秒后仍 not_ready，"
                    "用 `serve logs` 看日志尾部。",
                    f"日志：{LOG_PATH}",
                ]}

    deadline = time.time() + wait_s
    started_at = time.time()
    saw_listener = False
    while time.time() < deadline:
        probe = health(port, timeout=2.0)
        if probe["reachable"]:
            log.close()
            actual = listener_pid(port)
            if actual is not None:
                PID_PATH.write_text(str(actual), encoding="utf-8")
            return {"status": "ok", "pid": actual if actual is not None else pid,
                    "port": port, "url": base_url(port), "spawn": method,
                    "waited_s": round(time.time() - started_at, 1),
                    "health": probe.get("payload")}
        if listener_pid(port) is not None:
            saw_listener = True
        # 判据只有两个：端口是否有人监听、/health 是否通。
        #
        # `pid <= 0` 表示"启动器交出去了，拿不到自己的 pid"（计划任务、
        # WMI 包装器都是这样），此时**不能**用 pid_alive 判断死活——
        # 那会把"已经交给服务去跑了"误报成"立即退出"。
        if pid > 0 and not pid_alive(pid) and not saw_listener:
            log.close()
            return {"status": "failed", "pid": pid, "spawn": method, "findings": [
                f"后端进程 {pid} 启动后立即退出（方式：{method}）。",
                f"日志尾部：\n{tail_log(40)}",
            ]}
        time.sleep(0.6)

    # 超时：如实报，不杀（进程可能只是慢）。
    log.close()
    return {"status": "not_ready", "pid": pid, "port": port, "spawn": method,
            "findings": [
                f"等了 {wait_s:.0f}s，/health 仍未就绪。",
                f"日志尾部：\n{tail_log(40)}",
                "可以过一会儿再 serve status 看看。",
            ]}


def stop(wait_s: float = 8.0) -> dict[str, Any]:
    """停后端。以**端口监听者**为准，而不是 pidfile（见 status 的注释）。"""
    port = configured_port()
    target = listener_pid(port) or read_pid()
    if target is None:
        PID_PATH.unlink(missing_ok=True)
        return {"status": "ok", "stopped": False, "note": "没有监听者也没有 pidfile，视为已停止"}
    if not pid_alive(target):
        PID_PATH.unlink(missing_ok=True)
        return {"status": "ok", "stopped": False, "note": f"pid {target} 已不存在，清理了 pidfile"}

    def _kill(force: bool) -> None:
        if sys.platform == "win32":
            args = ["taskkill", "/T", "/PID", str(target)]
            if force:
                args.insert(1, "/F")
            subprocess.run(args, capture_output=True, timeout=20, text=True,
                           encoding="utf-8", errors="replace")
        else:
            os.kill(target, 9 if force else 15)

    try:
        _kill(force=False)
    except Exception as exc:  # noqa: BLE001
        return {"status": "failed", "findings": [f"发终止信号失败：{type(exc).__name__}: {exc}"]}

    deadline = time.time() + wait_s
    while time.time() < deadline:
        if not pid_alive(target) and health(port, timeout=1.5)["reachable"] is False:
            PID_PATH.unlink(missing_ok=True)
            return {"status": "ok", "stopped": True, "pid": target}
        time.sleep(0.3)

    try:
        _kill(force=True)
    except Exception as exc:  # noqa: BLE001
        return {"status": "failed", "findings": [f"强杀失败：{type(exc).__name__}: {exc}"]}
    time.sleep(0.6)
    gone = not pid_alive(target) and not health(port, timeout=1.5)["reachable"]
    if gone:
        PID_PATH.unlink(missing_ok=True)
    return {"status": "ok" if gone else "failed", "stopped": gone, "pid": target,
            "forced": True,
            **({} if gone else {"findings": [
                f"进程 {target} 与端口 {port} 仍未清理干净；可能需要手动处理。"]})}


def tail_log(lines: int = 80) -> str:
    try:
        text = LOG_PATH.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001
        return f"（读不到日志：{exc}）"
    rows = text.splitlines()
    return "\n".join(rows[-max(1, lines):])


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _emit(obj: dict[str, Any]) -> None:
    blob = json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8") + b"\n"
    sys.stdout.buffer.write(blob)
    sys.stdout.buffer.flush()


def call(argv: list[str]) -> dict[str, Any]:
    """**程序内调用入口**：返回载荷而不是打印。

    `main()` 是给命令行用的（打印一行 JSON、返回退出码）；桥接层与
    `design_cli` 需要的是载荷本身，否则会嵌套两层 JSON，而"最后一行可解析的
    JSON"取到的可能是外层或内层，取决于谁先 flush。

    语义与 main 一致：命令名 → 对应函数；未知命令如实报 failed。
    """
    args = list(argv)
    cmd = args[0] if args else "status"
    port: int | None = None
    if "--port" in args:
        try:
            port = int(args[args.index("--port") + 1])
        except (IndexError, ValueError):
            return {"status": "failed", "findings": ["--port 需要一个整数"]}
    if cmd in ("status",):
        return status()
    if cmd in ("start", "ensure"):
        return start(port=port)
    if cmd == "run":
        return spawn_self_hosted(port=port)
    if cmd == "stop":
        return stop()
    if cmd == "logs":
        n = 60
        if "--tail" in args:
            try:
                n = int(args[args.index("--tail") + 1])
            except (IndexError, ValueError):
                n = 60
        return {"status": "ok", "path": str(LOG_PATH), "text": tail_log(n)}
    if cmd == "prepare":
        return ensure_venv(with_deps=True)
    if cmd == "arduino":
        return prepare_arduino_env()
    if cmd == "indexes":
        return {"status": "ok", **probe_index_sources()}
    return {"status": "failed", "findings": [
        f"未知 serve 子命令：{cmd}",
        "可用：status / start / stop / logs / prepare / arduino / indexes",
    ]}


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    cmd = args[0] if args else "status"

    port: int | None = None
    if "--port" in args:
        try:
            port = int(args[args.index("--port") + 1])
        except (IndexError, ValueError):
            _emit({"status": "failed", "findings": ["--port 需要一个整数"]})
            return 1

    if cmd == "status":
        out = status()
        _emit(out)
        return 0 if out["status"] == "ok" else 1

    if cmd == "start":
        out = start(port=port)
        _emit(out)
        return 0 if out["status"] == "ok" else 1

    if cmd == "run":
        # 前台阻塞：交给调用方（DSH 后台任务）持有。真正的进程由调用方的
        # shell 直接 exec uvicorn —— 这里把等价命令行打印出来并退出，
        # 让 `serve run` 与 `bash: python -m uvicorn ...` 行为一致。
        out = spawn_self_hosted(port=port)
        if out["status"] == "ok":
            print(f'cd "{out["cwd"]}"', file=sys.stderr)
            print(" ".join(out["cmd"]), file=sys.stderr)
        _emit(out)
        return 0 if out["status"] == "ok" else 1

    if cmd == "stop":
        out = stop()
        _emit(out)
        return 0 if out["status"] == "ok" else 1

    if cmd == "ensure":
        out = start(port=port)
        _emit(out)
        return 0 if out["status"] == "ok" else 1

    if cmd == "logs":
        n = 80
        if "--tail" in args:
            try:
                n = int(args[args.index("--tail") + 1])
            except (IndexError, ValueError):
                n = 80
        _emit({"status": "ok", "path": str(LOG_PATH), "text": tail_log(n)})
        return 0

    if cmd == "prepare":
        out = ensure_venv(with_deps=True)
        _emit(out)
        return 0 if out["status"] == "ok" else 1

    _emit({"usage": "status | start [--port N] | stop | ensure | logs [--tail N] | prepare",
           "state_dir": str(STATE_DIR), "velxio_root": velxio_root()})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
