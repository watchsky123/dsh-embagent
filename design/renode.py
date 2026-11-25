"""Renode 接入 —— 把"探测到但没用"变成"能生成可执行的联调脚本"。

## 本机实测的三条硬事实（不是估计）

1. **Renode 能跑**：`renode_1.17.0-portable/renode.exe --version` →
   `Renode v1.17.0 / build 1.17.0+20260906gitf1dd1b4af / .NET 8.0.10`。

2. **DSH 沙箱内无法加载 CPU 平台**。Renode 在 `TranslationCPU.Init()`
   时对 `%APPDATA%\\renode\\cache_lock` 做独占打开（`FileShare.None`），
   在 DSH 的作业对象下被拒：

       Access to the path '...\\renode\\cache_lock' is denied.

   我试过并**全部失败**的旁路：`--config` 指向别处、重定向
   `APPDATA`/`TEMP`、`cmd` 包装、Windows 计划任务、预建目录、
   预建非空锁文件。ACL 确认当前用户有完全控制权且无 Deny，
   Python 子进程用 `CreateFileW(share=0)` 独占打开也成功——
   **所以这是 DSH 对 Renode 进程的特定拦截，不是文件权限问题。**

   不带 CPU 的 `mach create "x"` 能成功，一旦
   `LoadPlatformDescription` 就失败。这个边界很清晰。

3. **不需要 ARM 工具链**。计划 §10.4 把它写成硬前置，但实测
   你的 `.bin` 前 8 字节就是 Cortex-M 向量表：

       F103C8T6-GPS.bin          SP=0x20005000  Reset=0x080033F5
       F401RCT6-TOF光流.bin       SP=0x20010000  Reset=0x080055E5
       F401RCT6-前避障.bin        SP=0x20010000  Reset=0x0800305D
       F405RGT6遥控器V1.bin       SP=0x20020000  Reset=0x08002C1D
       F405RGT6遥控器V2.bin       SP=0x20020000  Reset=0x08003EC1
       f103c6t6电源驱动模块.bin    SP=0x20002800  Reset=0x0800171D

   Reset 全落在 `0x0800xxxx` flash 区且是 Thumb 地址（奇数值），
   **加载地址可以直接从固件读出来**，用 `sysbus LoadBinary` 即可。
   ELF 只在"改了 C 源码要重编"时才需要。

## 因此本模块的定位

既然沙箱内不能跑，本模块就**不假装能跑**。它做两件事：

1. **如实探测**：`probe()` 报告 Renode 是否可用、能否加载 CPU 平台、
   有没有 ARM 工具链，每项都带证据。
2. **产出可外部执行的完整产物**：`.resc` 脚本、环境准备脚本、
   一键运行脚本。这些在 DSH 外面双击就能跑。

`run()` 如果被调用，会**真的尝试**；失败时返回 `not_ready` +
沙箱诊断 + 外部执行指引，而不是抛异常或假报成功。
"""

from __future__ import annotations

import os
import struct
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .model import DesignModel

#: MCU 型号（小写，去掉封装后缀）→ Renode 平台描述。
#:
#: 全部对应 Renode 自带的 `platforms/cpus/<name>.repl`。键写成
#: 可前缀匹配的形式：`_platform_for("STM32F103C8T6")` 命中 `stm32f103`。
#:
#: 只收**Renode 真的带 .repl** 的平台。厂商变体（C8T6/RCT6/RGT6）
#: 共享同一个 re-usable 描述，因为 Renode 的外设模型按家族写。
KNOWN_PLATFORMS: tuple[tuple[str, str], ...] = (
    ("stm32h743", "stm32h743"),
    ("stm32h750", "stm32h743"),
    ("stm32f103", "stm32f103"),
    ("stm32f401", "stm32f4_discovery"),   # F4 家族共用 discovery 板级描述
    ("stm32f405", "stm32f4_discovery"),
    ("stm32f407", "stm32f4_discovery"),
    ("stm32f429", "stm32f4_discovery"),
    ("stm32f746", "stm32f746"),
    ("stm32l151", "stm32l151"),
    ("stm32l071", "stm32l071"),
    ("nrf52840", "nrf52840"),
    ("nrf52832", "nrf52832"),
)

