# 借鉴 Traceforge：详细修改方案（M4–M6 + agent）

> **状态：详细实现方案，未执行。** 是 `adoption-plan.md`（十项能力的
> 决策层）的**实现层展开**：每个模块给出文件位置、数据结构、函数签名、
> 代码骨架、验收命令与退化验证。按本文件可以直接开工。
>
> 事实来源：`workspace\vendor\traceforge` v1.0.1 源码精读 +
> `dsh_embagent` 当前代码（行号为 2026-11-18 实测）。
> 与两份旧文档冲突时以本文为准——
> **但架构分层（判定/生成/库/导出四层）以 `adoption-architecture.md` 为准**，
> 本文的 §5（布线不写 KiCad 文件）已被它取代：走 P11，导出层独占文件写入。

---

## 0. 全局约定（每节都要遵守）

### 0.1 依赖政策

Traceforge 作为**只读参考源码**放在 `workspace\vendor\traceforge`，
**不 pip install、不进 dsh_embagent 的依赖树**。搬运方式是
"读它的源码 → 抄算法/结构 → 写进我们的模块"，带 `# ported from
traceforge <file>:<lines>` 注释。理由：它的依赖树（anthropic/httpx/
pydantic v2/typer）与我们不同源，直接依赖会把两套版本管理搅在一起。

### 0.2 三处统一约定（来自 AGENTS.md，逐条落到下面的设计里）

| 约定 | 在本方案里的体现 |
|---|---|
| `inferred` 永不参与硬检查 | LCSC=catalog 级不进硬选型（§2）；agent 无直写工具（§6）；interop 的 links 保持 source 语义（§1） |
| 闸门必有出口 + 出口必有确认 | `promote_part` / `promote` / `hw_put` 三个出口（§2/§1/§6） |
| 判定只在 Python 一份 | 布线器、评分、grounding 全在 Python；TS 只注册工具转发 CLI（§7） |

### 0.3 每个模块的完成定义（DoD）

一个模块算完成，当且仅当：

1. 代码 + `describe()` 更新
2. 单测进 `tests/test_design.py`
3. 验收入口进 `scripts/check_m4.py`（或对应 check 文件）
4. **退化验证做完**（把关键闸门改回错的，确认测试红）
5. `ARTIFACTS` 登记新产物（若产生新文件）
6. AGENTS.md 追加踩坑条目（若踩了新坑）

---

## 1. M4-a：交换格式 `interop`（2 天，最先做）

### 1.1 目标与判据

**判据**：手写一份"避障小车"的 interop 文件（MCU + 电机驱动 + 超声波
+ 电源），导入后走完整审查管线——归因给出功能断言、
`hw_assert --remove TRIG` 变红、交接单报出缺电气申报。
**达成即"审查任何来源的设计"成立。**

### 1.2 文件

| 文件 | 动作 | 内容 |
|---|---|---|
| `design/interop.py` | **新建** | schema + 校验 + 双向转换 |
| `design/store.py` | 改 1 行 | `ARTIFACTS += ("interop.json",)` |
| `design_cli.py` | 加 2 命令 | `interop-import` / `interop-export` |
| `src/tools.ts` | 加 1 工具 | `hw_interop` |

### 1.3 schema（pydantic，宽松子集原则）

