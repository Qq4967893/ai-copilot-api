# Agent Eval

MVP 阶段尚未实现，此目录为占位。

Eval 是生产变更的质量 Gate（技术设计 §25）：Prompt、Model、Tool Schema
发生重要变化时必须跑回归 Eval，传统单元测试覆盖不了 Agent 的行为质量。

需要覆盖的维度（§25.3）：

- Tool selection 正确性
- 不必要的 Tool call
- Tool 参数正确性
- Permission handling
- Tool failure handling
- No-data vs failure 区分
- Final answer groundedness
- Evidence completeness
- Sensitive data handling
