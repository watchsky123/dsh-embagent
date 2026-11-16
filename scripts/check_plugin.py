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
import os
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
    # 主题跟随：宿主信号（data-ds-dark-theme）+ 两套字面调色板 + 翻转监听，
    # 少任何一套，"背景跟随系统"都会静默失效（表现为永远深色）。
    check("client 含宿主深色信号选择器",
          "data-ds-dark-theme] .hwv-root" in client_text)
    check("client 含浅色字面调色板",
          "--hwv-p-canvas-bg: #f7f8fa" in client_text.replace("  ", " ")
          or "--hwv-p-canvas-bg:#f7f8fa" in client_text
          or "--hwv-p-canvas-bg: #f7f8fa" in client_text)
    check("client 含深色字面调色板",
          "#101216" in client_text and "#15171c" in client_text)
    check("client 监听主题翻转",
          "data-ds-dark-theme" in client_text and "MutationObserver" in client_text)

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

    layout = PLUGIN / "scripts" / "check_layout_runtime.mjs"
    if layout.is_file():
        # 壳的行为断言：把手、隐藏即停、segmented、持久化读写与降级。
        run = subprocess.run(["node", str(layout)], cwd=str(PLUGIN),
                             capture_output=True, encoding="utf-8",
                             errors="replace", timeout=180)
        check("外壳行为（把手/隐藏即停/持久化）", run.returncode == 0,
              (run.stdout + run.stderr).strip()[-500:])

    # Remote 端点签名：宿主网关在注册期用 Function.prototype.toString 解析
    # 参数表，出现默认值/解构/rest 会抛 gateway/signature-invalid（曾让
    # hwvelxio/snapshot 整个视图读不到快照）。这里对**编译产物**跑同一算法，
    # 源码干净但忘记 build 也照样能抓到。
    sig = PLUGIN / "scripts" / "check_remote_signature.mjs"
    if sig.is_file():
        run = subprocess.run(["node", str(sig)], cwd=str(PLUGIN),
                             capture_output=True, encoding="utf-8",
                             errors="replace", timeout=60)
        check("Remote 端点签名合法（无默认值/解构/rest）", run.returncode == 0,
              (run.stdout + run.stderr).strip()[-400:])

    # 工具注册：AI 改工作区必须有**受校验**的路径。上一版模型能调 8 个工具
    # 但一条都不能改设计，只能直接写 design.json——那条路绕过 validate()。
    tools_check = PLUGIN / "scripts" / "check_tools.mjs"
    if tools_check.is_file():
        run = subprocess.run(["node", str(tools_check)], cwd=str(PLUGIN),
                             capture_output=True, encoding="utf-8",
                             errors="replace", timeout=60)
        check("AI 协作工具齐备且标注读写", run.returncode == 0,
              (run.stdout + run.stderr).strip()[-500:])

    put_check = PLUGIN / "scripts" / "check_put_stdin.mjs"
    if put_check.is_file():
        # 设计正文走 stdin：Windows argv 上限 32767，截断后的 JSON 仍能解析，
        # 后果是**静默写入一份残缺设计**。
        run = subprocess.run(["node", str(put_check)], cwd=str(PLUGIN),
                             capture_output=True, encoding="utf-8",
                             errors="replace", timeout=180)
        check("设计写入走 stdin 且不被截断", run.returncode == 0,
              (run.stdout + run.stderr).strip()[-400:])

    dead_css = PLUGIN / "scripts" / "check_dead_css.py"
    if dead_css.is_file():
        run = subprocess.run([sys.executable, str(dead_css)], cwd=str(ROOT),
                             capture_output=True, encoding="utf-8",
                             errors="replace", timeout=60)
        check("样式与组件同步（无死规则/无缺样式）", run.returncode == 0,
              (run.stdout + run.stderr).strip()[-300:])


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
    """跑一次 design_cli，返回它最后一行 JSON。

    子进程继承本进程环境，所以 ``HWV_WORKSPACE``（下面设到临时目录）会生效，
    自检**不会**在用户真实工作区里留下任何东西。
    """
    proc = subprocess.run(
        [sys.executable, "-m", "hw_velxio.design_cli", *args],
        cwd=str(ROOT), capture_output=True, timeout=180,
    )
    stdout = proc.stdout.decode("utf-8", "replace").strip().splitlines()
    if not stdout:
        raise RuntimeError(
            f"design_cli 没有输出（args={args}）；stderr={proc.stderr.decode('utf-8', 'replace')[:400]}")
    return json.loads(stdout[-1])


