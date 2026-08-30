"""自动布局引擎：把「语义结构」铺成几何安全的图规格 JSON。

设计取舍：大语言模型擅长理解语义（有哪些节点、谁连谁、什么含义），
但不擅长算数——直接让它输出坐标几乎必然撞上间距/路由/标签门禁。
因此这里只让模型产出语义，坐标与端口全部由本地确定性算法计算，
再交由 Skill 的校验器把关，失败时按策略放宽重排。
"""

from __future__ import annotations

import math
from typing import Any

# ---------------------------------------------------------------- 常量

CANVAS_WIDTH = 960

# 各类图的默认画布高度；节点多时会按需增高
BASE_HEIGHT = {
    "architecture": 600,
    "data-flow": 600,
    "flowchart": 640,
    "sequence": 700,
    "comparison": 620,
    "timeline": 520,
    "mind-map": 620,
    "agent": 700,
    "memory": 720,
    "use-case": 600,
    "class": 700,
    "state-machine": 620,
    "er-diagram": 680,
    "network-topology": 620,
}

NODE_W = 180
NODE_H = 56

MARGIN_X = 40
TOP_Y = 118          # 标题与副标题占位
LANE_GAP = 26        # 泳道带之间的垂直间距
LANE_PAD_Y = 30      # 泳道内节点到带顶/底的留白
LANE_HEADER = 26     # 泳道标题条高度
BOTTOM_SPACE = 96    # 图例与页脚占位
MIN_NODE_GAP = 44    # 同层节点最小水平间距（> showcase 的 40）
MIN_LANE_W = 220

MAX_PER_ROW = 5      # 单行最多节点数，超出则折行，避免挤成细条
ROW_GAP = 30         # 折行后的行间距
MIN_GAP_FLOOR = 14   # 间距压缩下限
MIN_NODE_SCALE = 0.72  # 节点等比缩小下限，再小文字会溢出

# 允许的取值白名单，模型输出会先被收敛到这里
ALLOWED_KINDS = {
    "rect", "double_rect", "cylinder", "document", "terminal", "hexagon",
    "circle", "circle_cluster", "folder", "speech", "icon_box",
    "user_avatar", "bot", "cloud_service", "review_card",
    "transit_station", "transit_junction", "transit_terminal",
    "ops_service", "trace_span", "otel_collector",
}
ALLOWED_FLOWS = {"control", "data", "read", "write", "async", "feedback", "neutral"}

# 语义关键词 → 图形种类。模型没给 kind 时兜底推断。
KIND_HINTS: list[tuple[tuple[str, ...], str]] = [
    (("user", "client", "browser", "person", "用户", "客户", "浏览器"), "user_avatar"),
    (("agent", "orchestrator", "coordinator", "planner", "智能体", "编排", "协调"), "hexagon"),
    (("llm", "model", "gpt", "claude", "gemini", "reasoning", "大模型", "模型"), "double_rect"),
    (("vector", "embedding", "faiss", "pinecone", "weaviate", "qdrant", "向量"), "cylinder"),
    (("database", "postgres", "mysql", "mongo", "redis", "db", "storage", "数据库", "存储"), "cylinder"),
    (("graph", "neo4j", "knowledge", "图数据库", "知识图谱"), "circle_cluster"),
    (("queue", "kafka", "stream", "topic", "pulsar", "nats", "队列", "消息", "流"), "rect"),
    (("api", "gateway", "endpoint", "server", "service", "网关", "接口", "服务"), "hexagon"),
    (("cache", "缓存"), "cylinder"),
    (("file", "document", "pdf", "doc", "文件", "文档"), "document"),
    (("folder", "bucket", "s3", "目录", "存储桶"), "folder"),
    (("tool", "function", "plugin", "工具", "函数"), "rect"),
    (("terminal", "shell", "console", "终端", "命令行"), "terminal"),
]

