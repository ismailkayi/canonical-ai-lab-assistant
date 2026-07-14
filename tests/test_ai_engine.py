import requests

from lab_ai_assistant.ai_engine import AIEngine


def test_native_tool_result_preserves_protocol(config) -> None:
    engine = AIEngine(config)
    engine.conversation_history.append({"role": "user", "content": "list labs"})
    engine._record_assistant_response(
        {
            "action": "list_environments",
            "parameters": {},
            "_tool_call_id": "call-123",
            "_assistant_message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call-123",
                        "type": "function",
                        "function": {
                            "name": "list_environments",
                            "arguments": "{}",
                        },
                    }
                ],
            },
        }
    )

    captured = {}
    engine._get_default_system_prompt = lambda *args, **kwargs: "system"

    def fake_call(messages, include_tools=True):
        captured["messages"] = messages
        return {"content": "One lab is active"}

    engine._call_inference = fake_call
    engine.feed_tool_result("list_environments", "lab_microcloud")

    assert [message["role"] for message in captured["messages"]] == [
        "system",
        "user",
        "assistant",
        "tool",
    ]
    assert captured["messages"][-1]["tool_call_id"] == "call-123"


def test_cancelled_native_call_is_closed(config) -> None:
    engine = AIEngine(config)
    engine._pending_tool_call_id = "call-cancel"

    engine.cancel_pending_tool_call("deploy_microcloud", "User rejected the plan")

    assert engine._pending_tool_call_id is None
    assert engine.conversation_history[-1]["role"] == "tool"
    assert engine.conversation_history[-1]["tool_call_id"] == "call-cancel"


def test_inference_retry_waits_for_restarted_backend(config, monkeypatch) -> None:
    engine = AIEngine(config)
    config.max_retries = 2
    config.inference_restart_timeout = 10
    successful_response = object()
    post_attempts = []
    health_checks = []

    def fake_post(*args, **kwargs):
        post_attempts.append((args, kwargs))
        if len(post_attempts) == 1:
            raise requests.ConnectionError("backend restarting")
        return successful_response

    class HealthyResponse:
        status_code = 200

    def fake_get(url, timeout):
        health_checks.append((url, timeout))
        return HealthyResponse()

    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr(requests, "get", fake_get)

    response = engine._post_inference("/v1/chat/completions", {"messages": []})

    assert response is successful_response
    assert len(post_attempts) == 2
    assert health_checks[0][0] == f"{engine.base_url}/health"


def test_rejected_lifecycle_result_is_described_as_failure(config) -> None:
    engine = AIEngine(config)
    captured = {}
    engine._get_default_system_prompt = lambda *args, **kwargs: "system"

    def fake_call(messages, include_tools=True):
        captured["messages"] = messages
        return {"content": "Request rejected"}

    engine._call_inference = fake_call
    engine.feed_tool_result(
        "deploy_microcloud",
        "Error: tool request rejected by schema validation: nodes must be an integer",
    )

    observation = captured["messages"][-1]["content"]
    assert "completed with a failure" in observation
    assert "completed successfully" not in observation
