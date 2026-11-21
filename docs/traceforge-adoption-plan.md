> **⚠ 本文件已被替代，保留仅作历史参考。**
> 现行版本：`adoption-plan.md`（决策层）+ `adoption-detail.md`（实现层，
> 含文件/签名/代码骨架/验收与退化验证）。执行时以那两份为准。
>
# Traceforge 借鉴方案 —— 适配 hw_velxio（不执行，待确认）

> 前提：源码已在 `workspace\vendor\traceforge`（MIT 许可，sdist SHA-256 已核对）。
> 本文是**决策文档**：明确"搬什么、怎么改、不搬什么、什么顺序"。
> 未动任何 `hw_velxio` 代码。
> 配套源码分析见文末附录 A（关键文件与行数、已实测验证的声明）。

## 0. 用户已定的三个前提

1. **不排斥**类似的单板布线器（适配到本项目）和 agent 循环（承认它优于我原有限枚举的设计）。
2. **目标是把它的好处都用起来**，给具体方案。
3. **不是纯搬，是适配**——本项目**不用 KiCad**，一切 KiCad 绑定要替换成 design.json / 立创生态。

MIT 许可允许移植代码（保留版权声明与许可文本即可）。
实际策略分三档：**直接移植改适配**（布线器算法）、**抄契约重实现**（grounding、评分）、
**抄数据通路自研**（LCSC 客户端可参考其结构但独立实现）。

## 1. 总架构：两套系统的拼合点

```
                 ┌─────────────────────────────────────────┐
用户需求 ──► agent 循环（新增，R3）                            │
                 │  工具集 = hw_velxio 现有 CLI 全套            │
                 │  产物 = design.json（唯一窄接口，过 validate）│
                 ▼                                           │
  ┌──────────────────────────────────────────────┐           │
  │ R1 元件数据源（LCSC 目录 → capability 动态表）   │◄──────────┤
  │ R2 布线器（移植 route.py 算法 → 输出 wires[]）  │           │
  │ R4 pins.h 生成 + 固件引脚交叉核对                │           │
  │ R5 Z 轴堆叠建模                                 │           │
  └──────────────────────────────────────────────┘           │
                 ▼                                           │
  审查机（已有，M1–M3）：阶梯 V0–V6 / 断言 / 归因 / 交接单       │
                 ▼                                           │
  R6 Renode 闭环（外部执行 + 串口日志回读 → V5 定状态）          │
  └───────────────────────────────────────────────────────────┘
```

**与 Traceforge 的本质差异**：它的 agent 在生成前没有审查机（生成后跑 KiCad 裁决）；
我们的 agent **每一步被断言/阶梯/交接单约束**。审查机是已有资产，agent 是它的调用方。

## 2. 分阶段方案

### R1 元件数据源（解"14 型号表"的通用性瓶颈）—— 3~4 天

**做什么**

* 新增 `hw_velxio/design/parts_catalog.py`：
  LCSC/JLCPCB 目录的搜索 / lookup / 校验客户端。
  结构参考 `vendor/traceforge/src/traceforge/parts/`（net.py 的请求层、
  catalog.py 的解析、verify.py 的封装核对），**独立实现**，不 import 它的代码。
* 缓存到**本项目目录**（`~/.dsh/hw_velxio/parts-cache/`），带离线模式
  （`HWV_PARTS_OFFLINE=1` 只读缓存）——照抄它的缓存/离线思想。
* `capability.py` 升级：`CAPABILITY` 支持动态条目。
  API 字段 → 我们字段的映射：

| LCSC 返回 | 映射到 | source 标注 |
|---|---|---|
| 封装尺寸（长宽高） | `footprint_mm2` / `size_mm` | `datasheet`（厂商数据，可信） |
| 器件类别（MCU/传感器/电源/驱动…） | `fills` | `datasheet` |
| 外设清单（datasheet 摘要 / pinout） | `provides`、`pins` | 有 pinout 数据 → `datasheet`；推测 → `inferred` |
| 手填的待补字段 | 其余 | `inferred`，**不参与硬过滤**（既有纪律） |

* 类别→fills 映射表扩到电机域：电机驱动（DRV8833/TB6612/ESC）、
  超声波（HC-SR04）、IMU（MPU6050）、舵机——避障小车所需品类。

**验收**