# Style 9-12 是「工程语义」风格，渲染前会先跑领域契约校验，
# 缺字段会直接 fail closed。这里补上各自的必需声明。
# 注意：这些风格还有更深的内容级要求（如 Style 11 需要 topics、
# Style 12 需要 ops_role=service 的节点），自动生成的通用图未必满足。
STYLE_SEMANTICS: dict[int, dict[str, Any]] = {
    9: {
        "semantic_profile": "c4-review",
        "diagram_type": "c4",
        "c4_level": "container",
    },
    10: {
        "semantic_profile": "cloud-fabric",
        "diagram_type": "deployment",
        "deployment_mode": "ACTIVE-ACTIVE",
    },
    11: {
        "semantic_profile": "event-transit",
        "diagram_type": "event_stream",
        "line_code": "LINE A · EVENT METRO",
    },
    12: {
        "semantic_profile": "ops-pulse",
        "diagram_type": "observability",
        "observation_window": "5m",
    },
}

# 连线关键词 → 流向语义
FLOW_HINTS: list[tuple[tuple[str, ...], str]] = [
    (("write", "store", "save", "persist", "update", "写入", "存储", "保存"), "write"),
    (("read", "query", "fetch", "retrieve", "get", "读取", "查询", "检索"), "read"),
    (("async", "event", "emit", "publish", "notify", "异步", "事件", "发布"), "async"),
    (("feedback", "monitor", "metric", "trace", "log", "telemetry", "反馈", "监控", "遥测"), "feedback"),
    (("data", "sync", "replicate", "transfer", "数据", "同步", "传输"), "data"),
]


# ---------------------------------------------------------------- 小工具


def _slug(text: str, fallback: str = "node") -> str:
    """把任意文本收敛成安全的 id。"""
    raw = "".join(ch if (ch.isalnum() or ch == "_") else "_" for ch in str(text).strip())
    raw = raw.strip("_").lower()
    return raw or fallback


def _unique_ids(items: list[dict[str, Any]], fallback_prefix: str) -> None:
    """原地为每个节点补足唯一 id。"""
    seen: set[str] = set()
    for index, item in enumerate(items):
        base = _slug(item.get("id") or item.get("label") or f"{fallback_prefix}{index + 1}",
                     f"{fallback_prefix}{index + 1}")
        candidate = base
        n = 2
        while candidate in seen:
            candidate = f"{base}_{n}"
            n += 1
        seen.add(candidate)
        item["id"] = candidate


def infer_kind(node: dict[str, Any]) -> str:
    """按标签关键词推断图形种类。"""
    kind = str(node.get("kind") or "").strip().lower()
    if kind in ALLOWED_KINDS:
        return kind
    text = f"{node.get('label', '')} {node.get('type_label', '')}".lower()
    for keywords, mapped in KIND_HINTS:
        if any(k in text for k in keywords):
            return mapped
    return "rect"


def infer_flow(edge: dict[str, Any]) -> str:
    """按标签关键词推断连线语义。"""
    flow = str(edge.get("flow") or "").strip().lower()
    if flow in ALLOWED_FLOWS:
        return flow
    if flow in {"main", "api"}:
        return "control"
    text = str(edge.get("label", "")).lower()
    for keywords, mapped in FLOW_HINTS:
        if any(k in text for k in keywords):
            return mapped
    return "control"


def _node_width(label: str, kind: str) -> int:
    """按标签长度与图形种类估算宽度，避免文字溢出。"""
    # 中文按 2 个字符宽算，英文按 1 个
    weight = sum(2 if ord(ch) > 0x2E80 else 1 for ch in str(label))
    width = 150 + weight * 7
    if kind in {"cylinder", "circle_cluster"}:
        width = max(width, 160)
    if kind in {"terminal", "document", "folder"}:
        width = max(width, 170)
    return int(min(300, max(NODE_W, math.ceil(width / 10) * 10)))


# ---------------------------------------------------------------- 层级计算


