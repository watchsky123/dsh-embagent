# 架构 v3：多域设计图 + 分域仿真 + 统一判定

> **状态：架构综合方案，未执行。** 整合三方实测事实：
>
> 1. `C:\Users\<user>\Desktop\RoboBuilder-main`（MIT，540 文件，47MB）
> 2. `workspace\vendor\traceforge`（MIT，v1.0.1）
> 3. 立创EDA专业版 4.1.97 的 CLI（本机实测可跑，沙箱不拦）
>
> 取代 `architecture-v2-lceda.md` 中与本文冲突的部分（§7 列明）。
>
> **执行展开见 `execution-plan-v3.md`**（P15–P23，含文件/签名/骨架/验收/止损）。

---

## 0. 核心洞察：两者是同一个结构，只是"关系"的域不同

我把两边的设计数据模型并排比对后，发现它们是**同构的**：

| | RoboBuilder `Design` | dsh_embagent `DesignModel` |
|---|---|---|
| 节点 | `DesignPart(name, part, pos_mm, yaw, spin)` | `Part(ref, kind, placement{x,y,z,rot}, pins, pads)` |
| 关系 1 | `DesignConnection(child, parent, kind, studs)` | `nets[{net, pins[{ref,pin}]}]` |
| 关系 2 | `DesignGearMesh` / `chains` / `treads` / `racks` / `cv_joints` / `differentials` | `links_list[{a,b,protocol,source}]` |
| 关系 3 | `ports: {port_letter → motor/sensor}` ← **极简电气** | `units_list`（功能单元分组） |
| 装配基准 | `chassis: str` | `boards[]` + `Placement.board_id` |
| 时序/程序 | `motor_program: [{t, motor, speed}]` | （无——这是我们的缺口） |
| 质量属性 | `bom()` + 每件 `mass_measured_g`/`com_mm` | `Part.electrical` + `size_mm`（无质量/惯量） |

**结论：两者都是「零件 + 带类型的关系」的图，只是关系落在不同的物理域。**

- 它的边：`stud`（凸柱）/ `pin`（销）/ `axle`（轴穿孔）/ `gear_mesh`（齿轮啮合）/ `chain` / `tread` / `rack` / `cv` / `diff` / `port`（电气）
- 我们的边：`net`（电气节点）/ `link`（板间协议）/ `board`（同板归属）

**所以最终形态不是"把 RoboBuilder 塞进 dsh_embagent"，而是：把两边的设计统一成一个多域图，仿真按域分派到不同后端，判定协议统一。**

---

## 1. 最终形态：五层

```
┌─ L0 任务层 ────────────────────────────────────────┐
│  Task = {目标, 障碍/场景, 约束, 预算}                  │
│  学 RoboBuilder：course + level + 仿真预算 B 次        │
│  我们增：需求数值（指向精度/带宽/延迟）+ 接口清单       │
└──────────────┬─────────────────────────────────────┘
               ▼
┌─ L1 设计图（唯一事实源）─────────────────────────────┐
│  design.json = 多域图                                 │
│    nodes: parts（位姿 SE(3) + 质量属性 + 电气参数）     │
│    edges: 按 kind 分域                                │
│      electrical  网表（已有）                          │
│      mechanical  凸柱/销/轴/齿轮/链条/履带/关节（新）   │
│      link        板间协议（已有）                       │
│      stack       叠放关系（新，v2 已规划）              │
│      port        控制器引脚→执行器/传感器（学它）       │
│  program: 时序动作（新）                               │
└──────┬───────────────────┬──────────────────┬────────┘
       │ 电气域             │ 机械域            │ 制造域
       ▼                   ▼                  ▼
┌─ L2 仿真（分域后端）──────────────────────────────────┐
│  electrical : 我们自己的 V0–V6（已交付）                │
│  kinematic  : MuJoCo 3.12（RoboBuilder 同款）          │
│  emc/thermal: 无模型 → 标 unverifiable（诚实）         │
│  manufacture: 立创EDA DRC（外部证据）                   │
└──────────────┬───────────────────────────────────────┘
               ▼
┌─ L3 判定（统一协议）────────────────────────────────┐
│  三态：pass / fail / unknown（缺失≠通过）              │
│  鲁棒性：三次启动（标称 / 偏 1mm / 晚 50ms）← 学它      │
│  截断：硬失败停下游（V0/V1，已有）                      │
│  预算：仿真调用次数上限（学它的成本约束）                │
└──────────────┬───────────────────────────────────────┘
               ▼
┌─ L4 产物 ──────────────────────────────────────────┐
│  电气：.eprj3（原理图/PCB）+ Gerber/STEP/3D 渲染       │
│  机械：MuJoCo 轨迹视频 + 帧接触表（学它 contact_sheet） │
│  报告：handoff.md 六类（含 external 实测证据）          │
└──────────────┬───────────────────────────────────────┘
               ▼
┌─ L5 反馈（闭环）──────────────────────────────────┐
│  真实测试回写 meta.physical_tests[]                    │
│  仿真器校准：initial.json → learned.json（学它的机制）   │
│  agent 看失败视频 → 重设计 → 再验                      │
└─────────────────────────────────────────────────────┘
```

