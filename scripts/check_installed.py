#!/usr/bin/env python3
"""安装自检：证明 dsh_embagent **被 profile 接受并会持续挂载**。

    python dsh_embagent/scripts/check_installed.py

(1)「每次重启都要重新启用」的真正原因有两类，都不是"重启会丢配置"：

  A. bundle 补丁语法不对 → loader 解析失败 → 整层被**静默跳过**
     （`skippedBundles`，只在启动 stderr 打一行）。第一版用的是
     `kind: plugin / entry:` —— 那不是 loader 语法，于是"插件装了但什么
     都没发生"，看起来就像需要重新启用。
  B. 插件依赖与宿主代次不匹配 → `failed to import: ... does not provide an
     export named 'X'`。只跑一次 pnpm install 就会复现，与重启无关。

所以本脚本检查的是**这四个会真实出错的环节**，而不是"文件存在"：

  1. profile manifest: `dsh.profile.bundles` 里确实有本包；
  2. profile manifest: 依赖规格与 lockfile 一致（漂移会让安装结果与声明不符）；
  3. `dsh --dump-config` 的组合树里有本包的行 —— 这是 loader **真正**会加载
     的东西，也是 patch 语法正确与否的唯一权威判定；
  4. 该次组合没有把本包列进 skipped（stderr 里不含本包名）。
  5. **运行中的宿主不早于当前产物** —— 少见但会把前四项全部骗过：

        改了 `src/service.ts` → `npm run build` → `dist` 是新的
        但宿主进程还是昨天启动的，Node 的 ESM 模块表里躺着**旧代码**
        → 界面报 `gateway/signature-invalid: hwvelxio/snapshot ...`
        而 1–4 全绿、`check_remote_signature.mjs` 也全绿（它读 dist，是对的）

     这条**不能**用"文件存在/时间戳单调"代替：必须比较宿主的**进程启动时间**
     与产物 mtime。没有正在运行的宿主时跳过（CI 里本来就没有）。
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

PACKAGE = "dsh-embagent"


def dsh_bin() -> Path:
    home = Path.home()
    return home / ".ai-manager" / "bin" / "dsh.cmd"


def profile_dir(name: str = "desktop") -> Path:
    return Path.home() / ".dsh" / "profiles" / name


FAILURES: list[str] = []


def _fmt(epoch: float) -> str:
    import datetime
    return datetime.datetime.fromtimestamp(epoch).strftime("%H:%M:%S")


def _host_process() -> tuple[int, float] | None:
    """正在运行的 DSH 桌面宿主：返回 (pid, 启动时间 epoch 秒)。

    用 PowerShell 的 `Get-CimInstance Win32_Process` 拿 `CreationDate`——
    这是**进程**启动时间，正是我们需要的量；文件时间戳说的是另一件事。
    找不到（非 Windows / 没在跑）返回 None，调用方跳过。
    """
    script = (
        "Get-CimInstance Win32_Process -Filter \"Name='DeepSeek Harness.exe'\" "
        "| Where-Object { $_.CommandLine -like '*dsh-desktop-host*' } "
        "| Select-Object -First 1 ProcessId,CreationDate "
        "| ForEach-Object { $_.ProcessId.ToString() + ' ' + "
        "$_.CreationDate.ToString('yyyy-MM-dd HH:mm:ss') }"
    )
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True, encoding="utf-8", errors="replace", timeout=40,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    line = (proc.stdout or "").strip()
    if line == "":
        return None
    parts = line.split(None, 1)
    if len(parts) != 2 or not parts[0].isdigit():
        return None
    try:
        import datetime
        started = datetime.datetime.strptime(parts[1].strip(), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return int(parts[0]), started.timestamp()


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name}{(' — ' + detail) if detail else ''}")
        FAILURES.append(name)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    profile = profile_dir()
    print(f"profile: {profile}\n")

    if not profile.is_dir():
        print(f"❌ profile 不存在：{profile}")
        return 1

    manifest = json.loads((profile / "package.json").read_text(encoding="utf-8"))
    bundles = manifest.get("dsh", {}).get("profile", {}).get("bundles", [])
    deps = manifest.get("dependencies", {})

    print("[1] manifest 声明")
    check("bundles 里有本包", PACKAGE in bundles, str(bundles[-3:]))
    check("dependencies 里有本包", PACKAGE in deps, str(deps.get(PACKAGE)))

    print("[2] 依赖规格与 lockfile 一致")
    lock = (profile / "pnpm-lock.yaml").read_text(encoding="utf-8", errors="replace")
    declared = deps.get(PACKAGE, "")
    block = re.search(
        r"\n      " + re.escape(PACKAGE) + r":\n((?:        .*\n)+)", lock)
    if block is None:
        check("lockfile 含本包条目", False, "缺条目，需重跑 pnpm install")
    else:
        spec = re.search(r"specifier:\s*(\S+)", block.group(1))
        check("lockfile 含本包条目", True)
        check("lockfile specifier 与 manifest 一致",
              spec is not None and spec.group(1).strip() == declared.strip(),
              f"manifest={declared!r} lock={spec.group(1) if spec else None!r}")
        # pnpm 把 file: 目录依赖归一成 link: —— 这是正常的，只要解析到同一路径。
        version = re.search(r"version:\s*(\S+)", block.group(1))
        resolved = version.group(1) if version else ""
        target = declared.split(":", 1)[-1].strip()
        check("解析目标与声明路径相同",
              resolved.split(":", 1)[-1].strip().replace("\\", "/").lower()
              == target.replace("\\", "/").lower(),
              f"lock={resolved} declared={declared}")

    print("[3] 组合树（loader 真正加载的东西）")
    cli = dsh_bin()
    if not cli.is_file():
        print(f"  skip 找不到 dsh 启动器：{cli}")
    else:
        proc = subprocess.run(
            [str(cli), "--profile", "desktop", "--dump-config"],
            capture_output=True, encoding="utf-8", errors="replace", timeout=180,
        )
        tree = proc.stdout
        # 组合树用 `# == <package>` 标注各 bundle 的层
        in_tree = re.search(r"#\s*==\s*" + re.escape(PACKAGE), tree) is not None
        row = re.search(r"- id:\s*embagent\s*\n\s*name:\s*" + re.escape(PACKAGE),
                        tree) is not None
        check("组合树含本包层", in_tree, "patch 未被 loader 接受（语法/路径问题）")
        check("组合树含插件行 id/name", row)
        # skipped 只会出现在 stderr；用包名判定而不是猜措辞
        skipped = PACKAGE in (proc.stderr or "") and "skip" in (proc.stderr or "").lower()
        check("本包未被 skipped", not skipped,
              (proc.stderr or "").strip().splitlines()[-1] if proc.stderr else "")

    print("[4] 已安装目标可加载")
    target = profile / "node_modules" / PACKAGE
    check("profile 里有安装目标", target.exists(), str(target))
    for rel in ("dist/index.js", "dist/client.js", "cordis.patch.yml"):
        check(f"{rel} 存在", (target / rel).is_file())
    if (target / "cordis.patch.yml").is_file():
        raw = (target / "cordis.patch.yml").read_text(encoding="utf-8")
        # **先剥注释再判定**：本文件的注释里就写着"早期的 kind: plugin 不是
        # loader 语法"，不剥掉会把说明文字当成内容，报一个假失败。
        body = "\n".join(
            line for line in raw.splitlines()
            if not line.lstrip().startswith("#"))
        check("bundle 补丁是 loader 语法（insert 列表）",
              "- insert:" in body and "kind: plugin" not in body
              and re.search(r"- insert:\s*\n\s*-\s+id:\s*\S+\s*\n\s*name:\s*\S+", body)
              is not None,
              body.strip()[:200] or "（补丁正文为空）")

    print("[5] 运行中的宿主不早于当前产物")
    host = _host_process()
    if host is None:
        print("  skip 没有正在运行的 DSH 宿主（CI / 命令行场景）")
    else:
        pid, started = host
        newest = max((target / rel).stat().st_mtime
                     for rel in ("dist/index.js", "dist/service.js", "dist/client.js")
                     if (target / rel).is_file())
        print(f"  宿主 pid={pid} 启动于 {_fmt(started)}；"
              f"产物最新 mtime {_fmt(newest)}")
        check("宿主加载的是当前产物（不是重建前的旧代码）",
              started >= newest,
              f"宿主启动比产物早 {(newest - started) / 60:.0f} 分钟 —— "
              "改过 src 后必须**重启 DSH**，刷新页面只能换客户端 bundle，"
              "宿主侧的 ESM 模块表在进程生命周期内不会重载")

    print()
    if FAILURES:
        print(f"❌ {len(FAILURES)} 项失败：{', '.join(FAILURES)}")
        print("修复后重启 DSH；若改了 patch，restart 是唯一可靠的方式"
              "（HMR 会重组合，但 patch 语法错误只在启动 stderr 里报）。")
        return 1
    print("✅ 本包会被持续挂载（manifest / lockfile / 组合树 / 产物 四者一致）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