* 搜"stm32f103 最小系统"→ 动态出 ≥5 个 MCU 候选，每个字段可追溯到 API 响应。
* 断网时缓存可用，`explain_miss()` 区分"目录查不到"与"约束砍光了"。
* 退化验证：把 `source != datasheet` 闸门去掉 → 必须有断言变红（闸门+出口成对）。

### R4 pins.h 生成 + 固件引脚交叉核对（最小块，先做）—— 2~3 天

**做什么**

* 新增 `hw_velxio/design/pinmap.py`：从 `.ioc` + design.json 的网名生成
  `pins.h`（宏名 = 网名，值 = 引脚），驱动代码引用宏而非裸引脚号。
* `design.json` 新增 `firmware_manifest`（哪个板跑哪个固件、固件期望哪些引脚）——
  这是"固件放在哪块板"的一等公民化（回答之前 Q2）。
* 交叉核对：固件源码里引用的宏/引脚 vs `.ioc` 配置 → 不一致就是
  "硬件改了软件没跟"的清单，进交接单 `blocking` 或新类 `contract_drift`。
* 与 Renode 联动：`.resc` 里哪个机台加载哪个 `.bin` 由 manifest 决定（现在是运行参数）。

**验收**

* `.ioc` → `pins.h` 可编译；改一个引脚 → 交叉核对列出受影响的固件文件与行。
* 驱动寄存器表借鉴 `firmware/drivers_data.py` 的格式（每条注明 datasheet 章节），
  扩展电机驱动与 HC-SR04 时序表。

### R2 布线器适配（解"无 PCB 文件"）—— 5~8 天，最大块

**做什么**

* 移植 `kicad/route.py` 核心算法到 `hw_velxio/design/router.py`。
  它是**纯几何算法**（栅格化 + A* + 协商拥塞 + 修复通道），KiCad 绑定只在输入/输出格式。
* 输入适配：design.json 的 parts placement + nets + board 轮廓。
  **焊盘坐标是新依赖**——从封装数据来（R1 的 LCSC 封装库，或立创封装 JSON）。
* 输出适配：直接写 `wires[]`（`Wire` 模型已有 `path_mm/layer/width_mm`；
  `ratlines.py` 的 MST 飞线升级为实际走线）。**无 KiCad 文件参与**。
* 范围裁剪（避障小车够用即可）：2 层、0.2mm 栅格、无盲埋孔、
  无差分对/长度匹配；保留 ground pour 思想（planes.py 简化版）。
* 未布通的网必须带 `failure_reasons`（它已有的机制）。

**验收**

* 一个 ≥20 元件 2 层设计布通率 ≥90%，产物是 `wires[]`；
  `physical.run_checks` 的 `net_reach` 通过（我们已有的中立检查）。
* 空板/全无网表 → `not_ready`，不是"0 错误"（AGENTS.md §0.1 纪律）。

### R3 agent 循环（承认优于枚举，但纪律不变）—— 4~5 天

**做什么**

* 新增 `hw_velxio/agent.py`：模型循环，工具集 = 现有 CLI
  （`hw_put / hw_synth / hw_generate / hw_ladder / hw_claims / hw_justify /
  hw_handoff / hw_units / hw_links / hw_ioc_links / parts 搜索…`）。
* **窄接口不变**：agent 的唯一持久化产物是 design.json，必须过 `hw_put` 的
  `validate()`。它不能写别的文件（除明确允许的生成物）。
* **grounding 契约**（借鉴 validate/grounding.py）：agent 的每个设计决定
  （选型/连线/布局）必须带 `cites`（工程里存在的 `ref` / 网名 / API 数据字段）。
  机器核对：引用不存在 → 该决定标记 UNGROUNDED 并降级为"待人工确认"，
  不参与自动评分。空候选是合法回答。
* **保留 `generate.py` 枚举**作为 agent 的初稿器（枚举给起点，agent 再迭代）——
  两者是上下游，不是替代关系。
* 测试：**脚本化假模型**（学它的 tests/conftest.py——688 个测试无 key 全绿的路径），
  我们的 agent 测试也不依赖真实 API。

**验收**

* 假模型 scripted 测试全绿；agent 产物全过 `hw_put` validate。
* agent 引用不存在的 ref → 被降级且不进评分（专写"闸门生效"断言）。
* 同一 prompt + 同一工具响应序列 → 输出一致（确定性仅在假模型下保证）。

