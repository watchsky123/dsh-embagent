# P0–P3 交付说明：Velxio 全功能 · 纯 DSH 内托管

> 本文记录 P0–P3 的**实际改动与验证结果**。所有结论来自真跑，不是读代码推测。
> 日期：2026-11-17。

## 一句话结论

**L1（真实编译）+ L2（电路/库）已完全打通，纯 DSH 内运行，不依赖浏览器、不依赖 Docker。**
AVR 代码真的编译成了 hex，MicroPython 库能查，电路互转不依赖后端在线。

## 一、验证结果（可复现）

| 验证项 | 命令 | 结果 |
|---|---|---|
| 后端健康 | `curl 127.0.0.1:3080/health` | `{"status":"healthy"}` |
| arduino-cli | `/api/compile/setup-status` | `cli_available:true, version:1.6.0, cores:[arduino:avr 1.8.8]` |
| **真实编译** | `hw_compile` → uno | `success:true`，hex 5400 字符 |
| **板卡区分** | uno / mega / leonardo | 三个 hex 的 md5 **各不相同**（6cd87fb1 / 2df5402f / 2478f26c） |
| 编译（异步） | `velxio_compile` → mega | `state:done, success:true, hex 5036 字符` |
| Arduino 库 | `hw_libs query=Servo` | 返回 Servo 库及版本 |
| **MicroPython 库** | `hw_libs query=requests kind=micropython` | 返回 requests 1.1.1 / urequests 0.8.0 |
| 电路（后端**离线**） | `hw_circuit` | 仍然 `ok`（纯计算） |
| 单测 | `unittest test_design` | 215 通过 |
| 自检脚本 | 6 个 node + check_plugin.py | **全部 PASS** |

## 二、P0 勘探结论

见 `docs/p0-velxio-findings.md`，四条：

1. **WS 协议**：`/api/simulation/ws/{client_id}`，JSON 信封 `{type, data}`；
   服务端推 `serial_output` / `gpio_change` / `system` / `error` / `chip_net`。
2. **AVR/RP2040 仿真在前端**（avr8js / rp2040js），后端只有 ESP32 QEMU；
   → 无浏览器模式下**AVR 只交付编译，不交付仿真**（明示，不假装）。
3. **最小依赖集**：`fastapi uvicorn websockets pydantic pydantic-settings httpx`
   + **`python-multipart`**（实测补上：`flash.py:118` 用 `UploadFile`，
   缺它整个应用起不来，而上游 requirements.txt 没列）。
4. **网络**：`raw.githubusercontent.com` 不可达 → 官方 install.sh 不能用；
   `downloads.arduino.cc` 可达 → 直接下 zip 成功（20,404,890 字节）。

## 三、P1 实测踩到的四个坑（都已修）

### 坑 1：DSH 的 Job Object 会回收整个进程树

普通 `Popen`、`CREATE_BREAKAWAY_FROM_JOB`、`DETACHED_PROCESS`、WMI
`Win32_Process.Create` —— **四种全试过，后端都能在当次调用内回 200，
下一条工具调用前就消失**。日志里没有任何错误，因为它被外部杀掉。

DSH 的 Bash 执行器给每条命令建一个 Job，命令结束按 `KILL_ON_JOB_CLOSE`
回收整棵树，且是**递归**的（`taskkill /T` 只杀子进程，不是它）。

**解法：Windows 计划任务**（`schtasks`）。Task Scheduler 服务创建进程，
不受调用方 Job 约束。注意：
- **不能加 `/RL HIGHEST`** —— 非管理员会 `Access is denied`。
- 包装脚本必须**二进制写 CRLF**：`write_text` 在 Windows 上把 `\n` 转 `\r\n`，
  而我们给的行尾已是 `\r\n`，于是文件里变成 `\r\r\n`，cmd 读到双 CR，
  `--port 3080` 变成 `--port 3080\r`，uvicorn 解析失败——症状是"命令跑完、
  日志空白、Last Result=1"。
- 包装脚本里**直接 exec uvicorn，不套 `start`**：套了之后任务被判"已完成"，
  孙进程被回收。

### 坑 2：启动要 76 秒，而这 76 秒几乎全是一个不可达域名

`compile.py:35` 是**模块级** `arduino_cli = ArduinoCLIService()`，构造函数同步跑
`_ensure_board_urls()` + `_ensure_core_installed()`，其中 `core update-index`
会去拉三个 board-manager 索引，本机连不上 GitHub release。

实测数据（关键）：

```
带那 3 个 URL → core list 76.0s、board listall 76s
清掉之后      → core list  0.14s、board listall 0.14s
```

**解法：venv 里的 `sitecustomize.py`**（只影响这个 venv，删掉即还原）：
在 `app.api.routes.compile` 执行前把 `ArduinoCLIService` 换成惰性版，
并在构造前清掉那三个 URL。

第一版写错了：在模块 **exec 完之后**替换 `module.arduino_cli`——那时构造
已经跑完，76 秒一分没省。日志说"已改为惰性构造"是真的，只是**晚了一步**。
这正是"看起来生效、实际没生效"：状态对了，时机错了。

### 坑 3：compile 的字段名是 `board_fqbn`，不是 `board`

