"""模型配置与 Provider（尚未实现）。

技术设计 §6.6：模型配置不得硬编码在业务逻辑中，通过 model profile 管理。

计划模块：
    provider.py  OpenAI client 构造；模型调用
    config.py    model profile: default / fast / complex
                 MVP 可以只有 default，但保留配置结构

Runtime Budget（§6.5）也归这里读取：
    max_turns / max_tool_calls / agent_timeout / tool_timeout /
    max_tool_result_size / max_context_size / model / model_parameters

模型升级必须过 Eval，不因为新模型发布就自动切换生产环境（§6.6）。
"""

from app.model.config import active_profile
from app.model.provider import build_model

__all__ = ["active_profile", "build_model"]
