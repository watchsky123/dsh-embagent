# 操作手册（照做，不是读懂）

> 从 `AGENTS.md` 第 8 章搬出来的。**搬的原因**：AGENTS.md 已经长到
> 67 KB，超过 DSH 的注入预算（65 KB），尾部会被**静默截断** ——
> 一个 agent 会以为自己读到了全部坑，其实少了最后几节。
> 内容一字未改，只是换了位置。

## 8. 操作手册（照着做，不是读着懂）

前面七章讲"为什么"，这章是**能直接照做的命令与判别流程**。
遇到对应症状时跳到这里，别从头推理。

### 8.1 Access denied 但权限看起来正常 → 三步判别

**症状**：`System.UnauthorizedAccessException: Access to the path '...' is denied`，
但你能删能建该目录。

```bash
# 第 1 步：查 ACL。当前用户有 (F) 且无 (DENY) → 不是权限问题
icacls "C:\Users\<u>\AppData\Roaming\renode"

# 第 2 步：用同语义的 Win32 调用复现独占打开
python -c "
import ctypes
from ctypes import wintypes
k=ctypes.WinDLL('kernel32',use_last_error=True)
k.CreateFileW.restype=wintypes.HANDLE
k.CreateFileW.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,
  ctypes.c_void_p,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]
h=k.CreateFileW(r'C:\Users\<u>\AppData\Roaming\renode\config.lock',
                0xC0000000,0,None,2,0,None)
print('OK' if h!=ctypes.c_void_p(-1).value else 'FAIL err=%d'%ctypes.get_last_error())
"

# 第 3 步（最关键）：最小命令对照实验
# 只做不加载 CPU 的那一步 → 成功；加上 CPU 加载 → 被拒。
# 两步结果不同 = 功能级拦截，不是文件系统问题。
```

**判定后立刻停止**：不要再去试 `--config` 换位、重定向 `APPDATA`/`TEMP`、
`cmd` 包装、计划任务、预建目录、预建锁文件、`chmod`。这七条实测全无效。

改成：代码返回 `not_ready` + 附被拒路径；生成宿主外可执行脚本。

### 8.2 中文引号 / 反引号导致语法错，报错位置在下游

**症状**：`SyntaxError` 指向的行看起来完全正常。

```bash
# 30 秒定位
grep -n '[“”‘’]' file.py                 # 中文引号
grep -n '`' src/client/styles.ts         # TS 模板字面量里的反引号
```

**改法**：字符串值里的中文引号换「」；docstring 里可以保留。
TS 模板字面量内不写反引号、不写正则（`\d` 会被吃掉）。

### 8.3 退化验证：确认断言真的能红

**每条断言写完立刻做，90 秒。** 不做这一步的断言很可能是恒真的。

```bash
cp impl.py impl.bak
python - <<'PY'
import io
p='impl.py'; s=io.open(p,encoding='utf-8').read()
s=s.replace('<关键判断>', 'pass  # TEMP: 模拟退化')
io.open(p,'w',encoding='utf-8').write(s)
PY
python -m dsh_embagent.scripts.check_xxx        # 必须 FAIL
cp impl.bak impl.py && rm impl.bak
python -m dsh_embagent.scripts.check_xxx        # 必须 PASS
```

**特别注意**：所有 `if ...: continue` 形式的闸门，去掉后测试**天然还是绿的**
（"什么都没发生"正是期望）。这类必须专写正面断言（`assert added == 0`）。

本次实测有效的三个退化：
| 改什么 | 期望失败信息 |
|---|---|
| `_attribution_map` → `return {}` | `D1 被判为 signal_diode，但 derive() 只给了它 connected` |
| 去掉 `only_declared` 闸门 | `added=13`（期望 0） |
| 读 `budget.json` 而非 `coverify.json` | `0 条`（期望 7） |

### 8.3b `s.replace(a, b)` 里 `a` 是空串 → 整份文件被插成 50 万行

**症状**：一个 400 行的文档变成 528867 行，每条 `### 标题` 前面多出一个字符
（前缀拼出来正是被插入的那段文本的首字母序列）。

**根因**：用切片当 `old`——

```python
i = s.index(start); j = s.index(end)     # ← end 在文件里出现在 start **之前**
s = s.replace(s[i:j], block)             # i>j → 切片是 '' → 见下
```

`str.replace('', X)` 会在**每个字符之间**插入 X，长度变成
`len(s) * (len(X) + 1)`。不报错，文件看着还在，内容全毁。

**判别**：`wc -l` 突然到十万量级；`grep -c '^### '` 异常大。

**改法**（三条一起用）：

```python
assert s.count(start) == 1          # 锚点唯一
i = s.index(start)
j = s.index(end, i)                 # ← 从 i 之后找，不是全文第一个
assert i < j and j - i > 100        # 空/过短切片就是上面那个形状
s = s[:i] + block + s[j:]           # 用拼接，不用 replace
```

**可恢复**：`R` 被插在每两个原字符之间，所以原串是
`S[k] = text[len(R) + k*(len(R)+1)]`，`len(R)` 由「第二次出现 R 的位置 − 1」
给出。实测 528867 行原样还原成 541 行，一个字不差。

**顺带一条**：这个仓库的 `CHANGES.md` / `docs/execution-plan-v3.md` 当时是
**未跟踪文件**（`git status` 显示 `??`），所以 `git checkout` 救不了。
改长文档前先 `git add`（或先 `cp` 一份）。

### 8.4 Windows 沙箱内启动长驻服务

**症状**：启动成功、同一次调用能连上，下次调用进程已消失。

试过无效：`Popen(start_new_session)`、`DETACHED_PROCESS`、
`CREATE_BREAKAWAY_FROM_JOB`、WMI、`nohup &`。原因：Job Object 带
`KILL_ON_JOB_CLOSE`，命令结束整棵树被回收。

**可用做法**：

```python
# 1) 写 .cmd —— 必须用二进制写 CRLF，不要用 echo 重定向
import io
s = ("@echo off\r\n"
     "cd /d D:\\proj\r\n"
     '"C:\\path\\python.exe" -m uvicorn app:main --port 3080\r\n')
io.open("run.cmd", "wb").write(s.encode("ascii"))

# 2) 注册并启动（不要加 /RL HIGHEST，会 Access denied）
#    schtasks /Create /TN "my-backend" /TR "D:\\proj\\run.cmd" /SC ONCE /ST 23:59 /F
#    schtasks /Run /TN "my-backend"
```

**验证**：隔 30 秒后在**另一次调用**里 `curl http://127.0.0.1:3080/health`。
同一次调用内能连上不算。

### 8.5 参数不生效（输出与输入无关）

**症状**：换 3 个不同参数，产物指纹完全相同。

**判别**：打印实际发出的 body，与 schema 逐字比对键名。

**修复**：服务端加 `model_config = ConfigDict(extra="forbid")`，
或客户端发送前做字段白名单守卫（本仓库用后者，见
`velxio_client.py:136` 的 `_compile_body()`）。

**验证**：同一接口跑 3 组参数，断言产物**两两不同**。
本仓库实测 uno/mega/leonardo 的 hex md5 = `6cd87fb1` / `2df5402f` / `2478f26c`。
单次调用通过不算验证。

### 8.6 跑命令前先确认这 6 个前提

跑任何 `design_cli` 命令前，先确认这 6 个前提，能省掉大半排查：

| 前提 | 怎么确认 |
|---|---|
| 在仓库根跑 | `pwd` 应是含 `dsh_embagent/` 的目录 |
| 工程存在 | `python -m dsh_embagent.design_cli list` |
| 产物已生成 | 看 `<工程>/ladder.json` 等是否在 |
| 判定器返回 dataclass | 用 `.to_dict()`，不是下标访问 |
| 中文限制 | 字符串值里不嵌中文引号 |
| 闸门有出口 | 写 `continue` 时同时写确认通道 |

### 8.7 验收脚本清单（一条命令验一层）

```bash
cd <repo-root>                    # 必须在这里，不在 dsh_embagent/

npm test                                         # 760 项单测
python -m dsh_embagent.scripts.check_m1             # M1 四条硬验收
python -m dsh_embagent.scripts.check_units_links    # P5
python -m dsh_embagent.scripts.check_generate       # P6
python -m dsh_embagent.scripts.check_m3             # M3 P7+P8
python -m dsh_embagent.scripts.check_ladder         # 阶梯
python -m dsh_embagent.scripts.check_justify        # 归因
python -m dsh_embagent.scripts.check_handoff        # 交接
python -m dsh_embagent.scripts.check_m18            # P18 统一判定
python -m dsh_embagent.scripts.check_p17            # P17 立创发射（真机那几条要装 lceda）
python -m dsh_embagent.scripts.check_p19            # P19 元件目录（真机那两条要联网）
python -m dsh_embagent.scripts.check_p20            # P20 固件（真编译那条要 gcc）
python -m dsh_embagent.scripts.check_p21            # P21 闭环（真 MuJoCo 那条可选）
python -m dsh_embagent.scripts.check_p22            # P22 agent 循环（全假模型，0 token）
python -m dsh_embagent.scripts.check_p23            # P23 测试规模与降级路径（子进程真跑）
python -m dsh_embagent.scripts.check_p0             # P0 结论层（前端契约 + 排序 + 过期）
python -m dsh_embagent.scripts.check_p1             # P1 下一步动作（映射表 + 前端接线）
python -m dsh_embagent.scripts.check_p2             # P2 按对象聚合（索引 + 画像 + 三态）
python -m dsh_embagent.scripts.check_p3             # P3 参数敏感度（接线扫描 + 两层扰动）
python -m dsh_embagent.scripts.check_p4             # P4 固件矩阵（每脚一行 + 四类状态）
python -m dsh_embagent.scripts.check_p5             # P5 需求草稿 + 从零开局（38 条）
python -m dsh_embagent.scripts.check_p26            # 几何/连通/自研布线/布局/通道（28 条，全离线）
python -m dsh_embagent.scripts.check_p40            # 机械模型 mjcf（15 条：构建/真加载/三态/退化）
python -m dsh_embagent.scripts.check_p41            # 四域 spec（21 条：来源分级/声明优先/归并/往返）
python -m dsh_embagent.scripts.check_p42            # 关节提案（12 条：有依据才提案/不编参数/应用后进 MJCF）
python -m dsh_embagent.scripts.check_p43            # 编排器（12 条：六段/不中止/确定性/发射隔离/退化）
python -m dsh_embagent.scripts.check_p44            # 机械判据（16 条：干涉/重心/扭矩/行程/并入 handoff/退化）
python -m dsh_embagent.scripts.check_p45            # 制造可行性（17 条：DFM 三态/DRC 分级/合成/命令/退化）
check_p46            # 前端两分区（26 条：分区/面板/动作/RPC/快照/跳转/退化）
check_p47            # 审计/环境/样例/有界自修（21 条：八段/缺产物 not_ready/必需项/样例/停法/退化）
check_p50            # 接口契约 + 纯 C 算法（14 条：逐引脚/外设 inferred/unknown/真编译/退化）
check_p49            # 项目计划图（27 条：版本/id 唯一/判据/坐标/四层图/任务 verify/退化）
check_p48            # 闭环修复（20 条：网表不再丢/空网表判死/deliverable 要证据/命令 status/parity 守卫/3 条退化）
```

**立创侧诊断脚本**（不是验收，是「说不清就量一遍」用的）：

```bash
python -m dsh_embagent.scripts.probe_route_geom     --session <sid>   # 元件/焊盘/板框/走线 + autoRouting
python -m dsh_embagent.scripts.probe_route_session  --path x.eprj3    # 同一工程不同窗口来源对照
python -m dsh_embagent.scripts.probe_migrate                          # §15.1 云槽位/save 真值 + §15.3 迁移配方
python -m dsh_embagent.scripts.lceda_probe                            # 版本/能力表/会话
```

布线 0 段时**先跑 `probe_route_geom`**：它会同时打出板框源数组与焊盘坐标，
一眼就能看出「板框 y 区间有没有覆盖焊盘」（AGENTS §3.20）。

