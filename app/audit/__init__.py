"""审计（MVP 阶段仅占位）。

按项目根 README 第 3 条，MVP 不实现业务 Audit 的具体代码。
agent_run 表已经建好（app/conversation/models.py 的 AgentRun），本轮只写
status / latency_ms / error_code。

接入时要补的字段（技术设计 §18.1）：
    model_profile / model_version / prompt_version / selected_tools /
    tool_call_ids / token_usage / cost metadata / final_response reference

Tool Audit（§18.2）由 internal-mcp-server 负责，本服务不重复记录。

§18.3：默认只记录 metadata，不记录未经过滤的业务 payload；确需记录时做
字段级脱敏 + 限定访问权限 + 设置 retention。
"""
