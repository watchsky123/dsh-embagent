# 使用体验计划（前端优先）

> 这份文档把"怎么优化体验"拆成可执行的六步。**P0 已交付**，其余按收益排序。
> 与 `execution-plan-v3.md` 的分工：v3 管"判定能力"，这里管"人怎么用上它"。

---

## 0. 起点：一条硬事实

到 P23 为止，一次完整判定会落下 11 份产物：

```
ladder / claims / checks / budget / deferred / crosscheck
coverify / electrical / justify / units / links / handoff
```

而前端快照（`design_cli.snapshot()`）在 P0 之前只带 6 个：

```
checks / budget / deferred / crosscheck / coverify / electrical / tiers
```

**P15–P23 新增的产物，进快照的是 0 个。** 前端不是"没打磨"，是**停在 M3**。

这条约束决定做事顺序：**先扩契约，再改 UI**。

> 判定只在 Python 一份（AGENTS §1.1）。所以任何"看得懂"的东西都必须
> **先由 Python 算出来放进快照**，UI 只渲染。TS 侧能单独做的只有排序与徽章。

---

## P0 ✅ 结论层（一屏结论）— 已交付

| 项 | 内容 |
|---|---|
| Python | `design/verdict.py`：九层横条 + 六类计数 + 排好序的 next（≤5） |
| 契约 | `snapshot().verdict` + `VerdictPayload` |
| UI | 分区 `verdict`（体检组首位 + 默认分区）+ `VerdictPanel` + 徽章 |
| 顺带 | `hint.view` 终于被消费（14 处 viewHint 从死代码变成活的） |

验收 `check_p0.py` 17 条。

**首屏长这样**：

```
[有失败] 不可打板：2 条阻断，V0 ✓ · V1 ? · V2 ✗ · V3 ? · V4 ? · V5 – · V6 –
         ⚠ 设计改过了，这一屏是上次判定的结论（2 天前）

 V0✓  V1?  V2✗  V3?  V4?  V5–  V6–
 阻断 2 · 缺数据 6 · 默认值 7 · 不会验 2 · 未接线 175 · 外部 0

 下一步（192 条，按紧急程度排）
 1  V2 几何失败          → 修好该层报出的问题      [阻断]
 2  4 项不可逆决策未闭合  → 逐项闭合              [阻断]
 3  板间链路一条都没声明   → 补 links_list        [缺数据]
```

---

## P1 ✅ finding 可操作化（把"看得懂"变成"能动手"）— 已交付

**问题**：每条 finding 只有 `message`。用户知道"V3 unknown"，不知道要补哪个字段。

**改法**：finding 加结构化 `next`：

```json
{"kind": "fill_field", "target": "electrical.current_ma", "ref": "U3",
 "hint": "给出 U3 的峰值电流"}
{"kind": "run_command", "command": "hw_links --apply", "hint": "补板间链路"}
{"kind": "open_doc", "url": "..."}
```

| 层 | 改动 |
|---|---|
| Python | `findings` 的生成处补 `next`（**可选字段**，旧产物仍能读） |
| UI | `FindingRow` 渲染成按钮：`fill_field` → 跳 Inspector 该字段；`run_command` → 复制命令 |

**判据**：点"补 current_ma"能落到 Inspector 的电气字段；点"补网表"复制的是正确命令。

**交付实测**（`check_p1.py` 25 条）：`design/nextstep.py` 把 27 个 `fix.need`
翻成四类动作（`edit_field` / `move_part` / `run_command` / `external`）；
blocking 从 1 条拆成 12 条（一条 finding 一条待办）；真工程上 200/202 条带动作。

**风险**：`next` 是**判定**（"该做什么"），必须 Python 给。UI 不许自己拼命令。

---

## P2 ✅ 按对象聚合（替代"按产物开页签"）— 已交付

**问题**：`claims` / `justify` / `physical_tests` 三份产物的索引都是 `ref`，
却分在三个页签里。

**改法**：点 2D 里一个元件 → Inspector 显示三栏：

```
U3  BME280
├─ 断言    powered ✓ / provides_i2c ? / default_level ✗
├─ 归因    "提供 I2C 从机"（db: 0.8）
└─ 实测    无记录
```

**为什么不是"给 claims 开一个分区"**：那样会变成 15 个页签，等于把 JSON 换个壳。
**按对象组织，不按产物组织。**

**判据**：不用切页签就能回答"这个件为什么在板上、它现在被验到什么程度"。

**交付实测**（`check_p2.py` 24 条）：`design/entity.py` 把五份产物按 `ref`
聚成一份画像；316 个位号一个不漏、`C1`/`C10` 不串；快照只带一行摘要
（计数 + 三态），正文走 `entity` 命令按需取。

---

## P3 ✅ 参数面板（136 项要有旋钮感）— 已交付

**问题**：136 个参数全 `source=public`，`trusted_ratio=0%`。
对纯仿真用户，"参数来源"是**唯一能做的事**。