```python
# design/interop.py
"""interop —— 任何生成方（Traceforge / Claude+KiCad / 人）与审查管线之间的桥。

设计原则：interop.json 是 design.json 的**宽松子集**——
能表达的都映射到 DesignModel 已有字段，不发明新概念。
导入走 hw_put 同一条 DesignModel.validate()，审查管线一行不改。
"""

from pydantic import BaseModel, ConfigDict, Field

class InteropPin(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ref: str
    pin: str            # 引脚号或名（导入后进 pins/Pad）

class InteropNet(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    pins: list[InteropPin]
    volts: float | None = None        # 电源网可申报电压 → V1 的绝对最大值检查立刻可用

class InteropPart(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ref: str
    kind: str                          # 型号或类别名（stm32f103 / C0603 / servo）
    label: str = ""
    role: str = "component"
    mpn: str = ""
    lcsc: str = ""
    size_mm: dict[str, float] = {"w": 0, "h": 0, "d": 0}
    physical_source: str = "estimated"  # estimated | catalog | datasheet | measured
    board_id: str = ""
    x_mm: float = 0; y_mm: float = 0; side: str = ""
    pins: list[str] = []               # 引脚名（顺序即号）
    electrical: dict[str, float] = {}   # max_v / current_ma / v_in ...

class InteropLink(BaseModel):
    """板间链路。source 语义与 links.py 完全一致：inferred 不进硬检查。"""
    model_config = ConfigDict(extra="forbid")
    id: str
    a: dict[str, str]                  # {unit, peripheral, pin?}
    b: dict[str, str]
    protocol: str                      # uart/i2c/spi/can/usb/power
    source: str = "inferred"           # declared 才参与硬检查
    level_v: float | None = None
    baud: int | None = None

class InteropFirmware(BaseModel):
    """固件↔板映射（解 Q2）。交叉核对在 M5 的 pinmap 里做。"""
    model_config = ConfigDict(extra="forbid")
    board: str
    file: str                          # .bin/.hex 相对路径
    mcu: str = ""                      # 显式型号（renode 用）

class InteropDoc(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_: str = Field("hwv-interop/1", alias="schema")
    origin: str = ""                   # 生成方标识，如 "traceforge-1.0.1"
    boards: list[dict] = []            # {id, w, h}
    parts: list[InteropPart] = []
    nets: list[InteropNet] = []
    links: list[InteropLink] = []
    firmware: list[InteropFirmware] = []
```

### 1.4 转换规则（逐字段）

| interop | → DesignModel | 规则 |
|---|---|---|
| `boards[]` | `boards: [Board(...)]` | id 原样；首块板兼作 `board` |
| `part.kind` | `Part.kind` | 原样（哈希也算合法——归因会如实 unknown） |
| `part.lcsc` | `Part.lcsc` | **字段已存在**（`model.py:267`），直通 |
| `part.electrical` | `Part.electrical` | 直通；有 `max_v` 则 claims 自动生成 `rated_voltage` |
| `net.volts` | `meta.net_volts[name]` | `ladder.net_volts()` 已读这里（`ladder.py:198`），V1 立即可判 |
| `links[]` | `links_list` | `source` 原样保留——inferred 照旧被 validate 跳过（`links.py` 的 `only_declared` 同款纪律） |
| `firmware[]` | `meta.firmware` | renode 的 `--firmware` 直接消费 |
| `part.pins` | `Part.pins` | 直通；无 `pads` 几何则 `pins.audit()` 报"未验证"（三档来源纪律不变） |

**导出方向**（`interop-export`）：把 design.json 的
parts/nets/links/boards 压平成 interop——用途是把**我们导入的
EasyEDA 工程交给 Traceforge 侧工具**（如它未来的布线）或存档。

### 1.5 CLI / 工具

```
python -m dsh_embagent.design_cli interop-import <interop.json> <工程名>
python -m dsh_embagent.design_cli interop-export <工程名> [--out x.json]
```

工具 `hw_interop`：参数 `{file?, project?, direction}`，
render 输出导入后的审查摘要（复用 handoff 渲染器）。

### 1.6 验收（写进 `scripts/check_m4.py`）

```python
# 夹具：避障小车 interop（合成，10 件）
# 断言：
1. interop-import 后 hw_justify：HC-SR04 的 TRIG/ECHO 上拉判出功能断言
2. hw_assert --remove TRIG 上拉 → reddened 非空且含 provides_default_level
3. 交接单报出"0 个元件申报电气参数"（故意不填 electrical）
4. 闸门断言：interop 里 source=inferred 的 link 落地后
   links.validate 的 declared_links == 0（inferred 不进硬检查）
5. schema 校验：多余字段（extra="forbid"）→ 明确报错而不是静默吞
6. 退化验证：把"volts→meta.net_volts"的映射删掉，
   V1 对 12V 网变 unknown（当前它判 fail 的能力消失）→ 断言红
```

---

## 2. M5-a：LCSC/JLC 元件数据源（3 天）

