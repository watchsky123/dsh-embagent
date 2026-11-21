# 架构方案 v2：以立创EDA为渲染后端、以确定性验证为裁判

> **状态：§0 与 §2–§4 已由 P17 执行（2026-11-21）。** 基于 2026-11-20 本机实测，
> 取代 `adoption-plan.md` / `adoption-detail.md` 中与本文冲突的部分（详见 §7）。
>
> 交付时的事实核对见 `CHANGES.md` P17 节的 F1–F8：其中 **§3.3 的
> `PCB_Document.autoRouting` 在本机被引擎拒绝**（原文假定可用），
> 已改为 `route=native` 如实报 `not_ready`；其余（自动导入、DRC、
> Gerber/BOM、光追渲染、差分对/等长组）实测存在。
>
> **本机的决定性事实**（不是文档推测，是跑出来的）：
>
> ```
> JLCEDA Pro 4.1.97.04b092ac        ← 你装的  D:\IE\ES\PCB\lceda-pro
> open --headless true  → {"ok":true,"sessionId":"2240c01d-...","durationMs":530}
> session list          → status:"alive"
> invoke --code "return 1+1" → {"ok":true,"value":2,"durationMs":2}
> doc api               → 300+ 个类型，含 autoRouting / RayTracer / Spice / Stackup
> ```
>
> **沙箱不拦它**（这与 Renode 完全不同，Renode 被文件锁拦截）。

---

## 0. 一句话结论

**P11 不该写 `emit_kicad.py`（3000 行）。该写 `emit_lceda.py`（约 400 行）。**

立创EDA专业版已经内置了 Traceforge 想搬的一切——自动布线、光追渲染、
Spice 仿真、差分对、等长组、功能分组、Gerber 导出——而且**提供了给 AI 的
CLI 与 MCP 接口**。我们搬 5000 行算法的收益，远小于写一层 400 行的桥。

| 原计划要搬 | 行数 | 实测替代 |
|---|---|---|
| `formats.py`（KiCad S-expr 写入） | 1073 | `SCH_SourcePrimitive*` / `PCB_Primitive*` API |
| `sheets.py` + `schematic_layout.py` + `schematic_route.py` | 2058 | `SCH_SourceDocument` + `ISCH_SourceDocumentFunctionGroup` |
| `route.py` + `routegeom.py` + `escape.py` | 5300 | `PCB_Document.autoRouting` / `fanout` |
| `models3d.py` | 942 | `PCB_RayTracerEngine` + `LIB_3DModel` |
| `impedance.py` + `stackup.py` + `diffpair.py` | ~1200 | `PCB_Stackup` / `IPCB_DifferentialPairItem` / `IPCB_EqualLengthNetGroupItem` |
| `sim/`（ngspice 桥） | ~2000 | `SCH_SimulationEngine` |
| **合计** | **~12600** | **0（调 API）** |

省下的不是时间，是**复杂度**：12600 行移植要长期维护、要跟 KiCad 版本演化赛跑；
400 行桥只要跟着 `doc api` 的帮助输出走。

---

> **⚠ 本节的总架构已被 `architecture-v3-unified.md` 替换为五层**
> （L0 任务 / L1 多域图 / L2 分域仿真 / L3 统一判定 / L4 产物 / L5 闭环）。
> §0 与 §2–§4（立创EDA 发射层）仍然有效。

## 1. 总架构

```
┌──────────────── 需求层 ────────────────┐
│  自然语言 / req.yaml                     │
│    ├─ 确定性路径：generate.py 有限枚举    │  ← 已交付（M2）
│    └─ AI 路径：agent_loop（M5 引入）      │  ← 输出一律落到下一层
└──────────────┬─────────────────────────┘
               ▼
┌────────── 事实源层 design.json ──────────┐
│  parts / nets / links / units / boards   │  ← 唯一真相（M1–M3 已建）
│  + 可选：wires / pours / z_stack         │
└──────┬──────────────────────┬───────────┘
       │ 写                  │ 读
       ▼                      ▲
┌──── 发射层 emit_lceda.py ────┐   ┌──── 审查层（已交付）────┐
│  invoke JS → 建工程/画图/布线 │   │  justify  归因            │
│  与 .eprj3 双向同步          │   │  claims   断言/删除即失败 │
└──────┬──────────────────────┘   │  ladder   V0–V6 阶梯      │
       │                          │  handoff  五类交接单      │
       ▼                          │  grounding 引用核验（M4） │
┌── 立创EDA 专业版（外部进程）──┐    └──────────▲──────────────┘
│  原理图 · PCB · 3D · DRC     │               │
│  Gerber · STEP · Spice 仿真  │───────────────┘
└──────────────────────────────┘   回读：网表/DRC/坐标/BOM
```

