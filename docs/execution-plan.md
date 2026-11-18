# hw_velxio 架构升级执行方案

> **本文是可执行方案，不是设计提案。** 设计提案见
> [architecture-target.md](architecture-target.md)。
>
> 本文与提案的分歧点，以及为什么，写在 §0.1 与 §16。
> 所有代码位置、行号、接口签名都来自**当前仓库实测**，不是推测。

---

## 0. 前提与边界

### 0.1 本方案的四条核心判断（与提案的分歧）

| # | 提案的说法 | 本方案 | 理由 |
|---|---|---|---|
| 1 | §5 闭环从「需求 → 单元」开始 | **从「现有工程反推」开始** | 提案的 `pick_parts(units, kg, constraints)` 依赖知识图谱；而建图要人工录 datasheet，你的 141 元件是几十小时人力。**AI 生成任何东西之前先要人做几十小时录入，与目标冲突** |
| 2 | §7 知识图谱是候选生成基础，应早做 | **降级到 P6，先做窄表** | PCBgen 的 10²⁰ 是「电源拓扑 × 器件参数」测出来的；你的拓扑已定（7 板已分），实际搜索空间是几千。图的多跳推理在这里用不上 |
| 3 | §5.2 用 `for attempt in range(MAX)` 循环收敛 | **有限枚举 + 批量排序，不写循环** | §11 Q3 自己说「收敛性没有可靠答案」。改枚举后不需证明收敛——有 `max_candidates` 上限即终止 |
| 4 | 实施顺序隐式从 L0 往上 | **L5/L4 → L3 → L0–L2** | 先定「什么叫合格」，再让生成器对准它。反过来做会导致生成的东西**没有判定器能验** |

### 0.2 第一交付物

**不是生成器，是「能解释并审查你现有 141 元件设计」的工具。**

M1 结束时你能得到：每个元件为什么在、删掉它会怎样、哪里没验证、谁去验。
这比一个还不知道对不对的生成器更早产生价值。

### 0.3 明确不做

| 不做 | 原因 |
|---|---|
| ❌ 改 `easyeda.py`(1071 行) / `easyeda_import.py`(591 行) | 导入器是只读资产，动了风险极高 |
| ❌ 重写 `physical.py` / `electrical.py` / `coverify.py` 的判定逻辑 | 只做**编排包装**，判定器本身是对的 |
| ❌ 进控制律（四旋翼姿态融合/PID/失效保护） | 算法不是电路；硬塞会让模型边界模糊（提案 Q6 同判断） |
| ❌ 热 / EMC / 机械 / 工艺仿真 | 无模型，只能标 `unverifiable` |
| ❌ 升 `design.json` 的 schema 版本 | 见 §2.1——会打断所有现有工程 |

---

## 1. 阶段总览

| 阶段 | 目标 | 新增模块 | 验收命令 | 估时 | 状态 |
|---|---|---|---|---|---|
| **P0** | 修电气零负载空过 | 改 `electrical.py` 2 处 | `electrical.ok` 空工程变 false | 0.5 天 | ✅ 完成 |
| **P1** | 验证阶梯 + 硬失败截断 | `ladder.py` (31KB) | `hw_ladder` 截断生效 | 3 天 | ✅ 完成 |
| **P2** | 断言层 `claims` | `claims.py` (24KB) | `hw_assert --remove` 变红 | 5 天 | ✅ 完成 |
| **P3** | 归因反推（含算法） | `justify.py` (~700) | `hw_justify` 有非 unknown | 6 天 | ✅ 完成 |
| **P4** | 交接层 | `handoff.py` (~600) | 生成两份 md | 3 天 | ✅ 完成 |
| **P5** | 单元层 + 板间 | `units.py`(~330) + `links.py`(~640) | 7 板可表达 | 8 天 | ✅ 完成 |
| **P6** | 能力表 + 枚举生成 | `capability.py`(~330) + `generate.py`(~470) | 同需求出 3 候选 | 8 天 | ✅ 完成 |
| **P7** | Renode 接入 | `renode.py` (~300) | 单板真跑出串口 | 5 天 | ⬜ 未开始 |
| **P8** | `.ioc` → links 抽取 | `ioc_links.py` (~400) | 32 串口出草稿 | 5 天 | ⬜ 未开始 |

**合计约 44 天**（单人，含测试）。**M1、M2 已完成。**

一条命令验各阶段硬验收：

```bash
python -m hw_velxio.scripts.check_m1      # 或 npm run verify
python -m hw_velxio.scripts.check_units_links   # M2 P5
python -m hw_velxio.scripts.check_generate      # M2 P6
```

### 1.1 三个里程碑

| 里程碑 | 包含 | 累计 | 交付价值 |
|---|---|---|---|
| **M1** | P0–P4 | 17.5 天 | **「能解释并审查现有设计」** |
| **M2** | +P5–P6 | 33.5 天 | **「能从需求生成候选」** |
| **M3** | +P7–P8 | 43.5 天 | **「能真跑固件 + 自动抽板间」** |

**M1 是分水岭。** M1 之后工具立刻可用；M2 之后才有生成能力。

### 1.2 M1 实际交付与计划的偏差

计划是估算，实现时会遇到计划里没写的事。**逐条记下来**——它们说明
"哪里比预想的复杂"，比"完成了"有信息量。

| 项 | 计划 | 实际 | 原因 |
|---|---|---|---|
| P3 归因 | ~500 行 | **700 行** | 多了双口径（全部元件 / 有网表元件）与套话过滤 |
| P4 交接 | ~400 行 | **600 行** | 多了 `handoff.md` 渲染器与产物指纹 |
| 断言条数（`示例-单板`） | — | 141 → **266** | 归因驱动的断言接进了 `derive()` |
| 删除即失败覆盖 | — | **10/10** 元件都有功能级断言 | 第一版只有 `connected`，见偏差一 |

#### 偏差一：`claims` 没有消费 `justify`（已修，这是真 bug）

第一版 `derive()` 只给每个元件生成一条 `claim.<ref>.connected`。
于是 `hw_assert --remove R1` 报的是：

```
count=1  reddened=['claim.R1.connected']
```

这句话只说"R1 已经不在网表里了"。而 `justify` 明明算出"R1 是
3.3V 到 PB10 的上拉"——那条知识**没有进入判定**。结果是删掉一个
上拉电阻，系统只是**变安静**（少一条断言），没有**变红**（没有
任何断言说"PB10 悬空了"）。这正是 `claims.py` 开头要治的病，
却在 P2 里又犯了一遍。

修法：`derive()` 增加一步，把 `justify` 的结论映射成断言
（`_JUSTIFY_RULES`，共 24 条映射）。现在：

```
R1    → claim.R1.provides_default_level
C66   → claim.C66.decouples
R96   → claim.R96.in_path
Q2    → claim.Q2.switches_power
X1    → claim.X1.oscillates
D3    → claim.D3.prevents_backfeed
U1025 → claim.U1025.receives_control
```

**回归测试**：`test_derive_consumes_justification`（推导级）与
`test_remove_reddens_a_functional_claim`（行为级）。后者直接断言
"删掉元件后变红的不能只有 `connected`"。我把 `_attribution_map`
改成恒返回 `{}` 模拟退化，测试确实失败：

```
AssertionError: [] is not true : D1 被判为 signal_diode，
但 derive() 只给了它 connected —— 归因结论没有被断言消费
```

#### 偏差二：`unknown` 比例被缺数据污染（已修）

`示例-无人机` 的 44 个元件**全部没有网表**（`nets == []`），
归因自然是 0%。第一版只报一个 `ratio`，看起来像"算法坏了"。
真相是**它没有网表可依**。

改成双口径：

