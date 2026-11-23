/**
 * 底部面板：判定 / 预算 / 未决 / 核对 / 证据 / 制造 / 积木。
 *
 * 每个面板只做两件事：**如实显示已有结论**，以及**给一个可执行动作**。
 * 没有数据的格子写"未验证"，不写"通过"——这两者在硬件上不是一回事。
 *
 * @module dsh-hw-velxio/client/panels
 */

import { useState } from 'react'
import type {
  BudgetPayload,
  ChecksPayload,
  CrosscheckPayload,
  DeferredPayload,
  EvidencePayload,
  ExportPayload,
  Finding,
  FirmwareSummaryPayload,
  NextStep,
  ReqDraftPayload,
  ParamsSummaryPayload,
  SnapshotPayload,
  VerdictPayload,
} from '../shared/types.js'

/**
 * 固件矩阵状态 → 样式类。**不用模板字符串拼类名**：那样源码里只剩一个
 * 前缀，`check_dead_css` 既看不到 `hwv-fw-ok` 被用、又会把前缀当成缺失类。
 * 显式映射让"组件用的类"在源码里可搜索。
 */
const FW_CLASS: Readonly<Record<'ghost' | 'direction' | 'idle' | 'ok', string>> = {
  ghost: 'hwv-fw-ghost',
  direction: 'hwv-fw-direction',
  idle: 'hwv-fw-idle',
  ok: 'hwv-fw-ok',
}

export interface PanelProps {
  readonly snapshot: SnapshotPayload
  readonly onSelect: (ref: string) => void
  /** 应用需求草稿（P5）。**人点的那一下**。 */
  readonly onApplyReq?: () => void
  /** 选中元件**并聚焦某个设计字段**（`edit_field` 的那一步用它）。 */
  readonly onSelectField?: (ref: string, field: string) => void
  readonly onCheck: () => void
  readonly onBudget: () => void
  readonly onCrosscheck: () => void
  readonly onLock: (name: string, value: string) => void
  readonly onExport: () => void
  readonly onLimit: (currency: string, value: number) => void
  /** 跑一次发射（P46，**长任务**）。不给就不渲染那个按钮。 */
  readonly onEmitRun?: () => void
  /** 只重验（8 段里只跑 verify）。 */
  readonly onEmitReverify?: () => void
  /** 只重跑 8 段里的某一段（P2）。不给就不渲染逐段按钮。 */
  readonly onEmitRunStage?: (stage: string) => void
  /** 发射是否正在跑：按钮禁用 + 顶部提示（进度按段刷新，不是流）。 */
  readonly emitRunning?: boolean
}

/**
 * 一步动作的按钮（P1）。**四类只有三种交互**：
 *
 * - `edit_field` / `move_part`：选中元件，让人在 Inspector / 2D 里改
 * - `run_command`：复制整条命令（本工具不替人执行——执行是不可逆的）
 * - `external`：不执行、只**定位**（选中有问题的件），并写明"由谁做"
 *
 * 没有"自动修复"按钮。改设计是人的决定，机器只把人送到该看的地方。
 */
function StepButton(props: {
  step: NextStep
  onSelect: (ref: string) => void
  onSelectField?: (ref: string, field: string) => void
}): JSX.Element {
  const { step, onSelect, onSelectField } = props
  const [copied, setCopied] = useState(false)

  const locate = (): void => {
    const first = step.refs?.[0]
    if (first === undefined || first === '') return
    // 有字段路径就**一路送到那个输入框**；否则只选中元件。
    if (step.field !== undefined && step.field !== '' && onSelectField !== undefined) {
      onSelectField(first, step.field)
      return
    }
    onSelect(first)
  }

  const copy = async (): Promise<void> => {
    const text = step.command ?? ''
    if (text === '') return
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      setTimeout(() => { setCopied(false) }, 1600)
    } catch {
      // 剪贴板被拒（非 https / 未授权）时**不要假装成功**：把命令原样留在
      // 按钮下面，人还能手抄。静默成功是最坏的一种失败。
      setCopied(false)
    }
  }

  if (step.kind === 'run_command') {
    return (
      <button className="hwv-btn hwv-step-btn" onClick={() => { void copy() }}
              title={step.command ?? ''}>
        {copied ? '已复制 ✓' : '复制命令'}
      </button>
    )
  }
  const canLocate = (step.refs?.length ?? 0) > 0
  return (
    <button className="hwv-btn hwv-step-btn" disabled={!canLocate}
            onClick={(event) => { event.stopPropagation(); locate() }}
            title={step.kind === 'external'
              ? `由${step.by ?? '人'}完成；点这里定位到该元件`
              : step.field !== undefined ? `在 Inspector 里改 ${step.field}` : ''}>
      {step.kind === 'external' ? '定位' : '去改'}
    </button>
  )
}

function Mark({ severity }: { severity: 'error' | 'warning' | 'info' | 'ok' }): JSX.Element {
  return <span className="mark">{severity === 'ok' ? '✓'
    : severity === 'info' ? '·'
      : severity === 'warning' ? '!' : '✗'}</span>
}

function FindingRow(props: {
  finding: Finding
  onSelect: (ref: string) => void
  onSelectField?: (ref: string, field: string) => void
}): JSX.Element {
  const { finding, onSelect, onSelectField } = props
  const severity = finding.ok ? 'ok' : finding.severity
  const cls = finding.ok ? 'ok' : severity === 'warning' ? 'warn' : severity === 'info' ? 'info' : 'err'
  return (
    <div className={`hwv-row ${cls}`}>
      <Mark severity={severity as 'error' | 'warning' | 'info' | 'ok'} />
      <span className="msg">
        {finding.message}
        {finding.next !== undefined && finding.next.kind === 'external'
          && <span className="hwv-next-by">（{finding.next.by ?? '人'}）</span>}
      </span>
      {finding.refs.map((ref) => (
        <span key={ref} className="hwv-refs"
              onClick={() => { onSelect(ref) }}>{ref}</span>
      ))}
      {finding.next !== undefined && (
        <StepButton step={finding.next} onSelect={onSelect}
                    onSelectField={onSelectField} />
      )}
    </div>
  )
}

/**
 * 参数（P3）：**136 个参数里，只有几个值得你去看。**
 *
 * ## 这一屏真正的信息是"132 个不用管"
 *
 * 实测：`ParamSet` 的唯一消费者是 `mjcf.build`，它只读了 4 个键。
 * 其余 132 个参数**没有任何代码读**——改成任何值，仿真结果一个字都不变。
 *
 * 所以四类必须分开报。把"没人读"混进"不敏感"是假绿：读的人会以为
 * "低敏感 = 不重要但评估过了"，而真相是**从未接入**。
 *
 * ## 排序在 Python 算
 *
 * `priority` 已经是排好的（先按响应方向数，再按 key）。UI 不重排——
 * 重排就等于多了一个"哪个更急"的事实源（AGENTS §1.1）。
 */
