"""生成 P23 的 15 个合成夹具 —— **可重跑**，产物是普通 `design.json`。

    python -m hw_velxio.scripts.gen_fixtures

## 为什么要留这个生成器

15 个夹具手写 15 份 JSON 会有 15 种风格（int / float 混用、字段顺序不同），
而夹具的用处是**当基准**：基准本身风格不一致，往返测试会先被它绊倒
（实测：第一版 15 个往返全红，原因是文件里 `0` 与 `from_dict` 后的 `0.0`）。

所以夹具由代码生成，并在最后做一次**规范化**：
`file == from_dict(file).to_dict()`。这个性质由 `test_phases` 的往返用例守住。

## 每个夹具存在的理由

见 `tests/test_phases.py` 的 `FIXTURE_EXPECT`（clean / validate / geometry）。
这里只管**生成**，不管"它应该被判成什么"——那是测试的断言，不是生成器的职责。
"""
import json
from pathlib import Path
from hw_velxio.design.model import Board, DesignModel, Joint, Part, Placement, Program

OUT = Path(__file__).resolve().parents[1] / 'tests' / 'fixtures' / 'designs'
OUT.mkdir(parents=True, exist_ok=True)

def part(ref, kind, *, x=10.0, y=10.0, side="top", w=5.0, h=5.0, d=1.0,
         source="datasheet", pins=None, board_id="", lcsc=None, role="component",
         mass_g=None):
    return Part(ref=ref, kind=kind, label=kind, role=role,
                size_mm={"w": w, "h": h, "d": d}, physical_source=source,
                placement=Placement(x_mm=x, y_mm=y, side=side, board_id=board_id),
                pins=list(pins or []), lcsc=lcsc, mass_g=mass_g)

def base(title, *, w=60.0, h=40.0, thick=1.6, kind="generic"):
    d = DesignModel(title=title)
    d.board = Board(kind=kind, name=title, outline=[(0,0),(w,0),(w,h),(0,h)],
                    size_mm={"w": w, "h": h}, thickness_mm=thick)
    return d

def save(d, name):
    path = OUT / f"{name}.json"
    canon = DesignModel.from_dict(d.to_dict()).to_dict()  # 规范化
    path.write_text(json.dumps(canon, ensure_ascii=False, indent=1), encoding='utf-8')
    print(f"{name:22} parts={len(d.parts):3} nets={len(d.nets):2} boards={len(d.board_list())} -> {path.stat().st_size}B")

# 1. 最小冒烟
d = base("tiny-blink")
d.add_part(part("U1","mcu", x=20, y=20, pins=["1","2","3","4"]))
d.add_part(part("R1","resistor", x=35, y=20, w=1.0, h=0.5, d=0.5, pins=["1","2"]))
d.add_part(part("D1","led", x=45, y=20, w=2.0, h=1.2, d=0.8, pins=["1","2"]))
d.connect("D13", ("U1","1"), ("R1","1"))
d.connect("LED_A", ("R1","2"), ("D1","1"))
d.connect("GND", ("D1","2"), ("U1","2"))
save(d, "tiny-blink")

