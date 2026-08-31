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
    # Pin the endpoint so a locally installed inference snap cannot change the
    # probed URLs and make this test environment-dependent.
    config.inference_auto_discovery = False
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

        @staticmethod
        def json():
            return {"data": [{"id": config.inference_model}]}

    def fake_get(url, timeout):
        health_checks.append((url, timeout))
        return HealthyResponse()

    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr(requests, "get", fake_get)

    response = engine._post_inference("/v1/chat/completions", {"messages": []})

    assert response is successful_response
    assert len(post_attempts) == 2
    # The retry must be gated on a readiness probe against the configured base.
    assert health_checks, "expected a readiness probe before retrying"
    assert all(url.startswith(engine.base_url) for url, _timeout in health_checks)


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


class _FakeResponse:
    """Minimal stand-in for a requests.Response from an OpenAI-style backend."""

    def __init__(self, message: dict, finish_reason: str = "stop") -> None:
        self.status_code = 200
        self._message = message
        self._finish_reason = finish_reason

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {"choices": [{"message": self._message, "finish_reason": self._finish_reason}]}


def _prepared_engine(config) -> AIEngine:
    config.inference_auto_discovery = False
    engine = AIEngine(config)
    engine._api_style = "openai"
    engine._runtime_discovery_attempted = True
    engine._resolved_model = config.inference_model
    return engine


def test_thinking_is_disabled_by_default(config) -> None:
    engine = _prepared_engine(config)
    captured: dict = {}

    def fake_post(payload, stream=False):
        captured["payload"] = payload
        return _FakeResponse({"content": "hello"})

    engine._post_openai_chat = fake_post
    engine._call_inference([{"role": "user", "content": "hi"}], include_tools=False)

    assert captured["payload"]["chat_template_kwargs"] == {"enable_thinking": False}


def test_thinking_can_be_enabled_explicitly(config) -> None:
    config.inference_enable_thinking = True
    engine = _prepared_engine(config)
    captured: dict = {}

    def fake_post(payload, stream=False):
        captured["payload"] = payload
        return _FakeResponse({"content": "hello"})

    engine._post_openai_chat = fake_post
    engine._call_inference([{"role": "user", "content": "hi"}], include_tools=False)

    assert "chat_template_kwargs" not in captured["payload"]


def test_truncated_reasoning_is_never_shown_as_the_answer(config) -> None:
    """A thinking transcript is an internal monologue, not a user-facing answer."""
    engine = _prepared_engine(config)
    monologue = "Thinking Process: 1. **Analyze the request:** The user wants a lab and"

    engine._post_openai_chat = lambda payload, stream=False: _FakeResponse(
        {"content": "", "reasoning_content": monologue}, finish_reason="length"
    )
    result = engine._call_inference([{"role": "user", "content": "hi"}], include_tools=False)

    assert result.get("error") is True
    assert monologue not in result["content"]
    assert "INFERENCE_MAX_OUTPUT_TOKENS" in result["content"]


def test_truncated_reasoning_retries_once_with_thinking_disabled(config) -> None:
    config.inference_enable_thinking = True
    engine = _prepared_engine(config)
    payloads: list[dict] = []

    def fake_post(payload, stream=False):
        payloads.append(payload)
        if len(payloads) == 1:
            return _FakeResponse(
                {"content": "", "reasoning_content": "thinking..."}, finish_reason="length"
            )
        return _FakeResponse({"content": "Here is the answer"})

    engine._post_openai_chat = fake_post
    result = engine._call_inference([{"role": "user", "content": "hi"}], include_tools=False)

    assert len(payloads) == 2
    assert "chat_template_kwargs" not in payloads[0]
    assert payloads[1]["chat_template_kwargs"] == {"enable_thinking": False}
    assert result["content"] == "Here is the answer"


def test_completed_reasoning_only_response_is_still_used(config) -> None:
    """Some backends finish normally but report the answer in reasoning_content."""
    engine = _prepared_engine(config)
    engine._post_openai_chat = lambda payload, stream=False: _FakeResponse(
        {"content": "", "reasoning_content": "The cluster is healthy."}, finish_reason="stop"
    )

    result = engine._call_inference([{"role": "user", "content": "hi"}], include_tools=False)

    assert result["content"] == "The cluster is healthy."
    assert not result.get("error")


