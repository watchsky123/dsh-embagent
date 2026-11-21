# 架构方案：融合 Traceforge 全能力 + 走 P11 发射器

> **状态：架构决策文档，未执行。** 回答你 8 点，并结合
> `adoption-plan.md` / `adoption-detail.md` 与两边**实际代码**。
> 本文只做**架构级判断**（分层、边界、依赖、决策点），
> 模块级签名与验收仍在 `adoption-detail.md`，本文覆盖它。

---

## 1. 先回答问 (1)：我们已有什么"生态"

**结论：只有导入侧，没有导出侧，也没有任何 KiCad 生态。**

### 1.1 资产盘点（实测行数）

| 资产 | 行数 | 性质 | 能不能当"生态"用 |
|---|---|---|---|
| `easyeda.py` | 1071 | **解析器**（.epro/.eprj ZIP → 元件/网表/图形路径） | 导入侧，**只能读** |
| `easyeda_import.py` | 591 | 导入编排（→ design.json） | 导入侧 |
| `importers.py` | 547 | 多格式入口（KiCad/Wokwi/Velxio/立创） | 导入侧 |
| `pins.py` / `packages.py` / `library.py` | 240/344/309 | 引脚三档来源、封装库、元件库 | **内部库，非交换格式** |
| `fabrication.py` | 210 | 制造包（BOM/坐标/DXF） | 导出侧，但**不是 Gerber** |
| `scene_import.py` | 316 | 3D 场景导入 | 导入侧 |
| `Assembly3D.tsx` | 538 | **three.js 真 3D 渲染**（WebGLRenderer/OrbitControls/BoxGeometry/padGeometry） | **导出侧（可视化）** |
| `firmware.py` | 605 | **固件静态审查**（未调用函数/超时路径/参数表/BOM 一致/重复代码） | 审查侧 |

**关键判断**：
* 我们没有 `.kicad_*` 的写入能力，也**没有 Gerber**——所以"接完整嵌入式开发生态"
  目前是**单向的**（能读进来，拿不出去）。
* 但我们已经有两个别人没有的东西：**three.js 真 3D**（不是图片，是可交互场景）
  和**固件静态审查**（Traceforge 没有，它只生成不审）。
* `easyeda.py` 能取到 `path`（图形路径，行 709-744）——**这是自建符号库的基础**，
  决定了下面第 2 节的路线选择。

---

## 2. 根本矛盾：P11 需要库，而我们不装 KiCad

### 2.1 矛盾在哪（实测）

`emit_kicad` 要写出合法的 `.kicad_sch`，必须嵌入**符号图形**。
Traceforge 的做法是（`design/generate.py:168` `resolve()`）：

```python
symbol = self.libs.symbol(part.symbol)   # ← 从 KiCad 安装目录读 .kicad_sym
```

本机实测（我跑过）：不装 KiCad → `LibraryError: KiCad's symbol
libraries were not found: install KiCad ...`。
**所以 P11 无法在"完全不装 KiCad"的前提下照搬。**

### 2.2 三条路（必须选一条）

| 路 | 做法 | 代价 | 是否违背"不用 KiCad" |
|---|---|---|---|
| **A** | 装 KiCad 但**只当库**（从不打开 GUI） | 最低：`generate.py` 原样能用 | 形式上违背，实质不违背 |
| **B** | 自建符号库：用 `easyeda.py` 解析出的图形 → 我们的 `SymbolLib` | 高：~800 行 + 图形质量风险 | **不违背** |
| **C** | **双轨**：主工作格式 design.json 不变；`.kicad_*` 只作**可选导出**；符号库优先自建，KiCad 库作 fallback | 中：分层清晰，两种都能活 | 不违背 |

### 2.3 推荐 C，理由三条

1. **主格式不能换。** 所有判定器（ladder/claims/justify/physical/electrical）
   都吃 `design.json`。让 `.kicad_sch` 成为工作格式 = 重写全部判定器。
   这是**不可接受的架构倒退**。
2. **导出是产物，不是状态。** `emit_kicad` 应该是 `export` 家族的第 N 种输出
   （与 `bom`/`scene3d`/`fabrication` 并列），幂等、可重跑、不进设计状态。
