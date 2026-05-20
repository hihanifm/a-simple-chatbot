import json
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

MOCK_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read contents of a file at the given path",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path to read"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "execute_shell",
            "description": "Run a shell command and return stdout/stderr",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command to run"},
                },
                "required": ["command"],
            },
        },
    },
]
MOCK_TOOL_NAMES = frozenset({"read_file", "execute_shell"})
MAX_TOOL_ROUNDS = 3
TOOL_TEST_PROMPT = (
    "You have read_file and execute_shell. Read /tmp/config.txt, run uname -a, "
    "then summarize what you found in one paragraph."
)

CURSOR = "|"
VERSION = open(os.path.join(os.path.dirname(__file__), "VERSION")).read().strip()

st.set_page_config(page_title="LLM Chatbot", page_icon="💬")
st.title("LLM Chatbot")


def mock_tool_handler(name, arguments_str):
    try:
        args = json.loads(arguments_str or "{}")
    except json.JSONDecodeError as e:
        return f"[MOCK_ERROR] invalid JSON: {e}"
    if name == "read_file":
        path = args.get("path", "")
        return f"[MOCK_READ] path={path}\nline1=alpha\nline2=beta\nline3=gamma"
    if name == "execute_shell":
        cmd = args.get("command", "")
        return (
            f"[MOCK_SHELL] command={cmd}\nexit_code: 0\nstdout:\nmock output for: {cmd}"
        )
    return f"[MOCK_ERROR] unknown handler for {name}"


def message_to_dict(msg):
    d = {"role": msg.role}
    if msg.content is not None:
        d["content"] = msg.content
    if msg.tool_calls:
        d["tool_calls"] = [
            {
                "id": tc.id,
                "type": tc.type,
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments,
                },
            }
            for tc in msg.tool_calls
        ]
    return d


def usage_to_dict(usage):
    if not usage:
        return None
    return {
        "prompt_tokens": usage.prompt_tokens,
        "completion_tokens": usage.completion_tokens,
        "total_tokens": usage.total_tokens,
    }


def add_failure(failures, round_num, kind, message):
    failures.append({"round": round_num, "kind": kind, "message": message})


def compute_tool_verdict(tool_trace, tool_failures, final_content):
    first_api_error = any(
        f["kind"] == "api_error" and f.get("round", 0) <= 1 for f in tool_failures
    )
    emitted = False
    valid_names = True
    tool_results_sent = False
    for rnd in tool_trace:
        msg = (rnd.get("response") or {}).get("message") or {}
        tcs = msg.get("tool_calls") or []
        if tcs:
            emitted = True
            for tc in tcs:
                fn = (tc.get("function") or {}).get("name")
                if fn and fn not in MOCK_TOOL_NAMES:
                    valid_names = False
        if rnd.get("tool_results"):
            tool_results_sent = True

    completed = bool(final_content) and tool_results_sent
    referenced = bool(
        final_content
        and ("[MOCK_READ]" in final_content or "[MOCK_SHELL]" in final_content)
    )

    notes = [f["message"] for f in tool_failures if f["kind"] != "tools_ignored"]

    return {
        "accepts_tools": not first_api_error,
        "emitted_tool_calls": emitted,
        "valid_tool_names": valid_names,
        "completed_after_tools": completed,
        "referenced_mock_data": referenced,
        "notes": notes,
        "failed": bool(tool_failures)
        or not (not first_api_error and emitted and valid_names and completed),
    }


def build_api_messages(session_messages, system_prompt):
    api_messages = []
    if system_prompt:
        api_messages.append({"role": "system", "content": system_prompt})
    for m in session_messages:
        if m["role"] == "user":
            api_messages.append({"role": "user", "content": m["content"]})
        elif m["role"] == "assistant":
            for rnd in m.get("tool_trace") or []:
                resp = rnd.get("response") or {}
                msg = resp.get("message")
                if msg:
                    api_messages.append(msg)
                for tr in rnd.get("tool_results") or []:
                    api_messages.append({
                        "role": "tool",
                        "tool_call_id": tr["tool_call_id"],
                        "content": tr["content"],
                    })
            content = m.get("content")
            if content:
                last = api_messages[-1] if api_messages else None
                if not (
                    last
                    and last.get("role") == "assistant"
                    and last.get("content") == content
                    and not last.get("tool_calls")
                ):
                    api_messages.append({"role": "assistant", "content": content})
    return api_messages


def render_assistant_bubble(msg):
    for f in msg.get("tool_failures") or []:
        if f.get("kind") == "tools_ignored":
            st.warning(f["message"])
        else:
            st.error(f["message"])
    if msg.get("content"):
        st.markdown(msg["content"])
    elif msg.get("tool_failures"):
        st.markdown("_No assistant text; see errors above._")


