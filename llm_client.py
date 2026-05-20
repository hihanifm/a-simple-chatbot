"""Pluggable LLM clients: OpenAI-compatible SDK and lab custom API."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any, Iterator, Optional

import openai

DEFAULT_OPENAI_CAPABILITIES = {
    "list_models": True,
    "stream": True,
    "tools": True,
}

OPENAI_REDACT_HEADERS = frozenset({
    "authorization",
    "api-key",
    "x-api-key",
    "openai-api-key",
})


def usage_to_dict(usage):
    if not usage:
        return None
    return {
        "prompt_tokens": usage.prompt_tokens,
        "completion_tokens": usage.completion_tokens,
        "total_tokens": usage.total_tokens,
    }


def redact_headers(
    headers: dict[str, str],
    extra_names: frozenset[str] = frozenset(),
) -> dict[str, str]:
    redact = {n.lower() for n in OPENAI_REDACT_HEADERS} | {n.lower() for n in extra_names}
    out = {}
    for k, v in headers.items():
        if k.lower() in redact:
            out[k] = "[REDACTED]"
        else:
            out[k] = v
    return out


@dataclass
class ChatCompleteResult:
    completion: Any
    raw_request: Optional[dict] = None
    raw_response: Optional[dict] = None
    error_body: Any = None


@dataclass
class StreamTraceState:
    chunk_count: int = 0
    content_chunks: int = 0
    samples: list = field(default_factory=list)
    truncated: bool = False


class LLMClient:
    adapter_kind: str = "base"
    capabilities: dict = {}

    def list_models(self) -> list[str]:
        raise NotImplementedError

    def chat_complete(
        self,
        *,
        model: str,
        messages: list,
        temperature: float,
        max_tokens: int,
        tools: Optional[list] = None,
        tool_choice: Optional[str] = None,
    ) -> ChatCompleteResult:
        raise NotImplementedError

    def iter_chat_stream(
        self,
        *,
        model: str,
        messages: list,
        temperature: float,
        max_tokens: int,
        stats: dict,
        trace: Optional[list] = None,
        max_trace_samples: int = 5,
    ) -> Iterator[str]:
        raise NotImplementedError


class OpenAIAdapter(LLMClient):
    adapter_kind = "openai"
    capabilities = dict(DEFAULT_OPENAI_CAPABILITIES)

    def __init__(self, base_url: str, api_key: str):
        self._client = openai.OpenAI(base_url=base_url, api_key=api_key or "none")

    def list_models(self) -> list[str]:
        return sorted(m.id for m in self._client.models.list().data)

    def chat_complete(
        self,
        *,
        model: str,
        messages: list,
        temperature: float,
        max_tokens: int,
        tools: Optional[list] = None,
        tool_choice: Optional[str] = None,
    ) -> ChatCompleteResult:
        kw = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            kw["tools"] = tools
            kw["tool_choice"] = tool_choice or "auto"
        completion = self._client.chat.completions.create(**kw)
        return ChatCompleteResult(completion=completion)

    def iter_chat_stream(
        self,
        *,
        model: str,
        messages: list,
        temperature: float,
        max_tokens: int,
        stats: dict,
        trace: Optional[list] = None,
        max_trace_samples: int = 5,
    ) -> Iterator[str]:
        t_start = time.time()
        first_token = True
        state = StreamTraceState()
        with self._client.chat.completions.create(
            model=model,
            messages=messages,
            stream=True,
            stream_options={"include_usage": True},
            temperature=temperature,
            max_tokens=max_tokens,
        ) as stream:
            for chunk in stream:
                state.chunk_count += 1
                delta_content = None
                finish_reason = None
                if chunk.choices:
                    delta_content = chunk.choices[0].delta.content
                    if delta_content:
                        state.content_chunks += 1
                        if first_token:
                            stats["ttft"] = time.time() - t_start
                            first_token = False
                        yield delta_content
                    finish_reason = chunk.choices[0].finish_reason
                    if finish_reason:
                        stats["finish_reason"] = finish_reason
                if chunk.usage:
                    stats["usage"] = usage_to_dict(chunk.usage)
                if chunk.model:
                    stats["resp_model"] = chunk.model
                if chunk.id:
                    stats["resp_id"] = chunk.id
                if trace is not None:
                    if len(state.samples) < max_trace_samples:
                        state.samples.append({
                            "index": state.chunk_count,
                            "id": chunk.id,
                            "model": chunk.model,
                            "finish_reason": finish_reason,
                            "delta_content": delta_content,
                            "usage": usage_to_dict(chunk.usage) if chunk.usage else None,
                        })
                    elif not state.truncated:
                        state.truncated = True
        summary = {
            "chunk_count": state.chunk_count,
            "content_chunks": state.content_chunks,
            "truncated": state.truncated if trace is not None else False,
            "samples": state.samples,
        }
        stats["stream_trace_summary"] = summary
        stats["total_time"] = time.time() - t_start


def make_llm_client(backend_cfg: dict, base_url: str, api_key: str) -> LLMClient:
    adapter = backend_cfg.get("adapter", "openai")
    if adapter == "lab":
        try:
            from lab_adapter import LabAdapter

            return LabAdapter(base_url)
        except ImportError:
            from lab_adapter_openai_reference import LabOpenAIReferenceAdapter

            return LabOpenAIReferenceAdapter(base_url, api_key)
    return OpenAIAdapter(base_url, api_key)
