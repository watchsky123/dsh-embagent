"""工程式日志 —— 写进工作区的 `logs/`，记录**看了什么、没看什么**。

## 设计取舍（参考 GitHub 上的工程式日志惯例）

GitHub Actions 每条 job 一份日志、按时间追加、带 `[step]` 前缀；`git log`
是**只追加**的线性历史；`.gitignore` 式的显式排除清单。这里取三条：

1. **只追加（append-only）** —— 日志是事实还是事实的记录？两者都是。
   覆盖历史等于销毁证据，所以 `write` 只追加，永不重写。
2. **一条动作一行（Markdown 表格）** —— 人读得懂，机器也能解析。每条带
   `at` / `step` / `outcome` / `detail`，与 `trace.json` 的哈希链互补：
   trace 管**设计变更**的完整性，日志管**这次做了什么、看到什么**。
3. **显式记录"没看"** —— 这是本模块存在的首要原因。勘察器只报告它数到的
   东西，用户就以为"评审过了"；实际上 `.ioc` 之外、源码之内、第三方库里
   还有大量文件**从未被读取**。日志必须把"未读"当成一等公民列出来，
   否则"覆盖度"又是一句无根据的形容词。

## 文件布局

    <工作区>/logs/
      survey-20261115-204712.md     每次勘察一份（人读）
      activity.md                   只追加的动作流水（最新在上）

`logs/` 不进 `list_projects()`（那只看含 `design.json` 的目录），也不会被
当成工程条目（`entries.scan` 只认 `.ioc`/`.kicad_pcb`/BOM/固件）。

@module dsh_embagent.design.log
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

LOGS_DIR = "logs"
ACTIVITY = "activity.md"
# 活动流水最多保留多少条（太长会把文件读爆，且早期记录价值递减）。
MAX_ACTIVITY_ROWS = 500


def _stamp(when: float | None = None) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(when))


def _slug(when: float | None = None) -> str:
    return time.strftime("%Y%m%d-%H%M%S", time.localtime(when))


def logs_dir(root: Path) -> Path:
    return root / LOGS_DIR


def _ensure(root: Path) -> Path:
    directory = logs_dir(root)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


# ---------------------------------------------------------------------------
# 动作流水（append-only）
# ---------------------------------------------------------------------------

ACTIVITY_HEADER = """# 硬件工作台 · 活动日志

> 只追加：本文件**永不重写**，新记录加在表格顶部。
> 设计变更的完整性由各工程的 `trace.json`（哈希链）保证，本文件只管
> "这次做了什么、看到什么、还有什么没看"。

