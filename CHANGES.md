# CHANGES — hw_velxio 变更对照表

> **用途：快速对照"某次改动动了什么"**，不用翻 git log 猜。
> 每条记：改了什么 / 为什么 / 怎么验 / 回归是否全绿。
>
> 格式约定：
> * 一个阶段一节，节内按"新增 / 修改 / 删除 / 不动"分组
> * 每个改动都写**验收命令**，不写"已完成"这种无法复核的话
> * 数字都来自实测，不写估算

---

## P15 — 多域图（2026-11-20）

**目标**：给 `design.json` 增加机械域（关系 / 时序 / 质量属性），
**不升 schema、旧文件零污染**。

**依据**：`docs/execution-plan-v3.md` P15 · `docs/architecture-v3-unified.md` L1

### 新增

| 文件 | 内容 | 行数 |
|---|---|---|
| `design/joints.py` | 机械关系层：推断 + 校验 + 落地 + 报告 | ~640 |
| `scripts/check_m15.py` | P15 验收（六组，含 5 个反例夹具） | ~430 |

### 修改

| 文件 | 改动 |
|---|---|
| `design/model.py` | 加 `Joint` / `Program` 两个 dataclass；`Part` 加 `mass_g` / `com_mm` / `inertia`；`DesignModel` 加 `joints_list` / `program` / `sim_params` / `physical_tests`；**全部条件写出** |
| `design/store.py` | `ARTIFACTS` 加 `joints.json` |
| `design_cli.py` | 加 `joints` 命令（`--infer` / `--promote` / `--apply`） |
| `src/tools.ts` | 加 `hw_joints` 工具（24 → 25 个） |
| `scripts/check_m15.py` | — |

### 新增的数据字段（**全部可选，空值不写**）

```python
# Part
mass_g: float | None = None          # None = 没人称过，不是 0 g
com_mm: list[float] | None = None    # 3 维
inertia: list[float] | None = None   # 6 分量

# DesignModel
joints_list: list[dict] = []         # Joint 表
program: dict | None = None          # 开环时序
sim_params: dict = {}                # 仿真器参数（对应 RoboBuilder Params）
physical_tests: list = []            # 真实测试（P21 闭环用）
```

### 关键设计决定（都带实测依据）

1. **`mass_g=None` 与 `mass_g=0.0` 分开**
   * 0 g 的零件在 MuJoCo 里让质量矩阵奇异
   * `to_dict` 用 `is not None` 判断，不用真值判断（否则 0 被吞）

2. **`Joint` 是独立表，不是 `Part` 属性**
   * 同一条关系属于两端，挂任一端都会让"另一端删了"变悬空引用
   * 单列表 + 两端 `ref`，V0 的引用完整性检查能统一覆盖

3. **`dof` 与 `params` 分工**
   * `dof` 直接翻译成 MuJoCo 关节类型
   * `params` 是 kind 专属（gear 的 ratio / axle 的 diameter），**缺就报 error，不编默认值**

4. **推断只做 `stack` / `port`，不做 `contact`**
   * 板内相邻元件"接触"是正常的（141 件就是上万条假关系）
   * 真正的"松动配合"需要间隙数据，现在没有

### 推断：三次实测踩坑与修正

| 版本 | 判据 | 实测结果 | 修正 |
|---|---|---|---|
| v1 | `^SPI` 判主控 | `Lora1`（射频模块）被判成主控 | 改为只看「主控独有信号」 |
| v2 | `^BOOT0$` 判主控 | `C89`（BOOT 电路电容）被判成主控 | **改为全局判定：连得最多的才是** |
| v3 | 逐件判 | 用 **GND 网**推 port，产出 38 条假关系 | **电源/地网排除** |
| v4 | 网名判外设（1 条即算） | 去耦电容 `C90` 被判成"传感器" | 门槛提到 ≥2 条 |
| v5 | b 端可以是任意件 | `U1 → C90`（滤波电容当被驱动对象） | **b 端排除无源件** |

最终 `示例-单板` 的推断结果：

```
主控 = U1（唯一，靠 BOOT0/NRST/OSC_IN/OSC_OUT 全局判定）
port = 1 条（U1 → LED1，网 LED2812，WS2812 可寻址灯）
skipped = 13 条（11 条「只有无源件」，2 条「只有主控」）
```

**1 条不是失败**——`示例-单板` 的 `ADC_BAT` / `ADC_LEFT_X` 这些网上，
除 U1 外只有分压电阻和滤波电容，**看不出被驱动的是什么**。
如实报 skipped 比硬凑 17 条假 port 好（那些假 port 会污染 P20 的固件核对）。

### 验收

```bash
cd <repo-root>
python -m hw_velxio.scripts.check_m15     # P15 六组验收
```

覆盖：
1. 5 个真实工程往返逐字节一致、**未新增任何键**
2. 带多域字段的 round-trip + 7 条字段校验（负质量/时序倒流/未知 kind…）
3. stack 推断（含"同层不算叠放""间距 50mm 不判"两个反例）
4. port 推断（含 5 个反例：GND 网 / 只有无源件 / BOOT0 上的电容 / 连 SPI 的模块 / 无主控）
5. 闸门（`inferred` 不落地）+ 出口（`promote` 才落地）
6. 空表 `not_ready`、缺参数 `error`、悬空引用 `error`

**退化验证**：把 `apply(only_declared=)` 的闸门去掉 →
3 条断言立刻变红（`added=1`、`promoted` 计数错、写入的标 inferred）。
恢复后全绿。**这条证明闸门有专门的"生效"断言**（AGENTS §1.6）。

### 回归

```
python -m unittest hw_velxio.tests.test_design       266 项 OK
check_m15 / check_m1 / check_units_links / check_generate
check_m3 / check_ladder / check_justify / check_handoff   全部通过
npm run check → 0 错；build OK；check_tools OK
```

**旧文件零污染**：5 个真实工程 `to_dict` 里没有出现任何新键。

### 不动

* `physical.py` / `electrical.py` / `coverify.py` 的判定逻辑（一行未改）
* schema 版本（仍是 `/1`）
* 266 项既有测试的断言

---

## P16 — MuJoCo 后端 + 全量参数表（2026-11-20）

**目标**：把 RoboBuilder 的**完整 136 项物理参数**搬进来（用户要求"全搬、更通用化"），
并接上 MuJoCo，使运动域可仿真。**环境只装在项目内**（`vendor/pylib`）。

**依据**：`docs/execution-plan-v3.md` P16

### 环境（不污染全局）

```bash
python -m pip install --target workspace\vendor\pylib \
  workspace\mujoco-3.12.0-cp311-cp311-win_amd64.whl
# 装上：mujoco 3.12.0 + numpy 2.4.6 + glfw/pyopengl/absl-py/etils/fsspec/zipp
# 当前解释器 Python 3.11.15，wheel 是 cp311 → 匹配
```

`design/mujoco_backend.py` 的 `_ensure_path()` 把该目录加进 `sys.path`，
**只在用时加载**——没装 MuJoCo 的机器上其余 300+ 功能照常。

### 新增

| 文件 | 内容 | 规模 |
|---|---|---|
| `design/sim_params_default.json` | **136 项**参数，16 个物理域，每项带 unit/note/universal/source | 生成产物 |
| `design/sim_params.py` | 参数集：加载/分组/覆盖/diff/信任级 | ~330 行 |
| `design/mjcf.py` | DesignModel → MJCF（12 种 Joint kind 映射、weld 模式） | ~640 行 |
| `design/mujoco_backend.py` | 编译/跑/轨迹/落地测试/悬停测试 | ~370 行 |
| `design/sim_dispatch.py` | 六域分派（电/几何/运动/制造/热/EMC） | ~230 行 |
| `scripts/gen_sim_params.py` | 从 RoboBuilder 全量搬运参数的一次性生成器 | ~230 行 |
| `scripts/check_m16.py` | P16 验收（10 组，含 4 个反例） | ~480 行 |

### 修改

| 文件 | 改动 |
|---|---|
| `design_cli.py` | 加 `sim` 命令（`--domain` / `--duration` / `--weld` / `--params`） |
| `design/store.py` | `ARTIFACTS` 加 `sim.json` |
| `src/tools.ts` | 加 `hw_sim`（25 → 26 个工具） |
| `package.json` | 加 `verify:m16` |

### 136 项参数：全搬 + 按域分组

| 域 | 项数 | 域 | 项数 |
|---|---|---|---|
| mesh（齿轮） | 18 | chain（链条） | 6 |
| stud（凸柱） | 17 | solver | 5 |
| tyre（轮胎） | 13 | course（场地） | 5 |
| axle（轴） | 12 | pullback（发条） | 5 |
| motor（电机） | 11 | tread（履带） | 5 |
| cable（线缆） | 10 | rack（齿条） | 4 |
| cord（绳索） | 10 | mechanism | 4 |
| servo（舵机） | 8 | program | 3 |

**通用性靠标记，不靠裁剪**：`universal=True` 31 项（dt/mu/接触软度/电机），
`universal=False` 105 项（stud 只对凸柱连接、mesh 只对齿轮、chain 只对链条）。
**不删**——同一套代码要能仿真乐高式结构和 PCB；删了以后要加回来时
"这个参数什么含义"就得重查源码。

### 四个实测踩坑（全部是"跑起来了但结果是错的"）

| # | 症状 | 根因 | 修法 |
|---|---|---|---|
| 1 | 所有零件位置 ×1000 | `placement.x_mm` 直接写进 `pos`，MuJoCo 按**米**解释 | 显式 `MM = 1e-3` 常量；验收断言 `pos="0 0 0.1"` |
| 2 | 关节全部消失 | 先拼 `world`、再改 `body.xml`，改的是另一个对象 | 顺序改为：建 body → 插关节 → **最后**拼 world |
| 3 | `QACC` 爆炸，末速 **61574 m/s** | 0402 电容估算质量 0.0004g → 质量矩阵奇异 | **最小质量下限 0.01g**；超小件标 `mass_floor` warning |
| 4 | `repeated name 'C1_g'` / `body 'C1' not found` | weld 模式下零件既拍平进板、又被单独拼进 world；`_excludes` 引用零件名 | 加 `in_world` 标记；weld 模式不生成 excludes |

**第 3 条引出了 P16 最重要的物理模型修正**：

PCB 上的元件是**焊死的**。`free_bodies=True` 让 141 个元件各自自由下落，
物理上就是错的。加 `weld_parts=True`：**每块板一个刚体**，板上零件是它的
子 geom。同一模型再跑：末速 **0.0 m/s**、倾角 1.9°。

三种模式（必须显式选）：

```
weld=False, free=False  → 全部焊接在世界（纯几何检查）
weld=True,  free=True   → 每块板自由（整机动学）← PCB 的正确模型
weld=False, free=True   → 每个零件自由（散件拼装，RoboBuilder 那种场景）
```

### 验收

```bash
python -m hw_velxio.scripts.check_m16
```

**两条止损线**（计划里写死，不过就停）：

| 止损线 | 实测 |
|---|---|
| 单件落地（100g 方块从 0.1m 落下） | z_end = **0.0104m** ≈ 半边长 0.01m ✓ |
| 四旋翼悬停（推力=重力） | 2 秒内波动 **0.0m** ✓ |

其余八组：136 项全在 / 16 域合法 / universal 标记 / diff 识别 `turned_on` /
单位 mm→m / 可编译 / `inferred` 关节闸门 / 缺参数缺尺寸进 skipped /
确定性 / 域分派 unknown / weld 稳定性 / 质量下限 / 真实工程 weld 后稳定。

**退化验证（两条，都有效）**：

| 去掉 | 断言变红 |
|---|---|
| `inferred` 关节闸门 | 2 条（"inferred 进了仿真"） |
| mm→m 换算 | 2 条，落地高度 **z_end=1254m**——仿真照跑不报错，只是结果荒谬 |

第二条特别值得记：**单位错了仿真不会报错**，这正说明"落地停在半边长"
这种物理断言的价值——它抓的是没有异常的错误。

### 回归

```
266 项单测 OK
check_m16 / check_m15 / check_m1 / check_units_links / check_generate
check_m3 / check_ladder / check_justify / check_handoff    全部通过
npm run check → 0 错；build OK；check_tools OK（26 个工具）
```

### 已知限制（P16 不承诺的）

| 限制 | 说明 |
|---|---|
| 所有 136 项都是 `source=public` | 从 RoboBuilder 公开数据搬来，**不是我们实测的**。可信比例 0%。参与硬判定前要 P21 的 promote |
| 碰撞体全是 box | 没有真实 STEP/网格。对干涉检查够用，对气动/外观不够 |
| 关节只生成基础类型 | `gear` 现在是简单 equality，没实现齿力上限/跳齿（要搬 RoboBuilder 的 mesh 模型，属后续） |
| 不仿真线缆/绳索/链条的动力学 | 参数搬了、模型没建（`cable`/`cord`/`chain` 域 26 项待接） |
| 无 SPICE | V6 仍是 `not_run`（要 ngspice 后端，M6 的 P19 或之后） |

### 不动

* P15 的 `joints.py` 判定逻辑
* `electrical.py` / `physical.py` / `coverify.py`
* schema 版本（仍 `/1`）
* 266 项既有测试

---

## P18 — 统一判定协议（2026-11-21）

**目标**：三次扰动 / 仿真预算 / 第六类外部证据。把"跑过"和"通过"分开。

**依据**：`docs/execution-plan-v3.md` P18

### 新增

| 文件 | 内容 | 规模 |
|---|---|---|
| `design/robustness.py` | 三次扰动协议 + 三态合并 + 按域适配 | ~220 行 |
| `scripts/check_m18.py` | P18 验收（9 组，含 5 个反例） | ~420 行 |

### 修改

| 文件 | 改动 |
|---|---|
| `design/ladder.py` | 加 V7（运动）/ V8（制造）；**`DEFAULT_LEVELS` 只到 V6** |
| `design/handoff.py` | 加第六类 `external`；`_blocking()` 排除 V7/V8 |
| `design/generate.py` | 加 `sim_budget`（验证次数硬上限） |
| `design_cli.py` | `ladder --with-sim`（显式才跑 V7） |
| `scripts/check_ladder.py` | 适配 9 层 |

### 三次扰动的合并规则（命门在第三条）

| 组合 | 结果 |
|---|---|
| 全 pass | **pass**（唯一能说"稳"的情况） |
| 任一 fail | **fail**（已经证明不稳） |
| 有 unknown（无 fail） | **unknown** ← 命门 |
| 空 | unknown |

**"任一次 unknown 就整体 unknown"**：如果 unknown 被当 pass，
"仿真没跑起来"会算成"通过"——AGENTS §0.1 那个病的另一张面孔。
扰动**抛异常也记 unknown**（不是 fail）：没跑起来不等于设计坏了，
但更不能当通过。

### 按域适配，不照搬乐高尺度

| 域 | 扰动 | 为什么 |
|---|---|---|
| 几何 V2 | 公差 ±0.1mm | PCB/装配公差 |
| 电气 V3 | 轨压 ±5% / 负载 +20% | 电源容差 |
| 运动 V7 | 初始位姿 ±1mm / 时序 +50ms | 沿用 RoboBuilder 尺度（同类场景） |

它的 1mm/50ms 是**地面小车**的尺度。把毫米搬到电气域是错的。

### 四个实测踩坑

| # | 症状 | 根因 | 修法 |
|---|---|---|---|
| 1 | 单测 **1.4s → 394s** | V7 进了默认层级，几乎每个测试都 `run_ladder()` | **`DEFAULT_LEVELS` 只到 V6**；V7 要 `--with-sim` 显式要 |
| 2 | V7 失败**同时**进 blocking 和 external | `_blocking()` 没排除 V7/V8 | 加 `if name in ("V7","V8"): continue` |
| 3 | 316 元件仿真 >300s 墙钟 | arena 默认 16MB 不够 → **约束被静默丢弃** | `<size memory="256M"/>`；V7 超 200 件降级到 0.4s |
| 4 | `r3demo` V7 fail（倾角 176.9°）| 元件 geom 的 z 在板中心平面，**下半截穿进地板** | 元件 z = `thick/2 + 元件半高`（放在板上表面） |

**第 1 条最值钱**：`DEFAULT_LEVELS` 这个改动把单测从 394s 拉回 1.43s。
这不是"优化"，是**默认值选错了**——贵的层不该在默认路径上。

**第 4 条**：`r3demo` 的 176.9° 看起来像"设计不稳"，实际是**几何摆错位置**。
诊断过程：质心在板内（19.1, 23.8 / 板 0..66×0..63）→ 说明不是不平衡
→ 查 z 坐标 → 元件下半截在 z<0 → 穿地弹出。

### 预算：验证次数是硬成本

`max_candidates` 限"生成几个"，`sim_budget` 限"验证几个"。
两者差几个数量级（316 件跑 1 秒 >300s）。

超预算时**候选保留但标 `unverified`**，报告给 `used / limit`。
实测 `sim_budget=3` + 20 候选 → 用 3、标 17 未验。

### 第六类 `external`：不进阻断

外部工具（立创EDA DRC / MuJoCo）用的是**别人的标准**：
立创的层规则/间距/焊盘补偿与我们不同；MuJoCo 的翻倒阈值是我们定的口径。

混进 `blocking` 会让人分不清"谁说的"。
`deliverable` 不看这一类——**外部证据不否决交付，但值得看一眼**。

实测 `r3demo`/`示例-单板` 的 external：

```
V7 运动（MuJoCo 动力学 + 三次扰动） 跑了 3 项（pass）
2 个物理域没有模型（thermal/emc → 只能实测）
```

### 验收

```bash
python -m hw_velxio.scripts.check_m18
python -m hw_velxio.design_cli ladder <id> --with-sim   # 显式跑 V7
```

九组：合并规则（5 个正反例）/ 异常→unknown / 按域适配 / 9 层 /
V8=not_run / V7 真跑 / 性能降级 / 预算 / external 不进阻断。

**退化验证（两条都有效）**：

| 去掉 | 断言变红 |
|---|---|
| `unknown` 当 `pass` | 3 条（含"整体 note 明说 unknown 不是通过"） |
| `_blocking` 排除 V7/V8 | 2 条（"V7 失败不进 blocking"、"external 不否决交付"） |

### 回归

```
266 项单测 OK（1.43s，与 P18 之前一致）
10 个验收脚本全绿
```

### 不动

* P15 的 joints 推断、P16 的 MJCF 生成/参数表
* `electrical.py` / `physical.py` / `coverify.py`
* 硬层仍只有 V0/V1（V7 失败**不截断**下游）

---

## P17 — 立创EDA 发射（2026-11-21）

**目标**：`design.json` → `.eprj3` 工程（原理图 + PCB + 板框）。
**不搬 12600 行 KiCad/排版/布线代码，只写一层桥。**

**依据**：`docs/execution-plan-v3.md` P17 · `docs/architecture-v2-lceda.md` §2–§4

### 新增

| 文件 | 内容 | 行数 |
|---|---|---|
| `design/lceda.py` | 会话封装（JSON 信封 / 三重自校验 / 能力探测 / 单位换算） | 646 |
| `design/emit_lceda.py` | 五段发射 + 回读 parity + DRC 证据 | 1400 |
| `design/stackup_spec.py` | 层数 / 网络类 / 差分对约束（传约束，不搬算法） | 233 |
| `scripts/lceda_probe.py` | 一次性探测（能力快照 / `--raw` 全量 API） | 87 |
| `scripts/check_p17.py` | P17 验收（24 条，含 2 条退化） | 528 |

### 修改

| 文件 | 改动 |
|---|---|
| `design/store.py` | `ARTIFACTS` 加 `emit.json` |
| `design/handoff.py` | 第六类 `external` 增加立创来源（DRC / parity / 布线）；`sources` 加 `emit.json` |
| `design_cli.py` | 加 `lceda` / `emit` / `readback` 三个命令 |
| `src/tools.ts` | 加 `hw_emit` 工具（当时实测 37 个；本节原来写的「26 → 27」数错了，见 P21 行注） |
| `package.json` | `verify:p17` / `verify:m4` |

### 实测得到的八条硬事实（写进代码注释，改之前先读）

| # | 事实 | 后果 |
|---|---|---|
| F1 | stdout 是 JSON、stderr 是人话 | 合并解析 → `Extra data: line 2`（已复现） |
| F2 | `ok:true` 只代表代码跑通 | 业务失败藏在 `value.ok=false` 里，必须验 |
| F3 | 原理图单位 = 10 mil，PCB 单位 = 1 mil | mm→mil 换算 `1/0.0254` |
| F4 | `createProject` 自带 Board1/Schematic1/PCB1 | 文档说「不初始化任何文档」——**文档错了** |
| F5 | 系统库 uuid 要单独取 | `searchByProperties` 结果里没有 `libraryUuid` |
| F6 | `getDocumentReadyState()` 永远 `notLoaded` | 不能拿它判就绪，只能轮询业务结果 |
| F7 | `saveFileToFileSystem` / `getProjectsPaths` 默认被拒 | 「未启用扩展和独立脚本的外部交互权限」 |
| F8 | **自动布线可用**；曾两次误判为环境问题，真因是板框 Y 写反 | 见下（2026-11-21 更正） |

### F8 的更正：真因是**板框 Y 轴写反**，不是引擎（2026-11-21 晚）

早先这条记了**两版错结论**，两版都把「这块板布出 0 段」当成环境问题：

| 当时的结论 | 实测否证 | 真相 |
|---|---|---|
| 「引擎拒绝执行」 | 阻塞调用返回 `success: true`（官方定义＝**已启动**） | 引擎一直在跑 |
| 「headless 没有布线的执行环境」 | 用户登录态的 `origin=user` GUI 窗口里**同样** `0/N` | 与窗口来源、登录态、headless 全无关 |
| 「矩形板框不受 Y 轴影响」 | 恰恰是矩形板框的**位置**写反了 | 见下 |

**真因**：`_outline_source` 的 `R` 模式写成

```python
["R", mm_to_mil(x0), mm_to_mil(y0), mm_to_mil(w), mm_to_mil(h), 0, 0]
```

而 `TPCB_PolygonSourceArray` 的 `R` 是 `R x y width height rot round`，
**`(x, y)` 是左上角、矩形向 −Y 延伸 height**（大佬手册 §19 实测）。
于是板框落在 `y ∈ [−1575, 0]`，元件在 `y > 0` —— **整块板都在板框外**。
布线器无处可布，于是一声不响地 `failedNets: [全部]`、`duration ≈ 800ms`。

**只改一处**（左上角 y 写 `y0 + h`）后的实测：

| 量 | 修前 | 修后 |
|---|---|---|
| `successNetsCount` | 0 / 2 | **2 / 2**（`failedNets` 空） |
| 盘上走线 | 0 | **9 段**（GND 5 + MID 4） |
| 过孔 | 0 | 0（引擎自己选了单层 45°） |
| DRC | — | `[]`（零违规） |
| 关会话重开后 | — | 元件 3 个、板框 `['R',0,1574.8,…,1574.8]`、9 段走线全在 |

**验收里也复现**：`check_p17` 第 2 条那块板 `route` 段
`{status: ok, tracks: 11, vias: 0, expected_nets: 2, routed_nets: 2, drc_violations: 0}`。

### 顺带纠正「判据读错了字段」

`IPCB_AutoRoutingResult.success` 只表示「已启动」；`successNetsCount`
只描述**这一次调用做了什么**——同一工程布完后再调一次就是 `0 / 2`，
而盘上走线一段没少。所以：

* `_run_route` 的判定改成**量几何**：每个 ≥2 焊盘的网络都必须有走线
  （`_expected_nets` 只算 ≥2 焊盘的网络，空设计 → `not_ready`，不是通过）
* 分母、`missing`、`tracks`、`vias`、`drc_violations`、`save` 全部进 `counts`
* `startAutoRouting` 作业模型仍不可用（`jobId` 查不到 ＝ 作业未建立），
  但**阻塞调用已经够用**，不再依赖它

### 发射链路（实测 43s，3 个器件 + 5 个引脚）

```
project   → createProject + openProject + 轮询就绪 + 补板数
schematic → 装器件（lcsc 精确回核）→ 读引脚坐标 → 每个引脚拉 120 单位短线 → 网络标签
pcb       → 板框（['R',…] 源数组）→ importChanges(force) → 按 Placement 逐个 modify → save
route     → autoRouting（零参数 = 布所有未布网络）→ 逐网络数量走线 → 判定在几何上
outputs   → Gerber/BOM/坐标文件（生成成功；落盘需外部权限）
```

### 两个实测 bug（都是「看起来像成功」）

**B1. 引脚引出线重叠 → 整条网络被吞掉。**
第一版所有引脚都朝右拉 120 单位：同一器件的两根引脚线在 y 相同处**重合**，
`sch_SourcePrimitiveWire.add` 的「连通分量规范化」把它们并成一组，
一个网络名吃掉另一个 —— 实测 `MID` 整条变成 `GND`，网表只剩 1 个网。
修法：方向**背离器件锚点**（`pin.x >= anchor.x` 向右，否则向左）。
判定依据是 parity：`wrong=2` → 修后 `wrong=0`。

