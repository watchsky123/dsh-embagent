/**
 * 「硬件工作台」—— IDE 外壳。
 *
 * 布局（v3 §5 + 前端方案 F2/F3）：
 *
 *   ┌ 工具栏（两段）：工程▾＋状态灯 │ 检查/预算/核对/导出 … 工作区▸ 视图组 AI评审 ┐
 *   ├ 2D 电路（电气权威） ⇹ 3D 装配（物理权威） ⇹ 检查器速览（宽可拖） ┤
 *   ├───────────────────────────── ⇊（高可拖）─────────────────────────────┤
 *   └ 底部：判定 / 预算 / 未决 / 核对 / 证据 / 制造 / 积木 ┘
 *
 * 三条纪律来自 Velxio 的实测行为：
 *
 * 1. **隐藏即卸载**：不在当前布局里的 pane **整个不渲染**，不是 `display:none`。
 *    3D 的 rAF 循环在隐藏副本里照跑（Velxio 点名的 "hidden copy still keeps
 *    its timer running"），2D 虽无循环，两者也走同一条规则，行为可预测。
 *    **代价如实说明**：切回来时视图重新自动取景——2D 的缩放/平移与 3D 的
 *    旋转角度不保留。视图工具（爆炸/剖面/网格）、选中、布局尺寸**保留**
 *    （它们存在 store，不存在组件里）。
 * 2. **尺寸可拖且被记住**：分栏 % / 检查器 px / 底部 px 三对把手，值在
 *    store（localStorage）；拖动中只走本地预览 state，松手才提交——
 *    每像素一次 localStorage 写是把功能做成性能事故。
 * 3. **一个状态两个视图**：selectedRef 仍由 store 持有；3D 的
 *    爆炸/剖面/网格也上收到 store（组件卸载不丢）。
 *
 * @module dsh-hw-velxio/client/HwIdeView
 */

import { useEffect, useRef, useState } from 'react'
import { Assembly3D } from './Assembly3D.js'
import { Circuit2D } from './Circuit2D.js'
import { Inspector } from './Inspector.js'
import {
  BlocksPanel, BriefPanel, BudgetPanel, ChecksPanel, CrosscheckPanel,
  DeferredPanel, EmitPanel, EvidencePanel, ExportPanel, ExportResult,
  FirmwarePanel, ParamsPanel, ReqPanel, SimPanel, VelxioPanel,
  VerdictPanel,
} from './panels.js'
import type { AvrSimStatus, VelxioStatus } from './panels.js'
import type { WorkbenchFace } from './rpc.js'
import { ResizeHandle } from './ResizeHandle.js'
import {
  BOTTOM_MAX, BOTTOM_MIN, DEFAULT_SECTION, GROUP_LABEL, INSPECTOR_MAX, INSPECTOR_MIN,
  SECTIONS, SECTION_LABEL, SPLIT_MAX, SPLIT_MIN, store, useHw,
} from './store.js'
import type { Layout, PanelId, SectionId } from './store.js'
import { composeReviewPrompt } from './view.js'

export interface HwIdeViewProps {
  readonly sessionId: string
  readonly face: WorkbenchFace
  readonly ai: { send: (text: string) => void }
  /** DSH 当前工作目录（会话的 workingDirectory 投影）；空串=读不到。 */
  readonly cwd: string
}

/**
 * 底部面板的**三个分组**。原来这里是 8 个平级页签。
 *
 * 合并逻辑：判定/预算/未决/核对/积木 都回答"这东西有没有毛病"→ 体检；
 * 制造/证据 是"交给别人"→ 交付；Velxio 是运行态 → 单独一组。
 * 组内再用分段控件切分区（见下方渲染），所以**功能一个没少**，
 * 只是顶部从"8 选 1"降到"3 选 1"。
 */
const PANELS: Array<{ id: PanelId; label: string; sections: readonly SectionId[] }> = [
  { id: 'health', label: GROUP_LABEL.health, sections: SECTIONS.health },
  { id: 'delivery', label: GROUP_LABEL.delivery, sections: SECTIONS.delivery },
  { id: 'velxio', label: GROUP_LABEL.velxio, sections: SECTIONS.velxio },
]

/** 工作区来源的可读标签（RPC 的 source 枚举 → 人话）。 */
const SOURCE_LABEL: Record<string, string> = {
  env: '环境变量', config: '已选择', default: '默认',
}

/** 阶段标签与徽章类。用**显式映射**而不是 ``phase-${x}`` 拼接：
 *  拼接出来的类名连人带脚本都搜不到（死样式检查当场抓到了这个问题）。 */
const PHASE_LABEL: Record<string, { text: string; cls: string }> = {
  empty: { text: '空工程', cls: 'phase-empty' },
  partial: { text: '半成品', cls: 'phase-partial' },
  complete: { text: '成品', cls: 'phase-complete' },
  broken: { text: '读不出', cls: 'phase-broken' },
}

/** 视图组的三个图标（13px stroke，同 Velxio 的 24-viewBox 线性风格）。 */
const VIEW_ICONS: Record<Layout, { d: string; label: string }> = {
  '2d': { d: 'M4 4h16v16H4zM4 10h16M10 10v10', label: '2D' },
  split: { d: 'M3 3h7v18H3zM14 3h7v18h-7z', label: '并排' },
  '3d': { d: 'M12 3l8 4.5v9L12 21l-8-4.5v-9L12 3zM12 12l8-4.5M12 12v9M12 12L4 7.5', label: '3D' },
}