### R5 Z 轴堆叠建模（解避障小车叠板）—— 2~3 天

**做什么**

* `model.py`：`Placement` 加 `stack_layer` / `z_offset_mm`；
  `Board` 加 `stack_position`；`Enclosure` 联动总高校验。
* 新检查 `check_stack`：叠板间连接器对位（排针坐标 x/y 对齐）、
  层间净高（元件最高点 vs 上一层板底）、总高与外壳内腔。
* 数据来源：排针/铜柱高度是标准件（2.54 排针 8.5mm、M3 铜柱规格），
  进一张小常数表（`source=datasheet`）；板厚来自封装/工艺默认 1.6mm。

**验收**

* 双板叠放设计 → 对位偏移超差报出；总高计算可手算复核。
* 没有堆叠信息的工程 → 该检查 `not_ready`（不是 pass）。

### R6 Renode 闭环（结果回读）—— 2 天

**做什么**

* `run.cmd` 执行后回读串口日志（Renode `showAnalyzer` 可写文件）→
  解析固件启动标记/预期输出 → **V5 从 `not_run` 变为 `pass/fail` + 证据行**。
* 多板：`connector Connect` 建立后，跨板协议日志（如 GPS→主板的 NMEA）可断言。
* 若未来 DSH 放宽沙箱，`probe()` 已能发现（`check_cpu=True`），内部执行路径直接启用。

**验收**

* 用现有 `F103C8T6-GPS.bin` 在 DSH 外跑通一次，日志回填 `ladder.json` V5 带 evidence。
* 没有日志时 V5 仍是 `not_run`（不假装）。

## 3. 顺序与里程碑

重排理由：**R1 是其他一切的地基**（布线器要封装坐标、agent 要真元件、
pins.h 要真引脚）；R4 最小且直接解 Q2/Q3，先做小头来验证管线。

| 阶段 | 内容 | 估时 | 里程碑 |
|---|---|---|---|
| R1 | 元件数据源 → capability 动态表 | 3–4 天 | **M4**（通用性 + 契约） |
| R4 | pins.h + 固件引脚交叉核对 | 2–3 天 | （并入 M4） |
| R2 | 布线器 → wires[] | 5–8 天 | **M5**（几何完整） |
| R5 | Z 轴堆叠 | 2–3 天 | （并入 M5） |
| R3 | agent 循环 + grounding | 4–5 天 | **M6**（agent 闭环） |
| R6 | Renode 结果回读 | 2 天 | （并入 M6） |

合计约 3 周。每个里程碑照旧：硬验收 + 退化验证 + 一条命令复现。

## 4. 明确不搬的

| 不搬 | 原因 |
|---|---|
| KiCad S 表达式生态（formats.py / project.py / kicad-cli 包装） | 本项目不用 KiCad；我们的规范模型是 design.json |
| Altium 读取 | 同上 |
| desktop app / hosted mode / TUI | DSH 已有前端；hosted 的密钥服务器与本场景无关 |
| bench 的 KiCad 依赖部分 | 换成我们的阶梯/断言当中立评分（requirements 权重 40% + 门槛乘法的**思想**保留） |
| 它的 agent 系统提示与评审团 | 我们有自己的纪律文本（AGENTS.md / 各模块 docstring），且评审团要 API 成本 |

## 5. 纪律继承清单（写给未来的自己）

* 每条闸门配出口（R1 的 source 闸门 → 缓存+扩表；R3 的 grounding → 人工确认队列）。
* `inferred` 永不参与硬过滤/硬检查——LCSC 数据标 `datasheet`，
  我们推测的字段标 `inferred`，**两者不混**。
* 每条硬判据做退化验证（改回错的，确认断言红）。
* 验收产物不留在用户工作区；`links_list` 不替用户 promote。
* 字符串值里不用中文引号；TS 侧读 Python 产物先核字段名。
* MIT 移植的文件头部保留原版权与许可声明。

---

## 附录 A：源码实测记录（2026-11-18，本机未装 KiCad）

**规模**：核心约 4.4 万行 Python——kicad 11,436 / design 8,738 / parts 7,038 /
validate 6,442 / tools 4,299 / firmware 2,061；顶层 cli.py 2,466、agent.py 680。
67 个测试文件、约 688 个测试函数，**无 API key 全绿**（脚本化假模型，见 §A.5）。

