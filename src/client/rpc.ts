/**
 * 客户端 → 宿主的 RPC 包装。
 *
 * 端点语法与载荷形状与宿主的 Typert 网关一致（`@Remote` 的**参数名**就是
 * 线上字段名）：
 *
 *     channel  '/api'
 *     endpoint 'hwvelxio/<method>'
 *     payload  { args: { <参数名>: value } }
 *
 * 这里不做任何几何/判定计算：拿不到就让 store 进入错误态，绝不返回一个
 * "看起来空"的对象——空工程与读失败在面板上必须长得不一样。
 *
 * @module dsh-embagent/client/rpc
 */

import type { ConnectionHandle } from '@deepseek-ai/dsh-client-connection/client'
import { makePickDirectory } from './directoryPicker.js'
import type { DirectoryPickerSeams } from './directoryPicker.js'
import type {
  BootstrapPayload,
  BudgetPayload,
  ChecksPayload,
  CrosscheckPayload,
  DeferredPayload,
  DesignDoc,
  EvidencePayload,
  ExportPayload,
  HintPayload,
  ProjectSummary,
  SnapshotPayload,
  EntityProfile,
  PreflightPayload,
  DiffPayload,
  MaturityPayload,
  AdrPayload,
} from '../shared/types.js'

const CHANNEL = '/api'
const NS = 'hwvelxio'

/** 宿主可能回的成功/失败两种信封。 */
type Envelope<T> =
  | { ok: true; value: T }
  | { ok: false; error: string | { code?: string; message?: string } }
  | T

export class RpcError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'RpcError'
  }
}

/** 把网关的失败信封压成一句可显示的原因。 */
function unwrap<T>(raw: unknown): T {
  if (raw !== null && typeof raw === 'object') {
    const record = raw as Record<string, unknown>
    if (record.ok === true && 'value' in record) return record.value as T
    if (record.ok === false) {
      const error = record.error
      if (typeof error === 'string') throw new RpcError(error)
      if (error !== null && typeof error === 'object') {
        const detail = error as Record<string, unknown>
        const code = typeof detail.code === 'string' ? detail.code : 'gateway/internal'
        const message = typeof detail.message === 'string' ? detail.message : ''
        throw new RpcError(message === '' ? code : `${code}: ${message}`)
      }
      throw new RpcError('RPC 失败但没有错误详情')
    }
    // Typert 某些传输模式直接返回值本身
    return raw as T
  }
  throw new RpcError(`无法解析的 RPC 返回：${JSON.stringify(raw).slice(0, 160)}`)
}

async function call<T>(handle: ConnectionHandle, method: string,
                        args: Record<string, unknown> = {}): Promise<T> {
  const raw = await handle.rpc.call(CHANNEL, `${NS}/${method}`, { args })
  return unwrap<T>(raw)
}

