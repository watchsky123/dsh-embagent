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
  SnapshotPayload,
} from '../shared/types.js'

export interface PanelProps {
  readonly snapshot: SnapshotPayload
  readonly onSelect: (ref: string) => void
  readonly onCheck: () => void
  readonly onBudget: () => void
  readonly onCrosscheck: () => void
  readonly onLock: (name: string, value: string) => void
  readonly onExport: () => void
  readonly onLimit: (currency: string, value: number) => void
}

function Mark({ severity }: { severity: 'error' | 'warning' | 'info' | 'ok' }): JSX.Element {
  return <span className="mark">{severity === 'ok' ? '✓'
    : severity === 'info' ? '·'
      : severity === 'warning' ? '!' : '✗'}</span>
}

function FindingRow(props: {
  finding: Finding
  onSelect: (ref: string) => void
}): JSX.Element {
  const { finding, onSelect } = props
  const severity = finding.ok ? 'ok' : finding.severity
  const cls = finding.ok ? 'ok' : severity === 'warning' ? 'warn' : severity === 'info' ? 'info' : 'err'
  return (
    <div className={`hwv-row ${cls}`}>
      <Mark severity={severity as 'error' | 'warning' | 'info' | 'ok'} />
      <span className="msg">{finding.message}</span>
      {finding.refs.map((ref) => (
        <span key={ref} className="hwv-refs"
              onClick={() => { onSelect(ref) }}>{ref}</span>
      ))}
    </div>
  )
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
                    onSelect={props.onSelect} />
      ))}
      {failed.length === 0 && (
        <div className="hwv-row ok"><Mark severity="ok" /><span className="msg">全部检查通过</span></div>
      )}
      {passed.length > 0 && (
        <details className="hwv-block-tight">
          <summary className="hwv-dim">通过的 {passed.length} 项</summary>
          {passed.map((finding) => (
            <FindingRow key={finding.check + finding.message} finding={finding}
                        onSelect={props.onSelect} />
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
