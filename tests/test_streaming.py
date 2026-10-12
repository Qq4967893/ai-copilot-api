"""SSE 流式链路。

重点不是 echo 的内容，而是这条管道的行为：事件顺序、落库时机、
中途失败、客户端断开。这些在换成真实 Agent 之后完全不变。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, MutableMapping
from typing import Any

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.agent.protocol import AgentEvent, AgentRequest
from app.api.sse import SSE_MEDIA_TYPE


def parse_sse(raw: str) -> list[tuple[str, dict]]:
    events = []
    for block in raw.strip().split("\n\n"):
        if not block.strip():
            continue
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        events.append((lines["event"], json.loads(lines["data"])))
    return events


async def create_conversation(client: AsyncClient) -> str:
    return (await client.post("/api/conversations", json={})).json()["id"]


async def test_event_sequence_and_content(client: AsyncClient):
    cid = await create_conversation(client)
    resp = await client.post(f"/api/conversations/{cid}/messages", json={"content": "你好"})

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith(SSE_MEDIA_TYPE)
    assert resp.headers["x-accel-buffering"] == "no"

    events = parse_sse(resp.text)
    names = [name for name, _ in events]
    assert names[0] == "message.started"
    assert names[-1] == "message.completed"
    assert set(names[1:-1]) == {"message.delta"}

    message_id = events[0][1]["message_id"]
    assert events[-1][1]["message_id"] == message_id

    streamed = "".join(data["text"] for name, data in events if name == "message.delta")
    assert streamed == "（echo）你好"
    assert events[-1][1]["content"] == streamed


async def test_messages_are_persisted(client: AsyncClient):
    cid = await create_conversation(client)
    await client.post(f"/api/conversations/{cid}/messages", json={"content": "你好"})

    messages = (await client.get(f"/api/conversations/{cid}")).json()["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[0]["content"] == "你好"
    assert messages[1]["content"] == "（echo）你好"
    assert messages[1]["status"] == "completed"


async def test_multi_turn_keeps_order(client: AsyncClient):
    cid = await create_conversation(client)
    for text in ("第一问", "第二问"):
        await client.post(f"/api/conversations/{cid}/messages", json={"content": text})

    messages = (await client.get(f"/api/conversations/{cid}")).json()["messages"]
    assert [m["content"] for m in messages] == [
        "第一问",
        "（echo）第一问",
        "第二问",
        "（echo）第二问",
    ]


async def test_unknown_conversation_is_a_plain_404_not_an_sse_error(client: AsyncClient):
    """流还没开始，错误就该是标准 JSON 信封，而不是 SSE error 事件。"""
    resp = await client.post("/api/conversations/NOPE/messages", json={"content": "hi"})
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"
    assert not resp.headers["content-type"].startswith(SSE_MEDIA_TYPE)


async def test_empty_content_is_rejected_before_streaming(client: AsyncClient):
    cid = await create_conversation(client)
    resp = await client.post(f"/api/conversations/{cid}/messages", json={"content": ""})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "INVALID_ARGUMENT"


async def test_oversized_content_is_rejected(client: AsyncClient):
    cid = await create_conversation(client)
    resp = await client.post(f"/api/conversations/{cid}/messages", json={"content": "x" * 4001})
    assert resp.status_code == 400


class _ExplodingAgent:
    """跑出几个 delta 之后抛异常。"""

    async def run(self, request: AgentRequest) -> AsyncIterator[AgentEvent]:
        yield AgentEvent(type="message.delta", data={"text": "部分"})
        raise RuntimeError("agent exploded")


async def test_midstream_failure_emits_error_event(app: FastAPI):
    app.state.agent = _ExplodingAgent()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        cid = await create_conversation(client)
        resp = await client.post(f"/api/conversations/{cid}/messages", json={"content": "hi"})

        # 头已经发出去了，所以状态码仍是 200，失败通过 error 事件表达。
        assert resp.status_code == 200
        events = parse_sse(resp.text)
        names = [n for n, _ in events]
        assert names == ["message.started", "message.delta", "error"]
        assert events[-1][1] == {"code": "INTERNAL", "message": "internal error"}
        assert "exploded" not in resp.text  # 内部细节不外泄

        messages = (await client.get(f"/api/conversations/{cid}")).json()["messages"]
        assistant = messages[1]
        assert assistant["status"] == "failed"
        # 已经产出的部分内容仍然保留下来
        assert assistant["content"] == "部分"


class _SlowAgent:
    async def run(self, request: AgentRequest) -> AsyncIterator[AgentEvent]:
        yield AgentEvent(type="message.delta", data={"text": "开头"})
        await asyncio.sleep(10)
        yield AgentEvent(type="message.delta", data={"text": "永远不会到达"})


async def test_client_disconnect_marks_message_cancelled(app: FastAPI):
    """客户端断开时，已产出的内容要落库并标成 cancelled。

    不能用 httpx 的 ASGITransport 模拟断开 —— 它会把 app 跑到底，提前 break 只是
    不再读了。这里直接驱动 ASGI 并发一个真实的 http.disconnect，跟 uvicorn 一样。

    注意 receive() 必须在发完 body 之后挂起：Starlette 的 StreamingResponse 会
    起一个协程循环 await receive() 等断开信号，如果 receive 每次都立即返回，
    那个循环就会空转并饿死整个事件循环。
    """
    app.state.agent = _SlowAgent()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        cid = await create_conversation(client)

        path = f"/api/conversations/{cid}/messages"
        body = json.dumps({"content": "hi"}).encode()
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "root_path": "",
            "headers": [
                (b"host", b"test"),
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
            "client": ("127.0.0.1", 1234),
            "server": ("test", 80),
        }

        received: list[bytes] = []
        body_sent = asyncio.Event()
        disconnected = asyncio.Event()

        async def receive() -> MutableMapping[str, Any]:
            if not body_sent.is_set():
                body_sent.set()
                return {"type": "http.request", "body": body, "more_body": False}
            await disconnected.wait()
            return {"type": "http.disconnect"}

        async def send(message: MutableMapping[str, Any]) -> None:
            if message["type"] == "http.response.body" and message.get("body"):
                received.append(message["body"])

        task = asyncio.create_task(app(scope, receive, send))

        async def wait_for_first_delta() -> None:
            while "开头" not in b"".join(received).decode(errors="ignore"):
                if task.done():
                    task.result()  # 让 app 里的真实异常浮上来
                    raise AssertionError("app finished without emitting the delta")
                await asyncio.sleep(0.01)

        await asyncio.wait_for(wait_for_first_delta(), timeout=5)
        disconnected.set()
        await asyncio.wait_for(asyncio.shield(task), timeout=5)

        # 收尾跑在独立 task 里，等它落库
        async def wait_for_finalize() -> dict:
            while True:
                messages = (await client.get(f"/api/conversations/{cid}")).json()["messages"]
                if messages[1]["status"] != "pending":
                    return messages[1]
                await asyncio.sleep(0.01)

        assistant = await asyncio.wait_for(wait_for_finalize(), timeout=5)
        assert assistant["status"] == "cancelled"
        # 断开前已经产出的内容要保住，不能丢
        assert assistant["content"] == "开头"
        # 客户端没收到后续事件
        assert "永远不会到达" not in b"".join(received).decode(errors="ignore")


async def test_agent_run_is_recorded(client: AsyncClient):
    from sqlalchemy import select

    from app.conversation.models import AgentRun
    from app.db.session import session_factory

    cid = await create_conversation(client)
    await client.post(f"/api/conversations/{cid}/messages", json={"content": "hi"})

    async with session_factory()() as session:
        runs = list((await session.execute(select(AgentRun))).scalars())

    assert len(runs) == 1
    assert runs[0].status == "completed"
    assert runs[0].conversation_id == cid
    assert runs[0].latency_ms is not None
    assert runs[0].error_code is None


class _EvidenceAgent:
    """模拟真实 Agent 会发出的完整事件序列，验证 HTTP 层的收集与落库。"""

    async def run(self, request: AgentRequest) -> AsyncIterator[AgentEvent]:
        yield AgentEvent(type="tool.started", data={"tool": "admin_api.deposits.statistics"})
        yield AgentEvent(
            type="result.evidence",
            data={
                "type": "data",
                "source": "admin-api",
                "reference": "deposits/statistics",
                "observed_at": "2026-09-22T03:14:15Z",
                "summary": "admin_api.deposits.statistics",
            },
        )
        yield AgentEvent(type="tool.completed", data={"tool": "admin_api.deposits.statistics"})
        yield AgentEvent(type="tool.failed", data={"tool": "admin_api.commission.statistics"})
        yield AgentEvent(
            type="result.data",
            data={"warning": {"code": "UPSTREAM_TIMEOUT", "message": "佣金查询超时"}},
        )
        yield AgentEvent(type="message.delta", data={"text": "入金查到了，佣金没查到。"})


async def test_evidence_and_warnings_are_persisted_to_payload(app: FastAPI):
    """Evidence / Warnings 必须落到 message.payload（技术设计 §23.2）。"""
    app.state.agent = _EvidenceAgent()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        cid = await create_conversation(client)
        resp = await client.post(f"/api/conversations/{cid}/messages", json={"content": "对比一下"})

        events = parse_sse(resp.text)
        # warnings 随 message.completed 一起回给前端
        completed = next(data for name, data in events if name == "message.completed")
        assert completed["warnings"] == [{"code": "UPSTREAM_TIMEOUT", "message": "佣金查询超时"}]

        messages = (await client.get(f"/api/conversations/{cid}")).json()["messages"]
        payload = messages[1]["payload"]
        assert payload is not None
        assert len(payload["evidence"]) == 1
        assert payload["evidence"][0]["reference"] == "deposits/statistics"
        assert payload["warnings"][0]["code"] == "UPSTREAM_TIMEOUT"


async def test_payload_is_null_when_there_is_no_evidence(client: AsyncClient):
    """EchoAgent 不产出证据时 payload 保持 null，不写空壳。"""
    cid = await create_conversation(client)
    await client.post(f"/api/conversations/{cid}/messages", json={"content": "hi"})
    messages = (await client.get(f"/api/conversations/{cid}")).json()["messages"]
    assert messages[1]["payload"] is None


class _ErroringAgent:
    """Agent 产出终止性 error 事件（例如 LLM 或 MCP 不可达）。"""

    async def run(self, request: AgentRequest) -> AsyncIterator[AgentEvent]:
        yield AgentEvent(type="message.delta", data={"text": "部分"})
        yield AgentEvent(
            type="error", data={"code": "UPSTREAM_ERROR", "message": "查询服务暂时不可用"}
        )


async def test_agent_error_event_marks_message_failed(app: FastAPI):
    """error 是终止事件：不能再发 message.completed，消息要落 failed。

    否则这条空回答会被当成正常历史喂回下一轮，agent_run 的成功率也会失真。
    """
    app.state.agent = _ErroringAgent()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        cid = await create_conversation(client)
        resp = await client.post(f"/api/conversations/{cid}/messages", json={"content": "hi"})

        names = [n for n, _ in parse_sse(resp.text)]
        assert names == ["message.started", "message.delta", "error"]
        assert "message.completed" not in names

        messages = (await client.get(f"/api/conversations/{cid}")).json()["messages"]
        assert messages[1]["status"] == "failed"
        assert messages[1]["content"] == "部分"
