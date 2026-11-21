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
  EntityProfile,
} from '../shared/types.js'
import type { WorkbenchFace } from './rpc.js'

/**
 * 底部面板的**分组**（3 个），不是原来的 8 个页签。
 *
 * 为什么合并：8 个平级页签要求用户先在一排同类控件里选一个，再去找那个页里
 * 的按钮；而"判定/预算/未决/核对"回答的其实是同一个问题（这东西有没有毛病），
 * "证据/制造"回答的是"交给别人"。分组后顶部只需 3 个入口，组内再用
 * 分段控件切换——认知负担从"8 选 1"降到"3 选 1，再 2~5 选 1"，且
 * 默认落在最有用的那个分区。
 */
export type PanelId = 'health' | 'delivery' | 'velxio'

/** 分组内的分区（保留原来的 8 个语义，一个都没丢）。 */
export type SectionId =
  | 'verdict'
  | 'params'
  | 'firmware'
  | 'req'
  | 'checks'
  | 'budget'
  | 'deferred'
  | 'crosscheck'
  | 'blocks'
  | 'export'
  | 'evidence'
  | 'velxio'
  | 'sim'

export type Layout = 'split' | '2d' | '3d'
const LAYOUTS = ['split', '2d', '3d'] as const

// -- 分组 / 分区 的结构化定义（单一事实源，UI 与 store 都读它）-----------

/** 每个分组里有哪几个分区，顺序即分段控件的顺序。 */
export const SECTIONS: Readonly<Record<PanelId, readonly SectionId[]>> = {
  health: ['verdict', 'params', 'checks', 'budget', 'deferred', 'crosscheck', 'blocks'],
  delivery: ['export', 'firmware', 'req', 'evidence'],
  velxio: ['velxio', 'sim'],
}

export const GROUP_LABEL: Readonly<Record<PanelId, string>> = {
  health: '体检', delivery: '交付', velxio: 'Velxio',
}

export const SECTION_LABEL: Readonly<Record<SectionId, string>> = {
  verdict: '结论', params: '参数', checks: '判定', budget: '预算', deferred: '未决', crosscheck: '核对',
  blocks: '积木', export: '制造', firmware: '固件', req: '需求', evidence: '证据/轨迹',
  velxio: '后端', sim: '仿真',
}

/** 每个分组的默认分区 —— 打开就该看到最常用的那个。 */
export const DEFAULT_SECTION: Readonly<Record<PanelId, SectionId>> = {
  health: 'verdict', delivery: 'export', velxio: 'velxio',
}

/** 是不是一个已知分区。**别猜**：host 的 `view` 是自由字符串。 */
export function isSectionId(value: string): boolean {
  return (Object.values(SECTIONS) as readonly (readonly string[])[])
    .some((list) => list.includes(value))
}

