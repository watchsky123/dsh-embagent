"""agent 循环 —— 追加式历史 + 入参先校验 + 四档审批，**假模型 0 token 可测**。

## 为什么是"循环"而不是"一次调用"

模型一次给不完一个设计：它得先看工程、跑判定、改设计、再判定。所以循环的
**每个动作都要留痕**，而且留痕方式决定了三件事能不能做：

| 需求 | 靠什么 |
|---|---|
| 中断后接着跑 | 历史只**追加**，从不原地改 |
| 重放结果一致 | 回放日志时**不重跑工具**，用记录下来的结果 |
| 事后审计"它为什么这么做" | 每条决策（allow/ask/deny）+ 理由都进日志 |

所以 `Journal` 是 JSONL，一行一个事件，**只 append**。它不是调试输出，
是这个循环的唯一事实源：`resume()` 只读它，`compare()` 只比它。

## 入参先校验，不是跑完再说

`ToolSpec.validate()` 在工具执行**之前**跑。理由：`hw_put` 少一个字段就会被
`DesignModel.validate()` 拒，但那时已经过了一轮模型往返；而多一个未知参数
（比如把 `project` 拼成 `proj`）如果不查，工具会**忽略它并按默认行为跑成功**
——这正是 AGENTS §3.7 那个"参数被静默吃掉"的形状。

## 四档审批（详见 `agent_tools.decide`）

```
read / judge  → 任何模式放行
write         → read-only 拒；ask 问；auto 放行
human         → read-only 拒；ask 问；**auto 也问**
```

`human` 档对应 `deferred lock`（不可撤销）。**让自动模式替人做不可逆决定，
等于把存档纪律的根基抽掉**，所以这一档没有"放行"的分支。

## 没有审批回调时默认拒绝

`ask` 落到没有回调的场合（CLI、CI）→ **拒绝并写明"需要人确认"**，
不是默默放行。默认放行的话，"在 CI 里跑过"会被当成"人确认过了"。

## 假模型

`Client` 只要一个 `complete(messages, tools) -> Response`。
`scripts/fake_model.py` 的 `ScriptedClient` 用预先写好的脚本回答，
**不联网、不花 token**，所以整条链路能进回归。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

from . import agent_tools as at

__all__ = [
    "Approval",
    "Client",
    "Interrupted",
    "Journal",
    "Response",
    "compare",
    "describe",
    "read_journal",
    "resume",
    "run",
]

#: 一次 run 最多几步。**上限不是防死循环的礼貌，是成本闸门**：
#: 每步都可能是一次模型调用 + 若干工具调用。
MAX_STEPS = 20


class Interrupted(RuntimeError):
    """按 `interrupt_after` 主动停下（用来验"中断再接着跑"）。"""

    def __init__(self, seq: int):
        super().__init__(f"在第 {seq} 个事件后中断")
        self.seq = seq


class Client(Protocol):
    """模型客户端。**只有这一个方法**，所以假模型 20 行就能写出来。"""

    def complete(self, messages: list[dict[str, Any]],
                 tools: list[dict[str, Any]]) -> "Response": ...


@dataclass
class Response:
    """一次模型回复：一段话 + 零到多个工具调用。"""

    text: str = ""
    calls: list[dict[str, Any]] = field(default_factory=list)

    @staticmethod
    def from_any(raw: Any) -> "Response":
        if isinstance(raw, Response):
            return raw
        if isinstance(raw, dict):
            return Response(text=str(raw.get("text") or ""),
                            calls=[dict(c) for c in (raw.get("calls") or [])])
        return Response(text=str(raw or ""))


#: 审批回调：给一个请求，回 `True`（同意）/ `False`（拒绝）/ 字符串（理由）。
Approval = Callable[[dict[str, Any]], Any]


# ---------------------------------------------------------------------------
# 日志：JSONL，只 append
# ---------------------------------------------------------------------------


class Journal:
    """追加式事件日志。**没有 update / delete 方法——这是有意的。**"""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else None
        self.events: list[dict[str, Any]] = []
        self._handle = None

    # -- 写 -------------------------------------------------------------

    def append(self, kind: str, **fields: Any) -> dict[str, Any]:
        event = {"seq": len(self.events) + 1, "kind": kind,
                 "at": _now(), **fields}
        self.events.append(event)
        if self.path is not None:
            if self._handle is None:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self._handle = self.path.open("a", encoding="utf-8")
            self._handle.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
            self._handle.flush()
        return event

    def close(self) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None

    def __len__(self) -> int:
        return len(self.events)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def read_journal(path: str | Path) -> list[dict[str, Any]]:
    """读回日志。坏行跳过并**记下来**，不当成"没有这一行"。"""
    events: list[dict[str, Any]] = []
    broken = 0
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            broken += 1
            continue
        if isinstance(row, dict):
            events.append(row)
    if broken:
        events.append({"seq": -1, "kind": "journal_warning",
                       "broken_lines": broken})
    return events


# ---------------------------------------------------------------------------
# 历史 → messages
# ---------------------------------------------------------------------------


def build_messages(task: str, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把事件流翻成对话历史。**只读事件，不改事件。**"""
    messages: list[dict[str, Any]] = [{"role": "user", "content": task}]
    for event in events:
        kind = str(event.get("kind") or "")
        if kind == "assistant":
            messages.append({
                "role": "assistant",
                "content": str(event.get("text") or ""),
                "calls": [dict(c) for c in (event.get("calls") or [])],
            })
        elif kind in ("result", "denied", "invalid"):
            # **日志里存全量，喂模型时再收体积**：日志是审计事实源，
            # 收过体积的日志会让 `resume()` 拿到一段被截断的候选清单。
            messages.append({
                "role": "tool",
                "tool": str(event.get("tool") or ""),
                "call_id": str(event.get("call_id") or ""),
                "status": str(event.get("status") or ""),
                "content": (_shrink(event.get("payload"))
                            or event.get("reason") or ""),
            })
    return messages


