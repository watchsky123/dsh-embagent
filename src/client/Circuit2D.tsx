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
 * @module dsh-embagent/client/Circuit2D
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { Finding, SnapshotPayload } from '../shared/types.js'
import { partFill, readPalette, useThemeRevision } from './theme.js'
import type { Palette } from './theme.js'

interface View {
  scale: number
  ox: number
  oy: number
}

/** 缩放夹取。下限给到 5%：再小板子就只剩几个像素，没有信息量。 */
const MIN_SCALE = 0.05
const MAX_SCALE = 12
/** 每次按键/按钮缩放的倍率（滚轮用指数，见 wheel 监听）。 */
const STEP = 1.2

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
  /** 空格键按住中：此时按下拖动 = 平移而非拖元件。 */
  const spaceRef = useRef(false)
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
  //
  // **按拼板尺寸**适配，不是按主板：多板工程里只适配主板会让其余板
  // 全部落在画布外（看起来像"只导了一块板"）。
  //
  // 抽成 `computeFit()` 是因为它现在有**三个**调用方：首次适配、双击复位、
  // 键盘 `0`。三处各写一遍缩放算式，迟早会出现"双击复位后和刚打开时不一样"。
  const computeFit = useCallback((): View | null => {
    if (size.w === 0 || size.h === 0) return null
    const panelW = projection.panel.size_mm.w * projection.px_per_mm
    const panelH = projection.panel.size_mm.h * projection.px_per_mm
    // 外壳比板大：只按板适配，外框会永远被裁在画布外（V1 第一版就是这样）。
    // 内容框 = 板 ∪ 外壳外框，原点可能为负，所以 ox/oy 要把它整体推进视野。
    const enc = projection.enclosure
    const minX = enc !== null && enc !== undefined ? Math.min(0, enc.outer.x_px) : 0
    const minY = enc !== null && enc !== undefined ? Math.min(0, enc.outer.y_px) : 0
    const maxX = enc !== null && enc !== undefined
      ? Math.max(panelW, enc.outer.x_px + enc.outer.w_px) : panelW
    const maxY = enc !== null && enc !== undefined
      ? Math.max(panelH, enc.outer.y_px + enc.outer.h_px) : panelH
    const contentW = Math.max(maxX - minX, 1)
    const contentH = Math.max(maxY - minY, 1)
    const padding = 36
    const scale = Math.max(MIN_SCALE, Math.min(
      Math.min((size.w - padding * 2) / contentW,
               (size.h - padding * 2) / contentH),
      8,
    ))
    return {
      scale,
      ox: (size.w - contentW * scale) / 2 - minX * scale,
      oy: (size.h - contentH * scale) / 2 - minY * scale,
    }
  }, [size, projection.panel.size_mm.w, projection.panel.size_mm.h,
      projection.px_per_mm, projection.enclosure])

  const fittedRef = useRef<string>('')
  useEffect(() => {
    if (size.w === 0 || size.h === 0) return
    const panelW = projection.panel.size_mm.w * projection.px_per_mm
    const panelH = projection.panel.size_mm.h * projection.px_per_mm
    const enc = projection.enclosure
    const key = `${projection.panel.board_count}:${panelW.toFixed(0)}x${panelH.toFixed(0)}`
      + `:${enc === null || enc === undefined ? 'no-shell' : `${enc.outer.w_px.toFixed(0)}x${enc.outer.h_px.toFixed(0)}`}`
      + `:${size.w}x${size.h}`
    if (fittedRef.current === key) return
    fittedRef.current = key
    const next = computeFit()
    if (next !== null) setView(next)
  }, [projection.panel.board_count, projection.panel.size_mm.w,
      projection.panel.size_mm.h, projection.px_per_mm, projection.enclosure,
      size, computeFit])

  /** 复位到"适合窗口"。双击画布、按 `0`、点比例胶囊都走这一条。 */
  const fitToView = useCallback((): void => {
    const next = computeFit()
    if (next !== null) setView(next)
  }, [computeFit])

  // -- 绘制 ---------------------------------------------------------------

  const themeRev = useThemeRevision()
  useEffect(() => {
    const canvas = canvasRef.current
    if (canvas === null || size.w === 0 || size.h === 0) return
    const dpr = globalThis.devicePixelRatio ?? 1
    canvas.width = Math.round(size.w * dpr)
    canvas.height = Math.round(size.h * dpr)
    const ctx = canvas.getContext('2d')
    if (ctx === null) return
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    // 主题颜色必须命令式读：canvas 不跟 CSS 变量。宿主主题翻转会触发
    // themeRev 自增，本 effect 随之重绘。
    draw(ctx, {
      projection, view, size, selectedRef, findings,
      drag: dragRef.current, placementOf,
      palette: readPalette(wrapRef.current),
    })
  }, [projection, view, size, selectedRef, findings, drag, placementOf, themeRev])

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
    // 中键或按住空格 = **平移优先**，不选中也不拖元件。
    // 大板子上"想移动视野却总抓到元件"是主要挫败点之一。
    const wantsPan = event.button === 1 || spaceRef.current
    if (ref !== '' && !wantsPan) {
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

  /**
   * 以某点为中心缩放。`clientX/clientY` 省略时按画布中心。
   *
   * 用**函数式 setView**：滚轮监听只注册一次，闭包里读不到最新 `view`，
   * 用 `prev` 才不会在快速连滚时丢掉中间那几次的累积。
   */
  const zoomAt = useCallback((factor: number, clientX?: number, clientY?: number): void => {
    setView((prev) => {
      const scale = Math.max(MIN_SCALE, Math.min(prev.scale * factor, MAX_SCALE))
      if (scale === prev.scale) return prev
      const rect = canvasRef.current?.getBoundingClientRect()
      const mx = clientX !== undefined && rect !== undefined
        ? clientX - rect.left
        : size.w / 2
      const my = clientY !== undefined && rect !== undefined
        ? clientY - rect.top
        : size.h / 2
      const ratio = scale / prev.scale
      return {
        scale,
        ox: mx - (mx - prev.ox) * ratio,
        oy: my - (my - prev.oy) * ratio,
      }
    })
  }, [size.w, size.h])

  /**
   * 滚轮缩放 —— **必须用原生非 passive 监听**。
   *
   * 这里曾是 React 的 `onWheel` + `event.preventDefault()`，而 React 18 在
   * 根容器上以 `passive` 方式注册 `wheel`，于是 `preventDefault()` 是空操作：
   * 缩放照算，浏览器的默认滚动也照做，页面跟着上下跑。控制台会打印
   * `Unable to preventDefault inside passive event listener invocation`。
   *
   * 另外两件事：
   * - **`ctrl+wheel` 也要拦**，否则触发浏览器整页缩放（整站变大，很难恢复）；
   * - **`deltaMode` 要归一化**：0=像素 / 1=行 / 2=页。不归一化时触摸板
   *   （大量小 delta）和 Firefox 行模式的手感完全不同。
   */
  useEffect(() => {
    const canvas = canvasRef.current
    if (canvas === null) return
    const handler = (event: WheelEvent): void => {
      // 先拦，再决定要不要缩放：即使 deltaY 为 0 也不能放行默认滚动。
      event.preventDefault()
      const unit = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? size.h : 1
      const dy = event.deltaY * unit
      if (dy === 0) return
      // 指数而非固定倍率：滚一格与滚十格的手感一致。
      zoomAt(Math.exp(-dy * 0.0015), event.clientX, event.clientY)
    }
    canvas.addEventListener('wheel', handler, { passive: false })
    return () => { canvas.removeEventListener('wheel', handler) }
  }, [zoomAt, size.h])

  /** 键盘：`+`/`=` 放大、`-` 缩小、`0` 复位、按住空格时拖动平移。 */
  const onKeyDown = (event: React.KeyboardEvent<HTMLDivElement>): void => {
    if (event.key === ' ') {
      // 空格只改标志位，不 preventDefault：它不该吃掉按钮的激活键。
      spaceRef.current = true
      return
    }
    if (event.key === '+' || event.key === '=') {
      event.preventDefault()
      zoomAt(STEP)
    } else if (event.key === '-' || event.key === '_') {
      event.preventDefault()
      zoomAt(1 / STEP)
    } else if (event.key === '0') {
      event.preventDefault()
      fitToView()
    }
  }

  const onKeyUp = (event: React.KeyboardEvent<HTMLDivElement>): void => {
    if (event.key === ' ') spaceRef.current = false
  }

  return (
    <div className="hwv-canvas-wrap" ref={wrapRef} tabIndex={0}
         onKeyDown={onKeyDown} onKeyUp={onKeyUp}
         aria-label="2D 电路画布（滚轮缩放，拖动平移，双击复位）">
      <canvas
        className="hwv-canvas"
        ref={canvasRef}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerLeave={onPointerUp}
        onDoubleClick={() => { fitToView() }}
        onContextMenu={(event) => { event.preventDefault() }}
      />
      {/* 缩放胶囊：没有它，用户不知道当前是 40% 还是 300%，也没有复位的锚点。 */}
      <div className="hwv-zoom" role="group" aria-label="缩放">
        <button className="hwv-zoom-btn" title="缩小（-）"
                onClick={() => { zoomAt(1 / STEP) }}>−</button>
        <button className="hwv-zoom-pct" title="适合窗口（双击画布或按 0）"
                onClick={fitToView}>{Math.round(view.scale * 100)}%</button>
        <button className="hwv-zoom-btn" title="放大（+）"
                onClick={() => { zoomAt(STEP) }}>+</button>
      </div>
      <div className="hwv-legend">
        <span><i className="src-datasheet" />数据手册</span>
        <span><i className="src-measured" />实测</span>
        <span><i className="src-derived" />推导</span>
        <span><i className="src-estimated" />估算</span>
        <span><i className="wire-copper" />铜线（已布线）</span>
        <span><i className="wire-ratline" />飞线（未布线）</span>
        {/* `!= null` 同时挡 undefined：老载荷/测试夹具可能整个键都不在，
            写成 `!== null` 会把 undefined 放过去，然后在读字段时崩掉整块画布。 */}
        {projection.enclosure != null && (
          <span><i className="body-shell" />外壳（虚线）</span>
        )}
        {projection.enclosure != null
          && (projection.enclosure.side_openings ?? 0) > 0 && (
          <span className="hwv-dim">
            侧面 {projection.enclosure.side_openings ?? 0} 处开口（见 3D）
          </span>
        )}
        <span className="hwv-dim">
          {projection.px_per_mm.toFixed(4)} px/mm · {projection.panel.size_mm.w.toFixed(2)}×
          {projection.panel.size_mm.h.toFixed(2)} mm
          {projection.panel.board_count > 1
            ? ` · ${projection.panel.board_count} 块板拼板`
            : ''}
        </span>
      </div>
      {projection.parts.length === 0 && (
        <div className="hwv-canvas-empty">
          <strong>这个工程有 0 个元件</strong>
          <span>板框画出来了，但上面是空的 —— 不是渲染坏了</span>
        </div>
      )}
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
  palette: Palette
}

function draw(ctx: CanvasRenderingContext2D, input: DrawInput): void {
  const { projection, view, size, selectedRef, findings, drag, palette } = input
  ctx.clearRect(0, 0, size.w, size.h)

  // 背景（主题色，命令式读取）
  ctx.fillStyle = palette.canvasBg
  ctx.fillRect(0, 0, size.w, size.h)

  ctx.save()
  ctx.translate(view.ox, view.oy)
  ctx.scale(view.scale, view.scale)

  // 1mm 网格：覆盖**整张拼板**（多板时所有板都要有网格）
  const step = projection.px_per_mm
  const panelW = projection.panel.size_mm.w * projection.px_per_mm
  const panelH = projection.panel.size_mm.h * projection.px_per_mm
  ctx.lineWidth = 1 / view.scale
  ctx.strokeStyle = palette.grid
  ctx.beginPath()
  for (let x = 0; x <= panelW; x += step) {
    ctx.moveTo(x, 0)
    ctx.lineTo(x, panelH)
  }
  for (let y = 0; y <= panelH; y += step) {
    ctx.moveTo(0, y)
    ctx.lineTo(panelW, y)
  }
  ctx.stroke()

  // 板轮廓：**每块板各画一块**。
  // 多板时按拼板偏移画，单板时 `boards` 长度 1、偏移 0 —— 同一条代码路径。
  const boardsToDraw = projection.boards.length > 0
    ? projection.boards
    : [{ ...projection.board, id: '', parts: projection.parts.length,
         x_mm: 0, y_mm: 0, w_mm: projection.board.size_mm.w,
         h_mm: projection.board.size_mm.h, x_px: 0, y_px: 0 }]
  for (const board of boardsToDraw) {
    ctx.beginPath()
    board.outline_px.forEach(([x, y], index) => {
      // `outline_px` 已经是**拼板坐标**（Python 侧加过偏移），不再平移
      if (index === 0) ctx.moveTo(x, y)
      else ctx.lineTo(x, y)
    })
    ctx.closePath()
    ctx.fillStyle = palette.boardFill
    ctx.fill()
    ctx.strokeStyle = palette.boardStroke
    ctx.lineWidth = 1.5 / view.scale
    ctx.stroke()
  }

  // 板名（多板时才有意义；缩得太小就不画，避免糊成一团）
  if (boardsToDraw.length > 1) {
    ctx.fillStyle = palette.label
    const fontPx = Math.max(9, Math.min(13, 11 / view.scale))
    ctx.font = `${fontPx}px ui-monospace, Consolas, monospace`
    ctx.textBaseline = 'top'
    for (const board of boardsToDraw) {
      const xs = board.outline_px.map(([x]) => x)
      const ys = board.outline_px.map(([, y]) => y)
      if (xs.length === 0) continue
      const minX = Math.min(...xs)
      const minY = Math.min(...ys)
      const label = board.id ? board.id.slice(-8) : board.kind
      ctx.globalAlpha = 0.75
      ctx.fillText(`${label} · ${board.parts ?? 0}`, minX + 4 / view.scale,
        minY + 4 / view.scale)
      ctx.globalAlpha = 1
    }
  }

  // 外壳（俯视图）：外框 + 内腔虚线 + 顶面开口。
  // **没有外壳就不画**——不拿板框假装外壳。
  drawEnclosure(ctx, projection.enclosure, view, palette)

  // 安装孔
  for (const hole of projection.mount_holes) {
    ctx.beginPath()
    ctx.arc(hole.x_px, hole.y_px, Math.max(hole.d_px / 2, 1), 0, Math.PI * 2)
    ctx.fillStyle = palette.holeFill
    ctx.fill()
    ctx.strokeStyle = palette.holeStroke
    ctx.lineWidth = 1 / view.scale
    ctx.stroke()
  }

  // 导线（**铜线**：真实走线几何）
  //
  // 用统一的**铜色**而不是每网一个色相：真实 PCB 的铜就是铜色，按网络分
  // 彩虹色会让人以为那是"逻辑连接"而不是"物理铜"。网络名在检查器里看。
  for (const wire of projection.wires) {
    if (wire.path_px.length < 2) continue
    ctx.beginPath()
    wire.path_px.forEach(([x, y], index) => {
      if (index === 0) ctx.moveTo(x, y)
      else ctx.lineTo(x, y)
    })
    ctx.strokeStyle = palette.trace
    ctx.globalAlpha = 0.9
    ctx.lineWidth = Math.max(1.0, 1.3 / view.scale)
    ctx.stroke()
  }
  ctx.globalAlpha = 1

  // 飞线（**不是铜线**）：从网表推导的"应该连成什么样"。
  // 画成细虚线 + 端点小圆点，视觉上明确区别于铜线。
  if (projection.ratlines.length > 0) {
    ctx.save()
    ctx.setLineDash([4 / view.scale, 3 / view.scale])
    for (const line of projection.ratlines) {
      if (line.path_px.length < 2) continue
      const hue = hueOf(line.net)
      ctx.beginPath()
      line.path_px.forEach(([x, y], index) => {
        if (index === 0) ctx.moveTo(x, y)
        else ctx.lineTo(x, y)
      })
      ctx.strokeStyle = `hsl(${hue} 55% ${palette.wireLightness})`
      ctx.globalAlpha = 0.55
      ctx.lineWidth = Math.max(0.8, 0.9 / view.scale)
      ctx.stroke()
      // 端点：焊盘位置的小圆点
      ctx.globalAlpha = 0.85
      ctx.fillStyle = `hsl(${hue} 60% ${palette.wireLightness})`
      for (const [x, y] of line.path_px) {
        ctx.beginPath()
        ctx.arc(x, y, Math.max(1.2, 1.6 / view.scale), 0, Math.PI * 2)
        ctx.fill()
      }
    }
    ctx.restore()
    ctx.globalAlpha = 1
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

    // 底层的元件画在顶层**下面**（半透明），一眼能看出是双面板。
    const isBottom = part.side === 'bottom'

    ctx.fillStyle = partFill(palette, part.physical_source)
    ctx.globalAlpha = isBottom ? 0.45 : 0.92
    ctx.fillRect(x, y, part.w_px, part.h_px)
    ctx.globalAlpha = 1

    ctx.lineWidth = (isSelected ? 2.2 : 1.2) / view.scale
    ctx.strokeStyle = isSelected ? palette.selStroke
      : severity === 'error' ? palette.errStroke
        : severity === 'warning' ? palette.warnStroke
          : palette.plainStroke
    ctx.strokeRect(x, y, part.w_px, part.h_px)

    // 焊盘（真实几何）。焊盘是**金属**，用高亮色画，比元件本体更醒目。
    if (part.pads.length > 0) {
      for (const pad of part.pads) {
        const px = pad.x_px + offsetX
        const py = pad.y_px + offsetY
        ctx.fillStyle = palette.padFill
        drawPad(ctx, pad.shape, px, py, pad.w_px, pad.h_px, pad.rotation_z)
        ctx.strokeStyle = palette.padStroke
        ctx.lineWidth = Math.max(0.6, 0.7 / view.scale)
        ctx.stroke()
      }
    }

    // 参考号：缩放太小时不画，避免糊成一团
    if (part.w_px * view.scale > 26 && part.h_px * view.scale > 12) {
      ctx.fillStyle = palette.label
      const px = Math.max(9, Math.min(13, 11 / view.scale))
      ctx.font = `${px}px ui-monospace, Consolas, monospace`
      ctx.textBaseline = 'middle'
      ctx.fillText(part.ref, x + 3 / view.scale, y + 8 / view.scale)
      if (part.physical_source === 'estimated') {
        ctx.fillStyle = palette.estMark
        ctx.fillText('?', x + part.w_px - 8 / view.scale, y + 8 / view.scale)
      }
      // 底层标记
      if (isBottom) {
        ctx.fillStyle = palette.plainStroke
        ctx.fillText('B', x + part.w_px - 14 / view.scale, y + 8 / view.scale)
      }
    }
  }

  ctx.restore()
}

/**
 * 焊盘形状：`RECT` / `OVAL` / `ELLIPSE` / `CIRCLE`。
 *
 * 形状来自立创 EDA 的 `PAD.defaultPad.padType`（实测 RECT 196 / OVAL 78 /
 * ELLIPSE 18）。画成真实形状比一律画方块有用得多 —— 引脚间距一眼可辨。
 */
/**
 * 外壳俯视图：外框实线、内腔与开口虚线/半透明。
 *
 * 侧面开口**不在这里画**：从正上方看，它只是边上的一条线，位置一猜就错。
 * 数量在 `side_openings` 里如实计数，图例里写出来。
 */
function drawEnclosure(ctx: CanvasRenderingContext2D,
                       enc: SnapshotPayload['projection2d']['enclosure'],
                       view: View, palette: Palette): void {
  if (enc === null || enc === undefined) return
  ctx.save()
  ctx.setLineDash([6 / view.scale, 4 / view.scale])
  ctx.strokeStyle = palette.warnStroke
  ctx.lineWidth = Math.max(0.9, 1.2 / view.scale)
  ctx.strokeRect(enc.outer.x_px, enc.outer.y_px, enc.outer.w_px, enc.outer.h_px)
  ctx.globalAlpha = 0.7
  ctx.strokeStyle = palette.plainStroke
  ctx.strokeRect(enc.inner.x_px, enc.inner.y_px, enc.inner.w_px, enc.inner.h_px)
  ctx.restore()

  // 顶面/底面开口：真实矩形（px 由 Python 算，这里不换算）
  ctx.save()
  ctx.fillStyle = palette.bodyShell
  ctx.globalAlpha = 0.55
  ctx.strokeStyle = palette.warnStroke
  ctx.lineWidth = Math.max(0.6, 0.8 / view.scale)
  for (const opening of enc.openings ?? []) {
    ctx.fillRect(opening.x_px, opening.y_px, opening.w_px, opening.h_px)
    ctx.strokeRect(opening.x_px, opening.y_px, opening.w_px, opening.h_px)
  }
  ctx.restore()
}

function drawPad(ctx: CanvasRenderingContext2D, shape: string,
                 x: number, y: number, w: number, h: number,
                 rotationZ: number): void {
  const rx = Math.max(w / 2, 0.4)
  const ry = Math.max(h / 2, 0.4)
  ctx.beginPath()
  if (shape === 'OVAL') {
    // 椭圆 + 两端半圆：标准"跑道形"焊盘
    const r = Math.min(rx, ry)
    ctx.moveTo(x - rx + r, y - ry)
    ctx.lineTo(x + rx - r, y - ry)
    ctx.arc(x + rx - r, y, r, -Math.PI / 2, Math.PI / 2)
    ctx.lineTo(x - rx + r, y + ry)
    ctx.arc(x - rx + r, y, r, Math.PI / 2, -Math.PI / 2)
    ctx.closePath()
  } else if (shape === 'ELLIPSE') {
    ctx.ellipse(x, y, rx, ry, 0, 0, Math.PI * 2)
  } else if (shape === 'CIRCLE') {
    ctx.arc(x, y, Math.max(rx, ry), 0, Math.PI * 2)
  } else {
    ctx.rect(x - rx, y - ry, rx * 2, ry * 2)
  }
}
