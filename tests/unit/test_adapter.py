import json

import httpx
import pytest
from pydantic import SecretStr

from waypoint_agent.config import Settings
from waypoint_agent.models.openai_compatible import OpenAICompatibleModel
from waypoint_agent.schemas import Findings
from waypoint_agent.tools.mock import mock_tools


async def test_http_adapter_completes_through_same_runtime(state, script, make_runtime):
    actions = iter(script)

    def handler(request):
        action = next(actions)
        function = (
            {"name": action["call"]["name"], "arguments": json.dumps(action["call"]["arguments"])}
            if action["kind"] == "call_tool"
            else {"name": "propose_completion", "arguments": json.dumps(action["findings"])}
        )
        return httpx.Response(
            200, json={"choices": [{"message": {"tool_calls": [{"function": function}]}}]}
        )

    adapter = OpenAICompatibleModel(
        Settings(_env_file=None, api_key=SecretStr("test-only")),
        transport=httpx.MockTransport(handler),
    )
    checkpoint = await make_runtime(model=adapter).run(state)
    assert checkpoint.state.task.status == "completed"
    assert checkpoint.state.findings.unread_email_ids == ["mail-alice-unread"]


async def test_http_adapter_uses_strict_function_calling_and_records_usage(data, state, script):
    def handler(request):
        body = json.loads(request.content)
        assert request.url == "https://provider.example/v1/chat/completions"
        assert body["parallel_tool_calls"] is False
        assert body["tool_choice"] == "required"
        assert all(t["function"]["strict"] for t in body["tools"])
        assert body["tools"][-1]["function"]["parameters"] == Findings.model_json_schema()
        assert "output_schema" in body["messages"][1]["content"]
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "function": {
                                        "name": "search_calendar",
                                        "arguments": json.dumps(script[0]["call"]["arguments"]),
                                    },
                                }
                            ]
                        }
                    }
                ],
                "usage": {"prompt_tokens": 40, "completion_tokens": 10, "total_tokens": 50},
            },
        )

    adapter = OpenAICompatibleModel(
        Settings(
            _env_file=None, api_key=SecretStr("test-only"), base_url="https://provider.example/v1"
        ),
        transport=httpx.MockTransport(handler),
    )
    reply = await adapter.next_action(state, mock_tools(data).describe())
    assert reply.action == script[0]
    assert reply.usage["total_tokens"] == 50


@pytest.mark.parametrize(
    "message",
    [
        {"refusal": "Cannot answer"},
        {"tool_calls": [{"function": {"name": "read_email", "arguments": "bad JSON"}}]},
        {"tool_calls": [{"function": {"name": "propose_completion", "arguments": "{}"}}]},
    ],
)
async def test_invalid_provider_payload_reaches_runtime(message, state, make_runtime):
    adapter = OpenAICompatibleModel(
        Settings(_env_file=None, api_key=SecretStr("test-only")),
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"choices": [{"message": message}]})
        ),
    )
    runtime = make_runtime(model=adapter, max_errors=1)
    checkpoint = await runtime.run(state)
    assert checkpoint.state.task.status == "failed"
    _, events = await runtime.repository.load(state.task.id)
    assert any(e.kind == "invalid_action" for e in events)
    assert all(e.usage is None for e in events)