export function HwIdeView(props: HwIdeViewProps): JSX.Element {
  const state = useHw()
  const panesRef = useRef<HTMLDivElement | null>(null)
  // 拖动中的实时预览值（本地）。null = 没在拖；松手 commit 进 store 并清空。
  const [liveSplit, setLiveSplit] = useState<number | null>(null)
  const [liveInspector, setLiveInspector] = useState<number | null>(null)
  const [liveBottom, setLiveBottom] = useState<number | null>(null)
  /** 「已发送」提示：按钮点完立即给反馈（上一版点完界面上什么都没变）。 */
  const [aiSent, setAiSent] = useState(false)
  /** 对话框输入的当前需求文本。 */
  const [aiRequest, setAiRequest] = useState('')
  /** 对话框展开态。收起时不渲染（与工作区设置同一条规则）。 */
  const [aiOpen, setAiOpen] = useState(false)

  useEffect(() => {
    void store.init(props.face)
    return () => { store.dispose() }
  }, [props.face])

  // 切到 Velxio 页签时读一次后端状态。
  //
  // **只在打开时读，不轮询**：后端启动要几秒到十几秒，轮询会让面板不停
  // 发 RPC；而用户想知道的"现在通不通"点一下"刷新"就够了。依赖里放
  // `state.panel` 而不是 `state.velxio`，避免刷新后自己再触发一次。
  useEffect(() => {
    if (state.panel === 'velxio' && state.velxio === null) {
      void store.refreshVelxio()
    }
  }, [state.panel, state.velxio])

  // 键盘切分区：`[` / `]`。**输入框里不抢键**——在需求草稿里打方括号
  // 是正常输入，不是快捷键。
  useEffect(() => {
    const onKey = (event: KeyboardEvent): void => {
      const target = event.target as HTMLElement | null
      if (target !== null
          && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA'
            || target.isContentEditable)) return
      if (event.key !== '[' && event.key !== ']') return
      const list = SECTIONS[state.panel]
      const at = list.indexOf(state.panelSection)
      const next = list[(at + (event.key === ']' ? 1 : -1) + list.length) % list.length]
      if (next !== undefined) store.showSection(next)
      event.preventDefault()
    }
    globalThis.addEventListener('keydown', onKey)
    return () => { globalThis.removeEventListener('keydown', onKey) }
  }, [state.panel, state.panelSection])

  // DSH 当前工作目录随会话变化（用户切了项目文件夹）。**渲染期同步**，
  // 不等 effect：这个值决定"新建工作区"落在哪，首帧点"新建"就必须已经就位。
  // `setDshCwd` 内部做了等值判断，只在真的变化时才广播，因此不会自激。
  if (props.cwd !== '' && props.cwd !== state.dshCwd) store.setDshCwd(props.cwd)

  const snapshot = state.snapshot
  const findings = snapshot?.checks.findings ?? []
  const verdict = snapshot?.verdict
  const failedLevels = (verdict?.levels ?? []).filter((l) => l.status === 'fail').length
  const blockingTotal = verdict?.counts?.blocking ?? 0
  const failed = findings.filter((f) => !f.ok)
  const errors = failed.filter((f) => f.severity === 'error')
  const warnings = failed.filter((f) => f.severity === 'warning')
  const deferred = snapshot?.deferred
  const openDecisions = deferred?.open_irreversible ?? []
  const mismatches = snapshot?.crosscheck.summary.mismatches ?? 0
  const budgetOver = (snapshot?.budget.violations ?? []).length
  // 三态口径：通过 / 失败（error）/ 警告（warning）/ 未验证。
  // 徽章只显示"阻断+失败"会把缺数据藏起来（AGENTS §0.2 在前端的版本），
  // 所以下面 `sectionCount('verdict')` 把 unknown 也算进警示色。
  const passedChecks = findings.filter((f) => f.ok).length
  const unknownChecks = snapshot?.checks.not_verified?.length ?? 0
  const projection = snapshot?.projection2d
  /** 板级汇总：每块板多少件、多少 error/warning、第一个问题位号（下钻用）。 */
  const boardRows = (projection?.boards ?? []).map((board) => {
    const own = (projection?.parts ?? []).filter((part) => part.board_id === board.id)
    const refs = new Set(own.map((part) => part.ref))
    const bad = findings.filter((finding) => !finding.ok
      && (finding.refs ?? []).some((ref) => refs.has(ref)))
    return {
      id: board.id,
      name: board.name !== '' ? board.name : board.id,
      parts: own.length > 0 ? own.length : board.parts,
      errors: bad.filter((finding) => finding.severity === 'error').length,
      warnings: bad.filter((finding) => finding.severity === 'warning').length,
      firstRef: bad[0]?.refs?.[0] ?? '',
    }
  })

  const splitPct = liveSplit ?? state.splitPct
  const inspectorWidth = liveInspector ?? state.inspectorWidth
  const bottomHeight = liveBottom ?? state.bottomHeight

  /**
   * 一个入口，三个阶段（P3）。
   *
   * `request` 为空 = 「AI 评审」（把确定性判定交给会话解释）；非空 = 用户的
   * 具体需求，同样进同一条会话，但要求模型按需调工具改工作区。
   * 两者都不是"第二个判定器"：判定永远来自检查器，这里只是把上下文递过去。
   */
  const ask = (request?: string): void => {
    if (snapshot === null) {
      // 没有工程时也能问：让 AI 去看工作区（此刻最该做的事）。
      props.ai.send(request !== undefined && request.trim() !== ''
        ? request.trim()
        : '请用 hw_survey 看一下当前工作区有哪些工程、各自处于什么阶段，'
          + '并把结论写进初审.md 的判断段。')
      setAiSent(request !== undefined && request.trim() !== '')
      return
    }
    const prompt = composeReviewPrompt({
      project: snapshot.project.id,
      board: snapshot.design.board.name,
      // **全部** finding 都送：只送失败项时，"9 条全过"会送出空清单，
      // 模型根本不知道板子上有几个元件。
      verdicts: findings.map((finding) => ({
        check: finding.check,
        ok: finding.ok,
        measured: finding.measured,
        limit: finding.limit,
        message: finding.message,
      })),
      vacuous: snapshot.checks.vacuous ?? [],
      phase: snapshot.phase === undefined ? undefined : {
        phase: snapshot.phase.phase,
        reason: snapshot.phase.reason,
        veto: snapshot.phase.veto,
      },
      notVerified: [
        ...snapshot.checks.not_verified,
        ...(snapshot.budget.confidence === 'none'
          ? ['预算：没有任何 claim，无法评估'] : []),
        ...(openDecisions.length > 0
          ? [`未闭合的不可逆决策：${openDecisions.join(', ')}`] : []),
      ],
      request,
      workspace: state.survey === null ? undefined : {
        path: state.survey.workspace,
        entries: state.survey.entries,
        notAdopted: state.survey.notAdopted,
        kinds: state.survey.kinds,
        readFiles: state.survey.readFiles,
        totalFiles: state.survey.totalFiles,
        mcus: state.survey.mcus,
      },
    })
    props.ai.send(prompt)
    setAiSent(true)
  }

  /**
   * 分区级计数（数字只在**分段控件**上显示）。
   *
   * 为什么数字不放在分组页签上：一个分组里有 5 个分区，"3" 究竟是 3 个错误
   * 还是 3 个未决？聚合数字必然含混。分组层只给一颗红/黄点表示"这组里有
   * 问题"，具体数字给到分区。
   */
  const sectionCount = (id: SectionId): { text: string; cls: string } | null => {
    if (id === 'verdict') {
      // 口径：**阻断 + 失败层 = 红**；**未验证/缺数据 = 黄**。
      // 不把"默认值/常驻缺数据"当成红（真实设计里常驻，红色会失效），
      // 但也不能像旧版那样完全不显示——那会让"没验过"看起来像"通过"。
      const fails = blockingTotal + failedLevels
      const unknown = unknownChecks + (verdict?.counts?.['unknown'] ?? 0)
      if (fails > 0) return { text: String(fails), cls: 'err' }
      if (unknown > 0) return { text: String(unknown), cls: 'warn' }
      if (verdict === undefined || verdict.rollup === 'unknown'
        || verdict.rollup === 'not_ready') return { text: '?', cls: 'warn' }
      return null
    }
    if (id === 'checks' && errors.length + warnings.length > 0) {
      return {
        text: String(errors.length + warnings.length),
        cls: errors.length > 0 ? 'err' : 'warn',
      }
    }
    if (id === 'budget' && budgetOver > 0) return { text: String(budgetOver), cls: 'err' }
    if (id === 'deferred' && openDecisions.length > 0) {
      return { text: String(openDecisions.length), cls: 'warn' }
    }
    if (id === 'crosscheck' && mismatches > 0) return { text: String(mismatches), cls: 'warn' }
    return null
  }

  /** 分组级：组内任一分区有问题就给一颗点（取最严重的颜色）。 */
  const groupIssue = (group: PanelId): 'err' | 'warn' | null => {
    let worst: 'err' | 'warn' | null = null
    for (const section of SECTIONS[group]) {
      const count = sectionCount(section)
      if (count === null) continue
      if (count.cls === 'err') return 'err'
      worst = 'warn'
    }
    return worst
  }

  // 状态灯：一眼回答"能不能交付"。红=错误，黄=警告/未决，绿=可交付。
  const dot = errors.length > 0
    ? { cls: 'err', text: `${errors.length} 个错误` }
    : warnings.length > 0 || openDecisions.length > 0
      ? { cls: 'warn', text: warnings.length > 0
        ? `${warnings.length} 个警告${openDecisions.length > 0 ? `，${openDecisions.length} 项未决` : ''}`
        : `可交付，但 ${openDecisions.length} 项未闭合` }
      : { cls: 'ok', text: '检查通过，可交付' }

  const show2d = state.layout !== '3d'
  const show3d = state.layout !== '2d'

  /**
   * 「有工程但读不到」：主区会自己把 `state.error` 显示出来，顶部横幅因此
   * **让位**——同一句话在屏幕上出现两遍，是这一轮一直在清理的那类重复。
   */
  const unavailable = snapshot === null
    && state.project !== ''
    && (state.bootstrap?.projects ?? []).length > 0

  /**
   * BOM 补全的工程要**在标题上**说清楚：元件是物料清单，位置是网格排布。
   *
   * 不这么标的话，用户看到一板整齐的元件会以为那是真实布局，照着去布线
   * —— 比不显示更糟。这句话来自 `meta.bom_fill.placement`（后端写的），
   * 前端不自己猜。
   */
  const bomFill = (snapshot?.design.meta?.['bom_fill'] ?? null) as
    { placement?: string; added?: number } | null
  const layoutNote = bomFill?.placement === 'grid-estimate'
    ? `BOM 清单（${bomFill.added ?? 0} 个元件 · 位置为自动排布）`
    : ''

  return (
    <div className="hwv-root">
      {/* ── 工具栏（两段 + 右段）────────────────────────────────────────── */}
      <div className="hwv-toolbar">
        <div className="hwv-tb-left">
          <span className="hwv-title">硬件工作台</span>
          <span className={`hwv-dot ${dot.cls}`} title={dot.text}
                role="status" aria-label={dot.text} />
          <select className="hwv-select" value={state.project}
                  onChange={(event) => { void store.open(event.target.value) }}
                  disabled={state.bootstrap === null}
                  aria-label="选择工程">
            {(state.bootstrap?.projects ?? []).length === 0
              ? <option value="">（还没有工程）</option>
              : (state.bootstrap?.projects ?? []).map((project) => (
                <option key={project.id} value={project.id}>
                  {project.id} — {project.board_name ?? project.board ?? ''}
                </option>
              ))}
          </select>
        </div>

        {/* 工具栏只有两个动作：**跑体检**（一次跑完检查/预算/核对）与**导出**。
            「检查/预算/核对」原本是三个平排按钮，但它们回答同一个问题，
            分开摆只是让人多点两次。AI 协作移到右侧抽屉（见 `aiOpen`）。 */}
        <div className="hwv-tb-actions">
          <button className="hwv-btn" disabled={snapshot === null || state.busy !== ''}
                  title="一次跑完：物理检查 + 五轨预算 + 交叉核对"
                  onClick={() => { void store.runHealth() }}>跑体检</button>
          <button className="hwv-btn primary"
                  disabled={snapshot === null || state.busy !== '' || openDecisions.length > 0}
                  title={openDecisions.length > 0 ? '不可逆决策未闭合，导出被闸门拦住' : '导出制造包'}
                  onClick={() => { void store.exportBundle() }}>导出</button>
        </div>

        <span className="hwv-spacer" />

        <div className="hwv-tb-right">
          {/* AI 协作：**抽屉**，不再是和"导出"同一排的按钮。
              它是"问一个问题"的入口，和"执行一个动作"不同级；平排会误触。 */}
          <button className={`hwv-btn hwv-ai-toggle${aiOpen ? ' on' : ''}`}
                  aria-expanded={aiOpen}
                  title="AI：评审 / 初审工作区 / 描述你想改什么"
                  onClick={() => { setAiOpen(!aiOpen); setAiSent(false) }}>
            AI
          </button>
          {/* 工作区：**唯一的入口**，两种状态下都在这里。已设时它就是一条
              可点的路径（点击展开设置块）；设置块本身在主区上方渲染一份，
              不随状态改变位置——"同一个东西在两种状态下长得不一样"是
              上一版四张卡的病根。 */}
          <button className={`hwv-btn hwv-ws-toggle${state.workspaceOpen ? ' on' : ''}`}
                  aria-expanded={state.workspaceOpen}
                  title={state.workspace !== ''
                    ? `工作区：${state.workspace}（点击${state.workspaceOpen ? '收起' : '展开'}设置）`
                    : '设置工作区'}
                  onClick={() => { store.setWorkspaceOpen(!state.workspaceOpen) }}>
            <span className={`hwv-dot ${state.workspaceWritable ? 'ok' : 'err'}`} aria-hidden="true" />
            <span className="hwv-ws-path">
              {state.workspace !== '' ? state.workspace : '设置工作区'}
            </span>
          </button>
          {/* segmented 视图组：一个控件，aria-pressed（仿 Velxio view-mode toggle） */}
          <div className="hwv-seg" role="group" aria-label="视图布局">
            {(['2d', 'split', '3d'] as const).map((layout, index) => (
              <ViewModeSegment key={layout} index={index}
                               layout={layout} active={state.layout === layout} />
            ))}
          </div>
          {snapshot !== null && (
            <>
              {/* 阶段徽章：**确定性推导**，与"检查通过"是两件事。
                  空设计会 9 项全过，徽章必须把这件事说出来。 */}
              {snapshot.phase !== undefined && (
                <span className={`hwv-chip ${PHASE_LABEL[snapshot.phase.phase]?.cls ?? 'phase-partial'}`}
                      title={[snapshot.phase.reason,
                        ...snapshot.phase.veto].filter((x) => x !== '').join('\n')}>
                  {PHASE_LABEL[snapshot.phase.phase]?.text ?? snapshot.phase.phase}
                </span>
              )}
              <span className={`hwv-chip ${errors.length > 0 ? 'err' : warnings.length > 0 ? 'warn' : 'ok'}`}>
                {errors.length > 0 ? `${errors.length} 错误`
                  : warnings.length > 0 ? `${warnings.length} 警告` : '检查通过'}
              </span>
              {(snapshot.checks.vacuous ?? []).length > 0 && (
                <span className="hwv-chip est"
                      title={`这些"通过"是因为没有可检查的对象，不是真的核对过：\n`
                        + (snapshot.checks.vacuous ?? []).join('\n')}>
                  {(snapshot.checks.vacuous ?? []).length} 项空集通过
                </span>
              )}
              <span className={`hwv-chip ${openDecisions.length > 0 ? 'warn' : 'ok'}`}>
                {openDecisions.length > 0 ? `未决 ${openDecisions.length}` : '可交付'}
              </span>
              {snapshot.budget.confidence === 'none' && (
                <span className="hwv-chip est">预算无可评数据</span>
              )}
            </>
          )}
        </div>
      </div>

      {/* ── 板级汇总（P1）：每块板自己的三态 + 第一个问题位号（点击下钻）──
          多板工程只看总数会掩盖"哪块板坏了"；单板也保留——它是"板"这个
          单位的唯一入口。数据全部来自快照（projection2d.parts.board_id），
          前端不重新判定，只按 refs 聚合。 */}
      {snapshot !== null && boardRows.length > 0 && (
        <div className="hwv-boardstrip" role="list" aria-label="板级汇总">
          <span className="hwv-boardstrip-label">板</span>
          {boardRows.map((board) => (
            <button key={board.id} role="listitem" type="button"
                    className={`hwv-board-chip${board.errors > 0 ? ' err'
                      : board.warnings > 0 ? ' warn' : ''}`}
                    title={`${board.name}：${board.parts} 件 · 错误 ${board.errors}`
                      + ` · 警告 ${board.warnings}（点击看这板的判定）`}
                    onClick={() => { store.openSection('checks', board.firstRef) }}>
              <span>{board.name}</span>
              <span className="hwv-board-parts">{board.parts} 件</span>
              {board.errors > 0 && <span className="n err">✕{board.errors}</span>}
              {board.warnings > 0 && <span className="n warn">!{board.warnings}</span>}
            </button>
          ))}
          <span className="hwv-boardstrip-counts">
            <span>通过 <b>{passedChecks}</b></span>
            <span className={errors.length > 0 ? 'n err' : ''}>失败 <b>{errors.length}</b></span>
            <span className={warnings.length > 0 ? 'n warn' : ''}>警告 <b>{warnings.length}</b></span>
            <span className={unknownChecks > 0 ? 'n warn' : ''}>未验证 <b>{unknownChecks}</b></span>
          </span>
        </div>
      )}

      {state.error !== null && !unavailable && (
        <div className="hwv-banner">
          <span>{state.error}</span>
          <span className="hwv-spacer" />
          <button className="hwv-btn" onClick={() => { store.clearNotice() }}>关闭</button>
        </div>
      )}
      {state.busy !== '' && <div className="hwv-banner info">正在{state.busy}…</div>}
      <ExportResult result={state.lastExport} />

      {/* ── AI 协作面板：**一个入口，三个阶段** ──────────────────────────
          ⓵ 初审工作区 → hw_survey 写初审.md  ⓶ 适配当前工程 → 让它按 hw_put 改
          ⓷ 直接输入需求 → 进同一条会话，按需调工具。
          三者都往会话里发，初审.md 是它们之间的接力棒。
          收起时**不渲染**（与工作区设置同一条规则）。 */}
      {aiOpen && (
        <>
          {/* 抽屉遮罩：点外面关掉。没有它，抽屉打开后主区仍然可交互，
              容易在"我到底在操作哪一层"上迷失。 */}
          <div className="hwv-drawer-scrim" role="presentation"
               onClick={() => { setAiOpen(false) }} />
          <section className="hwv-ai hwv-drawer" role="dialog" aria-label="AI 协作">
          <div className="hwv-ai-row">
            <span className="hwv-field-label">AI 协作</span>
            <button className="hwv-btn" onClick={() => { ask(); setAiOpen(false) }}>
              评审当前工程
            </button>
            <button className="hwv-btn"
                    onClick={() => {
                      // 初审：先让工具生成事实段，再由 AI 写判断段。
                      // 不直接发一大段提示词——工具才有确定性。
                      props.ai.send(
                        '请调用 hw_survey（write=true）勘察当前工作区，'
                        + '它会刷新初审.md 的**事实段**（确定性生成，不要改它）。'
                        + '然后你只写初审.md 的「判断」段：每个工程处于什么阶段、'
                        + '最可能卡在哪、下一步该做什么。'
                        + '只引用事实段，没有事实支撑的请明确标为推测。')
                      setAiSent(true)
                      setAiOpen(false)
                    }}>
              初审工作区
            </button>
            <button className="hwv-btn"
                    disabled={snapshot === null}
                    title={snapshot === null ? '先打开一个工程' : '按初审与工程现状适配可视化'}
                    onClick={() => {
                      if (snapshot === null) return
                      props.ai.send(
                        `请把工程「${snapshot.project.id}」适配成「硬件工作台」能正常可视化的状态。`
                        + '\n约束：'
                        + '\n1) 先读工作区里的初审.md（若不存在或已过期，先调 hw_survey write=true）。'
                        + '\n2) 尺寸未知的元件**留 0 并标 physical_source=estimated**，'
                        + '不要编造 datasheet 数值——检查器会把估算值单列出来。'
                        + '\n3) 写入必须走 hw_put（走 DesignModel 校验）或 hw_setpart，'
                        + '不要用通用文件工具直接改 design.json。'
                        + '\n4) 半成品就按半成品可视化：缺高度按薄片（d=0）显示是可以接受的，'
                        + '但要如实保留 estimated/unsized 标记。'
                        + '\n5) 最后跑 hw_check 并把结果如实汇报（含 vacuous 与 not_verified）。')
                      setAiSent(true)
                      setAiOpen(false)
                    }}>
              适配当前工程
            </button>
            <span className="hwv-spacer" />
            {aiSent && (
              <span className="hwv-ai-sent" role="status">
                ✓ 已发到会话，切换回「对话」查看
              </span>
            )}
          </div>
          <div className="hwv-ai-row">
            <input className="hwv-input hwv-ai-input"
                   placeholder="描述你想改什么，例如：给 004 补上 BOM 里的元件，尺寸未知的先标估算"
                   aria-label="AI 需求"
                   value={aiRequest}
                   onChange={(event) => { setAiRequest(event.target.value); setAiSent(false) }}
                   onKeyDown={(event) => {
                     if (event.key === 'Enter' && aiRequest.trim() !== '') {
                       ask(aiRequest)
                       setAiRequest('')
                     }
                   }} />
            <button className="hwv-btn primary"
                    disabled={aiRequest.trim() === ''}
                    onClick={() => { ask(aiRequest); setAiRequest('') }}>
              发送
            </button>
          </div>
          </section>
        </>
      )}

      {/* ── 工作区设置块：唯一实现，主区上方一份；收起时**不渲染** ─────── */}
      {state.workspaceOpen && <WorkspaceSettings face={props.face} />}

      {/* ── 主区：panes ⇹ 检查器（宽度持久化可拖）──────────────────────── */}
      {snapshot === null
        ? (unavailable
            // 有工程、下拉里也选中了，但快照读失败——**这不是空态**。
            // 上一版只要 snapshot===null 就渲染「还没有工程」，于是出了
            // "下拉显示 示例-无人机、正文说还没有工程"的自相矛盾。
            ? <Unavailable project={state.project} error={state.error}
                           onRetry={() => { void store.open(state.project) }} />
            : <EmptyState ai={props.ai} />)
        : (
          <div className="hwv-main">
            <div className="hwv-panes" ref={panesRef}>
              {/* 2D：保持挂载（无循环），隐藏期间缩放/平移不丢 */}
              {show2d && (
                <div className="hwv-pane" style={state.layout === 'split'
                  ? { flex: `${splitPct} 1 0%` } : { flex: '1 1 0%' }}>
                  <div className="hwv-pane-head">
                    {layoutNote !== '' ? `2D · ${layoutNote}` : '2D 电路 · 电气/拓扑权威'}
                    <span className="hwv-spacer" />
                    <span className="hwv-dim">{snapshot.projection2d.parts.length} 元件</span>
                  </div>
                  <Circuit2D
                    snapshot={snapshot}
                    selectedRef={state.selectedRef}
                    findings={findings}
                    onSelect={(ref) => { store.select(ref) }}
                    onMove={(ref, x, y, rot) => { void store.movePart(ref, x, y, rot) }}
                  />
                </div>
              )}
              {state.layout === 'split' && (
                <ResizeHandle orientation="vertical" min={SPLIT_MIN} max={SPLIT_MAX}
                              value={splitPct}
                              title="拖动调整 2D/3D 分栏"
                              ariaLabel="2D 与 3D 分栏"
                              toValue={(base, deltaPx) => {
                                const width = panesRef.current?.clientWidth ?? 0
                                return width <= 0 ? base : base + (deltaPx / width) * 100
                              }}
                              onPreview={setLiveSplit}
                              onCommit={(v) => { setLiveSplit(null); store.setSplitPct(v) }} />
              )}
              {/* 3D：隐藏即卸载 —— 停止 rAF（Velxio 的"hidden copy timer"教训）。
                  代价如实说明：重新挂载会回到自动取景视角；视图工具
                  （爆炸/剖面/网格）状态存在 store，不随卸载丢失。 */}
              {show3d && (
                <div className="hwv-pane" style={{ flex: '1 1 0%' }}>
                  <div className="hwv-pane-head">
                    3D 装配 · 物理/机械权威
                    <span className="hwv-spacer" />
                    <span className="hwv-dim">{snapshot.scene3d.parts.length} 实体</span>
                  </div>
                  <Assembly3D
                    snapshot={snapshot}
                    selectedRef={state.selectedRef}
                    findings={findings}
                    onSelect={(ref) => { store.select(ref) }}
                    explode={state.explode}
                    section={state.section}
                    grid={state.grid3d}
                    onExplode={(v) => { store.setExplode(v) }}
                    onSection={(v) => { store.setSection(v) }}
                    onGrid={(v) => { store.setGrid3d(v) }}
                  />
                </div>
              )}
            </div>

            <ResizeHandle orientation="vertical" min={INSPECTOR_MIN} max={INSPECTOR_MAX}
                          value={inspectorWidth}
                          title="拖动调整检查器宽度"
                          ariaLabel="检查器宽度"
                          onPreview={setLiveInspector}
                          onCommit={(v) => { setLiveInspector(null); store.setInspectorWidth(v) }} />

            <Inspector
              snapshot={snapshot}
              width={inspectorWidth}
              selectedRef={state.selectedRef}
              findings={findings}
              focusField={state.focusField}
              entity={state.entity}
              onSelect={(ref) => { store.select(ref) }}
              onMove={(ref, x, y, rot, z) => { void store.movePart(ref, x, y, rot, z) }}
              onSetPart={(ref, patch) => { void store.setPart(ref, patch) }}
              onDelete={(ref) => { void store.deletePart(ref) }}
              onAdd={(ref, kind, role, x, y) => { void store.addPart(ref, kind, role, x, y) }}
            />
          </div>
        )}

      {/* ── 底部面板（高度持久化可拖）────────────────────────────────── */}
      {snapshot !== null && (
        <ResizeHandle orientation="horizontal" min={BOTTOM_MIN} max={BOTTOM_MAX}
                      value={bottomHeight}
                      title="拖动调整底部面板高度"
                      ariaLabel="底部面板高度"
                      onPreview={setLiveBottom}
                      onCommit={(v) => { setLiveBottom(null); store.setBottomHeight(v) }} />
      )}
      <div className="hwv-bottom" style={{ height: snapshot !== null ? bottomHeight : undefined, flexBasis: snapshot !== null ? bottomHeight : undefined }}>
        <div className="hwv-tabs">
          {PANELS.map((panel) => {
            const issue = groupIssue(panel.id)
            return (
              <button key={panel.id}
                      className={`hwv-tab${state.panel === panel.id ? ' active' : ''}`}
                      aria-pressed={state.panel === panel.id}
                      onClick={() => { store.setPanel(panel.id) }}>
                {panel.label}
                {issue !== null && <span className={`hwv-tab-dot ${issue}`} />}
              </button>
            )
          })}
        </div>
        {/* 组内分区：只有 2 个及以上时才显示分段控件（单分区分组不摆一排只有一项的按钮）。
            **没有工程时不渲染**：那时没有分区内容可切，多一排空按钮只会添乱，
            而且会和空态的「开始卡」页签争同一份 aria-selected 语义。 */}
        {snapshot !== null
          && (PANELS.find((p) => p.id === state.panel)?.sections.length ?? 0) > 1 && (
          <div className="hwv-subtabs" role="tablist" aria-label="分区">
            {(PANELS.find((p) => p.id === state.panel)?.sections ?? []).map((section) => {
              const count = sectionCount(section)
              return (
                <button key={section} role="tab"
                        aria-selected={state.panelSection === section}
                        className={`hwv-subtab${state.panelSection === section ? ' active' : ''}`}
                        onClick={() => { store.setPanelSection(section) }}>
                  {SECTION_LABEL[section]}
                  {count !== null && <span className={`count ${count.cls}`}>{count.text}</span>}
                </button>
              )
            })}
          </div>
        )}
        {snapshot === null
          ? <div className="hwv-body hwv-dim">先新建或导入一个工程。</div>
          : (
            <PanelBody
              state={state}
              snapshot={snapshot}
              onSelect={(ref) => { store.select(ref) }}
              onSelectField={(ref, field) => { store.selectField(ref, field) }}
              onExport={() => { void store.exportBundle() }}
            />
          )}
      </div>
    </div>
  )
}

