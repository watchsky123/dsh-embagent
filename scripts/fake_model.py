"""假模型 —— 让整条 agent 链路 **0 token 进回归**。

## 为什么假模型是必需品，不是测试小工具

真模型不能进回归：慢、贵、**不确定**。而 agent 循环最容易坏的地方恰恰
不是模型，是**外围**：工具白名单、入参校验、审批、日志、续跑。
这些全部与模型无关，所以用一份"预先写好的回答"就能把它们全部压住。

`ScriptedClient` 的纪律（照 Traceforge `tests/conftest.py:111` 的 `FakeMessages`）：

* 每次调用**记下请求**（`requests`），断言可以检查"它到底看到了什么"
* 脚本用完再被调用 → **立刻报错**（`AssertionError`），不是返回空
  ——空回复会让"循环少跑一轮"伪装成"模型没有工具要调"
* `skip(n)` 支持续跑：从第 n 条脚本继续（对应 `agent_loop.resume`）

## 用法

```python
client = ScriptedClient([
    call("hw_generate", requirement="examples/req_car.yaml", max_candidates=2),
    call("hw_put", project="bot", design=lambda r: r[0]),   # 参数可以依赖上一次结果
    call("hw_ladder", project="bot"),
    done("做完了"),
])
```

`call()` 的 `**args` 里可以放可调用对象：它收到 `(上一条工具结果)`，返回实际值。
这样"把 generate 的候选喂给 put"不需要在测试里写中间人。
"""

from __future__ import annotations

import copy
import json
from typing import Any, Callable

__all__ = ["ScriptedClient", "call", "done", "text"]


def _resolve(value: Any, previous: Any) -> Any:
    if callable(value):
        return value(previous)
    if isinstance(value, dict):
        return {k: _resolve(v, previous) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_resolve(v, previous) for v in value]
    return value


def call(tool: str, *, id: str = "", **args: Any) -> Callable[[Any], dict[str, Any]]:
    """一个工具调用步骤。``args`` 里支持可调用对象（见模块文档）。

    第一个参数叫 ``tool`` 而不是 ``name``：工具参数里本来就有 ``name``
    （`hw_promote` 的决策名），叫成 ``name`` 会撞上关键字冲突。
    """
    def step(previous: Any) -> dict[str, Any]:
        return {"text": "", "calls": [{
            "id": id or f"call_{tool}",
            "name": tool,
            "arguments": {k: _resolve(v, previous) for k, v in args.items()},
        }]}
    return step


def text(message: str) -> dict[str, Any]:
    """只有话、没有工具调用（循环会因为它没有 calls 而结束）。"""
    return {"text": message, "calls": []}


def done(message: str = "完成") -> dict[str, Any]:
    return text(message)


class ScriptedClient:
    """顺序回答的假模型。**不联网、不花 token、行为完全确定。**"""

    def __init__(self, script: list[Any]):
        self.script = list(script)
        self.index = 0
        self.requests: list[dict[str, Any]] = []

    # -- 续跑 -----------------------------------------------------------

    def skip(self, n: int) -> None:
        """跳过前 n 条脚本（`resume` 用它对齐已经做过的那几步）。"""
        self.index = max(0, int(n))

    @property
    def remaining(self) -> int:
        return max(0, len(self.script) - self.index)

    # -- Client 协议 ----------------------------------------------------

    def complete(self, messages: list[dict[str, Any]],
                 tools: list[dict[str, Any]]) -> dict[str, Any]:
        self.requests.append({
            "messages": copy.deepcopy(messages),
            "tools": [t.get("name") for t in tools],
            "at": self.index,
        })
        if self.index >= len(self.script):
            raise AssertionError(
                f"假模型被调用了 {self.index + 1} 次，但脚本只有 "
                f"{len(self.script)} 步（说明循环多绕了一圈）")
        step = self.script[self.index]
        self.index += 1
        previous = _last_tool_payload(messages)
        raw = step(previous) if callable(step) else step
        if isinstance(raw, str):
            return {"text": raw, "calls": []}
        return raw

    # -- 自省 -----------------------------------------------------------

    def describe(self) -> dict[str, Any]:
        return {"script": len(self.script), "used": self.index,
                "remaining": self.remaining,
                "requests": len(self.requests),
                "tools_seen": sorted({t for r in self.requests for t in r["tools"]})}


def _last_tool_payload(messages: list[dict[str, Any]]) -> Any:
    """最近一条工具结果。`call()` 里的可调用对象靠它接上一步。"""
    for message in reversed(messages):
        if message.get("role") == "tool":
            content = message.get("content")
            if isinstance(content, (dict, list)):
                return content
            if isinstance(content, str):
                try:
                    return json.loads(content)
                except ValueError:
                    return content
            return content
    return None