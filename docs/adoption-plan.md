# 借鉴方案：把 Traceforge 的能力适配进 dsh_embagent

> **状态：方案，未执行。** 基于 `workspace\vendor\traceforge`（v1.0.1
> sdist，SHA-256 已核对）的源码精读。每条都标了"搬什么、不搬什么、
> 适配点在哪、验收是什么"。
>
> 与 `execution-plan.md` 的关系：那是已交付的 M1–M3；本文是 M4 起的
> **增量计划**。冲突时以本文为准（它更新）。
>
> **实现层展开见 `adoption-detail.md`**（文件/数据结构/函数签名/
> 代码骨架/验收命令/退化验证，按它可直接开工）。
>
> **走 P11（emit_kicad）后的架构调整见 `adoption-architecture.md`**
> ——它重新划了分层（判定/生成/库/导出），并覆盖本文与 detail 的
> 部分安排（尤其 P11 与"不用 KiCad"的冲突解法）。
>
> **⚠ 立创EDA 路线见 `architecture-v2-lceda.md`**——它是本机实测后
> 的架构决策，取代本文 §1.5（布线器）与 execution-plan 的 P11
> （emit_kicad）。本文其余部分（interop/grounding/LSCS/pinmap）仍有效。
>
> **编号对照**（两套文档并用）：本文 M4–M6 ≈ `execution-plan.md`
> 状态表的 P9–P14：
>
> | 本文批次 | execution-plan | 内容 |
> |---|---|---|
> | M4（interop/grounding/评分） | P9 前置 + P12 的 grounding 部分 | 审查层强化 |
> | M5（LCSC + pins.h 交叉核对） | P9 + P10 | 通用性与软硬契约 |
> | M6（Z 轴堆叠 + 布线器） | P13 + P11 | 几何与走线 |
> | agent 循环 | P12 | 横切 |
> | （Renode 外部闭环） | P14 | 已在 M3 交付产物，剩 serial 回流 |
>
> execution-plan 的 P9–P14 表若与本文冲突（估时/文件名），以
> `adoption-detail.md` 为准——它写于更晚、对过双方源码。

---

## 0. 三条总原则（先读）

1. **只搬"能力"，不搬"宿主"**。Traceforge 的价值在确定性算法
   （布线器/校验器/目录查询）和纪律（grounding/中立评分），
   不在 KiCad 文件格式和 CLI 外壳。我们没有 KiCad，
   所以凡是触碰 `.kicad_*` 文件的部分都要重定向到 design.json。

2. **闸门必须有出口，出口必须有确认**（AGENTS.md §1.6 的推广）。
   本方案新增的三条闸门（AI finding 引用核验、LCSC 数据信任级、
   agent 工具白名单）各自配了出口与验收断言。

3. **生成与审查分离，审查可独立售卖**。Traceforge 自己承认
   "AI 不能替代 review"——我们的定位就是那个 review 层。
   因此**不把它的生成器缝死进我们**，而是定义一份
   **中间交换格式**，让任何生成方（Traceforge / Claude+KiCad /
   未来的别的）的产物都能进我们的审查管线。

---

## 1. 十项能力逐条方案

### 1.1 元件数据源：接 LCSC/JLCPCB 免 key 端点（最高优先级）

**它怎么做的**（`parts/providers/jlc.py`，~300 行）：
三个公开 JSON 端点，**不需要账号**：

```
JLC 详情   https://cart.jlcpcb.com/shoppingCart/smtGood/getComponentDetail?componentCode={code}
JLC 搜索   https://jlcpcb.com/api/overseas-pcb-order/v1/shoppingCart/smtGood/selectSmtComponentList
LCSC 详情  https://wmsc.lcsc.com/ftps/wm/product/detail?productCode={code}
```

模块明说："none of them is a contractual API, so every parser here is
defensive about missing fields"——**每个解析器都防御字段缺失**。
数据模型 `PartInfo` 含：mpn/封装/引脚数/库存/价格阶梯/库类型
（basic 免贴片费）/datasheet 链接/lifecycle/替代料。

**适配（关键差异）**：我们是**能力表驱动选型**（`capability.py`），
不是目录搜索驱动。所以不是"把 LCSC 爬下来"，而是：

