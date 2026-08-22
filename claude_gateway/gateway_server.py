"""Headless Claude Code gateway for the Superset AI chat panel.

Wraps `claude -p` (Claude Code CLI, headless mode) as a small HTTP service so
Superset's existing `/api/v1/vdt-ai-chat/query` proxy (superset/superset_config.py)
can call it exactly the way it called the old rule-based agent_gateway.py: POST
{question, session_id, context, row_limit} in, {"answer": "..."} (or
{"message": "..."} on error) out.

Two endpoints serve the same turn: /api/v1/agent/query buffers it into that single
JSON reply, and /api/v1/agent/stream forwards it as Server-Sent Events so the panel
can paint the answer while it is still being written. Both run through
`_query_claude_stream`, so they cannot drift apart.

The gateway also owns the MCP server as a sidecar: one `mcp_server.py` started at
boot and shared by every turn, rather than one spawned per turn by the CLI itself.
See MCP_TRANSPORT and `_start_mcp_sidecar` below.

CLI flags below were verified against `claude --version` 2.1.197's `--help` output
inside the built claude_gateway image (--mcp-config, --strict-mcp-config,
--allowedTools/--disallowedTools, --permission-mode dontAsk, --resume/--session-id,
--append-system-prompt, --output-format stream-json --verbose,
--include-partial-messages, -p). There is no
--max-turns flag in this CLI version (an earlier draft assumed one); runaway
agentic loops are instead bounded by DEADLINE_SECONDS below. If the installed
Claude Code CLI version changes, re-run `claude --help` in the image and diff
against `_build_argv`.
"""

from __future__ import annotations

import json
import os
import re
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from loguru import logger
from render_config import render

logger.remove()
logger.add(
    sys.stderr,
    level="INFO",
    colorize=True,
    format=(
        "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
        "<level>{level: <7}</level> | "
        "<level>{message}</level>"
    ),
)

PORT = int(os.getenv("CLAUDE_GATEWAY_PORT", "8090"))
MCP_CONFIG_PATH = "/app/mcp_servers.json"
SYSTEM_PROMPT_PATH = Path("/app/system_prompt.md")
DEADLINE_SECONDS = float(os.getenv("CLAUDE_DEADLINE_SECONDS", "150"))
# Alias ("sonnet", "opus", "fable") or full model name ("claude-haiku-4-5-20251001").
# Unset keeps the CLI's own default model.
MODEL = os.getenv("CLAUDE_GATEWAY_MODEL")

# The MCP server used to run over stdio, which meant the CLI spawned a fresh
# `python3 /app/mcp_server.py` for every turn: python start-up, the SQLAlchemy
# import and a Superset login, all re-paid per question, and a race where the
# first tool call of a turn could reach the model before the server had finished
# registering its tools ("No such tool available", then a retry that worked).
# "http" starts that server exactly once, below, and points the CLI at it over
# loopback instead. Set MCP_TRANSPORT=stdio to fall back to the old behaviour.
MCP_TRANSPORT = os.getenv("MCP_TRANSPORT", "http").lower()
MCP_HTTP_HOST = os.getenv("MCP_HTTP_HOST", "127.0.0.1")
MCP_HTTP_PORT = int(os.getenv("MCP_HTTP_PORT", "8765"))
MCP_SERVER_SCRIPT = os.getenv("MCP_SERVER_SCRIPT", "/app/mcp_server.py")
MCP_STARTUP_TIMEOUT = float(os.getenv("MCP_STARTUP_TIMEOUT", "30"))