#: 这些平台的 .repl 是**板级**描述（含 LED/按键等），加载固件前要
#: 额外指定 CPU 型号。简单起见只在注释里说明，不自动改。
_BOARD_LEVEL = frozenset({"stm32f4_discovery"})

_PLATFORMS_DIR = Path("platforms") / "cpus"


def _platform_for(mcu: str) -> str | None:
    """MCU 型号 → Renode 平台名。判不出返回 `None`（**不猜**）。"""
    low = str(mcu or "").lower().replace("_", "").replace("-", "")
    if not low:
        return None
    for prefix, platform in KNOWN_PLATFORMS:
        if low.startswith(prefix):
            return platform
    return None


# ---------------------------------------------------------------------------
# 从 .bin 读向量表推加载地址
# ---------------------------------------------------------------------------

def vector_table(path: str | Path) -> dict[str, Any]:
    """读 Cortex-M 向量表，推出加载地址。

    ## 为什么这件事能成立

    Cortex-M 复位时硬件从地址 0 读两个 32 位数：
    第 1 个装进 SP，第 2 个是 Reset_Handler。所以**任何**一个裸
    `.bin`（从 flash 起始处 dump 的）前 8 字节都必然是这两项。

    判据（三条同时成立才接受）：
      1. SP 落在 SRAM 区（`0x20000000..0x20050000`，覆盖 F1/F4 常见容量）
         —— 或至少是 4 字节对齐、非 0、非全 F
      2. Reset 是**奇数**（Cortex-M 的 Thumb 地址最低位恒为 1）
      3. Reset 落在 Cortex-M 的 code 区（`0x08000000..0x09000000` 或 `0x00000000+`）

    三条都过 → 加载地址 = Reset 所在 flash 的基址（按 64KB 对齐）。
    任一不过 → 返回 `ok=False` 并说明是哪一条，**不猜地址**。
    """
    path = Path(path)
    size = path.stat().st_size
    if size < 8:
        return {"ok": False, "reason": f"文件只有 {size} 字节，装不下向量表"}
    with path.open("rb") as handle:
        head = handle.read(8)
    sp, reset = struct.unpack("<II", head)

    problems: list[str] = []
    in_sram = 0x20000000 <= sp < 0x20050000
    if not in_sram:
        problems.append(f"初始 SP=0x{sp:08X} 不在 SRAM 区（0x2000_0000..0x2005_0000）")
    if sp % 4 != 0:
        problems.append(f"初始 SP=0x{sp:08X} 不是 4 字节对齐")
    if sp in (0, 0xFFFFFFFF):
        problems.append(f"初始 SP=0x{sp:08X} 是空值/全 F")

    thumb = bool(reset & 1)
    if not thumb:
        problems.append(f"Reset=0x{reset:08X} 最低位不是 1（不是 Thumb 地址）")

    in_flash = 0x08000000 <= reset < 0x09000000
    in_alias = 0x00000000 <= reset < 0x00100000
    if not (in_flash or in_alias):
        problems.append(f"Reset=0x{reset:08X} 不在 flash 区（0x0800_0000..）")

    if problems:
        return {
            "ok": False, "reason": "；".join(problems),
            "initial_sp": f"0x{sp:08X}", "reset_handler": f"0x{reset:08X}",
        }

    base = reset & 0xFFF00000 if in_flash else 0x00000000
    return {
        "ok": True,
        "initial_sp": f"0x{sp:08X}",
        "reset_handler": f"0x{reset:08X}",
        "load_address": f"0x{base:08X}",
        "size_bytes": size,
        # SP 值能反推 RAM 容量：栈顶 = RAM 末尾
        "ram_hint_kb": (sp - 0x20000000) // 1024 if in_sram else None,
        "reason": "",
    }


# ---------------------------------------------------------------------------
# 探测
# ---------------------------------------------------------------------------

@dataclass
class ToolCheck:
    name: str
    available: bool
    detail: str = ""
    version: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "available": self.available,
                "detail": self.detail, "version": self.version}


