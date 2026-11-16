/**
 * 「硬件工作台」的客户端状态。
 *
 * 一个 store，一个真值：2D 与 3D 共享 `selectedRef`，所以点一个另一个同步
 * 高亮；改一个元件两边一起变。不做视图各自的状态副本——那是 v1 的病根。
 *
 * 数据只有两个来源：宿主 RPC（`WorkbenchFace`）与本地 UI 选择。判定结果永远
 * 从 RPC 拿，客户端不重算。
 *
 * @module dsh-hw-velxio/client/store
 */

import { useSyncExternalStore } from 'react'
import type {
  BootstrapPayload,
  ExportPayload,
  ProjectSummary,
  SnapshotPayload,
} from '../shared/types.js'
import type { WorkbenchFace } from './rpc.js'

export type PanelId =
  | 'checks'
  | 'budget'
  | 'deferred'
  | 'crosscheck'
  | 'evidence'
  | 'export'
  | 'blocks'

export type Layout = 'split' | '2d' | '3d'
const LAYOUTS = ['split', '2d', '3d'] as const

/**
 * 可拖与持久化的布局尺寸（Velxio 纪律：每块面板 MIN/MAX/DEFAULT 三件套）。
 * 值存 localStorage；存储被禁就静默用默认（编辑器记忆是 nicety，不是崩溃理由）。
 */
export const SPLIT_MIN = 20
export const SPLIT_MAX = 80
export const SPLIT_DEFAULT = 50

export const INSPECTOR_MIN = 200
export const INSPECTOR_MAX = 420
export const INSPECTOR_DEFAULT = 268

export const BOTTOM_MIN = 90
export const BOTTOM_MAX = 600
export const BOTTOM_DEFAULT = 200

const STORAGE_PREFIX = 'dsh-hwv.'

function readStored(key: string, fallback: number, min: number, max: number): number {
  try {
    const raw = globalThis.localStorage?.getItem(STORAGE_PREFIX + key)
    if (raw === null || raw === '') return fallback
    const parsed = Number(raw)
    if (!Number.isFinite(parsed)) return fallback
    return Math.max(min, Math.min(max, parsed))
  } catch {
    return fallback
  }
}

function writeStored(key: string, value: number): void {
  try {
    globalThis.localStorage?.setItem(STORAGE_PREFIX + key, String(value))
  } catch { /* private mode / disabled storage：记忆是 nicety */ }
}

function readStoredString(key: string, fallback: string): string {
  try {
    return globalThis.localStorage?.getItem(STORAGE_PREFIX + key) ?? fallback
  } catch {
    return fallback
  }
}

/** 枚举值的读回校验：坏值（手改存储/旧版本残留）退回默认，不污染布局判断。 */
function readStoredEnum<T extends string>(key: string, allowed: readonly T[], fallback: T): T {
  const raw = readStoredString(key, fallback)
  return (allowed as readonly string[]).includes(raw) ? (raw as T) : fallback
}

function writeStoredString(key: string, value: string): void {
  try {
    globalThis.localStorage?.setItem(STORAGE_PREFIX + key, value)
  } catch { /* 同上 */ }
}

export interface HwState {
  readonly ready: boolean
  readonly loading: boolean
  readonly busy: string
  readonly error: string | null
  readonly notice: string | null
  readonly face: WorkbenchFace | null
  readonly bootstrap: BootstrapPayload | null
  readonly project: string
  readonly snapshot: SnapshotPayload | null
  readonly layout: Layout
  readonly selectedRef: string
  readonly panel: PanelId
  readonly hintSeq: number
  readonly lastExport: ExportPayload | null
  /** 2D|3D 分栏中 2D 的百分比（split 布局下生效）。 */
  readonly splitPct: number
  /** 检查器列宽 px。 */
  readonly inspectorWidth: number
  /** 底部面板高 px。 */
  readonly bottomHeight: number
  /**
   * 3D 视图工具态。**存在 store 而不是组件里**：布局改为隐藏即卸载后，
   * 组件的内部 state 会随卸载消失——爆炸/剖面/网格必须活过切视图。
   */
  readonly explode: boolean
  readonly section: boolean
  readonly grid3d: boolean
  /** 当前工作区（用户资产目录）。null = 还没读过。 */
  readonly workspace: string
  readonly workspaceSource: 'env' | 'config' | 'default' | ''
  readonly workspaceWritable: boolean
  /**
   * 工作区设置块是否展开。**一处状态、两个入口**：有工程时入口在工具栏，
   * 没工程时在空态右上角——两处读同一个值，任一时刻只渲染其中一个，
   * 因此不会出现两份同时打开。
   */
  readonly workspaceOpen: boolean
  /** DSH 当前工作目录（由会话注入）："新建工作区"默认落在它下面。 */
  readonly dshCwd: string
  /**
   * 工作区勘察摘要（条目数 / 未接入 / MCU / 读取覆盖度）。
   * 评审 prompt 用它解决"2085 个文件的工作区被当成空板子"的问题。
   */
  readonly survey: SurveySummary | null
}

