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
  layout: 'split',
  selectedRef: '',
  panel: 'checks',
  hintSeq: 0,
  lastExport: null,
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
      if (keep !== '') await this.open(keep)
      else this.set({ snapshot: null, project: '' })
    } catch (error) {
      this.set({ loading: false, ready: true, error: message(error) })
    }
  }

  async reloadProjects(): Promise<void> {
    const face = this.state.face
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

  setLayout(layout: Layout): void { this.set({ layout }) }
  setPanel(panel: PanelId): void { this.set({ panel }) }

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
