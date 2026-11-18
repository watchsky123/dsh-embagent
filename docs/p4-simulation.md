# P4：仿真能力交付与边界

> 本文回答"AVR/ESP32 仿真到底能不能用、怎么用、为什么有些不能"。
> 所有数字都是本机实测。

## 一、结论速览

| 能力 | 状态 | 说明 |
|---|---|---|
| **AVR 真实执行 + 串口** | ✅ **可用** | avr8js 在宿主 Node 内跑，`Serial.print` 真的出字节 |
| **AVR 引脚电平** | ✅ 可用 | `hw_sim run` 返回 `pinEvents`（最近 40 条） |
| **AVR 元件动画** | ❌ 不提供 | 那是 Velxio 前端 150+ Web Components 的职责，需浏览器 |
| **AVR 编译** | ✅ 可用 | arduino-cli 真实编译（P1 已交付） |
| **ATmega328p 家族** | ✅ Uno/Nano/Mini/Pro Mini | avr8js 的寄存器表就是 328p |
| **Mega2560** | ❌ 明确拒绝 | 寄存器地址不同，硬套会得到错误的串口行为 |
| **ESP32 仿真** | ❌ 不可用（详见第三节） | 需 QEMU `.so` + 授权 key + Linux |
| **RP2040 仿真** | ❌ 不可用 | 同属前端 rp2040js；未纳入本阶段 |

## 二、AVR 是怎么跑起来的

### 2.1 关键发现：avr8js 不依赖浏览器

Velxio 的 AVR 仿真在前端（`frontend/src/simulation/AVRSimulator.ts`），
但底层库 `avr8js` 是**纯 TypeScript、零运行时依赖、不碰 DOM**——
它只操作一个 `Uint16Array`（程序存储器）和一块 `Uint8Array`（数据空间）。
所以在 Node 里 `require('avr8js')` 直接可用。

实测（`_probe_avr/probe.mjs`，用 arduino-cli 编出的真 hex）：

```
hex：4603 字符 → 810 个非零字
指令数：50000000
串口字节：11
串口文本："HELLO-AVR\r\n"
RESULT: OK
```

`"HELLO-AVR\r\n"` 正是固件里 `Serial.println("HELLO-AVR")` 发出的原文。

### 2.2 一个必须记住的坑：程序存储器是 `Uint16Array`

`new CPU(progMem, sramBytes)` 的 `progMem` 必须是 **`Uint16Array`**（AVR 按
16 位取指），不是 `Uint8Array`。传错了的症状极具误导性：

```
TypeError: Cannot set properties of undefined (setting '63')
    at new AVREEPROM (...peripherals/eeprom.js:61)
```

报错指向 **EEPROM**，真因却在 CPU 的程序存储器类型。所以 `parseIntelHex()`
先把字节拼成字再交给 CPU。

### 2.3 定时器必须建

Arduino 的 `millis()` / `delay()` 依赖 **timer0 溢出中断**。不建
`AVRTimer(timer0Config)`，`delay()` 会永远等下去——固件看起来"卡死"，
其实是模型缺了一个外设。所以 `AvrSession` 里三个定时器全部实例化。

### 2.4 用法

```
hw_compile project=demo boardFqbn=arduino:avr:uno filesJson=[…]   # 编出 hex
hw_sim     project=demo action=start                               # 起会话（自动读产物）
hw_sim     project=demo action=run                                 # 推进 + 取串口
hw_serial  project=demo                                            # 只读新增字节
hw_sim     project=demo action=stop
```

或在面板的 **Velxio** 页签下半部分：起会话 / 推进 / 读串口 / 停止 + 终端。

### 2.5 三个如实的数字

- **`serialTotal`**：累计字节；
- **本次新增**：`drain()` 的增量语义（读过的不会重复返回）；
- **`droppedBytes`**：因 64KB 环形缓冲被丢的字节。**丢就报**——
  串口数据不完整这件事必须能被看见，不能静默。

## 三、ESP32 QEMU：为什么本机不可用

结论：**不是配置问题，是三重硬约束。** 依据是
`velxio-master/Dockerfile.standalone` 的 QEMU 获取逻辑。

### 3.1 二进制是 Linux 的 `.so`，且需要授权 key

```
libqemu-xtensa.so + libqemu-riscv32.so（+ 4 个 esp32*-rom.bin）
```

Dockerfile 里三条获取路径：

1. `prebuilt/qemu/` 里有现成的 `.so` → 直接用；
2. `QEMU_RELEASE_URL` → 从指定 URL 下；
3. `VELXIO_LICENSE_KEY` → 从 velxio.dev 的**带授权 key 的下载端点**取。

三条都不满足时，构建**直接失败**并打印：

