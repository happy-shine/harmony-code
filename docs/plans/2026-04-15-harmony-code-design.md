# harmony-code 设计文档

**日期**: 2026-04-15
**状态**: 设计定稿, 待进入实施 plan

## 产品定位

harmony-code = **Claude Code (CC) runtime + deer-flow UI/管理层** 的组合产品。

定位为**通用对话式 agent 平台**: 用户通过 deer-flow 的 UI 和 CC 对话, 由 CC 驱动用户自带的 skill / MCP server 完成业务。产品本体不耦合任何具体业务 (例如 emergency_plan_swiftgen 这类应急预案生成 app 是"用户装的 skill", 不进主代码)。

## 锁定的关键决策

| # | 决策 | 选择 |
|---|------|------|
| Q1 | CC 在最终系统里的角色 | **A**: 替换 deer-flow 的 harness (保留 frontend + gateway, 删 LangGraph runtime) |
| Q2 | Agent/Model/MCP/Skill 配置源头 | **A**: deer-flow UI 是唯一管理面, CC 配置文件由 adapter 按需合成 |
| Q3 | 部署形态 | **B**: 小团队内网服务; 黑盒用 CC (不改源码); 一次 `claude login` 全服共用 OAuth |
| Q4 | CC 子进程生命周期 | **B**: ephemeral per-message + `--resume`; CC 的 `~/.claude/projects/{hash}/{sid}.jsonl` 是会话 SoT, deer-flow DB 只存 `thread_id → session_id + cwd` 映射 |
| Q5 | 前端渲染 CC jsonl | **M2**: 前端原生理解 CC jsonl, Gateway 极薄转发; 废弃 artifacts, 换成 **workspace 文件浏览器** |

## 三个不变量

1. **CC 是黑盒**。adapter 只通过官方 CLI 参数 + stdin/stdout jsonl 驱动它, 永不 patch `claude-code-main/src/`。这份源码在项目里只是研究参考, 不参与构建。
2. **CC 的 session jsonl 是会话 SoT**。deer-flow DB 只存映射; 历史回放直接读 `~/.claude/projects/{hash}/{sid}.jsonl`。
3. **deer-flow DB 是配置 SoT**。用户、thread 元数据、enabled MCP / skill 列表、model 偏好全在 DB, 每次 spawn CC 时 adapter 合成出 CC 需要的配置文件/参数。

---

## Section 1: 架构总览

```
┌─────────────────────────────────────────────────────────────┐
│ deer-flow frontend (Next.js, port 3000)                     │
│  - 线程化对话 UI                                              │
│  - CC-native 消息渲染器 (原生理解 CC jsonl)                   │
│  - workspace 文件浏览器 (替代 artifacts)                     │
│  - Agent/Model/MCP/Skill 管理界面                            │
└─────────────────────┬───────────────────────────────────────┘
                      │ HTTP (CRUD) + SSE (消息流)
                      ▼
┌─────────────────────────────────────────────────────────────┐
│ deer-flow gateway (FastAPI, port 8001)                      │
│  - 沿用: auth/users, threads, uploads, models, mcp, skills  │
│  - 新增: CC adapter router (POST /threads/{tid}/messages)   │
│  - 新增: workspace 文件浏览/下载 router                      │
│  - 删除: LangGraph 相关 (harness/agents/middlewares)        │
└─────────────────────┬───────────────────────────────────────┘
                      │ spawn(每条消息一次)
                      ▼
┌─────────────────────────────────────────────────────────────┐
│ CC subprocess (ephemeral)                                   │
│  claude -p --resume <sid> --output-format stream-json       │
│         --verbose --mcp-config <generated.json>             │
│         --permission-mode bypassPermissions                 │
│  cwd = backend/.deer-flow/threads/{tid}/user-data/workspace │
└─────────────────────────────────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────────┐
│ CC 本地状态 (服务端统一)                                       │
│  ~/.claude/                                                 │
│    ├── credentials              (一次 claude login)         │
│    ├── projects/{cwd-hash}/     (session jsonl = SoT)       │
│    └── skills/ (可选, 全局 skill)                            │
└─────────────────────────────────────────────────────────────┘
```

### 三条数据通路

| 通路 | 来源 | 终点 | 形态 |
|---|---|---|---|
| 消息下行 | 用户输入 | CC positional prompt | 单条用户消息 |
| 消息上行 | CC stdout | 前端 | jsonl → SSE `data:` 直通, 前端原生解析 |
| 配置注入 | deer-flow DB | CC spawn 参数 | 临时 mcp-config.json + 环境变量 + permission-mode |