# ---------------------------------------------------------------------------
# 主循环
# ---------------------------------------------------------------------------


def run(client: Client, task: str, *,
        project: str = "",
        mode: str = "auto",
        tools: at.ToolTable | None = None,
        journal: str | Path | None = None,
        approval: Approval | None = None,
        max_steps: int = MAX_STEPS,
        interrupt_after: int = 0) -> dict[str, Any]:
    """跑一轮。``interrupt_after`` 非 0 时在第 N 个事件后抛 `Interrupted`。

    ``client`` 若支持 ``skip(n)`` 且给了 ``journal``（续跑场景），
    会跳过前 n 次回复——见 `resume()`。
    """
    table = tools or at.ToolTable()
    log = Journal(journal)
    log.append("task", text=str(task), project=project, mode=mode,
               tools=table.names())
    try:
        _drive(client, table, log, mode=mode, approval=approval,
               max_steps=max_steps, interrupt_after=interrupt_after,
               project=project)
    finally:
        log.close()
    return _report(log, table, mode)


def _drive(client: Client, table: at.ToolTable, log: Journal, *,
           mode: str, approval: Approval | None, max_steps: int,
           interrupt_after: int, project: str) -> None:
    task = str(log.events[0].get("text") or "")
    for _step in range(max_steps):
        messages = build_messages(task, log.events[1:])
        response = Response.from_any(client.complete(messages, table.schemas()))
        call_rows = [{"id": str(c.get("id") or f"call_{len(log.events)}"),
                      "name": str(c.get("name") or ""),
                      "args": dict(c.get("arguments") or c.get("args") or {})}
                     for c in response.calls]
        log.append("assistant", text=response.text, calls=call_rows)
        _maybe_interrupt(log, interrupt_after)
        if not call_rows:
            return

        for call in call_rows:
            name, args = call["name"], call["args"]
            spec = table.get(name)
            if spec is None:
                log.append("invalid", tool=name, call_id=call["id"],
                           reason=f"工具 {name!r} 不在白名单里")
                _maybe_interrupt(log, interrupt_after)
                continue
            problems = spec.validate(args)
            if problems:
                # **入参不合法就不动手**：不跑工具，把问题原样回给模型
                log.append("invalid", tool=name, call_id=call["id"],
                           reason="；".join(problems), args=args)
                _maybe_interrupt(log, interrupt_after)
                continue

            decision = at.decide(spec, mode)
            log.append("decision", tool=name, call_id=call["id"],
                       tier=spec.tier, decision=decision)
            if decision == "deny":
                log.append("denied", tool=name, call_id=call["id"],
                           reason=f"{spec.tier} 档在 {mode} 模式下不放行")
                _maybe_interrupt(log, interrupt_after)
                continue
            if decision == "ask":
                allowed, reason = _ask(approval, spec, args, project)
                if not allowed:
                    log.append("denied", tool=name, call_id=call["id"],
                               reason=reason)
                    _maybe_interrupt(log, interrupt_after)
                    continue
                log.append("approved", tool=name, call_id=call["id"],
                           by="人确认")

            result = table.call(name, args)
            log.append("result", tool=name, call_id=call["id"],
                       status=str(result.get("status") or "?"),
                       payload=result)
            _maybe_interrupt(log, interrupt_after)


