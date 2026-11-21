# 执行方案 M4–M9：多域设计 → 分域仿真 → 统一判定 → 闭环

> **状态：执行方案，未开工。** 上位文档 `architecture-v3-unified.md`（五层架构）；
> 本文是它的**可开工展开**：阶段、文件、数据结构、代码骨架、验收命令、退化验证、止损。
>
> **三个参考源码**（均已本地核实）：
>
> | 参考 | 位置 | 已核实的关键事实 |
> |---|---|---|
> | RoboBuilder | `C:\Users\<user>\Desktop\RoboBuilder-main` | MuJoCo 3.12；`sim/csim.py` 2937 行；**用字符串拼 MJCF XML**；`Params` 136 项逐一落到 `frictionloss/armature/solref/solimp/margin`；`initial.json`→`learned.json` 校准改变 135 项 |
> | Traceforge | `workspace\vendor\traceforge` | `firmware/` 2061 行（7 驱动/5 targets）可搬；LCSC 端点免 key；grounding 契约 |
> | 立创EDA Pro 4.1.97 | `D:\IE\ES\PCB\lceda-pro` | CLI 实测可跑：`open --headless` / `invoke` / `doc api` / `--mcp`；**沙箱不拦**；`PCB_Document.autoRouting` + `importAutoRouteJsonFile` 存在 |

---

## 0. 总览

### 0.1 九个阶段

| 阶段 | 名称 | 层 | 估时 | 可独立验收 |
|---|---|---|---|---|
| **P15** | 多域图（Joint/Program/质量属性） | L1 | 4 天 | ✅ |
| **P16** | MuJoCo 后端（MJCF 生成） | L2 | 6 天 | ✅ |
| **P17** | 立创EDA 发射（.eprj3） | L2/L4 | 7 天 | ✅ |
| **P18** | 统一判定协议（三次启动/预算/截断） | L3 | 4 天 | ✅ |
| **P19** | LCSC 元件数据源 | L1 | 3 天 | ✅ |
| **P20** | 固件（7 驱动/5 targets + pins.h） | 横切 | 5 天 | ✅ |
| **P21** | 闭环（physical_tests + 参数校准） | L5 | 4 天 | ✅ |
| **P22** | agent 循环（假模型 0 token） | 横切 | 4 天 | ✅ |
| **P23** | 测试扩充 266→600+ | 横切 | 4 天 | 持续 |

**合计 ~41 天**（单人）。

### 0.2 三个里程碑

| 里程碑 | 含 | 累计 | 交付价值 |
|---|---|---|---|
| **M4** | P15–P18 | 21 天 | **「一个设计，电气+机械都能量化判定，出 lceda 工程」** |
| **M5** | +P19–P20 | 29 天 | **「选型通到 LCSC 目录，固件能生成并交叉核对」** |
| **M6** | +P21–P23 | 41 天 | **「实测回写、参数校准、agent 可驱动」** |

### 0.3 顺序理由（为什么不按编号）

```
P15 (多域图) ──┬──► P16 (MuJoCo) ──┐
                │                   ├──► P18 (统一判定) ──► P21 (闭环)
                └──► P17 (立创发射) ─┘
                        ↑
P19 (LCSC) ─────────────┘   P20 (固件) 独立，可与 P17 并行
                                             P22 (agent) 依赖 P15/P18 的工具面
```

**P15 必须最先**：没有 Joint/Program，P16 无输入、P18 无对象。

---

## P15：多域图（4 天）

### 目标

`design.json` 从"电气模型"升级为"多域图"，且**不升 schema、不破坏旧文件**。

### 文件

| 文件 | 动作 |
|---|---|
| `design/model.py` | 加 `Joint` / `Program` 两个 dataclass；`Part` 加 `mass_g/com_mm/inertia`；`DesignModel` 加 `joints_list/program/sim_params/physical_tests` |
| `design/joints.py` | **新建** relation 推断（~250 行） |
| `design/store.py` | `ARTIFACTS += ("joints.json", "sim_params/")`（后者是目录，特殊处理） |
| `design_cli.py` | 加 `joints` 命令（推导/校验） |
| `src/tools.ts` | 加 `hw_joints` |

### 数据结构（严格照 execution-plan §2.1 的可选字段模式）

```python
# model.py

@dataclass
class Joint:
    """机械关系。**同名概念在不同域有不同 kind**，靠 kind 分派。

    这套字段是照 RoboBuilder 的 DesignConnection(child,parent,kind,studs)
    扩的：多一个 `params`（齿轮比/轴径/DOF限位）和 `dof`（给 MuJoCo 用）。
    """
    a: str                                  # part.ref
    b: str
    kind: str                               # pin|axle|gear|chain|tread|rack|
                                            # hinge|slide|stud|stack
    params: dict[str, Any] = field(default_factory=dict)
    dof: list[str] = field(default_factory=list)   # ["rz"] / ["rx","ry"] / [] 固定
    source: str = "declared"                # declared | inferred | imported
    evidence: str = ""

    def to_dict(self): ...
    @staticmethod
    def from_dict(d): ...

@dataclass
class Program:
    """时序动作。照 RoboBuilder 的 motor_program：[(t, motor, speed)]。"""
    steps: list[dict[str, Any]] = field(default_factory=list)  # {t_s, actuator, value}
    loop: bool = False
    units: str = "normalized"               # normalized(0..1) | rpm | rad_s

# Part 加三个（都可选，空则不写）
mass_g: float | None = None
com_mm: list[float] | None = None
inertia: list[float] | None = None          # Ixx Iyy Izz Ixy Ixz Iyz

# DesignModel 加四个（都可选）
joints_list: list[dict[str, Any]] = field(default_factory=list)
program: dict[str, Any] | None = None
sim_params: dict[str, Any] = field(default_factory=dict)
physical_tests: list[dict[str, Any]] = field(default_factory=list)
```

