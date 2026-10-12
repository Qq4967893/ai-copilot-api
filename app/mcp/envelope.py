"""internal-mcp-server 的 Tool Result Envelope。

字段与 Go 侧 internal/toolruntime/envelope.go 逐一对应。几个从源码核实的要点，
决定了下面解析器的写法：

- 下游业务失败是 HTTP 200 + JSON-RPC result + status="ERROR"，不是协议错误。
  所以判断成败**只看 status**。
- isError=true 只代表「参数不合法」（此时没有 structuredContent），
  永远不代表后端失败 —— 不能拿它判断业务结果。
- Go 侧代码里**没有 NO_DATA 状态**（MCP 的 README 与实现不符），别为它写分支。
- warnings 永远是 []，Go 侧没有任何地方 append。仍然解析，向前兼容。
- 数字经过 float64 往返（1234.50 -> 1234.5），commission_amount 是字符串。
  不要在本服务里对这些值做算术。
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ToolSource(BaseModel):
    model_config = ConfigDict(extra="ignore")

    service: str = ""
    resource: str = ""
    observed_at: datetime | None = None


class ToolErrorDetail(BaseModel):
    model_config = ConfigDict(extra="ignore")

    code: str = "UPSTREAM_ERROR"
    message: str = ""
    retryable: bool = False


class ToolPagination(BaseModel):
    model_config = ConfigDict(extra="ignore")

    limit: int = 0
    offset: int = 0
    total_count: int = 0


class ToolEnvelope(BaseModel):
    model_config = ConfigDict(extra="ignore")

    status: Literal["SUCCESS", "ERROR"]
    data: Any = None
    source: ToolSource | None = None
    pagination: ToolPagination | None = None
    warnings: list[str] = Field(default_factory=list)
    error: ToolErrorDetail | None = None

    @property
    def failed(self) -> bool:
        return self.status == "ERROR"

    @property
    def is_empty(self) -> bool:
        """SUCCESS 且没有实质内容。

        注意这和 failed 是完全不同的两件事（技术设计 §16.2）——
        调用方绝不能把 failed 当成 is_empty。
        """
        if self.failed:
            return False
        data = self.data
        if data is None:
            return True
        if isinstance(data, dict):
            items = data.get("items")
            if isinstance(items, list):
                return not items
            return not data
        if isinstance(data, list):
            return not data
        return False


def parse_tool_output(raw: Any) -> ToolEnvelope | None:
    """尽力从 Agents SDK 的 tool output 里还原 envelope。

    use_structured_content=True 时，SDK 走的是 `json.dumps(structured_content)`，
    所以最常见的输入是一个 JSON **字符串**（不是 dict）。其余形状是兜底。

    形状无法识别时返回 None —— 调用方必须按**失败**处理，不能按成功。
    把未知形状当成「查询成功但没数据」正是 §16.2 明令禁止的那类错误。
    """
    payload = _coerce_mapping(raw)
    if payload is None:
        return None
    try:
        return ToolEnvelope.model_validate(payload)
    except Exception:
        return None


def _coerce_mapping(raw: Any) -> dict[str, Any] | None:
    if isinstance(raw, dict):
        # SDK 在非 structured 路径下会给 {"type": "text", "text": "<json>"}
        if raw.get("type") == "text" and isinstance(raw.get("text"), str):
            return _load_json_object(raw["text"])
        return raw
    if isinstance(raw, str):
        return _load_json_object(raw)
    if isinstance(raw, list):
        # 多个 content block：取第一个能解析出 envelope 的
        for item in raw:
            found = _coerce_mapping(item)
            if found is not None and "status" in found:
                return found
        return None
    # pydantic 模型等带 model_dump 的对象
    dump = getattr(raw, "model_dump", None)
    if callable(dump):
        try:
            dumped = dump()
        except Exception:
            return None
        return dumped if isinstance(dumped, dict) else None
    return None


def _load_json_object(text: str) -> dict[str, Any] | None:
    try:
        loaded = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    return loaded if isinstance(loaded, dict) else None
