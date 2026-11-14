/**
 * 「硬件工作台」—— IDE 外壳。
 *
 * 布局（v3 §5）：
 *
 *   ┌ 工具栏：工程 / 检查 / 预算 / 核对 / 导出 / 2D·3D·并排 / AI 评审 ┐
 *   ├ 2D 电路（电气权威） │ 3D 装配（物理权威） │ 检查器 ┤
 *   └ 底部：判定 / 预算 / 未决 / 核对 / 证据 / 制造 / 积木 ┘
 *
 * 2D 与 3D 共享 `selectedRef`：一个状态，两个视图。底部每个面板都是一等
 * 公民，不是 v1 那种"5 个平级 tab 谁都点不开"。
 *
 * @module dsh-hw-velxio/client/HwIdeView
 */

import { useEffect, useState } from 'react'
import { Assembly3D } from './Assembly3D.js'
import { Circuit2D } from './Circuit2D.js'
import { Inspector } from './Inspector.js'
import {
  BlocksPanel, BudgetPanel, ChecksPanel, CrosscheckPanel, DeferredPanel,
  EvidencePanel, ExportPanel, ExportResult,
} from './panels.js'
import type { WorkbenchFace } from './rpc.js'
import { store, useHw } from './store.js'
import type { PanelId } from './store.js'
import { composeReviewPrompt } from './view.js'

export interface HwIdeViewProps {
  readonly sessionId: string
  readonly face: WorkbenchFace
  readonly ai: { send: (text: string) => void }
}

const PANELS: Array<{ id: PanelId; label: string }> = [
  { id: 'checks', label: '判定' },
  { id: 'budget', label: '预算' },
  { id: 'deferred', label: '未决' },
  { id: 'crosscheck', label: '核对' },
  { id: 'evidence', label: '证据/轨迹' },
  { id: 'export', label: '制造' },
  { id: 'blocks', label: '积木' },
]