### 2.1 端点（实测自 `parts/providers/jlc.py`，全部免 key）

```
JLC 详情  https://cart.jlcpcb.com/shoppingCart/smtGood/getComponentDetail?componentCode={lcsc}
JLC 搜索  https://jlcpcb.com/api/overseas-pcb-order/v1/shoppingCart/smtGood/selectSmtComponentList
          POST {keyword, currentPage, pageSize} → data.componentList[]
LCSC 详情 https://wmsc.lcsc.com/ftps/wm/product/detail?productCode={code}
```

**依赖**：只用 `httpx`（它的 `parts/net.py:19` 同款）。
**解析纪律（照抄它的原话）**："none of them is a contractual API,
so every parser here is defensive about missing fields"——
所有字段 `.get()` + 默认值，绝不 `[]` 硬取。

### 2.2 文件

| 文件 | 动作 | 内容 |
|---|---|---|
| `design/parts_catalog.py` | **新建** | 端点客户端 + 缓存 + `PartEnriched` |
| `design/store.py` | 改 | `ARTIFACTS += ("parts_cache.json",)`（工程级缓存） |
| `design_cli.py` | 加 2 命令 | `parts-search` / `parts-enrich` |
| `src/tools.ts` | 加 1 工具 | `hw_parts` |

### 2.3 数据结构与信任级

```python
@dataclass
class PartEnriched:
    lcsc: str; mpn: str; package: str
    pin_count: int | None
    size_mm: dict[str, float]        # 从 package 尺寸表来（见 2.4）
    datasheet_url: str
    library_type: str                # basic | preferred | extended
    stock: int | None
    lifecycle: str                   # normal / discontinued ...
    price_breaks: list[tuple[int, float]]
    source: str                      # "lcsc:<code>:<iso日期>"
```

**信任级（核心纪律）**：

```
estimated(0) < catalog(1) < datasheet(2) < measured(3)
```

* LCSC 回填的 `size_mm/pins` → `physical_source="catalog"`
* **catalog 不进 `capability.candidates` 的硬选型**——它出现在
  `explain_miss().rejected`（原因=`source=catalog 未确认`）
  和新的 `hw_parts suggest`（"表外可选"列表）
* 出口：`hw_parts promote <mpn>` → 人确认后把该型号写进
  CAPABILITY 并标 `source="datasheet"`（前提：人核对过手册页；
  promote 时必须传 `--datasheet <url>` 留痕）

### 2.4 封装尺寸表（catalog→size_mm 的桥）

LCSC 的 package 字段是字符串（`"0603"` / `"SOT-23"` / `"LQFP-48"`）。
建 `design/package_sizes.py`：**按封装族建表**（不是按芯片），
初版覆盖 `PORTED_PACKAGES = (01005…2512, SOD*, SOT-23/89/223,
SOIC-8/14/16, TSSOP, QFN, LQFP-32…100, SOP-8, TO-252, SMA/SMB)`，
每个封装一行 `{family, w, h, d, pin_pitch}`。
数据来源：JEDEC/厂商封装图（一次性人工录入，约 30 行表）。
**这直接回答了 Q1 的"快速复现 2D"**：常见封装是标准化的，
一张表覆盖大多数芯片。

### 2.5 缓存与离线（照抄它的 Cache 设计）

`parts_cache.json`（工程级，TTL 7 天）+ `HWV_PARTS_OFFLINE=1`
环境变量。**任何网络失败 → 返回缓存（标 `stale`）或 `not_ready`，
绝不抛异常**——判定器不能因为目录挂了而挂。

### 2.6 验收

```
1. hw_parts search "stm32f103 lqfp48" → ≥5 条，每条带 package/lcsc/stock
2. hw_parts enrich C89852（ATtiny85）→ size_mm≈{4.9,6.0,1.6}（SOIC-8W）
   且 Part 写回后 physical_source=catalog
3. 闸门断言：catalog 型号在 generate 的 explain_miss.rejected 里，
   不在 candidates 里
4. promote 后（--datasheet 留痕）→ 出现在 candidates
5. 拔网线（或 HWV_PARTS_OFFLINE=1）→ not_ready + 缓存 stale 标记，
   主流程不受影响
6. 退化验证：把"catalog 不进硬选型"的判断删掉 → 验收 3 变红
```

