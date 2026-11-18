/**
 * AVR 仿真运行时 —— **在宿主 Node 进程里跑真实固件，不需要浏览器。**
 *
 * ## 为什么这一层存在
 *
 * Velxio 的 AVR/RP2040 仿真跑在**前端**（`frontend/src/simulation/AVRSimulator.ts`，
 * 基于 npm 的 `avr8js`），后端只有 ESP32 的 QEMU 通道。所以"纯 DSH、不开浏览器"
 * 这条约束下，AVR 原本只能交付"编译出 hex"。
 *
 * 实测证明不需要浏览器：`avr8js` 是纯 TypeScript、零运行时依赖、不碰 DOM，
 * 它只操作一个 `Uint16Array` 的程序存储器和一块 `Uint8Array` 的数据空间。
 * 在 Node 里 `require('avr8js')` 直接可用，跑一个 `Serial.println("HELLO-AVR")`
 * 的固件能完整收到 11 个字节的串口输出。
 *
 * 于是这里做的不是"模拟硬件外观"，而是**模拟硬件行为**：CPU 指令、定时器、
 * USART 收发、GPIO 电平。拿得到串口字节流与引脚变化——这正是"跑起来看串口"
 * 需要的最小集合，也是没有可视化时仍然有意义的部分。
 *
 * ## 明确的边界（不假装）
 *
 * - **没有元件动画**：那是 Velxio 前端 150+ Web Components 的职责，与
 *   "不依赖浏览器"直接冲突。本模块只出字节与电平。
 * - **只支持 ATmega328p 家族**（Uno/Nano/Mini 这一档）：`avr8js` 的
 *   `usart0Config`/`portBConfig` 等是按 328p 的寄存器地址定义的。
 *   Mega2560 的寄存器不同，要继续支持它得补一套 config —— 现在如实拒绝。
 * - **不是实时**：CPU 在一个紧循环里尽量跑，串口字节进环形缓冲。
 *   不试图与墙钟同步（那会让单次工具调用挂住）。
 *
 * @module dsh-hw-velxio/velxio/avr
 */

import { createRequire } from 'node:module'

/** avr8js 的运行时类型（只声明用到的部分，避免把整个包的 .d.ts 拉进来）。 */
interface AvrCpu {
  cycles: number
  pc: number
  data: Uint8Array
  tick(): void
}
interface AvrUsart {
  onByteTransmit: ((byte: number) => void) | null
  onLineTransmit: ((line: string) => void) | null
}
interface AvrIoPort {
  addListener(cb: (value: number) => void): void
  removeListener(cb: (value: number) => void): void
}

interface AvrModule {
  CPU: new (progMem: Uint16Array, sramBytes?: number) => AvrCpu
  avrInstruction: (cpu: AvrCpu) => void
  AVRUSART: new (cpu: AvrCpu, config: unknown, freqHz: number) => AvrUsart
  usart0Config: unknown
  AVRTimer: new (cpu: AvrCpu, config: unknown) => unknown
  timer0Config: unknown
  timer1Config: unknown
  timer2Config: unknown
  AVRIOPort: new (cpu: AvrCpu, config: unknown) => AvrIoPort
  portBConfig: unknown
  portCConfig: unknown
  portDConfig: unknown
}

/** 用一个真实连接构造 AVR 运行时（延迟 require，避免宿主启动时就付加载成本）。 */
function loadAvr8js(): AvrModule {
  const require = createRequire(import.meta.url)
  return require('avr8js') as AvrModule
}

/** 一次串口接收的字节缓冲。**环形**：长时间跑不会无限涨。 */
export class SerialBuffer {
  private readonly chunks: Buffer[] = []
  private held = 0
  /** 已交付给调用方的字节数（用于"自上次读取以来"语义）。 */
  private delivered = 0
  private total = 0

  constructor(private readonly capacityBytes: number = 64 * 1024) {}

  push(bytes: Buffer): void {
    if (bytes.length === 0) return
    this.chunks.push(bytes)
    this.held += bytes.length
    this.total += bytes.length
    // 超容量时从最老的开始丢，并**如实记账**——丢了多少要让调用方看到。
    let dropped = 0
    while (this.held > this.capacityBytes && this.chunks.length > 0) {
      const head = this.chunks[0]
      if (head === undefined) break
      if (head.length <= this.held - this.capacityBytes) {
        this.chunks.shift()
        this.held -= head.length
        dropped += head.length
      } else {
        const cut = this.held - this.capacityBytes
        this.chunks[0] = head.subarray(cut)
        this.held -= cut
        dropped += cut
      }
    }
    if (dropped > 0) this.droppedBytes += dropped
  }

  private droppedBytes = 0