`to_dict` 条件写出（照 `boards` 的做法 `model.py:700`）：

```python
**({"joints_list": [...]} if self.joints_list else {}),
**({"program": ...} if self.program else {}),
**({"sim_params": ...} if self.sim_params else {}),
**({"physical_tests": [...]} if self.physical_tests else {}),
```

### relation 推断（`joints.py`）

**我们比 RoboBuilder 多一样东西：网表。** 所以三类可推断：

```python
def infer(design: DesignModel) -> list[Joint]:
    """从几何与网表推断机械关系。**推断标 source=inferred，不参与硬检查。**

    可推断的三类：
      1. stack   —— z 轴相邻 + 板/件边界重叠（叠板/叠件）
      2. port    —— 网表里"控制器引脚 → 执行器/传感器"，按 pin 名语义判断
      3. contact —— 两件包围盒接触，且没有任何连接声明（提示"可能是松动配合"）

    不可推断（必须人给）：
      axle / gear / chain / tread / rack / hinge —— 需要轴径、齿数、轨迹参数
    """
```

**纪律**：`source="inferred"` 的 Joint **不进入 P16 的 MJCF**（同 `links.validate`
跳过 inferred 的做法）。要参与仿真必须 `promote`。

### 验收（`scripts/check_m15.py`）

```
1. 旧 design.json（5 个真实工程）读进来 → joints_list 为空、行为不变
   （回归：现有 266 测试必须全绿）
2. 合成叠板设计 → infer() 出 ≥1 条 kind=stack 且 source=inferred
3. 构造"网表里 MCU 引脚的网连到电机" → infer() 出 port 关系
4. 闸门断言：inferred 的 Joint 进 to_mjcf() 后什么都不生成
5. 退化验证：把 source 判断删掉 → 验收 4 变红
6. 写一个带 Joint/Program 的 design.json → 读回逐字段相等（round-trip）
```

---

## P16：MuJoCo 后端（6 天）

### 目标

`DesignModel` → MJCF XML → MuJoCo → 轨迹结果。**只做机械域**，不碰电气。

### 关键决策：字符串拼 XML，还是 `MjSpec`？

RoboBuilder 用**字符串拼 XML**（`self.xml = f'<mujoco ...>'`，1808 行起）。
我们**也这么选**，理由：

1. 它的 136 个参数是在 XML 里逐字段落的，照抄能直接对上
2. `MjSpec`（MuJoCo 3.x 的 Python 建模 API）在 3.12 有，但文档/示例少于 XML
3. XML 可读、可 diff、可存档——**符合我们"产物可审计"的纪律**

### 文件

| 文件 | 动作 | 行数估 |
|---|---|---|
| `design/mjcf.py` | **新建** DesignModel → MJCF XML | ~450 |
| `design/mujoco_backend.py` | **新建** 编译/跑/取轨迹/视频 | ~300 |
| `design/sim_dispatch.py` | **新建** 按域分派（含 electrical/geometry/lceda 占位） | ~150 |
| `design_cli.py` | 加 `sim` 命令（`--domain kinematic`） | — |

### MJCF 生成骨架（照 `csim.py:842` 的 `part_xml` 结构）

```python
# design/mjcf.py
def to_mjcf(design: DesignModel, params: dict | None = None) -> str:
    """DesignModel → MJCF 字符串。

    ## 与 RoboBuilder 的对应关系（这是移植的精度所在）

    | 它的 | 我们的 |
    |---|---|
    | part_xml(n, ref)：body 位姿相对父件 | `_body_xml(part, parent)` 用 Placement 算相对位姿 |
    | joint kind=motor/pin/axle | `Joint.kind` 分派：hinge/slide/固定 |
    | `<contact><exclude>` | 同一 Board 内零件互斥（避免装配体自碰撞） |
    | `<equality>`（齿轮/链条） | P15 的 `gear`/`chain` Joint → equality |
    | `<tendon>`（绳索） | 飞行器不需要，跳过 |
    | 136 个 Params → MJCF 属性 | `sim_params` → 同名映射（见下表） |

    每个参数落到哪（照它的做法）：
      stud_pull_n      → equality 的 solref / 自定义力桩
      axle_fric_nm     → joint frictionloss
      motor_friction   → joint frictionloss（按 stall 折算）
      rotor_j          → joint armature
      mesh_mu          → equality 的 solimp（近似）
      dt               → <option timestep>
    """
```

**参数映射表**（P16 的交付物之一，`design/sim_params_default.json`）：

