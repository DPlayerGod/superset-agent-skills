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
logger.add(sys.stderr, level="INFO", format="{time:YYYY-MM-DD HH:mm:ss} {level} {message}")

PORT = int(os.getenv("CLAUDE_GATEWAY_PORT", "8090"))
MCP_CONFIG_PATH = "/app/mcp_servers.json"
SYSTEM_PROMPT_PATH = Path("/app/system_prompt.md")
DEADLINE_SECONDS = float(os.getenv("CLAUDE_DEADLINE_SECONDS", "150"))
# Alias ("sonnet", "opus", "fable") or full model name ("claude-haiku-4-5-20251001").
# Unset keeps the CLI's own default model.
MODEL = os.getenv("CLAUDE_GATEWAY_MODEL")

MCP_SERVER_NAME = "superset-postgres"
ALLOWED_TOOLS = ",".join(
    f"mcp__{MCP_SERVER_NAME}__{tool}"
    for tool in (
        "list_datasets",
        "describe_table",
        "run_sql_readonly",
        "get_chart",
        "create_dataset",
        "create_chart",
        "update_chart",
        "create_dashboard",
        "add_charts_to_dashboard",
        "delete_chart",
        "delete_dashboard",
    )
)
# Explicitly deny Claude's built-in tools so the gateway can only ever reach
# Postgres/Superset through the vetted MCP tools above, never Bash/file edits.
#
# ALLOWED_TOOLS above is only an auto-approval list, not a boundary: under
# --permission-mode dontAsk a built-in tool that appears in neither list still
# runs. Observed on 2026-08-14 - turns called TaskCreate/TaskUpdate despite them
# being absent from ALLOWED_TOOLS. So every built-in the gateway does not want has
# to be named here, and a name only denies itself: denying "Task" left TaskCreate,
# TaskUpdate and the rest of the family reachable. Ordered by what they would cost
# if a Superset user talked the model into one:
#   Monitor/Workflow/PowerShell - take a shell command, i.e. the Bash ban's back door
#   RemoteTrigger/Agent       - run work elsewhere, or spawn a whole second agent
#   Cron*/ScheduleWakeup      - schedule work that outlives this request
#   SendMessage/PushNotification/DesignSync/Artifact - send data outbound
#   EnterWorktree/ExitWorktree - touch the filesystem
#   AskUserQuestion           - blocks forever: nothing can answer it headless
#   Task*/*PlanMode/ToolSearch - harmless, but each one burns a tool call per turn
#   *McpResource*             - read MCP resources the 7 vetted tools do not expose
#
# The list is deliberately over-broad: a name that does not exist in the installed
# CLI costs nothing, while a missing one costs seconds. Both ToolSearch (4.2s) and
# RemoteTrigger (3.0-5.6s) were caught this way on 2026-08-18, each spending a whole
# round-trip before the first SQL statement ran.
DISALLOWED_TOOLS = ",".join(
    (
        "Bash", "PowerShell", "Read", "Write", "Edit", "Glob", "Grep",
        "WebFetch", "WebSearch", "NotebookEdit", "Task", "Agent",
        "Monitor", "Workflow", "RemoteTrigger",
        "Artifact", "AskUserQuestion", "EnterPlanMode", "ExitPlanMode",
        "CronCreate", "CronDelete", "CronList", "ScheduleWakeup",
        "SendMessage", "PushNotification", "DesignSync",
        "EnterWorktree", "ExitWorktree",
        "TaskCreate", "TaskGet", "TaskList", "TaskUpdate", "TaskOutput", "TaskStop",
        "ListMcpResourcesTool", "ReadMcpResourceTool", "ReadMcpResourceDirTool",
        # ToolSearch fetches schemas for deferred tools. With --strict-mcp-config
        # there is nothing for it to find, but the model still spends a call on it:
        # observed 2026-08-18 on "Tổng FTE hiện tại", 4.2s before the first SQL ran.
        "ToolSearch",
        # No --plugin-dir is ever passed (the A/B "skills" variant and its plugin
        # were removed 2026-08-18 - see docs/ab_results.md), so there are never any
        # skills to load. Denying it up front avoids the model reaching for it
        # anyway and eating a failed call: measured 7.6-8.9s of dead time per turn
        # on 2026-08-18 before this was denied unconditionally.
        "Skill",
    )
)
# ReportFindings is deliberately NOT denied, and this is load-bearing rather than an
# oversight. Denying it too - leaving the CLI with an empty built-in tool list -
# takes the MCP tools down with it: measured 2026-08-18, 2/2 turns made no tool call
# at all and answered "Top 5 dự án theo FTE" by writing out a fake
# `Tool: mcp__superset-postgres__run_sql_readonly` block with invented projects
# ("Project Phoenix", 42.75) in place of the real ones (Project Delta, 201.65). The
# same run with ReportFindings left open called run_sql_readonly twice and returned
# the real figures. Whatever the CLI does internally, an all-denied list is not a
# stricter version of this one - it is a silently hallucinating one. Re-test with
# `--disallowedTools` before removing any name here.

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
    return SYSTEM_PROMPT_PATH.read_text(encoding="utf-8") if SYSTEM_PROMPT_PATH.exists() else ""