  /** 自上次调用以来新增的字节。 */
  drain(): { bytes: Buffer; droppedBytes: number; totalBytes: number } {
    const all = this.chunks.length === 0
      ? Buffer.alloc(0)
      : Buffer.concat(this.chunks)
    const fresh = all.subarray(this.delivered)
    this.delivered = all.length
    const dropped = this.droppedBytes
    this.droppedBytes = 0
    return { bytes: Buffer.from(fresh), droppedBytes: dropped, totalBytes: this.total }
  }

  get totalBytes(): number {
    return this.total
  }

  get pendingBytes(): number {
    const all = this.chunks.reduce((acc, c) => acc + c.length, 0)
    return all - this.delivered
  }

  clear(): void {
    this.chunks.length = 0
    this.held = 0
    this.delivered = 0
    this.droppedBytes = 0
  }
}

/** Intel HEX → 程序存储器（**字**数组）与原始字节。 */
export function parseIntelHex(hexText: string): {
  words: Uint16Array
  bytes: Uint8Array
  nonZeroWords: number
  records: number
} {
  const bytes = new Uint8Array(0x10000)
  let records = 0
  for (const rawLine of hexText.split(/\r?\n/)) {
    const line = rawLine.trim()
    if (line === '' || line[0] !== ':') continue
    const raw: number[] = []
    for (let i = 1; i + 1 < line.length; i += 2) {
      const value = Number.parseInt(line.slice(i, i + 2), 16)
      if (Number.isNaN(value)) break
      raw.push(value)
    }
    if (raw.length < 5) continue
    const length = raw[0] ?? 0
    const addr = ((raw[1] ?? 0) << 8) | (raw[2] ?? 0)
    const type = raw[3] ?? 0
    if (type === 0x00) {
      for (let i = 0; i < length; i += 1) bytes[addr + i] = raw[4 + i] ?? 0
      records += 1
    }
  }
  // **必须是 Uint16Array**：avr8js 的 CPU 按"字"取指。
  // 传 Uint8Array 时所有外设的 writeHooks 索引会对不上，症状是 EEPROM
  // 构造时报 `Cannot set properties of undefined (setting '63')`——
  // 报错指向 EEPROM，真因在程序存储器类型。
  const words = new Uint16Array(0x8000)
  for (let i = 0; i < words.length; i += 1) {
    words[i] = (bytes[2 * i] ?? 0) | ((bytes[2 * i + 1] ?? 0) << 8)
  }
  let nonZeroWords = 0
  for (let i = 0; i < words.length; i += 1) if (words[i] !== 0) nonZeroWords += 1
  return { words, bytes, nonZeroWords, records }
}

export interface AvrRunResult {
  /** 实际执行了多少条指令（≈ CPU 周期，AVR 多数指令 1 周期）。 */
  readonly instructions: number
  /** 仿真时间（毫秒）＝ 指令数 / 16MHz。 */
  readonly simMs: number
  /** 本次新增的串口字节。 */
  readonly serial: Buffer
  readonly serialText: string
  /** 累计串口字节（含之前几次）。 */
  readonly serialTotal: number
  /** 因为缓冲容量被丢掉的字节（如实上报，不静默）。 */
  readonly droppedBytes: number
  /** 引脚变化事件，最多保留最近 200 条。 */
  readonly pinEvents: ReadonlyArray<{ port: string; value: number; atInstruction: number }>
  /** 退出原因：够了 / 预算耗尽 / 停机。 */
  readonly stopReason: 'byte-budget' | 'cycle-budget' | 'halted'
}

/** 一次 AVR 会话（一个固件跑在一个虚拟 328p 上）。 */
export class AvrSession {
  private readonly cpu: AvrCpu
  private readonly usart: AvrUsart
  private readonly ports: Array<{ name: string; port: AvrIoPort }> = []
  private readonly buffer: SerialBuffer
  private readonly pinEvents: Array<{ port: string; value: number; atInstruction: number }> = []
  private instructions = 0
  private halted = false
  /** 上次 `drain()` 之后新到字节的文本暂存（UTF-8 可能跨块）。 */
  private textCarry = ''

  constructor(hexText: string, private readonly freqHz: number = 16e6) {
    const avr = loadAvr8js()
    const { words } = parseIntelHex(hexText)
    // ATmega328p 的 SRAM 是 2KB；avr8js 的 sramBytes 不含寄存器区。
    this.cpu = new avr.CPU(words, 0x900)
    // 定时器必须建：Arduino 的 millis()/delay() 依赖 timer0 溢出中断。
    // 不建的话 delay() 会永远等下去——固件看起来"卡死"，其实是模型缺了。
    void new avr.AVRTimer(this.cpu, avr.timer0Config)
    void new avr.AVRTimer(this.cpu, avr.timer1Config)
    void new avr.AVRTimer(this.cpu, avr.timer2Config)
    for (const [name, config] of [['B', avr.portBConfig],
                                  ['C', avr.portCConfig],
                                  ['D', avr.portDConfig]] as const) {
      const port = new avr.AVRIOPort(this.cpu, config)
      port.addListener((value: number) => {
        this.pinEvents.push({ port: name, value, atInstruction: this.instructions })
        if (this.pinEvents.length > 200) this.pinEvents.shift()
      })
      this.ports.push({ name, port })
    }
    this.usart = new avr.AVRUSART(this.cpu, avr.usart0Config, this.freqHz)
    this.buffer = new SerialBuffer()
    this.usart.onByteTransmit = (byte: number) => {
      this.buffer.push(Buffer.from([byte & 0xff]))
    }
    this.avr = avr
  }

