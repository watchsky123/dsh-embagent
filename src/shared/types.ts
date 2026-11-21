/**
 * 宿主与客户端共享的**纯类型**。
 *
 * 这个文件不 import 任何 node 模块，客户端 `import type` 它是安全的：类型在
 * 转译后完全消失，不会把 `node:fs` 带进浏览器 bundle。字段名与 Python
 * ``hw_velxio.design`` 的 JSON 输出一一对应——改一侧必须改另一侧。
 *
 * @module dsh-hw-velxio/shared
 */

export type BackendStatus = 'ok' | 'not_ready' | 'failed'

export type PhysicalSource = 'datasheet' | 'measured' | 'estimated' | 'derived'

export interface Placement {
  x_mm: number
  y_mm: number
  z_mm: number
  rotation_z: 0 | 90 | 180 | 270
  mount: 'board' | 'panel' | 'air'
}

export interface DesignPart {
  ref: string
  kind: string
  label: string
  role: string
  size_mm: { w: number; h: number; d: number }
  physical_source: PhysicalSource
  placement: Placement
  lcsc?: string
  electrical?: Record<string, unknown>
  notes?: string[]
}

export interface BoardDoc {
  kind: string
  name: string
  outline: Array<[number, number]>
  size_mm: { w: number; h: number }
  thickness_mm: number
  mount_holes: Array<{ x_mm: number; y_mm: number; diameter_mm: number; source: string }>
  px_per_mm: number
}

export interface DesignDoc {
  schema: string
  units: 'mm'
  id: string
  title: string
  board: BoardDoc
  parts: DesignPart[]
  nets: Array<{ net: string; pins: Array<{ ref: string; pin: string }> }>
  wires: Array<{ net: string; path_mm: Array<[number, number, number]> }>
  enclosure?: { inner_mm: { w: number; h: number; d: number }; wall_mm: number; openings: unknown[] }
  meta: Record<string, unknown>
  warnings?: string[]
}

export interface ProjectSummary {
  id: string
  path: string
  has_design: boolean
  artifacts: Record<string, boolean>
  title?: string
  board?: string
  board_name?: string
  parts?: number
  nets?: number
  error?: string
}

export interface DesignSummary {
  id: string
  title: string
  board: { kind: string; name: string; size_mm: { w: number; h: number }; thickness_mm: number }
  parts: number
  parts_by_source: Record<string, number>
  nets: number
  wires: number
  enclosure: boolean
  estimated_parts: string[]
}

export interface Projection2D {
  units: 'mm'
  px_per_mm: number
  /** 拼板（多板时的总画布尺寸）。单板时等于那唯一一块板。 */
  panel: {
    size_mm: { w: number; h: number }
    gap_mm: number
    board_count: number
  }
  /** 主参考板（兼容旧字段）。多板时是 `boards[0]`。 */
  board: {
    id: string
    kind: string
    name: string
    w_px: number
    h_px: number
    thickness_mm: number
    px_per_mm: number
    size_mm: { w: number; h: number }
    outline_px: Array<[number, number]>
    /** 拼板内的偏移（px）。单板恒为 0。 */
    x_px?: number
    y_px?: number
    parts?: number
  }
  /** 全部板（单板时长度 1）。 */
  boards: Array<{
    id: string
    kind: string
    name: string
    x_mm: number
    y_mm: number
    w_mm: number
    h_mm: number
    w_px: number
    h_px: number
    x_px: number
    y_px: number
    thickness_mm: number
    size_mm: { w: number; h: number }
    outline_px: Array<[number, number]>
    parts: number
  }>
  parts: Array<{
    ref: string
    kind: string
    label: string
    role: string
    x_px: number
    y_px: number
    w_px: number
    h_px: number
    rotation_z: number
    physical_source: PhysicalSource
    size_mm: { w: number; h: number; d: number }
    z_mm: number
    side: string
    pads: Array<{
      num: string
      x_px: number
      y_px: number
      w_px: number
      h_px: number
      shape: string
      rotation_z: number
      layer: number
    }>
  }>
  wires: Array<{
    net: string
    path_px: Array<[number, number]>
    layer: number
    width_px: number
    routed: boolean
  }>
  /** 飞线：从网表推导，**不是铜线**（`routed` 恒为 false）。 */
  ratlines: Array<{
    net: string
    path_px: Array<[number, number]>
    length_mm: number
    pins: string[]
    routed: boolean
    approximate: boolean
  }>
  mount_holes: Array<{ x_px: number; y_px: number; d_px: number; source: string }>
  estimated_parts: string[]
  warnings: string[]
}