**B2. 空板的 DRC 通过不算数。**
`r3demo`（4 个元件全部 `no_symbol`）在空 PCB 上跑 DRC，`pass=True`。
这正是 AGENTS §0.1 的形状：**缺失被写成通过**。
修法：元件数为 0 时 `drc.status=not_ready`、`pass=False`，
note 写「板上一块元件都没有，DRC 通过不算数」。

### 另外三处假绿/静默失败，一并修掉

| 症状 | 根因 | 修法 |
|---|---|---|
| `C999999999` 竟然装上了 | `searchByProperties` 是**模糊**搜索，会返回近似结果 | 回核 `Supplier Part` 与请求的 lcsc 相等才算 |
| 3 个元件导入后只看到 2 个 | `importChanges` 返回 true ≠ 图元已全部落地 | 按**期望位号**轮询，最多 6 秒 |
| `openProject` 偶发返回 false | 实测一次 false，紧接重试即成功 | 重试一次，仍失败才报错 |

### 验收（`python -m hw_velxio.scripts.check_p17` → **26 条，26 通过**）

```
纯函数 10 条
[OK ] H1  单位：1 英寸 = 1000 PCB 单位（mil），往返一致
[OK ] H2  工程名只留 a-zA-Z0-9-（立创硬限制）
[OK ] H3  lcsc 占位符（C?）当没有，不猜符号
[OK ] H4  板框：矩形用 R 模式（实测可建），非矩形用折线源数组
[OK ] H5  parity：相等判 ok，网名不一致判 not_ready（逐条指出）
[OK ] H6  回读不到网表 → not_ready（空数据不算通过）
[OK ] H7  空 jobs 表 → not_ready（不是 ok）
[OK ] H8  空板 DRC 通过不算数（缺数据不是通过）
[OK ] H9  多板：按 Placement.board_id 分板（U1→B1，U2→B2）
[OK ] H10 回读路径的空板 DRC 同样不算数（两处守卫都要在）

通道 5 条（假 CLI，不需要真立创）
[OK ] C1  F1：stdout 信封 + stderr 人话 → 不因 Extra data 崩
[OK ] C2  F2：CLI ok:true 但业务 ok:false → 抛 LcedaError
[OK ] C3  expect：缺少必需子串 → 抛错
[OK ] C4  退化：关掉 require_ok → 业务失败静默通过（证明 C2 承重）
[OK ] C5  退化：stdout+stderr 合并解析 → Extra data（证明 F1 承重）

真机 9 条（需要装立创EDA）
[OK ] 1.  探测：版本 + 22 条能力逐条核对（doc api 实测，0 缺失）
[OK ] 6.  闸门：无 lceda CLI → not_ready、jobs 全 not_ready、不抛异常
[OK ] 2.  最小工程：43.4s 建工程 + 原理图 + PCB + 板框，落盘 .eprj3 文件夹
[OK ] 5.  搜不到符号 → unresolved=1（U9），其余 3 个继续装
[OK ] 3.  回读 parity：5 个引脚 missing=0 / wrong=0 / extra=0
[OK ] 4a. 布线：ok 只在「每网络都有走线」时给出（判据在几何上）
[OK ] 4a2. 板框 R 模式左上角 y = max Y（区间覆盖 [0,h] 而不是 [-h,0]）
[OK ] 4a3. 分母只算 ≥2 焊盘的网络（空设计/单焊盘网 → not_ready）
[OK ] 4b. DRC 记为外部证据（写明 external，不进 ladder）
[OK ] 4c. route=none → ok 且说明只放置
[OK ] 4d. 叠层：无 stackup 字段 → 标 assumed 并说清
```

### 回归

| 项 | 结果 |
|---|---|
| 266 项单测 | **1.096s** OK |
| 10 个既有验收脚本 | 全绿 |
| `npm run check`（TS） | 0 错 |
| 工具注册 | 37 个（`grep -c "name: 'hw_"` 实测），`check_tools.mjs` 通过 |
| 探针残留 | 已清理（LCEDA-Pro 工程目录只留 `r3demo` 与用户原有的 `hwforge_probe.eprj2`） |

### 已知缺口（不装作没有）

| 缺口 | 现状 | 谁能解 |
|---|---|---|
| 自动布线 | 引擎在本机拒绝 | 立创界面手动布，或等 M9 自研路由器（`--route ours`） |
| 产物落盘 | 默认被外部交互权限挡 | 人在立创设置里开一次 |
| 原理图排版 | 网格排列，不按功能分组 | 分组要 `units_list` + 版面算法（未做） |
| PCB 的 Y 轴方向 | **未做对照实验** | 非对称板框的上下镜像可能反了；矩形不受影响 |
| 器件符号覆盖 | 依赖 `lcsc`/`mpn` | P19（LCSC 数据源）补全后覆盖率才上来 |
| 多板对应 | 按顺序（板名是尺寸串） | 无法按名字匹配，已在报告里写明 |

---

## P19 — LCSC 元件数据源（2026-11-21）

**目标**：能力表从 14 型号 → 目录级，且 **catalog 级不进硬选型**。

**为什么必须先做**：P17 实测 `r3demo` 的 4 个元件全部 `no_symbol` ——
立创只认 LCSC 编号 / 厂商型号，而导入的 EasyEDA 工程里 `kind` 是哈希。
没有这一层，发射层一个器件都装不上。

**依据**：`docs/execution-plan-v3.md` P19 · `docs/adoption-detail.md` §2

### 新增

| 文件 | 内容 | 行数 |
|---|---|---|
| `design/parts_catalog.py` | 免 key 端点（3 个）+ 7 天缓存 + `PartEnriched` + 离线降级 | 677 |
| `design/package_sizes.py` | 封装族 → 尺寸表（10 片式 + 46 族 + 5 公式 + 24 别名） | 328 |
| `scripts/check_p19.py` | P19 验收（25 条，含退化） | 477 |
| `tests/fixtures/parts/*.json` | 抓下来的**真实**返回（2 个详情 + 1 页搜索，2.6–2.8KB each） | 3 文件 |

### 修改

| 文件 | 改动 |
|---|---|
| `design/capability.py` | 加**覆盖层**：`catalog_parts.json`（catalog 级）+ `capability_promoted.json`（datasheet 级）；`entries()` 是唯一视图；`explain_miss` 先查信任级再查能力 |
| `design/store.py` | `ARTIFACTS` 加 `parts_cache.json` |
| `design_cli.py` | 加 `parts-search` / `parts-enrich` / `parts-promote` |
| `src/tools.ts` | 加 `hw_parts` 工具（37 → 38 个） |
| `package.json` | `verify:p19` |

### 三个端点（2026-11-21 本机实测可访问，全免 key）

| 用途 | 端点 | 实测返回 |
|---|---|---|
| JLC 详情 | `cart.jlcpcb.com/…/getComponentDetail?componentCode=` | 6853 字节 / 99 字段 |
| JLC 搜索 | `jlcpcb.com/api/…/selectSmtComponentList`（POST） | `data.componentPageInfo.list[]`，total=18 |
| LCSC 详情 | `wmsc.lcsc.com/ftps/wm/product/detail?productCode=` | 38130 字节 / `result.productModel` |

**没按 plan 用 httpx**：仓库现有约定是 stdlib `urllib`（`velxio_client.py`
/ `serve.py`）。为一个 GET + 一个 POST 引入新依赖不值得。

### 三处计划与实测不符

| 计划 | 实测 |
|---|---|
| 「搜索返回 `data.componentList[]`」 | 实际是 **`data.componentPageInfo.list[]`**；照 `componentList` 取会永远拿到空 |
| 「enrich C89852 → size_mm≈{4.9,6.0,1.6}」 | 封装是 `SOIC-8-208mil`，JEDEC 宽体是 **{5.28, 5.2, 1.75}**；计划那三个数是估的 |
| 「Catalog 用 `encapsulationNumber` 之类」 | 那个字段是**每盘颗数**（95），不是尺寸/引脚数；一个都不能用 |

### 信任级怎么落地（这是本阶段的重点）

```
estimated(0) < catalog(1) < datasheet(2) < measured(3)
```

**两个文件，文件名就是信任级**（不是一个文件加 flag）：

| 文件 | `source` | 进 `candidates()`？ | 出现在哪 |
|---|---|---|---|
| `catalog_parts.json` | `catalog` | **否** | `explain_miss.rejected`，原因 = `source=catalog 未确认（parts-promote --datasheet 后可进选型）` |
| `capability_promoted.json` | `datasheet` | 是 | 正常参与选型 |

三条不变量：

1. **`save_catalog_entry` 强制 `source="catalog"`** —— 传 `datasheet` 进来也改回去，
   「必须人确认」的闸门绕不过去（验收 5b）
2. **`promote_entry` 必须有 `http(s)` 的 `--datasheet`**，否则**不写盘**（验收 5c）
3. **promote 是移动**：写进确认文件的同时从目录文件删掉，一个型号不会两边都在（验收 5d）

**闸门的出口**：`parts-promote <lcsc|mpn> --datasheet=<url> --role=<role> --needs=a,b,c`。
`--role` / `--needs` 是这个动作真正的内容 —— 目录里**没有**「它是什么」和
「它能干什么」，这两项只能人（或 agent 读完手册）给。所以 promote 不是
"点一下升级"，是一次带依据的判断。

### 实测到的三个坑

| 症状 | 根因 | 修法 |
|---|---|---|
| ATtiny85 被判成「生命周期正常」 | 只看 `componentStatus`，它是 `yes` | 先看 `noBuyReason`（"no longer manufactured"）→ `discontinued`；**空对象返回 `unknown` 不是 `normal`** |
| 0402 的 2D 投影是竖的 | 片式元件的 `CHIP_SIZES` 是 (长,宽,高)，我给了 `w=宽 h=长` | 长轴放 X（两个焊盘沿长轴排开） |
| `parts-promote` 报 `Object of type set is not JSON serializable` | `fills`/`provides` 是 set，直接 `json.dumps` | 在**写盘那一个点**统一 `_jsonable()`，调用方不用记得 |

### 验收（`python -m hw_velxio.scripts.check_p19` → **25 条，25 通过**）

```
封装表 3 条
[OK ] 1a  10 种实测封装串都能查出名义尺寸（0402/SOIC-8-208mil/LQFP-48(7x7)/SOT-23-3/SMA/…）
[OK ] 1b  BGA-64 不在表里 → unknown 且尺寸全 0（不猜）
[OK ] 1c  族公式推出来的标 formula，不冒充 jedec 名义值

解析 5 条（离线，喂抓下来的真实返回）
[OK ] 2a  C89852 → ATTINY85-20SU / SOIC-8-208mil / {5.28,5.2,1.75} / stock=0 / discontinued / 8 脚
[OK ] 2b  componentStatus=yes 但 noBuyReason 说明停产 → discontinued
[OK ] 2c  空对象不崩，缺字段是 None 不是 0
[OK ] 2d  非对象返回 notes 而不是异常
[OK ] 2e  搜索结果 ≥5 条且每条带 lcsc/封装/库存

接口与缓存 6 条
[OK ] 3   接口形状变了（没有 componentPageInfo）→ not_ready，不假装搜到 0 条
[OK ] 4a  TTL 内命中 → ok/fresh，不发网络
[OK ] 4b  过期 + 离线 → ok/stale（陈旧数据比没有好）
[OK ] 4c  无缓存 + 离线 → not_ready 且不抛异常
[OK ] 4d  坏缓存文件当没有（加速层不是事实源）
[OK ] 4e  HWV_PARTS_OFFLINE=1 真的挡住网络

信任级闸门 5 条
[OK ] 5a  catalog 条目进 explain_miss.rejected（点名未确认），不进 candidates
[OK ] 5b  写目录层时 source 被强制成 catalog（绕过不了）
[OK ] 5c  promote 无 --datasheet → 拒绝且不写盘（留痕是硬条件）
[OK ] 5d  promote 带 --datasheet → 进 candidates，且从目录层移走
[OK ] 5e  退化：放水 trusted() → 5a 的判据变红（证明它承重）

回填 3 条
[OK ] 6a  目录尺寸 → size_mm 且 physical_source=catalog
[OK ] 6b  公式推的尺寸标 estimated（不冒充 catalog）
[OK ] 6c  没有尺寸就不写 size_mm（不写 0）
[OK ] 6d  接缝：回填出的 lcsc 正是 P17 发射层认的那个

真机 2 条（联网）
[OK ] 7a  search「stm32f103 lqfp48」→ 5 条，C8304/C8734/… 带封装与库存
[OK ] 7b  enrich C89852 → size_mm 非 0、physical_source=catalog
```

### 回归

| 项 | 结果 |
|---|---|
| 266 项单测 | OK（1.87s） |
| 11 个验收脚本（含 check_p17） | 全绿 |
| `npm run check`（TS） | 0 错 |
| 工具注册 | 38 个，`check_tools.mjs` 通过 |
| 覆盖层残留 | 已清理：`design/` 下只剩 `sim_params_default.json`（验收用临时文件，不写仓库） |

### 已知缺口

| 缺口 | 现状 | 谁能解 |
|---|---|---|
| 覆盖率仍然有限 | 目录能查**任何**编号，但回填要人指定 lcsc | 导入器补 `lcsc`，或 `parts-search` 后人工选型 |
| `fills` / `provides` | 目录里没有，promote 时必须人给 | 这是有意的：猜语义 = 假通过 |
| BGA / 特殊封装 | 尺寸表里没有 → unknown | 需要时人工录入一行 |
| 库存/价格时效 | 缓存 7 天 | 打板前重跑一次 `parts-enrich` |

---

## P20 — 固件（2026-11-21）

**目标**：搬 Traceforge 的 `firmware/`（2061 行）+ 扩 MCU + 加 `pins.h` 交叉核对。

**依据**：`docs/execution-plan-v3.md` P20 · `docs/adoption-detail.md` §3

### 一处目录改名（必须先说）

计划写 `design/firmware/*.py`，实际是 **`design/fwgen/`**：
`design/firmware.py` **已经存在**（P4 的固件文本检查 6 条，`cmd_firmware` 在用）。
Python 不允许 `firmware.py` 与 `firmware/` 并存；改名比搬家安全。
`fwgen` = firmware generation，与已有的 `fwcheck` 语义区分开。

### 新增（2893 行）

| 文件 | 来源 | 行数 | 改动 |
|---|---|---|---|
| `design/fwgen/drivers_data.py` | 搬（MIT） | 262 | 原样；每个器件保留 datasheet 章节引用 |
| `design/fwgen/drivers.py` | 搬（MIT） | 379 | 生成代码的署名改成 hw_velxio |
| `design/fwgen/scaffold.py` | 搬（MIT） | 477 | 导入改本包；清单名 `hwv_firmware.json`；5 个 target 不变 |
| `design/fwgen/mcus.py` | 搬（MIT） | 258 | **3 → 7** 个 MCU |
| `design/fwgen/netlist.py` | **重写** | 102 | 原实现读 KiCad/yaml；我们只读 `design.json` |
| `design/fwgen/pinmap.py` | 搬（MIT）+ **增量** | 745 | `.ioc` 入口 / `value` 字段 / HAL 别名 / 交叉核对 |
| `scripts/check_p20.py` | 新 | 637 | 24 条验收（含 1 条退化、4 条 CLI 端到端） |

### 修改

| 文件 | 改动 |
|---|---|
| `design/ioc_links.py` | `parse_ioc` **附加**返回 `modes`（`PA2.Mode=…`）；不改既有键，P8 的调用方不用动 |
| `design/store.py` | `ARTIFACTS` 加 `pinmap.json` / `pinmap_crosscheck.json` |
| `design_cli.py` | 加 `pinmap-gen` / `pinmap-check` / `fw-driver` / `fw-scaffold` |
| `src/tools.ts` | 加 `hw_fwgen` 工具（38 → 39 个） |
| `package.json` | `verify:p20` |

### 没搬的两块（各有原因）

| 没搬 | 为什么 |
|---|---|
| `cli.py`（174 行） | 我们有自己的 `design_cli`；它依赖 `rich`，为一个入口点加依赖不值得 |
| `toolchain.py`（262 行） | `design/toolchain.py` 已有探针与产线导出；再搬一份 = 两个事实源 |

搬的是 **1271 行**（原样/近原样：`drivers_data` 256 + `drivers` 372 + `scaffold` 466 + `mcus` 177）；增量另记：`mcus` +81 行（7 个 MCU）、`netlist` 102 行重写、`pinmap` 745 行里约 296 行是移植。`pinmap` 的固件侧抽取与交叉核对是它没有的（计划里也写明是"我们的增量"）。

### MCU：3 → 7，以及**不搬引脚能力表**的理由

7 个 `.ioc` 文件真实用到 5 个 STM32 型号，`.ioc` 里的 `Mcu.UserName` 实测：

| `.ioc` | UserName | 表里的 key |
|---|---|---|
| `F103-GPS` | STM32F103C8Tx | `stm32f103` |
| `f103c6-car` | STM32F103C6Tx | `stm32f103c6` |
| `F401-Bottom` / `F401-Front` | STM32F401RCTx | `stm32f401rc` |
| `F405-Joystick-Screen` / `F405Joystick` | STM32F405RGTx | `stm32f405rg` |
| `H743PilotX` | STM32H743IITx | `stm32h743ii` |

加上原有的 `attiny85` / `esp32s3` = **7** 条。
C6 必须排在通用 `stm32f103` **之前**：`find_mcu` 按插入顺序取第一个命中，
否则 `.ioc` 写 C6 会被报成 C8（引脚一样、型号报错）。

**每条新 MCU 只写型号/封装/平台，不写 `pins` 与 `functions`**：
`.ioc` 给的是端口引脚名（PA0/PH9），不是封装脚号，`pins.h` 与交叉核对
都按端口名工作，所以那张"封装脚号 → 引脚名"的表用不上；`functions`
（某脚能做哪些复用功能）要从 datasheet 的 AF 表逐脚抄，**没抄就不写**。
留空的后果只是少一条"这个脚做不了 I2C"的提醒，比编一张假表好。

### `pins.h` 的两套写法（一套事实）

```c
/* VL53-PWR-EN: U1 pin PA15 = PA15, GPIO */
#define PIN_VL53_PWR_EN       GPIO_PIN_15
#define PIN_VL53_PWR_EN_PORT  'A'          /* 寄存器级（scaffold 的 stm32 target 用）*/
#define PIN_VL53_PWR_EN_PIN   15
#define HWV_PIN_VL53_PWR_EN   GPIO_PIN_15  /* HAL 写法 */
#define HWV_PORT_VL53_PWR_EN  GPIOA
```

为什么两套都要：Traceforge 的 stm32 模板是**寄存器级**的（`'A'` + 位号），
而真实项目几乎都用 HAL（`GPIO_PIN_n` + `GPIOA`）。只给一套，另一半就得手改。
两套都从同一个 `(port, bit)` 推出，不会分叉。

### 交叉核对：三类结论，双方引用

| 结论 | 级别 | 例子（真机跑出来的） |
|---|---|---|
| 幽灵引脚 | **error** | `幽灵引脚 PA11 ← cc_fw\main.c:4（硬件 cc.ioc 里没有 PA11 的配置）` |
| 闲置 | 提示 | `PB9 ← cc.ioc 配了 PB9（LPN），固件没用到` |
| 方向冲突 | **error** | `PA15 ← cc_fw\dir.c:3（配成 Output，固件却在读）` |

**只报"有幽灵引脚"不给行号等于没报**——人还得自己搜。所以每条都带
`文件:行号` 与双方依据。

### 实测到的四个坑

| 症状 | 根因 | 修法 |
|---|---|---|
| PA15/PB9/PC0 被当成"没配置"漏掉 | CubeMX 对普通 GPIO 输出**不写 `.Mode` 行**，只写 `Signal=GPIO_Output` + `GPIO_Label` | `configured` 看 Mode **或** Signal **或** Label；方向也从 Signal 推 |
| 宏名是 `A2` 而不是 `USART2_TX` | 宏名退让顺序写成 `label → 引脚名`，漏了信号名 | 顺序改为 `label → signal → 引脚名` |
| CLI 报 `fail`、工具层判成异常 | `**report` 把 `status` 覆盖回 "fail"，而 CLI 三态是 `ok/not_ready/failed` | 先摘 `status` 再翻译，判定原值另存 `verdict` |
| `--out=demo/firmware` 写到了 cwd | 相对路径按进程 cwd 解析 | 工程类命令的 `--out` / `--ioc` / `--src` 统一按**工程目录**解析 |

第一条最值得记：`GPIO_Output` 型引脚恰恰是 `.ioc` 里**唯一带人起名字**的那几个，
漏掉它们等于 `pins.h` 里一个有用的宏都没有。

### 验收（`python -m hw_velxio.scripts.check_p20` → **24 条，24 通过**）

```
pins.h 4 条
[OK ] 1a  合成 .ioc → pins.h 含 VL53_PWR_EN 与 USART2_TX 宏（值是 GPIO_PIN_n）
[OK ] 1b  无 `.Mode` 行、只有 `Signal=GPIO_Output` 的脚也算已配置（方向从 Signal 推）
[OK ] 1c  SWD 调试脚（PA13/PA14）标 reserved，不算闲置 I/O
[OK ] 1d  真实 F401-Front.ioc：13 个配置脚 + 3 个人给的 label 都进宏

交叉核对 4 条
[OK ] 2a  合成固件用未配的 PA11 → 幽灵引脚，带 文件:行号
[OK ] 2b  配了但固件没用的 PB9 → 闲置（提示级，不拉低状态）
[OK ] 2c  只出现 GPIO_PIN_n 没有端口 → unpaired，不猜端口
[OK ] 3   方向冲突：配成 Output 却被读 → error，带双方依据

真编译器 1 条
[OK ] 4   改 .ioc 的 label → 重生成 pins.h → 引用处编译失败（真 gcc -fsyntax-only）

驱动 3 条
[OK ] 5a  驱动表 7 个器件
[OK ] 5b  SHT31 → init/read C 代码，含地址 0x44 / 命令 0x2400 / CRC-8
[OK ] 5c  表外器件不生成半成品代码

骨架 3 条
[OK ] 6a  5 个 target 都出骨架（4–8 个文件不等）
[OK ] 6b  target 与 MCU 家族不匹配 → 拒绝
[OK ] 6c  骨架的 pins.h 带 HWV_ 别名（与 pinmap-gen 同一套事实）

SDK 1 条
[OK ] 7   非 STM32 固件（Arduino/AVR/ESP-IDF）→ not_ready 并点名 SDK

MCU 3 条
[OK ] 8a  MCU 表 3 → 7
[OK ] 8b  C6/C8 不混淆（C6 排在前面）
[OK ] 8c  `.ioc` 的 MCU 认不出 → None + 原因，不猜型号

退化 1 条
[OK ] 9   幽灵引脚降为提示 → 验收 2 的判据变红（证明它承重）

CLI 端到端 4 条（临时工作区，不碰用户工程）
[OK ] 10a pinmap-gen 写出 pins.h
[OK ] 10b pinmap-check 幽灵引脚 → failed 并落盘
[OK ] 10c fw-scaffold 出 stm32 骨架
[OK ] 10d fw-driver 出 sht31.c/.h
```

### 回归

| 项 | 结果 |
|---|---|
| 266 项单测 | OK（1.29s） |
| **13** 个验收脚本（含 P17/P19/P20） | 全绿 |
| `npm run check`（TS） | 0 错 |
| 工具注册 | 39 个，`check_tools.mjs` 通过 |
| 用户工程污染 | 已清理（测试写入的 `示例-单板/firmware/`、`pinmap.json` 已删；CLI 验收用 `HWV_WORKSPACE` 沙盒） |

### 已知缺口

| 缺口 | 现状 | 谁能解 |
|---|---|---|
| MCU 引脚能力表（AF 复用） | 7 条 MCU 只写型号，`functions` 空 | 逐芯片从 datasheet AF 表录入；不抄就不写 |
| 引脚存在性校验 | 没有"这个脚在该封装上是否存在"的表 | 同上（封装脚号表） |
| 固件抽取只认 STM32 HAL | Arduino/AVR/ESP-IDF 报 `not_ready` | 加一族就加一张 API 表（照 `_API_KINDS` 的写法） |
| 抽取是**单行**匹配 | 跨行写法（`HAL_GPIO_WritePin(GPIOA,\n GPIO_PIN_5, …)`）抽不到 | 记在 `unpaired` 里，不猜 |
| 骨架的 stm32 target 是寄存器级 | 无 HAL/CMSIS、无时钟树 | 真实项目该换成 CubeMX 工程 |

---

## P21 — 闭环（2026-11-21）

**目标**：`physical_tests` 回写 + 仿真参数校准（**含「重放旧失败」校验**）。