```
capability.CAPABILITY（14 型号，人填）
        ↑ 回填（自动，带来源标注）
LCSC 端点（搜参数→拿封装/引脚数/datasheet URL）
        ↓
design/parts_cache/（本工程缓存，不上传）
```

新增 `design/parts_catalog.py`：

* `enrich(ref_or_mpn) -> PartEnriched`：查 LCSC，
  填 `Part.lcsc`（字段我们**已有**，`model.py:267`）、
  `size_mm`（从封装尺寸）、`pins`（引脚数）、`datasheet_url`
* **信任级沿用我们的纪律**：LCSC 给的 `physical_source="catalog"`，
  高于 `estimated`、低于 `datasheet`；`source` 字段记
  `lcsc:<code>:<date>`
* 搜到的型号**自动进 CAPABILITY 候选池**（`fills` 从 category 推），
  `source="catalog"`——按 AGENTS §1.2，**catalog 不进硬选型**，
  只出现在 `explain_miss` 的"表外可选"提示里；人确认后才升
  `datasheet` 级。**出口**：`promote_part(mpn)`。

**验收**：
1. `hw_parts search "stm32f103 lqfp48"` 返回 ≥5 条带封装的结果
2. `hw_parts enrich C89852` 后 `Part.size_mm` 非 0 且 `physical_source=catalog`
3. **闸门断言**：catalog 级型号出现在 `generate.candidates` 的
   `explain_miss.rejected` 里（原因=`source=catalog 未确认`），
   而不出现在硬候选里
4. 断网/超时 → 返回缓存或 `not_ready`，**不抛异常**（学它的防御式解析）

**工作量**：2–3 天。**解什么**：Q1 的通用性——从 14 型号到全目录。

---

### 1.2 交换格式：让任何生成方的产物进我们的审查管线

**为什么**：我们的审查机（归因/断言/阶梯/交接）是独立价值，
但它现在只能审查"自己生成的或导入的 EasyEDA 工程"。
定义一份**导入 schema**，Traceforge 的 `design.yaml`、
Claude+KiCad 的 `.kicad_pcb`（未来）、手工写的设计（现在）就都能审。

**它已经把路铺好了**：Traceforge 的 `parts/verify.py` 证明
目录数据可以机验设计；它的 `sexpr.py`（无损 S 表达式解析器）
和 `kicad/pcb.py` 的 connectivity 计算是现成参考实现——
**未来要审 KiCad 文件时，读法直接抄它的**。

新增 `design/interop.py`：

```
design.json ⇄ interop.json
interop.json 字段：
  boards[]: {id, size_mm}
  parts[]: {ref, mpn?, lcsc?, size_mm, physical_source, board_id, pins{号:网名}}
  nets[]:  {name, pins[{ref,pin}]}
  links[]: {a:{unit,peripheral}, b:{...}, protocol, source}
  firmware[]: {board, file, entry_expect?}        ← 解 Q2 的固件↔板
```

**适配点**：不引入新文件格式——`interop.json` 就是我们
`design.json` 的宽松子集（pydantic 校验），`put` 走现有
`DesignModel.validate()`。这样**审查管线一行不改**。

**验收**：手写一个"避障小车"interop.json（MCU+电机驱动+超声波×3+电源），
`hw_import` 进来后：归因给出每个元件的功能断言、
`hw_assert --remove TRIG` 变红、交接单报出缺电气申报。
**这一条做完，主问题的答案从"骨架级"变成"审查级完整"**。

**工作量**：2 天。

---

### 1.3 grounding：AI 结论必须押可机验的引用

**它怎么做的**（`validate/grounding.py`，~200 行）：
每条 AI finding 必须带 `cites: [ref:U1, net:VBUS, pin:U1.3, file:120]`；
机器逐条核对；**引用不存在或没引用 → 标 UNGROUNDED、降 P3、
永远不能 fail 门槛**；系统提示明写"空 findings 是合法回答"。

**适配**：三个 AI 触点全部接入：

| 触点 | 现在 | 加 grounding 后 |
|---|---|---|
| `justify` 的 why | 规则生成（已安全） | 不变 |
| 未来"AI 生成设计" | 不存在 | spec 级引用核验 |
| **交接单的自然语言 action** | 模板拼的 | 模板可继续，但引入 AI 改写时必须带 `cites` |

