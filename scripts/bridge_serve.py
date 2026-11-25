"""把 easyeda-api 的 Bridge Server 用**计划任务**拉起来（不要用 bash 后台任务）。

    python -m dsh_embagent.scripts.bridge_serve [start|stop|status]

## 为什么不用 `node ... &` / `run_in_background`

实测两次教训：

1. DSH 的 Job Object 带 `KILL_ON_JOB_CLOSE`：bash 调用一结束，整棵进程树被回收
   （AGENTS §3.5 / §8.4）。所以"后台跑 node"活不过这一次调用。
2. 更糟的是：**长驻的后台 bash 任务会占住 niubash 的 exec tree**，
   之后每次 `ops_bash` 都报
   `EXECUTOR_UNAVAILABLE: unknown error, link ... awk.exe`。
   实测杀掉那个后台任务后 bash 立刻恢复。

所以长驻服务一律走 Windows 计划任务（`schtasks`），与 bash 生命周期无关。

## 它做什么

* `start`：写 `.cmd`（**二进制 CRLF**，AGENTS §3.5）→ 注册 ONCE 任务 → 跑 → 轮询
  `/health` 直到 `service == "easyeda-bridge"`，打印端口与 `edaConnected`
* `status`：只扫描 49620-49629 的 `/health`
* `stop`：`schtasks /End` + `/Delete`，并顺带杀掉占端口的 node

`edaConnected: false` 是**正常状态**，不是失败：说明 EDA 侧扩展没装/没勾
「允许外部交互」。这时 `start` 仍然返回 `ok`，并把原因写进 `note`。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

PORT_RANGE = range(49620, 49630)
SERVICE = "easyeda-bridge"
TASK_NAME = "hwv-eda-bridge"
SKILL_DIR = Path(os.path.expanduser("~")) / ".dsh" / "skills" / "easyeda-api"
BRIDGE_JS = SKILL_DIR / "scripts" / "bridge-server.mjs"
LOG_FILE = Path(os.path.expanduser("~")) / ".dsh" / "eda-bridge.log"


def _health(port: int, timeout: float = 0.6) -> dict | None:
    try:
        with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/health", timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, OSError, ValueError):
        return None


def find_port() -> tuple[int, dict] | None:
    for port in PORT_RANGE:
        data = _health(port)
        if data and data.get("service") == SERVICE:
            return port, data
    return None


def status() -> dict:
    found = find_port()
    if not found:
        return {"ok": True, "running": False,
                "note": "Bridge 没在跑；`start` 可以拉起"}
    port, data = found
    return {"ok": True, "running": True, "port": port,
            "edaConnected": bool(data.get("edaConnected")),
            "edaWindowCount": data.get("edaWindowCount"),
            "activeWindowId": data.get("activeWindowId"),
            "note": "" if data.get("edaConnected") else
            ("Bridge 活着但 EDA 没连上 —— 扩展没装，或装了没勾「允许外部交互」，"
             "或勾了没完全重启 EDA。装法见手册 §3.2")}


def _node_exe() -> str:
    import shutil
    return shutil.which("node") or "node"


def start(timeout_s: float = 20.0) -> dict:
    if not BRIDGE_JS.exists():
        return {"ok": False, "reason": f"没装 easyeda-api skill（缺 {BRIDGE_JS}）"}
    found = find_port()
    if found:
        port, data = found
        return {"ok": True, "started": False, "port": port,
                "edaConnected": bool(data.get("edaConnected")),
                "note": "Bridge 已在跑，没重启"}

    cmd_file = SKILL_DIR / "bridge-run.cmd"
    node = _node_exe()
    # **必须二进制写 CRLF**：文本模式写出 `\r\r\n`，会把 `--` 参数切断（§3.5）
    body = ("@echo off\r\n"
            f'cd /d "{SKILL_DIR}"\r\n'
            f'"{node}" "{BRIDGE_JS}" >> "{LOG_FILE}" 2>&1\r\n')
    cmd_file.write_bytes(body.encode("ascii", "replace"))

    subprocess.run(["schtasks", "/Create", "/TN", TASK_NAME, "/TR", str(cmd_file),
                    "/SC", "ONCE", "/ST", "23:59", "/F"],
                   capture_output=True, text=True)
    subprocess.run(["schtasks", "/Run", "/TN", TASK_NAME],
                   capture_output=True, text=True)

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        found = find_port()
        if found:
            port, data = found
            return {"ok": True, "started": True, "port": port,
                    "edaConnected": bool(data.get("edaConnected")),
                    "note": "" if data.get("edaConnected") else
                    ("Bridge 已起，但 EDA 未连上：装 run-api-gateway 扩展并勾"
                     "「允许外部交互」，然后完全重启 EDA")}
        time.sleep(0.5)
    return {"ok": False, "started": False,
            "reason": f"{timeout_s}s 内端口 {PORT_RANGE.start}-{PORT_RANGE.stop - 1} "
                      f"没有出现 {SERVICE}；看日志 {LOG_FILE}"}


def stop() -> dict:
    subprocess.run(["schtasks", "/End", "/TN", TASK_NAME],
                   capture_output=True, text=True)
    subprocess.run(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"],
                   capture_output=True, text=True)
    found = find_port()
    killed = False
    if found:
        port = found[0]
        try:
            out = subprocess.run(["netstat", "-ano", "-p", "TCP"],
                                 capture_output=True, text=True).stdout
            for line in out.splitlines():
                cols = line.split()
                if (len(cols) >= 5 and cols[0] == "TCP"
                        and cols[3] == "LISTENING"
                        and cols[1].endswith(f":{port}")):
                    pid = cols[4]
                    subprocess.run(["taskkill", "/PID", pid, "/F"],
                                   capture_output=True, text=True)
                    killed = True
                    break
        except OSError:
            pass
    return {"ok": True, "stopped": True, "killedPort": killed}


def main(argv: list[str]) -> int:
    action = (argv[0] if argv else "status").lstrip("-")
    if action == "start":
        out = start()
    elif action == "stop":
        out = stop()
    else:
        action = "status"
        out = status()
    print(json.dumps({"action": action, **out}, ensure_ascii=False, indent=1))
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))