def compute_levels(nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> dict[str, int]:
    """用最长路径分层（Kahn 拓扑排序），保证每条边都从低层指向高层。"""
    ids = {n["id"] for n in nodes}
    indegree = {n["id"]: 0 for n in nodes}
    children: dict[str, list[str]] = {n["id"]: [] for n in nodes}

    for edge in edges:
        src, dst = edge["source"], edge["target"]
        if src not in ids or dst not in ids or src == dst:
            continue
        children[src].append(dst)
        indegree[dst] += 1

    level = {n["id"]: 0 for n in nodes}
    queue = [n["id"] for n in nodes if indegree[n["id"]] == 0]
    processed = 0

    while queue:
        current = queue.pop(0)
        processed += 1
        for child in children[current]:
            level[child] = max(level[child], level[current] + 1)
            indegree[child] -= 1
            if indegree[child] == 0:
                queue.append(child)

    # 存在环时，剩余节点按出现顺序顺延，避免全部堆在第 0 层
    if processed < len(nodes):
        remaining = [n["id"] for n in nodes if n["id"] not in queue and indegree.get(n["id"], 0) > 0]
        for index, node_id in enumerate(remaining):
            level[node_id] = max(level.values(), default=0) + 1 + index // 3
    return level


# ---------------------------------------------------------------- 主入口


def build_spec(
    blueprint: dict[str, Any],
    mode: str,
    style: int,
    *,
    gap_scale: float = 1.0,
) -> dict[str, Any]:
    """把语义蓝图铺成完整的图规格 JSON。

    blueprint 结构（来自大模型或本地启发式解析）：
      {"title","subtitle","lanes":[{"name","nodes":[...]}],
       "nodes":[{"id","label","kind","type_label","lane"}],
       "edges":[{"source","target","flow","label"}]}
    """
    nodes = [dict(n) for n in blueprint.get("nodes", []) if n.get("label") or n.get("id")]
    edges = [dict(e) for e in blueprint.get("edges", [])]

    _unique_ids(nodes, "node")

    # 丢弃悬空引用，避免校验器直接拒绝
    known = {n["id"] for n in nodes}
    edges = [e for e in edges if e.get("source") in known and e.get("target") in known]

    for node in nodes:
        node["kind"] = infer_kind(node)
        node.setdefault("label", node["id"])
    for edge in edges:
        edge["flow"] = infer_flow(edge)

    if not nodes:
        raise ValueError("未能从描述中提取出任何节点，请换一种说法或写得更具体一些")

    lanes = _resolve_lanes(blueprint, nodes, edges)
    return _compose(nodes, edges, lanes, blueprint, mode, style, gap_scale)


def _resolve_lanes(
    blueprint: dict[str, Any], nodes: list[dict[str, Any]], edges: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """确定泳道（横向分组带）。

    优先用模型显式给出的 lanes；否则用节点上的 lane 字段；
    都没有就按拓扑层级自动切分。
    """
    by_id = {n["id"]: n for n in nodes}
    lanes: list[dict[str, Any]] = []

    for raw in blueprint.get("lanes", []) or []:
        if isinstance(raw, str):
            lanes.append({"name": raw, "node_ids": []})
        elif isinstance(raw, dict):
            lanes.append({
                "name": raw.get("name") or raw.get("label") or "",
                "node_ids": [i for i in (raw.get("nodes") or []) if i in by_id],
            })

    # 节点自带 lane 字段
    for node in nodes:
        lane_name = node.get("lane") or node.get("group")
        if not lane_name:
            continue
        match = next((l for l in lanes if l["name"] == lane_name), None)
        if match is None:
            match = {"name": lane_name, "node_ids": []}
            lanes.append(match)
        if node["id"] not in match["node_ids"]:
            match["node_ids"].append(node["id"])

    # 没有分组线索时按拓扑层级自动切
    if not lanes or not any(l["node_ids"] for l in lanes):
        levels = compute_levels(nodes, edges)
        buckets: dict[int, list[str]] = {}
        for node in nodes:
            buckets.setdefault(levels[node["id"]], []).append(node["id"])
        lanes = [
            {"name": "", "node_ids": buckets[key]} for key in sorted(buckets)
        ]

    # 未归组的节点补到末尾一条泳道
    placed = {i for l in lanes for i in l["node_ids"]}
    orphans = [n["id"] for n in nodes if n["id"] not in placed]
    if orphans:
        lanes.append({"name": "", "node_ids": orphans})

    return [l for l in lanes if l["node_ids"]]


def _pack_rows(
    ids: list[str],
    sizes: dict[str, tuple[int, int]],
    inner: float,
    gap: float,
) -> tuple[list[list[str]], list[float]]:
    """把一条泳道里的节点打包成若干行。

    从「尽量少分行」开始尝试，只要节点会被压缩到不可读就增加行数；
    这样既不会出现细长条，也不会溢出容器。返回 (行分组, 每行缩放系数)。
    """
    total = len(ids)
    if total == 0:
        return [], []

    per_row = min(total, MAX_PER_ROW)
    while per_row > 1:
        rows = [ids[i:i + per_row] for i in range(0, total, per_row)]
        scales: list[float] = []
        feasible = True
        for row in rows:
            widths = [sizes[i][0] for i in row]
            raw = sum(widths)
            count = len(row)
            row_gap = gap
            if count > 1:
                avail_for_gap = inner - raw
                if avail_for_gap < row_gap * (count - 1):
                    row_gap = max(MIN_GAP_FLOOR, avail_for_gap / (count - 1))
            needed = raw + row_gap * (count - 1)
            if needed <= inner:
                scales.append(1.0)
                continue
            avail = inner - row_gap * (count - 1)
            scale = avail / raw if raw else 1.0
            if scale < MIN_NODE_SCALE:
                feasible = False
                break
            scales.append(scale)
        if feasible:
            return rows, scales
        per_row -= 1

    # 兜底：每行一个节点，宽度不足时仍按可放下处理
    rows = [[i] for i in ids]
    scales = [
        min(1.0, inner / sizes[i][0]) if sizes[i][0] > inner else 1.0 for i in ids
    ]
    return rows, scales


def _compose(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    lanes: list[dict[str, Any]],
    blueprint: dict[str, Any],
    mode: str,
    style: int,
    gap_scale: float,
) -> dict[str, Any]:
    """计算坐标并组装成 Skill 可接受的 spec。"""
    by_id = {n["id"]: n for n in nodes}

    # 1) 每个节点先定尺寸
    sizes = {}
    for node in nodes:
        sizes[node["id"]] = (_node_width(node["label"], node["kind"]), NODE_H)

    # 2) 逐条泳道计算带高与内部水平排布
    content_left = MARGIN_X + 20
    content_right = CANVAS_WIDTH - MARGIN_X - 20
    content_width = content_right - content_left

    gap = MIN_NODE_GAP * gap_scale
    lane_layouts: list[dict[str, Any]] = []
    cursor_y = TOP_Y

    for index, lane in enumerate(lanes):
        ids = lane["node_ids"]

        # 自适应打包：先尽量少分行，放不下就增加行数，直到节点不再被压扁
        rows, row_scales = _pack_rows(ids, sizes, content_width - 2 * LANE_PAD_Y, gap)

        positions: list[dict[str, Any]] = []
        for row_index, (row, scale) in enumerate(zip(rows, row_scales)):
            widths = [sizes[i][0] * scale for i in row]
            inner = content_width - 2 * LANE_PAD_Y
            count = len(row)
            row_gap = gap

            if count > 1:
                avail_for_gap = inner - sum(widths)
                if avail_for_gap < row_gap * (count - 1):
                    row_gap = max(MIN_GAP_FLOOR, avail_for_gap / (count - 1))

            total = sum(widths) + row_gap * (count - 1)
            start_x = content_left + LANE_PAD_Y + max(0.0, (inner - total) / 2)
            row_y = cursor_y + LANE_HEADER + LANE_PAD_Y + row_index * (NODE_H + ROW_GAP)

            x = start_x
            for node_id, w in zip(row, widths):
                positions.append({
                    "id": node_id,
                    "x": round(x, 1),
                    "y": round(row_y, 1),
                    "w": round(w, 1),
                    "h": NODE_H,
                    "row": row_index,
                })
                x += w + row_gap

        row_count = len(rows)
        lane_h = (
            LANE_HEADER + LANE_PAD_Y * 2
            + row_count * NODE_H
            + (row_count - 1) * ROW_GAP
        )

        lane_layouts.append({
            "name": lane["name"],
            "y": cursor_y,
            "height": lane_h,
            "positions": positions,
            "count": len(ids),
        })
        cursor_y += lane_h + LANE_GAP

    height = max(BASE_HEIGHT.get(mode, 620), int(cursor_y + BOTTOM_SPACE))

    # 3) 端口方向：跨泳道用 下→上，同泳道内用 右→左（反向则左→右）
    lane_index = {}
    for lane_i, layout in enumerate(lane_layouts):
        for pos in layout["positions"]:
            lane_index[pos["id"]] = (lane_i, pos)

    def ports_for(edge: dict[str, Any]) -> tuple[str, str]:
        """端口方向：跨泳道或跨行走垂直，同行才走水平，避免反向折线。"""
        src, dst = edge["source"], edge["target"]
        if src not in lane_index or dst not in lane_index:
            return "bottom", "top"
        si, spos = lane_index[src]
        di, dpos = lane_index[dst]
        if si != di:
            return ("bottom", "top") if di > si else ("top", "bottom")
        # 同一泳道：跨行走垂直，同行按左右位置走水平
        if spos["row"] != dpos["row"]:
            return ("bottom", "top") if dpos["row"] > spos["row"] else ("top", "bottom")
        return ("right", "left") if dpos["x"] >= spos["x"] else ("left", "right")

    # 4) 组装节点
    out_nodes = []
    for layout in lane_layouts:
        for pos in layout["positions"]:
            node = by_id[pos["id"]]
            item: dict[str, Any] = {
                "id": pos["id"],
                "kind": node["kind"],
                "x": pos["x"],
                "y": pos["y"],
                "width": pos["w"],
                "height": pos["h"],
                "label": node["label"],
            }
            if node.get("type_label"):
                item["type_label"] = str(node["type_label"]).upper()
            if node.get("subtitle"):
                item["subtitle"] = node["subtitle"]
            out_nodes.append(item)

    # 5) 容器泳道
    out_containers = []
    for index, layout in enumerate(lane_layouts):
        if not layout["name"]:
            continue
        out_containers.append({
            "id": f"lane_{index}",
            "x": MARGIN_X,
            "y": round(layout["y"], 1),
            "width": CANVAS_WIDTH - MARGIN_X * 2,
            "height": round(layout["height"], 1),
            "label": layout["name"],
        })

    # 6) 连线
    out_edges = []
    for index, edge in enumerate(edges):
        src_port, dst_port = ports_for(edge)
        item: dict[str, Any] = {
            "id": f"e{index}",
            "source": edge["source"],
            "target": edge["target"],
            "source_port": src_port,
            "target_port": dst_port,
            "flow": edge["flow"],
        }
        if edge.get("label"):
            item["label"] = str(edge["label"])
        out_edges.append(item)

    # 7) 图例：按实际用到的 flow 生成
    used_flows = []
    for edge in out_edges:
        if edge["flow"] not in used_flows:
            used_flows.append(edge["flow"])
    legend_labels = {
        "control": "primary flow",
        "data": "data movement",
        "read": "read / query",
        "write": "write / store",
        "async": "async event",
        "feedback": "feedback / telemetry",
        "neutral": "association",
    }
    legend = [{"flow": f, "label": legend_labels.get(f, f)} for f in used_flows]

    title = blueprint.get("title") or "Technical Diagram"
    spec: dict[str, Any] = {
        "schema_version": 1,
        "mode": mode,
        "template_type": mode,
        "style": style,
        "quality_profile": "standard",
        "width": CANVAS_WIDTH,
        "height": height,
        "title": title,
        "containers": out_containers,
        "nodes": out_nodes,
        "arrows": out_edges,
    }
    if blueprint.get("subtitle"):
        spec["subtitle"] = blueprint["subtitle"]

    # 工程语义风格（9-12）补上契约必填声明
    spec.update(STYLE_SEMANTICS.get(style, {}))
    if style == 12 and out_nodes:
        spec["critical_path_id"] = out_nodes[0]["id"]

    if legend and len(legend) <= 4:
        spec["legend"] = legend
        spec["legend_orientation"] = "horizontal"
        spec["legend_x"] = MARGIN_X + 8
        spec["legend_y"] = height - 52

    spec["footer"] = f"Style {style} · {mode} · generated from prompt"
    spec["footer_x"] = MARGIN_X + 8
    spec["footer_y"] = height - 22
    return spec