/** 面板需要的全部宿主能力。store 只依赖这个结构，方便测试时注入假实现。 */
export interface WorkbenchFace {
  bootstrap(): Promise<BootstrapPayload>
  snapshot(project: string, pitchMm?: number): Promise<SnapshotPayload>
  /** 把需求草稿变成真需求（P5）。`confirm` 必须由人点。 */
  applyReqDraft(project: string, confirm: boolean): Promise<Record<string, unknown>>
  /** 一个元件的完整画像（断言 / 归因 / 检查 / 实测 / 单元）。 */
  entity(project: string, ref: string): Promise<EntityProfile>
  projects(): Promise<ProjectSummary[]>
  createProject(project: string, board: string, title: string): Promise<DesignDoc>
  importProject(project: string, path: string, bomPath?: string): Promise<DesignDoc>
  movePart(project: string, ref: string, xMm: number, yMm: number,
           rotationZ: number, zMm?: number): Promise<DesignDoc>
  addPart(project: string, ref: string, kind: string, role: string,
          xMm: number, yMm: number, rotationZ?: number): Promise<DesignDoc>
  deletePart(project: string, ref: string): Promise<DesignDoc>
  setPart(project: string, ref: string, patch: Record<string, unknown>): Promise<DesignDoc>
  check(project: string, pitchMm?: number): Promise<ChecksPayload>
  budget(project: string): Promise<BudgetPayload>
  setBudgetLimits(project: string, limits: Record<string, number>): Promise<BudgetPayload>
  crosscheck(project: string): Promise<CrosscheckPayload>
  deferred(project: string, action: string, name?: string, value?: string): Promise<DeferredPayload>
  evidence(project: string): Promise<EvidencePayload>
  exportBundle(project: string): Promise<ExportPayload>
  /**
   * 跑一次发射（P46）。**这是长任务**：8 段、几十秒到几分钟。
   *
   * `jobs` 可以只跑一段（如 `verify`）来"只重验"。返回的是 `emit.json`
   * 的摘要（与 `snapshot.emit` 同形状），不是进度流。
   */
  emitRun(project: string, jobs?: string, route?: string): Promise<Record<string, unknown>>
  /** 发射进度/最后一次结果（读 emit.json + 运行日志尾巴，不推进任何东西）。 */
  emitProgress(project: string): Promise<Record<string, unknown>>
  trace(project: string, action?: string): Promise<Record<string, unknown>>
  velxioHealth(): Promise<Record<string, unknown>>
  /** 启动/停止 Velxio 后端（委托 Python 侧的 serve 子命令）。 */
  velxioControl(action: 'start' | 'stop' | 'status'): Promise<Record<string, unknown>>
  /** AVR 仿真：起会话（宿主 Node 内的 avr8js，不经过后端）。 */
  simStart(project: string, board: string, hex: string): Promise<Record<string, unknown>>
  /** AVR 仿真：推进并取串口增量。 */
  simRun(project: string, maxInstructions?: number, maxBytes?: number): Promise<Record<string, unknown>>
  simStop(project: string): Promise<Record<string, unknown>>
  simStatus(project: string): Promise<Record<string, unknown>>
  /** 只读串口（不推进 CPU）。 */
  serialRead(project: string): Promise<Record<string, unknown>>
  runBlock(project: string, block: string, params?: Record<string, unknown>): Promise<Record<string, unknown>>
  hint(): Promise<HintPayload>
  /** 勘察工作区（P2）。write=true 会刷新初审.md 的事实段。 */
  survey(write?: boolean): Promise<Record<string, unknown>>
  /** P3：发射前预检（三层）。 */
  preflight(project: string): Promise<import('../shared/types.js').PreflightPayload>
  /** P3：回读差异（会开立创会话，慢）。 */
  readbackDiff(project: string): Promise<import('../shared/types.js').DiffPayload>
  /** P3：成熟度六维。 */
  maturity(project: string): Promise<import('../shared/types.js').MaturityPayload>
  /** P3：ADR 列表 / 校验。 */
  adrList(project: string): Promise<import('../shared/types.js').AdrPayload>
  adrValidate(project: string): Promise<import('../shared/types.js').AdrPayload>
  /** 读回初审.md（含 stale 判定）。 */
  initialReview(): Promise<Record<string, unknown>>
  workspace(): Promise<WorkspacePayload>
  setWorkspace(path: string): Promise<WorkspacePayload>
  createWorkspace(parent: string, name: string): Promise<WorkspacePayload>
  /** 原生文件夹选择（由 view.ts 注入宿主的 directoryPicker 实现）。 */
  pickDirectory(): Promise<string | null>
}

/** 工作区载荷（`source` 说清这个根从哪来，面板要显示出来）。 */
export interface WorkspacePayload {
  readonly status?: string
  readonly findings?: readonly string[]
  readonly workspace: string
  readonly exists?: boolean
  readonly writable?: boolean
  readonly source?: 'env' | 'config' | 'default'
  readonly default?: string
  readonly config_path?: string
  readonly created?: boolean
  readonly projects?: readonly string[]
}

