# dsh_embagent — 硬件工作台（DSH 插件）

一份 **mm 规范模型**，两个投影：**2D 电路是电气/拓扑权威，3D 装配是物理/机械权威**。
体素网格只做求解索引（重叠/keepout/限高），不参与渲染。这正是 dsh-embagent-docs:方案-v3.md
的核心判断：hwforge 的 3D 不是"要做好的烂 3D"，而是一个被误当成渲染器的求解器。

落到 DSH 里，它是一个**会话视图**（与 Chat / Trajectory 并列的「硬件工作台」标签），
> 历史方案 / 执行记录已迁到私有仓库 `dsh-embagent-docs`（22 篇，索引见其 `INDEX.md`）；
> 本仓库只保留**跟代码一起改**的功能文档（`README` / `AGENTS` / `CHANGES` / `runbook` / `architecture-*` 等）。

不是右侧边栏。前端重构（模仿 Velxio 的分布形式，F1–F7）见
**dsh-embagent-docs:方案-前端-v1.md**。

```
┌ 硬件工作台 ─────────────────────────────────────────────────────────────┐
│ [工程▾] [检查] [预算] [核对] [导出] [2D|并排|3D] [AI 评审]   [状态徽章]   │
├──────────────────────┬──────────────────────┬──────────────────────────┤
│ 2D 电路 · 电气权威    │ 3D 装配 · 物理权威    │ 检查器                    │
│ 拖拽移动 / 右键旋转   │ 轨道 / 爆炸 / 剖面    │ 尺寸/来源/位置/该件判定    │
├──────────────────────┴──────────────────────┴──────────────────────────┤
│ [判定] [预算] [未决] [核对] [证据/轨迹] [制造] [积木] [Velxio]            │
└────────────────────────────────────────────────────────────────────────┘
```

## Velxio 能力（编译 / 电路 / 库 / 仿真）

四类能力，**全部在 DSH 内跑，不需要浏览器、不需要 Docker**：

| 工具 | 做什么 | 需要后端？ |
|---|---|---|
| `hw_compile` | 真实编译 Arduino/ESP-IDF → hex（arduino-cli） | ✅ 需要 |
| `hw_circuit` | Wokwi diagram ↔ 电路 JSON（复用 Velxio 的 `app/mcp/wokwi`） | ❌ 纯计算 |
| `hw_libs` | Arduino + MicroPython 库搜索/安装 | ✅ 需要 |
| `hw_sim` / `hw_serial` | **真跑固件**（avr8js）并读串口 | ❌ 不需要 |
| `dsh_embagent` | 后端生命周期：status/start/stop/logs | — |

后端管理（**不需要 Docker**，用本机 venv 跑 uvicorn）：

```bash
python -m dsh_embagent.serve prepare   # 建 venv + 装依赖
python -m dsh_embagent.serve arduino   # 预置 arduino-cli + AVR core
python -m dsh_embagent.serve start     # 启动（计划任务托管，跨工具调用存活）
python -m dsh_embagent.serve status    # 三态 + 工具链 + 补丁状态
python -m dsh_embagent.serve stop
```

场景补丁（让后端启动从 76 秒降到 0.5 秒）：

```bash
python -m dsh_embagent.patch status|install|remove
```

**详细说明**：dsh-embagent-docs:docs/p4-simulation.md（含 AVR 可直接跑的
实测证据、ESP32 为什么不可用的三重约束、补丁改了什么）。

**明确的边界**：AVR 仿真支持 ATmega328p 家族（Uno/Nano/Mini/Pro Mini），
**不提供元件动画**（那需要浏览器）；Mega2560 与 ESP32 会如实拒绝，不硬套。

## 工作区（工程住哪）

工程**不在插件目录里**——那是安装位置。工作区是用户资产目录，按优先级解析：

| 优先级 | 来源 | 说明 |
|---|---|---|
| 1 | `HWV_WORKSPACE` 环境变量 | 脚本/CI/多工作区切换 |
| 2 | `~/.dsh/hw_velxio.json` 的 `workspace` | **界面里"选择文件夹"写这里** |
| 3 | `~/.dsh/hw_velxio/workspace/` | 没有选择时的默认 |

界面里只有一个工作区入口——工具栏右上角那条**可点路径**（点开是设置块）：

- **浏览文件夹…** → 客户端侧解析两个入口（顺序与宿主自己的 native 客户端一致）：
  Electron 桌面壳的 `__DSH_DIRECTORY_PICKER__`（preload IPC，直达**系统资源管理器**
  选择框）优先；其次 `uiWorkspace.pickDirectory()`（RPC 到宿主 directoryPicker，
  远程/无桌面时宿主退化为应用内 browse，失败会**明确报出原因**）。
  注意 `ctx.directoryPicker` 是**宿主 node 侧**的服务，浏览器上下文里取不到——
  接错层的版本正是"打不开资源管理器"的原因。
- **新建** → 在 DSH 当前工作目录下建子文件夹并切过去；
  之后新建的工程都落在里面。

工作区是**设置**，不是"开始"：默认工作区始终可用，所以空态不把它写成前置条件，
只在抬头用一行事实说明"工程将建在 …"。设置块收起时**不渲染**（不是 `display:none`）。

空态只有一张「开始」卡，两个页签二选一：**新建预设板卡** / **从文件导入**。

## 立创 EDA 工程（`.epro2`）—— 唯一能给出网表的来源

`dsh_embagent` 的 2D 是**电气权威**，它需要"有哪些元件"和"谁连到谁"。工作区
里大部分材料只能给一半：

| 材料 | 元件清单 | 连接关系 |
|---|---|---|
| `.ioc` | ✗ | 部分（知道用了哪个外设，不知道对端） |
| `BOM` | ✓ | ✗ |
| `.bin` / `.hex` | ✗ | ✗ |
| **`.epro2`** | **✓** | **✓（网络名）** |

`.epro2` 本质是 ZIP：`project2.json`（元数据）+ `.epru`（变更流，每行一个
JSON，`|` 分隔）+ `IMAGE/*.webp`（原理图截图）。解析要点：

- 带 `type` 的记录只有 `id`/`ticket`，**真正的属性在"带 key/value 的 ATTR
  记录"里**，靠 `parentId` 归属；
- 同一物理元件在原理图/PCB/封装库里**各存一份**，必须按标号合并并**补全
  字段**（不是丢弃——不同文档携带的字段不同）；
- 值为 `={Value}` 的是未解析表达式，不能当真值显示。

