# 2D / 3D 视图优化方案（v2：决策已定，执行中）

> **v1**（`c3d2fc0`）是纯分析：盘点 + 三层架构 + 分期 + 三个待定决策。
> **v2**（本文件）合并 2026-11-24 的三项决策，补齐可行性/成本/边界，并按顺序执行。
>
> 纪律：每个数字标出处；没出处的写"待定"；**成本是人日估算，不是实测**；
> 执行到哪一步就写在哪一步，没跑的写"未执行"，不写成"已完成"。

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

## 1. 现状盘点（v1 实测，保留）

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
**导出 SVG/PNG**、**1 脚/极性方向**、**外壳开孔投影**。丝印缺失根因同上。

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
3. **`enclosure` 扩字段**：`outer_mm / floor_z_mm / board_origin_mm /
   openings[{face,x,y,w,h}] / lid / has_screws`；拼板时按 `panel.size_mm`
   而不是主板，否则多板工程外壳只盖一半。

### 2.2 投影层：`scene3d` 升 v2（只加字段，不改旧字段）

新增 `bodies[]`、`kinematics[]`（由 joints 精简）、`parts[].footprint`、
`enclosure.outer_mm/openings/lid`、`silk`（阻塞，见 §7）。带 `schema: 2`，
旧客户端仍能渲染旧字段。

### 2.3 渲染层：按"每块钱的观感收益"排序

| 改动 | 收益 | 备注 |
|---|---|---|
| 修双画 / 爆炸 / 剖面 / 外壳偏移 | 立刻不"脏" | 纯 bug（V0） |
| **丝印纹理**（silk → `CanvasTexture` 贴板面） | +60% | 依赖 §5.3，**当前阻塞** |
| **环境光 + 阴影**（`RoomEnvironment` + PMREM、`castShadow`、地面接影） | +30% | 现在三盏平行光无阴影，物体像贴纸 |
| **程序化封装形状库**（0603/0805/SOT-23/SOIC/QFN/电解圆柱/排针/Type-C） | +25% | 按 `package` 映射，几 KB |
| **bodies 形状词汇**（圆柱=电机、盘=桨、盒=机臂/电池、prism=外壳腔） | 从无到有 | V1 落地 |
| 外壳真几何（内腔 `Shape.holes` + 开口 + 盖 + 壁厚） | 从"透明盒"到"能看结构" | V2 落地 |
| 交互：正交 / 预设视角 / 双击聚焦 / 悬停气泡 / 隔离 / 量尺 / 截图 / GLB 导出 | 可用性 | V4 |
| 运动演示：`joints.dof=["rz"]` → 父子层级 + 角度滑杆，极限取 `mechanical.limits`，超限变红但**只演示不判定** | 差异化 | V4 |
| 性能：`InstancedMesh` 合批、edges 合并、焊盘 instancing | 500 → ~10 draw call | 43 件不急，200 件以上必须 |

2D 同期补：外壳开孔投影（V2）、层控、网络高亮、DRC 叠加、拼板 V-cut、量尺、SVG 导出。

---

## 3. 分期（每期带验收闸门）

| 期 | 内容 | 估算 | 新闸门 |
|---|---|---|---|
| **V0** ✅ | 修双画走线、爆炸按 `side/高度档`、剖面改滑杆（**无 cap**，cap 需 stencil，留到 V2） | 1 天 | `check_assembly3d.mjs` 增"同一网只有一条铜线""剖面移动后高矮件都可见" |
| **V1** ✅ | `bodies_list` 表 + `Enclosure` 扩字段 + `scene3d` v2 + 跨表引用完整性 + 契约类型 | 2–3 天 | 新 `check_bodies.py`：缺 `source` 拒收、悬空 joint 报 error、装配树无环 |
| **V2** | 外壳真几何 + 2D 开孔投影 + 丝印纹理 + 环境光 + 阴影 + 封装形状库 | 3–5 天 | "无丝印数据时不出假丝印"；"`estimated` 件必须虚线"；帧时间上限 |
| **V3** | 电机 / 桨 / 机臂 / 电池目录并接进 3D | 2–3 天 | "无尺寸的桨不画桨叶形状"（像素断言） |
| **V4** | 交互（正交 / 预设 / 量尺 / 隔离 / 导出）+ 关节滑杆演示 | 2–3 天 | 量尺读数与 Python mm 一致（同源断言） |

**最小可见收益**：V0 + V1 + V2 的外壳与光照，约 5–7 天，从"多块板 + 方块"
变成"能认出一台无人机、外壳是真的、电机/桨有尺寸来源"。

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
* **不做 PDF 表格自动抽取**（2026-11-24 决策）：厂家尺寸画在装配图上，不在文本层；
  自动化天花板只是"给你候选链接，仍要人读图"。
* **不改旧载荷字段**：`scene3d` v2 只加字段。

---

## 5. 三项决策：可行性、成本、落地边界（2026-11-24 已定）

