/**
 * 「硬件工作台」的宿主半 —— 把 Python 侧能力暴露成 Typert Remote 端点。
 *
 * 端点形如 `hwvelxio/<method>`，载荷 `{ args: { <参数名>: value } }`（参数名
 * 即线上字段名）。客户端用 `conn.rpc.call('/api', 'hwvelxio/snapshot', {args})`。
 *
 * 这一层**只编排，不判定**：模型、物理检查、预算、闸门、制造包都在
 * `hw_velxio.design`（Python）。任何"在 TS 里再算一遍"的冲动都要拒绝——那会
 * 造出第二个事实源，而两个事实源迟早会给出两个结论。
 *
 * @module dsh-hw-velxio/service
 */

import type { Context } from '@deepseek-ai/cordis'
import { Remote, TypertRemoteService } from '@deepseek-ai/dsh-typert-protocol'
import { bridge, design, pythonExecutable, repoRoot } from './bridge.js'
import type {
  BootstrapPayload,
  BoardPreset,
  BudgetPayload,
  ChecksPayload,
  CrosscheckPayload,
  DeferredPayload,
  DesignDoc,
  EvidencePayload,
  ExportPayload,
  HintPayload,
  ImportFormat,
  PartSizeEntry,
  ProjectSummary,
  SnapshotPayload,
} from './shared/types.js'

export const NS = 'hwvelxio'

/** 最近一次工具/命令动作，客户端据此知道"该刷新哪个工程"（最便宜的通知）。 */
interface HintState {
  project: string
  view: string
  seq: number
  at: number
}

const hintState: HintState = { project: '', view: '', seq: 0, at: 0 }

export function noteHint(project: string, view: string): HintState {
  hintState.project = project
  hintState.view = view
  hintState.seq += 1
  hintState.at = Date.now()
  return { ...hintState }
}

/** 失败载荷：形状与正常载荷兼容，但**一定带 status 与原因**。 */
function fail<T>(message: string, status: 'failed' | 'not_ready' = 'failed'): T {
  return {
    status,
    findings: [message],
    ok: false,
  } as unknown as T
}

function findingsOf(payload: Record<string, unknown>): string[] {
  const raw = payload.findings
  if (Array.isArray(raw)) return raw.map((x) => String(x))
  return []
}

function statusOf(payload: Record<string, unknown>): 'ok' | 'not_ready' | 'failed' {
  const raw = payload.status
  return raw === 'ok' || raw === 'not_ready' || raw === 'failed' ? raw : 'failed'
}

/** 服务名与 wire namespace。 */
export class HwVelxioGateway extends TypertRemoteService {
  constructor(ctx: Context) {
    super(ctx, NS)
  }

  // -- 目录 ---------------------------------------------------------------

  @Remote('bootstrap')
  async bootstrap(): Promise<BootstrapPayload> {
    const [boards, catalog, formats, projects, status] = await Promise.all([
      design('boards'),
      design('catalog'),
      design('formats'),
      design('list'),
      bridge('status'),
    ])
    const hwforge = {
      root: String(status.hwforge_root ?? ''),
      available: status.hwforge_available === true,
      capabilities: Number(status.hwforge_capabilities ?? 0),
    }
    const velxioRaw = (status.velxio ?? {}) as Record<string, unknown>
    const velxio = {
      url: String(velxioRaw.url ?? ''),
      reachable: velxioRaw.reachable === true,
      error: typeof velxioRaw.error === 'string' ? velxioRaw.error : undefined,
      version: typeof velxioRaw.version === 'string' ? velxioRaw.version : undefined,
    }
    if (statusOf(boards) === 'failed') {
      return fail<BootstrapPayload>(
        `读不到板卡目录：${findingsOf(boards).join('；') || '未知原因'}`)
    }
    return {
      status: 'ok',
      boards: (boards.boards ?? []) as BoardPreset[],
      catalog: (catalog.parts ?? []) as PartSizeEntry[],
      formats: (formats.formats ?? []) as ImportFormat[],
      projects: (projects.projects ?? []) as ProjectSummary[],
      hwforge,
      velxio,
      python: { executable: pythonExecutable(), root: repoRoot() },
    }
  }

  @Remote('projects')
  async projects(): Promise<{ status: 'ok' | 'failed'; projects: ProjectSummary[]; findings?: string[] }> {
    const payload = await design('list')
    return {
      status: statusOf(payload) === 'ok' ? 'ok' : 'failed',
      projects: (payload.projects ?? []) as ProjectSummary[],
      findings: findingsOf(payload),
    }
  }