**为什么这一层非有不可**：P15–P20 每一层都在推导，整个仓库里**没有一条真机实测**。
RoboBuilder 的实测最能说明代价——它的仿真器在修好 135 个参数之前，
15 个「仿真通过」的实车全部跑不完赛道。**仿真越自信，错得越整齐。**

### 新增（1162 行）

| 文件 | 内容 | 行数 |
|---|---|---|
| `design/real_tests.py` | `physical_tests` 结构 + 引用纪律 + `contact_sheet` | 407 |
| `design/sim_calibration.py` | 校准规则 + 成对存档 | 323 |
| `scripts/check_p21.py` | 验收（19 条，含退化） | 432 |

### 修改

| 文件 | 改动 |
|---|---|
| `design/handoff.py` | `_external()` 消费 `physical_tests`：逐条报 + 一条汇总「N 条实测失败仿真复现不出来」 |
| `design/store.py` | `ARTIFACTS` 加 `calibration.json` / `sim_params/initial.json` |
| `design_cli.py` | 加 `physical` / `calibrate` / `contact-sheet` |
| `src/tools.ts` | 加 `hw_physical` 工具（39 → 40 个） |
| `package.json` | `verify:p21` |

### 校准规则：**采纳标准不是「新设计能过」**

```
旧设计在新参数下 → fail（复现了录像）  → 采纳
                 → pass（被调松了）    → 拒绝 old_passed
                 → unknown（跑不起来）  → 拒绝 old_unknown
```

第二条是整套方案最关键的一处闸门。如果采纳标准是「新设计能过」，参数就成了
**作弊工具**：把 `mesh_mu` 调到 0，任何设计都"稳"。换成「旧设计必须复现那段录像」，
参数才有资格被用来预测未发生的事。**和我们「不能自己出题自己批」同源。**

四条拒绝理由（都实测生效，各有断言）：

| 理由 | 触发条件 |
|---|---|
| `old_passed` | 新参数下旧设计反而通过了 = 调松 |
| `old_unknown` | 旧设计跑不起来/判不出——**unknown 不是复现** |
| `too_many_params` | 一次动 >2 个参数，不可归因 |
| `unknown_param` | 参数名不在表里（写错名字不能变成"新增项"） |

每次 ≤2 个参数的来历：一次改 5 个，旧设计当然"复现"了，但你不知道是哪 2 个在起作用。

### `physical_tests` 的三条纪律

| 纪律 | 实现 |
|---|---|
| `fail` 必须写 `observed` | 只写「失败了」等于没写，别人没法据此改设计 |
| `implicated` 必须是 `ref:<位号>` 且在设计里存在 | V0 同款符号检查；写错的位号会让归因指错地方 |
| `sim_reproduced` 是**三态** | `True` / `False` / `None`。**`None`（没试过）不许写成 `False`**；`to_dict` 时直接省略该键 |

### 实测到的三个坑

| 症状 | 根因 | 修法 |
|---|---|---|
| `cal.accepted` 永远 False，而 `to_dict()['accepted']` 是 True | `accepted` 写成**字段**默认 False，采纳后不更新 | 改成**属性** `bool(self.adopted)`，只有一个事实源 |
| 报告里「没评估新设计」和「新设计判不出」长得一样 | `new_status` 默认 `"unknown"` | 默认 `""`，另给 `new_evaluated` 布尔 |
| `handoff.py` 字符串语法错 | `"这说明"仿真通过"在…"` —— 中文引号进了字符串值 | 换「」（AGENTS §3.3，又一次） |

### 验收（`python -m hw_velxio.scripts.check_p21` → **19 条，19 通过**）

```
记录与交接 6 条
[OK ] 1a  记录一条 fail（带 implicated / observed / evidence）
[OK ] 1b  往返：sim_reproduced=False 保住（不变成 None）
[OK ] 1c  没试过仿真 → 字段不写出来（None ≠ False）
[OK ] 1d  引用不存在的位号 → 拒绝写入（不静默丢弃）
[OK ] 1e  handoff 第六类报「实测失败」+「仿真复现不出来」，且全是 external
[OK ] 1f  实测失败不进阻断

校准 4 条
[OK ] 2   采纳标准是「旧设计在新参数下也失败」→ 参数被采纳
[OK ] 3   反向：新设计能过但旧设计不复现 → **拒绝采纳**（且不去评估新设计）
[OK ] 4a  另外三种拒绝理由都生效（unknown / 超 2 个 / 参数名不存在）
[OK ] 4b  采纳后参数来源升到 measured（不再是 public）

成对存档 3 条
[OK ] 5a  sim_params/initial.json + learned-<date>.json 成对存在
[OK ] 5b  点名 turned_on（初始是 0、校准后非 0 = 初始没建模的物理）
[OK ] 5c  再存一次不覆盖 initial（它是校准前的基线）

缩略图 2 条
[OK ] 6   contact_sheet：无 ffmpeg → not_ready 且给出可直接粘走跑的命令行
[OK ] 6b  视频不存在 → not_ready（不是产出空图）

真 MuJoCo 2 条
[OK ] 7a  default_runner 的判定落在 pass/fail/unknown 里（实测 pass）
[OK ] 7b  旧设计在新参数下不失败 → 校准拒绝（unknown 与 pass 都不算复现）

退化 1 条
[OK ] 8   采纳标准换成「新设计通过」→ 第 3 条的判据变红（证明它承重）

真工程 1 条
[OK ] 9   r3demo：handoff 第六类出现「实测失败：上电自检」+「1 条实测失败仿真复现不出来」
```

### 真机跑了一遍（r3demo）

```
physical r3demo --record --test="上电自检" --result=fail \
  --observed="上电后无输出，3.3V 轨跌到 0.7V" --implicated=ref:PS1 --reproduced=false
  → status ok, pt-001, failed_unreproduced=['pt-001']

calibrate r3demo --test=pt-001 --candidates='[{"mu_tyre_rod":0.57}]'
  → accepted=False, reason=old_passed（真 MuJoCo 判旧设计 pass）
```

**这就是这条规则在真数据上的样子**：一个"看起来能让设计更稳"的参数改动，
因为**没解释已经发生的失败**而被拒绝。真机跑完后 `r3demo` 的
`physical_tests` / `sim_params/` / `calibration.json` 已清空（不留夹具）。

### 回归

| 项 | 结果 |
|---|---|
| 266 项单测 | OK（1.17s） |
| **14** 个验收脚本（含 P17/P19/P20/P21） | 全绿 |
| `npm run check`（TS） | 0 错 |
| 工具注册 | **41** 个（`grep -c "name: 'hw_"` 实测）。**注**：P15–P20 各节原来写的 24/25/26 → 27/28/29/30 是错的——那串数是当时随手读的"新增"行号，不是总数。本行起按实测写 |
| 用户工程污染 | 已清理（r3demo 的记录/存档全删） |

### 已知缺口

| 缺口 | 现状 | 谁能解 |
|---|---|---|
| `contact_sheet` 没跑过 | 本机没有 ffmpeg（PATH 上实测没有）→ 返回可执行命令 | 装 ffmpeg 后粘那条命令；验收的判据已按"两选一"写好 |
| 校准靠 V7 判 | V7 只判"会不会翻倒"，不判"爬不爬得上台阶" | 给具体场景写判断器（接口 `Runner` 已留出） |
| 旧设计要另给 | 默认 old == new，只能验"参数能不能复现" | `--old=<另一个工程>` |
| 一条失败改一对参数 | 顺序贪心，不回溯 | 参数少时够用；参数多需要实验设计 |

---

## P22 — agent 循环（2026-11-21）

**目标**：给出一个**可审计**的循环，把 P15–P21 的工具串起来，全程假模型 0 token 可测。

### 新增（1632 行）

| 文件 | 内容 | 行数 |
|---|---|---|
| `design/agent_loop.py` | 追加式日志 + 入参先校验 + 四档审批 + 续跑 | 465 |
| `design/agent_tools.py` | 白名单（14 个工具 / 4 档）+ argv 桥 | 396 |
| `scripts/fake_model.py` | `ScriptedClient`（照 Traceforge `conftest.py:111`） | 134 |
| `scripts/check_p22.py` | 验收 19 条（含退化） | 424 |
| `agent_docs/` | 5 份设计空间说明（照 RoboBuilder 的做法） | 213 |

### 修改

| 文件 | 改动 |
|---|---|
| `design/generate.py` | 加 `materialize(candidate, req)`：报告里的候选 → 真设计（P22 要 `generate → put`） |
| `design/store.py` | `ARTIFACTS` 加 `agent_journal.jsonl` |
| `design_cli.py` | 加 `agent` 命令（`--script` 假模型 / `--resume` 续跑） |
| `package.json` | `verify:p22` |

**没加 `hw_agent` 工具**：这层是"内层循环"，给外层 agent 再开一个入口只会多一条
绕过审批的路。要用就从 CLI 走，并从 `--script` / `--resume` 二选一。

### 白名单：14 个工具，4 档

| 档 | 工具 | read-only | ask | auto |
|---|---|---|---|---|
| read | `list` `get` `snapshot` `generate` | 放行 | 放行 | 放行 |
| judge | `ladder` `claims` `justify` `handoff` `links` `units` `sim` | 放行 | 放行 | 放行 |
| write | `put`（唯一写 design.json）`emit` | **拒** | 问 | 放行 |
| human | `promote`（`deferred lock`，不可撤销） | **拒** | 问 | **也问** |

**没有** `setpart` / `move` / `delpart`（验收 1a 断言这一点）。理由不是"不放心模型"，
是**改动的粒度决定了后果能不能查**：改一个 `part.ref` 会让归因、断言、实测记录
同时指向不存在的件，而 `validate()` 看不见这种错。要改就整份 `put`。

`judge` 档放行的理由：判定是**只读语义**的——读设计、写自己的产物
（`ladder.json`），不改 `design.json`。不给反馈回路，agent 就是在盲改。

`human` 档在 `auto` 也问的理由：`deferred lock` 没有 unlock。让自动模式替人做
不可逆决定，等于把整个存档纪律的根基抽掉。

### 追加式日志：三件事都靠它

| 需求 | 靠什么 |
|---|---|
| 中断后接着跑 | 历史只 append，从不原地改 |
| 重放结果一致 | 续跑时**不重跑工具**，用记录下来的结果 |
| 事后审计"它为什么这么做" | 每条 decision（allow/ask/deny）+ 理由都进日志 |

`Journal` **没有 update / delete 方法**——这是刻意的（验收 6d 断言 seq 严格递增
且没有改动类事件）。`compare()` 只比 `(kind, tool, status)` 三元组：
payload 里可能有时间戳，比它会把"一致"变成随机失败；但**工具与状态必须一致**，
那才是"重放出了不同的结论"。

### 实测到的四个坑

| 症状 | 根因 | 修法 |
|---|---|---|
| `max_candidates=2` 却生成 36 个候选 | 工具参数用下划线、CLI 用连字符（`--max-candidates`），`--max_candidates` 被静默忽略 | `build_argv` 里把 `_` 转 `-`，并写明"两者必须对应" |
| `generate` 永远报"需要 --requirement" | 老命令解析 `--key value`，新命令解析 `--key=value` | 加 `arg_style`（`space` / `eq`）逐工具声明——**这是实测差异，不是风格偏好** |
| 循环在第二步 `KeyError: 0` | 日志里把上一轮的 `candidates` **截断成了字符串** | 日志存全量、**喂模型时**才收体积；顺带把 `hw_generate` 的候选瘦身（前 N 个带 design，其余只留一行摘要） |
| `call()` 里传 `name=` 报重复实参 | 第一个形参也叫 `name`，和 `hw_promote` 的参数撞名 | 形参改名 `tool` |

第二条最值得记：**同一个仓库里两种 flag 风格并存**。统一成一种会让另一半
全部失效，而失效方式是"参数被静默忽略"——正是 AGENTS §3.7 那个形状。

### 验收（`python -m hw_velxio.scripts.check_p22` → **19 条，19 通过**）

```
白名单 4 条
[OK ] 1a  不存在 setpart/move/delpart（14 个工具，泄漏=[]）
[OK ] 1b  写入口只有 hw_put / hw_emit
[OK ] 1c  人确认通道只有 hw_promote
[OK ] 1d  四档都有工具（4/7/2/1）

审批 5 条
[OK ] 2a  审批表：human 在 auto 也是 ask（完整 12 格逐一比对）
[OK ] 3a  read-only 下 hw_put 被拒，且**工具一次都没执行**
[OK ] 3b  对照组：auto 下同一个 hw_put 放行（差异来自模式）
[OK ] 3c  auto 下 hw_promote 仍要问；无回调 → 拒绝且不执行
[OK ] 3d  有回调且同意 → 执行，请求带 tool/tier/args

入参 2 条
[OK ] 4a  缺必填 / 未知参数 / 类型不对都报出来
[OK ] 4b  入参不合法 → 记 invalid，**工具一次都没跑**

端到端 3 条（假模型）
[OK ] 5a  generate → put → ladder，ladder=ok 且 design.json 落盘
[OK ] 5b  过程中没有任何人确认请求
[OK ] 5c  脚本用尽后再被调用 → 明确报错（不静默结束）

续跑 4 条
[OK ] 6a  第 7 个事件处抛 Interrupted，日志已落盘
[OK ] 6b  续跑与一口气跑：**决策序列逐条一致**
[OK ] 6c  续跑不重跑已做过的工具
[OK ] 6d  日志只追加：seq 严格递增，无 update/delete

退化 1 条
[OK ] 7   把「human 任何模式都问」改成 auto 放行 → 2a 判据变红
```

### 真机跑了一遍（r3demo）

```
agent "读一下 r3demo 并判定" --project=r3demo --mode=auto --script=<3 步脚本>
→ status ok｜8 个事件｜2 次工具调用｜ladder=ok
```

日志落在 `r3demo/agent_journal.jsonl`（跑完已删）。

### 回归

| 项 | 结果 |
|---|---|
| 266 项单测 | OK |
| **15** 个验收脚本（P17–P22 六个新脚本） | 全绿 |
| `npm run check`（TS） | 0 错 |
| 工具注册 | 41 个（未新增，P22 不加外层入口） |
| 用户工程污染 | 已清理（r3demo 的 `agent_journal.jsonl` 已删） |

### 已知缺口 / 与计划不同

| 项 | 计划 | 实际 |
|---|---|---|
| `agent_docs/` 份数 | "照 RoboBuilder 的 11 份" | **5 份**（README + 设计空间/判定/闸门/recipes）。11 份里有 6 份是 RoboBuilder 自己的乐高领域内容，搬过来是噪音 |
| 真模型 | 未涉及 | 只有 `Client` 协议（一个方法）+ 假模型。接真模型还要一层 provider 适配（**没写，也没假装写过**） |
| 流式入参校验 | "流式" | 校验在**每次工具调用前**做（含未知键/类型），不是逐 token 的增量解析——假模型一次给全量参数，逐 token 那层没有可测的对象 |
| 工具失败 | — | 记 `failed` 并继续循环（不中断）；`status` 汇总为 `failed` |

---

## P23 — 测试扩充（2026-11-21）

**目标**：把 P15–P22 的判据沉淀成可回归的单测与夹具，并保证**没有 MuJoCo /
没有 lceda 时整套仍然全绿**。

### 数字（全部实测）

| 项 | 计划 | 实测 |
|---|---|---|
| 单测 | 266 → **≥550** | **665**（+399），1.4s 跑完 |
| 验收脚本 | 12 | **20**（`scripts/check_*.py`） |
| 合成夹具 | +15 | **15**（`tests/fixtures/designs/`，76KB） |
| 降级路径 | 必须全绿 | 无 MuJoCo / 无 lceda 各跑一遍**都全绿**（子进程真跑） |

### 新增

| 文件 | 内容 | 行数 |
|---|---|---|
| `tests/test_phases.py` | P15–P22 的单测（表驱动 + 动态生成） | 1185 |
| `tests/fixtures/designs/*.json` | 15 个合成夹具 | 76KB |
| `scripts/check_p23.py` | 规模与降级验收 | 202 |

### 修改

| 文件 | 改动 |
|---|---|
| `design/mujoco_backend.py` | 加 `HWV_NO_MUJOCO=1` 关闭开关（降级路径**可测**） |
| `package.json` | `test` 跑两个模块；加 `verify:p23` / `verify:all`；**所有脚本前加 `cd ..`** |

### 为什么用"表驱动 + 动态生成"

这些层里绝大多数判据是**同一形状的 N 个实例**：136 个仿真参数、56 个封装、
12 种关节、9 个阶梯层。手写 400 个几乎一样的 `def test_xxx` 只让文件膨胀。

但**每个实例仍要一条独立用例**：一条挂掉，`unittest` 直接告诉你是**哪一个**
参数/封装/关节坏了。塞进一个 `for` 循环里断言，坏了只知道"某个参数有问题"。
`add_cases()` 拿到的正是这个折中，并且**方法名重复时直接抛**——
否则"测了 136 个"可能其实只跑了 1 个。

### 15 个夹具与它们的"存在理由"

| 夹具 | 存在理由 |
|---|---|
| `tiny-blink` | 最小冒烟（3 件） |
| `dense-0402` | 21 件密集 0402，几何/网表压力 |
| `stacked-2layer` | **叠板**：两块板 z 向叠放 + `stack` 关系 |
| `folded-hinge` | **折叠**：两块板 + `hinge`（-90..+90） |
| `quadrotor` | **四旋翼**：4 电机 + 电池 + 时序 |
| `multilayer-4` | **多层板**：`stackup.copper_layers=4` |
| `out-of-board` | 元件超出板框 → `validate` 与 V2 **都该报** |
| `overlap-parts` | 同面重叠 → `validate` 干净，**V2 必须 fail** |
| `reversed-polarity` | 12V 进 3.6V 器件 → V1 硬截断（P1 的合成夹具） |
| `no-netlist` | 有件无网表 → 归因只能到 `connected` |
| `single-pin-net` | 单脚网（网表边界） |
| `dup-ref` | 重复 `ref` → `validate` 必须拒 |
| `diffpair-usb` | `USB_D+/D-` → stackup 差分对 |
| `many-boards` | 三板 → units/links 多板路径 |
| `gimbal-camera` | 云台相机（真实形状：相机+舵机+MCU） |

**每个夹具都在 `FIXTURE_EXPECT` 里登记了它属于哪一档**：
`clean`（validate 干净且 V2 不 fail）/ `validate`（结构就该报）/
`geometry`（结构干净但 V2 必须 fail）。没登记的夹具只是磁盘上的 JSON——
`check_p23` 第 3b 条会点名。

### 三个实测坑

| 症状 | 根因 | 修法 |
|---|---|---|
| 15 个夹具里 12 个 V2 是 `unknown` | P18 的纪律"警告 → unknown"（没 keepout / 没安装孔 / 没限高） | 期望表改成三档，`clean` 允许 `unknown`；只有 `geometry` 档要求 `fail` |
| 往返测试 15 个全红 | 夹具文件里的 `0` 是 int，`from_dict` 后变 `0.0` | 夹具做一次**规范化**（写 `from_dict(file).to_dict()`），此后 file == round-trip |
| `npm test` 报 `No module named 'hw_velxio'` | npm 在包目录里跑，而 `python -m hw_velxio` 要求仓库根在 `sys.path` | 所有脚本前加 `cd ..`（这条以前就有，只是没人从包目录跑过） |

第一条不是 bug：它正是 P18 那条纪律在起作用。**警告不能当通过**，
所以"只有警告"的设计 V2 只能是 `unknown`。

### 降级路径怎么"真跑"

MuJoCo 装在项目内 `vendor/pylib`（卸不掉），lceda 是用户装的应用（不该为测试删）。
所以给两条显式开关，然后在**子进程**里重跑整套：

```
HWV_NO_MUJOCO=1                          → available() 返回 False
HWV_LCEDA=C:/no-such-lceda-pro.exe       → find_exe() 返回这个不存在的路径
```

验收 4a/4b 各跑一遍 665 个单测（都全绿），4c 是对照组（同一台机器，开关一个
False 一个 True），4d 是降级下 `check_ladder` / `check_handoff` / `check_p22`
仍通过。**没有 4c 的对照组，"降级后全绿"可能只是因为开关根本没生效。**

### 验收（`python -m hw_velxio.scripts.check_p23` → **10 条，10 通过**）

```
规模 6 条
[OK ] 1a  单测总数 ≥ 550（实测 665）
[OK ] 1b  全部通过（不是 skip 掉了）
[OK ] 2   验收脚本 ≥ 12（实测 20）
[OK ] 3a  合成夹具 ≥ 15（实测 15）
[OK ] 3b  每个夹具都登记了设计意图
[OK ] 3c  计划的四类场景都在（叠板/折叠/四旋翼/多层板）

降级 4 条（子进程真跑）
[OK ] 4a  没有 MuJoCo 时 665 个单测仍全绿
[OK ] 4b  没有 lceda 时 665 个单测仍全绿
[OK ] 4c  对照组：开关生效（一个 False 一个 True）
[OK ] 4d  降级下 check_ladder / check_handoff / check_p22 仍通过
```

### 回归

| 项 | 结果 |
|---|---|
| `npm test` | **665 项 OK**（1.4s） |
| `npm run verify:all` | 单测 + m1 + m18 + p22 + p23 全绿 |
| 16 个 check 脚本逐个跑 | 全绿 |
| `npm run check`（TS） | 0 错 |
| 工具注册 | 41 个不变（P23 只加测试） |
| 用户工程污染 | 无（夹具全在 `tests/fixtures/`，验收用临时工作区） |

### 已知缺口

| 缺口 | 现状 |
|---|---|
| 降级只验了 MuJoCo / lceda | 没验"没有 gcc""没有 arduino-cli"——那两个只在少数脚本里用 |
| 夹具的 `stacked-2layer` V2 是 `unknown` | 多板叠放的 3D 投影还没接，V2 判不出（**不是通过**） |
| 覆盖率没有量化 | 665 条是按"每条判据一个实例"铺的，没跑过 coverage |

---

## P0 — 结论层（一屏结论）（2026-11-21）

**目标**：让"这版行不行、下一步做什么"出现在**首屏**，而不是散在 11 个页签里。

### 为什么先做这个，而不是先做自然语言

做完 NL 入口后，用户看到的第一屏**还是九个 tab**，他不知道该点哪。
结论页是**所有入口的公共出口**：NL、CLI、导入三条路都收敛到它。
先把出口做对，再开入口。

### 交付

| 文件 | 内容 | 规模 |
|---|---|---|
| **[verdict.py](design/verdict.py)** | 结论层：九层横条 + 六类计数 + 排序后的下一步 | 233 行 |
| [check_p0.py](scripts/check_p0.py) | 验收 17 条（含 2 条退化/反例） | 316 行 |
| `src/shared/types.ts` | `VerdictPayload` / `VerdictLevel` / `VerdictNext` + `SnapshotPayload.verdict` | 46 行 |
| `src/client/panels.tsx` | `VerdictPanel`（一句话 + 横条 + 计数 + 五条） | 125 行 |
| `src/client/store.ts` | 分区 `verdict`（health 组首位 + 默认分区）+ **`hint.view` 终于被消费** | 18 行 |
| `src/client/HwIdeView.tsx` | `case 'verdict'` + 徽章口径（阻断 + 失败层） | 20 行 |
| `src/client/styles.ts` | 横条 / 计数 chip / 下一步行 | 62 行 |
| `src/tools.ts` | `hw_snapshot` 带结论；`snapshot`/`ladder`/`handoff` 三个工具跳结论页 | 40 行 |

### 一屏长什么样

```
[有失败]  不可打板：2 条阻断，V0 ✓ · V1 ? · V2 ✗ · V3 ? · V4 ? · V5 – · V6 –
          ⚠ 设计改过了，这一屏是**上次判定**的结论（2 天前）

  V0✓  V1?  V2✗  V3?  V4?  V5–  V6–
  阻断 2 · 缺数据 6 · 默认值 7 · 不会验 2 · 未接线 175 · 外部 0

  下一步（192 条，按紧急程度排）
  1  V2 几何（重叠 / 越界 / 高度）失败      → 修好该层报出的问题      [阻断]
  2  4 项不可逆决策未闭合                  → 逐项闭合            [阻断]
  3  板间链路一条都没声明                  → 补 links_list        [缺数据]
```

### 三条纪律

1. **不重新判定**。只投影已有产物（`ladder.json` + `handoff.build(store)`）。
   所以它很便宜，也因此**可能过期**——`stale=true` 就是那个意思。
2. **排序在 Python 算**（AGENTS §1.1）。UI 只按顺序渲染，不重排。
3. **`unknown` 与 `not_run` 各自可见**。并进"没通过"会让人去改设计；
   并进"通过"就是假绿。

### 排序口径

类别（谁能承担后果）：

```
blocking < missing_data < assumed < unverifiable < orphan < external
```

同类别内按状态：`fail` < `truncated` < `open` < `not_ready` < `missing` < `unknown` < `default` < `pass`。

取前 **5** 条：一屏放得下的上限。**超过 5 条等于没有优先级**——
对着 12 条待办，人会先关掉页面。