**三条不变量**（沿用 AGENTS.md，新架构下仍成立）：

1. **判定只在 Python 一份**。lceda 的 DRC 结果**不进**我们的阶梯——
   它作为"外部证据"记进 `handoff` 的 `assumed`/`external` 类，
   我们的 V0–V6 仍是唯一裁判。
2. **inferred 不进硬检查**。lceda 自动布线的线宽/间距若来自它自己的
   规则，标 `source=lceda:auto`，不进硬结论。
3. **闸门必有出口**。写入 lceda 的每个动作有确认通道（§4.3）。

---

## 2. 八项需求的落点（逐条）

### (1) 生态：立创EDA（已实测可用）

**不接 KiCad。** 生态链全部走 lceda-pro：

| 能力 | 实测入口 |
|---|---|
| 打开工程/会话 | `open --path x.eprj3 [--headless]` → sessionId |
| 跑任意编辑器 API | `invoke --session <sid> --code '<js>'` / `--code-file` |
| 查 API 与格式 | `doc api` / `doc format --class-name <T>` |
| AI 工具直连 | `--mcp stdio\|http --port 3030` |
| 双向同步 | `.eprj3` 文件夹工程：磁盘改→编辑器重载，编辑器存→磁盘落盘 |

**关键设计**：`.eprj3` 是**文件夹 + 日志格式**
（每行 `{type,id,ticket}||{key:value}`），Git 友好、第三方可读写。
这对我们意味着：**design.json 与 .eprj3 可以做双向映射而不失精度**，
而不像 KiCad 那样只能靠 S-expression 全量重写。

**风险与对策**（CLI 在演进）：
* 命令与 API **运行时发现**，不硬编码——`emit_lceda.py` 启动时先跑
  `doc api --class-name` 核对目标签名，对不上就报 `not_ready` 并打印
  实际帮助（学习它自己的纪律："trust the help output of the installed version"）
* 记录探测到的版本号进 `interop.json` 的 `origin` 字段

### (2) 固件：7 驱动 / 3 MCU / 5 targets —— **搬**

这条 Traceforge 确实领先，且**与生态无关**（纯代码生成），照搬：

| 搬什么 | 源 | 改造 |
|---|---|---|
| 寄存器表（7 个传感器） | `firmware/drivers_data.py` (256行) | 保留其"每条注明 datasheet 章节"的写法 |
| 驱动生成器 | `firmware/drivers.py` (372行) | 输出目录改 `project/firmware/` |
| 引脚表（3 MCU） | `firmware/mcus.py` (177行) | **扩到我们的 7 个 MCU**（已有 `.ioc` 型号） |
| 脚手架（5 targets） | `firmware/scaffold.py` (466行) | 保留 targets，接我们的 `pins.h` |
| 工具链探测 | `firmware/toolchain.py` (262行) | 复用现有 `design/toolchain.py` |

**增量**（它没有的）：`pins.h` 从 **`.ioc`** 生成（P8 已解析 77 端点），
而不是从 KiCad 网名；再加**固件引脚交叉核对**（硬件配了软件没用 /
软件用了硬件没配）。这是我们的差异化，保留在 M5-b。

**验收**：对 7 个 `.ioc` 任一生成 `pins.h` + scaffold，`arduino-cli`
或 `arm-none-eabi-gcc` 编译通过；交叉核对能报出错配并引用双方文件行号。

### (3) 可读原理图 —— **调 API，不写排版器**

它 2058 行的 `sheets.py`/`schematic_layout.py`/`schematic_route.py`
做的是"符号嵌入 + 功能分组框 + 层次页 + net label 正交布线"。
实测 lceda 全部支持：

