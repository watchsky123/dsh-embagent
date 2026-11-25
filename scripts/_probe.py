"""真实工程探针的路径解析 —— 开源仓库里**不写死开发者机器上的目录**。

这些 check 脚本会探测"真实工程"（使用者自己的 EasyEDA / CubeMX / 固件目录），
用来证明解析器在脏数据上不崩。路径属于个人资产，不能进公开仓库，所以改成环境变量：

    DSH_EMBAGENT_PROBE_WORKSPACE   工作区目录（*.epro2 / *.ioc / *.bin 都在这里）
    DSH_EMBAGENT_PROBE_PROJECT     单个工程目录（含 design.json / emit.json）
    DSH_EMBAGENT_PROBE_DESIGN      单个 design.json
    DSH_EMBAGENT_PROBE_IOC         单个 .ioc
    DSH_EMBAGENT_PROBE_FIRMWARE    固件根目录
    DSH_EMBAGENT_PROBE_EPRO2       单个 .epro2
    DSH_EMBAGENT_PROBE_SIM_PARAMS  RoboBuilder 的 initial.json

**没设就给一个必然不存在的路径。** 调用处都有 `is_file()` / `is_dir()` 守卫，
于是自动落到 `[skip] … 这一节没跑，不是通过`——这比"用示例文件冒充真实工程"诚实。
"""

from __future__ import annotations

import os
from pathlib import Path

#: 根目录名里带 noneхistent 三个字，就是为了在日志里一眼看出"没设探针"。
_MISSING_ROOT = Path("/nonexistent/dsh-embagent-probe")


def path(env: str, *parts: str) -> Path:
    """读一个探针路径；没设环境变量时返回指向不存在位置的路径。"""
    raw = os.environ.get(env, "").strip()
    if raw:
        base = Path(raw)
    else:
        base = _MISSING_ROOT / env.removeprefix("DSH_EMBAGENT_PROBE_").lower()
    return base.joinpath(*parts) if parts else base


def project_id() -> str:
    """真实工程探针的**工程 id**（`DSH_EMBAGENT_PROBE_PROJECT_NAME`）。

    没有就给空串：调用方据此 `skip`，而不是拿一个假名字去 `DesignStore(...)`
    ——那会抛 `FileNotFoundError`，看起来像"检查坏了"。
    """
    return os.environ.get("DSH_EMBAGENT_PROBE_PROJECT_NAME", "").strip()
