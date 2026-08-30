"""Fireworks Tech Graph —— 本地 Web 控制台后端。

把 Skill 的 CLI 能力（render / check / inspect / PNG / GIF / 交互 HTML）
包装成 HTTP 接口，供浏览器界面调用。仅在 127.0.0.1 监听。
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

# ---------------------------------------------------------------- 路径与引导

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
STATIC_DIR = HERE / "static"
RUNTIME_DIR = HERE / "runtime"

SKILL_ROOT = Path(
    os.environ.get(
        "FIREWORKS_SKILL_ROOT",
        str(Path.home() / ".codebuddy" / "skills" / "fireworks-tech-graph"),
    )
).resolve()
SCRIPTS_DIR = SKILL_ROOT / "scripts"

CHROME_CANDIDATES = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
)


def find_chrome() -> str | None:
    """按优先级探测可用的 Chromium 内核浏览器。"""
    env = os.environ.get("FIREWORKS_CHROME_PATH")
    if env and Path(env).exists():
        return env
    for candidate in CHROME_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    for name in ("chrome", "msedge", "chromium", "google-chrome"):
        found = shutil.which(name)
        if found:
            return found
    return None


CHROME = find_chrome()

# GIF 动效链路需要这两个变量，缺一个就会 MODULE_NOT_FOUND。
os.environ.setdefault(
    "FIREWORKS_PUPPETEER_PATH", str(SKILL_ROOT / "node_modules" / "puppeteer-core")
)
if CHROME:
    os.environ.setdefault("FIREWORKS_CHROME_PATH", CHROME)

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from diagram_ir import DiagramValidationError  # noqa: E402
from interactive_html import build_interactive_html  # noqa: E402
from motion import probe_motion_runtime, render_motion_gif  # noqa: E402
from validate_svg import run_check  # noqa: E402

import heuristic  # noqa: E402
import layout as layout_engine  # noqa: E402
import llm  # noqa: E402


def _load_generator():
    """动态加载带连字符的生成器模块。"""
    spec = importlib.util.spec_from_file_location(
        "ftg_generator", SCRIPTS_DIR / "generate-from-template.py"
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载生成器：{SCRIPTS_DIR / 'generate-from-template.py'}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


GENERATOR = _load_generator()
VIEWBOX_DEFAULTS: dict[str, tuple[float, float]] = getattr(
    GENERATOR, "DEFAULT_VIEWBOX", {}
)
AI_AUTHORED_STYLES: dict[int, str] = getattr(GENERATOR, "_AI_AUTHORED_STYLES", {})

DIAGRAM_TYPES = [
    {"id": key, "viewBox": f"0 0 {int(w)} {int(h)}", "width": int(w), "height": int(h)}
    for key, (w, h) in VIEWBOX_DEFAULTS.items()
]

STYLE_NAMES = {
    1: ("Flat Icon", "扁平图标风"),
    2: ("Dark Terminal", "暗黑极客风"),
    3: ("Blueprint", "工程蓝图风"),
    4: ("Notion Clean", "Notion 极简风"),
    5: ("Glassmorphism", "玻璃态卡片风"),
    6: ("Claude Official", "Claude 官方风"),
    7: ("OpenAI Official", "OpenAI 官方风"),
    8: ("Dark Luxury", "暗夜奢华风"),
    9: ("C4 Review Canvas", "C4 评审画布"),
    10: ("Cloud Fabric", "云部署拓扑"),
    11: ("Event Transit", "事件流轨道"),
    12: ("Ops Pulse", "运维脉搏"),
}

# Style 9-12 是工程评审专用风格，渲染前会执行领域契约校验，要求
# C4 的 scope/seed、Cloud 的 icon manifest、Event 的 topic rails、Ops 的
# 黄金信号与 trace 瀑布等结构化数据。通用布局引擎产出的图无法满足，
# 因此不提供自动生成，引导用户改用内置示例或手写 JSON。
AUTO_STYLES = frozenset({1, 2, 3, 4, 5, 6, 7})

MOTION_PRESETS = {
    1: "memory-weave",
    2: "tool-grounding",
    3: "service-blueprint",
    4: "memory-lifecycle",
    5: "agent-orchestration",
    6: "governed-runtime",
    7: "token-stream",
    8: "golden-circuit",
    9: "review-trace",
    10: "cloud-flow",
    11: "event-transit",
    12: "ops-pulse",
}

CHECK_NAMES = ("xml", "markers", "geometry", "composition")
CHECK_LABELS = {
    "xml": "XML 结构",
    "markers": "箭头标记",
    "geometry": "几何安全",
    "composition": "构图质量",
}

RUNTIME_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------- 文件与工具


def _new_file(suffix: str) -> Path:
    return RUNTIME_DIR / f"{uuid.uuid4().hex}{suffix}"


def _write(svg_text: str, suffix: str = ".svg") -> Path:
    path = _new_file(suffix)
    path.write_text(svg_text, encoding="utf-8")
    return path


def _url(path: Path) -> str:
    return f"/files/{path.name}"


def _read_viewbox(svg_text: str) -> tuple[int, int]:
    """从 SVG 文本里解析画布尺寸，失败时回退 960x600。"""
    match = re.search(r'viewBox="0 0\s*([\d.]+)[ ,]+([\d.]+)"', svg_text[:4000])
    if match:
        try:
            return max(1, int(float(match.group(1)))), max(1, int(float(match.group(2))))
        except ValueError:
            pass
    for attr in ("width", "height"):
        pass
    return 960, 600


def _run_checks(svg_path: Path) -> dict[str, dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    for name in CHECK_NAMES:
        try:
            ok, details = run_check(svg_path, name)
        except Exception as exc:  # 校验器本身异常不能拖垮整个请求
            ok, details = False, [f"{type(exc).__name__}: {exc}"]
        results[name] = {
            "ok": bool(ok),
            "label": CHECK_LABELS[name],
            "details": [str(item) for item in (details or [])][:40],
        }
    return results


def _cairosvg_usable() -> tuple[bool, str]:
    """真正加载并渲染一次，避免 find_spec 的假阳性（Windows 缺 libcairo）。"""
    try:
        import cairosvg  # noqa: WPS433
    except Exception as exc:
        return False, str(exc)
    try:
        cairosvg.svg2png(
            bytestring=b'<svg xmlns="http://www.w3.org/2000/svg" width="8" height="8"><rect width="8" height="8"/></svg>',
            output_width=8,
        )
        return True, ""
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


# ---------------------------------------------------------------- 配置持久化

# API Key 落在 .git 忽略的 runtime 目录之外会污染工作区，这里统一放到
# 用户目录，避免被误提交。
CONFIG_PATH = Path(
    os.environ.get(
        "FTG_CONFIG",
        str(Path.home() / ".codebuddy" / "fireworks-tech-graph-config.json"),
    )
)

DEFAULT_CONFIG: dict[str, Any] = {
    "enabled": False,
    "base_url": "https://api.openai.com/v1",
    "api_key": "",
    "model": "gpt-4o-mini",
    "temperature": 0.2,
    "lang": "auto",
}


def load_config() -> dict[str, Any]:
    config = dict(DEFAULT_CONFIG)
    try:
        if CONFIG_PATH.exists():
            stored = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(stored, dict):
                config.update({k: v for k, v in stored.items() if k in DEFAULT_CONFIG})
    except Exception:
        pass
    # 环境变量优先级最高，方便无界面部署
    env_key = os.environ.get("OPENAI_API_KEY")
    env_url = os.environ.get("OPENAI_BASE_URL")
    env_model = os.environ.get("OPENAI_MODEL")
    if env_key:
        config["api_key"] = env_key
        config["enabled"] = True
    if env_url:
        config["base_url"] = env_url
    if env_model:
        config["model"] = env_model
    return config


def save_config(config: dict[str, Any]) -> None:
    payload = {key: config.get(key, DEFAULT_CONFIG[key]) for key in DEFAULT_CONFIG}
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _chrome_screenshot(svg_path: Path, out: Path, width: int, height: int, scale: float) -> subprocess.CompletedProcess:
    """用独立 profile 调用 Chrome 截图。

    必须隔离 user-data-dir：机器上已有常驻 Chrome 时，共享目录会让 headless
    实例去争抢单例锁而挂起。同时用 DEVNULL 收住 stdin，避免任何等待输入。
    """
    profile = Path(tempfile.mkdtemp(prefix="ftg-chrome-"))
    cmd = [
        CHROME,
        "--headless=new",
        "--disable-gpu",
        "--disable-software-rasterizer",
        "--no-sandbox",
        "--hide-scrollbars",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-extensions",
        "--disable-background-networking",
        "--disable-sync",
        "--metrics-recording-only",
        "--mute-audio",
        f"--user-data-dir={profile}",
        f"--force-device-scale-factor={scale:.6f}",
        f"--window-size={width},{height}",
        f"--screenshot={out}",
        svg_path.as_uri(),
    ]
    try:
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=150,
            stdin=subprocess.DEVNULL,
        )
    finally:
        shutil.rmtree(profile, ignore_errors=True)


def _sweep_runtime(max_age_seconds: int = 7200) -> int:
    """清理过期产物，避免长时间运行后磁盘堆积。"""
    removed = 0
    now = time.time()
    for path in RUNTIME_DIR.iterdir():
        try:
            if now - path.stat().st_mtime > max_age_seconds:
                path.unlink(missing_ok=True)
                removed += 1
        except OSError:
            continue
    return removed


# ---------------------------------------------------------------- 任务队列

TASKS: dict[str, dict[str, Any]] = {}
TASKS_LOCK = threading.Lock()


def _gif_worker(task_id: str, payload: dict[str, Any]) -> None:
    src = _write(payload["svg"], ".svg")
    out = _new_file(".gif")
    report = _new_file(".motion.json")
    try:
        result = render_motion_gif(
            src,
            out,
            report_path=report,
            preset=payload.get("preset", "auto"),
            duration=payload.get("duration", 5.75),
            fps=payload.get("fps", 20),
            width=payload.get("width", 960),
            dry_run=False,
        )
        with TASKS_LOCK:
            TASKS[task_id] = {
                "status": "done",
                "result": result,
                "gif_url": _url(out),
                "gif_name": f"{payload.get('name') or 'diagram'}.gif",
                "report_url": _url(report) if report.exists() else None,
            }
    except Exception as exc:
        with TASKS_LOCK:
            TASKS[task_id] = {
                "status": "error",
                "error": f"{type(exc).__name__}: {exc}",
            }


# ---------------------------------------------------------------- 数据模型


class RenderRequest(BaseModel):
    mode: str = "architecture"
    spec: dict[str, Any]


class SvgPayload(BaseModel):
    svg: str
    name: str = "diagram"


class PngRequest(SvgPayload):
    width: int = Field(default=1920, ge=256, le=8000)


class HtmlRequest(SvgPayload):
    title: str = "Technical Diagram"


class GifRequest(SvgPayload):
    preset: str = "auto"
    duration: float = Field(default=5.75, ge=1.0, le=12.0)
    fps: int = Field(default=20, ge=5, le=30)
    width: int = Field(default=960, ge=320, le=2400)


class GenerateRequest(BaseModel):
    prompt: str
    mode: str = "architecture"
    style: int = Field(default=1, ge=1, le=12)
    lang: str = "auto"


class ConfigRequest(BaseModel):
    enabled: bool = False
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    lang: str = "auto"


# ---------------------------------------------------------------- 应用

app = FastAPI(title="Fireworks Tech Graph Console", version="1.0.0")


@app.get("/api/meta")
def api_meta() -> dict[str, Any]:
    styles = []
    for index in sorted(STYLE_NAMES):
        en, zh = STYLE_NAMES[index]
        styles.append(
            {
                "id": index,
                "en": en,
                "zh": zh,
                "renderable": index not in AI_AUTHORED_STYLES,
                "auto": index in AUTO_STYLES,
                "motion": MOTION_PRESETS.get(index),
            }
        )
    return {
        "ok": True,
        "skill_root": str(SKILL_ROOT),
        "diagram_types": DIAGRAM_TYPES,
        "styles": styles,
        "checks": [{"id": n, "label": CHECK_LABELS[n]} for n in CHECK_NAMES],
    }


@app.get("/api/doctor")
def api_doctor() -> dict[str, Any]:
    cairo_ok, cairo_error = _cairosvg_usable()
    try:
        motion = probe_motion_runtime()
    except Exception as exc:
        motion = {"ok": False, "error": str(exc)}
    return {
        "ok": True,
        "python": sys.version.split()[0],
        "chrome": CHROME,
        "cairosvg": {"installed": importlib.util.find_spec("cairosvg") is not None, "usable": cairo_ok, "error": cairo_error},
        "raster_export": {"ok": cairo_ok or bool(CHROME)},
        "png_engine": "cairosvg" if cairo_ok else ("chrome-headless" if CHROME else None),
        "node": shutil.which("node"),
        "ffmpeg": shutil.which("ffmpeg"),
        "motion": motion,
    }


@app.get("/api/examples")
def api_examples() -> dict[str, Any]:
    fixtures_dir = SKILL_ROOT / "fixtures"
    items = []
    for path in sorted(fixtures_dir.glob("*")):
        if path.suffix not in {".json", ".svg"}:
            continue
        title = path.stem.replace("-", " ").title()
        style_match = re.search(r"style(\d+)", path.stem)
        items.append(
            {
                "id": path.stem,
                "path": str(path),
                "title": title,
                "kind": path.suffix.lstrip("."),
                "style": int(style_match.group(1)) if style_match else None,
            }
        )
    return {"ok": True, "examples": items}


@app.get("/api/example/{name}")
def api_example(name: str) -> dict[str, Any]:
    fixtures_dir = SKILL_ROOT / "fixtures"
    target = next(
        (p for p in fixtures_dir.glob("*") if p.stem == name and p.suffix in {".json", ".svg"}),
        None,
    )
    if target is None:
        raise HTTPException(status_code=404, detail=f"未找到示例：{name}")
    content = target.read_text(encoding="utf-8")
    payload: dict[str, Any] = {"ok": True, "kind": target.suffix.lstrip("."), "name": target.stem}
    if target.suffix == ".json":
        payload["spec"] = json.loads(content)
    else:
        payload["svg"] = content
    return payload


@app.post("/api/render")
def api_render(req: RenderRequest) -> dict[str, Any]:
    try:
        data = json.loads(json.dumps(req.spec))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"spec 不是合法 JSON：{exc}") from exc

    mode = str(data.get("mode") or data.get("template_type") or req.mode)
    # mode 与 template_type 必须一致，否则 normalize_diagram 直接判冲突。
    data["mode"] = mode
    data["template_type"] = mode

    try:
        svg, report = GENERATOR.build_svg_with_report(mode, data)
    except DiagramValidationError as exc:
        raise HTTPException(status_code=400, detail=f"[schema] {exc}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"[layout] {exc}") from exc

    path = _write(svg)
    checks = _run_checks(path)
    passed = all(item["ok"] for item in checks.values())
    return {
        "ok": True,
        "mode": mode,
        "svg": svg,
        "svg_url": _url(path),
        "checks": checks,
        "passed": passed,
        "report": report,
    }


@app.post("/api/check")
def api_check(req: SvgPayload) -> dict[str, Any]:
    path = _write(req.svg)
    checks = _run_checks(path)
    return {
        "ok": True,
        "checks": checks,
        "passed": all(item["ok"] for item in checks.values()),
    }


@app.post("/api/inspect")
def api_inspect(req: SvgPayload) -> dict[str, Any]:
    try:
        root = ET.fromstring(req.svg)
    except ET.ParseError as exc:
        raise HTTPException(status_code=400, detail=f"SVG 解析失败：{exc}") from exc
    roles: dict[str, int] = {}
    for element in root.iter():
        role = element.get("data-graph-role")
        if role:
            roles[role] = roles.get(role, 0) + 1
    return {
        "ok": True,
        "metadata": {
            key.replace("-", "_"): root.get(key)
            for key in (
                "data-generator",
                "data-schema-version",
                "data-style-id",
                "data-visual-theme",
                "data-diagram-type",
                "data-motion-scene",
                "data-semantic-profile",
                "data-semantic-valid",
                "viewBox",
            )
        },
        "roles": roles,
    }


@app.post("/api/png")
def api_png(req: PngRequest) -> dict[str, Any]:
    width, height = _read_viewbox(req.svg)
    scale = max(1.0, req.width / width)

    if _cairosvg_usable()[0]:
        import cairosvg  # noqa: WPS433

        out = _new_file(".png")
        try:
            cairosvg.svg2png(bytestring=req.svg.encode("utf-8"), write_to=str(out), output_width=req.width)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"cairosvg 渲染失败：{exc}") from exc
        engine = "cairosvg"
    elif CHROME:
        src = _write(req.svg, ".svg")
        out = _new_file(".png")
        proc = _chrome_screenshot(src, out, width, height, scale)
        if not out.exists() or out.stat().st_size == 0:
            detail = (proc.stderr or proc.stdout or "").strip()[-800:]
            raise HTTPException(status_code=500, detail=f"Chrome 截图失败：{detail or '无输出'}")
        engine = "chrome-headless"
    else:
        raise HTTPException(status_code=500, detail="没有可用的 PNG 渲染器（cairosvg 不可用且未找到 Chrome）")

    return {
        "ok": True,
        "engine": engine,
        "url": _url(out),
        "filename": f"{req.name}.png",
        "width": req.width,
        "height": int(round(height * scale)),
    }


@app.post("/api/html")
def api_html(req: HtmlRequest) -> dict[str, Any]:
    try:
        html = build_interactive_html(req.svg, req.title, {"slug": req.name})
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"交互 HTML 生成失败：{exc}") from exc
    out = _new_file(".html")
    out.write_text(html, encoding="utf-8")
    return {"ok": True, "url": _url(out), "filename": f"{req.name}.html"}


@app.post("/api/gif")
def api_gif(req: GifRequest) -> dict[str, Any]:
    if not os.environ.get("FIREWORKS_PUPPETEER_PATH") or not CHROME:
        raise HTTPException(status_code=500, detail="GIF 链路未就绪：缺少 puppeteer-core 或 Chrome")
    task_id = uuid.uuid4().hex
    with TASKS_LOCK:
        TASKS[task_id] = {"status": "running", "started": time.time()}
    threading.Thread(
        target=_gif_worker,
        args=(task_id, req.model_dump()),
        daemon=True,
    ).start()
    return {"ok": True, "task_id": task_id}


@app.get("/api/gif/{task_id}")
def api_gif_status(task_id: str) -> dict[str, Any]:
    with TASKS_LOCK:
        task = TASKS.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    elapsed = time.time() - task.get("started", time.time())
    payload = dict(task)
    payload["elapsed"] = round(elapsed, 1)
    return payload


@app.post("/api/cleanup")
def api_cleanup() -> dict[str, Any]:
    return {"ok": True, "removed": _sweep_runtime()}


# ---------------------------------------------------------------- 提示词生成


def _blueprint_from_llm(prompt: str, mode: str, style: int, lang: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """调用大模型产出语义蓝图。"""
    config = load_config()
    raw = llm.chat_completions(
        base_url=config["base_url"],
        api_key=config["api_key"],
        model=config["model"],
        messages=llm.build_messages(prompt, mode, style, lang),
        temperature=float(config.get("temperature") or 0.2),
    )
    blueprint = llm.extract_json(raw)
    return blueprint, {"source": "llm", "model": config["model"]}


def _repair_with_llm(
    prompt: str, mode: str, style: int, lang: str,
    blueprint: dict[str, Any], errors: list[str],
) -> dict[str, Any] | None:
    """把校验失败原因回传给模型，要求它在语义层面修正后重试。"""
    config = load_config()
    hint = (
        "The previous blueprint produced a diagram that failed validation.\n"
        "Problems:\n" + "\n".join(f"- {e}" for e in errors[:8]) + "\n\n"
        "Common fixes: reduce the number of nodes, spread them across more "
        "lanes, shorten labels, remove edges that would cross other edges.\n"
        "Return the corrected JSON blueprint only."
    )
    messages = llm.build_messages(prompt, mode, style, lang)
    messages.append({"role": "assistant", "content": json.dumps(blueprint, ensure_ascii=False)})
    messages.append({"role": "user", "content": hint})
    try:
        raw = llm.chat_completions(
            base_url=config["base_url"],
            api_key=config["api_key"],
            model=config["model"],
            messages=messages,
            temperature=float(config.get("temperature") or 0.2),
        )
        return llm.extract_json(raw)
    except Exception:
        return None


def _attempt(blueprint: dict[str, Any], mode: str, style: int, gap_scale: float) -> dict[str, Any]:
    """用给定间距系数铺一次，返回渲染结果与问题清单。"""
    spec = layout_engine.build_spec(blueprint, mode, style, gap_scale=gap_scale)
    data = dict(spec)
    data["mode"] = mode
    data["template_type"] = mode
    try:
        svg, report = GENERATOR.build_svg_with_report(mode, data)
    except (DiagramValidationError, ValueError) as exc:
        return {"ok": False, "spec": spec, "error": str(exc), "issues": [str(exc)]}

    path = _write(svg)
    checks = _run_checks(path)
    issues: list[str] = []
    for key, value in checks.items():
        for detail in value.get("details", []):
            issues.append(f"[{value.get('label', key)}] {detail}")
    report_issues = (report or {}).get("issues") or []
    issues.extend(str(i) for i in report_issues)
    return {
        "ok": all(v["ok"] for v in checks.values()),
        "spec": spec,
        "svg": svg,
        "checks": checks,
        "report": report,
        "issues": issues,
    }


@app.post("/api/generate")
def api_generate(req: GenerateRequest) -> dict[str, Any]:
    """由自然语言提示词生成图规格并直接渲染。

    流程：语义蓝图（大模型或本地启发式）→ 本地布局 → 渲染 → 门禁校验；
    未通过则先放宽间距重排，仍失败且有大模型时把错误回传要求修正。
    """
    prompt = (req.prompt or "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="请输入描述内容")

    style = req.style
    if style in AI_AUTHORED_STYLES:
        raise HTTPException(
            status_code=400,
            detail=f"Style {style}（{AI_AUTHORED_STYLES[style]}）为 AI 手绘风格，请改用其他风格",
        )
    if style not in AUTO_STYLES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Style {style} 是工程评审专用风格，需要 C4 层级、部署归属、事件轨道或"
                "可观测性指标等领域数据，提示词自动生成暂不支持。"
                "请从「示例」下拉选择该风格的官方样例，或手写 JSON 规格。"
            ),
        )

    config = load_config()
    use_llm = bool(config["enabled"] and config.get("api_key") and config.get("model"))
    attempts: list[str] = []

    if use_llm:
        try:
            blueprint, meta = _blueprint_from_llm(prompt, req.mode, style, req.lang)
        except llm.LLMError as exc:
            # 大模型不可用时降级到本地解析，保证功能不中断
            blueprint = heuristic.parse(prompt, req.mode)
            meta = {"source": "heuristic", "fallback_reason": str(exc)}
            attempts.append(f"大模型调用失败，已回退本地解析：{exc}")
    else:
        blueprint = heuristic.parse(prompt, req.mode)
        meta = {"source": "heuristic"}

    # 第一轮：标准间距
    result = _attempt(blueprint, req.mode, style, 1.0)

    # 第二轮：放宽间距重排（解决节点过密、标签碰撞）
    if not result["ok"]:
        attempts.append(f"标准间距未通过：{result['issues'][:3]}")
        relaxed = _attempt(blueprint, req.mode, style, 1.6)
        if relaxed["ok"] or (not result.get("svg") and relaxed.get("svg")):
            result = relaxed

    # 第三轮：有大模型时把错误喂回去，让它在语义层面收敛
    if not result["ok"] and use_llm:
        attempts.append(f"放宽间距仍未通过：{result['issues'][:3]}")
        fixed = _repair_with_llm(prompt, req.mode, style, req.lang, blueprint, result["issues"])
        if fixed:
            for scale in (1.0, 1.6):
                retried = _attempt(fixed, req.mode, style, scale)
                if retried.get("svg"):
                    result = retried
                    blueprint = fixed
                    if retried["ok"]:
                        break

    payload: dict[str, Any] = {
        "ok": bool(result.get("svg")),
        "source": meta.get("source"),
        "model": meta.get("model"),
        "fallback_reason": meta.get("fallback_reason"),
        "spec": result["spec"],
        "blueprint": blueprint,
        "attempts": attempts,
        "issues": result.get("issues", []),
    }
    if result.get("svg"):
        payload.update({
            "svg": result["svg"],
            "checks": result["checks"],
            "passed": all(v["ok"] for v in result["checks"].values()),
            "report": result.get("report"),
        })
    else:
        payload["error"] = result.get("error") or (result.get("issues") or ["生成失败"])[0]
    return payload


@app.get("/api/config")
def api_get_config() -> dict[str, Any]:
    config = load_config()
    return {
        "ok": True,
        "config": {**config, "api_key": "***" if config["api_key"] else ""},
        "has_key": bool(config["api_key"]),
        "config_path": str(CONFIG_PATH),
    }


@app.post("/api/config")
def api_set_config(req: ConfigRequest) -> dict[str, Any]:
    current = load_config()
    payload = req.model_dump()
    # 前端用 *** 占位表示「保持原值不变」
    if payload.get("api_key") == "***":
        payload["api_key"] = current["api_key"]
    save_config(payload)
    return {"ok": True, "has_key": bool(payload["api_key"])}


@app.post("/api/config/test")
def api_test_config() -> dict[str, Any]:
    config = load_config()
    if not config.get("base_url") or not config.get("model"):
        raise HTTPException(status_code=400, detail="请先填写 API 地址与模型名称")
    try:
        text = llm.chat_completions(
            base_url=config["base_url"],
            api_key=config["api_key"],
            model=config["model"],
            messages=[{"role": "user", "content": "Reply with the single word: ok"}],
            timeout=45,
        )
        return {"ok": True, "reply": text.strip()[:200]}
    except llm.LLMError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/config/models")
def api_list_models() -> dict[str, Any]:
    config = load_config()
    try:
        models = llm.list_models(base_url=config["base_url"], api_key=config["api_key"])
        return {"ok": True, "models": models}
    except llm.LLMError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


# 静态资源：/files 供下载，/ 提供界面（必须最后挂载）
app.mount("/files", StaticFiles(directory=str(RUNTIME_DIR)), name="files")
app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")


if __name__ == "__main__":
    import argparse
    import webbrowser

    import uvicorn

    parser = argparse.ArgumentParser(description="Fireworks Tech Graph Web 控制台")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=int(os.environ.get("FTG_PORT", "8777")))
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    _sweep_runtime(max_age_seconds=86400)
    url = f"http://{args.host}:{args.port}/"
    # 控制台用纯 ASCII：cmd 默认按 GBK 解码，中文会乱码。
    print(f"[fireworks-tech-graph] Skill : {SKILL_ROOT}")
    print(f"[fireworks-tech-graph] Chrome: {CHROME or 'not found'}")
    print(f"[fireworks-tech-graph] Console: {url}")
    if not args.no_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