| 它做的 | lceda API |
|---|---|
| 器件放置 | `SCH_SourcePrimitiveComponent`（`AddOptions`/`AddResult`） |
| 导线 | `SCH_SourcePrimitiveWire` |
| 网络标签 | `SCH_SourcePrimitiveAttribute.createNetLabel`（`ISCH_SourcePrimitiveAttributeAddNetLabelOptions`） |
| **功能分组框** | `ISCH_SourceDocumentFunctionGroup` + `transformFunctionGroup` |
| 层次页 | `SCH_SourceSchematic.addPage`（`IDMT_SourceSchematicAddPageOptions`） |
| DRC/ERC | `SCH_Drc` |
| 导出 PNG/SVG | `SCH_ManufactureData` + `ISCH_ExportPngResolution` |

**发射流程**（`emit_lceda.py` 的一个函数）：

```python
def emit_schematic(sid: str, design: DesignModel) -> EmitResult:
    """design.json → lceda 原理图。分四步，每步 invoke 一次。

    ① createProject / addSchematic / addPage
    ② 逐器件 placeComponent（ref+symbol 从 LIB_Device 按 lcsc 搜）
    ③ 逐网 addWire + createNetLabel（网名沿用我们的 net 名）
    ④ transformFunctionGroup（按 units.json 的角色分组）
    """
```

**注意**：`ok:true` 只代表 JS 跑通——**每个 invoke 内部必须自己校验
业务结果并 throw**（lceda 文档明确警告这点）。这与我们"缺失≠通过"一致。

### (4) 3D 渲染 —— **调 `PCB_RayTracerEngine`**

它 942 行的 `models3d.py` 做的是"查 STEP 库 + 分配 + 渲染 PNG"。
实测 lceda 有：

* `PCB_RayTracerEngine` —— **光追渲染**（含材质/光源/背景配置
  `IPCB_RayTracer*Config`）
* `LIB_3DModel` / `ILIB_3DModelSearchItem` —— 3D 模型库查询与分配
* `PCB_ManufactureData` —— 可导出 STEP

**发射**：`invoke` 调 `pcb_RayTracerEngine.render(...)` 出 PNG，
存进工程目录 `render/pcb-3d.png`，前端（DSH GUI）当图片展示。

**这一条同时解掉原方案的缺口**：我们的 3D 一直是"自建体素场景、
不能出渲染图"，现在有真渲染了。**体素场景保留**（用于干涉检查的
数值判定），渲染图变成它的可视化补充——两者不冲突。

### (5) 多层 / 阻抗 / 差分对 —— **传约束，不搬算法**

它 1200 行做叠层建模 + 闭式阻抗 + 蛇形调偏斜。实测 lceda 有：

* `PCB_Stackup` / `IPCB_StackupLayer` —— 叠层（含 JLCPCB 预设）
* `PCB_Layer` / `EPCB_LayerType` / `TPCB_LayersOfInner` —— 层管理
* `IPCB_NetClassItem` —— 网络类（线宽/间距）
* `IPCB_DifferentialPairItem` —— 差分对
* `IPCB_EqualLengthNetGroupItem` / `IPCB_PadPairGroupItem` —— 等长组
* `PCB_Document.fanout` —— 扇出

**我们的角色**：从 `design.json` 的 `meta.net_classes` / `meta.stackup`
把约束**传过去**，然后**回读** lceda 的结果做审查（线宽是否达 net class、
差分对是否等长）。**算法在它那边，判定在我们这边。**

新增 `design/stackup_spec.py`（约 120 行）：约束的 schema + 传给 lceda 的映射。

### (6) 测试规模：从 266 → 目标 600+

学它 `tests/conftest.py:111` 的 **scripted fake model**（假模型，
0 token 回归）。我们已有 8 个 `check_*.py`，扩法：

| 扩什么 | 怎么扩 |
|---|---|
| 每模块单测 | 现在 266 项；按模块覆盖率补到 ~500（emit/grounding/score/pinmap/parts 各 30–50） |
| 端到端 | `fake_model.py` + 需求文本 → generate → emit → 回读 → ladder 全绿 |
| 回归夹具 | 真实 5 工程 + 合成 15 个（避障小车、叠板、多层） |
| **lceda 联调** | **必须有"无 lceda 也全绿"的降级测试**——emit 层检测不到 CLI 时全部报 `not_ready`，其余 500 项不受影响 |

