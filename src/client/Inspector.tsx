/**
 * 检查器：选中元件的属性与它自己的判定。
 *
 * 尺寸来源（`physical_source`）在这里是**可编辑的**，因为这正是最常见的
 * 真实动作：量了一个件、查到数据手册，就把 `estimated` 改成 `measured`。
 * 但改它要用户显式点，不能由程序自动"升级"——伪造来源比缺数据更危险。
 *
 * 样式纪律（前端方案 F1）：布局与间距全走令牌类（`hwv-*`），组件里不留
 * 裸 px/hex；唯一的内联值是把手给来的列宽。
 *
 * @module dsh-hw-velxio/client/Inspector
 */

import { useEffect, useState } from 'react'
import type { DesignPart, Finding, SnapshotPayload } from '../shared/types.js'

export interface InspectorProps {
  readonly snapshot: SnapshotPayload
  /** 列宽 px：把手拖动 + localStorage（styles.ts 不再钉死 268）。 */
  readonly width: number
  readonly selectedRef: string
  readonly findings: readonly Finding[]
  readonly onSelect: (ref: string) => void
  readonly onMove: (ref: string, xMm: number, yMm: number, rotationZ: number, zMm: number) => void
  readonly onSetPart: (ref: string, patch: Record<string, unknown>) => void
  readonly onDelete: (ref: string) => void
  readonly onAdd: (ref: string, kind: string, role: string, xMm: number, yMm: number) => void
}

const SOURCE_LABEL: Record<string, string> = {
  datasheet: '数据手册',
  measured: '实测',
  derived: '推导',
  estimated: '估算（不可当精确值）',
}