def _ask(approval: Approval | None, spec: at.ToolSpec, args: dict[str, Any],
         project: str) -> tuple[bool, str]:
    if approval is None:
        # **默认拒绝**：没有回调就没人同意。"在 CI 里跑过" 不等于 "人确认过了"。
        return False, (f"{spec.name}（{spec.tier} 档）需要人确认，"
                       "但没有提供审批回调 → 拒绝")
    request = {"tool": spec.name, "tier": spec.tier, "project": project,
               "args": args}
    try:
        answer = approval(request)
    except Exception as exc:                          # noqa: BLE001
        return False, f"审批回调抛异常：{exc}"
    if answer is True:
        return True, ""
    if isinstance(answer, str) and answer:
        return False, answer
    return False, "人未同意"


def _maybe_interrupt(log: Journal, after: int) -> None:
    if after and len(log) >= after:
        log.close()
        raise Interrupted(len(log))


#: 单个值进上下文的上限。**不要设太小**：把 agent 下一步要用的
#: 候选设计截掉了，循环就断了（实测踩过：截断后 `[0]` 直接 KeyError）。
_SHRINK_LIMIT = 20_000


def _shrink(result: Any, limit: int = _SHRINK_LIMIT) -> Any:
    """喂给模型前收体积。**只影响对话上下文，不影响日志。**"""
    if not isinstance(result, dict):
        return result
    out: dict[str, Any] = {}
    for key, value in result.items():
        text = json.dumps(value, ensure_ascii=False, default=str)
        out[key] = value if len(text) <= limit else {
            "truncated": True, "chars": len(text), "head": text[:limit] + "…"}
    return out


def _report(log: Journal, table: at.ToolTable, mode: str) -> dict[str, Any]:
    kinds: dict[str, int] = {}
    for event in log.events:
        kind = str(event.get("kind") or "?")
        kinds[kind] = kinds.get(kind, 0) + 1
    results = [e for e in log.events if e.get("kind") == "result"]
    failed = [e for e in results if str(e.get("status")) not in ("ok", "not_ready")]
    ladders = [e for e in results if e.get("tool") == "hw_ladder"]
    final_text = ""
    for event in reversed(log.events):
        if event.get("kind") == "assistant" and not event.get("calls"):
            final_text = str(event.get("text") or "")
            break
    denied = [e for e in log.events if e.get("kind") in ("denied", "invalid")]
    status = "ok" if not failed and not denied else "failed"
    return {
        "status": status,
        "mode": mode,
        "tools": len(table.specs),
        "events": len(log),
        "by_kind": kinds,
        "tool_calls": len(results),
        "failed_calls": [f"{e.get('tool')}:{e.get('status')}" for e in failed],
        "denied": [f"{e.get('tool')}:{e.get('reason')}" for e in log.events
                   if e.get("kind") == "denied"],
        "invalid": [f"{e.get('tool')}:{e.get('reason')}" for e in log.events
                    if e.get("kind") == "invalid"],
        "final_text": final_text[:400],
        "final_ladder": (str((ladders[-1].get("payload") or {}).get("status"))
                         if ladders else ""),
        "stopped_for": ("final" if final_text else
                        "max_steps" if len(results) else "no_action"),
        "journal": str(log.path) if log.path else "",
    }


