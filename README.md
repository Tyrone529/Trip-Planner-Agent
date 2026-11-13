# Trip Planner Agent · 约束感知旅行规划助手

支持真实地点查询、确定性排程、约束校验、内容锁定与单日重规划的旅行助手。用户可以保留已确定的住宿和活动，只调整指定日期，并查看修改前后的版本差异。

## 功能

- 生成 1–7 天行程，接收目的地、交通方式、偏好、预算、日内时间、必去地点和步行上限。
- 查询景点、住宿、天气及路线；支持高德 HTTP、本地 MCP stdio 和远程 MCP SSE。
- 模型从已查询 POI ID 中选择候选，代码拒绝未知 ID，并根据交通耗时排程。
- 检查时间冲突、交通衔接、预算、必去地点等；最多两轮删减修正，不静默放宽约束。
- 锁定住宿或活动，按天进行室内、轻松、约束或自然语言修改。
- SQLite 保存任务、版本、差异与查询检查点；乐观版本校验防止旧版本覆盖。
- 参数校验、超时、分类重试、300 秒缓存与查询结果复用、来源和执行记录。
- Vue 工作台展示行程、费用不确定性、版本和工具记录；无浏览器地图凭据时显示明确标注的坐标示意。

## 架构与技术栈

```text
Vue 3 / TypeScript / Ant Design Vue
             ↓ HTTP
FastAPI → 自定义 Planner / asyncio
             ↓
POI、住宿、天气查询 → DeepSeek JSON 选点 → 路线查询与确定性排程
             ↓
规则校验 → 有界修正 → SQLite 版本保存
```

后端采用 Python、Pydantic、httpx、MCP Python SDK 和 SQLite。当前主流程是显式工作流，不是自主 ReAct 循环；没有引入 LangGraph、RAG 或独立 Reflection Agent。SQLite 保存业务状态和检查点，不是语义长期记忆。

## 仓库目录

源码位于仓库内的 `Trip-Planner-Agent/` 子目录；下文命令中的相对路径均从克隆后的仓库根目录开始。代码入口列表中的 `app/` 路径相对于该源码子目录。

## 快速开始

需要 Python 3.11+、Node.js 20+ 和 pnpm。以下命令适用于 macOS/Linux；Windows 请使用虚拟环境 Scripts 下的 Python。

```bash
git clone https://github.com/Tyrone529/Trip-Planner-Agent.git
cd Trip-Planner-Agent
cd Trip-Planner-Agent/app/backend
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-enhanced.txt
cp .env.example .env
.venv/bin/python -m uvicorn app.api.main:app --host 127.0.0.1 --port 8013
```

另开一个终端，在仓库根目录执行：

```bash
cd Trip-Planner-Agent/app/frontend
pnpm install --frozen-lockfile
cp .env.example .env
pnpm exec vite --host 127.0.0.1 --port 5174 --strictPort
```

打开 http://127.0.0.1:5174/workspace 。保持两个终端运行；前端负责代理 API 请求，前端退出后已打开的页面可能显示 Network Error。已有 .env 时不要再次复制覆盖。

### 无密钥演示

选择「离线示例（规则规划）」和北京。建议先使用 2026-11-05 至 2026-11-07 体验固定快照：生成行程 → 锁定住宿/活动 → 修改第二天 → 查看版本差异。

离线模式不调用模型，数据是人工测试快照。日期超出固定天气快照范围会提示缺少预报，不代表真实出行建议。基础表单也默认走离线模式。

### 真实服务配置

仅在本地 Trip-Planner-Agent/app/backend/.env 填写：

| 变量 | 用途 |
|---|---|
| LLM_API_KEY | 模型服务密钥 |
| LLM_BASE_URL | 兼容 Chat Completions 的服务地址 |
| LLM_MODEL_ID | 账户实际可用、支持 JSON 输出的模型 ID |
| AMAP_API_KEY | 高德 Web 服务 Key，HTTP 及本地 MCP 模式需要 |
| AMAP_MCP_URL | 可选远程高德 SSE 地址，可能包含凭据，请勿公开 |
| TRIP_DB_PATH | 可选 SQLite 文件位置 |

远程 MCP 须提供 maps_text_search、maps_search_detail、maps_weather 和 walking/driving/transit_integrated 对应路线工具。未设置远程地址时 MCP 模式启动项目内置 stdio 服务器。

浏览器真实底图需另外在 Trip-Planner-Agent/app/frontend/.env 配置 VITE_AMAP_JS_KEY 和 VITE_AMAP_SECURITY_CODE。后端 Web Key 不等于浏览器 JS Key。VITE_API_BASE_URL 留空使用本地代理。模型与地图服务可能收费；本仓库不包含任何可用凭据。

## 测试与评测

```bash
# 在 Trip-Planner-Agent/app/backend 下执行
.venv/bin/python -m pytest tests -q
.venv/bin/python evaluation/evaluate.py

# 在 Trip-Planner-Agent/app/frontend 下执行
pnpm run build
```

2026-11-05 至 2026-11-06 本地验收记录：

| 检查 | 结果 |
|---|---|
| 自动化测试 | 47/47 通过 |
| 固定离线评测 | 30/30 通过 |
| 补充 API / 真实服务断言 | 50/50 通过 |
| 重启后 HTTP 冒烟 | 4/4 通过 |
| 前端生产构建 | 通过，存在较大资源包提示 |

离线集包含 16 个可行规划、4 个预算不可行案例、10 个局部修改案例。可行案例修正前草案有 20 项硬约束错误，修正后为 0；这是同一流程的离线消融，不是原版对照或模型准确率。各组测试有覆盖重叠，不能相加为独立业务场景总数。

脱敏记录见 [evaluation-results](Trip-Planner-Agent/evaluation-results)。其中 before-fix 记录保留了首轮短距离公交无路线等失败；后续使用较长路线验证通过，不表示所有路线都可查询。轻松模式活动数反增问题已修复，并增加 0/1/2 个锁定活动的回归。

## 边界

- 价格、房态、营业时间和预约余量未接入，费用可能是估算或未知；不支持订票支付。
- 室内属性缺失时不能保证避雨；混合交通目前按公交查询，步行总量不完整。
- 住宿偏好不等于酒店档次约束，选点/路线并非全局最优求解。
- 查询检查点只在 300 秒内复用；重启将未完成任务标记为中断，不是完整持久执行引擎。提交幂等仍有改进空间。
- 基础结果页编辑只存浏览器 sessionStorage，不同步后端版本，也不重算预算。图片/PDF 导出已验证 UI 成功流程，未逐页核验下载产物。
- 浏览器真实底图在验收环境中因缺凭据未验证；坐标示意已验证。

## 代码入口

- app/backend/app/upgrade/engine.py：编排、选点、排程和修正
- app/backend/app/upgrade/providers.py、tools.py：数据适配、工具可靠性
- app/backend/app/upgrade/validation.py、store.py：约束与持久化
- app/backend/app/upgrade/api.py、mcp_server.py：HTTP / MCP 接口
- app/frontend/src/views/Workspace.vue：增强工作台
- app/backend/tests/test_enhanced.py：自动化测试

## 来源与许可

项目参考 Datawhale HelloAgents 第 13 章。当前增强主流程改为自定义 Planner，详细来源及修改说明见 [ATTRIBUTION.md](ATTRIBUTION.md)。保留原 [CC BY-NC-SA 4.0 许可证](LICENSE.txt)。