/** 分区属于哪个分组。由 `SECTIONS` 反推，避免两处各写一份对不齐。 */
export function groupOf(section: SectionId): PanelId {
  for (const group of Object.keys(SECTIONS) as PanelId[]) {
    if (SECTIONS[group].includes(section)) return group
  }
  return 'health'
}

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
  /**
   * 要聚焦的设计字段路径（如 `electrical.power_w`）。
   *
   * 「下一步」按钮（P1）用它把用户送到 Inspector 里**那个输入框**，
   * 而不只是选中元件。空串 = 不聚焦。
   * 用完不清空：人可能再点一次同一个字段。
   */
  readonly focusField: string
  /**
   * 选中元件的完整画像（P2 按对象聚合）。**按需加载**——
   * 快照里只有一行摘要，正文走 `entity` RPC，否则 316 个件会把快照撑爆。
   */
  readonly entity: EntityProfile | null
  readonly entityLoading: boolean
  /** 当前分组里的分区（与 3D 的 `section` 剖面开关**不同**，故用 panelSection）。 */
  readonly panelSection: SectionId
  readonly hintSeq: number
  readonly lastExport: ExportPayload | null
  /**
   * Velxio 后端状态。
   *
   * `null` = 还没读（面板显示"正在读…"），**不是**"不可用"。
   * 这个区别很重要：`velxioHealth` 这个端点一直存在却从没被调用过，
   * 于是面板上"后端没起"和"面板没查"长得一样。
   */
  readonly velxio: Record<string, unknown> | null
  /**
   * AVR 仿真状态与串口累积文本。
   *
   * `sim` 与 `velxio` 分开：前者是执行（avr8js），后者是编译服务。
   * 后端没起不影响仿真，反过来也一样——混成一个字段会让一个坏掉
   * 另一个也显示不出来。
   */
  readonly sim: Record<string, unknown> | null
  readonly serial: string
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
  panel: 'health',
  focusField: '',
  entity: null,
  entityLoading: false,
  panelSection: 'checks',
  hintSeq: 0,
  lastExport: null,
  velxio: null,
  sim: null,
  serial: '',
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
      // **工具跑完要把面板切到该看的那一页**。宿主早就把 `view` 带过来了
      // （`service.ts` 的 `hintState.view`），但客户端一直只读 `project`——
      // 于是 `viewHint(project, 'checks')` 那 14 处调用全是死代码，
      // 用户跑完 `hw_ladder` 还得自己点页签。
      //
      // 只认**已知的分区**：未知字符串忽略而不是猜。切分区同时切分组，
      // 否则会出现"选中了不在当前分组里的分区"这种错位状态。
      const view = hint.view
      if (typeof view === 'string' && view !== '' && isSectionId(view)) {
        if (view !== this.state.panelSection || groupOf(view) !== this.state.panel) {
          this.setPanelSection(view as SectionId)
          this.setPanel(groupOf(view as SectionId))
        }
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
      else this.set({ snapshot: null, project: '', entity: null })
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
  /**
   * 切分组。**同时把分区落到该组默认值**：只改 panel 不改 section 会让
   * 新组里出现一个不属于它的分区，渲染时找不到组件就变空白。
   */
  setPanel(panel: PanelId): void {
    const sections = SECTIONS[panel]
    const keep = sections.includes(this.state.panelSection)
    this.set({ panel, panelSection: keep ? this.state.panelSection : DEFAULT_SECTION[panel] })
  }

  /** 切分区。同时把分组对齐到分区所属的组。 */
  setPanelSection(id: SectionId): void {
    this.set({ panelSection: id, panel: groupOf(id) })
  }

  /** 深链：从工具/外部按分区名跳转（`checks` / `budget` / … / `sim`）。 */
  showSection(section: SectionId): void {
    this.setPanelSection(section)
  }

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

  /** 选中元件 + 指定要聚焦的字段（空串 = 只选中）。 */
  selectField(ref: string, field: string): void {
    this.set({ selectedRef: ref, focusField: field })
    void this.loadEntity(ref)
  }

  select(ref: string): void {
    const next = ref === this.state.selectedRef ? '' : ref
    this.set({ selectedRef: next })
    void this.loadEntity(next)
  }

  /**
   * 拉一个元件的画像。**失败不抛**：画布上点一下不该弹错误框，
   * 画像区自己显示"读不到"。
   */
  private async loadEntity(ref: string): Promise<void> {
    const face = this.state.face
    const project = this.state.project
    if (ref === '' || face === null || project === '') {
      this.set({ entity: null, entityLoading: false })
      return
    }
    this.set({ entityLoading: true })
    try {
      const profile = await face.entity(project, ref)
      // 用户可能在等待期间又点了别的件：**只认最后一次**
      if (this.state.selectedRef !== ref) return
      this.set({ entity: profile.status === 'ok' ? profile : null, entityLoading: false })
    } catch {
      if (this.state.selectedRef !== ref) return
      this.set({ entity: null, entityLoading: false })
    }
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
      this.set({ panel: 'health', panelSection: 'checks' })
      return result
    })
  }

  runBudget(): Promise<void> {
    return this.edit('评估预算', async (face, project) => {
      const result = await face.budget(project)
      this.set({ panel: 'health', panelSection: 'budget' })
      return result
    })
  }

  /** 交叉核对。保留单独入口：核对失败的含义和物理检查不同。 */
  runCrosscheck(): Promise<void> {
    return this.edit('交叉核对', async (face, project) => {
      const result = await face.crosscheck(project)
      this.set({ panel: 'health', panelSection: 'crosscheck' })
      return result
    })
  }

  /**
   * **一次跑完体检**：检查 + 预算 + 核对。
   *
   * 为什么合并：这三个按钮原来平排在同一行，用户每次都要点三次、看三个页。
   * 它们回答的是同一个问题（"这东西有没有毛病"），合成一个动作、一个结果页，
   * 是这次 IA 减法的核心。
   *
   * **逐步容错**：某一步 `not_ready`（例如"没有 claim 就无法评估预算"）
   * 不该让整轮体检中止——那是"这一项没数据"，不是"体检失败"。所以这里
   * 不走 `edit()`（它把 not_ready 也当失败并中止），而是逐项收集后给一条
   * 汇总。
   */
  async runHealth(): Promise<void> {
    const face = this.state.face
    const project = this.state.project
    if (face === null || project === '') return
    this.set({ busy: '跑体检', error: null, notice: null })
    const notes: string[] = []
    let failed = 0
    const steps: ReadonlyArray<readonly [string, () => Promise<Record<string, unknown>>]> = [
      ['检查', () => face.check(project) as unknown as Promise<Record<string, unknown>>],
      ['预算', () => face.budget(project) as unknown as Promise<Record<string, unknown>>],
      ['核对', () => face.crosscheck(project) as unknown as Promise<Record<string, unknown>>],
    ]
    for (const [name, run] of steps) {
      try {
        const result = await run()
        const status = String(result?.status ?? 'ok')
        if (status === 'ok') continue
        const detail = Array.isArray(result?.findings)
          ? (result.findings as unknown[]).map(String).join('；') : ''
        // `not_ready` 是"没数据"，`failed` 才是"出错"——两者都要说出来，
        // 但不能都算失败。
        notes.push(`${name}：${status === 'not_ready' ? '无法评估' : '出错'}`
          + (detail === '' ? '' : `（${detail}）`))
        if (status === 'failed') failed += 1
      } catch (error) {
        notes.push(`${name}：${message(error)}`)
        failed += 1
      }
    }
    const summary = failed > 0 ? `体检完成（${failed} 项出错）` : '体检完成'
    this.set({
      busy: '',
      error: null,
      notice: notes.length === 0 ? summary : `${summary} · ${notes.join(' · ')}`,
      panel: 'health',
      focusField: '',
  entity: null,
  entityLoading: false,
  panelSection: 'checks',
    })
    await this.open(project)
    await this.reloadProjects()
  }

  /** 应用需求草稿（P5）。**这里就是"人确认"那一下**，不做自动调用。 */
  applyReqDraft(): Promise<void> {
    return this.edit('应用需求草稿', async (face, project) => {
      const result = await face.applyReqDraft(project, true)
      this.set({ panel: 'delivery', panelSection: 'req' })
      return result
    })
  }

  lockDecision(name: string, value: string): Promise<void> {
    return this.edit('闭合决策', async (face, project) => {
      const result = await face.deferred(project, 'lock', name, value)
      this.set({ panel: 'health', panelSection: 'deferred' })
      return result
    })
  }

  exportBundle(): Promise<void> {
    return this.edit('导出制造包', async (face, project) => {
      const result = await face.exportBundle(project)
      this.set({ panel: 'delivery', panelSection: 'export', lastExport: result })
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
      this.set({ panel: 'health', panelSection: 'blocks' })
      return result
    })
  }

  /**
   * 读 Velxio 后端状态。**失败不抛**：读不到就是读不到，面板显示错误即可，
   * 不能因为"后端没起"把整个工作台打进错误态——检查/预算/导出都不依赖它。
   */
  async refreshVelxio(): Promise<void> {
    const face = this.state.face
    if (face === null) return
    try {
      const payload = await face.velxioHealth()
      this.set({ velxio: payload })
    } catch (error) {
      this.set({
        velxio: {
          status: 'failed',
          reachable: false,
          findings: [error instanceof Error ? error.message : String(error)],
        },
      })
    }
  }

  /** 启动/停止 Velxio 后端。动作本身走宿主 RPC → Python `serve` 子命令。 */
  controlVelxio(action: 'start' | 'stop'): Promise<void> {
    return this.edit(action === 'start' ? '启动 Velxio 后端' : '停止 Velxio 后端',
      async (face) => {
        const payload = await face.velxioControl(action)
        await this.refreshVelxio()
        this.set({ panel: 'velxio', panelSection: 'velxio' })
        return payload
      })
  }

  // -- AVR 仿真（宿主 Node 内 avr8js）------------------------------------

  /**
   * 起仿真会话。
   *
   * hex 从**编译产物**读：`<工程>/velxio_compile.json` 的 `data.hex_content`。
   * 面板没有文件系统权限，读盘这件事交给宿主（`simStart` 收到的是 hex 文本，
   * 由 invoker 负责取）。这里先把 snapshot 里能拿到的板卡传下去。
   */
  async startSim(): Promise<void> {
    const face = this.state.face
    const project = this.state.project
    if (face === null || project === '') return
    this.set({ busy: '启动仿真', error: null, notice: null })
    try {
      // 板卡：优先工程自己的 board.kind，退回 uno。
      const boardKind = this.state.snapshot?.design?.board?.kind ?? 'arduino:avr:uno'
      const fqbn = boardKind.includes(':') ? boardKind : `arduino:avr:${boardKind.replace('arduino-', '')}`
      // hex：由宿主从 velxio_compile.json 读；面板传空串表示"你去取"。
      const payload = await face.simStart(project, fqbn, '')
      const status = String(payload.status ?? 'failed')
      if (status !== 'ok') {
        const findings = Array.isArray(payload.findings) ? payload.findings.map(String) : []
        this.set({ busy: '', error: findings.join('；') || '仿真起不来' })
        return
      }
      this.set({ busy: '', sim: payload, serial: '', panel: 'velxio', panelSection: 'sim' })
    } catch (error) {
      this.set({ busy: '', error: message(error) })
    }
  }

  /** 推进仿真并把新串口文本追加进面板缓冲。 */
  async runSim(): Promise<void> {
    const face = this.state.face
    const project = this.state.project
    if (face === null || project === '') return
    this.set({ busy: '推进仿真', error: null })
    try {
      const payload = await face.simRun(project)
      const fresh = typeof payload.serialText === 'string' ? payload.serialText : ''
      const merged = this.state.serial + fresh
      // 面板只留最近 32KB：终端不需要无限历史，而无上限会拖慢渲染。
      this.set({
        busy: '',
        sim: payload,
        serial: merged.length > 32768 ? merged.slice(-32768) : merged,
      })
    } catch (error) {
      this.set({ busy: '', error: message(error) })
    }
  }

  /** 只读串口（不推进 CPU）。 */
  async readSerial(): Promise<void> {
    const face = this.state.face
    const project = this.state.project
    if (face === null || project === '') return
    try {
      const payload = await face.serialRead(project)
      const fresh = typeof payload.text === 'string' ? payload.text : ''
      const merged = this.state.serial + fresh
      this.set({ serial: merged.length > 32768 ? merged.slice(-32768) : merged })
    } catch (error) {
      this.set({ error: message(error) })
    }
  }

  async stopSim(): Promise<void> {
    const face = this.state.face
    const project = this.state.project
    if (face === null || project === '') return
    try {
      const payload = await face.simStop(project)
      this.set({ sim: { ...payload, running: false } })
    } catch (error) {
      this.set({ error: message(error) })
    }
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