export function ParamsPanel(props: PanelProps): JSX.Element {
  const params: ParamsSummaryPayload | undefined = props.snapshot.params
  if (params === undefined || params.error !== undefined) {
    return (
      <div className="hwv-body">
        <div className="hwv-note hwv-dim">
          {params?.error ?? '没有参数摘要（宿主没返回 params）。'}
        </div>
      </div>
    )
  }
  const counts = params.counts ?? {}
  const wired = counts.wired ?? 0
  const noop = counts.wired_noop ?? 0
  const unwired = counts.unwired ?? 0
  const missing = counts.missing ?? 0

  return (
    <div className="hwv-body">
      <div className="hwv-summary">
        共 {params.total} 个参数 · <b>只有 {wired} 个真的驱动模型</b>
      </div>

      <div className="hwv-verdict-counts">
        <span className={`hwv-chip${wired > 0 ? ' on' : ''}`}>驱动模型 {wired}</span>
        <span className="hwv-chip">读了但无效果 {noop}</span>
        <span className={`hwv-chip${unwired > 0 ? ' on' : ''}`}>未接线 {unwired}</span>
        <span className={`hwv-chip${missing > 0 ? ' on' : ''}`}>表里缺项 {missing}</span>
      </div>

      <div className="hwv-warn-line">
        「未接线」意思是**没有任何代码读它**，不是「不敏感」。
        在它接上之前，改这个值不会影响任何结论。
      </div>

      <div className="hwv-pane-head">最该核的（按响应强度排）</div>
      {params.priority.length === 0
        ? (
          <div className="hwv-note hwv-dim">
            一个都没接线。先让代码真的读参数，再谈选值。
          </div>
        )
        : params.priority.map((row, index) => (
          <div key={row.key} className="hwv-row">
            <span className="hwv-mark-num">{index + 1}</span>
            <span className="msg hwv-mono">{row.key}</span>
            <span className="hwv-chip">{row.group}</span>
            <span className="hwv-dim">{String(row.value)}</span>
            <span className="hwv-chip on">
              {row.changed.length} 个扰动方向有响应
            </span>
          </div>
        ))}

      <div className="hwv-note hwv-dim">
        {params.note ?? ''}
        完整 136 行表：<span className="hwv-mono">sensitivity &lt;工程&gt;</span>
      </div>
    </div>
  )
}

/**
 * 固件矩阵（P4）：**引脚 × (硬件配置 / 固件使用 / 结论)**，每脚一行。
 *
 * ## 为什么不是"三张问题表"
 *
 * `pinmap-check` 输出的是 ghost / idle / direction 三张表——**没有问题的脚
 * 不在任何一张表里**。于是"这个脚到底配没配、用没用"只能靠人拿 `.ioc`
 * 和源码对。矩阵按脚一行，才回答得了那句话。
 *
 * ## 四类结论，顺序 = 严重度
 *
 * | 状态 | 含义 | 后果 |
 * |---|---|---|
 * | `ghost` | 硬件没配、固件在用 | **运行时必炸**（写一个不存在的端口） |
 * | `direction` | 配的是输入，固件在写（或反之） | 静默失效 |
 * | `idle` | 配了，固件没用到 | 浪费，也可能是漏写 |
 * | `ok` | 核对过 | — |
 *
 * 调试脚（SWD/JTAG）**不算闲置**——`crosscheck` 已经排除了它们，
 * 这里不能加回来（否则凭空多一堆"闲置"，人就不看了）。
 */
/**
 * 需求草稿（P5）：**一句话 → 结构化需求**的那一半。
 *
 * ## 这一屏回答的是「模型填的那份草稿，现在到哪一步了」
 *
 * ```
 * 草稿 → 校验 → 人确认 → 才成为 generate 的输入
 * ```
 *
 * 三道闸门各显示各的状态。**不给"一键生成设计"**——那会跳过
 * `validate` 与 `generate`，把"模型说行"当成"可以开工"。
 *
 * ## `numbers` 单独一块，是因为它填了也没用
 *
 * `Requirement.numbers` 没有任何消费方（实测：只有 `from_dict`/`to_dict` 过一遍）。
 * 所以校验把它列进 warnings，面板也必须**单独显示**——混在普通警告里，
 * 人会以为那些数字在约束设计。
 */
