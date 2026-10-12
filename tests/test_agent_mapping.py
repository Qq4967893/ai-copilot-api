"""Agents SDK 事件 -> 我们的 AgentEvent 的映射。

用 SDK 自带的 ScriptedModel 造确定性模型 + 进程内假 MCP server，
不需要 LLM、不烧 token，但 SDK 的 tool 调用和 structured-content 处理
路径都是真实执行的。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from agents.testing import ScriptedModel, assistant_message, function_call
from pydantic import SecretStr
from structlog.testing import capture_logs

from app.agent.evidence import MAX_TURNS_CODE, UNPARSEABLE_CODE
from app.agent.operations import OperationsCopilotAgent
from app.agent.protocol import AgentEvent, AgentRequest
from app.auth.identity import Identity
from app.config.settings import (
    AgentSettings,
    AuthSettings,
    DatabaseSettings,
    DevUser,
    LlmSettings,
    ModelProfile,
    ModelSettings,
    Settings,
)
from tests.fake_mcp import FakeMCPServer, error_envelope, success_envelope

TOOL = "admin_api.deposits.statistics"
OTHER_TOOL = "admin_api.commission.statistics"
ARGS = {"start_time": "2026-09-01", "end_time": "2026-09-07"}


def make_settings(max_turns: int = 8, agent_timeout_seconds: float = 90.0) -> Settings:
    return Settings(
        database=DatabaseSettings(dsn=SecretStr("sqlite+aiosqlite:///:memory:")),
        auth=AuthSettings(dev_user=DevUser(user_id="10001", scopes=["admin.api.deposits.read"])),
        llm=LlmSettings(api_key=SecretStr("test-key")),
        model=ModelSettings(
            profiles={
                "default": ModelProfile(
                    model="test-model",
                    max_turns=max_turns,
                    agent_timeout_seconds=agent_timeout_seconds,
                )
            }
        ),
        agent=AgentSettings(impl="operations"),
    )


def make_request(text: str = "最近7天入金情况") -> AgentRequest:
    return AgentRequest(
        identity=Identity(user_id="10001", display_name="dev", scopes=[]),
        conversation_id="01J0000000000000000000000A",
        user_input=text,
        request_id="01J0000000000000000000000B",
    )


async def collect(
    *, script: list[Any], mcp: FakeMCPServer, settings: Settings | None = None
) -> list[AgentEvent]:
    settings = settings or make_settings()
    model = ScriptedModel(script)
    agent = OperationsCopilotAgent(
        settings,
        server_factory=lambda _req, _profile: mcp,
        model_factory=lambda _profile: model,
    )
    return [event async for event in agent.run(make_request())]


def kinds(events: list[AgentEvent]) -> list[str]:
    return [e.type for e in events]


def warnings_of(events: list[AgentEvent]) -> list[dict[str, Any]]:
    return [e.data["warning"] for e in events if e.type == "result.data" and "warning" in e.data]


def evidence_of(events: list[AgentEvent]) -> list[dict[str, Any]]:
    return [e.data for e in events if e.type == "result.evidence"]


def tool_script(tool: str = TOOL, args: dict[str, Any] | None = None) -> list[Any]:
    """先调一次 tool，再基于结果给出最终回答。"""
    return [
        {"output": [function_call(name=tool, arguments=json.dumps(args or ARGS), call_id="c1")]},
        {"output": [assistant_message("最近 7 天共 12 笔入金。")]},
    ]


# --------------------------------------------------------------------------
# 成功路径
# --------------------------------------------------------------------------


async def test_successful_tool_call_maps_to_events():
    mcp = FakeMCPServer(
        {TOOL: success_envelope({"total_count": 12, "customer_count": 8}, "deposits/statistics")}
    )
    events = await collect(script=tool_script(), mcp=mcp)

    assert "tool.started" in kinds(events)
    assert "tool.completed" in kinds(events)
    assert "tool.failed" not in kinds(events)
    # tool.started 必须在 tool.completed 之前
    assert kinds(events).index("tool.started") < kinds(events).index("tool.completed")

    started = next(e for e in events if e.type == "tool.started")
    assert started.data["tool"] == TOOL
    assert started.data["arguments"] == ARGS

    completed = next(e for e in events if e.type == "tool.completed")
    assert completed.data["tool"] == TOOL
    assert completed.data["empty"] is False
    assert completed.data["source"]["resource"] == "deposits/statistics"


async def test_evidence_comes_from_the_envelope_not_the_model():
    mcp = FakeMCPServer({TOOL: success_envelope({"total_count": 12}, "deposits/statistics")})
    # 模型的回答里完全不提数据来源
    events = await collect(script=tool_script(), mcp=mcp)

    items = evidence_of(events)
    assert len(items) == 1
    assert items[0]["type"] == "data"
    assert items[0]["source"] == "admin-api"
    assert items[0]["reference"] == "deposits/statistics"
    assert items[0]["observed_at"] is not None
    assert TOOL in items[0]["summary"]


async def test_assistant_text_is_streamed_as_deltas():
    mcp = FakeMCPServer({TOOL: success_envelope({"total_count": 1}, "deposits/statistics")})
    events = await collect(script=tool_script(), mcp=mcp)

    text = "".join(e.data["text"] for e in events if e.type == "message.delta")
    assert text == "最近 7 天共 12 笔入金。"


async def test_empty_result_is_success_not_failure():
    """SUCCESS + 空结果 ≠ 失败（技术设计 §16.2）。"""
    mcp = FakeMCPServer(
        {
            TOOL: success_envelope(
                {"items": []}, "deposits", {"limit": 20, "offset": 0, "total_count": 0}
            )
        }
    )
    events = await collect(script=tool_script(), mcp=mcp)

    assert "tool.failed" not in kinds(events)
    assert warnings_of(events) == []
    completed = next(e for e in events if e.type == "tool.completed")
    assert completed.data["empty"] is True
    assert completed.data["pagination"]["total_count"] == 0
    assert "结果为空" in evidence_of(events)[0]["summary"]


# --------------------------------------------------------------------------
# 失败路径 —— 本轮最重要的正确性属性
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("code", "retryable"),
    [
        ("UPSTREAM_ERROR", True),
        ("UPSTREAM_TIMEOUT", True),
        ("UNAUTHENTICATED", False),
        ("FORBIDDEN", False),
        ("INVALID_ARGUMENT", False),
        ("RATE_LIMITED", True),
    ],
)
async def test_tool_error_always_produces_a_warning(code: str, retryable: bool):
    """无论模型在回答里说了什么，status=ERROR 必定产出 warning。

    这是 §16.2 的应用层防线：instructions 教模型区分「失败」和「无数据」是
    软约束，这一层是硬保证。
    """
    mcp = FakeMCPServer({TOOL: error_envelope(code, "下游炸了", retryable)})
    # 模型偏偏睁眼说瞎话
    script = [
        {"output": [function_call(name=TOOL, arguments=json.dumps(ARGS), call_id="c1")]},
        {"output": [assistant_message("这个区间没有相关记录。")]},
    ]
    events = await collect(script=script, mcp=mcp)

    failed = [e for e in events if e.type == "tool.failed"]
    assert len(failed) == 1
    assert failed[0].data["code"] == code
    assert failed[0].data["retryable"] is retryable

    found = warnings_of(events)
    assert len(found) == 1
    assert found[0]["code"] == code

    # 失败必须留下 limitation 证据，而不是 data 证据
    items = evidence_of(events)
    assert [i["type"] for i in items] == ["limitation"]

    assert "tool.completed" not in kinds(events)


async def test_unparseable_tool_result_is_treated_as_failure():
    """形状不认识时按失败处理，绝不按「查询成功但没数据」。"""
    mcp = FakeMCPServer({TOOL: {"totally": "unexpected"}})
    events = await collect(script=tool_script(), mcp=mcp)

    failed = [e for e in events if e.type == "tool.failed"]
    assert len(failed) == 1
    assert failed[0].data["code"] == UNPARSEABLE_CODE
    assert warnings_of(events)[0]["code"] == UNPARSEABLE_CODE
    assert "tool.completed" not in kinds(events)


async def test_partial_failure_reports_both_sides():
    """一个成功一个失败时，证据和警告都要在（§16.3）。"""
    mcp = FakeMCPServer(
        {
            TOOL: success_envelope({"total_count": 12}, "deposits/statistics"),
            OTHER_TOOL: error_envelope("UPSTREAM_TIMEOUT", "admin-api request timed out", True),
        }
    )
    script = [
        {
            "output": [
                function_call(name=TOOL, arguments=json.dumps(ARGS), call_id="c1"),
                function_call(name=OTHER_TOOL, arguments=json.dumps(ARGS), call_id="c2"),
            ]
        },
        {"output": [assistant_message("入金查到了，佣金没查到。")]},
    ]
    events = await collect(script=script, mcp=mcp)

    assert kinds(events).count("tool.started") == 2
    assert kinds(events).count("tool.completed") == 1
    assert kinds(events).count("tool.failed") == 1

    types = sorted(i["type"] for i in evidence_of(events))
    assert types == ["data", "limitation"]

    found = warnings_of(events)
    assert len(found) == 1
    assert found[0]["code"] == "UPSTREAM_TIMEOUT"


async def test_parallel_calls_are_paired_by_call_id_not_order():
    """并行调用时 tool 名靠 call_id 配对，不能靠顺序。"""
    mcp = FakeMCPServer(
        {
            TOOL: success_envelope({"total_count": 1}, "deposits/statistics"),
            OTHER_TOOL: error_envelope("FORBIDDEN", "no permission"),
        }
    )
    script = [
        {
            "output": [
                function_call(name=TOOL, arguments=json.dumps(ARGS), call_id="call-a"),
                function_call(name=OTHER_TOOL, arguments=json.dumps(ARGS), call_id="call-b"),
            ]
        },
        {"output": [assistant_message("好的。")]},
    ]
    events = await collect(script=script, mcp=mcp)

    completed = next(e for e in events if e.type == "tool.completed")
    failed = next(e for e in events if e.type == "tool.failed")
    # 关键：失败的那条必须归到 OTHER_TOOL，不能张冠李戴
    assert completed.data["tool"] == TOOL
    assert failed.data["tool"] == OTHER_TOOL


# --------------------------------------------------------------------------
# 预算与连接失败
# --------------------------------------------------------------------------


async def test_max_turns_yields_a_warning_not_an_error():
    """步数用完是「回答可能不完整」，不是 INTERNAL。"""
    mcp = FakeMCPServer({TOOL: success_envelope({"total_count": 1}, "deposits/statistics")})
    # 每轮都只调 tool，永远不给最终回答 -> 必然撞上 max_turns
    script = [
        {"output": [function_call(name=TOOL, arguments=json.dumps(ARGS), call_id=f"c{i}")]}
        for i in range(6)
    ]
    events = await collect(script=script, mcp=mcp, settings=make_settings(max_turns=2))

    assert "error" not in kinds(events)
    codes = [w["code"] for w in warnings_of(events)]
    assert MAX_TURNS_CODE in codes


async def test_mcp_connect_failure_becomes_an_error_event():
    mcp = FakeMCPServer(raise_on_connect=RuntimeError("Connection lost"))
    events = await collect(script=tool_script(), mcp=mcp)

    assert kinds(events) == ["error"]
    assert events[0].data["code"] == "UPSTREAM_ERROR"
    # 不把内部细节抛给用户
    assert "Connection lost" not in events[0].data["message"]


class _SlowToolMCPServer(FakeMCPServer):
    """call_tool 挂住，用来触发 asyncio.timeout 的整轮预算超时。"""

    async def call_tool(
        self, tool_name: str, arguments: dict[str, Any] | None = None, *args: Any, **kwargs: Any
    ) -> Any:
        await asyncio.sleep(30)
        raise AssertionError("预算超时应先取消这个调用")


async def test_budget_timeout_maps_to_timeout_not_upstream_error():
    """asyncio.timeout 抛出的是消息为空的裸 TimeoutError，按文本匹配不到
    "timeout"，会兜成 UPSTREAM_ERROR —— 必须显式落成 TIMEOUT。"""
    mcp = _SlowToolMCPServer()
    events = await asyncio.wait_for(
        collect(
            script=tool_script(),
            mcp=mcp,
            settings=make_settings(agent_timeout_seconds=0.2),
        ),
        timeout=5,
    )

    # tool.started 先到，随后整轮预算超时
    assert kinds(events) == ["tool.started", "error"]
    assert events[-1].data["code"] == "TIMEOUT"
    assert events[-1].data["message"] == "查询超时，请稍后再试"
    # 超时路径同样要走 finally 的清理
    assert mcp.cleaned_up


async def test_mcp_rate_limit_maps_to_rate_limited():
    mcp = FakeMCPServer(raise_on_connect=RuntimeError("HTTP error 429"))
    events = await collect(script=tool_script(), mcp=mcp)

    assert events[0].type == "error"
    assert events[0].data["code"] == "RATE_LIMITED"


async def test_no_tool_call_still_streams_an_answer():
    """不需要查数据的问题（比如概念解释）也要正常走完。"""
    mcp = FakeMCPServer()
    events = await collect(
        script=[{"output": [assistant_message("入金是指客户向账户转入资金。")]}], mcp=mcp
    )

    assert "tool.started" not in kinds(events)
    assert "".join(e.data["text"] for e in events if e.type == "message.delta")
    assert warnings_of(events) == []


async def test_mcp_cleanup_runs_when_consumer_stops_early():
    """消费方提前结束时，MCP 连接必须被清理，且不能挂住。

    这模拟客户端断开：StreamingResponse 会 aclose() 这个异步生成器，
    我们的 finally 要在展开过程中完成 cleanup。
    """
    mcp = FakeMCPServer({TOOL: success_envelope({"total_count": 1}, "deposits/statistics")})
    settings = make_settings()
    agent = OperationsCopilotAgent(
        settings,
        server_factory=lambda _req, _profile: mcp,
        model_factory=lambda _profile: ScriptedModel(tool_script()),
    )

    stream = agent.run(make_request())
    first = await anext(stream)
    assert first.type in {"tool.started", "message.delta"}
    assert mcp.connected

    # 提前关掉生成器，等价于客户端断开
    await asyncio.wait_for(stream.aclose(), timeout=5)
    assert mcp.cleaned_up


# --------------------------------------------------------------------------
# 日志标记 —— 凭据过期 vs 用户没权限
# --------------------------------------------------------------------------


async def test_unauthenticated_envelope_is_flagged_as_credential_expiry_in_logs():
    """session cookie 过期也是 envelope UNAUTHENTICATED，日志里要能和
    「这个用户没权限」区分开。"""
    mcp = FakeMCPServer({TOOL: error_envelope("UNAUTHENTICATED", "session expired")})
    with capture_logs() as logs:
        events = await collect(script=tool_script(), mcp=mcp)

    assert [e.data["code"] for e in events if e.type == "tool.failed"] == ["UNAUTHENTICATED"]
    failed_logs = [entry for entry in logs if entry["event"] == "tool failed"]
    assert len(failed_logs) == 1
    assert failed_logs[0]["credential_expired"] is True


async def test_other_envelope_errors_are_not_flagged_as_credential_expiry():
    mcp = FakeMCPServer({TOOL: error_envelope("FORBIDDEN", "no permission")})
    with capture_logs() as logs:
        await collect(script=tool_script(), mcp=mcp)

    failed_logs = [entry for entry in logs if entry["event"] == "tool failed"]
    assert len(failed_logs) == 1
    assert failed_logs[0]["credential_expired"] is False