def _run_cli_stdin(args: list[str], payload: str) -> dict:
    """跑一次 design_cli，把 payload 交给它的 stdin。

    `put <id> -` 的设计正文从这里读——和 `bridge.ts` 的 `designPut` 同一
    条通路。测这一条而不是 argv 版本，因为 argv 有 32K 上限而设计会超。
    """
    proc = subprocess.run(
        [sys.executable, "-m", "hw_velxio.design_cli", *args],
        cwd=str(ROOT), input=payload.encode("utf-8"),
        capture_output=True, timeout=180,
    )
    stdout = proc.stdout.decode("utf-8", "replace").strip().splitlines()
    if not stdout:
        raise RuntimeError(
            f"design_cli 没有输出（args={args}）；"
            f"stderr={proc.stderr.decode('utf-8', 'replace')[:400]}")
    return json.loads(stdout[-1])


def sandbox_path() -> str:
    """当前自检工作区（由 check_python 设进环境）。"""
    return os.environ.get("HWV_WORKSPACE", "")


def check_python() -> None:
    print("[3] Python 端到端")
    project = "_check_plugin"
    import shutil
    import tempfile
    # 自检用独立工作区：工程根现在由 HWV_WORKSPACE 决定，
    # 不隔离就会往用户工作区写 _check_plugin 工程。
    sandbox = Path(tempfile.mkdtemp(prefix="hwv-check-ws-"))
    os.environ["HWV_WORKSPACE"] = str(sandbox)
    try:
        _check_python_body(project)
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)