```json
{
  "note": "每个参数的物理含义与来源。initial 版来自公开数据，learned 版来自实测。",
  "params": {
    "dt":          {"value": 0.0005, "unit": "s", "mjcf": "option.timestep", "source": "public"},
    "mu_default":  {"value": 0.5,    "unit": "-", "mjcf": "geom.friction", "source": "public"},
    "rotor_j":     {"value": 2e-5,   "unit": "kg m^2", "mjcf": "joint.armature", "source": "public"},
    "...": "按无人机需要的项列（不搬乐高那 136 项）"
  }
}
```

**无人机只保留约 15–20 项**，不是 136 项：

```
结构：mass、com、inertia、arm 刚度
接触：mu（脚垫/地面）、solref、solimp、margin
驱动：motor KV、桨半径、桨叶数、CT、CP、rotor_j
环境：dt、gravity、风（可选）
```

### `mujoco_backend.py`

```python
def run(design, *, domain="kinematic", params=None, duration_s=5.0,
        video=False, out_dir=None) -> SimResult:
    """编译跑一遍，返回判定所需的最小集：
      passed/failed、结束姿态、最终位置、是否立住、轨迹、视频路径、
      用了多少步/多少秒。
    """
```

### 验收

```
1. 单件落地测试：一个 100g 方块从 0.1m 落下 → 停在 z≈0（不穿地、不弹飞）
   ← 这是 MuJoCo 接入的最小可用证明
2. 四旋翼悬停：4 个推力合成的合力 = 重力 → 高度保持 ±1cm 5 秒
   （用 MUJOCO 自带的多旋翼激励，不写控制律，只给恒定推力）
3. 折叠包络：两个 hinge Joint 扫 -90..+90° → 输出每帧包围盒，与实物（或 CAD）比对
4. 确定性：同 params 同设计两次跑 → 最终位姿逐位相等（MuJoCo 单线程确定性）
5. 闸门断言：inferred Joint 不进 MJCF（P15 验收 4 的镜像）
6. 性能：单次 5 秒仿真 < 30s（超时即减步长/降 dt 的止损信号）
```

---

## P17：立创EDA 发射（7 天）

### 目标

`design.json` → `.eprj3` 工程（原理图 + PCB 已布线）。**实测 CLI 可用、沙箱不拦。**

### 文件

| 文件 | 动作 |
|---|---|
| `design/lceda.py` | **新建** 会话封装 + 版本探测（~180 行） |
| `design/emit_lceda.py` | **新建** 四段发射（~420 行） |
| `design/stackup_spec.py` | **新建** 层数/网络类/差分对约束（~120 行） |
| `scripts/lceda_probe.py` | 一次性探测（运维） |

### 会话封装（照实测的 JSON envelope）

```python
@dataclass
class LcedaSession:
    exe: str
    session_id: str = ""
    version: str = ""     # 从 --help 头部抓：JLCEDA Pro 4.1.97.04b092ac

    def open(self, project=None, *, headless=True) -> dict:
        """{"ok":true,"value":{"sessionId":...}} —— 实测 530ms"""

    def invoke(self, code: str, *, expect: str = "", timeout=60_000) -> dict:
        """跑 JS。**强制自校验**：

        它的文档原话：ok:true 只代表代码跑通，不代表操作成功。
        所以模板强制：
            const r = await ...;
            if (!r || !r.ok) throw new Error('<业务失败>');
            return {ok:true, ...};

        `expect` 是必须出现的关键字，没出现就判失败。
        """

    def doc(self, kind: str, class_name: str = "") -> dict:
        """api / format / external —— **运行时探测，不硬编码签名**。"""
```

### 四段发射

```python
def emit(design, *, jobs=("project","schematic","pcb","outputs"),
         route="native", session=None) -> EmitReport:
    """段与段只通过 lceda 工程状态耦合，不传内存对象——
    任何一段失败不影响已落盘的部分。"""
```

| 段 | invoke 内容 | 用到的 API（实测存在） |
|---|---|---|
| `project` | createProject(EPRJ3) + addBoard + 设层数/尺寸 | `dmt_Project`、`SCH_Document` |
| `schematic` | 装器件 → 连线 → 网络标签 → 功能分组 | `SCH_SourcePrimitiveComponent`、`...Wire`、`...Attribute.createNetLabel`、`ISCH_SourceDocumentFunctionGroup` |
| `pcb` | 放置 → 扇出 → 布线 | `PCB_PrimitiveComponent`、`PCB_Document.fanout`、`PCB_Document.autoRouting` |
| `outputs` | Gerber/钻孔/BOM/STEP/3D PNG | `PCB_ManufactureData`、`SCH_ManufactureData`、`PCB_RayTracerEngine` |

**布线三条路**（参数 `--route`）：
* `native`：`PCB_Document.autoRouting`（默认）
* `ours`：跑我们的路由 → `importAutoRouteJsonFile`（实测存在）
* `none`：只放置

### 关键：器件→符号的映射

`schematic` 段最大的难点不是画，是**找到符号**：

```python
def resolve_symbol(session, part) -> str | None:
    """按 part.lcsc 或 mpn 从 LIB_Device 搜符号。
    搜不到 → 该器件进 unresolved（**不猜符号**），整段继续。
    """
```

