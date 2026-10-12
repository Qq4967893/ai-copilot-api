"""MCP Client（尚未实现）。

职责（技术设计 §2.4 / §8）：以 Streamable HTTP 连接 internal-mcp-server，
发现并调用已注册的 Tool，做 Tool allowlist 与 runtime policy，把 Tool Result
的错误信封映射成本服务的错误语义。

计划模块：
    client.py   MCP Client 与连接复用；Tool 发现与调用
    auth.py     Service Auth + Delegated User Identity 头
                （已有实现：app/auth/identity.py 的 mcp_headers()）
    errors.py   MCP 错误码 -> 本服务 ErrorCode 的映射

关键约束：
- internal-mcp-server 把下游失败作为 status="ERROR" 的 Envelope 返回，
  不是 MCP 协议错误。必须严格区分 SUCCESS+空结果 与 DOWNSTREAM_ERROR /
  TIMEOUT / PERMISSION_DENIED（技术设计 §16.2），不能都当成「没有数据」。
- Tool Raw Result 不直接返回前端，必须经过 Response Mapping 和敏感数据过滤（§23.3）。
- Envelope 字段与 Go 侧逐字段一致：
  status / data / source / pagination / warnings / error{code,message,retryable}
"""