**注意**：脚本名不要加 `check_` 前缀重复（`$s` 循环时容易写成 `check_check_m1`）。

改动 Python 后至少跑 M1 + 改到的那个阶段；改 TS 后跑
`npm run check && npm run build && node scripts/check_tools.mjs`。

### 8.8 清理验收残留

验收会往用户工作区写夹具与产物。收尾三件事：

```bash
# 1) 删夹具工程（先确认它确实是夹具）
python -c "
import sys; sys.path.insert(0,'.')
from dsh_embagent.design.store import DesignStore
d=DesignStore('m1_p2_accept').load()
print([p.ref for p in d.parts])   # 应等于 ['U1','R1','D1']
"

# 2) 恢复被验收改动的真实工程（links_list 必须清空）
python -c "
import sys; sys.path.insert(0,'.')
from dsh_embagent.design.store import DesignStore
s=DesignStore('示例-多板'); d=s.load()
d.links_list=[]; s.save(d); print('restored')
"

# 3) 删临时文件
rm -f *.json.tmp
```

**最容易忘的是第 2 条**：`promote` 过的 inferred 链路会留在 `links_list` 里，
等于替用户做了决定。

**P23 的新约定**：合成夹具放在 `tests/fixtures/designs/*.json`，**每个都要在
`test_phases.FIXTURE_EXPECT` 里登记它存在的理由**（`clean` / `validate` /
`geometry`）。没登记的夹具只是躺在磁盘上的 JSON——`check_p23` 的 3b 条会点名。

## 9. 环境类坑（从 AGENTS §3.4–§3.6 搬来，内容一字未改）

### 3.4 Windows 文件锁：Renode 在 DSH 沙箱内跑不起来

Renode 在 `TranslationCPU.Init()` 时对 `%APPDATA%\renode\cache_lock`
做独占打开，DSH 作业对象下被拒：

```
System.UnauthorizedAccessException: Access to the path '...\cache_lock' is denied.
```

**关键对照**：不带 CPU 的 `mach create "x"` 能成功，
一加 `LoadPlatformDescription` 就失败。

试过并失败的旁路（不要重复）：
`--config` 指向别处、重定向 `APPDATA`/`TEMP`、`cmd` 包装、
Windows 计划任务、预建目录、预建非空锁文件。

ACL 确认当前用户有完全控制权、无 Deny，Python 子进程
`CreateFileW(share=0)` 独占打开也成功 → 是 DSH 对 Renode 进程的特定拦截，
**不是文件权限问题**。

**现状**：`renode.report()` 如实报 `runnable_here: False`，
产物（`.resc` + `run.cmd`）交人在 DSH 外执行。
`run.cmd` 里预建 `%APPDATA%\renode`——Renode **只会写、不会建**该目录。

### 3.5 Velxio 后端存活要靠 Windows 计划任务

DSH 的 Job Object 有 `KILL_ON_JOB_CLOSE`，bash 调用结束时整棵进程树被回收。

试过并失败的方案：`Popen`、`CREATE_BREAKAWAY_FROM_JOB`、`DETACHED_PROCESS`、
WMI `Win32_Process.Create`。

**可用方案**：`schtasks`（`dsh_embagent/serve.py` 已封装）。
三个必须注意的点：
* 不要加 `/RL HIGHEST`（Access denied）
* 包装 `.cmd` 必须写成**二进制 CRLF**，否则 `\r\r\n` 会破坏 `--port 3080`
* 直接 exec uvicorn，不要用 `start`

### 3.6 后端导入慢 77 秒的根因是模块级网络调用

`compile.py:35` 在模块级构造 `ArduinoCLIService()`，触发
`_ensure_board_urls()` + `_ensure_core_installed()`，三个不可达的
board-manager URL 各耗一次超时。

实测：清除后 `core list` 从 76.0s → 0.14s。

修法：venv 的 `sitecustomize.py`（`dsh_embagent/patch.py` 是唯一生成处）。
digest **是运行时算的**（`sha256(PATCH_BODY)[:16]`，见 `_expected_digest()`），
不是写死的常量——改了 `PATCH_BODY` 就会自动重算，`status` 会报不匹配。
**必须在模块 exec 之前打补丁**——第一版在 exec 之后替换
`module.arduino_cli`，构造已经发生了，无效。

## 10. 客户端接线类坑（从 AGENTS §3.13–§3.14 搬来，内容一字未改）

### 3.13 宿主给了字段、客户端不读 → 那 14 处调用全是死代码

`service.ts` 早就在 hint 里放了 `view`（`hintState.view`），
但客户端 `poll()` **只读 `project`**：

```ts
if (hint.project !== undefined && hint.project !== '') { … }   // view 从来没被读
```

后果不是报错，是**功能静默缺失**：`tools.ts` 里 14 处
`viewHint(project, 'checks')` 全成了装饰，用户跑完 `hw_ladder`
还得自己点页签去找结论。

**判别**：一个字段在**发送侧**有赋值、在**接收侧**没有读取，就是这种形状。
grep 一次即可——`grep -rn "hint\.view" src/`，只有发送侧命中就是死的。

**修法**：读它，并且**只认已知值**（未知字符串忽略而不是猜）：

```ts
if (typeof view === 'string' && isSectionId(view)) { … }
```

切分区必须同时切分组，否则会出现"选中了不在当前分组里的分区"这种错位状态。

**同类风险**：任何"宿主塞进 payload、客户端按需取"的契约字段
（`presentationMeta`、`phase`、`verdict`）都可能是这个形状。
加字段时**同时写一条断言它被读到**——`check_p0` 的 6b/6c/6d 就是干这个的
（读 TS 源码断言"已 import 并渲染"，因为 tsc 抓不到"只定义不接线"）。

### 3.14 `fix.need` 是一串散在十几个文件里的字面量 → 漏一个就静默失去按钮

`physical.py` / `links.py` / `coverify.py` 各自写 `"need": "xxx"`，
由 `nextstep.NEEDS` 翻成可点的动作。**新加一个 need 而忘了进表**的后果是：

    那条 finding 没有按钮 —— 不报错、不失败、只是没了

和 §1.2 的闸门是同一个形状。所以 `check_p1` 的 1a **扫源码**收集所有
`"need": "..."` 再比对表，把"漏了"变成红的。加 need 时必须同时加表项。

三条同时记住：

* **降级不是填空，是换一句话**。命令模板缺 `{lcsc}` 时不要把占位符填空
  然后显示「查  的尺寸」——读起来像 bug，人会怀疑工具而不是怀疑参数。
* **没有靶子就不给按钮**。"整个设计一个申报都没有"这类，挑第一个元件当靶子
  是猜；点"去改"跳到一个随机件，比没按钮更糟。
* **refs 可能在 finding 顶层**（`physical` 的惯用法），不在 `fix` 里。
  两者都要接受，否则下一步会显示「把 ? 分开」。

---

## 11. 未被消费的字段/参数（从 AGENTS §3.16 搬来，内容一字未改）

### 3.16 「没人读」被报成「不敏感」= 最难发现的假绿

P3 查清的事实：**136 个仿真参数里只有 4 个真的被代码读**（`mjcf.build` 的
`params.get`），另 1 个（`density_kg_m3`）被读但不在表里，**其余 132 个没有任何
代码读过它们**。

如果把 132 个"没人读"的参数报成"敏感度低"，读的人会理解成
**「评估过，不重要」**——而真相是**从未接入**。这是 AGENTS §0.1 在
「一个字段是否被消费」这个维度上的复刻，而且比它更隐蔽：被读的参数
至少有输出可比，没被读的参数**没有任何可观察差异**。

**约定**：报四类，各报各的，措辞必须不同：

| 类别 | 措辞必须说清 |
|---|---|
| `wired` | 改了模型真的变 |
| `wired_noop` | **被代码读取**，但本设计里没有对应机制 |
| `unwired` | **没有任何代码读它**（不是"低敏感"） |
| `missing` | 代码读了但**表里没有这一项** |

**判别**：任何"某维度敏感度低/影响小"的结论，先问一句——
**这条链路上有人读它吗？** 读的入口是 `grep -rn "params\.get("`。

**顺带一条**：接线的事实来源要**扫源码**，不要手写一张表
（手写的表和代码分叉的方式是"表里多一个、代码里没有"）。
`(?<![\w.])` 这个后向断言是必要的——`joint.params.get("range_deg")` 是
**关节私有参数**，不是 `ParamSet`，不加断言会把它误报成"表里缺项"。

---

## 12. 参数形状与 MuJoCo 路径（从 AGENTS §3.33–§3.34 搬来，一字未改）

### 3.33 参数的**形状**没写清 → 标量把 `build()` 崩掉（P40 实测）

`joints.py` 的 `REQUIRED_PARAMS` 只给了**键名**（`hinge: ("range_deg",)`），
而 `mjcf._joint_xml` 里是 `lo, hi = joint.params.get("range_deg", [-180, 180])`
—— 它要的是**一对数**。于是 `range_deg: 180`（标量，舵机文档里最常见的写法）
直接抛：

```
TypeError: cannot unpack non-iterable float object
```

而且是在 `build()` **内部**抛：`ladder --with-sim`、`mjcf` 命令收到的都是
异常，而不是"这个关节被跳过"。第一版 `cmd_mjcf` 把它折成
`{"status": "failed"}`，看起来像"工具坏了"，其实是一条**契约没写清**。

**修法（三条一起）**：

1. `_range_pair()` 明确接受 `[lo, hi]` 或 `min_deg`/`max_deg`（仓库自带
   fixture `folded-hinge.json` 用的就是后者——两种写法都得认）
2. **标量不猜**：`180` 既可能 `±180` 也可能 `0..180`，物理不同 →
   跳过该关节并**在原因里说清要 `[lo, hi]`**
3. `describe()` 增 `param_shapes`，把形状写出来（`required_params` 只有键名，
   不够）

**判别**：`missing != []` 的检查通过、但 `build()` 抛 `TypeError` → 就是它。

---

### 3.34 MuJoCo `from_xml_path` 在**非 ASCII 路径**上必然失败（本机工作区就叫中文）

```
ValueError: ParseXML: Error opening file
'C:\Users\<user>\Desktop\示例工作区\p40-fixture\model.xml'
```

文件**确实存在**（1345 字节），`read_text` 读得好好的；换成
`MjModel.from_xml_string(xml)` 立刻就编译通过。`from_xml_path` 走的是底层 C
接口，Windows 上对非 ASCII 路径没有处理。

**修法**：`load_check()` **先用 Python 读文件、再喂字符串**，永远不把路径
交给 MuJoCo。这样也顺带绕开"路径里有空格"之类的问题。

**判别**：`FileNotFoundError` 形状的报错，而 `Path(p).is_file()` 是 `True`。

---

## 13. 本地 ARM 工具链（装法 + 那个 Low IL 坑）

### 13.1 装在哪

```
workspace\dsh_embagent\_tools\arm-gcc\13.3.rel1\      ← 仓库内，1.2 GB
    bin\arm-none-eabi-gcc.exe  (Arm GNU Toolchain 13.3.Rel1, build arm-13.24)
```

`_tools/arm-gcc/` 与 `_tools/_dl/` 已进 `.gitignore`（**不入库**）。

### 13.2 装法（可复现，三步）

```bash
# 1) 下**官方 zip**（不要 exe 安装器；安装器会写注册表 + 改 PATH）
curl -L -o _tools/_dl/arm-gcc.zip \
  https://developer.arm.com/-/media/Files/downloads/gnu/13.3.rel1/binrel/arm-gnu-toolchain-13.3.rel1-mingw-w64-i686-arm-none-eabi.zip
# 284 MiB；实测 2 分钟（2.4 MB/s）

# 2) 解压，顶层目录改名为版本号
python -m zipfile -e _tools/_dl/arm-gcc.zip _tools/arm-gcc/
# 再把 arm-gnu-toolchain-13.3.rel1-.../ 改名为 13.3.rel1/

# 3) **必做**：去继承的 Low 完整性标签（见 13.5，不做则编译全 Permission denied）
icacls "workspace\dsh_embagent\_tools" /remove:d Everyone
icacls "workspace\dsh_embagent\_tools\arm-gcc" /grant "%USERNAME%:(OI)(CI)F" /T
icacls "workspace\dsh_embagent\_tools\arm-gcc" /setintegritylevel "(OI)(CI)Medium" /T /C
# 实测：8604 个文件，各 1 秒级
```