# The one flag that decides whether a turn can reach Postgres at all.
#
# By default the CLI connects --mcp-config servers asynchronously and starts the turn
# without waiting for them - it says so itself under `--debug mcp`:
#   [MCP] --mcp-config servers running fully async (nonblocking)
#   MCP server "superset-postgres": Starting connection with timeout of 30000ms
# so the first request to the model goes out with the MCP tools still missing, and the
# first tool call of every process comes back "No such tool available:
# mcp__superset-postgres__<whatever>". Sometimes the model retried into a connection
# that had since completed and the turn limped through (which is why the failures
# looked like flip-flopping tool names, 02:56 and 02:58 on 2026-08-20); sometimes it
# gave up and answered from nothing. Neither is acceptable, and no amount of naming
# tools correctly in the system prompt fixes it - the tools genuinely are not there
# yet. Measured 2026-08-20 against CLI 2.1.197, `mcp_servers` / MCP tool count in the
# init event:
#   unset / "1" / "true"  -> pending,   0 tools
#   "0" / "false"         -> connected, 29 tools
# Blocking costs the MCP handshake once per turn; against the loopback sidecar below
# that measured 58ms, which is nothing next to the round-trip it removes.
CLAUDE_ENV = {**os.environ, "MCP_CONNECTION_NONBLOCKING": "0"}

MCP_SERVER_NAME = "superset-postgres"
_MCP_TOOLS = (
    "list_datasets",
    "describe_table",
    "execute_sql",
    "run_sql_readonly",
    "get_chart",
    "create_dataset",
    "create_chart",
    "update_chart",
    "create_dashboard",
    "add_charts_to_dashboard",
    "update_dashboard",
    "health_check",
    "get_instance_info",
    "list_charts",
    "list_dashboards",
    "list_databases",
    "get_dashboard_info",
    "get_dataset_info",
    "get_database_info",
    "get_chart_preview",
    "get_chart_sql",
    "generate_explore_link",
    "open_sql_lab_with_context",
)


_CHART_CREATION_MCP_TOOLS = {
    f"mcp__{MCP_SERVER_NAME}__create_chart",
    f"mcp__{MCP_SERVER_NAME}__create_dataset",
    f"mcp__{MCP_SERVER_NAME}__create_dashboard",
    f"mcp__{MCP_SERVER_NAME}__add_charts_to_dashboard",
}


def _is_chart_requested(question: str) -> bool:
    if not question:
        return False
    q = question.lower().strip()

    # 1. Explicit chart creation/modification/action signals
    chart_signals = [
        "chart", "biểu đồ", "đồ thị", "dashboard", "dataset", "visualize", "plot", "vẽ", "table",
        "tạo", "create", "make", "gen", "generate", "thêm", "gắn", "pie", "bar", "line",
        "donut", "heatmap", "big_number", "treemap", "bảng"
    ]
    if any(sig in q for sig in chart_signals):
        return True

    # 2. Confirmation turn: Any short affirmative/follow-up response (<= 8 words)
    is_short_reply = len(q.split()) <= 8 and not q.endswith("?")
    words = {w.strip(".,!?\"'").lower() for w in q.split()}
    confirm_tokens = {
        "có", "co", "ok", "yes", "y", "lưu", "luu", "save", "confirm", "được", "duoc",
        "đồng", "dong", "ý", "y", "phải", "phai", "đúng", "dung", "chuẩn", "chuan",
        "chính", "xác", "ừ", "u", "uh", "dạ", "da", "tiếp", "tiep", "tục", "tuc",
        "làm", "lam", "tạo", "tao", "vẽ", "ve", "lại", "lai", "rồi", "roi"
    }

    if is_short_reply and bool(words.intersection(confirm_tokens)):
        return True

    return False


def _get_allowed_tools(question: str = "") -> str:
    """MCP tools eligible for this turn's --allowedTools.

    Excludes the chart-creation tools unless the question asks for one - this has to
    happen here, not only in _get_disallowed_tools below, because a tool listed in
    both --allowedTools and --disallowedTools for the same invocation is NOT denied:
    measured against CLI 2.1.197 on 2026-08-20, a question with no chart keywords
    ("Top 10 nhan su co FTE cao nhat trong thang gan nhat") still ran create_dataset
    and create_chart to completion, because the old code put every MCP tool in
    --allowedTools unconditionally and relied on --disallowedTools alone to gate
    chart creation - allowedTools won. The two functions must stay in sync: whatever
    _get_disallowed_tools hard-blocks here must also be absent from this list.
    """
    tools = _MCP_TOOLS
    if not _is_chart_requested(question):
        chart_tool_names = {name.rsplit("__", 1)[-1] for name in _CHART_CREATION_MCP_TOOLS}
        tools = tuple(t for t in tools if t not in chart_tool_names)
    return ",".join(f"mcp__{MCP_SERVER_NAME}__{tool}" for tool in tools)