```
ERROR: Velxio docker build needs the QEMU runtime libraries.
Pick one:
  a) Free personal key from https://velxio.dev/license/signup ,
     then re-build with --build-arg VELXIO_LICENSE_KEY=vlx_personal_...
  b) Build QEMU yourself (see docs/BUILD-QEMU.md) and drop the .so
     files plus the three esp32*-rom.bin files into prebuilt/qemu/.
```

### 3.2 本机的实际情况

```
prebuilt/qemu/          只有一个 .gitkeep（仓库不自带二进制）
which qemu-system-xtensa → 无
docker                  无
pip install esp32-qemu  → No matching distribution
```

而且 `.so` 是 **Linux 动态库**，本机是 Windows——即使拿到也不能直接加载。

### 3.3 所以这里的选择是"如实降级"，不是"假装支持"

`hw_sim` 对 ESP32 的板卡返回 `not_ready`，并说明要什么：

> ESP32 需要 QEMU（见 docs/p4-simulation.md）

**没有**做一个"看起来能跑"的假通道。要真正启用，需要下列之一：

| 路径 | 代价 | 可行性 |
|---|---|---|
| 拿授权 key + Linux 环境 | 要 velxio.dev 的 key；本机无 Docker/Linux | 需外部条件 |
| 自行编译 QEMU for Windows | 见 `velxio-master/docs/BUILD-QEMU.md`，需交叉编译工具链 | 工作量大，未验证 |
| 用 WSL/Linux 跑整套后端 | 需要 WSL | 可能可行，但违背"纯 DSH 内"的前提 |

## 四、`sitecustomize` 现在是什么

之前它是**手工放在 venv 里的一个文件**，有三个问题：重装 venv 就丢、
不可见、不好关。现在收敛为 `hw_velxio/patch.py`：

```
python -m hw_velxio.patch status    # 在不在、是不是当前版本
python -m hw_velxio.patch install   # 写入/刷新（幂等）
python -m hw_velxio.patch remove    # 删除，完全还原
```

**自动重建**：`serve.ensure_venv()` 装完依赖后会自动 `install`（幂等），
所以 venv 重建不会丢补丁。

**可见**：`serve status` 里有 `patch` 字段：

```json
"patch": {"installed": true, "up_to_date": true, "digest": "e08004f57ed7bf14",
          "note": "补丁存在且是当前版本"}
```

**可关**：`HW_VELXIO_EAGER_CLI=1` 回到上游原始行为（启动慢 76 秒，但零介入）。

### 它到底改了什么

在 `app.api.routes.compile` 执行**之前**，把
`app.services.arduino_cli.ArduinoCLIService` 换成惰性版，并在构造前清掉
三个不可达的 board-manager URL。

实测数据（整条链路的根因）：

```
带那 3 个 URL → core list 76.0s
清掉之后      → core list  0.14s
```

**不改 Velxio 源码**——那是第二事实源，且升级 checkout 后就丢。

## 五、验收命令

```bash
# 1. 补丁状态
python -m hw_velxio.patch status

# 2. 后端
python -m hw_velxio.serve start
python -m hw_velxio.serve status

# 3. 编译 + 仿真（在会话里用工具）
#    hw_compile → hw_sim(start) → hw_sim(run) → hw_serial
```

自检脚本（都在 `scripts/`）：

| 脚本 | 覆盖 |
|---|---|
| `check_tools.mjs` | 22 个工具都注册、描述不越权 |
| `check_host_runtime.mjs` | 宿主半在真 cordis 里 apply 不炸 |
| `check_remote_signature.mjs` | 所有 `@Remote` 参数表是纯标识符 |
| `check_client_runtime.mjs` | 客户端能渲染、视图注册正确 |
| `check_layout_runtime.mjs` | 布局状态不污染、隐藏即卸载 |
| `check_plugin.py` | patch/样式/宿主契约/端到端 |

## 六、诚实的边界（汇总）

1. **没有元件动画**。这是与"不依赖浏览器"的直接冲突，不是没做完。
   面板给的是串口字节流与引脚电平——无可视化时仍然能验证固件行为的那部分。
2. **只支持 ATmega328p**。Mega2560 的寄存器不同，硬套会得到错误结果，
   所以明确拒绝而不是"大概能跑"。
3. **ESP32 需要 QEMU**（授权 key + Linux `.so`），本机不具备，如实降级。
4. **不是实时仿真**。CPU 在一个循环里尽量跑，不与墙钟同步——同步会让
   单次工具调用挂住。
5. **`sim` 会话活在宿主进程里**。插件卸载时会 `disposeAll()` 释放；
   宿主重启则会话消失（需要重新 `hw_sim start`）。这是有意的：
   会话绑定进程，比"跨重启恢复一个半死的 CPU"更可预测。
