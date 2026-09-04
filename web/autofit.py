"""构图门禁修复：把越出容器的节点收回去，并把报错翻译成可执行的坐标提示。

背景
----
Skill 的构图质量门禁会对「节点 ↔ 容器」的留白做检查::

    COMPOSITION_QUALITY: CONTAINER_GUTTER:a1@g_access=-16.35>0.0

含义是节点 ``a1`` 的**中心落在容器 ``g_access`` 内**，但节点矩形越出了容器边界，
最紧的一侧只剩 ``-16.35``（standard profile 要求 ≥ 0；showcase profile 要求 ≥ 20）。

原始报错只给一个负数，看不出是哪一边越界、越了多少、该改哪个字段。
本模块做两件事：

1. :func:`explain` —— 把报错翻译成「哪条边、超出多少、建议改成什么值」。
2. :func:`fit_into_containers` —— 自动把越界节点收回容器（优先平移、必要时才缩小），
   让图先渲染出来，再把改动明细回传给用户。
"""

from __future__ import annotations

import re
from typing import Any

# 修复目标留白。门禁只要求 ≥0，这里主动留 8px：
# 贴着容器边缘画很容易触发 min_label_clearance 等相邻规则，一次修到位。
TARGET_GUTTER = 8.0
MIN_WIDTH = 90.0  # 缩小下限，再窄文字必然溢出
MIN_HEIGHT = 40.0

# 生成器 node_bounds() 的默认值，缺字段时保持一致
DEFAULT_WIDTH = 180.0
DEFAULT_HEIGHT = 76.0

GUTTER_RE = re.compile(r"CONTAINER_GUTTER:([^@;=]+)@([^=;]+)=(-?[\d.]+)>([\d.]+)")

_SIDES = (
    ("左", "x"),
    ("上", "y"),
    ("右", "x + width"),
    ("下", "y + height"),
)