_BASE_DISALLOWED_TOOLS = {
    "Bash", "PowerShell", "Read", "Write", "Edit", "Glob", "Grep",
    "WebFetch", "WebSearch", "NotebookEdit", "Task", "Agent",
    "Monitor", "Workflow", "RemoteTrigger",
    "Artifact", "AskUserQuestion", "EnterPlanMode", "ExitPlanMode",
    "CronCreate", "CronDelete", "CronList", "ScheduleWakeup",
    "SendMessage", "PushNotification", "DesignSync",
    "EnterWorktree", "ExitWorktree",
    "TaskCreate", "TaskGet", "TaskList", "TaskUpdate", "TaskOutput", "TaskStop",
    "ListMcpResourcesTool", "ReadMcpResourceTool", "ReadMcpResourceDirTool",
    "ToolSearch",
}


def _get_disallowed_tools(variant: str, question: str = "") -> str:
    disallowed = _BASE_DISALLOWED_TOOLS.copy()
    if variant == "baseline":
        disallowed.add("Skill")

    # Belt-and-suspenders: _get_allowed_tools already excludes these when a chart
    # was not requested, but listing them here too costs nothing and guards against
    # the two functions drifting out of sync.
    if not _is_chart_requested(question):
        disallowed.update(_CHART_CREATION_MCP_TOOLS)

    return ",".join(sorted(disallowed))


# Tools whose JSON result carries a chart/dashboard the panel can preview inline.
_PREVIEW_TOOLS = {"create_chart", "update_chart", "create_dashboard", "add_charts_to_dashboard"}

# Variants. "baseline" runs the exact argv this gateway has always used;
# "skills" is semantic only (no --plugin-dir loaded). The plugin marketplace
# (registered in .claude/settings.json) is always available to both variants.
VARIANTS = ("baseline", "skills")
DEFAULT_VARIANT = "baseline"

_SESSION_ID_RE = re.compile(r"^[0-9a-fA-F-]{36}$")

# The CLI's two complaints about being asked to start/continue a session the wrong
# way round, verified against 2.1.197. Both are raised before any API call, so
# retrying the other way is cheap; a timeout deliberately does NOT match, since
# retrying that would double the turn's latency for no reason.
_WRONG_SESSION_MODE = re.compile(r"is already in use|No conversation found with session ID", re.I)

_known_sessions: set[str] = set()
_known_sessions_lock = threading.Lock()

# Serializes turns that share a session_id so an impatient double-send (or a
# slow first turn overlapping a retry) can't launch two `claude -p
# --session-id <same>` processes writing the same transcript concurrently -
# that race was observed contributing to turns running past Superset's own
# outbound timeout (see superset/superset_config.py).
_session_locks: dict[str, threading.Lock] = {}
_session_locks_guard = threading.Lock()


def _lock_for_session(session_id: str) -> threading.Lock:
    with _session_locks_guard:
        return _session_locks.setdefault(session_id, threading.Lock())


def _system_prompt() -> str:
    role = Path("/app/claude_gateway/role_prompt.md")
    docs = Path("/app/mock_data_docs.md")
    parts = []
    if role.exists():
        parts.append(role.read_text(encoding="utf-8"))
    if docs.exists():
        parts.append(docs.read_text(encoding="utf-8"))
    if not parts and SYSTEM_PROMPT_PATH.exists():
        parts.append(SYSTEM_PROMPT_PATH.read_text(encoding="utf-8"))
    return "\n\n".join(parts)


def _build_prompt(question: str, context: dict[str, Any], row_limit: int) -> str:
    ctx_user = context.get("superset_user") if isinstance(context, dict) else None
    ctx_path = context.get("path") if isinstance(context, dict) else None
    header = f"[Superset user: {ctx_user}] [Page: {ctx_path}] [row_limit: {row_limit}]"
    return f"{header}\n\n{question}"


