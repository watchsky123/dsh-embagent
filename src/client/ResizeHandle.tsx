/**
 * 可拖把手 —— 分布形式来自 Velxio `EditorPage.tsx` 的三个 resize handler
 * （:365-450），语义一致但收成一个可复用组件：
 *
 *   · 拖动挂 **document 级** move/up（指针滑出把手也不丢）；
 *   · 拖动期间 `body.cursor` + `userSelect:'none'`，结束复原；
 *   · 行把手向上拖 = 变高（Velxio 的 `startY - clientY` 语义）；
 *   · 值域换算交给 `toValue`（默认像素加法；百分比分栏传入按容器宽换算
 *     的函数——Velxio 测的是 content-box，我们同样不吃 border/padding 的
 *     亏：换算函数拿到的是纯像素位移）；
 *   · clamp 到 [min,max]；拖动中走 onPreview 实时预览，**松手才 onCommit**
 *     （避免每像素一次 localStorage 写 + store 广播）。
 *
 * 持久化由调用方负责（本插件在 store 里，前缀 dsh-hwv.）。
 *
 * @module dsh-embagent/client/ResizeHandle
 */

import { useCallback, useRef } from 'react'

export interface ResizeHandleProps {
  /** vertical = 左右分栏（拖宽度）；horizontal = 上下分栏（拖高度）。 */
  readonly orientation: 'vertical' | 'horizontal'
  readonly min: number
  readonly max: number
  /** 当前值（单位与 toValue 的值域一致；默认 px）。 */
  readonly value: number
  /**
   * 把"基准值 + 像素位移"换算成值域里的新值。默认 `base + deltaPx`；
   * 百分比分栏传 `(base, d) => base + d / containerWidth * 100`。
   */
  readonly toValue?: (base: number, deltaPx: number) => number
  readonly onCommit: (value: number) => void
  /** 可选：拖动过程中的实时值（不提交，供预览/节流刷新用）。 */
  readonly onPreview?: (value: number) => void
  readonly title?: string
  readonly ariaLabel?: string
}

function clamp(raw: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, raw))
}

const DEFAULT_TO_VALUE = (base: number, deltaPx: number): number => base + deltaPx

export function ResizeHandle(props: ResizeHandleProps): JSX.Element {
  const { orientation, min, max, value, onCommit, onPreview, toValue } = props
  const convert = toValue ?? DEFAULT_TO_VALUE
  // 拖动基准存在 ref：一次拖动内不重渲染也不会丢起点。
  const dragRef = useRef<{ start: number; base: number } | null>(null)

  const onMouseDown = useCallback((event: React.MouseEvent) => {
    event.preventDefault()
    const vertical = orientation === 'vertical'
    dragRef.current = {
      start: vertical ? event.clientX : event.clientY,
      base: value,
    }
    document.body.style.cursor = vertical ? 'col-resize' : 'row-resize'
    document.body.style.userSelect = 'none'

    const compute = (coord: number): number => {
      const drag = dragRef.current
      if (drag === null) return value
      // 行把手向上拖 = 底部面板变高（startY - clientY），与 Velxio 一致。
      const deltaPx = vertical ? coord - drag.start : drag.start - coord
      return clamp(convert(drag.base, deltaPx), min, max)
    }
    const onMove = (ev: MouseEvent): void => {
      if (dragRef.current === null) return
      onPreview?.(compute(vertical ? ev.clientX : ev.clientY))
    }
    const onUp = (ev: MouseEvent): void => {
      const live = dragRef.current !== null
      dragRef.current = null
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
      document.removeEventListener('mousemove', onMove)
      document.removeEventListener('mouseup', onUp)
      if (live) onCommit(compute(vertical ? ev.clientX : ev.clientY))
    }
    document.addEventListener('mousemove', onMove)
    document.addEventListener('mouseup', onUp)
  }, [orientation, min, max, value, convert, onCommit, onPreview])

  return (
    <div
      className={`hwv-handle ${orientation === 'vertical' ? 'vertical' : 'horizontal'}`}
      role="separator"
      aria-orientation={orientation === 'vertical' ? 'vertical' : 'horizontal'}
      aria-label={props.ariaLabel ?? '拖动调整面板大小'}
      title={props.title ?? '拖动调整大小'}
      onMouseDown={onMouseDown}
    />
  )
}
