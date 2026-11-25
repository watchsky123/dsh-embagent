/**
 * 仿真会话管理器 —— 把 `AvrSession` 包成**可被工具反复访问**的东西。
 *
 * ## 为什么需要它
 *
 * `hw_sim(start)` 与 `hw_serial(read)` 是两次独立的工具调用，中间隔着若干秒
 * 到若干分钟。AVR 会话必须活在两次调用之间，而宿主进程（index.ts 的 apply）
 * 是唯一的长期持有者。所以会话存在模块级注册表里，按工程名索引。
 *
 * ## 与 Velxio 后端的边界（重要）
 *
 * 这一层**不经过 Python、不经过 HTTP**，因为：
 *
 * - Velxio 后端的仿真通道是 ESP32 QEMU（需要授权 key 的 `.so`）；AVR 的
 *   CPU 模型在它前端，后端根本没有。
 * - 而 `avr8js` 是纯 TypeScript、零依赖、不碰 DOM，在 Node 里能直接跑。
 *
 * 所以这里不是"绕过 Velxio"，而是**补上它在无浏览器场景下缺的那一块**：
 * 后端负责编译（真 hex），这里负责执行（真指令+真串口）。两边都以
 * "谁有能力谁做"为准，不在中间再造一层判定。
 *
 * ## 状态是如实的
 *
 * - `running` / `stopped` 由会话是否存活决定；
 * - 串口字节有**累计总数**、**上次读取以来新增**、**因缓冲被丢**三个数；
 *   丢字节不静默——那是"数据不完整"，必须能被看见。
 *
 * @module dsh-embagent/velxio/sim
 */

import { AvrSession, avrBoardSupport } from './avr.js'

export interface SimStatus {
  readonly project: string
  readonly running: boolean
  readonly board: string
  readonly hexBytes: number
  readonly instructions: number
  readonly simMs: number
  readonly serialTotal: number
  readonly serialPending: number
  readonly startedAt: string
  readonly note: string
}

/** 一个活着的会话。 */
interface LiveSession {
  readonly project: string
  readonly board: string
  readonly session: AvrSession
  readonly meta: Record<string, unknown>
  readonly startedAt: number
  /** 累计执行过的指令（跨多次 `run` 调用）。 */
  instructions: number
}

const sessions = new Map<string, LiveSession>()

function nowIso(): string {
  return new Date().toISOString()
}

/** 数字节数：hex 是 ASCII 十六进制，每 2 字符 1 字节。 */
function hexByteLength(hexText: string): number {
  return Math.floor((hexText.trim().length) / 2)
}

export interface StartOptions {
  readonly project: string
  readonly board: string
  readonly hex: string
  readonly freqHz?: number
  readonly meta?: Record<string, unknown>
}

/**
 * 起一个仿真会话。已有同名会话时**先停掉旧的**并如实说明——
 * 静默替换会让人以为"两个会话都在跑"。
 */
export function start(options: StartOptions): Record<string, unknown> {
  const support = avrBoardSupport(options.board)
  if (!support.supported) {
    return {
      status: 'not_ready',
      findings: [
        `这个板不能走 AVR 运行通道：${support.reason}`,
        '能跑的是 ATmega328p 家族（Uno/Nano/Mini/Pro Mini）。'
        + 'ESP32 需要 QEMU（见 dsh-embagent-docs:docs/p4-simulation.md），Mega2560 需要另补寄存器表。',
      ],
    }
  }
  if (options.hex.trim() === '') {
    return { status: 'not_ready', findings: ['没有 hex：先 hw_compile 编出固件。'] }
  }

  const replaced = sessions.has(options.project)
  if (replaced) stop(options.project)

  let session: AvrSession
  try {
    session = new AvrSession(options.hex, options.freqHz ?? 16e6)
  } catch (error) {
    return {
      status: 'failed',
      findings: [
        `AVR 会话建不起来：${error instanceof Error ? error.message : String(error)}`,
        '常见原因：hex 不是 Intel HEX 格式，或 hex 为空。',
      ],
    }
  }

  sessions.set(options.project, {
    project: options.project,
    board: options.board,
    session,
    meta: options.meta ?? {},
    startedAt: Date.now(),
    instructions: 0,
  })

  return {
    status: 'ok',
    project: options.project,
    board: options.board,
    hexBytes: hexByteLength(options.hex),
    replaced,
    note: 'AVR 仿真在宿主 Node 进程内跑（avr8js），不需要浏览器、不需要后端。',
  }
}

