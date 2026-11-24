# 2D / 3D 视图优化方案（分析，v1）

> 状态：**方案**，未执行。依据是读 `Assembly3D.tsx`（538 行）、`Circuit2D.tsx`（626 行）、
> `design/projection.py`、`design_cli.py::scene3d`、`design/model.py` 后的实测盘点。
> 每个数字都标了出处；没有出处的判断写"待定"。

---

## 0. 结论

**问题不在渲染库，在模型里没有"整机"这一层。**

3D 现在投影的是 PCB 域：每颗元件按 `size_mm` 画成一个盒子
（`Assembly3D.tsx` L351），板和焊盘之外没有第二种几何。电机 / 螺旋桨 / 机臂 /
外壳在 `design.json` 里**没有实体**——`enclosure` 只有 `inner_mm / wall_mm /
openings[]` 三个字段（`model.py` L725–745），`openings` 连渲染层都没读。

嘉立创 EDA 更强，是因为三样东西：**元件 3D 模型库、丝印纹理、环境光照**。
它做的仍是"PCB 的高保真渲染"；整机装配（电机/桨/机架）不在它范围内。
所以正确姿势是：**它的强项借三个杠杆，它没有的地方做我们的差异**。

---

## 1. 现状盘点（实测）

### 1.1 数据层 `design/model.py`

| 事实 | 出处 |
|---|---|
| `Part` 有 `size_mm{w,h,d}` / `mass_g` / `com_mm` / `inertia` / `footprint` | L286–446 |
| `Placement.mount ∈ ("board","panel","air")`（存在"离板"挂载位） | L69 |
| `rotation_z ∈ {0,90,180,270}`，AABB 精确，但**方向 / 1 脚不表达** | L94 |
| `DesignModel` 有 `boards/parts/nets/wires/units_list/links_list/blocks_list/joints_list/mechanical/program/sim_params/physical_tests/enclosure` | L941–1010 |
| **没有** 电机 / 桨 / 机臂 / 机架 / 外壳实体 | — |
| **全仓没有丝印字段**（`grep -rn silk design/` 零命中），导入器不解析 | `design/importers.py` |
| `Joint(a,b,kind,params,dof,source)` 已存在，`joints_list` 会随 `design.to_dict()` 发出 | L780–867、L1328 |

⇒ **机械关系有，机械实体没有**：能算扭矩/行程/质心，但没有"电机长什么样"。

### 1.2 3D 载荷 `design_cli.py::scene3d`（L62–175）

送出 `panel/board/boards/parts(ref,bbox,size,rotation,source,mount,side,pads)/
wires/ratlines/mount_holes/enclosure`。

没送：`joints`、`footprint`、`package`、`lcsc`、模型引用、丝印、过孔、板色。
⇒ 渲染层**拿不到"这是 0805 还是 QFN"**，只能一律画盒子。这是设计使然。

### 1.3 3D 渲染 `src/client/Assembly3D.tsx`

| 事实 | 位置 |
|---|---|
| 每件 = `BoxGeometry(w,h,d)` + Edges + 每焊盘一个小盒 | L351 / L374 / L386 |
| **铜线画了两遍**：细长盒子一遍（L298–320），CatmullRom 圆管一遍（L399–408） | L298 / L399 |
| 爆炸 = `(index % 4 + 1) * 3.5`，按**数组下标**分组；底层件 z 为负，方向也反 | L360 |
| "剖面"是**固定**裁剪面 `y < thickness+1`，不可移动、无 cap | L465 |
| 外壳 = 一个 `opacity 0.08` 盒子 + 边框；`openings` 未画；位置假定板在原点（拼板偏移忽略） | L427 |
| **没有** shadowMap / 环境贴图 / PMREM / 正交相机 / 文字贴图（grep 零命中） | 全文件 |
| 调色板只有 `board3d/pad3d/trace/wire3d/shell3d/hole3d`；无阻焊/丝印/金属度 | `theme.ts` L79 |
| 仓库无任何 `.glb/.gltf/.stl/.obj/.step`（find 零结果） | — |