### (7) 融合 agent —— **两条输入路径，同一审查出口**

它：英文 → agent 写 spec → 编译器。
我们：需求 → 确定性枚举 + agent 辅助 → 同一 design.json。

**融合方式**（不是二选一）：

```
需求
 ├─[路径 A] 确定性枚举（快、可复现、无 token）→ 候选 N 个
 └─[路径 B] agent（贵、能理解模糊需求）→ 1 个候选 + 理由
        ↓ 两条路径都产出 interop.json
     interop-import → design.json
        ↓
     阶梯 + 断言 + 交接单（同一裁判）
```

**agent 的工具面**（沿用 adoption-detail §6.2 的白名单）：
只有 `hw_put` 能写 design.json；`invoke` lceda 的 JS **不直接暴露给 agent**，
而是包成 `hw_emit` 工具（内部固定脚本模板，参数是 design.json 字段）——
**防止 agent 写出绕过校验的 JS**。

另外：lceda 自带 `--mcp`，可以**把它的编辑器当工具**接给 agent
（查库、导 Gerber）。这与"我们自己的 hw_* 工具"是两套，并存。

### (8) P11 改道：`emit_lceda.py`

见 §0 的对照表。下面给模块设计。

---

## 3. `emit_lceda.py` 的模块设计

### 3.1 文件

```
design/lceda.py          会话管理 + invoke 封装 + 版本探测（~180 行）
design/emit_lceda.py     四段发射：工程/原理图/PCB/输出（~400 行）
design/stackup_spec.py   叠层与网络类约束（~120 行）
scripts/lceda_probe.py   一次性探测：命令/API 面/版本（~80 行，运维用）
```

### 3.2 会话管理（`lceda.py`）

```python
"""lceda-pro 会话封装。

为什么每次操作都重新探测：CLI 与 API 仍在演进（文档原话），
命令名/签名可能变。硬编码 = 换一版就崩，且崩在业务深处。
"""
@dataclass
class LcedaSession:
    exe: str            # HWV_LCEDA 或自动探测
    session_id: str = ""
    version: str = ""   # 从 --help 头部抓，记进 interop.json

    def open(self, project: str | None = None, *, headless: bool = True) -> dict
    def invoke(self, code: str, *, timeout: int = 60_000) -> dict   # 检查 ok
    def doc(self, kind: str, class_name: str = "") -> dict          # api/format/external
    def close(self) -> dict

def probe() -> dict
    """探测能力：exe 存在? 版本? 关键类存在?
    任一项缺失 → not_ready + 打印实际帮助（不猜）。"""
```

**`invoke` 的硬性纪律**（来自 lceda 文档原文警告）：

> `ok:true` 只代表代码跑通，不代表操作成功——必须在校验业务结果，
> 不满足就 throw。

所以封装里强制：

```python
def invoke(self, code: str, *, expect: str = "") -> dict:
    """expect 是必须在返回值里出现的关键字/结构。
    代码模板强制 include 一段自校验：
        const r = ...; if (!r || !r.ok) throw new Error('...');
        return { ok:true, ... }
    """
```

### 3.3 四段发射（`emit_lceda.py`）

```python
def emit(design, *, boards=None, jobs=("project","schematic","pcb","outputs"),
         session=None) -> EmitReport:
    """按需跑四段，**每段可单独调用**（前端能分开触发）。

    段与段之间只通过 lceda 工程状态耦合——不传内存对象，
    这样任何一段失败都不影响已落盘的部分。
    """
```

**① `emit_project`**：createProject(EPRJ3) / 逐板 addBoard / 设层数与尺寸

**② `emit_schematic`**：装器件 → 连线 → 网络标签 → 功能分组
* 器件按 `part.lcsc` 从 `LIB_Device` 搜；搜不到 → 记 `unresolved` 
  （**不猜符号**，这也是 M5 的 LCSC 数据源的价值）
* 分组按 `units.json` 的 `role`

