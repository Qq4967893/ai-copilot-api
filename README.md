# ai-copilot-api

AI Copilot 的应用后端与 Agent Runtime（技术设计 §2.4）。会话是它的 source of
truth；Agent 推理、LLM 调用、MCP Client 都在这里，业务数据一律不落库。

当前状态：基础架构 + **Agent 端到端链路已打通**。用户提问会经由 OpenAI Agents SDK
调用 `internal-mcp-server` 的只读工具，再流式返回带证据的回答。

## 能力

已实现：

- 配置加载（JSON 文件 + 环境变量覆盖 + 启动时校验）
- 结构化日志、`X-Copilot-Request-Id` 关联与访问日志
- 统一错误模型，与 `internal-mcp-server` 逐字段一致
- MySQL + Alembic 迁移，四张表：`conversation` / `message` / `feedback` / `agent_run`
- 会话 CRUD、游标分页、消息持久化、反馈
- SSE 流式响应，含中途失败与客户端断开的处理
- `/healthz` `/readyz`
- **Operations Copilot Agent**（OpenAI Agents SDK，单 Agent）
- **MCP Client** —— Streamable HTTP 连 `internal-mcp-server`，带 delegated user identity
- **model profile 与 Runtime Budget**（`max_turns` / 超时 / 工具超时）
- **Evidence 与 Warnings** —— 从真实 tool 调用推导并落到 `message.payload`
- `evals/questions.yaml` —— Eval 问题集草稿（14 条，尚未实现自动跑分）

**尚未实现**（目录已建好，内含约定说明）：

- `app/audit/` —— Agent Audit（`agent_run` 表已建，当前只写 status / latency）
- `app/observability/otel.py` —— OpenTelemetry
- Eval runner —— 目前只有问题集，没有自动评分
- 登录鉴权 —— MVP 用配置里的固定开发用户
- Guardrails（输入/输出）—— 当前只靠 instructions 约束
- Context 压缩 —— 当前固定取最近 20 条消息

## 安全模型

MVP 阶段没有登录（见项目根 `README.md` 第 4 条），身份来自配置：

```json
"auth": { "dev_user": { "user_id": "10001", "scopes": ["admin.api.deposits.read"] } }
```

`app/auth/dependency.py` 的 `get_current_identity()` 是唯一的身份入口，接入真实
登录后只换这个函数体。

往下游调 `internal-mcp-server` 时用 delegated user identity（技术设计 §10.3），
头由 `app/auth/identity.py` 的 `mcp_headers()` 构造：

```
Authorization: Bearer <mcp.service_token>
X-Copilot-User-Id / X-Copilot-Session-Id / X-Copilot-Request-Id   必填
X-Copilot-Scopes                                                  可选，逗号分隔
```

四个值都必须匹配 `^[A-Za-z0-9_.:@-]{1,128}$`，否则 MCP 直接 401 —— `mcp_headers()`
会提前断言，把问题暴露在本服务而不是一个语义模糊的上游错误。

AI 不放大当前运营用户的权限：最终数据权限仍由 `admin-api` / `website-api` 判断。

## 配置

```bash
cp config/config.example.json config/config.local.json
```

`config/config.local.json` 已在 `.gitignore` 里，密钥只放这里或环境变量。

优先级：**环境变量 > JSON 文件 > 字段默认值**。文件里出现未知键、必填项缺失或
取值越界，一律启动失败并以非零码退出，不做静默回退。

配置文件路径可用 `APP_CONFIG_FILE` 指定，默认 `config/config.local.json`；
文件不存在不算错误（容器里可以完全依赖环境变量）。

嵌套用**双下划线**：

