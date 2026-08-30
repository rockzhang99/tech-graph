"""大模型客户端与提示词构建。

只要求「语义蓝图」，不要求坐标——坐标交给 layout.py 确定性计算。
这样模型不需要做算术，输出更稳定，也不会撞上几何门禁。
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Any

DEFAULT_TIMEOUT = 120

SYSTEM_PROMPT = """You convert a natural-language system description into a STRICT JSON blueprint for a technical diagram.

Output ONLY a JSON object, no markdown fences, no commentary.

Schema:
{
  "title": "short diagram title",
  "subtitle": "one-line description",
  "lanes": [{"name": "Layer Name", "nodes": ["node_id_1", "node_id_2"]}],
  "nodes": [
    {"id": "snake_case_id", "label": "Human Label", "kind": "rect", "type_label": "ROLE"}
  ],
  "edges": [
    {"source": "node_id", "target": "node_id", "flow": "control", "label": "verb"}
  ]
}

Rules:
- 4 to 10 nodes. Use "lanes" to group nodes into horizontal layers, ordered top to bottom.
- Every node id referenced in "lanes" and "edges" MUST exist in "nodes".
- ids: lowercase snake_case, ascii only.
- "kind" must be one of: rect, double_rect, cylinder, document, terminal, hexagon, circle, circle_cluster, folder, speech, icon_box, user_avatar, bot, cloud_service, review_card, transit_station, transit_junction, transit_terminal, ops_service, trace_span, otel_collector
- "flow" must be one of: control, data, read, write, async, feedback, neutral
- Keep labels under 26 characters. Keep edge labels to one short verb.
- Prefer a clear top-to-bottom flow. Avoid cycles.
- Respond in the same language as the user's description for labels and title."""

FEWSHOT = """Example

User: Draw a RAG pipeline: user query, embed, vector search, rerank, LLM, answer. Style 1.

JSON:
{"title":"RAG Pipeline","subtitle":"retrieval augmented generation flow","lanes":[{"name":"Input","nodes":["user_query"]},{"name":"Retrieval","nodes":["embedder","vector_store"]},{"name":"Generation","nodes":["llm"]},{"name":"Output","nodes":["answer"]}],"nodes":[{"id":"user_query","label":"User Query","kind":"user_avatar","type_label":"INPUT"},{"id":"embedder","label":"Embedder","kind":"rect","type_label":"ENCODE"},{"id":"vector_store","label":"Vector Store","kind":"cylinder","type_label":"INDEX"},{"id":"llm","label":"LLM","kind":"double_rect","type_label":"REASON"},{"id":"answer","label":"Answer","kind":"document","type_label":"OUTPUT"}],"edges":[{"source":"user_query","target":"embedder","flow":"control","label":"submit"},{"source":"embedder","target":"vector_store","flow":"write","label":"index"},{"source":"vector_store","target":"llm","flow":"read","label":"context"},{"source":"llm","target":"answer","flow":"data","label":"generate"}]}"""


def build_messages(prompt: str, mode: str, style: int, lang: str = "auto") -> list[dict[str, str]]:
    """构造对话消息。"""
    user = (
        f"Diagram type: {mode}\n"
        f"Visual style: {style}\n"
        f"Language for text: {lang}\n\n"
        f"Description:\n{prompt.strip()}\n\n"
        "Return only the JSON blueprint."
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": FEWSHOT},
        {"role": "assistant", "content": FEWSHOT.split("JSON:\n", 1)[1]},
        {"role": "user", "content": user},
    ]


# ---------------------------------------------------------------- 调用


class LLMError(RuntimeError):
    """大模型调用失败。"""


def _post(url: str, payload: dict[str, Any], headers: dict[str, str], timeout: int) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", "replace")[:500]
        except Exception:
            pass
        raise LLMError(f"HTTP {exc.code}: {body or exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise LLMError(f"连接失败：{exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise LLMError(f"响应不是合法 JSON：{exc}") from exc


def chat_completions(
    *,
    base_url: str,
    api_key: str,
    model: str,
    messages: list[dict[str, str]],
    temperature: float = 0.2,
    timeout: int = DEFAULT_TIMEOUT,
) -> str:
    """调用 OpenAI 兼容的 /chat/completions 接口，返回消息文本。"""
    if not base_url:
        raise LLMError("未配置 API 地址")
    if not model:
        raise LLMError("未配置模型名称")

    url = base_url.rstrip("/")
    if not url.endswith("/chat/completions"):
        url = url + "/chat/completions"

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "stream": False,
    }
    result = _post(url, payload, headers, timeout)

    try:
        return result["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError(f"响应结构异常：{str(result)[:300]}") from exc


def list_models(*, base_url: str, api_key: str, timeout: int = 20) -> list[str]:
    """拉取可用模型列表（用于配置页下拉）。"""
    url = base_url.rstrip("/")
    # 兼容传入完整 endpoint 的情况
    url = re.sub(r"/chat/completions$", "", url)
    if not url.endswith("/models"):
        url = url + "/models"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        raise LLMError(f"获取模型列表失败：{exc}") from exc
    items = data.get("data") or data.get("models") or []
    names = []
    for item in items:
        if isinstance(item, str):
            names.append(item)
        elif isinstance(item, dict) and item.get("id"):
            names.append(item["id"])
    return sorted(set(names))


# ---------------------------------------------------------------- 解析


def extract_json(text: str) -> dict[str, Any]:
    """从模型输出里稳健地抠出 JSON 对象。

    模型常会裹 ```json 代码块或前后加说明，这里逐级降级处理。
    """
    if not text:
        raise LLMError("模型返回为空")
    cleaned = text.strip()

    # 去掉 Markdown 代码块
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", cleaned)
    if fence:
        cleaned = fence.group(1).strip()

    # 直接解析
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    # 截取第一个 { 到最后一个 }
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end > start:
        try:
            parsed = json.loads(cleaned[start:end + 1])
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError as exc:
            raise LLMError(f"JSON 解析失败：{exc}") from exc

    raise LLMError(f"未能从模型输出中解析出 JSON：{text[:200]}")