### 5.1 电机 / 桨尺寸来源 → **做目录，只收常见型号，不确定的留空**

**可行性**：LCSC/JLC 那条路**口径不对**——`parts_catalog.py` 的两个入口
（JLC `getComponentDetail` 99 字段、LCSC `product/detail`）都是**贴片元件库**，
电机/桨/电池不在其中；商城详情通常只有型号与 PDF 链接。真值在**厂家机械图**
（外径、含轴高、机座孔距、轴径、质量）。

**现成地基**：`parts_catalog.py` 已有缓存（TTL 7 天）、离线模式
（`HWV_PARTS_OFFLINE`）、防御式解析、信任级 `catalog`、以及
`parts-promote --datasheet <url>` 的人工升级出口。新目录复用同一套纪律。

**规模**（"不用太多"）：8 条——电机 2207/2306/2806/1404、桨 5.1"/7"/3.5"、
电池 4S 1500（第 9 条 4S 850 可选）。每条 6–9 个数字 + `evidence` URL + 页码。

**成本**：schema+loader+CLI 1 天（~250–300 行）；数据 8 条 ≈1 天（读图真的慢）；
闸门 0.5 天 ⇒ **2.5–3 人日**。

**边界**：不确定字段写 `null` 并标 `source: catalog`/`estimated`，3D 画线框
占位框（不是实心）。**不猜 KV/轴长/螺距变体**。

**执行时的实测**（2026-11-25）：tmotorhobby 商品页只返回导航文本、
getfpv / robu.in 返回 403、ligpower 正文截断、iflight.cn 连接失败。
所以目录里 4 个电机 + 1 个电池的尺寸**全空**（`source=unknown` + `need`），
3 条桨只填了型号换算出的直径（`source=derived`）。这不是偷懒：
可访问的页面没有给出可引用的尺寸表。

### 5.2 外壳形态 → **参数化腔体（内腔 + 壁厚 + 开口），2D 也投影**

**可行性最高**：`Enclosure` 已存在，且**已在判定链里**——
`physical.py:59` 把内腔 `d` 当限高用（`space.height`），超限给
`lower_or_raise_enclosure` 的下一步；`openings` 是唯一"能写但没人用"的字段。

**要加**：`outer_mm`（派生，不存冗余）、`floor_z_mm`、`board_origin_mm`、
`lid{thickness, lip_mm, has_screws}`、`openings[{id, face, x_mm, y_mm, w_mm,
h_mm, kind}]`；拼板按 `panel.size_mm`。

**校验三条**（三态）：开口越界 = fail；内腔装不下拼板 + 间隙 = fail；
最高件 > 内腔高 − 盖厚 = fail（复用 `space.height`）。**缺数据 = unknown，不是 pass。**

**成本**：模型+校验 0.5 / 投影 0.25 / 3D 1 / 2D 0.25 / 闸门 0.5 ⇒ **2–2.5 人日**。

**边界**：只表达"方盒 + 圆角 + 矩形/圆开口"；不做拔模/卡扣/异形。开口位置手填；
"从接口自动提案开口"可以，但必须 `proposed` 且人确认。

### 5.3 丝印来源 → **扩导入器抓丝印线，但当前阻塞**

**可行性最高，但不是"已经能做"**：丝印与板框、铜线在**同一份 `.epro2`** 里
（`easyeda.py::parse()` 已遍历全部 records，`LINE` 解析了 1119 条铜线、
`POLY` 解析了板框），所以丝印只是**被丢弃的记录**，**不需要启动立创、不需要 bridge**
（对比发射链路的 `invoke` ≤30 s / `getByLcscIds` 异步 flaky，稳定性完全不同）。

**两个未测点（不能猜）**：

1. **层号表**：仓库没有 silk 层号（只见 1=顶层 / 2=底层 / 12=多层）。
   必须拿一个真实工程做 `layerId` 直方图。猜错就是"丝印画到阻焊层上"。
2. **`TEXT` 记录**：全仓零解析。丝印文字要取字符/高度/旋转，canvas 画出来
   **字体是近似的**，不可能逐字一致——这一条必须写进 UI 说明。

**体积**：100×80 板、200 件 ⇒ 丝印约 2k–5k 段。塞 `design.json` 会让主文件涨
1–2 MB，而它是每次读写都要过校验的文件。⇒ **独立产物 `silk.json`**
（与 `diff.json`/`baseline.json` 同构），`design.json` 只存"有/无 + 统计"。

**成本**：探针 0.5；解析+模型 1；产物+载荷 0.5；2D 0.5；3D 纹理 0.5；闸门 0.5
⇒ **3–4 人日**（回退 bridge 路径 +1 天并继承其不稳定性）。

**阻塞条件**：磁盘上**没有真实 `.epro2`**（`find` 零结果）。没有样本 → 探针做不了
→ 层号表只能靠猜 → 违反 §0.1。**拿到一个真实工程再开工。**