### 四处实测坑

| 症状 | 根因 | 修法 |
|---|---|---|
| `TypeError: int() argument must be ... not 'list'` | `ladder.json` 里 `failed` 是**计数**、`unknown` 是**明细列表**——同层两个字段形状不同 | `_count()` 两种都收 |
| `styles.ts` 报 `TS1005: ',' expected` | CSS 注释里写了反引号（`` `unknown` ``）→ **截断了模板字面量**（AGENTS §3.1 第 N 次） | 注释里去掉反引号 |
| `f-string expression part cannot include a backslash` | 验收脚本里 `f"...{'case 'verdict'' in src}"` | 先算成布尔变量再插值 |
| 3c 那条"反例"恒为真 | 真实项目的 ranks 恰好是 `[0,0,1,1,1]`，交换前两位不改变序列 | 改用**合成序列** `[0,1,2]` vs `[2,0,1]` |

第二条最值得记：**同一个坑（styles.ts 里的反引号）在三轮之后又踩了一次**。
它不是"不小心"，是模板字面量里写 CSS 这件事本身有陷阱。

### 顺带修好的一个死代码

`service.ts` 早就把 `view` 放进 hint（`hintState.view`），但客户端 `poll()`
**只读 `project`**——于是 `tools.ts` 里 14 处 `viewHint(project, 'checks')`
全是死代码，用户跑完 `hw_ladder` 还得自己点页签。

现在 `poll()` 消费 `hint.view`：认**已知分区**才切（未知字符串忽略，不猜），
切分区同时切分组（否则会出现"选中了不在当前分组里的分区"）。

### 验收（`python -m hw_velxio.scripts.check_p0` → **17 条，17 通过**）

```
空态 2 条
[OK ] 1a  没有 ladder.json → rollup=not_ready 且 levels 为空（不是 pass）
[OK ] 1b  空态 summary 明说"空白不代表通过"

真产物 6 条
[OK ] 2a  层名与顺序和 ladder.json 逐项相同
[OK ] 2b  unknown 折成计数（原字段是明细列表）
[OK ] 2c  rollup 由九层压成：有 fail 就是 fail
[OK ] 3a  下一步 ≤5 条（一屏放得下）
[OK ] 3b  类别按紧急度升序（blocking 最前）
[OK ] 3c  判据 x == sorted(x) 对乱序输入为假（用合成序列）

不自己判定 2 条
[OK ] 4a  六类计数与 handoff 逐类一致（只投影、不重判）
[OK ] 4b  两次 compose 结果一致（排序稳定，不是字典序抖动）

过期 2 条
[OK ] 5a  design.json 比 ladder.json 新 → stale=true（fresh 对照为 False）
[OK ] 5b  stale 时 summary 明说"下面是上次判定"

契约 4 条
[OK ] 6a  snapshot 载荷里带 verdict
[OK ] 6b  verdict 是 health 组第一个分区 + 标签「结论」+ 默认分区
[OK ] 6c  VerdictPanel 存在且 view 里有 case 'verdict'
[OK ] 6d  VerdictPanel 已 import 并渲染（不是只定义）

退化 1 条
[OK ] 7   把类别优先级倒过来 → next 顺序真的变了（3b 会红）
```

6b/6c/6d 是**读 TS 源码**的断言。它们不验"界面好不好看"（那要截图），
只验"契约没被悄悄改掉"——**只定义不接线**这类错误，tsc 是抓不到的。

### 回归

| 项 | 结果 |
|---|---|
| `npm test` | **672 项 OK**（665 + 7 条 verdict 单测） |
| `npm run check` / `npm run build` | 0 错 / 构建 OK（client.js 1.14 MB） |
| 16 个 check 脚本 + check_p0 | 全绿 |
| `check_tools.mjs` | 41 个工具，注册通过 |
| 用户工程 | 无写入（check_p0 用临时工作区） |

### 已知缺口

| 缺口 | 现状 |
|---|---|
| 横条点击只触发"跑判定" | 还没有"点 V2 → 定位到出问题的元件"（那是 P2 的按对象聚合） |
| 徽章口径只算阻断 + 失败层 | 缺数据/默认值不进徽章（它们常驻，进徽章等于没信号）；想看要进别的分区 |
| 结论页不显示层内明细 | 只知道 V2 失败，不知道哪 3 条——点一下跑的是全量判定 |

---

## P1 — finding 可操作化（2026-11-21）

**目标**：把"哪条判据红了"变成"点一下就动手"。

### 数据本来就在，缺的是一层翻译

`physical.py` 早就给每条 finding 塞了：

```python
fix={"need": "move_apart", "refs": ["C146", "Lora1"]}
```

`links.py` 塞了 `fix={"need": "declare_link", "peripheral": ...}`，
`coverify` 塞了 `fix={"need": "faster_servo_or_slower_target"}`。
**问题不在数据，在没有人把 `need` 翻成动作。**

### 交付

| 文件 | 内容 | 规模 |
|---|---|---|
| **[nextstep.py](design/nextstep.py)** | `fix.need` → 四类动作（唯一映射处，28 条） | 329 |
| [check_p1.py](scripts/check_p1.py) | 验收 25 条（含 2 条退化/反证） | 350 |
| `handoff.py` | `Item.fix` + `Item.next`；**blocking 拆成一条条** | ±60 |
| `design_cli.py` | 快照里每条 finding 带 `next` | ±20 |
| `verdict.py` | `next[*].step` | ±8 |
| `types.ts` | `NextStep` + `Finding.next` + `VerdictNext.step` | 30 |
| `panels.tsx` | `StepButton`（复制 / 去改 / 定位）+ `FindingRow` 渲染 | 75 |
| `store.ts` | `focusField` + `selectField()` | 18 |
| `Inspector.tsx` | `focusField` → **滚动并聚焦那个输入框** | 14 |
| `HwIdeView.tsx` | `onSelectField` 一路透传 | 6 |
| `styles.ts` | 小一号的步骤按钮 + 「由谁做」 | 10 |

### 四类动作（没有 `auto_fix`）

| kind | 含义 | UI |
|---|---|---|
| `edit_field` | 改设计里的某个字段，带**实际路径**（`electrical.power_w`） | 选中 + 滚动聚焦那个框 |
| `move_part` | 元件位置问题 | 选中（2D 高亮，人自己拖） |
| `run_command` | 有确切命令 | **复制整条 CLI** |
| `external` | 本工具不做 | 定位 + 写明"由谁做" |

**没有"自动修复"按钮。** 改设计是人的决定，机器只把人送到该看的地方。

### 三条纪律

1. **一张表，不散在各处**。`NEEDS` 是唯一映射处；`fix.need` 出现新值而表里没有
   → 返回 `None`（UI 退回自由文本），**不临时编一个**。
2. **参数不全就降级，不补**。命令模板缺 `{lcsc}` → 降级 `external` 并说
   "先补 lcsc"，**不替你猜一个编号**（猜错会去查错的料，比不动更糟）。
3. **没有靶子就不给按钮**。"整个设计一个申报都没有"这种，`edit_field` 不带 `refs`
   → 按钮禁用并说明"全设计范围"。**挑第一个件当靶子是猜**——
   点"去改"跳到一个随机元件，比没按钮更糟。

### 一处结构改动：blocking 从 1 条变 12 条

`_blocking` 原来把一层里所有失败 finding 拼进**一个**总条目：

```
V2 几何失败 → "C146 与 Lora1 重叠…；C118 超界…；Q3 超界…；Q5 超界…"
```

10 个几何问题挤在一句话里，人没法"点其中一条去改"。现在**一条 finding 一条待办**，
每条带自己的 `fix`，层名放进标题前缀（`V2 · …`）。

**不是"总条目 + 细分条目"两张表**——同一批问题报两遍，人会以为有 20 个
（AGENTS §4.3）。所以换成而不是追加。`示例-多板` 的阻断数因此 2 → 12，
这是**更准**，不是变多。

### 五处实测坑

| 症状 | 根因 | 修法 |
|---|---|---|
| `next` 显示「把 **?** 分开」 | `physical` 把 `refs` 放 finding **顶层**、`fix` 里不带 | `_blocking` 合并 finding.refs 进 fix；`build(refs=)` 也接受 |
| `next` 显示「查  **的**尺寸」（双空格） | 降级空间用原模板填空，`{lcsc}` 变空串 | 降级时给**独立的一句话**，不填空 |
| 3 条 coverify 的 need 没映射 | 表只收了 physical/links 的，漏了 coverify 的三个 | 验收 1a **扫源码**，把这个漏变成红的 |
| 「申报电气参数」点了跳到随机件 | 我在 fix 里塞了 `design.parts[:1]` | **去掉 refs**——没有靶子就不给按钮 |
| 改完 `handoff` 后 `SyntaxError: invalid character '：'` | 用正则替换 `basis=..."))` 时把嵌套括号切错 | 手工改正；改用"锚定唯一 basis 键 + 保留缩进"替换 |

第二条最值得记：**降级不是"填空"，是换一句话**。填空出来的空位读起来像 bug，
而人第一反应是怀疑工具，不是怀疑参数。

### 验收（`python -m hw_velxio.scripts.check_p1` → **25 条，25 通过**）

```
映射表 4 条
[OK ] 1a  源码里出现的 27 个 `fix.need` **全部**在表里（漏一个 = 那条悄悄变回文字）
[OK ] 1b  只用四类动作，且**没有 auto_fix**
[OK ] 1c  每条都有 label
[OK ] 1d  edit_field 都有 field、run_command 都有 command

不编的动作 4 条
[OK ] 2a  表里没有 / 空 fix / None → 都返回 None
[OK ] 2b  命令缺 lcsc → 降级 external，且 label 不含空占位符
[OK ] 2c  参数齐了 → run_command，命令里带真实编号
[OK ] 2d  refs 全部展开（不是只取第一个）

数据链路 6 条
[OK ] 3a  真工程上 200/202 条带动作（四类都有）
[OK ] 3b  每条 blocking/fail 都带动作（11 条，没动作的 0 条）
[OK ] 3c  拆成一条条后层名仍可读（11/11 带 V 前缀）
[OK ] 4a  「板间链路一条都没声明」→ run_command = links --infer --apply
[OK ] 4b  「申报电气参数」→ edit_field = electrical.power_w
[OK ] 4b2 那一条**不**带 refs（全设计范围，挑一个件就是猜）
[OK ] 4c  「尺寸是估算值」→ external + 带具体位号（btn1）

首屏与快照 3 条
[OK ] 5a  verdict.next 5/5 条带 step
[OK ] 5b  快照里失败的 finding 带 next
[OK ] 5c  **通过的 finding 一条 next 都没有**（OK 的不给按钮）

前端接线 5 条
[OK ] 6a  NextStep 类型存在，Finding/VerdictNext 都带
[OK ] 6b  StepButton 存在，判定行与结论行**都渲染**
[OK ] 6c  复制失败不假装成功（catch 把 copied 置回 false）
[OK ] 6d  edit_field 走 onSelectField 一路到 Inspector.focusField
[OK ] 6e  Inspector 真的**聚焦那个框**（不是只存着不用）

退化 2 条
[OK ] 7   从表里拿掉 move_apart → 那些条目真的失去 next
[OK ] 7b  恢复后条数回到原值（测试不留副作用）
```

6b/6d/6e 是**读 TS 源码**的断言：tsc 抓不到"定义了但没渲染"这种错。

### 一屏现在长这样（真工程）

```
不可打板：12 条阻断，V0 ✓ · V1 ? · V2 ✗ · V3 ? · V4 ? · V5 – · V6 –

 1. [阻断] V2 · 7 个元件高度未知（d=0）
      → 去改：给 CARD1、U2、U8… 填高度（size_mm.d）      [定位 → CARD1]
 2. [阻断] V2 · C118 的足迹超出板轮廓 0.25 mm
      → 去改：把超界的元件移回板框内（C118）            [定位 → C118]
 3. [阻断] V2 · C146 与 Lora1 实体重叠 1.60×0.90×0.80 mm
      → 去改：把 C146、Lora1 分开（间距小于安全值）      [定位 → C146]
```

### 回归

| 项 | 结果 |
|---|---|
| `npm test` | **684 项 OK**（672 + 12 条 nextstep 单测） |
| `npm run check` / `build` | 0 错 / OK |
| 17 个 check 脚本（含 P0/P1） | 全绿 |
| 工具注册 | 41 个不变（`hw_snapshot` 的文本里带上了命令） |
| 用户工程 | 无写入 |

### 已知缺口

| 缺口 | 现状 |
|---|---|
| `edit_field` 只聚焦 `electrical.power_w` | 其它字段（`size_mm`、`meta.max_height_mm`）能跳到 Inspector，但没有专门的聚焦逻辑 |
| `external` 类只能"定位" | 要人做的（装 ffmpeg、立创里手动布线）没有勾选/驳回的记录 |
| 没有"我修完了"按钮 | 改完要自己重跑判定；下一步不会自动刷新 |

---

## P2 — 按对象聚合（2026-11-21）

**目标**：点一个元件，**五份产物里关于它的话一次看全**。

### 为什么不是"再开五个分区"

`claims` / `justify` / `checks` / `physical_tests` / `units` 的索引全是 `ref`，
却分在五个页签里。人的问题只有一个：

    U3 这个件，为什么在板上？它现在被验到什么程度？

要回答这一句，得切五次页签、在 316 个元件里各找一遍。
**按对象组织，不按产物组织。** 这是 P2 唯一的设计决定。

### 交付

| 文件 | 内容 | 规模 |
|---|---|---|
| **[entity.py](design/entity.py)** | 按 `ref` 聚合五份产物 + 紧凑索引 + 三态压合 | 319 |
| [check_p2.py](scripts/check_p2.py) | 验收 24 条（含 1 条反证 + 1 条退化） | 331 |
| `design_cli.py` | `entity <id> [<ref>]` + 快照里带 `entities` 索引 | ±45 |
| `types.ts` | `EntitySummary` / `EntityProfile` / `EntityClaim` / `EntityTest` | 68 |
| `service.ts` / `rpc.ts` | `entity` RPC（按需取，不进快照正文） | 12 |
| `store.ts` | `loadEntity()`：选中才拉，**只认最后一次点击** | 34 |
| `Inspector.tsx` | `EntitySections`：归因 / 断言 / 单元 / 实测 | 105 |

### 快照只带一行摘要

316 个元件的完整画像塞进快照会撑到几 MB，而人一次只看一个。
所以：

```
snapshot.entities = { "U3": {status, claims, failed_claims, unknown_claims, findings, tests} }
                     ↑ 只有计数与三态，没有正文
entity <id> U3     = 完整画像（按需取）
```

验收 5b 专门断言**索引里没有正文**——这是"别把快照当数据库用"的闸门。

### 三态压合的顺序，以及为什么

```
实测失败 > 断言失败 > 检查 error > unknown > 无数据(not_ready) > pass
```

实测压最前：真机试过、挂了，比任何推断都硬。
`not_ready` 排在 `pass` 之前：**一条数据都没有不能算通过**（AGENTS §0.1）。

### 缺数据的三处说法**各不相同**

这是这一层最容易被做错的地方——三处都写「无」，读的人就分不清是
"没有"还是"没跑"。

| 缺什么 | 怎么写 |
|---|---|
| 没有归因产物 | 「没有归因产物（跑一次 hw_justify 才有）。**这不等于"没有作用"**」 |
| 没有断言 | 「这个件没有断言（跑一次 hw_claims 才有）。**没有断言不等于没问题**」 |
| 没有实测 | 该栏**不渲染**（实测本来可选，跑过才有） |

验收 6c 断言这三处措辞**互不相同**，防止以后被"统一一下文案"抹平。

### 两处实测坑

| 症状 | 根因 | 修法 |
|---|---|---|
| `TS2300: Duplicate identifier 'findings'` | `EntityProfile.findings`（检查项数组）与 `status='failed'` 时的 `findings`（失败原因字符串）**同名** | 前者改名 `checks`，Python 侧同步 |
| 2d 判「来源非法」，而那个件的 `physical_source` 是合法的 `derived` | 我在验收里**又写了一份**允许值清单，漏了 `derived` | 改成引用 `model.PHYSICAL_SOURCES` 常量——两个清单迟早会分叉 |

第二条和 §1.1（判定只有一份）是同一个道理，只是发生在**常量**上。

### 验收（`python -m hw_velxio.scripts.check_p2` → **24 条，24 通过**）

```
索引 4 条
[OK ] 1a  覆盖设计里每一个位号（316/316，不多不少）
[OK ] 1b  claim 计数之和 = claims.json 的 count（归位没丢，266/266）
[OK ] 1c  `C1` 与 `C10` 不串（用 scope.part 索引，不用 id 正则切）
[OK ] 1d  一条 finding 牵涉两个件时，每个件都记到

画像 5 条
[OK ] 2a  五份齐全（断言/归因/检查/实测/单元）
[OK ] 2b  归因不是空的
[OK ] 2c  没接网络的件 nets 为空，且归因说明"不接任何网络"
[OK ] 2d  尺寸来源在 model.PHYSICAL_SOURCES 里（引用常量，不另写清单）
[OK ] 2e  位号不存在 → status=failed 并说明（不返回空壳）

缺数据 3 条（合成夹具：不写 claims.json）
[OK ] 3a  没有 claims.json → total=0（不是"0 条通过"）
[OK ] 3b  没有 justify.json → confidence=missing（不是 unknown/空）
[OK ] 3c  这份画像的 status 不是 pass

三态压合 5 条
[OK ] 4a  实测失败压过断言通过 → fail
[OK ] 4b  断言 unknown 压过全过 → unknown
[OK ] 4c  一条数据都没有 → not_ready
[OK ] 4d  全过 → pass
[OK ] 4e  检查里 error 级失败 → fail

契约 6 条
[OK ] 5a  快照里带 entities 索引
[OK ] 5b  索引里没有正文（不把快照当数据库）
[OK ] 6a  宿主有 entity RPC，客户端 face 也有
[OK ] 6b  Inspector 真的渲染画像（EntitySections 已挂上）
[OK ] 6c  缺数据的三处说法各不相同
[OK ] 6d  按需加载：选中才拉，不在快照里塞正文

退化 1 条
[OK ] 7    检查项不归位 → 失败件数变少（1d/2a 会红）
```

3a–3c 用**合成夹具**（只有 `design.json`，没有 `claims.json`）：真实工程里
每个件都有断言，这个分支用真实数据测不到（AGENTS §0.3）。

### 回归

| 项 | 结果 |
|---|---|
| `npm test` | **696 项 OK**（684 + 12 条 entity 单测） |
| `npm run check` / `build` | 0 错 / OK（client.js 3.52 MB） |
| 18 个 check 脚本 | 全绿 |
| 工具注册 | 41 个不变 |
| 用户工程 | 无写入 |

### 已知缺口

| 缺口 | 现状 |
|---|---|
| 2D 画布还没按 `entities.status` 着色 | 索引已经有，画布没消费（下一版） |
| 画像里没有 `next`（下一步动作） | 断言失败只给 `on_fail` 文案，没接 `nextstep` |
| 单元层只显示名字与角色 | 板级聚合（P2 的另一半"点板"）没做 |

---

## P3 — 参数敏感度（2026-11-21）

**目标**：136 个参数全 `source=public`（一项都进不了硬判定），
回答"**我该去核哪一个？**"

### 实测答案：一个都不用核，因为 132 个根本没人读

先把事实查清楚：`ParamSet` 的**唯一消费者是 `mjcf.build`**，它只
`params.get` 了 4 个键。另外 1 个（`density_kg_m3`）也被读，
但**不在参数表里**（走 `DEFAULT_DENSITY` 常量）。

```
表内 136 个参数
├─ wired      2  dt / mu_board              改它 MJCF 真的变
├─ wired_noop 2  axle_fric_nm / board_solref 读了，但本设计无对应机制
└─ unwired  132                             **没有任何代码读它**
表外 1 个
└─ missing    1  density_kg_m3              代码读了，但表里没这一项
```

### 这一层的核心：未接线 ≠ 不敏感

把 132 个"没人读"报成"敏感度低"是**假绿**——读的人会以为
"低敏感 = 不重要但评估过了"，而真相是**从未接入**。所以四类各报各的：

| 类别 | 该做什么 |
|---|---|
| `wired` | **核这个值** |
| `wired_noop` | 设计里没这个物理，不用核 |
| `unwired` | 要么补接线，要么从表里拿掉 |
| `missing` | **参数表缺项**，补一行 |

验收 3b/3c 断言 `unwired` 的说明写「没人读」、`wired_noop` 的写「被读取」——
**两套措辞不一样**，防止被"统一文案"抹平。

### 两层敏感度，只有一层靠 MuJoCo

| 层 | 测什么 | 依赖 |
|---|---|---|
| **模型层** | 扰动 → MJCF 文本变不变 | 无（字符串比对） |
| **判定层** | 扰动 → 某层状态翻 | V7（要 MuJoCo） |

**没有 MuJoCo 时判定层是 `unknown`，不是「不敏感」**——AGENTS §0.1 在
参数维度上的复述。验收 4c 专门跑子进程验这一条。

判定层**复用 `sim_calibration.default_runner`**（内部就是 V7），
不另写第二份 MuJoCo 调用——两处阈值早晚分叉（AGENTS §1.1）。

### 交付

| 文件 | 内容 | 规模 |
|---|---|---|
| **[sensitivity.py](design/sensitivity.py)** | 接线扫描 + 两层扰动 + 四类分类 | 285 |
| [check_p3.py](scripts/check_p3.py) | 验收 23 条（含 1 条反证 + 1 条退化） | 340 |
| `design_cli.py` | `sensitivity <id> [--verdict]` + 快照带 `params` 摘要 | ±50 |
| `types.ts` | `ParamsSummaryPayload` / `ParamsPriority` | 28 |
| `store.ts` | 分区 `params`（health 组，结论之后） | 4 |
| `panels.tsx` | `ParamsPanel`：四类各自可见 + 优先级列表 | 76 |

### 快照只带一句话

打开工程不该跑 136 次 MJCF 构建。所以快照只带**分类计数 + 前几行**：

```
snapshot.params = { total: 136, counts: {...}, priority: [2 行] }
sensitivity <id> = 完整 136 行
```

实测成本：`snapshot` 全量 **0.4s**（316 件的板也这么快，因为只对 4 个
wired 键 build）。

### 四处实测坑

| 症状 | 根因 | 修法 |
|---|---|---|
| `range_deg`/`ratio`/`net` 被报成"参数表缺项" | 正则把 `joint.params.get(...)`（**关节私有参数**）也抓了 | 加后向断言 `(?<![\w.])`；验收 1b 守住 |
| 四类之和 137 ≠ 136 | `missing` 是**表外**项，不该算进表内总数 | 报告加 `in_table_counts`（三者之和 = 总数）与 `off_table` |
| `_READ_RE` 整个消失 → `NameError` | 我用 CRLF 文件做 LF 字符串替换，前一次把定义删了没补回 | 按 `

` 拼接重插；**这个仓库的 Python 文件是 CRLF** |
| f-string 里 `\'` 报 SyntaxError | `f"...{'case \'params\':' in view}"` | 先算布尔变量再插值（第三次踩，AGENTS §8.2） |

### 验收（`python -m hw_velxio.scripts.check_p3` → **23 条，23 通过**）

```
分类 5 条
[OK ] 1a  wired_keys **就是** mjcf 源码里的 params.get 集合（表随代码走，不写魔数）
[OK ] 1b  joint.params.get(...) 不算 ParamSet 接线
[OK ] 1c  表内三类之和 = params_total；missing 是表外项
[OK ] 1d  真工程上未接线占绝大多数（132/136）
[OK ] 1e  density_kg_m3 被读但不在表里 → 报 missing

排序 2 条
[OK ] 2a  priority 只含 wired，且**已排序**
[OK ] 2b  每个 wired 都记了哪些扰动方向有响应

未接线 ≠ 不敏感 4 条
[OK ] 3a  mu_tyre_rod 在表里但没人读 → 归 unwired（不是 wired_noop）
[OK ] 3b  每条 unwired 的说明写「代码读/没人读」，**不写「不敏感」**
[OK ] 3c  wired_noop 的说明都说清「被读取了」（与 unwired 区分开）
[OK ] 3d  反证：unwired 的行 status 不是 wired_noop

判定层 3 条
[OK ] 4a  每个扰动都返回 baseline 与 perturbed
[OK ] 4b  状态落在 pass/fail/unknown
[OK ] 4c  **没有 MuJoCo 时是 unknown**（子进程真跑，不是不敏感）

契约 6 条
[OK ] 5a  CLI sensitivity 返回四类计数
[OK ] 5b  快照带 params 摘要（total=136）
[OK ] 5c  快照只带优先项（不是 136 行全表）
[OK ] 6a  params 是 health 组分区，标签「参数」
[OK ] 6b  ParamsPanel 存在且 view 里有 case 'params'
[OK ] 6c  面板上四类**各自可见**
[OK ] 6d  面板不重排 priority（排序只在 Python 一份）

退化 2 条
[OK ] 7   去掉后向断言 → 多抓出关节私有参数（1b 会红）
[OK ] 7b  恢复后 wired 集合回到原值
```