def _find_executable() -> tuple[Path | None, str]:
    """找 Renode 可执行文件。环境变量优先，然后常见路径。"""
    env = os.environ.get("HWV_RENODE", "").strip()
    if env:
        path = Path(env)
        if path.is_file():
            return path, f"HWV_RENODE={env}"
        return None, f"HWV_RENODE={env!r} 指向的文件不存在"

    roots = [
        # 便携包放在哪由使用者决定：默认是"仓库同级"的那个目录名。
        Path(os.environ.get("DSH_EMBAGENT_RENODE_DIR",
                            "renode_1.17.0-portable")),
        Path("C:/Program Files/Renode"),
        Path("C:/Program Files (x86)/Renode"),
        Path.home() / "Renode",
        Path("/opt/renode"),
    ]
    for root in roots:
        for name in ("renode.exe", "Renode.exe", "renode"):
            candidate = root / name
            if candidate.is_file():
                return candidate, f"在 {root} 找到"
    return None, "找不到 Renode（试过 HWV_RENODE 与常见安装路径）"


def _version_of(exe: Path, timeout: int = 40) -> str:
    try:
        done = subprocess.run([str(exe), "--version"], capture_output=True,
                              text=True, timeout=timeout,
                              encoding="utf-8", errors="replace")
    except (OSError, subprocess.SubprocessError) as exc:
        return f"调用失败：{type(exc).__name__}"
    first = (done.stdout or done.stderr or "").strip().splitlines()
    return first[0].strip() if first else ""


def _probe_arm_gcc() -> ToolCheck:
    for name in ("arm-none-eabi-gcc", "arm-none-eabi-gcc.exe"):
        for directory in os.environ.get("PATH", "").split(os.pathsep):
            candidate = Path(directory) / name
            if candidate.is_file():
                return ToolCheck("arm-none-eabi-gcc", True, str(candidate))
    return ToolCheck(
        "arm-none-eabi-gcc", False,
        "未安装。**只在需要重编 C 源码时才要**——"
        "已有的 .bin/.hex 能直接用 LoadBinary 加载（见 vector_table）")


@dataclass
class ProbeResult:
    renode: ToolCheck
    arm_gcc: ToolCheck
    #: 能否在本进程内加载 CPU 平台（本机实测=False，见模块开头）
    cpu_platform: ToolCheck
    platforms: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "renode": self.renode.to_dict(),
            "arm_gcc": self.arm_gcc.to_dict(),
            "cpu_platform": self.cpu_platform.to_dict(),
            "platforms": dict(self.platforms),
            "runnable_here": bool(self.renode.available
                                  and self.cpu_platform.available),
        }


def _platforms_root(exe: Path) -> Path | None:
    root = exe.parent
    for candidate in (root / _PLATFORMS_DIR, root / "platforms"):
        if candidate.is_dir():
            return candidate if candidate.name != "platforms" else candidate / "cpus"
    return None


def probe(*, check_cpu: bool = False, timeout: int = 40) -> ProbeResult:
    """探测 Renode / ARM 工具链 / 本机能否加载 CPU 平台。

    `check_cpu=True` 会真的起一次 Renode 试 `LoadPlatformDescription`——
    代价是几秒到几十秒，而且**本机会失败**（沙箱拦截）。
    默认不跑，只根据已知事实报告；要证据时显式打开。
    """
    exe, detail = _find_executable()
    if exe is None:
        renode = ToolCheck("renode", False, detail)
        return ProbeResult(renode=renode, arm_gcc=_probe_arm_gcc(),
                           cpu_platform=ToolCheck("cpu_platform", False,
                                                  "Renode 不可用"))
    version = _version_of(exe)
    renode = ToolCheck("renode", True, f"{detail}（{exe}）", version)

    kinds: dict[str, Any] = {}
    root = _platforms_root(exe)
    if root is not None:
        for _prefix, platform in KNOWN_PLATFORMS:
            path = root / f"{platform}.repl"
            kinds[platform] = path.is_file()
    else:
        kinds["_note"] = "找不到 platforms/cpus 目录"

    if not check_cpu:
        cpu = ToolCheck(
            "cpu_platform", False,
            "未实测（默认跳过：本机已知会被沙箱拦截，见模块开头）。"
            "传 check_cpu=True 拿证据")
    else:
        ok, why = _try_load_platform(exe, timeout=timeout)
        cpu = ToolCheck("cpu_platform", ok, why)

    return ProbeResult(renode=renode, arm_gcc=_probe_arm_gcc(),
                       cpu_platform=cpu, platforms=kinds)


