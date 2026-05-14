import os
import time
import streamlit as st
import openai

BACKENDS = {
    "Ollama": {
        "default_url": "http://host.docker.internal:11434/v1",
        "default_model": "llama3",
        "default_key": "ollama",
    },
    "Internal": {
        "default_url": os.environ.get("INTERNAL_LLM_URL") or "http://host.docker.internal:35700/v1",
        "default_model": "",
        "default_key": "",
    },
    "OpenAI": {
        "default_url": "https://api.openai.com/v1",
        "default_model": "gpt-4o-mini",
        "default_key": "",
    },
}

st.set_page_config(page_title="LLM Chatbot", page_icon="💬")
st.title("💬 LLM Chatbot")

# --- Sidebar ---
with st.sidebar:
    st.header("Backend")
    backend = st.selectbox("Provider", list(BACKENDS.keys()))
    cfg = BACKENDS[backend]
    if st.session_state.get("_last_backend") != backend:
        st.session_state.pop("fetched_models", None)
        st.session_state.pop("selected_model", None)
st.session_state["_last_backend"] = backend

    base_url = st.text_input("Base URL", value=cfg["default_url"], key=f"base_url_{backend}")
    api_key = st.text_input(
        "API Key", value=cfg["default_key"], type="password",
        help="Leave as 'ollama' for Ollama, blank if your server needs no key."
    )

    col1, col2 = st.columns([3, 1])
    with col2:
        fetch_clicked = st.button("⟳", help="Fetch available models from the server")
    if fetch_clicked:
        if not base_url:
            st.warning("Enter a Base URL first.")
        else:
            try:
                _client = openai.OpenAI(base_url=base_url, api_key=api_key or "none")
                st.session_state.fetched_models = sorted(
                    m.id for m in _client.models.list().data
                )
            except Exception as e:
                st.error(f"Could not fetch models: {e}")

    fetched = st.session_state.get("fetched_models")
    with col1:
        if fetched:
            default_idx = 0
            prev = st.session_state.get("selected_model", cfg["default_model"])
            if prev in fetched:
                default_idx = fetched.index(prev)
            model = st.selectbox("Model", fetched, index=default_idx)
        else:
            model = st.text_input("Model", value=cfg["default_model"])
    st.session_state.selected_model = model

    st.divider()
    st.subheader("Request params")
    temperature = st.slider("Temperature", 0.0, 2.0, 1.0, step=0.1)
    max_tokens = int(st.number_input("Max tokens", min_value=1, value=2048))
    system_prompt = st.text_area("System prompt", placeholder="Optional system message…")

    st.divider()
    if st.button("Clear chat"):
        st.session_state.messages = []
        st.rerun()


def render_details(stats):
    with st.expander("Details"):
        col1, col2 = st.columns(2)
        with col1:
            st.markdown("**Request**")
            st.json({
                "base_url": stats.get("req_base_url"),
                "model": stats.get("req_model"),
                "temperature": stats.get("req_temperature"),
                "max_tokens": stats.get("req_max_tokens"),
                "system_prompt": stats.get("req_system_prompt"),
                "messages_in_context": stats.get("req_message_count"),
            })
        with col2:
            st.markdown("**Response**")
            usage = stats.get("usage")
            total_time = stats.get("total_time")
            tps = (
                round(usage["completion_tokens"] / total_time, 1)
                if usage and total_time
                else None
            )
            st.json({
                "id": stats.get("resp_id"),
                "model": stats.get("resp_model"),
                "finish_reason": stats.get("finish_reason"),
                "usage": usage,
                "ttft_s": round(stats["ttft"], 3) if stats.get("ttft") else None,
                "total_time_s": round(total_time, 3) if total_time else None,
                "tokens_per_sec": tps,
            })


def stream_response(client, messages, model, temperature, max_tokens, stats):
    t_start = time.time()
    first_token = True
    with client.chat.completions.create(
        model=model,
        messages=messages,
        stream=True,
        stream_options={"include_usage": True},
        temperature=temperature,
        max_tokens=max_tokens,
    ) as stream:
        for chunk in stream:
            if chunk.choices:
                delta = chunk.choices[0].delta.content
                if delta:
                    if first_token:
                        stats["ttft"] = time.time() - t_start
                        first_token = False
                    yield delta
                if chunk.choices[0].finish_reason:
                    stats["finish_reason"] = chunk.choices[0].finish_reason
            if chunk.usage:
                u = chunk.usage
                stats["usage"] = {
                    "prompt_tokens": u.prompt_tokens,
                    "completion_tokens": u.completion_tokens,
                    "total_tokens": u.total_tokens,
                }
            if chunk.model:
                stats["resp_model"] = chunk.model
            if chunk.id:
                stats["resp_id"] = chunk.id
    stats["total_time"] = time.time() - t_start


# --- Chat state ---
if "messages" not in st.session_state:
    st.session_state.messages = []

# Replay history
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
    if msg["role"] == "assistant" and msg.get("stats"):
        render_details(msg["stats"])

# --- Input ---
if prompt := st.chat_input("Type a message…"):
    if not base_url:
        st.error("Set a Base URL in the sidebar first.")
        st.stop()
    if not model:
        st.error("Set a Model name in the sidebar first.")
        st.stop()

    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    api_messages = []
    if system_prompt:
        api_messages.append({"role": "system", "content": system_prompt})
    api_messages += [{"role": m["role"], "content": m["content"]} for m in st.session_state.messages]

    client = openai.OpenAI(base_url=base_url, api_key=api_key or "none")

    stats = {
        "req_base_url": base_url,
        "req_model": model,
        "req_temperature": temperature,
        "req_max_tokens": max_tokens,
        "req_system_prompt": system_prompt or None,
        "req_message_count": len(api_messages),
    }

    with st.chat_message("assistant"):
        try:
            response = st.write_stream(
                stream_response(client, api_messages, model, temperature, max_tokens, stats)
            )
        except Exception as e:
            st.error(f"Error: {e}")
            response = None

    if response:
        st.session_state.messages.append({"role": "assistant", "content": response, "stats": stats})
        render_details(stats)