def _build_argv(prompt: str, session_id: str, resume: bool, variant: str = DEFAULT_VARIANT, question: str = "") -> list[str]:
    argv = [
        "claude", "-p",
        "--output-format", "stream-json", "--verbose",
        # Without this the CLI only emits whole assistant messages, so the first
        # character of an answer arrives at the same moment as the last one:
        # measured on "Hi", text landed at 4.5s while the model's real time-to-
        # first-token was 2.4s. The extra `stream_event` lines are ignored by the
        # buffered /query path and drive the SSE /stream path.
        "--include-partial-messages",
        "--mcp-config", MCP_CONFIG_PATH,
    ]
    # baseline: deny Skill tool; skills: allow marketplace skills from .claude/settings.json
    # Note: --strict-mcp-config is omitted because in Claude Code CLI 2.x it disables --mcp-config servers
    argv += [
        "--allowedTools", _get_allowed_tools(question=question),
        "--disallowedTools", _get_disallowed_tools(variant, question=question),
        "--permission-mode", "dontAsk",
        "--append-system-prompt", _system_prompt(),
    ]
    if MODEL:
        argv += ["--model", MODEL]
    argv += ["--resume", session_id] if resume else ["--session-id", session_id]
    argv.append(prompt)
    return argv


def _log_event(event: dict[str, Any]) -> None:
    etype = event.get("type")
    if etype in ("assistant", "user"):
        for block in event.get("message", {}).get("content", []) or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                tool_name = block.get("name")
                tool_input = json.dumps(block.get("input", {}), ensure_ascii=False)[:300]
                try:
                    logger.opt(colors=True).info("<yellow>[TOOL USE]</yellow> <cyan><b>{}</b></cyan>({})", tool_name, tool_input)
                except Exception:
                    logger.info("tool_use: {}({})", tool_name, tool_input)
            elif block.get("type") == "tool_result":
                content_str = str(block.get("content"))
                is_err = (
                    block.get("is_error")
                    or bool(re.search(r'["\']success["\']\s*:\s*false', content_str, re.I))
                    or "<tool_use_error>" in content_str
                    or "error executing tool" in content_str.lower()
                    or "permission to use" in content_str.lower()
                )
                is_cached = bool(re.search(r"cached[\"'\\]*\s*:\s*(true|1)", content_str, re.IGNORECASE))
                try:
                    if is_err and not is_cached:
                        logger.opt(colors=True).error("<red><b>[TOOL RESULT - ERROR]</b></red> <red>{}</red>", content_str[:300])
                    elif is_cached:
                        logger.opt(colors=True).info("<magenta>[TOOL RESULT - CACHE HIT (0.1ms)]</magenta> {}", content_str[:300])
                    else:
                        logger.opt(colors=True).info("<green>[TOOL RESULT - SUCCESS]</green> <blue>{}</blue>", content_str[:300])
                except Exception:
                    logger.info("tool_result: {}", content_str[:300])
    elif etype == "system" and event.get("subtype") == "init":
        tools = event.get("tools") or []
        try:
            logger.opt(colors=True).info(
                "<green>[INIT]</green> mcp_servers={} tools=<yellow>{}</yellow> mcp_tools={}",
                event.get("mcp_servers"),
                len(tools),
                sorted(_short_tool_name(t) for t in tools if isinstance(t, str) and t.startswith("mcp__")),
            )
        except Exception:
            logger.info("init: tools={}", len(tools))
    elif etype == "result":
        is_err = event.get("is_error")
        cost = event.get("total_cost_usd")
        try:
            if is_err:
                logger.opt(colors=True).error("<red><b>[TURN FAILED]</b></red> status=<red>ERROR</red> cost=<yellow>${:.5f}</yellow>", float(cost or 0))
            else:
                logger.opt(colors=True).info("<green><b>[TURN COMPLETED]</b></green> status=<green>SUCCESS</green> cost=<yellow>${:.5f}</yellow>", float(cost or 0))
        except Exception:
            logger.info("result: is_error={} cost={}", is_err, cost)


def _short_tool_name(name: str) -> str:
    prefix = f"mcp__{MCP_SERVER_NAME}__"
    return name[len(prefix):] if name.startswith(prefix) else name