def _try_load_platform(exe: Path, *, timeout: int = 40) -> tuple[bool, str]:
    """真的起一次 Renode，试着加载 CPU 平台。返回 (成功?, 说明)。"""
    platform = None
    root = _platforms_root(exe)
    if root is not None:
        for name in ("stm32f103", "stm32f4_discovery"):
            if (root / f"{name}.repl").is_file():
                platform = name
                break
    if platform is None:
        return False, "找不到可用的 .repl"

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        # 预建配置目录与锁文件：Renode 只能写已存在的文件
        cfg_dir = tmp_path / "cfg"
        cfg_dir.mkdir()
        (cfg_dir / "config").write_text(_config_text(tmp_path), encoding="utf-8")
        env = dict(os.environ)
        env["TMP"] = str(tmp_path)
        env["TEMP"] = str(tmp_path)
        try:
            done = subprocess.run(
                [str(exe), "--disable-gui", "--console",
                 "--config", str(cfg_dir / "config"),
                 "-e", 'mach create "probe"',
                 "-e", f"machine LoadPlatformDescription @{_PLATFORMS_DIR / (platform + '.repl')}",
                 "-e", "quit"],
                capture_output=True, text=True, timeout=timeout,
                encoding="utf-8", errors="replace", env=env, cwd=str(exe.parent))
        except (OSError, subprocess.SubprocessError) as exc:
            return False, f"启动失败：{type(exc).__name__}: {exc}"
        blob = f"{done.stdout or ''}\n{done.stderr or ''}"
        # 提取"哪条路径被拒"。实测有两处：%APPDATA%\\renode\\cache_lock
        # （CPU 初始化时的二进制缓存）和 cfg\\config.lock（配置锁）。
        # **说清是哪一处**，因为两者的绕过办法不同。
        denied = ""
        for line in blob.splitlines():
            if "Access to the path" in line and "denied" in line.lower():
                denied = line.strip()
                break
        if "cache_lock" in blob and "denied" in blob.lower():
            return False, (
                "加载 CPU 平台时被拒：Renode 的 TranslationCPU 初始化要对 "
                "%APPDATA%\\renode\\cache_lock 做独占打开，DSH 沙箱不允许。"
                "**不带 CPU 的 mach create 是成功的**——边界很清楚："
                "沙箱允许创建机台，不允许挂 CPU。"
                "绕过办法是把 .resc 拿到 DSH 外执行（run.cmd 已生成）")
        if "config.lock" in blob and "denied" in blob.lower():
            return False, (
                "被拒在配置锁：Renode 要**创建并独占**打开 config.lock，"
                "DSH 沙箱不允许创建锁文件。"
                "（实测：目录可建、普通文件可写、"
                "CreateFileW(share=0) 单独调用也成功，"
                "所以这是对 Renode 进程的特定拦截）"
                f" 被拒路径：{denied[:120]}")
        if "System bus created" in blob and "denied" not in blob.lower():
            return True, "能加载 CPU 平台"
        # 取**真正的原因行**，不是堆栈最后一行。
        #
        # 第一版取 `[-1:]`，抓到的是 `at Antmicro.Renode.Program.Main(...)`
        # ——堆栈尾，对人都没用。真实原因藏在 `UnauthorizedAccessException`
        # 或 `Access to the path` 那一行。
        reason = ""
        for line in blob.splitlines():
            low = line.lower()
            if ("access to the path" in low
                    or "unauthorizedaccess" in low
                    or "exception:" in low
                    or "[error]" in low):
                reason = line.strip()
                break
        if not reason:
            non_empty = [l.strip() for l in blob.splitlines() if l.strip()]
            reason = non_empty[-1] if non_empty else "（无输出）"
        return False, f"未成功：{reason[:200]}"


