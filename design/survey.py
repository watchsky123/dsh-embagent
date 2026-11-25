"""工作区勘察 —— 初审.md 的**事实**那一半。

## 为什么不是"AI 写初审.md"

AI 判断"这是空/半成品/成品"依赖的事实（哪些目录是工程、每个工程缺什么、
哪些文件没被任何工程引用）全是可机械算出的。让 AI 一次写完，它会把
"我看到 30 个 .bin" 写成"完成度约 70%"这种无依据的话——而这段文字会落盘，
下一次 AI 又会把它当**事实**引用。错误因此会被放大，而不是被纠正。

所以拆两半：

    事实（本模块，确定性生成，勿手改）
    判断（AI 撰写，引用事实，可以错、可以被推翻）

## 指纹（fingerprint）

AI 拿着三天前的初审去适配今天的工作区，是必然会发生的事。指纹 = 工作区的
目录树 + 各工程 design.json 的 mtime/size。工具可以据此说一句
"初审已过期（工作区有 N 个文件变化）"，而不是默默地用旧结论。

@module dsh_embagent.design.survey
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from . import entries as entries_module
from . import log as log_module
from . import phase as phase_module
from .model import DesignModel
from .store import DesignStore, list_projects, project_root

# 勘察时最多扫多少个文件（工作区可能是一个装满 .bin 的真实目录）。
MAX_FILES = 4000
# 目录树里跳过的东西：体积大、与电气设计无关。
#
# `logs` 必须在名单里：它是**本工具自己的输出**。不排除的话，每跑一次勘察
# 报告就多一个文件，覆盖度从 19/2085 漂到 19/2088——**观测行为改变了观测
# 对象**。日志不能成为它自己统计的一部分。
SKIP_DIRS = {"__pycache__", ".git", "node_modules", ".venv", "venv", "dist",
             "build", "obj", "Debug", "Release", "logs"}
# 这些扩展名对硬件设计有意义；其余只计数不列名（避免初审被 .log 淹没）。
#
# `.ioc` 曾经**不在**这个名单里，于是 7 个 CubeMX 工程的芯片型号全部丢失——
# 而 `Mcu.UserName` 是全工作区最硬的硬件事实。这类遗漏比多列几个文件糟得多。
RELEVANT_EXT = {
    ".kicad_pcb", ".kicad_sch", ".kicad_pro", ".sch", ".brd", ".json",
    ".csv", ".xlsx", ".xls", ".bin", ".hex", ".elf", ".param", ".md",
    ".ioc", ".step", ".stp", ".dxf", ".gbr", ".drl", ".zip", ".epro2",
}

# 这些文件名是第三方许可证/模板，**不是项目内容**。它们曾经占满 32 条
# loose 清单里的 18 条，把真正有价值的 .ioc/.param 挤到看不见。
NOISE_NAMES = {"license.md", "license.txt", "license", "copying", "notice",
               "changelog.md", "code_of_conduct.md", "contributing.md",
               "readme.md"}


def _is_noise(path: Path) -> bool:
    """第三方样板文件：计数但**不进 loose 清单**。"""
    if path.name.lower() in NOISE_NAMES:
        return True
    parts = {part.lower() for part in path.parts}
    return bool(parts & {"drivers", "cmsis", "middlewares", "hal_driver"})


def _fingerprint(root: Path, projects: list[dict[str, Any]]) -> str:
    """工作区指纹：目录树的形状 + 每个工程 design.json 的 mtime/size。"""
    digest = hashlib.sha256()
    digest.update(str(root).encode("utf-8"))
    for row in projects:
        digest.update(str(row.get("id", "")).encode("utf-8"))
        path = root / str(row.get("id", "")) / "design.json"
        try:
            stat = path.stat()
            digest.update(f"{stat.st_mtime_ns}:{stat.st_size}".encode("ascii"))
        except OSError:
            digest.update(b"missing")
    return "sha256:" + digest.hexdigest()[:16]


def _walk_files(root: Path) -> tuple[list[Path], int]:
    """列出工作区里的文件（跳过噪声目录）。返回 (文件, 被跳过的目录数)。"""
    out: list[Path] = []
    skipped = 0
    stack = [root]
    while stack and len(out) < MAX_FILES:
        current = stack.pop()
        try:
            entries = list(current.iterdir())
        except OSError:
            continue
        for entry in entries:
            if len(out) >= MAX_FILES:
                break
            try:
                if entry.is_dir():
                    if entry.name in SKIP_DIRS or entry.name.startswith("."):
                        skipped += 1
                        continue
                    stack.append(entry)
                elif entry.is_file():
                    out.append(entry)
            except OSError:
                continue
    return out, skipped


def _phase_of(store: DesignStore, design: DesignModel) -> dict[str, Any]:
    """单个工程的阶段：直接复用确定性推导，不在这里另写一套判断。"""
    open_list: list[str] = []
    try:
        from . import discipline
        ledger = discipline.ledger_from_json(store.read("deferred.json"))
        open_list = list(ledger.open_irreversible())
    except Exception:  # noqa: BLE001
        open_list = []
    return phase_module.evaluate(design, open_irreversible=open_list)


def survey(*, root: Path | None = None) -> dict[str, Any]:
    """勘察工作区。返回纯事实（不含任何 AI 判断）。"""
    base = root if root is not None else project_root()
    started = time.time()
    exists = base.is_dir()

    projects: list[dict[str, Any]] = []
    referenced: set[str] = set()
    if exists:
        for row in list_projects():
            project_id = str(row.get("id", ""))
            entry: dict[str, Any] = {
                "id": project_id,
                "title": row.get("title", ""),
                "board": row.get("board_name") or row.get("board") or "",
                "parts": int(row.get("parts") or 0),
                "nets": int(row.get("nets") or 0),
                "artifacts": [name for name, present
                              in (row.get("artifacts") or {}).items() if present],
            }
            store = DesignStore(project_id)
            try:
                design = store.load()
                stage = _phase_of(store, design)
                entry.update({
                    "phase": stage["phase"],
                    "phase_reason": stage["reason"],
                    "veto": stage["veto"],
                    "counts": stage["counts"],
                })
            except Exception as exc:  # noqa: BLE001
                # 读不出来本身就是最重要的勘察结论之一：不能静默跳过。
                entry.update({
                    "phase": "broken",
                    "phase_reason": f"design.json 读不出：{type(exc).__name__}: {exc}",
                    "veto": [], "counts": {},
                })
            projects.append(entry)
            referenced.add(str(base / project_id))

    files, skipped_dirs = _walk_files(base) if exists else ([], 0)
    project_dirs = {str(base / str(item["id"])) for item in projects}

    # -- 工作区条目：真实项目（可能还没接进工作台）-------------------------
    # 这一层是"难以评审"的直接修复：上一版只认含 design.json 的目录，
    # 于是 7 个 CubeMX 工程（423 个源文件、7 个真实 MCU 型号）一个都不算。
    entries_payload = entries_module.scan(base) if exists else {
        "entries": [], "by_kind": {}, "read_paths": [],
        "adopted": 0, "not_adopted": 0}

    loose: list[dict[str, Any]] = []
    ext_counts: dict[str, int] = {}
    unreferenced = 0
    noise = 0
    for path in files:
        rel = path.relative_to(base).as_posix()
        ext = path.suffix.lower()
        ext_counts[ext or "(无扩展名)"] = ext_counts.get(ext or "(无扩展名)", 0) + 1
        # 工程目录内的文件算"被引用"（它们就是工程的一部分）。
        inside_project = any(str(path).startswith(d + "\\") or
                             str(path).startswith(d + "/")
                             for d in project_dirs)
        if inside_project:
            continue
        # 第三方许可证/模板：计数但不进清单，否则会把 .ioc/.param 挤掉。
        if _is_noise(path):
            noise += 1
            continue
        if ext in RELEVANT_EXT:
            try:
                size = path.stat().st_size
            except OSError:
                size = 0
            loose.append({"path": rel, "ext": ext, "size": size})
        else:
            unreferenced += 1
    loose.sort(key=lambda row: row["path"])

    # -- 覆盖度：哪些打开过、哪些没读 -------------------------------------
    coverage = log_module.build_coverage(entries_payload, files, base)

    payload: dict[str, Any] = {
        "status": "ok" if exists else "not_ready",
        "workspace": str(base),
        "exists": exists,
        "surveyed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "elapsed_ms": int((time.time() - started) * 1000),
        "fingerprint": _fingerprint(base, projects) if exists else "",
        "projects": projects,
        "entries": entries_payload["entries"],
        "entries_by_kind": entries_payload["by_kind"],
        "coverage": coverage,
        "counts": {
            "projects": len(projects),
            "empty": sum(1 for p in projects if p.get("phase") == "empty"),
            "partial": sum(1 for p in projects if p.get("phase") == "partial"),
            "complete": sum(1 for p in projects if p.get("phase") == "complete"),
            "broken": sum(1 for p in projects if p.get("phase") == "broken"),
            "entries": len(entries_payload["entries"]),
            "entries_adopted": entries_payload["adopted"],
            "entries_not_adopted": entries_payload["not_adopted"],
            "files": len(files),
            "read_files": coverage["read_files"],
            "unread_files": coverage["unread_files"],
            "loose_files": len(loose),
            "noise_files": noise,
            "other_files": unreferenced,
            "skipped_dirs": skipped_dirs,
            "truncated": len(files) >= MAX_FILES,
        },
        "loose_files": loose,
        "ext_counts": dict(sorted(ext_counts.items(), key=lambda kv: -kv[1])[:24]),
    }
    if not exists:
        payload["findings"] = [f"工作区不存在：{base}"]
    return payload


# ---------------------------------------------------------------------------
# 初审.md
# ---------------------------------------------------------------------------

FACT_BEGIN = "<!-- hwv-survey:begin 机器生成，勿手改 -->"
FACT_END = "<!-- hwv-survey:end -->"


def _human_size(size: int) -> str:
    if size >= 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    if size >= 1024:
        return f"{size / 1024:.0f} KB"
    return f"{size} B"


def render_facts(payload: dict[str, Any]) -> str:
    """把勘察结果渲染成初审.md 的**事实块**（AI 不得修改这一段）。"""
    counts = payload.get("counts") or {}
    lines = [
        FACT_BEGIN,
        f"<!-- workspace: {payload.get('workspace', '')} -->",
        f"<!-- at: {payload.get('surveyed_at', '')} -->",
        f"<!-- fingerprint: {payload.get('fingerprint', '')} -->",
        "",
        "### 事实：工程清单",
        "",
    ]
    projects = payload.get("projects") or []
    if not projects:
        lines.append("_工作区里没有任何工程（没有含 design.json 的子目录）。_")
    else:
        lines.append("| 工程 | 标题 | 板卡 | 元件 | 网表 | 阶段 | 差什么 |")
        lines.append("|---|---|---|---|---|---|---|")
        for row in projects:
            veto = row.get("veto") or []
            gap = "；".join(str(v) for v in veto) if veto else "—"
            gap = gap.replace("|", "/")
            lines.append(
                f"| `{row.get('id', '')}` | {row.get('title', '') or '—'} "
                f"| {row.get('board', '') or '—'} | {row.get('parts', 0)} "
                f"| {row.get('nets', 0)} | **{row.get('phase', '?')}** | {gap} |")
        lines += [
            "",
            "阶段是**确定性推导**（`design/phase.py`），不是 AI 判断：",
            "",
            "| 阶段 | 判据 |",
            "|---|---|",
            "| `empty` | 一个元件都没有 → 所有物理检查都会因空集而通过（假绿灯） |",
            "| `partial` | 有元件，但存在尺寸缺失/估算、或不可逆决策未闭合 |",
            "| `complete` | 元件齐全、尺寸都有出处、不可逆决策已闭合 |",
            "| `broken` | design.json 读不出来 |",
        ]

    # -- 工作区条目：真实项目（可能还没接进工作台）-----------------------
    entries = payload.get("entries") or []
    by_kind = payload.get("entries_by_kind") or {}
    lines += ["", "### 事实：工作区条目（真实项目，不只是工作台工程）", ""]
    if entries:
        lines.append(
            f"共 **{len(entries)}** 个条目，其中**已接入工作台 "
            f"{counts.get('entries_adopted', 0)} 个**、未接入 "
            f"{counts.get('entries_not_adopted', 0)} 个。"
            f"类型：{' · '.join(f'`{k}` ×{v}' for k, v in by_kind.items())}")
        lines += ["",
                  "| 条目 | 类型 | 阶段 | MCU / 内容 | 源文件 | 未读源码 | 引脚 |",
                  "|---|---|---|---|---|---|---|"]
        for entry in entries:
            kind = str(entry.get("kind", ""))
            detail = str(entry.get("mcu") or "")
            if kind == "bom":
                detail = f"{entry.get('rows', 0)} 行元件"
            elif kind == "fc-params":
                detail = f"{entry.get('count', 0)} 个参数"
            elif kind == "firmware":
                detail = f"{int(entry.get('size', 0)) / 1024:.0f} KB"
            stage = str(entry.get("phase", "")) or ("adopted" if entry.get("adopted") else "")
            lines.append(
                f"| `{entry.get('id', '')}` | {kind} | **{stage}** | {detail or '—'} "
                f"| {entry.get('source_total', '') or '—'} "
                f"| {entry.get('unread_sources', '') or '—'} "
                f"| {entry.get('pin_count', '') or '—'} |")
        lines += ["",
                  "> `not_adopted` = **有内容但没接入**（缺 `design.json`），"
                  "不是「不存在」。它的源码/固件真实存在，只是还进不了 2D/3D。",
                  "> `adopted` = 已是工作台工程。两种都不是错误，是两种不同状态。"]
    else:
        lines.append("_没有识别到任何工作区条目。_")

    # -- 覆盖度：哪些看了、哪些没看 --------------------------------------
    coverage = payload.get("coverage") or {}
    lines += ["", "### 事实：读取覆盖度（哪些看了、哪些没看）", ""]
    if coverage:
        total = coverage.get("total_files", 0)
        read = coverage.get("read_files", 0)
        lines += [
            "| 项 | 数量 |",
            "|---|---|",
            f"| 工作区文件总数 | {total} |",
            f"| **实际打开** | **{read}** "
            f"（{coverage.get('read_ratio', 0) * 100:.1f}%） |",
            f"| **未读** | **{coverage.get('unread_files', 0)}** |",
            "",
            f"> {coverage.get('note', '')}",
        ]
        unread_by_ext = coverage.get("unread_by_ext") or {}
        if unread_by_ext:
            lines += ["", "未读文件按扩展名："
                      + " · ".join(f"`{ext}` ×{n}"
                                   for ext, n in list(unread_by_ext.items())[:14])]
        unread = coverage.get("unread") or []
        if unread:
            lines += ["", "<details><summary>未读清单（前 120 个，点击展开）"
                          "</summary>", ""]
            for row in unread[:120]:
                lines.append(f"- `{row['path']}`")
            if len(unread) > 120:
                lines.append(f"- …另有 {len(unread) - 120} 个")
            lines += ["", "</details>"]

    loose = payload.get("loose_files") or []
    lines += ["", "### 事实：根目录上的散件", ""]
    if loose:
        lines.append(f"共 {counts.get('loose_files', len(loose))} 个"
                     f"（已排除 {counts.get('noise_files', 0)} 个第三方许可证/模板）：")
        lines.append("")
        for row in loose[:40]:
            lines.append(f"- `{row['path']}` · {_human_size(int(row['size']))}")
        if len(loose) > 40:
            lines.append(f"- …另有 {len(loose) - 40} 个")
    else:
        lines.append("_没有工程之外的相关文件。_")

    ext_counts = payload.get("ext_counts") or {}
    if ext_counts:
        lines += ["", "### 事实：扩展名分布", ""]
        lines.append(" · ".join(f"`{ext}` ×{count}"
                                for ext, count in ext_counts.items()))

    truncated = counts.get("truncated")
    if truncated:
        lines += ["", f"> 文件数达到上限 {MAX_FILES}，清单被截断——"
                      "不要把「没有列出」当成「不存在」。"]

    lines.append(FACT_END)
    return "\n".join(lines)


def initial_review_path(root: Path | None = None) -> Path:
    base = root if root is not None else project_root()
    return base / "初审.md"


def write_initial_review(payload: dict[str, Any],
                         *, root: Path | None = None) -> Path:
    """写初审.md。

    **保留已有的人工/AI 判断段**：只替换机器事实块，其余原样保留。
    覆盖整个文件会把 AI 上一轮的判断冲掉，那是"每次初审都从零开始"。
    """
    path = initial_review_path(root)
    facts = render_facts(payload)
    existing = ""
    if path.is_file():
        try:
            existing = path.read_text(encoding="utf-8")
        except OSError:
            existing = ""

    judgement = ""
    if FACT_BEGIN in existing and FACT_END in existing:
        tail = existing.split(FACT_END, 1)[1]
        # 去掉我们自己的判断段标题之外的内容都保留
        judgement = tail.strip()
    if judgement == "":
        judgement = "\n".join([
            "## 判断（AI 撰写）",
            "",
            "请在此填写：每个工程现在处于什么状态、最可能卡在哪、建议的下一步。",
            "**只引用上面的「事实」段**；没有事实支撑的结论请明确标为推测。",
            "",
            "_由 `hw_survey` 生成本段占位；AI 可以改写本段，但不要改动事实块。_",
        ])

    body = "\n".join([
        "# 工作区初审",
        "",
        f"> 工作区：`{payload.get('workspace', '')}`  ",
        f"> 生成时间：{payload.get('surveyed_at', '')}  ",
        "> 本文件由「硬件工作台」维护：**事实段由机器生成**，判断段由 AI 撰写。",
        "",
        facts,
        "",
        judgement,
        "",
    ])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")

    # -- 日志：勘察报告 + 活动流水（都在 <工作区>/logs/）-------------------
    # 报告写在初审.md 之外，因为初审.md 会被 AI 改写（判断段），而勘察
    # 报告是**一次事件**的快照，不该被同一次会话的后续动作擦掉。
    log = log_module
    base = root if root is not None else project_root()
    coverage = payload.get("coverage") or {}
    try:
        report = log.write_survey_report(base, payload, coverage)
        log.write_coverage_report(base, coverage)
        counts = payload.get("counts") or {}
        log.append_activity(
            base, step="survey",
            outcome="ok" if payload.get("status") == "ok" else "failed",
            detail=(f"{counts.get('entries', 0)} 条目"
                    f"（未接入 {counts.get('entries_not_adopted', 0)}）；"
                    f"读 {coverage.get('read_files', 0)}/"
                    f"{coverage.get('total_files', 0)} 个文件；"
                    f"fp={payload.get('fingerprint', '')}"),
        )
        payload["log"] = {
            "report": str(report),
            "activity": str(log.logs_dir(base) / log.ACTIVITY),
        }
    except OSError as exc:
        payload["log"] = {"error": f"写日志失败：{exc}"}
    return path


def read_initial_review(root: Path | None = None) -> dict[str, Any]:
    """读回初审.md，并解析出指纹（用于判断是否过期）。"""
    path = initial_review_path(root)
    if not path.is_file():
        return {"exists": False, "path": str(path), "fingerprint": "",
                "text": "", "stale": False}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return {"exists": False, "path": str(path), "fingerprint": "",
                "text": "", "error": str(exc), "stale": False}
    fingerprint = ""
    for line in text.splitlines():
        if line.startswith("<!-- fingerprint:") and line.endswith("-->"):
            fingerprint = line[len("<!-- fingerprint:"):-3].strip()
            break
    current = survey(root=root)
    return {
        "exists": True,
        "path": str(path),
        "fingerprint": fingerprint,
        "current_fingerprint": current.get("fingerprint", ""),
        "stale": fingerprint != "" and fingerprint != current.get("fingerprint", ""),
        "text": text,
    }