| 工程 | 全部元件 | 有网表元件 | 无网表元件 |
|---|---|---|---|
| `示例-单板` | 99.3% | 99.3% | 0% |
| `示例-多板` | 44.3% | **99.3%** | 55.4% |
| `001-blink` | 100% | 100% | 0% |
| `r3demo` | 100% | 100% | 0% |
| `示例-无人机` | 0% | — | **100%** |

`示例-多板` 那行是关键：全部口径 44.3% 会让人以为算法差，
实际有网表的元件里判出 99.3%——差距全是 175 个没网表的元件。
**一个数字会骗人，两个不会。**

#### 偏差三：`kind` 不该只看 ref 前缀（已修）

`r3demo` 的四个元件 `ref` 是 `CAM1`/`SV1`/`PS1`，前缀一个都命不中，
但 `kind` 分别是 `camera_module`/`gimbal_servo`/`psu_5v`——**完全
可读的类型名**。有类型名不用，等于假装信息不存在。

现在 `category_of()` 先看 ref 前缀（更可靠，它记录的是设计者填
参考号时的意图），判不出才看 `kind`/`role`/`label` 里的类型词。
`r3demo` 因此从 0% 变成 100%。

注意区分「类型名」与「哈希」：`camera_module` 是类型名，
`0deda055e6014e4e` 是哈希。哈希命中不了任何词条，如实落到
`unknown`。

#### 偏差四：自动网名有多种写法（已修）

第一版只把 `$` 前缀当自动名（立创 EDA 的 `$1N8009`）。但
`001-blink`（Wokwi）用的是 `w1`/`w2`/`w3`。于是理由里出现
"串在 w1 与 w2 之间"——读的人会以为那是什么信号名，其实是
导入器随手编的序号。

现在 `_AUTO_NET_RE` 同时认 `^\$` 与 `^[wW]\d+$`。
`001-blink` 的覆盖率因此到 100%。

#### 偏差五：P1 的验收夹具选错了（已修，且是概念错误）

我拿真实工程 `示例-单板` 验 P1，看到 `stopped_at=""` 就以为截断坏了。
**其实是对的**：它的 V2 是 `fail`（6 处重叠），而 `HARD_LEVELS`
只有 `V0`/`V1`——几何重叠不影响电气判决，不该截断下游。

P1 必须用**极性接反**的合成夹具（12V 接进额定 3.6V 的 MCU 脚）。
这个区分现在写死在 `check_m1.py` 里，并在文件头注释说明理由。

#### 偏差六：`_cell` 会把 dict 塞进表格（已修）

阶梯的 `findings` 放的是结构化记录（dict），不是字符串。第一版
直接 `str(f)`，于是 `handoff.md` 的"你要做什么"列里出现了一整段
Python dict 字面量，JSON 里的引号还把表格行截断了。

现在 `_finding_text()` 优先取 `message` 字段（那已是最精炼的描述）。
另外加了 220 字符上限，超出标 `…（完整内容见 handoff.json）`。

#### 偏差七：`coverify` 从不落盘，交接单第三类永远空（已修）

计划 §7.4 要求交接单里出现「默认值：coverify 7 条 not_verified」。
第一次生成 `handoff.md` 时，第三类「机器用了默认值」是 **0 条**。

根因：`coverify` 的结果只出现在 `snapshot` 的**返回值**里，从不写盘；
而 `handoff._assumed()` 去 `budget.json` 里翻 `coverify` 字段——
那个字段从来不存在。于是这一段代码永远读到 `None`，
第三类永远是空的。

`示例-单板` 实际有 7 条 `not_verified`，一条都没进交接单：

```
指向精度未声明，按默认 1.0° 计算（影响 CV-01）
最大跟踪角速度未声明，按默认 45.0°/s 计算（影响 CV-04）
检测距离未声明，按默认 3000.0mm 计算（影响 CV-03）
最小目标尺寸未声明，按默认 150.0mm 计算（影响 CV-03）
链路带宽未声明，按默认 8.0Mbps 计算（影响 CV-05）
端到端延迟未声明，按默认 300.0ms 计算（影响 CV-06）
电源电流未声明，按默认 2000.0mA 计算（影响 CV-07）
```

这是典型的「静默丢项」：**空表看起来像"没有默认值"，
真相是"有 7 个但没人去看"**——比误报更危险的形状。

修法：给 `coverify` 一条独立命令 + 独立产物 `coverify.json`
（原来它只能靠 `snapshot` 顺带算，没有自己的写入路径）。

回归：`check_handoff.py` 增加两条断言——有 `coverify.json` 时必须
报出默认值、没有时**不能虚构**。实测把 `_assumed` 改回读
`budget.json`，测试立刻报 `FAIL ... 0 条`。

#### 偏差八：工具读错了 coverify 的字段名（已修）

`hw_coverify` 第一版按 `id` / `status` / `counts.passed` 读结果，
而实际字段是 `check_id` / `passed`（布尔）/ `summary.total`。
类型检查抓不到（都是 `Record<string, unknown>`），运行时会静默读成空，
于是工具报「通过 0 / 失败 0」而 `ok=false`——自相矛盾。

教训：**TS 侧读 Python 产物时，字段名必须对着真实输出核一遍**，
不能按命名习惯猜。这台机器上 `tsc` 是绿的，错的是契约假设。

---

### 1.3 M2（P5–P6）实际交付与计划的偏差

P5 交付 `units.py` + `links.py`，P6 交付 `capability.py` + `generate.py`。
验收入口：

```bash
python -m hw_velxio.scripts.check_units_links    # 或 npm run verify:units
python -m hw_velxio.scripts.check_generate       # 或 npm run verify:generate
```

计划 §12.3 的两条判据都过了：

| 阶段 | 判据 | 实测 |
|---|---|---|
| P5 | `示例-多板` 分出 ≥2 unit | **8 个**（元件总数 316 对齐） |
| P6 | 同需求两次结果一致（确定性） | 逐字节一致；20 个候选 |

#### 偏差一：`role` 判不出单元角色，必须换信号（已修）

计划 §8.1 的骨架是：

```python
roles = sorted({p.role for p in members if p.role != "component"})
```

实测 `示例-多板` 8 块板的 `role` 全是导入器兜底值
（`component` / `connector` / `module`），没有一个是真实语义角色。
照这个骨架走，8 个单元会全部落到同一个 `unknown`，而理由是
"role 里没有信息"——这对人没有用。

改用**板上的具名网络**：网络名是人写下的意图，是这份数据里唯一
还带语义的东西。实测主板有 64 条具名网（OSC_IN/BOOT0/NRST/RF-*/
VBAT/BTN_...），其余 7 块板 **0 条**。这个差别本身就是结论。

结果：主板判成 `mcu`（`roles` 还含 radio/power/sensor/storage/user_io），
其余 7 块如实 `unknown`。

#### 偏差二：8 个单元里 7 个判不出，比例必须拆成两个（已修）

沿用 M1 的双口径。实测：

```
全部单元口径   1/8  = 12.5%      ← 看起来算法很差
有具名网口径   1/1  = 100.0%     ← 算法没问题，差的是数据
```

只报一个数会让人去改算法，真正该做的是补那 7 块板的网表。

#### 偏差三：UART 波特率不能按整数分频算（已修，是假失败）

计划 §8.2 只有一句 `_baud_reachable(design, link)`。第一版按整数
分频实现：

```
divisor = clock/(16*baud)，取整，算实际值
```

实测 `8MHz + 115200` → divisor 4.34 → 取整 4 → 实际 125000 →
偏差 8.5% → 判"不可达"。**这个结论是错的**：STM32 的 USART BRR
有 4 位小数分频，4.34 可写成 4 + 5/16 = 4.3125 → 实际 115942 →
偏差 0.64% → 完全可用。