**这就是 P19（LCSC）必须在 P17 前的原因**：没有 `lcsc/mpn`，搜不到符号。

### 验收

```
1. probe：`hw_emit status` 报版本 + 关键类存在（doc api 核对 5 个类）
2. 最小工程：1 个电阻 + 2 个焊盘 → createProject + 画原理图 + 导出 PNG
3. 回读 parity：lceda 导出的网表 vs design.json 的 nets 逐条相等
4. 布线：2 层、3 网、无 DRC error（用 lceda 的 DRC 当外部证据，不进 ladder）
5. 器件搜不到 → unresolved 非空且其余器件继续（不整批失败）
6. 闸门断言：无 lceda CLI → `not_ready` 且 jobs 全空，**不抛异常**
7. 退化验证：把 invoke 的 expect 自校验删掉 → 人为制造业务失败仍报 ok → 验收 6 变红
```

### 实测状态（2026-11-21 交付）

| 项 | 计划 | 实测 |
|---|---|---|
| 文件规模 | ~720 行 | `lceda.py` 646 + `emit_lceda.py` 1400 + `stackup_spec.py` 233 = **2279 行**（+ 探测 87 + 验收 528） |
| 单向发射耗时 | 未定 | **43s**（3 器件 / 5 引脚 / 1 块板） |
| 器件→符号 | 「lcsc 或 mpn 搜」 | 必须**回核 `Supplier Part`**：`searchByProperties` 是模糊的，`C999999999` 会返回近似结果 |
| 布线 | `PCB_Document.autoRouting` | **本机引擎拒绝执行**（5 条排除实验见 `CHANGES.md` P17 F8）；`route=native` → `not_ready` |
| 产物落盘 | `saveFileToFileSystem` | 默认被「外部交互权限」拦；生成正常（Gerber zip） |
| parity | 原理图网表 vs design | 原理图网表**读回来是陈旧的**，改用 **PCB 网表**（差别：多一次导入，但那才是拿去制造的东西） |
| 板框 | 「设层数/尺寸」 | `pcb_PrimitiveLine` 在层 11 上被拒；必须 `pcb_MathPolygon.createPolygon(['R',…])` + `pcb_PrimitivePolyline.create` |
| 元件摆放 | 「用我们的 Placement」 | `importChanges` 会把器件丢在板框外（y≈-225），必须 `modify` 逐个摆回 |
| 验收 | 7 条 | **24 条**（9 真机 + 5 通道/退化 + 10 纯函数），全通过 |

未做 / 做不到（已写进 `CHANGES.md`）：原理图按功能分组排版、PCB 的 Y 轴方向对照实验、
`--route ours`（M9）、多板按名字匹配。

---

## P18：统一判定协议（4 天）

### 目标

一套协议覆盖所有域：**三态 + 三次启动 + 预算 + 截断**。

### 文件

| 文件 | 动作 |
|---|---|
| `design/robustness.py` | **新建** 三次启动扰动统一定义（~120 行） |
| `design/ladder.py` | 加 **V7 kinematic**（实物/仿真运动）与 **V8 manufacture**（外部证据） |
| `design/generate.py` | 加 `sim_budget`（仿真调用上限） |
| `design/handoff.py` | 加第六类 `external`（lceda/MuJoCo 的证据） |

### 三次启动（照 RoboBuilder 的判定）

```python
# design/robustness.py
PERTURBATIONS = (
    {"name": "nominal",   "desc": "标称初始条件"},
    {"name": "shift_1mm", "desc": "位置偏移 1mm"},
    {"name": "late_50ms", "desc": "时序延后 50ms"},
)

def triplicate(fn, subject) -> dict:
    """跑三次扰动，**全过才算 pass**。
    任一次 fail → fail；任一次 unknown → unknown（不是 pass）。
    """
```

**按域适配**（不照搬它的数值，但照搬协议）：

| 域 | 扰动 |
|---|---|
| 几何 V2 | 公差 ±0.1mm |
| 电气 V3 | 轨压 ±5% / 负载阶跃 / 温度 ±10℃ |
| 运动 V7 | 初始位姿 ±1mm / 时序 +50ms / 质量 ±5% |

### 预算（`generate.py`）

```python
def generate(req, *, max_candidates=50, sim_budget=200):
    """枚举 + 判定都要计数。**仿真次数是硬成本**：
    RoboBuilder 给 agent 的 B 次就是这个——真实测试几小时，仿真虽快也不能无限。
    超出即停，报告 used/limit。
    """
```

### 验收

```
1. 三次启动：构造"标称过、偏 1mm 挂"的夹具 → 整体 unknown（不是 pass）
2. V7 层：MuJoCo 仿真 + 三次扰动 → 三态正确
3. sim_budget=5 时跑 20 候选 → 只验 5 个，报告 used=5
4. handoff 第六类：lceda DRC error → external 类，**不进 blocking**
5. 截断仍生效：V1 fail → V2..V8 全 not_run（回归 check_ladder）
6. 退化验证：把"任一次 unknown → 整体 unknown"改成 "pass" → 验收 1 变红
```

---

