/**
 * Host → Python 的唯一通道。
 *
 * 设计纪律（与 hwforge 宿主半同一条）：
 *
 * 1. **不重写判定。** 模型、检查、预算、闸门全在 Python（`dsh_embagent.design`），
 *    这里只做进程调用与 JSON 解析。TS 侧再写一份就是第二个事实源。
 * 2. **不把失败变成空成功。** 三种状态原样透传：`ok` / `not_ready`（前置缺失）
 *    / `failed`（跑了但出错）。解析不出 JSON 也是 `failed`，绝不当成 `{}`。
 * 3. **一次调用一行 JSON。** 契约来自 `hwforge.blocks_cli`，两边一致。
 *
 * @module dsh-embagent/bridge
 */

import { execFile } from 'node:child_process'
import { existsSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

export type Status = 'ok' | 'not_ready' | 'failed'

export interface BridgeResult {
  readonly status: Status
  readonly findings?: readonly string[]
  readonly wrote?: readonly string[]
  readonly [key: string]: unknown
}

/** 插件目录（`dist/` 的上一级）。 */
function moduleDir(): string | null {
  try {
    const url = (import.meta as { url?: unknown }).url
    if (typeof url !== 'string' || !url.startsWith('file:')) return null
    return dirname(fileURLToPath(url))
  } catch {
    return null
  }
}

/** 仓库根：同时能找到 `dsh_embagent/bridge.py` 与（通常）`hwforge/` 的目录。 */
export function repoRoot(): string {
  const candidates: string[] = []
  const push = (value: string | undefined): void => {
    if (value !== undefined && value !== '') candidates.push(resolve(value))
  }
  push(process.env.DSH_EMBAGENT_ROOT)
  const here = moduleDir()
  if (here !== null) {
    push(join(here, '..'))
    push(join(here, '..', '..'))
    push(join(here, '..', '..', '..'))
  }
  push(process.cwd())
  for (const candidate of candidates) {
    if (existsSync(join(candidate, 'dsh_embagent', 'bridge.py'))) return candidate
  }
  return candidates[0] ?? process.cwd()
}

/** Python 解释器。允许用环境变量覆盖（虚拟环境/多版本共存）。 */
export function pythonExecutable(): string {
  const configured = process.env.DSH_EMBAGENT_PYTHON
  if (configured !== undefined && configured !== '') return configured
  return process.platform === 'win32' ? 'python' : 'python3'
}

export interface SpawnOptions {
  /** 默认 120s；检查大板会慢一些，但不要无限等。 */
  readonly timeoutMs?: number
  /** 追加到子进程环境（例如传入 stdin 之外的数据用不到，留作扩展）。 */
  readonly env?: NodeJS.ProcessEnv
  /**
   * 写到子进程 stdin 的内容。
   *
   * `design_cli put <id> -` 从 stdin 读设计 JSON —— 那是**大对象**（几十个
   * 元件的 design.json 有几十 KB），塞进命令行会被 Windows 的 32767 字符
   * 上限截断。所以走 stdin 而不是 argv。
   */
  readonly stdin?: string
}

/**
 * 跑一个 `python -m <module> ...` 并解析最后一行 JSON。
 *
 * stdout 里可能有其它行（Python 的警告、库的 print），所以取**最后一行可解析
 * 的 JSON**而不是第一行；解析失败时把 stderr 尾部带回去，让面板显示真正原因。
 */
export function runPython(
  module: string,
  args: readonly string[],
  options: SpawnOptions = {},
): Promise<BridgeResult> {
  const cwd = repoRoot()
  const timeoutMs = options.timeoutMs ?? 120_000
  const payload = options.stdin
  return new Promise((resolvePromise) => {
    try {
      const child = execFile(
      pythonExecutable(),
      ['-m', module, ...args],
      {
        cwd,
        timeout: timeoutMs,
        maxBuffer: 32 * 1024 * 1024,
        windowsHide: true,
        env: {
          ...process.env,
          PYTHONIOENCODING: 'utf-8',
          PYTHONUTF8: '1',
          ...options.env,
        },
      },
        (error, stdout, stderr) => {
          const parsed = parseLastJson(stdout)
          if (parsed !== null) {
            const status = normaliseStatus(parsed)
            resolvePromise({ ...parsed, status })
            return
          }
          const detail = String(stderr || '').trim()
          const tail = detail.length > 1500 ? detail.slice(-1500) : detail
          resolvePromise({
            status: 'failed',
            findings: [
              error !== null && error !== undefined
                ? `Python 调用失败：${error.message}`
                : `Python 没有输出可解析的 JSON（module=${module}）`,
              tail,
            ].filter((line) => line !== ''),
          })
        },
      )
      // 大对象走 stdin：`put <id> -` 从这里读设计 JSON。不写就 end()，
      // 否则子进程会一直等 stdin 而撞上 timeout。
      if (child.stdin !== null && child.stdin !== undefined) {
        child.stdin.on('error', () => { /* 子进程提前退出：由 callback 报错 */ })
        if (payload !== undefined) child.stdin.write(payload, 'utf8')
        child.stdin.end()
      }
    } catch (error) {
      // `spawn` 可以**同步**抛（EPERM/ENOENT），不接住就是未处理的 rejection，
      // 客户端会看到"读取中"永远不结束。
      resolvePromise({
        status: 'failed',
        findings: [
          `无法启动 Python（${pythonExecutable()}）：`
          + (error instanceof Error ? error.message : String(error)),
          `工作目录：${cwd}`,
        ],
      })
    }
  })
}

function normaliseStatus(payload: Record<string, unknown>): Status {
  const raw = payload.status
  if (raw === 'ok' || raw === 'not_ready' || raw === 'failed') return raw
  return 'failed'
}

function parseLastJson(stdout: string): Record<string, unknown> | null {
  const lines = stdout.trim().split(/\r?\n/)
  for (let index = lines.length - 1; index >= 0; index -= 1) {
    const line = lines[index]?.trim() ?? ''
    if (!line.startsWith('{')) continue
    try {
      const parsed = JSON.parse(line) as unknown
      if (parsed !== null && typeof parsed === 'object' && !Array.isArray(parsed)) {
        return parsed as Record<string, unknown>
      }
    } catch {
      // 继续往前找：可能是 Python 打了一行带大括号的日志
    }
  }
  return null
}

/** 规范模型入口（`dsh_embagent.design_cli`）。 */
export function design(...args: string[]): Promise<BridgeResult> {
  return runPython('dsh_embagent.design_cli', args)
}

/**
 * 慢动作版 `design()` —— 给"启动后端"这类会**故意等待**的命令用。
 *
 * 为什么需要它：`runPython` 默认超时 120s，而 `serve start` 要等后端把
 * `/health` 打起来（冷启动包含 arduino 环境预置与 uvicorn 引导，最长等 150s）。
 * 两者一撞，`serve start` 会在**还没等到结果时**被 SIGTERM 杀掉，父进程拿到
 * 非零退出码、拿不到任何 JSON，于是面板只显示一句 `Command failed`——
 * 看起来像后端启动失败，其实是"我们没等够"。
 *
 * 超时值必须**大于** Python 侧自己的等待窗口，让 Python 有机会返回
 * 结构化的 `not_ready`（带日志尾部），而不是被外部砍断。
 */
export function designLong(timeoutMs: number, ...args: string[]): Promise<BridgeResult> {
  return runPython('dsh_embagent.design_cli', args, { timeoutMs })
}

/**
 * 提交完整设计 JSON（走 `DesignModel` 校验）。
 *
 * 正文走 **stdin** 而不是 argv：Windows 的命令行上限是 32767 字符，
 * 几十个元件的 design.json 很容易超过——截断后的 JSON 仍然"能解析"，
 * 后果是**静默写入一份残缺设计**。这类错误比失败更难发现。
 */
export function designPut(project: string, json: string): Promise<BridgeResult> {
  return runPython('dsh_embagent.design_cli', ['put', project, '-'], { stdin: json })
}

/** hwforge 积木 / Velxio HTTP 能力入口（`dsh_embagent.bridge`）。 */
export function bridge(...args: string[]): Promise<BridgeResult> {
  return runPython('dsh_embagent.bridge', args)
}