export interface Scene3DPart {
  ref: string
  label: string
  kind: string
  role: string
  bbox_mm: [number, number, number, number, number, number]
  /** 未旋转足迹（渲染层建几何用这个，再按 rotation_z 转一次）。 */
  size_mm: { w: number; h: number; d: number }
  rotation_z: number
  physical_source: PhysicalSource
  mount: string
  /** 安装面：`top` / `bottom` / 空。底层元件的 z 是负的（贴板底面）。 */
  side: string
  /** 焊盘（真实几何，mm 板坐标）。 */
  pads: Array<{
    num: string
    x_mm: number
    y_mm: number
    w_mm: number
    h_mm: number
    shape: string
    rotation_z: number
    layer: number
  }>
}

export interface Scene3D {
  units: 'mm'
  /** 拼板（多板时的总尺寸）。 */
  panel: {
    size_mm: { w: number; h: number }
    gap_mm: number
    board_count: number
  }
  /** 主参考板（兼容旧字段）。 */
  board: {
    kind: string
    id: string
    outline: Array<[number, number]>
    size_mm: { w: number; h: number }
    thickness_mm: number
  }
  /** 全部板：**每块各自挤出**（轮廓已是拼板坐标）。 */
  boards: Array<{
    id: string
    kind: string
    name: string
    x_mm: number
    y_mm: number
    outline: Array<[number, number]>
    size_mm: { w: number; h: number }
    thickness_mm: number
    parts: number
  }>
  parts: Scene3DPart[]
  wires: Array<{
    net: string
    path_mm: Array<[number, number, number]>
    layer: number
    width_mm: number
    routed: boolean
  }>
  /** 飞线：从网表推导，**不是铜线**（`routed` 恒为 false）。 */
  ratlines: Array<{
    net: string
    path_mm: Array<[number, number, number]>
    length_mm: number
    pins: string[]
    routed: boolean
    approximate: boolean
  }>
  mount_holes: Array<{ x_mm: number; y_mm: number; diameter_mm: number; source: string }>
  enclosure: { inner_mm: { w: number; h: number; d: number }; wall_mm: number; openings: unknown[] } | null
}

export interface Finding {
  check: string
  ok: boolean
  severity: 'error' | 'warning' | 'info'
  refs: string[]
  measured: unknown
  limit: unknown
  message: string
  fix: Record<string, unknown>
  /**
   * 可点的下一步（P1）。由 `nextstep.py` 从 `fix.need` 翻出来。
   * **没有 = 这条没有机械动作**，UI 显示 `message`/`action` 即可，不编按钮。
   */
  next?: NextStep
}

/** 一步可执行的动作。四类，UI 只按 `kind` 分支。 */
export interface NextStep {
  kind: 'edit_field' | 'move_part' | 'run_command' | 'external'
  need?: string
  /** 人能读的一句话（已展开 refs） */
  label: string
  /** 定位用：选中这些位号 */
  refs?: readonly string[]
  /** `edit_field` 的设计字段路径，如 `electrical.power_w` */
  field?: string
  /** `run_command` 可直接粘走的整条 CLI */
  command?: string
  /** `external` 由谁做 */
  by?: string
}

export interface ChecksPayload {
  status?: BackendStatus
  ok: boolean | null
  error?: string
  pitch_mm: number
  space?: {
    dims_mm: number[]
    dims_voxels: number[]
    occupied_voxels: number
    placements: number
  }
  findings: Finding[]
  not_verified: string[]
  unsized: string[]
  estimated: string[]
  /**
   * **假绿灯清单**：这些检查"通过"是因为没有可检查的对象（空集），
   * 而不是真的核对过。必须与真通过分开显示——否则 `parts: []` 的工程
   * 看起来像"9 项全部通过"。
   */
  vacuous?: string[]
  summary: {
    total: number
    failed: number
    errors: number
    warnings: number
    refs_with_issues: string[]
  }
}

