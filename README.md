# tech-graph · Fireworks Tech Graph 本地控制台

为 [fireworks-tech-graph](https://github.com/yizhiyanhua-ai/fireworks-tech-graph) Agent Skill 提供的一套**本地浏览器控制台**：把原生的命令行能力（渲染、几何校验、PNG / GIF / 交互 HTML 导出）包装成可视化界面，鼠标点几下就能出图。

> Skill 本体装在 `~/.codebuddy/skills/fireworks-tech-graph`，本项目不复制它的代码，而是在运行时调用它的生成器与校验器。

---

## 功能

| 能力 | 说明 |
|---|---|
| 提示词生成 | 用自然语言描述系统，自动生成图（详见下节） |
| 图类型 | 14 种：架构、数据流、流程图、时序、对比、时间线、思维导图、Agent、记忆、用例、类图、状态机、ER、网络拓扑 |
| 视觉风格 | 12 种（Style 8「暗夜奢华」为 AI 手绘风，不接受 JSON 渲染；Style 9–12 为工程评审专用，不支持提示词生成） |
| 质量门禁 | XML 结构 / 箭头标记 / 几何安全 / 构图质量，失败项直接列出明细 |
| 导出 | SVG 源文件、PNG（960–3840px）、GIF 语义动效（5.75s / 115 帧）、离线交互 HTML |
| 布局报告 | 视觉风格、节点连线规模、构图评分（实测值对照上限，如 `total_bends 4 / 限 8`）、问题清单 |
| 其他 | 13 个内置示例、JSON 编辑器、拖拽导入 `.svg` / `.json`、画布缩放与三种背景、`Ctrl+Enter` 快捷渲染 |

---

## 环境要求

| 依赖 | 版本 | 用途 | 必需 |
|---|---|---|---|
| Python | 3.9+ | 后端与 Skill 生成器 | 是 |
| Node.js | 18+ | GIF 渲染 worker | 仅 GIF |
| Chrome / Edge / Chromium | 任意新版 | PNG 截图、GIF 渲染 | 是（PNG 兜底） |
| FFmpeg | 任意新版 | GIF 编码与校验 | 仅 GIF |
| `puppeteer-core` | 25.3.0 | GIF 渲染器 | 仅 GIF |

**安装可选依赖**（GIF 动效需要）：

```bash
python -m pip install -r web/requirements.txt
npm install --prefix ~/.codebuddy/skills/fireworks-tech-graph --no-save puppeteer-core@25.3.0
```

---

## 快速开始

```powershell
.\start.bat
```

脚本会自动检查依赖、安装缺失的 Python 包、启动服务并打开浏览器。控制台输出：

```
  Fireworks Tech Graph - Local Web Console
  -----------------------------------------

  Starting server ...
  Browser will open: http://127.0.0.1:8777/
  Close this window to stop the service.
```

默认监听 `http://127.0.0.1:8777/`。关闭窗口即停止服务。

**手动启动 / 换端口：**

```bash
python web/server.py --port 9000 --no-browser
# 或用环境变量
FTG_PORT=9000 python web/server.py
```

---

## 使用流程

**方式 A — 提示词生成（推荐）**

1. 左栏选**图类型**（第 1 步）和**视觉风格**（第 2 步）
2. 第 3 步用自然语言描述你的系统
3. 点**生成图表**，自动产出 JSON 规格并直接渲染
4. 右栏查看质量门禁，按需导出

```
RAG 流程：用户查询 → 向量化 → 向量库检索 → 大模型生成 → 答案
用户 → API 网关 → 用户服务 / 订单服务 / 支付服务 → PostgreSQL + Redis
Kafka → Spark 处理 → 写入 S3 → Athena 查询
```

**方式 B — 内置示例 / 手写 JSON**

1. 从「示例」下拉选一个内置场景（会自动同步图类型与风格）
2. 或在第 4 步的编辑器里粘贴自己的 JSON 规格
3. 点**渲染 SVG**

拖拽 `.svg` 或 `.json` 文件到预览区可直接导入。

### 提示词生成的两种模式

| 模式 | 要求 | 能力 |
|---|---|---|
| 本地解析 | 无 | 识别「A → B → C」链路与并列元素，按关键词推断图形与流向。零配置、离线可用 |
| 大模型 | 需配置 API | 理解复杂自然语言描述，自动规划节点、分层与连线语义 |

配置入口在第 3 步的「模型设置」，支持任何 **OpenAI 兼容接口**（OpenAI / DeepSeek / 通义 / 智谱 / 本地 Ollama 等）。API Key 只存在本机
`~/.codebuddy/fireworks-tech-graph-config.json`，不进 Git；也可用环境变量 `OPENAI_API_KEY` / `OPENAI_BASE_URL` / `OPENAI_MODEL` 覆盖。

**关键设计**：大模型只负责语义（有哪些节点、谁连谁），**坐标全部由本地布局引擎确定性计算**。
模型不擅长算数，直接输出坐标几乎必然撞上间距与路由门禁；分工之后既稳定又可控。
生成后会跑完整校验，未通过先放宽间距重排，仍失败且配置了模型时，会把错误回传要求修正。

### 风格支持范围

| 风格 | 提示词生成 | 说明 |
|---|:---:|---|
| 1–7 | ✅ | 完全支持 |
| 8 | — | AI 手绘风，不接受 JSON 渲染（界面置灰） |
| 9–12 | — | 工程评审专用，需 C4 层级 / 部署归属 / 事件轨道 / 黄金信号等领域数据，请用内置示例或手写 JSON |

### 图规格 JSON 示例

```json
{
  "mode": "architecture",
  "style": 7,
  "title": "API Integration Flow",
  "width": 960,
  "height": 700,
  "containers": [
    { "id": "entry", "x": 40, "y": 120, "width": 880, "height": 110, "label": "Integration" }
  ],
  "nodes": [
    { "id": "app", "kind": "rect", "x": 80, "y": 156, "width": 180, "height": 54, "label": "Application" }
  ],
  "arrows": [
    { "source": "app", "target": "sdk", "source_port": "right", "target_port": "left", "flow": "control" }
  ]
}
```

完整字段见 `~/.codebuddy/skills/fireworks-tech-graph/fixtures/` 下的官方 fixture。

---

## 项目结构

```
tech-graph/
├── start.bat                # 一键启动（纯 ASCII，避免 cmd 编码问题）
├── web/
│   ├── server.py            # FastAPI 后端
│   ├── layout.py            # 确定性布局引擎：语义 → 几何安全坐标
│   ├── llm.py               # 大模型客户端与提示词构建
│   ├── heuristic.py         # 本地启发式解析（无 API Key 兜底）
│   ├── requirements.txt
│   └── static/              # 前端：index.html / style.css / app.js
├── 技术规范.md               # 上游 Skill 的技术栈与部署兼容性分析
├── 二次开发改动记录.md         # 每次改动的提示词与实现记录
└── README.md
```

运行时产物落在 `web/runtime/`（已被 `.gitignore` 忽略，服务启动时会清理超过 24 小时的文件）。

---

## HTTP 接口

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/meta` | 图类型、风格、门禁清单 |
| GET | `/api/doctor` | 运行环境自检 |
| GET | `/api/examples` | 内置示例列表 |
| GET | `/api/example/{name}` | 加载指定示例 |
| POST | `/api/render` | JSON → SVG + 质量门禁 + 布局报告 |
| POST | `/api/check` | 校验已有 SVG |
| POST | `/api/inspect` | 读取 SVG 语义元数据 |
| POST | `/api/png` | 导出 PNG |
| POST | `/api/html` | 导出离线交互 HTML |
| POST | `/api/gif` | 提交 GIF 任务（异步，返回 `task_id`） |
| GET | `/api/gif/{task_id}` | 轮询 GIF 任务状态 |
| POST | `/api/generate` | 提示词 → 图规格 + 渲染 + 门禁 |
| GET | `/api/config` | 读取大模型配置（Key 脱敏） |
| POST | `/api/config` | 保存大模型配置 |
| POST | `/api/config/test` | 测试接口连通性 |
| GET | `/api/config/models` | 拉取可用模型列表 |
| POST | `/api/cleanup` | 清理过期产物 |

---

## 已知坑（Windows）

这里记录三个排查成本很高的坑，都已在本项目中修掉，改动细节见 `二次开发改动记录.md`。

1. **cairosvg 可用性误报** — 上游 `doctor` 只用 `importlib.util.find_spec` 判断模块是否存在，Windows 上显示可用但实际导出必崩（缺 `libcairo-2.dll`）。本项目改为真正渲染一次再判定，并自动回退 Chrome headless。

2. **Chrome 截图挂死** — 机器上开着 Chrome 时，headless 实例共用默认 user-data-dir 会争抢单例锁而挂起。本项目每次截图分配独立 `--user-data-dir`，从挂死变成约 3 秒完成。

3. **cmd 中文乱码** — bat 以 UTF-8 存盘而 cmd 按 GBK 解码，中文提示会变成乱码并被当成命令执行。启动脚本已改为纯 ASCII（文件名也用 `start.bat`）。

**其他注意：**

- 服务只监听 `127.0.0.1`，不对外暴露
- GIF 仅支持**本工具渲染生成**的 SVG（需带语义契约），外部导入的 SVG 会 fail-closed，这是上游 Skill 的限制

---

## 许可

MIT License © yoruaki

---

## 作者

**yoruaki**

- 邮箱：wemarkrss@qq.com
- 站点：https://www.zhangyipao.com