### 回归

| 项 | 结果 |
|---|---|
| `npm test` | **705 项 OK**（696 + 9 条 sensitivity 单测） |
| `npm run check` / `build` | 0 错 / OK（client.js 3.53 MB） |
| 19 个 check 脚本 | 全绿 |
| 工具注册 | 41 个不变 |
| 用户工程 | 无写入 |

### 已知缺口

| 缺口 | 现状 |
|---|---|
| 132 个参数未接线 | **这是结论，不是 bug**：要接哪个由人定；现在至少能看见 |
| 判定层只验了 `dt`/`mu_board` | 其它 wired 键（`axle_fric_nm`/`board_solref`）在本设计里 noop |
| 扰动只有 ×0.5 / ×2 | 非线性参数可能两点都迟钝；可加 ±10% 细扫 |
| 没有"接上这个参数"的工具 | `unwired` 只告诉你没接，不给改代码的按钮 |

---

## P4 — 固件矩阵（2026-11-21）

**目标**：`pinmap.json` / `pinmap_crosscheck.json` 是**唯一还没有界面**的产物。
把它们变成"每脚一行、一眼看完"的矩阵。

### 为什么不是"三张问题表"

`pinmap-check` 输出的是 ghost / idle / direction **三张表**：

```
ghost:     [ {pin: PA11, ...} ]
idle:      [ {pin: PB9, ...} ]
direction: [ {pin: PA5, ...} ]
counts:    {ghost: 1, idle: 2, direction: 1}
```

问题在于：**没有问题的引脚不在任何一张表里**。于是"这个脚到底配没配、
用没用"只能靠人拿 `.ioc` 和源码对——而那正是矩阵该干的事。

```
引脚    硬件配置(.ioc)      固件使用(src/)     结论
PA5     —                  app.c:10 (write)   幽灵引脚（硬件没配）
PA15    out / VL53-PWR-EN  —                  闲置（配了没用）
PA3     out / EN           app.c:12 (write)   ✓
PA13    Serial_Wire        —                  ✓（调试脚，不算闲置）
```

### 交付

| 文件 | 内容 | 规模 |
|---|---|---|
| **[firmware_matrix.py](design/firmware_matrix.py)** | 每脚一行 + 四类状态 + 摘要 | 282 |
| [check_p4.py](scripts/check_p4.py) | 验收 25 条（含 1 条对照 + 1 条退化） | 375 |
| `design_cli.py` | `fw-matrix <id> [--ioc=] [--src=] [--ref=]` + 快照带摘要 | ±75 |
| `types.ts` | `FirmwareSummaryPayload` / `FirmwareRow` / `FirmwareUse` | 42 |
| `store.ts` | 分区 `firmware`（delivery 组） | 3 |
| `panels.tsx` | `FirmwarePanel`：矩阵表 + 四类计数 + 降级说明 | 145 |
| `styles.ts` | 四类状态色 | 14 |

### 三条最容易犯的错（各有一条断言）

1. **幽灵引脚整行消失**。它硬件里没有配置，所以不在"已配置引脚"里。
   只遍历已配置脚的矩阵会把**唯一运行时必炸**的那类漏掉。验收 1a + 退化 8。
2. **调试脚被算成闲置**。SWD/JTAG 由调试器用，不该出现在应用代码里。
   `crosscheck` 已排除，矩阵**不能加回来**（否则凭空多一堆"闲置"，人就不看了）。验收 2a。
3. **没有配置时报"全对"**。空集合不能算通过。验收 3a/3b 都返回 `not_ready`。

### 四处实测坑

| 症状 | 根因 | 修法 |
|---|---|---|
| **`cmd_firmware` 撞名** | 我以为这名字没人用，而 **P20 早就有一个 `cmd_firmware`**（源码文本检查）。字典字面量里同键**后者胜** → 原命令被静默顶掉 | 改名为 `cmd_fw_matrix` / `"fw-matrix"`；验收 6a 守住原命令还在 |
| `extract_uses` 抛 `ValueError: ... is not in the subpath of ...` | `relative_to(root)` 遇到工程外的 `--src`（用户的固件常在另一个仓库） | 加 try/except 退回 `path.name`；`root` 只用于显示，**不是约束** |
| 报告里 `scanned` 恒为 0 | `extract_uses` 返回的键叫 **`files`**，我猜成了 `scanned`（AGENTS §3.2） | 用真键名；验收 6c 断言它 > 0 |
| `IocPin(... reserved_debug=True)` → TypeError | `reserved_debug` 是**派生属性**（由 mode/signal 推），不是字段 | 构造时只给 mode/signal，让推导自己算 |

第一条最值得记：**P20 已经有 `firmware` 命令**，我按"P4 是固件矩阵"就占了那个名字。
字典同键不报错、不警告，只是**悄悄换掉**——和 §3.13 的"字段没人读"是同一形状的
静默失效，只是这次发生在**注册表**上。

### 验收（`python -m hw_velxio.scripts.check_p4` → **25 条，25 通过**）

```
四类出行 5 条
[OK ] 1a  幽灵引脚**出行**（硬件没配，固件在用）
[OK ] 1b  已配置的脚每脚一行（ok 的也在，不是只列问题）
[OK ] 1c  行数 = 配置脚数 + 幽灵脚数
[OK ] 1d  四类计数之和 = 总行数
[OK ] 1e  状态判定与 crosscheck 一致（只投影，不另判）

调试脚 2 条
[OK ] 2a  SWD 脚**不算闲置**（crosscheck 已排除，矩阵不能加回来）
[OK ] 2b  真闲置的脚仍然是 idle

空集合 2 条
[OK ] 3a  硬件侧一个配置都没有 → not_ready（不是 ok）
[OK ] 3b  只有固件用法、没有硬件配置 → 仍然 not_ready

真 .ioc 4 条
[OK ] 4a  行数 = 配置脚 + 幽灵脚（13 + 1）
[OK ] 4b  配了没用的脚被报成 idle（有据可查）
[OK ] 4c  往不存在的脚写 → ghost
[OK ] 4d  行按严重度排序（ghost 最前）

摘要 2 条
[OK ] 5a  摘要里只有有问题的行（ok 行不进快照）
[OK ] 5b  摘要保留计数与总数（不因截断丢信息）

契约 6 条
[OK ] 6a  `firmware` 仍是 P20 的检查（没被矩阵命令顶掉）
[OK ] 6b  `fw-matrix` 是矩阵命令
[OK ] 6c  报告里带扫了几个文件（`files` 计数，不是猜的键名）
[OK ] 6d  快照里一定有 firmware 字段（没配置时 not_ready + 原因）
[OK ] 7a  firmware 是 delivery 组分区，标签「固件」
[OK ] 7b  FirmwarePanel 存在且 view 有 case

面板 2 条
[OK ] 7c  四类各自可见（幽灵/冲突/闲置/核对过）
[OK ] 7d  not_ready 时说清「这不是全对」

退化 2 条
[OK ] 8   只遍历已配置脚 → 幽灵脚整行消失（1a 会红）
[OK ] 8b  对照组：正常实现里它是在的
```

1/2/3 用**合成夹具**（真实 `.ioc` 里恰好没有方向冲突、也没有"幽灵+调试"同框）；
4 用**真 `.ioc`**（F401-Front，13 个配置脚）——AGENTS §0.3 那个区分的第四次应用。

### 真机跑了一遍

```
fw-matrix 001-blink --ioc=F401-Front.ioc --src=F401-Front-master/App
→ status ok | mcu STM32F401RCT6 | 扫了 19 个文件
  counts {ok: 10, ghost: 0, direction: 0, idle: 3}
  idle PA15 / PB9 / PC0（都带人起的名字：VL53-PWR-EN、LPN、I2C_RST）
```

### 回归

| 项 | 结果 |
|---|---|
| `npm test` | **713 项 OK**（705 + 8 条 matrix 单测） |
| `npm run check` / `build` | 0 错 / OK（client.js 3.55 MB） |
| 20 个 check 脚本 | 全绿 |
| 工具注册 | 41 个不变 |
| 用户工程 | 无写入（`fw-matrix` 写的是夹具工程 `001-blink` 的产物） |

### 已知缺口

| 缺口 | 现状 |
|---|---|
| **没有做成"第三块画布"** | 现在是**底部面板里的矩阵表**，不是独立画布。做画布要先改 `Layout`（split/2d/3d）与把手体系，那是另一档工作量；表格已经能回答"配没配、用没用" |
| 快照里的矩阵要扫源码 | 每次 `snapshot` 都跑 `extract_uses`；工程大时会慢，可以按 mtime 缓存 |
| 只认 STM32 HAL 的单行配对 | Arduino/ESP-IDF 报 `not_ready`（`crosscheck` 的口径，不是本层） |

---

## P5 — 需求草稿 + 确认闸门（2026-11-21）

**目标**：把"一句话"变成**可校验的结构化需求**，并且**人确认后才生效**。

### 推翻了两条错前提（这是本节最重要的部分）

| 初版说法 | 实测 | 结论 |
|---|---|---|
| 「0 个 provider，agent_loop 只有假模型」 | `@deepseek-ai/dsh-llm` 的 `ctx.llm` 由 `dsh-base` 挂载；**`dsh-our-free-model` 2.8.3 就是适配器**；hw_velxio 早在用 `session.prompt()` 投递 | **P5 不写 provider** |
| 「字段是 `units` / `links`」 | `Requirement` 是 `id/title/roles/interfaces/constraints/numbers/notes` | 照真契约校验 |

第二条错得很典型：我照着 `generate` 的**内部**结构猜外部契约。正确做法是
`sed -n '53,112p' design/generate.py` 读一遍——那正是 AGENTS §3.2 的"对着真实输出核"。

`agent_loop.py`（P22）是**内层循环**（0 token 回归用），和 DSH 会话是**两条并行会话**。
把 P5 建在内层上 = 在已经有会话的地方再造一条。**先例**：hwforge 试过在宿主侧投递，
三轮花在 `agent vs agentId` / `ctx.get vs ctx.sessions` / `inject` 上，结论是
**「host composes, client delivers」**——hw_velxio 现在正是这个形状。

### 第二个实测发现：`Requirement.numbers` 没有任何消费方

`numbers` 只在 `from_dict` / `to_dict` 里过一遍，**没有任何模块读 `req.numbers`**
（验收 3c 扫整个 `design/` 的**读取形状** `req.numbers` / `.numbers.get(` / `.numbers[`
来复核）。`constraints` 也只有 `area_mm2` 被 `capability.candidates` 消费。

含义：**模型填了 `numbers: {detect_range_m: 2.0}`，它不会影响任何结论。**
这和 P3 的"132 个参数没人读"是同一形状（§3.16），所以 `validate()` 把它单列成
`field_without_consumer` **warning**——不拦 apply，但一定说出来。

### 交付

| 文件 | 内容 | 规模 |
|---|---|---|
| **[req_draft.py](design/req_draft.py)** | 草稿 + 校验 + 三道闸门 | 305 |
| **[docs/p5-plan.md](docs/p5-plan.md)** | 修正后的方案（含被推翻的两条） | 150 |
| [check_p5.py](scripts/check_p5.py) | 验收 **29 条** | 405 |
| `design_cli.py` | `req-draft <id> [--set/--validate/--apply --confirm]` + 快照带 `req` | ±110 |
| `src/tools.ts` | `hw_req_draft`（模型可调）+ `hw_req_apply`（**人的动作**） | ±175 |
| `types.ts` / `store.ts` / `service.ts` / `rpc.ts` | `req` 分区 + `applyReqDraft` RPC | ±90 |
| `panels.tsx` | `ReqPanel`：角色 / 接口 / 问题 / 「填了但没人读」/ 确认按钮 | ±175 |
| `design/store.py` | `ARTIFACTS` 补 `req_draft.json` / `req.json` / **`firmware_matrix.json`**（P4 漏登） | 3 |

### 三道闸门

```
草稿（模型可反复改）→ validate → 人确认（confirm）→ 才写 req.json
```

| 闸门 | 拦什么 | 出口 |
|---|---|---|
| `validate` | 未知 role / 悬空 interface / 自环 / 空 roles / 负约束 | 改草稿再 validate |
| `apply` 要 `confirm` | 未经人确认不能变需求 | 人点「确认并应用」 |
| `numbers` 无消费方 | 填了却不影响任何结论 | **报告里说清** |

**草稿与需求是两个文件**，因为合成一个的话"模型写了但人没看"和"人确认了"
在磁盘上长得一模一样。`apply` 是唯一跨越那一步的动作。

### 五个实测坑

| 症状 | 根因 | 修法 |
|---|---|---|
| `check_p5` SyntaxError | 断言文案里用了中文引号 (`"没什么可查"`) —— **第四次**（§3.3） | 换「」 |
| 3c 自己把自己判红 | 按"行里有 numbers"筛，把 `generate.py:57` 的**文档字符串**当成读取点 | 判据精确到读取形状 |
| `req-draft` 加进 `COMMANDS` 前没 grep | 这次先查了（§3.17 的教训）；`req-draft` 无冲突 | — |
| `4f` 崩：`工程 'p5-fixture' 不存在` | `snapshot` 要 `design.json`——**工程先存在才谈需求** | 夹具补 `design.json`；同时记成真实缺口 |
| 参数 `type: 'object'` 缺 `additionalProperties` | DSH 的 `ObjectValueSchemaSpec` 要求它 | 补 `additionalProperties: true` |

第四条不是我的夹具问题，是**P5 的真实边界**：从零一句话开局时工程还不存在，
而 `req-draft` 挂在已有工程上。见下方已知缺口。

### 验收（`python -m hw_velxio.scripts.check_p5` → **29 条，29 通过**）

```
契约 3 条
[OK ] 1a  ReqDraft 字段 == Requirement 字段（**比对**，不抄清单）
[OK ] 1b  已知角色取自 roles.ROLE_TEMPLATES
[OK ] 1c  CONSUMED_CONSTRAINTS 只列源码里真读过的键

校验 7 条
[OK ] 2a  合法草稿 → ok
[OK ] 2b  空草稿 → 不通过（roles_empty）
[OK ] 2c  未知角色 → 拦住，且列出可用角色
[OK ] 2d  悬空 interface → 拦住
[OK ] 2e  自环 → 拦住
[OK ] 2f  area_mm2 为负 → 拦住
[OK ] 2g  interface 项不是对象 → 记 _invalid 并报出来（不静默丢）

numbers 4 条
[OK ] 3a  填了 → 报 field_without_consumer（**不是「低影响」**）
[OK ] 3b  它是 warning 不是 problem：不拦 apply，但一定说出来
[OK ] 3c  整个 design/ 里没有 `req.numbers` 的读取点（结论可复核）
[OK ] 3d  未消费的 constraints 键也报（constraint_unread）

端到端 6 条（真 generate）
[OK ] 4a  草稿落盘且**不碰** req.json（两个文件）
[OK ] 4b  草稿合法但没 confirm → 拒绝且不写
[OK ] 4c  校验不过时**即使 confirm=true 也拒**（闸门有先后）
[OK ] 4d  校验过 + 确认 → 写 req.json，内容与草稿一致
[OK ] 4e  应用后的 req.json 能被 `generate --fast` 消费（出 2 个候选）
[OK ] 4f  快照带 req 摘要（draft / validation / applied）

前端与工具 6 条
[OK ] 5a  hw_req_draft 与 hw_req_apply 都注册（43 个工具）
[OK ] 5b  hw_req_apply 的描述写明**这是人的动作**
[OK ] 5c  req 是 delivery 组分区，标签「需求」
[OK ] 5d  ReqPanel 存在且 view 有 case
[OK ] 5e  应用按钮校验没过时**禁用**（不是点了才报错）
[OK ] 5f  有 applyReqDraft 的宿主 RPC

退化 3 条
[OK ] 6a  对照组：合法草稿本来 0 problem
[OK ] 6b  退化：不查悬空 interface → 那条草稿变成「通过」（2d 会红）
[OK ] 6c  恢复后重新拦住
```

4e 走**真 `generate --fast`**（不是 mock）：校验通过 ⇒ generate 真能枚举出候选。
按 AGENTS §0.3 的区分，1/2/3 用合成夹具证明判据本身对，4 用真链路证明它有用。

### 回归

| 项 | 结果 |
|---|---|
| `npm test` | **723 项 OK**（713 + 10 条 req_draft 单测） |
| 21 个 check 脚本 | 全绿 |
| `npm run check` / `build` | 0 错 / OK（3.57 MB） |
| 工具注册 | **43 个**（41 + `hw_req_draft` + `hw_req_apply`） |
| 用户工程 | 已清理（001-blink 的 `req_draft.json` / `firmware_matrix.json` 已删） |

### 已知缺口

| 缺口 | 现状 |
|---|---|
| **从零开始没有入口** | `req-draft` 挂在**已有工程**上（要 `design.json`）。一句话从零开局要先 `hw_new`——这条断链是 P5 唯一的真缺口 |
| 草稿只能整体替换 | 没有"改一个字段"的动作；模型要重发整份（小，但值得知道） |
| `numbers` 没接消费方 | `coverify` 本可以用它，但没接；现在只能如实报"没人读" |
| 面板不能编辑草稿 | 只能看 + 确认。改草稿要走会话（`hw_req_draft`）或 CLI |

---

## P5b — 补「从零开局」断链（2026-11-21）

**目标**：让"说一句话就开工"真的能走通——**需求可以先于工程存在**。

### 断在哪

`req-draft` 原本只写 `<project>/req_draft.json`，而工程目录要有
`design.json` 才算存在。于是从零开始时：

```
req-draft mybot --set={...}   → 工程不存在 → 写不进去
snapshot(mybot)               → FileNotFoundError（工程都没有）
```

`check_p5` 的 `4f` 就是崩在这条上。当时我记成了缺口（AGENTS §3.19），
现在补上。

### 补法：草稿按「工程在不在」决定落点 + apply 顺手建工程

```
工程已存在 → <project>/req_draft.json
工程还没有 → <工作区>/_drafts/<id>.json        ← 新增
```

`apply --confirm` 时工程不存在 → **顺手建一个空工程**（板卡预设 + 空账本 +
`auto_lock` + 落盘），再写 `req.json`。所以完整链路是：

```
hw_req_draft(action="draft", requirement={...})   # 工程还没有 → 工作区草稿
hw_req_apply(confirm=true)                        # 建工程 + req.json
hw_generate(requirement=<project>/req.json)       # 出候选
```

### 改了什么

| 文件 | 改动 |
|---|---|
| `design/req_draft.py` | `DRAFT_DIR=" _drafts"`、`_workspace_draft_path`、`draft_scope`、`list_workspace`、`_create_project`；`save/load/apply` 都按 scope 走 |
| `design_cli.py` | `--board=` / `--title=`；`--list` 列工作区草稿；快照 `req` 带 `scope` / `project_exists` |
| `src/tools.ts` | `hw_req_draft(action="list")`；`hw_req_apply` 带 `board`，render 里报"已顺手建工程" |
| `types.ts` / `panels.tsx` | `req.scope` / `project_exists`；面板显示「工程还没有」，按钮文案变「建工程并应用」 |
| `scripts/check_p5.py` | 新增 **9 条**（7a–7i） |

### 一个必须写下的教训：apply 之后草稿要搬进工程

第一版 apply 只写了 `req.json`，**工作区那份草稿留在原地**。后果是两份副本：
工程级与工作区级。之后改其中一份，另一份不会跟着变，而"改的是哪一份"
在报告里分不出来——这正是这个仓库反复要避免的**两个事实源**。

验收 `7h` 就是抓这个的：断言 `draft_scope == "project"`、
工作区那份**已删**、`workspace_draft_removed is True`。搬完之后面板才看得见
（`snapshot` 按工程走）。

### 验收（`check_p5` 从 29 → **38 条**，全通过）

```
从零开局 9 条
[OK ] 7a  工程确实还不存在（本组前提，不是假设）
[OK ] 7b  工程不存在 → 草稿落到工作区 `_drafts/<id>.json`
[OK ] 7c  这份草稿能读回来（load 先看工程再看工作区）
[OK ] 7d  没确认 → 拒绝，且**工程不会被建出来**（建工程在确认之后）
[OK ] 7e  确认 → 顺手建工程 + 写 req.json
[OK ] 7f  建出来的工程**是合法 DesignModel**（validate() 空）
[OK ] 7g  建工程走了 auto_lock（deferred.json 已锁决策）
[OK ] 7h  apply 后草稿**搬进工程**（工作区那份已删）
[OK ] 7i  搬完后工作区列表里不再有它（没有第二份副本）
```

7f 与 7g 不是凑数：不 `validate()` 的"工程"是空壳；不 `auto_lock` 的话
导出闸门会问「mcu_family 未闭合」而用户以为自己漏了什么
（`cmd_put` 那段注释里记着同一个坑）。

### 回归

| 项 | 结果 |
|---|---|
| `npm test` | **729 项 OK**（723 + 6 条从零开局单测） |
| 22 个 check 脚本 | 全绿（P19 的 2 条真机联网检查 `not_ready`——网络超时，**不是回归**） |
| `npm run check` / `build` / `check_tools` | 0 错 / OK / 通过 |

### 仍缺

| 缺口 | 现状 |
|---|---|
| 面板看不到工作区草稿 | `snapshot` 按工程走；工程不存在时面板不渲染 `req`。CLI `req-draft --list` 能列，会话里 `hw_req_draft(action="list")` 也能列 |
| 建工程的板卡默认 `custom` | 给的是诚实的 50×30 通用板；要别的板就 `--board=arduino-uno` 之类 |

## P24–P32 — 从「画得出来」到「能打板」（2026-11-21 晚）

> 起点：拿到大佬手册后重测 P17，发现「自动布线布不出来」的真因是自己板框 Y 写反
> （见上面 P17 的 F8 更正）。修掉之后整条出板链才可能闭环，于是这一轮把
> **多板 / 自研布线 / 几何判定 / 铺铜 / 制造输出 / DRC 分级** 一次做完。

### 实测结果（一块 2 板 4 件的合成板，两步都真跑）

| 段 | 结果 | 证据 |
|---|---|---|
| project | ok | 2 块板，`createProject` 竞态已修（等用户态 + 24 次重试）|
| schematic | ok | 4 件装成 / 8 条短线+网名 / `unresolved=0` |
| pcb | ok | 板框 2/2、元件 4/4 按设计坐标、`importChanges` 轮询到位 |
| route=native | **ok** | 4/4 网、16 段走线、0 过孔、`split_nets=0`（几何判定）|
| route=ours | **ok** | 栅格 158×119 / 79×60，4/4 网、12~25 段、0 过孔 |
| pour | **ok** | 双面都有铜：顶层 7/4 fills、底层 1/1 fills（放 6 个缝合过孔之后）|
| outputs | **ok** | 7 个产物全部落盘：Gerber zip 14.2KB × 2、BOM 6.7KB、坐标 6.9KB、`.epro2` 35.9KB |
| drc | 结论 **ok** | 可信类别（clearance/netlist/physical）**0 条**；Connection Error 4 条（不可信）|

### 这一段新增的能力

| 能力 | 文件 | 关键判据 |
|---|---|---|
| 几何回读 | `design/pcb_geo.py` | 焊盘/走线/过孔/板框源数组，落 `pcb_geometry` 形状 |
| **连通性判定** | `design/pcb_geo.connectivity` | 并查集，**不依赖 DRC**；空输入 → `not_ready` |
| 自研布线器 | `design/router.py` | 栅格 A*，8 邻域只出 0/45/90 度；栅格超限要明说 |
| 缝合过孔 | `router.stitch_sites` | 避开别的网络的铜；板挤就返回空并照实说 |
| 铺铜 | `emit._run_pour`（新 job `pour`） | `getCopperRegion()` 有铜才算成 |
| 制造输出 | `emit._run_outputs` | 生成 File → `arrayBuffer` → base64 → **Python 写盘** |
| DRC 分级 | `emit._drc_categories` | clearance/netlist/physical 可信，connection 只提示 |

### 这一轮踩到 / 修掉的 6 个坑（都写进 AGENTS 了）

| # | 症状 | 根因 |
|---|---|---|
| 1 | `createProject` 抛 `Cannot read properties of undefined (reading 'username')` | 会话刚开、用户态还没装载（等 5 秒就正常）。第一版没包 try/catch，整个发射在第 1 步 failed |
| 2 | 多板时第一块板的网络一条走线都没有（"布通 2/3"） | `openDocument` 返回 true ≠ 文档已激活；换板后只等 900ms 就布线，布在了上一块板上 |
| 3 | 底层铺铜 `pourFills = 0`（顶层 7） | 孤岛被丢弃（`preserveSilos:false`）：底层那片铜和任何焊盘无电气连接。要放**缝合过孔** |
| 4 | DRC 分类 4 条全落进 `other` | 分组树里网络名（GND/A1）也是带 `name` 的节点，把父节点的「Connection Error」覆盖了 |
| 5 | 产物名 `bom.xlsx.xlsx`、`src.epro2.epro2` | `File.name` 已经带扩展名，又拼了一次 |
| 6 | 工程只存在立创库里，磁盘上没有 `.eprj3` 文件夹（`getProjectsPaths` 被拒） | 补导出 `.epro2`（实测是合法 zip：`project2.json` + `IMAGE/`） |