/** 停掉会话并清空缓冲。没在跑也算 ok（幂等）。 */
export function stop(project: string): Record<string, unknown> {
  const live = sessions.get(project)
  if (live === undefined) {
    return { status: 'ok', stopped: false, note: '没有正在跑的会话' }
  }
  const summary = {
    instructions: live.instructions,
    serialTotal: live.session.describe().serialTotal,
  }
  live.session.dispose()
  sessions.delete(project)
  return { status: 'ok', stopped: true, ...summary }
}

/** 推进仿真。`run(project, {...})`。 */
export function run(project: string,
                    options: { maxInstructions?: number; maxBytes?: number } = {}):
  Record<string, unknown> {
  const live = sessions.get(project)
  if (live === undefined) {
    return {
      status: 'not_ready',
      findings: [`工程 ${project} 没有在跑的仿真会话。`,
                 '先 hw_sim action=start（需要有 hex）。'],
    }
  }
  const result = live.session.run(options)
  live.instructions += result.instructions
  return {
    status: 'ok',
    project,
    instructions: result.instructions,
    simMs: Math.round(result.simMs * 10) / 10,
    stopReason: result.stopReason,
    serialText: result.serialText,
    serialBytes: result.serial.length,
    serialTotal: result.serialTotal,
    droppedBytes: result.droppedBytes,
    pinEvents: result.pinEvents,
    describe: live.session.describe(),
  }
}

/** 只读串口（不推进 CPU）。 */
export function serialRead(project: string): Record<string, unknown> {
  const live = sessions.get(project)
  if (live === undefined) {
    return {
      status: 'not_ready',
      findings: [`工程 ${project} 没有在跑的仿真会话。先 hw_sim action=start。`],
    }
  }
  const pending = live.session.readPending()
  return {
    status: 'ok',
    project,
    text: pending.text,
    bytes: pending.bytes.length,
    totalBytes: pending.totalBytes,
    droppedBytes: pending.droppedBytes,
    describe: live.session.describe(),
  }
}

/** 状态摘要（不推进）。 */
export function status(project?: string): Record<string, unknown> {
  if (project !== undefined && project !== '') {
    const live = sessions.get(project)
    if (live === undefined) {
      return { status: 'not_ready', running: false, project,
               findings: ['没有在跑的会话'] }
    }
    const d = live.session.describe()
    const out: SimStatus = {
      project,
      running: true,
      board: live.board,
      hexBytes: Number(live.meta.hexBytes ?? 0),
      instructions: Number(d.instructions ?? 0),
      simMs: Math.round(Number(d.simMs ?? 0) * 10) / 10,
      serialTotal: Number(d.serialTotal ?? 0),
      serialPending: Number(d.serialPending ?? 0),
      startedAt: new Date(live.startedAt).toISOString(),
      note: 'AVR 仿真（avr8js，宿主 Node 内）',
    }
    return { status: 'ok', ...out, describe: d }
  }
  const all = [...sessions.values()].map((live) => ({
    project: live.project,
    board: live.board,
    instructions: live.instructions,
    startedAt: new Date(live.startedAt).toISOString(),
  }))
  return { status: 'ok', running: all.length, sessions: all, at: nowIso() }
}

/** 宿主卸载时清干净（index.ts 的 dispose 会调）。 */
export function disposeAll(): number {
  const count = sessions.size
  for (const live of sessions.values()) live.session.dispose()
  sessions.clear()
  return count
}