### 删除 / 保留 (粗粒度)

- **删**: `backend/packages/harness/` 全部; LangGraph server (port 2024); `frontend/src/core/api/` 的 LangGraph SDK; `frontend/src/core/artifacts/`; `frontend/src/core/messages/` 现有模型
- **保留**: frontend 路由与组件壳; gateway 的 models/mcp/skills/memory/uploads/threads router (改实现)
- **搁置 (源码保留, MVP 不挂路由)**: `backend/app/channels/` (Slack/Feishu/Telegram)

---

## Section 2: CC Adapter 核心

### Spawn 调用模板

```bash
# 首条消息 (无 session)
claude -p \
  --output-format stream-json --verbose \
  --mcp-config <tmp>/mcp-<thread_id>.json \
  --permission-mode bypassPermissions \
  --model <model_from_db> \
  --add-dir <thread_cwd>/uploads \
  <user_prompt>

# 后续消息
claude -p --resume <session_id> \
  --output-format stream-json --verbose \
  --mcp-config <tmp>/mcp-<thread_id>.json \
  --permission-mode bypassPermissions \
  --model <model_from_db> \
  --add-dir <thread_cwd>/uploads \
  <user_prompt>
```

- cwd: `backend/.deer-flow/threads/{tid}/user-data/workspace`
- stdin 不接, 用 positional `<user_prompt>` (最老牌最兼容)
- stdout: jsonl 每行一个事件, adapter 逐行转发
- stderr: 捕获, 异常时 emit 成 SSE `error` 事件
- 超时: 默认单次 10 分钟 (`config.yaml` 可调)
- 附件: 落 `{thread_cwd}/uploads/`, `--add-dir` 授权读取, prompt 文本由前端拼路径提示

### Session 映射

DB `threads` 表字段: `thread_id, session_id (nullable), cwd`

- Thread 创建: 插一行, session_id 空, 初始化 `workspace/ uploads/ outputs/`
- 首条消息: adapter 不带 `--resume`, 从 CC 的 `system` init 事件读 `session_id` 立即写回 DB
- 后续消息: 读 DB 拿 `session_id`, 加 `--resume`
- 删 thread: DB 删行 + `.deer-flow/threads/{tid}/` + `~/.claude/projects/{cwd-hash}/{sid}.jsonl`

### 并发与隔离

| 策略 | MVP 默认 | 超限行为 |
|---|---|---|
| Per-thread 串行 | 1 | HTTP 409 `thread_busy` |
| Per-user 并发 | 3 | HTTP 429 `user_concurrency_limit` |
| Server-wide 并发 | 20 | HTTP 503 `server_busy` |

文件系统隔离: CC cwd 严格在 `{thread_cwd}`; `--add-dir` 只加 `uploads/`; 环境变量白名单 (PATH/HOME/CLAUDE_CODE_* 等), 屏蔽 `AWS_* / GCP_* / *_TOKEN / *_KEY / DATABASE_URL`。

### 取消 (用户点 Stop)

```
前端 abort fetch (SSE reader.cancel)
  → Gateway 检测 client disconnect
  → adapter SIGTERM CC, 2s 未退 SIGKILL
  → CC 自己把半成品 turn 落到 session jsonl
  → 下条 --resume 正常续
```

### 错误处理

| 场景 | adapter 行为 | SSE 事件 |
|---|---|---|
| CC exit 0 | 正常结束, close SSE | 原生 `result` + `event: done` |
| CC exit ≠ 0 | 收集 stderr 尾部 500 行 | `{code, stderr_tail, exit_code}` |
| CC > timeout | SIGKILL | `{code:"timeout"}` |
| session jsonl 损坏 | 降级: 不带 resume 重开 | `{type:"session_reset"}` + 新 session_id |
| 并发冲突 | 入口拦截, 不 spawn | HTTP 409 (不走 SSE) |

### 权限模式

MVP 用 `--permission-mode bypassPermissions`。理由: 小团队内部可信 + cwd 隔离 + 环境变量白名单。未来需要 gated 流程时用 MCP **permission prompt tool** (CC 官方机制), 不回改 permission-mode。

---

## Section 3: Gateway API

### 端点总览