### 13.3 为什么不污染

| 项 | 有没有 |
|---|---|
| 安装器 / 写注册表 | **无**（纯 zip 解压）|
| 改系统 PATH | **无**（用绝对路径调用）|
| 服务 / 计划任务 / 开机启动 | **无** |
| 系统目录写入 | **无**（全在仓库 `_tools/`）|
| 唯一占用 | `_tools/arm-gcc` **1.2 GB** |

**卸载 = 删掉 `_tools/arm-gcc` 这一个目录**（那三条 `icacls` 只作用于它，删了即复原）。

### 13.4 验证（4 步，缺一不可）

```bash
B=_tools/arm-gcc/13.3.rel1/bin
$B/arm-none-eabi-as.exe  -o a.o a.s                 # ① 汇编器能写
$B/arm-none-eabi-gcc.exe -mcpu=cortex-m4 -mthumb -Os -c t.c -o t.o   # ② 编译器能写
$B/arm-none-eabi-gcc.exe -mcpu=cortex-m4 -mthumb -nostdlib \
    --specs=nano.specs -Wl,-Ttext=0x08000000 -o fw.elf start.c t.c   # ③ 链接
$B/arm-none-eabi-readelf.exe -h fw.elf | grep -E "Machine|Entry"      # ④ 目标对
$B/arm-none-eabi-gcc.exe -mcpu=cortex-m7 -mthumb -mfloat-abi=hard \
    -mfpu=fpv5-d16 -Os -c t.c -o m7.o                                 # M7 + 硬浮点
```

**实测结果（2026-11-22）**：

```
as ✓；gcc -c ✓；链接 ✓ → text 108 B / bss 4 B
readelf: Type=EXEC  Machine=ARM  Entry=0x8000000
objdump counter: add.w r0,r0,r0,lsl#1 / adds r0,#1 / bx lr   ← Thumb 指令
Cortex-M7 + fpv5-d16 硬浮点 ✓
```

`doctor` 会报 `arm_gcc ok`（`scope=repo`，附版本号）；`PATH` 里**查不到**
`arm-none-eabi-gcc` —— 这正是"不污染"的证据。

### 13.5 那个坑：**Low 完整性标签 → 编译全 Permission denied**

**症状**（三种，同一个根因）：

```
Cannot create temporary file in C:\Users\...\AppData\Local\Temp\: Permission denied
Assembler messages: Fatal error: can't create x.o: Permission denied
cc1.exe: fatal error: cannot open 'd.s' for writing: Permission denied
```

**根因**：本仓库目录带继承标签

```
dsh_embagent\  Mandatory Label\Low Mandatory Level:(I)(OI)(CI)(NW)
```

于是**解压出来的 exe 也带 Low IL**；而 Windows 会**用镜像文件的 IL 启动进程**
→ 工具链进程是 Low → `NW`（No-Write-Up）→ **写不了 Medium 目录**（桌面工程、
`D:\tmp`），连自己的临时文件都建不了。

**判别**（一行）：

```bash
icacls _tools/arm-gcc/13.3.rel1/bin/arm-none-eabi-gcc.exe | grep -i Mandatory
# Low Mandatory Level → 就是它
icacls <exe> /setintegritylevel Medium     # 报 Access is denied → 你缺 WRITE_OWNER
```

**为什么容易误判**（我踩过的两条错路）：

| 当时以为 | 真相 |
|---|---|
| 「沙箱不让写这个路径」 | 同一 shell 里 bash / w64devkit gcc 写同一目录**都成功** |
| 「i686 vs x64 位数问题」 | w64devkit 的 gcc **也是 i686**，一切正常 —— 差别只在标签 |
| 「`TMP` 指错了」 | 改成 `D:/tmp/...` 仍 denied；换成 Low 目录才通 |

**修法顺序**（缺一不可，顺序反了会被拒）：

1. `icacls <_tools> /remove:d Everyone` —— 先摘掉 `(DENY)(DC)`
2. `icacls <_tools/arm-gcc> /grant "<user>:(OI)(CI)F" /T` —— 拿到 WRITE_DAC/OWNER
3. `icacls <_tools/arm-gcc> /setintegritylevel "(OI)(CI)Medium" /T /C` —— 递归提级

**新装任何"从 zip 解压的可执行工具"到本仓库时，都要做第 3 步**，否则会以为是
工具坏了（其实是这道标签墙）。

**顺带**：这条也解释了一类"诡异"现象 —— 往本仓库里解压的 `.exe` 双击能跑但
一写文件就失败；而 `D:\deeptool\w64devkit` 里的同名工具毫无问题（它没有标签）。

## 14. 软件任务为什么要拆三层（P51）

`plan` 第一版对**软件能力**（S1–S6）也只用"缺数据 → 一条任务 + 一条验收"，
于是 `复刻-示例` 的软件任务只有 **12 条**（6 能力 × 2）。看着像把软件
做完了，实际**"写"这一步根本没出现在任务表里**。

拆法（`design/plan.py` 的 `SW_BREAKDOWN`）：

```
S1 风控   spec(写判据) → impl(写状态机) → inject(注入测试)
        → threshold(阈值来源) → latency(测延迟) → V(验收)
S2 控制律  gains → cascade → step → margin → V
S3 EKF     state → impl → converge → noise → V
S4 视觉    iface → algo → acc → V
S5 任务    fsm → impl → wp → V
S6 遥测    frame → impl → rate → V
```

实测：任务 **28 → 50**，软件 **12 → 34**（平均 5.7 条/能力），
硬件仍是 16 条（2 条/能力）——**软件的"写"真的进了任务表**。

四条硬纪律：

1. **第一层必须是"写"**（`design`/`code`）。若第一层是"测"，说明分解退化了。
2. **链式依赖**：剥掉能力级前置（S1 依赖 H2/H8 的验收）后，
   每条恰好依赖前一条 —— 不是并列，否则"写→测"可以同时做。
3. **能力级 `-V` 依赖链尾**（`T-S1-latency`），不是依赖全部前置。
4. **`artifact` 只报存在性，不判 done**：实测造一个空 `failsafe.h`，
   `present` 变 `True` 而 `status` 仍是 `open` —— 文件在 ≠ 实现对（§0.1）。

验收：`check_p51` **14 条**（含 1 条退化：清空 `SW_BREAKDOWN` → S1 从 7 条掉回 2 条）。

**踩过的一个小坑**：我在 `check_p51` 的 2a 第一版写了
`all(... or True for ...)` —— 一个**恒真子句**，正是 §4.1 说的"永远绿的断言"。
自己写完读一遍才抓到。写 `or True` 之前先问：这半句还会失败吗？

## 15. 可编译骨架 + 真编译（P52）

`fw-build <工程>` 从 `design.json` + `interface.json` 生成最小工程，用**仓库内**
`_tools/arm-gcc/13.3.rel1` 真编成 `.elf` + `.bin`：

```
fw/build/startup.c   向量表（16 个 Cortex-M 标准异常）+ Reset_Handler + 弱符号
fw/build/link.ld     MEMORY 块（FLASH/RAM 容量**带 datasheet 出处**）
fw/build/main.c      main + 外设 TODO（来自 interface 的 peripherals）
fw/build/Makefile    给人手工编也能用
```

### 四条硬判据（缺一条就不是 ok）

| 判据 | 实测（复刻-示例，F103C8）|
|---|---|
| 编译返回 0 | ✅ |
| `Machine = ARM` | `ARM` |
| Entry 在 FLASH | `0x8000045` |
| `text+data ≤ flash`、`data+bss ≤ ram` | 168+4 ≤ 65536、4+4 ≤ 20480 |
| 出得来 `.bin` | 172 B |

### 三条退化（`check_p52` 守）

* FLASH 挪到 `0x20000000` → Entry 不在 FLASH → `failed`
* RAM 改到 1 B → `data+bss = 8 B 超过 RAM 1 B` → `failed`
* `main.c` 插语法错 → 编译不过 → `failed` + 编译器原文

### 诚实边界

* **内存表只有 7 个型号**（`MCU_MEMORY`），表外型号 → `not_ready` + 列出已知，**不猜容量**
* `main.c` 是**骨架**：外设初始化只有 `TODO`。它证明"能编、能链、地址与容量对"，
  **不证明功能对**——功能对要靠 `fw-algo` 的用例和台架实测
* 没有工具链 → `not_ready`（环境缺 ≠ 设计错）

### 三个踩过的坑（都在同一次真编译里）

| 症状 | 真因 |
|---|---|
| `undefined reference to SystemCoreClock` | `extern uint32_t SystemCoreClock;` 又在函数里赋值 = **声明不是定义**；改成定义 |
| `ld: RAM 越界` 而容量明明够 | link.ld 里那条 `ASSERT(ORIGIN+LENGTH <= 同一表达式)` **恒等**，误报；删掉，容量交给 `size` 判 |
| `firmware.bin` 永远不生成 | 写成 `gcc -O binary out in` —— gcc 的 `-O` 是优化级别、顺序也反；要用 **objcopy** |

## 16. 飞控参数 → 能力映射（P53）

### 16.1 真实数据在哪、长什么样

| 文件 | 格式 | 实测 |
|---|---|---|
| `config_20261021_102258.param` | **`name=value`**，一行一个，`#` 注释 | 47 条（`pid_*` 40 + `alt_*` 2 + `cal_*` 3 + `sys_*`/`imu_*` 各 1）|
| `H743PilotX-master/App/Inc/Config.h` | **`MF_PARAM(name, def, type, 'x') /*注释*/`** | 206 条；注释是**语义来源**（`/*飞行模式 0=手动 1=油门居中悬停*/`）|

注意这是**自定义格式**，不是 ArduPilot 的 `NAME,VALUE`。第一版按 ArduPilot 写会一条都读不到。

### 16.2 两种 MF_PARAM 形状（同一个宏，两种条目）

```
MF_PARAM( sys_fly_mode, 0, int32_t, 'i') /*飞行模式*/ \
MF_PARAM( ahr_gizmo, 0, int32_t, 'e', mf_MAHONY,mf_MAHONY_BF) /*姿态解算*/ \
```

**枚举型**第 5 段是枚举列表。第一版正则只认 4 个字段 → 2 条枚举参数被记成
"不完整"。修法：第 5 组 `(?:\s*,\s*([^)]*))?`。

**还有一类不是参数**：`#define MF_PARAM(name, defval, ...)` 宏定义行里也有
`MF_PARAM(` —— 第一版把它当条目，于是又报 2 条"不完整"。
**判别**：`line.lstrip().startswith("#define")` 一律跳过。

### 16.3 映射怎么定（三条）

1. **按参数名族**，不按值：`pid_in_*` = 角速率内环、`pid_ou_*` = 姿态外环、
   `pid_yaw_rate_*` = 偏航内环、`pid_gps_*` = GPS 位置环、`alt_*` = 定高控制侧、
   `bar_*` = 定高观测侧、`bat_*` = 电源、`imu_*`/`cal_*`/`mag_*` = 融合。
2. **最长前缀优先**：`pid_yaw_rate_p` 必须落进 `pid_yaw_rate`，不能落进 `pid_yaw`。
3. **每条映射必须有 `basis`**（"凭什么说这个参数属于这条能力"）。写不出 → 进
   `unmapped`（实测 113 条：`ahr_gizmo`/`bbx_spi_bus`/`led_on`… 确实不该硬塞）。

### 16.4 唯一能判的东西：**族完整度**