---

## 2. L1 的关键改造：从"电气模型"到"多域图"

### 2.1 数据模型增量（全部可选字段，不升 schema）

沿用 `execution-plan.md` §2.1 的纪律：**只加可选字段**，`to_dict` 条件写出。

```python
# model.py 新增（都在既有可选字段模式内）

@dataclass
class Joint:                        # 机械关系（学 DesignConnection）
    a: str                          # part ref
    b: str
    kind: str                       # stud | pin | axle | gear | chain | tread |
                                    # rack | cv | diff | hinge | slide
    params: dict[str, Any] = {}     # meshing ratio / axle diameter / DOF limits
    dof: list[str] = []             # ["rx","ry","rz"] 之类，MuJoCo 直接用
    source: str = "declared"        # declared | inferred | imported

@dataclass
class Program:                      # 时序（学 motor_program）
    steps: list[dict]               # [{t_s, actuator, value}]
    loop: bool = False

# Part 新增
mass_g: float | None = None         # 质量（LCSC/datasheet/实测）
com_mm: list[float] | None = None   # 质心
inertia: list[float] | None = None  # 转动惯量（6 分量）
joints: list[Joint] = []            # 它参与的关系（也可放 design 级）

# DesignModel 新增
joints_list: list[Joint] = []
program: Program | None = None
physical_tests: list[dict] = []     # L5：真实测试记录
sim_params: dict = {}               # 仿真器参数（对应它的 Params）
```

**为什么放 `sim_params`**：RoboBuilder 的 136 个参数里 135 个被校准改变，
说明"仿真器参数"是**一级设计数据**，不是代码常量。我们要把
`initial → learned` 这套成对机制搬过来。

### 2.2 关系的推断（我们的强项，不做成纯手填）

RoboBuilder 要求 agent **显式声明**每个连接（`DesignConnection`）。
我们比它多一样东西：**已有的网表**。所以机械关系可以**推断一部分**：

| 关系 | 我们能否推断 | 依据 |
|---|---|---|
| `stud`/`pin`（接触式连接） | ⚠️ 需声明 | 网表不含机械 |
| `axle`（轴穿过孔） | ⚠️ 需声明 | 同上 |
| `gear` | ⚠️ 需声明 | 齿数比要参数 |
| `hinge`/`slide`（运动副） | ✅ **可从几何推断** | 两件相邻 + 引脚类型 |
| `stack`（叠放） | ✅ 可从 z 推断 | `Placement.z_mm` 与板厚 |
| `port`（控制器→执行器） | ✅ **可从网表推断** | 网表 + 引脚类型 |

**纪律**：推断的关系标 `source="inferred"`，**不参与硬检查**（AGENTS §1.2）。
这与 RoboBuilder 不同——它全部显式声明，因为它的 agent 必须写出来。
我们允许推断，但推断只提示。

---

## 3. L2 的关键改造：MuJoCo 作为一个"域后端"

### 3.1 为什么是 MuJoCo（RoboBuilder 的实际选择）

`pyproject.toml` 实测依赖：
```
mujoco==3.12.0   numpy  scipy  trimesh  rtree  manifold3d  cadquery
```
`sim/csim.py` 2937 行，`import mujoco`，`class Params`（136 项）。

**它用 MuJoCo 而不是自己写物理，这是重要信号**：MuJoCo 现成支持
刚体、关节、接触、执行器，且**支持多旋翼模型**。

### 3.2 分域分派