| Path | 方法 | 状态 | 说明 |
|---|---|---|---|
| `/api/auth/*` | - | **启用** | 打开 `backend/app/server/` 现成的 better-auth |
| `/api/threads` | GET/POST | 保留 | 列表/创建, 创建时 init cwd 目录 |
| `/api/threads/{tid}` | GET/DELETE | 保留改实现 | 删连带清 cwd + session jsonl |
| **`/api/threads/{tid}/messages`** | **POST** | **新增** | **核心 SSE 端点** |
| `/api/threads/{tid}/cancel` | POST | 新增 | 兜底: 显式取消 |
| `/api/threads/{tid}/workspace` | GET | 新增 | 返回 cwd 下文件树 |
| `/api/threads/{tid}/workspace/files/{*path}` | GET | 新增 | 下载/预览单文件 |
| `/api/threads/{tid}/session-jsonl` | GET | 新增 | 返回 CC 原始 session jsonl |
| `/api/uploads` | POST | 保留 | 文件上传到 `{thread_cwd}/uploads/` |
| `/api/models` | GET/PUT | 保留 | 模型列表 + 用户偏好 |
| `/api/mcp` | GET/POST/DELETE | 保留改实现 | MCP server CRUD |
| `/api/skills` | GET/POST/DELETE | 保留改实现 | skill 安装/卸载/启用 |
| `/api/memory/*` | GET/POST | 保留 | CC 原生 memory 目录的 CRUD 包装 |
| ~~`/api/agents/*`~~ | - | **删除** | LangGraph 产物 |
| ~~`/api/suggestions`~~ | - | 删或改 | 如 LangGraph 产物则删 |
| ~~`/api/channels/*`~~ | - | **搁置** | MVP 不挂 |

### SSE 协议

**请求**:
```http
POST /api/threads/{tid}/messages
Content-Type: application/json
Authorization: Bearer <session-token>

{
  "content": "...",
  "attachments": ["uploads/xxx.pdf"]
}
```

**响应**:
```
HTTP/1.1 200 OK
Content-Type: text/event-stream

data: {CC jsonl 每行原样}

...

data: {"type":"result","duration_ms":...,"total_cost_usd":...,"usage":{...}}

event: done
data: {}
```

**协议分层约定**: CC stdout 每行 jsonl → 原样 SSE `data:`。唯一例外:
- Gateway 注入的 `_adapter` 前缀事件 (`spawning` / `spawned` / `canceled`)
- 异常时的 `event: error` (payload 含 code/exit_code/stderr_tail)

CC 原生事件永不以下划线开头, 不会冲突。

### 鉴权

better-auth + HttpOnly cookie; thread 所有操作前校验 `threads.user_id == current_user.id`; workspace 文件访问路径 `resolve().is_relative_to(cwd)`。

### 请求取消两条路径

1. 主: 客户端断 SSE → Starlette `request.is_disconnected()` → SIGTERM CC
2. 兜底: `POST /api/threads/{tid}/cancel` → 查 running pid → SIGTERM

---

## Section 4: 前端改造

### 事件模型 (TypeScript, 与 CC stream-json 1:1)

```ts
type CCBlock =
  | { type: "text"; text: string }
  | { type: "thinking"; thinking: string }
  | { type: "tool_use"; id: string; name: string; input: unknown }
  | { type: "tool_result"; tool_use_id: string; content: ...; is_error?: boolean }

type CCAssistantEvent = { type: "assistant"; message: { id; content: CCBlock[]; stop_reason? }; parent_tool_use_id? }
type CCUserEvent      = { type: "user"; message: { content: CCBlock[] }; parent_tool_use_id? }
type CCSystemInit     = { type: "system"; subtype: "init"; session_id; model; cwd; tools[]; mcp_servers[] }
type CCResultEvent    = { type: "result"; duration_ms; total_cost_usd?; usage? }

type AdapterEvent     = { type: "_adapter"; subtype: "spawning" | "spawned" | "canceled" }
```

### Reducer (事件 → UIMessage[])

- Assistant 事件累加: 同 `message.id` 多次合并 content blocks; 文本/思考增量拼接; tool_use 整块替换
- tool_result 回填: 找对应 `tool_use_id` 的 block, 填 result
- Subagent 嵌套: 带 `parent_tool_use_id` 的事件挂在对应 `Task` tool_use 的 children
- TodoWrite 旁路: 该 tool_use 不在消息流渲染, 送到右侧 Todos 面板

### 组件树

```
<Thread>
  <SystemInitBanner model cwd tools mcp_servers />
  <MessageList>
    <UserBubble>
    <AssistantMessage>
      <TextBlock markdown streaming/>
      <ThinkingBlock defaultCollapsed/>
      <ToolUseBlock>
        <ToolHeader icon name status/>
        <ToolInputPreview input collapsed/>
        <ToolResultPanel result/>
        {name === "Task" && <NestedMessages/>}
      </ToolUseBlock>
    </AssistantMessage>
  </MessageList>
  <ResultFooter duration cost usage/>
  <Composer/>
</Thread>
```