/** 视图组里的一段：分隔线（非首段）+ 带图标的按下态按钮。 */
function ViewModeSegment(props: {
  layout: Layout
  active: boolean
  index: number
}): JSX.Element {
  const icon = VIEW_ICONS[props.layout]
  return (
    <>
      {props.index > 0 && <div className="hwv-seg-divider" />}
      <button className="hwv-seg-btn"
              aria-pressed={props.active}
              title={`视图：${icon.label}`}
              onClick={() => { store.setLayout(props.layout) }}>
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none"
             stroke="currentColor" strokeWidth="2" strokeLinecap="round"
             strokeLinejoin="round" aria-hidden="true">
          <path d={icon.d} />
        </svg>
        <span className="vm-label">{icon.label}</span>
      </button>
    </>
  )
}

function PanelBody(props: {
  state: ReturnType<typeof useHw>
  snapshot: NonNullable<ReturnType<typeof useHw>['snapshot']>
  onSelect: (ref: string) => void
  onSelectField: (ref: string, field: string) => void
  onExport: () => void
}): JSX.Element {
  const common = {
    snapshot: props.snapshot,
    onSelect: props.onSelect,
    onSelectField: props.onSelectField,
    onCheck: () => { void store.runCheck() },
    onBudget: () => { void store.runBudget() },
    onCrosscheck: () => { void store.runCrosscheck() },
    onLock: (name: string, value: string) => { void store.lockDecision(name, value) },
    onExport: props.onExport,
    onLimit: (currency: string, value: number) => { void store.setBudgetLimit(currency, value) },
    onApplyReq: () => { void store.applyReqDraft() },
    // P46：发射是长任务，`busy` 会一直挂着；`只重验` 只跑 verify 那一段。
    onEmitRun: () => { void store.emitRun() },
    onEmitReverify: () => { void store.emitReverify() },
  }
  // 按**分区**渲染（不是分组）：分组只是导航，分区才是内容。
  switch (props.state.panelSection) {
    case 'verdict': return <VerdictPanel {...common} />
    case 'params': return <ParamsPanel {...common} />
    case 'firmware': return <FirmwarePanel {...common} />
    case 'req': return <ReqPanel {...common} />
    case 'checks': return <ChecksPanel {...common} />
    case 'budget': return <BudgetPanel {...common} />
    case 'deferred': return <DeferredPanel {...common} />
    case 'crosscheck': return <CrosscheckPanel {...common} />
    case 'evidence': return <EvidencePanel {...common} />
    case 'emit': return <EmitPanel {...common} />
    case 'brief': return <BriefPanel {...common} />
    case 'export': return <ExportPanel {...common} />
    case 'blocks':
      return (
        <BlocksPanel
          {...common}
          hwforge={props.state.bootstrap?.hwforge
            ?? { available: false, capabilities: 0, root: '' }}
          onRunBlock={(block, params) => {
            const project = store.getSnapshot().project
            void store.runBlock(project, block, params)
          }}
        />
      )
    case 'velxio':
      return (
        <VelxioPanel
          {...common}
          status={(props.state.velxio as unknown as VelxioStatus | null) ?? null}
          onRefresh={() => { void store.refreshVelxio() }}
          onStart={() => { void store.controlVelxio('start') }}
          onStop={() => { void store.controlVelxio('stop') }}
        />
      )
    case 'sim':
      return (
        <SimPanel
          {...common}
          sim={(props.state.sim as unknown as AvrSimStatus | null) ?? null}
          serial={props.state.serial}
          onStart={() => { void store.startSim() }}
          onRun={() => { void store.runSim() }}
          onRead={() => { void store.readSerial() }}
          onStop={() => { void store.stopSim() }}
        />
      )
    default: return <ChecksPanel {...common} />
  }
}

