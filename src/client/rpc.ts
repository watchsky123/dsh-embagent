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
 * @module dsh-hw-velxio/client/rpc
 */

import type { ConnectionHandle } from '@deepseek-ai/dsh-client-connection/client'
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
  trace(project: string, action?: string): Promise<Record<string, unknown>>
  velxioHealth(): Promise<Record<string, unknown>>
  runBlock(project: string, block: string, params?: Record<string, unknown>): Promise<Record<string, unknown>>
  hint(): Promise<HintPayload>
}

/** 用一个真实连接构造 face。 */
export function createFace(handle: ConnectionHandle): WorkbenchFace {
  return {
    bootstrap: () => call<BootstrapPayload>(handle, 'bootstrap'),
    snapshot: (project, pitchMm) =>
      call<SnapshotPayload>(handle, 'snapshot', pitchMm === undefined
        ? { project } : { project, pitchMm }),
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
    trace: (project, action) =>
      call<Record<string, unknown>>(handle, 'trace',
        action === undefined ? { project } : { project, action }),
    velxioHealth: () => call<Record<string, unknown>>(handle, 'velxioHealth'),
    runBlock: (project, block, params) =>
      call<Record<string, unknown>>(handle, 'runBlock',
        params === undefined ? { project, block } : { project, block, params }),
    hint: () => call<HintPayload>(handle, 'hint'),
  }
}