### 特殊工具渲染 pack

| Tool | 定制 |
|---|---|
| `Read` / `Write` / `Edit` | 差异着色 + 行号 + 点击跳 workspace 浏览器 |
| `Bash` | 终端风格, stdout/stderr 分色 |
| `Glob` / `Grep` | 结果列表, 命中文件可点 |
| `TodoWrite` | 消息流不显示, 旁路右侧面板 |
| `Task` | 内嵌 `<NestedMessages>` 显示子 agent 工作流 |
| `WebFetch` / `WebSearch` | URL 卡片 |
| 其他 MCP tool | Default fallback (JSON 折叠输入 + 智能嗅探结果) |

### WorkspacePanel (右侧面板, 替代 artifacts)

```
<Tabs>
  <Tab name="Files"> 文件树 + 预览 (md/code/图片) </Tab>
  <Tab name="Todos"> 订阅 TodoWrite 流 </Tab>
  <Tab name="Session"> 原始 session jsonl 只读查看 </Tab>
</Tabs>
```

### Stream Client

```ts
export async function* openMessageStream(threadId, payload, signal): AsyncGenerator<StreamEvent> {
  const resp = await fetch(`/api/threads/${threadId}/messages`, {
    method: "POST", body: JSON.stringify(payload),
    headers: { "Content-Type": "application/json" }, signal,
  })
  // ... SSE 帧解析
}

export function useThreadStream(threadId) {
  const [messages, dispatch] = useReducer(messageReducer, [])
  const [status, setStatus] = useState<"idle"|"running"|"error">("idle")
  const abortRef = useRef<AbortController>()
  const send = async (content, attachments?) => { /* iterate stream, dispatch */ }
  const cancel = () => abortRef.current?.abort()
  return { messages, status, send, cancel }
}
```

---

## Section 5: 用户隔离与鉴权

### 两层主体, 一个服务级凭证

- **deer-flow 层**: 多用户 (`users / threads / uploads / mcp_servers / skills / user_prefs` DB 表); 所有数据隔离靠 `threads.user_id` 严格校验
- **CC 层**: 单实例, 无用户概念; 服务器级 OAuth 一次 `claude login`, 所有 thread 共用
- **关键**: CC 不知道"用户"是什么, 区分在 deer-flow 层做

### 认证

启用 `backend/app/server/` 现成的 better-auth:
```
POST /api/auth/sign-in { email, password }
  → Set-Cookie: better-auth.session_token (HttpOnly, Secure, SameSite=Lax)
```

**Admin CLI**: `python -m app.admin create-user --email xxx --role admin`

**Roles**: `admin` / `member` 两级
- admin: 管理全局 MCP/skill (`user_id=NULL`), 踢用户, 看任何 thread 诊断
- member: 只管自己数据

### Thread 文件系统隔离

```
backend/.deer-flow/threads/{tid}/user-data/
  ├── .claude/         ← Section 6 讲的动态 skills/settings
  ├── workspace/        ← CC cwd
  ├── uploads/          ← 用户上传
  └── outputs/
```

- CC cwd = `workspace/`; `--add-dir` 只加 `uploads/`
- 环境变量白名单 + 敏感变量屏蔽
- 所有文件访问 API 层做路径规范化和 scope 校验
- MVP **不上**: chroot / cgroup / container (文档写明: 升级到 C/SaaS 前必须上)

### Session jsonl 归属

- 物理: `~/.claude/projects/{cwd-hash}/{sid}.jsonl` 全服共用目录
- 访问控制在 Gateway 做: 只通过 `/api/threads/{tid}/session-jsonl` 访问, 校验 `user_id`
- 孤儿 jsonl (磁盘有但 DB 无): 每日半夜 GC 扫对账
- **`{cwd-hash}` 派生规则** (M0 实测, CC 2.1.92): CC 对 cwd 调用 `os.path.realpath` 后把路径分隔符 `/` 换成 `-` 得到目录名。例如 `/tmp/cc-spike/workspace` 在 macOS 会因 `/tmp` → `/private/tmp` 符号链接被规范化成 `-private-tmp-cc-spike-workspace`。SessionStore 计算 jsonl 路径时必须先 `realpath(cwd)` 再派生, 否则会开文件失败。

### Hook frame 转发策略 (M1)

CC 在每次 `system.init` 之前会先发 `system/hook_started` + `system/hook_response` 帧, 其 payload 内嵌 hook 进程的 stdout 原文。hook 脚本可能读 env / secrets / 凭据, 这些内容以 **明文** 出现在 stdout 里。