```python
# design/sim_dispatch.py（新，~150 行）
DOMAINS = {
    "electrical": run_electrical,    # 已有 electrical.run
    "geometry":   run_physical,      # 已有 physical.run_checks
    "kinematic":  run_mujoco,         # 新增（搬 csim 的参数封装模式）
    "manufacture": run_lceda_drc,     # 新增（外部证据）
}
# 没有后端 → 该域返回 unknown，不是 pass（我们既有纪律）
```

**关键：MuJoCo 只回答机械域问题**。它不回答网表、不回答电气、
不回答 PCB。**判定结论永远来自统一协议（L3），不只来自某个引擎。**

### 3.3 无人机的具体适配（回答上一轮的疑问）

| 无人机要仿的 | MuJoCo 能做 | 需要什么 |
|---|---|---|
| 机架装配干涉 | ✅ 换成碰撞体 | 已有 `pads`/`size_mm` |
| 桨盘干涉、折叠包络 | ✅ 关节扫掠 | `Joint.kind="hinge"` + DOF 限 |
| 质心/转动惯量 | ✅ 计算 | `Part.mass_g` + `com_mm` |
| 悬停配平、姿态响应 | ✅ 刚体+推力 | 桨的 actuator |
| 抗扰鲁棒性 | ✅ 三次启动 | 抄它的判定协议 |
| **推力曲线/气动干扰** | ⚠️ 要自定义力模型 | 桨 $C_T/C_P$，需实测标定 |
| 振动/疲劳 | ❌ | 无 FEA/模态 |

**所以"只仿真不管真机"的目标下，无人机机械仿真可行范围 =
几何 + 惯量 + 配平 + 运动包络 + 抗扰，不含气动细节**——气动的标 `unverifiable`。

---

## 4. L3 的关键改造：判定协议统一（这是最该学的部分）

RoboBuilder 的判定比我们现在的更严格，有三点值得直接搬：

### 4.1 鲁棒性判定：三次启动

```
V(d, o) = 1  当且仅当
  从标称初始条件 → 通过
  从偏移 1mm → 通过
  从延后 50ms → 通过
  且到达后立住 5s
```

**为什么好**：单次通过可能靠运气。三次扰动相当于**鲁棒性下限**。

**我们的适配**：
* 电气域：轨压波动 ±5% / 温度 ±10℃ / 负载阶跃（对应 V3）
* 运动域：初始位姿扰动 / 时序抖动（对应 V8 kinematic）
* 几何域：公差 ±0.1mm（对应 V2）

### 4.2 预算约束：`B` 次仿真调用

RoboBuilder 给 agent 一个**硬仿真预算**（它论文里说"真实测试要几小时，
每次迭代都贵，必须选得好"）。我们现在的 `max_candidates` 只是"候选数上限"，
不是"仿真次数上限"。

**改造**：`generate.py` 的枚举加 `sim_budget`；超出即停，报告用了多少。
这让"生成"和"验证"的成本**显式化**。

### 4.3 判定的自洽性：校准验证

项目页实测的这条最狠：

> **≤2 个参数/次失败，且只有"旧设计在新参数下也按录像那样失败"才保留**

即校准时**不是**"调到新设计能过"，而是"**调到旧失败能被重放**"。

**我们的适配**（`design/sim_calibration.py` 新，~200 行）：

```python
def calibrate(design, real_test, candidates_params) -> dict:
    """用真实测试校准仿真参数。
    验收条件：V(旧设计, 新参数) 必须复现真实失败，否则这组参数不采用。
    """
```

这是**防止"为了通过而作弊"的机制**，与我们"不能自己出题自己批"完全同源。

---

## 5. L5 的关键改造：闭环的两种数据

RoboBuilder 的闭环靠**视频**。我们不可能有 240 小时的实验室，但可以：

### 5.1 `physical_tests[]` 结构（学它 + 加我们的引用纪律）

```json
"physical_tests": [
  {
    "id": "pt-001",
    "subject": "避障小车 v1",
    "test": "爬 15mm 台阶",
    "result": "fail",
    "observed": "右前轮打滑，机架上翘 12°",
    "evidence": {
      "video": "tests/2026-11-20/run01.mp4",
      "frames": ["00:03", "00:07"],
      "contact_sheet": "tests/2026-11-20/sheet.png"
    },
    "implicated": ["wheel_R", "motor_R"],       // 哪些件被牵连（引用纪律）
    "sim_params_before": "initial",
    "sim_reproduced": false                      // 新参数下能否复现
  }
]
```