3. **符号库可分阶段。** 先支持"库里有就用"（自建一小批常用件），
   缺的型号**如实报 `not_ready` 并列出缺哪些符号**——这符合我们纪律，
   也避免了"为了导出而编造符号图形"。

**C 的边界条款（写进代码注释）**：
```
export_kicad 的输入只有 design.json + SymbolLib。
- SymbolLib 优先查自建库（easyeda 导入）
- 查不到：若检测到 KiCad 安装目录，用它的（fallback）
- 都没有：该元件进 missing_symbols，导出结果标 partial
   —— 绝不生成"看起来像但引脚不对"的符号
```

---

## 3. 融合架构（回答问 7：两条路线怎么合）

### 3.1 一张图

```
                    ┌─────────────── 输入（三条路） ───────────────┐
 英文/中文需求 ──────┤  ① agent 写 spec（Traceforge 路线，新增）      │
 已有工程(EasyEDA) ──┤  ② interop 导入（M4，新增）                   │
 .ioc + 源码 ────────┤  ③ 现有 importers（已有）                     │
                    └───────────────────┬──────────────────────────┘
                                        ▼
                            design.json（唯一事实源，schema 不升）
                                        │
              ┌─────────────────────────┼─────────────────────────┐
              ▼                         ▼                         ▼
      ┌───────────────┐        ┌────────────────┐        ┌────────────────┐
      │ 判定层（已有） │        │ 生成层（新增） │        │ 导出层（新增） │
      │ V0–V6 阶梯     │◄───────│ 枚举/agent 生成│        │ emit_kicad     │
      │ claims 断言    │  裁决  │ router 布线    │        │ gerber/CPL/BOM │
      │ justify 归因   │        │ pinmap 固件契约│        │ schematic SVG  │
      │ physical 几何  │        │ fw scaffold    │        │ STEP / 3D 渲染 │
      │ handoff 交接单 │        └────────────────┘        └────────────────┘
      └───────┬───────┘                 ▲                         ▲
              │                         │                         │
              ▼                         │                         │
       判决书（人看+机读）───────────────┘                         │
       grounding 保证每条结论有引用                                 │
                                  库层（新增）────────────────────┘
                                   SymbolLib（自建+KiCad fallback）
                                   FootprintLib（packages.py 扩展）
                                   parts_catalog（LCSC 免 key）
```

### 3.2 分层的三条硬规则

1. **判定层只吃 design.json，不认导出格式。** 导出失败不影响判定。
2. **生成层只能产 design.json（经 `hw_put` 校验）或库条目。** 不能直写
   `.kicad_*`——写文件是导出层的独占职责。
3. **库层是唯一的"外部数据入口"**（LCSC / KiCad 库 / easyeda 符号），
   且**每条库数据必须带 source**（`lcsc:` / `kicad:` / `easyeda:`），
   按信任级 `estimated<catalog<datasheet<measured` 参与判定。

### 3.3 两条路线的融合点（问 7 的核心）

| 环节 | 确定性路线（我们） | agent 路线（它） | 融合后 |
|---|---|---|---|
| 需求→方案 | 有限枚举 20 候选 | agent 自由写 spec | **枚举给 agent 提供候选池**；agent 可以在池外自由发挥，产出必须过 `hw_put` |
| 选型 | `capability` 表过滤 | 搜 LCSC 自由选 | 共用 `parts_catalog`；agent 选的型号也要过信任级 |
| 裁决 | V0–V6 阶梯 | KiCad ERC/DRC | **阶梯永远在**；有 KiCad 时额外跑 ERC/DRC（可选增益） |
| 结论 | handoff 五类 | P0–P3 + grounding | 统一进 handoff；grounding 对所有 AI 结论生效 |
| 确定性 | 同需求逐字节一致 | 不可复现 | **两条产物都带 source 标记**：`candidate.source=enum|agent`，可比较 |

**关键设计**：agent 不是替代枚举，而是**在枚举之外再加一个候选来源**。
`generate.candidates` 的 `source` 字段区分两者，排名算法（M4 的中立评分）
对两者一视同仁——**这让人能判断 agent 到底比枚举好多少**（可测量，不是信仰）。

---

## 4. 八项逐一落地（问 2–6、8）

### 4.1 问 (2) 固件全 targets（arduino/platformio/esp-idf/stm32/avr）

