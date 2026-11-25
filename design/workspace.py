"""工作区根：**工程不再住在本插件目录里**。

为什么必须改：工程原先写死在 ``dsh_embagent/project/``——那是插件安装目录，
既不该承载用户数据（升级/重装会覆盖、npm 打包会连带），也没法让用户
"选一个文件夹当工作区"。

三种根，按优先级（**都是绝对路径，都要存在才采用**）：

1. ``HWV_WORKSPACE`` 环境变量 —— 给脚本/CI/多工作区切换用；
2. ``~/.dsh/hw_velxio.json`` 的 ``workspace`` —— 客户端"选择文件夹"写这里；
3. 兜底 ``~/.dsh/hw_velxio/workspace/`` —— 没有选择时的默认工作区。

工程目录布局（**工作区是用户资产，与本插件的代码无关**）::

    <workspace>/
      001-blink/
        design.json      规范模型（唯一真值）
        trace.json       过程轨迹（append-only 哈希链）
        ...
        sources/         原始导入件（KiCad/BOM/STEP 等）
        fab/             制造包
"""

from __future__ import annotations

import json
import os
from pathlib import Path

CONFIG_NAME = "hw_velxio.json"


def config_path() -> Path:
    """``~/.dsh/hw_velxio.json``（与 hwforge 的配置同目录、同形）。"""
    home = os.environ.get("USERPROFILE") or os.environ.get("HOME") or str(Path.home())
    return Path(home) / ".dsh" / CONFIG_NAME


def load_config() -> dict:
    """读配置。坏 JSON 当作没有配置——**不让一个语法错误锁死整个工作台**。"""
    try:
        data = json.loads(config_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def save_config(patch: dict) -> dict:
    """合并写入配置（保留未知键，原子的 replace）。"""
    current = load_config()
    current.update({k: v for k, v in patch.items() if v is not None})
    target = config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    os.replace(tmp, target)
    return current


def default_workspace() -> Path:
    home = os.environ.get("USERPROFILE") or os.environ.get("HOME") or str(Path.home())
    return Path(home) / ".dsh" / "dsh_embagent" / "workspace"


def workspace_root() -> Path:
    """当前工作区根（不创建；调用方决定是否 mkdir）。"""
    env = os.environ.get("HWV_WORKSPACE", "").strip()
    if env:
        return Path(env).expanduser()
    configured = load_config().get("workspace")
    if isinstance(configured, str) and configured.strip():
        return Path(configured.strip()).expanduser()
    return default_workspace()


def set_workspace(path: str) -> dict:
    """切换工作区并持久化。返回配置快照。"""
    target = Path(path).expanduser()
    if not target.is_absolute():
        raise ValueError(f"工作区必须是绝对路径：{path!r}")
    if not target.is_dir():
        raise ValueError(f"工作区不存在或不是目录：{target}")
    return save_config({"workspace": str(target)})


def plugin_root() -> Path:
    """``dsh_embagent/`` 源码目录本身（只读用途：示例、脚本）。**不写用户数据**。"""
    return Path(__file__).resolve().parent.parent


def examples_root() -> Path:
    """随插件分发的示例件（只读）。"""
    return plugin_root() / "examples"