def test_failure_diagnosis_closes_tool_call_and_withholds_tools(config) -> None:
    engine = AIEngine(config)
    engine._pending_tool_call_id = "call-boom"
    engine._get_default_system_prompt = lambda *args, **kwargs: "system"
    captured: dict = {}

    def fake_call(messages, include_tools=True):
        captured["include_tools"] = include_tools
        captured["messages"] = messages
        return {"content": "Root cause: OVN uplink had no free NIC."}

    engine._call_inference = fake_call
    diagnosis = engine.diagnose_tool_failure("deploy_microcloud", "Script failed (exit 1): boom")

    # The model must not be able to launch another infrastructure operation.
    assert captured["include_tools"] is False
    assert engine._pending_tool_call_id is None
    assert diagnosis == "Root cause: OVN uplink had no free NIC."

    roles = [message["role"] for message in engine.conversation_history]
    assert "tool" in roles, "the pending native tool call must still be closed"
    assert "Script failed (exit 1): boom" in captured["messages"][-1]["content"]


def test_failure_diagnosis_degrades_silently(config) -> None:
    """A diagnosis outage must never hide the deterministic failure report."""
    engine = AIEngine(config)
    engine._get_default_system_prompt = lambda *args, **kwargs: "system"

    def boom(messages, include_tools=True):
        raise requests.ConnectionError("backend down")

    engine._call_inference = boom

    assert engine.diagnose_tool_failure("deploy_microcloud", "Script failed") == ""


def test_cold_start_disconnect_is_reported_to_the_user(config, monkeypatch) -> None:
    """An idle-unloaded model must not look like a frozen assistant."""
    config.inference_auto_discovery = False
    config.max_retries = 2
    config.inference_restart_timeout = 0
    engine = AIEngine(config)
    notices: list[str] = []
    engine.status_notifier = notices.append

    attempts: list[int] = []

    def fake_post(*args, **kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            raise requests.ConnectionError("model unloaded after idle timeout")
        return "ok"

    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr(engine, "_wait_for_inference_ready", lambda _timeout: True)

    assert engine._post_inference("/v1/chat/completions", {"messages": []}) == "ok"
    assert notices, "the user must be told the model is reloading"
    assert "reloading" in notices[0].lower()


def test_status_notifier_failure_never_breaks_a_request(config, monkeypatch) -> None:
    config.inference_auto_discovery = False
    config.max_retries = 2
    config.inference_restart_timeout = 0
    engine = AIEngine(config)

    def broken_notifier(_message):
        raise RuntimeError("UI is gone")

    engine.status_notifier = broken_notifier
    attempts: list[int] = []

    def fake_post(*args, **kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            raise requests.ConnectionError("model unloaded")
        return "ok"

    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr(engine, "_wait_for_inference_ready", lambda _timeout: True)

    assert engine._post_inference("/v1/chat/completions", {"messages": []}) == "ok"


class _FakeStream:
    """Minimal SSE response stand-in."""

    status_code = 200

    def __init__(self, events: list[str]) -> None:
        self._events = events

    def raise_for_status(self) -> None:
        return None

    def iter_lines(self):
        for event in self._events:
            yield f"data: {event}".encode()
        yield b"data: [DONE]"


def _sse(delta: dict, finish: str | None = None) -> str:
    import json as _json

    return _json.dumps({"choices": [{"delta": delta, "finish_reason": finish}]})


def test_streaming_emits_prose_progressively(config) -> None:
    engine = _prepared_engine(config)
    seen: list[str] = []
    engine.stream_callback = seen.append
    engine._post_openai_chat = lambda payload, stream=False: _FakeStream(
        [
            _sse({"role": "assistant"}),
            _sse({"content": "Hello"}),
            _sse({"content": " there"}),
            _sse({}, finish="stop"),
        ]
    )

    result = engine._call_inference([{"role": "user", "content": "hi"}], include_tools=False)

    assert result["content"] == "Hello there"
    # The callback receives the answer so far, so a UI can render it as it grows.
    assert seen == ["Hello", "Hello there"]


def test_streaming_is_requested_only_when_someone_is_listening(config) -> None:
    engine = _prepared_engine(config)
    captured: dict = {}

    def fake_post(payload, stream=False):
        captured["stream_flag"] = payload["stream"]
        captured["stream_kwarg"] = stream
        return _FakeResponse({"content": "hi"})

    engine._post_openai_chat = fake_post
    engine._call_inference([{"role": "user", "content": "hi"}], include_tools=False)

    assert captured["stream_flag"] is False
    assert captured["stream_kwarg"] is False


def test_streaming_can_be_disabled_by_config(config) -> None:
    config.inference_stream = False
    engine = _prepared_engine(config)
    engine.stream_callback = lambda _text: None
    captured: dict = {}

    def fake_post(payload, stream=False):
        captured["stream_flag"] = payload["stream"]
        return _FakeResponse({"content": "hi"})

    engine._post_openai_chat = fake_post
    engine._call_inference([{"role": "user", "content": "hi"}], include_tools=False)

    assert captured["stream_flag"] is False


def test_streamed_structured_json_is_not_previewed(config) -> None:
    """A half-written JSON object is noise, not an answer."""
    engine = _prepared_engine(config)
    seen: list[str] = []
    engine.stream_callback = seen.append
    engine._post_openai_chat = lambda payload, stream=False: _FakeStream(
        [
            _sse({"content": '{"action": "list_'}),
            _sse({"content": 'environments"}'}),
            _sse({}, finish="stop"),
        ]
    )

    result = engine._call_inference([{"role": "user", "content": "hi"}], include_tools=False)

    assert seen == [], "structured output must not be shown while it is being written"
    assert result.get("action") == "list_environments"


def test_streamed_tool_call_fragments_are_reassembled(config) -> None:
    """Tool arguments may arrive split across chunks."""
    engine = _prepared_engine(config)
    seen: list[str] = []
    engine.stream_callback = seen.append
    engine._post_openai_chat = lambda payload, stream=False: _FakeStream(
        [
            _sse(
                {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "call-1",
                            "type": "function",
                            "function": {"name": "delete_environment", "arguments": '{"works'},
                        }
                    ]
                }
            ),
            _sse({"tool_calls": [{"index": 0, "function": {"arguments": 'pace": "lab_mc"}'}}]}),
            _sse({}, finish="tool_calls"),
        ]
    )

    result = engine._call_inference([{"role": "user", "content": "hi"}])

    assert result["action"] == "delete_environment"
    assert result["parameters"] == {"workspace": "lab_mc"}
    assert result["_tool_call_id"] == "call-1"
    assert seen == [], "a tool call is not user-facing prose"