**它的实现**（`firmware/scaffold.py` 466 行 + `pinmap.py` 296 行）：
核心抽象是 `PinMap`：

```python
class PinMap:
    mcu: Mcu
    ref: str
    assignments: list[PinAssignment]   # net → macro → pin
    unconnected: list[str]
```

`PinMap` 从**网表**推（它从 KiCad 网表；我们从 design.json 的 nets），
然后每个 target 一个模板函数：`_files_arduino_like` / `_main_avr` /
`_main_idf` / `_main_stm32` + `_avr_makefile` + `_manifest`。

**适配**：
```
design.json.nets ──► PinMap（我们的版本，加 board_id 支持多板）
                       │
       ┌───────────────┼───────────────┬──────────────┬──────────────┐
       ▼               ▼               ▼              ▼              ▼
   arduino .ino   platformio.ini   esp-idf CMake   stm32 CubeMX    avr Makefile
   (Uno/Nano)     + src/main.cpp   + main.c        + main.c        + main.c
```

**我们的增量（它没有）**：
* **生成 + 审查闭环**：`firmware.py`（605 行）已有 `scan_uncalled`/
  `scan_timeout_paths`/`scan_bom_consistency` → 生成后立刻自审，
  把"生成的代码有没有未调用函数/超时路径"变成检查项
* **多板**：`PinMap` 带 `board`, 一块板一份固件工程（它只有单板）
* **固件 ↔ `.ioc` 交叉核对**（detail §3）：它没有

### 4.2 问 (3) 可读原理图

**它的实现**（实测行数）：`schematic_layout.py` 932（Label/PinPos/Item
几何 + 碰撞）、`sheets.py` 854（Group/SheetPlan/PowerSymbols + 层次页）、
`schematic_route.py` 207（正交连线）、`schematic_lint.py` 329（布局自检）。

**两种产出，建议都做**：

| 产出 | 渲染方 | 用途 | 工作量 |
|---|---|---|---|
| **A. KiCad 原理图文件** | 我们自己写 `.kicad_sch` | 别人能打开/编辑/打样 | 需 `SymbolLib`（§2） |
| **B. 我们的 SVG/Canvas 原理图** | 前端（已有 2D 缩放基建） | 在 DSH 内直接看，不依赖 KiCad | 后端出几何 → 前端画 |

**B 是 A 的前置**：两者共用同一份"布局几何"（元件框/pin 坐标/正交连线），
只是序列化成不同格式。**先做 B（可视化），再做 A（导出）**——
B 能立刻验证布局算法对不对，而不用先解决符号库。

### 4.3 问 (4) 3D 渲染怎么实现的

**关键事实（实测）**：Traceforge 的 3D 是**两层**：

```
① 模型分配/生成 —— 它自己实现，可搬
   models3d.py:782  generate_model() 写"minimal AP214 STEP"
   """one solid per box (body and pin stubs), in millimetres"""
   + assign_model() 把 STEP 路径写进 footprint 的 (model ...)

② 渲染成 PNG —— 外包，不可搬
   models3d.py:885  render_3d() → kicad-cli pcb render（KiCad 8+）
   源码注释："Rendering is delegated to kicad-cli；nothing is faked"
```

**我们不能搬 ②**，但我们有**更强的替代**：`Assembly3D.tsx`（538 行）
已经在用 three.js 真渲染（WebGLRenderer + OrbitControls + BoxGeometry +
padGeometry）。所以：

```
我们已有的 three.js 场景 ──► 加"元件模型几何"即可出图
       ▲
       │ 几何来源三档（与 pins 的三档来源同构）：
       │  measured   = STEP 文件解析（有就最好）
       │  datasheet  = 封装尺寸表（package_sizes.py，M5 已在计划）
       │  estimated  = 包围盒占位（就是它 generate_model 的做法）
```

**结论**：3D 渲染我们**不需要 kicad-cli**，且能做得比它好（可交互 vs PNG）。
要搬的只是它 `generate_model` 的"最小 STEP 生成"（~70 行），
用于「需要把模型交给外部工具」的场景（如给结构工程师）。

### 4.4 问 (5) 多层/阻抗/差分对

**它的实现**：`stackup.py` 424（叠层模型+JLCPCB 预设+参考面）、
`impedance.py`（微带/带状线/耦合对闭式公式 + 宽/隙解算）、
`diffpair.py`（耦合走廊 + 蛇形调偏斜）。