## P19：LCSC 元件数据源（3 天）

### 目标

能力表从 14 型号 → 目录级，且 **catalog 级不进硬选型**。

### 文件（沿用 `adoption-detail.md` §2 的设计，此处只列差异）

| 文件 | 动作 |
|---|---|
| `design/parts_catalog.py` | **新建**：免 key 端点 + 缓存（TTL 7 天）+ `PartEnriched` |
| `design/package_sizes.py` | **新建**：封装族 → 尺寸表（~30 行覆盖 01005→LQFP100） |
| `design_cli.py` | 加 `parts-search` / `parts-enrich` / `parts-promote` |

### 端点（实测自 Traceforge `parts/providers/jlc.py`，全部免 key）

```
JLC 详情  https://cart.jlcpcb.com/shoppingCart/smtGood/getComponentDetail?componentCode={lcsc}
JLC 搜索  https://jlcpcb.com/api/overseas-pcb-order/v1/shoppingCart/smtGood/selectSmtComponentList
LCSC 详情 https://wmsc.lcsc.com/ftps/wm/product/detail?productCode={code}
```

**解析纪律（照抄它的注释）**："none of them is a contractual API,
so every parser here is defensive about missing fields"——全部 `.get()`。

### 信任级（核心纪律，与 AGENTS §1.2 一致）

```
estimated(0) < catalog(1) < datasheet(2) < measured(3)
```
* catalog 级不进 `capability.candidates` 硬选型，只出现在 `explain_miss.rejected`
* 出口：`parts-promote <mpn> --datasheet <url>`（人核对后升 datasheet）

### 验收

```
1. search "stm32f103 lqfp48" → ≥5 条带 package/stock
2. enrich C89852 → size_mm 非 0、physical_source=catalog
3. 闸门：catalog 型号在 explain_miss.rejected，不在 candidates
4. promote 后进 candidates
5. HWV_PARTS_OFFLINE=1 → 缓存 stale 或 not_ready，主流程不崩
6. 退化验证：删掉信任级判断 → 验收 3 变红
```

### 实测状态（2026-11-21 交付）

| 项 | 计划 | 实测 |
|---|---|---|
| 文件规模 | ~410 行（含 cli/ts） | `parts_catalog.py` 677 + `package_sizes.py` 328 + 验收 466 = **1471 行**，另加 3 个真实返回夹具 |
| 搜索返回形状 | `data.componentList[]` | **`data.componentPageInfo.list[]`**；按 `componentList` 取永远得到空 |
| enrich C89852 尺寸 | ≈{4.9, 6.0, 1.6}（SOIC-8W） | JEDEC 宽体 208mil = **{5.28, 5.2, 1.75}**；计划那三个数是估的 |
| HTTP 库 | httpx | stdlib `urllib`（仓库现有约定；不为 GET+POST 加依赖） |
| 封装尺寸来源 | 「JEDEC/厂商封装图 30 行」 | 10 片式 + 46 族 + 5 族公式 + 24 别名；**查不到返回 unknown 且尺寸全 0**，公式推的标 `formula` |
| 信任级 | 「catalog 不进硬选型」 | 两个文件分别承载 catalog / datasheet；`save_catalog_entry` **强制** source=catalog（绕不过），`promote_entry` 无手册页**不写盘** |
| 闸门出口 | `parts-promote <mpn> --datasheet` | 还必须给 `--role` / `--needs`：目录里没有「它是什么」「它能干什么」，这两项只能人给 |
| 验收 | 6 条 | **25 条**（3 封装 + 5 解析 + 6 接口/缓存 + 5 闸门含退化 + 4 回填 + 2 真机），全通过 |

三处「计划写错、实测为准」：搜索键名、C89852 的尺寸、`encapsulationNumber` 是
**每盘颗数**（95）不是尺寸或引脚数。

未做 / 有意不做：BGA 等特殊封装的尺寸（表里没有就 unknown）、
`fills`/`provides` 的自动推导（猜语义 = 假通过）、导入器自动补 `lcsc`。

---

## P20：固件（5 天）

### 目标

搬 Traceforge 的 `firmware/`（2061 行）+ 扩 MCU + 加 `pins.h` 交叉核对。

### 文件

| 文件 | 来源 | 改造 |
|---|---|---|
| `design/firmware/drivers_data.py` | 搬（256 行） | 保留"每项注明 datasheet 章节" |
| `design/firmware/drivers.py` | 搬（372 行） | 输出目录 `firmware/` |
| `design/firmware/mcus.py` | 搬（177 行） | **3 → 7 个 MCU**（用 `.ioc` 型号） |
| `design/firmware/scaffold.py` | 搬（466 行） | targets 保持 5 个，接我们的 `pins.h` |
| `design/firmware/toolchain.py` | 搬（262 行） | 复用 `design/toolchain.py` |
| `design/pinmap.py` | **新建**（~350 行） | `.ioc` → `pins.h` + 固件侧抽取 + 交叉核对 |

### `pins.h` 与交叉核对（我们的增量，它没有）