def test_broken_stream_falls_back_to_a_normal_request(config) -> None:
    engine = _prepared_engine(config)
    engine.stream_callback = lambda _text: None
    calls: list[bool] = []

    class _ExplodingStream(_FakeStream):
        def iter_lines(self):
            yield b'data: {"choices":[{"delta":{"content":"partial"}}]}'
            raise requests.ConnectionError("stream died")

    def fake_post(payload, stream=False):
        calls.append(stream)
        if stream:
            return _ExplodingStream([])
        return _FakeResponse({"content": "recovered answer"})

    engine._post_openai_chat = fake_post
    result = engine._call_inference([{"role": "user", "content": "hi"}], include_tools=False)

    assert calls == [True, False], "a failed stream must be retried without streaming"
    assert result["content"] == "recovered answer"


def test_labeled_tool_contract_from_small_model_is_parsed() -> None:
    from lab_ai_assistant.ai_engine import _extract_structured_response

    raw = """I recommend a segregated network training lab.

action: deploy_microcloud
parameters:
  nodes: 3
  network_mode: fully-segregated-4nic
  sizing_tier: small
  user_prefix: segnet
reasoning: The user explicitly requested four isolated traffic planes.
missing_params: []
"""

    result = _extract_structured_response(raw)

    assert result["action"] == "deploy_microcloud"
    assert result["parameters"] == {
        "nodes": 3,
        "network_mode": "fully-segregated-4nic",
        "sizing_tier": "small",
        "user_prefix": "segnet",
    }
    assert result["message"] == "I recommend a segregated network training lab."
    assert "four isolated traffic planes" in result["reasoning"]


def test_labeled_unknown_tool_is_not_executed() -> None:
    from lab_ai_assistant.ai_engine import _extract_structured_response

    raw = """action: destroy_everything
parameters:
  workspace: lab_microcloud
"""

    result = _extract_structured_response(raw)

    assert result.get("action") is None
    assert result["content"] == raw


def test_labeled_parameterless_tool_is_parsed() -> None:
    from lab_ai_assistant.ai_engine import _extract_structured_response

    raw = """I will inspect the current host.

action: inspect_host_environment
parameters: {}
"""

    result = _extract_structured_response(raw)

    assert result["action"] == "inspect_host_environment"
    assert result["parameters"] == {}
