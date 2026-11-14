/**
 * 2D 电路视图 —— **电气/拓扑权威**。
 *
 * 画布只读 ``snapshot.projection2d``（mm 的投影），不自己从像素推几何：
 * 比例、Y 翻转、旋转都在 Python 侧算过一次，这里再算就是第二个算法。
 *
 * 交互：
 *   · 点元件 = 选中（3D 同步高亮）
 *   · 拖元件 = 改 placement（松手才提交，拖拽中只预览）
 *   · 右键元件 = 旋转 90°
 *   · 空白处拖 = 平移；滚轮 = 缩放
 *
 * @module dsh-hw-velxio/client/Circuit2D
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { Finding, SnapshotPayload } from '../shared/types.js'

interface View {
  scale: number
  ox: number
  oy: number
}

interface DragState {
  ref: string
  startX: number
  startY: number
  dx: number
  dy: number
}

interface PanState {
  startX: number
  startY: number
  ox: number
  oy: number
}

export interface Circuit2DProps {
  readonly snapshot: SnapshotPayload
  readonly selectedRef: string
  readonly findings: readonly Finding[]
  readonly onSelect: (ref: string) => void
  readonly onMove: (ref: string, xMm: number, yMm: number, rotationZ: number) => void
}

const SOURCE_FILL: Record<string, string> = {
  datasheet: '#21517a',
  measured: '#1f5c40',
  derived: '#3a4a6b',
  estimated: '#4a3a6b',
}

function hueOf(text: string): number {
  let hash = 0
  for (let index = 0; index < text.length; index += 1) {
    hash = (hash * 31 + text.charCodeAt(index)) >>> 0
  }
  return hash % 360
}

/** 某元件的最坏严重度，用于描边颜色。 */
function severityOf(ref: string, findings: readonly Finding[]): 'error' | 'warning' | null {
  let worst: 'error' | 'warning' | null = null
  for (const finding of findings) {
    if (finding.ok || !finding.refs.includes(ref)) continue
    if (finding.severity === 'error') return 'error'
    if (finding.severity === 'warning') worst = 'warning'
  }
  return worst
}