```
.ioc（P8 已解析 77 端点/84 label）
   → pins.h :  #define HWV_PIN_TRIG GPIO_PIN_8  /* PA8 VL53-PWR-EN */
固件源码（glob）
   → 抽取 GPIO_PIN_x / GPIOx / USARTn
   → 交叉核对：
       硬件配了软件没用  → 闲置（提示）
       软件用了硬件没配  → **幽灵引脚（error）**
       方向冲突          → error
```

**输出必须引用双方**：`"幽灵引脚 PA8: F401-Front.ioc 无此配置 ← 固件 main.c:42"`

### 验收

```
1. 对 F401-Front.ioc 生成 pins.h，含 VL53_PWR_EN/USART2_TX 宏
2. 合成固件（用 .ioc 没配的 PA10、没用已配的 PB6）→ 1 幽灵 + 1 闲置，带行号
3. 改 .ioc 一个引脚 → 重生成 → 编译失败于引用处
4. firmware driver SHT31 → 生成 init/read C 代码，含寄存器地址
5. scaffold --target arduino/esp-idf → 5 个 target 都能出骨架
6. 非 STM32 SDK → not_ready，不猜
7. 退化验证：幽灵引脚降为提示 → 验收 2 红
```

### 实测状态（2026-11-21 交付）

| 项 | 计划 | 实测 |
|---|---|---|
| 目录 | `design/firmware/*.py` | **`design/fwgen/`**：`design/firmware.py` 已存在（P4 的 6 条文本检查），Python 不允许同名并存 |
| 搬运量 | 2061 行全搬 | 搬 **1271 行**（`drivers_data`/`drivers`/`scaffold`/`mcus` 原样或近原样）+ 增量（`netlist` 重写、`pinmap` 加大半）；`cli.py`（174）与 `toolchain.py`（262）**没搬**：前者依赖 `rich` 且我们有自己的 CLI，后者与 `design/toolchain.py` 会分叉 |
| MCU | 3 → 7 | **7**（C6/C8 拆成两条：C6 必须排在通用 f103 前面，否则 `.ioc` 写 C6 报成 C8） |
| `pins.h` | `#define HWV_PIN_TRIG GPIO_PIN_8` | 两套写法并存：寄存器级 `PIN_x_PORT`（`'A'`+位号，scaffold 的 stm32 target 用）+ HAL `HWV_PIN_x = GPIO_PIN_15` / `HWV_PORT_x = GPIOA` |
| 交叉核对 | 3 类 | 3 类，全部带 `文件:行号` 与双方依据；幽灵/方向冲突是 **error**，闲置是提示 |
| 验收 | 7 条 | **24 条**（4 pins + 4 核对 + 1 真 gcc 编译 + 3 驱动 + 3 骨架 + 1 SDK + 3 MCU + 1 退化 + 4 CLI 端到端），全通过 |

四处「实测坑、计划没提」：CubeMX 对 GPIO 输出**不写 `.Mode` 行**（只看 Mode 会漏掉
所有带 label 的脚）、宏名退让顺序漏了信号名、CLI 的 `status` 被 `**report` 覆盖、
`--out` 相对路径落到了 cwd。

未做 / 有意不做：MCU 的 AF 复用表（没抄就不写）、引脚存在性校验（缺封装脚号表）、
非 STM32 固件的抽取（报 `not_ready`，不猜别的 SDK 写法）。

---

## P21：闭环（4 天）

### 目标

`physical_tests` 回写 + 仿真参数成对校准（**含"重放旧失败"校验**）。

### 文件

| 文件 | 动作 |
|---|---|
| `design/real_tests.py` | **新建**：`physical_tests` 结构 + 回写 + `contact_sheet` |
| `design/sim_calibration.py` | **新建**：参数校准 + 自洽性校验 |
| `design/handoff.py` | `_external()` 消费 `physical_tests`（实测=最高级证据） |

### `physical_tests` 结构（照 RoboBuilder 的 loop，加我们的引用纪律）

```json
{
  "id": "pt-001",
  "subject": "避障小车 v1",
  "test": "爬 15mm 台阶",
  "result": "fail",
  "observed": "右前轮打滑，机架上翘 12°",
  "evidence": {"video": "...mp4", "frames": ["00:03"], "contact_sheet": "...png"},
  "implicated": ["ref:wheel_R", "ref:motor_R"],
  "sim_params": "initial",
  "sim_reproduced": false
}
```

### 校准的自洽性校验（**这条是全方案最关键的机制**）

RoboBuilder 项目页实测：

> **≤2 个参数/次失败，且只有"旧设计在新参数下也按录像那样失败"才保留**

```python
def calibrate(design, test, params, candidates) -> dict:
    """用真实失败校准仿真参数。

    **验收条件不是"新设计能过"，而是"旧设计在新参数下复现录像里的失败"。**
    这条防止为了通过而作弊——与我们"不能自己出题自己批"同源。
    """
    adopted = []
    for cand in candidates[:2]:        # 每次最多 2 个参数
        new = {**params, **cand}
        old_verdict = run(design_old, new)     # 旧设计
        if old_verdict.reproduces(test):        # 复现录像
            adopted.append(cand)
    return {"adopted": adopted, "params": ...}
```

### 验收