```bash
# 导入（可选 --bom：用物料清单补全 PCB 里没有的元件）
python -m dsh_embagent.design_cli import <工程id> board.epro2 --bom BOM.csv

# 给**已有**工程补 BOM（工程已有但内容不全时用这个）
python -m dsh_embagent.design_cli bom <工程id> BOM.csv [--dry-run]
```

### 需求 → 设计：R2 角色映射（`roles` 命令）

需求侧的分析在 hwforge（`spec.analyse_requirement()`，O1.0/O1.5 两级）。
它产出**角色**，而 `assemble` 需要**模板**——两者之间原本没有任何代码连接，
所以"需求 → 设计"只能靠手工写死一个参考设计。

`design/roles.py` 填的就是这一格：一张声明式映射表 + 一个校验器。

```bash
# 只校验映射表：表里写的模板在 hwforge 里真的存在吗
python -m dsh_embagent.design_cli roles --validate

# 直接给角色
python -m dsh_embagent.design_cli roles --roles camera,gimbal_pan,servo_power

# 从自然语言需求出发（需要 hwforge 可导入）
python -m dsh_embagent.design_cli roles --goal "做一个壁挂云台相机，手机看实时视频"
```

**映射表**（9 行，两边都由 `validate()` 对照真实代码校验）：

| 需求角色 | 装配模板 | 说明 |
|---|---|---|
| `camera` | `camera_module` | 相机模组 |
| `encoder` | —（外部） | 编码器通常是 MCU 内建，不单独占元件 |
| `gimbal_pan` / `gimbal_tilt` | `gimbal_servo` | 同模板两个实例，`axis` 区分 |
| `servo_power` | `psu_5v` | 舵机吃 5V；堵转 700mA vs 静态 4mA，需独立供电 |
| `radio` | `mcu_board` | 与 `mcu` 合并（ESP32 自带 Wi-Fi） |
| `mount` | `wall_bracket` | 机械件，无电气引脚 |
| `display_peer` | —（外部） | 手机本身，不占板 |
| `undefined_hw` | —（外部） | 需求不完整的信号，必须原样传到 unresolved |

**三条纪律**：

1. **映射不到就报 `unmatched`，不猜最像的。** 猜错代价是整块板选型错误。
2. **模板 key 从 hwforge 运行时读**，不抄一份清单——抄一份就是第二事实源。
3. **能力校验是"造一遍看引脚"，三态**：`True`/`False`/`None`（无法判断）。
   把"不知道"混进"通过"是这套系统一直在防的事。

`needs` 描述的是**电气能力**（能出 PWM、能吃 5V/2A），不是工厂的形状参数。
第一版拿 `needs=("pwm",)` 去对 `inspect.signature`，一次报出 5 条假问题
（签名里只有 `w_mm`/`d_mm`/`axis`）——检查本身是对的，错的是"消费"的定义。

**它不做什么**：不排坐标、不连线、不出板框。计划是"要装哪些元件、用哪个
模板"，把它当设计看会高估它。

### 装配：计划 → 可渲染的设计（`roles --assemble`）

`roles --assemble <id>` 把计划变成 `DesignModel`（真实坐标 + 网表 + 板框），
之后 2D/3D、物理检查、预算全都能用 —— 那些只看 `DesignModel`。

```bash
# 从需求一路装到设计并落盘
python -m dsh_embagent.design_cli roles --goal "做一个壁挂云台相机" --assemble my-board
```

实测（4 角色 → 4 元件 / 2 网表 / 66×63mm 板 / 9 项检查全过）：

```
CAM1  camera       x=  0.0..  8.0  y= 32.0.. 57.0
SV1   gimbal_pan   x= 14.0.. 34.0  y= 32.0.. 52.0
SV2   gimbal_tilt  x= 40.0.. 60.0  y= 32.0.. 52.0
PS1   servo_power  x= 15.0.. 45.0  y=  0.0.. 20.0
```

**三条纪律**：

1. **引脚名来自模板，不是猜的。** 布局与接线都用模板工厂**实际产出**的
   引脚名（`camera_module` → `5V/GND/DATA`，`gimbal_servo` → `PWM/VCC/GND`）。
   猜一个"应该叫 VCC"的引脚会让 `pins.audit()` 报它不存在，但那时错误
   信息已经离需求很远了。
2. **接不上的线不画，但要说出来。** 找不到电源脚就进 `meta.unrouted`，
   留空比接错强。**一个脚不构成网** —— 单件装配时每个脚都进 `unrouted`。
3. **布局是分带网格，不是优化。** 按功能带（电源/控制/执行/结构）分行，
   带内居中。`meta.layout_note` 明确写"不是优化过的布局"。

**坐标不自己算**：调 `hwforge.assembly_build.build_from_spec()`，由它负责
模板实例化 + 引脚解析（`footprint.resolve_pins` 的 INV-1..6 校验：引脚
严格在体内、不重合、边声明必须真）。本模块只做布局与网表，不绕过那些不变量。

### 独立运行：**不依赖 hwforge**

dsh_embagent **不需要 clone hwforge**。迁移前，需求分析、模板、装配、体素
空间、预算、账本、协同验证、阶梯全部来自 hwforge 的 4366 行代码 ——
没有它，除了 2D 视图什么都做不了。

现在这些能力都是 dsh_embagent 自己的：

| 模块 | 取代 | 行数 |
|---|---|---|
| `design/library.py` | `hwforge.assembly_parts` | 16 个模板（尺寸 + 引脚 + 引脚位置） |
| `design/geometry.py` | `hwforge.space` + `hwforge.statics` | 体素场 + 地面静力学 |
| `design/budget.py` | `hwforge.budget` | 五轨预算 + 耦合组池化 |
| `design/ledger.py` | `hwforge.deferred` + `hwforge.trace` | 账本 + 哈希链 |
| `design/coverify.py` | `hwforge.coverify` + `hwforge.software` | 8 条 CV 检查 |
| `design/tiers.py` | `hwforge.tiers` | 21 级阶梯 |
| `design/toolchain.py` | `hwforge.downstream` | 外部工具探测 |

**验证方式不是看代码**：`TestStandalone` 用一个上下文把 `hwforge` 从
`sys.modules` 里彻底挡掉（`sys.modules["hwforge"] = None`，比改环境变量
更硬 —— 环境变量只影响路径解析），然后跑完整链路：

```
需求 → 角色 → 校验 → 装配 → 物理检查 → 协同验证 → 阶梯 → 场景往返
```

实测：**把 hwforge 目录整个移走，184 个测试全过**，端到端 CLI 正常
（5 元件 / 9 项检查 / O2.12 / 2D+3D 都有）。

