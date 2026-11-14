"""插件自检：构建产物 + 宿主契约 + Python 端到端。

    python hw_velxio/scripts/check_plugin.py

它检查三件**会真实出错**的事，而不是"文件存在"：

1. **产物形状**：`dist/index.js` 能被 Node 加载；`dist/client.js` 带
   `__ModuleLoader__` 闭包头，且**没有** `require("node:...")`——那在浏览器里
   是必崩的。
2. **宿主契约**：从安装包 `app.asar` 里重读一次，确认
   `uiConversation.views.register` / `conversation.view` /
   `session.prompt` 三个 API 仍然存在。类型能通过而运行时不存在，是这个插件
   最可能的沉默失败。
3. **Python 端到端**：建工程 → 导入 KiCad → 快照 → 检查 → 预算 → 核对 →
   闸门拦截 → 闭合 → 导出，全部按 `status` 判定。
"""

from __future__ import annotations

import json
import re
import struct
import subprocess
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PLUGIN = Path(__file__).resolve().parent.parent
ROOT = PLUGIN.parent
ASAR = Path(r"D:/AI_IDE/deepseek/resources/app.asar")

FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name}{(' — ' + detail) if detail else ''}")
        FAILURES.append(name)


# ---------------------------------------------------------------------------
# 1. 产物
# ---------------------------------------------------------------------------

def check_artifacts() -> None:
    print("[1] 构建产物")
    host = PLUGIN / "dist" / "index.js"
    client = PLUGIN / "dist" / "client.js"
    check("dist/index.js 存在", host.is_file())
    check("dist/client.js 存在", client.is_file())
    if not host.is_file() or not client.is_file():
        return

    client_text = client.read_text(encoding="utf-8", errors="replace")
    check("client 使用 __ModuleLoader__ 闭包",
          "__ModuleLoader__.load(" in client_text
          and "dsh-hw-velxio" in client_text)
    check("client 不引用 node: 内建模块",
          re.search(r'require\(["\']node:', client_text) is None)
    check("client 含 conversation.view 注册",
          "conversation.view" in client_text)
    check("client 内联了 three（非 external）",
          "WebGLRenderer" in client_text or "BufferGeometry" in client_text)

    proc = subprocess.run(
        ["node", "-e",
         f"import({json.dumps(host.as_uri())}).then(m => {{"
         "  if (typeof m.apply !== 'function') { console.log('NO_APPLY'); process.exit(2) }"
         "  console.log('APPLY_OK', Array.isArray(m.inject) ? m.inject.join(',') : '')"
         "}).catch(e => { console.error(e.message); process.exit(1) })"],
        cwd=str(PLUGIN), capture_output=True,
        encoding="utf-8", errors="replace", timeout=120,
    )
    ok = proc.returncode == 0 and "APPLY_OK" in proc.stdout
    check("dist/index.js 可加载且导出 apply", ok,
          (proc.stderr or proc.stdout).strip()[:300])

    runtime = PLUGIN / "scripts" / "check_client_runtime.mjs"
    if runtime.is_file():
        smoke = subprocess.run(["node", str(runtime)], cwd=str(PLUGIN),
                               capture_output=True, encoding="utf-8",
                               errors="replace", timeout=180)
        check("客户端 bundle 能注册并渲染（伪宿主）",
              smoke.returncode == 0,
              (smoke.stdout + smoke.stderr).strip()[-400:])


# ---------------------------------------------------------------------------
# 2. 宿主契约
# ---------------------------------------------------------------------------

def _asar_text(pattern: str, limit: int = 200_000_000) -> bytes | None:
    """在 app.asar 里找第一个含 pattern 的文件，返回它所在的片段。"""
    if not ASAR.is_file():
        return None
    with ASAR.open("rb") as fh:
        head = fh.read(16)
        payload = struct.unpack("<I", head[4:8])[0]
        json_size = struct.unpack("<I", head[12:16])[0]
        fh.seek(16)
        header = json.loads(fh.read(json_size))
        base = 8 + payload

        def walk(node: dict, prefix: str = ""):
            for key, value in node.get("files", {}).items():
                if "files" in value:
                    yield from walk(value, prefix + "/" + key)
                else:
                    yield prefix + "/" + key, value

        needle = pattern.encode()
        for name, meta in walk(header):
            offset = meta.get("offset")
            if offset is None or int(meta.get("size", 0)) == 0:
                continue
            if int(meta["size"]) > limit:
                continue
            fh.seek(base + int(offset))
            blob = fh.read(int(meta["size"]))
            if needle in blob:
                return blob
    return None


