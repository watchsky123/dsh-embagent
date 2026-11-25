"""立创EDA专业版（``lceda-pro``）的会话封装 —— **只做通道，不做判定**。

这里只回答三件事：能不能连上、这条命令跑没跑通、它说的版本是什么。
「设计对不对」的判定仍然只有一份（`ladder` / `handoff`）；立创的 DRC 属于
**外部证据**，进 `handoff` 的 `external` 类，不混进 ladder。

## 为什么每个操作都重新探测

立创自己的 `doc api` 帮助里写着 API 仍在演进。实测 525 个类型 / 绝大多数方法
标 `beta` —— 硬编码签名等于换一版就崩，而且崩在业务深处（已经创建了一半工程）。
所以这里的能力表（`CAPABILITIES`）是**运行时可查**的：缺哪个方法就报
``not_ready`` 并点名，而不是猜。

## 实测硬事实（2026-11-21，本机 JLCEDA Pro 4.1.97.04b092ac）

这条清单不是推测，每一条都有对照实验；改代码前先读它。

F1. **stdout 是 JSON，stderr 是人话。** 失败时 stdout 是
    ``{"ok":false,"error":{"code","message"}}``，stderr 还会再打一行
    ``CODE: message``。两者**不能合并**，合并后 ``json.loads`` 会抛
    ``Extra data: line 3``。成功时信封为
    ``{"ok":true,"value":…,"logs":[...],"durationMs":N}``。

F2. **``ok:true`` 只代表代码跑通，不代表操作成功。** 这是立创文档原话，
    实测也如此：``sch_SourcePrimitiveComponent.add`` 缺 ``libraryUuid`` 时
    CLI 层 ``ok:true``，业务层是 ``{ok:false,error:{code:"PARAM_REQUIRED"}}``。
    所以本模块的 ``invoke`` 默认检查业务信封（``require_ok``）。

F3. **单位**：原理图 1 单位 = 10 mil（0.254 mm），PCB 1 单位 = 1 mil
    （0.0254 mm）。``SYS_Unit`` 的 remarks 原话。mm→mil 换算见 ``MM_TO_MIL``。

F4. **``createProject`` 自己会建 Board1/Schematic1/PCB1**，而且自带的
    文档说「不初始化任何文档」——**文档错了，实测为准**。另外
    ``createBoard`` 在没 ``openProject`` 时返回 ``undefined``。
    就绪还必须轮询：``data[0].schematic`` 先出现，``data[0].pcb`` 后出现，
    只看 schematic 会拿到 ``pcb === null``。

F5. **系统库 uuid 要单独取。** ``lib_Device.searchByProperties`` 的结果里
    没有 ``libraryUuid``（文档示例也提示了），必须
    ``lib_LibrariesList.getSystemLibraryUuid()``。器件在库内的唯一定位是
    ``(libraryUuid, name)``，不是 uuid —— 器件装进工程后 symbol/footprint
    的 uuid 会**重新生成**，缓存 uuid 一定错。

F6. **``getDocumentReadyState()`` 不可用。** 文档已经能读图元、跑 DRC、
    导出网表时，它仍然返回 ``"notLoaded"``。拿它判就绪会永远卡住；
    真正的就绪信号是「操作本身读到了东西」，所以这里用**轮询业务结果**
    而不是轮询状态位。

F7. **``saveFileToFileSystem`` 默认被拒。** 报
    「未启用扩展和独立脚本的外部交互权限」。生成 Gerber 是好的
    （内存里有 zip），落盘要人先去设置里开权限。这是 ``not_ready`` 闸门，
    不是失败。

F8. **自动布线可用；曾经布不通，真因是我自己的板框坐标写反了。**
    曾经记过两版错误结论：「引擎拒绝执行」和「headless 没有布线的执行
    环境」。两次都是把同一块板布出 0 段当成环境问题。实测链条：

    * 用户登录态 GUI 窗口（``origin=user``）里同一块板也是
      ``successNetsCount: 0 / failedNets: [全部]`` —— 与窗口来源、登录态无关
    * 板框 ``R`` 模式源数据是 ``['R', 0, 0, 1968.5, 1574.8, 0, 0]``，
      而 ``R`` 的 ``(x, y)`` 是**左上角**、矩形向 **−Y** 延伸（手册 §19）
      ⇒ 板框落在 ``y∈[-1575, 0]``，元件在 ``y>0``，**整块板在板框外**
    * 只把左上角 y 改成 ``+height``（覆盖 ``y∈[0, 1575]``）后，同一块板
      立刻 ``successNetsCount: 2/2``、盘上 9 段 45° 单层走线、0 过孔、
      DRC ``[]``；关会话重开后元件/板框/走线全在

    另外两条实测：``autoRouting`` 的 ``success`` 只表示「已启动」，
    ``successNetsCount`` 只描述**这一次调用**做了什么（布完再调一次就是
    0/N，而走线一段没少）——所以判定必须量几何，不能读这个字段。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

__all__ = [
    "CAPABILITIES",
    "CliError",
    "LcedaError",
    "LcedaSession",
    "MM_TO_MIL",
    "PCB_UNIT_MM",
    "SCH_UNIT_MM",
    "find_exe",
    "mm_to_mil",
    "probe",
    "run_cli",
    "version_of",
]

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

#: 环境变量优先（与 `HWV_MUJOCO` 同一套约定）。
EXE_ENV = "HWV_LCEDA"

#: 本机实测路径。**不是必须**——`find_exe` 按顺序找，第一个存在的就用。
DEFAULT_EXES = (
    r"D:\IE\ES\PCB\lceda-pro\lceda-pro.exe",
    r"C:\Program Files\LCEDA-Pro\lceda-pro.exe",
    r"C:\Program Files\lceda-pro\lceda-pro.exe",
)

#: F3：原理图数据单位（mm）。
SCH_UNIT_MM = 0.254
#: F3：PCB 数据单位（mm）。
PCB_UNIT_MM = 0.0254
#: 1 mm = 39.3700787 mil。
MM_TO_MIL = 1.0 / PCB_UNIT_MM

#: 默认单次 invoke 预算（毫秒）。立创 CLI 自己默认 60s。
DEFAULT_TIMEOUT_MS = 120_000

#: 每条命令的名字里出现这些字样就当「会话相关命令」，不再重复探测 exe。
_SESSION_COMMANDS = ("session", "invoke", "doc", "functions")

#: 各段要求存在的方法。缺哪个 → `not_ready` 并点名，不猜。
#:
#: 类名 → 方法名，都是 `doc api --class-name <类> --method-name <方法>`
#: 能查到的（所以 `probe` 里 `missing` 非空就是真的缺，不是写错）。
CAPABILITIES: dict[str, tuple[tuple[str, str], ...]] = {
    "cli": (("DMT_Project", "createProject"),
            ("DMT_Project", "openProject"),
            ("DMT_Project", "getCurrentProjectInfo"),
            ("DMT_Board", "createBoard"),
            ("DMT_EditorControl", "openDocument")),
    "schematic": (("SCH_SourcePrimitiveComponent", "add"),
                  ("SCH_SourcePrimitiveComponent", "get"),
                  ("SCH_SourcePrimitiveWire", "add"),
                  ("SCH_SourcePrimitiveAttribute", "addNetLabel"),
                  ("LIB_Device", "searchByProperties"),
                  ("LIB_LibrariesList", "getSystemLibraryUuid")),
    "pcb": (("PCB_Document", "importChanges"),
            ("PCB_PrimitiveComponent", "getAll"),
            ("PCB_PrimitiveComponent", "modify"),
            ("PCB_PrimitivePolyline", "create"),
            ("PCB_MathPolygon", "createPolygon"),
            ("PCB_Drc", "check"),
            ("PCB_Net", "getNetlist")),
    "route": (("PCB_Document", "autoRouting"),),
    "outputs": (("PCB_ManufactureData", "getGerberFile"),
                ("PCB_ManufactureData", "getBomFile"),
                ("SYS_FileSystem", "saveFileToFileSystem")),
}


class CliError(RuntimeError):
    """CLI 本身没跑起来（找不到 exe / 非 JSON 输出 / 超时）。"""


class LcedaError(RuntimeError):
    """CLI 跑通了，但业务结果不成立（F2）。"""


# ---------------------------------------------------------------------------
# 进程层
# ---------------------------------------------------------------------------


def find_exe() -> str:
    """按 环境变量 → 常见安装路径 → PATH 的顺序找可执行文件。

    找不到返回空串（调用方据此报 ``not_ready``，**不抛异常**）——
    没装立创不是错误，是前置缺失。
    """
    env = os.environ.get(EXE_ENV, "").strip()
    if env:
        # 给了就认，即使不存在也如实返回，让人看见自己给错了
        return env
    for candidate in DEFAULT_EXES:
        if Path(candidate).exists():
            return candidate
    found = shutil.which("lceda-pro")
    return found or ""


def _parse_envelope(text: str) -> dict[str, Any]:
    """从 stdout 里取**第一个** JSON 对象（F1：不能把 stderr 拼进来）。"""
    stripped = text.strip()
    if not stripped:
        raise CliError("没有任何输出")
    decoder = json.JSONDecoder()
    start = stripped.find("{")
    if start < 0:
        raise CliError(f"输出不是 JSON：{stripped[:200]}")
    try:
        obj, _end = decoder.raw_decode(stripped[start:])
    except json.JSONDecodeError as exc:
        raise CliError(f"输出不是合法 JSON（{exc}）：{stripped[:200]}") from exc
    if not isinstance(obj, dict):
        raise CliError(f"输出不是 JSON 对象：{str(obj)[:200]}")
    return obj


def run_cli(
    exe: str,
    argv: Sequence[str],
    *,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
    stdin_text: str | None = None,
) -> dict[str, Any]:
    """跑一条 ``lceda-pro`` 命令，返回信封。

    **只解析 stdout**：立创失败时 stderr 会多打一行 ``CODE: message``，
    合并进来就是 ``Extra data``（F1）。
    """
    if not exe:
        raise CliError("没有 lceda-pro 可执行文件（设 HWV_LCEDA 或装立创EDA专业版）")
    if not Path(exe).exists() and shutil.which(exe) is None:
        raise CliError(f"lceda-pro 不存在：{exe}")
    cmd = [exe, *argv]
    try:
        proc = subprocess.run(  # noqa: S603 - 路径来自 find_exe，不是用户输入拼接
            cmd,
            input=(stdin_text.encode("utf-8") if stdin_text is not None else None),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=max(1, timeout_ms) / 1000.0,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise CliError(f"命令超时（{timeout_ms}ms）：{' '.join(argv[:3])}…") from exc
    except OSError as exc:
        raise CliError(f"无法启动 lceda-pro：{exc}") from exc
    out = proc.stdout.decode("utf-8", errors="replace")
    err = proc.stderr.decode("utf-8", errors="replace")
    try:
        envelope = _parse_envelope(out)
    except CliError as exc:
        # 把 stderr 留在异常里供诊断，但**不参与解析**
        raise CliError(f"{exc}（stderr：{err.strip()[:200]}）") from exc
    envelope["_stderr"] = err.strip()
    return envelope


# ---------------------------------------------------------------------------
# 会话
# ---------------------------------------------------------------------------


@dataclass
class LcedaSession:
    """一个 ``lceda-pro`` 会话。``exe`` 为空表示没装（一切调用都 ``not_ready``）。"""

    exe: str = field(default_factory=find_exe)
    session_id: str = ""
    version: str = ""
    headless: bool = True
    project_path: str = ""
    project_uuid: str = ""
    project_name: str = ""
    #: 探测到的能力（类 → 方法集合）。`probe_capabilities()` 填。
    capabilities: dict[str, set[str]] = field(default_factory=dict)

    # -- 生命周期 ---------------------------------------------------------

    @property
    def ready(self) -> bool:
        return bool(self.exe) and bool(self.session_id)

    def open(self, path: str = "", *, headless: bool = True) -> "LcedaSession":
        """开会话。``path`` 给 ``.eprj3`` 文件夹就顺带把工程挂上并**双向同步**。

        CLI 原话：``.eprj3`` 文件夹工程与编辑器保持双向同步——磁盘改动自动
        重载，编辑器保存自动落盘。这就是 ``outputs`` 段不依赖
        ``saveFileToFileSystem`` 的原因：**工程本身就在磁盘上**。
        """
        if not self.exe:
            raise CliError("没有 lceda-pro 可执行文件")
        self.version = self.version or version_of(self.exe)
        argv = ["open"]
        if path:
            argv += ["--path", str(path)]
        if headless:
            argv.append("--headless")
        envelope = run_cli(self.exe, argv, timeout_ms=DEFAULT_TIMEOUT_MS)
        if not envelope.get("ok"):
            raise CliError(f"open 失败：{(envelope.get('error') or {}).get('message')}")
        value = envelope.get("value") or {}
        self.session_id = str(value.get("sessionId") or "")
        self.headless = bool(value.get("headless", headless))
        self.project_path = str(value.get("path") or path or "")
        if not self.session_id:
            raise CliError("open 没有返回 sessionId")
        return self

    def close(self, *, destroy: bool = False) -> dict[str, Any]:
        if not self.session_id:
            return {"ok": False, "reason": "没有会话"}
        argv = ["session", "close", "--session", self.session_id]
        if destroy:
            argv.append("--destroy")
        try:
            return run_cli(self.exe, argv, timeout_ms=30_000)
        finally:
            self.session_id = ""

    @staticmethod
    def list_sessions(exe: str = "") -> list[dict[str, Any]]:
        exe = exe or find_exe()
        if not exe:
            return []
        envelope = run_cli(exe, ["session", "list"], timeout_ms=30_000)
        value = envelope.get("value") or []
        return value if isinstance(value, list) else []

    # -- 调用 -------------------------------------------------------------

    def invoke(
        self,
        code: str,
        *,
        require_ok: bool = True,
        expect: Iterable[str] = (),
        check: Callable[[Any], Any] | bool | None = None,
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
        label: str = "",
    ) -> Any:
        """跑一段 JS（``async function`` 体），返回它的返回值。

        **三重自校验**，缺一不可（F2）：

        1. ``require_ok``：返回值是 ``{ok:false,error}`` 信封时抛 ``LcedaError``。
           —— ``ok:true`` 只代表代码跑通。
        2. ``expect``：这些子串必须出现在返回值里（序列化后比对）。
        3. ``check``：自定义判定。返回 ``False`` / 报错字符串即失败。

        ``code`` 走 ``--code-file -``（标准输入）而不是 ``--code``：
        Windows 命令行有 ~32KB 上限，一个正经的发射脚本轻松超过。
        """
        if not self.session_id:
            raise LcedaError(f"{label or 'invoke'}：没有会话")
        argv = ["invoke", "--session", self.session_id,
                "--code-file", "-", "--timeout", str(int(timeout_ms))]
        envelope = run_cli(self.exe, argv, timeout_ms=timeout_ms + 15_000,
                           stdin_text=code)
        where = label or "invoke"
        if not envelope.get("ok"):
            error = envelope.get("error") or {}
            raise LcedaError(
                f"{where}：{(error.get('code') or '?')} "
                f"{(error.get('message') or '')}".strip())
        value = envelope.get("value")
        if require_ok and isinstance(value, dict) and "ok" in value:
            if value.get("ok") is not True:
                error = value.get("error") or {}
                raise LcedaError(
                    f"{where}：业务失败 {(error.get('code') or '?')} "
                    f"{(error.get('message') or '')}".strip())
        blob = json.dumps(value, ensure_ascii=False, default=str)
        for token in expect:
            if token not in blob:
                raise LcedaError(f"{where}：返回值里没有 {token!r}")
        if callable(check):
            verdict = check(value)
            if verdict is not True:
                reason = verdict if isinstance(verdict, str) else "自定义检查未通过"
                raise LcedaError(f"{where}：{reason}")
        elif check is False:
            raise LcedaError(f"{where}：检查为假")
        return value

    def doc(self, kind: str, class_name: str = "", method_name: str = "") -> dict[str, Any]:
        """查编辑器 API 参考。``kind`` ∈ ``api`` / ``format`` / ``external``。

        **运行时探测，不硬编码签名**——立创的 API 还在演进（多数标 beta）。
        """
        argv = ["doc", kind]
        if class_name:
            argv += ["--class-name", class_name]
        if method_name:
            argv += ["--method-name", method_name]
        envelope = run_cli(self.exe, argv, timeout_ms=30_000)
        if not envelope.get("ok"):
            raise CliError(f"doc {kind} 失败：{(envelope.get('error') or {}).get('message')}")
        return envelope.get("value") or {}

    # -- 能力探测 ---------------------------------------------------------

    def probe_capabilities(self, jobs: Iterable[str] = ()) -> dict[str, Any]:
        """逐条核对 ``CAPABILITIES`` 里的方法是否真的存在。

        返回 ``{ok, checked, missing, jobs}``。``missing`` 非空就是**真的缺**
        （类名/方法名来自 ``doc api`` 的输出，写错了也会落到这里）。
        """
        wanted = list(jobs) or list(CAPABILITIES)
        cache: dict[str, set[str]] = {}
        checked = 0
        missing: list[str] = []
        for job in wanted:
            for class_name, method_name in CAPABILITIES.get(job, ()):
                if class_name not in cache:
                    try:
                        info = self.doc("api", class_name)
                    except CliError as exc:
                        missing.append(f"{class_name}.{method_name}（{exc}）")
                        cache[class_name] = set()
                        continue
                    cache[class_name] = {
                        str(m.get("name")) for m in (info.get("methods") or [])
                    }
                checked += 1
                if method_name not in cache[class_name]:
                    missing.append(f"{class_name}.{method_name}")
        self.capabilities = cache
        return {"ok": not missing, "checked": checked, "missing": missing,
                "jobs": wanted}

    # -- 工程 -------------------------------------------------------------

    def open_project(self, friendly_name: str, *, timeout_ms: int = 60_000) -> dict[str, Any]:
        """建（或接管）一个工程并等它**真的就绪**。

        就绪判定见 F4：必须等到 ``data[0].schematic.page`` **和**
        ``data[0].pcb`` 同时出现。只看 schematic 会拿到 ``pcb === null``
        （实测崩溃点：``Cannot read properties of null (reading 'uuid')``）。
        """
        script = _JS_PROJECT % {"name": json.dumps(friendly_name)}
        value = self.invoke(
            script,
            expect=("projectUuid", "boards"),
            timeout_ms=timeout_ms,
            label="project",
        )
        self.project_uuid = str(value.get("projectUuid") or "")
        self.project_name = str(value.get("projectName") or friendly_name)
        self.project_path = str(value.get("projectPath") or "")
        return value

    def current_project(self) -> dict[str, Any]:
        """读当前工程结构（板/原理图/页/PCB）。"""
        return self.invoke(_JS_CURRENT, timeout_ms=30_000, label="current")

    def wait_project_ready(self, timeout_ms: int = 15_000) -> dict[str, Any]:
        """等工程可读，返回 `{projectUuid, friendlyName, boards}`。

        `open` 只保证会话存在，**不保证工程装好了**。所有要读工程的调用
        前面都得过这一步；跳过它就会在 `getCurrentProjectInfo()` 上拿到
        `undefined`。
        """
        tries = max(1, int(timeout_ms / 250))
        script = _JS_READY.replace("__TRIES__", str(tries))
        value = self.invoke(script, expect=("projectUuid", "boards"),
                            timeout_ms=timeout_ms + 30_000, label="ready")
        self.project_uuid = str(value.get("projectUuid") or "")
        self.project_name = str(value.get("friendlyName") or "")
        return value

    def projects_root(self) -> str:
        """工程根目录（`sys_FileSystem.getProjectsPaths()[0]`）。

        `getAllProjectsUuid()` 在**工程未打开时返回空数组**（实测 n=0），
        所以「同名工程已存在」只能靠磁盘判断，不能靠这个 API。
        """
        try:
            value = self.invoke(
                "const roots = await eda.sys_FileSystem.getProjectsPaths();"
                "return {ok: true, roots: roots || []};",
                expect=("roots",), timeout_ms=30_000, label="projects_root")
        except LcedaError:
            return ""
        roots = value.get("roots") or []
        return str(roots[0]) if roots else ""


def version_of(exe: str) -> str:
    """从 ``--help`` 的第一行抓版本（``JLCEDA Pro 4.1.97.04b092ac``）。"""
    try:
        proc = subprocess.run([exe, "--help"], stdout=subprocess.PIPE,  # noqa: S603
                              stderr=subprocess.DEVNULL, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    for line in proc.stdout.decode("utf-8", errors="replace").splitlines():
        line = line.strip()
        if line:
            return line
    return ""


# ---------------------------------------------------------------------------
# 探测
# ---------------------------------------------------------------------------


def probe(jobs: Iterable[str] = (), *, exe: str = "") -> dict[str, Any]:
    """一键探测：exe / 版本 / 关键类与方法 / 会话数。

    返回 ``status`` ∈ ``ok`` / ``not_ready``。**不抛异常**——
    没装立创是前置缺失，不是 bug。
    """
    exe = exe or find_exe()
    if not exe:
        return {
            "status": "not_ready",
            "reason": "没有 lceda-pro 可执行文件",
            "how": f"设环境变量 {EXE_ENV} 指向 lceda-pro.exe，或安装立创EDA专业版",
            "exe": "",
            "version": "",
            "missing": ["lceda-pro"],
        }
    if not Path(exe).exists() and shutil.which(exe) is None:
        return {
            "status": "not_ready",
            "reason": f"指到不存在的路径：{exe}",
            "how": f"检查 {EXE_ENV}",
            "exe": exe,
            "version": "",
            "missing": ["lceda-pro"],
        }
    version = version_of(exe)
    session = LcedaSession(exe=exe, version=version)
    try:
        session.open(headless=True)
    except CliError as exc:
        return {
            "status": "not_ready",
            "reason": f"开会话失败：{exc}",
            "exe": exe,
            "version": version,
            "missing": [],
        }
    try:
        caps = session.probe_capabilities(jobs)
    finally:
        session.close()
    return {
        "status": "ok" if caps["ok"] else "not_ready",
        "exe": exe,
        "version": version,
        "checked": caps["checked"],
        "missing": caps["missing"],
        "jobs": caps["jobs"],
        "note": "" if caps["ok"] else "缺的方法是真的缺（名字取自 doc api，不是猜的）",
    }


# ---------------------------------------------------------------------------
# JS 片段
# ---------------------------------------------------------------------------
#
# 这些片段刻意保持「无参数、只读」，参数由 emit_lceda 用 json.dumps 注入。
# 共同遵守两条：**轮询业务结果**（F6：状态位不可信）与
# **业务信封自校验**（F2：先看 r.ok 再取 r.value）。

#: 建工程 + 等就绪。``%(name)s`` 是已加引号的工程名。
_JS_PROJECT = r"""
const want = %(name)s;
const projectUuid = await eda.dmt_Project.createProject(
  want, undefined, undefined, undefined, 'dsh_embagent emit');