假失败和假通过一样坏：人会去改本来不需要改的时钟。现在按参考手册
的 BRR 结构算（`baud_reachable`，含 `fraction_bits` 参数）：

| 时钟 | 目标 | 结果 | 实际 |
|---|---|---|---|
| 8 MHz | 115200 | ✅ 可达 | 115942（0.64%） |
| 8 MHz | 3 Mbps | ❌ 不可达 | 500000（83%） |
| 72 MHz | 921600 | ✅ 可达 | 923077（0.16%） |

#### 偏差四：`RF_RXEN` 被当成 UART（已修）

第一版用子串匹配（`"_rx"`），于是 `RF_RXEN`（射频收发**使能**脚，
一个 GPIO）被判成 UART 链路，立刻出现在推断结果里。

改成**词元**匹配：`U1_RX` → `['u','1','rx']` → 命中；
`RF_RXEN` → `['rf','rxen']` → 不命中。另外 `USART2` 这种
"词 + 数字"的写法用"词元前缀 + 纯数字"兜住（`.ioc` 里的外设名
全是这个形状）。

#### 偏差五：推断的链路没算"已覆盖"，报告自相矛盾（已修）

第一版 `observed_unlinked` 只对**声明**的链路做差集。于是
`SPI_MISO_1` 同时出现在"推断出的 6 条链路"和"15 条没有链路的网"里。
同一份报告前后矛盾，人会以为推断失效。

修法：推断链路也算覆盖，但单独记 `covered_by_inferred`，
让人知道这条是被"推断"覆盖的，不是被"声明"覆盖的。

#### 偏差六：只按能力过滤，选出了气压计当电台（已修）

计划 §9.1 的过滤条件只有 `needs ⊆ provides`。第一版照做，于是
`radio` 单元选出了 `bmp280`——一个气压计。它的能力集合
（`spi`/`i2c`/`gpio`）确实覆盖 radio 的需求（`spi`/`gpio`），
**过滤成立，语义荒谬**。

补 `fills` 维度：

* `provides` 回答"它能干什么活"
* `fills` 回答"它是什么东西"

两者不可互相替代。`fills` 是可核对的**类别事实**（看 datasheet
第一页就知道），所以它能参与硬过滤。现在 `radio` 只从
`esp32s3` / `lora-sx1278` 里选，`sensor` 里不会出现 MCU。

#### 偏差七：`needs` 把"消耗"写成了"提供"（已修）

`_UNIT_NEEDS['motor']` 原来是 `("pwm","power")`，结果 `servo-sg90`
被过滤掉——因为它 `provides={"pwm"}`，`power` 在 `requires` 里。

错在把"消耗什么"（requires）当成了"提供什么"（provides）。
舵机**用** 5V，它不**给** 5V。修法：`needs` 只写必须能接的接口，
供电交给 `electrical` / `budget` 管。有了 `fills` 做语义闸门后，
`needs` 可以写得很短（`sensor` 直接留空）。

#### 偏差八：一颗料兼两职被列成两行（已修）

`esp32s3` 既能当 `mcu` 又能当 `radio`（它有 Wi-Fi，`roles.py` 里
`radio.mergeable_with=('mcu',)` 写的就是这件事）。第一版两个单元
各列一行，于是：

* 面积被算了两次（49mm² 的芯片算成 98mm²，候选排名被扭曲）
* 链路里出现"自己连自己"的自环网

修法：`_collapse()` 把同型号合并成一颗料，`units` 记多个、
`model` 一个；`to_design` 里两端指向同一颗料的链路**跳过**
（那是芯片内部连接，不是板间链路）。

#### 偏差九：单元层和链路层曾是"没人消费的报告"（已修）

这是 M1 修过的老毛病又出现一次：`justify` 算出了结论但没人用。
P5 的两个产物如果不进交接单，就只是两个没人看的 JSON。

现在 `handoff` 读 `units.json` / `links.json`，"缺数据"类因此从 2 条
变成 6 条：

```
板间链路一条都没声明
15 条外设信号网没有对应链路
7 个单元完全没有网表
```

回归：`check_handoff.py` 断言这四类必须出现在交接单里，且
**没有产物时不能虚构**（改回旧行为立刻 FAIL）。

---


## 2. 数据模型变更

### 2.1 关键约束：不能升 schema

实测 `design/model.py:716`：

```python
schema = str(data.get("schema") or "")
if schema and schema != SCHEMA:
    raise DesignError(f"不认识的 schema：{schema!r}（期望 {SCHEMA}）")
```

**升 `/2` 会打断所有现有工程。** 改为**只加可选字段**，靠 `to_dict` 的条件写出实现向后兼容（照抄现有 `boards` 的做法，`model.py:700`）：

```python
# 现有做法：多板设计才写 boards，单板不写，旧文件读进来也不变
**({"boards": [b.to_dict() for b in self.boards]} if self.boards else {}),
```

### 2.2 新增字段清单

| 字段 | 位置 | 默认 | 向后兼容机制 |
|---|---|---|---|
| `justification` | `Part` | `None` | 旧文件无此键 → None；`to_dict` 时省略 |
| `unit` | `Part` | `""` | 同上 |
| `links` | `DesignModel` | `[]` | 旧文件无此键 → 空列表 |
| `units` | `DesignModel` | `[]` | 同上 |

**兼容性已验证**：`Part.from_dict`（`model.py:276`）是显式构造，未知键被忽略。

### 2.3 `Justification`（新 dataclass）

```python
# design/model.py，Part 之前
@dataclass
class Justification:
    """元件为什么在。**这是 S2/S3 判据的载体。**

    为什么要独立类型而不是塞进 notes：notes 是自由文本，
    而这里的每个字段都要被 handoff.md 消费、被断言引用。
    """
    requirement: list[str] = field(default_factory=list)   # 服务的需求 id
    why: str = ""                                          # 人话理由
    effect_if_removed: str = ""                            # 删掉会怎样
    alternatives: list[str] = field(default_factory=list)  # 便于换料
    #: asserted（人/AI 明确声明）| inferred_by_topology（从网表结构推）
    #: | backfill（从现有工程反推）| unknown（判不出）
    confidence: str = "asserted"
    source: str = "human"          # human | ai | import
```

### 2.4 `Part` 新增字段

```python
# design/model.py:178，electrical 之后
justification: Justification | None = None
unit: str = ""            # 所属功能单元 id（对应 units 里的项）
```

`to_dict`（`model.py:253` 起）追加：

```python
if self.justification is not None:
    out["justification"] = self.justification.to_dict()
if self.unit:
    out["unit"] = self.unit
```

`from_dict`（`model.py:276`）追加：

```python
justification=(Justification.from_dict(data["justification"])
               if data.get("justification") else None),
unit=str(data.get("unit") or ""),
```

### 2.5 `Link`（新 dataclass，照抄 hwforge 契约）

**不重造**——`hwforge/interfaces.py:76` 已有这个模型，字段含义一致：

```python
@dataclass
class Link:
    """板间连接。

    **端点用 unit.peripheral，不用引脚。** 理由（照抄 hwforge 的注释）：
    a link is between *devices*, and both ends of a UART are already
    two pins by the time it exists.
    """
    id: str
    a: dict[str, Any]          # {unit, peripheral, pins}
    b: dict[str, Any]
    protocol: str = ""         # uart | i2c | spi | can | usb | analog
    params: dict[str, Any] = field(default_factory=dict)   # {baud, level_v}
    #: ioc（从 .ioc 抽）| declared（人声明）| inferred（配对推测）
    source: str = "declared"
    evidence: str = ""
```

**约束**：`source == "inferred"` 的 link **永不参与硬检查**（提案 I2）。

### 2.6 `Unit`（新 dataclass）