**最实际的落点**：给 `claims.json` 的每条断言加
`cites: ["net:3.3V", "ref:C66", "pin:C66.1"]` 字段——
让"删除即失败"变成**可复核的声明**：断言说它依赖 `net.3.3V`，
机器验证这条网真的存在且连着 C66。这把 AGENTS §0.1 的
"缺失写成通过"从靠纪律防，变成**结构上不可能**。

**验收**：
1. 每条功能断言的 `cites` 逐条可解析且存在于设计
2. **闸门断言**：手工构造一条 cites 指向不存在网名的断言，
   `claims.check` 必须把它标为 ungrounded 且**不计入 pass**
3. 退化验证：去掉 grounding 步骤，断言 2 变红

**工作量**：2 天。

---

### 1.4 中立评分：候选排序从"四元组"升级为加权门槛制

**它怎么做的**（`benchmarks/SCORING.md`）：
`requirements 40 + drc 15 + routing 15 + parity 10 + parts 10 +
board 5 + mfg 5`，**除 requirements 外全部乘以 requirements 分**
——空板过 DRC 得 0 分。

**适配**：`generate._rank` 现在是
`(hard_failures, soft_failures, unknowns, cost)`，问题：
**需求满足度和硬失败是并列的**——一个"没满足需求但没报错"的候选
会排在一个"满足需求但有一处错"的前面。

改为（数值可调，乘法门槛建议照搬）：

```
score = 100 × Σ(w_k × s_k × gate) / Σ(w_k)
  requirements 40  ← 接口/角色覆盖率（现在隐含在枚举里，要显式化）
  electrical   15  ← V1/V3 结论
  geometric    15  ← V2 结论
  claims       10  ← 功能断言通过率
  parts        10  ← 选型可信度（datasheet=1 / catalog=0.5 / 无=0）
  area          5  ← 面积贴合度
  cost          5  ←（LCSC 接入后才有真值）
gate = requirements（乘法门槛，照搬）
```

**验收**：
1. 构造"需求覆盖 0% 但零失败"的候选 → 排名垫底（门槛生效）
2. 同需求两次输出逐字节一致（确定性不回退）
3. 权重改动 → SCORING_VERSION 递增（学它的纪律）

**工作量**：1.5 天。

---

### 1.5 单板布线器：移植为"生成 2D 投影的几何引擎"（最大的单项）

**它怎么做的**（`kicad/route.py` 2698 行 + `routegeom.py` + `escape.py`
1410 + `planes.py` 1242，合计 ~5300 行）：0.2mm 栅格、每格 owner、
A* 8 向 + 转弯罚分、协商拥塞撕裂重布、修复通道、差分对阻抗解算、
蛇形调偏斜、平面灌铜。

**适配（三处重定向）**：

1. **输出**：不写 `.kicad_pcb`，产出我们的
   `Wire(path, layer)` + `Pad` + 铜区多边形，直接进 design.json
   → 2D 投影层直接能画（`Circuit2D.tsx` 已有缩放/平移）
2. **栅格/间距来源**：它读 fab profile（JLCPCB 能力）；
   我们读 `budget.json` 的 EMC 轨 + `design.json` 的净类
3. **裁决**：布完不跑 KiCad DRC，跑**我们已有的**
   `physical.run_checks()`（重叠/间距/可达性已经是体素判定）——
   **布线正确性的裁决者仍然是自家阶梯**，这不是弱化，
   是把 5300 行的算法嫁接到我们已有的验证地基上

**不搬**：`stackup.py`/`impedance.py`（多层/阻抗对我们当前场景过度，
留待需要时再评估）、桌面壳、TUI。

**分两步交付**（风险控制）：
* **M4-a**：只搬 `route.py` 核心算法到 `design/router.py`，
  跑通"2 层、单电源网+GND 平面"的最小场景，
  在 `r3demo`（4 元件）上出第一张**有真实走线的 2D 图**
* **M4-b**：接 `escape.py`（QFN/QFP 扇出）+ 差分对（避障小车用不到，可缓）

**验收**：
1. `r3demo` 布通率 100%（4 元件应全通）且 `physical.run_checks`
   零 error
2. `001-blink` 布通后 2D 面板能看到走线（截图/渲染证据）
3. 故意造一条不可达网 → `failure_reasons` 给出"哪些铜皮挡路"
4. 布线结果**确定性**（同输入两次逐字节一致——它的格子模型支持这个）