| 结论 | 条件 |
|---|---|
| `ready` | 该能力的必需参数齐全（S2 要 10 项，实测 10/10）|
| `partial` | 缺一部分（**写明缺哪个**）|
| `absent` | 一个都没有 |

**参数存在 ≠ 能力达标**：有 `pid_in_roll_p=0.05` 只说明"增益有值"，
不说明"角速率环稳定"。所以 `params` **不改能力状态**——`plan` 里 S2 仍是
`unknown`，只多一条 `evidence.params`。

### 16.5 实测（真工作区）

```
206 参数 / 93 映射 / 113 未映射 / 15 族 / 6 能力
族齐：H3 H8 S1 S2 S3 S5（6/6）
known_absent：H4（光流参数未进参数集）H5（GNSS 门限未进）
整体 status = not_ready   ← **参数集里没有的能力就是缺口**，不是 ok
```

`known_absent` 是我**主动登记**的已知缺口（不是"没解析到"）：
`S1` 的失效判据参数（角度/下降率/链路超时）**一条都没有** —— 这条写进报告，
比"参数齐全所以没问题"有用得多。

### 16.6 冲突检测

两个 `.param` 同参数不同值 → `failed`（并指出是哪两份文件）。
理由：两份"当前值"同时存在时，**哪份是真的没说清**，不能挑一个当准。

## 17. 引脚契约进固件：用错脚 = 编译期错误（P54）

### 17.1 之前的问题

P52 的骨架里引脚是**两套来源**：`main.c` 里的 TODO 注释 vs `interface.json`
的契约。于是"板子把 UART_TX 从 30 改到 31"这种事，固件不知道，
**只能等台架才发现**（或者永远发现不了）。

### 17.2 现在：`hwv_pins.h` 由契约生成

`fw-build` 多写一个 `hwv_pins.h`（唯一来源 = `interface.json`）：

```c
#define HWV_MCU_REF "U1"
#define HWV_MCU_PINS 48
#define HWV_PIN_NET_3V3            1  /* 同网还有脚 24, 36, 48 */
#define HWV_PIN_UART_TX           30
#define HWV_PIN_GPIO_1      0  /* power 3V3 */
#define HWV_PIN_GPIO_30     1
```

四个检查宏（**必须在函数体内用**，`_Static_assert` 是块作用域的）：

| 宏 | 判什么 | 失败时看到什么 |
|---|---|---|
| `HWV_PIN(NAME)` | 名字在契约里吗 | `'HWV_PIN_NOSUCH_NET' undeclared`（还会 `did you mean`）|
| `HWV_CHECK_GPIO(NAME)` | 能当 GPIO 吗 | `static assertion failed: "HWV_CHECK_GPIO: pin NET_3V3 is power/ground ..."` |
| `HWV_CHECK_PIN_NUMBER(n)` | 脚号超范围吗 | `static assertion failed: "pin number out of MCU range"` |
| `HWV_EXPECT(NAME, n)` | **契约把线挪走了吗** | `"HWV_EXPECT: interface.json moved ..."` |

`HWV_EXPECT` 是**真正的回归保护**：固件里写"我假设 UART_TX 在 30"，
某天 interface 改成 31 → 编译期就报错。

### 17.3 判据是**反**的：负例必须编不过

`build()` 现在跑三条负例，每条都**期望编译失败**：

| 负例 | 源码 | 实测 |
|---|---|---|
| 电源脚当 GPIO | `HWV_CHECK_GPIO(NET_3V3)` | `error: static assertion failed: ... is power/ground` |
| 脚号超范围 | `HWV_CHECK_PIN_NUMBER(HWV_MCU_PINS + 1)` | `error: ... out of MCU range` |
| 名字不在契约 | `HWV_PIN(NOSUCH_NET)` | `error: 'HWV_PIN_NOSUCH_NET' undeclared` |

**任一条编过 → 整体 `failed`**，findings 里写"契约约束没生效（用错脚不会被拦住）"。
理由：闸门写了但没挂上去，就是 §0.1 的假绿。

### 17.4 三个踩过的坑

1. **同网多脚 → 宏重定义**：GND 落在 4 个脚上，第一版每脚定义一个
   `HWV_PIN_GND` → `warning: "HWV_PIN_GND" redefined`，而且读的人以为这个网
   只有一根线。修法：**同名只定义一次**（取最小脚号），注释写其余脚号。
2. **负例的样本从生成物里正则找 = 自指脆性**：第一版让 `_negative_cases`
   去 `hwv_pins.h` 里找 `GPIO_x 0` 挑电源脚 —— 退化实验把 0 改成 1 之后
   **样本找不到了，负例跟着消失**，于是"闸门拆掉"看起来像"闸门还在"。
   修法：`generate` 把 `contract_pins` 记进 `fw_build.json`，负例从那里取。
3. **错误信息抓错行**：detail 取"含 error 或 HWV_ 的行"，先命中的是无关
   `warning: "HWV_PIN_GND" redefined`。修法：**先找 `error:`**。

### 17.5 实测

```
骨架：Machine=ARM  Entry=0x8000045  text 168 B
负例：3/3 全部编不过，错误信息都指向真原因
退化：把 HWV_PIN_GPIO_<n> 全改成 1 → power_as_gpio 编过 → 整体 failed
无契约：不凭空造引脚宏（HWV_MCU_PINS 0）
```

### 17.6 一条验收纪律（两次踩过）

`check_p0`–`p4` 依赖**当前工作区**。为了跑 `fw-build` 把工作区切到别的目录之后，
这五个会**全部 FAIL**（`工程 '示例-多板' 没有 design.json`），
看起来像功能坏了，其实是环境变了。

**约定**：切过工作区之后，跑全套验收前先切回去：
`design_cli workspace set "C:\Users\<user>\Desktop\示例工作区"`。

### 17.7 边界（诚实）

`hwv_pins.h` 只保证**引脚这一层**一致。它不能证明：外设寄存器配置对、
时序对、功能对。那些要靠 `.ioc`/datasheet 和台架。

## 18. `.ioc` → 外设配置层（P55）

### 18.1 之前的问题

P50 的 `interface.json` 把**时钟 / 中断 / 复用功能 / 时序**四类一律记 `unknown`。
那不是"没问题"，是"没有数据"。而 CubeMX 的 `.ioc` 里**就是这些数据**——
只是从来没人读进契约。

### 18.2 现在读什么（`design/ioc.py`）

| 事实 | 从哪来 | 本模块做什么 |
|---|---|---|
| 时钟树 | `RCC.*Freq_Value` / `PLL*` / `DIV*` / `HPRE` | **原样引用**（不重算倍频链）|
| 中断 | `NVIC.<IRQ>_IRQn=true\:抢占\:子\:…` | 拆开；**内核异常与用户中断分开** |
| 复用功能 | `P<端口><n>.Signal` | 给**功能名**（`PA9=USART1_TX`），**不给 AF 编号** |
| 外设参数 | `<IP>.<参数>=值` + `<IP>.IPParameters` | 只收**写出来的**行 |
| 芯片身份 | `Mcu.Family/Package/UserName/CPN` | 与 `design.json` 的 kind 对 |

实测 7 个真实文件（`check_p55` 1a 复核）：

```
文件                       引脚  用户外设  用户中断  时钟    封装
F103-GPS.ioc                19     4        3     64 MHz  LQFP48
F401-Bottom.ioc             25     8        7     72 MHz  LQFP64
F401-Front.ioc              16     4        3     72 MHz  LQFP64
F405-Joystick-Screen.ioc    36    13       10    168 MHz  LQFP64
F405Joystick.ioc            35    11       12     96 MHz  LQFP64
H743PilotX.ioc              63    22       17    480 MHz  LQFP176
f103c6-car.ioc              14     5        4     64 MHz  LQFP48
```

### 18.3 三条纪律（每条都对应一个真坑）

1. **`.ioc` 只写非默认值。** 实测：`USART1.BaudRate` 只出现在 7 个文件里的 5 个
   （值都是 115200），`I2C1.ClockSpeed` **一个都没有**。所以"没写" ≠ "没配置"
   ≠ "默认值"。`IPParameters` 记的是 CubeMX **跟踪**了哪些项；跟踪了但没写值 →
   `tracked_not_written`，**明确标成推断**，不当事实。
2. **不给 AF 编号。** `.ioc` 说"PA9 做 USART1_TX"，没说那是 AF7。编号在 datasheet
   的复用表里 → `af_number` 恒 `unknown`，`unknown` 清单里保留这一条。
3. **不逐脚对账。** `.ioc` 用端口名（`PA9`），网表用封装脚号（`30`），中间缺一张
   datasheet 的引脚定义表 → `af.join="unknown"`，**不编表**。

### 18.4 硬判据：给错 `.ioc` 要炸

| 判据 | 级别 | 例子（实测） |
|---|---|---|
| `ioc.mcu_mismatch` | **硬** | F103 的板配 H743 文件 → `failed`：`.ioc 的芯片是 STM32H743IIT6（STM32H7/LQFP176），design.json 的 MCU 是 stm32f103c8t6（stm32f1/LQFP-48）` |
| `ioc.mcu_variant` | 软 | C8 的板配 C6 的文件 → 同族同封装，引脚兼容，但闪存不同 |
| `ioc.peripheral_unrouted` | 软 | `.ioc` 配了 I2C1，网表里没有任何 i2c 网 |
| `ioc.peripheral_unconfigured` | 软 | 网表有 USB 网，`.ioc` 没配 USB 外设 |

判 fail 的只有**族/封装不符**（那一定是给错文件）；同族不同型号判 fail 会误伤
（C6/C8 引脚一样）。

### 18.5 固件侧：配置也进编译期检查

`hwv_pins.h` 多出两类宏 + 两个检查宏：

```c
#define HWV_SYSCLK_HZ    64000000u
#define HWV_APB1_HZ      32000000u
#define HWV_CFG_USART1_BAUDRATE  115200u

#define HWV_CHECK_CLOCK(hz)          _Static_assert((hz) == HWV_SYSCLK_HZ, ...)
#define HWV_EXPECT_CFG(ip, param, v) _Static_assert(HWV_CAT(HWV_CFG_, ...) == (v), ...)
```

负例从 3 条变 **5 条**（都**必须编不过**）：

```
power_as_gpio        → "HWV_CHECK_GPIO: pin GND is power/ground ..."
pin_out_of_range     → "HWV_CHECK_PIN_NUMBER: pin number out of MCU range"
name_not_in_contract → 'HWV_PIN_NOSUCH_NET' undeclared
clock_wrong          → "HWV_CHECK_CLOCK: SYSCLK differs from .ioc (interface.json)"
cfg_wrong            → "HWV_EXPECT_CFG: USART1.BAUDRATE differs from .ioc ..."
```

**没给 `.ioc` 时**：不生成 `HWV_SYSCLK_HZ`，于是 `HWV_CHECK_CLOCK(8000000)` 编不过
（`HWV_SYSCLK_HZ undeclared`）——"缺契约"不会悄悄变成"默认 8 MHz"。

### 18.6 三个坑

1. **带后缀的端口名会被严格正则漏掉。** `.ioc` 里晶振/SWD/BOOT 脚写作
   `PH0 - OSC_IN`、`PA0-WKUP`、`PC13-ANTI_TAMP`、`PB3 (JTDO/TRACESWO)`。
   第一版用 `^(P[A-Z]\d+)\.` → 每个文件少 3–4 个脚（合计 22 个），
   少的恰好是**晶振与调试脚**。修法：`^(P[A-Z]\d{1,2})(?!\d)[^.]*\.`，
   端口名取第一组，原样名存 `name_raw`。
2. **NVIC 里的内核异常混进用户中断。** `NVIC.SysTick_IRQn`、`HardFault_IRQn` 等
   9 个也是 `true`；不拆开就会把"3 个用户中断"报成"12 个中断"。修法：
   `nvic.irqs` / `nvic.core` 分开，两边都报。
3. **收窄 `unknown` 时别把 AF 编号也删掉。** 有 `.ioc` 后"AF map"确实不再是
   unknown，但"**编号**"仍是 —— 它只能从 datasheet 来。`unknown` 清单要写
   "AF 编号"，不是整条删掉。