/** 用一个真实连接构造 face。 */
export function createFace(
  handle: ConnectionHandle,
  seams?: () => DirectoryPickerSeams,
): WorkbenchFace {
  return {
    bootstrap: () => call<BootstrapPayload>(handle, 'bootstrap'),
    snapshot: (project, pitchMm) =>
      call<SnapshotPayload>(handle, 'snapshot', pitchMm === undefined
        ? { project } : { project, pitchMm }),
    entity: (project, ref) =>
      call<EntityProfile>(handle, 'entity', { project, ref }),
    applyReqDraft: (project, confirm) =>
      call<Record<string, unknown>>(handle, 'applyReqDraft', { project, confirm }),
    projects: async () => {
      const payload = await call<{ projects: ProjectSummary[] }>(handle, 'projects')
      return payload.projects ?? []
    },
    createProject: (project, board, title) =>
      call<DesignDoc>(handle, 'createProject', { project, board, title }),
    importProject: (project, path, bomPath) =>
      call<DesignDoc>(handle, 'importProject',
        bomPath === undefined || bomPath === ''
          ? { project, path }
          : { project, path, bomPath }),
    movePart: (project, ref, xMm, yMm, rotationZ, zMm) =>
      call<DesignDoc>(handle, 'movePart',
        zMm === undefined
          ? { project, ref, xMm, yMm, rotationZ }
          : { project, ref, xMm, yMm, rotationZ, zMm }),
    addPart: (project, ref, kind, role, xMm, yMm, rotationZ) =>
      call<DesignDoc>(handle, 'addPart',
        rotationZ === undefined
          ? { project, ref, kind, role, xMm, yMm }
          : { project, ref, kind, role, xMm, yMm, rotationZ }),
    deletePart: (project, ref) => call<DesignDoc>(handle, 'deletePart', { project, ref }),
    setPart: (project, ref, patch) =>
      call<DesignDoc>(handle, 'setPart', { project, ref, patch }),
    check: (project, pitchMm) =>
      call<ChecksPayload>(handle, 'check', pitchMm === undefined
        ? { project } : { project, pitchMm }),
    budget: (project) => call<BudgetPayload>(handle, 'budget', { project }),
    setBudgetLimits: (project, limits) =>
      call<BudgetPayload>(handle, 'setBudgetLimits', { project, limits }),
    crosscheck: (project) => call<CrosscheckPayload>(handle, 'crosscheck', { project }),
    deferred: (project, action, name, value) =>
      call<DeferredPayload>(handle, 'deferred',
        name === undefined ? { project, action } : { project, action, name, value: value ?? '' }),
    evidence: (project) => call<EvidencePayload>(handle, 'evidence', { project }),
    exportBundle: (project) => call<ExportPayload>(handle, 'exportBundle', { project }),
    emitRun: (project, jobs, route) => call<Record<string, unknown>>(handle, 'emitRun', {
      project,
      ...(jobs === undefined || jobs === '' ? {} : { jobs }),
      ...(route === undefined || route === '' ? {} : { route }),
    }),
    emitProgress: (project) =>
      call<Record<string, unknown>>(handle, 'emitProgress', { project }),
    trace: (project, action) =>
      call<Record<string, unknown>>(handle, 'trace',
        action === undefined ? { project } : { project, action }),
    velxioHealth: () => call<Record<string, unknown>>(handle, 'velxioHealth'),
    velxioControl: (action) =>
      call<Record<string, unknown>>(handle, 'velxioControl', { action }),
    simStart: (project, board, hex) =>
      call<Record<string, unknown>>(handle, 'simStart', { project, board, hex }),
    simRun: (project, maxInstructions, maxBytes) =>
      call<Record<string, unknown>>(handle, 'simRun',
        maxInstructions === undefined ? { project } : { project, maxInstructions, maxBytes }),
    simStop: (project) => call<Record<string, unknown>>(handle, 'simStop', { project }),
    simStatus: (project) => call<Record<string, unknown>>(handle, 'simStatus', { project }),
    serialRead: (project) => call<Record<string, unknown>>(handle, 'serialRead', { project }),
    runBlock: (project, block, params) =>
      call<Record<string, unknown>>(handle, 'runBlock',
        params === undefined ? { project, block } : { project, block, params }),
    preflight: (project) => call<PreflightPayload>(handle, 'preflight', { project }),
    readbackDiff: (project) => call<DiffPayload>(handle, 'readbackDiff', { project }),
    maturity: (project) => call<MaturityPayload>(handle, 'maturity', { project }),
    adrList: (project) => call<AdrPayload>(handle, 'adrList', { project }),
    adrValidate: (project) => call<AdrPayload>(handle, 'adrValidate', { project }),
    hint: () => call<HintPayload>(handle, 'hint'),
    survey: (write) => call<Record<string, unknown>>(handle, 'survey',
      write === true ? { write: true } : {}),
    initialReview: () => call<Record<string, unknown>>(handle, 'initialReview'),
    workspace: () => call<WorkspacePayload>(handle, 'workspace'),
    setWorkspace: (path) => call<WorkspacePayload>(handle, 'setWorkspace', { path }),
    createWorkspace: (parent, name) =>
      call<WorkspacePayload>(handle, 'createWorkspace', { parent, name }),
    // 原生选择器不走本插件的 RPC：它是宿主**客户端**的能力（桌面壳 preload
    // 或 uiWorkspace），seams 每次点击时才解析，装载顺序不影响可用性。
    pickDirectory: makePickDirectory(seams ?? (() => ({}))),
  }
}
