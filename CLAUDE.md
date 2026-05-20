# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Streamlit chatbot for testing LLM backends: OpenAI-compatible providers (Ollama, Internal, OpenAI) via `llm_client.OpenAIAdapter`, plus **Lab (custom API)** for a non-OpenAI internal lab server via `lab_adapter.py` (implemented at the office).

## Running locally (no Docker)

```bash
pip install -r requirements.txt
streamlit run app.py
# opens at http://localhost:8501
```

## Running with Docker

```bash
make build && make up    # http://localhost:8600
make logs                # tail logs (prints the URL as a reminder)
```

`make` alone prints all available targets.

## Architecture

- **`app.py`** — Streamlit UI, probes, API trace, message history.
- **`llm_client.py`** — `make_llm_client()`, `OpenAIAdapter`, `redact_headers()`, `ChatCompleteResult`.
- **`lab_adapter.py.example`** — office template; copy to `lab_adapter.py` at the lab (not committed here if sensitive).
- **`lab_adapter_openai_reference.py`** — default when `lab_adapter.py` is missing: OpenAI `/v1` over httpx with **raw_request** / **raw_response** in trace. Lab Base URL defaults to Ollama (`LAB_LLM_URL`).

Key sections in `app.py`:

- **`BACKENDS` dict** — `adapter`: `openai` or `lab`, default URL/model/key. Internal URL falls back to `http://host.docker.internal:35700/v1` if `INTERNAL_LLM_URL` is unset or empty (use `or`, not `get()`). Lab URL uses `LAB_LLM_URL` or `http://host.docker.internal:8080`.
- **Sidebar** — provider selectbox, base URL, API key (hidden for Lab; auth in `lab_adapter.build_headers()`). Capabilities from the client gate model fetch, streaming, and mock probes. Switching provider auto-fetches models when `list_models` is supported.
- **`make_llm_client()`** — returns `OpenAIAdapter`, office `LabAdapter`, or default `LabOpenAIReferenceAdapter`.
- **`stream_response()`** — delegates to `client.iter_chat_stream()`. Captures TTFT, finish_reason, usage into `stats`.
- **Streaming display** — uses `st.empty()` placeholder showing `_Thinking..._` until first token arrives, then appends tokens with `|` cursor manually. Do NOT use `st.write_stream()` (no pre-stream placeholder) or `st.spinner()` inside `st.chat_message` — spinner disrupts the bubble context and causes assistant messages to disappear from history replay.
- **Streaming** — sidebar `Stream responses` checkbox under Request params (default on; disabled when mock probes are on). On: token-by-token via `stream_response()`. Off: single blocking completion via `blocking_chat_response()`.
- **Mock probes (tools/skills)** — when the sidebar system prompt is empty, requests use `PROBE_BASE_SYSTEM_PROMPT` (debugging assistant persona) so the API always gets a real `system` message plus the user turn. Mock skills adds a catalog or full SKILL.md appendix via `build_effective_system_prompt()`. Two skill modes: **Catalog + load_skill** or **Full skill in system**. Uses `run_tool_chat` with mock tools and/or `load_skill`. Skills test prompt: `Hey, how do I ping?`
- **API trace** — per-round **request** / **response** JSON (and **tool_results** / **stream_trace_summary** when relevant). Lab adapter adds **raw_request** / **raw_response** (redacted headers) for debugging the native HTTP API.
- **Office handoff** — implement `lab_adapter.py` from `lab_adapter.py.example` with your internal coding agent; set `capabilities` and `REDACT_HEADER_NAMES`; test with API trace raw panels.
- **`session_state.messages`** — list of `{role, content, stats}` dicts. `stats` is only present on assistant messages. System prompt is prepended to `api_messages` at send time but not stored in history.

## Editing caution

- Always verify syntax with `python3 -c "import ast; ast.parse(open('app.py').read())"` before committing.
- Avoid Unicode characters (e.g. `▌`, `…`) in string literals — they cause SyntaxError inside the Docker Python environment.
- When making changes to indentation-heavy blocks (sidebar `with st.sidebar:`, nested `if/else`), rewrite the full file rather than using surgical edits — incremental edits have repeatedly caused IndentationError.

## Docker

Docker maps host **8600** to container **8501** (Streamlit inside the image). Local `streamlit run app.py` still uses 8501. `extra_hosts: host-gateway` lets the container reach host services via `host.docker.internal` on Mac and Linux. `INTERNAL_LLM_URL` and `LAB_LLM_URL` are set in docker-compose when not in the environment. Compose mounts `app.py`, `llm_client.py`, and `lab_adapter_openai_reference.py` for live edits.

## pip-cache

Docker **build never uses PyPI** — only wheels under `pip-cache/<uname -m>/` (e.g. `pip-cache/x86_64`).

```bash
make pip-cache    # online once per machine arch
make build        # offline; log must show: pip: OFFLINE install from /tmp/pip-cache/...
make up
```

Proxy or `PIP_INDEX_URL` in the environment are honored for `make pip-cache` only if already set; `make build` does not need them. Copy `pip-cache/x86_64/` to office Linux or run `make pip-cache` there.