```
1. 写一条 fail 的 physical_test（带 implicated refs）→ handoff 报 external 类
2. 校准：给一个"仿真通过但实测失败"的案例 → 采用新参数后，旧设计在仿真里也失败
3. **反向**：给一组"让新设计能过但旧设计不复现失败"的参数 → **拒绝采用**
4. 参数存档：sim_params/initial.json 与 learned-<date>.json 成对存在
5. contact_sheet：视频 → 单张缩略图（ffmpeg 抽帧拼接）
6. 退化验证：把"必须复现旧失败"改成"新设计通过即采用" → 验收 3 变红
```

### 实测状态（2026-11-21 交付）

| 项 | 计划 | 实测 |
|---|---|---|
| 文件 | 2 个新模块 + handoff 改 | `real_tests.py` 407 + `sim_calibration.py` 323 + 验收 432 = **1162 行** |
| 校准规则 | ≤2 参数/次，必须复现旧失败 | 四条拒绝理由全部实测生效：`old_passed` / `old_unknown` / `too_many_params` / `unknown_param` |
| 「重放旧失败」 | 采纳条件 | **这是唯一采纳路径**。真机 r3demo 上 `mu_tyre_rod=0.57` 被拒（旧设计在新参数下仍 pass）——规则在真数据上的一次实际拦截 |
| 三态 | `sim_reproduced: false` | 实为**三态** `True/False/None`；`None` 不写进 JSON（否则"没试过"会变成"没复现"） |
| 成对存档 | initial + learned | `sim_params/initial.json` + `learned-<date>.json`；`turned_on` 单列（初始 0 → 校准后非 0 = 初始没建模的物理） |
| contact_sheet | ffmpeg 抽帧拼接 | **本机没有 ffmpeg**（PATH 实测无）→ 返回可执行命令行 + `not_ready`；验收判据写成"要么出图、要么给命令" |
| 验收 | 6 条 | **19 条**（6 记录/交接 + 4 校准 + 3 存档 + 2 缩略图 + 2 真 MuJoCo + 1 退化 + 1 真工程），全通过 |

三处实测坑：`accepted` 写成字段导致"采纳了但报 False"、`new_status` 默认 `"unknown"`
把"没评估"和"判不出"混成一个、`handoff.py` 里中文引号又破坏了一次字符串（AGENTS §3.3）。

未做 / 有意不做：contact_sheet 的实际拼接（缺 ffmpeg）、场景专用判断器
（接口 `Runner` 已留）、校准的回溯搜索（贪心，一条失败最多动一对参数）。

---

## P22：agent 循环（4 天）

### 目标

沿 Traceforge 骨架，工具面收窄，全程假模型 0 token 可测。

### 文件

| 文件 | 动作 |
|---|---|
| `design/agent_loop.py` | **新建**：追加式历史 + 流式入参校验 + 三档审批（~400 行） |
| `design/agent_tools.py` | **新建**：白名单工具表（~200 行） |
| `scripts/fake_model.py` | **新建**：ScriptedClient（照 `traceforge/tests/conftest.py:111`） |
| `agent_docs/` | **新建**：照 RoboBuilder 的 11 份文档，写我们的设计空间说明 |

### 工具白名单（**没有直写 design.json 的细粒度工具**）

| 暴露 | 说明 |
|---|---|
| `hw_list/get/snapshot` | 读 |
| `hw_ladder/claims/justify/handoff/links/units` | 判定（产物独立） |
| `hw_generate` | 出候选 |
| `hw_sim` | 分域仿真 |
| `hw_emit` | 发射（**只暴露封装后的**，不暴露裸 invoke） |
| `hw_put` | **唯一写 design.json 入口**（过 validate） |
| `hw_promote` | 人确认通道（auto 模式也必问） |

**没有**：`setpart/move/delpart`（细粒度改动太危险）。

### 验收

```
1. ScriptedClient 端到端：需求 → hw_generate → 选 candNN → hw_put → hw_ladder 全绿
2. 闸门：注册表里不存在 setpart/move/delpart
3. read-only 模式下 hw_put 被拒
4. 中断 resume → 重放结果一致
5. 退化验证：auto 放行 promote → 验收 3 红
```

### 实测状态（2026-11-21 交付）

| 项 | 计划 | 实测 |
|---|---|---|
| 文件 | `agent_loop` ~400 + `agent_tools` ~200 + `fake_model` + `agent_docs/` | 465 + 396 + 134 + 验收 424 + 文档 213 = **1632 行** |
| 白名单 | 14 个工具 / 没有 setpart/move/delpart | 14 个，四档 4/7/2/1；泄漏断言 1a 通过 |
| 三档审批 | auto / ask / 人确认 | 实为 **四档 × 三模式** = 12 格，逐格断言；`human` 在 `auto` 也是 `ask` |
| 追加式历史 | "追加式" | `Journal` **没有 update/delete 方法**；seq 严格递增有断言 |
| 流式入参校验 | "流式" | 校验在每次调用**前**做（含未知键/类型），不是逐 token 增量解析——假模型一次给全量，那层没有可测对象 |
| 假模型端到端 | 需求 → generate → 选 cand → put → ladder | 实测通过：3 次工具调用、`ladder=ok`、`design.json` 落盘 |
| 中断 resume | 重放结果一致 | 第 7 个事件处中断 → 续跑到 11 个事件，决策序列**逐条一致**，且不重跑工具 |
| `agent_docs/` | 照 RoboBuilder 的 11 份 | **5 份**（其余 6 份是乐高领域内容，搬来是噪音） |
| 验收 | 5 条 | **19 条**（4 白名单 + 5 审批 + 2 入参 + 3 端到端 + 4 续跑 + 1 退化），全通过 |