| 环境变量 | 对应配置 |
|---|---|
| `SERVER__HOST` / `SERVER__PORT` | `server.host` / `server.port` |
| `DATABASE__DSN` | `database.dsn` |
| `MCP__BASE_URL` / `MCP__SERVICE_TOKEN` | `mcp.base_url` / `mcp.service_token` |
| `AUTH__DEV_USER` | `auth.dev_user`（JSON 字符串） |
| `LLM__BASE_URL` / `LLM__API_KEY` | `llm.base_url` / `llm.api_key` |
| `AGENT__IMPL` | `agent.impl`（`echo` / `operations`） |
| `CORS__ALLOW_ORIGINS` | `cors.allow_origins`（JSON 数组） |
| `LOG_LEVEL` | `log_level` |

默认端口是 **8082**：本机 `admin-api` 占 8000、`internal-mcp-server` 占 8081。

### Agent 实现切换

`agent.impl` 有两个取值：

- `echo` —— 不调 LLM，把输入分片回吐。**前端开发、CI 和大部分测试都用它**：
  确定性、不烧 token、不依赖外部服务。`config.example.json` 里的默认值。
- `operations` —— 真实 Agent。需要 `llm.api_key` 和 `mcp.service_token`，
  缺任一项启动就会失败并说明缺什么，而不是等第一个请求打过来才报错。

模型走 OpenAI 兼容端点（自建网关 / Azure / OpenAI 官方都行），只需要配
`llm.base_url` + `llm.api_key`。模型名和 Runtime Budget 在 `model.profiles` 里，
不硬编码在代码中（技术设计 §6.5 / §6.6）。

两个实现细节值得知道，都是踩过的：

- 走的是 **Chat Completions** 而非 Responses API —— Agents SDK 默认用 Responses，
  而兼容网关通常只实现 `/v1/chat/completions`。
- **SDK 自带的 tracing 被显式关闭**。它默认会把 Agent Run 的输入输出上传到
  OpenAI 的 traces 端点，用网关 key 既会失败刷日志，更等于把内部业务数据发给
  第三方（§17.5）。系统级可观测性归 OpenTelemetry。

## 本地运行

需要一个 MySQL 8：

```bash
mysql -uroot -e "CREATE DATABASE ai_copilot CHARACTER SET utf8mb4"

make install
make migrate
make run
```

其他 target：`make test` / `make lint` / `make fmt` / `make revision m="说明"`。

## API

```
GET    /healthz                                 存活，不查依赖
GET    /readyz                                  就绪，查 MySQL
POST   /api/conversations                       建会话
GET    /api/conversations?limit=&cursor=        列表，按时间倒序游标分页
GET    /api/conversations/{id}                  会话详情（含消息）
DELETE /api/conversations/{id}                  软删除
POST   /api/conversations/{id}/messages         发消息，返回 SSE 流
POST   /api/messages/{id}/feedback              反馈（upsert）
```

`/readyz` 刻意不检查 `internal-mcp-server`：技术设计 §22.4 要求不能因为非关键
外部系统短暂异常就让整个 API 无法接收请求。

## SSE

`POST /api/conversations/{id}/messages` 返回 `text/event-stream`：

```
event: message.started
data: {"message_id": "01J..."}

event: message.delta
data: {"text": "（echo）"}

event: message.completed
data: {"message_id": "01J...", "content": "（echo）你好"}
```

事件类型（技术设计 §4.6）：

```
message.started / message.delta / message.completed
tool.started   / tool.completed / tool.failed
result.data    / result.navigation / result.evidence
error
```

`operations` 会产出 `tool.*` 和 `result.evidence`；`result.data` 当前用于携带
`{"warning": {...}}`。`echo` 只产出 `message.delta`。

### Evidence 与 Warnings

回答结束后，`message.payload` 里会有：

```json
{
  "evidence": [
    {"type": "data", "source": "admin-api", "reference": "deposits/statistics",
     "observed_at": "...", "summary": "admin_api_deposits_statistics({...})"}
  ],
  "warnings": [{"code": "UPSTREAM_TIMEOUT", "message": "..."}]
}
```

**这两样都由应用层从实际发生的 tool 调用推导，不采信模型自述**
（`app/agent/evidence.py`）。模型可能漏报、错报或编造引用，而 envelope 里的
`service` / `resource` / `observed_at` 是 `internal-mcp-server` 写进去的事实。