export interface BudgetPayload {
  status?: BackendStatus
  ok: boolean | null
  error?: string
  confidence?: 'none' | 'partial' | 'full'
  currencies?: string[]
  resolution_order?: string[]
  limits?: Record<string, number>
  by_currency?: Record<string, {
    used: number
    limit: number | null
    ratio: number | null
    over: boolean
    default_limit: boolean
  }>
  violations?: Array<Record<string, unknown>>
  claims?: Array<{ module: string; currency: string; amount: number; ref: string; ratio: number | null }>
  heatmap?: Record<string, Record<string, number>>
  coverage?: { claimed_parts: number; total_parts: number; uncovered: string[]; note: string }
  nodes?: Record<string, { parent: string | null; budget: Record<string, number>; coupling_group: string | null }>
}

export interface DeferredPayload {
  status?: BackendStatus
  error?: string
  deferred?: Array<{ kind: string; ref: string; why: string; at: string }>
  locked?: Array<{ name: string; value: string; at: string }>
  open_irreversible?: string[]
  deliverable?: boolean
  reasons?: Record<string, string>
  message?: string
  kinds?: string[]
  irreversible?: Record<string, string>
  locked_now?: string[]
}

export interface CrosscheckPayload {
  status?: BackendStatus
  ok: boolean | null
  error?: string
  px_per_mm: number
  mismatches: Array<{
    kind: string
    severity: 'error' | 'warning' | 'info'
    refs: string[]
    message: string
    measured: unknown
    expected: unknown
  }>
  not_verified: string[]
  summary: { mismatches: number; errors: number; warnings: number; unverified: number; refs: string[] }
  note: string
}

export interface EvidencePayload {
  status?: BackendStatus
  error?: string
  design_hash: string
  trace: {
    events: number
    chain_ok: boolean
    content_ok?: boolean
    tampered_seqs?: number[]
    head: string
    last: unknown
  }
  gate: { deliverable: boolean; open_irreversible: string[]; reasons: Record<string, string>; message: string }
  checked_at: string
}

export interface SnapshotPayload {
  status: BackendStatus
  version?: string
  findings?: readonly string[]
  project: ProjectSummary
  design: DesignDoc
  summary: DesignSummary
  problems: string[]
  projection2d: Projection2D
  scene3d: Scene3D
  checks: ChecksPayload
  budget: BudgetPayload
  deferred: DeferredPayload
  crosscheck: CrosscheckPayload
  evidence: EvidencePayload
  /**
   * 阶段：**确定性推导**（`design/phase.py`），不是 AI 判断。
   * 空设计的 9 条检查会全部"通过"，`phase=empty` 是唯一能揭穿这件事的信号。
   */
  phase?: PhasePayload
  /**
   * 结论层（`design/verdict.py`）：**首屏**。九层横条 + 六类计数 + 排好序的下一步。
   *
   * 只投影已有产物、不重新判定，所以它是**便宜且可能过期的**：
   * `stale=true` 表示设计改过但阶梯没重跑——那时这屏的结论不作数。
   */
  verdict?: VerdictPayload
  /**
   * 实体索引（P2）：每个位号一行三态摘要。**只放计数与状态**，不放正文——
   * 316 个元件的正文会把快照撑到几 MB，而人一次只看一个元件。
   * 详情走 `entity` 命令按需取。
   */
  entities?: Readonly<Record<string, EntitySummary>>
  /**
   * 参数敏感度摘要（P3）。**只带分类计数与前几个"真的驱动模型"的参数**——
   * 完整 136 行表走 `sensitivity` 命令按需取（打开工程不该跑 136 次 MJCF 构建）。
   */
  params?: ParamsSummaryPayload
  /**
   * 固件矩阵摘要（P4）：引脚 × (硬件配置 / 固件使用 / 结论)。
   *
   * **只带计数与有问题的前几行**——一个 100 脚 MCU 有 90 行 ok，
   * 塞进快照既无用又占宽。完整矩阵走 `fw-matrix` 命令。
   */
  firmware?: FirmwareSummaryPayload
  /**
   * 需求草稿（P5）。草稿 ≠ 需求：`req_draft.json` 模型可改，
   * `req.json` 只有人确认（apply confirm）后才有。
   */
  req?: ReqDraftPayload
}

/** 需求草稿的当前状态。字段与 `generate.Requirement` 一一对应。 */
export interface ReqDraftPayload {
  readonly draft: ReqDraftBody | null
  readonly validation: ReqValidation | null
  readonly applied: boolean
  /** 草稿落在哪一层：`project`（工程目录）/ `workspace`（工程还没有）/ `""`（没有） */
  readonly scope?: string
  readonly project_exists?: boolean
  readonly files?: Readonly<Record<string, string>>
  /** 下一步该做什么（**Python 给的**，UI 不重排） */
  readonly next?: string
  readonly error?: string
}

