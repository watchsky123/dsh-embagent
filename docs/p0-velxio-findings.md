# P0 勘探结论 — Velxio 无浏览器托管前提

> 本文件是 P0 的产出，四条结论都来自**读代码 + 真实网络/下载验证**，不是推测。
> P1–P3 的实现必须遵守这里的约束。

## P1 结论：WS 协议（`/api/simulation/ws/{client_id}`）

来源：`velxio-master/backend/app/api/routes/simulation.py`（全文 460 行）。

**连接**：`ws://127.0.0.1:<port>/api/simulation/ws/<client_id>`，`client_id` 由客户端自定
（同一 id 重连会顶掉旧连接，旧连接清理被跳过）。

**帧格式**：一律 **JSON 文本**，双向都是信封 `{"type": <str>, "data": {<obj>}}`。
服务端推送的 `type` 直接等于内部事件名（`serial_output` / `gpio_change` / `system` /
`error` / `chip_net` …），不是固定 `"event"`。

### 客户端 → 服务端（OSS 镜像里有实现的）

| type | data | 说明 |
|---|---|---|
| `start_esp32` | `{board, firmware_b64, sensors[], wifi_enabled, bus_map}` | 起 QEMU；`board` ∈ `esp32`/`esp32-s3`/`esp32-c3` |
| `stop_esp32` | `{}` | 停并回收 |
| `load_firmware` | `{firmware_b64}` | 热换固件 |
| `esp32_serial_input` | `{bytes:[int], uart:0}` | 串口**输入**（写进 guest 的 UART） |
| `esp32_gpio_in` | `{pin:int, state:0|1}` | 外部拉引脚 |
| `esp32_adc_set` | `{channel, millivolts}` 或 `{channel, raw}` | ADC |
| `esp32_bus_map` | `{spi,i2c,uart,pulls}` | 总线成员 |
| `esp32_status` | `{}` | 查询，服务端回一条 `esp32_status` |

### 服务端 → 客户端（我们要消费的）

| type | data | 用途 |
|---|---|---|
| `serial_output` | `{data: str, uart: int}` | **串口输出**，`hw_serial` 的数据源 |
| `gpio_change` | `{pin, state}` | 引脚电平 |
| `system` | `{event}` | 生命周期（started/stopped…） |
| `error` | `{message, code?}` | 失败原因，**必须原样上报** |
| `chip_net` | `{net, level, ts}` | 跨板连接（Phase 4 之后才用） |

### 不可用（无 pro overlay 时的行为）

`start_pi` / `start_stm32` / `start_picow` 一族**没有任何 OSS 实现**：
`dispatch_ws_sim_handler` 返回未处理，`start_*` 会回一条
`error{message: PRO_BOARD_MESSAGE}`。这是**设计如此**，不是 bug——
RPi/STM32/品牌板是付费能力。我们的工具必须把这条 `error` 转成人话，不能等成"卡住"。

## P2 结论：AVR / RP2040 仿真在哪里

**在前端，不在后端。**

- `frontend/src/simulation/AVRSimulator.ts`、`RP2040Simulator.ts`、`RiscVSimulator.ts`、
  `Esp32C3Simulator.ts` —— 依赖 npm 的 `avr8js@0.21.0`、`rp2040js@1.3.2`（见 `frontend/package.json`）。
- 后端只有三条仿真通道：**ESP32 → QEMU 子进程**（`esp_qemu_manager.py`，UART0 走 TCP
  socket 再转成 `serial_output` 事件）、**RPi/STM32 → pro overlay**、**自定义芯片 → WASM**
  （`wasm_chip_runtime.py`）。
- `prebuilt/qemu/` 里**只有一个 `.gitkeep`**：QEMU 二进制不在仓库里，
  靠 `Dockerfile.standalone` 装 `qemu-system-xtensa` / `qemu-system-riscv32`
  （可用 `QEMU_ESP32_BINARY` / `QEMU_RISCV32_BINARY` 指向自备二进制）。

**对方案的直接影响**：只依赖 DSH、不开浏览器时，

- **ESP32 族**：可以做到真仿真（QEMU + WS 串口）。
- **AVR / RP2040**：后端没有 CPU 模型，只能
  (a) 在 Node 宿主里加载 `avr8js` / `rp2040js` 自建 runner（能拿到串口 + 引脚，
      **拿不到元件动画**），或 (b) 只交付"真实编译出 hex"，不交付仿真。
- 本阶段（P0–P3）取 (b)：**编译是真的，AVR 仿真留给 P4**。

## P3 结论：后端最小依赖集

`backend/requirements.txt` 共 8 项，但**只有 6 项在 OSS 导入链上**：

| 包 | 是否必需 | 依据 |
|---|---|---|
| fastapi | ✅ 必需 | `app/main.py:21` 顶层 |
| uvicorn[standard] | ✅ 必需 | 运行入口 |
| websockets | ✅ 必需 | WS 端点 |
| pydantic | ✅ 必需 | 5 处，schema |
| pydantic-settings | ✅ 必需 | `app/core/config.py` |
| httpx | ✅ 必需 | iot_gateway / micropython_libs / news 三个路由 |
| mcp | ⚠️ **仅** `app/mcp/server.py` | `main.py` **不导入**它；是独立 MCP 服务入口 |
| esptool | ❌ **代码里 0 处 import** | 只在镜像里当 CLI 用 |
| wasmtime | ⚠️ 仅 ESP32 自定义芯片 | `esp32_worker.py:220` 是**函数内 lazy import** |
| zstandard | ❌ 代码里 0 处 import | Pi 启动镜像用，pro 能力 |

→ **最小可跑集**：`fastapi==0.115.0 uvicorn[standard]==0.32.0 websockets>=12.0
pydantic>=2.11.0 pydantic-settings>=2.6.0 httpx>=0.27.1`。
`wasmtime` 按需装（只有用自定义芯片才需要）。

## P4 结论：网络可达性（实测）

| 目标 | 结果 | 含义 |
|---|---|---|
| `pypi.tuna.tsinghua.edu.cn` | 302 ✅ | pip 可装（当前镜像） |
| `github.com` | 200 ✅ | 可达 |
| `raw.githubusercontent.com` | **000 ❌** | 官方 arduino-cli install.sh **不可用** |
| `downloads.arduino.cc` | 302/206 ✅ | **arduino-cli 直接下载可用** |
| `registry.npmjs.org` | 200 ✅ | 前端依赖可装（P4 若走 avr8js） |

**实测已成功下载** `arduino-cli_latest_Windows_64bit.zip`（20,404,890 字节，
内含 `arduino-cli.exe` + `LICENSE.txt`）。

**因此**：arduino-cli 必须**直接下 zip**，不能走 `install.sh`（那条路径在本机必然失败）。
镜像源同理：`--additional-urls` 走 `downloads.arduino.cc` 的 package index。

## 对 P1–P3 的硬约束

1. 后端必须以 `cwd = <velxio_root>/backend` 启动，`app` 包才能被 import。
2. venv 必须独立（系统 Python 是 Anaconda + torch，混装必炸）。
3. arduino-cli 只从 `downloads.arduino.cc` 下 zip。
4. ESP32 仿真需要 QEMU 二进制，仓库不自带；缺就报"缺 QEMU"而不是假装能跑。
5. AVR/RP2040 的"仿真"在本阶段明确**不交付**，只交付编译。
6. `start_pi`/`start_stm32` 的 `PRO_BOARD_MESSAGE` 要转成人话上报。
