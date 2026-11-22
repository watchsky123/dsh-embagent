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
python -m hw_velxio.scripts.check_xxx        # 必须 FAIL
cp impl.bak impl.py && rm impl.bak
python -m hw_velxio.scripts.check_xxx        # 必须 PASS
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
| 在仓库根跑 | `pwd` 应是含 `hw_velxio/` 的目录 |
| 工程存在 | `python -m hw_velxio.design_cli list` |
| 产物已生成 | 看 `<工程>/ladder.json` 等是否在 |
| 判定器返回 dataclass | 用 `.to_dict()`，不是下标访问 |
| 中文限制 | 字符串值里不嵌中文引号 |
| 闸门有出口 | 写 `continue` 时同时写确认通道 |

### 8.7 验收脚本清单（一条命令验一层）

```bash
cd <repo-root>                    # 必须在这里，不在 hw_velxio/

npm test                                         # 755 项单测
python -m hw_velxio.scripts.check_m1             # M1 四条硬验收
python -m hw_velxio.scripts.check_units_links    # P5
python -m hw_velxio.scripts.check_generate       # P6
python -m hw_velxio.scripts.check_m3             # M3 P7+P8
python -m hw_velxio.scripts.check_ladder         # 阶梯
python -m hw_velxio.scripts.check_justify        # 归因
python -m hw_velxio.scripts.check_handoff        # 交接
python -m hw_velxio.scripts.check_m18            # P18 统一判定
python -m hw_velxio.scripts.check_p17            # P17 立创发射（真机那几条要装 lceda）
python -m hw_velxio.scripts.check_p19            # P19 元件目录（真机那两条要联网）
python -m hw_velxio.scripts.check_p20            # P20 固件（真编译那条要 gcc）
python -m hw_velxio.scripts.check_p21            # P21 闭环（真 MuJoCo 那条可选）
python -m hw_velxio.scripts.check_p22            # P22 agent 循环（全假模型，0 token）
python -m hw_velxio.scripts.check_p23            # P23 测试规模与降级路径（子进程真跑）
python -m hw_velxio.scripts.check_p0             # P0 结论层（前端契约 + 排序 + 过期）
python -m hw_velxio.scripts.check_p1             # P1 下一步动作（映射表 + 前端接线）
python -m hw_velxio.scripts.check_p2             # P2 按对象聚合（索引 + 画像 + 三态）
python -m hw_velxio.scripts.check_p3             # P3 参数敏感度（接线扫描 + 两层扰动）
python -m hw_velxio.scripts.check_p4             # P4 固件矩阵（每脚一行 + 四类状态）
python -m hw_velxio.scripts.check_p5             # P5 需求草稿 + 从零开局（38 条）
python -m hw_velxio.scripts.check_p26            # 几何/连通/自研布线/布局/通道（28 条，全离线）
python -m hw_velxio.scripts.check_p40            # 机械模型 mjcf（15 条：构建/真加载/三态/退化）
python -m hw_velxio.scripts.check_p41            # 四域 spec（21 条：来源分级/声明优先/归并/往返）
python -m hw_velxio.scripts.check_p42            # 关节提案（12 条：有依据才提案/不编参数/应用后进 MJCF）
python -m hw_velxio.scripts.check_p43            # 编排器（12 条：六段/不中止/确定性/发射隔离/退化）
python -m hw_velxio.scripts.check_p44            # 机械判据（16 条：干涉/重心/扭矩/行程/并入 handoff/退化）
```

**立创侧诊断脚本**（不是验收，是「说不清就量一遍」用的）：

```bash
python -m hw_velxio.scripts.probe_route_geom     --session <sid>   # 元件/焊盘/板框/走线 + autoRouting
python -m hw_velxio.scripts.probe_route_session  --path x.eprj3    # 同一工程不同窗口来源对照
python -m hw_velxio.scripts.probe_migrate                          # §15.1 云槽位/save 真值 + §15.3 迁移配方
python -m hw_velxio.scripts.lceda_probe                            # 版本/能力表/会话
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
from hw_velxio.design.store import DesignStore
d=DesignStore('m1_p2_accept').load()
print([p.ref for p in d.parts])   # 应等于 ['U1','R1','D1']
"

# 2) 恢复被验收改动的真实工程（links_list 必须清空）
python -c "
import sys; sys.path.insert(0,'.')
from hw_velxio.design.store import DesignStore
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