**决策 (2026-04-15): 默认在 Gateway SSE 层 drop 掉所有 `type == "system" && subtype` 以 `hook_` 开头的帧, 不转发给前端。** 理由:
- 单用户自用场景也不应默认把 hook stdout 暴露给浏览器 (开发者工具 / 扩展可读)
- 调试时按需开一个 `?include_hooks=1` query 开关即可, 不默认开
- frontend 的事件模型 (Section 4 TS types) 因此不需要 `system.hook_*` 变体

### HOME 泄漏 (M1 已知限制, 延后至 M5 修复)

M0 实测: 在用户 dev 机器上 spawn CC 时, 全局 `~/.claude/skills/` / 插件 skills / 用户级 MCP servers (如 `bilibili` / `js-reverse`) 会自动进入 spawned thread 的 init 帧。这意味着 **thread 可用 skill/MCP 面不由 thread config 决定, 而与 host 的 `$HOME` 耦合**。

- M1: 接受泄漏, 在 audit log 的 `skills_enabled` / `mcp_servers_enabled` 字段里如实记录全部出现项 (包括泄漏进来的), 前端渲染时不区别对待
- M5: spike 以下选项中的可行者
  - a. spawn 时 `HOME=<thread-workspace>` 或类似的 `CLAUDE_CONFIG_DIR` 覆盖 (需先确认 CC 是否识别)
  - b. 把 thread-scope `.claude/` 的优先级设为最高, 然后在 config 里显式 deny 全局项
  - c. pre-spawn 生成 ephemeral `$HOME`, 内只链必要凭据文件
- 不上 C/SaaS 前必须修, 因为多租户里 host `$HOME` 的 skill 可能包含其他用户的代码

### 审计

每次 spawn 一行结构化 JSON 日志:
```json
{
  "ts": "...", "user_id": "...", "thread_id": "...", "session_id": "...",
  "pid": 12345, "model": "...",
  "cmd_args_hash": "...",     // 不存 prompt 原文, 存 hash
  "prompt_len": 234,
  "mcp_servers_enabled": [...],
  "skills_enabled": [...]
}
```

加一条"结果"日志 (duration/exit_code/cost)。查日志 + 读 session jsonl 能还原"用户让 CC 干了什么"。

---

## Section 6: Skills / MCP 管理流

### Thread cwd 下的 `.claude/` 布局

```
.deer-flow/threads/{tid}/user-data/
  ├── .claude/                              ← CC 向上寻找命中这里
  │   ├── skills/
  │   │   ├── my_skill  → symlink → skills_store/sk_abc/
  │   │   └── ...
  │   └── settings.json                     ← per-spawn 动态生成
  ├── workspace/ uploads/ outputs/
```

每次 spawn, adapter 重建 `.claude/skills/` (幂等、快)。

### MCP 管理流

**DB schema**:
```sql
CREATE TABLE mcp_servers (
  id TEXT PRIMARY KEY,
  user_id TEXT,                -- NULL = 全局
  name TEXT NOT NULL,
  transport TEXT,              -- "stdio" | "sse" | "http"
  command TEXT, args JSONB,
  url TEXT, headers JSONB,
  env JSONB,
  enabled BOOLEAN DEFAULT true,
  created_at TIMESTAMP
);
CREATE UNIQUE INDEX ON mcp_servers (user_id, name);
```

**spawn 时合成**:
```python
def compose_mcp_config(user_id, thread_id) -> Path:
    rows = db.query("""
        SELECT ... FROM mcp_servers
        WHERE enabled AND (user_id = :uid OR user_id IS NULL)
    """, uid=user_id)
    config = {"mcpServers": {r.name: _to_cc_mcp_entry(r) for r in rows}}
    tmp = Path(f"/tmp/deer-flow/mcp-{thread_id}-{pid}.json")
    tmp.write_text(json.dumps(config))
    return tmp
```

**生效时机**: 下一条消息生效, 正在跑的不中断, 无常驻 MCP 守护进程。

### Skills 管理流

**MVP 支持两种 source**: 上传 zip/tar.gz + Git URL

```python
def install_skill(user_id, source_type, source_payload):
    skill_id = new_id()
    dest = SKILLS_STORE / skill_id
    if source_type == "upload":
        extract_archive(source_payload, dest)
    elif source_type == "git":
        git_clone(source_payload, dest)
    validate_skill_md(dest / "SKILL.md")
    name = parse_frontmatter_name(dest / "SKILL.md")
    db.skills.insert(id=skill_id, user_id=user_id, name=name, path=str(dest), enabled=True)
```