def _build_prompt(question: str, context: dict[str, Any], row_limit: int) -> str:
    ctx_user = context.get("superset_user") if isinstance(context, dict) else None
    ctx_path = context.get("path") if isinstance(context, dict) else None
    header = f"[Superset user: {ctx_user}] [Page: {ctx_path}] [row_limit: {row_limit}]"
    return f"{header}\n\n{question}"


def _build_argv(prompt: str, session_id: str, resume: bool, variant: str = DEFAULT_VARIANT) -> list[str]:
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
        "--strict-mcp-config",
        "--allowedTools", ALLOWED_TOOLS,
        "--disallowedTools", DISALLOWED_TOOLS,
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
                logger.info("tool_use: {}({})", block.get("name"), json.dumps(block.get("input", {}))[:300])
            elif block.get("type") == "tool_result":
                logger.info("tool_result: {}", str(block.get("content"))[:300])
    elif etype == "result":
        logger.info("result: is_error={} cost={}", event.get("is_error"), event.get("total_cost_usd"))


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
    prompt: str, session_id: str, resume: bool, variant: str = DEFAULT_VARIANT
) -> Iterator[dict[str, Any]]:
    """Runs one headless Claude turn, yielding progress as the CLI produces it.

    Event shapes, all JSON-serialisable so the SSE endpoint can forward them as-is:

      {"type": "delta", "text": ...}   a slice of the answer, as it is generated
      {"type": "reset"}                a new assistant message began - drop the
                                       text streamed so far, it was narration
                                       before a tool call and is not the answer
      {"type": "tool", "name": ...}    a tool call started
      {"type": "done", "answer", "previews", "timing"}     turn finished
      {"type": "error", "message", "timing"}               turn failed

    Exactly one terminal event (`done` or `error`) is always yielded last, so a
    consumer can loop until it sees one. Abandoning the generator early (the
    client disconnected) runs the `finally` below, which kills the process group.
    """
    argv = _build_argv(prompt, session_id, resume, variant)
    logger.info(
        "exec claude ({}, session={}, variant={})", "resume" if resume else "new", session_id, variant
    )

    started_at = time.monotonic()
    # start_new_session puts the CLI and everything it spawns (MCP servers, helper
    # processes) in one process group, so the deadline can take the whole tree down.
    proc = subprocess.Popen(
        argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd="/app", start_new_session=True
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
            for event in _stream_claude(prompt, session_id, resume=resume, variant=variant):
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


if __name__ == "__main__":
    render()
    logger.info("claude_gateway listening on :{}", PORT)
    ThreadingHTTPServer(("0.0.0.0", PORT), GatewayHandler).serve_forever()
