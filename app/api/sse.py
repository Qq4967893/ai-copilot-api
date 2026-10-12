"""SSE 帧编码。

Web 与 API 之间用 SSE event framing（技术设计 §4.6）。格式简单到不值得引入
sse-starlette：一个 event 行 + 一个 data 行 + 空行。
"""

from __future__ import annotations

import json
from typing import Any

# 反向代理相关的响应头。X-Accel-Buffering 是给 nginx 的 —— 不加的话流式响应
# 会被攒在代理里成批下发，前端看到的就不是增量了。
SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}
SSE_MEDIA_TYPE = "text/event-stream"


def sse_frame(event: str, data: dict[str, Any]) -> str:
    payload = json.dumps(data, ensure_ascii=False, default=str)
    # data 里不会出现裸换行（json.dumps 会转义），所以单行 data 是安全的。
    return f"event: {event}\ndata: {payload}\n\n"