**③ `emit_pcb`**：放置（用我们 `Placement.x/y/side`）→ 扇出 → 布线
* 布线有三种，按 `--route` 参数选：
  * `native`：调 `PCB_Document.autoRouting`
  * `ours`：跑我们的路由（搬 Traceforge 的 `route.py`）→ 
    `importAutoRouteJsonFile`（**实测存在这个方法**）
  * `none`：只放置，不布线
* 默认 `native`；`ours` 留作对比实验（M6-b）

**④ `emit_outputs`**：Gerber/钻孔/BOM/STEP/3D PNG/SVG
* 全走 `PCB_ManufactureData` / `SCH_ManufactureData`
* 输出到 `project/fab/`

### 3.4 回读（审查的输入）

```python
def read_back(session, design) -> dict:
    """从 lceda 读回：网表、DRC 结果、器件坐标、BOM。
    用于：
      * 校验我们发射的拓扑与 lceda 理解的一致（parity）
      * 把 lceda 的 DRC 当"外部证据"记进 handoff
    """
```

**关键**：回读的网表和我们的 `design.json` 做 **parity 对比**——
不一致就报错。这是"两个系统之间不静默分叉"的保证，等价于
Traceforge 的 `pcb.parity` 检查。

### 3.5 CLI 与工具

```
hw_emit <工程> [--jobs project,schematic,pcb,outputs] [--route native|ours|none]
               [--headless/--visible] [--keep-open]
hw_lceda status|probe|sessions|close
```

工具 `hw_emit` 带 `presentationMeta`（改完前端刷新）；
返回里必须有 `session_id` 与 `project_path`，方便用户自己打开看。

---

## 4. 与既有纪律的接合

### 4.1 判定仍然只有一份

lceda 的 DRC/ERC **不写进** `ladder.json`。它进 `handoff` 的新类别
`external`（第六类）：

| handoff 六类 | 来源 |
|---|---|
| blocking / missing_data / assumed / unverifiable / orphan | 我们自己的判定 |
| **external**（新增） | lceda DRC/ERC 结果、Gerber 可制造性 |

理由：外部工具的判定标准与我们不同（层数规则、安全间距取值），
混进同一张表会让人分不清"谁说的"。

### 4.2 闸门与出口

| 闸门 | 出口 |
|---|---|
| emit 前 `DesignModel.validate()` | 校验失败 → 不发射，报字段级错误 |
| 器件搜不到符号 → `unresolved` | `hw_parts enrich <lcsc>` 补库后重发 |
| `inferred` 链路不发射 connector | 与 renode 一致（先 promote） |
| lceda 版本不匹配 → `not_ready` | 打印实际帮助，人决定是否升级/降级 |
| `--route ours` 的结果需人确认 | 回读 parity 通过才写回 design.json |

### 4.3 每条闸门的验收断言（AGENTS §1.6 要求）

必须专写"闸门生效"的断言，否则 `if ...: continue` 退化后测试天然绿：

```
1. design.json 少字段 → emit 拒绝，错误含字段路径
2. 无 lceda CLI → emit 返回 not_ready 且 emit_report.jobs 全空
3. lcsc 搜不到 → 该器件进 unresolved，其余继续（不整批失败）
4. inferred 链路 → 不生成 connector
5. `--route ours` 结果与 design.json 不一致 → parity 报错、不写回
```

---

## 5. 修订后的批次与工作量

| 批次 | 内容 | 估时 | 依赖 |
|---|---|---|---|
| **M4** | interop + grounding + 中立评分 | 5.5 天 | 无（已在 detail 定义） |
| **M5-a** | LCSC 数据源（符号搜索也靠它） | 3 天 | — |
| **M5-b** | **固件：搬 Traceforge 7驱动/5targets + 扩 MCU + pins.h 交叉核对** | 4–5 天 | M5-a（封装/引脚数据） |
| **M6** | **`emit_lceda` 四段 + 回读 parity + stackup 约束** | 6–7 天 | M5-a（器件搜索） |
| **M7** | agent 循环 + 假模型端到端 | 4 天 | M4/M5 的工具面 |
| **M8** | 测试扩充 266→600+ | 持续 | 各批同步 |
| **M9（可选）** | `--route ours`：搬 Traceforge router 做对比 | 4 天 | M6 |

