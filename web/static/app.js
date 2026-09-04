/* Fireworks Tech Graph 控制台 —— 前端交互 */
"use strict";

const $ = (id) => document.getElementById(id);

const state = {
  meta: null,
  types: [],
  styles: [],
  mode: "architecture",
  style: 1,
  svg: "",
  report: null,
  checks: null,
  zoom: 1,
  gifTimer: null,
};

/* ---------------------------------------------------------------- 工具 */

function toast(message, kind = "ok", duration = 3200) {
  const el = $("toast");
  el.textContent = message;
  el.className = `toast ${kind}`;
  el.hidden = false;
  requestAnimationFrame(() => el.classList.add("show"));
  clearTimeout(el._t);
  el._t = setTimeout(() => {
    el.classList.remove("show");
    setTimeout(() => (el.hidden = true), 220);
  }, duration);
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  let data = null;
  try {
    data = await res.json();
  } catch {
    data = null;
  }
  if (!res.ok) {
    const detail = data && data.detail ? data.detail : `HTTP ${res.status}`;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return data;
}

const post = (path, body) => api(path, { method: "POST", body: JSON.stringify(body) });

function download(url, filename) {
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
}

function escapeHtml(text) {
  return String(text)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

/** 预览前的基础消毒：移除脚本与事件属性，保留图形本体。 */
function sanitizeForPreview(svg) {
  return svg
    .replace(/<\?xml[^>]*\?>/gi, "")
    .replace(/<!DOCTYPE[^>]*>/gi, "")
    .replace(/<script[\s\S]*?<\/script>/gi, "")
    .replace(/<foreignObject[\s\S]*?<\/foreignObject>/gi, "")
    .replace(/\son\w+\s*=\s*"[^"]*"/gi, "")
    .replace(/\son\w+\s*=\s*'[^']*'/gi, "")
    .replace(/javascript:/gi, "");
}

/** 文件名安全化，避免非法字符导致下载失败。 */
function safeName(name) {
  return String(name || "diagram").replace(/[\\/:*?"<>|]/g, "-").slice(0, 80);
}

/* ---------------------------------------------------------------- 初始化 */

async function init() {
  await loadMeta();
  await loadExamples();
  bindEvents();
  refreshDoctor();
  loadConfig();
  const first = state.types[0];
  if (first) selectType(first.id);
  const style = state.styles.find((s) => s.id === 1) || state.styles[0];
  if (style) selectStyle(style.id);
}

async function loadMeta() {
  const meta = await api("/api/meta");
  state.meta = meta;
  state.types = meta.diagram_types;
  state.styles = meta.styles;

  $("typeChips").innerHTML = state.types
    .map(
      (t) =>
        `<button class="chip" data-type="${t.id}" title="viewBox ${t.viewBox}">${t.id}</button>`
    )
    .join("");

  $("styleChips").innerHTML = state.styles
    .map(
      (s) =>
        `<button class="chip" data-style="${s.id}" ${s.renderable ? "" : "disabled"} title="${
          s.renderable ? `${s.en} · ${s.motion}` : `${s.en} · AI 手绘，不支持 JSON 渲染`
        }"><b>${s.id} ${s.zh}</b><span>${s.en}</span></button>`
    )
    .join("");
}

async function loadExamples() {
  const data = await api("/api/examples");
  const select = $("exampleSelect");
  select.innerHTML =
    '<option value="">— 选择内置示例 —</option>' +
    data.examples
      .map((e) => `<option value="${e.id}">${escapeHtml(e.title)}${e.style ? ` · S${e.style}` : ""}</option>`)
      .join("");
  state.examples = data.examples;
}

/* ---------------------------------------------------------------- 选择交互 */

function selectType(id) {
  state.mode = id;
  document.querySelectorAll("[data-type]").forEach((el) => {
    el.classList.toggle("active", el.dataset.type === id);
  });
  const type = state.types.find((t) => t.id === id);
  if (type) $("typeHint").textContent = `${type.width} × ${type.height}`;
}

function selectStyle(id) {
  state.style = id;
  document.querySelectorAll("[data-style]").forEach((el) => {
    el.classList.toggle("active", Number(el.dataset.style) === id);
  });
  $("styleHint").textContent = `Style ${id}`;
  const style = state.styles.find((s) => s.id === id);
  const warn = $("styleWarn");
  if (style && !style.renderable) {
    warn.hidden = false;
    warn.textContent = `Style ${id}（${style.zh}）为 AI 手绘风格，不接受 JSON 渲染，请改用其他风格。`;
  } else if (style && style.auto === false) {
    warn.hidden = false;
    warn.textContent =
      `Style ${id}（${style.zh}）是工程评审专用风格，需要领域数据，提示词生成暂不支持。` +
      `可从下方「示例」选择该风格样例，或手写 JSON。`;
  } else {
    warn.hidden = true;
  }

  // 提示词生成入口随风格可用性开关
  const btn = $("btnGenerate");
  if (style && style.auto === false) {
    btn.disabled = true;
    btn.title = "该风格不支持提示词生成";
  } else {
    btn.disabled = false;
    btn.title = "";
  }
}

/** 当前编辑器里的 spec 与所选图类型/风格同步。 */
function syncSpecFields(spec) {
  const next = { ...spec };
  next.mode = state.mode;
  next.template_type = state.mode;
  if (Number(next.style) !== state.style) next.style = state.style;
  return next;
}

/* ---------------------------------------------------------------- 渲染 */

async function renderDiagram() {
  const raw = $("editor").value.trim();
  if (!raw) {
    showJsonError("请先加载示例或粘贴 JSON 规格。");
    return;
  }
  let spec;
  try {
    spec = JSON.parse(raw);
  } catch (err) {
    showJsonError(`JSON 语法错误：${err.message}`);
    return;
  }
  showJsonError(null);

  const btn = $("btnRender");
  const label = btn.querySelector(".btn-label");
  const original = label.textContent;
  btn.disabled = true;
  label.textContent = "渲染中…";

  try {
    const result = await post("/api/render", { mode: state.mode, spec });
    applySvg(result.svg, { report: result.report, checks: result.checks, mode: result.mode });

    // 回写规范化后的 spec，让用户看到生成器实际采用的值；
    // 外部格式转换 / 越界节点自动回收时，直接把修正后的 spec 写回编辑器，方便继续微调。
    const effective = result.spec || spec;
    $("editor").value = JSON.stringify(syncSpecFields(effective), null, 2);
    const title = effective.title || effective.mode || "技术图";
    $("previewTitle").textContent = title;
    if (result.passed) {
      if (result.autofit && result.autofit.length) {
        toast(`已自动收回 ${result.autofit.length} 个越界节点，全部门禁通过`, "ok", 4200);
      } else {
        toast(result.converted ? "外部格式已转换并渲染，全部门禁通过" : "渲染完成，全部质量门禁通过", "ok");
      }
    } else {
      toast("渲染完成，但存在门禁未通过项", "err", 4200);
    }
  } catch (err) {
    toast(err.message, "err", 6000);
  } finally {
    btn.disabled = false;
    label.textContent = original;
  }
}

function applySvg(svg, extra = {}) {
  state.svg = svg;
  state.report = extra.report || null;
  state.checks = extra.checks || null;

  const canvas = $("canvas");
  canvas.innerHTML = sanitizeForPreview(svg);
  canvas.style.transform = `scale(${state.zoom})`;

  $("emptyState").hidden = true;
  $("canvasWrap").hidden = false;

  const vb = svg.match(/viewBox="0 0\s*([\d.]+)[ ,]+([\d.]+)"/);
  if (vb) {
    $("previewMeta").textContent = `${Math.round(vb[1])} × ${Math.round(vb[2])} · ${(svg.length / 1024).toFixed(1)} KB`;
  }

  setExportsEnabled(true);
  renderGates(state.checks);
  renderReport(state.report, extra.checks);
}

function setExportsEnabled(enabled) {
  ["btnSvg", "btnHtml", "btnPng", "btnGif"].forEach((id) => {
    $(id).disabled = !enabled;
  });
}

/* ---------------------------------------------------------------- 门禁与报告 */

function renderGates(checks) {
  const box = $("gates");
  if (!checks) {
    box.innerHTML = '<div class="gate idle"><span class="gate-dot"></span><span class="gate-name">等待渲染</span></div>';
    $("gateSummary").textContent = "—";
    $("gateDetails").hidden = true;
    return;
  }

  const entries = Object.entries(checks);
  const failed = entries.filter(([, v]) => !v.ok);

  box.innerHTML = entries
    .map(
      ([key, value]) =>
        `<div class="gate ${value.ok ? "pass" : "fail"}">
           <span class="gate-dot"></span>
           <span class="gate-name">${value.label || key}</span>
           <span class="gate-badge">${value.ok ? "通过" : `${value.details.length} 项`}</span>
         </div>`
    )
    .join("");

  $("gateSummary").textContent = failed.length
    ? `${failed.length} 项未通过`
    : `${entries.length}/${entries.length} 通过`;

  const details = $("gateDetails");
  const lines = failed.flatMap(([key, value]) =>
    value.details.map((d) => `[${value.label || key}] ${d}`)
  );
  if (lines.length) {
    details.hidden = false;
    details.textContent = lines.join("\n");
  } else {
    details.hidden = true;
  }
}

function renderReport(report, checks) {
  const box = $("report");
  if (!report) {
    box.innerHTML = '<p class="muted">渲染后可查看布局与语义报告。</p>';
    return;
  }

  const kv = (k, v) =>
    `<div class="kv"><b>${escapeHtml(k)}</b><span>${escapeHtml(String(v))}</span></div>`;
  const pill = (ok, text) =>
    `<span class="pill ${ok ? "ok" : "bad"}">${escapeHtml(text)}</span>`;

  let html = "";

  // 风格与语义契约
  const style = report.style || {};
  const sem = report.semantics || {};
  html += `<div class="sub">样式与语义</div>`;
  if (style.name) html += kv("视觉风格", `S${style.id ?? "?"} · ${style.name}`);
  if (report.mode) html += kv("图类型", report.mode);
  if (sem.visual_theme) html += kv("主题", sem.visual_theme);
  if (sem.profile) html += kv("语义契约", sem.profile);
  if (sem.ok !== undefined) {
    html += `<div class="kv"><b>契约校验</b><span>${pill(sem.ok, sem.ok ? "通过" : "未通过")}</span></div>`;
  }

  // 规模
  const sum = report.summary || {};
  html += `<div class="sub">规模</div>`;
  if (sum.nodes !== undefined) html += kv("节点", sum.nodes);
  if (sum.edges !== undefined) html += kv("连线", sum.edges);
  if (Array.isArray(report.edges)) html += kv("路由边", report.edges.length);
  if (sum.bridged_crossings !== undefined) html += kv("桥接交叉", sum.bridged_crossings);

  // 画布
  const canvas = report.canvas || {};
  if (canvas.width && canvas.height) {
    html += kv("画布", `${Math.round(canvas.width)} × ${Math.round(canvas.height)}`);
  }

  // 构图预算：limits 是上限，metrics 是实测，放在一起对照最有用
  const comp = report.composition || {};
  if (comp && typeof comp === "object" && Object.keys(comp).length) {
    html += `<div class="sub">构图评分</div>`;
    if (comp.score !== undefined) {
      html += `<div class="kv"><b>得分</b><span>${pill(comp.ok, `${comp.score}${comp.ok ? " 达标" : " 未达标"}`)}</span></div>`;
    }
    const metrics = comp.metrics || {};
    const limits = comp.limits || {};
    const fmtNum = (v) => (typeof v === "number" ? (Number.isInteger(v) ? v : v.toFixed(2)) : v);
    Object.entries(metrics).forEach(([k, v]) => {
      const limit = limits[`max_${k}`] !== undefined ? limits[`max_${k}`] : limits[`min_${k}`];
      const suffix = limit !== undefined ? ` / 限 ${fmtNum(limit)}` : "";
      html += kv(k, `${fmtNum(v)}${suffix}`);
    });
  }

  // 问题清单
  const issues = Array.isArray(report.issues) ? report.issues : [];
  const violations = Array.isArray(comp.violations) ? comp.violations : [];
  const problems = [...issues, ...violations];
  if (problems.length) {
    html += `<div class="sub">问题 (${problems.length})</div>`;
    problems.slice(0, 12).forEach((w) => {
      html += `<div class="kv"><b>—</b><span>${escapeHtml(typeof w === "string" ? w : JSON.stringify(w))}</span></div>`;
    });
  }

  box.innerHTML = html || '<p class="muted">无额外报告字段。</p>';
}

/* ---------------------------------------------------------------- 提示词生成 */

function setGenStatus(message, kind) {
  const el = $("genStatus");
  if (!message) {
    el.hidden = true;
    return;
  }
  el.hidden = false;
  el.className = `gen-status ${kind || ""}`;
  el.textContent = message;
}

function updateAiSource() {
  const el = $("aiSource");
  const cfg = state.config || {};
  if (cfg.enabled && cfg.has_key) {
    el.className = "ai-source llm";
    el.textContent = `大模型 · ${cfg.model || "未指定模型"}`;
  } else {
    el.className = "ai-source";
    el.textContent = "本地解析（无需 API Key）";
  }
}

async function loadConfig() {
  try {
    const data = await api("/api/config");
    state.config = data;
    updateAiSource();
    $("cfgPath").textContent = data.config_path || "";
  } catch {
    state.config = { enabled: false };
    updateAiSource();
  }
}

async function generateFromPrompt() {
  const prompt = $("prompt").value.trim();
  if (!prompt) {
    setGenStatus("请先输入描述内容。", "err");
    $("prompt").focus();
    return;
  }
  const btn = $("btnGenerate");
  const label = btn.querySelector(".btn-label");
  const original = label.textContent;
  btn.disabled = true;
  label.textContent = "生成中…";
  setGenStatus("正在解析描述并计算布局…", "");

  try {
    const result = await post("/api/generate", {
      prompt,
      mode: state.mode,
      style: state.style,
      lang: "auto",
    });

    if (result.svg) {
      $("editor").value = JSON.stringify(result.spec, null, 2);
      applySvg(result.svg, {
        report: result.report,
        checks: result.checks,
        mode: state.mode,
      });
      $("previewTitle").textContent = result.spec.title || "技术图";
      const nodeCount = (result.spec.nodes || []).length;
      const edgeCount = (result.spec.arrows || []).length;

      if (result.passed) {
        setGenStatus(`已生成 ${nodeCount} 个节点 / ${edgeCount} 条连线，全部门禁通过。`, "ok");
        toast("生成完成", "ok");
      } else {
        setGenStatus(`已生成 ${nodeCount} 个节点 / ${edgeCount} 条连线，有门禁未通过，可在右侧查看明细后微调 JSON。`, "err");
      }
      if (result.fallback_reason) {
        setGenStatus(`大模型不可用，已回退本地解析：${result.fallback_reason}`, "err");
      }
    } else {
      setGenStatus(result.error || "生成失败", "err");
      toast(result.error || "生成失败", "err", 6000);
    }
  } catch (err) {
    setGenStatus(err.message, "err");
    toast(err.message, "err", 7000);
  } finally {
    btn.disabled = false;
    label.textContent = original;
  }
}

/* ---------------------------------------------------------------- 模型设置 */

async function openConfigModal() {
  $("configModal").hidden = false;
  $("configMsg").hidden = true;
  try {
    const data = await api("/api/config");
    state.config = data;
    $("cfgEnabled").checked = !!data.config.enabled;
    $("cfgBaseUrl").value = data.config.base_url || "";
    $("cfgApiKey").value = "";
    $("cfgApiKey").placeholder = data.has_key ? "已保存，留空则不修改" : "sk-...";
    $("cfgModel").value = data.config.model || "";
    $("cfgTemp").value = data.config.temperature ?? 0.2;
    $("tempLabel").textContent = String(data.config.temperature ?? 0.2);
    $("cfgPath").textContent = data.config_path || "";
  } catch (err) {
    showConfigMsg(`读取配置失败：${err.message}`, "err");
  }
}

function showConfigMsg(message, kind) {
  const el = $("configMsg");
  el.hidden = false;
  el.className = `form-msg ${kind || ""}`;
  el.textContent = message;
}

async function saveConfig() {
  const payload = {
    enabled: $("cfgEnabled").checked,
    base_url: $("cfgBaseUrl").value.trim(),
    api_key: $("cfgApiKey").value.trim() || "***",
    model: $("cfgModel").value.trim(),
    temperature: Number($("cfgTemp").value),
    lang: "auto",
  };
  if (!payload.api_key || payload.api_key === "***") {
    if (!(state.config && state.config.has_key)) {
      showConfigMsg("请填写 API Key。", "err");
      return;
    }
  }
  try {
    await post("/api/config", payload);
    showConfigMsg("已保存到本机。", "ok");
    await loadConfig();
    updateAiSource();
  } catch (err) {
    showConfigMsg(`保存失败：${err.message}`, "err");
  }
}

async function testConfig() {
  const btn = $("btnTestConfig");
  btn.disabled = true;
  const original = btn.textContent;
  btn.textContent = "测试中…";
  showConfigMsg("正在连接…", "");
  try {
    // 先落盘再测，确保测的是用户刚填的值
    await post("/api/config", {
      enabled: $("cfgEnabled").checked,
      base_url: $("cfgBaseUrl").value.trim(),
      api_key: $("cfgApiKey").value.trim() || "***",
      model: $("cfgModel").value.trim(),
      temperature: Number($("cfgTemp").value),
      lang: "auto",
    });
    const result = await api("/api/config/test");
    showConfigMsg(`连接成功：${result.reply || "ok"}`, "ok");
    await loadConfig();
    updateAiSource();
  } catch (err) {
    showConfigMsg(`连接失败：${err.message}`, "err");
  } finally {
    btn.disabled = false;
    btn.textContent = original;
  }
}

async function fetchModels() {
  const btn = $("btnFetchModels");
  btn.disabled = true;
  const original = btn.textContent;
  btn.textContent = "…";
  try {
    const result = await api("/api/config/models");
    const list = result.models || [];
    $("modelList").innerHTML = list
      .slice(0, 200)
      .map((m) => `<option value="${escapeHtml(m)}"></option>`)
      .join("");
    showConfigMsg(`已拉取 ${list.length} 个模型，可在输入框选择。`, "ok");
  } catch (err) {
    showConfigMsg(`拉取失败：${err.message}`, "err");
  } finally {
    btn.disabled = false;
    btn.textContent = original;
  }
}

/* ---------------------------------------------------------------- 导出 */

async function exportSvg() {
  if (!state.svg) return;
  const blob = new Blob([state.svg], { type: "image/svg+xml;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  download(url, `${safeName($("previewTitle").textContent)}.svg`);
  setTimeout(() => URL.revokeObjectURL(url), 4000);
  toast("SVG 已下载", "ok");
}

async function exportPng() {
  const width = Number($("pngWidth").value);
  const btn = $("btnPng");
  btn.disabled = true;
  const original = btn.textContent;
  btn.textContent = "渲染中…";
  try {
    const result = await post("/api/png", {
      svg: state.svg,
      width,
      name: safeName($("previewTitle").textContent),
    });
    download(result.url, result.filename);
    toast(`PNG 已导出 · ${result.width}×${result.height}`, "ok");
  } catch (err) {
    toast(err.message, "err", 6000);
  } finally {
    btn.disabled = false;
    btn.textContent = original;
  }
}

async function exportHtml() {
  const btn = $("btnHtml");
  btn.disabled = true;
  try {
    const result = await post("/api/html", {
      svg: state.svg,
      title: $("previewTitle").textContent,
      name: safeName($("previewTitle").textContent),
    });
    download(result.url, result.filename);
    toast("交互 HTML 已下载", "ok");
  } catch (err) {
    toast(err.message, "err", 6000);
  } finally {
    btn.disabled = false;
  }
}

async function exportGif() {
  const btn = $("btnGif");
  const progress = $("gifProgress");
  btn.disabled = true;
  progress.hidden = false;

  try {
    const started = await post("/api/gif", {
      svg: state.svg,
      name: safeName($("previewTitle").textContent),
    });
    pollGif(started.task_id);
  } catch (err) {
    progress.hidden = true;
    btn.disabled = false;
    toast(err.message, "err", 7000);
  }
}

function pollGif(taskId) {
  const progress = $("gifProgress");
  const btn = $("btnGif");
  const text = progress.querySelector(".progress-text");

  clearInterval(state.gifTimer);
  state.gifTimer = setInterval(async () => {
    try {
      const task = await api(`/api/gif/${taskId}`);
      if (task.status === "running") {
        text.textContent = `渲染帧… ${Math.round(task.elapsed || 0)}s`;
        return;
      }
      clearInterval(state.gifTimer);
      progress.hidden = true;
      btn.disabled = false;

      if (task.status === "done") {
        const result = task.result || {};
        const size = result.size_bytes ? `${(result.size_bytes / 1024).toFixed(0)} KB` : "";
        if (task.gif_url) download(task.gif_url, task.gif_name || "diagram.gif");
        toast(`GIF 已完成 ${size ? `· ${size}` : ""}`, "ok", 4200);
      } else {
        toast(task.error || "GIF 生成失败", "err", 8000);
      }
    } catch (err) {
      clearInterval(state.gifTimer);
      progress.hidden = true;
      btn.disabled = false;
      toast(err.message, "err", 7000);
    }
  }, 1200);
}

/* ---------------------------------------------------------------- 环境与文件 */

async function refreshDoctor() {
  const strip = $("doctorStrip");
  try {
    const doc = await api("/api/doctor");
    const motionOk = doc.motion && doc.motion.ok;
    const pngOk = doc.raster_export && doc.raster_export.ok;
    const level = pngOk && motionOk ? "ok" : pngOk ? "warn" : "err";
    const parts = [];
    parts.push(`PNG ${doc.png_engine || "无"}`);
    parts.push(motionOk ? "GIF 就绪" : "GIF 不可用");
    strip.innerHTML = `<span class="dot ${level}"></span><span class="doctor-text">${parts.join(" · ")}</span>`;
    state.doctor = doc;
  } catch (err) {
    strip.innerHTML = `<span class="dot err"></span><span class="doctor-text">后端未响应</span>`;
  }
}

async function showDoctorModal() {
  const modal = $("modal");
  modal.hidden = false;
  $("doctorBody").textContent = "加载中…";
  try {
    const doc = await api("/api/doctor");
    $("doctorBody").textContent = JSON.stringify(doc, null, 2);
  } catch (err) {
    $("doctorBody").textContent = `获取失败：${err.message}`;
  }
}

async function loadExample(id) {
  if (!id) return;
  try {
    const data = await api(`/api/example/${encodeURIComponent(id)}`);
    if (data.kind === "json") {
      const spec = data.spec;
      if (spec.style) selectStyle(Number(spec.style));
      const mode = spec.mode || spec.template_type;
      if (mode && state.types.some((t) => t.id === mode)) selectType(mode);
      $("editor").value = JSON.stringify(syncSpecFields(spec), null, 2);
      showJsonError(null);
      toast("示例已载入，点击「渲染 SVG」", "ok");
    } else {
      // 静态 SVG 示例：直接进入预览与导出
      applySvg(data.svg, {});
      $("previewTitle").textContent = data.name;
      const checks = await post("/api/check", { svg: data.svg });
      renderGates(checks.checks);
      toast("静态 SVG 示例已载入", "ok");
    }
  } catch (err) {
    toast(err.message, "err", 5000);
  }
}

function showJsonError(message) {
  const el = $("jsonError");
  if (!message) {
    el.hidden = true;
    return;
  }
  el.hidden = false;
  el.textContent = message;
}

function formatEditor() {
  const raw = $("editor").value.trim();
  if (!raw) return;
  try {
    $("editor").value = JSON.stringify(JSON.parse(raw), null, 2);
    showJsonError(null);
  } catch (err) {
    showJsonError(`JSON 语法错误：${err.message}`);
  }
}

/**
 * 读取文本文件内容，自动处理 UTF-8 / UTF-16 / GBK。
 *
 * File.text() 永远按 UTF-8 解码，遇到 GBK 导出的文件（国内工具很常见）不会报错，
 * 而是返回一串替换字符；用 fatal 模式显式解码就能捕捉到并回退到 gb18030。
 */
async function readTextFile(file) {
  const bytes = new Uint8Array(await file.arrayBuffer());
  if (bytes[0] === 0xff && bytes[1] === 0xfe) return new TextDecoder("utf-16le").decode(bytes);
  if (bytes[0] === 0xfe && bytes[1] === 0xff) return new TextDecoder("utf-16be").decode(bytes);
  if (bytes[0] === 0xef && bytes[1] === 0xbb && bytes[2] === 0xbf) {
    return new TextDecoder("utf-8").decode(bytes.subarray(3));
  }
  try {
    return new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  } catch {
    try {
      return new TextDecoder("gb18030").decode(bytes);
    } catch {
      throw new Error("文件既不是 UTF-8 也不是 GBK 编码，请先转成 UTF-8");
    }
  }
}

async function handleSvgFile(file) {
  try {
    const text = await readTextFile(file);
    if (!text.includes("<svg")) throw new Error("不是有效的 SVG 文件");
    applySvg(text, {});
    $("previewTitle").textContent = file.name.replace(/\.svg$/i, "");
    const checks = await post("/api/check", { svg: text });
    renderGates(checks.checks);
    toast("SVG 已导入，可执行校验与导出", "ok");
  } catch (err) {
    toast(err.message, "err", 5000);
  }
}

async function handleSpecFile(file) {
  try {
    const spec = JSON.parse(await readTextFile(file));
    if (spec.style) selectStyle(Number(spec.style));
    const mode = spec.mode || spec.template_type;
    if (mode && state.types.some((t) => t.id === mode)) selectType(mode);
    $("editor").value = JSON.stringify(syncSpecFields(spec), null, 2);
    showJsonError(null);
    toast("JSON 已导入，点击「渲染 SVG」", "ok");
  } catch (err) {
    toast(`导入失败：${err.message}`, "err", 5000);
  }
}

/* ---------------------------------------------------------------- 事件绑定 */

function setZoom(value, fromRange = false) {
  state.zoom = Math.min(2, Math.max(0.2, value));
  $("canvas").style.transform = `scale(${state.zoom})`;
  $("zoomLabel").textContent = `${Math.round(state.zoom * 100)}%`;
  if (!fromRange) $("zoomRange").value = Math.round(state.zoom * 100);
}

function fitZoom() {
  const stage = $("stage");
  const svg = $("canvas").querySelector("svg");
  if (!svg || !svg.viewBox || !svg.viewBox.baseVal || !svg.viewBox.baseVal.width) return;
  const vb = svg.viewBox.baseVal;
  const pad = 44;
  const scale = Math.min(
    (stage.clientWidth - pad) / vb.width,
    (stage.clientHeight - pad) / vb.height
  );
  setZoom(Math.max(0.2, scale));
}

function setStageBackground(kind) {
  const stage = $("stage");
  stage.classList.remove("bg-light", "bg-dark", "bg-check");
  stage.classList.add(`bg-${kind}`);
  ["bgLight", "bgDark", "bgCheck"].forEach((id) => $(id).classList.remove("active"));
  $({ light: "bgLight", dark: "bgDark", check: "bgCheck" }[kind]).classList.add("active");
}

function bindEvents() {
  $("typeChips").addEventListener("click", (e) => {
    const chip = e.target.closest("[data-type]");
    if (chip) selectType(chip.dataset.type);
  });

  $("styleChips").addEventListener("click", (e) => {
    const chip = e.target.closest("[data-style]");
    if (chip && !chip.disabled) selectStyle(Number(chip.dataset.style));
  });

  $("exampleSelect").addEventListener("change", (e) => loadExample(e.target.value));
  $("btnRender").addEventListener("click", renderDiagram);
  $("btnFormat").addEventListener("click", formatEditor);
  $("btnLoadSpec").addEventListener("click", () => $("fileSpec").click());
  $("fileSpec").addEventListener("change", (e) => {
    const file = e.target.files[0];
    if (file) handleSpecFile(file);
    e.target.value = "";
  });

  $("btnUpload").addEventListener("click", () => $("fileSvg").click());
  $("fileSvg").addEventListener("change", (e) => {
    const file = e.target.files[0];
    if (file) handleSvgFile(file);
    e.target.value = "";
  });

  $("btnSvg").addEventListener("click", exportSvg);
  $("btnPng").addEventListener("click", exportPng);
  $("btnHtml").addEventListener("click", exportHtml);
  $("btnGif").addEventListener("click", exportGif);

  $("btnGenerate").addEventListener("click", generateFromPrompt);
  $("btnAiSettings").addEventListener("click", openConfigModal);

  $("configModalClose").addEventListener("click", () => ($("configModal").hidden = true));
  $("configModal").addEventListener("click", (e) => {
    if (e.target === $("configModal")) $("configModal").hidden = true;
  });
  $("btnSaveConfig").addEventListener("click", saveConfig);
  $("btnTestConfig").addEventListener("click", testConfig);
  $("btnFetchModels").addEventListener("click", fetchModels);
  $("cfgTemp").addEventListener("input", (e) => ($("tempLabel").textContent = e.target.value));

  $("btnDoctor").addEventListener("click", showDoctorModal);
  $("modalClose").addEventListener("click", () => ($("modal").hidden = true));
  $("modal").addEventListener("click", (e) => {
    if (e.target === $("modal")) $("modal").hidden = true;
  });

  $("btnRaw").addEventListener("click", () => {
    $("jsonBody").textContent = JSON.stringify(
      { checks: state.checks, report: state.report },
      null,
      2
    );
    $("jsonModal").hidden = false;
  });
  $("jsonModalClose").addEventListener("click", () => ($("jsonModal").hidden = true));
  $("jsonModal").addEventListener("click", (e) => {
    if (e.target === $("jsonModal")) $("jsonModal").hidden = true;
  });

  $("zoomRange").addEventListener("input", (e) => setZoom(Number(e.target.value) / 100, true));
  document.querySelectorAll("[data-zoom]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const mode = btn.dataset.zoom;
      if (mode === "out") setZoom(state.zoom - 0.15);
      else if (mode === "in") setZoom(state.zoom + 0.15);
      else fitZoom();
    });
  });

  $("bgLight").addEventListener("click", () => setStageBackground("light"));
  $("bgDark").addEventListener("click", () => setStageBackground("dark"));
  $("bgCheck").addEventListener("click", () => setStageBackground("check"));

  // 拖拽导入
  const stage = $("stage");
  let dragDepth = 0;
  window.addEventListener("dragenter", (e) => {
    e.preventDefault();
    dragDepth++;
    stage.classList.add("dragging");
  });
  window.addEventListener("dragover", (e) => e.preventDefault());
  window.addEventListener("dragleave", () => {
    dragDepth = Math.max(0, dragDepth - 1);
    if (!dragDepth) stage.classList.remove("dragging");
  });
  window.addEventListener("drop", (e) => {
    e.preventDefault();
    dragDepth = 0;
    stage.classList.remove("dragging");
    const file = e.dataTransfer && e.dataTransfer.files[0];
    if (!file) return;
    if (file.name.endsWith(".svg")) handleSvgFile(file);
    else if (file.name.endsWith(".json")) handleSpecFile(file);
    else toast("仅支持 .svg 与 .json 文件", "err");
  });

  document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
      e.preventDefault();
      renderDiagram();
    }
    if (e.key === "Escape") {
      $("modal").hidden = true;
      $("jsonModal").hidden = true;
      $("configModal").hidden = true;
    }
  });

  window.addEventListener("resize", () => {
    if (state.svg) fitZoom();
  });
}

init().catch((err) => {
  console.error(err);
  toast(`初始化失败：${err.message}`, "err", 8000);
});