/**
 * 空态：**一个「开始」主卡**。工作区设置块不在这里——它是设置，由主区
 * 上方统一渲染一份（`WorkspaceSettings`），入口是工具栏那个路径按钮。
 *
 * 之前的四张等权重卡片有三处真问题（不是审美问题）：
 *
 * 1. 「选择文件夹」与「在当前工作区新建」是**同一个设置的两个入口**，
 *    却各占一张卡，看起来像两个功能；
 * 2. 「新建工作区」不是"开始"，是**设置**。而在空态里它还是**空操作**：
 *    点完工作区变了、工程数仍是 0，空态原样重绘——用户看不到成功；
 * 3. 「在当前工作区新建」与「在当前工作区新建工程」只差两个字。
 *
 * 工作区设没设不影响能否开始：默认工作区始终可用——所以这里也不再把
 * "先选工作区"写成前置条件。
 */
/**
 * 「有工程但读不到」——**不是空态**。
 *
 * 空态说的是"还没有工程，来建一个"；这里说的是"工程在，但这次没读成"。
 * 两种情况共用一块区域，但话必须不一样：把读失败画成"还没有工程"会让
 * 用户去新建一个已经存在的工程（真实截图上就出现了下拉选中 `示例-无人机`、
 * 正文写「还没有工程」的自相矛盾）。
 */
