# agent 文档 —— 给"要在本仓库里动手的模型"看的设计空间说明

> 照 RoboBuilder 的做法：**设计空间写给人看，也写给模型看**。
> 模型不是靠猜的，它读这份文档知道"能做什么、做完怎么验、哪里不许碰"。

## 铁律（先读这四条，其余都可以慢慢看）

1. **判定只有一份**：所有结论来自 `python -m hw_velxio.design_cli <cmd>` 的一行 JSON。
   不要自己算"应该能通过"。
2. **缺失 ≠ 通过**。`unknown` / `not_ready` / `not_verified` 都不是 pass。
   看到一个空表，先问"是没人查，还是查了没有"。
3. **写 design.json 只有一条路**：`hw_put`（过 `DesignModel.validate()`）。
   没有 `setpart` / `move` / `delpart`——改设计就整份提交。
4. **不可逆决定要问人**：`deferred lock` 一旦锁上就没有 unlock。

## 文档索引

| 文件 | 讲什么 |
|---|---|
| `01-design-space.md` | `design.json` 的字段、三种状态（声明/推断/缺）、什么不该填 |
| `02-judging.md` | 阶梯 V0–V8、断言层、归因，以及**怎么读它们的结论** |
| `03-gates.md` | 哪些闸门不能绕、每条闸门的出口在哪 |
| `04-recipes.md` | 三个常见任务的正确做法（含反例） |

## 你能用的工具（白名单，14 个）

```
读     hw_list  hw_get  hw_snapshot  hw_generate
判定   hw_ladder  hw_claims  hw_justify  hw_handoff  hw_links  hw_units  hw_sim
写     hw_put（唯一写 design.json）  hw_emit（产出立创工程，不改设计）
人确认 hw_promote（任何模式都要问）
```

**没有**细粒度改动工具。这是有意的：改一个 `part.ref` 会让归因、断言、
实测记录同时指向不存在的件，而 `validate()` 看不到这件事。