### 验收

```
python -m hw_velxio.scripts.check_p26     # 20 条，全离线（几何 + 连通性 + 布线器）
python -m hw_velxio.scripts.check_p17     # 26 条（真机那几条要装 lceda）
npm test                                  # 742 项（新增 13 条 pcb_geo/router 单测）
```

## P24–P32 续 — 分批发射 / 多板稳定 / 布局规则 / 通道封装（2026-11-21 深夜）

> 用户原话：**「我允许运行时间长很多,但需要多板能成功运行」**。所以这一轮
> 一切以"多板 + 大板都不整段失败"为准，验收用的是 **2 块板 × 12 个器件**
> （24 件、48 根引脚线、两条网络各 12 焊盘）的合成板。

### 实测结果（一条命令跑完 8 段，耗时 181s）

| 段 | 状态 | 计数 |
|---|---|---|
| project | ok | 2 块板 |
| **layout** | ok | 检查 24 件；`--autolayout` 前 6 处违规 → 后 **0**（重排 24 个）|
| schematic | ok | 装 24 件 / 48 根接线 / **分 4 批** |
| pcb | ok | 板框 2/2、元件 24/24（**分 4 批**摆放）、`save=true` |
| route=native | ok | 113 段走线、0 过孔、铺铜前 4/4 连通 |
| pour | ok | 2 块板、GND 双面 + 缝合过孔 |
| **verify** | ok | **4/4 条网几何连通（含铺铜）**，两块板都 ok |
| outputs | ok | 7 个产物全落盘 |

### 新增/改动的能力

| 能力 | 位置 | 关键点 |
|---|---|---|
| 器件分批 | `_JS_SCHEMATIC` + `SCH_PART_BATCH=6` | 每批 ≤6 件，失败只损失一批 |
| 接线的分批 | `_JS_SCHEMATIC_WIRE` + `SCH_WIRE_BATCH=12` | 与装器件**分成两段**（耗时差 10 倍）|
| 摆放分批 | `_JS_PCB_MOVE` + `PCB_MOVE_BATCH=6` | 141 件也不整段超时 |
| 导入补导 | `_run_pcb` | 12 件里偶尔 1 件 8s 才落地；补导一次（幂等）|
| **布局规则** | `design/pcb_layout.py`（新） | 连接器出板 / 中心距 120 mil / 包围盒重叠 / 边距 |
| 自动重排 | `pcb_layout.autoplace` + `--autolayout` | 只保证不短路，不冒充"最优摆放" |
| **通道封装** | `design/lceda_channel.py`（新） | CLI + Bridge 同一套 `invoke`；不可用就抛、不偷偷降级 |
| **verify 段** | `_run_verify`（新 job） | 铺铜**之后**的最终连通性 |

### 这一轮又修掉的 5 个坑

| # | 症状 | 根因 |
|---|---|---|
| 1 | 12 件的板只报 11 件（`missing_on_pcb: 0` 却自相矛盾）| `importChanges` 之后图元**陆续落地**，只等 6 秒不够；且元件数用了第一次读的快照 |
| 2 | 多板时 Board2 的 GND 判成 4 个孤岛 | 引擎对 12 焊盘的地网只布了一部分；而**铺铜才是地的通路** → 判定必须在铺铜之后（新增 verify 段）|
| 3 | 判定器对矩形焊盘用 `min(hw,hh)` → 误报断开 | 0402 是 16×17 mil，从长边贴上来的线被算成没接触；改成**线段到矩形**的真实距离 |
| 4 | 先铺地再铺 3V3，3V3 一片铜都没有（`pourFills=0`）| 同层多张铺铜**互相吃光**；反过来的顺序则地没了。两层板的正解：**地铺整板，其它电源按 20 mil 宽走线** |
| 5 | 布局检查报 `(1535.4, 236.2) mm` 这种坐标 | 内部换成了 mil，打印时没换回 mm（单位混用，AGENTS §3.3 的变体）|
| 6 | 3 个器件 `placed=3`/`save=true`，PCB 上只有 2 个，`parity.missing=2` | 分批把「装器件」「接线」拆开后**没在导入前 save 原理图**；`importChanges` 读的是已提交源码，且 save 之后还要等 ~1.2s（AGENTS §3.30）|

### 验收

```
python -m hw_velxio.scripts.check_p26   # 28 条，全离线（几何/连通/布线/布局/通道）
npm test                                # 751 项（新增 9 条：布局 6 + 通道 3）
```

## P24 — Bridge 通道真机验证（2026-11-22 凌晨）

> 用户装好官方扩展后，第一次真机把 `--channel=bridge` 跑到了底。

### 先排掉的两个误会

| 现象 | 真相 |
|---|---|
| 扩展报 `Daemon not found — retrying` | 用户先装的是**第三方 EDA Agent Connector 1.18.0**（`zhoushoujian`），它要自己的 `easyeda-agent`（代码里 `DAEMON_PORT = 60832`）。我们的是 `easyeda-bridge`（49620–49629）——**不是同一个 daemon** |
| `edaConnected: false` | 官方 **Run API Gateway 1.0.8**（EasyEDA）装上后，握手 `service === "easyeda-bridge"`、`register {windowId}` 与我们的 `bridge-server.mjs` **完全对得上**；勾「允许外部交互」+ 完全重启后即 `true` |

### 实测能跑 / 不能跑

| 操作 | 结果 |
|---|---|
| `/health`、`/eda-windows` | ✅ `edaConnected: true`，1 窗口 `4c056d72…` |
| 单次 `invoke`（读工程/读网表） | ✅ 毫秒级 |
| **整条 8 段发射** | ✅ **一次**（76s）：project/layout/schematic/pcb/route/pour/verify/outputs 全 ok，`channel.kind=bridge` |
| 重复发射（窗口里已开着工程） | ❌ 撞 30s：`openProject 连续两次返回 false` → 失败 |
| 叠层/网络类/差分对 | ❌ 单次就超 30s |

⇒ 结论写进代码与报告：**Bridge 用于「必须在用户窗口里做的短操作」，长任务用 CLI**。
`emit` 里 bridge 通道会自带一句说明；`BridgeChannel.invoke` 现在**把 HTTP 500 的 body 读出来**
（原来只报 "HTTP 500"，等于什么都没说）。

### 同一轮修掉的真 bug：原理图第一根 label 被吞

| 症状 | 根因 | 修法 |
|---|---|---|
| `wired=4` 全 ok，但 `parity.missing=1`（R1.1 的 `A1` 没了），`verify` 只判 1 条网还报 ok | `openDocument` 之后**立刻**发第一条 `addNetLabel`，EDA 吞掉它（返回值仍是 ok） | ① 接线前再等 600ms ② **写完回读** `sch_Netlist.getNetlist('JLCEDA')` 逐引脚核对，不一致重试一次，仍不对 → `not_ready` |

修后 CLI 实测：`pin_nets_ok=4 / pin_nets_bad={}`、`parity ok(0)`、`verify 2/2`。
另外把两个"静默"堵上：**parity 不一致 → pcb 段 not_ready**；
**设计里有、板上不足 2 焊盘的网 → verify 记 `not_connected_vs_design` 并 not_ready**。


## P40 — 机械链打通：`mjcf` 命令（2026-11-22）

> 计划见 [`docs/plan-v5.md`](docs/plan-v5.md) P40。这一段只做一件事：
> 把 `mjcf.build()` 从**死代码**接出来，并且把判据落在「MuJoCo 真的编译过」上。

### 新增

| 位置 | 内容 |
|---|---|
| `design/mjcf.py` | `load_check()`（`ok`/`not_ready`/`failed` 三态）、`_range_pair()`、`describe().param_shapes` |
| `design_cli.py` | `cmd_mjcf` + `COMMANDS["mjcf"]`（`--build/--free/--weld/--no-floor/--out=`） |
| `design/store.py` | `ARTIFACTS` 加 `model.xml` |
| `design/agent_tools.py` | `hw_mjcf` ToolSpec（`arg_style="eq"`） |
| `src/tools.ts` | `hw_mjcf` DSH 工具（工具数 43 → **44**） |
| `scripts/check_p40.py` | **15 条**验收（构建/真加载/三态/退化） |

### 实测

| 用例 | 结果 |
|---|---|
| 关节齐 + 尺寸齐 | `ok`；MuJoCo **3.12.0** 编译通过，`nbody=4`、`njnt=1` |
| `--build` | `model.xml` 落盘，1345 字，内容与返回值一致 |
| hinge 缺 `range_deg` | `not_ready`，`joints_skipped=1`、`parts_skipped=0`，原因点名 `range_deg` |
| **标量** `range_deg: 180` | `not_ready` + 原因写「有歧义，不猜」（**原来会抛 TypeError**） |
| 没有关节表 | `not_ready` + 「静几何不是可动结构」 |
| `HWV_NO_MUJOCO=1` | `not_ready`（没验证过 ≠ 通过），不抛异常 |
| 退化：拆掉「关节跳过」闸门 | 3a 从 `not_ready` 变绿 → 断言确实挂在闸门上 |

### 同轮修掉的两个真 bug

1. **`range_deg` 形状契约没写清**（AGENTS §3.33）：`REQUIRED_PARAMS` 只给键名，
   代码要一对数 → 标量直接 `TypeError` 抛在 `build()` 里，`ladder --with-sim`
   也会中招。改成：接受 `[lo, hi]` 或 `min_deg`/`max_deg`，**标量不猜**（跳过并
   说明），`describe()` 增 `param_shapes`。
2. **MuJoCo `from_xml_path` 在非 ASCII 路径必然失败**（AGENTS §3.34）：
   工作区叫 `示例工作区`，文件存在却报 `ParseXML: Error opening file`。
   改成**先用 Python 读、再 `from_xml_string`**。

### 验收

```
python -m hw_velxio.scripts.check_p40   # 15/15
npm test                               # 752 通过
node scripts/check_tools.mjs           # 44 个工具，全过
npm run check                          # tsc 干净
```


## P41 — spec 扩四域（2026-11-22）

> 计划见 [`docs/plan-v5.md`](docs/plan-v5.md) P41。目标只有一个：让 `design.json`
> 能表达**固件怎么想**、**机械怎么连**、**哪些网是电源**，并且把两个"靠名字猜"
> 的地方变成**可以显式声明**。

### 新增

| 位置 | 内容 |
|---|---|
| `design/model.py` | `power_nets` / `firmware` / `mechanical` 三块；`SOURCE_ORDER` + `source_at_least()`；`Part.jlc_class`；`catalog`/`proposed` 两种来源 |
| `design/power.py`（新） | 电源/地判据**唯一一份** + `classify(declared, nets)`（声明优先，未声明标 `inferred`）|
| `design/router.py` | `_POWER_HINTS`/`_is_power` 改为 `power.py` 的别名（一张表，两个用家）|
| `design/emit_lceda.py` | 铺铜改用 `power.classify`，每块板记 `nets_source` |
| `design/dims.py`（新） | 尺寸来源分类（`datasheet/measured/catalog` 可信；`estimated/proposed/unknown` 不可信）|
| `design_cli.py` | `dims` 命令（`--apply` 用立创 3D 模型回写，标 `catalog`）|
| `design/agent_tools.py` / `src/tools.ts` | `hw_dims`（工具数 44 → **45**）|
| `scripts/check_p41.py` | **21 条**验收（含 1 条退化）|

### 修掉的两个老 bug

1. **`catalog` 不在允许值里**（§3.35）：`parts_catalog` 一直在写它 →
   "目录回填过的元件"过不了 `validate()`。
2. **电源/地判据有两份**：`emit_lceda._GROUND_HINTS` 与 `router._POWER_HINTS`，
   还靠一个私有函数互借。现在只有 `design/power.py` 一份。

### 实测

| 用例 | 结果 |
|---|---|
| 全部夹具 `to_dict→from_dict→to_dict` | **逐字节一致**；旧文件**不长出新键** |
| `power_nets` 声明 vs 缺省 | `declared` vs `inferred`，结果不同且来源都写出来 |
| 只声明 `+3V3`（没声明地）| 地按名字兜底，来源记 `declared+inferred-ground` |
| `mechanical.joints` | 归并进 `joints_list`，`mechanical` 里不再留 `joints`（一遍读一次不会变两条）|
| `dims` 3D Transform | `100,60,40` mil → 2.54/1.524/1.016 mm |
| `dims` 3D Title | `C0402_L1.0-W0.5-H0.5` → 1.0/0.5/0.5 mm |
| `proposed` | `dims` 判**不可信**；`source_at_least("proposed","estimated") == False` |
| 退化：把 proposed 塞进可信表 | 6d 变红 → 闸门确实在那张表上 |

### 验收

```
python -m hw_velxio.scripts.check_p41   # 21/21
python -m hw_velxio.scripts.check_p40   # 15/15（没回归）
python -m hw_velxio.scripts.check_p26   # 28/28
npm test                                # 753 通过
node scripts/check_tools.mjs            # 45 个工具
```

**注**：`check_p0` / `check_p23` 在 **GBK 控制台**下会崩在 `✓` 的编码上
（与本段无关）；设 `PYTHONIOENCODING=utf-8` 后两者均 exit=0。


## P42 — 关节提案（`joints-propose`）（2026-11-22）

> 计划见 [`docs/plan-v5.md`](docs/plan-v5.md) P42。解决的是这个矛盾：
> `joints` 对缺参数**一律报 error、不编默认值**（纪律），但没有任何机械关系的
> 半成品**什么也验不了**。

### 新增

| 位置 | 内容 |
|---|---|
| `design/joint_propose.py`（新） | 四条提案规则 + `apply()` + `describe()` |
| `design/model.py` | `JOINT_SOURCES` 增 `proposed`（沿用 P41 的信任层）；`Joint` 的 source 校验改读常量 |
| `design_cli.py` | `joints-propose` 命令（`--apply` 才写进 design）|
| `design/store.py` | `ARTIFACTS` 加 `joints.proposed.json` |
| `design/nextstep.py` | 四个新 need：`hinge_deg` / `hinge_partner` / `gear_ratio` / `geometry` |
| `design/agent_tools.py` / `src/tools.ts` | `hw_joints_propose`（工具数 45 → **46**）|
| `scripts/check_p42.py` | **12 条**验收（含 1 条退化）|

### 提案规则（都能复算）

| kind | 依据 | 参数 |
|---|---|---|
| `stack` / `port` / `contact` | `joints.infer()` 的几何事实 | 无（照搬 evidence）|
| `axle` | 轴类零件足迹中心落在另一件里 | `diameter_mm` = 轴件短边 |
| `slide` | 轨与滑块足迹互相包含 | `range_mm` = ±(轨长 − 滑块长)/2 |
| `hinge` | 执行器 + 同单元从动件 | `range_deg` = `mechanical.limits.hinge_deg`；**没给就不提** |
| `gear` | 齿数不在模型里 | **一律 unresolved**（不编 ratio）|

推不出的一律进 `unresolved`，每条带 `need`（已进 `nextstep.NEEDS`，有按钮）。

### 顺带修掉一个真 bug（§3.37）

`joints.validate` 的 `status` 原来只看 `checked`（查了几项），
**不数 findings 里的 error** → `hinge` 缺 `range_deg` 时 `status` 仍是 `ok`，
下游 `cmd_joints` 也就一路绿。改成三档：`not_ready`（没声明）/ `failed`（有 error）/ `ok`。

### 实测

| 用例 | 结果 |
|---|---|
| 叠放 + 舵机/摆臂 + limits | 3 条提案（stack 1 / axle 1 / hinge 1），每条有 evidence |
| `axle` 轴径 | 15.0 mm（= 舵机短边，几何算的）|
| 无 `hinge_deg` | **不提 hinge**，`unresolved` 里 `need=hinge_deg` |
| 有齿轮件 | `gear` 零提案，`unresolved` 里 `need=gear_ratio` |
| `joints` 老行为 | 缺 `range_deg` 仍 error，且现在 `status=failed`（原为 ok）|
| `--apply` | 3 条进 `joints_list`（`source=proposed`）；`joints` 转 ok |
| MJCF | `njnt=1`，MuJoCo 编译通过 → **提案真的进得了物理模型** |
| 退化：缺 limits 时编 ±180 | 2a 变红 |

### 验收

```
python -m hw_velxio.scripts.check_p42   # 12/12
check_p1                                # 25/25（四个新 need 已进表）
npm test                                # 754 通过
node scripts/check_tools.mjs            # 46 个工具
全套离线验收 19 个 check_*              # 全 exit=0
```


## P43 — 编排器（`scaffold`）：一句需求 → 一份半成品（2026-11-22）

> 计划见 [`docs/plan-v5.md`](docs/plan-v5.md) P43。把 P40–P42 的零件串成
> **固定顺序**，并落实用户那条要求：**所有项目都是半成品，缺件不该让产出归零**。

### 新增

| 位置 | 内容 |
|---|---|
| `design/scaffold.py`（新） | 六段编排 + `open_items_markdown()` + `CONTINUE_ON_GAP` 开关 |
| `design_cli.py` | `scaffold` 命令（`--route/--template/--target/--to/--mcu/--ioc/--spec`）|
| `design/store.py` | `ARTIFACTS` 加 `scaffold.json` / `open_items.md` |
| `src/tools.ts` | `hw_scaffold`（工具数 46 → **47**）|
| `scripts/check_p43.py` | **12 条**验收（含 1 条退化）|

### 六段（每段一行写进 `scaffold.json`）

```
requirement → design → pinmap → joints 提案 → firmware 骨架 → mechanical(MJCF)
```

* **选路必须显式**：`--route=synth|generate|roles`，不给就 `failed` 并说明
  "三条路差别很大，不猜"
* **缺 `.ioc`** → 走网表推引脚（`build_pinmap`），标 `source: proposed`
* **缺关节参数** → 走 P42 提案，推不出的进 `open_items.md`
* **本命令不碰立创**，除非 `--to pcb`；发射失败**不拖累**其它段

### 实测

| 用例 | 结果 |
|---|---|
| `synth` + `blink`（arduino 板）| 六段齐全；design ok；pinmap not_ready（列出 7 个已知 MCU，说明开发板不是裸 MCU）；**firmware 段仍在** |
| 手搓 ATtiny85 设计 | pinmap **ok**（2 条，来源 netlist）；固件骨架 5 个文件（target=avr）；mechanical 如实 not_ready（静几何）|
| 同输入跑两次 | `scaffold.json` **逐字节一致**（不带时间戳）|
| `--to pcb` + 无 lceda | `pcb=not_ready`，`design` 仍 ok |
| `open_items.md` | 917 字，表格式，写明"不阻塞" |
| 退化：关 `CONTINUE_ON_GAP` | firmware 段消失 → 1b2 变红 |

### 一条设计取舍（§3.39）

`scaffold` 会写 design.json，而 agent 循环的不变式是"写入口只有一个 `hw_put`"。
所以它**只做 CLI + DSH 工具，不进 `agent_tools.TOOLS`** —— 否则等于开了
第二个绕过 `hw_put` 校验的写入口（`check_p22` 1b 会立刻红）。

### 顺带修：AGENTS.md 超预算被**静默截断**

AGENTS.md 长到 **67287 字节**，而 DSH 的注入预算 65536 → 尾部（§8.7 验收清单、
§8.8 清理残留）**agent 看不到**，但它以为自己读全了。

把整节 §8 搬到 [docs/runbook.md](docs/runbook.md)（内容一字未改，附了搬移原因），
AGENTS.md 降到 **56530 字节**，§8 只留一段指针。

### 验收

```
python -m hw_velxio.scripts.check_p43   # 12/12
npm test                                # 754 通过
node scripts/check_tools.mjs            # 47 个工具
全套离线验收 20 个 check_*              # 全 exit=0（含 p22/p23）
```


## P44 — 机械判据（`mech`）：干涉 / 重心 / 扭矩 / 行程（2026-11-22）

> 计划见 [`docs/plan-v5.md`](docs/plan-v5.md) P44。补的是"机械域一条判据都没有"
> 这个洞：`joints` 只查参数给没给，`mjcf` 只查能不能编译。

### 新增

| 位置 | 内容 |
|---|---|
| `design/mech_check.py`（新） | 四条判据 + 三态 + `basis` |
| `design_cli.py` | `mech` 命令（写 `mech.json`）|
| `design/handoff.py` | `_mechanical()`：fail→blocking、unknown→missing_data、pass 不报；`mech.json` 进 sources/checked |
| `design/nextstep.py` | 七个新 need（`fix_interference`/`move_cog`/`provide_mass`/`provide_torque`/`give_joint_travel`/`give_support`/`mech_unknown`）|
| `design/store.py` | `ARTIFACTS` 加 `mech.json` |
| `design/agent_tools.py` / `src/tools.ts` | `hw_mech`（工具数 47 → **48**）|
| `scripts/check_p44.py` | **16 条**验收（含 1 条退化）|

### 四条判据

| check | 依据 | 三态 |
|---|---|---|
| `interference` | 3D bbox 相交（**有 joint 的对跳过**：铰链/叠放的接触是有意的）| 重叠 > 0.1mm → fail |
| `center_of_gravity` | 质量加权 xy 重心 vs **z 最低层**足迹包围盒 | 出框 → fail；没质量/没着地层 → unknown |
| `servo_torque` | 从动件质量 × 力臂 vs 标称 × **0.5 安全系数** | 超出 → fail；缺标称 → unknown（**仍算出要多少**）|
| `joint_range` | `limits.required_deg` vs 关节 `params` 行程 | 不够 → fail；没给需求 → unknown |

### 实测

| 用例 | 结果 |
|---|---|
| A1/A2 重叠 | `fail`，指到两个位号，`fix.need=fix_interference` |
| 重心 (58.4, 12.9) vs 支撑盒 [10..14]² | `fail`「重心落在支撑面之外（会倾覆）」|
| 舵机没写扭矩 | **`unknown`** + `required_kgcm=0.047`（缺的是标称值，不是需求值）|
| 标称 1.2 kg·cm | `pass`（1.2×0.5=0.6 ≥ 0.05）|
| 需求 180° / 关节 90° | `fail`「行程不够」 |
| 交接单 | 3 条机械条目：1 blocking（重心）+ 2 missing_data（扭矩/行程）；pass 不出现 |
| 退化：重心判据恒 pass | 2a 变红 |

### 验收

```
python -m hw_velxio.scripts.check_p44   # 16/16
npm test                                # 755 通过
node scripts/check_tools.mjs            # 48 个工具
全套离线验收 22 个 check_*              # 全 exit=0
```


## P45 — 制造可行性：DFM 接入 + DRC 分级（2026-11-22）

> 计划见 [`docs/plan-v5.md`](docs/plan-v5.md) P45。核心发现来自 API 元数据本身：
> **DFM 结果不完整时是抛 `INCOMPLETE_RESULT`，不是返回一个失败列表**。
> 原文："不得视为检查通过"。

### 新增

| 位置 | 内容 |
|---|---|
| `design/dfm.py`（新） | `dfm_verdict()` 三态 + `merge()` 三路合成 + `TRUSTWORTHY_DRC` 表 |
| `design/emit_lceda.py` | pcb 段同一次调用里跑 `getDfmAvailability()` + `checkDfm({})`，**不吞错**；`report.fabrication`（进 emit.json）|
| `design_cli.py` | `dfm` 命令（默认重判 `emit.json`；`--live` 才去立创真跑）|
| `design/agent_tools.py` / `src/tools.ts` | `hw_dfm`（工具数 48 → **49**）|
| `scripts/check_p45.py` | **15 条**验收（含 1 条退化）|

### 三态映射

| 输入 | 结论 |
|---|---|
| `getDfmAvailability()` 不可用 | `not_ready` |
| 抛 `INCOMPLETE_RESULT` | **`unknown`** |
| `DFM_NOT_CONFIGURED` | `not_ready` |
| `failedItems > 0`（防御分支）| `unknown` |
| 正常返回 | `ok` |

合成：可信违规（`clearance/netlist/physical` + 自建几何）> 0 → `failed`；
DFM 非 ok → `not_ready`；否则 `ok`。**`connection` 只提示**（实测不认 API 过孔）。

### 实测

| 用例 | 结果 |
|---|---|
| DFM 不可用 | `not_ready`「仅在私有化构建可用」|
| 抛 `INCOMPLETE_RESULT` | `unknown` + 原话 |
| `DFM_NOT_CONFIGURED` | `not_ready`「没查 ≠ 通过」|
| DRC `clearance=2` | 合成 `failed` |
| 只 3 条 `connection` | 合成 **`ok`** + 「不认 API 过孔」说明 |
| `dfm <id>`（无 emit.json）| `not_ready`「先跑 emit」|
| `dfm <id>`（有 emit.json）| 重判 `failed`，`checked={drc_boards:1, dfm_boards:1}` |
| 退化：清空 `TRUSTWORTHY_DRC` | 2b 变绿 |