def _tool_result_text(content: Any) -> str | None:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            block.get("text")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str)
        ]
        return "\n".join(parts) if parts else None
    return None


def _collect_preview(event: dict[str, Any], pending: dict[str, str], previews: list[dict[str, Any]]) -> list[str]:
    """Correlates tool_use ids with their tool_result and keeps previewable results.

    Returns the short names of the tool calls this event started, so the caller can
    both count them and tell the panel what is running without walking the content
    blocks a second time. Results from the read-only tools travel through here too,
    but none of them carry an `embed_url`, so they drop out on their own.
    """
    etype = event.get("type")
    started: list[str] = []
    for block in event.get("message", {}).get("content", []) or []:
        if not isinstance(block, dict):
            continue
        if etype == "assistant" and block.get("type") == "tool_use":
            started.append(_short_tool_name(block.get("name", "")))
            tool_use_id = block.get("id")
            if tool_use_id:
                pending[tool_use_id] = _short_tool_name(block.get("name", ""))
        elif etype == "user" and block.get("type") == "tool_result":
            if pending.get(block.get("tool_use_id")) not in _PREVIEW_TOOLS:
                continue
            text = _tool_result_text(block.get("content"))
            if not text:
                continue
            try:
                parsed = json.loads(text)
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(parsed, dict) and parsed.get("embed_url"):
                previews.append(parsed)
    return started


def _kill_tree(proc: subprocess.Popen) -> None:
    """Kills the CLI and every process it spawned; falls back to the CLI alone."""
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        proc.kill()


def _stream_claude(
    prompt: str, session_id: str, resume: bool, variant: str = DEFAULT_VARIANT, question: str = ""
) -> Iterator[dict[str, Any]]:
    """Runs one headless Claude turn, yielding progress as the CLI produces it."""
    argv = _build_argv(prompt, session_id, resume, variant, question=question)
    try:
        logger.opt(colors=True).info(
            "<cyan>[EXEC CLAUDE]</cyan> mode=<yellow>{}</yellow>, session=<white>{}</white>, variant=<blue>{}</blue>",
            "resume" if resume else "new",
            session_id,
            variant,
        )
    except Exception:
        logger.info(
            "exec claude ({}, session={}, variant={})", "resume" if resume else "new", session_id, variant
        )

    started_at = time.monotonic()
    # start_new_session puts the CLI and everything it spawns (MCP servers, helper
    # processes) in one process group, so the deadline can take the whole tree down.
    proc = subprocess.Popen(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd="/app",
        start_new_session=True,
        env=CLAUDE_ENV,
    )

    # The deadline has to be enforced by a watchdog rather than checked inside the
    # read loop: `for line in proc.stdout` blocks until a line arrives, so a turn
    # that stalls without printing anything (an unreachable Anthropic endpoint does
    # exactly this - the CLI prints its `init` event and then waits) would never
    # reach an in-loop check. The loop ends when the last writer to the pipe dies,
    # which is why this kills the group and not just the CLI: any surviving child
    # keeps its inherited copy of stdout open and the read stays parked.
    timed_out = threading.Event()

    watchdog = threading.Timer(DEADLINE_SECONDS, lambda: (timed_out.set(), _kill_tree(proc)))
    watchdog.daemon = True
    watchdog.start()

    answer: str | None = None
    is_error = False
    first_event_at: float | None = None
    tool_calls = 0
    cli_duration_ms: int | None = None

    def timing() -> dict[str, Any]:
        return {
            "total_ms": round((time.monotonic() - started_at) * 1000),
            "first_event_ms": round((first_event_at - started_at) * 1000) if first_event_at else None,
            "tool_calls": tool_calls,
            "cli_duration_ms": cli_duration_ms,
            "variant": variant,
        }

    pending_tool_names: dict[str, str] = {}
    previews: list[dict[str, Any]] = []

    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if first_event_at is None:
                first_event_at = time.monotonic()

            # Partial-message traffic is high-volume and carries no bookkeeping:
            # handle it first and skip the logging/preview walk below entirely.
            if event.get("type") == "stream_event":
                inner = event.get("event", {})
                if inner.get("type") == "message_start":
                    yield {"type": "reset"}
                elif inner.get("type") == "content_block_delta":
                    delta = inner.get("delta", {})
                    # text_delta only: thinking_delta is not for the user and
                    # input_json_delta is a tool argument being assembled.
                    if delta.get("type") == "text_delta" and delta.get("text"):
                        yield {"type": "delta", "text": delta["text"]}
                continue

            _log_event(event)
            for tool_name in _collect_preview(event, pending_tool_names, previews):
                tool_calls += 1
                yield {"type": "tool", "name": tool_name}
            if event.get("type") == "result":
                answer = event.get("result") or ""
                is_error = bool(event.get("is_error"))
                if isinstance(event.get("duration_ms"), (int, float)):
                    cli_duration_ms = round(event["duration_ms"])
    finally:
        watchdog.cancel()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _kill_tree(proc)

    # `answer is None` guards the race where the watchdog fires just as a turn
    # finishes: a `result` event only arrives once the turn is complete, so having
    # one means the answer beat the deadline and should be returned, not discarded.
    if timed_out.is_set() and answer is None:
        yield {"type": "error", "message": "Claude gateway timed out waiting for a response", "timing": timing()}
    elif answer is None and proc.returncode not in (0, None):
        stderr = proc.stderr.read()[:500] if proc.stderr else ""
        yield {"type": "error", "message": f"claude exited with code {proc.returncode}: {stderr}", "timing": timing()}
    elif is_error:
        # A "result" event with is_error=true still carries human-readable text
        # in `result` (e.g. "Invalid API key · Fix external API key") - surface
        # it as the error message rather than showing it to the user as a
        # normal, successful answer.
        message = answer or f"claude reported an error (exit code {proc.returncode})"
        yield {"type": "error", "message": message, "timing": timing()}
    else:
        yield {"type": "done", "answer": answer, "previews": previews, "timing": timing()}