### 18.7 实测（真实项目 `复刻-示例` + `F103-GPS.ioc`）

```
clock：SYSCLK 64 MHz / HCLK 64 MHz / APB1 32 MHz（源 PLL）
nvic ：GROUP_4，用户中断 3（DMA1_Channel4 / EXTI4 / USART1），内核异常 9
外设 ：uart ← ioc（USART1/USART2）；usb ← net（网表有、.ioc 没配）
提示 ：SPI2 配了但网表没有 spi 网；网表有 USB 但 .ioc 没配
固件 ：Machine=ARM Entry=0x8000045 text 168 B；负例 5/5 全部编不过
退化：HWV_CHECK_CLOCK 改空操作 → clock_wrong 编过 → 整体 failed
```

### 18.8 边界（诚实）

`.ioc` 给的是**配置**，不是**物理对照**，也不是**功能正确**：

* 功能名有了，AF **编号**没有（要 datasheet 的复用表）
* 时钟频率有了，**时序约束**（建立/保持/采样时刻）没有（要器件 datasheet）
* 端口名有了，**封装脚号对照**没有（要 datasheet 的引脚定义表；缺它就只能按名字对）
* "配置对"不等于"外设初始化对"——`main.c` 仍是骨架，寄存器配置是 TODO

## 19. 引脚定义表：`.ioc` 的 `PA9` ↔ 网表的 `30`（P56）

### 19.1 之前的问题

P55 把 `.ioc` 的配置读进了契约，但只能**按名字对**：`.ioc` 说 `PA9`，网表说
`30`，中间差一张表。于是"`.ioc` 把这个脚配成 UART，板子却接去 SPI"这种事，
仍然只在台架上才会发现——检查器只能说"这两个网名看起来像一个族的"。

### 19.2 表从哪来

`design/pinout/<型号>.json`，一个型号一个文件。来源是 **LCSC/EasyEDA 厂商符号**
（`easyeda.com/api/products/<LCSC>/components`）：符号里的 `P~` 图元带
`pinNumber` 与 `pinName`，**编号就是封装脚号**。

```
P~show~0~30~315~185~180~gge41~0^^…^^1~328.7~189~0~USART1_TX…^^1~324.5~184~0~30…
             ↑ 脚号 30                                    ↑ 名字 30
```

每条表记 `lcsc` / `mpn` / `package` / `count` / `source_url` / `retrieved` /
`sha256`（对 `dataStr`）—— 可复现、可核对。

**已建表**：`stm32f103c8t6`(C8734) / `stm32f103c6t6`(C8303) /
`stm32f401rct6`(C74524) / `stm32h743iit6`(C89597)。

**被拒绝的**：`stm32f405rgt6`(C15742) 的符号只有 **55/64** 个脚 ——
不建表，也不补 9 个猜的脚（`pinout build` 直接返回 `failed` + 原因）。

### 19.3 凭什么拿它判人失败（三层）

| 层 | 判据 | 位置 |
|---|---|---|
| 自检 | 脚号 1..N 连续、数量 == 封装脚数、端口名合法 | `pinout.validate()` |
| 交叉 | 非 GPIO 脚名与 `fwgen.mcus` 的 `power_pins` **前缀覆盖**（4 张表逐一比；写法差异见 19.6）| `check_p56` 1c |
| 端到端 | 真实项目 EasyEDA 网表 + CubeMX `.ioc` → **0 冲突**，且 `PA9↔30` 等全对 | `check_p56` 4a/4b |

### 19.4 新判据

| 判据 | 级别 | 条件 |
|---|---|---|
| `ioc.pin_conflict` | **硬** | `.ioc` 配了通信外设，网表把这脚接**别的协议族**（uart↔spi…）|
| `ioc.pin_conflict` | **硬** | `.ioc` 配了通信外设，网表把这脚接**电源/地** |
| `ioc.pin_unrouted` | 软 | `.ioc` 配了通信外设，网表里这个脚没接 |
| `ioc.pin_family_unknown` | 软 | 网名不含族关键词，判不出对错 |
| `ioc.pin_role_conflict` | 软 | `.ioc` 是 TX、网表是 RX 网（方向可能反了）|
| `ioc.port_not_in_package` | 软 | `.ioc` 里有这颗芯片**没有的端口**（封装/型号对不上）|

**方向只判软**：网名可能站在对方视角（`RX` 是 MCU 的 RX 还是模块的 RX？），
硬判会误伤。协议族与电源/地不会歧义，才判硬。

### 19.5 实测（真实项目 `复刻-示例` + `F103-GPS.ioc`）

```
引脚表 stm32f103c8t6（C8734，LQFP-48，catalog）
19 个 `.ioc` 脚全部落到封装脚号上，冲突 0
  PA9 ↔ 30  UART_TX   ok        PA10 ↔ 31  UART_RX  ok
  PD0 ↔  5  OSC_IN    ok        PD1  ↔  6  OSC_OUT  ok
  PA8 ↔ 29  GPS_EN    ok
  PA2/PA3/PA13-15（USART2/SPI2）配了但网表没接 → 5 条 `ioc.pin_unrouted`
unknown 只剩：AF 编号 / 时序（脚号对照已由表覆盖）
```

**这两个来源是独立的**：端口名来自 CubeMX，封装脚号来自 EasyEDA 网表。
它们在这张表上对得上，才说明表是对的。

退化验证（`check_p56` 4d/4e）：

```
表里 26↔30 对调 → 真实项目立刻 failed（"PB13 配成 SPI2_SCK，网表的脚 30 是 UART_TX"）
把表拿掉       → 冲突归零、pin_join 为空、"脚号对照"回到 unknown（不是永远绿）
```

### 19.6 三个坑

1. **符号不完整**：F405 只有 55 个脚。要是"缺的跳过"，就会得到一张
   55 行的表——查询 56 号脚返回 None，然后被当成"这脚不存在"。
   正确做法是**整表拒绝**。
2. **脚名带后缀**：`VSS_1` / `VSS_2` / `VDD_3` / `VSSAVREF-` / `PC13-TAMPER-RTC`。
   端口名要按 `P[A-Z]\d{1,2}` 截，电源名要按前缀归类，且**同名多脚合法**
   （VSS 出现 3 次是同一个网络）。

   **同一颗芯片的两份资料写法会不一样**（实测）：

   | 型号 | 符号里 | 型号表里 | 处理 |
   |---|---|---|---|
   | F401 | `VDDAVREF+` / `VSSAVREF-` | `VDDA` / `VSSA` | 前缀覆盖（`VDDA…` 以 `VDDA` 开头）|
   | H7 | `PDR` | `PDR_ON` | 前缀覆盖（反方向：`PDR_ON` 以 `PDR` 开头）|
   | F401/H7 | `VCAP1` / `VCAP2` / `VREF+` | 没列 | allowlist（额外电源脚，**显式列出**不静默放宽）|

   所以交叉核对判的是**前缀覆盖 + allowlist**，不是字符串全等 ——
   但 allowlist 里每一项都要写进文档，否则就是"放宽到永远为真"。
3. **`.ioc` 的端口名和表里的端口名不是一回事**：`.ioc` 的键可能写作
   `PH0 - OSC_IN`，表里是 `PH0`。两边都取 `P[A-Z]\d{1,2}` 再比 ——
   否则"表里有、`.ioc` 里没有"会静默变成 `port_not_in_package`。

### 19.7 边界

* 表只给**封装脚号 ↔ 端口名**与"这脚是不是电源/地/复位/启动"，
  不给**复用功能编号**（AF7/AF8 仍要 datasheet）
* `source="catalog"`（厂商符号），不是 datasheet 原文；要更硬的来源就
  从 datasheet 的表 5 手工录一张并标 `datasheet`
* 表不对 → 会**误判**用户的设计；所以冲突信息里必须写出"表说脚 30=PA9，
  `.ioc` 说 PA9 是 USART1_TX，网表说 30 是 UART_TX"，让人能判断是谁错了

## 20. AF 复用表：这个脚能不能做这个功能（P57）

### 20.1 之前的问题

P56 让 `.ioc` 的 `PA9` 对上了网表的 `30`，但"**`PA9` 能不能做 `USART1_TX`、
AF 编号是几**"仍然只能信 `.ioc`。于是"`.ioc` 把一个 UART 放到根本做不了
UART 的脚上"、以及"固件里 `GPIO_AF4_USART1` 写错"这两类错，都查不出来。

### 20.2 数据来源

**ST 官方 `STM32_open_pin_data`**（`github.com/STMicroelectronics/STM32_open_pin_data`）
—— **CubeMX 自己用的那份库**，BSD-3-Clause（可再分发，保留版权声明）。

| 文件 | 给什么 |
|---|---|
| `mcu/<型号>.xml` | 每个脚的 `Position`（封装脚号）、`Type`、**全部信号名** |
| `mcu/IP/GPIO-<族>_gpio_v*_Modes.xml` | 每个信号的 `GPIO_AF`（F4/H7）或 `RemapBlock`（F1）|

每条表记两个文件的 `sha256` + 仓库 URL + `retrieved` + `license`。

### 20.3 F1 与 F4 必须分开（**最容易造假的地方**）

| 族 | 机制 | 表里的形状 |
|---|---|---|
| **F1**（F103）| 固定功能 + `AFIO_MAPR` 重映射，**没有 AF0–AF15** | `af: null`，`remap: {default, macros}` |
| **F4/H7** | `AF0..AF15` | `af: 7`，无 remap |

实测（F103C8）：
* `PA9 = USART1_TX` 是**默认功能**（`USART1_REMAP0`）
* `PB6 = USART1_TX` 只在 **`USART1_REMAP1`** 打开时成立
  （表里 `default=false`，`macros=[__HAL_AFIO_REMAP_USART1_ENABLE]`）

所以 F1 的表**一个 AF 编号都不许出现**，`validate()` 会拒绝。

### 20.4 新判据

| 判据 | 级别 | 条件 |
|---|---|---|
| `ioc.function_not_on_pin` | **硬** | `.ioc` 把通信/显示外设放到一根**明确没有这一类功能**的脚上 |
| `ioc.function_not_in_table` | 软 | 同类但非通信族（TIM/ADC…）缺失 —— 只提示 |
| `ioc.function_unknown` | 软 | 信号名表里完全没有 —— **判不出**，不是冲突 |
| `ioc.pin_not_in_afmap` | 软 | 这颗芯片没有这个脚 |

有了 AF 表，`unknown` 清单**只剩「时序约束」**：时钟/中断/复用功能/参数
是声明的，脚号对照有引脚表，AF 编号有 AF 表。

### 20.5 实测

```
F103（复刻-示例 + F103-GPS.ioc）：19 个 `.ioc` 功能全部查得到
  by_status: ok 11 / exti 2 / gpio 6；style=F1_REMAP；冲突 0
F401：PA9 → USART1_TX = AF7（与 CubeMX 生成的 `GPIO_AF7_USART1` 一致）
H743：41 条 CubeMX 生成的 AF 证据全部与表一致
两个厂商源：ST 的 `Position` == P56 引脚表的封装脚号（4 张表逐脚相等）
编译期：AF 写错 → `HWV_EXPECT_AF` 断言失败；F1 用 `HWV_EXPECT_AF` → 未声明
```

### 20.6 四个坑（都实测过）

1. **`.ioc` 的信号名有四种写法**，不能只做精确匹配：

   | `.ioc` | 表里 | 规则 |
   |---|---|---|
   | `USART1_TX` | `USART1_TX` | 精确 |
   | `S_TIM1_CH1` | `TIM1_CH1` | 去前导 `S_` |
   | `ADCx_IN0` | `ADC1_IN0` | `ADCx` 族通配 |
   | `GPXTI4` | —— | EXTI 模式，任何 GPIO 脚都能，不查表 |
   | `S_TIM2_CH1_ETR` | `TIM2_CH1` + `TIM2_ETR`（两条） | 去掉 `_ETR` 再比 |

