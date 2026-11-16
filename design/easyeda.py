"""立创 EDA Pro V3 格式解析 —— 按**官方规范**读出真实坐标与网表。

## 规范来源（不是猜的）

`D:/IE/ES/PCB/lceda-pro/docs/easyeda-format.md` 与客户端自带的
`lceda-pro doc format --class-name <T>`。关键几条：

1. **每行是两个 JSON，用 `||` 拼接**：外层 `{type,id,ticket}`（一致性框架），
   内层是原子 key-value 数据。
2. **最终一致性**：同 `type`+`id` 时保留 **ticket 更大**的记录；删除是内层
   置空字符串 `||""`。
3. **单位**：原理图（`SCH_PAGE`/`SYMBOL`）坐标用 **10 mil**；PCB
   （`PCB`/`FOOTPRINT`）用 **mil**。布尔用 `1`/`0`，颜色 `#RRGGBB`。
4. **元件**（`TMPcbComponent`）：`x`/`y` 单位 mil，`angle` 逆时针为正。
5. **网表**（`TPadNetWire`）：一行 `PAD_NET`，id 是键式数组串
   `["PAD_NET", componentId, padNum, padId]`，载荷 `padNet` 是网络名。
   **连接关系只能从这里读**——`TNet` 只存网络级设置，不描述连接。

上一版解析器按 `|` 切分并靠"有没有 type"过滤，把 `||` 的第二段当成独立
记录，因此坐标与网表全部丢失。这一版按规范重写。

@module hw_velxio.design.easyeda
"""

from __future__ import annotations

import collections
import json
import math
import re
import zipfile
from pathlib import Path
from typing import Any

# 一行里的两个 JSON 用 `||` 分隔；行与行之间也用 `|` 收尾。
# 规范写法：`{...}||{...}|` —— 所以按 `|` 切分后**成对出现**。
RECORD_SEPARATOR = "|"

# PCB 文档里长度单位是 mil；换算到 mm。
MIL_TO_MM = 0.0254
# 原理图文档里单位是 10 mil。
SCHEMATIC_UNIT_MM = 10 * MIL_TO_MM

# 文档类型头，标识一个文档的类别（`DOCTYPE` 记录）。
DOC_TYPES = ("SCH_PAGE", "SYMBOL", "FOOTPRINT", "DEVICE", "PCB", "PANEL")

# 原理图侧（SCH_PAGE）的 ATTR 携带**元件值与 3D 模型尺寸**，这是补全
# "值 + 尺寸"的唯一来源：
#
#   `Value`               → 1uF / 100nF / 237kΩ（真实值）
#   `3D Model Transform`  → 9 个逗号分隔参数，前三项是 sizeX/Y/Z（单位 mil）
#   `3D Model Title`      → 封装名，形如 `C0603_L1.6-W0.8-H0.8`
#
# PCB 侧只有 `Device` 哈希（指向库），所以必须按 `Designator` 把
# 原理图的属性桥接到 PCB 元件上。
SCH_ATTRS = {
    "Value": "value",
    "Comment": "comment",
    "3D Model Transform": "transform_3d",
    "3D Model Title": "model_title",
}

# 属性键 → 统一字段名。ATTR 行是 `parentId` 指向图元的 key/value。
WANTED_ATTRS = {
    "Designator": "designator",
    "Value": "value",
    "Comment": "comment",
    "3D Model Transform": "transform_3d",
    "3D Model Title": "model_title",
    "DeviceName": "device",
    "Device": "device",
    "Footprint": "footprint",
    "FootprintName": "footprint_name",
    "Value": "value",
    "Name": "name",
    "Supplier Part": "supplier_part",
    "Manufacturer Part": "manufacturer_part",
    "LCSC Part Name": "lcsc",
    "3D Model": "model_3d",
    "Datasheet": "datasheet",
    "Description": "description",
}


class EasyEdaError(Exception):
    """`.epro2` 结构不符合预期。"""


def _open_archive(path: Path) -> zipfile.ZipFile:
    try:
        return zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        raise EasyEdaError(
            f"{path.name} 不是 ZIP（若是 .eprj2/.eprj 则是 SQLite，需客户端转换）：{exc}"
        ) from exc