---

## 3. M5-b：pins.h 生成 + 固件引脚交叉核对（3 天，解 Q2/Q3）

### 3.1 数据流（全部是已有数据的重新组合）

```
.ioc（P8 已解析：77 端点 + 84 label）
   │  parse_ioc() 已有 → {pin: (peripheral, role, label)}
   ▼
design/pinmap.py ──► 生成 pins.h
   │   #pragma once
   │   /* 由 dsh_embagent 从 F401-Front.ioc 生成 —— 不要手改 */
   │   #define HWV_PIN_TRIG      GPIO_PIN_8   /* PA8  VL53-PWR-EN */
   │   #define HWV_PIN_USART2_TX GPIO_PIN_2   /* PA2  USART2_TX  */
   ▼
固件源码（用户的，glob 指定）
   │  抽取器（正则，STM32 HAL 形状固定）：
   │    GPIO_PIN_\d+ / GPIOA..GPIOG / USART\d+ / (SDK 宏)
   ▼
交叉核对 report（pinmap_crosscheck.json）
   ├─ 硬件配了软件没用   → "引脚闲置"（提示级）
   ├─ 软件用了硬件没配   → "幽灵引脚"（**error 级**——运行时必炸或静默失效）
   └─ 双方都有但语义不符 → "方向冲突"（硬件配 TX 软件当 RX 输入读）
```

### 3.2 文件

| 文件 | 动作 |
|---|---|
| `design/pinmap.py` | **新建**：pins.h 生成 + 固件侧抽取 + 核对 |
| `design/store.py` | `ARTIFACTS += ("pinmap_crosscheck.json",)` |
| `design_cli.py` | 加 `pinmap-gen` / `pinmap-check` |

### 3.3 关键实现点

* **符号表来源**：`.ioc` 的 `GPIO_Label` 就是符号名
  （`PA15.GPIO_Label=VL53-PWR-EN` → `HWV_PIN_VL53_PWR_EN`）。
  无 label 的引脚用 `HWV_PIN_<端口><号>`。
* **命名消毒**：label 里的 `-`/空格 → `_`，防宏炸裂。
* **抽取器按 HAL 家族分表**（学它 `firmware/mcus.py` 的做法）：
  初版只做 STM32 HAL（`GPIO_PIN_x` + `GPIOx` + `HAL_UART`…），
  其他家族报 `not_ready: 未支持的固件 SDK`，**不硬猜**。
* **交叉核对输出必须引用双方**：
  `"幽灵引脚 PA8: 硬件 F401-Front.ioc 无此配置 ← 固件 main.c:42"`。

### 3.4 验收

```
1. 对 F401-Front.ioc 生成 pins.h → 含 VL53_PWR_EN 与 USART2_TX 宏
2. 合成固件夹具（用了 .ioc 没配的 PA10；没用已配的 PB6）→
   crosscheck 报 1 幽灵 + 1 闲置，各带文件行号
3. 改 .ioc 一个引脚 → 重生成 pins.h → 编译失败于引用处（演示命令给出）
4. 非 STM32 固件 → not_ready 说明，不猜
5. 退化验证：把"幽灵引脚=error"降成提示 → 验收 2 断言红
```

---

## 4. M6-a：Z 轴堆叠（3 天，解避障小车叠板）

### 4.1 数据模型（一处新增，全链路自动受益）

```python
# Placement 新增（model.py:110 起）
z_stack: str = ""      # "above:<board_id>" 空=不叠放
standoff_mm: float = 0.0  # 层间高度（排针长）。>0 时与 z_stack 联用
```

`to_dict/from_dict` 按现有可选字段模式追加（空值不写，旧文件不变——
与 `side`/`board_id` 同款，`model.py:150-152`）。
**schema 不升级**（AGENTS/execution-plan §2.1 纪律）。

### 4.2 两条新检查（`design/physical.py` 追加）