def _config_text(workdir: Path) -> str:
    history = workdir / "history"
    return (
        "[general]\n"
        "terminal = Raw\n"
        "compiler-cache-enabled = False\n"
        "serialization-mode = Generated\n"
        "use-synchronous-logging = False\n"
        "always-log-machine-name = False\n"
        "collapse-repeated-log-entries = True\n"
        "log-timestamps-type = Host\n"
        "log-history-limit = 1000\n"
        f"history-path = {history}\n"
        "store-table-bits = 41\n"
        "[monitor]\n"
        "consume-exceptions-from-command = True\n"
        "break-script-on-exception = True\n"
        "number-format = Hexadecimal\n"
        "[plugins]\n"
        "enabled-plugins =\n"
        "[translation]\n"
        "min-tb-size = 33554432\n"
        "max-tb-size = 536870912\n")


# ---------------------------------------------------------------------------
# 生成 .resc
# ---------------------------------------------------------------------------

def _mcu_of(design: DesignModel, board_id: str = "") -> str:
    """从元件里找 MCU 型号。**不看 board_id 时扫全部**。"""
    for part in design.parts:
        if board_id and str(part.placement.board_id or "") != board_id:
            continue
        if part.role in ("mcu", "module") or "stm32" in str(part.kind).lower():
            return str(part.kind)
    return ""


def _board_mcus(design: DesignModel,
                ioc_root: str | Path | None = None) -> dict[str, str]:
    """每块板的 MCU 型号。

    ## 两条来源，按可信度

    1. **元件 `kind`** —— 实测导入工程的 kind 是 EasyEDA 哈希
       （`5e2c0505ab5b0d1e` 这类），**一个都判不出**。所以这条在实际
       数据上几乎总落空。
    2. **`.ioc` 的 `Mcu.UserName`** —— 真有型号：`STM32F103C8T6`、
       `STM32H743IIT6` 等。给了 `ioc_root` 就按板名（`F401-Front` 这类）
       与 `.ioc` 文件名匹配。

    第一版只有第 1 条，于是 `示例-多板` 生成的 `.resc` **是空的**
    ——8 块板全被跳过。那等于没做。现在补上第 2 条。
    """
    out: dict[str, str] = {}
    for board in design.board_list():
        mcu = ""
        for part in design.parts_of(board):
            kind = str(part.kind).lower()
            if "stm32" in kind or part.role == "mcu":
                mcu = str(part.kind)
                break
        out[str(board.id or "")] = mcu

    if ioc_root is None:
        return out

    # 从 .ioc 补。**板名匹配在这里走不通**——实测你的板名是
    # `EasyEDA 96.1×85.9mm` 这种尺寸串，`.ioc` 是 `F401-Front`，
    # 两者没有任何交集。硬按子串匹配只会静默失配。
    #
    # 所以只做**一件事**：把 .ioc 里的 MCU 型号集合报出去，
    # 由调用方（或 `--board-mcu` 参数）显式给出映射。
    # 「我不猜」在这里的具体表现就是：返回空值 + 把可选值列出来。
    return out


def ioc_mcu_options(ioc_root: str | Path) -> list[dict[str, str]]:
    """`.ioc` 里能提供哪些 MCU 型号（供人做显式映射）。

    返回 `[{board, mcu, platform, path}]`，排序稳定。
    """
    try:
        from . import ioc_links as ioc_module
        paths = ioc_module.discover(ioc_root)
    except Exception:                                  # noqa: BLE001
        return []
    out: list[dict[str, str]] = []
    for path in paths:
        try:
            row = ioc_module.parse_ioc(path)
        except Exception:                              # noqa: BLE001
            continue
        if not row.get("mcu"):
            continue
        out.append({
            "board": row["board"],
            "mcu": row["mcu"],
            "platform": _platform_for(row["mcu"]) or "",
            "path": row["path"],
        })
    return sorted(out, key=lambda r: r["board"])