def _num(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number


def _rect(item: dict[str, Any]) -> tuple[float, float, float, float]:
    """还原成 (left, top, right, bottom)，默认值与生成器 node_bounds 一致。"""
    x = _num(item.get("x"))
    y = _num(item.get("y"))
    return (
        x,
        y,
        x + _num(item.get("width"), DEFAULT_WIDTH),
        y + _num(item.get("height"), DEFAULT_HEIGHT),
    )


def _area(bounds: tuple[float, float, float, float]) -> float:
    return max(0.0, bounds[2] - bounds[0]) * max(0.0, bounds[3] - bounds[1])


def containing_container(
    node: tuple[float, float, float, float],
    containers: list[tuple[str, tuple[float, float, float, float]]],
) -> tuple[str, tuple[float, float, float, float]] | None:
    """与 composition_quality.containing_container 同规则：中心落入且面积最小。"""
    cx = (node[0] + node[2]) / 2
    cy = (node[1] + node[3]) / 2
    matches = [
        item
        for item in containers
        if item[1][0] <= cx <= item[1][2] and item[1][1] <= cy <= item[1][3]
    ]
    return min(matches, key=lambda item: _area(item[1])) if matches else None


def _gutters(
    node: tuple[float, float, float, float],
    container: tuple[float, float, float, float],
) -> list[float]:
    """四边留白，顺序与 _SIDES 一致：左 / 上 / 右 / 下。"""
    return [
        node[0] - container[0],
        node[1] - container[1],
        container[2] - node[2],
        container[3] - node[3],
    ]


# ---------------------------------------------------------------- 报错翻译


def gutter_violations(message: str) -> list[tuple[str, str, float, float]]:
    """从门禁报错里解析出 (节点 id, 容器 id, 实测留白, 要求下限)。"""
    return [
        (node_id.strip(), container_id.strip(), _num(actual), _num(limit))
        for node_id, container_id, actual, limit in GUTTER_RE.findall(message or "")
    ]


def explain(spec: dict[str, Any], message: str) -> str:
    """把 CONTAINER_GUTTER 报错翻译成带具体坐标与改法的提示。"""
    violations = gutter_violations(message)
    if not violations:
        return message

    nodes = {str(n.get("id")): n for n in (spec.get("nodes") or []) if isinstance(n, dict)}
    containers = {str(c.get("id")): c for c in (spec.get("containers") or []) if isinstance(c, dict)}

    details: list[str] = []
    for node_id, container_id, actual, limit in violations:
        node, container = nodes.get(node_id), containers.get(container_id)
        if node is None or container is None:
            continue
        node_b, cont_b = _rect(node), _rect(container)
        gutters = _gutters(node_b, cont_b)
        worst = min(range(4), key=lambda i: gutters[i])
        side, expr = _SIDES[worst]
        gap = round(gutters[worst], 2)

        if gap < 0:
            head = f"节点 {node_id} 的{side}边越出容器 {container_id} {abs(gap)}px"
        else:
            head = f"节点 {node_id} 距容器 {container_id} {side}边仅 {gap}px（要求 ≥ {limit}）"

        details.append(
            f"{head}（{node_id}.{expr} = {round(node_b[worst], 2)}，"
            f"{container_id} 对应边界 = {round(cont_b[worst], 2)}）"
            f" → {_suggestion(node_b, cont_b, worst, max(limit, TARGET_GUTTER))}"
        )

    return "\n".join(details) if details else message


def _suggestion(
    node_b: tuple[float, float, float, float],
    cont_b: tuple[float, float, float, float],
    side: int,
    target: float,
) -> str:
    """给出「改 x/y」或「改 width/height」两种方案的具体数值。"""
    if side == 0:  # 左越界：右移 x
        return f"把 x 改为 ≥ {round(cont_b[0] + target, 2)}（当前 {round(node_b[0], 2)}）"
    if side == 1:  # 上越界：下移 y
        return f"把 y 改为 ≥ {round(cont_b[1] + target, 2)}（当前 {round(node_b[1], 2)}）"
    if side == 2:  # 右越界：缩 width 或左移 x
        max_right = round(cont_b[2] - target, 2)
        new_w = round(max_right - node_b[0], 2)
        return f"把 x 改为 ≤ {round(max_right - (node_b[2] - node_b[0]), 2)} 或 width 改为 ≤ {new_w}"
    max_bottom = round(cont_b[3] - target, 2)
    new_h = round(max_bottom - node_b[1], 2)
    return f"把 y 改为 ≤ {round(max_bottom - (node_b[3] - node_b[1]), 2)} 或 height 改为 ≤ {new_h}"


# ---------------------------------------------------------------- 自动回收


def fit_into_containers(
    spec: dict[str, Any], target: float = TARGET_GUTTER
) -> tuple[dict[str, Any], list[str]]:
    """把越出容器的节点收回去。

    策略：**先平移、放不下才缩小**。平移不改变节点尺寸，不会引入文字溢出；
    只有容器确实装不下时才等比压缩，且不低于 MIN_WIDTH / MIN_HEIGHT。
    返回 (新 spec, 改动明细)；没有越界时改动明细为空列表。
    """
    nodes = [n for n in (spec.get("nodes") or []) if isinstance(n, dict)]
    containers = [c for c in (spec.get("containers") or []) if isinstance(c, dict)]
    if not nodes or not containers:
        return spec, []

    bounds = [(str(c.get("id") or f"container-{i}"), _rect(c)) for i, c in enumerate(containers)]
    changes: list[str] = []

    for node in nodes:
        node_b = _rect(node)
        match = containing_container(node_b, bounds)
        if match is None:
            continue
        container_id, cont_b = match
        if min(_gutters(node_b, cont_b)) >= target:
            continue

        x, y = _num(node.get("x")), _num(node.get("y"))
        w = _num(node.get("width"), DEFAULT_WIDTH)
        h = _num(node.get("height"), DEFAULT_HEIGHT)
        before = (round(x, 2), round(y, 2), round(w, 2), round(h, 2))

        # 水平方向：先缩到能放下（不低于下限），再夹紧位置
        inner_w = (cont_b[2] - cont_b[0]) - 2 * target
        if inner_w < MIN_WIDTH:
            w = max(MIN_WIDTH, min(w, cont_b[2] - cont_b[0]))
            x = (cont_b[0] + cont_b[2] - w) / 2
        else:
            w = min(w, inner_w)
            x = min(max(x, cont_b[0] + target), cont_b[2] - target - w)

        inner_h = (cont_b[3] - cont_b[1]) - 2 * target
        if inner_h < MIN_HEIGHT:
            h = max(MIN_HEIGHT, min(h, cont_b[3] - cont_b[1]))
            y = (cont_b[1] + cont_b[3] - h) / 2
        else:
            h = min(h, inner_h)
            y = min(max(y, cont_b[1] + target), cont_b[3] - target - h)

        after = (round(x, 2), round(y, 2), round(w, 2), round(h, 2))
        if after == before:
            continue

        node["x"], node["y"], node["width"], node["height"] = x, y, w, h
        parts = []
        for label, old, new in zip(("x", "y", "width", "height"), before, after):
            if abs(old - new) > 0.01:
                parts.append(f"{label} {old}→{new}")
        changes.append(f"节点 {node.get('id')} 收回容器 {container_id}（{'，'.join(parts)}）")

    return spec, changes


__all__ = [
    "TARGET_GUTTER",
    "explain",
    "fit_into_containers",
    "gutter_violations",
]