同理，只要某次 tool 调用的 envelope 是 `status: "ERROR"`，`warnings` 里就**一定**
有记录 —— 不管模型在回答里说了什么。instructions 教模型区分「查询失败」和
「没有数据」是软约束，这一层是硬保证，两者共同构成技术设计 §16.2 的防线。

几个行为约定：

- **流开始之前**的错误（会话不存在、参数非法）是标准 HTTP 错误，带 JSON 错误
  信封，不是 SSE 事件。
- **流开始之后**的失败没法再改状态码，统一发一个 `error` 事件，助手消息落库为
  `failed`，已产出的部分内容保留。
- 客户端断开时助手消息落库为 `cancelled`，已产出内容保留。

## 错误码

响应体与 `internal-mcp-server` 一致：

```json
{ "error": { "code": "NOT_FOUND", "message": "conversation not found" } }
```

| Code | HTTP |
|---|---|
| `INVALID_ARGUMENT` | 400 |
| `UNAUTHENTICATED` | 401 |
| `PERMISSION_DENIED` | 403 |
| `NOT_FOUND` | 404 |
| `RATE_LIMITED` | 429 |
| `INTERNAL` | 500 |
| `UPSTREAM_ERROR` | 502 |
| `TIMEOUT` | 504 |

未捕获异常一律返回固定的 `internal error`，堆栈只进日志。

### MCP 侧的四层失败

`internal-mcp-server` 有四种互不相同的失败，`app/mcp/errors.py` 分别映射：

1. **HTTP 非 2xx、非 JSON-RPC** —— 401（service token / 身份头不对）、429（限流）等
2. **JSON-RPC error** —— tool 授权失败、tool 未注册（numeric code 通常是 0，别按数字判断）
3. **`isError: true`** —— **参数不合法**，此时没有 `structuredContent`；永不代表后端失败
4. **envelope `status: "ERROR"`** —— 下游业务失败，HTTP 200

第 4 类最需要小心：它长得像成功（200 + result），但绝不能表述成「没有相关记录」。
只有 `status: "SUCCESS"` 且结果为空才是真的没数据。

注意 Go 侧代码里**没有 `NO_DATA` 状态**（MCP 的 README 与实现不符），
`envelope.warnings` 也永远是 `[]`（没有任何地方 append）—— 别照那两处写代码。

## 测试

```bash
make test
```

测试跑文件型 SQLite，不需要本机有 MySQL。模型只用通用列类型，所以同一份
metadata 两边都能建表；`tests/test_migrations.py` 会在 SQLite 上跑一遍
`alembic upgrade head` 再和 `Base.metadata` 比对，防止「改了 model 忘了生成迁移」。

**Agent 测试不需要 LLM 也不需要 MCP**：`tests/test_agent_mapping.py` 用 SDK 自带的
`agents.testing.ScriptedModel` 造确定性模型，配 `tests/fake_mcp.py` 的进程内假
MCP server。SDK 的 tool 调用和 structured-content 处理路径是被真实执行的，
所以 envelope → Evidence 的整条映射链路都得到验证。

## 已知限制

- 每轮对话都新建一个 MCP 连接。`MCPServerStreamableHttp` 的 headers 在构造时
  固定，而 `X-Copilot-User-Id` 是 per-user 的，共享实例会串用户身份。
  代价是每轮多 3 个 MCP 往返（initialize / initialized / tools list），
  而 MCP 侧限流是 per-user 60/min、burst 10 —— **本地联调时建议把
  `internal-mcp-server` 的 `rate_limit` 调高**，否则连问两个问题就会 429。
  后续可用 `httpx_client_factory` 从 contextvar 取 header 来共享长连接
  （Go 侧是 stateless，服务端本身支持），等真实延迟数据出来再决定。
- Context 固定取最近 20 条消息，还没有做 §7.4 的摘要压缩。
- `evals/questions.yaml` 只有问题集，没有自动跑分。
