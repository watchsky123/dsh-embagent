"""实体层 —— 点一个元件，把它**所有**相关结论聚到一起。

## 为什么需要它（而不是再开几个页签）

`claims` / `justify` / `physical_tests` / `checks` / `units` 这五份产物的索引
全是 `ref`，但它们在 UI 上分在五个页签里。人的问题只有一个：

    U3 这个件，为什么在板上？它现在被验到什么程度？

要回答这一句，现在得切五次页签、在 316 个元件里各找一遍。

**按对象组织，不按产物组织。** 这是 P2 的唯一设计决定。

## 三条纪律

1. **只聚合，不判定**。每一栏都是别的模块算好的结论，这里不改一个字节。
   `status` 那一栏是**压**出来的（fail > unknown > pass），不是新判定。
2. **没有数据就说没有**。`claims` 里没有这个 `ref` → `claims.total = 0`
   且 `status = not_ready`，而不是"通过"。这是 AGENTS §0.1 那条在实体层的复述。
3. **索引要小，详情按需取**。316 个元件的完整 bundle 塞进快照会把它撑爆，
   所以快照只带一行摘要（`index()`），详情走单独的 `entity` 命令。
"""

from __future__ import annotations

from typing import Any

__all__ = ["index", "profile", "describe"]

#: 单个实体最多回几条 claim / 测试记录。**不是分页**，是防止一个件
#: 挂 50 条断言把 Inspector 撑爆（实测最重的件也就 6 条）。
MAX_CLAIMS = 12
MAX_TESTS = 8
MAX_FINDINGS = 8


def _by_ref_claims(claims: dict[str, Any] | None) -> dict[str, list[dict[str, Any]]]:
    """`claims.json` → {ref: [claim, ...]}。

    `scope.part` 是唯一可靠的索引。**别用 `id` 反推**（`claim.C1.connected`
    里的 `C1` 与 `C10` 前缀相同，正则切会错）。
    """
    out: dict[str, list[dict[str, Any]]] = {}
    for raw in (claims or {}).get("claims") or []:
        if not isinstance(raw, dict):
            continue
        ref = str((raw.get("scope") or {}).get("part") or "")
        if ref:
            out.setdefault(ref, []).append(raw)
    return out


