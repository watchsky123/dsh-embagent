"""agent 能用的工具表 —— **白名单**，不是"全部 CLI 命令"。

## 为什么收窄

`design_cli` 有 50 多个命令。其中 `put` 之外的写入（`setpart` / 导入 /
发射 / promote）如果都暴露给 agent，它总能找到一条绕过判定的路。所以这里只放
**一条写入口**（`hw_put`，过 `DesignModel.validate()`）和一扇人确认门
（`hw_promote`，`deferred lock`）。

**没有** `hw_setpart` / `hw_move` / `hw_delpart`：细粒度改动太危险——
改了 `part.ref` 会让一条归因、一条断言、一条实测记录同时指向不存在的件。
要改设计就整份 `put`，让 `validate()` 一次看全。

## 四档，按"谁能承担后果"分

| 档 | 含义 | read-only | ask | auto |
|---|---|---|---|---|
| `read` | 只读（`list` / `get` / `snapshot` / `generate`） | 放行 | 放行 | 放行 |
| `judge` | 我们自己的判定（`ladder` / `claims` / …） | 放行 | 放行 | 放行 |
| `write` | 产生或修改产物（`put` / `emit`） | **拒** | 问 | 放行 |
| `human` | 人确认通道（`promote` = `deferred lock`） | **拒** | 问 | **也问** |

`judge` 放行的理由：判定是**只读语义**的——它读设计、写自己的产物
（`ladder.json` 等），不改 `design.json`。不让 agent 判定，它就没有反馈回路。

`human` 在 `auto` 也问的理由：`deferred lock` 是**不可撤销**的决定
（`deferred.json` 只追加、没有 unlock）。让自动模式替人做不可逆决定，
等于把整个仓库存档纪律的根基抽掉。

## 参数怎么变成 CLI

一个实现：`_to_argv()`。`project` 走第一个位置参数，其余 `--key=value`；
`True` 变成 `--key`（开关），列表逗号连接，对象 JSON。这样加工具只写一行
表项，不用写第二个 handler（AGENTS §1.1：判定只有一份）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable

__all__ = [
    "TIERS",
    "ToolSpec",
    "ToolTable",
    "TOOLS",
    "build_argv",
    "decide",
    "describe",
]

#: 四档。顺序有意义：越往后越需要人的同意。
TIERS = ("read", "judge", "write", "human")


@dataclass(frozen=True)
class ToolSpec:
    """一个工具。``command`` 是 `design_cli` 的命令名——**唯一实现**。"""

    name: str
    tier: str
    command: str
    description: str
    params: dict[str, str] = field(default_factory=dict)
    required: tuple[str, ...] = ()
    #: 工具自己的额外说明（比如"会创建 lceda 工程"）
    note: str = ""
    #: 参数的写法。**这是实测出来的差异，不是风格偏好**：
    #: P17–P21 新加的命令解析 `--key=value`，而 P0–P16 的老命令解析
    #: `--key value`（`cmd_generate` 只认后者）。统一成一种会让另一半全部失效。
    arg_style: str = "space"
    #: 少数工具不能靠通用 argv 拼装（`hw_put` 要传内存里的对象）。
    #: **其余全部走 `command`**，不给每个工具写一个 handler。
    handler: Callable[[dict[str, Any], Callable[[str, list[str]], dict[str, Any]]],
                      dict[str, Any]] | None = None

    def schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description + (f"（{self.note}）" if self.note else ""),
            "params": dict(self.params),
            "required": list(self.required),
        }

    def validate(self, args: dict[str, Any]) -> list[str]:
        """**入参校验**。在动手之前查，不是跑完再说。

        未知参数直接拒：多传一个 `project` 之外的键，说明调用方对工具的理解
        是错的，而"忽略未知键"会让它以为调用成功了。
        """
        problems: list[str] = []
        for key in self.required:
            if key not in args or args[key] in (None, ""):
                problems.append(f"{self.name}: 缺必填参数 {key!r}")
        for key, value in args.items():
            if key not in self.params:
                problems.append(
                    f"{self.name}: 未知参数 {key!r}（可用：{', '.join(self.params)}）")
                continue
            want = self.params[key]
            if want == "string" and not isinstance(value, str):
                problems.append(f"{self.name}.{key}: 期望 string，收到 {type(value).__name__}")
            elif want == "number" and not isinstance(value, (int, float)):
                problems.append(f"{self.name}.{key}: 期望 number，收到 {type(value).__name__}")
            elif want == "boolean" and not isinstance(value, bool):
                problems.append(f"{self.name}.{key}: 期望 boolean，收到 {type(value).__name__}")
            elif want == "object" and not isinstance(value, dict):
                problems.append(f"{self.name}.{key}: 期望 object，收到 {type(value).__name__}")
        return problems


#: 名字 → spec。handler 里要按名字取 spec 拼 argv 时用。
TOOLS_BY_NAME: dict[str, ToolSpec] = {}


def build_argv(spec: ToolSpec, args: dict[str, Any]) -> list[str]:
    """`{project: 'x', route: 'none'}` → `['x', '--route=none']`。"""
    argv: list[str] = []
    project = args.get("project")
    if isinstance(project, str) and project:
        argv.append(project)
    style = spec.arg_style
    for key, value in args.items():
        if key == "project":
            continue
        # 工具参数用下划线（JSON 习惯），CLI 用连字符（`--max-candidates`）。
        # **两者必须对应**，否则 CLI 静默忽略这个参数——实测踩过：
        # 传 `max_candidates=2` 却生成了 36 个候选。
        flag = key.replace("_", "-")
        if value is True:
            argv.append(f"--{flag}")
            continue
        if value is False or value in (None, ""):
            continue
        if isinstance(value, (list, tuple)):
            text_value = ",".join(str(x) for x in value)
        elif isinstance(value, dict):
            text_value = json.dumps(value, ensure_ascii=False)
        else:
            text_value = str(value)
        argv.append(f"--{flag}={text_value}" if style == "eq"
                    else f"--{flag}")
        if style != "eq":
            argv.append(text_value)
    return argv


class ToolTable:
    """白名单。``call`` 只走 `design_cli.COMMANDS`，不另写一套。"""

    def __init__(self, specs: list[ToolSpec] | None = None,
                 *, runner: Callable[[str, list[str]], dict[str, Any]] | None = None):
        self.specs = {spec.name: spec for spec in (specs or TOOLS)}
        self._runner = runner

    # -- 查询 -----------------------------------------------------------

    def get(self, name: str) -> ToolSpec | None:
        return self.specs.get(name)

    def names(self) -> list[str]:
        return sorted(self.specs)

    def schemas(self) -> list[dict[str, Any]]:
        return [self.specs[name].schema() for name in self.names()]

    def by_tier(self, tier: str) -> list[str]:
        return sorted(name for name, spec in self.specs.items() if spec.tier == tier)

    # -- 执行 -----------------------------------------------------------

    def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        """跑一个工具。**先校验入参**，不合法就不动手。"""
        spec = self.get(name)
        if spec is None:
            return {"status": "failed",
                    "findings": [f"工具 {name!r} 不在白名单里（可用：{', '.join(self.names())}）"]}
        problems = spec.validate(args)
        if problems:
            return {"status": "failed", "findings": problems, "not_run": True}
        runner = self._runner or _default_runner
        if spec.handler is not None:
            try:
                return spec.handler(args, runner)
            except Exception as exc:                  # noqa: BLE001
                return {"status": "failed",
                        "findings": [f"{type(exc).__name__}: {exc}"[:400]]}
        try:
            return runner(spec.command, build_argv(spec, args))
        except Exception as exc:                      # noqa: BLE001 - 工具失败要落成结果
            return {"status": "failed",
                    "findings": [f"{type(exc).__name__}: {exc}"[:400]]}


def _put_handler(args: dict[str, Any],
                 runner: Callable[[str, list[str]], dict[str, Any]]) -> dict[str, Any]:
    """`hw_put` 的桥：把内存里的 design 对象落成临时文件再交给 `put`。

    为什么不给 `cmd_put` 加一条"从 stdin 读 JSON"：那条路已经有
    （`-`），但它要求 caller 会写 stdin；通用调度器只传 argv。
    临时文件最省事，且保证 **`put` 仍然是唯一写入口**。
    """
    import tempfile
    from pathlib import Path

    project = str(args.get("project") or "")
    design = args.get("design")
    if not project or not isinstance(design, dict):
        return {"status": "failed",
                "findings": ["hw_put 需要 project 与 design（对象）"]}
    fd, name = tempfile.mkstemp(prefix="hwv-put-", suffix=".json")
    try:
        with open(fd, "w", encoding="utf-8") as handle:
            json.dump(design, handle, ensure_ascii=False)
        return runner("put", [project, str(Path(name))])
    finally:
        try:
            Path(name).unlink()
        except OSError:
            pass


def _generate_handler(args: dict[str, Any],
                      runner: Callable[[str, list[str]], dict[str, Any]]) -> dict[str, Any]:
    """`hw_generate` 的桥：给前几个候选附上 **materialized design**。

    为什么要附：agent 的下一步是 `hw_put`，而 `put` 要整份设计。
    如果不给，agent 只能自己拼设计——那等于让模型手写 `design.json`，
    正是我们要避免的（它会把 `pin` 写成 `"PA0"` 这种没人校验的东西）。

    只给前 `materialize` 个（默认 3 个）：50 个候选 × 完整设计会把
    上下文吃光，而排在后面的候选本来也不会被选。
    """
    from . import generate as generate_module

    cli_args = {k: v for k, v in args.items() if k != "materialize"}
    runner_result = runner("generate",
                           build_argv(TOOLS_BY_NAME["hw_generate"], cli_args))
    if runner_result.get("status") != "ok":
        return runner_result
    limit = int(args.get("materialize") or 3)
    req_path = str(args.get("requirement") or "")
    if args.get("fast") is True:
        runner_result["materialized"] = 0
        runner_result["note"] = "fast 模式只出清单，没有 design（也就不能直接 put）"
        return runner_result
    try:
        req = generate_module.load_requirement(req_path)
    except (OSError, ValueError) as exc:
        runner_result["note"] = f"需求读不回来，候选没有附 design：{exc}"
        return runner_result
    attached = 0
    for row in (runner_result.get("candidates") or [])[:max(1, limit)]:
        if not isinstance(row, dict):
            continue
        try:
            design = generate_module.materialize(row, req)
        except Exception as exc:                      # noqa: BLE001
            row["design_error"] = f"{type(exc).__name__}: {exc}"[:200]
            continue
        row["design"] = design.to_dict()
        attached += 1
    # 候选清单**要瘦身**：`generate` 能枚举出 36 个，全带回去会把上下文吃光，
    # 而 agent 只需要"前几个可用的 + 其余的一行摘要"。
    rows = [r for r in (runner_result.get("candidates") or []) if isinstance(r, dict)]
    keep = rows[:max(1, limit)]
    others = [{"id": r.get("id"), "why": str(r.get("why") or "")[:80],
               "hard_failures": r.get("hard_failures"), "cost": r.get("cost")}
              for r in rows[len(keep):]]
    runner_result["candidates"] = keep
    runner_result["candidates_total"] = len(rows)
    runner_result["others"] = others[:20]
    runner_result["materialized"] = attached
    runner_result["note"] = (f"候选 {len(rows)} 个；前 {len(keep)} 个带 design"
                             f"（可直接喂 hw_put），其余见 others")
    return runner_result


def _promote_handler(args: dict[str, Any],
                     runner: Callable[[str, list[str]], dict[str, Any]]) -> dict[str, Any]:
    """`hw_promote` 的桥：`deferred` 吃**位置参数**（`<id> lock <name> <value>`）。

    这一档是"人确认不可撤销决定"，所以 `action` 只放 `lock` / `show`：
    让 agent 自己选 `unlock` 等于给它一扇撤销权，而账本本来就没有 unlock。
    """
    project = str(args.get("project") or "")
    action = str(args.get("action") or "show")
    if action not in ("lock", "show"):
        return {"status": "failed",
                "findings": [f"hw_promote 只允许 lock / show（收到 {action!r}）；"
                             "账本没有 unlock，所以也不给 agent 这个机会"]}
    argv = [project, action]
    if action == "lock":
        argv += [str(args.get("name") or ""), str(args.get("value") or "")]
    return runner("deferred", argv)


def _default_runner(command: str, argv: list[str]) -> dict[str, Any]:
    """走 `design_cli` 的分发表。**判定仍然只有那一份实现。**"""
    from .. import design_cli

    handler = design_cli.COMMANDS.get(command)
    if handler is None:
        return {"status": "failed",
                "findings": [f"design_cli 里没有命令 {command!r}"]}
    return handler(argv)


def decide(spec: ToolSpec, mode: str) -> str:
    """`allow` / `ask` / `deny`。**这是唯一一处审批判断。**"""
    if mode not in ("read-only", "ask", "auto"):
        return "deny"
    if spec.tier in ("read", "judge"):
        return "allow"
    if mode == "read-only":
        return "deny"
    if spec.tier == "human":
        # 不可逆决定：**任何模式都要问**，包括 auto
        return "ask"
    return "allow" if mode == "auto" else "ask"


# ---------------------------------------------------------------------------
# 白名单本体
# ---------------------------------------------------------------------------

#: 刻意**不存在**的工具（验收会断言这一点）。
FORBIDDEN = ("hw_setpart", "hw_move", "hw_delpart", "hw_import", "hw_new",
             "hw_workspace", "hw_block", "hw_compile")

TOOLS: list[ToolSpec] = [
    # -- read ------------------------------------------------------------
    ToolSpec("hw_list", "read", "list", "列出工作区里的工程与产物状态"),
    ToolSpec("hw_get", "read", "get", "读一个工程的 design.json",
             {"project": "string"}, ("project",)),
    ToolSpec("hw_snapshot", "read", "snapshot", "读判定摘要（不重跑检查）",
             {"project": "string"}, ("project",)),
    ToolSpec("hw_generate", "read", "generate",
             "按需求有限枚举候选（只出清单，不写工程）",
             {"requirement": "string", "max_candidates": "number",
              "fast": "boolean", "materialize": "number"},
             ("requirement",),
             note="前 materialize 个候选带 design，可直接喂给 hw_put",
             handler=_generate_handler),
    # -- judge -----------------------------------------------------------
    ToolSpec("hw_ladder", "judge", "ladder", "跑 V0–V6 验证阶梯",
             {"project": "string", "with_sim": "boolean"}, ("project",)),
    ToolSpec("hw_claims", "judge", "claims", "推导并求值断言层",
             {"project": "string"}, ("project",)),
    ToolSpec("hw_justify", "judge", "justify", "从拓扑反推每个元件为什么在板上",
             {"project": "string", "ref": "string"}, ("project",)),
    ToolSpec("hw_handoff", "judge", "handoff", "交接单（六类未验证项）",
             {"project": "string"}, ("project",)),
    ToolSpec("hw_links", "judge", "links", "板间链路四查",
             {"project": "string"}, ("project",)),
    ToolSpec("hw_dims", "judge", "dims",
             "每颗元件的尺寸从哪来（datasheet/measured/catalog/estimated/proposed/unknown）"
             "；--apply 用立创 3D 模型回写（标 catalog）",
             {"project": "string", "apply": "boolean"},
             ("project",), arg_style="eq",
             note="proposed/estimated 不可进机械与布局判据"),
    ToolSpec("hw_mjcf", "judge", "mjcf",
             "机械模型（MJCF）：构建 + 用 MuJoCo 真的加载一次；--build 落 model.xml",
             {"project": "string", "build": "boolean", "free": "boolean",
              "weld": "boolean", "no_floor": "boolean", "out": "string"},
             ("project",), arg_style="eq",
             note="没装 MuJoCo 时是 not_ready（没验证过），不是 pass"),
    ToolSpec("hw_units", "judge", "units", "切功能单元并判角色",
             {"project": "string"}, ("project",)),
    ToolSpec("hw_sim", "judge", "sim", "分域仿真（--domain/--duration）",
             {"project": "string", "domain": "string", "duration": "number"},
             ("project",)),
    # -- write -----------------------------------------------------------
    ToolSpec("hw_put", "write", "put",
             "**唯一写 design.json 的入口**（过 DesignModel.validate）",
             {"project": "string", "design": "object"}, ("project", "design"),
             note="整份覆盖；细粒度改动没有工具，这是有意的",
             handler=_put_handler),
    ToolSpec("hw_emit", "write", "emit", "发射到立创EDA（封装后的命令，不暴露裸 invoke）",
             {"project": "string", "jobs": "string", "route": "string",
              "reuse": "boolean"}, ("project",),
             arg_style="eq"),
    # -- human -----------------------------------------------------------
    ToolSpec("hw_promote", "human", "deferred",
             "人确认通道（deferred lock 不可撤销）",
             {"project": "string", "action": "string", "name": "string", "value": "string"},
             ("project", "action", "name", "value"),
             note="auto 模式也必问",
             handler=_promote_handler),
]


TOOLS_BY_NAME.update({spec.name: spec for spec in TOOLS})


def describe() -> dict[str, Any]:
    table = ToolTable()
    return {
        "module": "hw_velxio.design.agent_tools",
        "tools": {tier: table.by_tier(tier) for tier in TIERS},
        "count": len(table.specs),
        "modes": ["read-only", "ask", "auto"],
        "tiers": list(TIERS),
        "forbidden": list(FORBIDDEN),
        "why_no_fine_grained": ("setpart/move/delpart 会让归因、断言、实测记录"
                                "同时指向不存在的件；改设计就整份 put"),
        "human_tier": "不可逆决定（deferred lock），任何模式都要问",
    }