export function Circuit2D(props: Circuit2DProps): JSX.Element {
  const { snapshot, selectedRef, findings, onSelect, onMove } = props
  const wrapRef = useRef<HTMLDivElement | null>(null)
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const [view, setView] = useState<View>({ scale: 1, ox: 0, oy: 0 })
  const [drag, setDrag] = useState<DragState | null>(null)
  const panRef = useRef<PanState | null>(null)
  const dragRef = useRef<DragState | null>(null)
  const [size, setSize] = useState({ w: 0, h: 0 })

  const projection = snapshot.projection2d
  const design = snapshot.design
  const placementOf = useMemo(() => {
    const map = new Map<string, { x: number; y: number; rot: number }>()
    for (const part of design.parts) {
      map.set(part.ref, {
        x: part.placement.x_mm,
        y: part.placement.y_mm,
        rot: part.placement.rotation_z,
      })
    }
    return map
  }, [design.parts])

  dragRef.current = drag

  // -- 尺寸 ---------------------------------------------------------------

  useEffect(() => {
    const wrap = wrapRef.current
    if (wrap === null) return
    const observer = new ResizeObserver(() => {
      setSize({ w: wrap.clientWidth, h: wrap.clientHeight })
    })
    observer.observe(wrap)
    setSize({ w: wrap.clientWidth, h: wrap.clientHeight })
    return () => { observer.disconnect() }
  }, [])

  // 首次拿到板子时缩放到合适大小
  const fittedRef = useRef<string>('')
  useEffect(() => {
    if (size.w === 0 || size.h === 0) return
    const key = `${projection.board.kind}:${size.w}x${size.h}`
    if (fittedRef.current === key) return
    fittedRef.current = key
    const padding = 36
    const scale = Math.min(
      (size.w - padding * 2) / Math.max(projection.board.w_px, 1),
      (size.h - padding * 2) / Math.max(projection.board.h_px, 1),
    )
    const next = Math.max(0.05, Math.min(scale, 8))
    setView({
      scale: next,
      ox: (size.w - projection.board.w_px * next) / 2,
      oy: (size.h - projection.board.h_px * next) / 2,
    })
  }, [projection.board.kind, projection.board.w_px, projection.board.h_px, size])

  // -- 绘制 ---------------------------------------------------------------

  useEffect(() => {
    const canvas = canvasRef.current
    if (canvas === null || size.w === 0 || size.h === 0) return
    const dpr = globalThis.devicePixelRatio ?? 1
    canvas.width = Math.round(size.w * dpr)
    canvas.height = Math.round(size.h * dpr)
    const ctx = canvas.getContext('2d')
    if (ctx === null) return
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    draw(ctx, {
      projection, view, size, selectedRef, findings,
      drag: dragRef.current, placementOf,
    })
  }, [projection, view, size, selectedRef, findings, drag, placementOf])

  const toBoard = useCallback((clientX: number, clientY: number) => {
    const canvas = canvasRef.current
    if (canvas === null) return { x: 0, y: 0 }
    const rect = canvas.getBoundingClientRect()
    return {
      x: (clientX - rect.left - view.ox) / view.scale,
      y: (clientY - rect.top - view.oy) / view.scale,
    }
  }, [view])

  const hitTest = useCallback((bx: number, by: number): string => {
    for (let index = projection.parts.length - 1; index >= 0; index -= 1) {
      const part = projection.parts[index]
      if (part === undefined) continue
      if (bx >= part.x_px && bx <= part.x_px + part.w_px
          && by >= part.y_px && by <= part.y_px + part.h_px) {
        return part.ref
      }
    }
    return ''
  }, [projection.parts])

  // -- 指针 ---------------------------------------------------------------

  const onPointerDown = (event: React.PointerEvent<HTMLCanvasElement>): void => {
    const { x, y } = toBoard(event.clientX, event.clientY)
    const ref = hitTest(x, y)
    ;(event.target as HTMLElement).setPointerCapture(event.pointerId)
    if (ref !== '') {
      onSelect(ref)
      const selected = projection.parts.find((p) => p.ref === ref)
      if (event.button === 2) {
        // 右键：旋转 90°
        const placement = placementOf.get(ref)
        if (placement !== undefined && selected !== undefined) {
          onMove(ref, placement.x, placement.y, (placement.rot + 90) % 360)
        }
        return
      }
      setDrag({ ref, startX: event.clientX, startY: event.clientY, dx: 0, dy: 0 })
      return
    }
    panRef.current = { startX: event.clientX, startY: event.clientY, ox: view.ox, oy: view.oy }
    onSelect('')
  }

  const onPointerMove = (event: React.PointerEvent<HTMLCanvasElement>): void => {
    const currentDrag = dragRef.current
    if (currentDrag !== null) {
      setDrag({
        ...currentDrag,
        dx: event.clientX - currentDrag.startX,
        dy: event.clientY - currentDrag.startY,
      })
      return
    }
    const pan = panRef.current
    if (pan !== null) {
      setView({
        ...view,
        ox: pan.ox + (event.clientX - pan.startX),
        oy: pan.oy + (event.clientY - pan.startY),
      })
    }
  }

  const onPointerUp = (event: React.PointerEvent<HTMLCanvasElement>): void => {
    const currentDrag = dragRef.current
    panRef.current = null
    if (currentDrag === null) return
    setDrag(null)
    const dx = event.clientX - currentDrag.startX
    const dy = event.clientY - currentDrag.startY
    if (Math.abs(dx) < 3 && Math.abs(dy) < 3) return
    const placement = placementOf.get(currentDrag.ref)
    if (placement === undefined) return
    const ppm = projection.px_per_mm
    // 画布 Y 向下，模型 Y 向上：拖拽的 dy 要取反
    onMove(currentDrag.ref,
      round(placement.x + dx / (view.scale * ppm)),
      round(placement.y - dy / (view.scale * ppm)),
      placement.rot)
  }

  const onWheel = (event: React.WheelEvent<HTMLCanvasElement>): void => {
    event.preventDefault()
    const rect = (event.currentTarget as HTMLElement).getBoundingClientRect()
    const mx = event.clientX - rect.left
    const my = event.clientY - rect.top
    const factor = event.deltaY < 0 ? 1.12 : 1 / 1.12
    const scale = Math.max(0.05, Math.min(view.scale * factor, 12))
    setView({
      scale,
      ox: mx - (mx - view.ox) * (scale / view.scale),
      oy: my - (my - view.oy) * (scale / view.scale),
    })
  }

  return (
    <div className="hwv-canvas-wrap" ref={wrapRef}>
      <canvas
        className="hwv-canvas"
        ref={canvasRef}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerLeave={onPointerUp}
        onWheel={onWheel}
        onContextMenu={(event) => { event.preventDefault() }}
      />
      <div className="hwv-legend">
        <span><i style={{ background: SOURCE_FILL.datasheet }} />数据手册</span>
        <span><i style={{ background: SOURCE_FILL.derived }} />推导</span>
        <span><i style={{ background: SOURCE_FILL.estimated }} />估算</span>
        <span className="hwv-dim">
          {projection.px_per_mm.toFixed(4)} px/mm · {projection.board.size_mm.w.toFixed(2)}×
          {projection.board.size_mm.h.toFixed(2)} mm
        </span>
      </div>
    </div>
  )
}