function Unavailable(props: {
  project: string
  error: string | null
  onRetry: () => void
}): JSX.Element {
  return (
    <div className="hwv-main hwv-main-scroll">
      <div className="hwv-empty-head">
        <div>
          <strong>读不到工程 {props.project}</strong>
          <div>工程目录还在，但这次快照请求失败了。修掉下面的原因后重试。</div>
        </div>
      </div>
      <section className="hwv-start" aria-label="读取失败">
        <div className="hwv-start-body">
          {props.error !== null && (
            <div className="hwv-warn-line" role="alert">{props.error}</div>
          )}
          <div className="hwv-hint">
            常见原因：宿主进程加载的是重建前的旧代码（改了 src 但没重启 DSH）、
            工程文件被手工改坏、或工作区被切到了别处。
          </div>
          <div className="hwv-start-foot">
            <span className="hwv-hint">工程 id：{props.project}</span>
            <span className="hwv-spacer" />
            <button className="hwv-btn primary" onClick={props.onRetry}>重试</button>
          </div>
        </div>
      </section>
    </div>
  )
}

function EmptyState(props: { ai: { send: (text: string) => void } }): JSX.Element {
  const state = useHw()
  const boards = state.bootstrap?.boards ?? []
  const formats = state.bootstrap?.formats ?? []
  const [projectId, setProjectId] = useState('001-blink')
  const [board, setBoard] = useState('arduino-uno')
  const [title, setTitle] = useState('Blink')
  const [importPath, setImportPath] = useState('')
  const [bomPath, setBomPath] = useState('')
  /**
   * 开始卡的三条路：新建预设 / 从文件导入 / **说需求给 AI**。
   *
   * 第三条是后加的：前两条都要求用户"已经知道要建什么、用哪块板"，
   * 而第一次打开时最真实的处境是"我想做个东西，但不知道从哪下手"。
   * 让 AI 走同一套工具链，比让人先猜一个板卡预设更诚实。
   */
  const [mode, setMode] = useState<'new' | 'import' | 'ask'>('new')
  const [askText, setAskText] = useState('')
  const [sent, setSent] = useState(false)

  const busy = state.busy !== ''

  return (
    <div className="hwv-main hwv-main-scroll">
      <div className="hwv-empty-head">
        <div>
          <strong>还没有工程</strong>
          <div>新建一个预设板卡，或把已有的 KiCad / Wokwi 文件接进来。</div>
        </div>
      </div>

      {/* ── 开始：一张卡两个页签，同一时刻只走一条路 ──────────────────── */}
      <section className="hwv-start" aria-label="开始">
        <div className="hwv-start-tabs" role="tablist" aria-label="开始方式">
          <button role="tab" aria-selected={mode === 'new'}
                  className={`hwv-start-tab${mode === 'new' ? ' on' : ''}`}
                  onClick={() => { setMode('new') }}>
            新建预设板卡
          </button>
          <button role="tab" aria-selected={mode === 'import'}
                  className={`hwv-start-tab${mode === 'import' ? ' on' : ''}`}
                  onClick={() => { setMode('import') }}>
            从文件导入
          </button>
          {/* 第三条路：**不知道从哪开始**时，直接把需求说给 AI。
              空态原本只有"新建/导入"两条，都需要用户先知道要建什么；
              而多数人第一次打开是"我想做个东西，但不知道选哪块板"。 */}
          <button role="tab" aria-selected={mode === 'ask'}
                  className={`hwv-start-tab${mode === 'ask' ? ' on' : ''}`}
                  onClick={() => { setMode('ask') }}>
            说需求给 AI
          </button>
        </div>

        {mode === 'ask' && (
          <div className="hwv-start-body">
            <p className="hwv-dim" style={{ margin: 0 }}>
              用一句话描述你要做的东西，AI 会按需要调工具（建工程 / 选板 / 加件 / 跑检查）。
              它改设计**只能**走 hw_put / hw_setpart，会过模型校验。
            </p>
            <textarea className="hwv-input" rows={3}
                      placeholder="例如：做一个壁挂云台相机，手机能看实时视频，5V 供电"
                      aria-label="需求"
                      value={askText}
                      onChange={(event) => { setAskText(event.target.value) }} />
            <div className="hwv-btn-row">
              <button className="hwv-btn primary"
                      disabled={askText.trim() === ''}
                      onClick={() => {
                        props.ai.send(askText.trim())
                        setAskText('')
                        setSent(true)
                      }}>
                发到会话
              </button>
              {sent && <span className="hwv-dim" role="status">✓ 已发，切回「对话」看进展</span>}
            </div>
          </div>
        )}

        {mode === 'new'
          ? (
            <div className="hwv-start-body">
              <div className="hwv-form-grid">
                <label className="hwv-field">
                  <span className="hwv-field-label">工程 id</span>
                  <input className="hwv-input" placeholder="001-blink" value={projectId}
                         onChange={(event) => { setProjectId(event.target.value) }} />
                </label>
                <label className="hwv-field">
                  <span className="hwv-field-label">标题</span>
                  <input className="hwv-input" placeholder="Blink" value={title}
                         onChange={(event) => { setTitle(event.target.value) }} />
                </label>
                <label className="hwv-field">
                  <span className="hwv-field-label">板卡预设</span>
                  <select className="hwv-select hwv-select-wide" value={board}
                          onChange={(event) => { setBoard(event.target.value) }}>
                    {boards.map((preset) => (
                      <option key={preset.kind} value={preset.kind}>
                        {preset.name} · {preset.size_mm.w}×{preset.size_mm.h}mm
                        {preset.source === 'estimated' ? '（估算）' : ''}
                      </option>
                    ))}
                  </select>
                </label>
              </div>
              <div className="hwv-start-foot">
                <span className="hwv-hint">
                  用板卡预设建一个空工程，2D 网表与 3D 实体同时成立。
                </span>
                <span className="hwv-spacer" />
                <button className="hwv-btn primary" disabled={busy}
                        onClick={() => { void store.createProject(projectId, board, title) }}>
                  新建工程
                </button>
              </div>
            </div>
          )
          : (
            <div className="hwv-start-body">
              <div className="hwv-form-grid">
                <label className="hwv-field">
                  <span className="hwv-field-label">源文件</span>
                  <input className="hwv-input" placeholder="KiCad .kicad_pcb / Wokwi .json"
                         value={importPath}
                         onChange={(event) => { setImportPath(event.target.value) }} />
                </label>
                <label className="hwv-field">
                  <span className="hwv-field-label">BOM（可选）</span>
                  <input className="hwv-input" placeholder=".csv" value={bomPath}
                         onChange={(event) => { setBomPath(event.target.value) }} />
                </label>
              </div>
              <div className="hwv-start-foot">
                <span className="hwv-hint">
                  支持 {formats.map((format) => format.ext).join(' / ')
                    || '.kicad_pcb / .json / .csv'}
                  {state.workspace !== '' && <> · 相对路径相对工作区</>}
                </span>
                <span className="hwv-spacer" />
                <button className="hwv-btn primary"
                        disabled={importPath.trim() === '' || busy}
                        onClick={() => { void store.importProject(importPath.trim(), bomPath.trim()) }}>
                  导入
                </button>
              </div>
            </div>
          )}
      </section>
    </div>
  )
}