**真机发射一次（p45-probe，2 件 2 网）**：`project/layout/schematic/pcb` 全 ok，
`emit.json` 里

```
fabrication.status = not_ready
reasons = ["DFM unknown：DFM 调用失败：DFM 仅在私有化构建可用"]
```

也就是说**本机（4.1.97，非私有化构建）没有 DFM 能力** —— `checkDfm` 抛的是
那句中文而不是 `INCOMPLETE_RESULT`。据此补了一条映射：错误文本含「私有化/
不可用」→ `not_ready`（没这能力 ≠ 结果不完整），并把这条**真机文本**写进了
`check_p45` 的 1g 用例。

### 一个必须记下来的操作事故（§3.43）

我新建"制造可行性"模块时，**直接覆盖了已存在的 `design/fabrication.py`**
（制造包导出模块，-194 行）。靠 `git show HEAD:...` 完整恢复，逻辑改放
`design/dfm.py`。教训：写新模块前先 `ls design/`；`write` 返回 "Updated"
而不是 "Created" 就说明覆盖了东西。

### 验收

```
python -m hw_velxio.scripts.check_p45   # 15/15
npm test                                # 756 通过
node scripts/check_tools.mjs            # 49 个工具
全套离线验收 23 个 check_*              # 全 exit=0
```


## P46 — 前端两个分区：`emit` 与 `brief`（2026-11-22）

> 计划见 [`docs/plan-v5.md`](docs/plan-v5.md) P46。此前 P40–P45 做出来的东西
> **界面上一点都看不到**：8 段发射跑完只回聊天记录，`open_items.md` 只有人
> 自己去翻文件。

### 后端（只投影，不重判）

| 位置 | 内容 |
|---|---|
| `design_cli.py` | `snapshot` 增 `emit` / `scaffold` 两块；新增 `emit_summary()` / `scaffold_summary()` / `emit-status` 命令（**只读 emit.json**，比 snapshot 便宜得多，前端可轮询）|
| `src/service.ts` | `@Remote('emitRun')`（**长任务，超时 15 分钟**）、`@Remote('emitProgress')` |
| `src/client/rpc.ts` | 对应两个方法（接口 + 工厂）|

### 前端

| 位置 | 内容 |
|---|---|
| `src/shared/types.ts` | `EmitSummary` / `EmitJobRow` / `EmitArtifact` / `ScaffoldSummary` / `ScaffoldStageRow` / `ScaffoldItem`；`SnapshotPayload.emit?` / `.scaffold?` |
| `src/client/store.ts` | 分区 `emit`（**排在 export 前**）/ `brief`；动作 `emitRun(jobs)` / `emitReverify()` |
| `src/client/panels.tsx` | `EmitPanel`（8 段状态条 + 连通计数 + 制造结论 + 产物列表 + 三个按钮）、`BriefPanel`（段状态 + 待办表）|
| `src/client/HwIdeView.tsx` | 两个 case + 把 `onEmitRun`/`onEmitReverify` 传进 `common` |
| `src/tools.ts` | `hw_emit`/`hw_scaffold`/`hw_mech` 的跳转改到 `emit`；**修掉 `hw_roles` 的 `'2d'`**（不是分区，一直被 `isSectionId` 静默丢弃，§3.13）|

### 实测

| 用例 | 结果 |
|---|---|
| 没发射过 | `emit.status=not_ready` + 「还没发射过（没有 emit.json）」 |
| 有 emit.json（2 段，1 段 not_ready）| 8 段列表 + `bad_jobs=['route']` + `verify.connected=4` + `fabrication.status` 一起带出 |
| 有 scaffold.json | `count=1`，待办逐条（`need=核对 pinmap`）|
| 前端接线 | 26 条断言全过（分区/标签/位置/面板导出/**真渲染**/动作/RPC/长超时/跳转）|
| 退化：`emit_summary` 凭空返回 ok | 4a 变红 |

### 顺带修掉两条**脆断言**（AGENTS §3.15 升级版）

`check_p4` / `check_p5` 原来按**前缀字面量**钉死 `delivery: ['export', 'firmware'…]`，
P46 往组里加两个分区就红了 —— 而"firmware 属于 delivery 组"的意图没变。
改成**解出数组判成员**（`_delivery_sections()`）。

### 验收

```
python -m hw_velxio.scripts.check_p46   # 26/26
npm test                                # 756 通过
npm run check / build                   # tsc 干净
node scripts/check_tools.mjs            # 49 个工具
全套离线验收 24 个 check_*              # 全 exit=0（含 p4/p5 修复后）
```

**注意**：这是前端改动，**刷新页面**才看得到新分区（`dist/` 已重新构建）。


## P47 — 六段 audit + doctor + examples + 有界自修（2026-11-22）

> 计划见 [`docs/plan-v5.md`](docs/plan-v5.md) P47。这是 v5 的最后一段：
> 前面六段把"能不能出板/做得对不对"补齐了，这一段补的是
> **"现在到哪一步了"（audit）、"这台机器行不行"（doctor）、
> "从哪开始抄"（examples）、"改一轮再判一轮"（fixloop）**。

### 新增

| 位置 | 内容 |
|---|---|
| `design/audit.py`（新） | 八段审计（顺序 = 人做事的顺序）+ `audit.md` 渲染 |
| `design/doctor.py`（新） | 8 项环境探测；`REQUIRED` 是**唯一**必需项事实源 |
| `design/examples.py`（新） | blink / button / gimbal 三个半成品样例 |
| `design/fixloop.py`（新） | 有界自修循环（分数 = failed×100 + not_ready；连续 2 轮无改善即停）|
| `design_cli.py` | `audit` / `doctor` / `examples` / `fixloop` 四个命令 |
| `design/agent_tools.py` / `src/tools.ts` | `hw_audit` / `hw_doctor` / `hw_examples` / `hw_fixloop`（工具 **49 → 53**）|
| `scripts/check_p47.py` | **21 条**验收（含 1 条退化）|

### 实测（`e1-probe` = 抄的 blink 样例）

```
audit: not_ready  {total 8, ok 1, not_ready 7}
  intake      not_ready  没有需求：后面所有段都只能算「猜」的一半
  architecture not_ready 没跑过 units
  parts       not_ready  1/3 有 lcsc（缺一颗就下不了单）
  schematic   not_ready  没发射过
  pcb         not_ready  没发射过
  firmware    not_ready  既没有引脚表也没有固件矩阵
  mechanical  not_ready  没跑过 mech
  release     not_ready  阻断 0，但还有 2 条缺数据
fixloop: stop=needs_editor，计划 2 项（不改设计）
doctor : 必需项齐全（workspace + lceda 4.1.97）；可选缺 4（bridge/lcsc/arduino-cli/ffmpeg）
```

### 两处**判据收紧**（都是"部分当全部"）

1. `parts` 原来"有几颗有 lcsc 就 ok" → 改成**全部**都要有（缺一颗就下不了单）
2. `release` 原来阻断 0 就写「可打板」→ 现在有缺数据时写「阻断 0，但还有 N 条缺数据」

### 一个真 bug：`REQUIRED` 表改了对结果没影响（§3.44）

第一版把 `required=True/False` 写死在每一行上，于是验收里把 `ffmpeg` 提为必需时
整体照样 `ok`。改成 `required` 只由 `REQUIRED` 推导后，那条断言才真能红。

### 验收

```
python -m hw_velxio.scripts.check_p47   # 21/21
npm test                                # 756 通过
npm run check / build                   # tsc 干净
node scripts/check_tools.mjs            # 53 个工具
全套离线验收 25 个 check_*              # 全 exit=0
```

**plan-v5（P40–P47）至此全部完成。**


## P48 — 闭环修复：网表不再丢 / 空输入不再绿 / 第二意见生效（2026-11-22）

> 起因：用户让"读原工程写规格 → 从 0 重建 → 比对"。手工版跑到一半发现
> **重建出来的 5 件骨架 0 条网表，却被判「可打板」**。这三处一修，链才成立。

### 修了什么

| # | 缺陷（实测） | 修法 |
|---|---|---|
| **F1** | `generate.materialize()` 恒 `nets=0`（`ref_of` 用角色名、链路用 `unit:` 前缀）| `ref_of` 认两种键；链路按协议族挑**真实引脚名**（`spi`→`SPI1`…）；挑不到记 `unresolved_links` |
| **F2** | 0 网表设计：`check`=ok、`ladder`=ok、`deliverable=True` | V0 加 `symbol.empty_netlist`（error）+ `thin_net`（warning）；`deliverable` 还要求 `emit.verify == ok` |
| **F3** | `check`/`ladder`/`handoff` 顶层 `status` 写死 `ok`（payload 里 `ok=false, failed=2`）| 新增 `_judge_status()`：从 payload 推，**阶梯按层看**（unknown≠failed）|
| **F4** | `parity` 在"设计侧 0 引脚"时容易被读成没问题 | `design_pins` 为空 → `not_ready` 并写明「对空输入没有意义」|

### 20 件板真机跑通（工作区 `示例复现区/重建-最小系统板`）

STM32F103C8T6 + LDO + Micro-USB + 8M/32.768k 晶振 + 14 个无源，**21 件 / 14 网**，
料号全部来自 BOM（缺件号的 6 颗 100nF 用 LCSC 目录里的 `C1591`）：

```
put        ok（21 件 15 网 → 修后 14 网）
check      ok（0 failed；先前 2 → 2 → 0 是改了两轮布局）
ladder     not_ready（V0 pass，V1–V6 unknown：没申报电气参数）
schematic  ok（装 21 器件 / 62 引脚接线）
pcb        ok（21/21 摆上板）
route      ok（190 段 / 17 过孔；铺铜前 10/14）
pour       ok（GND 铺铜）
verify     not_ready（**11/14**；未连通 VBUS / USB_DP / USB_DM）
parity     **ok，逐引脚 62/62 一致**（第二意见生效）
audit      failed（parts ok / schematic ok / pcb failed 11/14）
handoff    deliverable=False（final: route=ok, pour=ok, verify=not_ready）
```

引擎自述 `failedNets=['VBUS','USB_DP','USB_DM']`，`verify` 独立判几何也确认这 3 条不通
—— **两个来源一致**，这正是之前缺的"第二意见"。

### 新增

- `scripts/check_p48.py`（**20 条**，含 3 条退化）
- `docs/plan-v6.md`（spec/compare 的方案，本次未做）
- 工作区 `C:\Users\<user>\Desktop\示例复现区`：`spec.md`（读原工程手写的规格）+ `比对.md`（手工比对）

### 顺带修

- `check_handoff` / `check_m18` 的 `deliverable` 断言按新语义改写（并改成**不钉数组总数**）
- `audit` 的 `pcb` 段读 `verify_detail.totals`（**这个键不存在**）→ 改成按 `boards[]` 累加；
  修前永远显示"连通 0/0"

### 验收

```
check_p48  20/20        npm test 760 通过        全套离线 26 个 check_* 全 exit=0
```


## P49 — 项目计划图 `plan`：需求 → 能力 → 模块 → 3D 图 → 任务（2026-11-22）

> 用户要的：**AI 每次分析需求时默认维护一份项目文件**，代码式、可细化延申；
> 同一项目后加需求就新增 `vN`；细化到**一个 3D 信息图**（后续 3D 图的源头），
> 图上能联系到每个模块的位置；再从图上衍生任务并拆解到可执行。

### 数据形状

```
<工程>/plan/v1.json        自包含快照（capabilities + modules + graph + tasks）
<工程>/plan/v2.json        继承 v1 的任务状态，判据按当前需求重算
<工程>/plan/index.json     版本索引（version / requirement_hash / created / counts）
```

| 层 | 内容 | 证据 |
|---|---|---|
| `capabilities[]` | 14 条能力（H1–H8 硬件 / S1–S6 软件），每条带 `metric/op/value/unit` | 能力模板 + 实物索引 + 需求数值 |
| `modules[]` | **每个元件一个节点**（含 0402 电容）+ 板 | `design.json` 的 `placement`（真实 mm）|
| `graph` | **四层 3D**：任务 z=-60 / 能力 z=+60 / 模块 / 板 | 边来源 `net`/`joint`/`placement`/`task`/`template` |
| `tasks[]` | 由能力缺口生成，2 层（动作 + 验收）| 每条必须带 `verify` |

### 实测（21 件板）

```
plan → v1：14 能力（全 unknown）/ 22 模块 / 64 节点 / 211 边 / 28 任务
加需求 {thrust_weight:2.4, rail_5v:4.85, phase_margin:35} → v2（parent v1）
        H1/H8/S2 变 pass，任务 28 → 22
同需求再跑 → created=False（不新建）
snapshot（前端轮询路径）→ 不建版，只报最新；有旧版对应时 stale=True
```

### 三个必须记住的设计点（AGENTS §3.48）

1. **判据先行**：能力模板自带量化判据；写不出判据的进 `missing_need` 不进表
2. **只有需求给了数值才判 pass/fail**，否则 `unknown`（"买了件"≠"能飞"）
3. **任务 id 带动作后缀**（`-P/-D/-V`）：实测同能力"补数据"与"改设计"都叫
   `T-H1-1`，依赖与状态继承会指到随机一条

### 新增

| 位置 | 内容 |
|---|---|
| `design/plan.py`（新） | 能力模板 + 证据索引 + 模块/3D 图 + 任务树 + 版本 |
| `design_cli.py` | `plan <id> [--req] [--text] [--list] [--show=vN] [--graph]`；`snapshot` 自动 bootstrap |
| `design/store.py` | `ARTIFACTS` 加 `plan/index.json` |
| `design/agent_tools.py` / `src/tools.ts` | `hw_plan`（工具 53 → **54**）|
| `scripts/check_p49.py` | **27 条**（含 1 条退化）|

### 验收

```
check_p49  27/27      npm test 760      tsc 干净      check_tools 54
全套离线验收 27 个 check_* 全 exit=0
```


## P49b — 多板干涉误报修复 + 类似项目的可行性实测（2026-11-22）

> 起因：用户要求"做一个类似示例-单板的项目，生成信息文件，并检查是否都能实现"。
> 我造了 3 板 28 件的 `复刻-示例`，跑完整链，撞出一个真 bug。

### 修：`mech` 的干涉判据没按板分组

3 板工程第一次 `mech`：**7 条 fail**，其中 **5 条是跨板误报**
（"遥控器板 U1 与 GNSS 板 U2 重叠"——`placement` 是各板局部坐标）。
修法：按 `board_id` 分组后再两两比；修后 fail **7 → 0**。
`check_p44` 增 1d/1e（跨板 pass / 同板同坐标必须 fail）。

### 实测（`复刻-示例`：3 板 / 28 件 / 18 网，28/28 有 LCSC）

```
plan v1     14 能力全 unknown（需求只给目标、无实测）· 31 模块 · 73 节点 · 436 边 · 28 任务
check       ok（9 项；先修了 2 处重叠 + 1 处越界）
ladder      not_ready（V0 pass，V1–V6 unknown）
mech        not_ready（pass 2 / fail 0 / unknown 2）
emit        project/schematic(28 器件 85 引脚)/pcb(28/28, 3 框)/route(189 段 16 过孔)/
            pour(3 板)/outputs(10 产物) = ok；layout not_ready(4 处)；verify 17/18（断 3V3）
parity      not_ready，checked 0 —— 多板回读失败（单板是 62/62）
handoff     deliverable=False（阻断 1 / 缺数据 4 / 不会验 3）
```

三条结论：
1. **结构层能做完**（接线/摆放/布线/铺铜/产物），**能力层 12/14 无数据入口**
2. **跨板网必须放连接器**，否则"同一个网跨板"物理上不存在（3V3 断是正确结论）
3. **多板 parity 缺口**：第二意见在最需要时缺席（记为 P2）

产物：工作区 `示例复现区/可行性报告.md`（能力逐条的"能不能给结论"矩阵）

### 验收

```
check_p44 18/18   npm test 760   27 个离线 check_* 全 exit=0
```


## P50 — 软件：软硬接口契约 + 纯 C 算法（真编译真跑）（2026-11-22）

> 起因：用户问"你做任务时考虑到软件了吗"。盘点结果：`plan` 里 6 条软件能力只是
> **判据**，我造的项目 `.c/.h` = **0 个**，`fw-matrix` 报"没有扫到任何源文件"。
> 这一段补两块**能在本机验证**的软件。

### `interface` —— 软硬接口契约

`interface.json` / `interface.md`：引脚↔网络 + 外设分组 + 缺口。

| 判据 | 结论 |
|---|---|
| 引脚↔网络逐条一致 | **唯一硬判据**；用元件没有的脚 → `iface.pin_missing` + `failed` |
| 外设分组 | 有 `.ioc` 按 `.ioc`；没有就按网名族**推断**（`source=inferred`）|
| 时钟/中断/AF/时序 | 一律 `unknown`，各带 `need`（**不猜**，AF 表本仓库没录）|

实测（`复刻-示例`）：48 脚 / 19 已连 / 29 未接 / 4 电源 / 4 地 / 2 外设组（uart、usb）。

### `fw-algo` —— 纯 C 算法 + PC 侧单元测试

为什么只做纯 C：目标板无板可跑、Renode 在本机被沙箱拦（§3.4）。
把不依赖硬件的部分抽出来，用**本机 gcc 真编译真跑**。

| 算法 | 用例 | 结果 |
|---|---|---|
| `failsafe`（风控状态机）| 9 条（正常/边界/角度/下降率/链路/不重复/负角度）| **9/9 ok** |
| `lpf`（一阶低通）| 9 条（直通/首拍/保持/单调/夹幅）| **9/9 ok** |
| **退化**：删掉角度判断 | — | **5 条红、status=failed** |

顺带一条真教训：`lpf` 第一版 `alpha0_hold` 用例**期望写错**（初始化成
alpha=0.1 却期望 alpha=0 的结果）→ 报 `got 90 want 100`。是**用例错、实现对**。

### 顺带

- `audit` 的 `firmware` 段现在会读 `interface.json`：有契约时报"接口契约在，
  但还缺固件本体"，不再只说"既没有引脚表也没有矩阵"。

### 新增

| 位置 | 内容 |
|---|---|
| `design/interface.py`（新） | 契约合成 + `interface.md` 渲染 |
| `design/fw_algo.py`（新） | 2 个算法模板 + gcc 编译运行 + `drop=` 退化入口 |
| `design_cli.py` | `interface` / `fw-algo` 命令 |
| `design/audit.py` | firmware 段读契约 |
| `design/agent_tools.py` / `src/tools.ts` | `hw_interface` / `hw_fw_algo`（工具 54 → **56**）|
| `scripts/check_p50.py` | **14 条**（含 2 条退化）|

### 验收

```
check_p50 14/14    npm test 760    28 个离线 check_* 全 exit=0    check_tools 56
```

### 还没做（按用户排序：3 → 2 → 5）

1. **软件任务分解到三层**（写/测/验）—— 现在 S1–S6 每项只有 2 条任务
2. **可编译骨架 + 真编译** —— 需 ARM 工具链（`doctor` 报未装）
3. **飞控参数 → 能力映射** —— `autopilot_config` / `.param` 还没接


## P50b — 本地 ARM 工具链（装在仓库内，零污染）+ Low IL 坑（2026-11-22）

> 用户问"ARM 工具链怎么安装？（希望装在这个文件夹里，不要污染）"。

### 装了什么

```
workspace\hw_velxio\_tools\arm-gcc\13.3.rel1\     1.2 GB
Arm GNU Toolchain 13.3.Rel1 (build arm-13.24)
```

### 零污染清单

| 项 | 有没有 |
|---|---|
| 安装器 / 注册表 / PATH / 服务 / 计划任务 | **全无** |
| 系统目录写入 | 无（全在仓库 `_tools/`）|
| 唯一占用 | 1.2 GB；`.gitignore` 已忽略 `_tools/arm-gcc/`、`_tools/_dl/` |
| 卸载 | **删一个目录** |

### 验证（真编译，不是看版本号）

```
as 汇编 ✓   gcc -c ✓   链接 ELF ✓（Machine=ARM  Entry=0x8000000  text 108B）
objdump: add.w r0,r0,r0,lsl#1 / adds r0,#1 / bx lr   ← Thumb
Cortex-M7 + fpv5-d16 硬浮点 ✓
doctor: arm_gcc ok（scope=repo）；PATH 里查不到 arm-none-eabi-gcc
```

### 撞到的坑：解压进来的 exe 带 **Low IL** → 编译全 Permission denied

本仓库目录有继承标签 `Mandatory Label\Low Mandatory Level:(OI)(CI)(NW)`
→ 解压出的 exe 也带 Low → **Windows 用镜像 IL 起进程** → 写不了 Medium 目录
（桌面工程 / `D:\tmp`），连自己的临时文件都建不了。

三种症状：`Cannot create temporary file in ...Temp: Permission denied` /
`can't create x.o` / `cannot open 'x.s' for writing`。

修法（三条，顺序不能反）：

```
icacls _tools /remove:d Everyone                      # 摘掉 DENY(DC)
icacls _tools\arm-gcc /grant "<user>:(OI)(CI)F" /T     # 拿 WRITE_DAC
icacls _tools\arm-gcc /setintegritylevel "(OI)(CI)Medium" /T /C   # 8604 个文件
```

两条错路（我踩过）：以为是"沙箱拦路径"（同一 shell bash/w64devkit gcc 写同目录都成功）；
以为是"i686 vs x64"（w64devkit 的 gcc **也是 i686**）。
**差别只在标签。**

### 新增

| 位置 | 内容 |
|---|---|
| `design/doctor.py` | `LOCAL_TOOLCHAIN` 探测（先仓库内、再 PATH，带 `scope`）|
| `scripts/check_p47.py` | `2a2`：`arm_gcc` 被探测到 |
| `docs/runbook.md` | §13 装法 / 不污染 / 4 步验证 / Low IL 坑 |
| `.gitignore` | `_tools/_dl/`、`_tools/arm-gcc/` |

### 验收

```
check_p47 22/22    doctor 9 项（arm_gcc ok）    npm test 760
```


## P51 — 软件任务分解：写 → 测 → 验（2026-11-22）

> 用户第 3 项：现在 S1–S6 每项只有"测一个数 + 一条验收"，缺"**写**"那一层。

### 改成什么

`design/plan.py` 新增 `SW_BREAKDOWN`（6 个软件能力 × 3–5 步），每步带
`kind` / `title` / **`artifact`（产物路径）** / `verify`，并链式依赖。

```
S1 风控   spec → impl → inject → threshold → latency → V
S2 控制律  gains → cascade → step → margin → V
S3 EKF     state → impl → converge → noise → V
S4 视觉    iface → algo → acc → V
S5 任务    fsm → impl → wp → V
S6 遥测    frame → impl → rate → V
```

### 实测（复刻-示例）

```
任务 28 → 50      软件任务 12 → 34（5.7 条/能力）    硬件 16 条（2 条/能力）
带 artifact 16 条  产物已存在 3 条（fw/failsafe 真的在）
```

### 四条硬判据（check_p51）

| 判据 | 做法 |
|---|---|
| 第一层是「写」 | design/code，不是 measure |
| 链式不是并列 | 剥掉能力级前置后，每条恰好依赖前一条 |
| `-V` 依赖链尾 | 不是依赖全部前置 |
| **产物存在 ≠ 通过** | 造空 `failsafe.h` → `present=True` 但 `status=open` |

### 退化

清空 `SW_BREAKDOWN` → S1 从 7 条掉回 2 条、`artifact` 字段消失
（证明分解真的挂在那张表上，不是碰巧）。

### 顺带抓到一个自己的错

`check_p51` 的 2a 第一版写了 `all(... or True for ...)` —— **恒真子句**，
正是 §4.1 警告的"永远绿的断言"。自己复读时抓到并改掉。

### 验收

```
check_p51 14/14    npm test 760
```


## P52 — 可编译骨架 + 真编译（进 .elf/.bin）（2026-11-22）

> 用户第 2 项。工具链（P50b）装好后，这一步就能真跑了。

### 产出

```
fw-build <工程> → fw/build/{startup.c, link.ld, main.c, Makefile}
                 → firmware.elf（9548 B）+ firmware.bin（172 B）
```

### 实测（复刻-示例，STM32F103C8T6）

```
status: ok
Machine = ARM        Entry = 0x8000045（FLASH 区）
text 168 B + data 4 B ≤ flash 65536 B
data  4 B + bss  4 B ≤ ram   20480 B
```

### 四条硬判据 + 三条退化