```python
@dataclass
class Unit:
    """功能单元——一块板或板上一个功能域。"""
    id: str
    role: str = ""                                  # 人话："主控与决策"
    board_id: str = ""                              # 对应 Board.id
    owns_roles: list[str] = field(default_factory=list)
    provides_to: list[str] = field(default_factory=list)
    justification: Justification | None = None
```

---

## 3. P0：修电气零负载空过（0.5 天）

### 3.1 问题（已实测复现）

```python
from hw_velxio.design.electrical import rail_under_load
r = rail_under_load('5V', 5.0, 0.0, 90.0, 4.8, rated_a=2.0)
# r.ok == True, r.v_at_load == 5.0, r.note == ''     ← 空过
```

真实工程上的表现（`示例-单板`，141 元件零电流声明）：

```json
{"ok": true, "rails": [{"name":"5V","load_a":0.0,"v_at_load":5.0,"ok":true}]}
```

### 3.2 根因

`design/electrical.py:443-452`：

```python
load_a = entry.get("load_a")
if load_a is None:
    load_a = total_load              # ← 没有 loads 时 total_load = 0.0
if not loads and load_a is None:     # ← 永远不成立（上一行已赋值 0.0）
    report.not_verified.append(f"{name}: 没有负载电流数据，轨跌无法验证")
    continue
```

`0.0` 不是 `None`，所以这个"没数据"分支**永远进不去**，直接算出 `5.0 >= 4.8 → ok=True`。

这与同仓库 `budgets.py:149` 的做法自相矛盾：

```python
"status": "ok" if confidence != "none" else "not_ready",   # ← 缺失就 not_ready
```

### 3.3 修改（两处）

**改动 1** — `design/electrical.py:443-452`：

```python
load_a = entry.get("load_a")
if load_a is None:
    load_a = total_load
# ★ 零负载 = 没有数据，不是"负载很轻"
if not loads and not load_a:
    report.not_verified.append(
        f"{name}: 没有任何元件申报负载电流，轨跌无法验证（不是通过）")
    continue
```

**改动 2** — `design/electrical.py:217` `rail_under_load` 内，加防御：

```python
if load_a <= 0.0:
    return RailResult(
        name=name, nominal_v=nominal_v, load_a=0.0,
        source_r_mohm=source_r_mohm, sag_v=0.0, v_at_load=nominal_v,
        min_required_v=min_required_v,
        ok=False,                       # ★ 不是 ok
        note="负载电流未声明：轨跌无法验证",
        rated_a=rated_a, over_rated=False)
```

### 3.4 验收

```bash
# 1. 单测（215 个必须仍通过；如有断言 electrical.ok==True 需按新语义修正）
python -m unittest hw_velxio.tests.test_design

# 2. 空工程
HWV_WORKSPACE=<空工程> python -m hw_velxio.design_cli snapshot <id> \
  | python -c "import json,sys; d=json.load(sys.stdin); e=d['electrical']; \
    print('ok=', e['ok'], 'not_verified=', e['not_verified'])"
# 期望：ok=False 且 not_verified 含"没有任何元件申报负载电流"

# 3. 真实工程（应从 true 变 false）
python -m hw_velxio.design_cli snapshot 示例-单板 | python -c "import json,sys; \
  print(json.load(sys.stdin)['electrical']['ok'])"
# 期望：False
```

### 3.5 风险

| 风险 | 缓解 |
|---|---|
| 现有测试断言 `electrical.ok == True` 会挂 | 这是**修正**不是破坏；改断言并在 commit message 说明语义变更 |
| 用户看到 `ok: false` 困惑 | P4 的 `handoff.md` 会把它列成「补 current_ma」待办 |

---

## 4. P1：验证阶梯 + 截断（3 天）

### 4.1 设计原则

**不改现有判定器。** `physical.run_checks()`(680 行)、`electrical.run()`(513 行)、
`coverify.run()`(674 行) 都是"全量跑完再报"——它们是对的，一次跑完能给出完整图像。

P1 只加**编排与截断**：

```python
# design/ladder.py
"""验证阶梯 V0→V6，硬失败截断。

为什么需要：生成阶段与审查阶段要的东西不同。
审查要"完整图像"（全跑）；生成要"尽快知道这条路走不通"（截断）。
极性反接意味着板子会烧，此时算时序是浪费。
"""
```

### 4.2 阶梯映射

| 层 | 名称 | 实现 | 复用 |
|---|---|---|---|
| **V0** | 符号 | 新写 ~60 行：`nets[].pins` 的 ref 存在、`links` 端点存在 | — |
| **V1** | 约束 | 新写 ~200 行：电平/极性/绝对最大值 + 从 `coverify` 抽 CV-07/CV-08 | 抽部分 |
| **V2** | 几何 | **直接调** `physical.run_checks(design, pitch_mm)` | 680 行 |
| **V3** | 电气 DC | **直接调** `electrical.run(design)` | 513 行 |
| **V4** | 时序/协议 | 新写 ~120 行：波特率可达（时钟/分频计算） | — |
| **V5** | 数字仿真 | P7 前返回 `not_run` | — |
| **V6** | SPICE | 永久 `not_run`（未接，如实报） | — |

### 4.3 核心代码骨架

```python
LEVELS = ("V0", "V1", "V2", "V3", "V4", "V5", "V6")
HARD_LEVELS = ("V0", "V1")          # 硬失败即截断


@dataclass
class LevelResult:
    level: str
    status: str                     # pass | fail | unknown | not_run
    findings: list[dict[str, Any]] = field(default_factory=list)
    unknown: list[str] = field(default_factory=list)
    ms: float = 0.0


@dataclass
class LadderResult:
    levels: list[LevelResult] = field(default_factory=list)
    stopped_at: str = ""            # "" = 跑完
    downstream: str = "run"         # run | truncated


def run_ladder(design, *, levels=None, stop_on_hard=True,
               pitch_mm: float = 0.5) -> LadderResult:
    seq = list(levels or LEVELS)
    out = LadderResult()
    for name in seq:
        t0 = time.perf_counter()
        runner = RUNNERS[name]
        try:
            res = runner(design, pitch_mm=pitch_mm)
        except NotImplementedError:
            out.levels.append(LevelResult(name, "not_run", [],
                                          ["本层未实现"]))
            continue
        res.ms = (time.perf_counter() - t0) * 1000
        out.levels.append(res)
        if stop_on_hard and name in HARD_LEVELS and res.status == "fail":
            out.stopped_at = name
            out.downstream = "truncated"
            for rest in seq[seq.index(name) + 1:]:
                out.levels.append(LevelResult(
                    rest, "not_run", [],
                    [f"上游 {name} 硬失败，本层未运行（结论无意义）"]))
            break
    return out
```

### 4.4 P0/P1 硬检查：绝对最大值（**新增最重要的一块**）

```python
#: 绝对最大值的保守上限。**来源写在注释里，不是编的。**
ABS_MAX = {
    # STM32 手册 VDD max = 4.0V（F1/F4/H7 家族通用）
    "stm32": {"pin_v": 4.0, "vdd_v": 4.0},
    # ESP32 数据手册：3.6V
    "esp32": {"pin_v": 3.6, "vdd_v": 3.6},
    # ATmega328P abs max：-0.5V to +6.0V
    "avr":   {"pin_v": 6.0, "vdd_v": 6.0},
}


def check_abs_max(design) -> LevelResult:
    """P0：引脚电压超过绝对最大值 → 烧板。"""
    findings = []
    unknown = []
    for net in design.nets:
        rail_v = _rail_voltage_of(net["net"])
        if rail_v is None:
            unknown.append(f"{net['net']}: 推不出轨电压，绝对值未判")
            continue
        for pinref in net.get("pins", []):
            part = design.part(pinref["ref"])
            if part is None:
                continue
            fam = _family_of(part.kind)
            if fam is None:
                unknown.append(f"{part.ref}: kind={part.kind!r} 判不出厂商族")
                continue
            limit = ABS_MAX[fam]["pin_v"]
            if rail_v > limit:
                findings.append({
                    "check": "hard.abs_max", "ok": False,
                    "severity": "error", "priority": "P0",
                    "refs": [part.ref],
                    "measured": rail_v, "limit": limit,
                    "message": f"{part.ref} 在 {net['net']}({rail_v}V) 上，"
                               f"超过 {fam} 引脚绝对最大值 {limit}V：会烧",
                })
    ...
```