  // -- 设计 ---------------------------------------------------------------

  @Remote('snapshot')
  async snapshot(project: string, pitchMm = 0.5): Promise<SnapshotPayload> {
    const payload = await design('snapshot', project, '--pitch', String(pitchMm))
    if (statusOf(payload) !== 'ok') {
      return fail<SnapshotPayload>(
        `读不到工程 ${project}：${findingsOf(payload).join('；') || '未知原因'}`,
        statusOf(payload) === 'not_ready' ? 'not_ready' : 'failed')
    }
    return payload as unknown as SnapshotPayload
  }

  @Remote('createProject')
  async createProject(project: string, board: string, title: string): Promise<DesignDoc> {
    const payload = await design('new', project, board, title)
    if (statusOf(payload) !== 'ok') {
      return fail<DesignDoc>(findingsOf(payload).join('；') || '创建失败')
    }
    noteHint(project, 'design')
    return (payload.design ?? {}) as DesignDoc
  }

  @Remote('importProject')
  async importProject(project: string, path: string, bomPath = ''): Promise<DesignDoc> {
    const args = ['import', project, path]
    if (bomPath !== '') args.push('--bom', bomPath)
    const payload = await design(...args)
    if (statusOf(payload) !== 'ok') {
      return fail<DesignDoc>(
        findingsOf(payload).join('；') || '导入失败',
        statusOf(payload) === 'not_ready' ? 'not_ready' : 'failed')
    }
    noteHint(project, 'design')
    return (payload.design ?? {}) as DesignDoc
  }

  @Remote('getDesign')
  async getDesign(project: string): Promise<DesignDoc> {
    const payload = await design('get', project)
    if (statusOf(payload) !== 'ok') {
      return fail<DesignDoc>(
        findingsOf(payload).join('；') || '读不到设计',
        statusOf(payload) === 'not_ready' ? 'not_ready' : 'failed')
    }
    return (payload.design ?? {}) as DesignDoc
  }

  // -- 编辑（写模型仍走 Python：校验只有一份） ----------------------------

  @Remote('movePart')
  async movePart(project: string, ref: string, xMm: number, yMm: number,
                 rotationZ: number, zMm = 0): Promise<DesignDoc> {
    const payload = await design('move', project, ref, String(xMm), String(yMm),
                                 String(rotationZ), String(zMm))
    if (statusOf(payload) !== 'ok') {
      return fail<DesignDoc>(findingsOf(payload).join('；') || '移动失败')
    }
    noteHint(project, 'placement')
    return this.getDesign(project)
  }

  @Remote('addPart')
  async addPart(project: string, ref: string, kind: string, role: string,
                xMm: number, yMm: number, rotationZ = 0): Promise<DesignDoc> {
    const payload = await design('addpart', project, ref, kind, role,
                                 String(xMm), String(yMm), String(rotationZ))
    if (statusOf(payload) !== 'ok') {
      return fail<DesignDoc>(findingsOf(payload).join('；') || '添加失败')
    }
    noteHint(project, 'placement')
    return this.getDesign(project)
  }

  @Remote('deletePart')
  async deletePart(project: string, ref: string): Promise<DesignDoc> {
    const payload = await design('delpart', project, ref)
    if (statusOf(payload) !== 'ok') {
      return fail<DesignDoc>(findingsOf(payload).join('；') || '删除失败')
    }
    noteHint(project, 'placement')
    return this.getDesign(project)
  }

  @Remote('setPart')
  async setPart(project: string, ref: string,
                patch: Record<string, unknown>): Promise<DesignDoc> {
    const payload = await design('setpart', project, ref, JSON.stringify(patch))
    if (statusOf(payload) !== 'ok') {
      return fail<DesignDoc>(findingsOf(payload).join('；') || '修改失败')
    }
    noteHint(project, 'placement')
    return this.getDesign(project)
  }

  // -- 判定 ---------------------------------------------------------------

  @Remote('check')
  async check(project: string, pitchMm = 0.5): Promise<ChecksPayload> {
    const payload = await design('check', project, '--pitch', String(pitchMm))
    if (statusOf(payload) !== 'ok') {
      return fail<ChecksPayload>(
        findingsOf(payload).join('；') || '检查无法运行',
        statusOf(payload) === 'not_ready' ? 'not_ready' : 'failed')
    }
    noteHint(project, 'checks')
    return payload as unknown as ChecksPayload
  }

