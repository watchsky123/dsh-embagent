/**
 * 原生文件夹选择 —— **客户端侧**的正确接线。
 *
 * 上一版接错了层，这是"宿主没有提供目录选择能力"的真实原因：
 * `ctx.directoryPicker` 是 **宿主（node）上下文**里的服务
 * （`dsh-host-directory-picker` 的文档原话："how the web GUI host lets an
 * operator select a workspace directory"）。hwforge 在它的宿主半
 * （TypertRemoteService）里 `ctx.get('directoryPicker')` 是对的；我们把同
 * 一行代码搬进**客户端（浏览器）上下文**，那里根本没有这个服务，
 * `ctx.get` 永远返回 undefined——于是每次点击都报"宿主没有提供"。
 *
 * 宿主自己的原生选择器客户端（`dsh-client-ui-directory-picker-native`）
 * 给出的顺序就是这里的顺序：
 *
 *   1. `globalThis.__DSH_DIRECTORY_PICKER__.pick()` —— Electron 桌面壳的
 *      preload IPC，直达主进程的原生对话框（Windows 资源管理器选择框）；
 *   2. `ctx.get('uiWorkspace').pickDirectory()` —— web GUI 路径，走 RPC 到
 *      宿主的 directoryPicker（native/browse 由宿主启动时探测决定）。
 *
 * 两个入口都**不接受 AbortSignal**（与宿主 seam 的 `capability.pick(signal)`
 * 不同，那是宿主侧的形状），所以超时只能在客户端做竞速；超时后对话框可
 * 能还开着，用户选完的结果会被忽略——如实写进错误文案，不假装取消了它。
 *
 * @module dsh-hw-velxio/client/directoryPicker
 */

/** 调用时才解析的两个客户端入口（延迟读取：ui-workspace 可能比本插件晚装载）。 */
export interface DirectoryPickerSeams {
  readonly desktop?: { pick(): Promise<unknown> }
  readonly uiWorkspace?: { pickDirectory(): Promise<unknown> }
}

/** 用户开了框一直不选时的上限：与宿主 seam 的约定一致（3 分钟）。 */
const PICK_TIMEOUT_MS = 180_000

/**
 * 构造 `pickDirectory()`：返回选中的绝对路径；取消返回 `null`。
 *
 * @param getSeams - 每次点击时重新解析入口，保证装载顺序不影响可用性。
 * @throws 当前上下文两个入口都拿不到时，报出**查过的是哪两个名字**，
 *         而不是含糊的"宿主没有提供能力"。
 */
export function makePickDirectory(
  getSeams: () => DirectoryPickerSeams,
): () => Promise<string | null> {
  return async (): Promise<string | null> => {
    const seams = getSeams()
    const run = seams.desktop?.pick?.()
      ?? seams.uiWorkspace?.pickDirectory?.()
      ?? null
    if (run === null) {
      throw new Error(
        '拿不到目录选择入口：当前上下文既没有桌面壳（__DSH_DIRECTORY_PICKER__），'
        + '也没有 uiWorkspace.pickDirectory。'
        + '（`ctx.directoryPicker` 是宿主侧服务，浏览器上下文里没有它——'
        + '请直接填路径，或在桌面壳里使用。）')
    }
    let timer: ReturnType<typeof setTimeout> | null = null
    try {
      const value = await Promise.race([
        run,
        new Promise<never>((_, reject) => {
          timer = setTimeout(() => {
            reject(new Error(`选择目录超时（${PICK_TIMEOUT_MS / 1000}s）；`
              + '对话框可能仍开着，之后的选择不会被采用'))
          }, PICK_TIMEOUT_MS)
        }),
      ])
      // 取消：native 返回 null；桌面壳可能返回空串/undefined，一并归一为 null。
      return typeof value === 'string' && value !== '' ? value : null
    } finally {
      if (timer !== null) clearTimeout(timer)
    }
  }
}
