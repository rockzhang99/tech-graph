"""外部 elements 图描述 → fireworks-tech-graph v1 spec。

第三方绘图工具导出的图描述长这样::

    {
      "title": "...",
      "diagramX": 25,
      "elements": [
        {"tag": "Textbox", "text": "...", "x": 15, "y": 15, ...},
        {"tag": "Group", "title": {"text": "..."}, "x": 15, "y": 71, ...},
        {"tag": "Shape", "shape": "rectangle", "texts": [...], "containerId": "g1"},
        {"tag": "Icon", "icon": "cpu", "texts": [...]},
        {"tag": "Relationship", "from": "g2", "to": "g1", "points": [[0,0],[0,-45]]}
      ]
    }

fireworks-tech-graph 只识别 ``containers`` / ``nodes`` / ``arrows``，其余顶层字段
全部忽略 —— 整个 ``elements`` 数组被丢掉，表现就是「只渲染出标题」。

本模块把它翻译成可渲染的 v1 spec：

=============  ==========================================================
外部 tag        目标
=============  ==========================================================
``Group``      ``containers[]``（label 取 ``title.text``）
``Shape``      ``nodes[]``（首行 text 为 label，其余合并为 sublabel）
``Icon``       小图标放大成可容纳文字的 ``nodes[]`` 矩形
``Textbox``    只取作画布标题，不单独渲染（FTG 自带标题块）
``Relationship``  ``arrows[]``，用绝对坐标 + ``route_points`` 表达
=============  ==========================================================

两点必须说明的设计取舍：

1. **箭头不走 source/target**。FTG 的 ``DiagramIR`` 要求箭头的两端必须是 node id，
   而外部格式里的连线挂在 Group（容器）上，直接映射会被判
   ``references unknown node``。改用 ``x1/y1/x2/y2`` + ``route_points``
   的纯坐标连线，渲染器支持这种形式，视觉上也最贴近原图。
2. **坐标整体缩放平移**。原图坐标系是任意的（本例 1170x1199），等比缩放到目标
   宽度后整体平移，顶部留出 ``PAD_TOP`` 给 FTG 自动渲染的标题块，避免压住内容。

``icon`` 名称（trending-up / cpu 等）无法保留：FTG 的节点只有少数内置图形，
没有通用图标表。
"""

from __future__ import annotations

import math
from typing import Any

# ---------------------------------------------------------------- 布局常量

TARGET_WIDTH = 1080.0  # 内容区最大宽度，超过则等比缩小
PAD_X = 48.0  # 左右留白
PAD_TOP = 118.0  # 顶部留给标题块（渲染器在 y≈56 处画标题，不自动下移内容）
PAD_BOTTOM = 56.0  # 底部留白（页脚/图例）
ICON_WIDTH = 152.0  # Icon 元素放大后的节点宽度，保证文字放得下
ICON_HEIGHT = 48.0

KIND_MAP = {
    "rectangle": "rect",
    "rect": "rect",
    "round_rect": "rect",
    "cylinder": "cylinder",
    "database": "cylinder",
    "storage": "cylinder",
    "hexagon": "hexagon",
    "diamond": "hexagon",
}

CONTAINER_TAGS = {"group", "container", "frame", "lane", "swimlane"}
NODE_TAGS = {"shape", "node", "box", "card"}
ICON_TAGS = {"icon"}
TEXT_TAGS = {"textbox", "text", "label"}
ARROW_TAGS = {"relationship", "arrow", "edge", "connector", "line"}

MIN_SIZE = 8.0


class ConversionError(ValueError):
    """输入不是可识别的 elements 格式，或缺少可转换的内容。"""


# ---------------------------------------------------------------- 小工具