**改法**：新分区 `params`：

| 参数 | 值 | 来源 | 敏感度 | 影响 |
|---|---|---|---|---|
| `mu_tyre_rod` | 0.0 | public | **高** | V7 |
| `mesh_mu` | 0.0 | public | 低 | — |

**先做表 + 敏感度排序**（不做滑块）：滑块是第二步。
敏感度 = 只扫一个参数、看哪一层结论会翻。**扫得多的排前面**，就是"该去核的 5 个"。

**判据**：前 5 行就是"最值得查 datasheet 的参数"，而不是字典序前 5 行。

**交付实测**（`check_p3.py` 23 条）：132/136 个参数**没有任何代码读**。
所以 P3 真正交付的不是"敏感度排序"，而是**把「未接线」和「不敏感」分开报**——
把"没人读"说成"低敏感"会让 132 个参数看起来像"评估过但不重要"。

---

## P4 ✅ 固件矩阵视图 — 已交付（做成面板，不是画布）

**问题**：`pins.h` 与引脚交叉核对**没有任何界面**。而它最容易一眼看完。

```
        硬件配置(.ioc)     固件使用(src/)    结论
PA15    Output/GPIO_OUT    WritePin         ✓
PB9     Output/LPN         —                闲置
PA11    —                  WritePin         ✗ 幽灵引脚
PA2     USART2_TX          HAL_UART         ✓
```

三种状态各一色（对齐 `pinmap-check` 的输出）。**全静态，渲染成本为零。**

**判据**：幽灵引脚红、闲置黄，与 `pinmap-check` 逐条对得上。

**交付实测**（`check_p4.py` 25 条）：做成**底部面板里的矩阵表**，不是第三块画布——
做画布要先改 `Layout`（split/2d/3d）与把手体系，那是另一档工作量；
表格已经能回答"这个脚配没配、用没用"。真 `.ioc` 上 13 个配置脚 + 1 个幽灵脚，
SWD 脚被正确排除在"闲置"外。

---

## P5 ✅ 需求草稿 + 确认闸门（**不写 provider**）— 已交付

**为什么放最后**：做完 NL 入口后，用户看到的第一屏如果还是九个 tab，
他仍然不知道点哪。**结论页是所有入口的公共出口，先做出口再开入口。**

**改法**：把 `req.yaml` 变成三段表单（units / links / constraints），
模型填、人校，然后"生成候选"。

```
需求 → [表单] → generate --dry-run 回显 → 确认 → 候选卡
```

**判据**：说一句话 → 表单被填 → 点生成 → 出候选卡，**中间的 req 可读可改**。

**交付实测**（`check_p5.py` 29 条）：**没有写 provider**——实测 `ctx.llm` 由
`dsh-base` 挂载、`dsh-our-free-model` 2.8.3 就是适配器、`session.prompt()`
早就在用。所以 P5 做的是 `hw_req_draft`（模型填草稿 + 校验反馈）+
`hw_req_apply`（人的确认），三道闸门：validate → confirm → 才写 req.json。
顺带查清 `Requirement.numbers` **没有任何消费方**，校验把它单列成警告。

**P5b 补「从零开局」**：草稿按「工程在不在」落点——工程还没有时写
`<工作区>/_drafts/<id>.json`，`apply --confirm` 顺手建工程。所以
"一句话 → 工程 + 需求 → 候选"是一条链，不用先手动 `hw_new`。

---

## 四件不该做的事

1. **不要在 TS 里算任何判定**。徽章数"几项空集通过"也不行。
2. **不要把 JSON 原样倒进面板**。现有 `evidence` 面板已有这个倾向。
3. **不要为"看起来高级"加图表**。三态色块比饼图准——饼图会把 `unknown`
   画成一种占比，而它不是数量。
4. **不要给每个 artifact 开一个分区**。见 P2 的理由。

另外两条 DESIGN.md 的硬要求别丢：滚轮=缩放且不带走页面滚动（§4.1）、
紫色是唯一强调色（§1.2 → 三态配色用语义色，不能全靠紫）。

---

## 优先级

| 序 | 内容 | 工作量 | 依赖 |
|---|---|---|---|
| P0 ✅ | 快照扩 3 字段 + 结论分区 + 徽章 + hint.view | 1–2 天 | — |
| P1 ✅ | finding 加 `next` + 按钮化 | 2–3 天 | P0 |
| P2 ✅ | 按对象聚合（元件 → 断言/归因/实测） | 2–3 天 | P0 |
| P3 ✅ | `params` 分区 + 敏感度排序 | 2–3 天 | Python 先有 `sensitivity()` |
| P4 | 固件矩阵视图 | 2–3 天 | `pinmap.json` 进快照 |
| P5 ✅ | 需求草稿 + 确认闸门 | 3–5 天 | — （provider 已有） |

P0 的收益/成本比最高：**1–2 天**换掉"敲 7 条命令 + 翻 8 个 JSON"。