**spawn 时物化 symlink**:
```python
def compose_skills_dir(user_id, thread_id):
    skills_dir = thread_cwd(thread_id).parent / ".claude" / "skills"
    shutil.rmtree(skills_dir, ignore_errors=True); skills_dir.mkdir(parents=True)
    rows = db.skills.query(
        "SELECT name, path FROM skills WHERE enabled AND (user_id=:uid OR user_id IS NULL)",
        uid=user_id)
    for r in rows:
        (skills_dir / r.name).symlink_to(r.path)
```

**与 deer-flow 自带目录的关系**:
- `skills/public/*` → 启动时 import 到 DB (`user_id=NULL`, `source='builtin'`)
- 用户新装落 `backend/skills_store/` (新目录, 与 `skills/custom/` 解耦避免路径混淆)

**CC plugin marketplace**: MVP 不接; 未来作为第 3 种 source 类型接入。

### Models 管理流

```
DB: user_prefs(user_id, default_model) + models(id, name, cc_model_alias, ...)
  ↓
UI 下拉选择
  ↓
spawn 时 cmd += ["--model", user_prefs.default_model or config.default_model]
```

### Memory (MVP)

CC 原生: `~/.claude/projects/{cwd-hash}/memory/` 是 per-project 记忆; 不同 thread cwd 不同 → 天然 per-thread memory, 0 串。

- MVP 不做跨 thread 的用户级长期记忆
- `/api/memory/*` 保留, 作为 CC 原生 memory 目录的 CRUD 包装
- 未来: pre-spawn hook 把 `users/{uid}/memory/*.md` 链到 thread `.claude/memory/`

### Spawn 完整合成顺序

```python
async def spawn_cc(thread_id, user_id, user_prompt):
    t = db.threads.get(thread_id)
    user_cwd = init_thread_dirs(t)                      # 幂等
    compose_skills_dir(user_id, thread_id)              # 1. skills symlink
    mcp_cfg_path = compose_mcp_config(user_id, thread_id)  # 2. mcp-config.json
    write_settings_json(user_id, thread_id)             # 3. .claude/settings.json
    env = build_clean_env()                             # 4. env 白名单
    prompt = augment_prompt(user_prompt, t.attachments) # 5. prompt 拼附件提示
    cmd = ["claude", "-p",                              # 6. cmd 构造
           "--output-format", "stream-json", "--verbose",
           "--mcp-config", str(mcp_cfg_path),
           "--permission-mode", "bypassPermissions",
           "--model", user_pref_model(user_id),
           "--add-dir", str(user_cwd / "uploads")]
    if t.session_id: cmd += ["--resume", t.session_id]
    cmd.append(prompt)
    # 7. spawn + 流式转发
```

---

## Section 7: 文件级清单

### backend/ 全删

```
backend/packages/harness/                            # 整个包
backend/langgraph.json
backend/app/gateway/routers/agents.py
backend/app/gateway/routers/runs.py
backend/app/gateway/routers/thread_runs.py
backend/app/gateway/routers/assistants_compat.py
backend/app/gateway/routers/suggestions.py
backend/Makefile 里的 dev / langgraph 目标
```

### backend/ 保留改实现

```
backend/app/gateway/app.py
backend/app/gateway/routers/threads.py      # 加 session_id/cwd 字段; 删连带清
backend/app/gateway/routers/mcp.py          # 改 DB 实现
backend/app/gateway/routers/skills.py       # 改 store 模式
backend/app/gateway/routers/models.py       # CC 模型列表 + 用户偏好
backend/app/gateway/routers/memory.py       # 包装 ~/.claude memory
backend/app/gateway/routers/uploads.py      # 写 {thread_cwd}/uploads/
backend/app/gateway/routers/artifacts.py    # 删除或改名 workspace.py 做文件树
backend/app/gateway/routers/channels.py     # 源码保留路由不挂 (MVP)
backend/app/gateway/services.py             # 删 LangGraph, 加 CCAdapter
backend/app/gateway/deps.py                 # 加 auth/user 依赖
```

### backend/ 新增

```
backend/app/cc_adapter/                     # 新建包
  __init__.py
  adapter.py                                # handle_message 主逻辑
  compose.py                                # skills/mcp/env 合成
  session_store.py                          # thread_id ↔ session_id + cwd
  stream_parser.py                          # jsonl 逐行解析 / session_id 抽取
  lifecycle.py                              # spawn/cancel/timeout/并发
  types.py
backend/app/gateway/routers/messages.py     # POST /api/threads/{tid}/messages (SSE)
backend/app/gateway/routers/workspace.py    # 文件树 + 下载
backend/app/gateway/routers/cancel.py       # POST /api/threads/{tid}/cancel
backend/app/admin/                          # CLI: create-user 等
backend/alembic/                            # DB migration
backend/.deer-flow/threads/                 # 结构不变, 加 .claude/ 层
backend/skills_store/                       # 用户安装 skill 物理落盘
```

