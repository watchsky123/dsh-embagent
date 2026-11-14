"""hw_velxio — HWForge × Velxio 联合工作台。

这个包是 DSH 插件 ``dsh-hw-velxio`` 的 **Python 桥接层**。
它把两个引擎放进同一个 "一次调用、一行 JSON" 的契约里：

* **HWForge 引擎**（跨层一致性判定）：直接复用 ``hwforge.blocks`` 的 14 个积木，
  通过 ``hwforge.blocks_cli`` 同款入口调度。
* **Velxio 引擎**（真实编译 / 仿真 / 库 / 电路）：通过 HTTP 调用一个**已运行**的
  Velxio FastAPI 后端；电路 JSON ↔ Wokwi 的纯计算则优先复用 Velxio 的
  ``app.mcp.wokwi``，不可用时降级为 HTTP。

设计纪律（继承自 hwforge，不得违反）：

* **三种状态，不是两种**：``ok`` / ``not_ready`` / ``failed``。前置缺失是顺序问题，
  不是失败；把 not_ready 压成 failed 会让模型去修不该修的东西。
* **不伪造产物**：Velxio 后端不可达时如实报 not_ready / failed，绝不返回假 hex。
* **判定只能来自确定性检查器**：模型/桥接层不产生 verdict，只搬运检查器的结论。
* **不猜**：未知接口记 UNRESOLVED，缺失值记 not_verified，而不是 0 或 true。

入口：``python -m hw_velxio.bridge <command> [args...]``（见 ``bridge.py``）。
"""

__version__ = "0.1.0"