def _check_python_body(project: str) -> None:
    workspace = cli("workspace", "show")
    check("工作区可解析", workspace.get("status") == "ok", str(workspace.get("findings")))
    check("工作区落在隔离目录",
          str(sandbox_path()) in str(workspace.get("workspace", "")),
          f"{workspace.get('workspace')} 不含 {sandbox_path()}")

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

    # -- P2/P4：阶段推导、空集通过、勘察与初审.md -------------------------
    print("  -- 阶段 / 假绿灯 / 勘察")
    empty = cli("new", "_check_empty", "arduino-uno", "空工程")
    check("建一个空工程（用于验证假绿灯）", empty.get("status") == "ok",
          str(empty.get("findings")))
    empty_snap = cli("snapshot", "_check_empty")
    stage = empty_snap.get("phase") or {}
    check("空工程的阶段是 empty（不是 complete）",
          stage.get("phase") == "empty", str(stage))
    vacant = (empty_snap.get("checks") or {}).get("vacuous") or []
    check("空工程的检查全部记为『空集通过』", len(vacant) >= 8,
          f"vacuous={vacant}")
    check("阶段给出『差什么』的原因", bool(stage.get("veto")), str(stage.get("veto")))

    # 有元件的工程：阶段必须不是 empty（尺寸来自导入，可能 complete 也可能 partial）。
    rich_snap = cli("snapshot", project)
    rich = rich_snap.get("phase") or {}
    check("导入过的工程阶段不是 empty", rich.get("phase") in ("partial", "complete"),
          str(rich))
    check("有元件时不再报『没有元件』",
          not any("没有一个元件" in str(v) for v in (rich.get("veto") or [])),
          str(rich.get("veto")))
    # 空集通过清单在"有元件"之后必须收缩（连接器/net 之外的不该再算空集）。
    rich_vacuous = (rich_snap.get("checks") or {}).get("vacuous") or []
    check("有元件后空集通过清单收缩", len(rich_vacuous) < len(vacant),
          f"有元件 {len(rich_vacuous)} vs 空 {len(vacant)}")

    surveyed = cli("survey", "--write")
    check("survey 成功", surveyed.get("status") == "ok",
          str(surveyed.get("findings")))
    counts = surveyed.get("counts") or {}
    check("survey 认出全部工程", counts.get("projects", 0) >= 2, str(counts))
    check("survey 给出指纹", str(surveyed.get("fingerprint", "")).startswith("sha256:"))
    review_path = Path(str(surveyed.get("initial_review", "")))
    check("初审.md 已写盘", review_path.is_file(), str(review_path))
    if review_path.is_file():
        text = review_path.read_text(encoding="utf-8")
        check("初审.md 含机器事实段标记", "hwv-survey:begin" in text)
        check("初审.md 含指纹", "fingerprint: sha256:" in text)
        check("初审.md 含判断段（留给 AI）", "## 判断" in text)
        check("初审.md 事实段列出工程", "_check_empty" in text)
        reread = cli("survey", "--read")
        check("初审.md 可以读回", reread.get("exists") is True)
        check("刚写完的文件不算过期", reread.get("stale") is False,
              f"fp={reread.get('fingerprint')} cur={reread.get('current_fingerprint')}")

    # -- 工作区条目 / 覆盖度 / 日志 --------------------------------------
    print("  -- 工作区条目（not_adopted 与会话工程分开）")
    # 造一个"有内容但没接入"的目录：有 .ioc 与源码，但没有 design.json。
    import_dir = Path(sandbox_path()) / "_check_cubemx_project"
    import_dir.mkdir(parents=True, exist_ok=True)
    (import_dir / "app.ioc").write_text(
        "Mcu.Family=STM32F1\n"
        "Mcu.Name=STM32F103C(8-B)Tx\n"
        "Mcu.UserName=STM32F103C8Tx\n"
        "ProjectManager.DeviceId=STM32F103C8Tx\n"
        "PA10.Signal=USART1_RX\n"
        "PA13.Signal=SYS_JTMS-SWDIO\n",
        encoding="utf-8")
    src_dir = import_dir / "Core" / "Src"
    src_dir.mkdir(parents=True, exist_ok=True)
    (src_dir / "main.c").write_text("int main(void){return 0;}\n", encoding="utf-8")
    (import_dir / "BOM_check.csv").write_text(
        "Designator,Value\nR1,10k\nC1,100nF\n", encoding="utf-8")

    scanned = cli("survey")
    entry_ids = [e["id"] for e in scanned.get("entries") or []]
    check("survey 认出未接入的目录条目",
          "_check_cubemx_project" in entry_ids, str(entry_ids))
    entry = next((e for e in (scanned.get("entries") or [])
                  if e["id"] == "_check_cubemx_project"), {})
    check("从 .ioc 读出真实 MCU 型号",
          entry.get("mcu") == "STM32F103C8Tx", str(entry.get("mcu")))
    check("未接入条目阶段是 not_adopted（不是 empty）",
          entry.get("phase") == "not_adopted", str(entry.get("phase")))
    check("not_adopted 说明缺的是 design.json",
          any("design.json" in str(v) for v in (entry.get("veto") or [])),
          str(entry.get("veto")))
    counts = scanned.get("counts") or {}
    check("条目数与工程数分开统计",
          counts.get("entries", 0) > counts.get("projects", 0),
          f"entries={counts.get('entries')} projects={counts.get('projects')}")

    # -- 覆盖度：必须如实报告"没读" --------------------------------------
    print("  -- 读取覆盖度（哪些看了、哪些没看）")
    coverage = scanned.get("coverage") or {}
    check("覆盖度给出文件总数", coverage.get("total_files", 0) > 0, str(coverage))
    check("覆盖度给出已读文件数", coverage.get("read_files", 0) >= 1, str(coverage))
    check("未读文件被显式列出（不是只报读过的）",
          coverage.get("unread_files", 0) > 0
          and isinstance(coverage.get("unread"), list),
          str({k: v for k, v in coverage.items() if k != "unread"}))
    check("未读清单带路径（可逐条核对）",
          all("path" in row for row in (coverage.get("unread") or [])[:5]))
    check("覆盖度说明只读了决定性文件",
          "未读取内容" in str(coverage.get("note", "")),
          str(coverage.get("note")))
    check("logs 目录不被算进文件总数（观测不能改变观测对象）",
          "logs" not in str([r["path"] for r in (coverage.get("unread") or [])[:50]]),
          "logs 出现在未读清单里")

    # -- 日志 ------------------------------------------------------------
    print("  -- 工程式日志（写入工作区 logs/）")
    listed = cli("log", "list")
    check("log list 可用", listed.get("status") == "ok",
          str(listed.get("findings")))
    log_dir = listed.get("dir") or ""
    check("日志目录在工作区内", sandbox_path() in log_dir or "_check" in log_dir,
          log_dir)
    files = [row["name"] for row in (listed.get("logs") or [])]
    check("logs/ 里有勘察报告", any(n.startswith("survey-") for n in files), str(files))
    check("logs/ 里有覆盖度报告",
          any(n.startswith("coverage-") for n in files), str(files))
    check("logs/ 里有活动流水", "activity.md" in files, str(files))

    report_name = next((n for n in files if n.startswith("survey-")), "")
    if report_name:
        read_back = cli("log", "read", report_name)
        check("log read 能读回报告", read_back.get("status") == "ok")
        body = read_back.get("text") or ""
        check("报告含条目表", "工作区条目" in body)
        check("报告含覆盖度段", "覆盖度" in body)
        check("报告含未读清单", "未读" in body)
    check("log read 拒绝路径穿越", cli("log", "read", "../design.json")
          .get("status") == "failed")

    before = (cli("log", "list").get("activity") or {}).get("rows") or []
    cli("log", "append", "_check", "ok", "自检写入")
    after = (cli("log", "list").get("activity") or {}).get("rows") or []
    check("活动日志只追加（旧的还在，新的在顶部）",
          len(after) == len(before) + 1 and after[0] != before[0],
          f"{len(before)} → {len(after)}")

    cov = cli("log", "coverage")
    check("log coverage 只读可用", cov.get("status") == "ok"
          and cov.get("total_files", 0) > 0, str(cov.get("findings")))

    # -- 立创 EDA（.epro2）：唯一能给出网表的材料 --------------------------
    print("  -- 立创 EDA 工程（.epro2 解析与导入）")
    import zipfile as _zip
    eda_path = Path(sandbox_path()) / "_check_board.epro2"
    # V3 规范：每行 `{outer}||{inner}|`，行间用换行分隔。
    eda_lines = ['{"type":"DOCHEAD","ticket":1}||{"docType":"PCB","uuid":"u1"}|']
    ticket = 100
    for i, (ref, value) in enumerate([("R1", "10k"), ("C1", "100nF"),
                                      ("U1", "STM32F103")]):
        eda_lines.append(
            f'{{"type":"COMPONENT","ticket":{ticket},"id":"e{i}"}}'
            f'||{{"x":{1000 + i * 200},"y":{500 + i * 100},"angle":90}}|')
        ticket += 1
        eda_lines.append(
            f'{{"type":"ATTR","ticket":{ticket},"id":"e{i}-d"}}'
            f'||{{"parentId":"e{i}","key":"Designator","value":"{ref}"}}|')
        ticket += 1
        if value:
            eda_lines.append(
                f'{{"type":"ATTR","ticket":{ticket},"id":"e{i}-v"}}'
                f'||{{"parentId":"e{i}","key":"Value","value":"{value}"}}|')
            ticket += 1
    for i, net in enumerate(["GND", "VCC3V3", "GND"]):
        key = json.dumps(["PAD_NET", f"e{i % 3}", "1", f"p{i}"])
        eda_lines.append(
            f'{{"type":"PAD_NET","ticket":{ticket},"id":{json.dumps(key)}}}'
            f'||{{"padNet":"{net}"}}|')
        ticket += 1
    eda_lines.append(
        f'{{"type":"POLY","ticket":{ticket},"id":"e900"}}'
        f'||{{"layerId":11,"polyType":"BOARD_OUTLINE",'
        f'"path":["R",0,0,4000,3000]}}|')
    with _zip.ZipFile(eda_path, "w") as archive:
        archive.writestr("project2.json",
                         '{"title":"自检板","editorVersion":"4.1.100"}')
        archive.writestr("board.epru", "\n".join(eda_lines) + "\n")

    formats = cli("formats")
    exts = [f["ext"] for f in (formats.get("formats") or [])]
    check("formats 列出 .epro2", ".epro2" in exts, str(exts))

    imported_eda = cli("import", "_check_eda", str(eda_path))
    check("import 接受 .epro2", imported_eda.get("status") == "ok",
          str(imported_eda.get("findings")))
    summary = imported_eda.get("summary") or {}
    check("epro2 导入得到 3 个元件", summary.get("parts") == 3, str(summary))
    snap_eda = cli("snapshot", "_check_eda")
    check("epro2 工程能出 2D 与 3D",
          len((snap_eda.get("projection2d") or {}).get("parts") or []) == 3
          and len((snap_eda.get("scene3d") or {}).get("parts") or []) == 3)
    design_eda = snap_eda.get("design") or {}
    meta = design_eda.get("meta") or {}
    # A：坐标来自 PCB（真实），不是网格占位
    check("坐标标记为真实", meta.get("coords_are_real") is True, str(meta.get("coords_are_real")))
    check("板框来自工程（4000 mil = 101.6 mm）",
          abs(float((design_eda.get("board") or {}).get("size_mm", {}).get("w", 0))
              - 101.6) < 0.5,
          str((design_eda.get("board") or {}).get("size_mm")))
    # B：网表真的进来了
    check("网表从 PAD_NET 导入（不是留空）",
          len(design_eda.get("nets") or []) > 0,
          f"{len(design_eda.get('nets') or [])} 条")
    check("网络名是真实的（GND/VCC3V3）",
          "GND" in json.dumps(design_eda.get("nets") or []),
          json.dumps(design_eda.get("nets") or [])[:120])
    checks_eda = snap_eda.get("checks") or {}
    check("未知尺寸仍记为 estimated（不编造数值）",
          len(checks_eda.get("estimated") or []) == 3,
          str(checks_eda.get("estimated")))

    # -- P1：引脚校验（从 0 搭的网表能不能被信任）--------------------------
    print("  -- P1 引脚校验")
    pin_design = {
        "schema": "hw_velxio.design/1", "units": "mm", "id": "_check_pin",
        "title": "引脚自检",
        "board": {"kind": "arduino-nano", "name": "Nano",
                  "outline": [[0, 0], [44.9, 0], [44.9, 17.8], [0, 17.8]],
                  "size_mm": {"w": 44.9, "h": 17.8}, "thickness_mm": 1.6,
                  "mount_holes": [], "px_per_mm": 3.7751},
        "parts": [{"ref": "U1", "kind": "mcu", "role": "module",
                   "size_mm": {"w": 10, "h": 10, "d": 2},
                   "physical_source": "datasheet",
                   "pins": ["1", "2", "3"],
                   "placement": {"x_mm": 2, "y_mm": 2, "rotation_z": 0,
                                 "mount": "board"}}],
        "nets": [{"net": "X", "pins": [{"ref": "U1", "pin": "99"}]}],
        "wires": [], "meta": {},
    }
    bad_pin = _run_cli_stdin(["put", "_check_pin", "-"], json.dumps(pin_design))
    check("编造的引脚被 validate 拦住（不再是静默通过）",
          any("引脚不存在" in str(x) for x in (bad_pin.get("problems") or [])),
          str(bad_pin.get("problems")))

    good_pin = json.loads(json.dumps(pin_design))
    good_pin["nets"] = [{"net": "X", "pins": [{"ref": "U1", "pin": "2"}]}]
    ok_pin = _run_cli_stdin(["put", "_check_pin_ok", "-"], json.dumps(good_pin))
    check("合法引脚不报错", not any("引脚不存在" in str(x)
                                    for x in (ok_pin.get("problems") or [])),
          str(ok_pin.get("problems")))

    unknown = json.loads(json.dumps(pin_design))
    unknown["id"] = "_check_pin_unknown"
    unknown["parts"][0].pop("pins")
    unknown["nets"] = [{"net": "X", "pins": [{"ref": "U1", "pin": "ANY"}]}]
    _run_cli_stdin(["put", "_check_pin_unknown", "-"], json.dumps(unknown))
    snap_unknown = cli("snapshot", "_check_pin_unknown")
    check("没有引脚表的元件报『未核对』而不是通过",
          any("引脚校验" in x and "无法核对" in x
              for x in ((snap_unknown.get("checks") or {}).get("not_verified") or [])),
          str((snap_unknown.get("checks") or {}).get("not_verified")))

    # -- P2：design.json → scene.json --------------------------------------
    print("  -- P2 design.json → scene.json（打通 hwforge）")
    scene_out = cli("scene", "_check_pin_ok", "--pitch", "0.5")
    check("scene 转换成功", scene_out.get("status") == "ok",
          str(scene_out.get("findings")))
    scene_summary = scene_out.get("summary") or {}
    check("scene 有 placements", scene_summary.get("placements") == 1,
          str(scene_summary))
    check("scene 体素尺寸 = 板尺寸 / pitch",
          (scene_summary.get("dims_voxels") or [0])[0] == 90,
          str(scene_summary.get("dims_voxels")))
    scene_path = Path(str(scene_out.get("scene", "")))
    check("scene.json 落在工程目录", scene_path.is_file(), str(scene_path))
    if scene_path.is_file():
        scene_doc = json.loads(scene_path.read_text(encoding="utf-8"))
        check("每个 placement 都有 meta.role（否则 inventory.bom 会整件跳过）",
              all((p.get("meta") or {}).get("role")
                  for p in scene_doc["space"]["placements"]))
        check("连线标注了『端点是元件中心』",
              (scene_doc.get("wires") or {}).get("estimated") is True)

    # -- P3：需求 → 设计（模板）--------------------------------------------
    print("  -- P3 需求 → 设计（模板组装）")
    for template in ("blink", "button", "i2c-sensor", "servo"):
        made = cli("synth", f"_check_synth_{template}", template)
        check(f"模板 {template} 能组装", made.get("status") == "ok",
              str(made.get("findings")))
        if made.get("status") == "ok":
            # 只断言"没有引脚/几何错误"。catalog 里标 `estimated` 的件
            # （按键、BME280）本来就该报"尺寸为估算值"——那是事实，不是缺陷，
            # 要求零问题等于逼模板去编造 datasheet 数值。
            real = [x for x in (made.get("problems") or [])
                    if "估算值" not in str(x)]
            check(f"模板 {template} 没有引脚/几何错误", not real, str(real))
    bad_synth = cli("synth", "_check_synth_bad", "blink", "--spec",
                    '{"pin": "D99"}')
    check("模板拒绝不存在的引脚（带可选值提示）",
          bad_synth.get("status") != "ok"
          and "没有引脚" in json.dumps(bad_synth, ensure_ascii=False),
          str(bad_synth.get("findings")))
    made_blink = cli("snapshot", "_check_synth_blink")
    check("组装出的设计 2D/3D 都能出",
          len((made_blink.get("projection2d") or {}).get("parts") or []) >= 3
          and len((made_blink.get("scene3d") or {}).get("parts") or []) >= 3)

    # -- P4：put 也自动锁 mcu_family ---------------------------------------
    print("  -- P4 put 的 auto_lock")
    check("put 自动锁 mcu_family（导出闸门不再被死结拦住）",
          "mcu_family" in (ok_pin.get("auto_locked") or []),
          str(ok_pin.get("auto_locked")))

    # P1：受校验的写入路径
    print("  -- 受校验的写入（hw_put 的 CLI 底座）")
    good = {
        "schema": "hw_velxio.design/1", "units": "mm", "id": project,
        "title": "put 自检",
        "board": {"kind": "arduino-uno", "name": "Arduino Uno",
                  "outline": [[0, 0], [72.58, 0], [72.58, 53.34], [0, 53.34]],
                  "size_mm": {"w": 72.58, "h": 53.34}, "thickness_mm": 1.6,
                  "mount_holes": [], "px_per_mm": 3.7751},
        "parts": [{"ref": "r1", "kind": "resistor", "role": "component",
                   "size_mm": {"w": 1.6, "h": 0.8, "d": 0.45},
                   "physical_source": "estimated",
                   "placement": {"x_mm": 10, "y_mm": 10, "mount": "board"}}],
        "nets": [], "wires": [], "meta": {},
    }
    put = _run_cli_stdin(["put", "_check_put", "-"], json.dumps(good))
    check("put 接受 stdin（argv 有 32K 上限）", put.get("status") == "ok",
          str(put.get("findings")))
    check("put 回报模型校验问题", isinstance(put.get("problems"), list))
    check("估算尺寸被 validate 点名",
          any("估算" in str(p) for p in (put.get("problems") or [])),
          str(put.get("problems")))

    bad = dict(good)
    bad["parts"] = [{"ref": "", "kind": "", "size_mm": {}}]
    rejected = _run_cli_stdin(["put", "_check_bad", "-"], json.dumps(bad))
    check("非法设计被模型拒绝（不是静默落盘）",
          rejected.get("status") != "ok", str(rejected))


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