**关键纪律**：`_family_of(kind)` 对你工程里 141 个 EasyEDA 哈希 id
（实测 `101005049fad...` 这类）**判不出来** → 返回 `None` → **跳过并记 `unknown`**。
不猜。

### 4.5 CLI 与工具

```bash
python -m hw_velxio.design_cli ladder <id> [--levels V0,V1,V2] [--no-truncate] [--pitch 0.5]
```

```typescript
// src/tools.ts 新增
{
  name: 'hw_ladder',
  description: '按 V0→V6 阶梯跑验证，P0/P1 硬失败截断下游。'
    + '便宜的先跑（符号/约束/几何 ms 级），贵的后跑（仿真/SPICE）。',
  parameters: {
    project: {...}, levels: {...}, stopOnHard: {...}, pitchMm: {...},
  },
}
```

### 4.6 验收

```bash
# 造一个极性反接设计：5V 灌 3.3V MCU
hw_ladder project=<坏设计> --levels V0,V1,V2,V3
# 期望：stopped_at=V1；V2/V3 的状态是 not_run（**不是 pass**）

# 真实工程
hw_ladder project=示例-单板
# 期望：V0 pass / V1 unknown(哈希 kind 判不出族) /
#        V2 fail(6 处重叠) / V3 unknown(零负载)
```

---

## 5. P2：断言层 `claims`（5 天）

### 5.1 支点逻辑

提案 §1.3：**每个元件必须携带一条可执行断言；断言的失败条件包含"该元件不存在"。**

实现关键在 `requires` 字段：

```python
missing = [r for r in claim.requires if not _exists(design, r)]
if missing:
    return "fail"          # ★ 依赖缺失 = 断言失败
```

于是"删掉 R12" → `claim.gps.reset_stable.requires` 里的 `part.R12` 找不到
→ **断言变红**。这就是"删除即失败"。

### 5.2 `design/claims.py`

```python
@dataclass
class Claim:
    id: str
    type: str            # presence | capability | protocol | numeric | composite
    scope: dict          # {unit} | {part} | {link}
    intent: str          # 人话
    requires: list[str] = field(default_factory=list)   # ★ part.R12 / link.L-IF01
    check: dict = field(default_factory=dict)
    on_fail: dict = field(default_factory=dict)         # {message, narrow[]}
    priority: str = "P2"    # P0 | P1 | P2 | P3


def _exists(design, ref: str) -> bool:
    kind, _, key = ref.partition(".")
    if kind == "part":
        return design.part(key) is not None
    if kind == "link":
        return any(l["id"] == key for l in design.links)
    if kind == "unit":
        return any(u.get("id") == key for u in design.units)
    return False


def evaluate(claim: Claim, design) -> str:      # pass | fail | unknown
    if any(not _exists(design, r) for r in claim.requires):
        return "fail"
    kind = claim.check.get("kind")
    if kind == "presence":   return _eval_presence(claim, design)
    if kind == "protocol":   return _eval_protocol(claim, design)
    if kind == "numeric":    return _eval_numeric(claim, design)
    if kind == "composite":
        subs = [evaluate(s, design) for s in claim.check.get("all_of", [])]
        if "fail" in subs:    return "fail"
        if "unknown" in subs: return "unknown"
        return "pass"
    return "unknown"
```

### 5.3 断言来源：**规则生成，不让 AI 写**

```python
#: 规则表：从元件的角色/类型推出它该有什么断言
CLAIM_RULES = (
    (lambda p: p.role == "pullup",
     lambda p: Claim(
         id=f"claim.{p.ref}.provides_default_level",
         type="capability", scope={"part": p.ref},
         intent=f"{p.ref} 为所在网络提供默认电平",
         requires=[f"part.{p.ref}"],
         check={"kind": "numeric",
                "expr": f"net_of({p.ref}).has_default_level == true"},
         on_fail={"message": f"{p.ref} 缺失 → 该网络悬空，电平不确定",
                  "narrow": [{"action": "add_part", "role": "pullup"}]},
         priority="P2")),
    (lambda p: p.role in ("mcu", "module"),
     lambda p: Claim(
         id=f"claim.{p.ref}.powered",
         type="numeric", scope={"part": p.ref},
         intent=f"{p.ref} 获得合规供电",
         requires=[f"part.{p.ref}"],
         check={"kind": "numeric",
                "expr": f"rail_of({p.ref}).v_at_load >= {p.ref}.min_v"},
         priority="P1")),
)
```

**为什么不让 AI 直接写断言**：AI 会写出"永远为真"的断言
（例如 `exists(part.R12)`——元件不在就求值不到，永远不红）。
规则生成保证断言**必然与元件绑定**。

### 5.4 存储与 CLI

```python
# design/store.py:34 ARTIFACTS 追加
ARTIFACTS = (..., "claims.json")
```

```bash
python -m hw_velxio.design_cli claims <id>              # 生成 + 求值
python -m hw_velxio.design_cli claims <id> --evaluate   # 只求值
python -m hw_velxio.design_cli assert <id> --remove R12 # ★ 删除即失败验证
```

`--remove` 的实现（**不真删**，在内存副本上剔除再求值）：

```python
def cmd_assert(args):
    store, design = _load(args[0])
    ref = args[args.index("--remove") + 1]
    if design.part(ref) is None:
        return {"status": "not_ready", "findings": [f"没有 {ref}"]}

    claims = claims_module.derive(design)
    before = {c.id: claims_module.evaluate(c, design) for c in claims}

    clone = DesignModel.from_dict(design.to_dict())   # 深拷贝
    clone.parts = [p for p in clone.parts if p.ref != ref]
    after = {c.id: claims_module.evaluate(c, clone) for c in claims}

    reddened = [cid for cid in before
                if before[cid] == "pass" and after[cid] == "fail"]
    return {"status": "ok", "removed": ref, "reddened": reddened,
            "count": len(reddened),
            "note": "这些断言因该元件缺失而变红——这就是它的存在理由"}
```

### 5.5 验收（**硬条件**）

```bash
hw_new p1 arduino-uno
hw_synth project=p1 template=blink specJson={"mcu":"arduino-nano"}
hw_assert p1 --remove R1
# 期望：count > 0，且 reddened 含 "claim.R1.*"
```

**如果 `reddened` 为空，说明断言机制没建对，P2 不算完成。**

---

## 6. P3：归因反推（6 天，含算法）

### 6.1 这是唯一需要真算法的部分

`design/justify.py`：从网表结构反推元件功能。