def render_tool_trace(msg):
    trace = msg.get("tool_trace")
    if not trace:
        return
    verdict = msg.get("tool_verdict") or {}
    failures = msg.get("tool_failures") or []
    header_fail = "FAIL | " if verdict.get("failed") or failures else ""
    checks = []
    for key, label in [
        ("accepts_tools", "accepts_tools"),
        ("emitted_tool_calls", "emitted_tool_calls"),
        ("valid_tool_names", "valid_tool_names"),
        ("completed_after_tools", "completed_after_tools"),
    ]:
        val = verdict.get(key)
        checks.append(f"{label} {'OK' if val else 'FAIL'}")
    ref = verdict.get("referenced_mock_data")
    checks.append(f"referenced_mock_data {'OK' if ref else 'heuristic FAIL'}")
    with st.expander("Tool trace"):
        st.markdown(f"**{header_fail}**" + " | ".join(checks))
        for note in verdict.get("notes") or []:
            st.caption(note)
        for rnd in trace:
            rn = rnd.get("round", "?")
            with st.expander(f"Round {rn}"):
                for rf in rnd.get("round_failures") or []:
                    st.error(rf.get("message", rf))
                if rnd.get("error_response") is not None:
                    st.markdown("**error_response**")
                    st.json(rnd["error_response"])
                st.markdown("**request**")
                st.json(rnd.get("request"))
                st.markdown("**response**")
                st.json(rnd.get("response"))
                if rnd.get("tool_results") is not None:
                    st.markdown("**tool_results**")
                    st.json(rnd.get("tool_results"))


