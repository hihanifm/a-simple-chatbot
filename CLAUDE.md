# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A single-file Streamlit chatbot for testing OpenAI-compatible LLM backends — Ollama, an internal server, or OpenAI itself. All logic lives in `app.py`.

## Running locally (no Docker)

```bash
pip install -r requirements.txt
streamlit run app.py
# opens at http://localhost:8501
```

## Running with Docker

```bash
make build && make up    # dev — http://localhost:8601
make prod-up             # prod — http://localhost:8600
make logs                # tail logs (prints the URL as a reminder)
```

`make` alone prints all available targets.

## app.py architecture

All code is in one file. Key sections:

- **`BACKENDS` dict** — default URL, model, and API key per provider. Internal URL falls back to `http://host.docker.internal:35700/v1` if `INTERNAL_LLM_URL` env var is unset or empty (use `or`, not `get()` default, because docker-compose sets it to empty string).
- **Sidebar** — provider selectbox, base URL / API key inputs, "Fetch available models" button, request params (temperature, max_tokens, system prompt), clear chat. Switching provider auto-fetches models via `/v1/models` and clears prior model selection; `_last_backend` in session state tracks the current provider.
- **`stream_response()` generator** — wraps `openai.OpenAI(base_url=...).chat.completions.create(stream=True)`. Captures TTFT, finish_reason, usage (via `stream_options={"include_usage": True}`), response id/model into a mutable `stats` dict passed by reference.
- **Streaming display** — uses `st.empty()` placeholder showing `|` cursor until first token arrives, then appends tokens manually. Do NOT use `st.write_stream()` here — it doesn't support the pre-stream cursor.
- **`render_details(stats)`** — collapsible expander showing request params (left col) and response metadata (right col): finish_reason, token counts, TTFT, total time, tokens/sec.
- **`session_state.messages`** — list of `{role, content, stats}` dicts. `stats` is only present on assistant messages. System prompt is prepended to `api_messages` at send time but not stored in history.

## Editing caution

- Always verify syntax with `python3 -c "import ast; ast.parse(open('app.py').read())"` before committing.
- Avoid Unicode characters (e.g. `▌`, `…`) in string literals — they cause SyntaxError inside the Docker Python environment.
- When making changes to indentation-heavy blocks (sidebar `with st.sidebar:`, nested `if/else`), rewrite the full file rather than using surgical edits — incremental edits have repeatedly caused IndentationError.

## Docker port convention

| Profile | Host port |
|---------|-----------|
| dev     | 8601      |
| prod    | 8600      |

Both map to container port `8501`. `extra_hosts: host-gateway` lets containers reach host services via `host.docker.internal` on both Mac and Linux. `INTERNAL_LLM_URL` defaults to `http://host.docker.internal:35700/v1` in docker-compose if not set in the environment.

## pip-cache

`pip-cache/` holds pre-downloaded wheels for offline builds. Directory is tracked (`.gitkeep`); wheels are gitignored. Run `make pip-cache` to populate for the current arch.