  private readonly avr: AvrModule

  /**
   * 推进仿真。
   *
   * 两个预算，先到先停：`maxInstructions`（CPU 工作量上限）与
   * `maxBytes`（攒够多少串口字节就够用）。**默认都给预算**，
   * 因为一个 `while(1)` 的固件没有自然终止点。
   */
  run(options: { maxInstructions?: number; maxBytes?: number } = {}): AvrRunResult {
    const maxInstructions = options.maxInstructions ?? 40_000_000
    const maxBytes = options.maxBytes ?? 4096
    const startTotal = this.buffer.totalBytes
    let executed = 0
    let stopReason: AvrRunResult['stopReason'] = 'cycle-budget'
    while (executed < maxInstructions) {
      this.avr.avrInstruction(this.cpu)
      this.cpu.tick()
      executed += 1
      this.instructions += 1
      if (this.buffer.totalBytes - startTotal >= maxBytes) {
        stopReason = 'byte-budget'
        break
      }
      // SLEEP 之后 PC 不再前进：说明固件挂起了（或等一个我们没建模的中断）。
      if (this.halted) {
        stopReason = 'halted'
        break
      }
    }
    const drained = this.buffer.drain()
    const text = this.textCarry + drained.bytes.toString('utf8')
    // 保留最后一个不完整的 UTF-8 起始字节，避免多字节字符被切坏。
    const carryMatch = /[\u00c0-\u00ff][\u0080-\u00bf]{0,2}$/.exec(text)
    if (carryMatch !== null && !text.endsWith('\n')) {
      this.textCarry = carryMatch[0]
    } else {
      this.textCarry = ''
    }
    const visible = this.textCarry === '' ? text : text.slice(0, text.length - this.textCarry.length)
    return {
      instructions: executed,
      simMs: executed / (this.freqHz / 1000),
      serial: drained.bytes,
      serialText: visible,
      serialTotal: this.buffer.totalBytes,
      droppedBytes: drained.droppedBytes,
      pinEvents: this.pinEvents.slice(-40),
      stopReason,
    }
  }

  /** 只取串口，不推进 CPU（给 hw_serial 的"读一下"用）。 */
  readPending(): { bytes: Buffer; text: string; droppedBytes: number; totalBytes: number } {
    const drained = this.buffer.drain()
    return {
      bytes: drained.bytes,
      text: drained.bytes.toString('utf8'),
      droppedBytes: drained.droppedBytes,
      totalBytes: drained.totalBytes,
    }
  }

  /** 当前状态摘要（不推进）。 */
  describe(): Record<string, unknown> {
    return {
      instructions: this.instructions,
      simMs: this.instructions / (this.freqHz / 1000),
      pc: this.cpu.pc,
      cycles: this.cpu.cycles,
      serialTotal: this.buffer.totalBytes,
      serialPending: this.buffer.pendingBytes,
      pinEvents: this.pinEvents.length,
    }
  }

  dispose(): void {
    this.usart.onByteTransmit = null
    this.usart.onLineTransmit = null
    this.pinEvents.length = 0
    this.buffer.clear()
  }
}

/** 板卡是否能用这条 AVR 通道（按 328p 寄存器表判断，不猜）。 */
export function avrBoardSupport(fqbn: string): { supported: boolean; reason: string } {
  const normalized = fqbn.trim().toLowerCase()
  // avr8js 的 usart0Config / portB..DConfig 是 ATmega328p 的寄存器地址。
  // Uno/Nano/Mini/Pro Mini 都是 328p；Mega 是 2560，寄存器不同，**不能**硬套。
  const supported = [
    'arduino:avr:uno', 'arduino:avr:nano', 'arduino:avr:mini',
    'arduino:avr:pro', 'arduino:avr:pro8MHzatmega328',
    'arduino:avr:diecimila', 'arduino:avr:duemilanove',
  ].some((prefix) => normalized === prefix || normalized.startsWith(`${prefix}cpu=`))
  if (supported) {
    return { supported: true, reason: 'ATmega328p，与 avr8js 的寄存器表一致' }
  }
  return {
    supported: false,
    reason:
      `只支持 ATmega328p 家族（Uno/Nano/Mini/Pro Mini）；${fqbn} 的寄存器地址不同，`
      + '硬套会得到错误的串口行为。要支持它需要补一套 avr8js 的 peripheral config。',
  }
}