```python
def check_stack_collision(design, built) -> list[dict]:
    """上层板元件（含板体自身）投影 vs 下层板元件，在 standoff 高度带内求交。
    体积交 > 0 → error，报双方 ref 与重叠体积。"""

def check_stack_align(design, built) -> list[dict]:
    """叠板对的连接器配对（按 label/role 匹配）：
    引脚距 > 0.1mm → error，报错位量与方向。"""
```

判定挂进 `run_checks()`（`physical.py:589`）→ 阶梯 V2 自动包含 →
交接单自动消费（`_blocking` 已读 V2 findings，**零改动**）。

### 4.3 验收

```
1. 两板夹具 standoff=6mm：上层 C1 与下层 U1 XY 重叠 → collision error
   报体积；错开 1mm → pass
2. 排针对位：2x5 2.54mm 连接器错 1.27mm → align error 报错位量
3. r3demo 加一块叠放板跑全管线，handoff 消费新检查
4. 退化验证：把 standoff 高度带判定删掉 → 验收 1 红半（只剩 XY 重叠）
```

---

> **本节已被 `architecture-v2-lceda.md` §0/§3 修订。**
> 实测立创EDA专业版有 `PCB_Document.autoRouting` 与
> `importAutoRouteJsonFile`，布线不必自研为主路径——
> 本节降级为 M9 可选的对比实验。

## 5. M6-b：布线器移植（4–5 天 a + 3 天 b，**有止损点**）

### 5.1 移植范围（精确到文件与行数）

| 源（traceforge） | 行数 | 去向（dsh_embagent） | 改造 |
|---|---|---|---|
| `kicad/route.py` 330–2440（`_Router` 类） | ~2100 | `design/router.py` | 数据源从 KiCad 板换成 DesignModel；输出从 Node 树换成 `Wire/Pad` |
| `kicad/routegeom.py`（精确形状+空间索引） | 全部 | 同上合并 | 原样（纯几何，无 KiCad 依赖） |
| `kicad/escape.py` | 1410 | **M6-b 再搬** | QFN/QFP 扇出 |
| `planes.py` | 1242 | 只搬"双面灌铜+缝合"子集 | 我们只要 GND 平面 |

**不搬**：`impedance.py`/`stackup.py`/`diffpair.py`（多层场景）。

### 5.2 接口（移植后的形状，已对过双方源码）

```python
# design/router.py
@dataclass
class RouteOptions:          # 字段名与 RouteOptions(route.py:81) 对齐，删多层项
    grid: float = 0.2
    clearance: float = 0.2
    track_width: float = 0.25
    via_cost: float = 8.0
    edge_clearance: float = 0.5
    plane_nets: list[str] = ["GND"]     # 走灌铜不走线
    attempts: int = 12
    max_expansions: int = 400_000
    time_limit: float = 90.0
    repair_time: float = 120.0

@dataclass
class RouteResult:
    routed: list[str]                  # 网名
    failed: list[str]                  # 网名
    failure_reasons: dict[str, str]    # 网名 → "哪些铜皮挡路"
    wires: list[Wire]                  # ← 直接进 design.json
    vias: list[dict]
    pours: list[dict]                  # GND 平面多边形
    stats: dict[str, float]            # 每网长度/过孔数

def route_board(design: DesignModel, *,
                options: RouteOptions | None = None,
                boards: list[str] | None = None,   # 多板逐板布
                ) -> RouteResult: ...
```

**三处重定向**（移植时唯一要动脑的地方）：

1. **输入**：它从 `.kicad_pcb` 读焊盘/外框/净类 →
   改为从 `DesignModel` 读 `pads[]`（有几何）、
   `Board.size_mm`、`meta.net_classes`
   （无 pads 的元件**跳过并记 warning**——没焊盘几何就没法布，
   这正好与 `pins.audit()` 的三档来源呼应）
2. **输出**：Node 树 → `Wire(path: list[(x,y)], layer)`，
   `physical_source="routed"`；**经 `hw_put` 的 validate 落盘**
3. **裁决**：布完调 `physical.run_checks()`（10 个检查名见
   `physical.py:194-533`），error=0 才算 `ok`——
   **布线正确性由自家阶梯裁决，不引 KiCad**

### 5.3 CLI / 2D 呈现