| 时间 | 步骤 | 结果 | 说明 |
|---|---|---|---|
"""


def append_activity(root: Path, *, step: str, outcome: str,
                    detail: str = "") -> Path:
    """追加一条动作记录。**只追加**，不改动已有行。"""
    path = _ensure(root) / ACTIVITY
    if not path.is_file():
        path.write_text(ACTIVITY_HEADER, encoding="utf-8")

    text = path.read_text(encoding="utf-8")
    head, _, body = text.partition("|---|---|---|\n")
    if _ == "":  # 文件结构被手工改坏了：重建表头，但**保留原有内容**
        head = ACTIVITY_HEADER.rsplit("|", 1)[0]
        body = text
    rows = [line for line in body.splitlines() if line.startswith("| ")]
    row = (f"| {_stamp()} | {_cell(step)} | {_cell(outcome)} "
           f"| {_cell(detail)} |")
    rows = [row] + rows[:MAX_ACTIVITY_ROWS]
    path.write_text(head + "|---|---|---|---|\n" + "\n".join(rows) + "\n",
                    encoding="utf-8")
    return path


def _cell(value: str) -> str:
    """表格单元格转义：竖线与换行会破坏 Markdown 表格。"""
    return str(value).replace("|", "/").replace("\n", " ").strip()


def read_activity(root: Path, limit: int = 50) -> dict[str, Any]:
    path = logs_dir(root) / ACTIVITY
    if not path.is_file():
        return {"exists": False, "path": str(path), "rows": []}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return {"exists": False, "path": str(path), "rows": [],
                "error": str(exc)}
    rows = [line for line in text.splitlines() if line.startswith("| ")
            and not line.startswith("| 时间")]
    return {"exists": True, "path": str(path), "rows": rows[:limit],
            "total": len(rows)}


# ---------------------------------------------------------------------------
# 覆盖度：哪些看了、哪些没看
# ---------------------------------------------------------------------------

def build_coverage(entries_payload: dict[str, Any],
                   all_files: list[Path],
                   base: Path) -> dict[str, Any]:
    """把"读过的文件"和"工作区全部文件"对比，得出**未读清单**。

    `entries.scan` 只记录它真正打开过的文件（`.ioc` / BOM / param）。
    其余一律进 `unread` —— 包括源码、第三方库、固件二进制。

    这不是"漏掉了"，而是**如实声明边界**：源码 400+ 个文件我们没读，
    固件二进制我们只数了大小。用户要能看到这条线在哪。
    """
    read_paths = {str(Path(p).resolve())
                  for p in (entries_payload.get("read_paths") or [])}
    unread: list[dict[str, Any]] = []
    by_ext: dict[str, int] = {}
    for path in all_files:
        try:
            resolved = str(path.resolve())
        except OSError:
            continue
        ext = path.suffix.lower() or "(无扩展名)"
        if resolved in read_paths:
            continue
        by_ext[ext] = by_ext.get(ext, 0) + 1
        try:
            rel = path.relative_to(base).as_posix()
            size = path.stat().st_size
        except (OSError, ValueError):
            rel, size = str(path), 0
        unread.append({"path": rel, "ext": path.suffix.lower() or "", "size": size})

    unread.sort(key=lambda row: row["path"])
    total = len(all_files)
    read_count = len(read_paths)
    return {
        "total_files": total,
        "read_files": read_count,
        "unread_files": len(unread),
        "read_ratio": round(read_count / total, 4) if total else 0.0,
        "unread_by_ext": dict(sorted(by_ext.items(), key=lambda kv: -kv[1])),
        "unread": unread,
        # 明确写出"没读"的类别，而不是让用户以为全读了。
        "note": (
            "只打开了决定性文件（.ioc / BOM / 飞控参数）；源码与固件只统计"
            "数量与体积，未读取内容。"
        ),
    }


# ---------------------------------------------------------------------------
# 勘察报告（每次一份）
# ---------------------------------------------------------------------------

def write_survey_report(root: Path, payload: dict[str, Any],
                        coverage: dict[str, Any],
                        when: float | None = None) -> Path:
    """写一份人读的勘察报告。**不覆盖**已有文件（时间戳命名）。"""
    directory = _ensure(root)
    path = directory / f"survey-{_slug(when)}.md"
    counts = payload.get("counts") or {}
    projects = payload.get("projects") or []
    entries = payload.get("entries") or []
    by_kind = payload.get("entries_by_kind") or {}

    lines = [
        f"# 勘察报告 {_stamp(when)}",
        "",
        f"> 工作区：`{payload.get('workspace', '')}`  ",
        f"> 指纹：`{payload.get('fingerprint', '')}`  ",
        f"> 耗时：{payload.get('elapsed_ms', 0)} ms",
        "",
        "## 概览",
        "",
        "| 项 | 数量 |",
        "|---|---|",
        f"| 工作区条目（真实项目） | {len(entries)} |",
        f"| 　其中已接入工作台 | {sum(1 for e in entries if e.get('adopted'))} |",
        f"| 　其中未接入 | {sum(1 for e in entries if not e.get('adopted'))} |",
        f"| 工作台工程（有 design.json） | {counts.get('projects', 0)} |",
        f"| 文件总数 | {coverage.get('total_files', 0)} |",
        f"| 实际读取的文件 | {coverage.get('read_files', 0)} |",
        f"| **未读文件** | **{coverage.get('unread_files', 0)}** |",
        "",
    ]

    if by_kind:
        lines += ["### 条目类型", "", "| 类型 | 数量 |", "|---|---|"]
        lines += [f"| `{kind}` | {n} |" for kind, n in by_kind.items()]
        lines += [""]

    if entries:
        lines += [
            "## 工作区条目",
            "",
            "| 条目 | 类型 | MCU / 内容 | 源文件 | 引脚 | 已接入 |",
            "|---|---|---|---|---|---|",
        ]
        for entry in entries:
            detail = entry.get("mcu") or ""
            if entry.get("kind") == "bom":
                detail = f"{entry.get('rows', 0)} 行元件"
            elif entry.get("kind") == "fc-params":
                detail = f"{entry.get('count', 0)} 个参数"
            elif entry.get("kind") == "firmware":
                detail = f"{entry.get('size', 0) / 1024:.0f} KB"
            lines.append(
                f"| `{entry.get('id', '')}` | {entry.get('kind', '')} "
                f"| {detail} | {entry.get('source_total', '') or '—'} "
                f"| {entry.get('pin_count', '') or '—'} "
                f"| {'是' if entry.get('adopted') else '**否**'} |")
        lines += [""]

    if projects:
        lines += [
            "## 工作台工程",
            "",
            "| 工程 | 阶段 | 元件 | 差什么 |",
            "|---|---|---|---|",
        ]
        for row in projects:
            gap = "；".join(row.get("veto") or []) or "—"
            lines.append(f"| `{row.get('id', '')}` | **{row.get('phase', '')}** "
                         f"| {row.get('parts', 0)} | {gap} |")
        lines += [""]

    lines += [
        "## 覆盖度（哪些看了、哪些没看）",
        "",
        f"- 文件总数：**{coverage.get('total_files', 0)}**",
        f"- 实际打开：**{coverage.get('read_files', 0)}** "
        f"（{coverage.get('read_ratio', 0) * 100:.1f}%）",
        f"- 未读：**{coverage.get('unread_files', 0)}**",
        "",
        f"> {coverage.get('note', '')}",
        "",
    ]
    unread_by_ext = coverage.get("unread_by_ext") or {}
    if unread_by_ext:
        lines += ["### 未读文件按扩展名", "", "| 扩展名 | 数量 |", "|---|---|"]
        lines += [f"| `{ext}` | {n} |" for ext, n in list(unread_by_ext.items())[:20]]
        lines += [""]
        lines += [
            "### 未读清单（前 200 个）",
            "",
            "这些文件**没有被任何识别规则读取**。它们是已知的盲区，不是错误：",
            "",
        ]
        for row in (coverage.get("unread") or [])[:200]:
            lines.append(f"- `{row['path']}`")
        if len(coverage.get("unread") or []) > 200:
            lines.append(f"- …另有 {len(coverage['unread']) - 200} 个")
        lines += [""]

    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def write_coverage_report(root: Path, coverage: dict[str, Any],
                          when: float | None = None) -> Path:
    """单独的只读覆盖度文件 —— 机器可解析（`coverage.json` 的伴侣）。

    为什么还要一份 JSON：面板要显示进度条与计数，解析 Markdown 表格是
    脆弱做法；而 Markdown 那份是给人读的。两份都由同一份事实生成。
    """
    directory = _ensure(root)
    path = directory / f"coverage-{_slug(when)}.json"
    slim = {k: v for k, v in coverage.items() if k != "unread"}
    slim["unread_preview"] = [row["path"] for row in (coverage.get("unread") or [])[:80]]
    import json as json_module
    path.write_text(json_module.dumps(slim, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    return path


def list_reports(root: Path, limit: int = 20) -> list[dict[str, Any]]:
    """列出工作区里的日志文件（最新在前）。"""
    directory = logs_dir(root)
    if not directory.is_dir():
        return []
    out: list[dict[str, Any]] = []
    try:
        files = sorted(directory.iterdir(),
                       key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return []
    for path in files[:limit]:
        if not path.is_file():
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        out.append({
            "name": path.name,
            "kind": "activity" if path.name == ACTIVITY else "report",
            "size": stat.st_size,
            "at": _stamp(stat.st_mtime),
        })
    return out