export function HwIdeView(props: HwIdeViewProps): JSX.Element {
  const state = useHw()

  useEffect(() => {
    void store.init(props.face)
    return () => { store.dispose() }
  }, [props.face])

  const snapshot = state.snapshot
  const findings = snapshot?.checks.findings ?? []
  const failed = findings.filter((f) => !f.ok)
  const errors = failed.filter((f) => f.severity === 'error')
  const warnings = failed.filter((f) => f.severity === 'warning')
  const deferred = snapshot?.deferred
  const openDecisions = deferred?.open_irreversible ?? []
  const mismatches = snapshot?.crosscheck.summary.mismatches ?? 0
  const budgetOver = (snapshot?.budget.violations ?? []).length

  const ask = (): void => {
    if (snapshot === null) return
    const prompt = composeReviewPrompt({
      project: snapshot.project.id,
      board: snapshot.design.board.name,
      verdicts: failed.map((finding) => ({
        check: finding.check,
        ok: finding.ok,
        measured: finding.measured,
        limit: finding.limit,
        message: finding.message,
      })),
      notVerified: [
        ...snapshot.checks.not_verified,
        ...(snapshot.budget.confidence === 'none'
          ? ['预算：没有任何 claim，无法评估'] : []),
        ...(openDecisions.length > 0
          ? [`未闭合的不可逆决策：${openDecisions.join(', ')}`] : []),
      ],
    })
    props.ai.send(prompt)
  }

  const panelCount = (id: PanelId): { text: string; cls: string } | null => {
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

  return (
    <div className="hwv-root">
      <div className="hwv-toolbar">
        <span className="hwv-title">硬件工作台</span>
        <select className="hwv-select" value={state.project}
                onChange={(event) => { void store.open(event.target.value) }}
                disabled={state.bootstrap === null}>
          {(state.bootstrap?.projects ?? []).length === 0
            ? <option value="">（还没有工程）</option>
            : (state.bootstrap?.projects ?? []).map((project) => (
              <option key={project.id} value={project.id}>
                {project.id} — {project.board_name ?? project.board ?? ''}
              </option>
            ))}
        </select>

        <button className="hwv-btn" disabled={snapshot === null || state.busy !== ''}
                onClick={() => { void store.runCheck() }}>检查</button>
        <button className="hwv-btn" disabled={snapshot === null || state.busy !== ''}
                onClick={() => { void store.runBudget() }}>预算</button>
        <button className="hwv-btn" disabled={snapshot === null || state.busy !== ''}
                onClick={() => { void store.runCrosscheck() }}>核对</button>
        <button className="hwv-btn primary"
                disabled={snapshot === null || state.busy !== '' || openDecisions.length > 0}
                onClick={() => { void store.exportBundle() }}>导出</button>

        <span className="hwv-spacer" />

        <div style={{ display: 'flex', gap: 2 }}>
          {(['2d', 'split', '3d'] as const).map((layout) => (
            <button key={layout}
                    className={`hwv-btn${state.layout === layout ? ' on' : ''}`}
                    onClick={() => { store.setLayout(layout) }}>
              {layout === '2d' ? '2D' : layout === '3d' ? '3D' : '并排'}
            </button>
          ))}
        </div>

        <button className="hwv-btn" disabled={snapshot === null} onClick={ask}>
          AI 评审
        </button>

        {snapshot !== null && (
          <>
            <span className={`hwv-chip ${errors.length > 0 ? 'err' : warnings.length > 0 ? 'warn' : 'ok'}`}>
              {errors.length > 0 ? `${errors.length} 错误`
                : warnings.length > 0 ? `${warnings.length} 警告` : '检查通过'}
            </span>
            <span className={`hwv-chip ${openDecisions.length > 0 ? 'warn' : 'ok'}`}>
              {openDecisions.length > 0 ? `未决 ${openDecisions.length}` : '可交付'}
            </span>
            {snapshot.budget.confidence === 'none' && (
              <span className="hwv-chip est">预算无可评数据</span>
            )}
          </>
        )}
      </div>

      {state.error !== null && (
        <div className="hwv-banner">
          <span>{state.error}</span>
          <span className="hwv-spacer" />
          <button className="hwv-btn" onClick={() => { store.clearNotice() }}>关闭</button>
        </div>
      )}
      {state.busy !== '' && <div className="hwv-banner info">正在{state.busy}…</div>}
      <ExportResult result={state.lastExport} />

      {snapshot === null
        ? <EmptyState face={props.face} />
        : (
          <div className="hwv-main">
            <div className="hwv-panes">
              <div className={`hwv-pane${state.layout === '3d' ? ' hidden' : ''}`}>
                <div className="hwv-pane-head">
                  2D 电路 · 电气/拓扑权威
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

              <div className={`hwv-pane${state.layout === '2d' ? ' hidden' : ''}`}>
                <div className="hwv-pane-head">
                  3D 装配 · 物理/机械权威
                  <span className="hwv-spacer" />
                  <span className="hwv-dim">
                    {snapshot.scene3d.parts.length} 实体
                  </span>
                </div>
                <Assembly3D
                  snapshot={snapshot}
                  selectedRef={state.selectedRef}
                  findings={findings}
                  onSelect={(ref) => { store.select(ref) }}
                />
              </div>
            </div>

            <Inspector
              snapshot={snapshot}
              selectedRef={state.selectedRef}
              findings={findings}
              onSelect={(ref) => { store.select(ref) }}
              onMove={(ref, x, y, rot, z) => { void store.movePart(ref, x, y, rot, z) }}
              onSetPart={(ref, patch) => { void store.setPart(ref, patch) }}
              onDelete={(ref) => { void store.deletePart(ref) }}
              onAdd={(ref, kind, role, x, y) => { void store.addPart(ref, kind, role, x, y) }}
            />
          </div>
        )}

      <div className="hwv-bottom">
        <div className="hwv-tabs">
          {PANELS.map((panel) => {
            const count = panelCount(panel.id)
            return (
              <button key={panel.id}
                      className={`hwv-tab${state.panel === panel.id ? ' active' : ''}`}
                      onClick={() => { store.setPanel(panel.id) }}>
                {panel.label}
                {count !== null && <span className={`count ${count.cls}`}>{count.text}</span>}
              </button>
            )
          })}
        </div>
        {snapshot === null
          ? <div className="hwv-body hwv-dim">先新建或导入一个工程。</div>
          : (
            <PanelBody
              state={state}
              snapshot={snapshot}
              onSelect={(ref) => { store.select(ref) }}
              onExport={() => { void store.exportBundle() }}
            />
          )}
      </div>
    </div>
  )
}

function PanelBody(props: {
  state: ReturnType<typeof useHw>
  snapshot: NonNullable<ReturnType<typeof useHw>['snapshot']>
  onSelect: (ref: string) => void
  onExport: () => void
}): JSX.Element {
  const common = {
    snapshot: props.snapshot,
    onSelect: props.onSelect,
    onCheck: () => { void store.runCheck() },
    onBudget: () => { void store.runBudget() },
    onCrosscheck: () => { void store.runCrosscheck() },
    onLock: (name: string, value: string) => { void store.lockDecision(name, value) },
    onExport: props.onExport,
    onLimit: (currency: string, value: number) => { void store.setBudgetLimit(currency, value) },
  }
  switch (props.state.panel) {
    case 'checks': return <ChecksPanel {...common} />
    case 'budget': return <BudgetPanel {...common} />
    case 'deferred': return <DeferredPanel {...common} />
    case 'crosscheck': return <CrosscheckPanel {...common} />
    case 'evidence': return <EvidencePanel {...common} />
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
    default: return <ChecksPanel {...common} />
  }
}

function EmptyState(props: { face: WorkbenchFace }): JSX.Element {
  const state = useHw()
  const boards = state.bootstrap?.boards ?? []
  const formats = state.bootstrap?.formats ?? []
  const [projectId, setProjectId] = useState('001-blink')
  const [board, setBoard] = useState('arduino-uno')
  const [title, setTitle] = useState('Blink')
  const [importPath, setImportPath] = useState('')
  const [bomPath, setBomPath] = useState('')

  return (
    <div className="hwv-main" style={{ display: 'block', overflow: 'auto', padding: 16 }}>
      <div className="hwv-empty">
        <strong>还没有工程</strong>
        新建一个，或导入一个 KiCad PCB / Wokwi diagram。
        导入后 2D 网表与 3D 实体同时成立。
      </div>

      <div style={{ display: 'flex', gap: 24, flexWrap: 'wrap', justifyContent: 'center' }}>
        <div style={{ minWidth: 300 }}>
          <h4 className="hwv-dim">新建</h4>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            <input className="hwv-input" placeholder="工程 id，如 001-blink" value={projectId}
                   onChange={(event) => { setProjectId(event.target.value) }} />
            <input className="hwv-input" placeholder="标题" value={title}
                   onChange={(event) => { setTitle(event.target.value) }} />
            <select className="hwv-select" value={board}
                    onChange={(event) => { setBoard(event.target.value) }}>
              {boards.map((preset) => (
                <option key={preset.kind} value={preset.kind}>
                  {preset.name} · {preset.size_mm.w}×{preset.size_mm.h}mm
                  {preset.source === 'estimated' ? '（估算）' : ''}
                </option>
              ))}
            </select>
            <button className="hwv-btn primary" disabled={state.busy !== ''}
                    onClick={() => {
                      void store.createProject(projectId, board, title)
                    }}>新建工程</button>
          </div>
        </div>

        <div style={{ minWidth: 340 }}>
          <h4 className="hwv-dim">导入</h4>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            <input className="hwv-input" placeholder="绝对或相对路径，如 hw_velxio/examples/kicad/sensor-node.kicad_pcb"
                   value={importPath}
                   onChange={(event) => { setImportPath(event.target.value) }} />
            <input className="hwv-input" placeholder="可选：BOM CSV 路径" value={bomPath}
                   onChange={(event) => { setBomPath(event.target.value) }} />
            <button className="hwv-btn" disabled={importPath.trim() === '' || state.busy !== ''}
                    onClick={() => { void store.importProject(importPath.trim(), bomPath.trim()) }}>
              导入
            </button>
            <div className="hwv-dim" style={{ fontSize: 11, lineHeight: 1.7 }}>
              支持：{formats.map((format) => format.ext).join(' / ') || '.kicad_pcb / .json / .csv'}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