```python
def infer(design, part) -> Justification:
    """从一个已存在的元件反推它的存在理由。

    三条线索，按可信度排序：
      L3 拓扑（最可信）：它在哪条网上？那条网有没有驱动源？
      L2 角色：roles.BY_ROLE[part.role].why（role≠component 时有效）
      L1 电压：它在哪条电源轨上？

    **不猜厂商型号语义**（EasyEDA 哈希 kind 猜不出），
    只说"从结构上看它是干什么的"。
    """
    nets = [n for n in design.nets
            if any(p["ref"] == part.ref for p in n.get("pins", []))]
    if not nets:
        return Justification(
            confidence="unknown", source="ai",
            why="未连接到任何网络（可能是机械件或漏连）",
            effect_if_removed="无电气影响")

    for net in nets:
        pins = net.get("pins", [])
        drivers = [p for p in pins if is_driver(design, p)]
        # 线索 L3-a：无驱动 + 像无源件 → 上拉/下拉
        if not drivers and _looks_passive(part):
            return Justification(
                confidence="inferred_by_topology", source="ai",
                why=f"{net['net']} 无驱动源，{part.label or part.ref} 提供默认电平",
                effect_if_removed=f"{net['net']} 悬空，电平不确定")
        # 线索 L3-b：单个驱动 + 串联 → 限流/分压
        if len(drivers) == 1 and _in_series(part, net):
            drv = drivers[0]
            return Justification(
                confidence="inferred_by_topology", source="ai",
                why=f"串在 {drv['ref']}.{drv['pin']} 与负载之间，限流/分压",
                effect_if_removed=f"{drv['ref']} 直连负载，可能过流")

    # 线索 L2：角色表（roles.py 的 why 字段）
    mapping = roles.BY_ROLE.get(part.role)
    if mapping is not None and mapping.why:
        return Justification(
            confidence="asserted", source="ai",
            why=f"角色 {part.role}：{mapping.why}",
            requirement=[part.role])

    return Justification(confidence="unknown", source="ai",
                         why="结构上无法判定功能，需人工补充")
```

### 6.2 最大的技术债：引脚无方向

实测 `design/model.py:181`：

```python
pins: list[str] = field(default_factory=list)   # ← 只有名字，没有方向
```

所以 `is_driver()` 只能三档推断，每档注明来源：

```python
def is_driver(design, pinref) -> bool | None:
    """这个引脚是输出吗？

    **DesignModel 的 pins 没有方向**（model.py:181），所以用三档推断：
      1. 板卡引脚 ("board","D13") → 数字口，约定为输出
      2. 引脚名含 TX/OUT/DO → 输出（命名约定）
      3. 其余 → None（不猜）
    """
    if pinref["ref"] == "board":
        return True                          # 板卡数字口
    name = pinref["pin"].upper()
    if any(t in name for t in ("TX", "OUT", "DO")):
        return True
    if any(t in name for t in ("RX", "IN", "DI", "GND", "VCC", "3V3", "5V")):
        return False
    return None
```

**建议 P3.5 补** `Part.pin_dirs: dict[str, str]`，来源是 `.ioc` 的
`PA2=USART2_TX`（有 TX 就是 out）。这是 P8 的输入之一。

### 6.3 归因粒度（提案 Q1 的答案）

| 元件类型 | 粒度 | 理由 |
|---|---|---|
| 功能件（MCU/GPS/雷达/相机/连接器） | **逐个** | 每个都是架构决策 |
| 无源件（电阻/电容/电感） | **按功能组** | 单个 0402 电容不需要独立理由，但"这组去耦电容"需要 |

你工程实测：141 元件里 **125 个 `role:"component"`**。按组能压到约 **20 组**。

### 6.4 验收

```bash
hw_justify 示例-单板
# 期望：
#   - 有 net 的元件 → confidence=inferred_by_topology 或 asserted
#   - 无 net 的元件 → confidence=unknown（如实报，不编）
#   - 每条都有 why + effect_if_removed
# 硬条件：confidence != unknown 的比例 > 20%
```

**抽查 3 个，人工核对理由对不对——这一步必须人做，机器验不了。**

### 6.5 风险

| 风险 | 缓解 |
|---|---|
| AI 写出空洞套话（提案 Q1 的担忧） | `why` **必须含具体网名/元件名**，模板强制拼接 |
| 哈希 kind 猜不出功能 | 返回 `unknown`。141 里可能一大半是 unknown——**这是诚实结果** |
| 无方向 pins 误判 | 三档推断，第 3 档返回 None |

---

## 7. P4：交接层（3 天）

### 7.1 `design/handoff.py`

聚合散在 4 处的"未验证"，输出结构化数据：

```python
def build(design, ladder, claims_result, budget, coverify) -> dict:
    return {
        "blocking":    _blocking(ladder, claims_result),   # P0/P1 失败
        "assumed":     _assumed(design, coverify),         # 用了默认值
        "unverifiable": _unverifiable(ladder),             # V7 类
        "missing_data": _missing_data(design, budget),     # 零负载、无 claim
        "orphan":      _orphan(design),                    # 未接线的外设
        "justified":   [(p.ref, p.justification) for p in design.parts
                        if p.justification is not None],
    }
```

### 7.2 输出格式（照提案 §8.2）

下面是一份 `handoff.md` 的样例（**内容在代码块内，不是本文的章节**）：

````markdown
# 交接：<project>（AI 生成，待人工确认）

### 一、必须确认才能打板（阻断）
| # | 项 | 状态 | 你要做什么 | 风险 |
|---|---|---|---|---|
| 1 | 5V 轨负载 | ❌ 0/141 有 current_ma | 给出每个负载电流 | 空中掉电 |

### 二、可打样但需实测（非阻断）
| # | 项 | 为什么没验证 | 怎么验 |
|---|---|---|---|
| 1 | 电机瞬态跌落 | DC 模型不含 | 示波器 + 阶跃负载 |

### 三、AI 用了默认值（需替换）
| 项 | 默认值 | 来源 |
|---|---|---|
| 指向精度 | 1.0° | assumed |

### 四、已生成但未接线（悬空）
| 单元 | 外设 | 说明 |
|---|---|---|
| front | USART1 | .ioc 里有，未在 interfaces 声明 |
````

### 7.3 归档

```python
ARTIFACTS = (..., "handoff.json")     # 结构；markdown 由 CLI 渲染
```

### 7.4 验收

```bash
hw_handoff 示例-单板
# 期望：handoff.md 四张表都非空。
# 基于实测的已知内容：
#   阻断：4 项不可逆决策未闭合
#   实测：6 处元件重叠（C77/C87/R96/R97/R250/R251 与 U1）
#   缺数据：0/141 有 electrical 声明
#   默认值：coverify 7 条 not_verified
```

---

## 8. P5：单元层 + 板间（8 天）

### 8.1 `design/units.py`（纯结构推导，不用 AI）

```python
def derive(design) -> list[Unit]:
    """从现有 board_id 分组反推单元。

    **不用 AI，纯结构。** 你的 7 块板在导入时已经有 Board.id，
    元件通过 Placement.board_id 归属（model.py:520 注释确认）。
    """
    units = []
    for board in design.board_list():
        members = design.parts_of(board)
        roles = sorted({p.role for p in members if p.role != "component"})
        units.append(Unit(
            id=board.id or "main",
            role=_infer_unit_role(roles),
            board_id=board.id,
            owns_roles=roles))
    return units
```

### 8.2 `design/links.py`（四条检查）

```python
def validate(design) -> list[dict]:
    findings = []
    for link in design.links:
        # ★ inferred 不参与硬检查（提案 I2）
        if link.get("source") == "inferred":
            continue
        # 1. 协议一致
        if not _same_protocol_family(link["a"]["peripheral"],
                                     link["b"]["peripheral"]):
            findings.append(_fail("link.protocol_match", link,
                f"{link['a']['peripheral']} 与 {link['b']['peripheral']} 协议族不同"))
        # 2. 电平兼容
        va = link.get("params", {}).get("level_v", 3.3)
        vb = _peripheral_level(design, link["b"])
        if vb is not None and va != vb:
            findings.append(_fail("link.level_match", link,
                f"{va}V ↔ {vb}V 不匹配：需电平转换"))
        # 3. 波特率可达
        ok, actual = _baud_reachable(design, link)
        if not ok:
            findings.append(_fail("link.baud_feasible", link,
                f"目标 {link['params'].get('baud')}，实际可达 {actual}"))
    # 4. 孤儿外设：.ioc 里有、links 里没有
    findings += _orphans(design)
    return findings
```