# ---------------------------------------------------------------------------
# 续跑
# ---------------------------------------------------------------------------


def resume(journal_path: str | Path, client: Client, *,
           tools: at.ToolTable | None = None,
           mode: str = "",
           approval: Approval | None = None,
           max_steps: int = MAX_STEPS,
           out: str | Path | None = None) -> dict[str, Any]:
    """从日志接着跑。**已做过的事不重跑**——否则重放结果不可能一致。

    做法：把旧事件原样灌进新日志（`replayed` 条），然后让 client 从第
    `assistant 事件数` 条回复继续。支持 ``skip(n)`` 的客户端（`ScriptedClient`）
    会跳过前 n 条脚本；不支持的（真模型）会把完整历史重发一遍——那也是对的做法。
    """
    events = read_journal(journal_path)
    if not events:
        return {"status": "not_ready", "reason": f"日志是空的：{journal_path}"}
    head = events[0]
    task = str(head.get("text") or "")
    effective_mode = mode or str(head.get("mode") or "auto")
    project = str(head.get("project") or "")

    table = tools or at.ToolTable()
    log = Journal(out)
    for event in events:
        if event.get("kind") == "journal_warning":
            continue
        log.events.append(event)
        if log.path is not None:
            if log._handle is None:                   # noqa: SLF001 - 自己类的私有字段
                log.path.parent.mkdir(parents=True, exist_ok=True)
                log._handle = log.path.open("a", encoding="utf-8")   # noqa: SLF001
            log._handle.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")  # noqa: SLF001
    if log.path is not None and log._handle is not None:    # noqa: SLF001
        log._handle.flush()                                  # noqa: SLF001

    already = sum(1 for e in events if e.get("kind") == "assistant")
    skip = getattr(client, "skip", None)
    if callable(skip):
        skip(already)
    try:
        _drive(client, table, log, mode=effective_mode, approval=approval,
               max_steps=max_steps, interrupt_after=0, project=project)
    finally:
        log.close()
    report = _report(log, table, effective_mode)
    report["resumed_from"] = str(journal_path)
    report["replayed_events"] = len(events)
    report["skipped_responses"] = already
    return report


def compare(events_a: list[dict[str, Any]],
            events_b: list[dict[str, Any]]) -> dict[str, Any]:
    """两次运行的**结果序列**是否一致。重放一致性就是这一条。

    只比 ``(kind, tool, status)``：payload 里可能有时间戳/耗时，
    比它会把"一致"变成随机失败。**但工具与状态必须一致**——
    那才是"重放出了不同的结论"。
    """
    def key(event: dict[str, Any]) -> tuple[str, str, str]:
        return (str(event.get("kind") or ""),
                str(event.get("tool") or ""),
                str(event.get("status") or event.get("decision") or ""))

    ka = [key(e) for e in events_a if e.get("kind") in
          ("assistant", "decision", "result", "denied", "invalid")]
    kb = [key(e) for e in events_b if e.get("kind") in
          ("assistant", "decision", "result", "denied", "invalid")]
    first_diff = -1
    for index, (left, right) in enumerate(zip(ka, kb)):
        if left != right:
            first_diff = index
            break
    same = ka == kb
    return {
        "status": "ok" if same else "failed",
        "same": same,
        "a_events": len(ka),
        "b_events": len(kb),
        "first_diff": first_diff,
        "detail": ("" if same else
                   f"第 {first_diff} 个决策点不同："
                   f"{ka[first_diff] if 0 <= first_diff < len(ka) else '(a 短)'}"
                   f" ≠ {kb[first_diff] if 0 <= first_diff < len(kb) else '(b 短)'}"),
    }


def describe() -> dict[str, Any]:
    return {
        "module": "hw_velxio.design.agent_loop",
        "max_steps": MAX_STEPS,
        "journal": "JSONL，只 append；resume 只读它，compare 只比它",
        "approval": "read/judge 放行；write 看模式；human 任何模式都问；无回调=拒绝",
        "why_append_only": "中断接着跑 / 重放一致 / 事后审计，三件事都靠它",
        "why_no_replay_of_tools": "重放时不重跑工具，用记录下来的结果",
    }