量级：43 件的板 ≈ 43 盒 + 43 边框 + 约 200 焊盘盒 + 走线（双画）≈ **500 draw call**。

### 1.4 2D 渲染 `src/client/Circuit2D.tsx`

已有：1mm 网格、板轮廓、安装孔、铜线、飞线（按网色虚线）、元件矩形、真实焊盘形状
（RECT/OVAL/ELLIPSE/CIRCLE）、参考号、底层半透明、`?` 估算标、缩放/自适应/拖拽。

缺：**层控**（顶层/底层铜、丝印、阻焊、courtyard、装配图）、**网络语义**
（点焊盘→高亮整条网/网名/长度）、**DRC 叠加**、**拼板 V-cut/tab**、**量尺**、
**导出 SVG/PNG**、**1 脚/极性方向**。丝印缺失根因同上。

---

## 2. 目标架构：三层各补各的

### 2.1 事实层：`design.json` 加 `bodies_list`

**不进 `parts`**，单列一张表——理由与 `joints_list` 单列完全相同：PCB 判定 /
网表 / 发射 / 回读 / 拼板不该看见电机；若塞进 `parts`（`mount="air"`），会直接
污染 `bbox` 重叠检查、`emit` 器件数、`artifacts` parity 对账。

```json
bodies_list: [{
  "ref": "M1",                       // 与 joints 的 a/b 同一命名空间
  "kind": "motor|propeller|arm|frame|shell|lid|battery|gimbal|generic",
  "parent": "F1",                    // 装配树（爆炸/运动/遮挡按它）
  "mount": "board|frame|air",
  "transform": {"x_mm":0,"y_mm":0,"z_mm":0,"rx":0,"ry":0,"rz":0},
  "shape": {"type":"box|cylinder|disc|prism|mesh",
            "w":0,"h":0,"d":0,"diameter_mm":0,"thickness_mm":0,
            "mesh":"assets/props/5in.glb"},
  "mass_g": 32.0, "com_mm": [0,0,16.0],
  "source": "datasheet",             // 必填，取 SOURCE_ORDER
  "evidence": "2207 datasheet p.3"
}]
```

三条硬规矩：

1. **`source` 逐实体必填**；`estimated/proposed` 在 3D 里画**虚线框 + `?`**
   （沿用 2D 既有约定），不得画成实心圆柱。没有尺寸 → `unknown` 占位框。
2. **引用完整性跨两表**：`Joint.a/b` 现指向 `Part`；加 `bodies` 后须
   `entity_by_ref()` 两表解析，V0 把"joint 指向不存在的 body"判 error。
3. **`enclosure` 扩字段**：`floor_z_mm / board_origin_mm / openings[{face,x,y,w,h}] /
   has_lid / material`；拼板时按 `panel.size_mm` 而不是主板，否则多板工程外壳只盖一半。

### 2.2 投影层：`scene3d` 升 v2（只加字段，不改旧字段）

新增 `bodies[]`、`kinematics[]`（由 joints 精简）、`parts[].footprint/package/height_source`、
`enclosure.openings/lid`、`board.mask_color`；可选 `silk[]`、`vias[]`。带 `schema: 2`，
旧客户端仍能渲染旧字段。

### 2.3 渲染层：按"每块钱的观感收益"排序