def _by_ref_justify(justify: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for raw in (justify or {}).get("parts") or []:
        if isinstance(raw, dict) and raw.get("ref"):
            out[str(raw["ref"])] = raw
    return out


def _by_ref_findings(checks: dict[str, Any] | None) -> dict[str, list[dict[str, Any]]]:
    """`checks.json` 的 findings 按 `refs` 摊开（一条可能牵涉多个件）。"""
    out: dict[str, list[dict[str, Any]]] = {}
    for raw in (checks or {}).get("findings") or []:
        if not isinstance(raw, dict):
            continue
        for ref in raw.get("refs") or []:
            out.setdefault(str(ref), []).append(raw)
    return out


def _by_ref_tests(design: Any) -> dict[str, list[dict[str, Any]]]:
    """`physical_tests` 的 `implicated` 形如 `ref:U3`，要剥前缀。

    **没写 implicated 的记录不归到任何件上**（那里是"整个设计"范围的实测，
    例如"整机上电无输出"）。硬安到某个件上等于编归因。
    """
    out: dict[str, list[dict[str, Any]]] = {}
    for raw in (getattr(design, "physical_tests", None) or []):
        if not isinstance(raw, dict):
            continue
        for token in raw.get("implicated") or []:
            text = str(token)
            ref = text[4:] if text.startswith("ref:") else text
            if ref:
                out.setdefault(ref, []).append(raw)
    return out


def _by_ref_units(units: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """哪个单元包含这个件（`units.json` 的 `parts` 是位号列表）。"""
    out: dict[str, dict[str, Any]] = {}
    for raw in (units or {}).get("units") or []:
        if not isinstance(raw, dict):
            continue
        for ref in raw.get("parts") or []:
            out[str(ref)] = {"id": raw.get("id"), "name": raw.get("name"),
                             "role": raw.get("role"),
                             "board_id": raw.get("board_id")}
    return out


def _rollup(claims: list[dict[str, Any]], findings: list[dict[str, Any]],
            tests: list[dict[str, Any]]) -> str:
    """三态压成一个。**顺序是有理由的：**

    1. 有实测失败 → `fail`（最硬的证据，压过一切）
    2. 有断言失败 / 检查 error → `fail`
    3. 有 unknown 的断言、未验证的检查、没结论的实测 → `unknown`
    4. 一条数据都没有 → `not_ready`（**不是 pass**）
    5. 其余 → `pass`
    """
    if any(str(t.get("result")) == "fail" for t in tests):
        return "fail"
    if any(str(c.get("status")) == "fail" for c in claims):
        return "fail"
    if any(f.get("ok") is False and f.get("severity") == "error" for f in findings):
        return "fail"
    if (any(str(c.get("status")) == "unknown" for c in claims)
            or any(str(t.get("result")) == "unknown" for t in tests)
            or any(f.get("ok") is not True for f in findings)):
        return "unknown"
    if not claims and not findings and not tests:
        return "not_ready"
    return "pass"


def _claim_row(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(raw.get("id") or ""),
        "type": str(raw.get("type") or ""),
        "intent": str(raw.get("intent") or ""),
        "status": str(raw.get("status") or "unknown"),
        "priority": str(raw.get("priority") or ""),
        "basis": str(raw.get("basis") or ""),
        # 失败时给出"该怎么办"：claims 自己带 `on_fail.narrow`
        "on_fail": str(((raw.get("on_fail") or {}).get("message")) or ""),
    }


def profile(design: Any, ref: str, *,
            claims: dict[str, Any] | None = None,
            justify: dict[str, Any] | None = None,
            checks: dict[str, Any] | None = None,
            units: dict[str, Any] | None = None) -> dict[str, Any]:
    """一个元件的完整画像。找不到这个 `ref` 时 `status=failed`，**不返回空壳**。"""
    part = next((p for p in design.parts if p.ref == ref), None)
    if part is None:
        return {"status": "failed",
                "findings": [f"设计里没有位号 {ref!r}"]}

    refs_claims = _by_ref_claims(claims).get(ref, [])[:MAX_CLAIMS]
    ref_findings = _by_ref_findings(checks).get(ref, [])[:MAX_FINDINGS]
    ref_tests = _by_ref_tests(design).get(ref, [])[:MAX_TESTS]
    ref_justify = _by_ref_justify(justify).get(ref)
    ref_unit = _by_ref_units(units).get(ref)

    nets: list[str] = []
    for entry in getattr(design, "nets", None) or []:
        for pin in entry.get("pins") or []:
            if pin.get("ref") == ref:
                nets.append(str(entry.get("net")))
                break

    status = _rollup(refs_claims, ref_findings, ref_tests)

    counts = {"pass": 0, "fail": 0, "unknown": 0}
    for claim in refs_claims:
        key = str(claim.get("status") or "unknown")
        if key in counts:
            counts[key] += 1

    out: dict[str, Any] = {
        "status": status,
        "ref": ref,
        "kind": str(part.kind or ""),
        "label": str(part.label or ""),
        "role": str(part.role or ""),
        "size_mm": dict(part.size_mm or {}),
        "physical_source": str(part.physical_source or ""),
        "placement": {"x_mm": part.placement.x_mm, "y_mm": part.placement.y_mm,
                      "side": part.placement.side,
                      "board_id": part.placement.board_id},
        "nets": nets,
        "claims": {
            "total": len(refs_claims),
            "pass": counts["pass"], "fail": counts["fail"],
            "unknown": counts["unknown"],
            "items": [_claim_row(c) for c in refs_claims],
        },
        # 叫 `checks` 不叫 `findings`：`status=failed` 时的 `findings`
        # 是"失败原因"字符串，两个同名会让 TS 的类型合并报错。
        "checks": [{
            "check": str(f.get("check") or ""),
            "ok": f.get("ok"),
            "severity": str(f.get("severity") or ""),
            "message": str(f.get("message") or ""),
        } for f in ref_findings],
        "tests": [{
            "id": str(t.get("id") or ""),
            "test": str(t.get("test") or ""),
            "result": str(t.get("result") or "unknown"),
            "observed": str(t.get("observed") or ""),
            "sim_reproduced": t.get("sim_reproduced"),
        } for t in ref_tests],
        "unit": ref_unit,
    }

    if ref_justify is not None:
        justification = ref_justify.get("justification") or {}
        out["justification"] = {
            "why": str(justification.get("why") or ""),
            "effect_if_removed": str(justification.get("effect_if_removed") or ""),
            "confidence": str(justification.get("confidence") or "unknown"),
            "source": str(justification.get("source") or ""),
            "category": str(ref_justify.get("category") or ""),
            "group": dict(ref_justify.get("group") or {}),
        }
    else:
        # **缺归因要说缺**，不能空着让人以为"这个件没有作用"
        out["justification"] = {"why": "", "effect_if_removed": "",
                                "confidence": "missing", "source": "",
                                "category": "", "group": {}}
    return out


def index(design: Any, *,
          claims: dict[str, Any] | None = None,
          checks: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    """**紧凑索引**：每个位号一行摘要，给 2D 画布和列表用。

    只放能做成徽章的东西（计数 + 三态），不放正文——316 个元件的正文
    会把快照撑到几 MB，而人一次只看一个元件。
    """
    claims_by_ref = _by_ref_claims(claims)
    findings_by_ref = _by_ref_findings(checks)
    tests_by_ref = _by_ref_tests(design)

    out: dict[str, dict[str, Any]] = {}
    for part in design.parts:
        ref_claims = claims_by_ref.get(part.ref, [])
        ref_findings = findings_by_ref.get(part.ref, [])
        ref_tests = tests_by_ref.get(part.ref, [])
        out[part.ref] = {
            "status": _rollup(ref_claims, ref_findings, ref_tests),
            "claims": len(ref_claims),
            "failed_claims": sum(1 for c in ref_claims
                                 if str(c.get("status")) == "fail"),
            "unknown_claims": sum(1 for c in ref_claims
                                  if str(c.get("status")) == "unknown"),
            "findings": sum(1 for f in ref_findings if f.get("ok") is not True),
            "tests": len(ref_tests),
        }
    return out


def describe() -> dict[str, Any]:
    return {
        "module": "hw_velxio.design.entity",
        "sources": ["design.json", "claims.json", "justify.json",
                    "checks.json", "units.json", "design.physical_tests"],
        "caps": {"claims": MAX_CLAIMS, "tests": MAX_TESTS,
                 "findings": MAX_FINDINGS},
        "status_order": "实测失败 > 断言失败 > 检查 error > unknown > 无数据(not_ready) > pass",
        "why": "按对象聚合（不按产物），一个元件的五份结论拼成一屏",
        "not_judging": "只聚合已有结论；status 是压出来的，不是新判定",
        "index": "快照只带一行摘要（计数 + 三态），正文走 entity 命令按需取",
    }