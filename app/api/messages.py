"""Message API（技术设计 §22.2）—— 返回 SSE 流式响应。

这里的会话生命周期是整条链路上最容易出错的地方，所以刻意拆成三段：

1. **请求处理阶段**（用 Depends 注入的 session）：校验会话归属、落用户消息、
   落一条 pending 的助手消息、commit。这一段的错误是正常的 HTTP 错误
   —— 404 / 400 会带着标准 error 信封返回，而不是变成一个 SSE error 事件。

2. **流式阶段**（不持有任何 DB session）：跑 Agent、转发事件、累积文本。
   刻意不在这里握着数据库连接 —— 真实 Agent 一次 Run 可能几十秒，
   把连接占住会拖垮连接池。

3. **收尾阶段**：另开一个短 session 更新助手消息并记录 agent_run。

顺带避开了 FastAPI 的一个坑：Depends 注入的 session 在响应*开始*时就被清理，
而 StreamingResponse 的 body 生成器是在那之后才执行的，在生成器里用它必然踩到
已关闭的 session。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from typing import Any

import structlog
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app.agent.protocol import AgentEvent, AgentRequest
from app.api.schemas import CreateMessageRequest
from app.api.sse import SSE_HEADERS, SSE_MEDIA_TYPE, sse_frame
from app.auth.dependency import AgentDep, IdentityDep, SessionDep
from app.conversation.models import MessageStatus, RunStatus
from app.conversation.service import ConversationService
from app.db.session import session_factory
from app.errors import ErrorCode
from app.observability.middleware import REQUEST_ID_HEADER

router = APIRouter(prefix="/api/conversations", tags=["messages"])
log = structlog.get_logger()

# 收尾 task 的强引用。事件循环只持弱引用，不存在这里的话 task 可能在跑完前被 GC。
_pending_finalizers: set[asyncio.Task[None]] = set()


@router.post("/{conversation_id}/messages")
async def create_message(
    conversation_id: str,
    body: CreateMessageRequest,
    request: Request,
    identity: IdentityDep,
    session: SessionDep,
    agent: AgentDep,
) -> StreamingResponse:
    service = ConversationService(session)
    conversation = await service.require_conversation(conversation_id, identity=identity)

    history = await service.recent_messages(conversation.id)
    await service.append_user_message(conversation=conversation, content=body.content)
    assistant = await service.start_assistant_message(conversation_id=conversation.id)
    # 在流开始之前就 commit：客户端中途断开也不会留下半条记录。
    await session.commit()

    assistant_id = assistant.id
    request_id = request.headers.get(REQUEST_ID_HEADER, "")
    agent_request = AgentRequest(
        identity=identity,
        conversation_id=conversation.id,
        history=history,
        user_input=body.content,
        request_id=request_id,
    )

    async def finalize(
        *,
        content: str,
        message_status: MessageStatus,
        run_status: RunStatus,
        latency_ms: int,
        error_code: str | None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        async with session_factory()() as own_session:
            own_service = ConversationService(own_session)
            message = await own_service.messages.get(assistant_id)
            if message is not None:
                await own_service.finish_assistant_message(
                    message, content=content, status=message_status, payload=payload
                )
            await own_service.record_run(
                identity=identity,
                conversation_id=conversation_id,
                message_id=assistant_id,
                status=run_status,
                latency_ms=latency_ms,
                error_code=error_code,
            )
            await own_session.commit()

    async def stream() -> AsyncIterator[str]:
        started = time.perf_counter()
        chunks: list[str] = []
        # Evidence 和 Warnings 由 Agent 从真实 tool 调用推导后发过来，
        # 这里只负责收集并落到 message.payload（技术设计 §23.2）。
        evidence: list[dict[str, Any]] = []
        warnings: list[dict[str, Any]] = []

        def elapsed_ms() -> int:
            return int((time.perf_counter() - started) * 1000)

        def payload() -> dict[str, Any] | None:
            if not evidence and not warnings:
                return None
            return {"evidence": evidence, "warnings": warnings}

        yield sse_frame("message.started", {"message_id": assistant_id})

        agent_error: dict[str, Any] | None = None

        try:
            async for event in agent.run(agent_request):
                if event.type == "message.delta":
                    chunks.append(str(event.data.get("text", "")))
                elif event.type == "result.evidence":
                    evidence.append(event.data)
                elif event.type == "result.data" and "warning" in event.data:
                    warnings.append(event.data["warning"])
                elif event.type == "error":
                    # error 是终止事件：记下来，循环结束后按失败收尾，
                    # 不能再发 message.completed。
                    agent_error = event.data
                yield sse_frame(event.type, event.data)
        except asyncio.CancelledError:
            # 客户端断开。这里绝对不能 await —— 此刻 Starlette 正在 aclose() 这个
            # 异步生成器，在关闭过程中挂起会直接死锁。把收尾丢给一个独立 task。
            log.info("stream cancelled", message_id=assistant_id)
            finalizer = asyncio.create_task(
                finalize(
                    content="".join(chunks),
                    message_status=MessageStatus.CANCELLED,
                    run_status=RunStatus.CANCELLED,
                    latency_ms=elapsed_ms(),
                    error_code=None,
                    payload=payload(),
                )
            )
            _pending_finalizers.add(finalizer)
            finalizer.add_done_callback(_pending_finalizers.discard)
            raise
        except Exception as exc:
            # 响应头已经发出去了，没法再返 500，只能发一个 error 事件。
            log.exception("agent run failed", message_id=assistant_id, error=str(exc))
            await finalize(
                content="".join(chunks),
                message_status=MessageStatus.FAILED,
                run_status=RunStatus.FAILED,
                latency_ms=elapsed_ms(),
                error_code=str(ErrorCode.INTERNAL),
                payload=payload(),
            )
            yield sse_frame("error", {"code": str(ErrorCode.INTERNAL), "message": "internal error"})
            return

        content = "".join(chunks)

        if agent_error is not None:
            # 失败的 run 必须落成 failed。否则这条空回答会被当成正常历史
            # 喂回下一轮（app/agent/context.py 靠 status 过滤），
            # agent_run 的审计和成功率指标也会失真。
            await finalize(
                content=content,
                message_status=MessageStatus.FAILED,
                run_status=RunStatus.FAILED,
                latency_ms=elapsed_ms(),
                error_code=str(agent_error.get("code", ErrorCode.INTERNAL)),
                payload=payload(),
            )
            return

        await finalize(
            content=content,
            message_status=MessageStatus.COMPLETED,
            run_status=RunStatus.COMPLETED,
            latency_ms=elapsed_ms(),
            error_code=None,
            payload=payload(),
        )
        yield sse_frame(
            "message.completed",
            {"message_id": assistant_id, "content": content, "warnings": warnings},
        )

    headers = dict(SSE_HEADERS)
    if request_id:
        headers[REQUEST_ID_HEADER] = request_id
    return StreamingResponse(stream(), media_type=SSE_MEDIA_TYPE, headers=headers)


__all__ = ["AgentEvent", "router"]
