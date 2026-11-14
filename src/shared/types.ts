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
  board: {
    kind: string
    name: string
    w_px: number
    h_px: number
    thickness_mm: number
    px_per_mm: number
    size_mm: { w: number; h: number }
    outline_px: Array<[number, number]>
  }
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
  }>
  wires: Array<{ net: string; path_px: Array<[number, number]> }>
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
  rotation_z: number
  physical_source: PhysicalSource
  mount: string
}

export interface Scene3D {
  units: 'mm'
  board: {
    kind: string
    name: string
    outline: Array<[number, number]>
    size_mm: { w: number; h: number }
    thickness_mm: number
  }
  parts: Scene3DPart[]
  wires: Array<{ net: string; path_mm: Array<[number, number, number]> }>
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
