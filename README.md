# agentteam — 多模型协作的软件开发 Agent 团队

四个不同角色、可以各自使用**不同模型**的 Agent，通过一条消息总线实时协作，把一个目标
变成可运行、可测试的真实代码：

```
                   ┌──────────────────── 消息总线 (Redis pub/sub 或内存队列) ────────────────────┐
                   │                                                                          │
  user ──task──▶ ┌─┴────────┐ ──plan──▶ ┌──────────┐ ──act──▶ ┌──────────┐ ──approved──▶ ┌────────┐
                 │ planner  │            │  actor   │          │ reviewer │               │ tester │
                 │ 拆解目标  │            │ 写代码   │          │ 质量门禁  │               │ 跑测试  │
                 └────▲─────┘            └────▲─────┘          └────┬─────┘               └───┬────┘
                      │                       │                     │                         │
                      │                       │◀── rejected (issues)┘                         │
                      │                       │◀────────── rerun (测试失败 + 输出) ────────────┘
                      └─────────── orchestrator 编排 / 限流 / 汇总 / 落盘 ─────────────────────┘
```

* **planner（规划者）**：把一句话目标拆成 2–6 个可验证步骤 + 测试命令 + 验收标准。
* **actor（执行者）**：真正调用工具（写文件、跑命令、搜索代码）干活，多轮迭代直到完成。
* **reviewer（审查者）**：静态自检 + 模型评审，不通过就把具体问题回退给 actor 重做。
* **tester（测试者）**：执行计划里的测试命令（默认 pytest），只有**真实退出码**说了算。
* **orchestrator（编排者）**：状态机、轮次上限、超时、实时打印、会话落盘。
* **bus（总线）**：`RedisBus`（跨进程/跨机器，带历史）或 `InMemoryBus`（单进程，零依赖）。

## 状态机

| 消息 | 发送者 → 接收者 | 含义 |
| --- | --- | --- |
| `task` | user → planner | 开始一轮新任务 |
| `plan` | planner → actor | 可执行计划 |
| `log` | 任意 → 广播 | 实时进度（工具调用、命令输出…） |
| `act` | actor → reviewer | 本轮改动清单 + 命令记录 |
| `review` | reviewer → 广播 | `approved=true` 进测试；`false` 回 actor 重做 |
| `test` | tester → orchestrator | 真实测试结果（通过/失败/跳过） |
| `rerun` | orchestrator → actor | 测试失败后的返工请求（带失败输出） |
| `done` | orchestrator → 广播 | 会话结束（`done` / `failed` / `timeout`） |
| `error` | 任意 → orchestrator | 不可恢复的错误，终止会话 |

测试失败最多重跑 `AGENT_MAX_ROUNDS` 次，审查连续不通过同样受限，超时由
`AGENT_SESSION_TIMEOUT` 兜底。

## 快速开始（离线，无需任何 API Key）

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"

# 内置 mock 模型会写出真实的 hello.py + test_hello.py，并真的运行 pytest
.\.venv\Scripts\python.exe -m agentteam.main

# 演示"审查不通过 → 返工 → 再审查"的完整回路
.\.venv\Scripts\python.exe -m agentteam.main --strict-first-review

# 自定义目标与工作区
.\.venv\Scripts\python.exe -m agentteam.main --goal "写一个 fizzbuzz 模块和测试" --workspace .\sandbox
```

## 一键启动（Windows，零配置）

```powershell
scripts\start-agentteam.bat            # 启动 Web UI 并自动打开浏览器
scripts\start-agentteam.bat cli        # 命令行跑一次内置示例目标
scripts\start-agentteam.bat cli --goal "写一个 fizzbuzz 模块和测试"
scripts\start-agentteam.bat check      # 打印解析后的配置
scripts\start-agentteam.bat sessions   # 列出最近的会话记录
scripts\start-agentteam.bat test       # 跑单元测试（pytest）
scripts\start-agentteam.bat help       # 查看用法
```

脚本会自动找 Python 3.10+（`py -3` → `python`，也可以用 `AGENTTEAM_PY` 指定解释器）、
创建 `.venv`、安装依赖，然后按模式启动；结尾的“按任意键关闭”可以用 `AGENTTEAM_NO_PAUSE=1` 关掉（CI 用）。
`.bat` 本身是纯 ASCII + `chcp 65001`，中文输出全部由 Python 侧负责，不会乱码。

## Web UI（浏览器里的实时控制台）

```powershell
.\.venv\Scripts\python.exe -m agentteam.main --serve --open-browser
```

打开 `http://127.0.0.1:8765`：左侧填目标 / 工作区 / 模型参数并启动会话，右侧是 WebSocket 推送的
实时消息流（planner → actor → reviewer → tester 逐条滚动），会话结束后随时可以查看历史会话与转录。