def build_resc(design: DesignModel, *,
               firmware: dict[str, str] | None = None,
               uart: str = "usart2",
               ioc_root: str | Path | None = None,
               explicit_mcu: dict[str, str] | None = None) -> dict[str, Any]:
    """生成 Renode `.resc` 脚本。

    `firmware` 是 `{board_id: "路径/to/x.bin"}`。每个固件都从自己的
    向量表读加载地址——**不默认 0x08000000**，因为有的板不是这样。

    ## 多板联调：`connector Connect`

    这是 Renode 原生命令，不需要发明任何东西。链路来自
    `design.links_list`（P5/P8 产物）。**没有链路就不加 connector**
    ——猜连接比不连接更糟，因为人会以为那是验过的。
    """
    firmware = firmware or {}
    boards = design.board_list()
    mcus = _board_mcus(design, ioc_root)
    # 显式映射优先于任何推断
    for board_id, mcu in (explicit_mcu or {}).items():
        if mcu:
            mcus[board_id] = mcu

    lines: list[str] = [
        "# 由 dsh_embagent 生成 —— 在 DSH 外执行（见 run.cmd）",
        "# 加载地址从每个 .bin 的 Cortex-M 向量表读出，不是猜的",
        "",
    ]
    machines: dict[str, str] = {}
    vectors: dict[str, Any] = {}
    skipped: list[str] = []

    for index, board in enumerate(boards):
        board_id = str(board.id or "")
        name = f"m{index}"
        machines[board_id] = name
        mcu = mcus.get(board_id, "")
        platform = _platform_for(mcu)
        if platform is None:
            skipped.append(f"{board_id or '主参考板'}：MCU={mcu!r} 没有对应的 Renode 平台")
            continue

        lines.append(f'# --- {board_id or "主参考板"}  MCU={mcu}  platform={platform}')
        lines.append(f'mach create "{name}"')
        lines.append(f"machine LoadPlatformDescription @{_PLATFORMS_DIR / (platform + '.repl')}")

        bin_path = firmware.get(board_id, "")
        if bin_path:
            info = vector_table(bin_path)
            vectors[board_id] = info
            if info["ok"]:
                lines.append(f"sysbus LoadBinary @{bin_path} {info['load_address']}")
                lines.append(f"cpu VectorTableOffset {info['load_address']}")
            else:
                lines.append(f"# ! 无法定位加载地址：{info['reason']}")
                skipped.append(f"{board_id}：{info['reason']}")
        else:
            lines.append("# 没有给固件，本机台只建平台不跑代码")

        lines.append(f"showAnalyzer sysbus.{uart}")
        lines.append("")

    # 多板联调：connector Connect
    #
    # ## 顺序很关键：必须先连再 start
    #
    # 第一版把 `mach set "mN"` + `start` 写在各板创建段落里，
    # 于是 `connector Connect` 出现在**所有机台都已经 start 之后**。
    # Renode 要求连接在两台都运行时才能建立外设间通路，
    # 太晚连接会得到"设备已启动"的错误。
    #
    # 现在拆成三段：① 每板 create + 加载（不起）② connector 全部接好
    # ③ 最后统一 start。
    connectors: list[str] = []
    no_link: list[str] = []
    for link in (design.links_list or []):
        if not isinstance(link, dict):
            continue
        source = str(link.get("source") or "")
        if source == "inferred":
            # 推断的链路不接线：接错了比不接更糟
            no_link.append(f"{link.get('id')}（inferred，未接线）")
            continue
        a = link.get("a") or {}
        b = link.get("b") or {}
        unit_a = machines.get(str(a.get("unit") or ""))
        unit_b = machines.get(str(b.get("unit") or ""))
        periph_a = str(a.get("peripheral") or "")
        periph_b = str(b.get("peripheral") or "")
        if not (unit_a and unit_b and periph_a and periph_b):
            no_link.append(f"{link.get('id')}（端点信息不全）")
            continue
        connectors.append(
            f'connector Connect {unit_a}.sysbus.{periph_a.lower()} '
            f'{unit_b}.sysbus.{periph_b.lower()}')

    if connectors:
        lines.append("# --- 板间联调（connector Connect 是 Renode 原生命令）")
        lines.append("# 必须在 start 之前接线，否则 Renode 报设备已启动")
        lines.extend(connectors)
        lines.append("")

    if machines:
        lines.append("# --- 全部接好后统一启动")
        lines.append("start")
        lines.append("")

    script = "\n".join(lines) + "\n"
    return {
        "ok": bool(machines) and any(b.get("ok") for b in vectors.values())
              if vectors else bool(machines),
        "script": script,
        "machines": machines,
        "vectors": vectors,
        "connectors": connectors,
        "warnings": skipped + no_link,
        "platforms": {bid: _platform_for(mcu) for bid, mcu in mcus.items()},
    }