**合计 ~23–26 天**（M9 可选 +4）。

**相对原 P11 方案的变化**：
* 原：`emit_kicad.py` 3000 行 + 布线移植 5300 行 = 5–6 天，**且风险高**
* 新：`emit_lceda.py` 400 行 + 布线"调 API" = 6–7 天，**但省 12600 行维护**

**为什么时间没省很多**：因为发射层的难点不在"写字"，在**器件→符号/封装的
映射**（靠 M5-a 的 LCSC 数据）、**parity 校验**（回读对比）、
**API 版本演化**（每次探测）。这三件无论走 KiCad 还是 lceda 都要做。

---

## 6. 与 Traceforge 的最终定位

改完后：

| 维度 | 它 | 我们 |
|---|---|---|
| 生态 | KiCad | **立创EDA（国产、免装 KiCad、你的机器已有）** |
| 输入 | 英文 → agent → spec | 需求 → **确定性枚举 + agent 双路径** → interop |
| 渲染 | 自己的示意图 + KiCad | **lceda 原生渲染（原理图/PCB/3D/光追）** |
| 布线 | 自研 2698 行 | **lceda 原生 + 可选我们的（导入接口）** |
| 多层/阻抗 | 自研 1200 行 | **lceda 叠层 API** |
| 固件 | 7驱动/5targets | **搬 + 扩 + pins.h 交叉核对** |
| 验证 | KiCad ERC/DRC + grounding | 自建 V0–V6 + grounding + **lceda 作外部证据** |
| 多板 | 无 | 8 单元/链路/叠板 |
| 归因 | 无 | 140/141 |
| 确定性 | agent（不确定） | 枚举（两次逐字节一致） |
| 产物 | KiCad 工程 + Gerber | **lceda .eprj3 + Gerber/STEP/3D** |

**核心差异一句话**：
它是一套**自洽的单板生成器**（自己做所有事）；
我们是一套**以验证为核心的编排层**（渲染/布线/制造外包给生态，
判定与可审计性留给自己）。

---

## 7. 对既有文档的修订

| 文档 | 修订 |
|---|---|
| `adoption-detail.md` §5（布线器移植） | **改**：默认走 lceda `autoRouting`；自研 router 降级为 M9 可选（用 `importAutoRouteJsonFile` 导入） |
| `adoption-detail.md` §5.2（输出 Wire/Pad） | **保留**：仍需要（回读 parity 与 2D 渲染要用） |
| `execution-plan.md` P11「发射器 + 布线」 | **改**：`emit_kicad.py` → `emit_lceda.py`；验收从"逐字节同"改为"parity 一致 + Gerber 可出" |
| `execution-plan.md` M4 里程碑 | **改**：不提 KiCad，改"布好线的立创EDA 单板" |
| `adoption-plan.md` §1.5 | **改**：标"已被本文取代" |
| `AGENTS.md` | **加** §3.9：lceda CLI 实测记录（会话/invoke/API 面/沙箱不拦） |

---

## 8. 风险与止损

| 风险 | 信号 | 止损 |
|---|---|---|
| **lceda CLI 版本演化** | `doc api` 少关键类 | 探测失败即 `not_ready` + 打印帮助；不降级为"假装成功" |
| **器件搜不到符号** | unresolved 比例 > 30% | 缩范围到"常用 200 器件"，其余报扩展清单 |
| **parity 对不上** | 回读网表 ≠ design.json | 停止写回；报差异明细（哪条网/哪个 pin） |
| **lceda 需 GUI 登录** | headless 无法导 Gerber | 改为 `--visible` + 人工点导出；文档写明 |
| **MCP 与我们的工具冲突** | agent 调了绕过校验的 JS | 我们只暴露封装后的 `hw_emit`，不暴露裸 invoke |
| **性能** | 单板发射 > 60s | 分段发射 + 缓存符号查询 |

**最后一条最重要**：`emit` 是**单向可重放**的——
从 design.json 能完全重建 lceda 工程，所以 lceda 工程可以当**产物**，
而非**事实源**。用户在 lceda 里的手工改动若要保留，
必须 `read_back` 回 design.json（显式操作），否则下次发射会覆盖。
**这一点必须写进 UI 提示。**