  @Remote('budget')
  async budget(project: string): Promise<BudgetPayload> {
    const payload = await design('budget', project)
    if (statusOf(payload) !== 'ok') {
      return fail<BudgetPayload>(
        findingsOf(payload).join('；') || '预算无法评估',
        statusOf(payload) === 'not_ready' ? 'not_ready' : 'failed')
    }
    noteHint(project, 'budget')
    return payload as unknown as BudgetPayload
  }

  @Remote('setBudgetLimits')
  async setBudgetLimits(project: string,
                        limits: Record<string, number>): Promise<BudgetPayload> {
    const payload = await design('budget', project, '--limits', JSON.stringify(limits))
    if (statusOf(payload) !== 'ok') {
      return fail<BudgetPayload>(findingsOf(payload).join('；') || '设置预算失败')
    }
    noteHint(project, 'budget')
    return payload as unknown as BudgetPayload
  }

  @Remote('crosscheck')
  async crosscheck(project: string): Promise<CrosscheckPayload> {
    const payload = await design('crosscheck', project)
    if (statusOf(payload) !== 'ok') {
      return fail<CrosscheckPayload>(findingsOf(payload).join('；') || '核对失败')
    }
    noteHint(project, 'crosscheck')
    return payload as unknown as CrosscheckPayload
  }

  // -- 过程纪律 -----------------------------------------------------------

  @Remote('deferred')
  async deferred(project: string, action: string, name = '',
                 value = ''): Promise<DeferredPayload> {
    const args = ['deferred', project, action]
    if (action === 'lock' && name !== '') args.push(name, value)
    if (action === 'defer' && name !== '') args.push(name, value)
    const payload = await design(...args)
    noteHint(project, 'deferred')
    if (statusOf(payload) !== 'ok') {
      return fail<DeferredPayload>(
        findingsOf(payload).join('；') || '决策账本操作失败',
        statusOf(payload) === 'not_ready' ? 'not_ready' : 'failed')
    }
    return payload as unknown as DeferredPayload
  }

  @Remote('trace')
  async trace(project: string, action = 'list'): Promise<Record<string, unknown>> {
    const payload = await design('trace', project, action)
    return statusOf(payload) === 'ok' ? payload : fail(payload.findings?.join('；') ?? '轨迹读取失败') as Record<string, unknown>
  }

  @Remote('evidence')
  async evidence(project: string): Promise<EvidencePayload> {
    const payload = await design('evidence', project)
    if (statusOf(payload) !== 'ok') {
      return fail<EvidencePayload>(findingsOf(payload).join('；') || '证据读取失败')
    }
    return payload as unknown as EvidencePayload
  }

  // -- 制造交接 -----------------------------------------------------------

  @Remote('exportBundle')
  async exportBundle(project: string): Promise<ExportPayload> {
    const payload = await design('export', project)
    noteHint(project, 'export')
    if (statusOf(payload) === 'not_ready') {
      return {
        status: 'not_ready',
        blocked: true,
        findings: findingsOf(payload),
      }
    }
    if (statusOf(payload) !== 'ok') {
      return fail<ExportPayload>(findingsOf(payload).join('；') || '导出失败')
    }
    return payload as unknown as ExportPayload
  }

  // -- 外部引擎 -----------------------------------------------------------

  @Remote('velxioHealth')
  async velxioHealth(): Promise<Record<string, unknown>> {
    return bridge('velxio_health')
  }

  @Remote('velxioBoards')
  async velxioBoards(): Promise<Record<string, unknown>> {
    return bridge('velxio_boards')
  }

  @Remote('runBlock')
  async runBlock(project: string, block: string,
                 params: Record<string, unknown> = {}): Promise<Record<string, unknown>> {
    const payload = await bridge('run', project, block, JSON.stringify(params))
    noteHint(project, `block:${block}`)
    return payload
  }

  @Remote('capacities')
  async capacities(): Promise<Record<string, unknown>> {
    const payload = await bridge('catalog')
    return payload
  }

  // -- 通知 ---------------------------------------------------------------

  @Remote('hint')
  async hint(): Promise<HintPayload> {
    return { status: 'ok', ...hintState }
  }
}

export type { ChecksPayload, BudgetPayload, DeferredPayload, CrosscheckPayload,
  EvidencePayload, ExportPayload, SnapshotPayload }