/** 勘察摘要：只保留 prompt 与界面需要的那几项。 */
export interface SurveySummary {
  readonly workspace: string
  readonly entries: number
  readonly notAdopted: number
  readonly kinds: readonly string[]
  readonly mcus: readonly string[]
  readonly readFiles: number
  readonly totalFiles: number
  readonly fingerprint: string
  /** 日志目录（`<工作区>/logs/`）。 */
  readonly logsDir: string
}

const INITIAL: HwState = {
  ready: false,
  loading: false,
  busy: '',
  error: null,
  notice: null,
  face: null,
  bootstrap: null,
  project: '',
  snapshot: null,
  layout: readStoredEnum('layout', LAYOUTS, 'split'),
  selectedRef: '',
  panel: 'checks',
  hintSeq: 0,
  lastExport: null,
  splitPct: readStored('splitPct', SPLIT_DEFAULT, SPLIT_MIN, SPLIT_MAX),
  inspectorWidth: readStored('inspectorWidth', INSPECTOR_DEFAULT, INSPECTOR_MIN, INSPECTOR_MAX),
  bottomHeight: readStored('bottomHeight', BOTTOM_DEFAULT, BOTTOM_MIN, BOTTOM_MAX),
  explode: readStoredString('view3d.explode', '0') === '1',
  section: readStoredString('view3d.section', '0') === '1',
  grid3d: readStoredString('view3d.grid', '1') === '1',
  workspace: '',
  workspaceSource: '',
  workspaceWritable: true,
  workspaceOpen: false,
  dshCwd: '',
  survey: null,
}

type Listener = () => void

function message(error: unknown): string {
  if (error instanceof Error) return error.message
  return String(error)
}

export class HwStore {
  private state: HwState = INITIAL
  private listeners = new Set<Listener>()
  private pollTimer: ReturnType<typeof setInterval> | null = null

  // -- 订阅 ---------------------------------------------------------------

  subscribe = (listener: Listener): (() => void) => {
    this.listeners.add(listener)
    return () => { this.listeners.delete(listener) }
  }

  getSnapshot = (): HwState => this.state

  private set(patch: Partial<HwState>): void {
    this.state = { ...this.state, ...patch }
    for (const listener of this.listeners) listener()
  }

  // -- 生命周期 -----------------------------------------------------------

  /** 交给组件：注入 face 并拉首次数据。重复调用是安全的。 */
  async init(face: WorkbenchFace): Promise<void> {
    if (this.state.face !== null) return
    this.set({ face })
    await this.reload()
    this.startPolling()
  }

  dispose(): void {
    if (this.pollTimer !== null) clearInterval(this.pollTimer)
    this.pollTimer = null
  }

  /**
   * 轮询宿主的 hint。工具/命令改了工程，面板要跟着刷新；没有推送通道时
   * 轮询一个自增序号是成本最低的做法（2.5s 一次，一次 RPC）。
   */
  private startPolling(): void {
    if (this.pollTimer !== null) return
    this.pollTimer = setInterval(() => { void this.poll() }, 2500)
  }

  private async poll(): Promise<void> {
    const face = this.state.face
    if (face === null || this.state.loading) return
    try {
      const hint = await face.hint()
      if (hint.seq === this.state.hintSeq) return
      this.set({ hintSeq: hint.seq })
      if (hint.project !== undefined && hint.project !== '') {
        if (hint.project !== this.state.project) {
          await this.open(hint.project)
        } else {
          await this.refresh()
        }
      } else {
        await this.reloadProjects()
      }
    } catch {
      // 轮询失败不打扰用户：下一次成功会自愈
    }
  }

  // -- 数据 ---------------------------------------------------------------

