# 02 · 怎么判定，以及怎么读结论

## 阶梯（`hw_ladder`）

| 层 | 查什么 | 硬失败截断下游？ |
|---|---|---|
| V0 | 符号：引用完整性（ref/pin/net 存在） | **是** |
| V1 | 约束：绝对最大值、极性 | **是** |
| V2 | 几何：重叠、超界、高度 | 否 |
| V3 | 电气 DC：轨压、负载 | 否 |
| V4 | 时序 | 否 |
| V5 | 数字仿真 | 否（通常 `not_run`） |
| V6 | SPICE | 否（通常 `not_run`） |
| V7 | 运动（MuJoCo + 三次扰动） | 否，**opt-in**（`--with-sim`） |
| V8 | 制造（要 P17 的立创工程） | 否 |

**只有 V0/V1 会截断**。V2 挂了不会让 V3 变成 `not_run`——几何重叠不影响
电气结论。看到 `stopped_at=""` 而 V2 是 `fail`，那是**对的**。

## 三种状态

`pass` / `fail` / `unknown`（层内还有 `not_run`）。
`unknown` 是"判不出"，不是"没问题"。**空结果不算 pass。**

## 断言层（`hw_claims`）

每个元件"为什么在板上"变成一条可执行断言。三态 + **语义覆盖率**：

```
connected        结构上连着（最弱）
provides_default_level   有默认电平（复位后不会浮空）
signal_*         数据线
power_*          供电
```

只有 `connected` 全绿，说明断言只查了"有没有线"——那是**假绿**。
看 `coverage`：语义覆盖率低就说明这一层没在干活。

## 归因（`hw_justify`）

从拓扑反推"它为什么在板上" + "删了会怎样" + 置信度。
判不出时返回 `unknown` 并说明原因（**不编理由**）。
两个比例要看：全部元件口径 / 有网表元件口径。两者差得远 = 缺数据，不是算法不行。

## 交接单（`hw_handoff`）

六类未验证项：`blocking` / `missing_data` / `assumed` / `unverifiable` /
`orphan` / `external`。

* `deliverable` **只看 `blocking`**——其余五类是"告诉你风险"，不是"不许打板"
* `external` 里是别人的判定（立创 DRC、MuJoCo 轨迹、实测记录）；
  混进 `blocking` 会让人分不清"谁说的"