| 参数 | 说明 |
| --- | --- |
| `--serve` | 启动 Web 服务（需要 `.[web]` 可选依赖：`pip install -e ".[web]"`） |
| `--host HOST` | 监听地址，默认 `127.0.0.1`（只本机可访问） |
| `--port PORT` | 监听端口，默认 `8765` |
| `--open-browser` | 启动后自动打开浏览器 |

> 会话结束后内存中的实时状态会被释放，页面自动改读磁盘上的 `transcript.json`，
> 因此长时间开着 Web UI 也不会越用越占内存。

## Windows 安装包（自带 Python，不需要预装环境）

`dist\agentteam-<版本>-setup.exe` 是 per-user 安装包：**不需要管理员权限**，也**不需要预装 Python**。

* 内置便携版 CPython 3.14 + 全部依赖（`agentteam[web]`），安装后 54 MB 左右，压缩包仅 18 MB
* 开始菜单：`agentteam Web UI` / `agentteam 命令行（示例目标）` / `会话记录（runs）` /
  `配置文件（.env）` / `说明文档` / `卸载 agentteam`（可选桌面快捷方式）
* 用户数据放在 `%LOCALAPPDATA%\agentteam\{workspace,runs,.env}`，**卸载不会删除**
* 从源码打包一条命令：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build-installer.ps1
```

脚本会：下载便携版 CPython（带缓存）→ 解包并 patch `._pth` → `pip --target` 装依赖 →
生成 `.cmd` 启动器 → 用内置离线目标跑冒烟测试 → 调用 Inno Setup 编译安装包；
没有 Inno Setup 时会先用 winget、失败再回退官方镜像静默安装（per-user）。

| 参数 | 说明 |
| --- | --- |
| `-Version 0.2.0` | 覆盖版本号（默认读 `pyproject.toml`） |
| `-PayloadOnly` | 只构建 payload（`build\installer\payload`），不编译安装包 |
| `-SkipSmokeTest` | 跳过 payload 冒烟测试 |
| `-Force` | 忽略缓存的便携版 Python，重新下载解包 |

## 接入真实模型（任意 OpenAI 兼容端点）

```powershell
# 云端
.\.venv\Scripts\python.exe -m agentteam.main --provider openai --model gpt-4o-mini --api-key sk-xxx

# 本地 Ollama / vLLM
.\.venv\Scripts\python.exe -m agentteam.main --provider openai --model qwen2.5-coder:14b `
    --base-url http://localhost:11434/v1 --api-key ollama
```

推荐用 `.env`（复制 `.env.example`），这样每个角色可以用**不同**的模型：

```ini
AGENT_LLM_PROVIDER=openai
OPENAI_API_KEY=sk-xxx
OPENAI_MODEL=gpt-4o-mini

AGENT_PLANNER_MODEL=gpt-4o            # 规划要强
AGENT_ACTOR_MODEL=qwen2.5-coder:14b   # 写代码要专
AGENT_ACTOR_BASE_URL=http://localhost:11434/v1
AGENT_ACTOR_API_KEY=ollama
AGENT_REVIEWER_MODEL=gpt-4o           # 审查要严
AGENT_TESTER_MODEL=gpt-4o-mini        # 测试只要总结
```

任意角色都支持 `AGENT_<ROLE>_PROVIDER / MODEL / BASE_URL / API_KEY / TEMPERATURE / MAX_TOKENS`
（`<ROLE>` ∈ `PLANNER` `ACTOR` `REVIEWER` `TESTER`），未设置的项继承全局默认值。