  private async reload(): Promise<void> {
    const face = this.state.face
    if (face === null) return
    this.set({ loading: true, error: null })
    try {
      const bootstrap = await face.bootstrap()
      if (bootstrap.status === 'failed') {
        this.set({
          loading: false,
          ready: true,
          error: (bootstrap.findings ?? []).join('；') || '读取目录失败',
        })
        return
      }
      const projects = bootstrap.projects ?? []
      const keep = projects.some((p) => p.id === this.state.project)
        ? this.state.project
        : (projects[0]?.id ?? '')
      this.set({ bootstrap, loading: false, ready: true })
      void this.loadWorkspace()
      // 勘察摘要与工作区一起拉：它决定评审 prompt 里"这里到底有什么"。
      void this.loadSurvey()
      if (keep !== '') await this.open(keep)
      else this.set({ snapshot: null, project: '' })
    } catch (error) {
      this.set({ loading: false, ready: true, error: message(error) })
    }
  }

  /** 由会话注入 DSH 当前工作目录（`workingDirectory` projection 的快照）。 */
  setDshCwd(cwd: string): void {
    if (cwd !== this.state.dshCwd) this.set({ dshCwd: cwd })
  }

  // -- 工作区 -------------------------------------------------------------

  /** 读当前工作区（工程住的目录，与插件安装位置无关）。 */
  async loadWorkspace(): Promise<void> {
    const face = this.state.face
    if (face === null) return
    try {
      const payload = await face.workspace()
      this.set({
        workspace: payload.workspace,
        workspaceSource: payload.source ?? '',
        workspaceWritable: payload.writable !== false,
      })
    } catch (error) {
      this.set({ error: message(error) })
    }
  }

  /** 切换工作区（客户端已用原生选择器选好目录）。成功后重载工程列表。 */
  async chooseWorkspace(path: string): Promise<void> {
    const face = this.state.face
    if (face === null || path === '') return
    this.set({ busy: '切换工作区', error: null })
    try {
      const payload = await face.setWorkspace(path)
      this.set({
        busy: '',
        workspace: payload.workspace,
        workspaceSource: payload.source ?? 'config',
        workspaceWritable: payload.writable !== false,
        project: '',
        snapshot: null,
        selectedRef: '',
      })
      await this.reload()
    } catch (error) {
      this.set({ busy: '', error: message(error) })
    }
  }

  /**
   * 在 parent 下新建工作区子目录并切过去。
   *
   * parent 由调用方给出 **DSH 当前工作目录**：这样工作区落在用户正在用的
   * 项目里，而不是插件目录或家目录的隐藏位置。
   */
  async createWorkspace(parent: string, name: string): Promise<void> {
    const face = this.state.face
    if (face === null) return
    this.set({ busy: '新建工作区', error: null })
    try {
      const payload = await face.createWorkspace(parent, name)
      this.set({
        busy: '',
        workspace: payload.workspace,
        workspaceSource: 'config',
        workspaceWritable: true,
        project: '',
        snapshot: null,
        selectedRef: '',
      })
      await this.reload()
    } catch (error) {
      this.set({ busy: '', error: message(error) })
    }
  }

  async reloadProjects(): Promise<void> {    const face = this.state.face
    if (face === null) return
    try {
      const projects: ProjectSummary[] = await face.projects()
      const bootstrap = this.state.bootstrap
      if (bootstrap !== null) this.set({ bootstrap: { ...bootstrap, projects } })
    } catch (error) {
      this.set({ error: message(error) })
    }
  }

  /** 打开一个工程并拉快照（2D + 3D + 判定 + 预算 + 闸门一次往返）。 */
  async open(project: string): Promise<void> {
    const face = this.state.face
    if (face === null || project === '') return
    this.set({ project, loading: true, error: null, busy: '读取工程' })
    try {
      const snapshot = await face.snapshot(project)
      if (snapshot.status !== 'ok') {
        this.set({
          loading: false,
          busy: '',
          snapshot: null,
          error: (snapshot.findings ?? []).join('；') || '读不到工程快照',
        })
        return
      }
      const stillThere = snapshot.design.parts.some((p) => p.ref === this.state.selectedRef)
      this.set({
        snapshot,
        loading: false,
        busy: '',
        selectedRef: stillThere ? this.state.selectedRef : '',
      })
    } catch (error) {
      this.set({ loading: false, busy: '', snapshot: null, error: message(error) })
    }
  }

  async refresh(): Promise<void> {
    if (this.state.project !== '') await this.open(this.state.project)
  }

  // -- 交互 ---------------------------------------------------------------

  setLayout(layout: Layout): void {
    writeStoredString('layout', layout)
    this.set({ layout })
  }
  setPanel(panel: PanelId): void { this.set({ panel }) }