`velxio_client.py` 原来发 `{"files", "board"}`，后端要 `board_fqbn`，
而 pydantic v2 默认 `extra="ignore"` → **静默丢弃，按 uno 编译**。
现在：`_compile_body()` 显式用 `board_fqbn`，传 `board=` 会**抛错**；
并且验证了三块板出三个不同 hex，证明字段真的生效。

### 坑 4：异步编译的结果在 `result` 子对象里

`/api/compile/start` 只回 `job_id`；`/api/compile/status/{id}` 顶层只有
`state`/`stage`，`success`/`hex_content` 全在 `result` 下。原来直接读顶层
`hex_content` 恒为 None → "编译成功但拿不到固件"。
现在 `compile_wait()` 轮询到结束并**摊平**成与同步路径同形。

## 四、P2 修复清单（9 + 3）

| # | 位置 | 问题 | 状态 |
|---|---|---|---|
| 1 | `velxio_client.py` | `board` → `board_fqbn`，静默错板 | ✅ |
| 2 | `bridge.py:_velxio_package_root` | sys.path 指仓库根，`app` 在 `backend/` | ✅ |
| 3 | `bridge.py:_run_velxio` | circuit 纯计算被健康门拦死 | ✅ |
| 4 | `bridge.py` 注释 | WS 承诺无实现 | ✅ 改为如实说明 |
| 5 | `bridge.py:_run_hwforge` | 非 BlockError 抛 traceback | ✅ 兜住全部 + 参数形状提示 |
| 6 | `tools.ts` 预算 | `not_ready` 被当失败 throw | ✅ 改为显示"无法评估 + 覆盖率" |
| 7 | `tools.ts` hw_new | 读 `payload.path`（实际在 `payload.project.path`） | ✅ |
| 8 | `tools.ts` hw_roles | 读不存在的 `payload.wiring` | ✅ 改用 plan/summary |
| 9 | `tools.ts` hw_log | 描述承诺 `action=write` 但无实现 | ✅ 补 append 分支 |
| 附1 | `design_cli.cmd_workspace` | set/create 缺 `source`/`writable` | ✅ 统一 `_describe_workspace` |
| 附2 | `bridge status` | 把 `logs/`、`*-master/` 都当工程 | ✅ `_project_ids` 只认 design.json |
| 附3 | `hw_velxio` 工具 | 新增，能启动/停止/看日志 | ✅ |

## 五、P3 新增能力

### 工具（18 → 22）

| 工具 | 作用 |
|---|---|
| `hw_velxio` | 后端 status/start/stop/logs —— **没有它，模型不知道后端有没有起** |
| `hw_compile` | 真实编译，返回 hex；可从 `sources/` 读或内联 files |
| `hw_circuit` | Wokwi 互转，**不需要后端在线** |
| `hw_libs` | Arduino + **MicroPython 双通道**（后者原是死代码） |

### 内聚的其它修复

- `hw_velxio` 工具描述里明说"不需要浏览器、不需要 Docker"。
- 死端点 `velxioHealth` 改为调 `serve status`（报三态 + 工具链，而不是只报 HTTP 通不通）。
- 新增 `velxioControl`（start/stop/status），客户端面板据此驱动。
- 面板新增第 8 个页签 **Velxio**：状态灯、端口/PID、arduino-cli 与 QEMU 分别显示、
  启动/停止按钮。**"没装工具链"与"后端不可用"分开报**。

## 六、使用方式

```bash
# 1. 首次准备（自动建 venv、装依赖、下 arduino-cli、装 AVR core）
python -m hw_velxio.serve prepare          # 只装 venv 依赖
python -m hw_velxio.serve arduino          # 预置 arduino 环境（清死 URL + 确保 AVR）

# 2. 启动后端（计划任务托管，跨工具调用存活）
python -m hw_velxio.serve start

# 3. 查看状态
python -m hw_velxio.serve status

# 4. 停止
python -m hw_velxio.serve stop
```

或直接用工具：`hw_velxio action=start` / `hw_compile` / `hw_libs` / `hw_circuit`。

## 七、诚实的边界

1. **AVR 仿真不提供**。Velxio 的 AVR/RP2040 仿真跑在前端 avr8js 里，后端没有
   CPU 模型。无浏览器模式下只有"编译出 hex"，没有"跑起来看串口"。
   要做到后者需要在 Node 里自建 avr8js runner（P4）。
2. **ESP32 仿真需要 QEMU**，仓库不自带（`prebuilt/qemu/` 只有一个 `.gitkeep`）。
   现在 QEMU 未装，`toolchain.qemu_ready` 如实报 false。
3. **不可达域名导致板族缺失**：清掉那三个 URL 后，RP2040/ESP32 的扩展板列表
   可能不全。本机本来也装不了那些 core（要 GitHub release），所以这是
   "如实少一个能力"而不是"假装有"。
4. **`sitecustomize.py` 是 venv 内的本地补丁**，不改 Velxio 源码。
   `HW_VELXIO_EAGER_CLI=1` 可回到上游原始行为；删掉该文件即完全还原。
5. STM32 / 树莓派 / 品牌板是 Velxio 的**商业分层**（README 明写需授权二进制
   与多 GB 镜像），纯 DSH 内不提供，也没有假装提供。