if (!projectUuid) throw new Error('createProject 返回 undefined');
const opened = await eda.dmt_Project.openProject(projectUuid);
if (!opened) throw new Error('openProject 返回 false');

// F4：schematic 先出现、pcb 后出现，必须两者都在才算就绪。
let info = null;
for (let i = 0; i < 40; i++) {
  await new Promise(r => setTimeout(r, 250));
  const cur = await eda.dmt_Project.getCurrentProjectInfo();
  const d0 = cur && cur.data && cur.data[0];
  if (d0 && d0.schematic && d0.schematic.page && d0.schematic.page.length
      && d0.pcb && d0.pcb.uuid) { info = cur; break; }
}
if (!info) throw new Error('工程 10 秒内没有就绪（schematic 或 pcb 缺失）');

// Panel 板也会出现在 data 里（实测多出 Panel1），所以按名字挑，不取 data[0]。
const boards = (info.data || []).map(b => ({
  name: b.name,
  schematicName: (b.schematic && b.schematic.name) || '',
  pages: (b.schematic && b.schematic.page || []).map(p => p.name),
  pageUuid: (b.schematic && b.schematic.page && b.schematic.page.length)
    ? b.schematic.page[0].uuid : '',
  schematicUuid: (b.schematic && b.schematic.uuid) || '',
  pcbName: (b.pcb && b.pcb.name) || '',
  pcbUuid: (b.pcb && b.pcb.uuid) || '',
}));
return {
  ok: true,
  projectUuid: projectUuid,
  projectName: want,
  friendlyName: info.friendlyName || want,
  boards: boards,
};
"""

#: 等工程真的可读。**每次开会话都要等**：`open` 返回 sessionId 不等于
#: 编辑器已经把工程装进内存——实测紧跟 `getCurrentProjectInfo()` 会拿到
#: `undefined`（`Cannot read properties of undefined (reading 'data')`）。
_JS_READY = r"""
for (let i = 0; i < __TRIES__; i++) {
  let cur = null;
  try { cur = await eda.dmt_Project.getCurrentProjectInfo(); } catch (e) { cur = null; }
  if (cur && cur.data && cur.data.length) {
    const boards = (cur.data || []).map(b => ({
      name: b.name,
      schematicName: (b.schematic && b.schematic.name) || '',
      schematicUuid: (b.schematic && b.schematic.uuid) || '',
      pages: ((b.schematic && b.schematic.page) || []).map(p => p.name),
      pageName: ((b.schematic && b.schematic.page) || []).length
        ? b.schematic.page[0].name : '',
      pageUuid: ((b.schematic && b.schematic.page) || []).length
        ? b.schematic.page[0].uuid : '',
      pcbName: (b.pcb && b.pcb.name) || '',
      pcbUuid: (b.pcb && b.pcb.uuid) || '',
    }));
    return {ok: true, projectUuid: cur.uuid, friendlyName: cur.friendlyName || '', boards: boards};
  }
  await new Promise(r => setTimeout(r, 250));
}
throw new Error('工程 15 秒内没有就绪（getCurrentProjectInfo 为空）');
"""

#: 读当前工程（不做任何写操作）。
_JS_CURRENT = r"""
const cur = await eda.dmt_Project.getCurrentProjectInfo();
if (!cur) throw new Error('没有打开的工程');
const boards = (cur.data || []).map(b => ({
  name: b.name,
  schematicName: (b.schematic && b.schematic.name) || '',
  pages: (b.schematic && b.schematic.page || []).map(p => p.name),
  pageUuid: (b.schematic && b.schematic.page && b.schematic.page.length)
    ? b.schematic.page[0].uuid : '',
  schematicUuid: (b.schematic && b.schematic.uuid) || '',
  pcbName: (b.pcb && b.pcb.name) || '',
  pcbUuid: (b.pcb && b.pcb.uuid) || '',
}));
return {ok: true, projectUuid: cur.uuid, friendlyName: cur.friendlyName || '', boards: boards};
"""


def mm_to_mil(value: float) -> float:
    """mm → PCB 单位（mil，F3）。"""
    return round(float(value) * MM_TO_MIL, 4)


def mil_to_mm(value: float) -> float:
    return round(float(value) * PCB_UNIT_MM, 6)


def report() -> dict[str, Any]:
    """``lceda status`` 的无参载荷：探测结果 + 各段的必需方法。"""
    found = probe()
    found["capabilities"] = {
        job: [f"{c}.{m}" for c, m in items] for job, items in CAPABILITIES.items()
    }
    found["units"] = {
        "schematic": SCH_UNIT_MM,
        "pcb": PCB_UNIT_MM,
        "mm_to_mil": round(MM_TO_MIL, 6),
    }
    return found