**这有没有用？取决于目标板**：

| 场景 | 需要吗 | 理由 |
|---|---|---|
| 避障小车（2 层、无高速） | **不需要** | 无阻抗控制需求 |
| 你的四旋翼（MIPI/USB/CAN） | **部分需要** | USB 差分对 + 一般阻抗 |
| 多层高密度（BGA/0.8mm） | 需要 | 但它自己承认"要用 via-in-pad" |

**建议：分层做，按需启用**
* **L1（必做）**：2–4 层 + GND 平面 + 单端阻抗（微带/带状线，~120 行公式）
* **L2（有用再补）**：差分对（USB/HDMI）+ 蛇形调偏斜（~200 行）
* **L3（不做）**：6–8 层、盲埋孔、BGA fan-out——它自己也没完全跑通

**架构上**：叠层模型放 `design/stackup.py`，作为 `router` 的**输入参数**
（而不是硬编码"2 层"）。这样加层数不改路由器。

### 4.5 问 (6) 测试规模（266 → ~688）

**它的做法（最值得学的）**：`tests/conftest.py` 的 **scripted fake model**
+ `fake_openai_server.py`（"A tiny OpenAI-compatible Chat Completions
server for tests (no model, scripted behaviour)"）。
**688 个测试，0 API key、0 token。**

**我们的差距分析**：266 项 vs 688 项，差在**覆盖面**不在质量
（我们有退化验证纪律，它没有明文写这条）。
补齐路径不是"写更多断言"，而是**给每个新模块定测试配额**：

| 模块 | 目标测试数 | 必有类型 |
|---|---|---|
| interop | 25 | schema 严格性 ×10、双向转换 ×8、错误路径 ×7 |
| parts_catalog | 30 | 解析防御性（缺字段/畸形 JSON）×15、缓存 TTL ×5、离线 ×5、闸门 ×5 |
| pinmap + fw scaffold | 45 | 5 targets × 各 5（生成/幂等/缺宏/编译命令/审查）×5 |
| schematic_layout | 40 | 几何不重叠、正交、sheet 拆分阈值、lint |
| router | 35 | 确定性、失败原因、平面、止损 |
| emit_kicad | 30 | 逐字节幂等、符号缺失 partial、非法引用 |
| agent_loop | 30 | scripted 端到端、审批矩阵、grounding |
| 库层 | 20 | 信任级、fallback |

**合计新增 ≈ 255**，加现有 266 ≈ **520**。
（它 688 里包含它特有的 Altium/KiCad 版本矩阵，我们不需要对标那个数。）

### 4.6 问 (8) 走 P11

P11 = `emit_kicad.py` + `router.py` + 回流。按 §2 的 C 方案拆成：

```
P11-a  export 骨架 + SymbolLib（自建优先，KiCad fallback）
       产出：.kicad_sch（原理图）+ .kicad_pcb（板框+封装，未布线）
       验收：KiCad 能打开；ERC 无"未连接符号"；两次导出逐字节同
P11-b  接 router（M6-a 的移植版）
       产出：有走线的 .kicad_pcb
       验收：KiCad DRC 0 error（若装了 KiCad）/ 我们的 physical 0 error（必须）
P11-c  Gerber/CPL/BOM 导出
       验收：Gerber 能被 KiCad 打开（若装）/ 格式自检（层名/单位/原点）
P11-d  回流：把 KiCad 里人改的结果导回 design.json（interop 读侧）
```

**P11-d 是"闭环"的关键**：工程师在 KiCad 里手调后，结果能回到我们的
判定层重新算归因/交接单。**没有 d，P11 就只是"一次性导出"。**

---

## 5. 新增/修改文件一览

### 5.1 新增（按层）