| 改动 | 收益 | 备注 |
|---|---|---|
| 修双画 / 爆炸 / 剖面 / 外壳偏移 | 立刻不"脏" | 纯 bug |
| **丝印纹理**（ref/轮廓/极性画到 canvas → `CanvasTexture` 贴板面） | +60% | 不需要 3D 模型库；无丝印数据时如实空着 |
| **环境光 + 阴影**（`RoomEnvironment` + PMREM、`castShadow`、地面接影） | +30% | 现在三盏平行光无阴影，物体像贴纸 |
| **程序化封装形状库**（0603/0805/SOT-23/SOIC/QFN/电解圆柱/排针/Type-C） | +25% | 按 `package` 映射，几 KB |
| **bodies 形状词汇**（圆柱=电机、盘=桨、盒=机臂/电池、prism=外壳腔） | 从无到有 | 依赖 2.1 |
| 外壳真几何（内腔 `Shape.holes` + 开口 + 盖 + 壁厚） | 从"透明盒"到"能看结构" | 开口数据一直没被画 |
| 交互：正交 / 预设视角 / 双击聚焦 / 悬停气泡 / 隔离 / 量尺 / 截图 / GLB 导出 | 可用性 | 量尺与 2D 共用 mm 唯一来源 |
| 运动演示：`joints.dof=["rz"]` → 父子层级 + 角度滑杆，极限取 `mechanical.limits`，超限变红但**只演示不判定** | 差异化 | 数据已具备；MJCF 回放做进阶 |
| 性能：`InstancedMesh` 合批、edges 合并、焊盘 instancing | 500 → ~10 draw call | 43 件不急，200 件以上必须 |

2D 同期补：层控、网络高亮、DRC 叠加、拼板 V-cut、量尺、SVG 导出。

---

## 3. 分期（每期带验收闸门）

| 期 | 内容 | 量级 | 新闸门 |
|---|---|---|---|
| **V0** | 修双画走线、爆炸按 `side/装配树`、剖面改滑杆 + cap、外壳吃拼板偏移与 `openings` | 1 天 | `check_assembly3d.mjs` 增"同一网只有一条铜线""剖面移动后高矮件都可见" |
| **V1** | `bodies_list` 表 + `Enclosure` 扩字段 + `scene3d` v2 + V0 跨表引用完整性 + 契约类型 | 2–3 天 | 新 `check_bodies.py`：缺 `source` 拒收、悬空 joint 报 error、装配树无环 |
| **V2** | 丝印纹理、环境光 + 阴影、封装形状库、instancing | 3–5 天 | "无丝印数据时不出假丝印"；"`estimated` 件必须虚线"；帧时间上限 |
| **V3** | 电机 / 桨 / 机臂 / 外壳腔体 / 盖 + 关节滑杆演示 | 3–5 天 | "无尺寸的桨不画桨叶形状"（像素断言）+ "关节超限变红但 verdict 不变" |
| **V4** | 交互（正交 / 预设 / 量尺 / 隔离 / 导出） | 2–3 天 | 量尺读数与 Python mm 一致（同源断言） |

**最小可见收益**：V0 + V1（只做 motor / propeller 两种 body）+ V2 的丝印与光照，
2–3 天即可从"多块板 + 方块"变成"能认出一台无人机"。

**纯渲染支线**（不动 `design.json`）：V0 + 封装形状库 + 光照阴影。电机/桨/外壳
**不可能**从这条线出来——它们必须先有实体。

---

## 4. 不做什么

* **不追 JLC 的元件模型库**：资源体积、许可、每颗件的 STEP 来源都是坑；
  程序化封装库覆盖 80% 观感。
* **不编尺寸**：电机直径、桨径（5.1" = 129.5 mm）、机臂长度必须来自
  datasheet / 实测，写 `source` 并留证据。渲染层不参与判定，但**画得像会让人
  以为它是真的**——`AGENTS.md` §0.1/§0.2 同样适用。
* **不在 TS 里做几何推导**：mm、开口位置、桨径一律由 Python 投影，TS 只做变换
  与材质（判定在 Python 的唯一来源纪律）。
* **不引 OpenCascade.js** 做 STEP 预览（体积/启动成本远超收益）；要 STEP 就离线
  转成 `.glb` 资产。
* **不改旧载荷字段**：`scene3d` v2 只加字段。

---

## 5. 待定的三个决策

1. **电机 / 桨的尺寸来源**：`bodies_list` 手填（最快最诚实），还是从 LCSC /
   厂家 datasheet 收一批常见型号做目录？
2. **外壳形态**：参数化腔体（内腔 + 壁厚 + 开口，2D 也能投影），还是导入 STEP
   （外形真，但不能参与判定、也不进 2D）？
3. **丝印来源**：是否扩导入器去立创抓丝印线（"丝印纹理"就从"依赖数据"变成"真能画"）。