# 03 · 闸门：哪些不能绕，出口在哪

本仓库每一条"不许"都配了一条"要怎样才能"。**只有闸门没有出口 = 功能不可用**。

| 闸门 | 它拦什么 | 出口 |
|---|---|---|
| `inferred` 不进硬检查 | 推断的链路/能力/关节 | 人把它改成 `declared`（`promote`） |
| `capability.candidates` 只收 datasheet/measured | catalog 级型号不能选型 | `parts-promote <lcsc> --datasheet=<url> --role= --needs=` |
| `generate` 只对新工程有效 | 不为哈希 `kind` 补映射 | 用户补 `lcsc`（P19 的目录能查） |
| `emit` 器件要 `lcsc`/`mpn` | 立创只认 LCSC 编号 | `parts-enrich` 回填 |
| 校准只采纳"旧设计复现" | 不能拿参数作弊 | 换一组能复现旧失败的参数 |
| `deferred lock` 不可撤销 | 不可逆决定 | **没有 unlock**：锁之前想清楚 |
| 写 design.json 只走 `put` | 细粒度改动绕过 validate | 整份提交 |

## 三类"看起来像成功"的失败

1. **空表**：某类检查 0 条。要问"没人查"还是"查了没有"。
   每类必须同时给 `checked` 与 `basis`。
2. **默认值**：`coverify` 里 7 条 `not_verified`（指向精度 1.0° 之类全是默认值）。
   机器替你做主了，不是"没问题"。
3. **外部工具通过**：空板 DRC 当然通过；`route=native` 引擎拒绝时
   不能报成功。**先看有没有东西可查**。

## 当你确实做不到

正确做法（照 AGENTS §7）：

1. 返回 `not_ready` + **具体哪一步被拒**（附错误行）
2. 把能做到的部分做成**可外部执行的产物**（`.resc`、命令行）
3. 在验收脚本里写明"这一条没做到，原因是什么"

反面：把 `not_run` 记成 `pass`，或写一个永远为真的断言来"通过"验收。
