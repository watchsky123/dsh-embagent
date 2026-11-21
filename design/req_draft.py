"""需求草稿 —— 把「一句话」变成**可校验的结构化需求**，但**不立刻生效**。

## 这一层解决什么

`generate` 只吃 `Requirement`（见 `generate.py`）：

```python
id / title / roles[] / interfaces[] / constraints{} / numbers{} / notes
```

人不会手写这七个字段；模型能写，但它写的东西**不能直接当需求**——
未知 role、悬空 interface、自环，任何一条错都会让 `generate` 枚举出一堆
`unresolved_roles`，而报告里只写"未处理的角色：xxx"。

所以这一层做三件事：

1. **收草稿**（`req_draft.json`）——模型可以反复改
2. **校验**（对着 `Requirement` 与 `roles.py` 的真实契约）
3. **人确认后才生效**（`req.json`）——两个文件，不是同一个

## 为什么草稿和需求要分成两个文件

合成一个的话，"模型写了但人没看"和"人确认了"在磁盘上长得一模一样。
分成两个，`apply` 是唯一跨越那一步的动作，而且它要 `confirm=True`。

这和 `design.json` 只走 `put`、`deferred` 只走 `lock` 是同一条纪律。

## `numbers` 没有任何消费方（实测）

`Requirement.numbers` 只在 `from_dict` / `to_dict` 里过一遍，
**没有任何模块读它**。`constraints` 也只有 `area_mm2` 被
`capability.candidates` 消费。

所以 `validate()` 必须把「你填了 numbers，但它不影响任何结论」报出来——
否则人会以为那些数字在约束设计。这和 AGENTS §3.16（没人读 ≠ 不敏感）
是同一形状，只是换了个字段。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = [
    "CONSUMED_CONSTRAINTS",
    "CONSUMERLESS_FIELDS",
    "DRAFT_DIR",
    "DRAFT_FILE",
    "REQ_FILE",
    "ReqDraft",
    "apply",
    "describe",
    "list_workspace",
    "load",
    "save",
    "validate",
]

#: 草稿与需求的落盘名。**两个文件**：前者模型可改，后者人确认后才有。
DRAFT_FILE = "req_draft.json"
REQ_FILE = "req.json"

#: **工作区级**草稿目录。工程还不存在时，草稿落在这里。
#:
#: 为什么需要它：草稿原本只写进 `<project>/req_draft.json`，而工程目录要有
#: `design.json` 才算存在。于是"从零一句话开局"走不通——还没有工程，草稿
#: 无处可放。工作区级草稿把这一步补上：**先有需求，再建工程**。
DRAFT_DIR = "_drafts"

#: 真正被代码消费的 `constraints` 键（**扫源码得到的，不是猜的**：
#: `capability.py` 里的 `constraints.get("area_mm2")`）。
CONSUMED_CONSTRAINTS = ("area_mm2",)

#: 能填但**没有任何消费方**的字段。填了不会改变任何结论。
CONSUMERLESS_FIELDS = ("numbers",)


@dataclass
class ReqDraft:
    """一份需求草稿。字段与 `generate.Requirement` **一一对应**。"""

    id: str = "req"
    title: str = ""
    roles: list[str] = field(default_factory=list)
    interfaces: list[dict[str, Any]] = field(default_factory=list)
    constraints: dict[str, Any] = field(default_factory=dict)
    numbers: dict[str, Any] = field(default_factory=dict)
    notes: str = ""

    # -- 序列化 ---------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "title": self.title,
            "roles": list(self.roles),
            "interfaces": [dict(i) for i in self.interfaces],
            "constraints": dict(self.constraints),
            "numbers": dict(self.numbers), "notes": self.notes,
        }

    @staticmethod
    def from_dict(data: dict[str, Any] | None) -> "ReqDraft":
        """宽容地收：类型不对就退回默认值，**不抛**。

        为什么不抛：这是**模型产出**的输入，抛异常会让调用方只看到
        "TypeError"，而真正该说的是"interfaces 第 3 项不是对象"。
        校验交给 `validate()`，它给的是结构化的问题清单。
        """
        data = data or {}
        interfaces: list[dict[str, Any]] = []
        for item in data.get("interfaces") or []:
            if isinstance(item, dict):
                interfaces.append({str(k): v for k, v in item.items()})
            else:
                # 保留成可报告的形状：校验时能指出"这一项不是对象"
                interfaces.append({"_invalid": item})
        roles = [str(r) for r in (data.get("roles") or [])]
        return ReqDraft(
            id=str(data.get("id") or "req"),
            title=str(data.get("title") or ""),
            roles=roles,
            interfaces=interfaces,
            constraints=dict(data.get("constraints") or {})
            if isinstance(data.get("constraints"), dict) else {},
            numbers=dict(data.get("numbers") or {})
            if isinstance(data.get("numbers"), dict) else {},
            notes=str(data.get("notes") or ""),
        )


def _known_roles() -> list[str]:
    """**从 `roles.py` 取**，不另写一份清单（AGENTS §3.15）。"""
    from . import roles as roles_module
    return sorted({t.role for t in roles_module.ROLE_TEMPLATES})


def validate(draft: ReqDraft) -> dict[str, Any]:
    """结构化校验。返回 ``{ok, problems[], warnings[], counts, basis}``。

    三类结论分开：`problems` 拦 `apply`；`warnings` 只提示（如 numbers 无消费方）。
    """
    known = _known_roles()
    problems: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    # -- roles --------------------------------------------------------
    if not draft.roles:
        problems.append({
            "kind": "roles_empty",
            "message": "roles 是空的：没有功能角色，generate 会枚举不出任何候选",
            "fix": "至少给一个角色（可用：" + ", ".join(known[:8]) + " …）",
        })
    unknown = [r for r in draft.roles if r not in known]
    if unknown:
        problems.append({
            "kind": "role_unknown",
            "message": f"{len(unknown)} 个角色不在 roles.py 里：{', '.join(unknown)}",
            "fix": "改成已知角色（" + ", ".join(known) + "）",
            "roles": unknown,
        })
    dupes = sorted({r for r in draft.roles if draft.roles.count(r) > 1})
    if dupes:
        warnings.append({
            "kind": "role_duplicated",
            "message": f"角色重复：{', '.join(dupes)}（会被合并，但你可能是想加数量）",
        })

    # -- interfaces ---------------------------------------------------
    known_set = set(draft.roles)
    for index, row in enumerate(draft.interfaces):
        if "_invalid" in row:
            problems.append({
                "kind": "interface_not_object",
                "message": f"interfaces[{index}] 不是对象",
                "fix": "每一项都要是 {a, b, protocol, level_v} 这样的对象",
            })
            continue
        a, b = str(row.get("a") or ""), str(row.get("b") or "")
        if not a or not b:
            problems.append({
                "kind": "interface_missing_end",
                "message": f"interfaces[{index}] 缺 a 或 b",
                "fix": "两端都要写（都是 roles 里的名字）",
            })
            continue
        dangling = [x for x in (a, b) if x not in known_set]
        if dangling:
            problems.append({
                "kind": "interface_dangling",
                "message": (f"interfaces[{index}] 引用了不在 roles 里的 "
                            f"{', '.join(dangling)}"),
                "fix": "要么把它加进 roles，要么改成本次真有的角色",
            })
        if a == b:
            problems.append({
                "kind": "interface_self_loop",
                "message": f"interfaces[{index}] 两端是同一个角色 {a}",
                "fix": "自环不是板间链路；芯片内部连接不该写成 interface",
            })

    # -- constraints --------------------------------------------------
    for key, value in draft.constraints.items():
        if key not in CONSUMED_CONSTRAINTS:
            warnings.append({
                "kind": "constraint_unread",
                "message": f"constraints.{key} 没有被任何代码读（只有 "
                           f"{', '.join(CONSUMED_CONSTRAINTS)} 会被消费）",
            })
            continue
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            problems.append({
                "kind": "constraint_not_number",
                "message": f"constraints.{key} 不是数字",
                "fix": "给它一个正数",
            })
        elif value <= 0:
            problems.append({
                "kind": "constraint_not_positive",
                "message": f"constraints.{key} = {value}（必须是正数）",
            })

    # -- numbers：**填了也不影响任何结论** -----------------------------
    #
    # 这一条不是"低影响"，是"没有消费方"。分开说，是因为后者会让人
    # 以为那些数字已经进模型了（AGENTS §3.16）。
    for name in CONSUMERLESS_FIELDS:
        value = getattr(draft, name, None)
        if value:
            warnings.append({
                "kind": "field_without_consumer",
                "message": (f"{name} 里填了 {len(value)} 项，但**没有任何代码读它**"
                            "——这些数不会影响 generate 或任何判定"),
                "fix": ("要么在消费方接上（coverify 的 numbers 还没接），"
                        "要么把它们写进 constraints 的已支持键"),
            })

    return {
        "ok": not problems,
        "problems": problems,
        "warnings": warnings,
        "counts": {
            "roles": len(draft.roles),
            "interfaces": len(draft.interfaces),
            "constraints": len(draft.constraints),
            "numbers": len(draft.numbers),
            "problems": len(problems),
            "warnings": len(warnings),
        },
        "basis": ("roles 比对 roles.ROLE_TEMPLATES；constraints 只认 "
                  f"{', '.join(CONSUMED_CONSTRAINTS)}；"
                  f"{', '.join(CONSUMERLESS_FIELDS)} 无消费方"),
    }


# ---------------------------------------------------------------------------
# 落盘
# ---------------------------------------------------------------------------


def _workspace_draft_path(store: Any, draft_id: str) -> Path:
    """工程还不存在时草稿放哪：``<工作区>/_drafts/<id>.json``。"""
    from .store import project_root
    return project_root() / DRAFT_DIR / f"{draft_id}.json"


def save(store: Any, draft: ReqDraft) -> dict[str, Any]:
    """写草稿。**不碰 `req.json`**——那是 `apply()` 的事。

    ## 落点按"工程在不在"决定（从零开局的关键）

    * 工程已存在 → `<project>/req_draft.json`
    * 工程还没有 → `<工作区>/_drafts/<id>.json`

    第二条是"说一句话就开工"的入口：需求先于工程存在。返回值里的 `scope`
    说清落在哪一层，**不让人猜**。
    """
    payload = draft.to_dict()
    report = validate(draft)
    payload["_validate"] = report

    if store.exists():
        store.write(DRAFT_FILE, payload)
        return {"status": "ok", "scope": "project", "file": DRAFT_FILE,
                "path": str(store.path(DRAFT_FILE)), "validation": report}

    path = _workspace_draft_path(store, store.id)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                        encoding="utf-8")
    except OSError as exc:
        return {"status": "failed", "scope": "workspace",
                "findings": [f"写工作区草稿失败：{exc}"]}
    return {"status": "ok", "scope": "workspace", "file": DRAFT_DIR + "/"
            + path.name, "path": str(path), "validation": report}


def load(store: Any) -> ReqDraft | None:
    """读草稿：**先看工程，再看工作区**。

    没有就返回 `None`（**不是空草稿**：那是"有但全空"）。
    """
    raw = store.read(DRAFT_FILE)
    if not isinstance(raw, dict):
        path = _workspace_draft_path(store, store.id)
        if path.is_file():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                raw = None
    if not isinstance(raw, dict):
        return None
    body = {k: v for k, v in raw.items() if not k.startswith("_")}
    return ReqDraft.from_dict(body)


def draft_scope(store: Any) -> str:
    """草稿现在落在哪：`project` / `workspace` / `""`（没有）。"""
    if isinstance(store.read(DRAFT_FILE), dict):
        return "project"
    if _workspace_draft_path(store, store.id).is_file():
        return "workspace"
    return ""


def list_workspace() -> list[dict[str, Any]]:
    """列出**工作区级**草稿（工程还没有的那些）。

    为什么需要它：`snapshot()` 按工程走——工程不存在时它根本不跑，
    于是工作区草稿在面板里看不见。这条命令让它们**可被发现**。
    """
    from .store import project_root
    folder = project_root() / DRAFT_DIR
    if not folder.is_dir():
        return []
    rows: list[dict[str, Any]] = []
    for path in sorted(folder.glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            rows.append({"id": path.stem, "path": str(path),
                         "ok": False, "roles": [],
                         "problems": [f"读不到：{exc}"], "warnings": []})
            continue
        body = {k: v for k, v in raw.items() if not k.startswith("_")}
        draft = ReqDraft.from_dict(body)
        report = validate(draft)
        rows.append({
            "id": draft.id or path.stem, "title": draft.title,
            "roles": list(draft.roles),
            "path": str(path), "ok": report["ok"],
            "problems": [p["message"] for p in report["problems"]],
            "warnings": [w["message"] for w in report["warnings"]],
        })
    return rows


def _create_project(store: Any, draft: ReqDraft, *,
                    board: str = "custom", title: str = "") -> dict[str, Any]:
    """按草稿建一个**空工程**（从零开局那一步）。

    复用 `new` 的同一套动作：板卡预设 + 空账本 + auto_lock + 落盘。
    **不重写**这些步骤——`auto_lock` 漏一次，导出闸门就会问"mcu_family 未闭合"
    而用户以为自己漏了什么（`cmd_put` 那段注释里记着同一个坑）。
    """
    from . import discipline
    from .boards import board_preset
    from .model import DesignModel

    if store.exists():
        return {"status": "ok", "created": False}

    root = store.dir.parent
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return {"status": "failed",
                "findings": [f"工作区无法创建：{root}（{exc}）"]}

    design = DesignModel(id=store.id, title=title or draft.title or store.id,
                         board=board_preset(board))
    ledger = discipline.new_ledger()
    discipline.auto_lock(design, ledger)
    store.save(design)
    store.write("deferred.json", discipline.ledger_to_json(ledger))
    discipline.append(store, "cli", "design.created",
                      {"board": board, "title": design.title,
                       "via": "req-draft --apply"})
    return {"status": "ok", "created": True, "board": board,
            "warnings": list(design.warnings)}


def apply(store: Any, *, confirm: bool = False,
          board: str = "custom", title: str = "") -> dict[str, Any]:
    """把草稿**变成真需求**（写 `req.json`）。四道闸门缺一不可。

    1. 有草稿（工程级或工作区级）
    2. 校验通过
    3. `confirm=True`（人点过）
    4. 工程不存在时**顺手建一个空工程**——这是"从零开局"那一步

    任一条不满足都**不写文件**，并说明是哪一条。
    """
    draft = load(store)
    if draft is None:
        return {"status": "not_ready",
                "reason": (f"没有草稿（{DRAFT_FILE} 或 "
                           f"{DRAFT_DIR}/<id>.json 都不存在）——先 hw_req_draft"),
                "scope": ""}

    report = validate(draft)
    if not report["ok"]:
        return {"status": "failed", "reason": "草稿没通过校验",
                "scope": draft_scope(store), "validation": report,
                "findings": [p["message"] for p in report["problems"]]}

    if not confirm:
        # **默认拒绝**：没确认就是没人确认过。这一条和 hw_promote 同源。
        return {"status": "failed",
                "reason": "草稿合法，但**没有人确认**（apply 要 confirm=True）",
                "scope": draft_scope(store), "validation": report,
                "findings": ["确认后才会写 " + REQ_FILE]}

    # **建工程放在确认之后**：没确认就建工程等于替人占了一个 id。
    created = _create_project(store, draft, board=board, title=title)
    if created.get("status") != "ok":
        return {"status": "failed", "reason": "建工程失败",
                "findings": created.get("findings") or [],
                "validation": report}

    store.write(REQ_FILE, draft.to_dict())

    # **草稿搬进工程**，删掉工作区那一份。
    #
    # 不搬的话会留下两份副本：工程级 `req_draft.json` 与工作区
    # `_drafts/<id>.json`。之后改了其中一份，另一份不会跟着变——
    # 而"改的是哪一份"在报告里分不出来。这正是这个仓库反复要避免的
    # 「两个事实源」。搬完之后 `draft_scope` 是 `project`，面板也就能看见。
    promoted = False
    prior_scope = draft_scope(store)
    store.write(DRAFT_FILE, {**draft.to_dict(), "_validate": report})
    if prior_scope == "workspace":
        try:
            _workspace_draft_path(store, store.id).unlink()
            promoted = True
        except OSError:
            # 删不掉不算失败：`load()` 先看工程，所以两份里工程那份胜出。
            # 但**要说出来**，别让人以为没留副本。
            promoted = False

    out: dict[str, Any] = {
        "status": "ok", "file": REQ_FILE, "requirement": draft.to_dict(),
        "validation": report, "scope": "project",
        "created_project": bool(created.get("created")),
        "promoted_from_workspace": prior_scope == "workspace",
        "workspace_draft_removed": promoted,
        "warnings": [w["message"] for w in report["warnings"]],
    }
    if created.get("created"):
        out["board"] = created.get("board")
        out["design_warnings"] = created.get("warnings") or []
    return out


def describe() -> dict[str, Any]:
    return {
        "module": "hw_velxio.design.req_draft",
        "fields": list(ReqDraft.__dataclass_fields__),
        "files": {"draft": DRAFT_FILE, "requirement": REQ_FILE},
        "consumed_constraints": list(CONSUMED_CONSTRAINTS),
        "consumerless": list(CONSUMERLESS_FIELDS),
        "why_two_files": ("草稿模型可改，需求只有人确认后才有；"
                          "合成一个的话『写了没看』和『确认了』在磁盘上一样"),
        "scope": ("草稿落点按『工程在不在』决定：工程在 → <project>/req_draft.json；"
                  f"工程还没有 → <工作区>/{DRAFT_DIR}/<id>.json（从零开局）"),
        "apply_creates_project": ("apply --confirm 时工程不存在会顺手建一个空工程"
                                  "（板卡预设 + auto_lock + 落盘）"),
        "list_workspace": (f"工作区 {DRAFT_DIR}/ 下的草稿可列出——"
                           "但 snapshot 按工程走，工程不存在时面板看不到它们"),
        "gate": "validate → confirm → 才写 req.json",
    }