function round(value: number): number {
  return Math.round(value * 100) / 100
}

interface DrawInput {
  projection: SnapshotPayload['projection2d']
  view: View
  size: { w: number; h: number }
  selectedRef: string
  findings: readonly Finding[]
  drag: DragState | null
  placementOf: Map<string, { x: number; y: number; rot: number }>
}

function draw(ctx: CanvasRenderingContext2D, input: DrawInput): void {
  const { projection, view, size, selectedRef, findings, drag } = input
  ctx.clearRect(0, 0, size.w, size.h)

  // 背景
  ctx.fillStyle = '#0f1115'
  ctx.fillRect(0, 0, size.w, size.h)

  ctx.save()
  ctx.translate(view.ox, view.oy)
  ctx.scale(view.scale, view.scale)

  const board = projection.board

  // 1mm 网格（只在板内画，避免整屏噪点）
  const step = projection.px_per_mm
  ctx.lineWidth = 1 / view.scale
  ctx.strokeStyle = 'rgba(255,255,255,0.045)'
  ctx.beginPath()
  for (let x = 0; x <= board.w_px; x += step) {
    ctx.moveTo(x, 0)
    ctx.lineTo(x, board.h_px)
  }
  for (let y = 0; y <= board.h_px; y += step) {
    ctx.moveTo(0, y)
    ctx.lineTo(board.w_px, y)
  }
  ctx.stroke()

  // 板轮廓
  ctx.beginPath()
  board.outline_px.forEach(([x, y], index) => {
    if (index === 0) ctx.moveTo(x, y)
    else ctx.lineTo(x, y)
  })
  ctx.closePath()
  ctx.fillStyle = '#17202b'
  ctx.fill()
  ctx.strokeStyle = '#3d5a75'
  ctx.lineWidth = 1.5 / view.scale
  ctx.stroke()

  // 安装孔
  for (const hole of projection.mount_holes) {
    ctx.beginPath()
    ctx.arc(hole.x_px, hole.y_px, Math.max(hole.d_px / 2, 1), 0, Math.PI * 2)
    ctx.fillStyle = '#0f1115'
    ctx.fill()
    ctx.strokeStyle = '#6e7c8c'
    ctx.lineWidth = 1 / view.scale
    ctx.stroke()
  }

  // 导线（语义：网表）
  for (const wire of projection.wires) {
    if (wire.path_px.length < 2) continue
    const hue = hueOf(wire.net)
    ctx.beginPath()
    wire.path_px.forEach(([x, y], index) => {
      if (index === 0) ctx.moveTo(x, y)
      else ctx.lineTo(x, y)
    })
    ctx.strokeStyle = `hsl(${hue} 70% 58%)`
    ctx.lineWidth = Math.max(1.2, 1.4 / view.scale)
    ctx.stroke()
  }

  // 元件
  for (const part of projection.parts) {
    const isSelected = part.ref === selectedRef
    const isDragged = drag?.ref === part.ref
    const offsetX = isDragged ? drag.dx / view.scale : 0
    const offsetY = isDragged ? drag.dy / view.scale : 0
    const x = part.x_px + offsetX
    const y = part.y_px + offsetY
    const severity = severityOf(part.ref, findings)

    ctx.fillStyle = SOURCE_FILL[part.physical_source] ?? '#3a3f4a'
    ctx.globalAlpha = 0.92
    ctx.fillRect(x, y, part.w_px, part.h_px)
    ctx.globalAlpha = 1

    ctx.lineWidth = (isSelected ? 2.2 : 1.2) / view.scale
    ctx.strokeStyle = isSelected ? '#7cc4ff'
      : severity === 'error' ? '#f85149'
        : severity === 'warning' ? '#d29922'
          : 'rgba(255,255,255,0.35)'
    ctx.strokeRect(x, y, part.w_px, part.h_px)

    // 参考号：缩放太小时不画，避免糊成一团
    if (part.w_px * view.scale > 26 && part.h_px * view.scale > 12) {
      ctx.fillStyle = '#e6edf3'
      const px = Math.max(9, Math.min(13, 11 / view.scale))
      ctx.font = `${px}px ui-monospace, Consolas, monospace`
      ctx.textBaseline = 'middle'
      ctx.fillText(part.ref, x + 3 / view.scale, y + 8 / view.scale)
      if (part.physical_source === 'estimated') {
        ctx.fillStyle = '#c8a6ff'
        ctx.fillText('?', x + part.w_px - 8 / view.scale, y + 8 / view.scale)
      }
    }
  }

  ctx.restore()
}