  /** 展开/收起工作区设置块（工具栏与空态共用这一个状态）。 */
  setWorkspaceOpen(open: boolean): void { this.set({ workspaceOpen: open }) }

  /**
   * 拉一次勘察摘要。**只读**（不传 write），不写初审.md、不写日志——
   * 界面刷新不该产生文件。用户点「初审工作区」才走 `hw_survey(write=true)`。
   */
  async loadSurvey(): Promise<void> {
    const face = this.state.face
    if (face === null) return
    try {
      const payload = await face.survey(false)
      const entries = (payload.entries ?? []) as Array<Record<string, unknown>>
      const counts = (payload.counts ?? {}) as Record<string, unknown>
      const coverage = (payload.coverage ?? {}) as Record<string, unknown>
      const mcus = Array.from(new Set(entries
        .map((row) => String(row.mcu ?? ''))
        .filter((name) => name !== '')))
        .slice(0, 6)
      this.set({
        survey: {
          workspace: String(payload.workspace ?? ''),
          entries: Number(counts.entries ?? entries.length),
          notAdopted: Number(counts.entries_not_adopted ?? 0),
          kinds: Object.keys((payload.entries_by_kind ?? {}) as Record<string, unknown>),
          mcus,
          readFiles: Number(coverage.read_files ?? 0),
          totalFiles: Number(coverage.total_files ?? 0),
          fingerprint: String(payload.fingerprint ?? ''),
          logsDir: `${String(payload.workspace ?? '')}\\logs`,
        },
      })
    } catch {
      // 勘察失败不打扰用户：评审仍可进行，只是少了工作区摘要。
      this.set({ survey: null })
    }
  }

  /**
   * 尺寸 setter 自己 clamp：把手也 clamp，但面板/工具/AI 走的是 store，
   * 只信调用方就等于把 -50px 或 9999px 写进 localStorage（下次启动读到它，
   * 界面就烂在一个没人负责的值上）。
   */
  setSplitPct(value: number): void {
    const clamped = Math.max(SPLIT_MIN, Math.min(SPLIT_MAX, value))
    writeStored('splitPct', clamped)
    this.set({ splitPct: clamped })
  }
  setInspectorWidth(value: number): void {
    const clamped = Math.max(INSPECTOR_MIN, Math.min(INSPECTOR_MAX, value))
    writeStored('inspectorWidth', clamped)
    this.set({ inspectorWidth: clamped })
  }
  setBottomHeight(value: number): void {
    const clamped = Math.max(BOTTOM_MIN, Math.min(BOTTOM_MAX, value))
    writeStored('bottomHeight', clamped)
    this.set({ bottomHeight: clamped })
  }

  /** 3D 视图三工具：state 住这里，组件卸载/重挂不丢（见 HwState 注释）。 */
  setExplode(value: boolean): void {
    writeStoredString('view3d.explode', value ? '1' : '0')
    this.set({ explode: value })
  }
  setSection(value: boolean): void {
    writeStoredString('view3d.section', value ? '1' : '0')
    this.set({ section: value })
  }
  setGrid3d(value: boolean): void {
    writeStoredString('view3d.grid', value ? '1' : '0')
    this.set({ grid3d: value })
  }

  select(ref: string): void {
    this.set({ selectedRef: ref === this.state.selectedRef ? '' : ref })
  }

  clearNotice(): void { this.set({ notice: null, error: null }) }

  // -- 编辑 ---------------------------------------------------------------

  private async edit<T>(label: string, action: (face: WorkbenchFace,
                                                project: string) => Promise<T>): Promise<void> {
    const face = this.state.face
    const project = this.state.project
    if (face === null || project === '') return
    this.set({ busy: label, error: null })
    try {
      const result = await action(face, project)
      const status = (result as { status?: string } | null)?.status
      if (status === 'failed' || status === 'not_ready') {
        const lines = (result as { findings?: string[] }).findings ?? []
        this.set({ busy: '', error: lines.join('；') || `${label}失败` })
        return
      }
      this.set({ busy: '' })
      await this.open(project)
      await this.reloadProjects()
    } catch (error) {
      this.set({ busy: '', error: message(error) })
    }
  }

  movePart(ref: string, xMm: number, yMm: number, rotationZ: number, zMm = 0): Promise<void> {
    return this.edit('移动元件', (face, project) =>
      face.movePart(project, ref, xMm, yMm, rotationZ, zMm))
  }

