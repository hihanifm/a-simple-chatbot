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

## Key behaviour notes

- **Ollama default URL is `host.docker.internal:11434`** — correct for Docker. If running locally without Docker, change it to `localhost:11434` in the sidebar.
- **Backend switch clears the fetched model list** — `_last_backend` in session state tracks this.
- **`INTERNAL_LLM_URL` env var** pre-fills the Internal backend's Base URL field (still editable at runtime).
- **API key defaults**: Ollama uses `"ollama"` (dummy required by the SDK); Internal/OpenAI default to blank.
- The `⟳` button fetches `/v1/models` from the current base URL and replaces the model text input with a dropdown.

## Docker port convention

| Profile | Host port |
|---------|-----------|
| dev     | 8601      |
| prod    | 8600      |

Both map to container port `8501` (Streamlit default). `extra_hosts: host-gateway` is set so containers can reach host-side services (Ollama) via `host.docker.internal` on both Mac and Linux.

## pip-cache

`pip-cache/` holds pre-downloaded wheels for offline builds. The directory is tracked (`.gitkeep`); the wheels themselves are gitignored. Run `make pip-cache` to populate for the current arch.