### backend/ 测试

```
backend/tests/test_harness_boundary.py      # 删
backend/tests/test_cc_adapter_spawn.py      # 新
backend/tests/test_cc_adapter_stream.py     # 新
backend/tests/test_compose_mcp.py           # 新
backend/tests/test_compose_skills.py        # 新
backend/tests/test_workspace_scope.py       # 新 (路径逃逸)
backend/tests/test_auth_thread_isolation.py # 新
```

### backend/app/server/ (better-auth)

`CLAUDE.md` 说 "not yet active", 本次启用, 绑定 `/api/auth/*`, session cookie。

### frontend/ 保留不动

```
src/app/layout.tsx, globals css, landing, blog/
src/components/landing/ ui/ ai-elements/
src/core/i18n/ settings/ notification/ rehype/ streamdown/ utils/
src/hooks/ lib/ styles/ env.js typings/
```

### frontend/ 保留改后端契约

```
src/core/skills/ mcp/ models/ memory/ uploads/
src/components/workspace/settings/
src/core/threads/export.ts                 # 下载 session jsonl
```

### frontend/ 重写

```
src/core/api/                              # 删 LangGraph SDK, 新 CC SSE client
src/core/threads/hooks.ts types.ts utils.ts
src/core/messages/                         # reducer + block types
src/core/todos/                            # 订阅 TodoWrite 流
src/core/tasks/                            # 若是 LangGraph task 则删, 重写为 Task tool 嵌套
src/components/workspace/messages/ chats/
src/components/workspace/input-box.tsx todo-list.tsx streaming-indicator.tsx
src/components/workspace/token-usage-indicator.tsx export-trigger.tsx
```

### frontend/ 删除

```
src/core/artifacts/
src/core/agents/                           # 或改为 MCP/skill 总览页
src/core/tools/                            # LangGraph tool 列表; CC 工具在 init 事件读
src/components/workspace/artifacts/
src/components/workspace/agents/
```

### frontend/ 新增

```
src/core/cc-events/                        # jsonl 事件解析
src/core/workspace/                        # 文件树 client
src/components/workspace/cc-blocks/        # 新渲染器
  TextBlock / ThinkingBlock / ToolUseBlock / ToolResultBlock
  SystemInitBanner / ResultFooter / NestedMessages
  tool-renderers/  (Read/Write/Edit/Bash/Glob/Grep/WebFetch/DefaultMcp)
src/components/workspace/file-browser/     # WorkspaceBrowser
src/components/workspace/session-viewer/   # 原始 jsonl
```

### 根目录

```
config.yaml                                # 加 cc_adapter 段, 删 LangGraph 段
extensions_config.json                     # 一次性迁进 DB, 然后废弃
Makefile                                   # dev 起 gateway+frontend+nginx, 删 LangGraph 2024
docker/                                    # compose 删 langgraph 容器
skills/public/                             # 启动时 import DB (source='builtin')
```

### 迁移顺序 (防炸)

1. 写设计 + plan (本文档 + writing-plans 产出)
2. 后端打底: cc_adapter 骨架 + `/messages` 最小转发, 不动 langgraph
3. 前端打底: `/dev/cc` 调试页验证端到端 SSE 原样显示
4. 替换主流程: thread 页切 CC SSE client, 装完整渲染器, 删 artifacts
5. 配置迁移: mcp/skills/models router 改 DB 实现, UI 对齐, extensions_config.json 一次性迁入
6. 删代码: harness/langgraph 全删, Makefile/docker 调整, 测试 rebaseline
7. 开 auth: better-auth 启用, thread 隔离校验, 审计日志
8. 文档: 新 README/CLAUDE.md 面向 harmony-code

每步独立可跑通。

---

## Section 8: MVP 范围 + 里程碑

### MVP "Done" 定义

一句话: **deer-flow UI 登录 → 开 thread → 聊天 → CC 在隔离 workspace 干活 → 用户自带 skill/MCP 可用 → 多用户互不可见。**

### MVP 必含能力