| 退化 | 结果 |
|---|---|
| FLASH 挪到 0x20000000 | `Entry '0x20000045' 不在 FLASH` → failed |
| RAM 改到 1 B | `data+bss = 8 B 超过 RAM 1 B` → failed |
| main.c 插语法错 | 编译不过 + 编译器原文 → failed |
| MCU 不在表（stm32l476）| `not_ready` + 列出已知型号（**不猜容量**）|

### 同一次真编译里踩到三个坑（runbook §15）

1. `undefined reference to SystemCoreClock` —— `extern` 声明 + 赋值 ≠ 定义
2. `ld: RAM 越界` 而容量明明够 —— link.ld 的 `ASSERT(ORIGIN+LENGTH <= 同式)` **恒等**
3. `firmware.bin` 不生成 —— 写成 `gcc -O binary out in`；gcc 的 `-O` 是优化级别，
   且顺序反了。要用 **objcopy**

### 新增

`design/fw_build.py`、`fw-build` 命令、`hw_fw_build`（工具 56 → **57**）、
`scripts/check_p52.py`（12 条，含 3 条退化）、store 登记 `fw_build.json`

### 验收

```
check_p52 12/12    npm test 760    tsc 干净    check_tools 57
```


## P53 — 飞控参数 → 能力映射（P53，软件三连的最后一块）（2026-11-23）

> 用户排的顺序：①接口契约 ④纯 C 算法（P50）→ ③三层分解（P51）→ ②真编译（P52）
> → **⑤参数映射（本段）**。P50–P52 已各自落地，这一段补最后一环。

### 问题

`plan` 对 S1–S6 一律 `unknown`，理由写"增益来源不明"——**而参数就躺在工作区**：

| 文件 | 格式 | 实测 |
|---|---|---|
| `config_20261021_102258.param` | `name=value` | 47 条 |
| `H743PilotX-master/App/Inc/Config.h` | `MF_PARAM(...) /*注释*/` | 206 条 |

在此之前**一条都没被读过**。

### 做了什么

`design/params.py` + `params` 命令 + `hw_params`（工具 57 → **58**）：

1. 解析两种真实格式（`.param` 的 `name=value`；`MF_PARAM` 的 C 表，注释当语义）
2. 按**参数名族**映射到能力，每条带 `basis`
3. 算**族完整度**（`ready`/`partial`/`absent`）——这是唯一能判的东西
4. `plan` 读 `params.json` 给能力挂 `evidence.params`，**不改 status**

### 实测（真工作区）

```
206 参数 / 93 映射 / 113 未映射 / 15 族 / 6 能力
族齐：H3 H8 S1 S2 S3 S5（6/6，S2 的 10 项必需增益全在）
known_absent：H4（光流参数未进参数集）H5（GNSS 门限未进）
整体 status = not_ready   ← 参数集里没有的能力就是缺口
```

### 三个坑（都已修，写进 runbook §16）

1. **格式猜错**：第一版按 ArduPilot 的 `NAME,VALUE` 写 → 一条都读不到。
   真实是 `name=value`。
2. **枚举型 MF_PARAM**：`MF_PARAM( ahr_gizmo, 0, int32_t, 'e', mf_MAHONY, mf_MAHONY_BF)`
   第 5 段是枚举列表 → 第一版正则只认 4 段，2 条被记成"不完整"。
3. **`#define MF_PARAM(...)` 不是参数**：宏定义行里也有 `MF_PARAM(` →
   第一版又报 2 条"不完整"。判据：`lstrip().startswith("#define")` 跳过。

### 纪律

**参数存在 ≠ 能力达标。** 有 `pid_in_roll_p=0.05` 只说明"增益有值"，
不说明"角速率环稳定"。所以 `params` 只产出 `evidence`，`plan` 里 S2 仍是
`unknown`（check_p53 的 3b 守着这条）。缺必需参数 = `partial`（2e 守着）。

### 新增

| 位置 | 内容 |
|---|---|
| `design/params.py`（新） | 两种解析器 + 15 族映射表 + 族完整度 + 冲突检测 |
| `design_cli.py` | `params <id> [--roots=a;b]` |
| `design/plan.py` | `_attach_param_evidence`（只加证据，不动状态）|
| `design/agent_tools.py` / `src/tools.ts` | `hw_params`；顺手修 4 行重复注释 |
| `scripts/check_p53.py` | **18 条**（含 1 条退化）|
| `design/store.py` | `ARTIFACTS` 加 `params.json` / `params.md` |

### 验收

```
check_p53 18/18    npm test 760    tsc 干净    check_tools 58
```

### 顺带

- 删掉了我第一次跑 `params` 时误写进工作区的 `复刻-示例/`（§4.4 夹具不进用户区）
- AGENTS 压到 63.5 KB（预算 65 KB / 有效 65.2 KB），细节全进 runbook §13–§16


## P54 — 引脚契约进固件：用错脚 = **编译期**错误（2026-11-23）

> 用户点的第 6 件事：`fw-build` 接 `interface.json`。

### 之前的问题

P52 的骨架里引脚是**两套来源**：`main.c` 的 TODO 注释 vs `interface.json` 的契约。
板子把 `UART_TX` 从 30 改到 31，固件不知道 —— 只能等台架才发现（或永远发现不了）。

### 现在：`hwv_pins.h` 由契约生成 + 四个编译期检查宏

```c
#define HWV_MCU_PINS 48
#define HWV_PIN_NET_3V3            1  /* 同网还有脚 24, 36, 48 */
#define HWV_PIN_UART_TX           30
#define HWV_PIN_GPIO_1      0  /* power 3V3 */
#define HWV_PIN_GPIO_30     1
```

| 宏 | 判什么 | 失败时看到什么 |
|---|---|---|
| `HWV_PIN(NAME)` | 名字在契约里吗 | `'HWV_PIN_NOSUCH_NET' undeclared`（带 `did you mean`）|
| `HWV_CHECK_GPIO(NAME)` | 能当 GPIO 吗 | `static assertion failed: "HWV_CHECK_GPIO: pin NET_3V3 is power/ground ..."` |
| `HWV_CHECK_PIN_NUMBER(n)` | 脚号超范围吗 | `"pin number out of MCU range"` |
| `HWV_EXPECT(NAME, n)` | **契约把线挪走了吗** | `"interface.json moved ..."` |

`HWV_EXPECT` 是**真回归保护**：固件写"我假设 UART_TX 在 30"，interface 改成 31 → 编译期报错。

### 判据反着来：负例必须编不过

`build()` 跑三条负例，**每条都期望编译失败**；任一条编过 → 整体 `failed`
（闸门写了没挂上 = §0.1 的假绿）。

实测（`复刻-示例`，真工具链）：

```
骨架：Machine=ARM  Entry=0x8000045  text 168 B
负例 3/3 全编不过：
  power_as_gpio       → error: static assertion failed: "HWV_CHECK_GPIO: pin GND is power/ground ..."
  pin_out_of_range    → error: static assertion failed: "pin number out of MCU range"
  name_not_in_contract→ error: 'HWV_PIN_NOSUCH_NET' undeclared
退化：把 HWV_PIN_GPIO_<n> 全改成 1 → power_as_gpio 编过 → 整体 failed
无契约：不凭空造引脚宏（HWV_MCU_PINS 0）
```

### 三个坑（runbook §17.4）

1. **同网多脚 → 宏重定义**：GND 落 4 个脚，第一版每脚定义 `HWV_PIN_GND`
   → `warning: redefined` + 读的人以为这网只有一根线。修法：同名只定义一次（最小脚号），注释写其余。
2. **负例样本从生成物里正则找 = 自指脆性**：退化实验把 `GPIO_x 0` 改成 1 后
   **样本消失、负例跟着消失**，"闸门拆掉"看起来像"闸门还在"。修法：`generate` 记
   `contract_pins` 进 `fw_build.json`，负例从那里取。
3. **错误信息抓错行**：detail 先命中无关 `warning: "HWV_PIN_GND" redefined`。修法：先找 `error:`。

### 新增/改动

| 位置 | 内容 |
|---|---|
| `design/fw_build.py` | `_pins_header()` 生成契约头；`_negative_cases()` 三条负例；`generate` 记 `contract_pins`；`main.c` include + 自检 |
| `scripts/check_p54.py` | **13 条**（含 1 条退化：拆闸门 → 负例编过）|
| `docs/runbook.md` | §17（含三个坑与边界）|
| `AGENTS.md` | §3.51–3.53 段扩成"软件三连+契约"，压到 65.2 KB 内 |

### 边界（诚实）

`hwv_pins.h` 只保证**引脚这一层**一致。外设寄存器配置、时序、功能对不对，
它不管 —— 那些要靠 `.ioc`/datasheet 和台架。

### 验收

```
check_p54 13/13    32 个离线 check 全 exit=0     npm test 765    tsc 干净
```


---

## 参考对照（历史阶段，摘要）

| 阶段 | 核心产出 | 验收 |
|---|---|---|
| P0 | `electrical.py` 零负载不再假绿 | 空工程 `ok=False` |
| P1 | `ladder.py` V0–V6 + 硬失败截断 | 极性反接 → `stopped_at=V1`，下游 `not_run` |
| P2 | `claims.py` 断言层 | `--remove R1` → `provides_default_level` 变红 |
| P3 | `justify.py` 归因反推 | 140/141 判出作用（99.3%） |
| P4 | `handoff.py` 交接层 | 五类分离，`missing` 不静默 |
| P5 | `units.py` + `links.py` | 8 单元 / 协议·电平·波特率·孤儿四查 |
| P6 | `capability.py` + `generate.py` | 20 候选、两次逐字节一致 |
| P7 | `renode.py` | 产物正确；`runnable_here=False`（沙箱） |
| P8 | `ioc_links.py` | 7 `.ioc` → 13 条链路（全 inferred） |

完整偏差记录见 `docs/execution-plan.md` §1.2–§1.4 与 §18。

## P55 — `.ioc` → 外设配置层（时钟/中断/复用功能/参数）（2026-11-23）

> 用户点的第 7 件事："把 `.ioc` 接进来补『外设配置』这一层"。

### 之前的问题

P50 的 `interface.json` 把时钟/中断/复用功能/时序**四类一律记 `unknown`**。
那不是"没问题"，是"没有数据"——而 CubeMX 的 `.ioc` 里正好就是这些数据。

### 新增

| 文件 | 内容 |
|---|---|
| `design/ioc.py` | `.ioc` → 芯片身份 / 时钟 / 中断 / 复用功能 / 外设参数（**只引用，不推导**）|
| `tests/fixtures/ioc/p55-usart.ioc` | 夹具（形状照抄实测的真实文件）|
| `tests/fixtures/ioc/p55-usart-c6.ioc` | 同族不同型号（验 variant 只提示不判 fail）|
| `tests/fixtures/ioc/p55-h743-wrong.ioc` | 给错 `.ioc`（验 `ioc.mcu_mismatch` 判 failed）|
| `scripts/check_p55.py` | 29 项验收（真实文件 + 契约 + 编译期 + 退化 + CLI）|

### 修改

| 文件 | 改动 |
|---|---|
| `design/interface.py` | 接入 `ioc.py`：新增 `clock`/`nvic`/`af`/`peripheral_config`/`warnings`；`unknown` 按"有没有 `.ioc`"收窄；硬判据 `ioc.mcu_mismatch` |
| `design/fw_build.py` | `hwv_pins.h` 带时钟/参数宏 + `HWV_CHECK_CLOCK`/`HWV_EXPECT_CFG`；负例 3 → 5 条；`fw_build.json` 记 `contract_config` |
| `design_cli.py` | `interface` 的说明更新（`.ioc` 有/无两种形状）|
| `docs/runbook.md` | 新增 §18 |
| `AGENTS.md` | §3.51–3.53 指针表加 §18 |

### 关键数字（实测）

```
7 个真实 .ioc：引脚 208 / 用户外设 67 / 用户中断 56，全部能读
真实项目 复刻-示例 + F103-GPS.ioc：
  clock 64 MHz、APB1 32 MHz、用户中断 3（DMA1_Channel4/EXTI4/USART1）
  提示 SPI2 配了但网表无 spi 网；网表有 USB 但 .ioc 没配
  fw-build：Machine=ARM、text 168 B、负例 5/5 编不过
退化：HWV_CHECK_CLOCK 改空操作 → clock_wrong 编过 → 整体 failed
```

### 验收

```
python -m hw_velxio.scripts.check_p55     # 29 项
python -m hw_velxio.scripts.check_p50     # 14 项（老契约形状不变）
python -m hw_velxio.scripts.check_p54     # 13 项（负例 5 条仍全红）
```

### 诚实边界

`.ioc` 给的是**配置**：功能名有了但 **AF 编号**没有（要 datasheet）；
时钟有了但**时序约束**没有；端口名有了但**封装脚号对照**没有；
"配置对"不等于"外设初始化对"（`main.c` 仍是骨架）。

## P56 — 引脚定义表：`.ioc` 的 `PA9` ↔ 网表的 `30`（2026-11-23）

> 用户点的第 8 件事：补"封装脚号 ↔ 端口名"那张表，让 `ioc.pin_conflict`
> 从软提示变成**硬判据**。

### 之前的问题

P55 把 `.ioc` 的配置读进了契约，但只能**按名字对**：`.ioc` 说 `PA9`，
网表说 `30`，中间差一张表。于是"`.ioc` 把这个脚配成 UART、板子却接去 SPI"
仍然只能等台架发现。

### 新增

| 文件 | 内容 |
|---|---|
| `design/pinout.py` | 表的读写/自检/建表（LCSC 厂商符号 → 脚号↔端口名）；**不完整的符号拒绝建表** |
| `design/pinout/stm32f103c8t6.json` | 48 脚（C8734）|
| `design/pinout/stm32f103c6t6.json` | 48 脚（C8303）|
| `design/pinout/stm32f401rct6.json` | 64 脚（C74524）|
| `design/pinout/stm32h743iit6.json` | 176 脚（C89597）|
| `scripts/check_p56.py` | 23 项验收（表自检 + 交叉核对 + 端到端 + 退化 + CLI）|
| `design_cli.py` `pinout` | `list` / `show` / `build --lcsc=` / `validate` |
| `agent_tools.py` + `src/tools.ts` | `hw_pinout` 工具（58 → 59）|

### 修改

| 文件 | 改动 |
|---|---|
| `design/interface.py` | `.ioc` 端口名经表映射到封装脚号，逐脚对账；新增硬判据 `ioc.pin_conflict`；`af.pin_numbers`、`pin_join`；有表时"脚号对照"不再是 unknown |
| `scripts/check_p55.py` | 2f/5c 改成**表感知**（有表时该 unknown 消失、拿掉表必须回来）|
| `docs/runbook.md` | 新增 §19 |
| `AGENTS.md` | §3.51–3.53 指针加 §19 |

### 关键数字（实测）

```
4 张表：48 / 48 / 64 / 176 脚，全部过 validate
交叉核对：4 张表的非 GPIO 脚名与 fwgen.mcus 的 power_pins **前缀覆盖**
  （写法差异显式列 allowlist：VDDAVREF+/PDR/VCAP/VREF+）
拒绝：STM32F405RGT6 的符号只有 55/64 脚 → 不建表
端到端（复刻-示例 + F103-GPS.ioc）：19 脚全部落位，冲突 0
  PA9↔30 UART_TX / PA10↔31 UART_RX / PD0↔5 OSC_IN / PD1↔6 OSC_OUT / PA8↔29 GPS_EN
  配了但没接：PA2/PA3/PB13/PB14/PB15（USART2/SPI2）→ 5 条软提示
退化：表里 26↔30 对调 → 真实项目 failed（PB13 配成 SPI2_SCK，脚 30 是 UART_TX）
拿掉表：冲突归零、pin_join 空、"脚号对照"回到 unknown
```

### 验收

```
python -m hw_velxio.scripts.check_p56     # 23 项
python -m hw_velxio.scripts.check_p55     # 29 项（改成表感知后仍全绿）
npm test                                  # 765
npm run check / build                     # tsc 干净
node hw_velxio/scripts/check_tools.mjs    # 59 个工具
```

### 诚实边界

* 表来源是 **LCSC/EasyEDA 厂商符号**（`source="catalog"`），不是 datasheet 原文；
  要更硬就按 datasheet 表 5 手工录一张并标 `datasheet`
* 表只给**脚号 ↔ 端口名**与电源/地/复位/启动；**不给 AF 编号**（仍要 datasheet）
* **表错了会误判**：冲突信息里同时写出"表说脚 30=PA9、`.ioc` 说 PA9 是 USART1_TX、
  网表说 30 是 UART_TX"，让人能判断是谁错了

## P57 — AF 复用表：这个脚能不能做这个功能（AF 编号）（2026-11-23）

> 用户点的第 9 件事：AF 复用表 —— `pins.h` 最后一块"不能判"的东西。

### 之前的问题

P56 让 `PA9` 对上了 `30`，但"`PA9` 能不能做 `USART1_TX`、AF 几号"仍然只能信 `.ioc`。
于是"`.ioc` 把 UART 放到做不了 UART 的脚上"和"固件 `GPIO_AF4_USART1` 写错"
这两类错都查不出来。

### 新增

| 文件 | 内容 |
|---|---|
| `design/afmap.py` | ST 官方 open pin data → AF 表：建表/自检/与引脚表交叉/查询（`match_signal`/`pins_for_signal`）|
| `design/afmap/stm32f103c8t6.json` | F1：48 脚，0 个 AF 编号（固定功能 + 重映射）|
| `design/afmap/stm32f103c6t6.json` | 同上 |
| `design/afmap/stm32f401rct6.json` | F4：64 脚，141 条 AF |
| `design/afmap/stm32h743iit6.json` | H7：176 脚，964 条 AF |
| `scripts/check_p57.py` | 24 项验收（表 + 两个厂商源 + 固件证据 + 冲突 + 编译期 + CLI）|
| `design_cli.py` `afmap` | `list` / `show --pin=` / `can --signal=` / `build --part=` / `validate` |
| `agent_tools.py` + `src/tools.ts` | `hw_afmap` 工具（59 → 60）|

### 修改

| 文件 | 改动 |
|---|---|
| `design/interface.py` | `.ioc` 的每个功能查 AF 表：**功能不在这根脚上 = 硬冲突** `ioc.function_not_on_pin`；`af.style/af_numbers/details`；`unknown` 只剩「时序」；人读版显示 `（AF7）`/`（重映射 …）` |
| `design/fw_build.py` | `hwv_pins.h` 生成 `HWV_AF_<功能>` + `HWV_EXPECT_AF`；负例 5 → **6 条**（含 AF 写错）；F1 不生成 AF 宏 |
| `scripts/check_p55.py` | 2f 变成"引脚表 + AF 表"双感知 |
| `scripts/check_p56.py` | 3d 改成"网表接反"（用 `.ioc` 造非法 AF 会被 P57 判死）|
| `docs/runbook.md` | 新增 §20 |
| `AGENTS.md` | §3.51–3.53 指针加 §20；§6 的"AF 表没录"改成"只有 4 个型号" |

### 关键数字（实测）

```
4 张表：48 / 48 / 64 / 176 脚；AF 条数 0 / 0 / 141 / 964
两个厂商源逐脚一致：ST Position == P56 引脚表的封装脚号（4 张表全等）
固件证据：F401 9 条、H743 41 条 GPIO_AF* 全部与表一致
真实项目（复刻-示例 + F103-GPS.ioc）：
  19 个功能：ok 11 / exti 2 / gpio 6；style=F1_REMAP；冲突 0
  F103 的 PB6=USART1_TX 只在 USART1_REMAP1 下成立（default=false）
F401 夹具：PA9→AF7；负例 6/6 全部编不过
退化：表里删掉 PA9=USART1_TX → 真实 `.ioc` 变红；拿掉表 → AF 编号回到 unknown
```

### 验收

```
python -m hw_velxio.scripts.check_p57     # 24 项
python -m hw_velxio.scripts.check_p55/p56 # 全绿（已改成表感知）
npm test / npm run check / build / check_tools
```

### 诚实边界

* 来源是 ST 官方引脚库（`catalog`），不是 datasheet PDF；要更硬就手工录并标 `datasheet`
* 表只给**功能与编号**，不给电气/时序、也不给"某 AF 只在特定批次可用"
* 只建了 4 个型号（F103C8/C6、F401RC、H743II）；表外型号功能一律 `unknown`
* F1 没有 AF 编号（用 AFIO 重映射），**不要**为了"统一"给它编 AF7

## P58–P59 — 立创侧扩产：封装几何 / 制造包 / 原理图 DRC / 拼板（2026-11-23）

> 用户点的第 10 件事：把「立创里有很多东西」真正接进链路（高耗时但要好效果）。

### 新增

| 文件 | 内容 |
|---|---|
| `design/lceda_geom.py` | `LIB_Footprint.getGeometry` → 本体/焊盘包围盒 + 焊盘统计（pitch/最小间隙）|
| `design/lceda_fab.py` | `pcb_ManufactureData` 5 种导出（STEP/DXF/PDF/网表/测试点），8 MB 上限分块 |
| `design/lceda_sch.py` | 逐页 `SCH_Drc.check(true,false,true)`，按 type 过滤 |
| `design/lceda_panel.py` | `DMT_Panel.createPanel` + 明确的拼板边界（`PNL_*` 未暴露）|
| `scripts/check_p58.py` | 13 项（几何自洽/mil 换算/写回纪律/退化）|
| `scripts/check_p59.py` | 12 项（5 种制造文件/假文件防护/DRC gate/拼板 not_ready）|

### 修改

| 文件 | 改动 |
|---|---|
| `design_cli.py` | 新增 `lceda-geom` / `lceda-fab` / `lceda-sch-drc` / `lceda-panel` 四个命令 |
| `design/store.py` | `ARTIFACTS` 加 `geom.json` / `fab.json` / `sch_drc.json` / `panel.json` |
| `design/audit.py` | schematic 段接入 `sch_drc.json`（有 error → failed；没跑 → note 里写 missing）|
| `docs/runbook.md` | 新增 §21 |
| `AGENTS.md` | 指针表加 §21；压缩 §17–§20 保持 65 KB 内 |

### 实测（`复刻-示例`，4.1.97.04b092ac）

```
lceda-geom：28 个位号 / 19 个 LCSC → 27 拿到几何，1 个 no_footprint 写明原因
  U1(C8734)：48 焊盘、pitch 0.5、最小间隙 0.23、title 7×7×1.4、full 10×10.05
lceda-fab：5/5 落盘（STEP 14.1 MB / DXF 1.18 MB / PDF 452 KB / 网表 36.7 KB / 测试点 8.3 KB）
  STEP 走 4 MB 分块（单次返回上限 8 MB）；ipc2581 只回 2 字节 {} → not_ready
lceda-sch-drc：3 页，0 error / 0 fatal / 31 warn
lceda-panel：容器建出 + 改名成功；但 PNL_ManufactureData/Drc/Import 的 doc api 全是 null
  → 状态 not_ready + blocked_by（拼板要在面板编辑器里手工做）
```

### 验收

```
python -m hw_velxio.scripts.check_p58   # 13 项
python -m hw_velxio.scripts.check_p59   # 12 项（约 80 秒，真调用）
```

### 诚实边界

* 几何是封装源数据（catalog），写回**不冒充 datasheet**（datasheet 一颗不动）
* 制造文件只是"立创导出的那种文件"，头字节对 ≠ 内容对
* 拼板**不可调**：这一版没有放板/阵列/面板 DRC/面板制造文件
* 所有命令都需要 `emit.json`（工程 uuid）与桌面客户端；缺任何一样都是 `not_ready`

---

## 后续

**`docs/execution-plan-v3.md`（P15–P23）与 `docs/ux-plan.md`（P0–P5）全部交付。**

| 批次 | 内容 | 状态 |
|---|---|---|
| v3 P15–P23 | 多域图 / MuJoCo / 立创发射 / 统一判定 / LCSC / 固件 / 闭环 / agent / 测试 | ✅ 9 段 |
| ux P0–P5（+P5b） | 结论层 / 下一步动作 / 按对象聚合 / 参数敏感度 / 固件矩阵 / 需求草稿 / 从零开局 | ✅ 7 段 |

合计 **22 个阶段**，**23 个验收脚本**，**729 项单测**，**43 个工具**。

### 还能往哪走

按"挡住什么"排，全部记在 `AGENTS.md` §6：

| 优先 | 缺口 | 为什么排前面 |
|---|---|---|
| 高 | 立创自动布线（引擎拒绝） | 不解决就出不了能直接打板的 PCB |
| 高 | 175 个元件没网表 | 归因、单元、链路三层在那些板上全是 `unknown` |
| 高 | 面板看不到工作区草稿 | 从零草稿要 CLI / 会话 `list` 才看得见 |
| 中 | 132 个仿真参数没接线 | 物理仿真目前只对 `dt`/`mu_board` 敏感 |
| 中 | `Requirement.numbers` 无消费方 | 填了不影响任何结论 |
| 中 | MCU 的 AF 复用表 | `pins.h` 只给引脚，不给功能校验 |
| 低 | 覆盖率量化 | 729 条按"每条判据一个实例"铺，没跑过 coverage |