export function Inspector(props: InspectorProps): JSX.Element {
  const { snapshot, selectedRef } = props
  const part = snapshot.design.parts.find((p) => p.ref === selectedRef)
  const [newRef, setNewRef] = useState('')
  const [newKind, setNewKind] = useState('led')

  /**
   * 数据完整性提示 —— 来自 `design.meta` 的**机器事实**，前端不自己推断。
   *
   * 两条最要紧的：
   *   1. 只导入了 8 块板中的 1 块（`parts_skipped`）；
   *   2. 从 BOM 补的元件位置是排布不是布局（`bom_fill.placement`）。
   * 用户在看一板元件时必须知道这两个限制，否则会把估算当事实。
   */
  const meta = snapshot.design.meta as Record<string, unknown>
  const provenanceNotes: string[] = []
  const skipped = Number(meta['parts_skipped'] ?? 0)
  const sourceBoards = Number(meta['source_boards'] ?? 0)
  if (Number.isFinite(skipped) && skipped > 0) {
    provenanceNotes.push(
      `本工程共 ${sourceBoards} 块板；当前模型是单板的，只导入了主板的 `
      + `${snapshot.design.parts.length - Math.round(Number(
        (meta['bom_fill'] as { added?: number } | undefined)?.added ?? 0))} 个元件，`
      + `${skipped} 个元件（其他 ${sourceBoards - 1} 块板）未导入、也未参与检查。`)
  }
  const bomFill = meta['bom_fill'] as { added?: number; unsized?: number } | undefined
  if (bomFill !== undefined && (bomFill.added ?? 0) > 0) {
    provenanceNotes.push(
      `其中 ${bomFill.added} 个元件来自 BOM：位置是**网格排布**，不是真实 PCB 布局，`
      + `不能照着布线。${(bomFill.unsized ?? 0) > 0
        ? `另有 ${bomFill.unsized} 个封装查不到、尺寸标 estimated。` : ''}`)
  }

  if (part === undefined) {
    return (
      <div className="hwv-inspector" style={{ width: props.width }}>
        <h4>检查器</h4>
        <div className="hwv-dim hwv-note">
          没有选中对象。<br />
          在 2D 或 3D 里点一个元件，两边会同步高亮。
        </div>
        <h4>加一个元件</h4>
        <div className="hwv-form">
          <input className="hwv-input" placeholder="参考号，如 LED1" value={newRef}
                 onChange={(event) => { setNewRef(event.target.value) }} />
          <input className="hwv-input" placeholder="类型，如 led / esp32 / servo" value={newKind}
                 onChange={(event) => { setNewKind(event.target.value) }} />
          <button className="hwv-btn" disabled={newRef.trim() === ''}
                  onClick={() => {
                    props.onAdd(newRef.trim(), newKind.trim() || 'unknown', 'component', 5, 5)
                    setNewRef('')
                  }}>添加到板角</button>
          <div className="hwv-dim">
            尺寸从内置尺寸表查；查不到的件会以 <b>estimated 0 尺寸</b> 加入，
            3D 显示为薄片、判定面板会单列它。
          </div>
        </div>
        <h4>工程</h4>
        <dl className="hwv-kv">
          <dt>板卡</dt><dd>{snapshot.design.board.name}</dd>
          <dt>尺寸</dt>
          <dd className="hwv-mono">{snapshot.design.board.size_mm.w.toFixed(2)} ×
            {snapshot.design.board.size_mm.h.toFixed(2)} mm</dd>
          <dt>厚度</dt><dd className="hwv-mono">{snapshot.design.board.thickness_mm} mm</dd>
          <dt>元件</dt><dd className="hwv-mono">{snapshot.design.parts.length}</dd>
          <dt>网表</dt><dd className="hwv-mono">{snapshot.design.nets.length}</dd>
        </dl>
        {/* 数据来源的**限制**必须在这里说，不能只写在日志里：
            "这个工程只导入了 8 块板中的 1 块" 是用户最需要知道的一句话。 */}
        {provenanceNotes.length > 0 && (
          <>
            <h4>数据完整性</h4>
            <ul className="hwv-provenance">
              {provenanceNotes.map((note) => (
                <li key={note}>{note}</li>
              ))}
            </ul>
          </>
        )}
      </div>
    )
  }

  const issues = props.findings.filter((f) => !f.ok && f.refs.includes(part.ref))

  return (
    <div className="hwv-inspector" style={{ width: props.width }}>
      <h4>检查器 · {part.ref}</h4>
      <dl className="hwv-kv">
        <dt>类型</dt><dd>{part.kind}</dd>
        <dt>标签</dt><dd>{part.label}</dd>
        <dt>角色</dt><dd>{part.role}</dd>
        <dt>尺寸</dt>
        <dd className="hwv-mono">
          {part.size_mm.w} × {part.size_mm.h} × {part.size_mm.d} mm
        </dd>
        <dt>来源</dt>
        <dd className={`hwv-src ${part.physical_source}`}>
          {SOURCE_LABEL[part.physical_source] ?? part.physical_source}
        </dd>
        {part.lcsc !== undefined && (<><dt>LCSC</dt><dd className="hwv-mono">{part.lcsc}</dd></>)}
      </dl>

      {part.physical_source === 'estimated' && (
        <div className="hwv-banner info" style={{ margin: 'var(--hwv-space-4) 0' }}>
          尺寸是估算值。3D 实体与判定都基于它，请核对后再当精确值使用。
        </div>
      )}

      <h4>位置（mm）</h4>
      <NumberRow label="X" value={part.placement.x_mm}
                 onCommit={(value) => {
                   props.onMove(part.ref, value, part.placement.y_mm,
                     part.placement.rotation_z, part.placement.z_mm)
                 }} />
      <NumberRow label="Y" value={part.placement.y_mm}
                 onCommit={(value) => {
                   props.onMove(part.ref, part.placement.x_mm, value,
                     part.placement.rotation_z, part.placement.z_mm)
                 }} />
      <NumberRow label="Z" value={part.placement.z_mm}
                 onCommit={(value) => {
                   props.onMove(part.ref, part.placement.x_mm, part.placement.y_mm,
                     part.placement.rotation_z, value)
                 }} />
      <div className="hwv-inline" style={{ marginTop: 'var(--hwv-space-3)' }}>
        <span className="hwv-dim">旋转</span>
        <button className="hwv-btn" onClick={() => {
          props.onMove(part.ref, part.placement.x_mm, part.placement.y_mm,
            (part.placement.rotation_z + 90) % 360, part.placement.z_mm)
        }}>{part.placement.rotation_z}° →</button>
      </div>

      <h4>尺寸与来源</h4>
      <SizeRow part={part} onSetPart={props.onSetPart} />

      <div className="hwv-btn-row">
        <button className="hwv-btn" onClick={() => {
          props.onSetPart(part.ref, { size_mm: part.size_mm, physical_source: 'measured' })
        }}>标为实测</button>
        <button className="hwv-btn" onClick={() => {
          props.onSetPart(part.ref, { size_mm: part.size_mm, physical_source: 'datasheet' })
        }}>标为手册</button>
        <button className="hwv-btn danger" onClick={() => { props.onDelete(part.ref) }}>
          删除
        </button>
      </div>

      <h4>该元件的判定</h4>
      {issues.length === 0
        ? <div className="hwv-dim">没有与它相关的未通过项。</div>
        : issues.map((finding) => (
          <div key={finding.check + finding.message}
               className={`hwv-row ${finding.severity === 'error' ? 'err' : 'warn'}`}>
            <span className="mark">{finding.severity === 'error' ? '✗' : '!'}</span>
            <span className="msg">{finding.message}</span>
          </div>
        ))}

      <h4>预算 claim</h4>
      <ClaimEditor part={part} onSetPart={props.onSetPart} />
    </div>
  )
}

