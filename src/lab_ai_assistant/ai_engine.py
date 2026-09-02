"""AI engine integration for the custom-topology MicroCloud assistant."""

import json
import logging
import re
import shutil
import subprocess
import time
from typing import Any, Callable, Optional
from urllib.parse import quote, urlparse

import requests

from lab_ai_assistant.config import Config
from lab_ai_assistant.scenarios import scenarios_summary
from lab_ai_assistant.sizing import SizingAdvisor
from lab_ai_assistant.tools import get_tool_definitions

logger = logging.getLogger(__name__)

_sizing_advisor = SizingAdvisor()


class AIEngine:
    """Interface to Canonical inference snap engine."""

    def __init__(self, config: Config):
        self.config = config
        self.base_url = config.inference_host.rstrip("/")
        self.model = config.inference_model
        self._openai_base_url: Optional[str] = self._configured_openai_base_url(self.base_url)
        self.conversation_history: list[dict[str, Any]] = []
        self._api_style: Optional[str] = None
        self._resolved_model: Optional[str] = None
        self._runtime_discovery_attempted = False
        self._runtime_source = "configuration"
        self._runtime_engine: Optional[str] = None
        self._snap_server_active = False
        self._supports_native_tools: bool = True  # optimistically assume support
        self._supports_thinking_control: bool = True  # optimistically assume support
        self._pending_tool_call_id: Optional[str] = None
        self.max_history_messages: int = 20
        # Live, host-grounded context injected into the system prompt every turn.
        # The orchestrator refreshes this so the model always plans against the
        # REAL host capacity and existing environments, never guesses.
        self.environment_context: str = ""
        # Optional sink for transient runtime notices (for example a local model
        # reloading after an idle unload). Kept as a plain callable so the engine
        # stays independent of any particular UI.
        self.status_notifier: Optional[Callable[[str], None]] = None
        # Optional sink for streamed answer text. Receives the accumulated answer
        # so far, letting a UI show progress while the model is still writing.
        self.stream_callback: Optional[Callable[[str], None]] = None

    def _notify(self, message: str) -> None:
        """Surface a transient runtime notice without ever breaking the request."""
        if not self.status_notifier:
            return
        try:
            self.status_notifier(message)
        except Exception:  # pragma: no cover - notification must never fail a call
            logger.debug("Status notifier raised; continuing", exc_info=True)

    def is_available(self) -> bool:
        """Check whether the discovered inference runtime and model are ready."""
        self._discover_runtime()
        model = self._resolve_model_name()
        return self._runtime_is_ready(model)

    def _runtime_is_ready(
        self,
        model: str,
        timeout: float = 5,
        log_failure: bool = True,
    ) -> bool:
        """Require readiness evidence appropriate to the selected backend and model."""
        root = self._service_root()
        encoded_model = quote(model, safe="")

        # OVMS/KServe exposes model-specific readiness independently from the
        # OpenAI-compatible /v3 API.
        for url in (
            f"{root}/v2/models/{encoded_model}/ready",
            f"{root}/v1/models/{encoded_model}",
        ):
            try:
                response = requests.get(url, timeout=timeout)
                if response.status_code == 200 and self._readiness_response_is_ready(url, response):
                    return True
            except requests.RequestException:
                continue

        # llama.cpp health is model-aware because one selected model is loaded by
        # the process. Accept it for /v1-style runtimes, but not for OVMS /v3.
        if self._runtime_source == "snap-status" and self._runtime_engine in {
            "cpu",
            "intel-onemkl",
            "nvidia-gpu",
            "amd-gpu",
        }:
            try:
                if requests.get(f"{root}/health", timeout=timeout).status_code == 200:
                    return True
            except requests.RequestException:
                pass

        # Custom OpenAI backends may only expose a model list. Verify that the
        # selected model is actually present instead of accepting any HTTP 200.
        for base in self._openai_base_candidates():
            try:
                response = requests.get(f"{base}/models", timeout=timeout)
                if response.status_code == 200 and model in self._models_from_payload(
                    response.json()
                ):
                    return True
            except (requests.RequestException, TypeError, ValueError):
                continue

        try:
            response = requests.get(f"{root}/api/tags", timeout=timeout)
            if response.status_code == 200 and model in self._models_from_payload(response.json()):
                return True
        except (requests.RequestException, TypeError, ValueError):
            pass

        if log_failure:
            logger.error("Inference engine not available at %s", self.runtime_endpoint)
        return False

    @staticmethod
    def _readiness_response_is_ready(url: str, response: requests.Response) -> bool:
        """Interpret model-status payloads instead of trusting HTTP 200 alone."""
        if "/v1/models/" not in url:
            return True
        try:
            payload = response.json()
        except (TypeError, ValueError):
            return False
        if not isinstance(payload, dict):
            return False
        if "ready" in payload:
            return payload.get("ready") is True
        statuses = payload.get("model_version_status")
        if isinstance(statuses, list) and statuses:
            return all(
                isinstance(item, dict)
                and str(item.get("state", "")).upper() == "AVAILABLE"
                and (
                    not isinstance(item.get("status"), dict)
                    or item["status"].get("error_code") in {None, "OK"}
                )
                for item in statuses
            )
        # llama.cpp and custom OpenAI servers generally use /models lists rather
        # than model-specific status. Unknown single-model payloads fail closed.
        return False

    @property
    def runtime_endpoint(self) -> str:
        """Return the active API base URL after best-effort discovery."""
        self._discover_runtime()
        if self._api_style == "ollama":
            return self._service_root()
        return self._openai_base_url or f"{self._service_root()}/v1"

    def get_runtime_info(self) -> dict[str, Any]:
        """Return user-facing information about the selected inference runtime."""
        self._discover_runtime()
        api_style = self._detect_api_style()
        return {
            "endpoint": self.runtime_endpoint,
            "model": self._resolve_model_name(),
            "api_style": api_style,
            "engine": self._runtime_engine,
            "source": self._runtime_source,
            "server_active": self._snap_server_active,
        }

    @staticmethod
    def _configured_openai_base_url(url: str) -> Optional[str]:
        parsed = urlparse(url)
        if parsed.path and parsed.path != "/":
            return url.rstrip("/")
        return None

    @staticmethod
    def _is_generic_model_name(model: str) -> bool:
        return model.strip().lower() in {"", "auto", "gemma4"}

    @staticmethod
    def _is_loopback_host(hostname: Optional[str]) -> bool:
        return hostname in {"127.0.0.1", "localhost", "::1"}

    def _targets_same_local_service(self, discovered_endpoint: str) -> bool:
        configured = urlparse(self.base_url)
        discovered = urlparse(discovered_endpoint)
        configured_path = configured.path.rstrip("/")
        if configured_path not in {"", "/v1", "/v3"}:
            return False
        configured_port = configured.port or (443 if configured.scheme == "https" else 80)
        discovered_port = discovered.port or (443 if discovered.scheme == "https" else 80)
        return (
            self._is_loopback_host(configured.hostname)
            and self._is_loopback_host(discovered.hostname)
            and configured_port == discovered_port
        )

    def _discover_runtime(self, force: bool = False) -> None:
        """Discover Canonical snap endpoint/model, preserving explicit remote overrides."""
        if self._runtime_discovery_attempted and not force:
            return
        if not self.config.inference_auto_discovery:
            self._runtime_discovery_attempted = True
            return

        engine = self.config.inference_engine.strip()
        command = shutil.which(engine) if engine else None
        if not command:
            return

        try:
            result = subprocess.run(
                [command, "status", "--format", "json"],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return
        if result.returncode != 0:
            return

        try:
            status = json.loads(result.stdout)
        except (TypeError, ValueError):
            return
        if not isinstance(status, dict):
            return

        endpoints = status.get("endpoints", {})
        model_payload = status.get("model", {})
        services = status.get("services", {})
        endpoint = endpoints.get("openai") if isinstance(endpoints, dict) else None
        discovered_model = model_payload.get("name") if isinstance(model_payload, dict) else None
        if not isinstance(endpoint, str) or not endpoint.startswith(("http://", "https://")):
            return
        if not isinstance(discovered_model, str) or not discovered_model:
            return

        # Auto-adjust only when the configured URL points at this local snap.
        # Explicit remote/custom endpoints continue to use their configured base.
        if self._targets_same_local_service(endpoint):
            self._openai_base_url = endpoint.rstrip("/")
            self._api_style = "openai"
            self._runtime_source = "snap-status"
            self._runtime_engine = str(status.get("engine") or engine)
            if self._is_generic_model_name(self.model):
                self.model = discovered_model
                self._resolved_model = discovered_model
            self._snap_server_active = (
                isinstance(services, dict) and services.get("server") == "active"
            )
            self._runtime_discovery_attempted = True

    def _service_root(self) -> str:
        """Return the scheme/authority root for backend health and non-OpenAI APIs."""
        candidate = self._openai_base_url or self.base_url
        parsed = urlparse(candidate)
        if parsed.scheme and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}"
        return self.base_url.rstrip("/")

    def _openai_base_candidates(self) -> list[str]:
        candidates: list[str] = []
        if self._openai_base_url:
            candidates.append(self._openai_base_url.rstrip("/"))
            if self._runtime_source == "configuration" and urlparse(self.base_url).path not in {
                "",
                "/",
            }:
                return candidates
        root = self._service_root()
        candidates.extend((f"{root}/v1", f"{root}/v3", root))
        return list(dict.fromkeys(candidates))

    def _readiness_urls(self, model: str) -> list[str]:
        root = self._service_root()
        encoded_model = quote(model, safe="")
        urls = [
            f"{root}/health",
            f"{root}/v2/health/ready",
            f"{root}/v2/models/{encoded_model}/ready",
            f"{root}/v1/models/{encoded_model}",
            f"{root}/api/tags",
        ]
        urls.extend(f"{base}/models" for base in self._openai_base_candidates())
        return list(dict.fromkeys(urls))

    @staticmethod
    def _models_from_payload(payload: Any) -> list[str]:
        """Extract concrete model names from OpenAI, llama.cpp, or Ollama lists."""
        if not isinstance(payload, dict):
            return []
        models: list[str] = []
        for item in payload.get("data", []):
            if isinstance(item, dict) and item.get("id"):
                models.append(str(item["id"]))
        for item in payload.get("models", []):
            if isinstance(item, dict):
                model_name = item.get("name") or item.get("model")
                if model_name:
                    models.append(str(model_name))
        return list(dict.fromkeys(models))

    def set_environment_context(self, context: str) -> None:
        """Refresh the live host-grounded context injected into the system prompt.

        Called by the orchestrator before each turn so the model always reasons
        against real host capacity and existing environments instead of guessing.
        """
        self.environment_context = (context or "").strip()

    def chat(
        self,
        user_message: str,
        system_prompt: Optional[str] = None,
        include_tools: bool = True,
    ) -> dict[str, Any]:
        """Send a user message to the local LLM and parse structured response."""
        self.conversation_history.append({"role": "user", "content": user_message})

        if system_prompt is None:
            system_prompt = self._get_default_system_prompt(include_tools)

        messages = [{"role": "system", "content": system_prompt}] + self.conversation_history

        try:
            response = self._call_inference(messages, include_tools=include_tools)
            self._record_assistant_response(response)
            return response
        except Exception as exc:
            logger.error(f"Error calling inference engine: {exc}")
            return {"content": f"Error: {str(exc)}", "error": True}

    def _call_inference(
        self,
        messages: list[dict[str, Any]],
        include_tools: bool = True,
        _thinking_retry: bool = False,
    ) -> dict[str, Any]:
        self._discover_runtime()
        api_style = self._detect_api_style()
        resolved_model = self._resolve_model_name()

        # Thinking models burn the entire output budget on hidden reasoning and
        # return an empty answer, so keep it off unless explicitly enabled.
        disable_thinking = not self.config.inference_enable_thinking or _thinking_retry

        if api_style == "openai":
            # Streaming is only worthwhile when someone is watching the output.
            want_stream = bool(self.config.inference_stream and self.stream_callback)
            payload: dict[str, Any] = {
                "model": resolved_model,
                "messages": messages,
                "stream": want_stream,
                "max_tokens": self.config.inference_max_output_tokens,
            }

            if disable_thinking and self._supports_thinking_control:
                payload["chat_template_kwargs"] = {"enable_thinking": False}

            # Pass tool definitions via the native API parameter when available.
            # This is significantly more token-efficient than embedding in prompt.
            if include_tools and self._supports_native_tools:
                api_tools = self._build_openai_tools()
                if api_tools:
                    payload["tools"] = api_tools

            response = self._post_openai_chat(payload, stream=want_stream)

            # Not every OpenAI-compatible backend understands chat_template_kwargs.
            # Drop it before assuming the rest of the request is at fault.
            if response.status_code in (400, 422) and "chat_template_kwargs" in payload:
                logger.warning(
                    "Backend rejected chat_template_kwargs; retrying without thinking control"
                )
                self._supports_thinking_control = False
                payload.pop("chat_template_kwargs", None)
                response = self._post_openai_chat(payload, stream=want_stream)

            # If backend rejects the tools parameter, retry without it and
            # fall back to embedding tools in the system prompt from now on.
            if response.status_code in (400, 422) and "tools" in payload:
                logger.warning(
                    "Backend rejected tools parameter; falling back to prompt-embedded tools"
                )
                self._supports_native_tools = False
                payload.pop("tools", None)
                # Inject tool definitions into the system message for this request.
                tools_text = f"\n\nAvailable tools:\n{json.dumps(get_tool_definitions(), indent=2)}"
                payload["messages"] = [
                    {
                        **message,
                        "content": (
                            str(message.get("content", "")) + tools_text
                            if message.get("role") == "system"
                            else message.get("content")
                        ),
                    }
                    for message in messages
                ]
                response = self._post_openai_chat(payload, stream=want_stream)

            response.raise_for_status()
            if want_stream:
                try:
                    message_payload, finish_reason = self._consume_openai_stream(response)
                except (requests.RequestException, ValueError) as exc:
                    # A broken stream must not lose the turn; the same request is
                    # cheap to repeat without streaming.
                    logger.warning("Streaming response failed (%s); retrying without stream", exc)
                    payload["stream"] = False
                    response = self._post_openai_chat(payload, stream=False)
                    response.raise_for_status()
                    result = response.json()
                    choice = result.get("choices", [{}])[0]
                    message_payload = choice.get("message", {})
                    finish_reason = choice.get("finish_reason", "")
            else:
                result = response.json()
                choice = result.get("choices", [{}])[0]
                message_payload = choice.get("message", {})
                finish_reason = choice.get("finish_reason", "")

            tool_calls = message_payload.get("tool_calls", [])

            if isinstance(tool_calls, list) and tool_calls:
                first_call = tool_calls[0]
                fn = first_call.get("function", {}) if isinstance(first_call, dict) else {}
                tool_call_id = first_call.get("id", "") if isinstance(first_call, dict) else ""
                tool_name = fn.get("name")
                tool_args_raw = fn.get("arguments", "{}")
                tool_params: dict[str, Any] = {}
                if isinstance(tool_args_raw, str):
                    try:
                        parsed_args = json.loads(tool_args_raw)
                        if isinstance(parsed_args, dict):
                            tool_params = parsed_args
                    except (TypeError, ValueError):
                        tool_params = {}
                elif isinstance(tool_args_raw, dict):
                    tool_params = tool_args_raw

                content = message_payload.get("content", "")
                reasoning_content = message_payload.get("reasoning_content", "")
                assistant_message = dict(message_payload)
                # The orchestrator executes one tool at a time. Keep only that call
                # in history so the protocol never waits for unhandled parallel IDs.
                assistant_message["tool_calls"] = [first_call]
                assistant_message.setdefault("role", "assistant")
                return {
                    "action": tool_name,
                    "parameters": tool_params,
                    "message": content or f"Preparing {tool_name}",
                    "_raw": json.dumps(message_payload),
                    "_assistant_message": assistant_message,
                    "_tool_call_id": tool_call_id,
                }

            content = message_payload.get("content", "")
            reasoning_content = message_payload.get("reasoning_content", "")

            raw_content = content
            if not raw_content and reasoning_content:
                if finish_reason == "length":
                    # A thinking model spent its whole budget on hidden reasoning
                    # and never emitted an answer. That transcript is an internal
                    # monologue, so surfacing it would read as the assistant
                    # talking to itself mid-sentence.
                    if not disable_thinking:
                        logger.warning(
                            "Model exhausted its output budget on reasoning; "
                            "retrying with thinking disabled"
                        )
                        return self._call_inference(
                            messages,
                            include_tools=include_tools,
                            _thinking_retry=True,
                        )
                    logger.warning(
                        "Model returned truncated reasoning and no answer even with "
                        "thinking disabled; consider raising INFERENCE_MAX_OUTPUT_TOKENS"
                    )
                    return {
                        "content": (
                            "The model used its entire response budget on internal "
                            "reasoning without producing an answer. Try a narrower "
                            "question, or raise INFERENCE_MAX_OUTPUT_TOKENS."
                        ),
                        "error": True,
                    }
                # Some backends complete normally but report the answer only in
                # reasoning_content. In that case it really is the response.
                raw_content = reasoning_content
        else:
            payload = {
                "model": resolved_model,
                "messages": messages,
                "stream": False,
                "format": "json",
                "options": {"num_predict": self.config.inference_max_output_tokens},
            }
            response = self._post_inference(f"{self._service_root()}/api/chat", payload)
            response.raise_for_status()
            result = response.json()
            raw_content = result.get("message", {}).get("content", "")

        return _extract_structured_response(raw_content)

    def _post_openai_chat(
        self,
        payload: dict[str, Any],
        stream: bool = False,
    ) -> requests.Response:
        """POST chat completion to the discovered base, probing compatible fallbacks."""
        last_response: Optional[requests.Response] = None
        for base in self._openai_base_candidates():
            response = self._post_inference(f"{base}/chat/completions", payload, stream=stream)
            last_response = response
            if response.status_code != 404:
                self._openai_base_url = base
                return response
        assert last_response is not None
        return last_response

    def _consume_openai_stream(
        self,
        response: requests.Response,
    ) -> tuple[dict[str, Any], str]:
        """Assemble a streamed chat completion into a normal message payload.

        Returns the same shape as a non-streamed response so all downstream
        interpretation stays in one place.
        """
        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        tool_calls: dict[int, dict[str, Any]] = {}
        finish_reason = ""
        emit = self._stream_emitter()

        for raw_line in response.iter_lines():
            if not raw_line:
                continue
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line.startswith("data:"):
                continue
            data = line[len("data:") :].strip()
            if data == "[DONE]":
                break
            try:
                event = json.loads(data)
            except (TypeError, ValueError):
                continue
            if not isinstance(event, dict):
                continue

            choices = event.get("choices") or [{}]
            choice = choices[0] if isinstance(choices[0], dict) else {}
            if choice.get("finish_reason"):
                finish_reason = str(choice["finish_reason"])

            delta = choice.get("delta") or {}
            if not isinstance(delta, dict):
                continue

            chunk = delta.get("content")
            if isinstance(chunk, str) and chunk:
                content_parts.append(chunk)
                emit("".join(content_parts))

            reasoning_chunk = delta.get("reasoning_content")
            if isinstance(reasoning_chunk, str) and reasoning_chunk:
                reasoning_parts.append(reasoning_chunk)

            self._merge_tool_call_deltas(tool_calls, delta.get("tool_calls"))

        message: dict[str, Any] = {
            "role": "assistant",
            "content": "".join(content_parts),
        }
        if reasoning_parts:
            message["reasoning_content"] = "".join(reasoning_parts)
        if tool_calls:
            message["tool_calls"] = [tool_calls[index] for index in sorted(tool_calls)]
        return message, finish_reason

    def _stream_emitter(self) -> Callable[[str], None]:
        """Build a guarded emitter that only forwards human-readable prose.

        The model may answer with the structured JSON contract instead of prose.
        Showing a half-written JSON object would be noise, so once the answer
        looks structured the preview stays silent and only the parsed result is
        shown at the end.
        """
        state = {"decided": False, "suppress": False}

        def emit(accumulated: str) -> None:
            if not self.stream_callback:
                return
            if not state["decided"]:
                stripped = accumulated.lstrip()
                if not stripped:
                    return
                state["decided"] = True
                state["suppress"] = stripped[0] in "{[`"
            if state["suppress"]:
                return
            try:
                self.stream_callback(accumulated)
            except Exception:  # pragma: no cover - preview must never break a turn
                logger.debug("Stream callback raised; continuing", exc_info=True)

        return emit

    @staticmethod
    def _merge_tool_call_deltas(
        tool_calls: dict[int, dict[str, Any]],
        deltas: Any,
    ) -> None:
        """Merge streamed tool-call fragments, whose arguments arrive in pieces."""
        if not isinstance(deltas, list):
            return
        for item in deltas:
            if not isinstance(item, dict):
                continue
            index = item.get("index", 0)
            if not isinstance(index, int):
                index = 0
            call = tool_calls.setdefault(
                index,
                {"id": "", "type": "function", "function": {"name": "", "arguments": ""}},
            )
            if item.get("id"):
                call["id"] = str(item["id"])
            if item.get("type"):
                call["type"] = str(item["type"])
            function = item.get("function")
            if not isinstance(function, dict):
                continue
            if function.get("name"):
                call["function"]["name"] = str(function["name"])
            arguments = function.get("arguments")
            if isinstance(arguments, str):
                call["function"]["arguments"] += arguments

    def _post_inference(
        self,
        path_or_url: str,
        payload: dict[str, Any],
        stream: bool = False,
    ) -> requests.Response:
        """Retry transient disconnects after the local inference service is ready."""
        url = (
            path_or_url
            if path_or_url.startswith(("http://", "https://"))
            else f"{self._service_root()}/{path_or_url.lstrip('/')}"
        )
        last_error: requests.RequestException | None = None
        for attempt in range(self.config.max_retries):
            try:
                return requests.post(
                    url,
                    json=payload,
                    timeout=self.config.response_timeout,
                    stream=stream,
                )
            except requests.ConnectionError as exc:
                last_error = exc
                if attempt + 1 < self.config.max_retries:
                    # Local inference snaps unload the model after an idle period,
                    # so the first request after a pause is dropped mid-connection.
                    logger.warning(
                        "Inference request disconnected; waiting for backend readiness "
                        "before retry %s/%s",
                        attempt + 2,
                        self.config.max_retries,
                    )
                    self._notify(
                        "Local inference model is reloading after being idle; waiting for it..."
                    )
                    self._wait_for_inference_ready(self.config.inference_restart_timeout)
            except requests.Timeout as exc:
                last_error = exc
                logger.warning(
                    "Inference request timed out after %ss (attempt %s/%s)",
                    self.config.response_timeout,
                    attempt + 1,
                    self.config.max_retries,
                )
                # Read timeouts on the same payload are usually not transient.
                # Retry only when the backend appears unavailable.
                backend_ready = self._runtime_is_ready(
                    self._resolve_model_name(),
                    timeout=2,
                    log_failure=False,
                )
                if backend_ready or attempt + 1 >= self.config.max_retries:
                    break
                logger.warning(
                    "Inference backend not ready after timeout; waiting before retry %s/%s",
                    attempt + 2,
                    self.config.max_retries,
                )
                self._notify("Local inference backend is not responding yet; waiting for it...")
                self._wait_for_inference_ready(self.config.inference_restart_timeout)
        assert last_error is not None
        raise last_error

    def _wait_for_inference_ready(self, timeout: float) -> bool:
        """Wait for a restarted local inference backend to expose a healthy endpoint."""
        deadline = time.monotonic() + max(timeout, 0.0)

        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            if self._runtime_is_ready(
                self._resolve_model_name(),
                timeout=min(2.0, remaining),
                log_failure=False,
            ):
                return True
            time.sleep(0.5)

        return False

    def _detect_api_style(self) -> str:
        """Detect server API shape and cache the result."""
        self._discover_runtime()
        if self._api_style:
            return self._api_style

        root = self._service_root()
        try:
            response = requests.get(f"{root}/api/tags", timeout=5)
            if response.status_code == 200:
                self._api_style = "ollama"
                return self._api_style
        except requests.RequestException:
            pass

        for base in self._openai_base_candidates():
            try:
                response = requests.get(f"{base}/models", timeout=5)
                if response.status_code == 200:
                    self._openai_base_url = base
                    self._api_style = "openai"
                    return self._api_style
            except requests.RequestException:
                continue

        for path in ("/health", "/v2/health/ready"):
            try:
                response = requests.get(f"{root}{path}", timeout=5)
                if response.status_code == 200:
                    self._openai_base_url = self._openai_base_url or f"{root}/v1"
                    self._api_style = "openai"
                    return self._api_style
            except requests.RequestException:
                continue

        # Preserve backward compatibility for custom OpenAI-compatible services.
        self._openai_base_url = self._openai_base_url or f"{root}/v1"
        self._api_style = "openai"
        return self._api_style

    def _resolve_model_name(self) -> str:
        """Pick a concrete model name, allowing broad config defaults like 'gemma4'."""
        self._discover_runtime()
        if self._resolved_model:
            return self._resolved_model

        candidates = self._fetch_available_models()
        configured = self.model

        if not candidates:
            self._resolved_model = configured
            return self._resolved_model

        for candidate in candidates:
            if candidate == configured:
                self._resolved_model = candidate
                return self._resolved_model

        if not self._is_generic_model_name(configured):
            self._resolved_model = configured
            return self._resolved_model

        for candidate in candidates:
            if configured in candidate or candidate.startswith(configured):
                self._resolved_model = candidate
                return self._resolved_model

        self._resolved_model = candidates[0]
        return self._resolved_model

    def _fetch_available_models(self) -> list[str]:
        """Query model list endpoints across supported API styles."""
        self._discover_runtime()
        models: list[str] = []
        if self._runtime_source == "snap-status" and self.model:
            models.append(self.model)

        root = self._service_root()
        endpoints = [f"{base}/models" for base in self._openai_base_candidates()]
        endpoints.append(f"{root}/api/tags")

        for endpoint in endpoints:
            try:
                response = requests.get(endpoint, timeout=5)
                if response.status_code != 200:
                    continue

                payload = response.json()
                models.extend(self._models_from_payload(payload))

                if models:
                    break
            except requests.RequestException:
                continue
            except (TypeError, ValueError):
                continue

        return list(dict.fromkeys(models))

    def _build_openai_tools(self) -> list[dict[str, Any]]:
        """Convert internal tool definitions to OpenAI-compatible tools format."""
        tools_def = get_tool_definitions()
        api_tools = []
        for tool in tools_def.get("tools", []):
            api_tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool["name"],
                        "description": tool.get("description", ""),
                        "parameters": tool.get("parameters", {"type": "object", "properties": {}}),
                    },
                }
            )
        return api_tools

    def _get_default_system_prompt(self, include_tools: bool = True) -> str:
        """System prompt focused on custom topology planning."""
        scenario_catalog = scenarios_summary()
        sizing_tiers = _sizing_advisor.describe_tiers()

        prompt = f"""You are a MicroCloud Lab & Demo Environment Assistant.
Your purpose is to help users quickly provision and manage MicroCloud lab, demo,
and teaching environments. This is NOT a production deployment tool — it creates
lightweight environments for learning, testing, proof-of-concepts, and demonstrations.

WHAT YOU DO:
- Deploy MicroCloud clusters inside LXD VMs for lab/demo/teaching purposes.
- Help users understand sizing, topology, and trade-offs for their lab needs.
- Manage environment lifecycle: list, scale, delete lab environments.
- Answer questions about MicroCloud, LXD, and MicroCeph.

WHEN INTRODUCING YOURSELF:
- You are a lab automation assistant, not a cloud architect.
- Emphasize that this tool provisions lab/demo/PoC environments quickly.
- Do NOT claim to be a "senior architect" or imply production-grade design.
- Keep introductions short: explain your capabilities and available commands.

CORE FACTS:
- MicroCloud storage is MicroCeph (no LVM mode).
- This automation currently configures MicroOVN for every deployed lab.
- Cluster size is flexible (including even counts); this automation targets >=3 nodes.
- For HA in production, at least 3 members are required, and 4 members are commonly recommended for critical deployments.
- Each MicroCloud node needs at least one Ceph disk (default: 1 OSD per node).
- In this tool's default flow, MicroCloud runs inside LXD VMs created by
  OpenTofu (nested-lxd-lab mode).
- Ceph disks are per-node virtual block volumes provisioned automatically
  by Terraform.
- ceph_disks_per_node: 1 is the default; recommend 2 for higher IOPS or larger
  clusters. Each OSD disk uses ceph_disk_gib GiB of host storage.
- local_disk_gib: 0 = disabled (default). Set >= 10 to add a local ZFS disk
  per node for fast local storage alongside distributed Ceph.
- network_mode defaults to standard-2nic: management/cluster traffic on eth0
  plus an IP-free OVN uplink on eth1.
- fully-segregated-4nic is optional. Use it when the user explicitly asks for
  separated traffic planes or wants to teach network segregation. It adds a
  static-IP OVN Geneve underlay on eth2 and one static-IP Ceph network on eth3
  for both public/client and internal/replication traffic. The system resolves
  non-overlapping CIDRs when the user does not provide advanced overrides.
- A deployed environment's network mode is immutable. Add/scale inherits it
  from Terraform state; changing modes requires a fresh environment.
- All resources remain in the default LXD project. Before deployment the
  deterministic layer reserves an exact hash-based resource namespace and
  rejects any profile/network/instance/volume name already used by unmanaged
  or orphaned LXD resources. Suggest a different user prefix when this happens.

SYSTEM ARCHITECTURE — HOW THE AUTOMATION WORKS:
This system has a layered pipeline. Understand each layer so you can accurately
tell users what is and isn't possible with the current automation.

┌─────────────────────────────────────────────────────────────┐
│ Layer 1: prep_host.sh (one-time host setup)                 │
│  Steps: snapd → LXD → OpenTofu → Ansible → SSH key → init  │
│  Result: Host is ready to create lab environments           │
└─────────────────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────────────────┐
│ Layer 2: deploy_microcloud.sh (creates a full environment)  │
│  Phase A — Detect: finds LXD network + storage pool on host │
│  Phase B — Size: auto-computes node resources from host     │
│            capacity OR uses explicit --node-cpu etc.         │
│  Phase C — Provision (OpenTofu):                            │
│    • Creates LXD VMs (Ubuntu 24.04 virtual machines)        │
│    • Creates an OVN uplink bridge (IP-free, for MicroOVN)   │
│    • Optionally creates OVN-underlay + Ceph plane bridges   │
│    • Creates per-node Ceph block volumes                    │
│    • Attaches eth1 + optional eth2/eth3 + Ceph disks        │
│    • Injects SSH key via cloud-init                         │
│    • Generates Ansible inventory file                       │
│  Phase D — Bootstrap (Ansible playbook microcloud.yml):     │
│    • Waits for VMs to boot                                  │
│    • Installs snaps: microcloud, microceph, microovn, lxd   │
│    • Validates standard or fully segregated NIC layout      │
│    • Auto-detects Ceph disk (non-root block device)         │
│    • Generates MicroCloud preseed YAML                      │
│    • Runs `microcloud preseed` on all nodes (cluster init)  │
│    • Creates OVN UPLINK physical network                    │
│    • Creates OVN default logical network                    │
│    • Verifies cluster formation                             │
│  Phase C and D ALWAYS run sequentially — there is currently │
│  no flag to run only Phase C without Phase D.               │
└─────────────────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────────────────┐
│ Layer 3: list_microcloud_environments.sh                    │
│  Queries tofu workspaces + lxc list to show active envs     │
└─────────────────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────────────────┐
│ Layer 4: scale_microcloud.sh                                │
│  Re-runs deploy_microcloud.sh with higher --nodes count     │
│  on the same workspace (OpenTofu handles the diff)          │
└─────────────────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────────────────┐
│ Layer 4b: add_cluster_node.sh (live cluster expansion)      │
│  Phase 1 — OpenTofu apply with incremented node count       │
│  Phase 2 — Ansible prepares ONLY the new nodes (snaps)      │
│  Phase 3 — `microcloud add` preseed to join new nodes live  │
│  Phase 4 — verify_cluster_health to confirm success         │
│  This is different from scale_environment: scale re-deploys │
│  via deploy script; add_cluster_node expands a live cluster. │
└─────────────────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────────────────┐
│ Layer 4c: verify_cluster_health.sh                          │
│  Runs cluster list on all 4 services from the initiator:    │
│  microcloud, lxc, microceph, microovn cluster list          │
│  Also shows storage pools and networks.                     │
└─────────────────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────────────────┐
│ Layer 5: cleanup_microcloud.sh                              │
│  Runs `tofu destroy` on the workspace → removes VMs,       │
│  networks, volumes. Then deletes workspace + inventory file.│
└─────────────────────────────────────────────────────────────┘

WHAT THIS MEANS FOR USER REQUESTS:
- "Deploy without installing snaps" → NOT possible with current scripts.
  Phase D (Ansible) always runs after Phase C. Be honest about this limitation.
- "Just create VMs" → Same answer: the script has no --skip-ansible flag.
  Suggest: deploy normally, then students can `snap remove` + re-install manually.
- "Use different snap channels" → NOT configurable via CLI flags. Channels are
  hardcoded in playbooks/microcloud.yml. Mention user can edit the playbook.
- "Custom preseed" → NOT exposed via CLI. Preseed is auto-generated by Ansible.
- "Change network after deploy" → NOT supported in place. Network mode and
  dedicated CIDRs are persisted geometry; create a fresh environment instead.
- "Add nodes to existing cluster" → Use add_cluster_node. It preserves the persisted
    deployment geometry, provisions new VMs, and joins them via `microcloud add`.
- "Scale to a larger node count" → scale_environment safely delegates to the same
    add-member workflow. Downscale is not implemented because members and Ceph OSDs
    must be drained before VM destruction.
- "Check if cluster is healthy" → Use verify_cluster_health tool.
- "More Ceph disks per node" → Use ceph_disks_per_node parameter in deploy_microcloud.
- "Add local fast storage" → Use local_disk_gib >= 10 in deploy_microcloud.
- Multiple labs can run concurrently. Give each new deployment a unique user_prefix;
  its workspace is always <user_prefix>_microcloud. Never pass workspace to
  deploy_microcloud. If a user proposes a workspace-like name, derive a short unique
  user_prefix (for example lab_microcloud_2 becomes lab-2).
- Never claim an existing lab must be deleted before creating another unless the
  deterministic validation result explicitly reports insufficient capacity or an
  exact resource-name collision.
- If a user asks for something the pipeline can't do, explain which layer
  handles it, why it's coupled, and suggest the closest achievable alternative.

IMPORTANT: Only pass parameters that actually exist in the tool definitions.
The orchestrator filters out unknown parameters, but you should never invent
parameters in the first place. Reason about what's possible from the architecture
above, not by guessing CLI flags.

SIZING — ALWAYS SHOW DETAILS:
When proposing or confirming a sizing tier, ALWAYS display the per-node resource numbers:
{sizing_tiers}

Example of correct behavior when recommending "small" tier:
  "I recommend the 'small' tier for your PoC:
   - Per node: 4 vCPU / 8 GB RAM / 40 GB root / 50 GB Ceph disk
   - Total (3 nodes): 12 vCPU / 24 GB RAM / 150 GB Ceph storage
   Shall I proceed?"
NEVER say just "small tier" without showing the resource numbers.

PLANNING FLOW:
0. If user asks for lifecycle actions (list/delete/scale/add-nodes/health-check), execute those tools directly.
1. Understand user intent (PoC, lab, demo, teaching, etc.).
2. Use LIVE ENVIRONMENT STATE for host capacity. Call inspect_host_environment only
    when the user requests a refresh or the state is missing.
3. Propose topology WITH full sizing details shown to the user.
4. Request deploy_microcloud when the plan is complete. The orchestrator validates
    and displays the exact resolved plan, then enforces user confirmation before execution.

TOOL DECISION RULES:
- If the user asks to create/deploy/prepare a lab and provides a purpose plus a
  node count, call deploy_microcloud. You may omit CPU/RAM/disk values; the
  deterministic host-aware sizing layer resolves them before approval.
- If the user explicitly asks for four NICs, fully segregated networking,
  separated OVN underlay, or a dedicated Ceph traffic plane, set
  network_mode=fully-segregated-4nic.
- Do not ask ordinary users to invent plane CIDRs. Omit ovn_underlay_cidr and
  ceph_network_cidr unless they requested exact values; the orchestrator selects
  non-overlapping subnets and displays them in the approval-bound plan.
- Explaining a proposed deployment is not a substitute for the tool call. Put
  the short rationale in message/reasoning and request deploy_microcloud in the
  same response so the user receives the exact validated plan.
- Do not mention or offer overcommit proactively. Every request starts with the
  normal strict capacity policy. If strict validation later supplies a bounded
  CPU/RAM candidate, the orchestrator asks you for a separate structured
  recommendation. Storage is never overcommitted.
- If the user explicitly asks whether memory/CPU overcommit is supported, never
  claim it is unavailable. Explain that new deployments use a strict-first,
  bounded, AI-gated CPU/RAM fallback with an explicit warning. It is intended for
  short-lived lab/demo/training workloads; benchmark, database performance, heavy
  Ceph I/O, and production-like workloads are normally declined. Exact ratios and
  runtime headroom remain deterministic, and storage cannot be overcommitted.

PLANNING MODE SUMMARY:
{scenario_catalog}

OUTPUT CONTRACT:
Always return valid JSON with keys:
- action: tool name or null
- parameters: object (ONLY use parameters that exist in the tool definition)
- message: user-visible explanation
- reasoning: concise reasoning
- missing_params: list

RESPONSE STYLE:
- Keep message concise and terminal-friendly (max ~8 lines unless user asks detail).
- Avoid repeating the same paragraph.
- Do not include markdown code fences or pseudo tool blocks in message.
- If calling a tool, keep message to one short plan sentence.
- Do not use a generic placeholder like "tool_call"; action must be one of the exact tool names or null.
- Always include a non-empty "reasoning" field.
- Use bullet points for comparisons and lists, not walls of text.

DEPLOYMENT SAFETY:
Do not ask for an informal confirmation before requesting a mutating tool. The
orchestrator owns confirmation and binds approval to the exact validated plan.
Never invent network interface names or disk paths.
Never pass parameters that are not defined in the tool definitions.
In nested-lxd-lab mode, do not block waiting for host physical OSD disk paths or manual NIC names.

KNOWLEDGE AND DOCUMENTATION POLICY:
- Treat live tool observations as authoritative for this host and current clusters.
- Fetch official documentation before making current/version-sensitive claims about
    requirements, supported topology, CLI semantics, snap channels, networking, storage,
    upgrade behavior, or destructive lifecycle procedures.
- Also fetch documentation when the user asks for the latest behavior, an official
    source, or when your confidence in a technical fact is low.
- You may reason without a documentation call for explanations and trade-offs that
    follow directly from verified host facts and deterministic plan calculations.
- If sources disagree, prefer live observations for local state and current official
    documentation for product behavior. Include the source URL in the answer.

EXECUTION SEMANTICS:
- Script-backed tools are synchronous: once the orchestrator calls a tool, it has already finished when this model sees the result.
- Never tell the user that a tool is "still running" or that work continues in the background unless a tool explicitly returned an asynchronous job handle.
- If a tool result contains an error, summarize the failure plainly and do not imply progress or pending background work.

CLEANUP:
Users can request environment deletion at any time.
When asked to delete/clean up, call delete_environment with the workspace name.
Confirm workspace name with user before deletion if not explicitly stated.

ENVIRONMENT MANAGEMENT:
- If user asks to list deployed labs/environments, call list_environments.
- If user asks to add nodes to a running cluster, call add_cluster_node.
- If user asks to scale up to a larger total, call scale_environment.
- Explain that downscale is unsupported rather than attempting it.
- If user asks to check cluster health/status, call verify_cluster_health.
- For scale/delete/add requests, gather or confirm the target workspace first.
- Never claim success unless the tool execution confirms it.
"""
        if self.environment_context:
            prompt += (
                "\n\nLIVE ENVIRONMENT STATE (auto-collected from THIS host, authoritative):\n"
                f"{self.environment_context}\n"
                "Treat the numbers above as ground truth. Size every proposal to fit the\n"
                "available capacity and account for resources already consumed by active\n"
                "environments. Never propose more than the host can provide. You do not\n"
                "need to call inspect_host_environment again unless the user asks to re-check."
            )

        if include_tools:
            # Only embed tools as text for backends without native tool support.
            # For OpenAI-compatible backends with native tools, they're passed via
            # the `tools` parameter in _call_inference (much more token-efficient).
            api_style = self._detect_api_style()
            if api_style != "openai" or not self._supports_native_tools:
                tools = get_tool_definitions()
                prompt += f"\n\nAvailable tools:\n{json.dumps(tools, indent=2)}"

        return prompt

    def reset_conversation(self):
        self.conversation_history = []
        self._pending_tool_call_id = None

    def get_conversation_history(self) -> list[dict[str, Any]]:
        return self.conversation_history.copy()

    def _summarize_response_for_history(self, response: dict[str, Any]) -> str:
        """Store a short natural-language summary instead of raw tool JSON."""
        content = (
            response.get("content", "")
            or response.get("message", "")
            or response.get("reasoning", "")
        )
        action = response.get("action")
        if action:
            summary = {
                "action": action,
                "parameters": response.get("parameters", {}),
                "message": content,
            }
            return json.dumps(summary, sort_keys=True)

        if content:
            return str(content)

        raw = response.get("_raw", "")
        return raw if isinstance(raw, str) else ""

    def _record_assistant_response(self, response: dict[str, Any]) -> None:
        """Preserve native protocol messages, with structured text for fallbacks."""
        assistant_message = response.get("_assistant_message")
        tool_call_id = response.get("_tool_call_id")
        if isinstance(assistant_message, dict) and tool_call_id:
            self.conversation_history.append(assistant_message)
            self._pending_tool_call_id = str(tool_call_id)
        else:
            self.conversation_history.append(
                {"role": "assistant", "content": self._summarize_response_for_history(response)}
            )
            self._pending_tool_call_id = None
        self._trim_conversation_history()

    def _trim_conversation_history(self) -> None:
        if len(self.conversation_history) <= self.max_history_messages:
            return
        self.conversation_history = self.conversation_history[-self.max_history_messages :]
        while self.conversation_history and self.conversation_history[0].get("role") == "tool":
            self.conversation_history.pop(0)

    def cancel_pending_tool_call(self, tool_name: str, reason: str) -> None:
        """Close a native tool call that policy blocked before execution."""
        self.record_tool_observation(tool_name, f"Tool call was not executed: {reason}")

    def record_tool_observation(self, tool_name: str, result: str) -> None:
        """Close a native tool call without requesting another model response."""
        if not self._pending_tool_call_id:
            return
        self.conversation_history.append(
            {
                "role": "tool",
                "tool_call_id": self._pending_tool_call_id,
                "name": tool_name,
                "content": result,
            }
        )
        self._pending_tool_call_id = None
        self._trim_conversation_history()

    def assess_lab_overcommit(
        self,
        user_request: str,
        evidence: str,
    ) -> dict[str, Any]:
        """Decide whether a bounded overcommit candidate fits the stated lab use."""
        messages = [
            {
                "role": "system",
                "content": (
                    "You are deciding whether to recommend a pre-validated CPU/RAM "
                    "overcommit candidate. Return only JSON with boolean 'recommend' "
                    "and short string 'rationale'. Recommend only for short-lived "
                    "lab/demo/training use where simultaneous peak load is unlikely. "
                    "Decline for benchmarks, performance testing, heavy Ceph I/O, "
                    "production-like use, or when safer downsizing/cleanup is preferable. "
                    "The deterministic system owns all ratios and limits; do not invent "
                    "or modify any numbers."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Original request:\n{user_request}\n\n" f"Pre-validated candidate:\n{evidence}"
                ),
            },
        ]
        try:
            response = self._call_inference(messages, include_tools=False)
        except Exception as exc:
            logger.error("Overcommit assessment failed: %s", exc)
            return {"recommend": False, "rationale": "AI assessment was unavailable."}

        recommend = response.get("recommend")
        rationale = response.get("rationale")
        if not isinstance(recommend, bool) or not isinstance(rationale, str):
            return {
                "recommend": False,
                "rationale": "AI did not return a valid overcommit assessment.",
            }
        return {"recommend": recommend, "rationale": rationale.strip()}

    def diagnose_tool_failure(self, tool_name: str, result: str) -> str:
        """Ask the model to explain why a tool failed and what to try next.

        Tool definitions are deliberately withheld so this call can only produce
        analysis text; it can never trigger another infrastructure operation. The
        caller keeps its own deterministic failure summary, so an empty return
        value simply means no analysis is appended.
        """
        self.record_tool_observation(tool_name, result)

        prompt = (
            f"The '{tool_name}' operation failed. Raw evidence:\n\n{result}\n\n"
            "Act as a MicroCloud/LXD troubleshooting expert and answer in at most "
            "8 lines:\n"
            "1. Most likely root cause, justified by specific evidence above.\n"
            "2. The single safest next diagnostic or remediation step.\n"
            "Do not claim the operation succeeded, do not imply background work "
            "continues, and do not invent log lines that are not shown."
        )
        self.conversation_history.append({"role": "user", "content": prompt})
        self._trim_conversation_history()

        messages = [
            {"role": "system", "content": self._get_default_system_prompt(include_tools=False)}
        ] + self.conversation_history

        try:
            response = self._call_inference(messages, include_tools=False)
        except Exception as exc:
            logger.error(f"Failure diagnosis could not be generated: {exc}")
            return ""

        if response.get("error"):
            return ""

        text = str(
            response.get("content") or response.get("message") or response.get("reasoning") or ""
        ).strip()
        if text:
            self.conversation_history.append({"role": "assistant", "content": text})
            self._trim_conversation_history()
        return text

    def feed_tool_result(self, tool_name: str, result: str) -> dict[str, Any]:
        """Inject a tool result into conversation history and get the AI's next step.

        This closes the agentic loop: the AI sees the tool output and reasons
        further before giving the user a final synthesised response.
        """
        lifecycle_tools = {
            "deploy_microcloud",
            "list_environments",
            "delete_environment",
            "scale_environment",
            "add_cluster_node",
            "verify_cluster_health",
        }
        is_failure = (
            result.startswith("Script failed")
            or result.startswith("Error:")
            or "Traceback" in result
        )
        has_failed_postconditions = (
            "POSTCONDITION STATUS: partial" in result or "POSTCONDITION STATUS: unhealthy" in result
        )

        if has_failed_postconditions:
            followup = (
                f"Tool '{tool_name}' finished, but deterministic postcondition verification "
                f"did not pass. Evidence:\n\n{result}\n\n"
                "Do not claim success. Explain which expected conditions were not met, "
                "distinguish script completion from cluster health, and propose the safest "
                "next diagnostic or remediation step."
            )
        elif tool_name in lifecycle_tools:
            if is_failure:
                followup = (
                    f"Tool '{tool_name}' completed with a failure. Result:\n\n{result}\n\n"
                    "Return a concise user-facing failure summary only. "
                    "Do not say the tool is still running, do not imply background work continues, "
                    "and do not pivot to topology planning unless the user explicitly asks for design/deployment."
                )
            else:
                followup = (
                    f"Tool '{tool_name}' completed successfully. Result:\n\n{result}\n\n"
                    "Return a concise user-facing summary of this operation only. "
                    "Do not say the tool is still running. Do not pivot to topology planning unless the user explicitly asks for design/deployment."
                )
        else:
            if is_failure:
                followup = (
                    f"Tool '{tool_name}' completed with a failure. Result:\n\n{result}\n\n"
                    "Return a concise user-facing failure summary only. "
                    "Do not say the tool is still running. "
                    "If the result is insufficient, ask for the missing information instead of assuming background work continues."
                )
            else:
                followup = (
                    f"Tool '{tool_name}' completed successfully. Result:\n\n{result}\n\n"
                    "Analyze this result and continue your plan. "
                    "If you have enough information, give your final recommendation. "
                    "If you need another tool, call it."
                )

        if self._pending_tool_call_id:
            self.conversation_history.append(
                {
                    "role": "tool",
                    "tool_call_id": self._pending_tool_call_id,
                    "name": tool_name,
                    "content": followup,
                }
            )
            self._pending_tool_call_id = None
        else:
            self.conversation_history.append({"role": "user", "content": followup})
        self._trim_conversation_history()
        system_prompt = self._get_default_system_prompt()
        messages = [{"role": "system", "content": system_prompt}] + self.conversation_history
        try:
            response = self._call_inference(messages)

            # If response is empty, retry with trimmed history to reduce context size.
            content = response.get("content", "") or response.get("message", "")
            action = response.get("action")
            if not content and not action:
                logger.warning(
                    "Empty response from model after tool result; retrying with trimmed context"
                )
                # Keep only the last 4 messages (recent context) to fit in context window.
                trimmed_history = self.conversation_history[-4:]
                short_messages = [{"role": "system", "content": system_prompt}] + trimmed_history
                response = self._call_inference(short_messages, include_tools=False)

            self._record_assistant_response(response)
            return response
        except Exception as exc:
            logger.error(f"Error in tool result synthesis: {exc}")
            return {"content": f"Error: {str(exc)}", "error": True}