/**
 * 工作区设置块——**唯一的实现**，由 `HwIdeView` 在主区上方渲染一份。
 *
 * 抽出来的理由正是这一轮的起因：设置若在两处各写一遍（工具栏一份、空态
 * 一份），它们必然漂移成"看起来像两个功能"。入口（工具栏那个按钮）与
 * 内容（这里）分离，入口只有一个。
 */
function WorkspaceSettings(props: { face: WorkbenchFace }): JSX.Element {
  const state = useHw()
  const [wsName, setWsName] = useState('hw-workbench')
  const [pickError, setPickError] = useState('')
  const busy = state.busy !== ''

  /**
   * 打开系统文件夹选择器。入口解析在 `directoryPicker.ts`：桌面壳的
   * `__DSH_DIRECTORY_PICKER__`（preload IPC，直达主进程的原生对话框）优先，
   * 其次是宿主 ui-workspace 客户端的 `pickDirectory()`（RPC 到宿主，由宿主
   * 启动时探测 native/browse）。拿不到入口时报出**查过的两个名字**。
   */
  const pickFolder = async (): Promise<void> => {
    setPickError('')
    try {
      const picked = await props.face.pickDirectory()
      if (picked === null || picked === '') return   // 用户取消：什么都不做
      await store.chooseWorkspace(picked)
    } catch (error) {
      setPickError(error instanceof Error ? error.message : String(error))
    }
  }

  return (
    <section className="hwv-settings" aria-label="工作区设置">
      {/* 块内不放「收起」按钮：工具栏那个 `aria-expanded=true` 的入口就是
          同一个动作的第二入口。两处都能收起，就又是这一轮要修的重复。 */}
      <span className="hwv-field-label">
        工作区 · {SOURCE_LABEL[state.workspaceSource] ?? '默认'}
      </span>

      <div className={`hwv-path${state.workspaceWritable ? '' : ' hwv-path-bad'}`}
           title={state.workspace}>
        {state.workspace || '（尚未解析出工作区）'}
      </div>

      {/* 勘察摘要：**这里要说清"有多少东西、读了多少"**。
          没有这块，用户会以为"工作台里 2 个工程"就是全部资产，
          而实际上工作区里还有 21 个真实条目、2085 个文件。 */}
      {state.survey !== null && (
        <div className="hwv-survey">
          <div className="hwv-survey-row">
            <span className="hwv-chip">{state.survey.entries} 个条目</span>
            {state.survey.notAdopted > 0 && (
              <span className="hwv-chip warn"
                    title="有真实内容（源码/固件/BOM），但缺 design.json，还进不了 2D/3D">
                {state.survey.notAdopted} 个未接入
              </span>
            )}
            {state.survey.kinds.slice(0, 6).map((kind) => (
              <span key={kind} className="hwv-chip dim">{kind}</span>
            ))}
          </div>
          <div className="hwv-survey-row">
            <span className="hwv-field-label">读取覆盖度</span>
            <span className="hwv-meter" role="img"
                  aria-label={`已读 ${state.survey.readFiles} / ${state.survey.totalFiles}`}>
              <span className="hwv-meter-fill"
                    style={{ width: `${state.survey.totalFiles === 0 ? 0
                      : Math.max(1, (state.survey.readFiles / state.survey.totalFiles) * 100)}%` }} />
            </span>
            <span className="hwv-hint">
              读了 {state.survey.readFiles} / {state.survey.totalFiles}
              {'　'}未读 {state.survey.totalFiles - state.survey.readFiles}
            </span>
          </div>
          {state.survey.mcus.length > 0 && (
            <div className="hwv-survey-row">
              <span className="hwv-field-label">检测到 MCU</span>
              <span className="hwv-hint hwv-mono">{state.survey.mcus.join(' · ')}</span>
            </div>
          )}
          <div className="hwv-hint">
            日志写在 <code className="hwv-mono">{state.survey.logsDir}</code>
            {' '}（只追加；含每次勘察报告与读取覆盖度）
          </div>
        </div>
      )}
      {!state.workspaceWritable && (
        <div className="hwv-warn-line">
          这个目录不可写：保存会失败。换一个文件夹，或修好权限。
        </div>
      )}

      {pickError !== '' && (
        <div className="hwv-warn-line">打开文件夹选择器失败：{pickError}</div>
      )}

      <div className="hwv-btn-row">
        <button className="hwv-btn" disabled={busy} onClick={() => { void pickFolder() }}>
          浏览文件夹…
        </button>
        <input className="hwv-input hwv-input-inline" value={wsName}
               aria-label="新工作区文件夹名"
               placeholder="文件夹名"
               onChange={(event) => { setWsName(event.target.value) }} />
        <button className="hwv-btn"
                disabled={busy || wsName.trim() === ''}
                title={state.dshCwd !== ''
                  ? `创建于 ${state.dshCwd}` : '读不到 DSH 当前工作目录'}
                onClick={() => {
                  const parent = state.dshCwd
                  if (parent === '') {
                    setPickError('读不到 DSH 当前工作目录，无法在此新建')
                    return
                  }
                  void store.createWorkspace(parent, wsName.trim())
                }}>
          新建
        </button>
      </div>
      <div className="hwv-hint">
        新建的文件夹落在 DSH 当前工作目录下：{state.dshCwd || '（未知）'}
      </div>
    </section>
  )
}
