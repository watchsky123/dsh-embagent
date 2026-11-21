# P5 方案（修正版）—— 需求草稿 + 确认闸门

> **这一版推翻了初版的两个前提**。初版写的是「加一层 provider 适配 + `units/links`
> 表单」，两处都错。下面是实测后的事实。

---

## §0 推翻的两条

### ① 不需要 provider 适配器

初版前提："0 个 provider，`agent_loop` 只有假模型。"

**错在层次。** DSH 侧早就齐了：

| 东西 | 在哪 | 状态 |
|---|---|---|
| `ctx.llm`（唯一受支持的 provider 入口） | `@deepseek-ai/dsh-llm` | 由 `@deepseek-ai/dsh-base` 挂载 |
| provider 适配器 | **`dsh-our-free-model` 2.8.3**（有 `adapter/dsh-llm.d.ts`） | 你 profile 里已经挂着 |
| 会话投递 | `sessions.binding(id).session.prompt(parts, 'queue')` | **hw_velxio 已在用**（`view.ts:116`） |

`agent_loop.py`（P22）是**内层循环**，用于 0 token 回归；它和 DSH 会话是两条并行的
会话。把 P5 建在内层上 = 在已经有会话的地方再造一条。

**先例**：hwforge 试过在宿主侧投递（`agent.followup`），三轮花在
`agent vs agentId`、`ctx.get vs ctx.sessions`、`inject` 上，结论是
**「host composes, client delivers」**。hw_velxio 现在正是这个形状。

### ② 需求字段不是 `units / links`

初版照 `generate` 内部猜的字段名。**实测 `Requirement` 是七个字段**：

```python
id / title / roles[] / interfaces[] / constraints{} / numbers{} / notes
```

`roles` 的取值必须是 `roles.ROLE_TEMPLATES` 里的 14 个之一。

---

## §1 实测到的第二个坑：`numbers` 没有任何消费方

`Requirement.numbers` 只在 `from_dict` / `to_dict` 里过一遍，
**没有任何模块读它**。`constraints` 也只有 `area_mm2` 被
`capability.candidates` 消费。

含义很直接：**模型填了 `numbers: {detect_range_m: 2.0}`，它不会影响任何结论。**

这和 P3 的「132 个参数没人读，不能报成不敏感」是**同一形状**
（AGENTS §3.16）。所以 `validate()` 必须把它报出来，
否则人会以为那些数字在约束设计。

---

## §2 交付什么

**一句话 → 结构化需求草稿 → 校验 → 人确认 → 才成为 `generate` 的输入。**

```
模型在会话里（已有）  hw_req_draft(action=draft, requirement={...})
                      ↓ 写 req_draft.json（**不是**需求本身）
                      hw_req_draft(action=validate)  ← 结构化反馈，模型可修
                      ↓
人看一眼（面板/CLI）  hw_req_apply(confirm=true)   ← human 档，auto 也问
                      ↓ 才写 req.json/req.yaml
                      hw_generate --requirement ...
```

三道闸门：

| 闸门 | 拦什么 | 出口 |
|---|---|---|
| `validate` | 未知 role / 悬空 interface / 自环 / 空 roles | 改草稿再 validate |
| `apply` 要 `confirm` | 未经人确认的草稿不能变成需求 | 人点确认 |
| `numbers` 无消费方 | 填了却不影响任何结论 | **在报告里说清**，不假装有效 |

---

## §3 文件与改动

| 文件 | 内容 |
|---|---|
| `design/req_draft.py`（新） | `ReqDraft` + `validate()` + `save/load/apply` + `describe()` |
| `design_cli.py` | `req-draft <id> [--set=<json>] [--validate] [--apply --confirm]` |
| `src/tools.ts` | `hw_req_draft`（write 档）+ `hw_req_apply`（**human 档**） |
| `snapshot` | 带 `req` 摘要（草稿在不在、过没过、应用了没） |
| 前端 | 分区 `req`（**delivery 组**，在「固件」之后）+ `ReqPanel` |
| `scripts/check_p5.py` | 验收（含退化） |

---

## §4 三条纪律（照抄已有那套）

1. **草稿 ≠ 需求**。`req_draft.json` 和 `req.json` 是两个文件；
   前者可以被模型反复改，后者只有人确认后才写。
2. **校验必须对着真实契约**。`roles` 比 `roles.ROLE_TEMPLATES`，
   不另写一份角色清单（AGENTS §3.15：再写一份清单 = 迟早分叉）。
3. **没人读的字段要说**。`numbers` 无消费方 → 报告里单列一条，
   措辞是「填了但没有任何消费方」，不是「低影响」。

---

## §5 验收要点（`check_p5.py`）

| 类别 | 断言 |
|---|---|
| 契约 | `ReqDraft` 字段 == `Requirement` 字段（**比对而不是抄**） |
| 校验 | 未知 role → fail；悬空 interface → fail；自环 → fail；空 roles → fail |
| 校验 | 全合法 → `ok`，且能 `generate --fast` 出候选（端到端） |
| 闸门 | 未 validate 就 apply → 拒；未 confirm 就 apply → 拒 |
| 诚实 | `numbers` 非空时报告单列「无消费方」 |
| 前端 | 分区在、面板在、`hw_req_apply` 是 human 档 |
| 退化 | 去掉 `validate` 闸门 → 悬空 interface 那条变红 |

---

## §7 从零开局（P5b 补上）

初版把草稿只写进 `<project>/req_draft.json`，而工程要有 `design.json` 才算存在。
于是"从零一句话开局"走不通——实测崩在 `check_p5` 的 `4f`（`FileNotFoundError`）。

**补法**：草稿按「工程在不在」决定落点。

```
工程已存在 → <project>/req_draft.json
工程还没有 → <工作区>/_drafts/<id>.json
```

`apply --confirm` 时工程不存在 → **顺手建一个空工程**（板卡预设 + 空账本 +
`auto_lock` + 落盘），再写 `req.json`。完整链路：

```
hw_req_draft(action="draft", requirement={...})   # 工程还没有 → 工作区草稿
hw_req_apply(confirm=true)                        # 建工程 + req.json
hw_generate(requirement=<project>/req.json)       # 出候选
```

**补的时候撞到的第二个坑**（`7h` 抓的）：apply 之后必须把草稿**搬进工程**、
删掉工作区那份。不搬就留两份副本，改一份另一份不跟着变——**两个事实源**。

---

## §6 不做

* 不写 provider（已有）
* 不在宿主侧投递（hwforge 的教训）
* 不改 `generate`（它已经能吃 `Requirement`）
* 不做"模型直接出 design.json"——那会跳过 `validate` 与 `generate`