def _num(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _text(value: Any) -> str:
    """取出文本：支持 ``{"text": "..."}`` 与裸字符串两种写法。"""
    if isinstance(value, dict):
        value = value.get("text", "")
    return str(value or "").strip()


def _dedupe(used: set[str], candidate: str, index: int) -> str:
    """保证 id 不与已有 id 冲突（外部格式里 Group 与 Shape 可能重名）。"""
    base = candidate or f"item-{index:03d}"
    if base not in used:
        used.add(base)
        return base
    suffix = 2
    while f"{base}-{suffix}" in used:
        suffix += 1
    used.add(f"{base}-{suffix}")
    return f"{base}-{suffix}"


def _absolute_points(element: dict[str, Any]) -> list[tuple[float, float]]:
    """把 Relationship 的相对 points 还原成绝对坐标。"""
    ox = _num(element.get("x"))
    oy = _num(element.get("y"))
    points: list[tuple[float, float]] = []
    for raw in element.get("points") or []:
        if isinstance(raw, (list, tuple)) and len(raw) >= 2:
            points.append((ox + _num(raw[0]), oy + _num(raw[1])))
    return points or [(ox, oy)]


# ---------------------------------------------------------------- 入口


def needs_conversion(spec: Any) -> bool:
    """判断 spec 是否为需要转换的外部 elements 格式。"""
    if not isinstance(spec, dict):
        return False
    if not isinstance(spec.get("elements"), list) or not spec["elements"]:
        return False
    # 已经是 FTG 原生格式（可能同时带 elements 副本）时不做转换
    return not spec.get("nodes") and not spec.get("containers")


def convert_spec(spec: dict[str, Any], mode: str = "architecture") -> dict[str, Any]:
    """把外部 elements 图描述转换为 fireworks-tech-graph v1 spec。"""
    elements = [item for item in spec.get("elements") or [] if isinstance(item, dict)]
    if not elements:
        raise ConversionError("elements 为空，没有可转换的内容")

    tagged = [(str(item.get("tag", "")).strip().lower(), item) for item in elements]

    # 1) 计算内容包围盒（Textbox 排除：它的职责交给 FTG 标题块）
    min_x = min_y = math.inf
    max_x = max_y = -math.inf

    def _swallow_box(x: float, y: float, w: float, h: float) -> None:
        nonlocal min_x, min_y, max_x, max_y
        min_x = min(min_x, x)
        min_y = min(min_y, y)
        max_x = max(max_x, x + w)
        max_y = max(max_y, y + h)

    for tag, item in tagged:
        if tag in TEXT_TAGS:
            continue
        if tag in ARROW_TAGS:
            for px, py in _absolute_points(item):
                min_x = min(min_x, px)
                min_y = min(min_y, py)
                max_x = max(max_x, px)
                max_y = max(max_y, py)
            continue
        _swallow_box(
            _num(item.get("x")),
            _num(item.get("y")),
            _num(item.get("width")),
            _num(item.get("height")),
        )

    if not math.isfinite(min_x) or not math.isfinite(min_y):
        raise ConversionError("无法从 elements 中解析出有效坐标")

    content_w = max(1.0, max_x - min_x)
    content_h = max(1.0, max_y - min_y)
    scale = min(1.0, TARGET_WIDTH / content_w)

    def tx(x: float) -> float:
        return round((x - min_x) * scale + PAD_X, 2)

    def ty(y: float) -> float:
        return round((y - min_y) * scale + PAD_TOP, 2)

    def tw(w: float) -> float:
        return round(max(MIN_SIZE, w * scale), 2)

    def th(h: float) -> float:
        return round(max(MIN_SIZE, h * scale), 2)

    canvas_w = round(content_w * scale + PAD_X * 2)
    canvas_h = round(content_h * scale + PAD_TOP + PAD_BOTTOM)

    containers: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    arrows: list[dict[str, Any]] = []
    used_ids: set[str] = set()

    # 2) 分组 → containers
    for index, (tag, item) in enumerate(tagged):
        if tag not in CONTAINER_TAGS:
            continue
        label = _text(item.get("title")) or _text(item.get("label")) or _text(item.get("text"))
        containers.append(
            {
                "id": _dedupe(used_ids, str(item.get("id") or f"group-{index:03d}"), index),
                "x": tx(_num(item.get("x"))),
                "y": ty(_num(item.get("y"))),
                "width": tw(_num(item.get("width"))),
                "height": th(_num(item.get("height"))),
                "label": label,
                "fill": item.get("bgColor") or "none",
                "stroke": item.get("borderColor"),
            }
        )

    # 3) 图形 / 图标 → nodes
    for index, (tag, item) in enumerate(tagged):
        if tag in ICON_TAGS:
            center = tx(_num(item.get("x")) + _num(item.get("width")) / 2)
            node = {
                "id": _dedupe(used_ids, str(item.get("id") or f"icon-{index:03d}"), index),
                "kind": "rect",
                "x": round(center - ICON_WIDTH / 2, 2),
                "y": ty(_num(item.get("y"))),
                "width": ICON_WIDTH,
                "height": ICON_HEIGHT,
                "label": _first_label(item, index),
            }
        elif tag in NODE_TAGS:
            node = {
                "id": _dedupe(used_ids, str(item.get("id") or f"node-{index:03d}"), index),
                "kind": KIND_MAP.get(str(item.get("shape", "rect")).strip().lower(), "rect"),
                "x": tx(_num(item.get("x"))),
                "y": ty(_num(item.get("y"))),
                "width": tw(_num(item.get("width"))),
                "height": th(_num(item.get("height"))),
                "label": _first_label(item, index),
            }
            sublabel = _sub_label(item)
            if sublabel:
                node["sublabel"] = sublabel
        else:
            continue

        if item.get("bgColor"):
            node["fill"] = item["bgColor"]
        if item.get("borderColor"):
            node["stroke"] = item["borderColor"]
        nodes.append(node)

    # 4) 连线 → 纯坐标 arrows
    for index, (tag, item) in enumerate(tagged):
        if tag not in ARROW_TAGS:
            continue
        points = [(tx(px), ty(py)) for px, py in _absolute_points(item)]
        if len(points) < 2:
            continue
        arrow: dict[str, Any] = {
            "id": _dedupe(used_ids, str(item.get("id") or f"edge-{index:03d}"), index),
            "x1": points[0][0],
            "y1": points[0][1],
            "x2": points[-1][0],
            "y2": points[-1][1],
            "flow": _flow_of(item),
        }
        if len(points) > 2:
            arrow["route_points"] = [[px, py] for px, py in points[1:-1]]
        label = _text(item.get("label"))
        if label:
            arrow["label"] = label
        if item.get("color"):
            arrow["color"] = item["color"]
        arrows.append(arrow)

    if not nodes and not containers:
        raise ConversionError("elements 里没有可识别的 Group / Shape / Icon")

    # 5) 组装 v1 spec
    title = _text(spec.get("title")) or _first_textbox(tagged) or "技术图"
    result: dict[str, Any] = {
        "schema_version": 1,
        "mode": mode,
        "template_type": mode,
        "width": canvas_w,
        "height": canvas_h,
        "title": title,
        "containers": containers,
        "nodes": nodes,
        "arrows": arrows,
    }
    subtitle = _first_textbox(tagged)
    if subtitle and subtitle != title:
        result["subtitle"] = subtitle
    if isinstance(spec.get("style"), int):
        result["style"] = spec["style"]
    return result


def _first_label(item: dict[str, Any], index: int) -> str:
    texts = item.get("texts")
    if isinstance(texts, list) and texts:
        return _text(texts[0]) or f"node-{index:03d}"
    return _text(item.get("text")) or _text(item.get("label")) or f"node-{index:03d}"


def _sub_label(item: dict[str, Any]) -> str:
    texts = item.get("texts")
    if not isinstance(texts, list) or len(texts) < 2:
        return ""
    parts = [_text(entry) for entry in texts[1:]]
    return " / ".join(part for part in parts if part)


def _first_textbox(tagged: list[tuple[str, dict[str, Any]]]) -> str:
    """取第一个 Textbox 的文本，去掉 Markdown 标题井号。"""
    for tag, item in tagged:
        if tag not in TEXT_TAGS:
            continue
        text = _text(item.get("text"))
        if text:
            return text.lstrip("#").strip()
    return ""


def _flow_of(item: dict[str, Any]) -> str:
    """外部格式没有 flow 语义，按连线方向粗分：垂直跨越 → feedback。"""
    points = _absolute_points(item)
    if len(points) >= 2:
        dx = abs(points[-1][0] - points[0][0])
        dy = abs(points[-1][1] - points[0][1])
        if dy > dx * 2:
            return "feedback"
    return "control"


__all__ = ["ConversionError", "convert_spec", "needs_conversion"]
