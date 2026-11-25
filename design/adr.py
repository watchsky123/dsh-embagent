"""ADR（C4）—— 为什么这么选，写在文件里而不是脑子里。

## 这一层补什么

工程师接手时最贵的问题不是"这电路是什么"，是"**为什么**不用那个方案"。
`design.json` 只有结果；`justify` 只回答"它在板上干什么"；都没有"当时否决了
什么、凭什么"。

`ADR/NNNN-slug.md` 一条决策一篇，固定四节：

```
## 决定
## 理由
## 被否方案
## 证据
```

## 纪律

* **四节缺一节 = 不算 ADR**（`validate` 会报）——"证据"可以是文件/命令/实测
* 编号只增不改：改主意就新增一条并链回旧条（旧条保留，历史是证据）
* 只记**非显然**的决策（选 LDO 还是 DCDC、为什么这组脚）；显然的不记
* `index.json` 是导航，不是第二份内容（内容只在 md 里）
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from pathlib import Path
from typing import Any

__all__ = ["DIR", "INDEX", "add", "list_adrs", "validate", "describe"]

DIR = "ADR"
INDEX = "index.json"
_REQUIRED = ("决定", "理由", "被否方案", "证据")


def _slug(title: str, fallback: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9]+", "-", title.strip().lower()).strip("-")
    if text:
        return text[:40]
    # 中文标题没有 ASCII 可转写 → 用短哈希，**不音译**（音译会造出假英文名）
    return fallback


def _index_path(project_dir: Path) -> Path:
    return Path(project_dir) / DIR / INDEX


def _read_index(project_dir: Path) -> list[dict[str, Any]]:
    path = _index_path(project_dir)
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []
    rows = data.get("entries") if isinstance(data, dict) else None
    return [row for row in rows or [] if isinstance(row, dict)]


def list_adrs(project_dir: Path) -> list[dict[str, Any]]:
    """索引里的条目（按 id 排序）。"""
    return sorted(_read_index(project_dir), key=lambda row: str(row.get("id")))


def add(project_dir: Path, *, title: str, decision: str, why: str,
        alternatives: str = "", evidence: str = "") -> dict[str, Any]:
    """新增一条 ADR。**decision/why 不能空**（空的是口号，不是决策）。"""
    if not str(decision).strip() or not str(why).strip():
        raise ValueError("ADR 的「决定」与「理由」不能为空")
    root = Path(project_dir) / DIR
    root.mkdir(parents=True, exist_ok=True)
    entries = _read_index(project_dir)
    number = len(entries) + 1
    while any(str(row.get("id")) == f"{number:04d}" for row in entries):
        number += 1
    adr_id = f"{number:04d}"
    slug = _slug(title, f"adr-{hashlib.sha1(title.encode('utf-8')).hexdigest()[:6]}")
    filename = f"{adr_id}-{slug}.md"
    body = f"""# {title}

* 编号：{adr_id}
* 日期：{date.today().isoformat()}
* 状态：accepted

## 决定

{decision.strip()}

## 理由

{why.strip()}

## 被否方案

{alternatives.strip() or "_未记录 —— 补上：当时还考虑过什么、为什么不用。_"}

## 证据

{evidence.strip() or "_未记录 —— 补上：文件 / 命令 / 实测。_"}
"""
    (root / filename).write_text(body, encoding="utf-8")
    entries.append({"id": adr_id, "file": filename, "title": title.strip(),
                    "date": date.today().isoformat()})
    _index_path(project_dir).write_text(
        json.dumps({"entries": entries}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    return {"status": "ok", "id": adr_id, "file": str(root / filename),
            "entries": len(entries)}


def validate(project_dir: Path) -> dict[str, Any]:
    """索引↔文件一致 + 四节齐全。**缺节不是"写得简略"，是不算 ADR**。"""
    root = Path(project_dir) / DIR
    entries = _read_index(project_dir)
    problems: list[str] = []
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in entries:
        adr_id = str(entry.get("id") or "")
        if not adr_id:
            problems.append("索引条目缺 id")
            continue
        if adr_id in seen:
            problems.append(f"id 重复：{adr_id}")
        seen.add(adr_id)
        path = root / str(entry.get("file") or "")
        if not path.is_file():
            problems.append(f"{adr_id}: 文件不存在 {entry.get('file')}")
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        missing = [name for name in _REQUIRED if f"## {name}" not in text]
        rows.append({"id": adr_id, "file": path.name, "missing": missing})
        if missing:
            problems.append(f"{adr_id}: 缺节 {missing}")
    return {"status": "ok" if entries and not problems else
            ("not_ready" if not entries else "failed"),
            "dir": str(root), "rows": rows, "problems": problems,
            "counts": {"entries": len(entries), "problems": len(problems)}}


def describe() -> dict[str, Any]:
    return {
        "module": "dsh_embagent.design.adr",
        "dir": DIR,
        "sections": list(_REQUIRED),
        "status": "ok / failed（缺节或索引对不上）/ not_ready（一条都没有）",
        "discipline": ("四节缺一不算 ADR；编号只增不改；只记非显然决策"),
    }