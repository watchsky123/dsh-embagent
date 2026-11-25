/**
 * ``dsh-embagent`` —— 客户端入口。
 *
 * 只做三件事：注入样式、注册会话视图、把 `uiConversation` 缺失的情况写进
 * 日志。任何几何/判定都不在这里——那些在 Python，客户端只负责显示与操作。
 *
 * @module dsh-embagent/client
 */

import type { Context } from '@deepseek-ai/cordis'
import { ensureStyles } from './styles.js'
import { registerHwIdeView } from './view.js'

export const name = 'embagent-view'

/**
 * 视图需要的前置服务。
 *
 * `slots`/`connection`/`sessions` 三个都要：没有 slots 就没有渲染座位，没有
 * connection 就没有 RPC，没有 sessions 就没有 AI 动作。`uiConversation` 故意
 * 不列——它通过 `ctx.get` 取，缺失时降级为"工具可用、视图不出现"，而不是整个
 * 插件加载失败。
 */
export const inject = ['slots', 'connection', 'sessions']

export function apply(ctx: Context): void {
  console.info('[dsh-embagent] client apply 开始')
  ensureStyles()
  try {
    registerHwIdeView(ctx)
    console.info('[dsh-embagent] 会话视图「硬件工作台」已注册')
  } catch (error) {
    console.error('[dsh-embagent] 视图注册失败：',
      error instanceof Error ? error.message : error)
  }
}

/**
 * 具名导出 store：宿主只读 `apply/inject/name`，多出来的导出不参与装载；
 * 它们存在的理由是 `scripts/check_layout_runtime.mjs` 需要在**真实的模块
 * 单例**上驱动布局状态做渲染断言（`display:none` 还是卸载，只有真 store
 * + 真组件才验得出来，伪造一个 store 就等于什么都没测）。
 */
export { store, useHw } from './store.js'