2. **CubeMX 生成的代码与它自己的引脚库外设名不一致**：代码写
   `GPIO_AF12_SDIO1`，库里是 `SDMMC1_D0`。按前缀换名（`SDIO*` → `SDMMC*`）
   才能对上。

3. **没有 `Alternate` 的 `HAL_GPIO_Init` 块不是 AF 证据**。第一版把
   `Init.Pin = …; HAL_GPIO_Init(GPIOE, …)` 也当证据（`af=0`、外设为空），
   于是 H743 凭空多出 2 条假证据。判据：**见过 `Alternate` 行才算 AF**。

4. **"表里没有" ≠ "芯片不能"**。非通信族（TIM/ADC/调试）缺失只给软提示，
   因为表覆盖的是 ST 的 MCU 描述，不能替 datasheet 承诺所有可用性限制。

### 20.7 边界

* 表给**功能与编号**，不给电气/时序、也不给"某个 AF 只在特定批次可用"
* 来源是厂商数据（`catalog`），不是 datasheet PDF；要更硬就手工录并标 `datasheet`
* F405 等没有 AF 表的型号：`.ioc` 功能一律 `unknown`，**不猜**

## 21. 立创侧扩产：封装几何 / 制造包 / 原理图 DRC / 拼板（P58–P59）

### 21.1 封装真几何（`lceda-geom`）

`LIB_Footprint.getGeometry(footprintUuid, libraryUuid)` 一次给出：本体包围盒、
焊盘包围盒、**每个焊盘**（位置/尺寸/层/bbox，单位 mil）。

实测（C8734 = STM32F103C8T6）：

```
pads 48   pitch 0.5 mm   最小焊盘间隙 0.23 mm（= 0.5 − 0.27）
title_size 7×7×1.4（3D 模型 L/W/H，本体）   full 10×10.05（焊盘最外沿）
bodyBBox 8.09×8.58（含丝印/引脚，**不是本体**）
```

**本体 ≠ 焊盘外包**：写 `size_mm` 只认 `title_size_mm`；`full_mm` 留给间距
判据。拿 `full` 当本体会把 LQFP48 记成 10×10、把 0603 记成 2.9×1.5。

写回纪律：只覆盖空 / `estimated` / `proposed` / `catalog`；`datasheet` /
`measured` **一个字节不动**（实测 `复刻-示例` 28 颗全是 datasheet，
`--apply` 结果 applied=0 / skipped=27 —— 这是对的）。

**两个实测坑**：

1. **同一个 LCSC 往往对应多个位号**（一堆 100nF）。第一版 `by_lcsc` 只留一个
   → "查了 28 个只记了 16 个"，其余**静默丢**。修法：按 LCSC 查一次，再展开到
   所有位号；`missing` 必须为 0。
2. **`footprintUuid` 会在"空/有"之间跳**：库 worker 异步装载，同一编号连查三次
   会看到 `has: false → false → true`。修法：`no_footprint` /
   `geometry_unavailable` **重试一轮**（第二轮缓存已暖）。

### 21.2 制造包扩产（`lceda-fab`）

`emit` 原来只导 3 样（Gerber/BOM/坐标）。实测可用的还有 5 样：

| kind | 调用 | 实测 | 头 |
|---|---|---|---|
| `step` | `get3DFile(name,'step')` | 14.1 MB | `ISO-10303-21` |
| `dxf` | `getDxfFile(name)` | 1.18 MB | `0\nSECTION` |
| `pdf` | `getPdfFile(name)` | 452 KB | `%PDF` |
| `netlist` | `getNetlistFile(name)` | 36.7 KB | JSON（`.enet`）|
| `testpoint` | `getTestPointFile(name)` | 8.3 KB | `PK`（zip）|
| `ipc2581` | `getIpc2581CFile(name)` | **2 B（`{}`）** | **不可用** |

**两个硬限制**：

1. **单次 `invoke` 返回上限 8 MB**（`RESULT_TOO_LARGE`）。STEP 的 base64 是
   18.8 MB → 先 `get3DFile` 把 base64 存进 `globalThis`，再 4 MB/块取 5 次。
   取回长度必须等于声明长度。
2. `saveFileToFileSystem` 默认被拒（外部交互权限）→ 一律"取字节 → Python 落盘"。

**判据**：头字节不对 = `failed`；≤4 字节 = `not_ready`（不产出假文件）；
文件落 `artifacts/fab/`，清单写 `fab.json`。

### 21.3 原理图 DRC（`lceda-sch-drc`）

`SCH_Drc.check(strict,false,true)` 返回**分组树**（顶层长度 = 类别数，不是
错误数），每条 `type ∈ {fatalError,error,warn}`。

**不要用布尔模式**：文档原话 —— strict 下 `warn` 与 `error` 一样会让返回值
`false`，而原理图里刻意保留的悬空脚（`CHECKFLOATPIN`）就是 warn。所以本模块
用详细模式、自己按 `type` 过滤：`error` 才判 `failed`，`warn` 单列不挡门。

实测 `复刻-示例`：3 页 / 0 error / 0 fatal / **31 warn**；`audit` 的
schematic 段现在会带上 `sch_drc.counts`（没跑就写 "missing"，不假判）。

### 21.4 拼板（`lceda-panel`）—— 只到容器

`DMT_Panel.createPanel()` 可用（返回 uuid，`modifyPanelName` 改名成功，
`getPanelInfo` 能读回 `itemType: Panel`）。但：

```
doc api --class-name PNL_ManufactureData → null
doc api --class-name PNL_Drc            → null
doc api --class-name PNL_Import         → null
PNL_Document 方法列表                    → 只有 save()
```

**没有把板子放进面板的 API**，也没有面板阵列/DRC/制造文件。所以
`lceda-panel` 建容器后状态是 **`not_ready`**，带 `blocked_by` 与 `api_evidence`；
`panel_done=false`。**建了容器 ≠ 拼板完成。**

### 21.5 四个通用坑

1. **`dmt_Team.getAllTeamsInfo()` 在会话刚开时可能抛**（`reading 'uuid' of null`）
   —— 用户态异步装载。必须 try/catch + 轮询 ~5 秒；工程级 API 全依赖它。
2. **每次 `invoke` 30 秒**：STEP 生成实测 29.9 秒（踩线）。更大的板会超时，
   这时如实 `not_ready`，不要重试到天荒地老。
3. **`getGeometry` 不含 `model3DBBox`**（接口文档里有，这个版本不返回）——
   高度只能用 3D 标题的 `H`。
4. **工程必须在立创里存在**：`lceda-fab/sch-drc/panel` 都从 `emit.json`
   拿 `projectUuid`/`pcbUuid`，没有就是 `not_ready`（先 emit）。

### 21.6 边界

* 几何是**封装源数据**（catalog），不是 datasheet；写回不冒充 datasheet
* 制造文件是**立创的导出**，不是我们画的；头字节对了只说明"是那种文件"
* 拼板不可调 —— 这一条是**结论**，不是没写完；要做只能人工在面板编辑器里拼

## 22. 规格层 + 总线级网表（P60 / A1+A2）

### 22.1 为什么加规格层

原来的链路是「需求 → 元件清单」，中间没有可评审的东西。真实项目是
**功能 → 子系统（块）→ 接口契约 → 选型**；缺这层，生成物只能是"功能示意板"，
接手的人也看不出为什么这样分。

`spec` 产出三份：`spec/vN.json`（机器）、`spec.md`（人）、`spec/index.json`。

| 内容 | 判据 |
|---|---|
| `blocks[]` | 每个角色一个块；没有模板 → 占位块 `source=proposed` + `missing` |
| `interfaces[]` | 每条有 `pins/voltage/direction/bus/rules`；缺电平写 null |
| `constraints[]` / `unknown[]` | 需求给的数进前者；**没给的进后者**（不填默认值）|
| 版本 | 同 `requirement_hash` 复用；变了才 `v(N+1)`（实测 v1 → v1 → v2）|

**规格不选型、不排脚、不生成网表**——它只是让"要做什么"先成立。

### 22.2 总线级网表（`design/bus.py`）

| 协议 | 线数 | 规则 | 交叉 |
|---|---|---|---|
| SPI | 4（SCK/MOSI/MISO/NSS）| SCK 串 22–33Ω、NSS 上拉 | 同名 |
| I2C | 2（SCL/SDA）| 上拉 4.7k、总线电容 ≤400pF | 同名 |
| UART | 2（TX/RX）| **TX↔RX 交叉**、共地 | 交叉 |
| CAN | 2（CANH/CANL）| 两端 120Ω、差分 90Ω | 同名 |
| USB | 2（DP/DM）| 22Ω 串联、ESD | 同名 |
| power | 2（V/GND）| 去耦、反接/过流保护 | 同名 |

**引脚来源是 AF 表**：网名 `SPI1_SCK` 的脚必须在表里真的写着 `SPI1_SCK`
（实测 F103C8：PA5/PA7/PA6/PA15）。凑不出 ≥2 条 → `expand()` 返回 `None`，
生成器退回单脚示意网并标 `bus_partial`，**不按顺序硬凑**。

同一颗芯片的多条总线不会抢脚（`used_a/used_b` 集合）；同名总线第二条会加
序号，避免制造假并联。

### 22.3 边界

* 总线只覆盖 **AF 表里的 4 个型号**；其余型号退回单脚示意（可见的 `bus_partial`）
* 规则（上拉/端接/串阻）是**文字约束**，还没有落到实际元件（A3 块库才补）
* `spec` 还没有被 `generate` 消费（A3 会接）；现在两者共享 `bus.BUSES` 一份事实

## 23. 块库（A3）：块 = 网表片段 + 参数 + 验证记录

### 23.1 为什么

`spec`（P60）只说到"这里有个块"，块里是空的；`generate` 仍然是"角色 → 一个
型号 + 每链路 1 脚"。块库把**常见电路**做成可实例化的数据，才有"从 28 件长到
300 件"的地基。`synth` 的 7 个整板模板是另一条路（不可组合），没有替代它。

### 23.2 数据与规则

`design/blocks/*.json`（实测 6 个：LDO / DCDC / MCU 最小系统 / USB / CAN / 去耦）。

```
parts: key, kind, label, ref_prefix, pins[], size_mm
nets : net, global, class, pins[{key, pin}], port?
rules: 文字约束（上拉/端接/贴近器件）
verified_by: [{project, ladder, date}]
```

实例化（`blocks.instantiate`）：

| 规则 | 说明 |
|---|---|
| ref 分配 | 按 `ref_prefix` + 递增，**跳过已用位号**（组合时不撞）|
| 全局网 | `GND/3V3/5V/VBUS/VIN/VOUT/VBAT` 标 `global`，跨块**合并成一条** |
| 局部网 | 加实例前缀（`USB1_USB_DP`），不制造假并联 |
| 单脚网 | 是**块的对外端口**，必须标 `"port": true`（如 `CAN_TX` 接块外 MCU）；没标的单脚网算错 |
| 摆放 | 栅格折行，按板宽算——`design.validate()` 会拦"足迹超出板轮廓"（实测踩过）|

`compose` 组合多块，`merge_into` 并进设计：**位号冲突直接失败**（不覆盖）。

### 23.3 验证状态：**6 个块全是 unverified**

`verified_by` 为空 → 统计里单独记 `unverified_blocks`，**不算通过**。
这些块来自常见参考电路（AMS1117、TJA1050、F103 最小系统、USBLC6），
没有在任何真板上验过。要进 `verified`，得给 `verified_by` 填真实证据
（哪个工程、哪条阶梯、哪次实测）——**不能自己给自己盖章**。

### 23.4 实测

```
blocks validate  6/6 结构通过（零件/引脚/尺寸/网表引用都对）
组合 3 块       19 件 / 15 网；GND/3V3/VBUS 各一条；位号无重复
组合 6 块       33 件（28→33 的第一步）；V0 阶梯 pass（网表引用完整）
位号冲突        失败并列出冲突位号
坏块           网表引用不存在的 key / 重复 key → validate 报错
```