function NumberRow(props: {
  label: string
  value: number
  onCommit: (value: number) => void
}): JSX.Element {
  const [text, setText] = useState(String(props.value))
  useEffect(() => { setText(String(props.value)) }, [props.value])
  return (
    <div className="hwv-inline" style={{ marginBottom: 'var(--hwv-space-2)' }}>
      <span className="hwv-dim" style={{ width: 18, flex: '0 0 18px' }}>{props.label}</span>
      <input className="hwv-input" value={text}
             onChange={(event) => { setText(event.target.value) }}
             onBlur={() => {
               const parsed = Number(text)
               if (Number.isFinite(parsed) && parsed !== props.value) props.onCommit(parsed)
               else setText(String(props.value))
             }}
             onKeyDown={(event) => { if (event.key === 'Enter') (event.target as HTMLInputElement).blur() }} />
    </div>
  )
}

function SizeRow(props: {
  part: DesignPart
  onSetPart: (ref: string, patch: Record<string, unknown>) => void
}): JSX.Element {
  const { part } = props
  const [w, setW] = useState(String(part.size_mm.w))
  const [h, setH] = useState(String(part.size_mm.h))
  const [d, setD] = useState(String(part.size_mm.d))
  useEffect(() => {
    setW(String(part.size_mm.w))
    setH(String(part.size_mm.h))
    setD(String(part.size_mm.d))
  }, [part.size_mm.w, part.size_mm.h, part.size_mm.d, part.ref])

  const commit = (): void => {
    const nextW = Number(w)
    const nextH = Number(h)
    const nextD = Number(d)
    if (![nextW, nextH, nextD].every((value) => Number.isFinite(value) && value >= 0)) return
    if (nextW === part.size_mm.w && nextH === part.size_mm.h && nextD === part.size_mm.d) return
    props.onSetPart(part.ref, {
      size_mm: { w: nextW, h: nextH, d: nextD },
      // 手工改过的尺寸不再算"数据手册"：来源至少降级为推导
      physical_source: part.physical_source === 'estimated' ? 'estimated' : 'derived',
    })
  }

  return (
    <div className="hwv-row-3">
      {([['w', w, setW], ['h', h, setH], ['d', d, setD]] as const).map(([key, value, setter]) => (
        <div key={key}>
          <div className="hwv-field-label">{key} (mm)</div>
          <input className="hwv-input" value={value}
                 onChange={(event) => { setter(event.target.value) }}
                 onBlur={commit}
                 onKeyDown={(event) => {
                   if (event.key === 'Enter') (event.target as HTMLInputElement).blur()
                 }} />
        </div>
      ))}
    </div>
  )
}

function ClaimEditor(props: {
  part: DesignPart
  onSetPart: (ref: string, patch: Record<string, unknown>) => void
}): JSX.Element {
  const { part } = props
  const electrical = part.electrical ?? {}
  const [power, setPower] = useState(String(electrical.power_w ?? ''))
  const [cost, setCost] = useState(String(electrical.cost_usd ?? ''))
  useEffect(() => {
    setPower(String((part.electrical ?? {}).power_w ?? ''))
    setCost(String((part.electrical ?? {}).cost_usd ?? ''))
  }, [part.ref])

  const commit = (): void => {
    const next: Record<string, number> = {}
    const powerValue = Number(power)
    const costValue = Number(cost)
    if (power !== '' && Number.isFinite(powerValue) && powerValue >= 0) next.power_w = powerValue
    if (cost !== '' && Number.isFinite(costValue) && costValue >= 0) next.cost_usd = costValue
    props.onSetPart(part.ref, { electrical: next })
  }

  return (
    <div className="hwv-row-3">
      <div>
        <div className="hwv-field-label">功率 (W)</div>
        <input className="hwv-input" value={power}
               onChange={(event) => { setPower(event.target.value) }} onBlur={commit} />
      </div>
      <div>
        <div className="hwv-field-label">成本 ($)</div>
        <input className="hwv-input" value={cost}
               onChange={(event) => { setCost(event.target.value) }} onBlur={commit} />
      </div>
    </div>
  )
}
