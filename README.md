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
| `--provider mock\|openai` | 全局 LLM 提供方 |
| `--model / --base-url / --api-key` | 全局端点覆盖（角色级请用 `.env`） |
| `--bus memory\|redis\|auto` | 总线实现（默认 `auto`） |
| `--redis-url URL` | Redis 地址，如 `redis://localhost:6379/0` |
| `--max-rounds N` | 测试失败后最多返工轮数（默认 3） |
| `--max-tool-steps N` | 执行者每轮最多工具迭代次数（默认 8） |
| `--session-timeout SEC` | 整个会话超时（默认 300 秒） |
| `--strict-first-review` | 演示用：让 reviewer 首轮必定打回一次 |
| `--env-file PATH` | 指定 `.env` 路径 |
| `--check` | 只打印解析后的配置随即退出 |
| `--quiet` | 只打印最终摘要，不输出实时消息流 |

会话记录目录通过 `AGENT_RUNS_DIR` 环境变量设置。退出码：`0` 成功、`1` 会话未通过、
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
.\.venv\Scripts\python.exe -m pytest          # 70 项，全离线，无网络 / 无 Docker / 无 API Key
```

用例覆盖：总线发布订阅与排空、配置分层（全局 → 角色覆盖）、四个 Agent 的决策分支、
沙箱越界拦截、LLM mock 与重试、编排状态机（含审查返工与测试失败返工）、会话落盘。

## 目录结构

```
src/agentteam/
├── bus.py            # InMemoryBus / RedisBus + Subscription
├── schemas.py        # Message / TaskPlan / ActResult / ReviewResult / TestResult
├── config.py         # .env + CLI 分层配置，按角色生成 LLM 客户端
├── console.py        # rich 实时打印
├── orchestrator.py   # 状态机、限流、汇总、transcript 落盘
├── llm/              # client(OpenAI 兼容) / mock(离线脚本) / factory
├── agents/           # base / planner / actor / reviewer / tester
└── tools/            # file / shell / search / registry
```

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
