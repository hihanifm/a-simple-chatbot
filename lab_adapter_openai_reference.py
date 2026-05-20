"""
Default lab adapter: OpenAI-compatible HTTP via httpx (not the OpenAI SDK).

Used automatically when lab_adapter.py is missing. Provides raw_request/raw_response
in API trace. Copy to lab_adapter.py at the office and replace endpoints/headers/body
mapping for your native API.

Env: LAB_OPENAI_API_KEY (Bearer; default "ollama" for Ollama)
"""

from __future__ import annotations

import json
import os
import time
from types import SimpleNamespace
from typing import Any, Iterator, Optional

import httpx

from llm_client import (
    DEFAULT_OPENAI_CAPABILITIES,
    ChatCompleteResult,
    LLMClient,
    redact_headers,
    usage_to_dict,
)

REDACT_HEADER_NAMES = frozenset({"authorization", "api-key", "x-api-key"})


def _to_ns(obj: Any) -> Any:
    if isinstance(obj, dict):
        return SimpleNamespace(**{k: _to_ns(v) for k, v in obj.items()})
    if isinstance(obj, list):
        return [_to_ns(x) for x in obj]
    return obj


def _parse_openai_completion(data: dict) -> Any:
    """JSON /v1/chat/completions response -> SDK-shaped completion."""
    choices = []
    for ch in data.get("choices") or []:
        msg = ch.get("message") or {}
        tool_calls = msg.get("tool_calls")
        if tool_calls:
            for tc in tool_calls:
                fn = tc.get("function") or {}
                tc["function"] = SimpleNamespace(
                    name=fn.get("name"),
                    arguments=fn.get("arguments") or "{}",
                )
        choices.append(
            SimpleNamespace(
                finish_reason=ch.get("finish_reason"),
                message=_to_ns(msg),
            )
        )
    usage = data.get("usage")
    return SimpleNamespace(
        id=data.get("id"),
        model=data.get("model"),
        choices=choices,
        usage=_to_ns(usage) if usage else None,
    )


class LabOpenAIReferenceAdapter(LLMClient):
    """Lab adapter flavor that speaks OpenAI-compatible /v1 over raw httpx."""

    adapter_kind = "lab"
    capabilities = dict(DEFAULT_OPENAI_CAPABILITIES)

    def __init__(self, base_url: str, api_key: str = ""):
        self.base_url = base_url.rstrip("/")
        self._api_key = api_key or os.environ.get("LAB_OPENAI_API_KEY") or "ollama"

    def build_headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    def _url(self, path: str) -> str:
        if path.startswith("/"):
            return f"{self.base_url}{path}"
        return f"{self.base_url}/{path}"

    def list_models(self) -> list[str]:
        headers = self.build_headers()
        url = self._url("/models")
        with httpx.Client(timeout=30.0) as http:
            resp = http.get(url, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        return sorted(m["id"] for m in data.get("data") or [] if m.get("id"))

    def _chat_body(
        self,
        messages: list,
        model: str,
        temperature: float,
        max_tokens: int,
        tools: Optional[list],
        stream: bool,
    ) -> dict:
        body = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": stream,
        }
        if stream:
            body["stream_options"] = {"include_usage": True}
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        return body

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
        headers = self.build_headers()
        body = self._chat_body(messages, model, temperature, max_tokens, tools, stream=False)
        url = self._url("/chat/completions")
        raw_request = {
            "method": "POST",
            "url": url,
            "headers": redact_headers(headers, REDACT_HEADER_NAMES),
            "body": body,
        }
        with httpx.Client(timeout=120.0) as http:
            resp = http.post(url, headers=headers, json=body)
        raw_response = {
            "status_code": resp.status_code,
            "headers": dict(resp.headers),
            "body": resp.text[:50000] if resp.text else None,
        }
        if resp.status_code >= 400:
            err = httpx.HTTPStatusError(
                "chat completion failed", request=resp.request, response=resp
            )
            raise err
        data = resp.json()
        return ChatCompleteResult(
            completion=_parse_openai_completion(data),
            raw_request=raw_request,
            raw_response=raw_response,
        )

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
        headers = self.build_headers()
        body = self._chat_body(messages, model, temperature, max_tokens, None, stream=True)
        url = self._url("/chat/completions")
        raw_request = {
            "method": "POST",
            "url": url,
            "headers": redact_headers(headers, REDACT_HEADER_NAMES),
            "body": body,
        }
        stats["raw_request"] = raw_request

        t_start = time.time()
        first_token = True
        chunk_count = 0
        content_chunks = 0
        samples = []
        truncated = False
        stream_lines = []

        with httpx.Client(timeout=120.0) as http:
            with http.stream("POST", url, headers=headers, json=body) as resp:
                resp.raise_for_status()
                for line in resp.iter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if payload == "[DONE]":
                        break
                    stream_lines.append(payload[:2000])
                    try:
                        data = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    chunk_count += 1
                    delta_content = None
                    finish_reason = None
                    choices = data.get("choices") or []
                    if choices:
                        delta = (choices[0].get("delta") or {})
                        delta_content = delta.get("content")
                        if delta_content:
                            content_chunks += 1
                            if first_token:
                                stats["ttft"] = time.time() - t_start
                                first_token = False
                            yield delta_content
                        finish_reason = choices[0].get("finish_reason")
                        if finish_reason:
                            stats["finish_reason"] = finish_reason
                    usage = data.get("usage")
                    if usage:
                        stats["usage"] = usage
                    if data.get("model"):
                        stats["resp_model"] = data["model"]
                    if data.get("id"):
                        stats["resp_id"] = data["id"]
                    if trace is not None and len(samples) < max_trace_samples:
                        samples.append({
                            "index": chunk_count,
                            "id": data.get("id"),
                            "model": data.get("model"),
                            "finish_reason": finish_reason,
                            "delta_content": delta_content,
                            "usage": usage,
                        })
                    elif trace is not None and not truncated:
                        truncated = True

                stats["raw_response"] = {
                    "status_code": resp.status_code,
                    "headers": dict(resp.headers),
                    "stream_line_count": len(stream_lines),
                    "stream_lines_sample": stream_lines[:10],
                }

        summary = {
            "chunk_count": chunk_count,
            "content_chunks": content_chunks,
            "truncated": truncated if trace is not None else False,
            "samples": samples,
        }
        stats["stream_trace_summary"] = summary
        stats["total_time"] = time.time() - t_start
