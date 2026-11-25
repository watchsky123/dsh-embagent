"""P19 验收：LCSC 元件数据源（``parts_catalog`` + ``package_sizes`` + 能力表覆盖层）。

    python -m dsh_embagent.scripts.check_p19

六条对应 `dsh-embagent-docs:docs/execution-plan-v3.md` §P19，其中第 6 条是**退化验证**。

## 两种夹具分开用（AGENTS §0.3）

* **抓下来的真实返回**（`tests/fixtures/parts/*.json`）：C89852 与 C1523 的
  JLC 详情、一页 STM32F103 搜索结果。它证明"解析器对着真数据是对的"，
  而且**不需要联网**也能反复跑。
* **真机联网**：两条（搜索 ≥5 条、详情尺寸非 0）。没网时记 `not_ready`，
  **不记 pass** —— 没验证过就是没验证过。

## 覆盖层的隔离

`capability` 的两个 JSON 是**人的决定**（promote 过的型号）。验收不能往里面
写测试数据，所以这里把它们指到临时文件，跑完还原。这条如果漏了，
下一次 `check_generate` 会看到表里凭空多出型号。
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dsh_embagent.design import capability, package_sizes, parts_catalog  # noqa: E402

PASS = "pass"
FAIL = "fail"
SKIP = "not_ready"

_RESULTS: list[dict[str, Any]] = []
FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "parts"


def check(name: str, ok: bool, detail: str = "", *, status: str = "") -> bool:
    resolved = status or (PASS if ok else FAIL)
    _RESULTS.append({"check": name, "status": resolved,
                     "ok": resolved == PASS, "detail": str(detail)[:700]})
    flag = {"pass": "OK ", "fail": "FAIL", "not_ready": "SKIP"}[resolved]
    print(f"[{flag}] {name}")
    if detail:
        print(f"       {str(detail)[:700]}")
    return ok


def fixture(name: str) -> dict[str, Any]:
    path = FIXTURES / name
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return {}


# ---------------------------------------------------------------------------
# 1. 封装表
# ---------------------------------------------------------------------------


def check_packages() -> None:
    # 这些串都是从真实 LCSC 返回里见过的形态
    cases = {
        "0402": (1.0, 0.5),            # 长 1.0 / 宽 0.5
        "0402 (1005 Metric)": (1.0, 0.5),
        "SOIC-8-208mil": (5.28, 5.2),
        "LQFP-48(7x7)": (7.0, 7.0),
        "SOT-23-3": (1.4, 2.9),
        "SOD-123": (1.6, 2.7),
        "SMA": (2.6, 4.3),
        "TO-252-2": (6.1, 9.8),
        "TSSOP-20": (4.4, 6.5),
        "DIP-8": (6.4, 9.7),
    }
    bad: list[str] = []
    for package, (width, length) in cases.items():
        got = package_sizes.lookup(package)
        if not got.ok:
            bad.append(f"{package}: 查不到")
            continue
        if abs(got.w - width) > 0.01 or abs(got.h - length) > 0.01:
            bad.append(f"{package}: {got.size_mm} != {{w:{width},h:{length}}}")
    check("1a. 封装表：10 种实测封装串都能查出名义尺寸",
          not bad, "；".join(bad) or "全部命中（source=jedec）")

    unknown = package_sizes.lookup("BGA-64")
    unknown_ok = (unknown.source == "unknown" and not unknown.ok
                  and unknown.size_mm == {"w": 0.0, "h": 0.0, "d": 0.0})
    check("1b. 封装表：表里没有的封装 → unknown 且尺寸全 0（不猜）",
          unknown_ok, f"BGA-64 → {unknown.to_dict()}")

    formula = package_sizes.lookup("SOIC-10")
    formula_ok = formula.source == "formula" and formula.ok
    check("1c. 封装表：族公式推出来的标 formula，不冒充 jedec 名义值",
          formula_ok, f"SOIC-10 → {formula.size_mm} source={formula.source}")


# ---------------------------------------------------------------------------
# 2. 解析（离线，用抓下来的真实返回）
# ---------------------------------------------------------------------------


def check_parsers() -> None:
    payload = fixture("jlc_detail_C89852.json")
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        check("2a. 解析 C89852 详情", False, "夹具缺失", status=SKIP)
    else:
        part = parts_catalog.parse_detail(data)
        expected_size = {"w": 5.28, "h": 5.2, "d": 1.75}
        ok = (part.lcsc == "C89852"
              and part.mpn == "ATTINY85-20SU"
              and part.size_mm == expected_size
              and part.size_source == "jedec"
              and part.stock == 0
              and part.lifecycle == "discontinued"
              and part.pin_count == 8
              and part.library_type == "extended"
              and part.price_breaks)
        check("2a. 解析 C89852 详情：型号/封装/尺寸/库存/停产/价格档",
              ok,
              f"mpn={part.mpn} size={part.size_mm} stock={part.stock} "
              f"lifecycle={part.lifecycle} pins={part.pin_count} "
              f"breaks={part.price_breaks[:2]}")

    # 停产判据只看 componentStatus 会误判（ATtiny85 的 status 是 yes）
    if isinstance(data, dict):
        only_status = str(data.get("componentStatus") or "")
        ok = only_status.lower() == "yes" and parts_catalog.parse_detail(data).lifecycle == "discontinued"
        check("2b. 停产识别：componentStatus=yes 但 noBuyReason 说明停产 → discontinued",
              ok, f"componentStatus={only_status!r} noBuyReason={str(data.get('noBuyReason'))[:60]!r}")

    # 防御解析：空对象 / 缺字段
    bare = parts_catalog.parse_detail({})
    bare_ok = (bare.lcsc == "" and bare.pin_count is None and bare.stock is None
               and bare.size_mm == {} and bare.lifecycle == "unknown")
    check("2c. 防御解析：空对象不崩，缺字段是 None 不是 0",
          bare_ok, f"stock={bare.stock} pin_count={bare.pin_count} size={bare.size_mm}")

    not_obj = parts_catalog.parse_detail("not-a-dict")  # type: ignore[arg-type]
    check("2d. 防御解析：非对象返回 notes 而不是异常",
          not_obj.lcsc == "" and bool(not_obj.notes), not_obj.notes)

    # 搜索条目
    search_payload = fixture("jlc_search_stm32f103.json")
    page = ((search_payload.get("data") or {}).get("componentPageInfo") or {})
    rows = page.get("list") or []
    items = [parts_catalog.parse_search_item(r) for r in rows]
    ok = (len(items) >= 5
          and all(i.get("lcsc") and i.get("package") for i in items)
          and any(i.get("stock") for i in items))
    check("2e. 解析搜索结果：≥5 条且每条带 lcsc/封装/库存",
          ok, f"{[(i.get('lcsc'), i.get('package'), i.get('stock')) for i in items[:3]]}")


# ---------------------------------------------------------------------------
# 3. 接口形状变了 → not_ready，不假装 0 条
# ---------------------------------------------------------------------------


def check_shape_change() -> None:
    class FakeNet(parts_catalog._Net):  # noqa: SLF001 - 故意替换网络层
        def __init__(self, payload: dict[str, Any]):
            super().__init__(offline=False)
            self.payload = payload

        def post_json(self, url: str, body: dict[str, Any]):
            return self.payload, ""

    # 真实形状有 data.componentPageInfo；这里模拟"换了一版"
    flat = {"code": 200, "data": {"list": [{"componentCode": "C1"}]}}
    result = parts_catalog.search("x", net=FakeNet(flat))
    check("3. 接口形状变了 → not_ready（不假装搜到 0 条）",
          result.get("status") == "not_ready"
          and "componentPageInfo" in str(result.get("reason")),
          f"status={result.get('status')} reason={result.get('reason')} "
          f"keys={result.get('keys')}")


# ---------------------------------------------------------------------------
# 4. 缓存与离线
# ---------------------------------------------------------------------------


def check_cache_offline() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="hwv-p19-cache-"))
    try:
        cache_path = tmp / "parts_cache.json"
        cache = parts_catalog.PartCache(cache_path)
        cache.load()
        part = parts_catalog.PartEnriched(
            lcsc="C0001", mpn="FAKE-PART", package="0402",
            size_mm={"w": 0.5, "h": 1.0, "d": 0.5}, size_source="jedec",
            pin_count=2, stock=100, lifecycle="normal", source="lcsc:C0001:2026-01-01")
        cache.put("C0001", part.to_dict())
        saved = cache.save()

        # -- 命中新鲜缓存：不发网络 --
        fresh = parts_catalog.enrich("C0001", cache=parts_catalog.PartCache(cache_path),
                                     net=parts_catalog._Net(offline=True))  # noqa: SLF001
        fresh_ok = (saved and fresh.get("status") == "ok"
                    and fresh.get("cache") == "fresh")
        check("4a. 缓存：TTL 内命中 → ok/fresh，不发网络", fresh_ok,
              f"saved={saved} status={fresh.get('status')} cache={fresh.get('cache')}")

        # -- 过期缓存 + 离线：stale 但仍可用于主流程 --
        old = json.loads(cache_path.read_text(encoding="utf-8"))
        old["entries"]["C0001"]["fetched_at"] = "2020-01-01T00:00:00Z"
        cache_path.write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")
        stale = parts_catalog.enrich("C0001", cache=parts_catalog.PartCache(cache_path),
                                     net=parts_catalog._Net(offline=True))  # noqa: SLF001
        stale_ok = (stale.get("status") == "ok"
                    and stale.get("cache") == "stale"
                    and (stale.get("part") or {}).get("stale") is True)
        check("4b. 缓存：过期 + 离线 → ok/stale（陈旧数据比没有好）",
              stale_ok, f"status={stale.get('status')} cache={stale.get('cache')} "
                        f"age={stale.get('age_days')}")

        # -- 无缓存 + 离线：not_ready，且**不抛异常** --
        try:
            miss = parts_catalog.enrich("C9999", cache=parts_catalog.PartCache(cache_path),
                                        net=parts_catalog._Net(offline=True))  # noqa: SLF001
            miss_ok = miss.get("status") == "not_ready"
            detail = f"status={miss.get('status')} reason={miss.get('reason')}"
        except Exception as exc:  # noqa: BLE001
            miss_ok = False
            detail = f"抛了 {type(exc).__name__}: {exc}"
        check("4c. 缓存：无缓存 + 离线 → not_ready 且不抛异常", miss_ok, detail)

        # -- 坏缓存文件当没有（不崩） --
        broken = tmp / "broken.json"
        broken.write_text("{not json", encoding="utf-8")
        bad_cache = parts_catalog.PartCache(broken).load()
        check("4d. 缓存：坏文件当没有（加速层不是事实源）",
              len(bad_cache) == 0, f"entries={len(bad_cache)}")

        # -- 环境变量真生效 --
        old_env = os.environ.get(parts_catalog.OFFLINE_ENV)
        os.environ[parts_catalog.OFFLINE_ENV] = "1"
        try:
            offline_on = parts_catalog.offline()
            result = parts_catalog.enrich("C1234", cache=parts_catalog.PartCache(cache_path))
        finally:
            if old_env is None:
                os.environ.pop(parts_catalog.OFFLINE_ENV, None)
            else:
                os.environ[parts_catalog.OFFLINE_ENV] = old_env
        check("4e. HWV_PARTS_OFFLINE=1 真的挡住网络",
              offline_on and result.get("status") == "not_ready",
              f"offline={offline_on} status={result.get('status')} reason={result.get('reason')}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# 5. 信任级闸门（覆盖层指向临时文件）
# ---------------------------------------------------------------------------


class _Overlay:
    """把两个覆盖文件指到临时目录，退出时还原。"""

    def __init__(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="hwv-p19-overlay-"))
        self.saved: tuple[Path, Path] = (capability.CATALOG_FILE,
                                         capability.PROMOTED_FILE)

    def __enter__(self) -> Path:
        capability.CATALOG_FILE = self.tmp / "catalog_parts.json"
        capability.PROMOTED_FILE = self.tmp / "capability_promoted.json"
        capability.reset_overlay()
        return self.tmp

    def __exit__(self, *exc: Any) -> None:
        capability.CATALOG_FILE, capability.PROMOTED_FILE = self.saved
        capability.reset_overlay()
        shutil.rmtree(self.tmp, ignore_errors=True)


_CATALOG_ENTRY = {
    "fills": [], "provides": [], "requires": [],
    "footprint_mm2": 5.28 * 5.2, "pins": {}, "clock_hz": None,
    "lcsc": "C89852", "mpn": "ATTINY85-20SU", "package": "SOIC-8-208mil",
    "datasheet_url": "https://example.invalid/attiny85.pdf",
    "note": "目录回填；fills/provides 未确认",
    "source": "catalog",
}


def _catalog_gate_ok(key: str = "attiny85-20su") -> tuple[bool, str]:
    """闸门判据本体：catalog 型号在 rejected（点名原因）、不在 candidates。"""
    rejected = capability.explain_miss({"i2c"}, role="mcu").get("rejected") or {}
    picked = capability.candidates({"i2c"}, role="mcu")
    reason = str(rejected.get(key) or "")
    ok = (capability.CATALOG_REASON in reason) and (key not in picked)
    return ok, f"rejected[{key}]={reason!r} in_candidates={key in picked}"


def check_gate() -> None:
    with _Overlay() as tmp:
        written = capability.save_catalog_entry("attiny85-20su", _CATALOG_ENTRY)
        ok, detail = _catalog_gate_ok()
        check("5a. 闸门：catalog 条目进 explain_miss.rejected（点名未确认），不进 candidates",
              written and ok, detail)

        # 入口不变量：传进来的 source 会被强制改回 catalog
        tampered = {**_CATALOG_ENTRY, "source": "datasheet"}
        capability.save_catalog_entry("cheater", tampered)
        catalog_raw = json.loads((tmp / "catalog_parts.json").read_text(encoding="utf-8"))
        stored = (catalog_raw.get("entries") or {}).get("cheater") or {}
        check("5b. 闸门：写目录层时 source 被强制成 catalog（绕过不了）",
              stored.get("source") == "catalog" and not capability.trusted("cheater"),
              f"stored.source={stored.get('source')!r} trusted={capability.trusted('cheater')}")

        # 没有手册页 → promote 拒绝，且**不写盘**
        before = (tmp / "capability_promoted.json").is_file()
        refused = capability.promote_entry("attiny85-20su",
                                            {"fills": {"mcu"}, "provides": {"i2c"}}, "")
        after = (tmp / "capability_promoted.json").is_file()
        check("5c. promote 无 --datasheet → 拒绝且不写盘（留痕是硬条件）",
              refused.get("ok") is False and before == after,
              f"ok={refused.get('ok')} reason={refused.get('reason')} wrote={after}")

        # 有手册页 → 升级，进 candidates
        promoted = capability.promote_entry(
            "attiny85-20su",
            {"fills": {"mcu"}, "provides": {"i2c"}},
            "https://example.invalid/attiny85.pdf")
        in_cands = "attiny85-20su" in capability.candidates({"i2c"}, role="mcu")
        still_catalog = "attiny85-20su" in capability.entries() and \
            capability.entries()["attiny85-20su"].get("source") == "datasheet"
        catalog_file = json.loads((tmp / "catalog_parts.json").read_text(encoding="utf-8"))
        moved = "attiny85-20su" not in (catalog_file.get("entries") or {})
        check("5d. promote 带 --datasheet → 进 candidates，且从目录层移走",
              promoted.get("ok") and in_cands and still_catalog and moved,
              f"trusted={capability.trusted('attiny85-20su')} in_candidates={in_cands} "
              f"moved_out_of_catalog={moved} datasheet={promoted.get('entry', {}).get('datasheet_url')}")

        # -- 退化验证：把信任级判断放水，5a 必须变红 --
        saved_trusted = capability.trusted
        try:
            capability.trusted = lambda key: capability.get(key) is not None  # type: ignore[assignment]
            capability.save_catalog_entry("attiny85-degraded",
                                           {**_CATALOG_ENTRY, "fills": ["mcu"]})
            ok_after, detail_after = _catalog_gate_ok("attiny85-degraded")
            degraded_ok = not ok_after
        finally:
            capability.trusted = saved_trusted  # type: ignore[assignment]
        check("5e. 退化验证：放水信任级判断 → 5a 的判据变红（证明它承重）",
              degraded_ok, f"放水后判据={ok_after}（期望 False）{detail_after}")


# ---------------------------------------------------------------------------
# 6. 回填字段
# ---------------------------------------------------------------------------


def check_backfill() -> None:
    payload = fixture("jlc_detail_C89852.json")
    data = payload.get("data") if isinstance(payload, dict) else None
    part = parts_catalog.parse_detail(data) if isinstance(data, dict) else parts_catalog.PartEnriched()
    fields = parts_catalog.to_design_fields(part.to_dict())
    ok = (fields.get("physical_source") == "catalog"
          and (fields.get("size_mm") or {}).get("w") == 5.28
          and len(fields.get("pins") or []) == 8
          and fields.get("lcsc") == "C89852")
    check("6a. 回填：目录尺寸 → size_mm 且 physical_source=catalog",
          ok, json.dumps(fields, ensure_ascii=False))

    # 公式推的尺寸 → estimated（不能冒充 catalog）
    formula_part = parts_catalog.PartEnriched(
        lcsc="C0002", mpn="X", size_mm={"w": 3.9, "h": 7.2, "d": 1.75},
        size_source="formula", pin_count=10).to_dict()
    formula_fields = parts_catalog.to_design_fields(formula_part)
    check("6b. 回填：公式推的尺寸标 estimated（不冒充 catalog）",
          formula_fields.get("physical_source") == "estimated",
          f"physical_source={formula_fields.get('physical_source')}")

    # -- 与 P17 的接缝：回填出来的 lcsc 必须能被发射层认出 ------------------
    from dsh_embagent.design import emit_lceda  # noqa: PLC0415 - 只为一个纯函数
    from dsh_embagent.design.model import Part  # noqa: PLC0415
    filled = parts_catalog.to_design_fields(part.to_dict())
    probe = Part(ref="U1", kind="mcu", physical_source="datasheet",
                 lcsc=filled.get("lcsc"), pins=["1"])
    probe.size_mm = filled.get("size_mm") or probe.size_mm
    check("6d. 接缝：回填出的 lcsc 正是 P17 发射层认的那个（lcsc 非空可解析）",
          emit_lceda._lcsc_of(probe) == "C89852",
          f"lcsc_of={emit_lceda._lcsc_of(probe)!r}（P17 的空值是 no_symbol 的来源）")

    # 没尺寸 → 什么都不写（不写 0）
    empty_fields = parts_catalog.to_design_fields(
        parts_catalog.PartEnriched(lcsc="C0003", size_source="unknown").to_dict())
    check("6c. 回填：没有尺寸就不写 size_mm（不写 0）",
          "size_mm" not in empty_fields, json.dumps(empty_fields, ensure_ascii=False))


# ---------------------------------------------------------------------------
# 7. 真机联网（两条）
# ---------------------------------------------------------------------------


def check_live() -> None:
    result = parts_catalog.search("stm32f103 lqfp48", limit=5)
    items = result.get("items") or []
    if result.get("status") != "ok":
        check("7a. 真机：search 返回 ≥5 条（带封装/库存）", False,
              f"status={result.get('status')} reason={result.get('reason')}",
              status=SKIP)
        check("7b. 真机：enrich C89852 → 尺寸非 0", False, "同上", status=SKIP)
        return
    ok = len(items) >= 5 and all(i.get("package") and i.get("lcsc") for i in items)
    check("7a. 真机：search 「stm32f103 lqfp48」≥5 条，每条带 lcsc/封装/库存",
          ok, f"total={result.get('total')} 样例="
              f"{[(i.get('lcsc'), i.get('mpn'), i.get('package'), i.get('stock')) for i in items[:3]]}")

    enriched = parts_catalog.enrich("C89852",
                                    cache=parts_catalog.PartCache(
                                        Path(tempfile.gettempdir()) / "hwv-p19-live.json"))
    part = enriched.get("part") or {}
    fields = parts_catalog.to_design_fields(part)
    size = fields.get("size_mm") or {}
    ok2 = (enriched.get("status") == "ok" and size.get("w", 0) > 0
           and fields.get("physical_source") == "catalog")
    check("7b. 真机：enrich C89852 → size_mm 非 0 且 physical_source=catalog",
          ok2, f"status={enriched.get('status')} cache={enriched.get('cache')} "
               f"size={size} source={fields.get('physical_source')}")


# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    only = ""
    for item in args:
        if item.startswith("--only="):
            only = item.split("=", 1)[1]

    print("=== P19 验收：LCSC 元件数据源 ===\n")
    if only in ("", "packages"):
        check_packages()
    if only in ("", "parsers"):
        check_parsers()
    if only in ("", "shape"):
        check_shape_change()
    if only in ("", "cache"):
        check_cache_offline()
    if only in ("", "gate"):
        check_gate()
    if only in ("", "backfill"):
        check_backfill()
    if only in ("", "live"):
        check_live()

    passed = sum(1 for r in _RESULTS if r["status"] == PASS)
    failed = sum(1 for r in _RESULTS if r["status"] == FAIL)
    skipped = sum(1 for r in _RESULTS if r["status"] not in (PASS, FAIL))
    status = "ok" if failed == 0 and skipped == 0 else ("failed" if failed else "not_ready")
    print(f"\n=== {passed} 通过 / {failed} 失败 / {skipped} 未做到 ===")
    payload = {"status": status, "check": "P19", "passed": passed,
               "failed": failed, "skipped": skipped, "results": _RESULTS}
    sys.stdout.buffer.write(json.dumps(payload, ensure_ascii=False, default=str)
                            .encode("utf-8") + b"\n")
    return 0 if status == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())