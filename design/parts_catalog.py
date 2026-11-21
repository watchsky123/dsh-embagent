"""LCSC / 嘉立创元件目录 —— 把「这个编号是什么」变成能落进设计的事实。

## 这一层解决什么

P17 实测暴露的硬事实：`r3demo` 的 4 个元件全部 `no_symbol` —— 立创只认
**LCSC 编号 / 厂商型号**，而导入的 EasyEDA 工程里 `kind` 是哈希、没有 `lcsc`。
没有这一层，**发射层一个器件都装不上**。

有了它：`C1523` → 型号 / 封装 / 尺寸 / 引脚数 / 库存 / 分级 / 手册页，
`parts-enrich` 回填进 `design.json`，P17 才有覆盖率。

## 端点（2026-11-21 实测可访问，全部免 key）

| 用途 | 端点 | 实测 |
|---|---|---|
| JLC 详情 | `cart.jlcpcb.com/.../getComponentDetail?componentCode=C89852` | 6853 字节，99 个字段 |
| JLC 搜索 | `jlcpcb.com/api/.../selectSmtComponentList`（POST） | `data.componentPageInfo.list[]`，total=18 |
| LCSC 详情 | `wmsc.lcsc.com/ftps/wm/product/detail?productCode=C89852` | 38130 字节，`result.productModel` |

**解析纪律**（Traceforge 的原话，照做）："none of them is a contractual API,
so every parser here is defensive about missing fields" —— 每个字段都 `.get()`
加默认值，绝不 `[]` 硬取。这些是**网页后端接口**，随时可能改形状。

## 为什么不用 plan 里说的 httpx

仓库现有约定是 stdlib `urllib`（`velxio_client.py` / `serve.py` 都是）。
为一个 GET + 一个 POST 引入新依赖不值得；`urllib.request` 够用，
且不增加插件安装时的解析成本。

## 信任级（本项目最硬的纪律之一）

```
estimated(0) < catalog(1) < datasheet(2) < measured(3)
```

LCSC 回填的数据是 **`catalog` 级**：它是**别人目录里的值**，不是手册页，
也不是我们量过的。所以：

* 可以写进 `design.json`（`physical_source="catalog"`），让 2D/3D 先有东西看
* **不进 `capability.candidates()` 的硬选型**（`TRUSTED_SOURCES` 只含
  `datasheet` / `measured`），只在 `explain_miss().rejected` 里出现
* 出口：`parts-promote <mpn> --datasheet <url>` —— 人核对过手册页才升级

## 缓存与离线

`parts_cache.json`（工程级，TTL 7 天）。`HWV_PARTS_OFFLINE=1` 时**不发网络请求**：
命中缓存 → `ok`（标 `stale`），未命中 → `not_ready`。
**任何网络失败都不抛异常**——判定器不能因为目录挂了而挂。
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from . import package_sizes

__all__ = [
    "DETAIL_TTL_DAYS",
    "OFFLINE_ENV",
    "PartEnriched",
    "PartCache",
    "describe",
    "enrich",
    "normalize_lcsc",
    "parse_detail",
    "parse_search_item",
    "search",
]

#: JLC 详情（有库存/价格/分级，首选）。
JLC_DETAIL_URL = ("https://cart.jlcpcb.com/shoppingCart/smtGood/"
                  "getComponentDetail?componentCode={lcsc}")
#: JLC 搜索（POST，关键词）。
JLC_SEARCH_URL = ("https://jlcpcb.com/api/overseas-pcb-order/v1/shoppingCart/"
                  "smtGood/selectSmtComponentList")
#: LCSC 详情（JLC 不供货/停产的型号，用它兜底）。
LCSC_DETAIL_URL = "https://wmsc.lcsc.com/ftps/wm/product/detail?productCode={lcsc}"

#: 详情缓存 7 天（库存/价格会动，元件规格基本不动）。
DETAIL_TTL_DAYS = 7
#: 搜索缓存 1 天（结果排序会变）。
SEARCH_TTL_DAYS = 1
#: 单次网络超时。
TIMEOUT_S = 20
#: 离线开关。**只影响发请求，不影响读缓存**。
OFFLINE_ENV = "HWV_PARTS_OFFLINE"

#: JLC 分级的三种叫法 → 我们的话。
_LIBRARY_TYPES = {
    "base": "basic",
    "basic": "basic",
    "preferred": "preferred",
    "expand": "extended",
    "extended": "extended",
    "economic": "extended",
    "standard": "extended",
}


# ---------------------------------------------------------------------------
# 数据
# ---------------------------------------------------------------------------


@dataclass
class PartEnriched:
    """一个元件的目录事实。**每项都带来源**，缺就是缺（不是 0）。"""

    lcsc: str = ""
    mpn: str = ""
    manufacturer: str = ""
    package: str = ""
    pin_count: int | None = None
    size_mm: dict[str, float] = field(default_factory=dict)
    size_source: str = "unknown"
    pin_pitch: float = 0.0
    datasheet_url: str = ""
    library_type: str = ""
    stock: int | None = None
    lifecycle: str = "unknown"
    price_breaks: list[tuple[int, float]] = field(default_factory=list)
    attributes: dict[str, str] = field(default_factory=dict)
    description: str = ""
    #: `lcsc:C89852:2026-11-21` —— 值从哪来、哪天拿的。
    source: str = ""
    fetched_at: str = ""
    from_cache: bool = False
    stale: bool = False
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "lcsc": self.lcsc,
            "mpn": self.mpn,
            "package": self.package,
            "size_source": self.size_source,
            "source": self.source,
            "fetched_at": self.fetched_at,
        }
        if self.manufacturer:
            out["manufacturer"] = self.manufacturer
        if self.pin_count is not None:
            out["pin_count"] = self.pin_count
        if self.size_mm:
            out["size_mm"] = dict(self.size_mm)
        if self.pin_pitch:
            out["pin_pitch"] = self.pin_pitch
        if self.datasheet_url:
            out["datasheet_url"] = self.datasheet_url
        if self.library_type:
            out["library_type"] = self.library_type
        if self.stock is not None:
            out["stock"] = self.stock
        if self.lifecycle != "unknown":
            out["lifecycle"] = self.lifecycle
        if self.price_breaks:
            out["price_breaks"] = [list(x) for x in self.price_breaks[:10]]
        if self.attributes:
            out["attributes"] = dict(list(self.attributes.items())[:20])
        if self.description:
            out["description"] = self.description[:200]
        if self.from_cache:
            out["from_cache"] = True
        if self.stale:
            out["stale"] = True
        if self.notes:
            out["notes"] = list(self.notes)
        return out

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "PartEnriched":
        """宽松还原（缓存里的旧结构也要能读）。"""
        breaks = []
        for pair in (data.get("price_breaks") or []):
            if isinstance(pair, (list, tuple)) and len(pair) >= 2:
                try:
                    breaks.append((int(pair[0]), float(pair[1])))
                except (TypeError, ValueError):
                    continue
        size = data.get("size_mm")
        return PartEnriched(
            lcsc=str(data.get("lcsc") or ""),
            mpn=str(data.get("mpn") or ""),
            manufacturer=str(data.get("manufacturer") or ""),
            package=str(data.get("package") or ""),
            pin_count=(int(data["pin_count"]) if isinstance(data.get("pin_count"),
                                                             (int, float)) else None),
            size_mm=({k: float(v) for k, v in size.items()}
                     if isinstance(size, dict) else {}),
            size_source=str(data.get("size_source") or "unknown"),
            pin_pitch=float(data.get("pin_pitch") or 0.0),
            datasheet_url=str(data.get("datasheet_url") or ""),
            library_type=str(data.get("library_type") or ""),
            stock=(int(data["stock"]) if isinstance(data.get("stock"),
                                                    (int, float)) else None),
            lifecycle=str(data.get("lifecycle") or "unknown"),
            price_breaks=breaks,
            attributes={str(k): str(v) for k, v in
                        (data.get("attributes") or {}).items()},
            description=str(data.get("description") or ""),
            source=str(data.get("source") or ""),
            fetched_at=str(data.get("fetched_at") or ""),
            from_cache=bool(data.get("from_cache")),
            stale=bool(data.get("stale")),
            notes=[str(x) for x in (data.get("notes") or [])],
        )


# ---------------------------------------------------------------------------
# 纯函数：解析
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def normalize_lcsc(raw: str) -> str:
    """`c1523` / ` C1523 ` / `供应商编号 C1523` → `C1523`。认不出返回空。"""
    text = str(raw or "").strip().upper()
    match = re.search(r"\bC\d{3,}\b", text)
    return match.group(0) if match else ""


def _map_library_type(raw: Any) -> str:
    return _LIBRARY_TYPES.get(str(raw or "").strip().lower(), "")


def _lifecycle_of(data: dict[str, Any]) -> str:
    """停产/停供不能只看 `componentStatus`（它对两者都可能是 `yes`）。

    实测：ATtiny85（C89852）`componentStatus="yes"`，但
    `noBuyReason="This product is no longer manufactured."`、库存 0。
    只看 status 会把停产件当正常件。

    反过来也要防：**什么都没给** 时返回 ``unknown``，不是 ``normal``。
    空对象算出「生命周期正常」就是 AGENTS §0.1 那个形状（缺失被写成通过）。
    """
    reason = str(data.get("noBuyReason") or "").lower()
    for word in ("no longer manufactured", "obsolete", "discontinued",
                 "end of life", "eol", "not recommended"):
        if word in reason:
            return "discontinued"
    if data.get("catalogBlackFlag") is True or data.get("manufacturerBlackFlag") is True:
        return "blocked"
    status = str(data.get("componentStatus") or "").strip().lower()
    if not status:
        # 没有 status 也没有 noBuyReason → 没有任何证据说它正常
        return "unknown"
    if status not in ("yes", "1", "true"):
        return "unconfirmed"
    return "normal"


def parse_detail(data: dict[str, Any], *, lcsc: str = "") -> PartEnriched:
    """JLC 的 `data` 对象 → `PartEnriched`。**纯函数**，可离线单测。

    每个字段都 `.get()`；缺失就是缺（`pin_count=None` / `stock=None`），
    不写 0 —— 0 库存和"不知道库存"是两件事。
    """
    if not isinstance(data, dict):
        return PartEnriched(lcsc=lcsc, notes=["详情不是对象（接口形状变了？）"])

    code = normalize_lcsc(str(data.get("componentCode") or "")) or lcsc
    package = str(data.get("componentSpecificationEn")
                  or data.get("componentSpecification") or "").strip()
    size = package_sizes.lookup(package)

    stock = data.get("stockCount")
    try:
        stock_value: int | None = int(stock) if stock is not None else None
    except (TypeError, ValueError):
        stock_value = None

    breaks: list[tuple[int, float]] = []
    for row in (data.get("prices") or []):
        if not isinstance(row, dict):
            continue
        try:
            breaks.append((int(row.get("startNumber")),
                           float(row.get("productPrice"))))
        except (TypeError, ValueError):
            continue

    attrs: dict[str, str] = {}
    for row in (data.get("attributes") or []):
        if not isinstance(row, dict):
            continue
        key = str(row.get("attribute_name_en") or "").strip()
        value = str(row.get("attribute_value_name") or "").strip()
        if key and value and value != "-":
            attrs[key] = value

    notes: list[str] = []
    if not package:
        notes.append("没有封装字段")
    elif not size.ok:
        notes.append(f"封装 {package!r} 不在尺寸表里 → size_mm 留空")
    if size.source == "formula":
        notes.append("尺寸是按封装族公式估的")

    part = PartEnriched(
        lcsc=code,
        mpn=str(data.get("componentModelEn") or "").strip(),
        manufacturer=str(data.get("componentBrandEn") or "").strip(),
        package=package,
        pin_count=size.pins if size.ok and size.pins else None,
        size_mm=size.size_mm if size.ok else {},
        size_source=size.source,
        pin_pitch=size.pitch if size.ok else 0.0,
        datasheet_url=str(data.get("dataManualUrl")
                          or data.get("dataManualOfficialLink") or "").strip(),
        library_type=_map_library_type(data.get("componentLibraryType")),
        stock=stock_value,
        lifecycle=_lifecycle_of(data),
        price_breaks=breaks,
        attributes=attrs,
        description=str(data.get("describe") or "")[:300],
        fetched_at=_now_iso(),
        notes=notes,
    )
    part.source = f"lcsc:{part.lcsc}:{_today()}"
    return part


def parse_search_item(item: dict[str, Any]) -> dict[str, Any]:
    """搜索结果的一条 → 精简字典。搜索页不保证有尺寸，所以**不查封装表**。"""
    if not isinstance(item, dict):
        return {}
    stock = item.get("stockCount")
    try:
        stock_value = int(stock) if stock is not None else None
    except (TypeError, ValueError):
        stock_value = None
    return {
        "lcsc": normalize_lcsc(str(item.get("componentCode") or "")),
        "mpn": str(item.get("componentModelEn") or "").strip(),
        "manufacturer": str(item.get("componentBrandEn") or "").strip(),
        "package": str(item.get("componentSpecificationEn") or "").strip(),
        "library_type": _map_library_type(item.get("componentLibraryType")),
        "stock": stock_value,
        "description": str(item.get("describe") or "")[:160],
        "datasheet_url": str(item.get("dataManualUrl") or "").strip(),
    }


# ---------------------------------------------------------------------------
# 缓存
# ---------------------------------------------------------------------------


class PartCache:
    """工程级缓存。结构：``{"version":1,"entries":{key:{fetched_at,data}}}``。

    **过期不等于删除**：过期的条目继续可用（标 `stale`），因为元件规格
    基本不变，而"完全没有数据"比"7 天前的数据"糟得多。
    """

    def __init__(self, path: Any = None, *, ttl_days: float = DETAIL_TTL_DAYS):
        self.path = path
        self.ttl_days = float(ttl_days)
        self.entries: dict[str, dict[str, Any]] = {}
        self.loaded = False

    # -- 存取 -----------------------------------------------------------

    def load(self) -> "PartCache":
        if self.path is None:
            self.loaded = True
            return self
        try:
            from pathlib import Path
            target = Path(self.path)
            if target.is_file():
                raw = json.loads(target.read_text(encoding="utf-8"))
                entries = raw.get("entries") if isinstance(raw, dict) else None
                if isinstance(entries, dict):
                    self.entries = {str(k): v for k, v in entries.items()
                                    if isinstance(v, dict)}
        except (OSError, ValueError):
            # 缓存坏了就当没有；不抛异常（它是加速层，不是事实源）
            self.entries = {}
        self.loaded = True
        return self

    def save(self) -> bool:
        if self.path is None:
            return False
        try:
            from pathlib import Path
            target = Path(self.path)
            target.parent.mkdir(parents=True, exist_ok=True)
            payload = {"version": 1, "updated_at": _now_iso(),
                       "entries": self.entries}
            target.write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                              encoding="utf-8")
            return True
        except OSError:
            return False

    # -- 查询 -----------------------------------------------------------

    def age_days(self, key: str) -> float | None:
        entry = self.entries.get(key)
        if not isinstance(entry, dict):
            return None
        stamp = str(entry.get("fetched_at") or "")
        if not stamp:
            return None
        try:
            when = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=timezone.utc)
        except ValueError:
            return None
        return (datetime.now(timezone.utc) - when).total_seconds() / 86400.0

    def get(self, key: str) -> dict[str, Any] | None:
        entry = self.entries.get(key)
        if not isinstance(entry, dict):
            return None
        data = entry.get("data")
        return data if isinstance(data, dict) else None

    def is_fresh(self, key: str) -> bool:
        age = self.age_days(key)
        return age is not None and age <= self.ttl_days

    def put(self, key: str, data: dict[str, Any]) -> None:
        self.entries[key] = {"fetched_at": _now_iso(), "data": data}

    def __len__(self) -> int:
        return len(self.entries)


# ---------------------------------------------------------------------------
# 网络
# ---------------------------------------------------------------------------


class _Net:
    """一次性的网络开关。所有失败都翻译成 ``(None, reason)``，**不抛**。"""

    def __init__(self, timeout_s: int = TIMEOUT_S, *, offline: bool = False):
        self.timeout_s = timeout_s
        self.offline = offline

    @staticmethod
    def _headers() -> dict[str, str]:
        return {"User-Agent": "hw_velxio/parts-catalog (+https://example.invalid)",
                "Accept": "application/json"}

    def get_json(self, url: str) -> tuple[dict[str, Any] | None, str]:
        if self.offline:
            return None, "offline"
        try:
            request = urllib.request.Request(url, headers=self._headers())
            with urllib.request.urlopen(request, timeout=self.timeout_s) as resp:
                raw = resp.read(4_000_000)
            return json.loads(raw.decode("utf-8", errors="replace")), ""
        except urllib.error.HTTPError as exc:
            return None, f"http {exc.code}"
        except urllib.error.URLError as exc:
            return None, f"网络不可达：{exc.reason}"
        except (TimeoutError, OSError) as exc:
            return None, f"网络错误：{exc}"
        except ValueError as exc:
            return None, f"返回不是 JSON：{exc}"

    def post_json(self, url: str, body: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        if self.offline:
            return None, "offline"
        try:
            payload = json.dumps(body).encode("utf-8")
            headers = {**self._headers(), "Content-Type": "application/json"}
            request = urllib.request.Request(url, data=payload, headers=headers)
            with urllib.request.urlopen(request, timeout=self.timeout_s) as resp:
                raw = resp.read(4_000_000)
            return json.loads(raw.decode("utf-8", errors="replace")), ""
        except urllib.error.HTTPError as exc:
            return None, f"http {exc.code}"
        except urllib.error.URLError as exc:
            return None, f"网络不可达：{exc.reason}"
        except (TimeoutError, OSError) as exc:
            return None, f"网络错误：{exc}"
        except ValueError as exc:
            return None, f"返回不是 JSON：{exc}"


def offline() -> bool:
    return str(os.environ.get(OFFLINE_ENV, "")).strip().lower() in ("1", "true", "yes", "on")


# ---------------------------------------------------------------------------
# 对外：搜索 / 详情
# ---------------------------------------------------------------------------


def search(keyword: str, *, limit: int = 10, page: int = 1,
           net: _Net | None = None) -> dict[str, Any]:
    """关键词搜索。返回 ``{status, items, total, reason}``。

    ``status`` 只有 ``ok`` / ``not_ready``：搜不到也是 ``ok``（`items=[]`），
    只有**拿不到结果**（网络/接口形状变了）才是 ``not_ready``。
    """
    text = str(keyword or "").strip()
    if not text:
        return {"status": "not_ready", "reason": "关键词为空", "items": [], "total": 0}
    limit = max(1, min(int(limit), 50))
    client = net or _Net(offline=offline())
    payload, reason = client.post_json(JLC_SEARCH_URL, {
        "keyword": text, "currentPage": max(1, int(page)), "pageSize": limit,
    })
    if payload is None:
        return {"status": "not_ready", "reason": reason or "没有返回",
                "items": [], "total": 0, "query": text}
    data = payload.get("data")
    if not isinstance(data, dict):
        return {"status": "not_ready", "reason": f"data 不是对象（{str(payload)[:80]}）",
                "items": [], "total": 0, "query": text}
    page_info = data.get("componentPageInfo")
    if not isinstance(page_info, dict):
        # 接口换过形状 → 如实报，不假装搜到了 0 条
        return {"status": "not_ready",
                "reason": "返回里没有 componentPageInfo（接口形状可能变了）",
                "keys": sorted(data.keys())[:10], "items": [], "total": 0,
                "query": text}
    rows = page_info.get("list")
    items = [x for x in (parse_search_item(r) for r in (rows or [])) if x.get("lcsc")]
    total = page_info.get("total")
    return {
        "status": "ok",
        "query": text,
        "items": items[:limit],
        "total": int(total) if isinstance(total, (int, float)) else len(items),
        "page": page,
        "source": f"jlc:search:{_today()}",
    }


def enrich(lcsc: str, *, cache: PartCache | None = None, allow_network: bool = True,
           net: _Net | None = None) -> dict[str, Any]:
    """查一个编号的详情。**缓存优先**，失败降级，绝不抛异常。

    优先级：新鲜缓存 → 网络 → 陈旧缓存 → not_ready。

    为什么「陈旧缓存」排在「失败」前面：7 天前的封装尺寸几乎一定还是对的，
    而 `not_ready` 会让整条发射链路停摆。**过期不删**是这个模块的核心取舍。
    """
    code = normalize_lcsc(lcsc)
    if not code:
        return {"status": "not_ready", "reason": f"认不出 LCSC 编号：{lcsc!r}",
                "part": None}

    store = cache if cache is not None else PartCache()
    if not store.loaded:
        store.load()

    cached = store.get(code)
    if cached is not None:
        part = PartEnriched.from_dict(cached)
        part.from_cache = True
        if store.is_fresh(code):
            return {"status": "ok", "part": part.to_dict(), "cache": "fresh",
                    "age_days": round(store.age_days(code) or 0.0, 2)}

    is_offline = offline() or not allow_network
    if is_offline and cached is None:
        return {"status": "not_ready",
                "reason": f"离线（{OFFLINE_ENV}）且缓存里没有 {code}",
                "part": None, "cache": "miss"}

    client = net or _Net(offline=is_offline)
    payload, reason = client.get_json(JLC_DETAIL_URL.format(lcsc=code))
    data = (payload or {}).get("data") if isinstance(payload, dict) else None
    if isinstance(data, dict):
        part = parse_detail(data, lcsc=code)
        if part.mpn or part.package or part.stock is not None:
            store.put(code, part.to_dict())
            store.save()
            return {"status": "ok", "part": part.to_dict(), "cache": "network",
                    "reason": ""}
        reason = "详情里没有型号/封装（形状可能变了）"

    # 降级：JLC 没有 → 试 LCSC 详情
    payload2, reason2 = client.get_json(LCSC_DETAIL_URL.format(lcsc=code))
    result = (payload2 or {}).get("result") if isinstance(payload2, dict) else None
    if isinstance(result, dict):
        part = parse_detail({
            "componentCode": result.get("productCode"),
            "componentModelEn": result.get("productModel"),
            "componentSpecificationEn": result.get("encapStandard")
            or result.get("productGradePlateName") or "",
            "componentBrandEn": result.get("brandNameEn"),
            "dataManualUrl": result.get("pdfUrl"),
            "describe": result.get("productNameEn"),
        }, lcsc=code)
        if part.mpn:
            part.notes.append("数据来自 LCSC 详情（JLC 没有这个编号）")
            store.put(code, part.to_dict())
            store.save()
            return {"status": "ok", "part": part.to_dict(), "cache": "lcsc",
                    "reason": reason}

    if cached is not None:
        part = PartEnriched.from_dict(cached)
        part.from_cache = True
        part.stale = True
        part.notes.append(f"网络失败（{reason or reason2}），用陈旧缓存")
        return {"status": "ok", "part": part.to_dict(), "cache": "stale",
                "age_days": round(store.age_days(code) or 0.0, 2),
                "reason": reason or reason2}

    return {"status": "not_ready",
            "reason": reason or reason2 or "查不到这个编号",
            "part": None, "cache": "miss"}


def to_design_fields(part: dict[str, Any]) -> dict[str, Any]:
    """`PartEnriched.to_dict()` → 能写进 `design.json` 的字段。

    **只写目录真有的东西**：`size_mm` 来自封装表就标 `catalog`
    （公式估的标 `estimated`），`pins` 只有拿到引脚数才写。
    """
    size_source = str(part.get("size_source") or "unknown")
    size = part.get("size_mm") or {}
    out: dict[str, Any] = {}
    if part.get("lcsc"):
        out["lcsc"] = part["lcsc"]
    if part.get("mpn"):
        out["mpn"] = part["mpn"]
    if isinstance(size, dict) and size.get("w"):
        out["size_mm"] = {
            "w": float(size.get("w") or 0.0),
            "h": float(size.get("h") or 0.0),
            "d": float(size.get("d") or 0.0),
        }
        out["physical_source"] = "catalog" if size_source == "jedec" else "estimated"
    pin_count = part.get("pin_count")
    if isinstance(pin_count, int) and pin_count > 0:
        out["pins"] = [str(i + 1) for i in range(pin_count)]
    return out


def describe() -> dict[str, Any]:
    """自省：端点、缓存、信任级。"""
    return {
        "module": "hw_velxio.design.parts_catalog",
        "endpoints": {
            "detail": "cart.jlcpcb.com/…/getComponentDetail",
            "search": "jlcpcb.com/api/…/selectSmtComponentList",
            "fallback": "wmsc.lcsc.com/ftps/wm/product/detail",
        },
        "cache": {"file": "parts_cache.json", "ttl_days": DETAIL_TTL_DAYS,
                  "search_ttl_days": SEARCH_TTL_DAYS,
                  "offline_env": OFFLINE_ENV,
                  "policy": "过期不删，标 stale 继续用"},
        "trust": {
            "tier": "catalog",
            "why": "目录数据不是手册页，也不是量过的 → 不进 capability 硬选型",
            "exit": "parts-promote <mpn> --datasheet <url>",
        },
        "http": "stdlib urllib（不引入 httpx；仓库现有约定如此）",
        "honest": ("接口不是契约 API，解析全部 .get()；"
                   "形状变了报 not_ready，不假装搜到 0 条"),
    }