`.env` 的查找顺序是 `--env-file` → `AGENT_ENV_FILE` → 项目根目录 `.env`；
`--check` 会打印最终生效的配置，并对“有 key 没用对模型”之类的组合给出告警。

## 消息总线

| `AGENT_BUS` | 实现 | 适用场景 |
| --- | --- | --- |
| `memory` | `InMemoryBus`（asyncio 队列 + firehose 订阅） | 单进程、CI、离线测试（默认回退） |
| `redis` | `RedisBus`（pub/sub + 消息历史） | 多进程 / 多机部署，带 `history()` 审计 |
| `auto` | 先试 Redis，失败自动回退内存 | 本地开发（默认） |

```powershell
# 启动 Redis（本机已装 Docker 时）
docker compose up -d redis

# 用 Redis 跑一次，多进程也能看到同一份实时日志
.\.venv\Scripts\python.exe -m agentteam.main --bus redis
```

> Redis 与 Docker 都不是必须的：`AGENT_BUS=auto`（默认）先尝试连接 `REDIS_URL`，
> 连不上就自动回退到进程内总线，功能与测试完全一致。

### 用 Docker 跑 Redis（可选）

```powershell
docker compose up -d redis        # 只启动总线，Agent 在宿主机跑
docker compose --profile app up   # Agent 也进容器，用 mock 模型自检
```

## 常用命令行参数

| 参数 | 说明 |
| --- | --- |
| `--goal "..."` | 本次目标，省略则用内置的 hello 模块示例 |
| `--workspace PATH` | 代码沙箱目录（默认 `workspace/`） |
| `--runs-dir PATH` | 会话记录目录（默认 `runs/`，等价于 `AGENT_RUNS_DIR`） |
| `--provider mock\|openai` | 全局 LLM 提供方 |
| `--model / --base-url / --api-key` | 全局端点覆盖（角色级请用 `.env`） |
| `--bus memory\|redis\|auto` | 总线实现（默认 `auto`） |
| `--redis-url URL` | Redis 地址，如 `redis://localhost:6379/0` |
| `--max-rounds N` | 测试失败后最多返工轮数（默认 3） |
| `--max-tool-steps N` | 执行者每轮最多工具迭代次数（默认 8） |
| `--session-timeout SEC` | 整个会话超时（默认 300 秒） |
| `--strict-first-review` | 演示用：让 reviewer 首轮必定打回一次 |
| `--env-file PATH` | 指定 `.env` 路径（也支持环境变量 `AGENT_ENV_FILE`） |
| `--check` | 只打印解析后的配置随即退出（并给出配置告警） |
| `--quiet` | 只打印最终摘要，不输出实时消息流 |
| `--version` | 打印版本并退出 |
| `--log-level LEVEL` | 日志级别（默认 `WARNING`） |

会话记录相关：

| 参数 | 说明 |
| --- | --- |
| `--list-sessions [N]` | 列出最近 N 个会话（默认 30），带状态与目标摘要 |
| `--replay ID` | 回放某个会话的消息流水（支持 id 前缀） |
| `--export ID` | 导出某个会话的产物 |
| `--export-to PATH` | 导出目标：目录，或 `*.md` / `*.json` / `*.log` 文件 |

会话记录目录通过 `AGENT_RUNS_DIR` 环境变量设置（或 `--runs-dir`）。退出码：`0` 成功、`1` 会话未通过、
`2` 配置错误、`130` 手动中断。

## 内置工具（受工作区沙箱保护）

| 工具 | 能力 |
| --- | --- |
| `file.write` / `file.read` / `file.list` / `file.delete` | 工作区内增删读写，拦截 `..` 越界与绝对路径逃逸 |
| `shell.run` | 执行命令（默认超时 `AGENT_SHELL_TIMEOUT`），返回退出码 + stdout/stderr |
| `search.grep` / `search.glob` | 在最终产物里检索代码与文件 |

工具执行结果统一为 `ToolResult`，会写进 `ActResult.changes` 与 `observations`，
既给 reviewer 审查，也进会话记录。

## 产物

每次会话在 `runs/<session-id>/` 下生成：

* `transcript.json` — 完整消息流（id / from / to / type / round / payload / created_at），可回放审计
* `transcript.md` — 人类可读的时间线，附最终状态与失败原因

