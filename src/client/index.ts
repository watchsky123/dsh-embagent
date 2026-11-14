/**
 * ``dsh-hw-velxio`` —— 客户端入口。
 *
 * 只做三件事：注入样式、注册会话视图、把 `uiConversation` 缺失的情况写进
 * 日志。任何几何/判定都不在这里——那些在 Python，客户端只负责显示与操作。
 *
 * @module dsh-hw-velxio/client
 */

import type { Context } from '@deepseek-ai/cordis'
import { ensureStyles } from './styles.js'
import { registerHwIdeView } from './view.js'

export const name = 'hw-velxio-view'

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
  console.info('[dsh-hw-velxio] client apply 开始')
  ensureStyles()
  try {
    registerHwIdeView(ctx)
    console.info('[dsh-hw-velxio] 会话视图「硬件工作台」已注册')
  } catch (error) {
    console.error('[dsh-hw-velxio] 视图注册失败：',
      error instanceof Error ? error.message : error)
  }
}
