"""引脚定义表（`pinout`）—— **封装脚号 ↔ 端口名**。

## 这一层补什么

`.ioc` 用端口名（`PA9`），`design.json` 的网表用封装脚号（`30`）。两者之间
差一张表，于是 P55 只能做到"按名字对"，做不到**逐脚对**：

| 没有这张表 | 有这张表 |
|---|---|
| `.ioc` 配了 USART1（PA9/PA10）| `.ioc` 的 PA9 **就是**网表的 30 |
| 网表的 30 接的是 UART_TX | 能判"`.ioc` 说 uart，网表说 spi" = 冲突 |
| "脚号对照"永远 `unknown` | 冲突变**硬判据**（`ioc.pin_conflict`）|

## 表从哪来（**只收有出处的**）

来源是 **LCSC/EasyEDA 的厂商符号**（`easyeda.com/api/products/<LCSC>/components`）：
符号里的 `P~` 图元带 `pinNumber` 与 `pinName`，编号就是封装脚号
（实测 F103C8T6 符号 48 个脚、1=VBAT、2=PC13…与 datasheet 表 5 同构）。

* `source = "catalog"`（厂商符号；不是 datasheet 原文）
* 每条表都记 `source_url` / `retrieved` / `dataStr` 的 `sha256` —— 可复现
* **符号不完整就不建表**（实测 STM32F405RGT6 的符号只有 55/64 个脚 →
  拒绝建表，而不是补 9 个"猜的"脚）

## 为什么敢用它做硬判据

1. 表本身要过 `validate()`：脚号连续、数量等于封装脚数、端口名形状合法
2. 与 `fwgen.mcus` 的 `power_pins` 交叉核对（两份独立资料说同样的电源脚名）
3. **端到端**：真实项目 `复刻-示例`（EasyEDA 网表）+ `F103-GPS.ioc`
   （CubeMX）用这张表对完 **0 冲突**，而且 `PA9↔30`、`PA12↔33` 这些
   独立来源的对应关系全部吻合（`check_p56` 守着）

## 边界

* 只管**封装脚号 ↔ 端口名**；不管复用功能编号（AF7/AF8 仍要 datasheet）
* 表里的 `kind`（power/ground/reset/boot/gpio/other）来自**引脚名**的形态，
  不是电气特性
* 换了封装（LQFP48 vs LQFP64）就是另一张表 —— 不跨封装复用
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from pathlib import Path
from typing import Any

__all__ = ["DIR", "load", "find", "build_table", "validate", "describe", "list_tables"]

#: 表目录：一个型号一个文件（`stm32f103c8t6.json`），**不合并** ——
#: 合并成一张大表后，改一个型号会动到别人；分开也能只提交验过的那些。
DIR = Path(__file__).with_name("pinout")

#: `P~show~0~30~315~185~180~gge41~0^^…`
_PIN_SHAPE = "P~"

#: 端口名：`PC13-TAMPER-RTC` → `PC13`；`PA0-WKUP(PA0)` → `PA0`
_PORT_RE = re.compile(r"^(P[A-Z]\d{1,2})(?!\d)")

#: 电源/地/复位/启动脚的名字形态（**只看名字，不当电气事实**）
#: H7 的 `PDR` 就是 F4 的 `PDR_ON`；`VDDAVREF+` 是 VDDA 与 VREF+ 合成一个脚。
_POWER_RE = re.compile(r"^(VDD|VSS|VBAT|VDDA|VSSA|VREF|VCAP|PDR)")
_RESET_RE = re.compile(r"^NRST")
_BOOT_RE = re.compile(r"^BOOT\d?")

#: 封装名 → 脚数（只收本项目用到的；表外封装不建表）
PACKAGE_PINS = {"lqfp48": 48, "ufqfp48": 48, "lqfp64": 64, "lqfp100": 100,
                "lqfp144": 144, "lqfp176": 176}


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text or "").lower())


def _package_pins(package: str) -> int | None:
    """`LQFP-48_L7.0-W7.0-P0.50...` → 48；`UFQFPN48` → 48。**认不出就 None。**"""
    low = str(package or "").lower()
    for key, count in PACKAGE_PINS.items():
        if key in low:
            return count
    match = re.search(r"(\d{2,3})", low)
    return int(match.group(1)) if match else None


def classify(name: str) -> tuple[str, str]:
    """引脚名 → `(kind, port)`。**kind 来自名字形态，不是电气事实。**

    | 例子 | kind |
    |---|---|
    | `PA9` / `PC13-TAMPER-RTC` | `gpio`（port 取 `P[A-Z]\\d{1,2}`）|
    | `VDD` / `VDD_3` / `VDDA` | `power` |
    | `VSS` / `VSS_2` | `ground` |
    | `NRST` | `reset` |
    | `BOOT0` | `boot` |
    | 认不出（`VCAP1` / `PH0-OSC_IN` 之外的怪名）| `other` |
    """
    raw = str(name or "").strip()
    if not raw:
        return "other", ""
    match = _PORT_RE.match(raw)
    if match:
        return "gpio", match.group(1)
    upper = raw.upper()
    if _POWER_RE.match(upper):
        return ("ground" if upper.startswith("VSS") else "power"), ""
    if _RESET_RE.match(upper):
        return "reset", ""
    if _BOOT_RE.match(upper):
        return "boot", ""
    return "other", ""


def _pins_from_symbol(doc: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """EasyEDA 符号 JSON → `{脚号: {name, port, kind}}`。"""

    def _shape_pin(shape: str) -> tuple[str, str] | None:
        seg = shape.split("^^")
        if len(seg) < 4:
            return None
        head = seg[0].split("~")
        if len(head) < 4:
            return None
        number = head[3].strip()
        # seg[3] 的字段是 `1~x~y~rot~<pinName>~anchor~…`，第 4 项是名字
        text = seg[3].split("~")
        if len(text) < 5:
            return None
        return number, text[4].strip()

    data = doc.get("dataStr") or {}
    shapes = [s for s in (data.get("shape") or []) if str(s).startswith(_PIN_SHAPE)]
    pins: dict[str, dict[str, Any]] = {}
    for shape in shapes:
        got = _shape_pin(str(shape))
        if got is None:
            continue
        number, name = got
        if not number.isdigit():
            continue
        kind, port = classify(name)
        pins[number] = {"name": name, "port": port, "kind": kind}
    return pins


def validate(table: dict[str, Any]) -> list[str]:
    """表自检 → 问题列表（**空列表才是能用的表**）。

    判据：脚号 1..N 连续、数量等于封装脚数、名字非空、端口名形状合法。
    数量与封装不符 = **符号不完整**，这时不建表（实测 STM32F405RGT6 55/64）。
    """
    problems: list[str] = []
    pins = table.get("pins") or {}
    count = int(table.get("count") or 0)
    expected = _package_pins(str(table.get("package") or ""))
    if count <= 0:
        problems.append("count 缺失或为 0")
    if expected is not None and count != expected:
        problems.append(f"脚数 {count} 与封装 {table.get('package')!r} 的 {expected} 不符")
    numbers = sorted(int(k) for k in pins if str(k).isdigit())
    if numbers and numbers != list(range(1, len(numbers) + 1)):
        problems.append(f"脚号不连续：{numbers[:3]}…{numbers[-3:]}（共 {len(numbers)}）")
    if len(numbers) != len(pins):
        problems.append("有非数字脚号")
    for number, row in pins.items():
        name = str((row or {}).get("name") or "")
        port = str((row or {}).get("port") or "")
        if not name:
            problems.append(f"{number} 脚没有名字")
        if port and not _PORT_RE.fullmatch(port):
            problems.append(f"{number} 脚端口名 {port!r} 形状非法")
        if (row or {}).get("kind") == "gpio" and not port:
            problems.append(f"{number} 脚标了 gpio 却没有端口名")
    return problems


def build_table(lcsc: str, *, part: str = "", package_hint: str = "",
                fetcher: Any = None) -> dict[str, Any]:
    """从 LCSC 符号建一张表（**不完整就拒绝**，返回 `status != ok`）。

    返回 `{status, table?, problems?, url, mpn, title}`；只有 `status == "ok"`
    时调用方才该落盘。
    """
    from . import parts_catalog as pc  # noqa: PLC0415  延迟导入（网络/缓存）

    lcsc = str(lcsc or "").strip().upper()
    if not lcsc:
        return {"status": "not_ready", "reason": "缺 LCSC 编号"}
    url = (f"https://easyeda.com/api/products/{lcsc}/components"
           f"?version=6.5.46")
    client = fetcher or pc._Net(offline=pc.offline())
    data, error = client.get_json(url)
    if not data:
        return {"status": "not_ready", "reason": f"取不到符号：{error}",
                "url": url}
    doc = data.get("result") or {}
    if not isinstance(doc, dict) or not doc.get("dataStr"):
        return {"status": "not_ready", "reason": "符号形状不认识（缺 dataStr）",
                "url": url, "keys": sorted(data)[:8]}
    head = (doc.get("dataStr") or {}).get("head") or {}
    para = head.get("c_para") or {}
    mpn = str(para.get("Manufacturer Part") or doc.get("title") or "")
    package = str(para.get("package") or package_hint or "")
    pins = _pins_from_symbol(doc)
    count = len(pins)
    expected = _package_pins(package)
    table = {
        "part": _norm(part or mpn),
        "mpn": mpn,
        "lcsc": lcsc,
        "title": str(doc.get("title") or ""),
        "package": package,
        "count": count,
        "pins": pins,
        "source": "catalog",
        "source_url": url,
        "source_kind": "LCSC/EasyEDA 符号（P~ 图元带 pinNumber/pinName）",
        "retrieved": date.today().isoformat(),
        "sha256": hashlib.sha256(
            json.dumps(doc.get("dataStr"), sort_keys=True,
                       ensure_ascii=False).encode("utf-8")).hexdigest(),
    }
    problems = validate(table)
    if expected is not None and count != expected:
        problems.insert(0, f"符号只有 {count} 个脚（封装 {package} 应为 {expected}）"
                           "—— 不完整的符号不建表，也不补猜的脚")
    if problems:
        return {"status": "failed", "problems": problems, "url": url,
                "mpn": mpn, "count": count, "package": package}
    return {"status": "ok", "table": table, "url": url, "mpn": mpn,
            "count": count, "package": package}


def list_tables() -> list[dict[str, Any]]:
    """已落盘的表（按 part 名排序）。读失败的表也列出来并标 `error`。"""
    out: list[dict[str, Any]] = []
    if not DIR.is_dir():
        return out
    for path in sorted(DIR.glob("*.json")):
        try:
            table = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            out.append({"part": path.stem, "file": path.name,
                        "error": f"{type(exc).__name__}: {exc}"})
            continue
        out.append({"part": table.get("part") or path.stem,
                    "file": path.name, "mpn": table.get("mpn", ""),
                    "lcsc": table.get("lcsc", ""), "count": table.get("count"),
                    "package": table.get("package"),
                    "source": table.get("source", ""),
                    "retrieved": table.get("retrieved", ""),
                    "problems": validate(table)})
    return out


def load(part: str) -> dict[str, Any] | None:
    """按 part 名（`stm32f103c8t6`）或表名读一张表。**没有就 None**（不猜）。"""
    key = _norm(part)
    if not key:
        return None
    path = DIR / f"{key}.json"
    if not path.is_file():
        return None
    try:
        table = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    return table if isinstance(table, dict) else None


def find(kind: str, lcsc: str = "") -> dict[str, Any] | None:
    """设计里的 MCU → 表。**先按 LCSC（最精确），再按型号名**；都没有就 None。"""
    wanted_lcsc = str(lcsc or "").strip().upper()
    if wanted_lcsc:
        for row in list_tables():
            if row.get("error"):
                continue
            if str(row.get("lcsc") or "").upper() == wanted_lcsc:
                return load(str(row.get("part") or ""))
    return load(kind)


def describe() -> dict[str, Any]:
    tables = list_tables()
    return {
        "module": "dsh_embagent.design.pinout",
        "dir": str(DIR),
        "tables": len(tables),
        "parts": [row.get("part") for row in tables],
        "source": "LCSC/EasyEDA 厂商符号（catalog）；datasheet 原文未录入",
        "build": ("pinout build --lcsc=C8734 [--part=stm32f103c8t6]；"
                  "符号脚数不等于封装脚数 → 拒绝建表"),
        "provides": ("封装脚号 ↔ 端口名（PA9↔30）、脚号 → 电源/地/复位/启动"),
        "not_provided": "复用功能编号（AF7/AF8）——仍在 datasheet 的 AF 表里",
        "discipline": ("表必须过 validate（脚号连续/数量等于封装/端口名合法）；"
                       "和 fwgen.mcus 的 power_pins 交叉核对；"
                       "不完整的符号不建表，不补猜的脚"),
    }