def _read_project(archive: zipfile.ZipFile) -> dict[str, Any]:
    try:
        data = json.loads(archive.read("project2.json").decode("utf-8", "replace"))
    except (KeyError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _iter_lines(archive: zipfile.ZipFile) -> Any:
    """逐行产出 `.epru` 的文本行。

    **行的边界是换行符，不是 `|`**。真实文件形态：

        {"type":"DOCHEAD","ticket":629}||{"docType":"FOOTPRINT",...}|\n
        {"type":"LAYER","ticket":1,"id":"[...]"}||{"layerType":"TOP",...}|\n

    行内两个 JSON 用 `||` 连接、行尾带一个 `|`；**行与行之间是 `|\n`**。
    第一版按 `|` 切分，于是每行的内层数据被切成了下一轮的第一个片段
    ——`INNER` 全是 None，坐标与网表全部丢失。这是"看起来解析成功、
    实际什么都没读到"的典型。
    """
    names = [n for n in archive.namelist() if n.endswith(".epru")]
    if not names:
        raise EasyEdaError("工程里没有 .epru（可能只有图片/元数据）")
    with archive.open(names[0]) as handle:
        buffer = ""
        while True:
            chunk = handle.read(1 << 20).decode("utf-8", "replace")
            if chunk == "":
                break
            buffer += chunk
            lines = buffer.split("\n")
            buffer = lines.pop()          # 最后一段可能不完整
            for line in lines:
                if line.strip() != "":
                    yield line
        if buffer.strip() != "":
            yield buffer


def _split_pair(line: str) -> tuple[dict[str, Any] | None, Any]:
    """把一行拆成 `(外层, 内层)`。

    行形态：`{outer}||{inner}|` 或 `{outer}||""|`（删除）。
    按 `||` 切一刀——这是规范定义的分隔符；**不要按单 `|` 切**（JSON 里
    字符串可能含 `|`，而且行尾也有一个）。
    """
    text = line.strip()
    # 去掉行尾的 `|`
    if text.endswith("|"):
        text = text[:-1]
    if text.startswith("{"):
        # 用括号配平找外层的结束位置（字符串里的括号要跳过）。
        end = _json_end(text)
        if end < 0:
            return None, None
        try:
            outer = json.loads(text[:end])
        except json.JSONDecodeError:
            return None, None
        if not isinstance(outer, dict):
            return None, None
        rest = text[end:]
        if rest.startswith("||"):
            rest = rest[2:]
        rest = rest.strip()
        if rest == "":
            return outer, None
        if rest == '""':
            return outer, ""          # 删除标记
        if rest.startswith("{"):
            try:
                inner = json.loads(rest)
            except json.JSONDecodeError:
                return outer, None
            return outer, inner if isinstance(inner, dict) else None
        return outer, None
    return None, None


def _json_end(text: str) -> int:
    """返回 `text` 里第一个完整 JSON 对象的结束下标（含 `}`），失败返回 -1。"""
    depth = 0
    in_string = False
    escape = False
    for index, char in enumerate(text):
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index + 1
    return -1


def _iter_records(archive: zipfile.ZipFile) -> Any:
    """产出 `(外层, 内层)`，并做**最终一致性**归并。

    规范：同 `type` + `id` 保留 `ticket` 更大的记录；删除（内层 `""`）参与
    同一比较。**但顺序必须保住**：`_doc` 由文件物理顺序决定（每个
    `DOCHEAD` 之后的所有记录属于该文档），一旦把记录丢进字典再取出，
    文档顺序就乱了——上一版正是如此，`_doc` 全是 `None`，于是"按 PCB
    过滤"把 264 个元件全滤掉了。

    做法：先按原顺序扫一遍，记录每个 `(type,id)` 的最大 ticket 与最终
    载荷；再按**第一次出现的位置**重放，用最终载荷。
    """
    # 第一遍：归并（同 doc+type+id 取最大 ticket），同时记录首次出现顺序。
    #
    # **归并键必须带文档。** 官方规范只说"type 和 id 相同时保留 ticket 更大的"，
    # 但 `id` 的作用域是**文档内**——实测：META 原始 1052 条，按 (type,id)
    # 全局去重后只剩 **1** 条（每个文档的 META 都用同一个 id），于是"只有 1 个
    # DEVICE 文档带 META"这个症状出现了；PAD 2746 条塌成 490 条，封装几何
    # 只剩零头。带文档作用域后：META 1052、PAD 2746、COMPONENT 2668 全部保留。
    order: list[tuple[str, str, str]] = []
    best: dict[tuple[str, str, str], tuple[int, Any, str]] = {}
    blocks: list[tuple[int, dict[str, Any], Any]] = []   # 块级记录（DOCHEAD 等）
    position = 0
    current_doc = ""
    for piece in _iter_lines(archive):
        outer, inner = _split_pair(piece)
        if outer is None:
            continue
        record_type = outer.get("type", "")
        if record_type == "DOCHEAD" and isinstance(inner, dict):
            # 文档实例用 docType + uuid 区分：一个工程有 11 个 PCB 文档
            #（拼板），只记 docType 会让 7 块板的元件混成一个巨板。
            doc_type = str(inner.get("docType") or "")
            uuid = str(inner.get("uuid") or "")
            current_doc = f"{doc_type}#{uuid}" if uuid else doc_type
            blocks.append((position, {**outer, "_doc": current_doc}, inner))
            position += 1
            continue
        record_id = outer.get("id")
        ticket = outer.get("ticket")
        if not isinstance(record_id, str) or not isinstance(ticket, int):
            blocks.append((position, {**outer, "_doc": current_doc}, inner))
            position += 1
            continue
        key = (current_doc, record_type, record_id)
        if key not in best:
            order.append(key)
        previous = best.get(key)
        if previous is None or ticket >= previous[0]:
            best[key] = (ticket, inner, current_doc)
        position += 1

    # 第二遍：按首次出现顺序重放，插入到对应文档段落里。
    out: list[tuple[int, dict[str, Any], Any]] = list(blocks)
    for key in order:
        ticket, inner, doc = best[key]
        out.append((0, {"type": key[1], "id": key[2], "ticket": ticket,
                        "_doc": doc}, inner))
    # 块级记录保留原位置语义（文档头在前），其余按 key 排序稳定即可：
    # `_doc` 已随每条记录携带，不再依赖重放顺序。
    for _, outer, inner in out:
        yield outer, inner


def _iter_raw_records(archive: zipfile.ZipFile) -> Any:
    """不去重的原始顺序（用于统计与调试）。"""
    for piece in _iter_lines(archive):
        outer, inner = _split_pair(piece)
        if outer is not None:
            yield outer, inner


def _doc_kind(outer: dict[str, Any]) -> str:
    """从文档头判断文档类型（PCB / SCH_PAGE / FOOTPRINT / DEVICE…）。"""
    for key in ("docType", "type"):
        value = outer.get(key)
        if isinstance(value, str) and value in DOC_TYPES:
            return value
    return ""


def parse(path: str | Path) -> dict[str, Any]:
    """解析 `.epro2`，返回**真实坐标与网表**。

    返回：

        {
          "status": "ok",
          "title", "editor",
          "components": [{ref, value, footprint, x_mm, y_mm, angle_deg,
                          size_mm?, nets: {padNum: netName}}],
          "nets": {netName: [ "REF.pad", ... ]},
          "counts": {...},
          "units": {"pcb": "mil", "schematic": "10mil"}
        }
    """
    file = Path(path)
    if not file.is_file():
        return {"status": "not_ready", "findings": [f"文件不存在：{file}"]}
    if file.suffix.lower() not in (".epro2", ".epro", ".eprj3"):
        return {"status": "not_ready",
                "findings": [f"不是立创 EDA Pro 工程：{file.name}"]}

    if file.suffix.lower() == ".eprj3":
        return _parse_folder(file)

    try:
        archive = _open_archive(file)
    except EasyEdaError as exc:
        return {"status": "failed", "findings": [str(exc)]}

    with archive:
        project = _read_project(archive)
        try:
            records = list(_iter_records(archive))
        except EasyEdaError as exc:
            return {"status": "failed", "findings": [str(exc)]}

    return _build_facts(records, project=project, source=str(file))


def _parse_folder(folder: Path) -> dict[str, Any]:
    """`.eprj3` 是 epro2 的解包形式：把每个 `.epru` 当独立文档读。"""
    records: list[tuple[dict[str, Any], Any]] = []
    project: dict[str, Any] = {}
    project_file = folder / "project2.json"
    if project_file.is_file():
        try:
            project = json.loads(project_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            project = {}
    for doc in sorted(folder.rglob("*.epru")):
        with zipfile.ZipFile.__new__(zipfile.ZipFile) if False else _nullcontext():
            pass
        try:
            text = doc.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for piece in text.split(RECORD_SEPARATOR):
            outer, inner = _split_pair(piece)
            if outer is not None:
                records.append((outer, inner))
    return _build_facts(records, project=project, source=str(folder))


class _nullcontext:
    def __enter__(self) -> None:
        return None

    def __exit__(self, *_: Any) -> None:
        return None


def _build_facts(records: list[tuple[dict[str, Any], Any]], *,
                 project: dict[str, Any], source: str) -> dict[str, Any]:
    """把记录整理成元件（含坐标）与网络（含引脚）。

    三种记录的归属规则**各不相同**，这是最容易接错的地方：

    | 记录 | 归属方式 |
    |---|---|
    | `ATTR` | 有复合 id（`e0-e18`），真正的 parent 在**内层 `parentId`** |
    | `COMPONENT` | 外层 id 就是元件 id（`e0`） |
    | `PAD_NET` | 键式 id `["PAD_NET", 元件id, 焊盘号, 焊盘id]` |

    第一版把 `ATTR` 的外层 id 当 parent（`e0-e18`），于是 645 条 Designator
    一条都匹配不上——元件全部退化成随机 id，标号与值全丢。
    """
    # -- 1. ATTR：parent 取内层 parentId -----------------------------------
    attrs: dict[str, dict[str, Any]] = {}
    for outer, inner in records:
        if outer.get("type") != "ATTR" or not isinstance(inner, dict):
            continue
        # PCB 的 ATTR 给标号/封装；SCH 的 ATTR 给值（Value/Library Link）。
        # 两者 id 空间不同，所以按 (文档, parentId) 分开存，避免串味。
        parent = inner.get("parentId")
        doc = str(outer.get("_doc") or "")
        if not isinstance(parent, str) or parent == "":
            # 没有 parentId 的 ATTR 是游离文本（原理图里的注释），不归属。
            continue
        key = inner.get("key")
        value = inner.get("value")
        slot_key = f"{doc}:{parent}"
        if isinstance(key, str) and key in WANTED_ATTRS and value is not None:
            attrs.setdefault(slot_key, {}).setdefault(WANTED_ATTRS[key], value)
        elif not isinstance(key, str):
            for k, v in inner.items():
                if k in WANTED_ATTRS and v is not None:
                    attrs.setdefault(slot_key, {}).setdefault(WANTED_ATTRS[k], v)

    # -- 1b. 原理图侧：按 **Designator** 归集值与尺寸 -----------------------
    #
    # PCB 元件只有 `Device` 哈希；真实的值与尺寸在原理图元件的 ATTR 上，
    # 两者靠 `Designator`（C1/R23/U29）对应。这是"值 + 尺寸"的唯一桥。
    sch_by_ref: dict[str, dict[str, Any]] = {}
    for parent, info in attrs.items():
        doc, _, _pid = parent.partition(":")
        if not doc.startswith("SCH_PAGE"):
            continue
        ref = str(info.get("designator") or "").strip()
        if ref == "":
            continue
        slot = sch_by_ref.setdefault(ref, {})
        for field in ("value", "comment", "transform_3d", "model_title"):
            if field not in slot and info.get(field) not in (None, ""):
                slot[field] = info.get(field)

    # -- 2. COMPONENT：外层 id 即元件 id；PCB 的 x/y 单位 mil --------------
    #
    # **必须按文档分区**：`COMPONENT` 在 SCH_PAGE（1664 个，原理图符号实例）
    # 和 PCB（441 个，真实布局）里都有。混在一起会让 1479 个"元件"里只有
    # 301 个有标号——因为原理图符号与 PCB 元件的 id 空间不同。
    # 坐标与网表只从 PCB 文档取。
    components: dict[tuple[str, str], dict[str, Any]] = {}
    for outer, inner in records:
        if outer.get("type") != "COMPONENT" or not isinstance(inner, dict):
            continue
        if not str(outer.get("_doc") or "").startswith("PCB"):
            continue
        component_id = outer.get("id")
        if not isinstance(component_id, str):
            continue
        x = inner.get("x")
        y = inner.get("y")
        angle = inner.get("angle", inner.get("rotation"))
        entry: dict[str, Any] = {
            "id": component_id, "x": x, "y": y, "angle": angle,
            "doc": str(outer.get("_doc") or ""),
            "layer": inner.get("layerId"),
            "attrs": {},
        }
        # `attrs` 在盘上是**逐键 JSON 字符串**，解码后是对象。
        raw_attrs = inner.get("attrs")
        if isinstance(raw_attrs, str):
            try:
                decoded = json.loads(raw_attrs)
                entry["attrs"] = decoded if isinstance(decoded, dict) else {}
            except json.JSONDecodeError:
                entry["attrs"] = {}
        elif isinstance(raw_attrs, dict):
            entry["attrs"] = raw_attrs
        # **键必须带文档**：`COMPONENT` 的 id 是文档内序号（`e0`、`e1`…），
        # 11 个 PCB 文档各自从 e0 开始。实测：全局按键合并后组件数从 273
        # 掉到 267 —— 每块板的 e0 互相顶掉了。
        key = (str(outer.get("_doc") or ""), component_id)
        # 同一 id 在同一文档里可能出现多次；保留**有坐标**的那份。
        previous = components.get(key)
        if previous is None or (previous["x"] is None and x is not None):
            components[key] = entry

    # -- 2b. 焊盘几何：FOOTPRINT 文档的 PAD + META.title -------------------
    #
    # 这是"网表从**声明**变成**事实**"的那一步。`PAD_NET` 只给引脚**名字**，
    # 没有位置 —— 于是 2D 画不出焊盘、飞线只能连元件中心、3D 没有焊点。
    # 真实几何在 `FOOTPRINT` 文档里：`COMPONENT` 的 `ATTR Footprint` 指向它。
    #
    # 链路：`COMPONENT(x,y,angle)` + `ATTR key=Footprint` → `FOOTPRINT uuid`
    #       → `PAD{num,centerX,centerY,defaultPad,relativeAngle,layerId}`
    footprint_titles: dict[str, str] = {}
    footprint_pads: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    # DEVICE 文档 → 元件型号 + 供应商封装名 + 3D 模型标题。
    # PCB 元件**没有** `Footprint` 属性时（少数情况），DEVICE 文档是唯一的
    # 封装线索来源 —— 实测 U10 就是这样：PCB 侧 `Footprint` 属性为空，
    # 但 DEVICE 文档写着 `Supplier Footprint: "SOP-8"` 和
    # `3D Model Title: "SOP-8_L5.0-W4.0-H1.8-LS6.2-P1.27"`。
    device_titles: dict[str, str] = {}
    device_footprints: dict[str, str] = {}
    device_model_titles: dict[str, str] = {}
    for outer, inner in records:
        doc = str(outer.get("_doc") or "")
        if not isinstance(inner, dict):
            continue
        if doc.startswith("FOOTPRINT"):
            uuid = doc.partition("#")[2]
            if uuid == "":
                continue
            if outer.get("type") == "META":
                title = inner.get("title")
                if isinstance(title, str) and title.strip() != "":
                    footprint_titles[uuid] = title.strip()
            elif outer.get("type") == "PAD":
                geometry = _pad_geometry(inner)
                if geometry is not None:
                    footprint_pads[uuid].append(geometry)
        elif doc.startswith("DEVICE"):
            uuid = doc.partition("#")[2]
            if uuid == "":
                continue
            if outer.get("type") == "META":
                title = inner.get("title")
                if isinstance(title, str) and title.strip() != "":
                    device_titles[uuid] = title.strip()
                attributes = inner.get("attributes")
                if isinstance(attributes, dict):
                    supplier_fp = attributes.get("Supplier Footprint")
                    if isinstance(supplier_fp, str) and supplier_fp.strip():
                        device_footprints[uuid] = supplier_fp.strip()
                    model_title = attributes.get("3D Model Title")
                    if isinstance(model_title, str) and model_title.strip():
                        device_model_titles[uuid] = model_title.strip()

    # -- 3. PAD_NET：键式 id → 焊盘网络 -----------------------------------
    # **必须按 (文档, 元件 id) 作用域**：拼板工程里每块板都有自己的 `U1`，
    # 只按 ref 或只按 component_id 收集会把不同物理元件的焊盘/网络混在一起。
    # 实测症状：主板的 U1 被报"没有 3 号引脚"，而 3 号引脚其实属于另一块板的 U1。
    pad_nets: dict[tuple[str, str], dict[str, str]] = {}
    all_pads: dict[tuple[str, str], set[str]] = {}
    # 网表**必须按 (文档, 网络名)** 作用域：`GND`/`3.3V` 这类网络名每块板
    # 都有，只按名字收集会让主板拿到别块板的引脚。实测症状：U2 只有 1..16
    # 号焊盘，却被要求有 17 号——那个 17 号来自另一块板的 U2。
    net_pins: dict[tuple[str, str], list[str]] = collections.defaultdict(list)
    nets_declared: set[str] = set()
    for outer, inner in records:
        outer_type = outer.get("type")
        if outer_type == "NET":
            try:
                key = json.loads(str(outer.get("id")))
            except json.JSONDecodeError:
                key = None
            if isinstance(key, list) and len(key) >= 2:
                nets_declared.add(str(key[1]))
            continue
        if outer_type != "PAD_NET" or not isinstance(inner, dict):
            continue
        try:
            key = json.loads(str(outer.get("id")))
        except json.JSONDecodeError:
            continue
        if not isinstance(key, list) or len(key) < 3:
            continue
        component_id, pad_num = str(key[1]), str(key[2])
        net = inner.get("padNet")
        if not isinstance(net, str) or net == "":
            continue
        pad_nets.setdefault((str(outer.get("_doc") or ""), component_id),
                            {})[pad_num] = net
        all_pads.setdefault((str(outer.get("_doc") or ""), component_id),
                            set()).add(pad_num)
        # PAD_NET 与 COMPONENT 在**同一个 PCB 文档**里，用该文档取标号。
        pad_doc = str(outer.get("_doc") or "")
        ref = str(attrs.get(f"{pad_doc}:{component_id}", {}).get("designator")
                  or component_id)
        net_pins[(pad_doc, net)].append(f"{ref}.{pad_num}")

    # -- 4. 合并：按**标号**合并，跨文档同名的元件是同一个物理件 ----------
    #
    # 这个工程是**拼板**：同一个原理图被复制到 11 个 PCB 文档上，所以
    # `C1` 在每块板上都出现 —— 它们是**同一个物理元件**的多次复制，
    # 不是不同元件。实测：11 块板的标号并集 == 原理图的 317 个标号，
    # 每块板的标号都 100% 被原理图覆盖。
    #
    # 所以合并键是**标号**（ref），不是 component_id：component_id 是文档内
    # 序号（每块板都从 `e0` 开始），按它合并会把不同板的不同元件混成一个。
    #
    # **主板块必须先算出来**：合并是按标号跨文档做的，谁先被处理谁的数据
    # 被保留。若按文档顺序处理，保留的可能是某块子板的坐标。
    doc_counts: dict[str, int] = {}
    for (doc_key, _component_id) in components:
        doc_counts[doc_key] = doc_counts.get(doc_key, 0) + 1
    main_doc = (max(doc_counts.items(), key=lambda kv: kv[1])[0]
                if doc_counts else "")

    merged: dict[str, dict[str, Any]] = {}
    # 主板块的元件**先**进 merged，这样"先到先得"的填充规则保留的是主板数据。
    ordered = ([(k, v) for k, v in components.items() if k[0] == main_doc]
               + [(k, v) for k, v in components.items() if k[0] != main_doc])
    for (doc_key, component_id), entry in ordered:
        info = attrs.get(f"{doc_key}:{component_id}", {})
        designator = str(info.get("designator") or "").strip()
        # 没有标号的元件用 "文档:id" 兜底（保证不与别的元件撞名）。
        ref = designator or f"{doc_key}:{component_id}"
        merge_key = ref
        row = merged.setdefault(merge_key, {
            "ref": ref, "designator": designator, "value": "", "footprint": "",
            "device": "", "lcsc": "", "model_3d": "",
            "x_mm": None, "y_mm": None, "angle_deg": 0.0,
            "nets": {}, "raw_ids": [],
            "size_mm": None, "model_title": "", "doc": "",
            "pads": [], "pad_geometry": [], "footprint_title": "",
            "pad_local": [],
            "side": "",
        })
        if row["value"] == "":
            value = info.get("value", "")
            # `={Value}` 是未解析表达式，不是真实值。
            if value and not str(value).startswith("="):
                row["value"] = value
        if row["footprint"] == "":
            row["footprint"] = info.get("footprint_name") or info.get("footprint", "")
        if row["device"] == "":
            row["device"] = info.get("device", "")
        if row["lcsc"] == "":
            row["lcsc"] = info.get("lcsc", "")
        if row["model_3d"] == "":
            row["model_3d"] = info.get("model_3d", "")
        device_uuid = str(info.get("device") or "")
        x, y = entry["x"], entry["y"]
        # 安装面：`COMPONENT.layerId` 1 = 顶层，2 = 底层。
        # 这一项决定重叠判定是否成立 —— 双面贴装的板上，顶层件与底层件
        # XY 重叠是正常的（实测主 PCB：71 顶 / 70 底）。
        #
        # **只认第一个**（主板先处理）：同一个物理元件被复制到 11 块板上，
        # 各板的安装面可能不同（实测 R259 在主板是顶层、在子板是底层）。
        # 取主板的值，否则顶层/底层的判定会随文档顺序漂移。
        layer_id = entry.get("layer")
        if row["side"] == "" and isinstance(layer_id, int):
            row["side"] = "top" if layer_id == 1 else "bottom" if layer_id == 2 else ""
        if isinstance(x, (int, float)) and row["x_mm"] is None:
            row["x_mm"] = round(float(x) * MIL_TO_MM, 3)
            if isinstance(y, (int, float)):
                row["y_mm"] = round(float(y) * MIL_TO_MM, 3)
            angle = entry["angle"]
            if isinstance(angle, (int, float)):
                row["angle_deg"] = round(float(angle), 2)
        # 焊盘几何：封装本地 → 元件绝对（mil）。导入器再统一做 mil→mm + y 翻转，
        # 与元件坐标走同一条路径，两者不会漂移。
        if not row["pad_geometry"]:
            fp_uuid = str(info.get("footprint") or "")
            local_pads = footprint_pads.get(fp_uuid) if fp_uuid else None
            if local_pads and isinstance(x, (int, float)) and isinstance(y, (int, float)):
                raw_angle = entry["angle"]
                row["pad_geometry"] = _absolute_pads(
                    local_pads, float(x), float(y),
                    float(raw_angle) if isinstance(raw_angle, (int, float)) else 0.0)
                row["footprint_title"] = footprint_titles.get(fp_uuid, "")
                # **封装本地**焊盘（未旋转）：本体尺寸必须在**封装自己的坐标系**
                # 里算，否则元件转了 90° 就会把长宽算反（实测 C0805 转 90°
                # 后拿到 1.41×2.34，正确是 2.0×1.25）。
                row["pad_local"] = [
                    {"num": pad["num"], "cx": pad["cx"], "cy": pad["cy"],
                     "w": pad["w"], "h": pad["h"]}
                    for pad in local_pads
                ]
        # **DEVICE 兜底**：PCB 侧没有 `Footprint` 属性时，用器件型号反查封装名。
        # 实测 U10：PCB 的 `Footprint` 属性为空，但 DEVICE 文档写着
        # `Supplier Footprint: "SOP-8"` —— 这是它唯一的封装线索。
        if row["footprint_title"] == "" and device_uuid:
            supplier_fp = device_footprints.get(device_uuid, "")
            if supplier_fp:
                row["footprint_title"] = supplier_fp
            model_title = device_model_titles.get(device_uuid, "")
            if model_title and row["model_title"] == "":
                row["model_title"] = model_title
                # DEVICE 的 `3D Model Transform` 是真实包围盒（mil）。
                if row["size_mm"] is None:
                    row["size_mm"] = _size_from_attrs({
                        "transform_3d": _device_transform(device_uuid, records),
                        "model_title": model_title,
                    })
        if not row["nets"]:
            row["nets"].update(pad_nets.get((doc_key, component_id), {}))
        if not row["pads"]:
            pads = all_pads.get((doc_key, component_id))
            if pads:
                # 自然序：`1,2,10` 而不是 `1,10,2`
                row["pads"] = sorted(pads, key=lambda x: (len(x), x))
        # 原理图侧的值与尺寸：PCB 只有 Device 哈希，真实值靠 Designator 桥接。
        sch_info = sch_by_ref.get(ref, {})
        if sch_info:
            if row["value"] == "":
                raw_value = str(sch_info.get("value") or "")
                if raw_value != "" and not raw_value.startswith("="):
                    row["value"] = raw_value
            if row["model_title"] == "":
                row["model_title"] = str(sch_info.get("model_title") or "")
            if row["size_mm"] is None:
                row["size_mm"] = _size_from_attrs(sch_info)
        row["raw_ids"].append(component_id)
        if row.get("doc") in (None, ""):
            row["doc"] = entry.get("doc", "")

    # -- 4b. 走线：LINE（铜）与 FILL（覆铜）-------------------------------
    #
    # 这是"2D 画布上一根线都没有"的**真正**原因：走线一直在文件里，
    # 只是从来没解析过。实测主板 PCB 文档有 **1119 条 LINE**（628 顶层 /
    # 491 底层，105 个网络）和 12 条 FILL（覆铜）。
    #
    # `LINE` 是**直线段**（startX/startY → endX/endY），单位 mil；
    # `width` 也是 mil。层用 `layerId`：1 = 顶层，2 = 底层。
    traces: list[dict[str, Any]] = []
    copper_fills: list[dict[str, Any]] = []
    copper_by_doc: dict[str, int] = collections.Counter()
    for outer, inner in records:
        doc = str(outer.get("_doc") or "")
        if not doc.startswith("PCB") or not isinstance(inner, dict):
            continue
        record_type = outer.get("type")
        if record_type == "LINE":
            sx, sy = inner.get("startX"), inner.get("startY")
            ex, ey = inner.get("endX"), inner.get("endY")
            if not all(isinstance(v, (int, float)) for v in (sx, sy, ex, ey)):
                continue
            width = inner.get("width")
            traces.append({
                "doc": doc,
                "net": str(inner.get("netName") or ""),
                "layer": int(inner.get("layerId") or 1),
                "width_mm": round(float(width) * MIL_TO_MM, 4)
                if isinstance(width, (int, float)) else 0.0,
                "x1": round(float(sx) * MIL_TO_MM, 4),
                "y1": round(float(sy) * MIL_TO_MM, 4),
                "x2": round(float(ex) * MIL_TO_MM, 4),
                "y2": round(float(ey) * MIL_TO_MM, 4),
            })
            copper_by_doc[doc] += 1
        elif record_type == "FILL":
            path = inner.get("path")
            if not isinstance(path, list) or not path:
                continue
            boxes: list[list[float]] = []
            for item in path:
                # `["R", x, y, w, h, rot, ...]` —— 矩形覆铜块
                if (isinstance(item, list) and len(item) >= 5
                        and item[0] == "R"
                        and all(isinstance(v, (int, float)) for v in item[1:5])):
                    boxes.append([round(float(item[1]) * MIL_TO_MM, 4),
                                  round(float(item[2]) * MIL_TO_MM, 4),
                                  round(float(item[3]) * MIL_TO_MM, 4),
                                  round(float(item[4]) * MIL_TO_MM, 4)])
            if not boxes:
                continue
            copper_fills.append({
                "doc": doc,
                "net": str(inner.get("netName") or ""),
                "layer": int(inner.get("layerId") or 1),
                "boxes": boxes,
            })

    # -- 5. 板框：POLY 且 polyType=BOARD_OUTLINE（layerId 11）--------------
    #
    # **一个工程可能有多个 PCB 文档**（拼板/多板项目）。这里收集全部板框，
    # 取**最大的一块**作为主轮廓，并把总数如实报告——只取第一个会得到
    # 23×23 mm 的小板，而工程其实是 70×45 的主板加若干子板。
    outlines: list[dict[str, Any]] = []
    for outer, inner in records:
        if not str(outer.get("_doc") or "").startswith("PCB") or outer.get("type") != "POLY":
            continue
        if not isinstance(inner, dict):
            continue
        if str(inner.get("polyType") or "") != "BOARD_OUTLINE":
            continue
        points = _parse_path(inner.get("path"))
        if len(points) < 3:
            continue
        mm_points = [[round(x * MIL_TO_MM, 3), round(y * MIL_TO_MM, 3)]
                     for x, y in points]
        xs = [p[0] for p in mm_points]
        ys = [p[1] for p in mm_points]
        outlines.append({
            "doc": str(outer.get("_doc") or ""),
            "points": mm_points,
            "w": round(max(xs) - min(xs), 3),
            "h": round(max(ys) - min(ys), 3),
            "area": round((max(xs) - min(xs)) * (max(ys) - min(ys)), 3),
        })
    outlines.sort(key=lambda o: -o["area"])
    outline_mm = outlines[0]["points"] if outlines else []

    components_out = sorted(merged.values(),
                            key=lambda c: _natural_key(str(c["ref"])))
    located = sum(1 for c in components_out if c["x_mm"] is not None)
    named = sum(1 for c in components_out if c["designator"] != "")
    placeholders = sum(1 for c in components_out
                       if "?" in str(c["designator"]) and c["designator"] != "")
    with_nets = sum(1 for c in components_out if c["nets"])
    sized = sum(1 for c in components_out if c.get("size_mm"))
    valued = sum(1 for c in components_out if c.get("value"))
    with_height = sum(1 for c in components_out
                      if (c.get("size_mm") or {}).get("d", 0) > 0)
    with_pads = sum(1 for c in components_out if c.get("pads"))
    with_pad_geometry = sum(1 for c in components_out if c.get("pad_geometry"))
    pads_placed = sum(len(c.get("pad_geometry") or []) for c in components_out)
    # 同一 (ref, pad) 可能被多份记录声明（多文档重复），去重后再排序。
    # 主板 = 元件最多的那个 PCB 文档（拼板工程的"主工程"）。
    # 由 parse() 决定而不是调用方自己算：两处各算一次必然漂移，
    # 漂移的后果是"元件取 A 板、网表取 B 板"。
    # `main_doc` 已在合并前算好（按 PCB 文档的元件数），这里不再重算 ——
    # 两处各算一次会漂移，漂移的后果是"元件取 A 板、网表取 B 板"。

    # 只保留主板的网表；其它板的网络如实计数但不下发（否则引脚会串板）。
    main_nets = [(name, pins) for (doc, name), pins in net_pins.items()
                 if doc == main_doc]
    nets_out = {
        name: sorted(set(pins))
        for name, pins in sorted(main_nets,
                                 key=lambda kv: (-len(set(kv[1])), kv[0]))
    }
    nets_other = sum(1 for (doc, _name) in net_pins if doc != main_doc)

    return {
        "status": "ok",
        "source": source,
        "title": project.get("title", ""),
        "editor": project.get("editorVersion", ""),
        "units": {"pcb": "mil", "schematic": "10mil"},
        "components": components_out,
        "nets": nets_out,
"nets_declared":sorted(nets_declared),
"main_doc":main_doc,
"nets_other_boards":nets_other,
        "outline_mm": outline_mm,
        "outline_count": len(outlines),
        # 走线（铜）与覆铜：2D/3D 的真实铜层几何。只下发**主板块**的，
        # 其余板如实计数（和网表同一规则，避免跨板串味）。
        "traces": [t for t in traces if t["doc"] == main_doc],
        "copper_fills": [f for f in copper_fills if f["doc"] == main_doc],
        "traces_by_doc": {doc: count for doc, count in copper_by_doc.items()},
        "boards": [{"doc": o["doc"], "w": o["w"], "h": o["h"],
                    "area": o["area"]} for o in outlines[:12]],
        # 带点集的完整板框（导入器要按文档取用）。
        "outlines_full": [{"doc": o["doc"], "points": o["points"],
                           "w": o["w"], "h": o["h"]} for o in outlines],
        "outlines": [{"w": o["w"], "h": o["h"]} for o in outlines[:12]],
        "board_size_mm": (
            {"w": round(max(p[0] for p in outline_mm)
                        - min(p[0] for p in outline_mm), 3),
             "h": round(max(p[1] for p in outline_mm)
                        - min(p[1] for p in outline_mm), 3)}
            if outline_mm else None),
        "counts": {
            "components": len(components_out),
            "named": named,
            "with_coords": located,
            "with_nets": with_nets,
            "nets": len(net_pins),
            "nets_declared": len(nets_declared),
            "pads": sum(len(v) for v in pad_nets.values()),
            "placeholders": placeholders,
            "with_3d": sum(1 for c in components_out if c["model_3d"]),
            "sized": sized,
            "valued": valued,
            "with_height": with_height,
            "with_pads": with_pads,
            "with_pad_geometry": with_pad_geometry,
            "pads_placed": pads_placed,
        },
        "note": _note(located, len(components_out), with_nets,
                      net_pin_count=len(net_pins), named=named,
                      placeholders=placeholders, sized=sized,
                      valued=valued, with_height=with_height),
    }


def _device_transform(device_uuid: str,
                      records: list[tuple[dict[str, Any], Any]]) -> str:
    """DEVICE 文档里的 `3D Model Transform`（9 个逗号分隔参数，mil）。

    实测：`BL24C256A-PARC` 的 transform 是 `244.094,196.85,0,90,0,0,0,0,0`
    → 包围盒 6.2×5.0×0 mm，即 SOP-8 的 5.0×4.0mm 本体 + 焊盘外伸。
    """
    for outer, inner in records:
        if outer.get("type") != "META":
            continue
        if not str(outer.get("_doc") or "").startswith("DEVICE"):
            continue
        if not isinstance(inner, dict):
            continue
        if str(outer.get("_doc") or "").partition("#")[2] != device_uuid:
            continue
        attributes = inner.get("attributes")
        if isinstance(attributes, dict):
            value = attributes.get("3D Model Transform")
            if isinstance(value, str) and value.strip():
                return value
    return ""


def _pad_geometry(inner: dict[str, Any]) -> dict[str, Any] | None:
    """一条 `PAD` 记录 → **封装本地**几何（mil，未旋转）。

    字段依据官方规范（`TPcbPad`）：

    | 字段 | 含义 |
    |---|---|
    | `num` | 焊盘号 = 引脚名，网表 `PAD_NET` 用的就是它 |
    | `centerX` / `centerY` | 封装本地坐标（mil），相对封装原点 |
    | `defaultPad` | `{padType, width, height, radius}`，`padType ∈ RECT/OVAL/ELLIPSE/CIRCLE` |
    | `relativeAngle` | 焊盘自身相对封装的旋转 |
    | `layerId` | 1 = 顶层，2 = 底层，12 = 多层（通孔） |
    | `hole` | 钻孔（通孔器件才有） |

    拿不到尺寸就返回 None —— 宁可不画焊盘，也不画一个编出来的形状。
    """
    if not isinstance(inner, dict):
        return None
    pad = inner.get("defaultPad")
    if not isinstance(pad, dict):
        return None
    width, height = pad.get("width"), pad.get("height")
    cx, cy = inner.get("centerX"), inner.get("centerY")
    num = inner.get("num")
    if num is None:
        return None
    for value in (width, height, cx, cy):
        if not isinstance(value, (int, float)):
            return None
    hole = inner.get("hole")
    hole_d = hole.get("diameter") if isinstance(hole, dict) else None
    return {
        "num": str(num),
        "cx": float(cx),
        "cy": float(cy),
        "w": float(width),
        "h": float(height),
        "shape": str(pad.get("padType") or "RECT").strip().upper(),
        "radius": float(pad.get("radius") or 0.0),
        "angle": float(inner.get("relativeAngle") or 0.0),
        "layer": int(inner.get("layerId") or 1),
        "hole": float(hole_d) if isinstance(hole_d, (int, float)) else 0.0,
    }


def _absolute_pads(pads: list[dict[str, Any]], ox: float, oy: float,
                   angle_deg: float) -> list[dict[str, Any]]:
    """封装本地焊盘 → **元件绝对**坐标（mil，仍是 EDA 坐标系）。

    变换是 `origin + R(angle)·local`。旋转方向由**走线端点**实测确定：
    把 `LINE` 的端点与焊盘对齐、比较"最近焊盘的网络名是否等于走线网络名"，
    逆时针（`+angle`）命中 709/1294，顺时针只有 496/1294 —— 差 43%。

    坐标仍留在 EDA 系（y 向上，未翻转），翻转交给导入器，和元件坐标同一条路径。
    """
    radians = math.radians(angle_deg)
    cos_a, sin_a = math.cos(radians), math.sin(radians)
    out: list[dict[str, Any]] = []
    for pad in pads:
        cx, cy = pad["cx"], pad["cy"]
        out.append({
            "num": pad["num"],
            "x": round(ox + cx * cos_a - cy * sin_a, 4),
            "y": round(oy + cx * sin_a + cy * cos_a, 4),
            "w": pad["w"],
            "h": pad["h"],
            "shape": pad["shape"],
            "radius": pad["radius"],
            "angle": round((pad["angle"] + angle_deg) % 360.0, 3),
            "layer": pad["layer"],
            "hole": pad["hole"],
        })
    return out


def _size_from_attrs(info: dict[str, Any]) -> dict[str, float] | None:
    """从原理图属性推出元件尺寸（mm）。

    两个来源，**优先用前者**：

    1. `3D Model Transform` —— 9 个逗号分隔参数，前三个是 sizeX/Y/Z（**mil**）。
       这是库作者按器件做的 3D 模型包围盒，最接近真实本体尺寸。
    2. `3D Model Title` —— 封装名里的 `L1.6-W0.8-H0.8`（mm）。用作高度来源
       与兜底：Transform 的 sizeZ 实测恒为 0，高度只能从标题拿。

    拿不到就返回 None —— **不猜、不填默认值**。调用方据此标 `estimated`。
    """
    transform = info.get("transform_3d")
    title = str(info.get("model_title") or "")
    width = height = depth = 0.0

    if isinstance(transform, str) and transform.strip() != "":
        parts = transform.split(",")
        try:
            numbers = [float(p) for p in parts[:3]]
        except ValueError:
            numbers = []
        if len(numbers) == 3:
            width = numbers[0] * MIL_TO_MM
            height = numbers[1] * MIL_TO_MM
            depth = numbers[2] * MIL_TO_MM

    # 标题里的 L/W/H（单位已是 mm）：补高度，或在 Transform 缺失时兜底。
    def _pick(letter: str) -> float:
        match = re.search(rf"{letter}(\d+(?:\.\d+)?)", title)
        return float(match.group(1)) if match else 0.0

    title_l, title_w, title_h = _pick("L"), _pick("W"), _pick("H")
    if depth <= 0 and title_h > 0:
        depth = title_h
    if width <= 0 and title_l > 0:
        width = title_l
    if height <= 0:
        if title_w > 0:
            height = title_w
        elif title_l > 0 and width > 0:
            height = title_l

    if width <= 0 or height <= 0:
        return None
    return {
        "w": round(width, 3),
        "h": round(height, 3),
        "d": round(depth, 3) if depth > 0 else 0.0,
    }


def _parse_path(path: Any) -> list[tuple[float, float]]:
    """解析 EasyEDA 的紧凑路径数组。

    形态：`["R", x, y, w, h, angle, ...]` 或 `["P", x1, y1, x2, y2, ...]`。
    `R` = 矩形（取四角），`P` = 多边形（点列），`L` = 线。

    规范里 `path` 是「图元路径」的紧凑编码；这里只处理板框用到的
    `R`/`P` 两种——遇到不认识的段就**停**并返回已解析的点，不猜。
    """
    if not isinstance(path, list) or len(path) == 0:
        return []
    kind = str(path[0])
    rest = path[1:]
    if kind == "P":
        points: list[tuple[float, float]] = []
        for index in range(0, len(rest) - 1, 2):
            x, y = rest[index], rest[index + 1]
            if isinstance(x, (int, float)) and isinstance(y, (int, float)):
                points.append((float(x), float(y)))
        return points
    if kind == "R" and len(rest) >= 4:
        x, y, w, h = (float(rest[i]) for i in range(4))
        return [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
    return []


def _note(located: int, total: int, with_nets: int, *, net_pin_count: int,
          named: int = 0, placeholders: int = 0, sized: int = 0,
          valued: int = 0, with_height: int = 0) -> str:
    parts: list[str] = []
    if total == 0:
        return "没有解析到元件"
    parts.append(f"{named}/{total} 个元件有标号")
    if placeholders:
        parts.append(f"{placeholders} 个标号仍是占位符（工程状态，不是解析失败）")
    if located == 0:
        parts.append("没有任何元件带坐标（可能只有原理图，无 PCB）")
    elif located < total:
        parts.append(f"{total - located} 个元件没有坐标")
    else:
        parts.append(f"全部 {located} 个元件都有真实坐标（mil → mm）")
    if with_nets == 0:
        parts.append("没有解析到焊盘网络归属")
    else:
        parts.append(f"{with_nets} 个元件有焊盘网络归属，共 {net_pin_count} 个网络")
    # 值与尺寸来自原理图侧，覆盖率天然低于坐标——如实报，不掩饰。
    parts.append(f"{valued}/{total} 个元件有真实值（来自原理图 Value）")
    if sized:
        parts.append(f"{sized}/{total} 个元件有真实尺寸（3D Model Transform）"
                     + (f"，其中 {with_height} 个有高度" if with_height else
                        "，但高度多为 0（Transform 的 sizeZ 恒为 0）"))
    else:
        parts.append("没有解析到元件尺寸（3D Model Transform 缺失）")
    return "；".join(parts)


def _natural_key(ref: str) -> tuple[str, int]:
    match = re.match(r"^([A-Za-z]+)(\d*)", ref)
    if match is None:
        return (ref, 0)
    return (match.group(1).upper(), int(match.group(2) or 0))


def summary(path: str | Path) -> dict[str, Any]:
    """轻量摘要（勘察用）：只要计数与网络名。"""
    full = parse(path)
    if full.get("status") != "ok":
        return full
    return {
        "status": "ok",
        "title": full["title"],
        "editor": full["editor"],
        "counts": full["counts"],
        "nets": dict(list(full["nets"].items())[:12]),
        "note": full["note"],
    }