**工作量**：M4-a 4–5 天，M4-b 3 天。**风险**：本方案最大，
先 a 后 b，a 不通就止损。

---

### 1.6 agent 循环：按 Traceforge 的骨架重构我们的"AI 参与面"

**你已拍板**：原设计不如它，接受引入。它的骨架
（`agent.py` 680 行）：

* 追加式历史（thinking block 合法、线程可恢复、中断安全）
* 工具入参**流式校验**（先对 schema 验，再执行）
* 只读工具并发、写工具串行（审批一次一个）
* **审批三级**：ask / auto（文件+简单命令放行，破坏性必问）/
  read-only
* 模型分档路由（主活 balanced、只读子任务 fast、规划 best，
  连续失败自动升级）——**成本控制**
* 测试全用**脚本化假模型**（688 测试 0 token）

**适配（与我们纪律的结合部）**：

* **工具面收窄**：agent 只暴露我们已有的 CLI 原语
  （`hw_generate/hw_ladder/hw_claims/hw_justify/hw_handoff/hw_links/
  hw_ioc_links/hw_parts...`），**没有直接写 design.json 的工具**——
  agent 要改设计只能走 `hw_put`（过 `DesignModel.validate()`）
  或 `promote`（人确认）。**"闸门+出口"在 agent 语境下的形态**
* **grounding 强制**：agent 的任何 finding/说明必须带
  `ref:/net:/pin:` 引用（接 §1.3），否则标 ungrounded 降级
* **假模型测试**：照搬 scripted-responses 模式，
  我们的验收脚本保持 0 token 可跑

**验收**：
1. 假模型驱动的端到端：需求 → agent 调 `hw_generate` →
   选候选 → `hw_put` → `hw_ladder` 全绿，全程 0 真实 token
2. **闸门断言**：agent 尝试直接写 design.json 的路径不存在
   （工具表里没有这种工具）
3. 审批：read-only 模式下任何写操作被拒

**工作量**：3–4 天。**依赖**：§1.1/1.3 先行（工具面才有内容可暴露）。

---

### 1.7 pins.h 生成 + 固件引脚交叉核对（解 Q2/Q3）

这条 Traceforge 没有现成物（它的 pinmap 是 KiCad 网名→C 头文件，
方向与我们一致但**缺交叉核对**），是我们的增量：

```
.ioc (P8 已解析) ──► pins.h 生成器（design/pinmap.py）
                        │  #define PIN_TRIG GPIO_PIN_8 /* PA8: 避障触发 */
                        ▼
固件源码（用户的）──► 固件侧引脚使用抽取（正则 HAL_GPIO.../GPIO_PIN_x）
                        │
                        ▼
              交叉核对 report：
                硬件配了软件没用 / 软件用了硬件没配 ←—— 你说的"软件随硬件改动"断点
```

**为什么能做**：P8 已经解析了 `.ioc`（77 个通信端点、84 个 label），
固件侧引脚使用是正则活（STM32 HAL 的调用形状固定）。

**验收**：
1. 对你任一 `.ioc` + 对应工程源码生成 `pins.h`，编译通过
2. 构造"固件用了 PA8 但 `.ioc` 没配"的夹具 → 交叉核对报出，
   且**报错引用双方**（`.ioc:文件名` + `固件:文件:行`）
3. 改 `.ioc` 里一个引脚 → 重生成 `pins.h` → 固件编译**失败于改动点**

**工作量**：3 天。**解**：Q2（固件↔板映射成为设计数据）、Q3（契约先行）。

---

### 1.8 Z 轴堆叠建模（解避障小车的叠板）

现状：`panel_layout` 是单层拼板，`Enclosure` 有内腔但无层间概念。
避障小车典型形态：主板上叠传感器板（排针对位，Z 间距 = 排针长）。

新增：

* `Placement.z_stack: {above: board_id, standoff_mm: float}`
* `physical.py` 新增两条检查：
  `stack.collision`（上层投影与上层件 vs 下层件，含 standoff 高度内干涉）
  `stack.connector_align`（叠板间连接器引脚对位——排针 2.54 间距
  错半个脚就是物理灾难）
