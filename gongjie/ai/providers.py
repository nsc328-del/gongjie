from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, AsyncIterator, Protocol

import httpx


@dataclass(slots=True)
class ContextMessage:
    role: str
    content: str


@dataclass(slots=True)
class RequestContext:
    system: str
    messages: list[ContextMessage]
    max_tokens: int | None = None
    temperature: float | None = None


@dataclass(slots=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int | None = None


@dataclass(slots=True)
class ReplySnapshot:
    content: str = ""
    reasoning: str = ""
    finish_reason: str | None = None
    model: str | None = None
    usage: Usage | None = None


@dataclass(slots=True)
class StreamEvent:
    kind: str
    value: Any = None


class ProviderError(Exception):
    def __init__(self, code: str, message: str, *, status: int | None = None, retryable: bool = False, original: str | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.retryable = retryable
        self.original = original

    def public_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "status": self.status, "retryable": self.retryable}


class ProviderAdapter(Protocol):
    id: str

    def build_request(self, context: RequestContext, profile: dict[str, Any], *, stream: bool) -> tuple[str, dict[str, str], dict[str, Any]]: ...

    def parse_stream(self, payload: str) -> list[StreamEvent]: ...

    def parse_response(self, payload: dict[str, Any]) -> ReplySnapshot: ...


def _endpoint(profile: dict[str, Any]) -> str:
    return f"{profile['base_url'].rstrip('/')}/{profile['path'].lstrip('/')}"


def _error_message(value: Any, fallback: str) -> str:
    if isinstance(value, dict):
        return str(value.get("message") or value.get("error") or fallback)
    if isinstance(value, str):
        return value
    return fallback


def classify_error(status: int | None, body: str | None) -> ProviderError:
    lowered = (body or "").lower()
    if status in {401, 403}:
        code = "auth"
    elif status == 429:
        code = "rate_limit"
    elif status == 404:
        code = "model_unavailable"
    elif status == 413 or (status == 400 and any(marker in lowered for marker in ("context too large", "context length", "maximum context", "too many tokens", "request body too large"))):
        code = "context_too_large"
    elif status == 400:
        code = "schema_unsupported"
    elif status is None:
        code = "network"
    else:
        code = "provider"
    retryable = code in {"rate_limit", "network"} or (code == "provider" and (status or 500) >= 500)
    message = (body or f"模型请求失败（{status or '网络'}）").strip()
    try:
        parsed = json.loads(message)
        message = _error_message(parsed.get("error", parsed), message)
    except (json.JSONDecodeError, AttributeError):
        pass
    return ProviderError(code, message, status=status, retryable=retryable, original=body)


class OpenAICompatibleAdapter:
    id = "openai-compatible"

    def build_request(self, context: RequestContext, profile: dict[str, Any], *, stream: bool) -> tuple[str, dict[str, str], dict[str, Any]]:
        messages: list[dict[str, str]] = []
        if context.system:
            messages.append({"role": "system", "content": context.system})
        messages.extend({"role": item.role, "content": item.content} for item in context.messages)
        body: dict[str, Any] = {"model": profile["model"], "messages": messages, "stream": stream}
        if stream:
            body["stream_options"] = {"include_usage": True}
        if context.max_tokens:
            body["max_tokens"] = context.max_tokens
        if context.temperature is not None:
            body["temperature"] = context.temperature
        return _endpoint(profile), {"Authorization": f"Bearer {profile['api_key']}", "Content-Type": "application/json"}, body

    def parse_stream(self, payload: str) -> list[StreamEvent]:
        if payload == "[DONE]":
            return [StreamEvent("done")]
        try:
            obj = json.loads(payload)
        except json.JSONDecodeError:
            return []
        if "error" in obj:
            message = _error_message(obj["error"], payload)
            return [StreamEvent("error", ProviderError("provider", message, original=payload))]
        events: list[StreamEvent] = []
        if obj.get("model"):
            events.append(StreamEvent("model", obj["model"]))
        choices = obj.get("choices") or []
        if choices:
            choice = choices[0]
            delta = choice.get("delta") or {}
            content = delta.get("content")
            if isinstance(content, str) and content:
                events.append(StreamEvent("text", content))
            reasoning = delta.get("reasoning_content") or delta.get("reasoning")
            if isinstance(reasoning, str) and reasoning:
                events.append(StreamEvent("reasoning", reasoning))
            if choice.get("finish_reason") is not None:
                events.append(StreamEvent("done", choice["finish_reason"]))
        if obj.get("usage"):
            usage = obj["usage"]
            details = usage.get("prompt_tokens_details") or usage.get("input_tokens_details") or {}
            events.append(StreamEvent("usage", Usage(
                input_tokens=int(usage.get("prompt_tokens", usage.get("input_tokens", 0)) or 0),
                output_tokens=int(usage.get("completion_tokens", usage.get("output_tokens", 0)) or 0),
                cached_input_tokens=details.get("cached_tokens", usage.get("cache_read_input_tokens")),
            )))
        return events

    def parse_response(self, payload: dict[str, Any]) -> ReplySnapshot:
        if "error" in payload:
            raise ProviderError("provider", _error_message(payload["error"], json.dumps(payload, ensure_ascii=False)))
        choice = (payload.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        content = message.get("content") or ""
        if isinstance(content, list):
            content = "".join(item.get("text", "") for item in content if isinstance(item, dict) and item.get("type") == "text")
        usage_obj = payload.get("usage") or {}
        details = usage_obj.get("prompt_tokens_details") or usage_obj.get("input_tokens_details") or {}
        usage = Usage(
            input_tokens=int(usage_obj.get("prompt_tokens", usage_obj.get("input_tokens", 0)) or 0),
            output_tokens=int(usage_obj.get("completion_tokens", usage_obj.get("output_tokens", 0)) or 0),
            cached_input_tokens=details.get("cached_tokens", usage_obj.get("cache_read_input_tokens")),
        ) if usage_obj else None
        return ReplySnapshot(content=str(content), finish_reason=choice.get("finish_reason"), model=payload.get("model"), usage=usage)


class AnthropicMessagesAdapter:
    id = "anthropic-messages"

    def build_request(self, context: RequestContext, profile: dict[str, Any], *, stream: bool) -> tuple[str, dict[str, str], dict[str, Any]]:
        messages = [{"role": item.role, "content": item.content} for item in context.messages if item.role in {"user", "assistant"}]
        body: dict[str, Any] = {
            "model": profile["model"], "messages": messages, "stream": stream,
            "max_tokens": context.max_tokens or 64000,
        }
        if context.system:
            body["system"] = context.system
        if context.temperature is not None:
            body["temperature"] = context.temperature
        return _endpoint(profile), {
            "x-api-key": profile["api_key"], "anthropic-version": "2023-06-01", "Content-Type": "application/json",
        }, body

    def parse_stream(self, payload: str) -> list[StreamEvent]:
        try:
            obj = json.loads(payload)
        except json.JSONDecodeError:
            return []
        event_type = obj.get("type")
        if event_type == "error":
            message = _error_message(obj.get("error"), payload)
            return [StreamEvent("error", ProviderError("provider", message, original=payload))]
        if event_type == "message_start":
            message = obj.get("message") or {}
            events = [StreamEvent("model", message.get("model"))] if message.get("model") else []
            if message.get("usage"):
                events.append(StreamEvent("usage", self._usage(message["usage"])))
            return events
        if event_type == "content_block_delta":
            delta = obj.get("delta") or {}
            if delta.get("type") == "text_delta":
                return [StreamEvent("text", delta.get("text", ""))]
            if delta.get("type") == "thinking_delta":
                return [StreamEvent("reasoning", delta.get("thinking", ""))]
        if event_type == "message_delta":
            events = [StreamEvent("done", (obj.get("delta") or {}).get("stop_reason"))]
            if obj.get("usage"):
                events.append(StreamEvent("usage", self._usage(obj["usage"])))
            return events
        if event_type == "message_stop":
            return [StreamEvent("done")]
        return []

    def parse_response(self, payload: dict[str, Any]) -> ReplySnapshot:
        if payload.get("type") == "error" or "error" in payload:
            raise ProviderError("provider", _error_message(payload.get("error"), json.dumps(payload, ensure_ascii=False)))
        content = "".join(item.get("text", "") for item in payload.get("content", []) if item.get("type") == "text")
        reasoning = "".join(item.get("thinking", "") for item in payload.get("content", []) if item.get("type") == "thinking")
        return ReplySnapshot(content=content, reasoning=reasoning, finish_reason=payload.get("stop_reason"), model=payload.get("model"), usage=self._usage(payload["usage"]) if payload.get("usage") else None)

    @staticmethod
    def _usage(value: dict[str, Any]) -> Usage:
        return Usage(
            input_tokens=int(value.get("input_tokens", 0) or 0),
            output_tokens=int(value.get("output_tokens", 0) or 0),
            cached_input_tokens=value.get("cache_read_input_tokens"),
        )


ADAPTERS: dict[str, ProviderAdapter] = {
    "openai-compatible": OpenAICompatibleAdapter(),
    "anthropic-messages": AnthropicMessagesAdapter(),
}


class ProviderClient:
    def __init__(self, timeout: float = 120.0):
        self.timeout = httpx.Timeout(timeout, connect=20.0)

    @staticmethod
    def adapter(profile: dict[str, Any]) -> ProviderAdapter:
        try:
            return ADAPTERS[profile["protocol"]]
        except KeyError as error:
            raise ProviderError("provider", "不支持的模型协议", retryable=False) from error

    async def stream(self, context: RequestContext, profile: dict[str, Any]) -> AsyncIterator[ReplySnapshot]:
        adapter = self.adapter(profile)
        url, headers, body = adapter.build_request(context, profile, stream=True)
        snapshot = ReplySnapshot()
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                async with client.stream("POST", url, headers=headers, json=body) as response:
                    if response.status_code < 200 or response.status_code >= 300:
                        raw = (await response.aread()).decode("utf-8", errors="replace")
                        raise classify_error(response.status_code, raw)
                    data_lines: list[str] = []
                    async for line in response.aiter_lines():
                        if line.startswith("data:"):
                            data_lines.append(line[5:].lstrip())
                            continue
                        if line == "" and data_lines:
                            payload = "\n".join(data_lines)
                            data_lines.clear()
                            for event in adapter.parse_stream(payload):
                                self._apply(snapshot, event)
                                if event.kind != "done" or event.value:
                                    yield ReplySnapshot(snapshot.content, snapshot.reasoning, snapshot.finish_reason, snapshot.model, snapshot.usage)
                    if data_lines:
                        for event in adapter.parse_stream("\n".join(data_lines)):
                            self._apply(snapshot, event)
                            yield ReplySnapshot(snapshot.content, snapshot.reasoning, snapshot.finish_reason, snapshot.model, snapshot.usage)
        except httpx.HTTPError as error:
            raise ProviderError("network", str(error), retryable=True) from error

    async def response(self, context: RequestContext, profile: dict[str, Any]) -> ReplySnapshot:
        adapter = self.adapter(profile)
        url, headers, body = adapter.build_request(context, profile, stream=False)
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(url, headers=headers, json=body)
        except httpx.HTTPError as error:
            raise ProviderError("network", str(error), retryable=True) from error
        if response.status_code < 200 or response.status_code >= 300:
            raise classify_error(response.status_code, response.text)
        try:
            payload = response.json()
        except json.JSONDecodeError as error:
            raise ProviderError("provider", "模型返回了无法解析的内容", status=response.status_code, original=response.text) from error
        return adapter.parse_response(payload)

    @staticmethod
    def _apply(snapshot: ReplySnapshot, event: StreamEvent) -> None:
        if event.kind == "text":
            snapshot.content += str(event.value or "")
        elif event.kind == "reasoning":
            snapshot.reasoning += str(event.value or "")
        elif event.kind == "model":
            snapshot.model = event.value
        elif event.kind == "usage":
            snapshot.usage = event.value
        elif event.kind == "done":
            if event.value:
                snapshot.finish_reason = str(event.value)
        elif event.kind == "error":
            raise event.value
