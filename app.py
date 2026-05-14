import os
import streamlit as st
import openai

BACKENDS = {
    "Ollama": {
        "default_url": "http://host.docker.internal:11434/v1",
        "default_model": "llama3",
        "default_key": "ollama",
    },
    "Internal": {
        "default_url": os.environ.get("INTERNAL_LLM_URL", ""),
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

    base_url = st.text_input("Base URL", value=cfg["default_url"])
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

    if st.button("Clear chat"):
        st.session_state.messages = []
        st.rerun()

# --- Chat state ---
if "messages" not in st.session_state:
    st.session_state.messages = []

# Replay history
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

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

    client = openai.OpenAI(
        base_url=base_url,
        api_key=api_key or "none",
    )

    def stream_response():
        with client.chat.completions.create(
            model=model,
            messages=st.session_state.messages,
            stream=True,
        ) as stream:
            for chunk in stream:
                delta = chunk.choices[0].delta.content
                if delta:
                    yield delta

    with st.chat_message("assistant"):
        try:
            response = st.write_stream(stream_response())
        except Exception as e:
            st.error(f"Error: {e}")
            response = None

    if response:
        st.session_state.messages.append({"role": "assistant", "content": response})
