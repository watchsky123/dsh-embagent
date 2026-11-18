/**
 * ``dsh-hw-velxio`` —— 宿主半入口。
 *
 * 组装三件事，每件单独兜底（一个注册失败不能把网关一起回滚，否则客户端
 * RPC 会永远悬住）：
 *
 * 1. **远程服务** ``hwvelxio``：客户端读快照、编辑器写模型都走它；
 * 2. **模型工具** ``hw_*``：让会话里的模型能触发检查/导出；
 * 3. **诊断行**：解析出的仓库根与 Python 解释器，写一次日志——根目录错时
 *    每个动作都会失败，而失败信息里通常没有根目录。
 *
 * @module dsh-hw-velxio
 */

import type { Context } from '@deepseek-ai/cordis'
import { pythonExecutable, repoRoot } from './bridge.js'
import { HwVelxioGateway } from './service.js'
import { registerTools } from './tools.js'

export const name = 'hw-velxio-host'

/**
 * `tools` 必须先就绪：`apply` 里要往工具注册表里放 8 个工具。缺它时 cordis
 * 会推迟 `apply`（fiber 置 INACTIVE），而不是让我们拿到 `undefined` 后静默
 * 跳过注册——后者会让"插件装了但模型看不见"变成一个没有报错的谜。
 */
export const inject = ['tools']

export function apply(ctx: Context): void {
  console.info(
    `[dsh-hw-velxio] host 半已加载：root=${repoRoot()} python=${pythonExecutable()}`)

  try {
    new HwVelxioGateway(ctx)
  } catch (error) {
    console.error('[dsh-hw-velxio] 远程服务注册失败：',
      error instanceof Error ? error.message : error)
  }

  try {
    const disposers = registerTools(ctx)
    for (const dispose of disposers) {
      ctx.effect(() => dispose, 'dsh-hw-velxio: tools')
    }
    console.info(`[dsh-hw-velxio] 已注册 ${disposers.length} 个工具`)
  } catch (error) {
    console.error('[dsh-hw-velxio] 工具注册失败（面板仍可用）：',
      error instanceof Error ? error.message : error)
  }

  // 仿真会话是**宿主进程内的状态**（avr8js 的 CPU/内存）。
  // 插件卸载或热重载时必须释放，否则旧会话的 CPU 对象会一直占着内存，
  // 而新会话用同一个工程名又建一份——两个都在，没人知道哪个是活的。
  ctx.effect(() => {
    return () => {
      void import('./velxio/sim.js').then((sim) => {
        const count = sim.disposeAll()
        if (count > 0) console.info(`[dsh-hw-velxio] 已释放 ${count} 个仿真会话`)
      }).catch(() => { /* 加载失败说明从没起过会话 */ })
    }
  }, 'dsh-hw-velxio: sim sessions')
}
