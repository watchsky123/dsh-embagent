/**
 * 验证 `designPut` 真的把 JSON 通过 **stdin** 送进 python。
 *
 * 为什么不能用 argv：Windows 命令行上限 32767 字符，几十个元件的
 * `design.json` 很容易超过。更糟的是**截断后的 JSON 仍然可能解析成功**，
 * 后果是静默写入一份残缺设计——比直接失败更难发现。这里用 60KB 标题
 * 证明走 stdin 的正文没有被截断。
 *
 * **工作区隔离**：脚本会 `HWV_WORKSPACE` 指到临时目录再跑。第一版忘了这
 * 一步，结果在你的真实工作区里留下了一个 `__stdin_probe__` 工程，标题是
 * 6 万个 `y`——初审.md 会把它当真实工程列出来。自检**不许**污染用户资产。
 *
 *   node scripts/check_put_stdin.mjs
 */

import { execFileSync } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const sandbox = mkdtempSync(join(tmpdir(), 'hwv-stdin-'))
const project = '_stdin_probe'
const big = 'y'.repeat(60_000)

const doc = {
  schema: 'hw_velxio.design/1', units: 'mm', id: project, title: big,
  board: { kind: 'arduino-uno', name: 'Arduino Uno',
    outline: [[0, 0], [72.58, 0], [72.58, 53.34], [0, 53.34]],
    size_mm: { w: 72.58, h: 53.34 }, thickness_mm: 1.6,
    mount_holes: [], px_per_mm: 3.7751 },
  parts: [], nets: [], wires: [], meta: {},
}

let failed = false
try {
  // 子进程继承环境：HWV_WORKSPACE 决定工程住哪。
  const { designPut, design } = await import('../dist/bridge.js')
  process.env.HWV_WORKSPACE = sandbox

  const put = await designPut(project, JSON.stringify(doc))
  if (put.status !== 'ok') {
    console.log('FAIL put:', put.findings)
    failed = true
  } else {
    console.log(`  ok   design 走 stdin 写入成功（${JSON.stringify(doc).length} 字节）`)
  }

  const back = await design('get', project)
  const len = (back.design?.title ?? '').length
  if (len !== big.length) {
    console.log(`  FAIL 正文被截断：读回 ${len} / 原始 ${big.length}`)
    failed = true
  } else {
    console.log(`  ok   正文完整（${len} 字符，argv 上限是 32767）`)
  }

  // 隔离必须真的生效：工程应落在沙箱里，不在用户工作区。
  const listed = await design('list')
  const ids = (listed.projects ?? []).map((row) => row.id)
  if (!ids.includes(project)) {
    console.log(`  FAIL 工程没落在隔离工作区（${sandbox}）；实际：${ids.join(', ')}`)
    failed = true
  } else {
    console.log('  ok   工程落在隔离工作区（没有污染用户工作区）')
  }
} finally {
  rmSync(sandbox, { recursive: true, force: true })
}

console.log()
if (failed) {
  console.log('❌ stdin 通路检查失败')
  process.exit(1)
}
console.log('✅ stdin 通路完整且不污染工作区')
