import json
import os
import time
import streamlit as st

from llm_client import make_llm_client

BACKENDS = {
    "Ollama": {
        "adapter": "openai",
        "default_url": "http://host.docker.internal:11434/v1",
        "default_model": "llama3",
        "default_key": "ollama",
    },
    "Internal": {
        "adapter": "openai",
        "default_url": os.environ.get("INTERNAL_LLM_URL") or "http://host.docker.internal:35700/v1",
        "default_model": "",
        "default_key": "",
    },
    "OpenAI": {
        "adapter": "openai",
        "default_url": "https://api.openai.com/v1",
        "default_model": "gpt-4o-mini",
        "default_key": "",
    },
    "Lab (custom API)": {
        "adapter": "lab",
        "default_url": os.environ.get("LAB_LLM_URL") or "http://host.docker.internal:8080",
        "default_model": "",
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
PROBE_BASE_SYSTEM_PROMPT = (
    "You are a debugging and analysis assistant. Help the user investigate "
    "issues clearly and follow any loaded agent skills."
)
SKILLS_TEST_PROMPT = "Hey, how do I ping?"
LOAD_SKILL_TOOL = {
    "type": "function",
    "function": {
        "name": "load_skill",
        "description": (
            "Load full instructions for an agent skill by name when the user task matches."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "skill_name": {
                    "type": "string",
                    "description": "Skill name from the catalog, e.g. mock-ping",
                },
            },
            "required": ["skill_name"],
        },
    },
}
SKILL_PROBE_MODES = ("catalog_tool", "full_system")
MOCK_SKILL_PING_MD = """---
name: mock-ping
description: >-
  Test probe skill. When the user says ping, reply PONG and cite mock-ping.
---

# Mock ping

If the user says ping, respond PONG and include the marker [MOCK_SKILL:mock-ping].
"""
MAX_STREAM_TRACE_SAMPLES = 5

CURSOR = "|"
VERSION = open(os.path.join(os.path.dirname(__file__), "VERSION")).read().strip()

st.set_page_config(page_title="LLM Chatbot", page_icon="💬")

st.markdown(
    """
<style>
button[data-testid="stBaseButton-clear_chat"],
button[data-testid="baseButton-clear_chat"] {
    background: linear-gradient(180deg, #f4a261 0%, #e76f51 100%) !important;
    color: #fff !important;
    border: 1px solid #c96a32 !important;
    font-weight: 600 !important;
}
button[data-testid="stBaseButton-clear_chat"]:hover,
button[data-testid="baseButton-clear_chat"]:hover {
    background: linear-gradient(180deg, #e76f51 0%, #d45d3e 100%) !important;
    border-color: #a85a28 !important;
    color: #fff !important;
}
</style>
""",
    unsafe_allow_html=True,
)

_header_left, _header_right = st.columns([5, 1], vertical_alignment="center")
with _header_left:
    st.title("LLM Chatbot")
with _header_right:
    if st.button("Clear chat", key="clear_chat", use_container_width=True):
        st.session_state.messages = []
        st.rerun()


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
    if name == "load_skill":
        skill_name = args.get("skill_name", "")
        if skill_name == "mock-ping":
            return MOCK_SKILL_PING_MD
        return f"[MOCK_ERROR] unknown skill: {skill_name}"
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


def api_error_body(exc):
    body = getattr(exc, "body", None)
    if body is not None:
        return body
    resp = getattr(exc, "response", None)
    if resp is None:
        return None
    try:
        return resp.json()
    except Exception:
        text = getattr(resp, "text", None) or str(resp)
        return {"text": text[:5000]}


def add_failure(failures, round_num, kind, message):
    failures.append({"round": round_num, "kind": kind, "message": message})


def parse_skill_frontmatter(skill_md):
    text = skill_md.strip()
    if not text.startswith("---"):
        return None, None
    end = text.find("---", 3)
    if end < 0:
        return None, None
    front = text[3:end].strip()
    name = None
    desc_lines = []
    in_desc = False
    for line in front.split("\n"):
        if line.startswith("name:"):
            name = line[5:].strip()
            in_desc = False
        elif line.startswith("description:"):
            rest = line[12:].strip()
            if rest in (">-", ">"):
                desc_lines = []
                in_desc = True
            else:
                desc_lines = [rest.lstrip(">- ").strip()]
                in_desc = False
        elif in_desc:
            desc_lines.append(line.strip())
    description = " ".join(desc_lines).strip() if desc_lines else None
    return name, description


def _init_mock_skills():
    name, description = parse_skill_frontmatter(MOCK_SKILL_PING_MD)
    if not name or not description:
        return [], False, "Bundled SKILL.md frontmatter parse failed"
    skills = [{"name": name, "description": description, "content": MOCK_SKILL_PING_MD}]
    expected_desc = (
        "Test probe skill. When the user says ping, reply PONG and cite mock-ping."
    )
    if name != "mock-ping" or description != expected_desc:
        return skills, False, "Bundled SKILL.md name/description mismatch"
    return skills, True, None


MOCK_SKILLS, BUNDLED_SKILL_VALID, BUNDLED_SKILL_INIT_ERROR = _init_mock_skills()


def referenced_marker(text):
    return bool(text and "[MOCK_SKILL:mock-ping]" in text)


def build_effective_system_prompt(user_system, mode=None):
    base = (user_system or "").strip() or PROBE_BASE_SYSTEM_PROMPT
    if mode is None:
        return base
    if mode == "catalog_tool":
        skill = MOCK_SKILLS[0] if MOCK_SKILLS else {"name": "mock-ping", "description": ""}
        appendix = (
            "Available agent skills (call load_skill with skill_name to load full instructions):\n"
            f"- {skill['name']}: {skill['description']}"
        )
    else:
        appendix = f"Loaded agent skill (mock-ping):\n{MOCK_SKILL_PING_MD}"
    return base + "\n\n" + appendix


def get_probe_tools(enable_tools, enable_skills, skills_mode):
    tools = []
    if enable_tools:
        tools.extend(MOCK_TOOLS)
    if enable_skills and skills_mode == "catalog_tool":
        tools.append(LOAD_SKILL_TOOL)
    return tools


def tool_names_from_tools(tools):
    return frozenset(t["function"]["name"] for t in tools)


def extract_load_skill_calls(tool_trace):
    calls = []
    for rnd in tool_trace:
        msg = (rnd.get("response") or {}).get("message") or {}
        for tc in msg.get("tool_calls") or []:
            fn = (tc.get("function") or {}).get("name")
            if fn != "load_skill":
                continue
            args_raw = (tc.get("function") or {}).get("arguments") or "{}"
            try:
                args = json.loads(args_raw)
            except json.JSONDecodeError:
                args = {"_raw": args_raw}
            content = None
            for tr in rnd.get("tool_results") or []:
                if tr.get("name") == "load_skill":
                    content = tr.get("content")
                    break
            preview = (content or "")[:200]
            if content and len(content) > 200:
                preview += "..."
            calls.append({
                "round": rnd.get("round"),
                "skill_name": args.get("skill_name"),
                "arguments": args,
                "content_preview": preview,
            })
    return calls


def compute_skills_verdict_from_trace(
    tool_trace, skills_failures, final_content, mode
):
    bundled_ok = BUNDLED_SKILL_VALID and not any(
        f["kind"] == "bundled_skill_invalid" for f in skills_failures
    )
    api_error = any(f["kind"] == "api_error" for f in skills_failures)
    load_calls = extract_load_skill_calls(tool_trace)
    called_load = bool(load_calls)
    load_ok = False
    valid_names = True
    for c in load_calls:
        if c.get("skill_name") and c["skill_name"] != "mock-ping":
            valid_names = False
    for rnd in tool_trace:
        for tr in rnd.get("tool_results") or []:
            if tr.get("name") == "load_skill" and referenced_marker(tr.get("content")):
                load_ok = True
                break
        if load_ok:
            break

    referenced = referenced_marker(final_content)
    notes = [
        f["message"] for f in skills_failures
        if f["kind"] not in ("skill_not_applied",)
    ]
    hard_failures = [f for f in skills_failures if f["kind"] != "skill_not_applied"]

    if mode == "catalog_tool":
        failed = bool(hard_failures) or not (
            bundled_ok and not api_error and called_load and load_ok and valid_names
        )
    else:
        failed = bool(hard_failures) or not (bundled_ok and not api_error and referenced)

    return {
        "skills_probe_mode": mode,
        "bundled_skill_valid": bundled_ok,
        "called_load_skill": called_load if mode == "catalog_tool" else None,
        "load_skill_result_ok": load_ok if mode == "catalog_tool" else None,
        "referenced_mock_skill": referenced,
        "notes": notes,
        "failed": failed,
    }


def build_skills_summary(
    tool_trace, effective_system_prompt, mode, skills_failures, final_content, tools
):
    failures = list(skills_failures)
    if not BUNDLED_SKILL_VALID:
        add_failure(
            failures, 0, "bundled_skill_invalid",
            BUNDLED_SKILL_INIT_ERROR or "Invalid bundled SKILL.md",
        )
        verdict = compute_skills_verdict_from_trace([], failures, final_content, mode)
        return {"skills_probe_mode": mode}, verdict, failures

    excerpt = effective_system_prompt or ""
    if len(excerpt) > 500:
        excerpt = excerpt[:500] + "..."

    if final_content:
        if mode == "catalog_tool" and tools and not extract_load_skill_calls(tool_trace):
            add_failure(
                failures, 0, "skill_not_applied",
                "Model replied with text only; never called load_skill",
            )
        elif mode == "full_system" and not referenced_marker(final_content):
            add_failure(
                failures, 0, "skill_not_applied",
                "Model replied without [MOCK_SKILL:mock-ping] marker",
            )

    summary = {
        "skills_probe_mode": mode,
        "system_skill_excerpt": excerpt,
        "load_skill_calls": extract_load_skill_calls(tool_trace),
    }
    verdict = compute_skills_verdict_from_trace(
        tool_trace, failures, final_content, mode
    )
    return summary, verdict, failures


def compute_tool_verdict(tool_trace, tool_failures, final_content, allowed_tool_names):
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
                if fn and fn not in allowed_tool_names:
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


def render_skills_trace(msg):
    summary = msg.get("skills_trace")
    if not summary:
        return
    verdict = msg.get("skills_verdict") or {}
    failures = msg.get("skills_failures") or []
    mode = verdict.get("skills_probe_mode") or summary.get("skills_probe_mode", "?")
    header_fail = "FAIL | " if verdict.get("failed") or failures else ""
    checks = [f"mode={mode}", f"bundled_skill_valid {'OK' if verdict.get('bundled_skill_valid') else 'FAIL'}"]
    if mode == "catalog_tool":
        cl = verdict.get("called_load_skill")
        checks.append(f"called_load_skill {'OK' if cl else 'FAIL'}")
        lr = verdict.get("load_skill_result_ok")
        checks.append(f"load_skill_result_ok {'OK' if lr else 'FAIL'}")
    ref = verdict.get("referenced_mock_skill")
    checks.append(f"referenced_mock_skill {'OK' if ref else 'heuristic FAIL'}")
    with st.expander("Skills trace"):
        st.markdown(f"**{header_fail}**" + " | ".join(checks))
        for note in verdict.get("notes") or []:
            st.caption(note)
        st.json(summary)
        if msg.get("tool_trace") or msg.get("api_trace"):
            st.caption("Full per-round payloads are in API trace below.")


def render_assistant_bubble(msg):
    for f in msg.get("skills_failures") or []:
        if f.get("kind") == "skill_not_applied":
            st.warning(f["message"])
        else:
            st.error(f["message"])
    for f in msg.get("stream_failures") or []:
        st.error(f["message"])
    for f in msg.get("tool_failures") or []:
        if f.get("kind") == "tools_ignored":
            st.warning(f["message"])
        else:
            st.error(f["message"])
    if msg.get("content"):
        st.markdown(msg["content"])
    elif msg.get("tool_failures") or msg.get("stream_failures") or msg.get("skills_failures"):
        st.markdown("_No assistant text; see errors above._")


def build_chat_api_trace(
    request_messages,
    model,
    temperature,
    max_tokens,
    stats,
    content,
    failures=None,
    use_stream=True,
    stream_summary=None,
    error_response=None,
    raw_request=None,
    raw_response=None,
):
    round_failures = [
        {"kind": f.get("kind"), "message": f.get("message")}
        for f in (failures or [])
    ]
    request = {
        "messages": json.loads(json.dumps(request_messages)),
        "model": model,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": use_stream,
    }
    if use_stream:
        request["stream_options"] = {"include_usage": True}
    entry = {
        "round": 1,
        "request": request,
        "round_failures": round_failures,
    }
    if error_response is not None:
        entry["error_response"] = error_response
        entry["response"] = None
    else:
        entry["response"] = {
            "id": stats.get("resp_id"),
            "model": stats.get("resp_model"),
            "finish_reason": stats.get("finish_reason"),
            "message": {"role": "assistant", "content": content},
            "usage": stats.get("usage"),
            "elapsed_s": round(stats.get("total_time") or 0, 3),
            "ttft_s": round(stats["ttft"], 3) if stats.get("ttft") else None,
        }
    if stream_summary:
        entry["stream_trace_summary"] = stream_summary
    if raw_request is not None:
        entry["raw_request"] = raw_request
    if raw_response is not None:
        entry["raw_response"] = raw_response
    return [entry]


def render_api_trace(msg):
    trace = msg.get("tool_trace") or msg.get("api_trace")
    if not trace:
        return
    stats = msg.get("stats") or {}
    if msg.get("tool_trace"):
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
        header = f"**{header_fail}**" + " | ".join(checks)
        notes = verdict.get("notes") or []
    else:
        rnd = trace[0] if trace else {}
        resp = rnd.get("response") or {}
        usage = resp.get("usage") or {}
        header = (
            f"finish_reason={resp.get('finish_reason')} | "
            f"elapsed_s={resp.get('elapsed_s')} | "
            f"ttft_s={resp.get('ttft_s')}"
        )
        if usage:
            header += (
                f" | prompt_tokens={usage.get('prompt_tokens')} | "
                f"completion_tokens={usage.get('completion_tokens')}"
            )
        notes = []
    with st.expander("API trace"):
        st.markdown(header)
        for note in notes:
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
                if rnd.get("stream_trace_summary") is not None:
                    st.markdown("**stream_trace_summary**")
                    st.json(rnd.get("stream_trace_summary"))
                if rnd.get("raw_request") is not None:
                    st.markdown("**raw_request**")
                    st.json(rnd.get("raw_request"))
                if rnd.get("raw_response") is not None:
                    st.markdown("**raw_response**")
                    st.json(rnd.get("raw_response"))


def run_tool_chat(
    client,
    api_messages,
    model,
    temperature,
    max_tokens,
    stats,
    tools,
    status=None,
    effective_system_prompt=None,
):
    tool_trace = []
    tool_failures = []
    messages = list(api_messages)
    final_content = None
    tool_results_sent = False
    last_completion = None
    allowed_tool_names = tool_names_from_tools(tools) if tools else frozenset()

    for round_num in range(1, MAX_TOOL_ROUNDS + 1):
        request_snapshot = {
            "messages": json.loads(json.dumps(messages)),
            "model": model,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            request_snapshot["tools"] = tools
            request_snapshot["tool_choice"] = "auto"
        if round_num == 1 and effective_system_prompt is not None:
            request_snapshot["effective_system_prompt"] = effective_system_prompt
        round_failures = []
        t_start = time.time()

        if status:
            status.update(label=f"Round {round_num}: calling API...")

        try:
            result = client.chat_complete(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                tools=tools if tools else None,
                tool_choice="auto" if tools else None,
            )
        except Exception as e:
            body = api_error_body(e)
            err_msg = f"API error (round {round_num}): {type(e).__name__}: {e}"
            add_failure(tool_failures, round_num, "api_error", err_msg)
            round_failures.append({"kind": "api_error", "message": err_msg})
            err_round = {
                "round": round_num,
                "request": request_snapshot,
                "response": None,
                "tool_results": [],
                "round_failures": round_failures,
                "error_response": body,
            }
            tool_trace.append(err_round)
            break

        elapsed = time.time() - t_start
        completion = result.completion
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
                if name not in allowed_tool_names:
                    expected = ", ".join(sorted(allowed_tool_names))
                    err_msg = (
                        f'Unknown tool "{name}" (round {round_num}); '
                        f"expected one of: {expected}"
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

            round_entry = {
                "round": round_num,
                "request": request_snapshot,
                "response": response_snapshot,
                "tool_results": tool_results,
                "round_failures": round_failures,
            }
            if result.raw_request:
                round_entry["raw_request"] = result.raw_request
            if result.raw_response:
                round_entry["raw_response"] = result.raw_response
            tool_trace.append(round_entry)

            if round_num >= MAX_TOOL_ROUNDS:
                err_msg = "Stopped: model kept requesting tools after 3 rounds"
                add_failure(tool_failures, round_num, "max_rounds", err_msg)
                break
            continue

        if msg.content:
            final_content = msg.content
            round_entry = {
                "round": round_num,
                "request": request_snapshot,
                "response": response_snapshot,
                "tool_results": [],
                "round_failures": round_failures,
            }
            if result.raw_request:
                round_entry["raw_request"] = result.raw_request
            if result.raw_response:
                round_entry["raw_response"] = result.raw_response
            tool_trace.append(round_entry)
            break

        err_msg = (
            f"Unexpected response: empty assistant message "
            f"(finish_reason={choice.finish_reason})"
        )
        add_failure(tool_failures, round_num, "stuck_no_content", err_msg)
        round_failures.append({"kind": "stuck_no_content", "message": err_msg})
        round_entry = {
            "round": round_num,
            "request": request_snapshot,
            "response": response_snapshot,
            "tool_results": [],
            "round_failures": round_failures,
        }
        if result.raw_request:
            round_entry["raw_request"] = result.raw_request
        if result.raw_response:
            round_entry["raw_response"] = result.raw_response
        tool_trace.append(round_entry)
        break

    if (
        tools
        and final_content
        and not any(
            (r.get("response") or {}).get("message", {}).get("tool_calls") for r in tool_trace
        )
    ):
        warn_msg = "Model replied with text only; no tool_calls"
        add_failure(tool_failures, 0, "tools_ignored", warn_msg)

    tool_verdict = compute_tool_verdict(
        tool_trace, tool_failures, final_content, allowed_tool_names
    )

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
    _is_lab = cfg.get("adapter") == "lab"

    if st.session_state.get("_last_backend") != backend:
        st.session_state.pop("fetched_models", None)
        st.session_state.pop("selected_model", None)
        st.session_state.pop("_client_caps", None)
        st.session_state["_last_backend"] = backend
        try:
            _c = make_llm_client(cfg, cfg["default_url"], cfg["default_key"] or "none")
            if _c.capabilities.get("list_models", True):
                st.session_state.fetched_models = _c.list_models()
        except Exception:
            pass

    base_url = st.text_input("Base URL", value=cfg["default_url"], key=f"base_url_{backend}")
    if _is_lab:
        st.caption(
            "Default: OpenAI-compatible lab adapter (httpx + raw API trace). "
            "Add lab_adapter.py at the office for your native API."
        )
        api_key = st.text_input(
            "API Key",
            value=os.environ.get("LAB_OPENAI_API_KEY") or cfg["default_key"] or "ollama",
            type="password",
            key="lab_api_key",
            help="Used by default lab adapter. Custom lab_adapter.py may use build_headers() instead.",
        )
    else:
        api_key = st.text_input(
            "API Key", value=cfg["default_key"], type="password",
            help="Leave as 'ollama' for Ollama, blank if your server needs no key.",
        )

    try:
        _sidebar_client = make_llm_client(cfg, base_url, api_key or "none")
        _client_caps = _sidebar_client.capabilities
    except Exception:
        _client_caps = {}
    st.session_state._client_caps = _client_caps

    _can_list_models = _client_caps.get("list_models", True)
    if st.button("Fetch available models", use_container_width=True, disabled=not _can_list_models):
        if not base_url:
            st.warning("Enter a Base URL first.")
        else:
            try:
                _client = make_llm_client(cfg, base_url, api_key or "none")
                if _client.capabilities.get("list_models", True):
                    st.session_state.fetched_models = _client.list_models()
            except Exception as e:
                st.error(f"Could not fetch models: {e}")
    if not _can_list_models:
        st.caption("Model list not supported for this provider; enter model name manually.")

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
    _probe_block = (
        st.session_state.get("enable_mock_tools", False)
        or st.session_state.get("enable_mock_skills", False)
    )
    _can_stream = _client_caps.get("stream", True)
    enable_streaming = st.checkbox(
        "Stream responses",
        value=True,
        key="enable_streaming",
        disabled=_probe_block or not _can_stream,
        help="Off waits for the full reply; on shows tokens as they arrive.",
    )
    if not _can_stream and not _probe_block:
        st.caption("Streaming not supported for this provider adapter.")
    system_prompt = st.text_area("System prompt", placeholder="Optional system message...")

    st.divider()
    st.subheader("Agent probes")
    _can_tools = _client_caps.get("tools", True)
    enable_mock_tools = st.checkbox(
        "Enable mock tools",
        value=False,
        key="enable_mock_tools",
        disabled=not _can_tools,
        help="Simulated read_file and execute_shell only. No disk or shell access.",
    )
    enable_mock_skills = st.checkbox(
        "Enable mock skills",
        value=False,
        key="enable_mock_skills",
        disabled=not _can_tools,
        help="Bundled SKILL.md in system and/or load_skill tool. Nothing loaded from disk.",
    )
    if not _can_tools:
        st.caption("Lab API adapter does not declare tool support.")
    skills_probe_mode = "catalog_tool"
    if enable_mock_skills:
        skills_probe_mode = st.radio(
            "Skills load mode",
            list(SKILL_PROBE_MODES),
            format_func=lambda m: (
                "Catalog + load_skill" if m == "catalog_tool" else "Full skill in system"
            ),
            key="skills_probe_mode",
        )
    if enable_mock_tools:
        st.caption("Tools are fake; nothing runs on the host.")
    if enable_mock_skills:
        st.caption("Skills are bundled mock SKILL.md only.")
        st.caption(
            "Empty System prompt uses a default probe persona, then skills are appended."
        )
    if enable_mock_tools or enable_mock_skills:
        with st.expander("Suggested test prompts"):
            if enable_mock_tools:
                st.markdown("**Tools**")
                st.code(TOOL_TEST_PROMPT, language=None)
                if st.button("Send test prompt", key="send_tools_test", use_container_width=True):
                    st.session_state.pending_prompt = TOOL_TEST_PROMPT
                    st.rerun()
            if enable_mock_skills:
                st.markdown("**Skills**")
                st.code(SKILLS_TEST_PROMPT, language=None)
                if st.button("Send test prompt", key="send_skills_test", use_container_width=True):
                    st.session_state.pending_prompt = SKILLS_TEST_PROMPT
                    st.rerun()

def stream_response(client, messages, model, temperature, max_tokens, stats, trace=None):
    yield from client.iter_chat_stream(
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        stats=stats,
        trace=trace,
        max_trace_samples=MAX_STREAM_TRACE_SAMPLES,
    )


def blocking_chat_response(client, messages, model, temperature, max_tokens, stats):
    t_start = time.time()
    result = client.chat_complete(
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    if result.raw_request:
        stats["raw_request"] = result.raw_request
    if result.raw_response:
        stats["raw_response"] = result.raw_response
    completion = result.completion
    stats["total_time"] = time.time() - t_start
    stats["ttft"] = stats["total_time"]
    if not completion.choices:
        return None
    choice = completion.choices[0]
    stats["resp_id"] = completion.id
    stats["resp_model"] = completion.model
    stats["finish_reason"] = choice.finish_reason
    stats["usage"] = usage_to_dict(completion.usage)
    return choice.message.content


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
        if msg.get("tool_trace") or msg.get("api_trace"):
            render_api_trace(msg)
        if msg.get("skills_trace"):
            render_skills_trace(msg)

# --- Input ---
_pending = st.session_state.pop("pending_prompt", None)
prompt = st.chat_input("Type a message...")
if _pending:
    prompt = _pending
if prompt:
    if not base_url:
        st.error("Set a Base URL in the sidebar first.")
        st.stop()
    if not model:
        st.error("Set a Model name in the sidebar first.")
        st.stop()

    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    probe_mode = skills_probe_mode if enable_mock_skills else None
    if enable_mock_skills:
        effective_sys = build_effective_system_prompt(system_prompt, probe_mode)
    elif enable_mock_tools:
        effective_sys = build_effective_system_prompt(system_prompt, mode=None)
    else:
        effective_sys = system_prompt or None
    api_messages = build_api_messages(st.session_state.messages, effective_sys)
    system_base = (system_prompt or "").strip() or (
        PROBE_BASE_SYSTEM_PROMPT if (enable_mock_skills or enable_mock_tools) else None
    )

    client = make_llm_client(cfg, base_url, api_key or "none")

    stats = {
        "req_adapter": client.adapter_kind,
        "req_base_url": base_url,
        "req_model": model,
        "req_temperature": temperature,
        "req_max_tokens": max_tokens,
        "req_system_prompt_sidebar": system_prompt or None,
        "req_system_prompt_base": system_base,
        "req_system_prompt_sent": effective_sys,
        "req_user_prompt_last": prompt,
        "req_message_count": len(api_messages),
        "mock_tools": enable_mock_tools,
        "mock_skills": enable_mock_skills,
        "skills_probe_mode": probe_mode,
        "req_stream": enable_streaming,
    }

    if enable_mock_tools or enable_mock_skills:
        probe_tools = get_probe_tools(enable_mock_tools, enable_mock_skills, probe_mode)
        status_label = "Agent probe..."
        with st.chat_message("assistant"):
            final_content = None
            tool_trace = []
            tool_verdict = {}
            tool_failures = []
            skills_trace = {}
            skills_verdict = {}
            skills_failures = []
            with st.status(status_label, expanded=True) as status:
                try:
                    final_content, tool_trace, tool_verdict, tool_failures, stats = run_tool_chat(
                        client, api_messages, model, temperature, max_tokens, stats,
                        tools=probe_tools,
                        status=status,
                        effective_system_prompt=effective_sys,
                    )
                    if enable_mock_skills:
                        skills_trace, skills_verdict, skills_failures = build_skills_summary(
                            tool_trace,
                            effective_sys,
                            probe_mode,
                            skills_failures,
                            final_content,
                            probe_tools,
                        )
                    status.update(label="Probe finished", state="complete")
                except Exception as e:
                    status.update(label="Probe failed", state="error")
                    add_failure(tool_failures, 0, "api_error", f"Error: {e}")
                    if enable_mock_skills:
                        skills_trace, skills_verdict, skills_failures = build_skills_summary(
                            tool_trace,
                            effective_sys,
                            probe_mode,
                            skills_failures,
                            final_content,
                            probe_tools,
                        )
            bubble = {
                "content": final_content,
                "tool_failures": tool_failures,
                "skills_failures": skills_failures,
            }
            render_assistant_bubble(bubble)
            if tool_trace:
                render_api_trace({
                    "tool_trace": tool_trace,
                    "tool_verdict": tool_verdict,
                    "tool_failures": tool_failures,
                    "stats": stats,
                })
            if enable_mock_skills and skills_trace:
                render_skills_trace({
                    "tool_trace": tool_trace,
                    "skills_trace": skills_trace,
                    "skills_verdict": skills_verdict,
                    "skills_failures": skills_failures,
                })

        msg_entry = {
            "role": "assistant",
            "content": final_content,
            "stats": stats,
            "tool_trace": tool_trace,
            "tool_verdict": tool_verdict,
            "tool_failures": tool_failures,
        }
        if enable_mock_skills:
            msg_entry["skills_trace"] = skills_trace
            msg_entry["skills_verdict"] = skills_verdict
            msg_entry["skills_failures"] = skills_failures
        st.session_state.messages.append(msg_entry)
    else:
        chat_failures = []
        with st.chat_message("assistant"):
            placeholder = st.empty()
            placeholder.markdown("_Thinking..._")
            response = None
            api_trace = None
            try:
                if enable_streaming:
                    response = ""
                    for token in stream_response(
                        client, api_messages, model, temperature, max_tokens, stats
                    ):
                        response += token
                        placeholder.markdown(response + CURSOR)
                    placeholder.markdown(response)
                    api_trace = build_chat_api_trace(
                        api_messages,
                        model,
                        temperature,
                        max_tokens,
                        stats,
                        response,
                        failures=chat_failures,
                        use_stream=True,
                        stream_summary=stats.get("stream_trace_summary"),
                        raw_request=stats.get("raw_request"),
                        raw_response=stats.get("raw_response"),
                    )
                else:
                    response = blocking_chat_response(
                        client, api_messages, model, temperature, max_tokens, stats
                    )
                    placeholder.markdown(response or "")
                    api_trace = build_chat_api_trace(
                        api_messages,
                        model,
                        temperature,
                        max_tokens,
                        stats,
                        response,
                        failures=chat_failures,
                        use_stream=False,
                        raw_request=stats.get("raw_request"),
                        raw_response=stats.get("raw_response"),
                    )
            except Exception as e:
                placeholder.empty()
                st.error(f"Error: {e}")
                body = api_error_body(e)
                add_failure(chat_failures, 0, "api_error", f"Error: {e}")
                api_trace = build_chat_api_trace(
                    api_messages,
                    model,
                    temperature,
                    max_tokens,
                    stats,
                    None,
                    failures=chat_failures,
                    use_stream=enable_streaming,
                    stream_summary=stats.get("stream_trace_summary")
                    if enable_streaming
                    else None,
                    error_response=body,
                    raw_request=stats.get("raw_request"),
                    raw_response=stats.get("raw_response"),
                )

        if response is not None:
            assistant_msg = {
                "role": "assistant",
                "content": response,
                "stats": stats,
                "api_trace": api_trace,
            }
            if chat_failures:
                assistant_msg["stream_failures"] = chat_failures
            st.session_state.messages.append(assistant_msg)
            render_api_trace(assistant_msg)
        elif api_trace:
            err_msg = {
                "role": "assistant",
                "content": None,
                "stats": stats,
                "api_trace": api_trace,
            }
            if chat_failures:
                err_msg["stream_failures"] = chat_failures
            st.session_state.messages.append(err_msg)
            render_api_trace(err_msg)

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