**hwforge 变成可选增强**：只有 `hw_block` 工具（跑 hwforge 的 14 个积木）
还用得到它。没有时它返回 `not_ready` 并说明原因，而不是报"未知能力" ——
两者都报会让人去查代码，而问题在环境。

**迁移中发现的两个真 bug**（都是新实现比旧实现更严/更诚实的结果）：

1. **预算的叶子节点从不被判**。原来只比"子节点申报之和 vs 父配额"，
   挂在叶子自己身上的申报从来没和它的配额比过 —— 一个"预算 3W、申报 4W"
   的叶子会静静通过。
2. **0 尺寸的元件让整个设计跑不了检查**。原生 `add_mm` 原本拒绝
   `h_mm=0`（"0 尺寸的盒子不是元件"），而 KiCad 导入的元件**就是没有
   厚度**（PCB 里只有平面）。结果是一个缺失的厚度，把全部物理判定变成
   了"未知"。现在：接受、按最小 1 格处理、**并记进 `zero_size_refs`** ——
   那个件的高度是推断值，不是实测值。

### DC 电学（`design/electrical.py`）

降额表能说"这颗 MOSFET 在 60% Vds"，但**说不出**"5V 轨在堵转时跌到 4.4V，
舵机褐变复位"。这个模块补的就是那块电路：

| ID | 模型 | 抓什么 |
|---|---|---|
| EL-01 | 线损 `I·L·ρ/A` | 细电源线 |
| EL-02 | 稳压器 `P=(Vin-Vout)·I` | 烤掉的 LDO |
| EL-03 | 结温 `Tj = Ta + P·Rθja` | 热失控 |
| EL-04 | 电源 `I·Rout` | 有限输出阻抗的轨跌 |

**实测数值**（不是估计）：1.4A / 1000mm 下 26AWG 压降 0.186V（过），
28AWG 是 0.298V（超 0.25V 预算）—— 两种线都标着"能过 2A"。
5→3.3V @1A 在 SOT-223 里 Tj = 135°C，会热关断；半载 83°C 没问题。

**过载是独立失效**：2A 电源带 2.1A，压降只有 0.19V（电压看着完全正常），
但电源在过载工作。两种失效的修法不同（更硬的电源 vs 更大的电源），
所以不折进同一个布尔。

**局限写在代码里**：只算直流（无电感/纹波/瞬态）、线阻用 20°C 标准值
（温度系数未建模）、Rθja 是典型值不是热仿真。

### 固件源码检查（`design/firmware.py`）

其他检查判的都是**我们建的模型**。对 `App/Src/application.cpp`（别人写的
1703 行）一句话都说不出来。六条文本/结构检查：

| 检查 | 抓什么 |
|---|---|
| `fw_uncalled` | 定义了但文本里没人调 |
| `fw_timeout` | 失联处理路径（标志置 true 无 false） |
| `fw_param` | 配置表声明 N 项、只读了 M 项 |
| `fw_bom` | BOM 值 vs 型号解码值 |
| `fw_dup` | 近乎相同的副本 |
| `fw_marker` | 调试残留 / TODO |

**每条 finding 都写明"看不见什么"** —— "这个函数文本里没人调"不等于
"这是死代码"（可能通过函数指针、中断向量、宏展开调用）。没有这句，
finding 会被当成结论。

**`fw_uncalled` 的计数规则踩过三个坑**，最终是：