---

## 6. 执行顺序与落地记录

顺序（依赖决定，不是偏好）：

```
V0 修 bug（双画/爆炸/剖面/外壳偏移）
└─ V1 bodies_list + Enclosure 扩字段 + scene3d v2 + 目录骨架
   ├─ V2 外壳真几何（2D 开孔 + 3D 腔体/盖）
   ├─ V3 目录数据（8 条）+ 3D 电机/桨渲染
   └─ V2' 光影（环境光/阴影/封装形状库）
V1 之后可并行：丝印（需样本，阻塞）
```

### 落地记录（本轮）

| 项 | 状态 | 证据 |
|---|---|---|
| 方案 v2 合并（本文件） | ✅ | 本文件 |
| V0 双画走线 / 爆炸分组 / 剖面滑杆 | ✅ 已落地 | `8de6f7b` |
| V1 `bodies_list` + 校验 + `entity` + 目录 + 3D 电机/桨 + 外壳扩字段与投影 | ✅ 已落地 | `4276313` + 补记 |
| 外壳 3D 腔体/盖按新语义定位 | ✅ 已落地 | 补记（见 CHANGES V1） |
| 丝印 | **阻塞**（磁盘上没有真实 `.epro2`） | 需要样本 |
| 光影 / 封装形状库 / 正交/量尺/导出 / 关节演示 | 未执行 | — |

### 6.1 V0

* `Assembly3D.tsx`：删掉重复的圆管铜线（保留贴板细盒）；爆炸改按
  `side`（顶层 +/底层 −）与高度秩；剖面改可移动（滑杆 → `store.sectionAt`）；
  外壳吃 `panel` 偏移与 `openings`。
* 闸门：`check_assembly3d.mjs` 增两条（单网单线、剖面移动后高矮件都可见）。

### 6.2 V1

* `design/model.py`：`Body` dataclass（`source` 必填 ∈ `SOURCE_ORDER`、
  `shape.type` 白名单、尺寸非负、`ref` 不与 `Part` 冲突）+ `bodies_list`
  的 `to_dict/from_dict` + `validate()` 追加跨表引用（joints 两端可在
  `Parts ∪ Bodies`）+ 装配树无环。
* `design/entity.py` / `design/mech_check.py` / `design/joints.py`：
  实体解析改走 `design.entity_by_ref()`。
* `design_cli.py`：`scene3d` 增加 `bodies[]`、`enclosure` 扩字段，`schema: 2`。
* `src/shared/types.ts` + `Assembly3D.tsx` + `Circuit2D.tsx`：消费新字段；
  `estimated/proposed` 画虚线。

### 6.3 V2（外壳）

* `Enclosure`：`outer_mm` 派生、`lid`、`openings[]` 校验；
  新 `design/shell.py` 出三态结论（`shell.openings` / `shell.inner` /
  `shell.height`），失败进 `checks.json` 的 findings。
* 2D：外框实线 + 内腔虚线 + 开口矩形（带 kind 标签）。
* 3D：腔体 + 盖（**已做**：腔底 z=−(地板+板厚)、腔顶 z=inner.d、x 从 −offset 起）。
  **开口挖孔**（`Shape.holes` / 分段墙板）与**螺柱**（从 `mount_holes` 推、标
  `proposed`）**未做**——2D 已按真实矩形投影，3D 先只画腔体与盖，不让人以为孔开好了。

### 6.4 V3（目录）

* `design/bodies_catalog.json`：8 条，每条 `source` + `evidence`；
  缺失字段 `null`。
* `design/bodies.py`：`lookup(ref/model)`、`install(design, model)`（把目录条目
  写进 `bodies_list` 并保留 source/evidence）、`audit`（缺字段清单）。
* CLI：`bodies list/show/install`。

---

## 7. 未决与阻塞

1. **丝印**：需要**一个真实 `.epro2`**（任意工程）。拿到后 0.5 天探针
   （`layerId` 直方图 + `TEXT` 抽样）→ 再决定 3 天实现。
2. **电机型号**：目录先收 2207 / 2306 / 2806 / 1404；你若实际用 2806 长航时，
   请点名优先哪个（不点名就按这 4 条，后续可加）。
3. **开口位置**：外壳开孔位置必须手填（USB/摄像头/天线各板不同）；
   "自动提案"只能标 `proposed`。
4. **封装形状库 / 光影 / 交互**：未排期，属 V2' 与 V4。

---

## 8. 验收命令

```bash
python -m hw_velxio.design_cli scene3d <project>     # 看 bodies/enclosure 投影
python -m hw_velxio.design_cli snapshot <project>    # 2D/3D 全量载荷
python scripts/check_scene3d.py                      # 投影契约
python scripts/check_bodies.py                       # 目录 + 实体引用完整性
node scripts/check_assembly3d.mjs                    # 3D 实渲染像素断言
npm test                                             # 单元测试
```