被改动的代码本身写在 `--workspace`（默认 `workspace/`）里。

## 测试

```powershell
.\.venv\Scripts\python.exe -m pytest          # 108 项，全离线，无网络 / 无 Docker / 无 API Key
```

用例覆盖：总线发布订阅与排空、配置分层（全局 → 角色覆盖）与配置告警、四个 Agent 的决策分支、
沙箱越界拦截、LLM mock 与重试、编排状态机（含审查返工与测试失败返工）、会话落盘 / 列表 / 回放 / 导出、
Web API（会话启动、录制回放、未知会话、静态页）、CLI（`--check` / `--version` / `--list-sessions` /
`--replay` / `--export` / `--serve` / 非法 env / 默认目标）。

## 目录结构

```
src/agentteam/
├── bus.py            # InMemoryBus / RedisBus + Subscription
├── sessions.py       # 会话记录落盘 / 列表 / 回放 / 导出
├── schemas.py        # Message / TaskPlan / ActResult / ReviewResult / TestResult
├── config.py         # .env + CLI 分层配置、告警，按角色生成 LLM 客户端
├── console.py        # rich 实时打印
├── orchestrator.py   # 状态机、限流、汇总、transcript 落盘
├── main.py           # CLI 入口（运行 / 回放 / 导出 / Web）
├── runtime.py        # 版本号、路径等运行时信息
├── web/              # FastAPI + WebSocket 控制台（app.py + static/index.html）
├── llm/              # client(OpenAI 兼容) / mock(离线脚本) / factory
├── agents/           # base / planner / actor / reviewer / tester
└── tools/            # file / shell / search / registry

scripts/
├── start-agentteam.bat    # 一键启动（web / cli / check / sessions / test）
├── build-installer.ps1    # 便携版 payload + Inno Setup 安装包
├── build-release.ps1      # 测试 → wheel/sdist → 安装包 → SHA256SUMS
└── bump-version.ps1       # 只改 pyproject.toml 里的版本号

packaging/
├── agentteam.iss          # Inno Setup 脚本（per-user 安装、中文向导）
└── translations/          # ChineseSimplified.isl（Inno Setup 不自带中文）
```

## 打包与发布

```powershell
# 1) 改版本号（唯一版本源：pyproject.toml）
powershell -ExecutionPolicy Bypass -File scripts\bump-version.ps1 -Version 0.2.0

# 2) 一把梭：pytest → wheel/sdist → 安装包 → dist\SHA256SUMS.txt
powershell -ExecutionPolicy Bypass -File scripts\build-release.ps1

# 3) 加 -Push 就直接提交、打 tag 并推送（tag 会触发 GitHub Actions 发布）
powershell -ExecutionPolicy Bypass -File scripts\build-release.ps1 -Push
```

推 `v*` tag 后，`.github/workflows/release.yml` 会先在 Linux 上跑 3.10 / 3.12 的测试，
再在 `windows-latest` 上构建 wheel、sdist 和安装包，附上 `SHA256SUMS.txt` 一起挂到 Release。

| 参数 | 说明 |
| --- | --- |
| `-Version X.Y.Z` | 覆盖版本号与 tag |
| `-SkipTests` / `-SkipDist` / `-SkipInstaller` | 跳过对应阶段 |
| `-Push` | 提交 + `git tag -a vX.Y.Z` + push（`-Force` 允许脏工作区） |

## 扩展

```python
from agentteam.agents.base import BaseAgent
from agentteam.bus import InMemoryBus
from agentteam.schemas import Message, MessageType, Role

class DocAgent(BaseAgent):
    role = Role.ACTOR  # 复用已有角色类型，或自行扩展 schemas.Role

    async def handle(self, message: Message) -> list[Message]:
        if message.type is not MessageType.ACT:
            return []
        await self.emit_log("生成了文档草稿", round=message.round)
        return [self.reply(MessageType.ACT, payload={"summary": "docs"}, recipient=Role.REVIEWER)]
```

自定义工具只需实现 `tool_name` + `run(**kwargs) -> ToolResult`，再注册进
`build_default_registry()`；模型通过 `tools` 列表发现它们。