### 23.5 边界

* 只覆盖 STM32F103C8 的最小系统；换 MCU 要新块
* 参数只有 `{vin,vout,iout}`；**没有做参数扫描/自动选料**
* `rules` 是文字，还没变成"检查项"（A5 约束账本）
* 块里没有封装/焊盘；`emit` 仍按 LCSC 匹配

## 24. 装配（A4）+ 约束账本（A5）

### 24.1 A4 装配：需求 → spec → 块 → 设计

`assemble <id> [--req=PATH] [--board=60x40] [--write]`

```bash
python -m dsh_embagent.design_cli assemble demo --write
python -m dsh_embagent.design_cli constraints demo --derive --write
```

映射是**显式表**（`assemble.ROLE_BLOCKS` / `INTERFACE_BLOCKS`）：

| 来源 | 块 |
|---|---|
| 角色 `mcu` | `mcu_support` |
| 角色 `power_in` | `power_dcdc_5v` + `power_ldo_3v3` |
| 接口 `usb` | `usb_device` |
| 接口 `can` | `can_transceiver` |

映射不到的进 `uncovered`，**不造占位件**（以前 `generate` 会选一个型号顶替；
那是枚举器的行为，`assemble` 不重复）。实测：`mcu+power_in+sensor`+`i2c/usb`
→ **23 件 / 17 网 / 4 块**，`uncovered=[sensor, i2c]`，V0 pass。

`--write` **只写空工程**：已有元件时返回失败并提示先 `new`。原因不是保守，
而是"装配 + 人工布局"混在一份设计里，再装配就会覆盖人的摆放。

### 24.2 A5 约束账本：规则 → 三态

约束来源可追溯（`spec` / `block` / `netlist` / `req`），求值只认**真设计**：

| 规则 | 判据 | 实测 |
|---|---|---|
| `decoupling.<轨>` | 轨上 ≥1 颗电容 | 轨上 3 件、0 电容 → **fail**；有电容但不在该轨 → fail（不是 unknown）|
| `i2c.pullup.<线>` | 该线有电阻上拉到电源 | 无上拉 → **fail**；两颗 4k7 → pass |
| `can.termination` | 120Ω 两端接 CANH/CANL | 无 → **fail**；有 → pass |
| `budget.power` | 块预算（电流×5V）≤ 需求 `power_w` | 8.30 W vs 1 W → fail；没给 `power_w` → **unknown** |
| `iface.*` | 网表里有该 class 的网 | 契约在、网表没有 → **unknown**（不是 pass）|

总状态：有 fail → `failed`；有 pass 且无 fail → `ok`；一条都求不出 → `not_ready`。

`audit` 的 pcb 段会读 `constraints.json`：有 fail 进 findings 并判 failed；
没有产物时 note 写"约束账本没跑（constraints --derive）"，**不假装查过**。

### 24.3 边界

* 块预算的功耗是**上限×5V 的估算**，不是实测（basis 里写了）
* 上拉/终端只查"有没有"，不查阻值对不对（阻值要 datasheet 与信号速率）
* 约束目前由 `audit` 消费；`ladder/coverify/plan` 还没接（B 档）

## 25. B 档：选型字段 / 网络类 / 块实例 / 变更图 / 引脚分配

### 25.1 选型与供应链（B1）

`Part` 新加六个字段（**全部可选**，旧文件往返不变）：

| 字段 | 形状 | 用途 |
|---|---|---|
| `mpn` | 字符串 | 厂家型号（`lcsc` 是目录号）|
| `footprint` | 字符串 | 封装名（如 `LQFP-48`）——**不是尺寸** |
| `alternates` | `[{mpn, lcsc, note}]` | 替代料（空 = 没评估）|
| `cost` | `{unit_cny, qty}` | 单价/用量（负值报错）|
| `supply` | `{stock, lead_time_days}` | 库存/交期 |
| `derating` | `{voltage_pct, current_pct}` | 降额比例 **0–1**（1.5 报错）|

`validate()` 只查"给了但不对"；空 = 没人填，不是 0。

### 25.2 网络类与约束（B2）

`NET_CLASSES` 是**单一事实源**（`model.py`）：power/ground/signal/spi/i2c/
uart/can/usb/clock/reset/boot/debug/analog/hs/diff。`validate()` 会拦：

* 未知 class（实测拼错 `wat` 会列全部允许值）
* `constraints` 不是数组
* 同一网里同一引脚出现两次

块库里的 `boot` 类就是这么暴露的——第一版没进表，装配直接 failed。

### 25.3 块实例（B3）

`DesignModel.blocks_list = [{instance, block, parts: [ref], nets: [name]}]`。

**它只是层次元数据**：零件/网表仍平铺，`flatten` 是恒等，所以所有已有判定器
不用改。`validate()` 查：块引用存在、一个元件只属一个块。`graph` 用它画
块边界。

### 25.4 变更图（B4）

`graph <id> [--impact=U1,net:3V3,block:MCU1] [--write]`

四种节点（part/net/block/firmware）+ 四种边（pin/block_part/block_net/
firmware）。`impact` 沿边**双向两跳**；目标不存在 → `not_ready`。

实测（装配后的 18 件板 + 一条固件 pinmap）：`impact(U1)` → 18 零件 /
8 网 / 3 块 / 1 固件引脚，85 条边。

**边界**：两跳在"MCU 是枢纽"的板上会覆盖全板（这是真的，不是错）；
再远就没有区分度了，所以不扩。

### 25.5 引脚分配器（B5）

`pin-assign <id> --mcu=stm32f103c8t6 [--need=USART1_TX,...] [--avoid=PA9]`

* 候选 = AF 表里功能名**完全等于** `组_角色` 的脚
* 按候选数升序贪心；冲突换组（`USART1` → `USART2`）
* 分不出来进 `unassigned` 并写原因（候选脚被谁占了）

实测：`USART1_TX/RX + I2C1_SCL/SDA` → PA9/PA10/PB6/PB7；`--avoid=PA9` 且
PB6/PB7 被 I2C 占用 → `USART1_TX` 进 unassigned（PA9 被禁、PB6 已用）；
`assign_protocol(uart, avoid=PA9,PB6)` → 换到 **USART2**（PA2/PA3）。

### 25.6 边界

* `cost/supply` 只是**字段**，还没接目录自动回填（要 LCSC 价格/库存接口）
* `graph` 不含机械域/引脚级边（只到"零件—网"）
* 引脚分配是贪心，不是全局最优（候选多时可能占掉后面要用的脚；
  可用 `--avoid`/`prefer` 引导）

## 26. C 档：差异闭环 / 可选基线 / 成熟度 / ADR

### 26.1 闭环（C1）：`readback --diff [--apply]`

`emit` 是单向的；人在 EDA 里改了网表后，`design.json` 不知道。`--diff` 把
PCB 网表读回来，按 **`(ref, pin) → net`** 逐脚对账（不按网名猜）：

| 差异 | 自动回写？ |
|---|---|
| `pin_moved` | ✅ 目标网存在时 |
| `eda_only` | ✅ 网/件/脚都在设计里时 |
| `design_only` / `net_added` / `net_removed` / `part_added` / `part_missing` | ❌ **留给人**（删除是破坏性动作）|

回写后必须过 `validate()`；不过就整份不落盘。空网表 → `not_ready`
（"没有差异 = ok"是假绿）。

### 26.2 可选基线（C2）：`baseline --record` / `baseline`

**降级形态**：基线由人显式记录；对比只输出 delta、`regressions`，
`gate=false` **不参与 pass/fail**；没有基线 → `not_ready`。

指标：`stages_ok`/`maturity_level`/`constraints_pass`/`parts`/`nets`
变小算变差；`stages_failed`/`stages_not_ready`/`constraints_fail` 变大算变差。

它证明"改动没让东西变差"，**不证明"满足需求"**。

### 26.3 成熟度（C3）：`maturity`

六维，只读本工程产物，**不引用任何参考工程**：

```
需求(req.json) · 规格(spec/vN) · 块(blocks_list + verified_by)
约束(constraints.json) · 证据(datasheet/measured 占比) · 发射(emit + verify)
```

`level` = pass 的维度数（0–6）；没有 req/spec → `not_ready`；
缺产物是 `missing`（不是 0 分也不是 pass）。`audit` 的 release 段会带上
`maturity_level`（实测 `示例-单板`：0/6，因为它没有 req/spec/约束账本）。

### 26.4 ADR（C4）：`adr add|list|validate`

`ADR/NNNN-slug.md`，固定四节：`## 决定` / `## 理由` / `## 被否方案` /
`## 证据`。缺一节 → `validate` 判 `failed`；空决策/理由直接 ValueError；
编号只增不改。

### 26.5 边界

* `diff` 只按引脚对账；网名被 EDA 重命名时，`net_added/removed` 可能成对出现
  （那是名字差异，不是拓扑差异）
* `apply` 不做删除、不处理新元件/新网（要人确认）
* 基线对比只看"变差"，不看"变得更好"是否真实（指标涨也可能是假象）
* ADR 的"证据"是自由文本，机器不判真伪

## 27. P65 修正：块布局按尺寸 + 预算上限三来源

### 27.1 布局：从固定栅格到按尺寸排

| 之前 | 现在 |
|---|---|
| 固定 6 mm 栅格、每块各自从原点排 | 按 `w/h` 行式排布（`_GAP_MM=2`），**跨块全局排一次** |
| 7×7 的 MCU 与晶振/排针大面积重叠（实测 **77 处**）| 组合 33 件两两不重叠（实测 **0 处**）|
| 板框 60×40 放不下 → `validate` 报"足迹超出" | **按内容自动扩**（v2 实测 60×49.4），并标 `board_auto_sized` |

`layout_fits` 的口径：**"放得进最终板框"**（不是"满足 5 mm 边距"）。
曾用 5 mm 边距当硬判据，把 38.4/40 的合法排布误报成放不下。

**还没做**：连接器不贴边——修完重叠后 V2 的新 fail 是
`space.connector_edge`（J2 距板边 10.9 mm > 3 mm）。那是独立的布局规则，
不是这次的范围。

### 27.2 预算上限：三个来源

`_power_limit()` 依次读：

```
req.numbers.power_w  →  req.constraints.power_w  →  spec.constraints[kind=power_w]
```

结果里带 `limit_from`（`req.numbers` / `req.constraints` / `spec.constraints`），
三处都没有才 `unknown`。

实测（v2）：上限 60 W 从 `req.constraints` 读到；块预算 26.18 A × 5 V =
**130.9 W** → `budget.power` **fail**（之前是 `unknown`——"读不到"被当成了
"没问题"的反面：读不到就不知道超没超）。

## 28. P66：连接器贴边 / 块内 LCSC / 6 个新角色

### 28.1 连接器贴边（V2 的最后一条 fail）

排布分两段：**底边一行**（x 从左到右）→ **左边一列**（y 从上到下），
每个连接器离边都 ≤1 mm。折行时若只把 x 复位到 2，第二行中间的连接器
会离边 8.1 mm（实测 J8/J9）——所以第二段改成"贴左边一列"。

实测：`physical.check_connector_edge` 无 error，V2 从 `fail` → `unknown`
（unknown 是只剩"高度未声明/无安装孔"这类 info 级项）。

### 28.2 块内补 LCSC

48 个块零件规格 → 23 个唯一 LCSC 编号；装配出的 43 件**全部有编号**。
23 个编号逐个能在立创 `getByLcscIds` 解析成器件（实测 23/23）。

两个坑：

* `C9900214195`（MP1584 模块）在目录里有，但立创库里**解析不到** →
  换 `C234060`（K7805-500 模块，能解析且有封装）。
* **封装关联是异步的**：同一编号连查三次，`footprintUuid` 会在"空/有"间跳
  （P58 的老问题）。所以"编号能解析"不等于"emit 一定能装"；emit 前还要复核。

### 28.3 6 个角色 → 5 个板侧接口块