四处实测坑：工具参数下划线 vs CLI 连字符（`max_candidates` 被静默忽略 → 生成 36 个而不是 2 个）、
同一仓库里 **两种 flag 风格并存**（`--key value` 与 `--key=value`）、
日志把上一轮的 `candidates` 截断成字符串导致下一步 `KeyError`、
`call()` 的形参 `name` 与 `hw_promote` 的参数撞名。

未做：真模型 provider 适配（只有 `Client` 协议 + 假模型，**没写也没假装写过**）、
逐 token 的增量参数解析。

---

## P23：测试扩充（4 天，持续）

| 目标 | 现状 → 目标 |
|---|---|
| 单测 | 266 → **≥550** |
| 端到端 | 8 个 check 脚本 → **12 个**（新增 m15/m16/m17/m18/m21/m22） |
| 回归夹具 | 5 真实 + 合成 → **+15 合成**（叠板/折叠/四旋翼/多层板） |
| 降级测试 | **必须有"无 MuJoCo / 无 lceda 也全绿"** |

### 实测状态（2026-11-21 交付）

| 项 | 计划 | 实测 |
|---|---|---|
| 单测 | 266 → ≥550 | **665**（+399），1.4s |
| 验收脚本 | 12 | **20** |
| 合成夹具 | +15 | **15**（76KB），覆盖叠板/折叠/四旋翼/多层板 |
| 降级路径 | 「必须有」 | 无 MuJoCo / 无 lceda 各跑一遍 **665 条全绿**（子进程真跑，带对照组） |
| 文件规模 | — | `tests/test_phases.py` 1185 + `check_p23.py` 202 行 |

三处实测坑：12 个夹具的 V2 是 `unknown`（P18「警告→unknown」纪律在起作用，
不是 bug）、夹具文件里 int/float 不一致导致往返测试全红（做一次规范化即可）、
`npm test` 从包目录跑时 import 不到 `hw_velxio`（所有脚本前加 `cd ..`）。

未做：没有 gcc / arduino-cli 的降级路径没验；覆盖率没量化（665 条是按
「每条判据一个实例」铺的）。

---

## 依赖图

```
P15 ──┬─► P16 ──┐
      ├─► P17 ◄─┤ P19（符号映射需要 lcsc）
      └─────────┴─► P18 ──► P21
P20 独立（可与 P16/P17 并行）
P22 依赖 P15/P18 的工具面
P23 贯穿
```

---

## 总估时与止损

| 阶段 | 估时 | 止损条件 |
|---|---|---|
| P15 | 4 | round-trip 失败 → 重新评估字段设计 |
| P16 | 6 | 单件落地/四旋翼悬停任一不通 → **停止，不接 P18 运动域** |
| P17 | 7 | parity 对不上 / 符号搜不到 >50% → 缩到"常用 200 器件" |
| P18 | 4 | 三次启动不可实现 → 降为"单次 + 显式标 unverified" |
| P19 | 3 | 端点失效 → 缓存模式兜底 |
| P20 | 5 | 固件编译不通 → 只出 pins.h，不生成驱动 |
| P21 | 4 | 校准无法复现旧失败 → 不做校准，只记实测 |
| P22 | 4 | 假模型端到端不通 → 回退"AI 只出 interop" |
| P23 | 4 | — |

**合计 41 天**。M4（P15–P18，21 天）是分水岭：**做完就能"一个设计，电气+机械都能量化判定，并出 lceda 工程"**。

---

## 诚实边界（做完也不承诺）

* **不做气动**：无人机推力曲线/地面效应/桨间干扰 → `unverifiable`
* **不做 FEA**：结构强度/振动/疲劳 → `unverifiable`
* **不做多层高密度布线的兜底**：lceda `autoRouting` 失败即如实报，不自研第二套
* **不做"一句话出整机"**：确定性枚举 + agent 辅助，都要过判定
* **catalog 级元件不进硬选型**：直到人核对手册并 promote
* **实测闭环需要你动手**：搭、测、录视频；工具只负责结构、校验、归档

---

## 与既有文档的关系

| 文档 | 关系 |
|---|---|
| `execution-plan.md` | P0–P8 已交付，**不动**；本文是 P9 之后的完整替代（旧 P9–P14 被 P15–P23 取代） |
| `architecture-v3-unified.md` | 上位架构，本文是它的执行展开 |
| `architecture-v2-lceda.md` | §2–§4（发射层）被 P17 吸收；§1 已标取代 |
| `adoption-plan.md` / `adoption-detail.md` | 被本文吸收；LCSC/grounding/评分/固件各节对应 P19/P20/P22 |
| `AGENTS.md` | 需追加：MuJoCo XML 生成踩坑、lceda CLI 实测记录、闭环校准纪律 |