export interface ReqDraftBody {
  readonly id: string
  readonly title: string
  readonly roles: readonly string[]
  readonly interfaces: readonly Readonly<Record<string, unknown>>[]
  readonly constraints: Readonly<Record<string, unknown>>
  readonly numbers: Readonly<Record<string, unknown>>
  readonly notes: string
}

export interface ReqIssue {
  readonly kind: string
  readonly message: string
  readonly fix?: string
}

/** `req-draft --apply` 的结果（RPC 直传，只用到这几个键）。 */
export interface ReqApplyResult {
  readonly status?: string
  readonly created_project?: boolean
  readonly board?: string
  readonly reason?: string
  readonly findings?: readonly string[]
}

export interface ReqValidation {
  readonly ok: boolean
  readonly problems: readonly ReqIssue[]
  /** 只提示，不拦 apply（如「numbers 没有消费方」） */
  readonly warnings: readonly ReqIssue[]
  readonly counts: Readonly<Record<string, number>>
  readonly basis?: string
}

/**
 * 一个引脚的结论。顺序 = 严重度：
 * `ghost`（硬件没配、固件在用，运行时必炸）> `direction`（方向冲突）
 * > `idle`（配了没用）> `ok`（核对过）。
 */
export interface FirmwareSummaryPayload {
  readonly status?: string
  /** 硬件侧没有可核对配置时的原因（这时 `status` 是 `not_ready`） */
  readonly reason?: string
  readonly counts?: Readonly<Record<string, number>>
  readonly total?: number
  readonly checked?: number
  readonly used_pins?: number
  readonly basis?: string
  /** **只有有问题的行**（ok 行不进快照） */
  readonly rows?: readonly FirmwareRow[]
  readonly rows_total?: number
  /** `.ioc` 里只出现端口或只出现位号、配不成对的次数 */
  readonly unknown?: number
  readonly note?: string
}

export interface FirmwareRow {
  readonly pin: string
  readonly status: 'ghost' | 'direction' | 'idle' | 'ok'
  readonly why?: string
  readonly config?: {
    readonly mode?: string
    readonly signal?: string
    readonly label?: string
    readonly macro?: string
    readonly reserved_debug?: boolean
  }
  readonly firmware?: readonly FirmwareUse[]
}

export interface FirmwareUse {
  readonly api?: string
  readonly file?: string
  readonly line?: number
  readonly text?: string
}

/**
 * 参数分类。四类**各报各的**，因为它们该做的事不同：
 *
 * - `wired`     代码真的读、改它模型会变 → **核这个值**
 * - `wired_noop` 代码读了，但本设计里没有对应机制 → 设计里没这个物理
 * - `unwired`   **没有任何代码读** → 要么补接线，要么从表里拿掉
 * - `missing`   代码读了但表里没这一项 → 参数表缺项
 */
export interface ParamsSummaryPayload {
  readonly total: number
  readonly counts: Readonly<Record<string, number>>
  /** **已排序**（P3 排序在 Python 算），前几个最该核 */
  readonly priority: readonly ParamsPriority[]
  readonly note?: string
  readonly error?: string
}

export interface ParamsPriority {
  readonly key: string
  readonly group: string
  readonly value: number | string | boolean | null
  /** 哪些扰动因子让它变了（`["0.5","2.0"]`） */
  readonly changed: readonly string[]
}

/** 一个元件的紧凑摘要（画布着色 / 列表徽章用）。 */
export interface EntitySummary {
  readonly status: 'pass' | 'fail' | 'unknown' | 'not_ready'
  readonly claims: number
  readonly failed_claims: number
  readonly unknown_claims: number
  readonly findings: number
  readonly tests: number
}