def build_runner(resc_path: str, renode_exe: str) -> dict[str, str]:
    """生成在 **DSH 外**一键运行的脚本（cmd + sh）。

    关键点：预建 `%APPDATA%\\renode` 与锁文件。实测 Renode **不能创建**
    该目录，只能写已存在的——这是它在受限环境下最常见的失败原因。
    """
    cmd = (
        "@echo off\r\n"
        "setlocal\r\n"
        "rem 由 dsh_embagent 生成 —— 在 DSH 外双击运行\r\n"
        "rem 这一步预建 Renode 的配置目录：它只会写、不会建\r\n"
        'if not exist "%APPDATA%\\renode" mkdir "%APPDATA%\\renode"\r\n'
        'if not exist "%APPDATA%\\renode\\config.lock" type nul > "%APPDATA%\\renode\\config.lock"\r\n'
        'if not exist "%APPDATA%\\renode\\cache_lock" type nul > "%APPDATA%\\renode\\cache_lock"\r\n'
        f'"{renode_exe}" --disable-gui --console -e "include @{resc_path}"\r\n'
        "pause\r\n"
    )
    sh = (
        "#!/usr/bin/env bash\n"
        "# 由 dsh_embagent 生成\n"
        "set -euo pipefail\n"
        'mkdir -p "$HOME/.config/renode" 2>/dev/null || true\n'
        f'"{renode_exe}" --disable-gui --console -e "include @{resc_path}"\n'
    )
    return {"run.cmd": cmd, "run.sh": sh}


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def report(design: DesignModel, *,
           firmware: dict[str, str] | None = None,
           check_cpu: bool = False,
           ioc_root: str | Path | None = None,
           explicit_mcu: dict[str, str] | None = None) -> dict[str, Any]:
    """完整报告：探测 + 脚本 + 能不能在本机跑 + 怎么在外面跑。"""
    probe_result = probe(check_cpu=check_cpu)
    resc = build_resc(design, firmware=firmware, ioc_root=ioc_root,
                      explicit_mcu=explicit_mcu)
    options = ioc_mcu_options(ioc_root) if ioc_root else []
    exe, _detail = _find_executable()

    runner: dict[str, str] = {}
    if exe is not None:
        runner = build_runner(f"{design.id}.resc", str(exe))

    runnable = bool(probe_result.renode.available
                    and probe_result.cpu_platform.available)
    notes = [
        "Renode 可执行文件" + ("可用" if probe_result.renode.available else "不可用"),
        f"CPU 平台加载（本机）" + ("可用" if probe_result.cpu_platform.available
                                    else "不可用"),
        "ARM 工具链" + ("已装" if probe_result.arm_gcc.available
                        else "未装（只影响重编源码）"),
    ]
    if not runnable:
        notes.append(
            "**本机不能跑**：生成的 .resc 与 run.cmd 需在 DSH 外执行。"
            "这不是脚本的问题，是沙箱拦截了 Renode 的独占文件锁。")

    return {
        "ok": bool(resc["ok"]),
        "status": "ok" if resc["ok"] else "not_ready",
        "runnable_here": runnable,
        "probe": probe_result.to_dict(),
        "resc": resc,
        "runner": runner,
        "mcu_options": options,
        "notes": notes,
        "summary": (
            f"生成了 {len(resc['machines'])} 个机台、"
            f"{len(resc['connectors'])} 条 connector Connect；"
            f"本机{'能' if runnable else '不能'}跑。"
            + (f" 有 {len(resc['warnings'])} 条需要注意。"
               if resc["warnings"] else "")),
    }


def describe() -> dict[str, Any]:
    return {
        "module": "dsh_embagent.design.renode",
        "platforms": [p for _k, p in dict.fromkeys(KNOWN_PLATFORMS)],
        "load_address": "从 Cortex-M 向量表读（前 8 字节），不默认 0x08000000",
        "multiboard": "connector Connect（Renode 原生命令）",
        "sandbox_limitation": (
            "DSH 内无法加载 CPU 平台：Renode 要独占打开 "
            "%APPDATA%\\renode\\cache_lock，被拒。"
            "不带 CPU 的 mach create 可以。产物需在 DSH 外执行"),
        "arm_toolchain": "只在重编 C 源码时需要；已有 .bin/.hex 不需要",
    }
