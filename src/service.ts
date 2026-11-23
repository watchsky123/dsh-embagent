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
import { bridge, design, designLong, pythonExecutable, repoRoot } from './bridge.js'
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
  PreflightPayload,
  DiffPayload,
  MaturityPayload,
  AdrPayload,
} from './shared/types.js'

export const NS = 'hwvelxio'

/**
 * 默认体素间距（mm）。
 *
 * 为什么做成常量而不是参数默认值：Typert 网关在**注册期**用
 * `Function.prototype.toString` 解析 `@Remote` 方法的参数表，源码里出现
 * `pitchMm = 0.5` 这种默认值会直接抛
 * `gateway/signature-invalid: SRC method must use unique identifier
 * parameters without destructuring, defaults, or rest`。可选参数写
 * `pitchMm?: number`，缺省值在**函数体内**兜底——wire 形状不变，行为不变。
 */
const DEFAULT_PITCH_MM = 0.5

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

  /** 一个元件的完整画像（P2 按对象聚合）。只读，不跑判定。 */
  @Remote('entity')
  async entity(project: string, ref: string): Promise<Record<string, unknown>> {
    const payload = await design('entity', project, ref)
    return payload as unknown as Record<string, unknown>
  }

  /**
   * 把需求草稿变成真需求（P5）。**`confirm` 是人的确认**，不是布尔开关的装饰：
   * 它为 `false` 时 Python 侧直接拒（`req_draft.apply` 的第三道闸门）。
   */
  @Remote('applyReqDraft')
  async applyReqDraft(project: string, confirm: boolean): Promise<Record<string, unknown>> {
    const args = ['req-draft', project, '--apply']
    if (confirm) args.push('--confirm')
    const payload = await design(...args)
    noteHint(project, 'req')
    return payload as unknown as Record<string, unknown>
  }

  @Remote('snapshot')
  async snapshot(project: string, pitchMm?: number): Promise<SnapshotPayload> {
    const payload = await design('snapshot', project, '--pitch', String(pitchMm ?? DEFAULT_PITCH_MM))
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
  async importProject(project: string, path: string, bomPath?: string): Promise<DesignDoc> {
    const args = ['import', project, path]
    if (bomPath !== undefined && bomPath !== '') args.push('--bom', bomPath)
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
                 rotationZ: number, zMm?: number): Promise<DesignDoc> {
    const payload = await design('move', project, ref, String(xMm), String(yMm),
                                 String(rotationZ), String(zMm ?? 0))
    if (statusOf(payload) !== 'ok') {
      return fail<DesignDoc>(findingsOf(payload).join('；') || '移动失败')
    }
    noteHint(project, 'placement')
    return this.getDesign(project)
  }

  @Remote('addPart')
  async addPart(project: string, ref: string, kind: string, role: string,
                xMm: number, yMm: number, rotationZ?: number): Promise<DesignDoc> {
    const payload = await design('addpart', project, ref, kind, role,
                                 String(xMm), String(yMm), String(rotationZ ?? 0))
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
  async check(project: string, pitchMm?: number): Promise<ChecksPayload> {
    const payload = await design('check', project, '--pitch', String(pitchMm ?? DEFAULT_PITCH_MM))
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
  async deferred(project: string, action: string, name?: string,
                 value?: string): Promise<DeferredPayload> {
    const args = ['deferred', project, action]
    if (name !== undefined && name !== ''
        && (action === 'lock' || action === 'defer')) args.push(name, value ?? '')
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
  async trace(project: string, action?: string): Promise<Record<string, unknown>> {
    const payload = await design('trace', project, action ?? 'list')
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

  /**
   * 跑一次发射（P46）。**长任务**：8 段、几十秒到几分钟，超时给 15 分钟。
   *
   * `jobs` 只跑指定段（`verify` = 只重验 —— 8 段里只有它下"能不能打板"的结论）。
   * 返回的是**快照里的 `emit` 摘要**（不是进度流，也不是重新算的结论）。
   */
  @Remote('emitRun')
  async emitRun(project: string, jobs?: string, route?: string): Promise<Record<string, unknown>> {
    const args = ['emit', project]
    if (jobs !== undefined && jobs !== '') args.push(`--jobs=${jobs}`)
    if (route !== undefined && route !== '') args.push(`--route=${route}`)
    const payload = await designLong(900_000, ...args)
    const status = statusOf(payload)
    if (status === 'failed') {
      return fail<Record<string, unknown>>(findingsOf(payload).join('；') || '发射失败')
    }
    noteHint(project, 'emit')
    const summary = await design('emit-status', project)
    return {
      ...summary,
      status: status === 'ok' ? 'ok' : status,
    }
  }

  /**
   * 发射摘要（P46）。**不是进度流**：长任务是一次 RPC，没有中间态；
   * 这里只报"上一次跑成什么样 + emit.json 的 mtime"。
   *
   * 之所以单独开一个 Remote 而不是复用 `snapshot`：snapshot 会跑检查/预算/
   * 核对（秒级），而这个会被前端轮询。
   */
  @Remote('emitProgress')
  async emitProgress(project: string): Promise<Record<string, unknown>> {
    const summary = await design('emit-status', project)
    return {
      ...summary,
      running: false,
      pollNote: '不是进度流：只能报上一次的结果（emit 是同步长任务）',
    }
  }

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

  // -- 工作区（用户资产目录）----------------------------------------------

  /**
   * 读当前工作区。`source` 说清它从哪来（env/config/default），
   * `writable` 是**写探针**的结果而不是猜的——只读目录必须在保存前暴露。
   */
  @Remote('workspace')
  async workspace(): Promise<Record<string, unknown>> {
    const payload = await design('workspace', 'show')
    if (statusOf(payload) !== 'ok') {
      return fail(payload.findings?.join('；') ?? '读不到工作区') as Record<string, unknown>
    }
    return payload
  }

  /** 切换到已有文件夹（客户端选完目录后调用）。 */
  @Remote('setWorkspace')
  async setWorkspace(path: string): Promise<Record<string, unknown>> {
    const payload = await design('workspace', 'set', path)
    if (statusOf(payload) !== 'ok') {
      return fail(payload.findings?.join('；') ?? '切换工作区失败') as Record<string, unknown>
    }
    noteHint('', 'workspace')
    return payload
  }

  /**
   * 在**指定父目录**下新建一个工作区子目录。
   *
   * 调用方（客户端）传入 DSH 当前工作目录：这样"新建工程"落在用户正在
   * 用的项目里，而不是落到插件目录或家目录的隐藏位置。
   */
  @Remote('createWorkspace')
  async createWorkspace(parent: string, name: string): Promise<Record<string, unknown>> {
    if (parent.trim() === '') throw new Error('需要 parent（父目录绝对路径）')
    if (name.trim() === '') throw new Error('需要 name（工作区目录名）')
    const payload = await design('workspace', 'create', parent, name)
    if (statusOf(payload) !== 'ok') {
      return fail(payload.findings?.join('；') ?? '新建工作区失败') as Record<string, unknown>
    }
    noteHint('', 'workspace')
    return payload
  }

  // -- 勘察（初审.md）------------------------------------------------------

  /**
   * 勘察工作区：工程清单 + 阶段 + 工程外的文件。
   *
   * **纯事实**：阶段来自 `design/phase.py` 的确定性推导，不含任何 AI 判断。
   * `write=true` 时同时刷新 `初审.md` 的机器事实段（保留判断段原文）。
   */
  @Remote('survey')
  async survey(write?: boolean): Promise<Record<string, unknown>> {
    const payload = await design('survey', ...(write === true ? ['--write'] : []))
    if (statusOf(payload) !== 'ok') {
      return fail(payload.findings?.join('；') ?? '勘察失败') as Record<string, unknown>
    }
    return payload
  }

  /** 读回初审.md（含指纹比对：`stale=true` 说明工作区已经变了）。 */
  @Remote('initialReview')
  async initialReview(): Promise<Record<string, unknown>> {
    return design('survey', '--read').then((payload) => (
      statusOf(payload) === 'ok'
        ? payload
        : fail(payload.findings?.join('；') ?? '读不到初审.md') as Record<string, unknown>))
  }

  // -- 外部引擎 -----------------------------------------------------------

  @Remote('velxioHealth')
  async velxioHealth(): Promise<Record<string, unknown>> {
    // 走 serve 子命令而不是 bridge('velxio_health')：前者报三态
    // （ok/not_ready/failed）+ 工具链状态 + 端口监听者，后者只报
    // "HTTP 通不通"。面板需要区分"后端没起"与"起了但缺 arduino-cli"。
    return design('serve', 'status')
  }

  /**
   * 启动/停止 Velxio 后端。
   *
   * **不在 TS 里做进程管理**：那会造第二事实源。全部委托 `serve` 子命令，
   * 这里只转发 action 并原样返回结果。
   */
  @Remote('velxioControl')
  async velxioControl(action: string): Promise<Record<string, unknown>> {
    const verb = (action ?? '').trim()
    if (verb !== 'start' && verb !== 'stop' && verb !== 'status') {
      return { status: 'failed', findings: [`未知动作 ${verb}（start/stop/status）`] }
    }
    // `start` 会等 /health 起来（最长 150s），必须有更长的超时，
    // 否则面板拿到的是被外部砍断的 "Command failed" 而不是结构化结果。
    return verb === 'start'
      ? designLong(240_000, 'serve', verb)
      : design('serve', verb)
  }

  @Remote('velxioBoards')
  async velxioBoards(): Promise<Record<string, unknown>> {
    return bridge('velxio_boards')
  }

  @Remote('runBlock')
  async runBlock(project: string, block: string,
                 params?: Record<string, unknown>): Promise<Record<string, unknown>> {
    const payload = await bridge('run', project, block, JSON.stringify(params ?? {}))
    noteHint(project, `block:${block}`)
    return payload
  }

  @Remote('capacities')
  async capacities(): Promise<Record<string, unknown>> {
    const payload = await bridge('catalog')
    return payload
  }

  // -- 仿真（AVR，宿主 Node 内）-------------------------------------------

  /**
   * 起一个 AVR 仿真会话。
   *
   * **不经过 Python**：AVR 的 CPU 模型不在 Velxio 后端（那是 ESP32 QEMU），
   * 而在 `avr8js` 里；它在 Node 中可直接运行。所以这一层是本插件自己的
   * 运行时，不是"绕过 Velxio"——是补上它在无浏览器场景下缺的那块。
   *
   * `hex` 传空串表示"**由宿主去取**"：面板没有文件系统访问，让它读
   * `<工程>/velxio_compile.json` 是把宿主的能力交给客户端，而客户端
   * 本来就不该碰盘。
   */
  @Remote('simStart')
  async simStart(project: string, board: string, hex: string): Promise<Record<string, unknown>> {
    const { start } = await import('./velxio/sim.js')
    let firmware = hex
    if (firmware.trim() === '') {
      const loaded = await this.loadCompiledHex(project)
      if (loaded.status !== 'ok') return loaded
      firmware = String(loaded.hex ?? '')
    }
    const out = start({ project, board, hex: firmware })
    if (out.status === 'ok') noteHint(project, 'velxio')
    return out
  }

  /**
   * 从工程目录读上次编译出的 hex。
   *
   * 找 `velxio_compile.json`：`hw_compile` / `hw_block` 把后端响应原样落盘，
   * hex 在 `data.hex_content`（异步路径）或顶层 `hex_content`（同步路径）——
   * **两种形状都要认**，否则"编译明明成功了，仿真说没有固件"。
   */
  private async loadCompiledHex(project: string): Promise<Record<string, unknown>> {
    const survey = await design('survey')
    const rows = Array.isArray(survey.projects) ? survey.projects as Array<Record<string, unknown>> : []
    const row = rows.find((entry) => String(entry.id) === project)
    const projectPath = row === undefined ? '' : String(row.path ?? '')
    if (projectPath === '') {
      return { status: 'not_ready', findings: [
        `找不到工程 ${project} 的路径；用 hw_projects 确认工程名。`] }
    }
    // 读盘与形状兼容都在 `velxio/artifact.ts` —— 与 `hw_sim` 工具共用一份。
    const { loadCompiledHex } = await import('./velxio/artifact.js')
    const loaded = loadCompiledHex(projectPath)
    return loaded.status === 'ok'
      ? { status: 'ok', hex: loaded.hex }
      : { status: loaded.status, findings: loaded.findings }
  }

  @Remote('simRun')
  async simRun(project: string, maxInstructions?: number,
               maxBytes?: number): Promise<Record<string, unknown>> {
    const { run } = await import('./velxio/sim.js')
    return run(project, {
      maxInstructions: maxInstructions ?? 40_000_000,
      maxBytes: maxBytes ?? 4096,
    })
  }

  @Remote('simStop')
  async simStop(project: string): Promise<Record<string, unknown>> {
    const { stop } = await import('./velxio/sim.js')
    return stop(project)
  }

  @Remote('simStatus')
  async simStatus(project: string): Promise<Record<string, unknown>> {
    const { status } = await import('./velxio/sim.js')
    return status(project)
  }

  /** 只读串口（不推进 CPU）。面板轮询就靠它。 */
  @Remote('serialRead')
  async serialRead(project: string): Promise<Record<string, unknown>> {
    const { serialRead } = await import('./velxio/sim.js')
    return serialRead(project)
  }

  // -- P3：治理类（预检 / 回读差异 / 成熟度 / ADR）------------------------

  /** 发射前预检（三层：编号 → 器件 → 引脚名）。**不开工程**，只问系统库。 */
  @Remote('preflight')
  async preflight(project: string): Promise<PreflightPayload> {
    const payload = await design('emit-preflight', project)
    noteHint(project, 'preflight')
    return payload as unknown as PreflightPayload
  }

  /**
   * 回读差异：`readback --diff`。**会开一个立创会话**（几十秒），
   * 用 `designLong` 而不是默认 120s —— 43 件工程的打开+读网表会撞默认超时。
   */
  @Remote('readbackDiff')
  async readbackDiff(project: string): Promise<DiffPayload> {
    const payload = await designLong(600_000, 'readback', project, '--diff')
    noteHint(project, 'diff')
    return payload as unknown as DiffPayload
  }

  /** 成熟度：六维只读本工程。 */
  @Remote('maturity')
  async maturity(project: string): Promise<MaturityPayload> {
    const payload = await design('maturity', project)
    noteHint(project, 'maturity')
    return payload as unknown as MaturityPayload
  }

  /** ADR 列表（只读）。 */
  @Remote('adrList')
  async adrList(project: string): Promise<AdrPayload> {
    return await design('adr', project, 'list') as unknown as AdrPayload
  }

  /** ADR 校验（四节齐全 + 索引↔文件一致）。 */
  @Remote('adrValidate')
  async adrValidate(project: string): Promise<AdrPayload> {
    return await design('adr', project, 'validate') as unknown as AdrPayload
  }

  // -- 通知 ---------------------------------------------------------------

  @Remote('hint')
  async hint(): Promise<HintPayload> {
    return { status: 'ok', ...hintState }
  }
}

export type { ChecksPayload, BudgetPayload, DeferredPayload, CrosscheckPayload,
  EvidencePayload, ExportPayload, SnapshotPayload }