def check_host_contract() -> None:
    print("[2] 宿主契约（重读 app.asar）")
    if not ASAR.is_file():
        print("  skip app.asar 不存在（不是安装版宿主）")
        return
    probes = {
        "uiConversation.views.register": "views.register",
        "conversation.view 座位": "conversation.view",
        "session.prompt": ".session.prompt(",
    }
    for label, pattern in probes.items():
        blob = _asar_text(pattern)
        check(f"安装包含 {label}", blob is not None,
              "没找到：契约可能已变，需重读注册代码")

    source = PLUGIN / "src" / "client" / "view.ts"
    text = source.read_text(encoding="utf-8")
    check("注册目标与 slot id 一致",
          "HW_IDE_TARGET = 'hw-ide'" in text and "id: HW_IDE_TARGET" in text)
    check("视图 id 与 views.register 的 target 同源",
          text.count("target: HW_IDE_TARGET") == 1)


# ---------------------------------------------------------------------------
# 3. Python 端到端
# ---------------------------------------------------------------------------

def cli(*args: str) -> dict:
    proc = subprocess.run(
        [sys.executable, "-m", "hw_velxio.design_cli", *args],
        cwd=str(ROOT), capture_output=True, timeout=180,
        env=None,
    )
    stdout = proc.stdout.decode("utf-8", "replace").strip().splitlines()
    if not stdout:
        raise RuntimeError(
            f"design_cli 没有输出（args={args}）；stderr={proc.stderr.decode('utf-8', 'replace')[:400]}")
    return json.loads(stdout[-1])


def check_python() -> None:
    print("[3] Python 端到端")
    project = "_check_plugin"
    subprocess.run([sys.executable, "-m", "hw_velxio.design_cli", "list"],
                   cwd=str(ROOT), capture_output=True, timeout=120)

    # 清理上一次
    import shutil
    shutil.rmtree(ROOT / "hw_velxio" / "project" / project, ignore_errors=True)

    created = cli("new", project, "kicad", "自检")
    check("new 建工程", created.get("status") == "ok", str(created.get("findings")))

    imported = cli("import", project,
                   str(PLUGIN / "examples" / "kicad" / "sensor-node.kicad_pcb"),
                   "--bom", str(PLUGIN / "examples" / "kicad" / "sensor-node-bom.csv"))
    check("import KiCad + BOM", imported.get("status") == "ok",
          str(imported.get("findings")))
    check("导入得到 3 个元件",
          (imported.get("summary") or {}).get("parts") == 3,
          str(imported.get("summary")))

    snapshot = cli("snapshot", project)
    check("snapshot 成功", snapshot.get("status") == "ok")
    check("快照含 2D 与 3D",
          "projection2d" in snapshot and "scene3d" in snapshot)
    check("2D 与 3D 元件数一致",
          len(snapshot["projection2d"]["parts"]) == len(snapshot["scene3d"]["parts"]))
    check("尺寸未知的件被标未验证",
          any("高度" in line for line in snapshot["checks"]["not_verified"])
          or snapshot["checks"]["ok"] is False)

    checks = cli("check", project)
    check("check 跑得动", checks.get("status") == "ok")

    budget = cli("budget", project)
    check("budget 无 claim 时报不可评估",
          budget.get("confidence") == "none" and budget.get("ok") is False,
          json.dumps(budget.get("confidence")))

    cross = cli("crosscheck", project)
    check("crosscheck 区分未验证与矛盾",
          cross.get("status") == "ok"
          and "not_verified" in cross and "mismatches" in cross)

    blocked = cli("export", project)
    check("导出被闸门拦住", blocked.get("status") == "not_ready"
          and blocked.get("blocked") is True)

    for name, value in (("package_family", "SMD 0603"),
                        ("connector_family", "JST-PH"),
                        ("power_architecture", "5V USB"),
                        ("bootloader_layout", "single app")):
        locked = cli("deferred", project, "lock", name, value)
        if locked.get("status") != "ok":
            check(f"闭合 {name}", False, str(locked.get("findings")))

    exported = cli("export", project)
    check("闭合后导出成功", exported.get("status") == "ok",
          str(exported.get("findings")))
    check("制造包含 BOM 与 DXF",
          all(name in (exported.get("written") or [])
              for name in ("bom.csv", "outline.dxf", "design.json")))

    evidence = cli("evidence", project)
    check("证据有设计哈希与轨迹",
          evidence.get("status") == "ok"
          and len(str(evidence.get("design_hash", ""))) == 16)

    tampered = cli("trace", project, "verify")
    check("轨迹链完整", tampered.get("ok") is True)


def main() -> int:
    print(f"插件目录：{PLUGIN}\n仓库根：{ROOT}\n")
    check_artifacts()
    check_host_contract()
    try:
        check_python()
    except Exception as exc:  # noqa: BLE001
        print(f"  FAIL Python 端到端异常：{type(exc).__name__}: {exc}")
        FAILURES.append("python-e2e")

    print()
    if FAILURES:
        print(f"❌ {len(FAILURES)} 项失败：{', '.join(FAILURES)}")
        return 1
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