### A.1 已实测验证的声明

| 声明 | 验证方式 | 结果 |
|---|---|---|
| 确定性 UUID | 直接调 `design.generate._uuid`（uuid5 纯函数） | 同名同值：`5d507628-8702-…` 两次一致 |
| spec 解析 | `load_spec('examples/555_blinker')` | 9 parts，引脚按号/名混合可读 |
| 驱动支持列表 | `firmware.drivers.supported_parts()` | `['BME280','DHT11','LM75','MCP23017','PCF85063A','SHT31','SSD1306']` |
| 完整编译 | `Generator(spec).resolve()` | **失败**：需本机 KiCad 符号库（未装）。端到端未验证 |

### A.2 关键文件索引（后续移植时的地图）

| 文件 | 行数 | 内容 | 对应我们 |
|---|---:|---|---|
| `design/spec.py` | 462 | 电路即代码 schema（pydantic，`extra="forbid"`） | design.json / `hw_put.validate()` |
| `design/generate.py` | 1,789 | spec→原理图+板+工程；贪心放置；`_uuid` 确定性 | `generate.py` 枚举 |
| `kicad/route.py` | 2,698 | 栅格 A* + 协商拥塞 + 修复通道 + 差分对；99 个函数 | **无**（R2 移植目标） |
| `kicad/planes.py` | 1,242 | 灌铜、pad-to-plane 过孔、缝合 | **无**（R2 简化目标） |
| `validate/grounding.py` | ~200 | AI finding 必须带 `cites`，押错标 UNGROUNDED 降 P3 | **无**（R3 借鉴核心） |
| `validate/review.py` | — | 评审团契约："空 findings 是合法回答" | 我们的"不编理由" |
| `parts/`（21 文件） | 7,038 | LCSC 搜索/校验/缓存/离线/成本/替代料 | `capability.py` 14 型号表 |
| `firmware/drivers_data.py` | 256 | 寄存器表，每条注明 datasheet 章节 | **无**（R4 参考格式） |
| `firmware/pinmap.py` | 296 | 从网表生成引脚头文件 | **无**（R4 直接对应） |
| `benchmarks/SCORING.md` | — | requirements 40% + 门槛乘法，中立工具评分 | 候选排序可借鉴 |

### A.3 它的 agent 循环设计要点（`docs/architecture.md` + `agent.py`）

一次用户回合反复调模型直到不再要工具；append-only 历史（可恢复、thinking 块合法）；
只读工具并发、写工具串行（审批一次一个）；`max_tokens` 截断的工具调用回错误结果
而不是执行残缺调用；被打断时未答工具调用补 "interrupted" 结果保持线程有效。
——这些细节是 R3 的工程清单，不只是概念。

### A.4 grounding 的精确语义（R3 必须照抄的部分）

```python
# validate/grounding.py:151
# 每条 AI finding 必须带 refs / nets / cites 之一；
# Grounder 逐条核对引用在设计里是否存在；
# 不存在或没引用 → finding["grounded"]=False，severity 强制降 P3，
# message 前缀 [UNGROUNDED: …]，且永远不能 fail 门槛。
```

配套系统提示写明"只报你领域的真问题；空 findings 列表是合法回答"。
 ours 的等价物：`justify` 的"必须点名具体网名"、`claims` 的"unknown 说清为什么"——
但它们是纪律文本，这是**机判降级机制**。R3 要做的是把纪律变成机制。

### A.5 测试无 key 全绿的方法（R3 必须复用）

`tests/conftest.py`：假模型 = 脚本化的响应列表（按调用顺序弹出），
超次数调用直接断言失败；`fake_openai_server.py`：本地起一个 OpenAI 兼容
假服务器测 provider 路径。我们的 agent 测试照此办理——
同一 prompt + 同一脚本响应 → 输出可断言，且零 API 成本。

### A.6 路由器的诚实边界（原文摘录，R2 验收要对照）

README Limitations：多层高密度板"布线可以中途停止"——4 层 RP2040 留 3 根
未连通、USB hub 留 1 根，示例挂 `.ci-skip` 标记；仅贯通孔；阻抗是闭式公式
不是场求解器；"读 route report（failure_reasons）和 check 再信一块板"。
→ R2 的验收标准必须包含 `failure_reasons` 非空时的如实报告，
不能为了"布通率好看"隐藏失败原因。