  addPart(ref: string, kind: string, role: string, xMm: number, yMm: number): Promise<void> {
    return this.edit('添加元件', (face, project) =>
      face.addPart(project, ref, kind, role, xMm, yMm))
  }

  deletePart(ref: string): Promise<void> {
    return this.edit('删除元件', (face, project) => face.deletePart(project, ref))
  }

  setPart(ref: string, patch: Record<string, unknown>): Promise<void> {
    return this.edit('修改元件', (face, project) => face.setPart(project, ref, patch))
  }

  // -- 动作 ---------------------------------------------------------------

  runCheck(): Promise<void> {
    return this.edit('运行物理检查', async (face, project) => {
      const result = await face.check(project)
      this.set({ panel: 'checks' })
      return result
    })
  }

  runBudget(): Promise<void> {
    return this.edit('评估预算', async (face, project) => {
      const result = await face.budget(project)
      this.set({ panel: 'budget' })
      return result
    })
  }

  runCrosscheck(): Promise<void> {
    return this.edit('交叉核对', async (face, project) => {
      const result = await face.crosscheck(project)
      this.set({ panel: 'crosscheck' })
      return result
    })
  }

  lockDecision(name: string, value: string): Promise<void> {
    return this.edit('闭合决策', async (face, project) => {
      const result = await face.deferred(project, 'lock', name, value)
      this.set({ panel: 'deferred' })
      return result
    })
  }

  exportBundle(): Promise<void> {
    return this.edit('导出制造包', async (face, project) => {
      const result = await face.exportBundle(project)
      this.set({ panel: 'export', lastExport: result })
      return result
    })
  }

  /** 跑一个 hwforge 积木（跨层判定）。产物由 hwforge 写进工程目录。 */
  runBlock(project: string, block: string, paramsJson: string): Promise<void> {
    return this.edit(`运行积木 ${block}`, async (face) => {
      let params: Record<string, unknown> = {}
      try {
        params = paramsJson.trim() === '' ? {} : JSON.parse(paramsJson) as Record<string, unknown>
      } catch {
        return { status: 'failed', findings: [`params 不是合法 JSON：${paramsJson}`] }
      }
      const result = await face.runBlock(project, block, params)
      this.set({ panel: 'blocks' })
      return result
    })
  }

  async setBudgetLimit(currency: string, value: number): Promise<void> {
    const face = this.state.face
    const project = this.state.project
    if (face === null || project === '') return
    this.set({ busy: '设置预算上限', error: null })
    try {
      await face.setBudgetLimits(project, { [currency]: value })
      this.set({ busy: '' })
      await this.open(project)
    } catch (error) {
      this.set({ busy: '', error: message(error) })
    }
  }

  /** 新建工程；成功后自动打开。 */
  async createProject(project: string, board: string, title: string): Promise<void> {
    const face = this.state.face
    if (face === null) return
    this.set({ busy: '新建工程', error: null })
    try {
      const result = await face.createProject(project, board, title)
      if ((result as { status?: string }).status === 'failed') {
        this.set({ busy: '', error: ((result as { findings?: string[] }).findings ?? []).join('；') })
        return
      }
      await this.reloadProjects()
      this.set({ busy: '' })
      await this.open(project)
    } catch (error) {
      this.set({ busy: '', error: message(error) })
    }
  }

  async importProject(path: string, bomPath = ''): Promise<void> {
    const face = this.state.face
    if (face === null) return
    const base = path.replace(/\\/g, '/').split('/').pop() ?? 'imported'
    const id = base.replace(/\.[^.]+$/, '').replace(/[^A-Za-z0-9_-]+/g, '-').slice(0, 48)
    if (id === '') {
      this.set({ error: '导入路径无法生成工程 id' })
      return
    }
    this.set({ busy: '导入工程', error: null })
    try {
      const result = await face.importProject(id, path, bomPath)
      if ((result as { status?: string }).status !== undefined
          && (result as { status?: string }).status !== 'ok') {
        this.set({
          busy: '',
          error: ((result as { findings?: string[] }).findings ?? []).join('；') || '导入失败',
        })
        return
      }
      await this.reloadProjects()
      this.set({ busy: '' })
      await this.open(id)
    } catch (error) {
      this.set({ busy: '', error: message(error) })
    }
  }
}

export const store = new HwStore()

/** React 绑定。 */
export function useHw(): HwState {
  return useSyncExternalStore(store.subscribe, store.getSnapshot, store.getSnapshot)
}
