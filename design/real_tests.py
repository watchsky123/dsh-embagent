"""实物测试记录 —— 让"我真做过了"变得可查、可反驳。

## 这一层补的是什么

P15–P20 的每一层都在**推导**：几何、电气、运动、制造。整个仓库里
**没有一条真机实测**。而 RoboBuilder 的实验说明这有多要命：它的仿真器
在 135 个参数修好之前，15 个"仿真通过"的实车全部跑不完赛道——
**仿真越自信，错得越整齐**。

所以这一层只做一件事：把「哪天、拿什么、试了什么、结果如何、看到什么、
牵涉哪些件、用哪套参数仿的、仿没仿出来」记成一条结构化记录，
并且**不许糊**：

| 字段 | 为什么必须分开 |
|---|---|
| `result` | `pass` / `fail` / `unknown` 三态。**"没测成"不是"失败"** |
| `observed` | `fail` 必须有现象描述。只写"失败了"等于没写 |
| `evidence` | 视频/帧/缩略图路径。没有证据的结论标注出来，不装作有 |
| `implicated` | `ref:<位号>`，**必须能对上设计里的元件**（V0 同款符号检查） |
| `sim_reproduced` | **三态**：`True` 仿出来了 / `False` 仿不出来 / `None` 没试过。`None` 不许写成 `False` |

最后一项是这一层的核心价值。`fail` + `sim_reproduced=False` 意味着
**仿真模型漏掉了这条失败机制**，比"仿真通过"严重得多——它说明我们
的 136 个参数里有一块物理是错的。

## 为什么不复用 `sim.json` / `ladder.json`

那两个是**机器算的**。这一层是**人看到的**。混在一张表里，下次就分不清
"这条结论是仿真给的还是我举着手机拍的"。`handoff` 把它们放进不同的类
（`external` vs 其余），理由相同。

## 引用纪律（照 P8/P15 的老规矩）

* `implicated` 写的是 `ref:U12` 这种**可解析**形式，裸位号也接受；
  解析不出、或设计里没有这个位号 → **error**，不静默丢弃
* 推导不出的东西留 `unknown`，不补零、不补默认值
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .model import DesignModel

__all__ = [
    "MAX_IMPLICATED",
    "RESULTS",
    "Evidence",
    "PhysicalTest",
    "add",
    "contact_sheet",
    "describe",
    "load",
    "summary",
    "validate",
]

#: 三条结果。``unknown`` 是"没测成/测不了"，**不是** fail，也**不是** pass。
RESULTS = ("pass", "fail", "unknown")

#: 一条记录最多列几个牵涉件（再多就不是"定位"而是"整机"了）。
MAX_IMPLICATED = 8

_REF_PREFIX = re.compile(r"^(?:ref:)?\s*([A-Za-z][\w.\-]*)$")


# ---------------------------------------------------------------------------
# 数据
# ---------------------------------------------------------------------------


@dataclass
class Evidence:
    """证据。**路径原样存**，不复制文件——视频可能几百 MB，且它属于人。"""

    video: str = ""
    frames: list[str] = field(default_factory=list)
    contact_sheet: str = ""
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if self.video:
            out["video"] = self.video
        if self.frames:
            out["frames"] = list(self.frames)
        if self.contact_sheet:
            out["contact_sheet"] = self.contact_sheet
        if self.note:
            out["note"] = self.note
        return out

    @staticmethod
    def from_dict(data: dict[str, Any] | None) -> "Evidence":
        row = data or {}
        return Evidence(
            video=str(row.get("video") or ""),
            frames=[str(x) for x in (row.get("frames") or [])],
            contact_sheet=str(row.get("contact_sheet") or ""),
            note=str(row.get("note") or ""),
        )

    @property
    def has_any(self) -> bool:
        return bool(self.video or self.frames or self.contact_sheet)


@dataclass
class PhysicalTest:
    """一条实物测试。字段语义见模块文档。"""

    id: str = ""
    subject: str = ""
    test: str = ""
    result: str = "unknown"
    observed: str = ""
    evidence: Evidence = field(default_factory=Evidence)
    implicated: list[str] = field(default_factory=list)
    #: 用哪一套仿真参数跑的（`initial` / `learned-2026-11-21` / 空=没仿）
    sim_params: str = ""
    #: **三态**：True 仿出来了 / False 仿不出来 / None 没试过
    sim_reproduced: bool | None = None
    at: str = ""
    by: str = ""
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.id,
            "subject": self.subject,
            "test": self.test,
            "result": self.result,
        }
        if self.observed:
            out["observed"] = self.observed
        if self.evidence.has_any:
            out["evidence"] = self.evidence.to_dict()
        if self.implicated:
            out["implicated"] = list(self.implicated)
        if self.sim_params:
            out["sim_params"] = self.sim_params
        # 关键：None（没试过）**不写**，与 False（试了没复现）分开
        if self.sim_reproduced is not None:
            out["sim_reproduced"] = bool(self.sim_reproduced)
        if self.at:
            out["at"] = self.at
        if self.by:
            out["by"] = self.by
        if self.notes:
            out["notes"] = list(self.notes)
        return out

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "PhysicalTest":
        result = str(data.get("result") or "unknown")
        if result not in RESULTS:
            result = "unknown"
        raw_repro = data.get("sim_reproduced")
        return PhysicalTest(
            id=str(data.get("id") or ""),
            subject=str(data.get("subject") or ""),
            test=str(data.get("test") or ""),
            result=result,
            observed=str(data.get("observed") or ""),
            evidence=Evidence.from_dict(data.get("evidence")),
            implicated=[str(x) for x in (data.get("implicated") or [])],
            sim_params=str(data.get("sim_params") or ""),
            sim_reproduced=(bool(raw_repro) if isinstance(raw_repro, bool) else None),
            at=str(data.get("at") or ""),
            by=str(data.get("by") or ""),
            notes=[str(x) for x in (data.get("notes") or [])],
        )


# ---------------------------------------------------------------------------
# 读 / 校验 / 写
# ---------------------------------------------------------------------------


def load(design: DesignModel) -> list[PhysicalTest]:
    """设计里已有的记录。**不排序**——文件里的顺序就是录入顺序。"""
    return [PhysicalTest.from_dict(row)
            for row in (getattr(design, "physical_tests", None) or [])
            if isinstance(row, dict)]


def _norm_ref(raw: str) -> str:
    match = _REF_PREFIX.match(str(raw or "").strip())
    return match.group(1) if match else ""


def validate(design: DesignModel, test: PhysicalTest) -> list[str]:
    """返回问题清单。**不抛异常**——一次列全部，让人一次改完。"""
    problems: list[str] = []

    if not str(test.test or "").strip():
        problems.append(f"{test.id or '(无 id)'}: test 不能为空（写了什么测试）")
    if test.result not in RESULTS:
        problems.append(f"{test.id}: result={test.result!r} 非法（可用 {RESULTS}）")
    if test.result == "fail" and not str(test.observed or "").strip():
        problems.append(
            f"{test.id}: fail 必须写 observed（看到什么现象）——"
            "只写「失败了」等于没写，别人没法据此改设计")
    if test.result == "fail" and not test.evidence.has_any:
        # 提示级：证据可能在别处（手机里、纸上），不能因此判 error
        problems.append(
            f"{test.id}: 警告：fail 没有 evidence（视频/帧/缩略图），"
            "这条结论无法复核")

    seen: set[str] = set()
    for raw in test.implicated:
        ref = _norm_ref(raw)
        if not ref:
            problems.append(f"{test.id}: implicated 里的 {raw!r} 解析不出位号")
            continue
        if design.part(ref) is None:
            problems.append(
                f"{test.id}: implicated 引用了不存在的元件 {ref!r}"
                "（写错的位号会让归因指错地方）")
            continue
        seen.add(ref)
    if len(test.implicated) > MAX_IMPLICATED:
        problems.append(
            f"{test.id}: implicated 有 {len(test.implicated)} 项，"
            f"超过 {MAX_IMPLICATED}——牵涉件太多就不是定位了")
    if test.result == "pass" and seen:
        problems.append(f"{test.id}: 警告：pass 却列了牵涉件 {sorted(seen)}")

    return problems


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def next_id(design: DesignModel) -> str:
    """`pt-001` 递增。用现有最大号 +1，不用 len()（删过就会撞号）。"""
    highest = 0
    for existing in load(design):
        match = re.fullmatch(r"pt-(\d+)", existing.id)
        if match:
            highest = max(highest, int(match.group(1)))
    return f"pt-{highest + 1:03d}"


def add(design: DesignModel, *, test: str, result: str,
        observed: str = "", implicated: Iterable[str] = (),
        evidence: dict[str, Any] | PhysicalTest | None = None,
        subject: str = "", sim_params: str = "",
        sim_reproduced: bool | None = None, by: str = "",
        test_id: str = "") -> tuple[PhysicalTest, list[str]]:
    """追加一条记录到 ``design.physical_tests``（**调用方负责 save**）。

    返回 ``(test, problems)``。``problems`` 非空时**不写**——
    写进去一条自相矛盾的记录比不写更糟（下次会拿它当事实）。
    """
    record = PhysicalTest(
        id=test_id or next_id(design),
        subject=subject or str(getattr(design, "title", "") or ""),
        test=test,
        result=result,
        observed=observed,
        evidence=(evidence if isinstance(evidence, Evidence)
                  else Evidence.from_dict(evidence)),
        implicated=[str(x) for x in implicated],
        sim_params=sim_params,
        sim_reproduced=sim_reproduced,
        at=_now(),
        by=by,
    )
    problems = validate(design, record)
    blocking = [p for p in problems if "警告" not in p]
    if blocking:
        return record, problems
    design.physical_tests = list(getattr(design, "physical_tests", None) or [])
    design.physical_tests.append(record.to_dict())
    return record, problems


def set_reproduced(design: DesignModel, test_id: str, value: bool | None) -> bool:
    """回写 ``sim_reproduced``。找不到这条记录返回 False。

    校准流程用它把"旧失败能在新参数下复现"这件事记到原始记录上——
    **证据链从测试记录出发**，不另存一份。
    """
    for row in (design.physical_tests or []):
        if str(row.get("id")) == test_id:
            if value is None:
                row.pop("sim_reproduced", None)
            else:
                row["sim_reproduced"] = bool(value)
            return True
    return False


def summary(tests: Iterable[PhysicalTest]) -> dict[str, Any]:
    """统计。``reproduced`` / ``not_reproduced`` / ``untried`` 三类分开数。

    这是这一层最该被看的一张表：`fail` 且 `not_reproduced` 的条数，
    就是「仿真模型漏掉了几种物理」的下界。
    """
    rows = list(tests)
    counts = {name: 0 for name in RESULTS}
    reproduced = not_reproduced = untried = 0
    implicated: dict[str, int] = {}
    for row in rows:
        counts[row.result] = counts.get(row.result, 0) + 1
        if row.sim_reproduced is True:
            reproduced += 1
        elif row.sim_reproduced is False:
            not_reproduced += 1
        else:
            untried += 1
        for raw in row.implicated:
            ref = _norm_ref(raw)
            if ref:
                implicated[ref] = implicated.get(ref, 0) + 1
    failed_unreproduced = [r.id for r in rows
                           if r.result == "fail" and r.sim_reproduced is False]
    return {
        "total": len(rows),
        "by_result": counts,
        "reproduced": reproduced,
        "not_reproduced": not_reproduced,
        "untried": untried,
        "failed_unreproduced": failed_unreproduced,
        "implicated": dict(sorted(implicated.items(),
                                  key=lambda kv: -kv[1])),
        "note": ("failed_unreproduced 是「仿真漏掉的物理」的下界；"
                 "untried 是没仿过的（None ≠ False）"),
    }


# ---------------------------------------------------------------------------
# contact_sheet：视频 → 一张缩略图
# ---------------------------------------------------------------------------

#: 抽帧 + 拼接用的命令。**本机实测 ffmpeg/ffprobe 都不在 PATH**，
#: 所以这里返回的永远是"该跑什么"，而不是假装跑过了。
_FFMPEG = "ffmpeg"


def contact_sheet(video: str | Path, out: str | Path = "", *,
                  columns: int = 4, thumb_width: int = 320,
                  frames: int = 16) -> dict[str, Any]:
    """把视频抽帧拼成缩略图（`ffmpeg` 的 `tile` 滤镜）。

    没有 ffmpeg → ``not_ready``，并给出**能直接粘走跑**的命令行。
    这跟 Renode 那条一样：环境不具备就说清差哪一步，不是"做不到"。
    """
    source = Path(str(video))
    if not source.is_file():
        return {"status": "not_ready", "reason": f"视频不存在：{source}"}
    target = Path(str(out)) if str(out) else source.with_name(
        f"{source.stem}_contact.png")
    columns = max(1, int(columns))
    frames = max(1, int(frames))
    rows = (frames + columns - 1) // columns
    tile = f"{columns}x{rows}"
    argv = [
        _FFMPEG, "-y", "-i", str(source),
        "-vf", f"fps=1,scale={int(thumb_width)}:-1,tile={tile}",
        "-frames:v", "1", str(target),
    ]
    command = " ".join(f'"{a}"' if " " in a else a for a in argv)
    exe = shutil.which(_FFMPEG)
    if not exe:
        return {
            "status": "not_ready",
            "reason": "本机没有 ffmpeg（实测 PATH 上没有）",
            "command": command,
            "out": str(target),
            "how": "装一个 ffmpeg 后粘上面那条命令；产物是一张 tile 缩略图",
        }
    try:
        proc = subprocess.run([exe, *argv[1:]],  # noqa: S603 - argv 由本函数拼，不含用户 shell
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              check=False, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"status": "not_ready", "reason": f"跑 ffmpeg 失败：{exc}",
                "command": command}
    ok = proc.returncode == 0 and target.is_file()
    return {
        "status": "ok" if ok else "failed",
        "command": command,
        "out": str(target),
        "bytes": target.stat().st_size if target.is_file() else 0,
        "log": proc.stdout.decode("utf-8", errors="replace")[-400:],
    }


def describe() -> dict[str, Any]:
    return {
        "module": "hw_velxio.design.real_tests",
        "results": list(RESULTS),
        "sim_reproduced": "True / False / None（None = 没试过，不许写成 False）",
        "reference_rule": "implicated 用 ref:<位号>，位号必须存在（V0 同款检查）",
        "contact_sheet": "需要 ffmpeg；没有就返回可直接执行的命令行",
        "why_separate_from_sim": ("sim.json / ladder.json 是机器算的，这一层是人看到的；"
                                  "混表会分不清结论来源"),
    }