def run_tool_chat(client, api_messages, model, temperature, max_tokens, stats, status=None):
    tool_trace = []
    tool_failures = []
    messages = list(api_messages)
    final_content = None
    tool_results_sent = False
    last_completion = None

    for round_num in range(1, MAX_TOOL_ROUNDS + 1):
        request_snapshot = {
            "messages": json.loads(json.dumps(messages)),
            "tools": MOCK_TOOLS,
            "tool_choice": "auto",
            "model": model,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        round_failures = []
        t_start = time.time()

        if status:
            status.update(label=f"Round {round_num}: calling API...")

        try:
            completion = client.chat.completions.create(
                model=model,
                messages=messages,
                tools=MOCK_TOOLS,
                tool_choice="auto",
                temperature=temperature,
                max_tokens=max_tokens,
            )
        except Exception as e:
            body = getattr(e, "body", None)
            err_msg = f"API error (round {round_num}): {type(e).__name__}: {e}"
            add_failure(tool_failures, round_num, "api_error", err_msg)
            round_failures.append({"kind": "api_error", "message": err_msg})
            tool_trace.append({
                "round": round_num,
                "request": request_snapshot,
                "response": None,
                "tool_results": [],
                "round_failures": round_failures,
                "error_response": body,
            })
            break

        elapsed = time.time() - t_start
        last_completion = completion

        if not completion.choices:
            err_msg = "Unexpected response: no choices in completion"
            add_failure(tool_failures, round_num, "empty_response", err_msg)
            round_failures.append({"kind": "empty_response", "message": err_msg})
            tool_trace.append({
                "round": round_num,
                "request": request_snapshot,
                "response": {"finish_reason": None, "message": None, "elapsed_s": round(elapsed, 3)},
                "tool_results": [],
                "round_failures": round_failures,
            })
            break

        choice = completion.choices[0]
        msg = choice.message
        assistant_dict = message_to_dict(msg)
        response_snapshot = {
            "id": completion.id,
            "model": completion.model,
            "finish_reason": choice.finish_reason,
            "message": assistant_dict,
            "usage": usage_to_dict(completion.usage),
            "elapsed_s": round(elapsed, 3),
        }

        tool_calls = msg.tool_calls or []
        tool_results = []

        if tool_calls:
            if status:
                status.update(label=f"Round {round_num}: {len(tool_calls)} tool call(s)")

            malformed = []
            for tc in tool_calls:
                if not tc.id or not tc.function or not tc.function.name:
                    malformed.append("tool_call missing id or function.name")
                    continue
                name = tc.function.name
                if name not in MOCK_TOOL_NAMES:
                    err_msg = (
                        f'Unknown tool "{name}" (round {round_num}); '
                        f"expected read_file or execute_shell"
                    )
                    add_failure(tool_failures, round_num, "unknown_tool", err_msg)
                    round_failures.append({"kind": "unknown_tool", "message": err_msg})
                try:
                    json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError as e:
                    err_msg = f"Malformed tool_calls in round {round_num}: {e}"
                    add_failure(tool_failures, round_num, "malformed_tool_calls", err_msg)
                    round_failures.append({"kind": "malformed_tool_calls", "message": err_msg})
                    malformed.append(str(e))

            if malformed and not round_failures:
                err_msg = f"Malformed tool_calls in round {round_num}: {'; '.join(malformed)}"
                add_failure(tool_failures, round_num, "malformed_tool_calls", err_msg)
                round_failures.append({"kind": "malformed_tool_calls", "message": err_msg})

            messages.append(assistant_dict)
            for tc in tool_calls:
                if not tc.id or not tc.function:
                    continue
                content = mock_tool_handler(tc.function.name, tc.function.arguments)
                tool_msg = {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": content,
                }
                messages.append(tool_msg)
                tool_results.append({
                    "tool_call_id": tc.id,
                    "name": tc.function.name,
                    "arguments": tc.function.arguments,
                    "content": content,
                })
            tool_results_sent = True

            tool_trace.append({
                "round": round_num,
                "request": request_snapshot,
                "response": response_snapshot,
                "tool_results": tool_results,
                "round_failures": round_failures,
            })

            if round_num >= MAX_TOOL_ROUNDS:
                err_msg = "Stopped: model kept requesting tools after 3 rounds"
                add_failure(tool_failures, round_num, "max_rounds", err_msg)
                break
            continue

        if msg.content:
            final_content = msg.content
            tool_trace.append({
                "round": round_num,
                "request": request_snapshot,
                "response": response_snapshot,
                "tool_results": [],
                "round_failures": round_failures,
            })
            break

        err_msg = (
            f"Unexpected response: empty assistant message "
            f"(finish_reason={choice.finish_reason})"
        )
        add_failure(tool_failures, round_num, "stuck_no_content", err_msg)
        round_failures.append({"kind": "stuck_no_content", "message": err_msg})
        tool_trace.append({
            "round": round_num,
            "request": request_snapshot,
            "response": response_snapshot,
            "tool_results": [],
            "round_failures": round_failures,
        })
        break

    if final_content and not any(
        (r.get("response") or {}).get("message", {}).get("tool_calls") for r in tool_trace
    ):
        warn_msg = "Model replied with text only; no tool_calls"
        add_failure(tool_failures, 0, "tools_ignored", warn_msg)

    tool_verdict = compute_tool_verdict(tool_trace, tool_failures, final_content)

    if last_completion:
        stats["resp_id"] = last_completion.id
        stats["resp_model"] = last_completion.model
        if last_completion.choices:
            stats["finish_reason"] = last_completion.choices[0].finish_reason
        stats["usage"] = usage_to_dict(last_completion.usage)

    total_elapsed = sum((r.get("response") or {}).get("elapsed_s") or 0 for r in tool_trace)
    stats["total_time"] = total_elapsed
    stats["tool_rounds"] = len(tool_trace)

    return final_content, tool_trace, tool_verdict, tool_failures, stats


# --- Sidebar ---
with st.sidebar:
    st.header("Backend")
    backend = st.selectbox("Provider", list(BACKENDS.keys()))
    cfg = BACKENDS[backend]

    if st.session_state.get("_last_backend") != backend:
        st.session_state.pop("fetched_models", None)
        st.session_state.pop("selected_model", None)
        st.session_state["_last_backend"] = backend
        try:
            _c = openai.OpenAI(base_url=cfg["default_url"], api_key=cfg["default_key"] or "none")
            st.session_state.fetched_models = sorted(m.id for m in _c.models.list().data)
        except Exception:
            pass

    base_url = st.text_input("Base URL", value=cfg["default_url"], key=f"base_url_{backend}")
    api_key = st.text_input(
        "API Key", value=cfg["default_key"], type="password",
        help="Leave as 'ollama' for Ollama, blank if your server needs no key.",
    )

    if st.button("Fetch available models", use_container_width=True):
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
    if fetched:
        prev = st.session_state.get("selected_model", cfg["default_model"])
        default_idx = fetched.index(prev) if prev in fetched else 0
        model = st.selectbox("Model", fetched, index=default_idx)
    else:
        model = st.text_input("Model", value=cfg["default_model"])
    st.session_state.selected_model = model

    st.divider()
    st.subheader("Request params")
    temperature = st.slider("Temperature", 0.0, 2.0, 1.0, step=0.1)
    max_tokens = int(st.number_input("Max tokens", min_value=1, value=2048))
    system_prompt = st.text_area("System prompt", placeholder="Optional system message...")

    st.divider()
    st.subheader("Tool calling test")
    enable_mock_tools = st.checkbox(
        "Enable mock tools",
        value=False,
        help="Simulated read_file and execute_shell only. No disk or shell access.",
    )
    if enable_mock_tools:
        st.caption("Tools are fake; nothing runs on the host.")
        with st.expander("Suggested test prompt"):
            st.code(TOOL_TEST_PROMPT, language=None)

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
                "mock_tools": stats.get("mock_tools"),
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
                "tool_rounds": stats.get("tool_rounds"),
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
                stats["usage"] = usage_to_dict(u)
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
        if msg["role"] == "assistant":
            render_assistant_bubble(msg)
        else:
            st.markdown(msg["content"])
    if msg["role"] == "assistant":
        if msg.get("tool_trace"):
            render_tool_trace(msg)
        if msg.get("stats"):
            render_details(msg["stats"])

# --- Input ---
if prompt := st.chat_input("Type a message..."):
    if not base_url:
        st.error("Set a Base URL in the sidebar first.")
        st.stop()
    if not model:
        st.error("Set a Model name in the sidebar first.")
        st.stop()

    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    api_messages = build_api_messages(st.session_state.messages, system_prompt)

    client = openai.OpenAI(base_url=base_url, api_key=api_key or "none")

    stats = {
        "req_base_url": base_url,
        "req_model": model,
        "req_temperature": temperature,
        "req_max_tokens": max_tokens,
        "req_system_prompt": system_prompt or None,
        "req_message_count": len(api_messages),
        "mock_tools": enable_mock_tools,
    }

    if enable_mock_tools:
        with st.chat_message("assistant"):
            final_content = None
            tool_trace = []
            tool_verdict = {}
            tool_failures = []
            with st.status("Tool calling...", expanded=True) as status:
                try:
                    final_content, tool_trace, tool_verdict, tool_failures, stats = run_tool_chat(
                        client, api_messages, model, temperature, max_tokens, stats, status=status
                    )
                    status.update(label="Tool calling finished", state="complete")
                except Exception as e:
                    status.update(label="Tool calling failed", state="error")
                    add_failure(tool_failures, 0, "api_error", f"Error: {e}")
            render_assistant_bubble({
                "content": final_content,
                "tool_failures": tool_failures,
            })
            if tool_trace:
                render_tool_trace({
                    "tool_trace": tool_trace,
                    "tool_verdict": tool_verdict,
                    "tool_failures": tool_failures,
                })

        st.session_state.messages.append({
            "role": "assistant",
            "content": final_content,
            "stats": stats,
            "tool_trace": tool_trace,
            "tool_verdict": tool_verdict,
            "tool_failures": tool_failures,
        })
        render_details(stats)
    else:
        with st.chat_message("assistant"):
            placeholder = st.empty()
            placeholder.markdown("_Thinking..._")
            response = ""
            try:
                for token in stream_response(
                    client, api_messages, model, temperature, max_tokens, stats
                ):
                    response += token
                    placeholder.markdown(response + CURSOR)
                placeholder.markdown(response)
            except Exception as e:
                placeholder.empty()
                st.error(f"Error: {e}")
                response = None

        if response is not None:
            st.session_state.messages.append({
                "role": "assistant",
                "content": response,
                "stats": stats,
            })
            render_details(stats)

# --- Bottom bar ---
_status_color = "#22c55e" if st.session_state.get("fetched_models") else "#94a3b8"
_status_label = "online" if st.session_state.get("fetched_models") else "unknown"
_display_url = base_url[:40] + "..." if len(base_url) > 40 else base_url
st.markdown(f"""
<style>
.bottom-bar {{
    position: fixed; bottom: 0; left: 0; right: 0;
    height: 30px; z-index: 30;
    background: var(--background-color);
    border-top: 1px solid rgba(128,128,128,0.2);
    display: flex; align-items: center;
    padding: 0 16px; gap: 14px;
    font-size: 12px; opacity: 0.75;
}}
.bottom-bar .dot {{
    width: 8px; height: 8px; border-radius: 50%;
    background: {_status_color}; display: inline-block;
}}
.main .block-container {{ padding-bottom: 48px !important; }}
</style>
<div class="bottom-bar">
    <strong>LLM Chatbot</strong>
    <span>{backend}</span>
    <span style="opacity:0.6">{_display_url}</span>
    <span><span class="dot"></span>&nbsp;{_status_label}</span>
    <span style="margin-left:auto; opacity:0.5">v{VERSION}</span>
</div>
""", unsafe_allow_html=True)