def _strip_markdown_json(content: str) -> str:
    """Extract likely JSON payload from mixed model text."""
    content = content.strip()

    # If a fenced JSON block exists anywhere, prefer its inner content.
    fenced = re.search(r"```(?:json)?\s*(.*?)\s*```", content, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        return fenced.group(1).strip()

    # Otherwise attempt to isolate the first complete top-level JSON object.
    obj = _extract_first_json_object(content)
    if obj:
        return obj

    return content


def _extract_first_json_object(content: str) -> str | None:
    """Return the first balanced top-level JSON object found in text."""
    start = content.find("{")
    while start != -1:
        depth = 0
        in_string = False
        escaped = False

        for idx in range(start, len(content)):
            ch = content[idx]

            if escaped:
                escaped = False
                continue

            if ch == "\\":
                escaped = True
                continue

            if ch == '"':
                in_string = not in_string
                continue

            if in_string:
                continue

            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return content[start : idx + 1].strip()

        start = content.find("{", start + 1)

    return None


def _extract_structured_response(raw_content: str) -> dict[str, Any]:
    """Parse flexible model outputs into orchestrator-friendly response objects.

    The model is asked to output strict JSON, but some backends occasionally
    return prose mixed with tool snippets (for example <tool_code> blocks).
    This function extracts tool calls when possible and otherwise falls back to
    plain content.
    """
    # Parse a labeled contract before generic JSON extraction. A parameterless
    # labeled tool uses `parameters: {}`; otherwise the generic object finder
    # mistakes that empty map for the entire response.
    labeled = _extract_labeled_tool_response(raw_content)
    if labeled is not None:
        return labeled

    cleaned = _strip_markdown_json(raw_content)

    # Preferred path: strict JSON object response.
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            parsed["_raw"] = raw_content
            _normalize_tool_fields(parsed)
            return parsed
    except json.JSONDecodeError:
        pass

    # Fallback path: <tool_code> ... </tool_code> blocks used by some models.
    tool_block_match = re.search(
        r"<tool_code>\s*(\{.*?\})\s*</tool_code>", raw_content, flags=re.DOTALL
    )
    if tool_block_match:
        block = tool_block_match.group(1)
        try:
            tool_payload = json.loads(block)
            action = (
                tool_payload.get("tool_name")
                or tool_payload.get("tool")
                or tool_payload.get("action")
            )
            parameters = tool_payload.get("parameters", {})
            prose = re.sub(
                r"<tool_code>\s*\{.*?\}\s*</tool_code>", "", raw_content, flags=re.DOTALL
            ).strip()
            result = {
                "action": action,
                "parameters": parameters if isinstance(parameters, dict) else {},
                "message": prose,
                "_raw": raw_content,
            }
            _normalize_tool_fields(result)
            return result
        except (TypeError, ValueError):
            pass

    # Fallback path: prose that includes a JSON object with tool fields.
    json_object_match = re.search(
        r"(\{\s*\"(?:tool_name|tool|action)\".*\})", raw_content, flags=re.DOTALL
    )
    if json_object_match:
        snippet = json_object_match.group(1)
        try:
            parsed = json.loads(snippet)
            if isinstance(parsed, dict):
                result = {
                    "action": parsed.get("tool_name") or parsed.get("tool") or parsed.get("action"),
                    "parameters": parsed.get("parameters", {}),
                    "message": raw_content,
                    "_raw": raw_content,
                }
                _normalize_tool_fields(result)
                return result
        except (TypeError, ValueError):
            pass

    # Small local models sometimes express the exact contract as a labeled block
    # rather than JSON:
    #
    # action: deploy_microcloud
    # parameters:
    #   nodes: 3
    #
    # This is still the model's decision; parse only a known tool and a strict
    # two-space parameter map so surrounding prose can never invent arguments.
    # Fallback path: plain text tool intent, e.g. "Calling inspect_host_environment".
    # Only match if the extracted tool name is one of the known tools.
    calling_match = re.search(
        r"\b(?:calling|run(?:ning)?|execute|use)[:\s]+([a-z_][a-z0-9_]*)\b",
        raw_content,
        flags=re.IGNORECASE,
    )
    if calling_match:
        extracted_tool = calling_match.group(1)
        # Validate against known tools to avoid false positives.
        known_tools = {t["name"] for t in get_tool_definitions().get("tools", []) if t.get("name")}
        if extracted_tool in known_tools:
            return {
                "action": extracted_tool,
                "parameters": {},
                "message": raw_content,
                "_raw": raw_content,
            }

    return {"content": raw_content, "_raw": raw_content}


def _extract_labeled_tool_response(raw_content: str) -> dict[str, Any] | None:
    action_match = re.search(
        r"(?m)^action:\s*([a-z_][a-z0-9_]*)\s*$",
        raw_content,
        flags=re.IGNORECASE,
    )
    if not action_match:
        return None

    action = action_match.group(1)
    known_tools = {t["name"] for t in get_tool_definitions().get("tools", []) if t.get("name")}
    if action not in known_tools:
        return None

    tail = raw_content[action_match.end() :]
    parameters_match = re.match(r"\s*\nparameters:\s*(\{\})?\s*(?:\n|$)", tail)
    if not parameters_match:
        return None

    parameters: dict[str, Any] = {}
    explicit_empty_map = parameters_match.group(1) == "{}"
    if not explicit_empty_map:
        for line in tail[parameters_match.end() :].splitlines():
            if not line.strip():
                continue
            line_match = re.fullmatch(r"  ([a-z_][a-z0-9_]*):\s*(.*?)\s*", line)
            # Parameter lines must be contiguous. Stop at reasoning/missing_params
            # rather than scanning arbitrary later prose.
            if not line_match:
                break
            key = line_match.group(1)
            raw_value = line_match.group(2)
            try:
                value = json.loads(raw_value)
            except (TypeError, ValueError):
                value = raw_value.strip("'\"")
            parameters[key] = value

    if not parameters and not explicit_empty_map:
        return None

    prose = raw_content[: action_match.start()].strip()
    reasoning_match = re.search(r"(?m)^reasoning:\s*(.+)$", tail)
    result: dict[str, Any] = {
        "action": action,
        "parameters": parameters,
        "message": prose,
        "_raw": raw_content,
    }
    if reasoning_match:
        result["reasoning"] = reasoning_match.group(1).strip()
    _normalize_tool_fields(result)
    return result


def _normalize_tool_fields(payload: dict[str, Any]) -> None:
    """Normalize heterogeneous tool call keys to the expected orchestrator schema."""
    if not payload.get("action"):
        payload["action"] = payload.get("tool_name") or payload.get("tool")

    if "parameters" not in payload or not isinstance(payload.get("parameters"), dict):
        payload["parameters"] = {}