/** 一个元件的完整画像（`entity <id> <ref>`）。**五份产物按 ref 聚在一起。** */
export interface EntityProfile {
  readonly status: 'ok' | 'failed'
  readonly findings?: readonly string[]
  readonly ref: string
  readonly kind: string
  readonly label: string
  readonly role: string
  readonly size_mm: Readonly<Record<string, number>>
  readonly physical_source: string
  readonly placement: {
    readonly x_mm: number
    readonly y_mm: number
    readonly side: string
    readonly board_id: string
  }
  readonly nets: readonly string[]
  readonly claims: {
    readonly total: number
    readonly pass: number
    readonly fail: number
    readonly unknown: number
    readonly items: readonly EntityClaim[]
  }
  /** 与这个件相关的物理/机械检查项（`EntityProfile.findings` 改名，避免与
   * `status: 'failed'` 时的 `findings`（失败原因字符串）撞名）。 */
  readonly checks: readonly EntityFinding[]
  readonly tests: readonly EntityTest[]
  /** 所属单元（没有单元产物时是 null） */
  readonly unit: { readonly id: string; readonly name: string; readonly role: string
    readonly board_id: string } | null
  readonly justification: {
    readonly why: string
    readonly effect_if_removed: string
    readonly confidence: string
    readonly source: string
    readonly category: string
    readonly group: Readonly<Record<string, unknown>>
  }
}

export interface EntityClaim {
  readonly id: string
  readonly type: string
  readonly intent: string
  readonly status: string
  readonly priority: string
  readonly basis: string
  readonly on_fail: string
}

export interface EntityFinding {
  readonly check: string
  readonly ok: boolean | null
  readonly severity: string
  readonly message: string
}

export interface EntityTest {
  readonly id: string
  readonly test: string
  readonly result: string
  readonly observed: string
  readonly sim_reproduced?: boolean
}

/** 项目阶段。`veto` 逐条说明为什么不是更高阶段（UI 直接显示）。 */
export interface PhasePayload {
  readonly phase: 'empty' | 'partial' | 'complete' | 'broken'
  readonly reason: string
  readonly veto: readonly string[]
  readonly counts: {
    readonly parts: number
    readonly nets: number
    readonly unsized: number
    readonly estimated: number
    readonly zero_height: number
  }
}

/** 一屏结论。排序在 Python 侧算（AGENTS §1.1：判定只有一份）。 */
export interface VerdictPayload {
  readonly status: string
  readonly project?: string
  /** 一句话：这版到底怎么样（含"该重跑了"这种语境） */
  readonly summary?: string
  readonly deliverable?: boolean
  /** 九层压成一个：`fail` > `unknown` > `pass`；没跑过是 `not_ready` */
  readonly rollup?: string
  readonly levels?: readonly VerdictLevel[]
  readonly levels_total?: number
  readonly counts?: Readonly<Record<string, number>>
  /** **已经排好序**的下一步（≤5）。UI 只按顺序渲染，不重排。 */
  readonly next?: readonly VerdictNext[]
  readonly next_total?: number
  readonly stale?: boolean
  readonly ladder_age?: string
  readonly note?: string
  readonly error?: string
}

export interface VerdictLevel {
  readonly level: string
  readonly title?: string
  readonly status: string
  readonly checked?: number
  readonly failed?: number
  /** 判不出的条数（原字段是明细列表，Python 已折成计数） */
  readonly unknown?: number
  readonly ms?: number
}

export interface VerdictNext {
  readonly category: string
  readonly title: string
  readonly action?: string
  readonly risk?: string
  readonly status?: string
  readonly basis?: string
  readonly step?: NextStep
}

export interface BoardPreset {
  kind: string
  name: string
  size_mm: { w: number; h: number; d: number }
  px: { w: number; h: number }
  px_per_mm: number
  thickness_mm: number
  source: PhysicalSource
  mount_holes: number
}

export interface PartSizeEntry {
  kind: string
  size_mm: { w: number; h: number; d: number }
  source: PhysicalSource
}

export interface ImportFormat {
  ext: string
  kind: string
  reads: string[]
  missing: string[]
}

export interface BootstrapPayload {
  status: BackendStatus
  findings?: readonly string[]
  boards: BoardPreset[]
  catalog: PartSizeEntry[]
  formats: ImportFormat[]
  projects: ProjectSummary[]
  hwforge: { root: string; available: boolean; capabilities: number }
  velxio: { url: string; reachable: boolean; error?: string; version?: string }
  python: { executable: string; root: string }
}

export interface ExportPayload {
  status: BackendStatus
  ok?: boolean
  blocked?: boolean
  path?: string
  written?: string[]
  tools?: Record<string, { available: boolean; detail: string }>
  tscircuit?: { ran: boolean; ok?: boolean; reason?: string; detail?: string }
  missing?: string[]
  note?: string
  findings?: readonly string[]
}

/** 工具调用留下的最小提示：客户端据此重新拉快照。 */
export interface HintPayload {
  status: BackendStatus
  project?: string
  view?: string
  seq: number
  at: number
}
