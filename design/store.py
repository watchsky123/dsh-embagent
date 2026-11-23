"""工程存储：``<workspace>/<id>/`` 下的产物布局。

**工作区由用户决定**（见 ``design/workspace.py``）：可以是任意文件夹，
默认 ``~/.dsh/hw_velxio/workspace/``。工程不再住在插件目录里——那是安装
位置，升级会覆盖、npm 打包会连带，也不该承载用户资产。

一个工程的磁盘形状（全部 UTF-8 JSON，人类可读、可 diff）：

    project/<id>/
      design.json       规范模型（唯一真值）
      trace.json        过程轨迹（append-only 哈希链）
      deferred.json     未决/不可逆决策账本
      budget.json       五轨预算树 + claim
      checks.json       物理/机械检查结果
      crosscheck.json   交叉核对 + 2D↔3D 一致性
      export.json       制造交接结果
      sources/          原始导入件（KiCad/BOM/STEP 等）

写入是**原子**的（先写 ``.tmp`` 再 replace），避免面板读到半个文件。
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from .model import DesignModel
from .workspace import workspace_root

ARTIFACTS = (
    "design.json",
    "trace.json",
    "deferred.json",
    "budget.json",
    "checks.json",
    "ladder.json",
    "claims.json",
    "justify.json",
    "coverify.json",
    "units.json",
    "links.json",
    "renode.json",
    "joints.json",
    "sim.json",
    "handoff.json",
    "handoff.md",
    "crosscheck.json",
    "export.json",
    "emit.json",
    "emit_readback.json",
    "parts_cache.json",
    "pinmap.json",
    "pinmap_crosscheck.json",
    "calibration.json",
    "sim_params/initial.json",
    "agent_journal.jsonl",
    "req_draft.json",
    "req.json",
    "firmware_matrix.json",
    "joints.proposed.json",
    "model.xml",
    "scaffold.json",
    "open_items.md",
    "mech.json",
    "plan/index.json",
    "spec/index.json",
    "spec.md",
    "interface.json",
    "fw_build.json",
    "interface.md",
    "params.json",
    "params.md",
    "geom.json",
    "fab.json",
    "sch_drc.json",
    "panel.json",
)


def project_root() -> Path:
    """工程根 = 当前工作区。**每次调用都重新解析**，切换工作区立即生效。"""
    return workspace_root()


def project_dir(project_id: str) -> Path:
    safe = "".join(c for c in project_id if c.isalnum() or c in "-_.").strip()
    if not safe or safe in (".", ".."):
        raise ValueError(f"非法工程 id：{project_id!r}")
    return project_root() / safe


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


class DesignStore:
    """读写一个工程目录。所有路径都在 ``project/`` 之下。"""

    def __init__(self, project_id: str):
        self.id = project_id
        self.dir = project_dir(project_id)

    # -- files ------------------------------------------------------------

    def path(self, name: str) -> Path:
        return self.dir / name

    def exists(self) -> bool:
        return self.path("design.json").is_file()

    def _read_json(self, name: str) -> Any | None:
        path = self.path(name)
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def _write_json(self, name: str, payload: Any) -> Path:
        path = self.path(name)
        _atomic_write(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        return path

    # -- design -----------------------------------------------------------

    def load(self) -> DesignModel:
        data = self._read_json("design.json")
        if data is None:
            raise FileNotFoundError(f"工程 {self.id} 没有 design.json")
        return DesignModel.from_dict(data)

    def save(self, design: DesignModel) -> Path:
        design.id = self.id
        return self._write_json("design.json", design.to_dict())

    # -- artifacts --------------------------------------------------------

    def read(self, name: str) -> Any | None:
        return self._read_json(name)

    def write(self, name: str, payload: Any) -> Path:
        return self._write_json(name, payload)

    def write_text(self, name: str, text: str) -> Path:
        """写**非 JSON** 产物（如 `handoff.md`）。同样原子。"""
        path = self.path(name)
        _atomic_write(path, text if text.endswith("\n") else text + "\n")
        return path

    def read_text(self, name: str) -> str:
        path = self.path(name)
        if not path.is_file():
            return ""
        return path.read_text(encoding="utf-8")

    def artifact_paths(self) -> dict[str, bool]:
        return {name: self.path(name).is_file() for name in ARTIFACTS}

    def delete(self) -> None:
        if self.dir.is_dir():
            shutil.rmtree(self.dir)

    def sources_dir(self) -> Path:
        d = self.dir / "sources"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def summary(self) -> dict[str, Any]:
        """给文件树/工程切换器的一行摘要；不读 design.json 之外的文件。"""
        out: dict[str, Any] = {
            "id": self.id,
            "path": str(self.dir),
            "has_design": self.exists(),
            "artifacts": self.artifact_paths(),
        }
        if self.exists():
            try:
                design = self.load()
                out.update({
                    "title": design.title,
                    "board": design.board.kind,
                    "board_name": design.board.name,
                    "parts": len(design.parts),
                    "nets": len(design.nets),
                })
            except Exception as exc:  # noqa: BLE001
                out["error"] = f"{type(exc).__name__}: {exc}"
        return out


def list_projects() -> list[dict[str, Any]]:
    root = project_root()
    if not root.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for entry in sorted(root.iterdir()):
        if entry.is_dir() and (entry / "design.json").is_file():
            out.append(DesignStore(entry.name).summary())
    return out
