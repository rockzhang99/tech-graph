"""本地启发式解析器：没配置大模型时的兜底方案。

从自然语言里抽取出「节点 + 连线 + 分层」，交给同一个布局引擎铺开。
能力有限，但保证零配置也能用，且完全离线。
"""

from __future__ import annotations

import re
from typing import Any

# 常见层级关键词：命中即作为泳道名，顺序即自上而下
LANE_KEYWORDS = [
    (("input", "ingress", "client", "source", "输入", "接入", "客户端", "入口"), "Input"),
    (("gateway", "edge", "lb", "proxy", "网关", "接入层", "边缘"), "Edge"),
    (("process", "service", "business", "compute", "处理", "服务", "业务", "计算"), "Processing"),
    (("agent", "orchestrat", "planner", "reason", "智能体", "编排", "推理"), "Agent"),
    (("memory", "store", "storage", "persist", "database", "记忆", "存储", "持久化"), "Storage"),
    (("retriev", "search", "recall", "检索", "召回"), "Retrieval"),
    (("output", "response", "deliver", "result", "输出", "响应", "交付"), "Output"),
    (("monitor", "observ", "telemetry", "eval", "评估", "监控", "观测"), "Observability"),
]

# 箭头分隔符，按长度降序匹配
ARROWS = ["→", "->", "=>", "-->", "｜>", "＞", ">"]


def _split_arrows(text: str) -> list[str] | None:
    """按箭头切分链路，如「A → B → C」。"""
    for arrow in ARROWS:
        if arrow in text:
            parts = [p.strip() for p in text.split(arrow)]
            if len(parts) >= 2 and all(parts):
                return parts
    return None


def _make_id(label: str, index: int) -> str:
    slug = re.sub(r"[^0-9a-zA-Z_\u4e00-\u9fff]+", "_", label).strip("_").lower()
    if not slug:
        slug = f"node{index + 1}"
    return slug


def _guess_lane(label: str) -> str:
    low = label.lower()
    for keywords, name in LANE_KEYWORDS:
        if any(k in low for k in keywords):
            return name
    return ""


def parse(prompt: str, mode: str) -> dict[str, Any]:
    """把自然语言描述解析成语义蓝图。"""
    text = (prompt or "").strip()
    if not text:
        raise ValueError("请输入描述内容")

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    seen: dict[str, str] = {}

    def add_node(label: str) -> str:
        label = label.strip().strip("，,。.;；:：")
        if not label:
            return ""
        key = label.lower()
        if key in seen:
            return seen[key]
        node_id = _make_id(label, len(nodes))
        if node_id in [n["id"] for n in nodes]:
            node_id = f"{node_id}_{len(nodes)}"
        seen[key] = node_id
        nodes.append({
            "id": node_id,
            "label": label[:26],
            "kind": "rect",
            "type_label": _guess_lane(label).upper() or "STEP",
            "lane": _guess_lane(label),
        })
        return node_id

    # 1) 优先识别显式链路
    chain = _split_arrows(text)
    if chain:
        prev = ""
        for part in chain:
            node_id = add_node(part[:40])
            if not node_id:
                continue
            if prev:
                edges.append({"source": prev, "target": node_id, "flow": "control", "label": ""})
            prev = node_id

    # 2) 否则按分隔符切分并列元素
    if not nodes:
        for chunk in re.split(r"[、,，;；\n]+", text):
            chunk = chunk.strip()
            if not chunk or len(chunk) > 40:
                continue
            add_node(chunk)

        # 并列元素串成顺序链（流程图语义）
        for index in range(len(nodes) - 1):
            edges.append({
                "source": nodes[index]["id"],
                "target": nodes[index + 1]["id"],
                "flow": "control",
                "label": "",
            })

    if not nodes:
        raise ValueError("没能从描述中识别出节点，试试「A → B → C」这样的写法，或配置一个大模型")

    # 3) 泳道：按节点自带 lane 归并，保持首次出现顺序
    lanes: list[dict[str, Any]] = []
    for node in nodes:
        name = node.get("lane") or "流程"
        match = next((l for l in lanes if l["name"] == name), None)
        if match is None:
            match = {"name": name, "nodes": []}
            lanes.append(match)
        match["nodes"].append(node["id"])

    return {
        "title": text[:40] if len(text) <= 40 else text[:37] + "...",
        "subtitle": "",
        "lanes": lanes,
        "nodes": nodes,
        "edges": edges,
    }
