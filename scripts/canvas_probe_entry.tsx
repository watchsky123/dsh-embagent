/**
 * 测试入口：把**真正的 Circuit2D** 挂到真实 DOM 上。
 *
 * 这不是插件的一部分，只被 `check_canvas_draw.mjs` 用 tsdown 打成一个小
 * bundle，然后在无头 Edge 里跑。
 *
 * 为什么必须挂真组件而不是复刻绘制逻辑：
 *
 * * 之前那轮的"像素检查"（`scripts/check_canvas_render.py`）用 Pillow
 *   **复刻**了绘制代码 —— 复刻对了只能证明我理解对了，复刻错了就什么都
 *   证明不了。用户截图里板子明显偏小，而那种检查照样全绿。
 * * 只有真组件 + 真 DOM + 真 ResizeObserver，才能覆盖"自适应缩放用的是
 *   哪个尺寸"这类问题。
 */

import { createRoot } from 'react-dom/client'
import { Circuit2D } from '../src/client/Circuit2D.js'
import type { SnapshotPayload } from '../src/shared/types.js'

/**
 * 快照由页面在加载本 bundle **之前**挂到 `window.__SNAPSHOT__`。
 *
 * 不能用 `declare const __SNAPSHOT__` + 打包器 `define`：`declare` 只是
 * 类型声明，运行时那个标识符根本不存在；打包器不注入就是 `ReferenceError`，
 * 而这个错误会被 React 的渲染错误边界吞进 console.error —— 表现是
 * "画布 0 像素"，看起来像渲染 bug，其实是注入没接上。
 */
const snapshot = (window as unknown as { __SNAPSHOT__: SnapshotPayload }).__SNAPSHOT__

if (snapshot === undefined || snapshot.projection2d === undefined) {
  console.error('[canvas-probe] window.__SNAPSHOT__ 没注入')
}

function App(): JSX.Element {
  return (
    <div className="hwv-root" style={{ height: '100%' }}>
      <div className="hwv-main">
        <div className="hwv-panes">
          <div className="hwv-pane" style={{ flex: '1 1 0%' }}>
            <div className="hwv-pane-head">2D 电路</div>
            <Circuit2D
              snapshot={snapshot}
              selectedRef=""
              findings={[]}
              onSelect={() => {}}
              onMove={() => {}}
            />
          </div>
        </div>
      </div>
    </div>
  )
}

const host = document.getElementById('host')
if (host !== null) {
  createRoot(host).render(<App />)
}