* `units.json`/`handoff` 消费：叠板关系进单元层

**验收**：
1. 合成两板叠放夹具：对位正确 → pass；错 1.27mm → `connector_align` fail
   并给出错位量
2. 上层板元件与下层板元件在 standoff 内碰撞 → `collision` fail 给出
   两元件号与重叠体积
3. `r3demo` 加一块叠放扩展板跑通全管线

**工作量**：3 天。

---

### 1.9 ngspice 桥（模拟仿真，可选最后做）

它的 `sim/`（engine/measure/tune，~2000 行）包的是 ngspice
命令行。我们的 V6 一直 `not_run`。**适配**：V6 的 runner 从
"永久 not_run" 改为"检测到 ngspice 在 PATH 就真跑电源轨的
DC 工作点"，结果进 `ladder.json`；没有就维持 not_run 并说清。

**工作量**：2 天。**前置**：元件要有 SPICE 模型——
这是它也承认的弱项（"generic models... real behaviour differs"），
对优先级判断：**排在 1.1/1.7 之后**。

---

### 1.10 明确不搬的清单

| 项 | 原因 |
|---|---|
| KiCad 文件读写（`formats.py`/`sexpr.py` 的写入侧） | 我们没有 KiCad；未来要审 KiCad 时**读侧**照它的抄 |
| 多层/阻抗/叠层（`stackup`/`impedance`/`diffpair`） | 当前场景（小车、两层）用不到，复杂度不划算 |
| 桌面壳/TUI/hosted 模式 | 我们有自己的 DSH 前端 |
| 模型路由/审批 UI 细节 | 只取三档审批与假模型测试思想 |
| bench 的 KiCad 依赖打分器 | 思想已在 §1.4 吸收，实现重写 |

---

## 2. 依赖图与顺序（结合你上轮认可的路径）

```
M4：审查层强化（先做，短平快，都是 2 天级）
  1.2 交换格式 interop ──┐
  1.3 grounding 契约 ────┼──► 审查管线可独立售卖、AI 可介入
  1.4 中立评分 ──────────┘
M5：通用性（解 Q1，最大增量）
  1.1 LCSC 元件数据源 ──► 能力表从 14 → 目录级
  1.7 pins.h + 交叉核对 ──► 解 Q2/Q3
M6：几何与在环
  1.8 Z 轴堆叠 ──► 解避障小车叠板
  1.5 布线器移植（先 a 后 b）──► 2D 出真走线
  1.9 ngspice 桥（可选）
横切：1.6 agent 循环（依赖 1.1/1.3 的工具面，建议在 M5 后启动）
```

与你认可的顺序对照：元件数据源（M5）→ pins.h（M5）→
Z 轴堆叠（M6）→ Renode 外部闭环（已在 M3 交付产物，§18.4），
本文把"布线器/agent/评分"三件新增插入，理由已给。

## 3. 总工作量与止损点

| 批次 | 内容 | 估时 | 止损条件 |
|---|---|---|---|
| M4 | interop + grounding + 评分 | ~5.5 天 | interop 若过不了 `DesignModel.validate` 的现实设计，重新评估 schema |
| M5 | LCSC + pins.h | ~6 天 | LCSC 端点不可用/被墙 → 只留缓存模式并明说 |
| M6 | 布线 a + 堆叠 (+b, +ngspice) | ~7–12 天 | **M4-a 在 r3demo 上布不通 → 止损，不硬上** |
| 横切 | agent 循环 | ~4 天 | 假模型端到端跑不通 → 回退为"AI 只出 spec，人跑 CLI" |

**全部完成 ≈ 23–27 天**。与 M1–M3 的 44 天估算比，这轮估时更紧，
因为大量"判定"已存在，新工作多为**搬运与适配**。

## 4. 每批次的诚实边界（先说清做不到什么）

* M4 完成后：能审查**任何来源**的设计（含 Traceforge 生成的），
  但**仍不生成 PCB 文件**——布线在 M6。
* M5 完成后：选型从 14 型号到目录级，但 **catalog 级数据不进硬选型**
  （纪律，AGENTS §1.2），要人确认。
* M6-a 完成后：两层板有真走线；**多层/阻抗不做**。
* agent 全程 0 token 可测（假模型），真用要花 token——
  这是成本，不隐瞒。