**第 4 条最重要**——它抓的是"设计者的隐性假设"。
实测你的 7 个 `.ioc`：**258 引脚 / 32 个串口 / 0 个 label**。
这条会逼着把"USART2 接谁"说出来。

### 8.3 验收

```bash
hw_units 示例-多板
# 期望：分出 ≥2 个 unit（该工程 316 元件）

hw_links 示例-多板
# 期望：报告未声明的外设（当前应全报，因为 links 为空）
```

---

## 9. P6：能力表 + 枚举生成（8 天）

### 9.1 `design/capability.py`（窄表，不是知识图谱）

```python
"""能力表——把搜索空间砍到几百的最便宜做法。

**不是图，是字典 + 过滤函数。** 理由：
你的项目拓扑已定（7 板已分），搜索空间不是 10²⁰ 而是几千。
图的多跳推理在这里用不上，而维护成本会吃掉收益。
"""

CAPABILITY: dict[str, dict] = {
    "stm32h743vit": {
        "provides": {"uart", "spi", "i2c", "can", "usb"},
        "requires": {"power(3.3V,400mA)"},
        "footprint_mm2": 196.0,
        "pins": {"USART1": ["PA9", "PA10"], "USART2": ["PD5", "PD6"]},
        "source": "datasheet",
    },
    # ... 先手工录 10 个（你项目用到的 7 个 MCU + 3 个模块）
}


def candidates(needs: set[str], constraints: dict,
               exclude: set[str] = frozenset()) -> list[str]:
    """纯过滤，毫秒级。没有图遍历。"""
    out = []
    for key, cap in CAPABILITY.items():
        if key in exclude:
            continue
        if not needs <= cap["provides"]:
            continue
        if cap["footprint_mm2"] > constraints.get("area_mm2", 1e9):
            continue
        if cap["source"] not in ("datasheet", "measured"):
            continue          # ★ inferred 不参与硬检查
        out.append(key)
    return out
```

### 9.2 `design/generate.py`（有限枚举，**不证收敛**）

```python
def generate(req, *, max_candidates: int = 50) -> list[Candidate]:
    """四段式，但**枚举而非迭代收敛**。

    与提案 §5.2 的区别：不写 `for attempt in range(MAX)` 循环。
    理由：收敛性无法证明（提案 Q3 自己说没答案），
    而枚举天然终止（有 max_candidates 上限）。
    """
    units = units_mod.derive_from_req(req)                  # ① 确定性
    cands: list[Candidate] = []
    seen: set[str] = set()
    for parts_choice in _enumerate_parts(units, req.constraints,
                                          budget=max_candidates):
        links_choice = links_mod.wire(parts_choice, req.interfaces)  # ③
        claims = claims_mod.derive_all(parts_choice, links_choice)   # ④
        sig = _signature(parts_choice, links_choice)
        if sig in seen:
            continue
        seen.add(sig)
        cands.append(Candidate(parts=parts_choice,
                               links=links_choice, claims=claims))
        if len(cands) >= max_candidates:
            break
    return _rank(cands, req)         # ★ 批量按阶梯排序，不迭代


def _rank(cands, req) -> list[Candidate]:
    """按 (硬失败数, 软失败数, unknown 数, 成本) 排序。"""
    for c in cands:
        r = ladder.run_ladder(c.to_design(), stop_on_hard=True)
        c.hard_failures = sum(1 for l in r.levels
                              if l.status == "fail" and l.level in ("V0", "V1"))
        c.soft_failures = sum(1 for l in r.levels if l.status == "fail")
        c.unknowns = sum(len(l.unknown) for l in r.levels)
        c.cost = sum(p.get("cost", 0) for p in c.parts)
    return sorted(cands, key=lambda c: (c.hard_failures, c.soft_failures,
                                         c.unknowns, c.cost))
```

### 9.3 为什么这样能终止（回应提案 Q3）

| 提案做法 | 本方案 |
|---|---|
| `while not ok: 改设计 → 验证` | `枚举 ≤50 个 → 批量验证 → 排序` |
| 需证明"约束单调收窄" | **不需证明**：枚举有硬上限 |
| 可能震荡 | 不可能（无循环） |
| 输出 1 个最优解 | 输出 3–5 个候选（工程师选） |

**代价**：不保证全局最优。对"可运行初版"这个目标，**可行解比最优解重要**。

### 9.4 验收

```bash
hw_generate --requirement req.yaml --max-candidates 20
# 期望：≥3 个候选；同一需求跑两次结果一致（确定性）
```

---

## 10. P7：Renode 接入（5 天）

### 10.1 现状（已核实）

| 事实 | 位置/值 |
|---|---|
| Renode 平台映射已就绪 | `hwforge/blocks/renode_sim.py` 的 `KNOWN_PLATFORMS`（H743/F103/F4 全在） |
| 运行器已实现 | `hwforge/downstream.py:260` `run_renode(script, timeout_s)` |
| `hw_velxio` 只探测不调用 | `design/toolchain.py:98` `probe_renode()`，无调用方 |
| Renode 在本机 | `C:/Users/<user>/Desktop/renode-master/renode`（bash 启动脚本） |
| **缺 ARM 工具链** | `which arm-none-eabi-gcc` → 无 |
| **缺 ELF** | 工作区 0 个 `.elf`（只有 9 个 `.bin`/`.hex`） |

### 10.2 `design/renode.py`

```python
"""把 Renode 从"探测到但没用"变成真能力。"""

def status() -> dict:
    return {"renode": probe_renode().to_dict(),
            "arm_toolchain": _probe_arm_gcc().to_dict()}


def run_board(design, board_id: str, *, elf: str = "",
              timeout_s: int = 180) -> dict:
    if not status()["renode"]["available"]:
        return {"status": "not_ready",
                "findings": ["找不到 Renode", probe_renode().reason]}
    if not elf:
        return {"status": "not_ready",
                "findings": ["没有 ELF：需要 arm-none-eabi-gcc 编译，"
                             "或用 .bin + 显式 load address"]}
    script = _write_resc(design, board_id, elf)
    return run_renode(script, timeout_s=timeout_s).to_dict()
```

### 10.3 `.resc` 生成（**只在 links 存在时才加 connector**）

```python
def _write_resc(design, board_id, elf) -> Path:
    board = design.board_by_id(board_id)
    mcu = _mcu_of(board)                 # 从 parts 找 U1 的 kind
    platform = _platform_for(mcu)        # 复用 hwforge 的映射
    lines = [
        f'mach create "{board_id}"',
        f'machine LoadPlatformDescription @{platform}',
        f'sysbus LoadELF @{elf}',
        'showAnalyzer sysbus.usart2',    # 串口输出 = 观测面
    ]
    # ★ 只在有 links 时才加 connector；没有 links 不猜
    for link in design.links:
        if link["a"]["unit"] == board_id:
            lines.append(
                f'connector Connect sysbus.{link["a"]["peripheral"]} '
                f'{link["b"]["unit"]}.sysbus.{link["b"]["peripheral"]}')
    lines.append("start")
    ...
```

**这就是"多板联调"** —— `connector Connect` 是 Renode 原生命令，不需要发明任何东西。

### 10.4 验收

```bash
# 前置：装 arm-none-eabi-gcc，编出 1 个 ELF
#   建议挑最简单的 F103-GPS（24 个 .c）
hw_renode --project <id> --board F103-GPS --elf build/gps.elf
# 期望：串口输出被捕获到 stdout
```

**如果 Renode 在 Windows 上能加载 `stm32f103.repl` 并跑出串口，
"多板联调"就只是 P5 的 `links` 生成器（~100 行）的事，不是能力问题。**