# 2. 密集 0402
d = base("dense-0402", w=80, h=60)
d.add_part(part("U1","mcu", x=10, y=10, pins=[str(i) for i in range(1,9)]))
for i in range(20):
    d.add_part(part(f"C{i+1}","capacitor", x=20+(i%10)*5.5, y=20+(i//10)*8,
                    w=1.0, h=0.5, d=0.5, pins=["1","2"]))
    d.connect(f"N{i+1}", ("U1", str(i%8+1)), (f"C{i+1}","1"))
    d.connect("GND", (f"C{i+1}","2"))
save(d, "dense-0402")

# 3. 叠板（两块板 z 方向叠放）
d = base("stacked-2layer", w=50, h=40)
d.boards = [
    Board(id="B_top", kind="generic", name="上层", outline=[(0,0),(50,0),(50,40),(0,40)], size_mm={"w":50,"h":40}),
    Board(id="B_bot", kind="generic", name="底层", outline=[(0,0),(50,0),(50,40),(0,40)], size_mm={"w":50,"h":40}),
]
d.board = d.boards[0]
d.add_part(part("U1","mcu", x=25, y=20, board_id="B_top", pins=["1","2","3","4"]))
d.add_part(part("U2","sensor", x=25, y=20, board_id="B_bot", pins=["1","2"]))
d.connect("SCL", ("U1","1"), ("U2","1"))
d.connect("SDA", ("U1","2"), ("U2","2"))
d.joints_list = [Joint(a="B_top", b="B_bot", kind="stack").to_dict()]
save(d, "stacked-2layer")

# 4. 折叠铰链
d = base("folded-hinge", w=40, h=30)
d.boards = [
    Board(id="B_left", kind="generic", name="左", outline=[(0,0),(40,0),(40,30),(0,30)], size_mm={"w":40,"h":30}),
    Board(id="B_right", kind="generic", name="右", outline=[(0,0),(40,0),(40,30),(0,30)], size_mm={"w":40,"h":30}),
]
d.board = d.boards[0]
d.add_part(part("U1","mcu", x=20, y=15, board_id="B_left", pins=["1","2","3","4"]))
d.add_part(part("S1","sensor", x=20, y=15, board_id="B_right", pins=["1","2"]))
d.connect("SPI_MOSI", ("U1","1"), ("S1","1"))
d.connect("SPI_SCK", ("U1","2"), ("S1","2"))
d.joints_list = [Joint(a="B_left", b="B_right", kind="hinge",
                       params={"axis": "z", "min_deg": -90, "max_deg": 90}).to_dict()]
save(d, "folded-hinge")

# 5. 四旋翼
d = base("quadrotor", w=120, h=120)
d.add_part(part("U1","mcu", x=2, y=2, pins=["1","2","3","4","5","6","7","8"]))
for i,(mx,my) in enumerate([(20,20),(72,20),(20,72),(72,72)]):
    d.add_part(part(f"M{i+1}","motor", x=mx, y=my, w=28, h=28, d=10, pins=["1","2"],
                    mass_g=30.0))
    d.connect(f"PWM{i+1}", ("U1", str(i+1)), (f"M{i+1}","1"))
    d.connect("GND", (f"M{i+1}","2"))
d.add_part(part("B1","battery", x=50, y=50, w=20, h=20, d=10, pins=["1","2"], mass_g=120.0))
d.connect("BATT", ("B1","1"), ("U1","8"))
d.program = Program(loop=True, units="normalized",
                    steps=[{"t_s": 0.0, "actuator": "M1", "value": 0.5}]).to_dict()
save(d, "quadrotor")

# 6. 多层板（4 层）
d = base("multilayer-4", w=70, h=50, thick=1.6)
d.add_part(part("U1","mcu", x=35, y=25, pins=["1","2","3","4"]))
d.add_part(part("R1","resistor", x=15, y=25, w=1.6, h=0.8, d=0.8, pins=["1","2"]))
d.connect("IO", ("U1","1"), ("R1","1"))
d.connect("GND", ("R1","2"), ("U1","2"))
d.stackup = {"copper_layers": 4, "thickness_mm": 1.6}
save(d, "multilayer-4")

# 7. 超出板框
d = base("out-of-board", w=40, h=30)
d.add_part(part("U1","mcu", x=10, y=10, pins=["1","2"]))
d.add_part(part("U2","sensor", x=39.5, y=29.5, w=8, h=8, pins=["1","2"]))
d.connect("SDA", ("U1","1"), ("U2","1"))
save(d, "out-of-board")

# 8. 同面重叠
d = base("overlap-parts", w=60, h=40)
d.add_part(part("U1","mcu", x=20, y=20, w=10, h=10, pins=["1","2"]))
d.add_part(part("U2","sensor", x=21, y=21, w=10, h=10, pins=["1","2"]))
d.connect("SDA", ("U1","1"), ("U2","1"))
save(d, "overlap-parts")

# 9. 极性反接（V1 硬失败）
d = base("reversed-polarity", w=50, h=40)
d.add_part(part("U1","mcu", x=25, y=20, pins=["1","2"], lcsc="C8734"))
d.parts[0].electrical = {"v_in": 12.0, "v_abs_max": 3.6}
d.add_part(part("P1","connector", x=10, y=20, w=8, h=6, pins=["1","2"]))
d.connect("VCC", ("P1","1"), ("U1","1"))
d.connect("GND", ("P1","2"), ("U1","2"))
save(d, "reversed-polarity")

# 10. 有件无网表
d = base("no-netlist", w=50, h=40)
for i in range(4):
    d.add_part(part(f"U{i+1}","module", x=10+i*10, y=20, pins=["1"]))
save(d, "no-netlist")

# 11. 单脚网
d = base("single-pin-net", w=40, h=30)
d.add_part(part("U1","mcu", x=20, y=15, pins=["1","2"]))
d.connect("TEST", ("U1","1"))
d.connect("GND", ("U1","2"))
save(d, "single-pin-net")

# 12. 重复 ref（validate 必须拒）
d = base("dup-ref", w=40, h=30)
d.add_part(part("U1","mcu", x=10, y=10, pins=["1"]))
d.parts.append(part("U1","sensor", x=30, y=10, pins=["1"]))
save(d, "dup-ref")

# 13. USB 差分对
d = base("diffpair-usb", w=50, h=40)
d.add_part(part("U1","mcu", x=20, y=20, pins=["1","2","3","4"]))
d.add_part(part("J1","connector", x=40, y=20, w=9, h=7, pins=["1","2","3","4"]))
d.connect("USB_D+", ("U1","1"), ("J1","1"))
d.connect("USB_D-", ("U1","2"), ("J1","2"))
d.connect("GND", ("U1","3"), ("J1","3"))
save(d, "diffpair-usb")

# 14. 三板
d = base("many-boards", w=40, h=30)
d.boards = [
    Board(id=f"B{i}", kind="generic", name=f"板{i}", outline=[(0,0),(40,0),(40,30),(0,30)],
          size_mm={"w":40,"h":30}) for i in range(1,4)
]
d.board = d.boards[0]
for i in range(3):
    d.add_part(part(f"U{i+1}","mcu" if i==0 else "sensor", x=20, y=15,
                    board_id=f"B{i+1}", pins=["1","2"]))
d.connect("SDA", ("U1","1"), ("U2","1"))
d.connect("SCL", ("U1","2"), ("U2","2"))
save(d, "many-boards")

# 15. 云台相机
d = base("gimbal-camera", w=50, h=50)
d.add_part(part("U1","mcu", x=30, y=6, pins=["1","2","3","4"]))
d.add_part(part("CAM1","camera", x=25, y=25, w=21, h=21, d=5, pins=["1","2","3"], mass_g=8.0))
d.add_part(part("SV1","servo", x=2, y=2, w=23, h=12, d=22, pins=["1","2","3"], mass_g=9.0))
d.connect("SCCB_SIOC", ("U1","1"), ("CAM1","1"))
d.connect("SCCB_SIOD", ("U1","2"), ("CAM1","2"))
d.connect("PWM", ("U1","3"), ("SV1","1"))
d.connect("VCC", ("U1","4"), ("SV1","2"))
save(d, "gimbal-camera")