**关键设计**：`implicated` 必须是**零件引用**（`ref:`），且**逐条可核对**——
沿用 grounding 契约。这样"真实失败"也能进归因（`justify`）和交接单。

### 5.2 仿真器参数成对存

```
sim_params/
  initial.json     # 未校准（对应它的 0/15）
  learned.json     # 校准后（对应它的 73%/93%）
  learned-<日期>.json
```

**为什么成对**：它证明了**未校准的仿真会给出 0 通过率**。
我们要能随时回答"这个结论是在哪版参数下得出的"——这就是产物指纹的思想。

### 5.3 `contact_sheet`：给 agent/人看视频的降维

它有个函数 `contact_sheet(video, out)` —— 把长视频转成缩略图网格。
**这是"agent 看视频"的关键工程细节**：直接喂视频太贵/太长，
接触表只给关键帧。

我们搬这个思路（用 ffmpeg 抽帧拼图），**不搬实现**（它的 `imageio-ffmpeg`）。

---

## 6. 模块清单：搬什么、新建什么

| 层 | 模块 | 来源 | 工作量 |
|---|---|---|---|
| L1 | `model.py` +Joint/Program/mass | 学它的字段设计 | 2 天 |
| L1 | `design/joints.py`（关系推断） | 自研（我们比它多网表） | 3 天 |
| L1 | `design/interop.py`（含机械关系） | 已规划 | 2 天 |
| L2 | `design/sim_dispatch.py` | 自研 | 1.5 天 |
| L2 | `design/mujoco_backend.py` | **搬 `sim/csim.py` 的 Params 封装模式**，几何/关节/力自己写 | 5–6 天 |
| L2 | `design/sim_calibration.py` | 搬它的"重放旧失败"验收集 | 2 天 |
| L2 | `design/emit_lceda.py` | 已规划（v2） | 6–7 天 |
| L3 | `ladder.py` 加 V8 kinematic | 自研（协议学它） | 2 天 |
| L3 | `generate.py` 加 `sim_budget` | 小改 | 0.5 天 |
| L3 | 三次启动扰动 | 搬协议 | 1 天 |
| L4 | `contact_sheet` | 搬思路 | 0.5 天 |
| L5 | `physical_tests` + 回写 | 学它的 loop | 2 天 |
| — | 固件（7 驱动/5 targets） | 搬 Traceforge | 4–5 天（已规划） |
| — | LCSC 数据源 | 搬 Traceforge | 3 天（已规划） |
| — | agent 循环 | 搬 Traceforge + 学它的 agent_docs | 4 天（已规划） |

**新增部分合计 ~28–32 天**（不含已规划的 v2 内容）。

---

## 7. 与既有文档的关系

| 文档 | 状态 |
|---|---|
| `execution-plan.md` M1–M3 | **不变**，已交付 |
| `execution-plan.md` P9–P14 | 修订：P11 改 `emit_lceda`；新增 P15（多域图）/P16（MuJoCo 后端）/P17（闭环回写） |
| `architecture-v2-lceda.md` | **保留** §0–§4（发射层），§1 的总架构被本文替换为五层 |
| `adoption-detail.md` §5（布线器） | 不变（已标降级为可选） |
| `adoption-plan.md` | 不变，标"已并入 v3" |

---

## 8. 一句话：最终形态是什么

**一套"设计→分域仿真→统一判定→产出→实测回写"的流水线，**
**其中：**

- **设计**用我们已有的多域图（电气是强项，机械是新增）
- **仿真**按域分派（电气归 V0–V6，运动归 MuJoCo，制造归立创EDA）
- **判定**只有一套协议（三态 + 三次启动 + 预算 + 截断）
- **产物**出到立创EDA（PCB/3D/Gerber）+ MuJoCo（轨迹视频）
- **闭环**靠 `physical_tests` 回写 + 仿真参数成对校准

**它和 RoboBuilder 的关系**：它证明"多域闭环是可行的"，也证明了
"未校准的仿真会给出 0 通过率"。我们接受这个教训——
**不做"一句出整机"，做"每一步可追问、每个未知都标出来"。**

**它和 Traceforge 的关系**：它做单板生成，我们做**多板 + 多域 + 审查**。

**它和立创EDA 的关系**：它做渲染/布线/制造，我们做决策与判定。