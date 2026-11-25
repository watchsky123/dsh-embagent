/**
 * 编译产物读取 —— 工具（`hw_sim`）与宿主端点（`simStart`）**共用这一份**。
 *
 * ## 为什么抽出来
 *
 * 两处都写一遍会在很小的细节上分叉，而分叉的后果是"工具能起仿真、面板不能"：
 *
 * - 异步编译（`/api/compile/start`）落盘形状是 `{ data: { hex_content } }`；
 * - 同步编译（`/api/compile/`）是顶层 `{ hex_content }`。
 *
 * 只认其中一种，就会在换一条路径编译之后报"没有固件"——而 `hex` 明明在文件里。
 *
 * ## 为什么由宿主读盘
 *
 * 面板（webview）没有文件系统访问。让它读 `<工程>/velxio_compile.json` 是把
 * 宿主的能力错配给客户端。所以面板传空 hex，宿主在这里补上。
 *
 * @module dsh-embagent/velxio/artifact
 */

import { existsSync, readFileSync } from 'node:fs'

export interface HexLoadResult {
  readonly status: 'ok' | 'not_ready' | 'failed'
  readonly hex?: string
  readonly path?: string
  readonly findings?: string[]
}

/**
 * 从工程目录读上次编译的 hex。
 *
 * `projectPath` 由调用方给（工具从 `design_cli survey` 拿，宿主同理）；
 * 这里只负责"给定目录，把 hex 取出来"，不猜工程在哪。
 */
export function loadCompiledHex(projectPath: string): HexLoadResult {
  if (projectPath.trim() === '') {
    return {
      status: 'not_ready',
      findings: ['没有工程路径：先用 hw_projects 确认工程名，或 hw_workspace 切到它的工作区。'],
    }
  }
  const artifact = `${projectPath}\\velxio_compile.json`
  if (!existsSync(artifact)) {
    return {
      status: 'not_ready',
      path: artifact,
      findings: [
        `没有编译产物：${artifact} 不存在。`,
        '先 hw_compile 编出 hex，或用 hexJson 直接给。',
      ],
    }
  }
  let parsed: Record<string, unknown>
  try {
    parsed = JSON.parse(readFileSync(artifact, 'utf8')) as Record<string, unknown>
  } catch (error) {
    return {
      status: 'failed',
      path: artifact,
      findings: [`读 ${artifact} 失败：${error instanceof Error ? error.message : String(error)}`],
    }
  }
  // 两种形状都认：异步在 data.hex_content，同步在顶层。
  const data = (parsed.data ?? {}) as Record<string, unknown>
  const hex = String(data.hex_content ?? parsed.hex_content ?? '')
  if (hex.trim() === '') {
    return {
      status: 'not_ready',
      path: artifact,
      findings: [
        `${artifact} 里没有 hex_content。`,
        '那次编译可能失败了——用 hw_compile 重编，并看它的 stderr。',
      ],
    }
  }
  return { status: 'ok', hex, path: artifact }
}
