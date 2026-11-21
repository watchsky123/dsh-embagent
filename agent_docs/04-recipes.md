# 04 · 三个常见任务的正确做法

## (A) 从需求生成一个新设计

```
hw_generate(requirement="examples/req_car.yaml", max_candidates=3, materialize=2)
  → candidates[0].design   ← 完整设计，可直接 put
hw_put(project="bot", design=<上面那份>)
hw_ladder(project="bot")       ← 看 V0/V1 有没有挂
hw_handoff(project="bot")      ← 看六类未验证项
```

**反例**：让模型自己手写 `design.json`。它会把 `pin` 写成 `"PA0"`（网表里
要的是引脚名 `"1"`），或者给 44 个元件编出一张网表。`put` 会过 `validate()`，
但 `validate()` 查不出"引脚名是编的"——那是 V0 的活。

## (B) 判定挂了，怎么改

```
hw_ladder → 找到 fail 的那一层
hw_claims → 看断言为什么红（哪条、哪个件、期望 vs 实际）
hw_justify(ref=U3) → 看这个件到底在干什么
```

改，然后**整份** `hw_put`，再 `hw_ladder`。

**反例**：看到 V3 挂了就把电压改小。先看 `electrical` 的 `basis`——
它可能是"零负载轨"（没声明电流），那不是设计错，是**缺数据**。

## (C) 实物测出问题之后

```
physical(project, --record --test="爬台阶" --result=fail --observed="右前轮打滑" \
         --implicated=ref:W2 --reproduced=false)
calibrate(project, --test=pt-001 --candidates='[{"mu_tyre_rod":0.57}]')
contact-sheet <video>      ← 有 ffmpeg 就出 tile 缩略图
```

**采纳规则**：只有"旧设计在新参数下复现录像里的失败"才采纳。
"新设计能过"**不是**采纳条件——那会让参数变成作弊工具。

**反例**：把 `mesh_mu` 调到 0 让一切"稳"。旧设计在新参数下也过了，
说明参数被调松了，不是解释了什么。