```
hw_route <工程> [--grid 0.2] [--boards PCB#xxx] [--write]
```

`--write` 时 wires/pours 经 `hw_put` 进 design.json；
2D 面板（`Circuit2D.tsx`）**已有折线绘制能力**，预期零改动即可显示，
有缺口就补 `projection.py` 的 wire 采样。

### 5.4 验收与止损（写死）

```
M6-a 验收：
1. r3demo（4 元件，2 电源网+3 信号网）→ routed=100%，physical 零 error
2. 001-blink 布通后 design.json 含 wires，2D 面板可见（渲染/截图证据）
3. 构造不可达网（板中央放接地点四面堵死）→
   failure_reasons["X"] == "no path（其他网的铜皮封死所有通路）"
4. 确定性：同输入 route 两次 wires 列表逐项相等
5. 性能：r3demo < 5s；示例-单板主板(141件) < 90s（超时=止损信号）

止损线：验收 1 不通且 48h 内修不好 → M6-b 不启动，
       记入 AGENTS.md「移植失败案例」。
```

---

## 6. agent 循环（4 天，横切，依赖 §1/§2 的工具面）

### 6.1 骨架（学 `agent.py` 680 行，简化到 ~400 行）

```python
# design/agent_loop.py —— 无第三方依赖（httpx 已在 M5 引入）
@dataclass
class AgentTurn:
    history: list[dict]          # 追加式（thinking 合法、可恢复）
    tools: list[Tool]            # 只暴露白名单
    approval: str                # ask | auto | read-only

def run_turn(turn, user_text, model_client) -> TurnResult:
    """循环调用模型直到不再要工具。读并发、写串行（审批一次一个）。"""
```

**模型客户端协议**：`class ModelClient: def complete(messages, tools)
-> ToolCall|Text`——**抽象出来**，测试注入 `ScriptedClient`（照抄它
`tests/conftest.py:111` 的 scripted responses 模式），
真实现用 Anthropic API（人工触发，不进自动测试）。

### 6.2 工具白名单（闸门：agent 没有直写 design.json 的工具）

| 暴露 | 读写 | 说明 |
|---|---|---|
| `hw_list/hw_get/hw_snapshot` | 读 | 现状查询 |
| `hw_ladder/hw_claims/hw_justify/hw_handoff/hw_links/hw_units` | 读+写产物 | 判定可跑，产物独立于 design.json |
| `hw_generate` | 读 | 出候选 |
| `hw_route` | 写产物 | 布线（M6 后） |
| `hw_parts search/enrich` | 读 | 目录查询（M5 后） |
| `hw_put` | **写 design.json** | **唯一写入口**，过 `DesignModel.validate()` |
| `hw_promote` | 写 | 人工确认通道（auto 模式下也必问） |

**没有**：`setpart`/`move`/`delpart`（细粒度改动太危险，
agent 改设计只能整份 put + 校验）。**审批矩阵**：
`read-only` 拒一切写；`ask` 每写必问；`auto` 放行产物写与 `hw_put`，
但 `hw_promote` 与删除类**必问**。

### 6.3 grounding 强制（接 adoption-plan §1.3）

agent 文本结论里出现 `ref:X / net:Y` 引用时，系统提示要求
`cites:[...]`；`ground_findings()`（§1.3 实现）对 agent 输出跑一遍，
无引用的结论降级为提示。

### 6.4 验收（全假模型，0 token）

```
1. ScriptedClient 端到端：需求文本 → 调 hw_generate → 选 candNN →
   hw_put → hw_ladder 全绿。断言：每步工具入参过 schema 校验
2. 闸门断言：工具注册表里不存在 setpart/move/delpart
3. 审批：read-only 下调用 hw_put → 被拒并返回原因
4. 追加式历史：中断后 resume，重放结果一致
5. 退化验证：把"auto 模式下 promote 必问"改成放行 → 验收 3 红
```

---

## 7. grounding + 中立评分（M4-b，2 天，与 §1 同批）

### 7.1 grounding 实现（学 `grounding.py` 151-200 行）

