"""设计成熟度（C3）—— 只按 **需求/规格/证据** 算，不引用任何参考工程。

## 六个维度（每维只会是 pass / unknown / missing / fail）

| 维度 | 判据 |
|---|---|
| 需求 | `req.json` 在，且有 roles/interfaces |
| 规格 | `spec/vN.json` 在，且块/契约非空 |
| 块 | `design.blocks_list` 非空；`verified_by` 非空的块数 |
| 约束 | `constraints.json`：pass>0 且 fail=0 → pass；有 fail → fail |
| 证据 | `datasheet`/`measured` 尺寸的元件占比 ≥ 80% |
| 发射 | `emit.json` 有工程 uuid，且 verify 为 ok |

`level` = pass 的维度数（0–6）。**没有 req/spec 就是 `not_ready`**：
连"要做什么"都没有，谈成熟度没有意义。

## 纪律

* 不引用参考工程、不做"和某项目比"——参考对照只作能力说明（C2）
* 缺产物 = `missing`，不是 0 分也不是 pass
* 每维都带 `checked`/`basis`，人能复核
"""

from __future__ import annotations

from typing import Any

__all__ = ["FILE", "compute", "describe"]

FILE = "maturity.json"

_DIMENSIONS = ("requirements", "spec", "blocks", "constraints", "evidence", "emit")


def _dim(dim: str, status: str, *, checked: dict[str, Any] | None = None,
         basis: str = "", detail: str = "", need: str = "") -> dict[str, Any]:
    return {"id": dim, "status": status, "checked": checked or {},
            "basis": basis, "detail": detail, "need": need}


def compute(store: Any, design: Any) -> dict[str, Any]:
    """六维成熟度。**只读本工程的产物**。"""
    from . import constraints as constraints_module
    from . import spec as spec_module

    rows: list[dict[str, Any]] = []
    req = store.read("req.json")
    ok_req = isinstance(req, dict) and bool(req.get("roles") or req.get("interfaces"))
    rows.append(_dim("requirements", "pass" if ok_req else "missing",
                     checked={"roles": len((req or {}).get("roles") or []),
                              "interfaces": len((req or {}).get("interfaces") or [])}
                     if isinstance(req, dict) else {},
                     basis="req.json",
                     detail=("需求在" if ok_req else "没有 req.json 或没有角色/接口"),
                     need=("" if ok_req else "req-draft 写需求并确认")))

    spec = spec_module.load_latest(store.dir)
    ok_spec = isinstance(spec, dict) and bool(spec.get("blocks"))
    rows.append(_dim("spec", "pass" if ok_spec else "missing",
                     checked={"blocks": len((spec or {}).get("blocks") or []),
                              "interfaces": len((spec or {}).get("interfaces") or [])}
                     if isinstance(spec, dict) else {},
                     basis="spec/vN.json",
                     detail=(f"规格 v{spec.get('version')}" if ok_spec
                             else "没有规格（或块为空）"),
                     need=("" if ok_spec else "spec <id> --build")))

    blocks = list(getattr(design, "blocks_list", None) or [])
    verified = 0
    try:
        from . import blocks as blocks_module
        library = blocks_module.load_all()
        verified = sum(1 for row in blocks
                       if (library.get(str(row.get("block"))) or {})
                       .get("verified_by"))
    except Exception:  # noqa: BLE001
        verified = 0
    rows.append(_dim("blocks", "pass" if blocks and verified else
                     ("unknown" if blocks else "missing"),
                     checked={"blocks": len(blocks), "verified": verified},
                     basis="design.blocks_list + design/blocks/*.json 的 verified_by",
                     detail=(f"{len(blocks)} 块，{verified} 块有验证记录" if blocks
                             else "还没按块装配"),
                     need=("" if blocks and verified else
                           "assemble 或给块补 verified_by（真板验证）")))

    cons = store.read(constraints_module.FILE)
    counts = (cons or {}).get("counts") if isinstance(cons, dict) else None
    if not isinstance(counts, dict):
        rows.append(_dim("constraints", "missing", basis="constraints.json",
                         detail="还没有约束账本",
                         need="constraints <id> --derive --write"))
    elif int(counts.get("fail") or 0):
        rows.append(_dim("constraints", "fail",
                         checked=dict(counts), basis="constraints.json",
                         detail=f"{counts.get('fail')} 条 fail",
                         need="先修 fail 的约束"))
    elif int(counts.get("pass") or 0):
        rows.append(_dim("constraints", "pass", checked=dict(counts),
                         basis="constraints.json",
                         detail=f"{counts.get('pass')} pass / "
                                f"{counts.get('unknown')} unknown"))
    else:
        rows.append(_dim("constraints", "unknown", checked=dict(counts),
                         basis="constraints.json",
                         detail="只有 unknown —— 没有可判的",
                         need="补数据让约束可求值"))

    parts = list(getattr(design, "parts", None) or [])
    trusted = [p for p in parts
               if str(getattr(p, "physical_source", "")) in ("datasheet", "measured")]
    ratio = (len(trusted) / len(parts)) if parts else 0.0
    rows.append(_dim("evidence", "pass" if parts and ratio >= 0.8 else
                     ("unknown" if parts else "missing"),
                     checked={"parts": len(parts), "trusted": len(trusted),
                              "ratio": round(ratio, 3)},
                     basis="physical_source（datasheet/measured）",
                     detail=f"{len(trusted)}/{len(parts)} 元件有可信尺寸来源",
                     need=("" if parts and ratio >= 0.8 else
                           "补 datasheet 尺寸，或跑 lceda-geom 把 catalog 回填")))

    emit = store.read("emit.json")
    project = (emit or {}).get("project") or {} if isinstance(emit, dict) else {}
    verify = str((emit or {}).get("verify") or "") if isinstance(emit, dict) else ""
    ok_emit = bool(project.get("uuid")) and verify == "ok"
    rows.append(_dim("emit", "pass" if ok_emit else
                     ("unknown" if project.get("uuid") else "missing"),
                     checked={"project_uuid": bool(project.get("uuid")),
                              "verify": verify or "missing"},
                     basis="emit.json",
                     detail=("已发射且连通性验证 ok" if ok_emit
                             else "没发射过，或 verify 不是 ok"),
                     need=("" if ok_emit else "emit <id>")))

    passed = [row for row in rows if row["status"] == "pass"]
    missing = [{"dimension": row["id"], "need": row["need"]}
               for row in rows if row["status"] != "pass" and row["need"]]
    status = ("not_ready" if not ok_req or not ok_spec else "ok")
    return {
        "status": status,
        "file": FILE,
        "level": len(passed),
        "total": len(_DIMENSIONS),
        "dimensions": rows,
        "missing": missing,
        "note": ("成熟度只看本工程的需求/规格/证据，**不与任何参考工程比较**；"
                 "缺产物是 missing，不是 0 分"),
    }


def describe() -> dict[str, Any]:
    return {
        "module": "hw_velxio.design.maturity",
        "file": FILE,
        "dimensions": list(_DIMENSIONS),
        "level": "pass 的维度数（0–6）；没有 req/spec → not_ready",
        "discipline": "只读本工程产物；不引用参考工程；缺产物是 missing",
    }