```
库层
  design/symbol_lib.py      ~600   自建符号库（easyeda→符号）+ KiCad fallback
  design/package_sizes.py   ~80    封装尺寸表（catalog→size_mm 的桥）
  design/parts_catalog.py   ~400   LCSC 免 key 客户端 + 缓存 + 信任级
生成层
  design/pinmap.py          ~350   PinMap + pins.h + .ioc 交叉核对
  design/fw_scaffold.py     ~450   5 targets 模板 + manifest
  design/router.py          ~2200  布线器（移植 route.py 核心）
  design/stackup.py         ~150   叠层模型（router 输入）
  design/agent_loop.py      ~400   agent 循环（scripted 可测）
导出层
  design/emit_kicad.py      ~900   .kicad_sch / .kicad_pcb 写入
  design/schematic_layout.py ~700  布局几何（SVG 与 KiCad 共用）
  design/gerber.py          ~400   Gerber/CPL 写出
  design/models3d.py        ~150   STEP 占位生成（搬它的 generate_model）
判定层（新增）
  design/grounding.py       ~150   引用核验
  design/score.py           ~200   中立评分（含 SCORING_VERSION）
  design/interop.py         ~400   双向转换
前端
  src/client/Schematic2D.tsx ~500  原理图 SVG/Canvas 渲染
```

### 5.2 修改

| 文件 | 改动 |
|---|---|
| `design/generate.py` | `_rank` 改用 `score.score_candidate`；`Candidate.source` 区分 enum/agent |
| `design/model.py` | `Placement.z_stack/standoff_mm`；`Part.symbol?`（库键，可选） |
| `design/physical.py` | +`check_stack_collision` / `check_stack_align` |
| `design/store.py` | ARTIFACTS +8（interop/parts_cache/pinmap/score/export_kicad/gerber…） |
| `design_cli.py` | +12 命令 |
| `src/tools.ts` | +8 工具 |
| `scripts/` | +`check_m4.py` / `check_m5.py` / `check_m6.py` / `fake_model.py` |

**总新增 ≈ 8000 行**（它对应部分 ≈ 14000 行，我们复用已有 44k 行基建）。

---

## 6. 分批与依赖（修订 execution-plan 的 M4/M5）

| 批 | 内容 | 天 | 依赖 |
|---|---|---|---|
| **B1** | interop + grounding + score | 4 | — |
| **B2** | symbol_lib + package_sizes + parts_catalog | 6 | — |
| **B3** | pinmap + fw_scaffold（5 targets）+ 生成→审查闭环 | 5 | B2 |
| **B4** | schematic_layout + Schematic2D（先可视化） | 5 | B2 |
| **B5** | emit_kicad 原理图（P11-a） | 5 | B2/B4 |
| **B6** | stackup + router（P11-b） | 7 | B2 |
| **B7** | gerber/CPL（P11-c） | 3 | B5/B6 |
| **B8** | 3D：STEP 生成 + three.js 接模型几何 | 3 | B2 |
| **B9** | Z 轴堆叠 | 3 | — |
| **B10** | agent_loop（scripted） | 4 | B1/B2 |
| **B11** | 测试补齐 520 项 | 随批 | 各批 |
| **B12** | P11-d 回流（interop 读侧） | 3 | B5 |

**合计 ≈ 48 天**（含测试）。分三个里程碑：
* **M4'**（B1–B3，15 天）：审查任意来源 + 固件全 targets
* **M5'**（B4–B7，20 天）：可读原理图 + 布线 + Gerber（**能打样**）
* **M6'**（B8–B12，13 天）：3D/堆叠/agent/回流

---

## 7. 需要你拍板的四个决策点

1. **符号库走 B 还是 A？** 推荐 **B 优先 + A fallback**（§2.3）。
   代价：自建库要支持你实际用到的型号，初期只能覆盖常用件。
2. **P11 的产物要不要"必须能打样"作为里程碑门槛？**
   若要，B7 不能缓；若不要（只要"KiCad 能打开"），B7 可推后。
3. **多层做到 L1 还是 L2？** 推荐 L1（2–4 层 + 单端阻抗），
   L2（差分对）等你有 USB/HDMI 的实际需求再加。
4. **agent 与枚举的候选要不要一起排名？** 推荐**一起**（带 `source` 标记），
   这是唯一能"用数据判断 agent 值不值"的做法。

---

## 8. 一句话

**P11 的本质不是"多写 3000 行 KiCad 格式"，而是"把
design.json 变成可交付物"。** 所以架构上它必须是**导出层**，
与 `emit_kicad` 并列的还有 `gerber`/`schematic`/`step`；
判定层一行不改，符号库作为唯一的新外部依赖，并且**必须可降级**
（缺符号 → partial，不编图形）。