def _run_claude(
    prompt: str, session_id: str, resume: bool, variant: str = DEFAULT_VARIANT
) -> tuple[str | None, list[dict[str, Any]], dict[str, Any], str | None]:
    """Drains `_stream_claude` into one buffered result, for the non-SSE endpoint.

    Returns (answer_text, previews, timing, error_message). `timing` is always
    populated - including on the timeout and error paths, where how long the turn
    ran before failing is exactly what you want to know.
    """
    for event in _stream_claude(prompt, session_id, resume, variant):
        if event["type"] == "done":
            return event["answer"], event["previews"], event["timing"], None
        if event["type"] == "error":
            return None, [], event["timing"], event["message"]
    # `_stream_claude` always ends on a terminal event; this only trips if that
    # contract is broken by a future edit.
    return None, [], {"variant": variant}, "Claude gateway produced no result"


def _query_claude_stream(
    question: str, session_id: str, context: dict[str, Any], row_limit: int, variant: str = DEFAULT_VARIANT
) -> Iterator[dict[str, Any]]:
    """One turn including session bookkeeping, as a stream of `_stream_claude` events."""
    prompt = _build_prompt(question, context, row_limit)
    with _lock_for_session(session_id):
        with _known_sessions_lock:
            seen_before = session_id in _known_sessions

        # Second pass only ever runs on the wrong-session-mode retry below, and it
        # is the last one - so a repeat of the same error is reported, not looped on.
        for resume, retryable in ((seen_before, True), (not seen_before, False)):
            emitted = False
            retry = False
            for event in _stream_claude(prompt, session_id, resume=resume, variant=variant, question=question):
                if retryable and not emitted and event["type"] == "error" and _WRONG_SESSION_MODE.search(event["message"]):
                    # `_known_sessions` is in-memory, so after a gateway restart it no
                    # longer knows which ids already have a transcript - and the browser
                    # now keeps a conversation's session id across reloads, so returning
                    # threads land here routinely. The CLI rejects the wrong mode
                    # outright, before spending an API call: `--session-id` refuses an id
                    # that already has a transcript, `--resume` refuses one that has none.
                    # Either way the fix is the other mode, and retrying costs a second
                    # rather than a whole turn. `emitted` guards the retry: once a delta
                    # has reached the browser the turn is no longer repeatable, so a late
                    # error matching this pattern has to be reported, not retried.
                    logger.warning("wrong session mode for {} ({}), retrying the other way", session_id, event["message"])
                    retry = True
                    break
                if event["type"] != "reset":
                    emitted = True
                if event["type"] == "done":
                    with _known_sessions_lock:
                        _known_sessions.add(session_id)
                yield event
            if not retry:
                return