```
mentions = 文本里所有 `name(` 出现（含限定调用）
decls    = 以 `;` 结尾 **且前缀像类型表达式**、不是定义的语句
calls    = mentions - decls - defs
```

只看"以 `;` 结尾"会把 `mat_mul_4x4(F, kf->P, F);` 这种**调用语句**
算成声明并减掉，函数看起来就没人调 —— 计数膨胀到 507，把真结果埋了。

**`fw_bom` 的正则也踩过坑**：早先锚在结尾 `(\d{4})[A-Z]?$`，而实测型号
`RS-05K1002FT` 后面还有封装后缀 `FT` —— 一个都没匹配上，检查看起来在跑，
实际永远 0 条。

### 语义树：判定链的解释层（`design/semantics.py`）

**"一个大元件不是原子。"** 舵机被建模成一个盒子 + 3 个引脚，后果是判定链
只知道"死区 5µs → 0.9°"，**不知道这 0.9° 是内部电位器决定的**。

语义树补的是判定链的**上游**——它让 `coverify` 的失败项说出：

```json
{"subsystem": "control_board", "field": "deadband_us", "value": "5 us",
 "why": "舵机控制板的 PWM 解析死区 5 us，经 180° 行程折算成角分辨率",
 "fix_options": ["换更低死区的舵机（数字舵机常见 1-2µs）", "放宽指向精度要求"],
 "cannot_fix_by": "固件 —— 这是模拟量分辨率，不是软件能改善的",
 "source": "datasheet", "trust": 0.7, "caveats": [...]}
```

**`cannot_fix_by` 是最有价值的字段**——它把"硬件下限"变成一条可执行判断，
让调用方不在错误的层浪费时间。

**与 hwforge 版的三处关键改动**：

| 改动 | 为什么 |
|---|---|
| 模板 key → 语义树 key 是**显式表** | 实测直接查 16 个模板只有 **7 个**命中（`camera_module` 查不到 `camera`）。字符串启发式猜不准，所以用能校验的表 |
| specs 是 `Quantity`（标量/范围 + 单位 + 来源） | hwforge 存的是 `"4.8-6.0 V"` 这类字符串，判定层没法比较 |
| 只做被消费的 4 棵 | 其余 40+ 棵树没有消费者，搬过来只是躺着 |

**范围不取中值**：`(1.0, 2.0)ms` 的脉宽取中值 1.5ms 算出来的角度是编的。
`Quantity.scalar()` 对范围返回 `None`，判定层拿不到就报未验证。

**可信度取最弱一环**：`gear_train` 的构成是 `teardown`(0.7)，但里面
`ratio_est` 是 `inferred`(0.3)——链路的强度是 0.3，不能因为一半可信就
把整条当可信。

**覆盖率如实报**：5 棵树覆盖 6/16 个模板，其余 10 个 `tree_for_template()`
返回 `None`——**"未建模"不等于"这个件简单"，是未考证**。

### `psu_5v`：唯一同时被两条判定链消费的树

| 判定链 | 从树里取什么 |
|---|---|
| `electrical.rail_under_load()` | `rated_a`（2A）、`r_out_mohm`（90mΩ）、`v_out`（5V） |
| `coverify` 的 `cv.current` | 电源侧根因（`converter.rated_a`） |

**树是上游，只写一处**。早先 `DEFAULT_INPUTS` 和树里各有一份 2.0A/90mΩ——
改一处不改另一处就漂移。现在 `rails_from_semantics()` 让 `electrical` 来取，
`provenance["rails"]` 记下参数来源。

**`cv.current` 失败时两边原因都报**：

```
负载侧: dc_motor 的 stall_current_ma = 700 mA
电源侧: converter 的 rated_a = 2 A
```

只报一边会误导——"换舵机还是换电源"取决于哪边是瓶颈。

实测（4 个舵机堵转 2.8A 对 2A 电源）：

```
压降后 4.748V | 过载 True | ok False
note: 负载下 4.75V 低于最低 4.80V：…；负载 2.80A 超过电源额定 2.00A：
      电压看着正常，但电源在过载工作，会发热/掉压/寿命骤降
```

**两种失效的修法不同**：压降要更硬的电源，过载要更大的电源。

### 需求 → 设计的其余三层

**R1 · 能力需求表**（`design/requirements.py`）

hwforge 的 `spec.py` 是**关键词表**且只覆盖一个参考案例：

| 需求 | `spec` 产出 |
|---|---|
| `壁挂云台相机 手机看视频` | 6 个角色 |
| `STM32 主控的温湿度采集板` | ❌ `undefined_hw` |

工作台换成**能力表**：能力是封闭集合（compute/image/motion/wireless/
sense/display/power/drive/mount/peer），说法是开放列表。加一种说法只加
一个词，不加一个 `if`。

```
STM32 温湿度采集板        → mcu, sensor
四轮小车 电机 电池 蓝牙    → radio, power_in, motor
壁挂云台相机              → camera, gimbal_pan, gimbal_tilt, mount, mcu
```

两条路**都跑再合并**：本地能力表在前（认得出更多说法），hwforge `spec`
在后（负责软件视图、跨域契约、`LookupRequest`）。

**P0 · 引脚守恒**（`design/assemble.py`）

硬不变量：**每个引脚 ∈ (某个网 ∪ `unrouted`)**。实测事故是
`CAM1.DATA`/`SV1.PWM` 两边都不在 —— 凭空消失。这条不变量还抓到两个
衍生 bug：

* `CAM1.5V` 同时在 `5V` 和 `SIG_CAM_PWR` 两个网里（**一个脚不能属两网**）；
* 单件装配时三个脚全部悬空却没交代。

**R4 · 协同验证**（`design/coverify.py`）

hwforge 的 8 条 CV 检查（需求数值 vs 硬件能力）。它回答**别的检查都不
回答**的问题：需求里写的数字，硬件能否兑现。每条检查**同时给出两个
数字** —— 只说"不够"不给数不是协同验证。

需求数值没声明时进 `not_verified`，**不拿默认值冒充"验过"**。

**R5 · 反向导入**（`design/scene_import.py`）

`design/scene.py` 只做了正向（工作台 → hwforge）。反向让
`hw_block assemble` 装的东西能在 2D/3D 里看：

| hwforge | DesignModel |
|---|---|
| `space.placements[].box`（体素） | `placement` + `size_mm` |
| `wires.runs[]` endpoints | `nets[]` + `pads[]` |
| `space.dims` | `board.size_mm`（**标为工作空间，不是板框**） |

**引脚只从走线端点反推** —— `placements[]` 里没有引脚表，没被碰到的引脚
就没有坐标，这比编一个诚实。

**P3 · 21 级阶梯**（`design/tiers.py`）

把工作台的检查映射到 hwforge 的 `O0..O6` 上，**四种"没验"分开报**：

| outcome | 含义 |
|---|---|
| `verified` | 验过 |
| `failed` | 有检查，没通过 |
| `not_run` | 有检查，这次没跑 |
| `no_checker` | 根本没有工具 |
| `needs_human` | 需要人（样机/HIL） |

**刻意不做两处映射**（会说过头）：`space.net_reach` 不映射到 O1（它只量
距离，证明不了引脚语义），`budget` 不映射到 O2（五轨预算不是降额应力）。

### 多板工程与 BOM 补全

**模型支持多板**（L3）。一个真实项目往往有多块 PCB：

| | 实测（本仓库的立创样例） |
|---|---|
| 源工程板数 | **8 块** |
| 源元件数 | 316 |

```bash
# 只导主板（默认）——其余板如实上报，不静默丢弃
python -m dsh_embagent.design_cli import <id> board.epro2
# 导入全部 8 块板（316 个元件一个不丢）
python -m dsh_embagent.design_cli import <id> board.epro2 --boards all
# 只导某一块
python -m dsh_embagent.design_cli import <id> board.epro2 --boards "PCB#a22effc9"
```

**模型结构**（`design/model.py`）：

| 字段 | 含义 |
|---|---|
| `board` | **主参考板**（始终存在，用于兼容单板路径） |
| `boards` | 全部板（空 = 单板；非空时 `board` 必须是其中**同一个对象**） |
| `Board.id` | 板标识（导入器用 `doc` id，如 `PCB#a22effc9`） |
| `Placement.board_id` | 元件的归属板（空 = 主板） |

**三条不变式**（都有测试守住）：

1. **坐标是各自板的局部坐标**。拼板平移只在 `projection.panel_layout()`
   里做一次 —— 移动一块板不会让上面所有元件的坐标都要改。
2. **Y 翻转用拼板总高度做一次**，不是每块板各翻一次。每块板单独翻会让
   两块板的相对位置镜像错乱。
3. **判定按板分组**。边界检查用**所属板**的轮廓；重叠检查**跨板直接短路**
   （两块板上的元件在物理上不可能碰撞）。实测：不做这一步，8 板工程会报
   **68 条重叠，全部是跨板假碰撞**（同板真碰撞 0 条）。

**拼板排布**（`panel_layout`，确定性）：

* 主板固定在 (0,0)；
* 其余板按**元件数降序、id 升序**，从左到右排，行宽上限 = 主板宽 × 1.6；
* 板间距 `PANEL_GAP_MM = 8mm`，底边对齐。

**跨板飞线不画**：板与板之间的连接不是 PCB 铜线，画出来会是一条横穿
拼板的假线。数量如实记在 `cross_board_ratlines_skipped`。

**`design.meta` 里的多板信息**：

```
source_boards: 8            # 源工程共有几块板
parts_skipped: 175          # 只导主板时丢了多少元件（--boards all 时为 0）
boards_skipped: [{doc, parts, size_mm}, ...]
model_note: "…"
```

**BOM 补全**（`--bom` / `bom` 命令）把物料清单变成看得见的元件：

```
BOM 行 ──> {ref, value, footprint, lcsc}
             ├─ packages.package_size(footprint) ──> w/h/d（38 种标准封装）
             └─ 标号前缀 ──> role
                        ▼
              网格摆位（**避开**已有元件与安装孔）
```

三条纪律，一条都不省：

1. **不覆盖已有元件**——PCB 导入的真实布局比 BOM 排布可信；
2. **位置是排布，不是布局**——`meta.bom_fill.placement == "grid-estimate"`，
   界面标题写"位置为自动排布"，警告里也写一句。照着它布线会出问题；
3. **封装查不到就留 0 + `estimated`**——不编造尺寸。

排不下就**不硬塞**：留在板外会变成 `space.bounds` 假问题，把真正的边界
问题淹掉。排不下的元件进 `meta.bom_fill.unplaced` 并写进 warnings。

### 格式规范（官方，不是猜的）

`D:/IE/ES/PCB/lceda-pro/docs/easyeda-format.md` 与客户端自带
`lceda-pro doc format --class-name <T>`。四条关键规则：

1. **每行是两个 JSON 用 `||` 拼接**：外层 `{type,id,ticket}` + 内层原子数据。
   **行与行之间是 `|\n`** —— 按单 `|` 切分会错位（第一版正是如此，
   结果"解析成功"但坐标与网表全丢）。
2. **最终一致性**：同 `type`+`id` 保留 `ticket` 更大的记录；删除是内层 `""`。
3. **单位**：原理图（`SCH_PAGE`/`SYMBOL`）用 **10 mil**；PCB
   （`PCB`/`FOOTPRINT`）用 **mil**。旋转逆时针为正。
4. **归属规则三种各不相同**（最易接错的地方）：

| 记录 | 归属方式 |
|---|---|
| `ATTR` | 有复合 id（`e0-e18`），真正的 parent 在**内层 `parentId`** |
| `COMPONENT` | 外层 id 即元件 id（`e0`） |
| `PAD_NET` | 键式 id `["PAD_NET", 元件id, 焊盘号, 焊盘id]` → 载荷 `padNet` |

把 `ATTR` 的外层 id 当 parent（第一版），645 条 Designator 一条都匹配不上。

### 实际能力（A + B 已实现）

| 数据 | 状态 | 来源 |
|---|---|---|
| 元件标号 | ✅ 273/273 | PCB 的 `ATTR` `Designator` |
| **元件坐标** | ✅ **真实**（mil → mm） | `COMPONENT.x/y` |
| **旋转角度** | ✅ 真实（逆时针为正） | `COMPONENT.angle` |
| **网络名 + 引脚归属** | ✅ 406 网络 / 1484 焊盘 | `PAD_NET` 键式 id |
| **板框尺寸** | ✅ 真实（70×45 mm） | `POLY.polyType=BOARD_OUTLINE` |
| **元件值（1uF/10K）** | ✅ 部分（65/273） | 原理图 `Value`，按 `Designator` 桥接 |
| **元件尺寸（如 C0603）** | ✅ 部分（103/273，62 个有高度） | `3D Model Transform` 前 3 项 = sizeX/Y/Z（**mil**） |

### 值与尺寸是怎么解出来的

PCB 元件只引用 `Device` 哈希，真实值与尺寸在**原理图侧**（`SCH_PAGE` 文档）：

| 数据 | 记录 | 说明 |
|---|---|---|
| 值 | `ATTR key=Value` | `1uF` / `100nF` / `237kΩ` |
| 尺寸 | `ATTR key=3D Model Transform` | 9 个逗号分隔参数，前三个是 sizeX/Y/Z，**单位 mil** |
| 高度 | `ATTR key=3D Model Title` | 封装名里的 `H0.8`（Transform 的 sizeZ 实测恒为 0） |

两者靠 **`Designator`（C1 / R23 / U29）** 桥接 —— PCB 与原理图各有标号，
实测有 256 个共有标号。

精度验证：C0603 → `62.992 mil × 0.0254 = 1.600 mm`；C0402 → `1.000 mm`；
LQFP-64 → `12.000 mm`。都是库里的真实包围盒。

**值/尺寸的 provenance 分开标**：有 Transform 的标 `derived`（由 3D 模型
推导），没有的标 `estimated` 且尺寸为 0 —— **不编造 datasheet 数值**。

### 一个真实的假发现及其修复

第一版只按标号前缀判角色，于是 `RF1`（SMA 座）被当成普通元件，
`space.bounds` 报「足迹超出板轮廓 3.92 mm」。而**连接器骑在板边是正常设计**。
现在角色**先看封装名**（`CONN`/`SMA`/`WAFER`/`USB`…），再看前缀 ——
这份工程里 `U1` 实际是 WAFER-MX1.25 排针，靠前缀永远判不对。

**拼板处理**：这个工程有 11 个 PCB 文档、7 块板框、273 个元件分布在各板。
导入时取**元件最多**的那块板（79 个元件 / 70×45 mm），其余如实报告为未导入。
把全部元件塞进一块板框会让 213 个"跑到板外"——那不是布局错误，是建模错误。

**尺寸未知仍标 `estimated`**，绝不编造 datasheet 数值。`checks.json` 的
`estimated`/`vacuous` 会如实标出，`space.overlap` 这类检查在补全尺寸前
无法真正判定。

## 工作区条目 vs 工作台工程

**两层概念，不要混。** 只把"含 `design.json` 的目录"当工程，会让真实项目隐形：

| 层 | 判据 | 例子 |
|---|---|---|
| 工作区条目 | `.ioc` / `.kicad_pcb` / `BOM*.xlsx` / 固件 / `.param` / `design.json` | `F103-GPS-master`（30 源文件 + STM32F103C8Tx） |
| 工作台工程 | 有 `design.json` | `001-blink` |
| 适配缺口 | 是条目但不是工程 → `not_adopted` | 上表 21 个 |

`not_adopted` **不是** `empty`：前者"有内容没接入"，后者"真的空"。混成一个词
会把 423 个源文件说成"什么都没有"——这是"难以评审"的直接原因。

`.ioc` 里的 `Mcu.UserName` 是全工作区**最硬的硬件事实**（`STM32F103C8Tx`），
比板卡预设的 `board.kind` 可靠。识别层据此判定条目类型与芯片型号。

## 日志与读取覆盖度

日志写在工作区 `<工作区>/logs/`，**只追加**：

| 文件 | 内容 |
|---|---|
| `activity.md` | 动作流水（最新在顶部，永不重写） |
| `survey-<时间戳>.md` | 每次勘察一份报告（人读） |
| `coverage-<时间戳>.json` | 读取覆盖度（机器读，面板进度条用） |

**覆盖度是这套东西存在的首要理由**：勘察器只报告它数到的东西，用户就
以为"评审过了"。实际情况是一份 2085 个文件的工作区里，只有 19 个被真正
打开过（`.ioc` / BOM / 飞控参数），2066 个文件没读——**源码与固件只数了
数量与体积**。日志把这条边界显式写出来，"哪些看了、哪些没看"是清单而不
是形容词。

`logs/` 被排除在勘察之外：不排除的话，每写一份报告文件总数就 +1，覆盖度
从 19/2085 漂到 19/2088——**观测行为改变了观测对象**。

## 从需求到仿真（P1–P4）

四条链路，每条都有可验证的出口：

### P1 引脚校验 —— 网表从"声明"变成"事实"

`Part` 有 `pins[]` 字段，`validate()` 校验网表里的引脚名。三档来源：

| 来源 | 含义 | 可信度 |
|---|---|---|
| `declared` | 元件自己声明（导入器从 `PAD_NET` 实测得到） | 最高 |
| `convention` | 行业约定（LED 的 `A`/`K`、电阻的 `1`/`2`、Arduino 的 `D0..D13`） | 中 |
| `unknown` | 没有声明也没有约定 → **不校验**，报"未核对" | — |

第三档刻意不猜：给一个猜出来的引脚表等于把"未验证"伪装成"已验证"。
实测修正：编造 `U1.NOT_A_PIN` 现在会被 `validate()` 拦住并列出候选引脚。

### P2 `design.json` → `scene.json` —— 打通 hwforge

```bash
python -m dsh_embagent.design_cli scene <id> [--pitch 0.5]
```

之后 `hw_block(simulate)` / `electrical` / `coverify` 都能读。两处如实标注：

- `wires.estimated=true`、`route_note`：端点只到**元件中心**（没有焊盘坐标），
  `length` 是中心距的体素化，**不是走线长度**；
- 没有 `electrical.rail` 时 `unverified` 明说"负载只能算已知件，是**下界**"。

### P3 需求 → 设计（模板）

```bash
python -m dsh_embagent.design_cli synth <id> blink --spec '{"pin":"D13"}'
```

| 模板 | 组成 |
|---|---|
| `blink` | MCU + R + LED |
| `button` | MCU + 按键 + 下拉电阻 |
| `led-bar` | MCU + n×(R+LED) |
| `i2c-sensor` | MCU + BME280/SSD1306/MPU6050（VCC/GND/SCL/SDA） |
| `ultrasonic` | MCU + HC-SR04 |
| `servo` | MCU + SG90 |
| `buzzer` | MCU + 蜂鸣器 |

**为什么是模板而不是自由生成**：模板保证每条网表的引脚名都真实存在，
组装完立刻用 `pins.audit()` 自检。模型只负责"选模板 + 填空"，
而不是自己发明连线。

覆盖范围有限且写在 `meta.scope_note` 里：模拟电路、电源树综合、多板互联、
阻抗/EMC 相关布局**都没有自动路径**。

### P4 `put` 也自动锁 `mcu_family`

`new`/`import` 会锁，`put` 之前漏了 → 用 `put` 建的设计锁完 4 个决策仍被
导出闸门拦住。已修。

## AI 协作（一个入口，三个阶段）

工具栏的「AI 协作」是一个入口，三个动作都往**同一条会话**里发；`初审.md`
是它们之间的接力棒。

| 阶段 | 动作 | 谁做判定 |
|---|---|---|
| 理解 | 初审工作区 → 刷新 `初审.md` | 事实段由 `hw_survey` **确定性生成**；判断段由 AI 写 |
| 适配 | 适配当前工程 → 补齐可视化所需的数据 | 尺寸查不到的**留 0 + `estimated`**，不编造 datasheet |
| 需求 | 输入框里描述要改什么 | AI 调工具改工作区，走 `hw_put`/`hw_setpart` |

### 为什么初审.md 要拆成两段

`<!-- hwv-survey:begin -->` 与 `<!-- …:end -->` 之间是**机器事实**（工程清单、
阶段、工程外文件、扩展名分布、指纹），由 `design/survey.py` 生成，重写时
只替换这一段。外面的「判断」段是 AI 的，重写会被保留。

让 AI 一次写完整份文件是不行的：它会把「我看到 30 个 .bin」写成「完成度约
70%」，而这句话落盘后，下一轮 AI 又会把它当**事实**引用——错误会被放大而
不是纠正。指纹（`sha256:…`）还能回答「这份初审是否已经过期」。

### 阶段是确定性推导，不是 AI 判断

`design/phase.py` 只看设计事实：

| 阶段 | 判据 |
|---|---|
| `empty` | 一个元件都没有 → 所有物理检查都会因空集而通过（**假绿灯**） |
| `partial` | 有元件，但尺寸缺失/估算、高度未知、或不可逆决策未闭合 |
| `complete` | 元件齐全、尺寸都有出处、不可逆决策已闭合 |
| `broken` | `design.json` 读不出来 |

`checks.json` 的 `vacuous` 字段列出**因为空集而通过**的检查，界面单列成
「N 项空集通过」的徽章。没有它，`parts: []` 的工程会显示「9 项全部通过」。

### 工具集

只读：`hw_projects` `hw_snapshot`；判定：`hw_check` `hw_budget` `hw_crosscheck`
`hw_export` `hw_block`；AI 协作：`hw_survey` `hw_new` `hw_put` `hw_setpart`
`hw_deferred` `hw_workspace` `hw_import` `hw_log`。

**`hw_put` 是唯一受校验的设计写入路径**（走 `DesignModel.from_dict` +
`validate()`，回报 `problems`）。不要用通用文件工具直接写 `design.json`——
那条路绕过校验，会把不合法的设计原样留在磁盘上。设计正文走 **stdin**
而不是 argv：Windows 命令行上限 32767 字符，截断后的 JSON 仍然"能解析"，
后果是静默写入一份残缺设计。


```bash
python -m dsh_embagent.design_cli workspace show          # 当前根 + 来源 + 可写性
python -m dsh_embagent.design_cli workspace set <绝对路径>   # 接已有目录
python -m dsh_embagent.design_cli workspace create <父目录> <名>  # 建子目录并切换
```

## 快速开始

```bash
# 依赖（一次）
cd dsh_embagent && npm install --legacy-peer-deps

# 构建：宿主半（tsc）+ 客户端半（tsdown，three.js 内联）
npm run build

# 类型检查（两半分别查：宿主用 node 类型，客户端用 DOM+JSX）
npm run check

# 客户端 bundle 冒烟：伪宿主里注册视图并渲染一次（不需要浏览器）
node scripts/check_client_runtime.mjs

# 外壳行为冒烟：把手/隐藏即卸载/segmented/持久化读写两侧与降级
# 含阶段徽章、空集通过徽章、AI 面板收起/展开
node scripts/check_layout_runtime.mjs

# 工具注册：AI 协作七件套都在、都写了描述、写操作都标了"写"
node scripts/check_tools.mjs

# 设计写入走 stdin 且不被 Windows 32K 命令行上限截断
node scripts/check_put_stdin.mjs

# Remote 端点签名（对编译产物跑宿主的解析算法）
node scripts/check_remote_signature.mjs

# 2D 画布**真实绘制**自检：挂真 Circuit2D 到无头 Edge，量真 canvas 像素
# （复刻绘制逻辑的检查证明不了"真组件画对了"——上一版就是这么漏的）
cd .. && node dsh_embagent/scripts/check_canvas_draw.mjs

# 3D 视图**真实渲染**自检：挂真 Assembly3D，截 WebGL 画面量亮度/颜色直方图
cd .. && node dsh_embagent/scripts/check_assembly3d.mjs

# 布局自检：真浏览器里量画布容器尺寸/DPR/自适应缩放
cd .. && node dsh_embagent/scripts/check_layout_browser.mjs

# 2D 投影几何自检（不依赖浏览器）

# 样式与组件同步（死规则双向扫描）
cd .. && python dsh_embagent/scripts/check_dead_css.py

# 自检：产物形状 + 上述冒烟 + 宿主契约（重读 app.asar）+ Python 端到端
cd .. && python dsh_embagent/scripts/check_plugin.py

# 安装持久性：manifest / lockfile / 组合树 / 产物 / **宿主是否在跑旧代码**
python dsh_embagent/scripts/check_installed.py

# 单元测试（215 个用例，标准库 unittest）
python -m unittest dsh_embagent.tests.test_design -v
```

仓库里已预置两个工程，打开视图即可看到内容：

| 工程 | 来源 | 内容 |
|---|---|---|
| `001-blink` | Wokwi diagram 导入 | 3 元件 / 5 网表（含板卡引脚） |
| `002-sensor-node` | KiCad PCB + BOM | 3 封装 / 板轮廓 40×30mm / LCSC |

## 装进 DSH（已安装到本机 desktop profile）

安装走的是宿主自己的通道，不是手改运行中的组合树：

1. `~/.dsh/profiles/desktop/package.json`：`dependencies` 加
   `dsh-embagent: file:<仓库根>/dsh_embagent`，`dsh.profile.bundles` 追加同名
   条目（原文件备份为 `package.json.bak-embagent-*`）；
2. profile 目录下用运行时自带的 pnpm 安装（产物在 profile 的
   `node_modules/dsh-embagent/` 下，**`<仓库根>/dsh_embagent` 源码原样保留**）；
3. `plugin_manager set_bundle dsh-embagent` 启用——一次失败暴露了真实契约：
   运行中的宿主是 **0.2.2-rc.1** 代次，而依赖树里装的 rc.6 `dsh-tools` 导入了
   rc.6 自己的 `dsh-llm`；宿主解析一侧到 app 的 0.2.2 `dsh-llm`，`CallId`
   不存在 → `failed to import`。把全部 `@deepseek-ai/*` 依赖钉到与宿主一致的
   `0.2.2-rc.1`（cordis 4.1.0）后重建，`application: applied`。

验证（三条互相独立）：

- `dsh --profile desktop --dump-config`：树里出现 `id: embagent / name: dsh-embagent`；
- `plugin_manager list_plugins`：`include:embagent` **enabled, fiberPhase=active**；
- `scripts/check_host_runtime.mjs` / `check_client_runtime.mjs`：真实 cordis 里
  apply 不抛、23 个远程端点齐全、8 个工具注册；伪宿主里 bundle 注册并渲染。

浏览器侧：GUI 会话视图标签栏出现「硬件工作台」（Chat / Trajectory 之后，
order=20）。若标签未出现，刷新一次页面即可——客户端 bundle 是连接时拉取的。

**主题跟随系统**：面板 chrome 引用宿主的 `--dsw-alias-*` 令牌（`ui-theme`
在 preference=system 时跟随 `prefers-color-scheme`，OS 翻转即生效）；canvas
与 three.js 不跟 CSS，由 `src/client/theme.ts` 监听 `body[data-ds-dark-theme]`
翻转后命令式重读字面调色板并重绘。改这些后**刷新页面即可**（客户端 bundle
连接时拉取；本次改动不涉及 host 半，无需 recomposition）。

**版本纪律**：本插件必须与宿主同代次（当前 `0.2.2-rc.1`，见 package.json 里
钉死的 devDependencies）。宿主升级后要对齐依赖再 `npm install && npm run build`，
否则会在 `dsh-llm` 这类共享包上出现"导出缺失"式的加载失败。

## 结构

```
dsh_embagent/
├── design/                 规范模型（Python，唯一事实源）
│   ├── model.py            DesignModel / Part / Placement / Board（mm，**多板**）
│   ├── boards.py           板卡预设（Velxio 像素 ↔ 毫米，含来源标签）
│   ├── catalog.py          常见元件尺寸表（datasheet / estimated）
│   ├── projection.py       mm ↔ px 投影 + **拼板排布**；Y 翻转只做一次
│   ├── importers.py        KiCad PCB（Edge.Cuts+封装+焊盘）/ BOM / Wokwi
│   ├── physical.py         体素求解 + 9 类检查（**按板分组**：跨板不判重叠）
│   ├── budgets.py          五轨预算（复用 hwforge.budget 代数）
│   ├── discipline.py       未决决策闸门 + append-only 轨迹（复用 hwforge）
│   ├── crosscheck.py       画图 vs 事实 + 2D↔3D 往返一致性
│   ├── fabrication.py      BOM/坐标/DXF/模型 + README（不伪造 Gerber）
│   ├── easyeda.py          立创 EDA 解析（焊盘/走线/层，按文档作用域去重）
│   ├── easyeda_import.py   立创工程 → 设计模型（含丢弃板上报）
│   ├── packages.py         38 种标准封装的尺寸/高度（本体，不是焊盘跨距）
│   ├── ratlines.py         网表 → 飞线（MST，端点用真实焊盘坐标）
│   ├── bom_fill.py         BOM → 元件（避让已有元件与安装孔）
│   ├── roles.py            需求角色 → 装配模板（R2 映射表 + 校验器）
│   ├── assemble.py         装配计划 → 设计（R3：坐标/网表/板框）
│   ├── requirements.py     能力需求表：任意需求 → 角色（R1 通用化）
│   ├── coverify.py         需求数值 vs 硬件能力（8 条 CV 检查）
│   ├── library.py          元件模板库（16 个模板 + 引脚，本地）
│   ├── geometry.py         体素空间 + 静力学（本地）
│   ├── budget.py           五轨预算代数（本地）
│   ├── ledger.py           未决决策账本 + 哈希链轨迹（本地）
│   ├── toolchain.py        外部工具探测（tscircuit / Renode）
│   ├── electrical.py       DC 电学：压降/轨跌/结温（EL-01..05）
│   ├── firmware.py         固件源码检查（6 条文本/结构）
│   ├── semantics.py        语义树：判定链的解释层（5 棵结构化树）
│   ├── scene_import.py     hwforge scene.json → DesignModel（反向）
│   ├── tiers.py            21 级阶梯：哪些验过/没跑/无检查/需人
│   └── store.py            project/<id>/ 原子写入
├── design_cli.py           Python 入口：一次调用、一行 JSON
├── bridge.py               hwforge 积木 + Velxio HTTP 能力
├── velxio_client.py        Velxio 后端薄客户端
├── src/                    宿主半（TypeScript）
│   ├── bridge.ts           进程调用 + 最后一行 JSON 解析
│   ├── service.ts          Typert Remote 端点（hwvelxio/*）
│   ├── tools.ts            8 个模型工具（hw_*）
│   └── shared/types.ts     宿主/客户端共享的纯类型
├── src/client/             客户端半（React）
│   ├── view.ts             conversation.view 注册（唯一的契约落点）
│   ├── HwIdeView.tsx       四栏 IDE 外壳
│   ├── Circuit2D.tsx       canvas 2D（拖拽/旋转/平移/缩放）
│   ├── Assembly3D.tsx      three.js 场景（挤出板/实体盒/线缆管/剖面）
│   ├── Inspector.tsx       选中对象属性与来源编辑
│   ├── panels.tsx          七个底部面板
│   └── store.ts            一个 store：2D/3D 共享 selectedRef
├── examples/               KiCad / Wokwi / BOM 夹具
├── project/                工程产物（design.json、trace.json、fab/…）
├── scripts/check_plugin.py 自检
└── tests/test_design.py   215 个用例
```

## 数据流（一次读 = 一次往返）

`hwvelxio/snapshot` 一次返回：规范模型、2D 投影、3D 场景、物理检查、预算、
未决闸门、交叉核对、证据哈希。客户端不重算任何判定——它只显示与操作。

编辑（移动/旋转/加删元件）也走 Python：`hwvelxio/movePart` 等端点会调
`design_cli`，校验只有一份。TS 侧再写一遍校验就是第二个事实源。

## 模型工具（模型可见，有调用卡）

| 工具 | 作用 | 写 |
|---|---|---|
| `hw_projects` | 工程列表与进度 | 否 |
| `hw_snapshot` | 判定/预算/闸门摘要 | 否 |
| `hw_check` | 跑物理检查 | 是 |
| `hw_import` | 导入 KiCad/Wokwi/BOM | 是 |
| `hw_budget` | 五轨预算 | 是 |
| `hw_crosscheck` | 交叉核对 + 2D↔3D | 是 |
| `hw_export` | 制造包（受闸门约束） | 是 |
| `hw_block` | 跑一个 hwforge 积木 | 是 |

## 实施状态（对照方案-v3 的 P1–P7）

| 阶段 | 状态 | 证据 |
|---|---|---|
| P0 桥接层 | ✅ | `bridge status/catalog/run` 通过 |
| P1 统一模型 + 投影 + 旧工程兼容 | ✅ | `design/model.py`、`projection.py`；用例 `TestProjection` |
| P2 3D 网格 + 2D/3D 选中联动 | ✅ | `Assembly3D.tsx`（three.js）、共享 `selectedRef` |
| P3 EDA 导入 | ✅ 部分 | KiCad PCB/BOM/Wokwi 已通；STEP、原理图网表未做（如实报） |
| P4 物理检查叠加 | ✅ | `physical.py` + 3D 冲突着色 + 面板定位 |
| P5 预算 + 过程纪律 | ✅ | `budgets.py` / `discipline.py`；闸门拦导出 |
| P6 制造 + 核对 | ✅ | `fabrication.py` / `crosscheck.py`；DXF/BOM/坐标 |
| P7 AI 动作 | ✅ | 「AI 评审」→ `session.prompt`，只递事实不递判定 |

## 边界（写死在代码里，不是口号）

- **不伪造数据**：缺尺寸 → `estimated` 并单列；缺高度 → 薄片 + 警告；
  预算无 claim → `confidence: none`（不是"通过"）；工具缺失 → `not_ready`。
- **不伪造 Gerber**：模型没有真实焊盘与布线，制造包只导出能自洽的内容，
  缺项写进 `README.txt` 与 `missing`。
- **不可绕过闸门**：不可逆决策未闭合时，导出端点与工具都直接失败并说明原因。
- **旧画布换算只做一次**：统一 `px_per_mm`，加载时提示"已按 X px/mm 归一"；
  视觉可能轻微缩放，换取 2D/3D 同源。
- **hwforge 无 LICENSE 文件**：商业/闭源分发前必须补齐。

## 配置

`~/.dsh/hw_velxio.json`（可选）：

```json
{ "hwforge_root": "<仓库根>/hwforge", "velxio_url": "http://127.0.0.1:3080" }
```

环境变量：`HWFORGE_ROOT`、`DSH_EMBAGENT_ROOT`（仓库根）、`DSH_EMBAGENT_PYTHON`
（解释器）、`DSH_EMBAGENT_VELXIO_URL`、`DSH_EMBAGENT_VELXIO_TOKEN`。

## 许可

本插件 **MIT**（见 [LICENSE](LICENSE)）。

它**不捆绑**第三方组件：Velxio（AGPLv3 + 商业双许可）、hwforge（无 LICENSE）、
立创 EDA（商业软件）都只在运行时可选择地通过进程 / HTTP 调用，需自行安装。
**MIT 不覆盖它们**，各自的分发条款见 [NOTICE](NOTICE)。