---

## 11. P8：`.ioc` → links 抽取（5 天）

### 11.1 `design/ioc_links.py`

```python
def extract_endpoints(ioc_path) -> list[Endpoint]:
    """PA2=USART2_TX → Endpoint(peripheral="USART2", pins=["PA2"], dir="tx")"""


def pair(endpoints) -> tuple[list[Link], list[str]]:
    """按可信度配对：
    1. 显式 rules（用户写的 links.yaml）→ source="declared"
    2. 同名外设 + 引脚交叉（PA2→PA3 / PA3→PA2）→ inferred, 高置信
    3. 板名语义（Front→前、Bottom→底）→ inferred, 低置信
    4. 无法配对 → 进 problems，**不猜**
    """
```

### 11.2 验收

```bash
hw_ioc-links --root "C:\Users\<user>\Desktop\示例工作区"
# 期望：7 个 .ioc → 抽出 32 个串口端点 → interfaces.draft.json
#       每条标 source=inferred + evidence="F401-Front.ioc: PA2=USART2_TX"
```

---

## 12. 测试策略

### 12.1 三层

| 层 | 内容 | 工具 |
|---|---|---|
| **单测** | 现有 215 个 + 新增（claims/ladder/justify 各 30–50 个） | `unittest` |
| **回归** | 用 `示例-单板`（141 元件）作夹具，关键数字不漂 | 新 `test_regression.py` |
| **端到端** | `hw_synth → hw_ladder → hw_handoff` 全链路 | 扩展 `check_plugin.py` |

### 12.2 回归夹具（关键设计）

```python
# tests/test_regression.py
FIXTURES = {
    "示例-单板": {
        "expect": {
            "parts": 141, "nets": 119, "wires": 1119,
            "checks.failed": 6,          # 6 处重叠（C77/C87/R96/R97/R250/R251）
            "electrical.ok": False,      # ★ P0 之后改为 False
            "deferred.open": 4,
        }
    },
}


def test_contract(project):
    """真实工程的关键数字不漂。**任何改动跑一遍。**"""
```

### 12.3 每阶段的硬验收（不可协商）

| 阶段 | 硬验收 |
|---|---|
| P0 | `rail_under_load(...load_a=0.0).ok == False` |
| P1 | 极性反接 → `stopped_at == "V1"`，V2/V3 是 `not_run`**不是 pass** |
| P2 | `hw_assert --remove R1` 的 `reddened` **非空** |
| P3 | `示例-单板` 141 元件里 `confidence != unknown` **> 20%** |
| P4 | `handoff.md` 四张表都有内容 |
| P5 | `示例-多板` 能分出 ≥2 个 unit |
| P6 | 同需求两次跑结果一致（确定性） |
| P7 | 单板真固件跑出串口字节 |
| P8 | 7 个 `.ioc` → ≥10 条 inferred links |

---

## 13. 回滚策略

**核心安全策略：所有阶段都是"加而不改"。**

| 改动 | 回滚方式 | 风险 |
|---|---|---|
| P0 电气 | 改回 2 处代码 | 低 |
| P1–P4 | 新文件；不加入 `COMMANDS` 表即无效 | 极低 |
| P5 schema | `to_dict` 只在非空时写 → 旧文件不变 | 低 |
| P6 generate | 独立命令，不进 `snapshot` | 低 |
| P7 renode | 独立命令 | 极低 |

`design_cli.py` 的命令注册在 `COMMANDS` 字典（第 1446–1478 行），
新增命令只是加一行——删掉即回滚。

---

## 14. 与现有纪律的对齐检查

| 现有纪律 | 本方案如何遵守 |
|---|---|
| 判定只在 Python 一份 | ladder/claims/justify 全在 Python，TS 只调 CLI |
| 不把失败变成空成功 | P0 修的就是这条；每层 `unknown` 与 `pass` 严格分离 |
| `inferred` 永不参与硬检查 | P8 配对标 inferred；P6 `candidates()` 过滤非 datasheet |
| 一次调用一行 JSON | 新 CLI 全部走 `_emit()`（`design_cli.py:44`） |
| 面板必须显示覆盖率 | handoff 三档（blocking / assumed / unverifiable） |
| 不改用户源码 | justify/claims 只读；`--remove` 在内存副本上做 |

---

## 15. 要新增/修改的文件清单

### 新增（9 个）

```
design/ladder.py        ~300   P1  验证阶梯 + 截断
design/claims.py        ~600   P2  断言定义/求值/规则生成
design/justify.py       ~500   P3  归因反推（含算法）
design/handoff.py       ~400   P4  交接聚合 + 两份 md
design/units.py         ~400   P5  功能单元
design/links.py         ~400   P5  板间四条检查
design/capability.py    ~300   P6  能力窄表 + 过滤
design/generate.py      ~500   P6  枚举 + 排序
design/renode.py        ~300   P7  Renode 接入
design/ioc_links.py     ~400   P8  .ioc → links
tests/test_regression.py ~200  全程  回归夹具
```

### 修改（5 个）

```
design/electrical.py    2 处     P0  零负载空过
design/model.py         +120 行   P2/P5  Justification / Link / Unit + to_dict/from_dict
design/store.py         +2 行     P2/P4  ARTIFACTS 加 claims.json / handoff.json
design_cli.py           +9 行    全程   COMMANDS 加 9 个命令
src/tools.ts            +~400 行  全程   hw_ladder / hw_claims / hw_justify / hw_handoff
```

**注：`physical.py` / `coverify.py` / `easyeda.py` / `requirements.py`
一行都不改**——判定器与导入器是资产，不是负债。

---

## 16. 需要你确认的四件事

### Q1 先做「审查机」还是「生成器」？

本方案选**审查机**（M1 = P0–P4，17.5 天）。

- **好处**：立刻可用，能解释你现有 141 元件；17.5 天见收益
- **代价**：前 17.5 天看不到"AI 生成设计"

**如果你更想先看到生成**，可以把 P5–P6 提前，但那样断言层没建好，
生成的东西无法验证——**会得到一堆没有判据的零件**。

### Q2 归因粒度

建议**功能件逐个 + 无源件按组**（提案 Q1 的倾向，我同意）。

你工程实测 125/141 是 `role:"component"`，按组能压到 ~20 组。

### Q3 是否先补 `kind` 映射来提高归因覆盖率？

P3 的验收目标是 `confidence != unknown > 20%`，这个数字偏低，
原因是你工程里 141 个元件的 `kind` 是 EasyEDA 哈希（实测 `101005049f85ad2f` 这类），
`_family_of()` 判不出厂商族。

**选项**：
- **A**：接受 20%，先让机制跑通（推荐）
- **B**：先补 `kind` 映射（从 BOM 的 Footprint 反推 R/C/U/Q → 族），
  能把覆盖率提到 60%+，但要多 2–3 天

### Q4 ARM 工具链谁装？

P7 的前置。三个选择：

- **A**：你手动装（[ARM 官方下载](https://developer.arm.com/downloads/-/arm-gnu-toolchain-downloads)）
- **B**：方案里加"自动下载到 `~/.dsh/hw_velxio/toolchain/`"一节（照抄 P1 里 arduino-cli 的做法）
- **C**：跳过 P7，先用 `.bin` + 显式 load address

---

## 17. 一句话总结

**先做能审查的（M1，17.5 天），再做能生成的（M2）。**

每个阶段都是「加文件不改文件」，M1 结束时你就有一个能告诉你
**"这 141 个元件为什么在、哪里没验、谁去验"** 的工具——
这比一个还不知道对不对的生成器更早产生价值。

---

**本文档是可执行方案。** 如果确认开始，第一步是 P0（改 `electrical.py` 两处 + 修正相关测试断言），
半天内可完成并验证。