1. 多用户登录 (better-auth, thread/workspace 严格隔离)
2. Thread 对话 (ephemeral CC + `--resume`)
3. SSE 原生 CC jsonl 流 (文字/thinking/tool_use/tool_result 分别正确渲染)
4. 取消 (SIGTERM → 下条 `--resume` 续)
5. 并发限制 (per-thread 1 / per-user 3 / server 20)
6. Workspace 文件浏览器 (CC 产物自动出现, 可预览下载)
7. 文件上传 (落 `uploads/`, CC 能读)
8. MCP 管理 UI (stdio + sse + http 三种 transport)
9. Skill 管理 UI (上传 + Git 两种 source)
10. Model 管理 UI
11. Memory (per-thread, CC 原生)
12. TodoWrite 旁路到右侧面板 (消息流无噪音)
13. Task 嵌套渲染 (subagent 在外层 tool_use 下折叠)
14. Session 导出 (下载原始 jsonl)
15. 审计日志 (结构化 JSON, spawn + 结果各一行)

### MVP 明确不做

| 不做 | 原因 | 何时做 |
|---|---|---|
| IM Channels (Slack/Feishu/Telegram) | 源码保留, 路由不挂 | 独立迭代 |
| CC plugin marketplace 集成 | 自建 skill 库够 | 有需求时加 source 类型 |
| 跨 thread 用户级 memory | CC 原生够 | pre-spawn hook |
| 网络出站白名单 / 容器沙箱 | 信任链够 | 升级到 C (SaaS) 前必须 |
| Image/binary 原生 block | positional prompt 更兼容 | CC CLI 稳定后 |
| Permission 交互式 gating | bypassPermissions 够 | 第一个不信任用户 |
| 成本追踪 / 预算告警 | 审计已有 cost, UI 延后 | v1.1 |
| 移动端 PWA | 响应式够 | 有需求 |
| Agent 配置管理 (多 agent 预设) | CC 就是"那个 agent" | 需要时做成 skill bundle |

### 反目标 (防止将来偏移)

- **不要**把 emergency_plan (或类似业务 app) 的领域逻辑放进 harmony-code
- **不要**在 adapter 里翻译 CC 事件, 翻译都在前端
- **不要**把 deer-flow 旧概念 (artifact/agent/assistant) 套到 CC

### 里程碑

| # | 名称 | 出口 |
|---|------|------|
| M0 | 设计固化 | 本文档 + writing-plans 详细 plan |
| M1 | CC Adapter 骨架能跑 | curl POST → SSE 原样 jsonl; `--resume` 能续 |
| M2 | 前端 CC-native 渲染器 | 正式 thread 页切新 client, 一次完整对话视觉"优雅" |
| M3 | 配置流打通 | UI 装 MCP+skill → 新 thread CC 能用 |
| M4 | Workspace 浏览器 + 文件流 | 上传→CC 读; CC 写→文件树自动更新 |
| M5 | 删代码 + 启用鉴权 | `make dev` 三进程; 两账号隔离测试过; 审计日志落盘 |
| M6 | 加固 + 文档 | 测试覆盖; 新 README/CLAUDE.md; skill 示例教程 |

MVP = M0..M6 完成。

### 已知风险

| 风险 | 缓解 |
|---|---|
| CC CLI 协议小版本变化 | stream_parser 只消费必需字段, 其余透传; 前端"可空字段"容错 |
| `bypassPermissions` + 任意 bash 被滥用 | MVP 信任边界; env 白名单测试; 文档写明沙箱未来 |
| 前端和 LangGraph SDK 耦合比预期深 | M2 前 feature branch 做 spike, 必要时新旧页面 coexist |
| CC session jsonl 未文档化细节 | 当只读黑盒, 查询只走 `/session-jsonl`, 不做结构化存储 |
| SKILL.md frontmatter 格式不稳 | validator 只校验 name 必填, 其余交给 CC |
| session jsonl 损坏 | Section 2 降级路径: 不带 resume 重开 + 前端提示 |

### 成功判据

1. **跑通一个真实 skill**: 把 emergency_plan 工作流打成 skill, 装进 harmony-code, 对话里完成一次四报告生成 —— **harmony-code 代码库里没有任何 emergency_plan 特有代码**
2. **两账号隔离**: A 看不到 B 的 thread/workspace/uploads; A 的 skill 不污染 B
3. **CC 忠实度**: 对话内容 vs session jsonl 人眼核对无信息丢失 (thinking/tool_use/result 都在)
4. **升级友好**: `claude` CLI 升级, harmony-code 不改代码仍跑

---

## 下一步

用 writing-plans skill 把本设计拆成按 milestone 组织的可执行实施 plan (包含任务拆分、依赖关系、每个任务的文件级改动、验收测试)。