def _query_claude(
    question: str, session_id: str, context: dict[str, Any], row_limit: int, variant: str = DEFAULT_VARIANT
) -> tuple[str | None, list[dict[str, Any]], dict[str, Any], str | None]:
    for event in _query_claude_stream(question, session_id, context, row_limit, variant):
        if event["type"] == "done":
            return event["answer"], event["previews"], event["timing"], None
        if event["type"] == "error":
            return None, [], event["timing"], event["message"]
    return None, [], {"variant": variant}, "Claude gateway produced no result"


class GatewayHandler(BaseHTTPRequestHandler):
    def _reply(self, status: int, payload: dict[str, Any]) -> None:
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _begin_stream(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        # BaseHTTPRequestHandler speaks HTTP/1.0, so there is no Content-Length and
        # no chunked framing: the reader consumes until the socket closes. That is
        # exactly what SSE wants, and it is why this must not go through `_reply`.
        self.end_headers()

    def _send_event(self, event: dict[str, Any]) -> None:
        self.wfile.write(f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode("utf-8"))
        self.wfile.flush()

    def _parse_query(self) -> tuple[str, str, dict[str, Any], int, str]:
        length = int(self.headers.get("Content-Length", "0") or 0)
        payload = json.loads(self.rfile.read(length) or b"{}")
        question = str(payload.get("question", "")).strip()
        if not question:
            raise ValueError("question is required")
        context = payload.get("context") if isinstance(payload.get("context"), dict) else {}
        row_limit = int(payload.get("row_limit", 200))
        session_id = payload.get("session_id")
        if not isinstance(session_id, str) or not _SESSION_ID_RE.match(session_id):
            session_id = str(uuid.uuid4())
        variant = payload.get("variant")
        if variant not in VARIANTS:
            variant = DEFAULT_VARIANT
        return question, session_id, context, row_limit, variant

    def _stream_query(self) -> None:
        try:
            question, session_id, context, row_limit, variant = self._parse_query()
        except (ValueError, json.JSONDecodeError) as error:
            self._reply(400, {"message": str(error)})
            return

        # Headers go out before the first turn event so the browser can start
        # reading; every failure past this point has to be reported inside the
        # stream, because the 200 has already been committed.
        self._begin_stream()
        try:
            for event in _query_claude_stream(question, session_id, context, row_limit, variant):
                self._send_event({**event, "variant": variant})
        except (BrokenPipeError, ConnectionResetError):
            # Abandoning the generator here propagates GeneratorExit into
            # `_stream_claude`, whose `finally` kills the CLI process group - so a
            # closed tab stops the turn instead of leaving it running to the deadline.
            logger.info("client disconnected mid-stream; turn aborted")
        except Exception:
            logger.exception("Unhandled error while streaming from Claude")
            try:
                self._send_event({"type": "error", "message": "Unable to process the request", "variant": variant})
            except (BrokenPipeError, ConnectionResetError):
                pass

    def do_GET(self) -> None:
        self._reply(200, {"status": "ok"}) if self.path == "/health" else self._reply(404, {"message": "Not found"})

    def do_POST(self) -> None:
        if self.path == "/api/v1/agent/stream":
            self._stream_query()
            return
        if self.path != "/api/v1/agent/query":
            self._reply(404, {"message": "Not found"})
            return
        try:
            question, session_id, context, row_limit, variant = self._parse_query()
            answer, previews, timing, error = _query_claude(question, session_id, context, row_limit, variant)
            if error:
                self._reply(502, {"message": error, "timing": timing, "variant": variant})
                return
            self._reply(200, {"answer": answer or "", "previews": previews, "timing": timing, "variant": variant})
        except (BrokenPipeError, ConnectionResetError):
            # The client gave up while the turn was still running (tab closed, or
            # the Superset proxy's socket timed out first). Nobody is left to reply
            # to, and the generic handler below would try anyway and raise a second,
            # uncaught BrokenPipeError - which is what buried this in a stack trace
            # logged as "Unhandled error while querying Claude".
            logger.info("client disconnected before the reply could be sent")
        except (ValueError, json.JSONDecodeError) as error:
            self._reply(400, {"message": str(error)})
        except Exception:
            logger.exception("Unhandled error while querying Claude")
            self._reply(502, {"message": "Unable to process the request"})

    def log_message(self, _format: str, *_args: Any) -> None:
        return


def _wait_for_port(host: str, port: int, timeout: float) -> bool:
    """True once something accepts connections on host:port, False if it never does.

    uvicorn binds the port only after the app is up, so a successful connect is a
    real readiness signal and not just "the process exists".
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket() as sock:
            sock.settimeout(0.5)
            if sock.connect_ex((host, port)) == 0:
                return True
        time.sleep(0.2)
    return False


def _start_mcp_sidecar() -> subprocess.Popen | None:
    """Starts the single long-lived MCP server that every turn then shares.

    Returns None under MCP_TRANSPORT=stdio, where the CLI still spawns its own copy
    per turn and there is nothing for the gateway to manage.
    """
    if MCP_TRANSPORT not in ("http", "streamable-http"):
        logger.info("MCP transport is stdio: the CLI will spawn mcp_server.py per turn")
        return None

    env = {
        **os.environ,
        "MCP_TRANSPORT": "http",
        "MCP_HTTP_HOST": MCP_HTTP_HOST,
        "MCP_HTTP_PORT": str(MCP_HTTP_PORT),
    }
    proc = subprocess.Popen([sys.executable, "-u", MCP_SERVER_SCRIPT], env=env, cwd="/app")
    if _wait_for_port(MCP_HTTP_HOST, MCP_HTTP_PORT, MCP_STARTUP_TIMEOUT):
        try:
            logger.opt(colors=True).info("<green>[MCP SERVER READY]</green> host=<cyan>{}</cyan>, port=<yellow>{}</yellow>, pid=<magenta>{}</magenta>", MCP_HTTP_HOST, MCP_HTTP_PORT, proc.pid)
        except Exception:
            logger.info("mcp sidecar ready on {}:{} (pid {})", MCP_HTTP_HOST, MCP_HTTP_PORT, proc.pid)
    else:
        try:
            logger.opt(colors=True).error(
                "<red><b>[MCP SERVER FAILED]</b></red> did not accept connections within {}s - turns will start with no MCP tools",
                MCP_STARTUP_TIMEOUT,
            )
        except Exception:
            logger.error("mcp sidecar did not accept connections within {}s", MCP_STARTUP_TIMEOUT)
    return proc


def _supervise_mcp_sidecar(proc: subprocess.Popen) -> None:
    """Restarts the sidecar if it dies, since every turn depends on it being up.

    Under stdio a crashed MCP server cost one turn; sharing one process makes it cost
    every subsequent turn instead, so the process needs an owner that notices.
    """
    while True:
        proc.wait()
        try:
            logger.opt(colors=True).error("<red><b>[MCP SERVER CRASHED]</b></red> exited with code <yellow>{}</yellow>; restarting", proc.returncode)
        except Exception:
            logger.error("mcp sidecar exited with code {}; restarting", proc.returncode)
        time.sleep(1)
        proc = _start_mcp_sidecar()
        if proc is None:
            return


if __name__ == "__main__":
    render()
    mcp_proc = _start_mcp_sidecar()
    if mcp_proc is not None:
        threading.Thread(target=_supervise_mcp_sidecar, args=(mcp_proc,), daemon=True).start()
    try:
        logger.opt(colors=True).info("<green><b>[GATEWAY SERVER STARTED]</b></green> listening on port <yellow>:{}</yellow>", PORT)
    except Exception:
        logger.info("claude_gateway listening on :{}", PORT)
    ThreadingHTTPServer(("0.0.0.0", PORT), GatewayHandler).serve_forever()