export function ReqPanel(props: PanelProps): JSX.Element {
  const req: ReqDraftPayload | undefined = props.snapshot.req
  if (req === undefined || req.error !== undefined) {
    return (
      <div className="hwv-body">
        <div className="hwv-note hwv-dim">
          {req?.error ?? '没有需求摘要（宿主没返回 req）。'}
        </div>
      </div>
    )
  }

  const draft = req.draft
  const validation = req.validation
  const problems = validation?.problems ?? []
  const warnings = validation?.warnings ?? []
  const consumerless = warnings.filter((w) => w.kind === 'field_without_consumer')
  const otherWarnings = warnings.filter((w) => w.kind !== 'field_without_consumer')

  if (draft === null) {
    return (
      <div className="hwv-body">
        <div className="hwv-note">
          还没有需求草稿。让会话里的模型产出它：
        </div>
        <div className="hwv-note hwv-mono">
          hw_req_draft(action=&quot;draft&quot;, requirement=&#123;...&#125;)
        </div>
        <div className="hwv-note hwv-dim">
          草稿**不是需求**：它要先过校验，再由人确认，才会写进 req.json。
        </div>
      </div>
    )
  }

  return (
    <div className="hwv-body">
      <div className="hwv-summary">
        {draft.title !== '' ? draft.title : draft.id}
        <span className="hwv-chip">{draft.roles.length} 个角色</span>
        <span className="hwv-chip">{draft.interfaces.length} 条接口</span>
        <span className={`hwv-chip${req.applied ? ' on' : ''}`}>
          {req.applied ? '已应用' : '未应用'}
        </span>
        {req.scope === 'workspace' && (
          <span className="hwv-chip warn">工程还没有</span>
        )}
      </div>

      <div className="hwv-pane-head">角色</div>
      <div className="hwv-inline" style={{ flexWrap: 'wrap', gap: 'var(--hwv-space-2)' }}>
        {draft.roles.map((role) => (
          <span key={role} className="hwv-chip hwv-mono">{role}</span>
        ))}
      </div>

      <div className="hwv-pane-head">接口</div>
      {draft.interfaces.length === 0
        ? <div className="hwv-note hwv-dim">没有声明接口。</div>
        : (
          <table className="hwv-tbl">
            <thead>
              <tr><th>a</th><th>b</th><th>协议</th></tr>
            </thead>
            <tbody>
              {draft.interfaces.map((row, index) => (
                <tr key={index}>
                  <td className="hwv-mono">{String(row.a ?? '?')}</td>
                  <td className="hwv-mono">{String(row.b ?? '?')}</td>
                  <td>{String(row.protocol ?? '—')}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

      <div className="hwv-pane-head">
        校验 {validation === null ? '' : validation.ok ? '通过' : `${problems.length} 个问题`}
      </div>
      {problems.length === 0
        ? (
          <div className="hwv-row ok">
            <span className="mark">✓</span>
            <span className="msg">结构上成立：角色都在 roles.py 里，接口两端都接得上。</span>
          </div>
        )
        : problems.map((problem, index) => (
          <div key={`${problem.kind}-${index}`} className="hwv-row err">
            <span className="mark">✗</span>
            <span className="msg">
              {problem.message}
              {problem.fix !== undefined && problem.fix !== '' && (
                <span className="hwv-next-by"> → {problem.fix}</span>
              )}
            </span>
          </div>
        ))}

      {/* numbers 单独一块：它**填了也不影响任何结论** */}
      {consumerless.length > 0 && (
        <>
          <div className="hwv-pane-head">填了但没人读的字段</div>
          {consumerless.map((warning, index) => (
            <div key={`cl-${index}`} className="hwv-row warn">
              <span className="mark">!</span>
              <span className="msg">{warning.message}</span>
            </div>
          ))}
        </>
      )}

      {otherWarnings.length > 0 && (
        <details className="hwv-block-tight">
          <summary className="hwv-dim">其他提示 {otherWarnings.length} 条</summary>
          {otherWarnings.map((warning, index) => (
            <div key={`w-${index}`} className="hwv-row info">
              <span className="mark">·</span>
              <span className="msg">{warning.message}</span>
            </div>
          ))}
        </details>
      )}

      <div className="hwv-btn-row">
        <button className="hwv-btn primary"
                disabled={req.applied || !(validation?.ok === true)}
                onClick={() => { props.onApplyReq !== undefined && props.onApplyReq() }}
                title={validation?.ok !== true
                  ? '校验没通过，不能应用'
                  : req.applied ? '已经应用过了'
                    : req.project_exists === false
                      ? '工程还不存在：确认后会顺手建一个空工程，再写 req.json'
                      : '确认并写进 req.json'}>
          {req.applied
            ? '已应用'
            : req.project_exists === false ? '建工程并应用' : '确认并应用'}
        </button>
        <span className="hwv-note hwv-dim">{req.next ?? ''}</span>
      </div>
    </div>
  )
}

export function FirmwarePanel(props: PanelProps): JSX.Element {
  const fw: FirmwareSummaryPayload | undefined = props.snapshot.firmware
  if (fw === undefined) {
    return (
      <div className="hwv-body">
        <div className="hwv-note hwv-dim">
          没有固件矩阵（宿主没返回 firmware）。
        </div>
      </div>
    )
  }
  if (fw.status === 'not_ready') {
    return (
      <div className="hwv-body">
        <div className="hwv-warn-line">
          {fw.reason ?? '硬件侧没有可核对的引脚配置'}
        </div>
        <div className="hwv-note hwv-dim">
          这不是「全对」——是**没有核对**。没有 `.ioc` 时硬件侧没东西可比，
          没有固件源码时软件侧没东西可比；两种情况都只能如实说不知道。
        </div>
        <div className="hwv-note hwv-dim">
          命令：<span className="hwv-mono">fw-matrix &lt;工程&gt; --ioc=&lt;.ioc&gt; --src=&lt;源码目录&gt;</span>
        </div>
      </div>
    )
  }

  const counts = fw.counts ?? {}
  const rows = fw.rows ?? []

  return (
    <div className="hwv-body">
      <div className="hwv-summary">
        核对 {fw.checked ?? 0} 个配置脚 · 固件用了 {fw.used_pins ?? 0} 个
        {fw.basis !== undefined && fw.basis !== '' && (
          <span className="hwv-chip">{fw.basis}</span>
        )}
      </div>

      <div className="hwv-verdict-counts">
        <span className={`hwv-chip${(counts.ghost ?? 0) > 0 ? ' on' : ''}`}>
          幽灵引脚 {counts.ghost ?? 0}
        </span>
        <span className={`hwv-chip${(counts.direction ?? 0) > 0 ? ' on' : ''}`}>
          方向冲突 {counts.direction ?? 0}
        </span>
        <span className="hwv-chip">闲置 {counts.idle ?? 0}</span>
        <span className="hwv-chip">核对过 {counts.ok ?? 0}</span>
      </div>

      {(counts.ghost ?? 0) + (counts.direction ?? 0) > 0 && (
        <div className="hwv-warn-line">
          幽灵引脚与方向冲突是 error 级：运行时必炸或静默失效。
        </div>
      )}

      <div className="hwv-pane-head">
        有问题的引脚（共 {fw.rows_total ?? rows.length} 行，这里显示前 {rows.length} 行）
      </div>
      {rows.length === 0
        ? (
          <div className="hwv-note hwv-dim">
            没有问题的引脚。注意：这只说明**核对过的那些**对得上，
            不代表所有脚都被核对过。
          </div>
        )
        : (
          <table className="hwv-tbl">
            <thead>
              <tr>
                <th>引脚</th>
                <th>硬件配置</th>
                <th>固件使用</th>
                <th>结论</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.pin}>
                  <td className="hwv-mono">{row.pin}</td>
                  <td>
                    {row.config?.mode !== undefined && row.config.mode !== ''
                      ? row.config.mode
                      : row.config?.signal ?? ''}
                    {row.config?.label !== undefined && row.config.label !== '' && (
                      <span className="hwv-danger-dim"> {row.config.label}</span>
                    )}
                    {row.status === 'ghost' && (
                      <span className="hwv-danger-dim">（硬件没配）</span>
                    )}
                  </td>
                  <td className="hwv-mono">
                    {(row.firmware ?? []).length === 0
                      ? '—'
                      : (row.firmware ?? []).map((use, index) => (
                        <div key={index}>
                          {use.file}
                          {use.line !== undefined && use.line > 0 ? `:${use.line}` : ''}
                          {use.api !== undefined && use.api !== '' ? ` (${use.api})` : ''}
                        </div>
                      ))}
                  </td>
                  <td className={FW_CLASS[row.status]}>
                    {FW_STATUS[row.status] ?? row.status}
                    {row.why !== undefined && row.why !== '' && (
                      <div className="hwv-danger-dim">{row.why}</div>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

      {fw.unknown !== undefined && fw.unknown > 0 && (
        <div className="hwv-note hwv-dim">
          另有 {fw.unknown} 处源码只出现端口或缺位号，**配不成对**
          （不猜端口——猜错比不报更糟）。
        </div>
      )}
    </div>
  )
}

const FW_STATUS: Record<string, string> = {
  ghost: '幽灵引脚',
  direction: '方向冲突',
  idle: '闲置',
  ok: '✓',
}

export function ChecksPanel(props: PanelProps): JSX.Element {
  const checks: ChecksPayload = props.snapshot.checks
  if (checks.status === 'not_ready') {
    return (
      <div className="hwv-body">
        <div className="hwv-row err"><Mark severity="error" />
          <span className="msg">检查无法运行：{checks.error}</span></div>
        <div className="hwv-dim hwv-block-tight">
          这是前置缺失，不是"通过"。修好环境后点「检查」重跑。
        </div>
      </div>
    )
  }
  const failed = checks.findings.filter((f) => !f.ok)
  const passed = checks.findings.filter((f) => f.ok)
  return (
    <div className="hwv-body">
      <div className="hwv-summary">
        体素 {checks.space?.dims_voxels?.join('×') ?? '—'}，
        间距 {checks.pitch_mm}mm，占用 {checks.space?.occupied_voxels ?? 0} 体素 ·
        <button className="hwv-btn" onClick={() => { props.onCheck() }}>重跑</button>
      </div>
      {failed.map((finding) => (
        <FindingRow key={finding.check + finding.message} finding={finding}
                    onSelect={props.onSelect}
                    onSelectField={props.onSelectField} />
      ))}
      {failed.length === 0 && (
        <div className="hwv-row ok"><Mark severity="ok" /><span className="msg">全部检查通过</span></div>
      )}
      {passed.length > 0 && (
        <details className="hwv-block-tight">
          <summary className="hwv-dim">通过的 {passed.length} 项</summary>
          {passed.map((finding) => (
            <FindingRow key={finding.check + finding.message} finding={finding}
                        onSelect={props.onSelect}
                        onSelectField={props.onSelectField} />
          ))}
        </details>
      )}
      {checks.not_verified.length > 0 && (
        <div className="hwv-block">
          <div className="hwv-dim">未验证（不是通过）：</div>
          {checks.not_verified.map((line) => (
            <div key={line} className="hwv-row info"><Mark severity="info" />
              <span className="msg">{line}</span></div>
          ))}
        </div>
      )}
    </div>
  )
}

const CURRENCY_LABEL: Record<string, string> = {
  power_w: '功率 W',
  thermal_c: '热 °C',
  emc_dbuv: 'EMC dBµV',
  timing_ns: '时序 ns',
  cost_usd: '成本 $',
}

/**
 * 结论（首屏）：**一句话 + 九层横条 + 六类计数 + 下一步五件事。**
 *
 * ## 为什么它取代 `判定` 成为默认分区
 *
 * 用户打开工程时的问题是"我这版行不行、下一步干什么"。旧的分区结构里，
 * 这个答案要跨 `判定`/`预算`/`未决`/`核对` 四个页签自己拼——而拼错的代价
 * 是真实的：P17 的坑就是人看到 "V2 通过" 以为能打板，而 V3 是 `not_run`。
 *
 * ## 三处不能省的东西
 *
 * 1. **`unknown` 与 `not_run` 必须各自可见**。把它们并进"没通过"会让人
 *    去改设计；并进"通过"就是假绿。
 * 2. **`stale` 必须显式报警**。设计改过而阶梯没重跑时，这一屏是**过期**的；
 *    不提示，人就会拿旧结论做新决定。
 * 3. **下一步按 Python 给的顺序渲染**。排序是判定（AGENTS §1.1），
 *    UI 不重排——重排就等于多了一个"哪个更急"的事实源。
 */
export function VerdictPanel(props: PanelProps): JSX.Element {
  const v: VerdictPayload | undefined = props.snapshot.verdict
  if (v === undefined || v.status !== 'ok') {
    return (
      <div className="hwv-body">
        <div className="hwv-note">
          {v?.error ?? '结论层不可用（宿主没返回 verdict）。'}
        </div>
        <div className="hwv-btn-row">
          <button className="hwv-btn" onClick={() => { void props.onCheck() }}>跑判定</button>
        </div>
      </div>
    )
  }
  const levels = v.levels ?? []
  const counts = v.counts ?? {}
  const next = v.next ?? []
  const categories: Array<[string, string]> = [
    ['blocking', '阻断'], ['missing_data', '缺数据'], ['assumed', '默认值'],
    ['unverifiable', '不会验'], ['orphan', '未接线'], ['external', '外部'],
  ]
  return (
    <div className="hwv-body">
      <div className={`hwv-verdict-head${v.deliverable === true ? '' : ' blocked'}`}>
        <span className="hwv-verdict-rollup">{ROLLUP_LABEL[v.rollup ?? ''] ?? '?'}</span>
        <span className="hwv-verdict-summary">{v.summary}</span>
      </div>

      {v.stale === true && (
        <div className="hwv-warn-line">
          设计改过了，这一屏是**上次判定**的结论（{v.ladder_age ?? '未知时间'}）。
          重跑判定前别拿它做决定。
        </div>
      )}

      {levels.length === 0
        ? (
          <div className="hwv-note">
            还没有阶梯产物——空白不代表通过。
          </div>
        )
        : (
          <div className="hwv-ladder" role="list" aria-label="验证阶梯">
            {levels.map((level) => (
              <button key={level.level} role="listitem"
                      className={`hwv-ladder-cell ${level.status}`}
                      title={`${level.level} ${level.title ?? ''}：${level.status}`
                        + `（查 ${level.checked ?? 0} 项，失败 ${level.failed ?? 0}，`
                        + `判不出 ${level.unknown ?? 0}）`}
                      onClick={() => { void props.onCheck() }}>
                <span className="lv">{level.level}</span>
                <span className="st">{LEVEL_MARK[level.status] ?? '?'}</span>
              </button>
            ))}
            {(v.levels_total ?? 0) > levels.length && (
              <span className="hwv-ladder-gap">
                还有 {(v.levels_total ?? 0) - levels.length} 层没跑（V7 运动 / V8 制造）
              </span>
            )}
          </div>
        )}

      <div className="hwv-verdict-counts">
        {categories.map(([key, label]) => (
          <span key={key} className={`hwv-chip${(counts[key] ?? 0) > 0 ? ' on' : ''}`}>
            {label} {counts[key] ?? 0}
          </span>
        ))}
      </div>

      <div className="hwv-pane-head">
        下一步（{(v.next_total ?? next.length)} 条，按紧急程度排）
      </div>
      {next.length === 0
        ? <div className="hwv-note">没有待办。注意：这只说明六类都空，不说明验证充分。</div>
        : next.map((row, index) => (
          <div key={`${row.category}-${index}`} className="hwv-row">
            <span className="hwv-mark-num">{index + 1}</span>
            <span className="msg">
              <b>{row.title}</b>
              {row.action !== undefined && row.action !== '' && (
                <span className="hwv-next-action"> → {row.action}</span>
              )}
            </span>
            <span className={`hwv-chip${row.category === 'blocking' ? ' on' : ''}`}>
              {CATEGORY_LABEL[row.category] ?? row.category}
            </span>
            {row.step !== undefined && (
              <StepButton step={row.step} onSelect={props.onSelect}
                          onSelectField={props.onSelectField} />
            )}
          </div>
        ))}
    </div>
  )
}

const ROLLUP_LABEL: Record<string, string> = {
  pass: '九层全过', fail: '有失败', unknown: '结论不完整', not_ready: '还没跑',
}

const LEVEL_MARK: Record<string, string> = {
  pass: '✓', fail: '✗', unknown: '?', not_run: '–', truncated: '!',
}

const CATEGORY_LABEL: Record<string, string> = {
  blocking: '阻断', missing_data: '缺数据', assumed: '默认值',
  unverifiable: '不会验', orphan: '未接线', external: '外部',
}

export function BudgetPanel(props: PanelProps): JSX.Element {
  const budget: BudgetPayload = props.snapshot.budget
  const [editing, setEditing] = useState('')
  const [value, setValue] = useState('')

  if (budget.status === 'not_ready') {
    return (
      <div className="hwv-body">
        <div className="hwv-row err"><Mark severity="error" />
          <span className="msg">预算无法评估：{budget.error}</span></div>
      </div>
    )
  }
  const confidence = budget.confidence ?? 'none'
  return (
    <div className="hwv-body">
      <div className="hwv-summary">
        覆盖率 {budget.coverage?.claimed_parts ?? 0}/{budget.coverage?.total_parts ?? 0} 件 ·
        仲裁顺序 {(budget.resolution_order ?? []).join(' > ')} ·
        状态 {confidence === 'none' ? '无可评估数据' : confidence === 'partial' ? '部分覆盖' : '全覆盖'}
        <button className="hwv-btn" onClick={() => { props.onBudget() }}>重算</button>
      </div>

      <table className="hwv-tbl">
        <thead>
          <tr>
            <th>货币</th><th>已用</th><th>上限</th><th>占用</th><th />
          </tr>
        </thead>
        <tbody>
          {Object.entries(budget.by_currency ?? {}).map(([currency, row]) => (
            <tr key={currency}>
              <td>{CURRENCY_LABEL[currency] ?? currency}</td>
              <td className="hwv-mono">{row.used}</td>
              <td className="hwv-mono">
                {row.limit ?? '—'}
                {row.default_limit && <span className="hwv-chip" style={{ marginLeft: 'var(--hwv-space-2)' }}>默认</span>}
              </td>
              <td style={{ color: row.over ? 'var(--hwv-err)' : 'var(--hwv-dim)' }}>
                {row.ratio === null ? '—' : `${Math.round(row.ratio * 100)}%`}
              </td>
              <td>
                <button className="hwv-btn" onClick={() => {
                  setEditing(currency)
                  setValue(String(row.limit ?? ''))
                }}>改</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {editing !== '' && (
        <div className="hwv-inline hwv-block-tight">
          <span className="hwv-dim">{CURRENCY_LABEL[editing] ?? editing} 新上限</span>
          <input className="hwv-input" style={{ width: 90, flex: '0 0 auto' }} value={value}
                 onChange={(event) => { setValue(event.target.value) }} />
          <button className="hwv-btn primary" onClick={() => {
            const parsed = Number(value)
            if (Number.isFinite(parsed) && parsed > 0) props.onLimit(editing, parsed)
            setEditing('')
          }}>应用</button>
          <button className="hwv-btn" onClick={() => { setEditing('') }}>取消</button>
        </div>
      )}

      {(budget.violations ?? []).length > 0 && (
        <div className="hwv-block">
          <div className="hwv-dim">超限项：</div>
          {(budget.violations ?? []).map((violation, index) => (
            <div key={index} className="hwv-row err"><Mark severity="error" />
              <span className="msg hwv-mono">{JSON.stringify(violation)}</span></div>
          ))}
        </div>
      )}

      <div className="hwv-dim hwv-block">
        {budget.coverage?.note}
      </div>
      {(budget.coverage?.uncovered ?? []).length > 0 && (
        <div className="hwv-dim">
          没有 claim 的元件：{(budget.coverage?.uncovered ?? []).join(', ')}
        </div>
      )}
    </div>
  )
}

export function DeferredPanel(props: PanelProps): JSX.Element {
  const deferred: DeferredPayload = props.snapshot.deferred
  const [name, setName] = useState('')
  const [value, setValue] = useState('')
  if (deferred.status === 'not_ready') {
    return (
      <div className="hwv-body">
        <div className="hwv-row err"><Mark severity="error" />
          <span className="msg">决策账本不可用：{deferred.error}</span></div>
      </div>
    )
  }
  const open = deferred.open_irreversible ?? []
  const reasons = deferred.reasons ?? {}
  return (
    <div className="hwv-body">
      <div className={`hwv-row ${deferred.deliverable ? 'ok' : 'err'}`}>
        <Mark severity={deferred.deliverable ? 'ok' : 'error'} />
        <span className="msg">{deferred.message}</span>
      </div>

      {open.length > 0 && (
        <div className="hwv-block-tight">
          <div className="hwv-dim">未闭合的不可逆决策（不闭合不能导出）：</div>
          {open.map((item) => (
            <div key={item} className="hwv-row warn">
              <Mark severity="warning" />
              <span className="msg">
                <b>{item}</b> — {reasons[item] ?? ''}
              </span>
              <button className="hwv-btn" onClick={() => { setName(item); setValue('') }}>
                闭合
              </button>
            </div>
          ))}
        </div>
      )}

      {name !== '' && (
        <div className="hwv-inline hwv-block-tight">
          <span className="hwv-dim">{name} 的取值</span>
          <input className="hwv-input hwv-inline-stretch" value={value}
                 placeholder="例如 SMD 0603 / JST-PH / 5V USB / 单 app 无 OTA"
                 onChange={(event) => { setValue(event.target.value) }} />
          <button className="hwv-btn primary" onClick={() => {
            if (value.trim() !== '') props.onLock(name, value.trim())
            setName('')
          }}>确认</button>
        </div>
      )}

      {(deferred.locked ?? []).length > 0 && (
        <div className="hwv-block">
          <div className="hwv-dim">已闭合：</div>
          {(deferred.locked ?? []).map((lock) => (
            <div key={lock.name} className="hwv-row ok">
              <Mark severity="ok" />
              <span className="msg"><b>{lock.name}</b> = {lock.value}
                <span className="hwv-dim"> (at {lock.at})</span></span>
            </div>
          ))}
        </div>
      )}

      {(deferred.deferred ?? []).length > 0 && (
        <details className="hwv-block-tight">
          <summary className="hwv-dim">可延后的 {deferred.deferred?.length} 项</summary>
          {(deferred.deferred ?? []).map((fact) => (
            <div key={fact.kind + fact.ref} className="hwv-row info">
              <Mark severity="info" />
              <span className="msg">{fact.kind} · {fact.ref} — {fact.why}
                <span className="hwv-dim"> ({fact.at})</span></span>
            </div>
          ))}
        </details>
      )}
    </div>
  )
}

export function CrosscheckPanel(props: PanelProps): JSX.Element {
  const cross: CrosscheckPayload = props.snapshot.crosscheck
  if (cross.ok === null || cross.mismatches === undefined) {
    return (
      <div className="hwv-body">
        <div className="hwv-row info"><Mark severity="info" />
          <span className="msg">核对不可用：{cross.error ?? '未知原因'}</span></div>
      </div>
    )
  }
  return (
    <div className="hwv-body">
      <div className="hwv-summary">
        {cross.summary.mismatches} 处差异 · {cross.summary.unverified} 项未验证 ·
        {cross.px_per_mm.toFixed(4)} px/mm
        <button className="hwv-btn" onClick={() => { props.onCrosscheck() }}>重跑</button>
      </div>
      {cross.mismatches.map((mismatch, index) => (
        <div key={index} className={`hwv-row ${mismatch.severity === 'error' ? 'err' : 'warn'}`}>
          <Mark severity={mismatch.severity === 'error' ? 'error' : 'warning'} />
          <span className="msg">{mismatch.message}</span>
          {mismatch.refs.map((ref) => (
            <span key={ref} className="hwv-refs"
                  onClick={() => { props.onSelect(ref) }}>{ref}</span>
          ))}
        </div>
      ))}
      {cross.mismatches.length === 0 && (
        <div className="hwv-row ok"><Mark severity="ok" /><span className="msg">没有矛盾</span></div>
      )}
      {cross.not_verified.length > 0 && (
        <details className="hwv-block-tight">
          <summary className="hwv-dim">未验证的 {cross.not_verified.length} 项</summary>
          {cross.not_verified.map((line) => (
            <div key={line} className="hwv-row info"><Mark severity="info" />
              <span className="msg">{line}</span></div>
          ))}
        </details>
      )}
    </div>
  )
}

export function EvidencePanel(props: PanelProps): JSX.Element {
  const evidence: EvidencePayload = props.snapshot.evidence
  if (evidence.status === 'not_ready') {
    return (
      <div className="hwv-body">
        <div className="hwv-row err"><Mark severity="error" />
          <span className="msg">证据不可用：{evidence.error}</span></div>
      </div>
    )
  }
  const trace = evidence.trace
  const ok = trace.content_ok !== false && trace.chain_ok
  return (
    <div className="hwv-body">
      <dl className="hwv-kv">
        <dt>设计哈希</dt><dd className="hwv-mono">{evidence.design_hash}</dd>
        <dt>轨迹事件</dt><dd className="hwv-mono">{trace.events}</dd>
        <dt>哈希链</dt>
        <dd style={{ color: ok ? 'var(--hwv-ok)' : 'var(--hwv-err)' }}>
          {ok ? '完整' : '被改动'}
          {trace.tampered_seqs !== undefined && trace.tampered_seqs.length > 0
            && <span>（事件 {trace.tampered_seqs.join(', ')} 内容对不上）</span>}
        </dd>
        <dt>链头</dt><dd className="hwv-mono">{trace.head}</dd>
        <dt>交付闸门</dt>
        <dd style={{ color: evidence.gate.deliverable ? 'var(--hwv-ok)' : 'var(--hwv-warn)' }}>
          {evidence.gate.message}
        </dd>
        <dt>核对时间</dt><dd className="hwv-mono">{evidence.checked_at}</dd>
      </dl>
      <div className="hwv-dim hwv-block-tight">
        轨迹是 append-only 的哈希链：每次改模型都会追加一条事件。
        「被改动」表示有事件的 payload 与其哈希对不上——这时任何"已完成"的说法都不可信。
      </div>
    </div>
  )
}

export function ExportPanel(props: PanelProps): JSX.Element {
  const deferred = props.snapshot.deferred
  const deliverable = deferred.deliverable === true
  return (
    <div className="hwv-body">
      <div className={`hwv-row ${deliverable ? 'ok' : 'warn'}`}>
        <Mark severity={deliverable ? 'ok' : 'warning'} />
        <span className="msg">
          {deliverable ? '不可逆决策已闭合，可以导出'
            : `导出被闸门拦住：${(deferred.open_irreversible ?? []).join(', ')} 未闭合`}
        </span>
      </div>
      <div className="hwv-inline hwv-block-tight">
        <button className="hwv-btn primary" disabled={!deliverable}
                onClick={props.onExport}>导出制造包</button>
        <span className="hwv-dim">
          含 BOM / 贴片坐标 / 板轮廓 DXF / 规范模型 / README；不伪造 Gerber
        </span>
      </div>
      <div className="hwv-dim hwv-block-tight">
        {props.snapshot.project.artifacts['export.json'] === true
          ? '已导出过，产物在 project 目录的 fab/ 下'
          : '尚未导出'}
      </div>
    </div>
  )
}

/**
 * 发射面板（P46）：8 段状态 + 产物 + 最终连通性。
 *
 * ## 为什么这一屏必须存在
 *
 * 发射是本项目**最贵**的一步（2 板 24 件实测 185s；Bridge 通道 76s），而此前
 * UI 上**完全看不到**：跑完只会"刷新一下"，人还得回聊天记录里读。这一屏只做
 * 两件事：把 8 段如实画出来，以及给"只重验"一个按钮。
 *
 * **`verify` 那一段是权威**（铺铜之后的连通性）；`route` 段只报"布了多少段"。
 */
/** 发射的 8 段（顺序 = Python `JOBS`；`layout` 不碰 EDA，但也是报告里的一段）。 */
const EMIT_STAGES = ['project', 'layout', 'schematic', 'pcb',
                     'route', 'pour', 'verify', 'outputs'] as const
const EMIT_STAGE_LABEL: Readonly<Record<string, string>> = {
  project: '工程', layout: '布局规则', schematic: '原理图', pcb: 'PCB',
  route: '布线', pour: '铺铜', verify: '连通', outputs: '产物',
}
const EMIT_STATUS_LABEL: Readonly<Record<string, string>> = {
  ok: '通过', not_ready: '未就绪', failed: '失败',
}

export function EmitPanel(props: PanelProps): JSX.Element {
  const emit = props.snapshot.emit
  const [copied, setCopied] = useState(false)
  if (emit === undefined) {
    return (
      <div className="hwv-body">
        <div className="hwv-row warn">
          <Mark severity="warning" />
          <span className="msg">这一版快照里没有发射摘要（后端没给 emit 字段）</span>
        </div>
      </div>
    )
  }
  const jobs = emit.jobs ?? []
  const artifacts = emit.artifacts ?? []
  const verify = emit.verify
  const fab = emit.fabrication ?? {}
  const fabStatus = typeof fab.status === 'string' ? fab.status : ''
  const total = artifacts.reduce((sum, a) => sum + a.size, 0)

  const copyDir = async (): Promise<void> => {
    // **不替人打开资源管理器**（那是宿主的动作，这里只给能粘的路径）。
    const path = `artifacts/`
    try {
      await navigator.clipboard.writeText(path)
      setCopied(true)
    } catch {
      setCopied(false)          // 复制失败**不假装成功**（P1 的同一条）
    }
  }

  return (
    <div className="hwv-body">
      <div className={`hwv-row ${emit.status === 'ok' ? 'ok' : 'warn'}`}>
        <Mark severity={emit.status === 'ok' ? 'ok' : 'warning'} />
        <span className="msg">
          {emit.status === 'not_ready'
            ? (emit.reason ?? '还没发射过')
            : `发射 ${emit.status} —— ${emit.note ?? ''}`}
        </span>
      </div>

      {props.emitRunning === true && (
        <div className="hwv-banner info" role="status" aria-live="polite">
          正在{props.snapshot.emit === undefined ? '发射' : '执行'}…每段结束会自动刷新，不必重复点
        </div>
      )}

      {/* 8 段时间线（P2）：每段一行，可单独重跑。**状态只做投影**——
          流水线在 Python，这里不猜“应该到哪一段了”。 */}
      <div className="hwv-emit-stages" role="list" aria-label="发射阶段">
        {EMIT_STAGES.map((stage) => {
          const row = jobs.find((job) => job.name === stage)
          const status = row === undefined ? '' : row.status
          return (
            <div key={stage} role="listitem"
                 className={`hwv-emit-stage ${status === '' ? 'idle' : status}`}
                 title={row?.note ?? '这一段还没跑'}>
              <span className="hwv-emit-dot" aria-hidden="true" />
              <span className="hwv-emit-name">{EMIT_STAGE_LABEL[stage] ?? stage}</span>
              <span className="hwv-emit-status">
                {row === undefined ? '未跑' : (EMIT_STATUS_LABEL[status] ?? status)}
              </span>
              <button type="button" className="hwv-btn hwv-emit-retry"
                      disabled={props.onEmitRunStage === undefined || props.emitRunning === true}
                      onClick={() => { props.onEmitRunStage?.(stage) }}>
                重跑
              </button>
            </div>
          )
        })}
      </div>

      <div className="hwv-inline hwv-block-tight">
        <button className="hwv-btn primary" disabled={props.onEmitRun === undefined}
                title="跑 8 段（几十秒到几分钟，中途不要重复点）"
                onClick={() => { props.onEmitRun?.() }}>发射</button>
        <button className="hwv-btn" disabled={props.onEmitReverify === undefined}
                title="只跑 verify：铺铜之后的连通性，是能不能打板的最终结论"
                onClick={() => { props.onEmitReverify?.() }}>只重验</button>
        <button className="hwv-btn" disabled={artifacts.length === 0}
                onClick={() => { void copyDir() }}>{copied ? '已复制' : '复制产物目录'}</button>
        {verify !== undefined && (
          <span className="hwv-dim">
            连通 {verify.connected}/{verify.nets}
            {verify.split > 0 ? `（断 ${verify.split}）` : ''}
          </span>
        )}
      </div>

      {jobs.length === 0
        ? <div className="hwv-dim">没有分段记录（emit.json 里没有 jobs）</div>
        : (
          <div className="hwv-block-tight">
            {jobs.map((job) => (
              <div key={job.name}
                   className={`hwv-row ${job.status === 'ok' ? 'ok' : job.status === 'failed' ? 'err' : 'warn'}`}>
                <Mark severity={job.status === 'ok' ? 'ok'
                  : job.status === 'failed' ? 'error' : 'warning'} />
                <span className="msg">
                  <strong>{job.name}</strong>
                  <span className="hwv-dim"> · {job.status}</span>
                  {job.note !== '' ? ` —— ${job.note}` : ''}
                </span>
              </div>
            ))}
          </div>
        )}

      {fabStatus !== '' && (
        <div className={`hwv-row hwv-block-tight ${fabStatus === 'ok' ? 'ok' : 'warn'}`}>
          <Mark severity={fabStatus === 'ok' ? 'ok' : 'warning'} />
          <span className="msg">
            制造结论：{fabStatus}
            {Array.isArray(fab.reasons) && (fab.reasons as unknown[]).length > 0
              ? ` —— ${(fab.reasons as string[]).join('；')}`
              : '（DRC 分级 + DFM，见 hw_dfm）'}
          </span>
        </div>
      )}

      {artifacts.length > 0 && (
        <div className="hwv-block-tight">
          <div className="hwv-dim">产物 {artifacts.length} 个（{(total / 1024).toFixed(1)} KB）：</div>
          {artifacts.map((a) => (
            <div key={`${a.kind}:${a.name}`} className="hwv-row">
              <span className="msg">
                {a.kind} · <code>{a.name}</code>
                <span className="hwv-dim"> · {a.size} B</span>
              </span>
            </div>
          ))}
        </div>
      )}
      <div className="hwv-dim hwv-block-tight">
        产物在工程目录的 <code>artifacts/</code> 下。**`verify` 段才是"能不能打板"**；
        `route` 段只报"布了多少段"（铺铜前判会误判，见 AGENTS §3.27）。
      </div>
    </div>
  )
}

/**
 * 半成品面板（P46）：`scaffold` 的段状态 + `open_items` 待办。
 *
 * `open_items` **不是闸门**：工程已经是可用的半成品，这些是"我没把握的地方"。
 * 所以这里**没有**"必须先清空才能导出"这类按钮 —— 那会把半成品变回未完成品。
 */
export function BriefPanel(props: PanelProps): JSX.Element {
  const scaffold = props.snapshot.scaffold
  if (scaffold === undefined) {
    return (
      <div className="hwv-body">
        <div className="hwv-row warn">
          <Mark severity="warning" />
          <span className="msg">这一版快照里没有半成品摘要（后端没给 scaffold 字段）</span>
        </div>
      </div>
    )
  }
  const stages = scaffold.stages ?? []
  const items = scaffold.open_items ?? []
  const empty = scaffold.status === 'not_ready'
  return (
    <div className="hwv-body">
      <div className={`hwv-row ${empty ? 'warn' : 'ok'}`}>
        <Mark severity={empty ? 'warning' : 'ok'} />
        <span className="msg">
          {empty ? (scaffold.reason ?? '还没跑过 scaffold')
            : `半成品 ${scaffold.status}（route=${scaffold.route ?? '?'}）—— 待办 ${scaffold.count} 条`}
        </span>
      </div>

      {stages.length > 0 && (
        <div className="hwv-block-tight">
          {stages.map((stage) => (
            <div key={stage.stage}
                 className={`hwv-row ${stage.status === 'ok' ? 'ok'
                   : stage.status === 'failed' ? 'err' : 'warn'}`}>
              <Mark severity={stage.status === 'ok' ? 'ok'
                : stage.status === 'failed' ? 'error' : 'warning'} />
              <span className="msg">
                <strong>{stage.stage}</strong>
                <span className="hwv-dim"> · {stage.status}</span>
                {stage.note !== '' ? ` —— ${stage.note}` : ''}
              </span>
            </div>
          ))}
        </div>
      )}

      {items.length > 0 && (
        <div className="hwv-block-tight">
          <div className="hwv-dim">待办（改完重跑 scaffold 就会消失）：</div>
          {items.map((item, index) => (
            <div key={`${index}:${item.what}`} className="hwv-row">
              <span className="msg">
                {item.what}
                {item.need !== '' ? <span className="hwv-dim"> · 需要：{item.need}</span> : null}
              </span>
            </div>
          ))}
        </div>
      )}
      {items.length === 0 && !empty && (
        <div className="hwv-dim hwv-block-tight">
          没有待办。<span className="hwv-dim">但这**也可能只是没跑过 scaffold**。</span>
        </div>
      )}
      <div className="hwv-dim hwv-block-tight">
        半成品允许带待办：**它不阻塞导出**（AGENTS §3.40）。
      </div>
    </div>
  )
}

export function BlocksPanel(props: PanelProps & {
  onRunBlock: (block: string, params: string) => void
  hwforge: { available: boolean; capabilities: number; root: string }
}): JSX.Element {
  const [block, setBlock] = useState('assemble')
  const [params, setParams] = useState('{}')
  return (
    <div className="hwv-body">
      <div className={`hwv-row ${props.hwforge.available ? 'ok' : 'err'}`}>
        <Mark severity={props.hwforge.available ? 'ok' : 'error'} />
        <span className="msg">
          hwforge {props.hwforge.available ? `可用（${props.hwforge.capabilities} 个积木）` : '不可用'}
          <span className="hwv-dim"> · {props.hwforge.root || '未解析到 checkout'}</span>
        </span>
      </div>
      <div className="hwv-inline hwv-block-tight">
        <input className="hwv-input" style={{ width: 140, flex: '0 0 auto' }} value={block}
               onChange={(event) => { setBlock(event.target.value) }} />
        <input className="hwv-input hwv-inline-stretch" value={params}
               onChange={(event) => { setParams(event.target.value) }} />
        <button className="hwv-btn" disabled={!props.hwforge.available}
                onClick={() => { props.onRunBlock(block, params) }}>运行</button>
      </div>
      <div className="hwv-dim hwv-block-tight">
        积木跑在工程的 project/&lt;id&gt;/ 目录里，产物与判定由 hwforge 写。
        这里是跨层判定，不替代本工程自己的物理检查。
      </div>
    </div>
  )
}

export function ExportResult(props: { result: ExportPayload | null }): JSX.Element | null {
  const { result } = props
  if (result === null) return null
  if (result.blocked === true) {
    return (
      <div className="hwv-banner">
        导出被拒绝：{(result.findings ?? []).join('；')}
      </div>
    )
  }
  return (
    <div className="hwv-banner info">
      已写出 {result.path}
      {result.written !== undefined && ` · ${result.written.length} 个文件`}
    </div>
  )
}

/**
 * Velxio 后端面板。
 *
 * 这个页签存在的理由：`velxioHealth` / `velxioBoards` / `capacities` 三个
 * 宿主端点**早就实现了，却从来没有被客户端调用过**——是三个死端点。
 * 面板上完全看不到后端状态，用户只能靠模型调 `hw_block` 才知道"编译失败
 * 是因为后端没起"。
 *
 * 设计纪律（与本仓库其它面板一致）：
 *
 * - `not_ready`（没起）与 `failed`（起了但坏）**分开显示**，因为处理方式
 *   不同：前者点一下启动，后者要看日志。
 * - **不把"没装工具链"显示成"后端不可用"**：后端可以健康而 arduino-cli
 *   缺失，那时 `/health` 是 200 但编译会失败。两个状态分开报。
 */
export function VelxioPanel(props: PanelProps & {
  status: VelxioStatus | null
  onRefresh: () => void
  onStart: () => void
  onStop: () => void
}): JSX.Element {
  const { status } = props
  if (status === null) {
    return (
      <div className="hwv-body">
        <div className="hwv-row">
          <Mark severity="info" />
          <span className="msg">正在读 Velxio 后端状态…</span>
        </div>
        <div className="hwv-inline hwv-block-tight">
          <button className="hwv-btn" onClick={props.onRefresh}>刷新</button>
        </div>
      </div>
    )
  }
  const reachable = status.reachable === true
  const tri = String(status.status ?? 'failed')
  const severity: 'ok' | 'error' | 'warning' = reachable ? 'ok' : tri === 'failed' ? 'error' : 'warning'
  const label = reachable ? '运行中' : tri === 'failed' ? '异常' : '未启动'
  const findings = Array.isArray(status.findings) ? status.findings.map(String) : []
  const toolchain = (status.toolchain ?? {}) as Record<string, unknown>
  return (
    <div className="hwv-body">
      <div className={`hwv-row ${severity === 'ok' ? 'ok' : severity === 'error' ? 'err' : ''}`}>
        <Mark severity={severity} />
        <span className="msg">
          Velxio 后端：{label}
          {typeof status.url === 'string' && <span className="hwv-dim"> · {status.url}</span>}
        </span>
      </div>
      <div className="hwv-dim hwv-block-tight">
        端口 {String(status.port ?? '')}
        {status.pid !== undefined && status.pid !== null && ` · PID ${String(status.pid)}`}
        {typeof status.pid_source === 'string' && status.pid_source !== ''
          && `（来源 ${status.pid_source}）`}
      </div>
      <div className={`hwv-row ${toolchain.arduino_cli_ready === true ? 'ok' : 'err'}`}>
        <Mark severity={toolchain.arduino_cli_ready === true ? 'ok' : 'error'} />
        <span className="msg">
          arduino-cli：{toolchain.arduino_cli_ready === true ? '就绪' : '未安装'}
          <span className="hwv-dim">
            {' '}· QEMU {toolchain.qemu_ready === true ? '就绪' : '未安装（ESP32 仿真不可用）'}
          </span>
        </span>
      </div>
      {!reachable && findings.length > 0 && (
        <div className="hwv-body hwv-block-tight">
          {findings.map((line, index) => (
            <div key={index} className="hwv-dim">! {line}</div>
          ))}
        </div>
      )}
      <div className="hwv-inline hwv-block-tight">
        <button className="hwv-btn" onClick={props.onRefresh}>刷新</button>
        <button className="hwv-btn" onClick={props.onStart} disabled={reachable}>启动</button>
        <button className="hwv-btn" onClick={props.onStop} disabled={!reachable}>停止</button>
      </div>
      <div className="hwv-dim hwv-block-tight">
        后端是**本机 venv 里的 uvicorn**，不需要浏览器、不需要 Docker。
        启动后 hw_compile 才可用；hw_circuit 不需要后端。
      </div>
    </div>
  )
}

/** Velxio 状态载荷：字段都可选——面板对"读不到"和"起来了但缺工具链"必须等重。 */
export interface VelxioStatus {
  readonly status?: string
  readonly reachable?: boolean
  readonly url?: string
  readonly port?: number
  readonly pid?: number | null
  readonly pid_source?: string
  readonly findings?: readonly string[]
  readonly toolchain?: Record<string, unknown>
}

/**
 * AVR 仿真面板：起会话、推进、看串口。
 *
 * 与 `VelxioPanel` 分开的理由：那个管**后端**（编译服务），这个管**执行**
 * （avr8js 在宿主 Node 里跑）。两者的失败模式完全不同——后端没起不影响
 * 仿真（只要已有 hex），而仿真没起也不影响编译。
 *
 * **明确不提供**：元件动画。Velxio 前端的 150+ Web Components 需要浏览器，
 * 与本插件"纯 DSH"的前提冲突。这里给的是**串口字节流**——那是无可视化时
 * 仍然能验证固件行为的唯一东西。
 */
export function SimPanel(props: PanelProps & {
  sim: AvrSimStatus | null
  serial: string
  onStart: () => void
  onStop: () => void
  onRun: () => void
  onRead: () => void
}): JSX.Element {
  const { sim } = props
  const running = sim?.running === true
  return (
    <div className="hwv-body">
      <div className={`hwv-row ${running ? 'ok' : ''}`}>
        <Mark severity={running ? 'ok' : 'info'} />
        <span className="msg">
          AVR 仿真：{running ? `运行中（${String(sim?.board ?? '')}）` : '未启动'}
          <span className="hwv-dim"> · avr8js 在宿主 Node 内跑，不需要浏览器</span>
        </span>
      </div>
      {running && (
        <div className="hwv-dim hwv-block-tight">
          指令 {String(sim?.instructions ?? 0)}（{String(sim?.simMs ?? 0)} ms 仿真）
          {' · '}串口累计 {String(sim?.serialTotal ?? 0)} 字节
          {' · '}待读 {String(sim?.serialPending ?? 0)}
        </div>
      )}
      <div className="hwv-inline hwv-block-tight">
        <button className="hwv-btn" onClick={props.onStart} disabled={running}>起会话</button>
        <button className="hwv-btn" onClick={props.onRun} disabled={!running}>推进</button>
        <button className="hwv-btn" onClick={props.onRead} disabled={!running}>读串口</button>
        <button className="hwv-btn" onClick={props.onStop} disabled={!running}>停止</button>
      </div>
      <pre className="hwv-mono hwv-block-tight" style={{
        whiteSpace: 'pre-wrap', wordBreak: 'break-all', margin: 0,
        maxHeight: 260, overflow: 'auto', minHeight: 80,
      }}>
        {props.serial === '' ? '（串口暂无输出）' : props.serial}
      </pre>
      <div className="hwv-dim hwv-block-tight">
        支持 ATmega328p（Uno/Nano/Mini/Pro Mini）。Mega2560 的寄存器不同，
        ESP32 需要 QEMU —— 两者都会如实拒绝，不硬套。
        {' '}不支持板卡时先 hw_compile 换一块，或看 docs/p4-simulation.md。
      </div>
    </div>
  )
}

/** 仿真状态载荷（字段都可选：读不到 ≠ 没在跑）。 */
export interface AvrSimStatus {
  readonly running?: boolean
  readonly board?: string
  readonly instructions?: number
  readonly simMs?: number
  readonly serialTotal?: number
  readonly serialPending?: number
  readonly startedAt?: string
  readonly findings?: readonly string[]
}