```python
# design/grounding.py
CITE_RE = re.compile(r"^(ref|net|pin|link):(.+)$")

def ground_claims(claims: list[Claim], design: DesignModel) -> list[dict]:
    """每条断言的 requires/cites 逐条核对；无引用或引用不存在 →
    ungrounded=True，**不计入 pass**（三态里它进 unknown 桶）。"""
```

`Claim` **不加字段**（`model.py` 的 Claim 是
`claims.py:G` 处的 dataclass，动它要过 put 校验链）——
grounding 结果写进 `claims.json` 的外层报告：
`{"grounded": [...], "ungrounded": [{id, problems}]}`。

### 7.2 中立评分实现

```python
# design/score.py
WEIGHTS_V1 = {          # SCORING_VERSION=1，改权重必须升版本
    "requirements": 40, "electrical": 15, "geometric": 15,
    "claims": 10, "parts_trust": 10, "area": 5, "cost": 5,
}
def score_candidate(cand, req) -> Scored:
    """s_k ∈ [0,1]；除 requirements 外全乘 gate=requirements。
    requirements = 接口覆盖率（req.interfaces 里每条是否被 links 覆盖）。"""
```

`generate._rank` 改用 `score_candidate().total` 排序；
**同分保持字典序**（确定性不回退）。

### 7.3 验收

```
1. 需求覆盖 0% 但零失败的候选 → 总分被 gate 压到 <10 分，排名垫底
2. cites 指向不存在网 → 该断言进 ungrounded 且不计 pass
3. 权重改动后 SCORING_VERSION 未升 → 测试红
4. 确定性：同候选集两次评分逐字节一致
```

---

## 8. 顺序、总表、止损

### 8.1 执行顺序（含依赖）

```
批 1  §1 interop(2d) ─► §7 grounding+评分(2d)          = M4，~4 天
批 2  §2 LCSC(3d) ─► §3 pinmap(3d)                      = M5，~6 天
批 3  §4 堆叠(3d) ─► §5 布线 a(4–5d) ─► (b 3d, 可缓)    = M6，~7–8 天
批 4  §6 agent(4d)                                       横切，~4 天
──────────────────────────────────────────────────────
合计 ~21–22 天（+布线 b 3d 可选）
```

### 8.2 交付物清单

| 新文件 | 批次 |
|---|---|
| `design/interop.py` + `design/grounding.py` + `design/score.py` | 批 1 |
| `design/parts_catalog.py` + `design/package_sizes.py` + `design/pinmap.py` | 批 2 |
| `design/router.py`（+ `physical.py` 加 2 检查） | 批 3 |
| `design/agent_loop.py` + `scripts/fake_model.py` | 批 4 |
| `scripts/check_m4.py` / `check_m5.py` / `check_m6.py` | 各批 |
| 改：`store.py`(ARTIFACTS×4)、`design_cli.py`(+9 命令)、`tools.ts`(+5 工具)、`generate.py`(_rank)、`model.py`(Placement 2 字段)、`physical.py`(+2 检查) | 各批 |

### 8.3 总止损表

| 风险 | 触发信号 | 动作 |
|---|---|---|
| LCSC 端点失效/被墙 | search 连续 403/超时 | 缓存模式兜底，M5 降级为"手动录入+校验" |
| 布线器移植失败 | §5.4 验收 1 两天修不好 | M6-b 取消，记 AGENTS；Z 轴堆叠不受影响 |
| agent 假模型端到端不通 | 批 4 验收 1 | 回退"AI 只产 interop，人跑 CLI"——价值仍在（interop 已解审查通用性） |
| DesignModel.validate 拒收真实 interop | 批 1 验收失败 | 放宽对应字段而不是绕过校验 |

### 8.4 诚实边界（做完也不承诺的事）

* 布线只做 2 层 + GND 平面；多层/阻抗/盲埋孔**不在计划内**
* LCSC 数据 `source=catalog`，**进不了硬选型**，直到人核对手册并 promote
* agent 真跑要花钱（token），自动化测试永远 0 token
* 固件引脚抽取只做 STM32 HAL 形状；其他 SDK 报 `not_ready`
* P7"DSH 内真跑 Renode"依旧无解（沙箱），维持"产物外部执行"
