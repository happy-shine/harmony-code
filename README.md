# harmony-code

`harmony-code` 是一个面向小团队和私有化部署的 Claude Code 多用户网关平台。项目基于 `deer-flow` 改造，保留了 Next.js 工作台、FastAPI 网关、上传、线程工作区、MCP 与 Skills 管理能力，并将原有 LangGraph Agent Runtime 替换为 Claude Code CLI 子进程执行模型。

> 当前版本适合内部团队使用，不建议直接作为公开 SaaS 部署。

## 核心特性

- **Claude Code CLI 执行底座**：每条用户消息都会启动独立的 `claude` 子进程，并通过 `stream-json` 事件流回传到前端。
- **多用户会话网关**：提供登录、会话 Cookie、用户隔离、线程管理、上传文件、工作区文件访问等基础能力。
- **线程级工作目录**：每个线程拥有独立的 `workspace/` 和 `uploads/`，方便 Claude Code 在隔离目录内读写和执行命令。
- **MCP 与 Skills 管理**：支持按用户安装和启用 MCP Server / Skills，并在每次运行时组合成 Claude Code 可识别的配置。
- **SSE 流式响应**：前端通过 Server-Sent Events 接收 Claude Code 输出，支持持续展示工具调用、文本增量和运行结果。
- **审计日志**：记录 `cc.spawn` 与 `cc.result` 事件，便于排查运行时间、退出码、成本与并发状态。
- **并发控制**：支持服务级、用户级、线程级并发限制，避免同一线程重复 `--resume` 导致状态竞争。

## 技术栈

| 模块 | 技术 |
| --- | --- |
| 前端 | Next.js、React、TypeScript、TanStack Query、Tailwind CSS |
| 后端 | FastAPI、SQLAlchemy、Alembic、SQLite、SSE |
| Agent 执行 | Claude Code CLI |
| 包管理 | `uv`、`pnpm` |
| 数据存储 | SQLite + 本地文件系统 |

## 项目结构

```text
harmony-code/
├── README.md                         # GitHub 中文说明
├── docs/plans/                       # 架构设计、实施计划、Claude Code CLI 记录
└── deer-flow-main/
    ├── backend/                      # FastAPI 网关、认证、数据库、CC Adapter
    ├── frontend/                     # Next.js 工作台
    ├── docker/                       # Docker 与部署辅助文件
    ├── docs/                         # 子系统文档
    └── README.md                     # 更细的英文运行说明
```

## 工作原理

```text
Browser
  │ HTTPS / SSE
  ▼
Next.js frontend
  │ API proxy
  ▼
FastAPI gateway
  │ spawn per message
  ▼
Claude Code CLI
  │ workspace + session jsonl
  ▼
HARMONY_DATA_DIR
```

网关负责用户、线程、权限、MCP/Skills 配置、上传文件和审计日志；Claude Code CLI 负责对话执行、工具调用、代码读写和终端命令。线程与 Claude Code session 的映射保存在 SQLite 中，后续对话会通过 `--resume` 继续同一个 session。

## 环境要求

- macOS / Linux 开发环境
- Python 3.12+
- Node.js 22+
- `uv`
- `pnpm` 10.26.2
- 已安装并登录的 Claude Code CLI

```bash
claude login
```

## 快速开始

```bash
git clone https://github.com/happy-shine/harmony-code.git
cd harmony-code/deer-flow-main
```

安装后端依赖：

```bash
cd backend
uv sync
```

安装前端依赖：

```bash
cd ../frontend
pnpm install
```

初始化数据目录和数据库：

```bash
cd ../backend
export HARMONY_DATA_DIR=$PWD/../.harmony-data
uv run alembic upgrade head
```

创建管理员用户：

```bash
uv run python -m app.admin create-user \
  --email admin@example.com \
  --password 'change-me' \
  --admin
```

## 本地开发

启动后端：

```bash
cd deer-flow-main/backend
export HARMONY_DATA_DIR=$PWD/../.harmony-data
uv run uvicorn app.gateway.harmony_app:app --reload --host 127.0.0.1 --port 8000
```

启动前端：

```bash
cd deer-flow-main/frontend
pnpm dev
```

打开浏览器访问：

```text
http://localhost:3000
```

## 常用配置

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `HARMONY_DATA_DIR` | 必填 | 数据根目录，保存 SQLite、线程工作区、上传文件、Skills 与临时 MCP 配置 |
| `HARMONY_MAX_SERVER` | `20` | 服务进程内最大并发 Claude Code 子进程数 |
| `HARMONY_MAX_PER_USER` | `3` | 单用户最大并发 Claude Code 子进程数 |
| `HARMONY_CC_TIMEOUT_SECONDS` | 未设置 | 单次 Claude Code 运行的最大秒数，未设置表示不限制 |

## 测试

后端测试：

```bash
cd deer-flow-main/backend
uv run pytest -q
```

前端测试：

```bash
cd deer-flow-main/frontend
pnpm test
```

前端类型检查：

```bash
cd deer-flow-main/frontend
pnpm typecheck
```

## 安全说明

- 网关会校验线程归属，非本人线程统一返回 404，避免暴露资源存在性。
- Claude Code 子进程环境变量采用 allowlist，只透传 `PATH`、`HOME`、`LANG`、`LC_ALL`、`TZ` 和 `CLAUDE_CODE_*`。
- 审计日志不记录用户 prompt、工具输出、Cookie 或 session token。
- 当前设计使用主机上的单个 Claude Code 登录态，适合可信内部团队，不适合作为多租户公开服务直接上线。

## 路线与限制

- 暂无完整的全局 MCP / Skills 管理后台。
- 并发限制为单进程内计数，多副本部署需要额外的分布式协调。
- 暂无计费、配额和消息级限流能力。
- 部分历史 `deer-flow` 文档仍在迁移中，当前运行方式以本 README 和 `deer-flow-main/README.md` 为准。

## 许可证与致谢

本项目基于 MIT License 发布，详见 `deer-flow-main/LICENSE`。

`harmony-code` 是基于 [deer-flow](https://github.com/bytedance/deer-flow) 的独立改造版本，不隶属于 ByteDance 或原项目团队。感谢原 `deer-flow` 作者提供的基础工程结构与开源贡献。
