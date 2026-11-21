# 01 · 设计空间（`design.json`）

## 一份设计长什么样

```
DesignModel
├── id / title / board（主参考板）
├── boards[]        多板时用；每块板有自己的局部坐标系
├── parts[]         元件：ref（稳定 id）、kind、size_mm、physical_source、placement
├── nets[]          网表：{"net": 名, "pins": [{"ref","pin"}]}
├── pins            引脚来源三档（见下）
├── joints_list     机械关系（可选，P15）
├── program         时序（可选）
├── sim_params      仿真参数覆盖（可选）
└── physical_tests  实物测试记录（可选，P21）
```

## 三种状态，别混

| 状态 | 写法 | 含义 |
|---|---|---|
| **声明** | 字段有值 | 人说的 / 导入器读到的 |
| **推断** | `source="inferred"` | 机器猜的，**不参与硬检查** |
| **缺** | 字段**不存在**（不是 0、不是空串） | 没人说过 |

`size_mm` 给 `{"w":0,"h":0,"d":0}` 是**声明"尺寸为零"**，不是"不知道"。
不知道就**别写这个键**。`mass_g`、`inertia`、`com_me` 同理：`None` ≠ `0`。

`physical_source` 的粒度（越往后越可信）：

```
estimated(0) < catalog(1) < datasheet(2) < measured(3)
```

## 什么时候不该填

* 没有网表 → **不要**编 `nets`。`示例-无人机` 的 44 个元件就是 0 网表，
  它能被判、能出 2D，但归因只能是 `connected`（"它连着什么"），
  不可能是 `provides_bus`（"它提供 I2C"）。
* 型号是 EasyEDA 哈希（`kind="5e2c0505ab5b0d1e"`）→ 不要为它编一张映射表。
  用户已决定不为哈希 kind 补映射（AGENTS §1.3）。
* 拿不准封装 → `package_sizes.lookup()` 返回 `unknown` 就留空，别写近似值。

## 一条自检

```
python -m hw_velxio.design_cli get <id>
```

看 `design` 里每个键**是不是都有人负责**。多出来的键、互相矛盾的键
（`pins` 有 8 个但 `size_mm` 是 0），都是下一步会报错的根源。