| 角色 | 块 | 板上是什么 |
|---|---|---|
| `radio` | `radio_nrf24` | 2×4 2.54 模块座（nRF24 模块外接）|
| `sensor` | `sensor_i2c` | 4P 座 + 4.7k 上拉 |
| `camera` | `camera_usb` | 4P 座 + USBLC6 ESD |
| `gimbal_pan` / `gimbal_tilt` | `servo_out`（两实例）| 3P 舵机座 |
| `motor` | `motor_esc` | XT60 电池座 + 3P 电调信号座 |

这些是**板侧接口**：模块/舵机/电调本身是外接件，板上只放座子与保护。

### 28.4 实测（示例一体-全链-v3）

```
43 件 / 35 网 / 11 块；9 个角色全覆盖（uncovered 角色 = 0）
板框自动 60×66；V2 无 error；约束 14 pass / 1 fail（预算 130.9W>60W）
LCSC：43/43；23 个编号全部可解析
```

### 28.5 边界

* 11 个块**全是 unverified**（verified_by 空）
* **端到端 emit 未验证**：对 v3 跑 emit 超过 10 分钟无输出，被取消，
  没有落 `emit.json` —— 这是"没验"，不是"通过"
* `uart`/`i2c`/`spi` 仍计为 uncovered 接口（总线不需要专属器件块）

## 29. P67：emit 分段定位 + 发射前预检

### 29.1 那次“10 分钟无输出”的真因：**参数被静默忽略**

`emit` 的 flag 是 `--key=value` 风格；我传的是空格：

```
emit <id> --jobs project,schematic,pcb --route none     # ← 不生效
emit <id> --jobs=project --route=none                   # ← 正确
```

`cmd_emit` 只匹配 `--jobs=`，空格形式被忽略 → **跑了全部 8 段**（43 件），
10 分钟是正常的。这就是 AGENTS §3.12 的老坑，换到 emit 上又踩一次。

**判别**：报告里 `commands` 是不是你给的那几段。传 1 段却看到 8 段 → 参数没生效。

**实测**：

| 命令 | 耗时 | 结果 |
|---|---|---|
| `--jobs=project`（r3demo，4 件）| **69.8 s**（含 8 段全跑，反证）| project ok |
| `--jobs=project`（v3，43 件）| **16.4 s** | project ok，`v3-3` |
| `--jobs=project` + 预检（v3）| **17.7 s** | project ok，`v3-4` |

### 29.2 发射前预检

`emit-preflight <id> [--strict-footprint]`，或 `emit` 默认先跑（`--no-preflight` 关）：

| 判据 | 不过 |
|---|---|
| 每件有 `lcsc`/`mpn` | `not_ready` 硬拦 |
| `lcsc` 能 `getByLcscIds` 解析 | `not_ready` 硬拦（否则装不上符号）|
| 解析出的器件有封装关联 | 默认 **warning**（`footprintUuid` 异步会跳）；`--strict-footprint` 才拦 |
| 问不到系统库 | `not_ready` + **明说“没验”**（不是通过）|

预检不过时 `cmd_emit` **直接返回、不建工程**（`check_p67` B1：emit 调用次数 = 0）。

v3 实测：43 件 / 23 编号 / 全部可解析；2 条暂无封装（C89811 类电容、C22787），
按 warning 放行。

### 29.3 边界

* 预检**不证明能装**：它只问系统库；符号/封装的引脚数、极性、坐标都没验
* `footprintUuid` 会跳（同一编号 3 次查询结果可能不同）——strict 模式会误拦，
  只在缓存稳定时用
* 无封装 warning 不挡 emit，但装到 PCB 时仍可能缺焊盘

## 30. P68：块引脚用符号名 + 预检查引脚存在性

### 30.1 27 个 `wire:pin_not_found` 的根因

块里连接器/模块的引脚写的是**逻辑名**（`VCC/GND/PWM/P/N`），而 EDA 符号用
**数字焊盘**（`1/2/3`）。电阻电容恰好写的是 `1/2`，所以只有 8 个连接器/模块
的 27 个脚失败。

**修法**：块零件加 `pin_map`（逻辑名 → 焊盘号），`instantiate` 同时映射
`Part.pins` 与网表引脚。块 JSON 仍可读（逻辑名），设计里是焊盘号。

实测（示例一体-新2，同一份需求）：

```
schematic：101 → 130 个引脚接线；unresolved 27 → 0
```

### 30.2 引脚名表：`design/symbol_pins.json`

**不是手编**：打开真实工程 `hwv-design-9` 的 PCB 文档，读
`pcb_Net.getNetlist('JLCEDA').components[].pinInfoMap` 的键，把
`位号 → lcsc` 对回去，得到 23 个编号的焊盘名表。

**为什么不用原理图网表**：`sch_Netlist.getNetlist()` 读回来是**陈旧的**
（手册 R2），刚写完常常是空串。

### 30.3 预检第三层：引脚存在性

`emit_preflight.check()` 现在三层：

1. 有 lcsc（硬拦）
2. `getByLcscIds` 能解析（硬拦）
3. **设计引脚 ⊆ 符号引脚名**（硬拦，查 `symbol_pins.json`）
   * 编号不在表里 → `unverified_pins`（warning，不假装知道）
   * `--strict-footprint` 仍只影响第 2.5 层（封装关联）

实测：新2 预检 `pin_missing=0 / unverified_pins=0`；`check_p68` 用合成夹具
证明"引脚不在表里 → not_ready"。

### 30.4 仍未闭环：PCB 导入 0 个元件

新2 的 emit：project ok / schematic ok（43 器件、130 引线）/ **pcb 报 ok
但 `components_on_pcb=0`**，parity `checked=0`。隔 2 分钟重开工程仍是 0，
所以不是"读到得早"——`importChanges` 这次没导入任何器件。

同一份代码此前（v3）导入过 43 件；差别是这次只跑三段、schematic 的保存
提交可能还没完成就调了 import。下一步：import 后**轮询到元件数 > 0**，
或按器件数放大 settle，失败就 `not_ready`（现在它错误地报了 ok）。

## 31. P69：PCB 导入「没导全」不许报 ok

### 31.1 症状与根因

示例一体-新2：schematic 43 器件 / 130 引线，`pcb` 段报 **ok** 但
`components_on_pcb=0`、parity `checked=0`。空板被当成了通过（AGENTS §0.1）。

`importChanges` 返回 true 只代表"调用成功"；图元是**陆续落地**的。
旧代码等 20 秒（40×500ms）就继续，43 件的板偶发"只到一半"甚至 0 件，
而收尾无条件 `job.status = "ok"` 把结果盖住。

### 31.2 修法（两条）

1. **轮询按件数放大**：`_poll_tries(need) = 40 + min(40, need)`
   （20 秒起，43 件 → 40 秒）
2. **硬判据**：`_import_verdict(on_board, need)`
   * `0/N` → `not_ready`（"importChanges 返回 true 但 0 个元件"）
   * `x/N` → `not_ready`（"只有 x/N，导入没导全"）
   * `N/N` → `ok`
   * 收尾再核一次 `comps_total < expected_total`，并**保留更具体的那条 note**
     （哪一板 x/N 比总数更有用）

`counts` 里新增 `expected_on_pcb`，报告能直接看出"期望 vs 实际"。

### 31.3 实测

```
_poll_tries(3)=43、_poll_tries(43)=80
_import_verdict: 0/43 → not_ready；20/43 → not_ready；43/43 → ok
fake session：导入 0 件 → pcb not_ready（note「0/2」）
              导入一半 → not_ready（「1/2」）
              全导入   → 不因"导入不足"报错
```

### 31.4 仍未做的事

**没有根因**为什么那次 import 导入了 0 件（可能是保存提交的竞态，也可能
是文档激活问题）。这两条修的是"不许把没导入当成 ok"，不是"保证一定导入"。
要定位还需在失败现场抓 `docPoll.docType` / `steps`。

## 32. 前端 P0–P2（结构 / Shell / Job）

> 方案见 [`docs/frontend-refactor.md`](frontend-refactor.md)。P3/P4 未做。

### 32.1 P0：一个注册点 + 一个数据入口 + 样式分片

| 新增 | 作用 |
|---|---|
| `client/features.ts` | 分区注册表（id/label/panel/order/badge/empty）；`store` 从它派生并重导出 |
| `client/api.ts` | `HwApi`：域命名空间 + 与 `WorkbenchFace` 同名的兼容层；store 只跟它说话 |
| `client/queries.ts` | 按域缓存（entity 30s）+ 前缀失效；`projects` **故意不强缓存**（`reloadProjects` 语义是重新拉） |
| `client/styles/` | tokens / layout / panels / dark 四个字符串模块 |

`check_dead_css` 改为扫分片（只读 `styles.ts` 会把全部规则判成缺失）；
`.hwv-fw-${status}` 改成显式映射（脚本才看得见 `hwv-fw-ok`）；
`.not_run` 的下划线不再被截成 `.not`。

### 32.2 P1：板级汇总 / 三态 / 深链

* 板条：按 `projection2d.parts.board_id` 聚合，点 chip → `openSection('checks', 第一个问题位号)`
* 三态：通过/失败/警告/**未验证** 同排；verdict 徽章把 `unknown` 算黄
* `[` / `]` 切分区（输入框里不抢键）
* `store.openSection(section, ref?)` 与 `select()` 的区别：后者是切换，前者保证选中

### 32.3 P2：发射时间线（进度粒度 = 段）

* Python：`emit(..., on_progress=cb)` 每段结束回调；CLI 回调里**增量写**
  `emit.json`。此前只在最后写，UI 在几分钟里看不到任何进展。
* 客户端：`jobs.ts` 记 running；EmitPanel 8 段时间线（工程/布局规则/原理图/
  PCB/布线/铺铜/连通/产物），每段一个"重跑"（`emitRun(project, stage)`）。
* **边界**：没有 SSE；粒度是一段，不是每颗器件；回调抛错不改发射结论
  （`check_p70` C7 守着）。

### 32.4 验收

```
check_p70  16/16（静态结构 + 回调行为 + 回调抛错）
check_dead_css / check_client_runtime / check_layout_runtime 通过
npm run check / build 通过
59 个离线 check：除环境类 3 条（check_installed 宿主未重启、
check_plugin 既有红、check_p17 真 EDA 在 4 个 lceda 进程下失败）外全绿
```

## 33. 前端 P3/P4（治理面板 / 空态与可访问性）

### 33.1 P3：四个治理面板（动作驱动，结果留在 store）

| 分区 | 后端 | 面板要点 |
|---|---|---|
| `preflight` | `emit-preflight` | 三层（编号/器件/引脚名）；**not_ready 也显示**哪一层没过 |
| `diff` | `readback --diff`（`designLong 600s`，会开立创会话）| 差异表 + 计数；回写只做加法/搬迁 |
| `maturity` | `maturity` | 六维三态 + level/6 + 每维 need |
| `adr` | `adr list` + `adr validate` | 四节校验；缺节用错误色且**不当作通过** |

共同纪律：没结果 → "空白不代表通过"；`governance()` 不走 `edit()`——
后者把 `not_ready` 当失败丢弃，而这几个面板的 not_ready 恰恰是内容。

### 33.2 P4：空态 / 可访问性 / 测试卫生

* 长任务横幅（HwIdeView busy + EmitPanel running）加
  `role="status" aria-live="polite"`：读屏能听到进度，不只看得见。
* 分区切换标题写明 `[ / ]`（键盘提示和数据同源，不另写一份）。
* `check_layout_browser` 断言全过后**显式 `process.exit(0)`**：
  headless Chrome 偶尔留句柄，脚本会"卡住"（CI 里表现为超时）。

### 33.3 验收

```
check_p71 16/16（注册表/网关/契约/store/面板/空态/aria）
check_layout_runtime 新增 4 条（成熟度 3/6、预检 not_ready、回读 pin_moved、ADR 缺节）
check_canvas_draw / check_assembly3d / check_layout_browser 通过
check_dead_css 绿
```
