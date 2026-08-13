"""Headless Claude Code gateway for the Superset AI chat panel.

Wraps `claude -p` (Claude Code CLI, headless mode) as a small HTTP service so
Superset's existing `/api/v1/vdt-ai-chat/query` proxy (superset/superset_config.py)
can call it exactly the way it called the old rule-based agent_gateway.py: POST
{question, session_id, context, row_limit} in, {"answer": "..."} (or
{"message": "..."} on error) out.

CLI flags below were verified against `claude --version` 2.1.197's `--help` output
inside the built claude_gateway image (--mcp-config, --strict-mcp-config,
--allowedTools/--disallowedTools, --permission-mode dontAsk, --resume/--session-id,
--append-system-prompt, --output-format stream-json --verbose, -p). There is no
--max-turns flag in this CLI version (an earlier draft assumed one); runaway
agentic loops are instead bounded by DEADLINE_SECONDS below. If the installed
Claude Code CLI version changes, re-run `claude --help` in the image and diff
against `_build_argv`.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from render_config import render

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("claude_gateway")

PORT = int(os.getenv("CLAUDE_GATEWAY_PORT", "8090"))
MCP_CONFIG_PATH = "/app/mcp_servers.json"
SYSTEM_PROMPT_PATH = Path("/app/system_prompt.md")
DEADLINE_SECONDS = float(os.getenv("CLAUDE_DEADLINE_SECONDS", "150"))

MCP_SERVER_NAME = "superset-postgres"
ALLOWED_TOOLS = ",".join(
    f"mcp__{MCP_SERVER_NAME}__{tool}"
    for tool in (
        "list_datasets",
        "describe_table",
        "run_sql_readonly",
        "create_dataset",
        "create_chart",
        "create_dashboard",
    )
)
# Explicitly deny Claude's built-in tools so the gateway can only ever reach
# Postgres/Superset through the vetted MCP tools above, never Bash/file edits.
DISALLOWED_TOOLS = "Bash,Read,Write,Edit,Glob,Grep,WebFetch,WebSearch,NotebookEdit,Task"

_SESSION_ID_RE = re.compile(r"^[0-9a-fA-F-]{36}$")

_known_sessions: set[str] = set()
_known_sessions_lock = threading.Lock()


def _system_prompt() -> str:
    return SYSTEM_PROMPT_PATH.read_text(encoding="utf-8") if SYSTEM_PROMPT_PATH.exists() else ""


def _build_prompt(question: str, context: dict[str, Any], row_limit: int) -> str:
    ctx_user = context.get("superset_user") if isinstance(context, dict) else None
    ctx_path = context.get("path") if isinstance(context, dict) else None
    header = f"[Superset user: {ctx_user}] [Page: {ctx_path}] [row_limit: {row_limit}]"
    return f"{header}\n\n{question}"


def _build_argv(prompt: str, session_id: str, resume: bool) -> list[str]:
    argv = [
        "claude", "-p",
        "--output-format", "stream-json", "--verbose",
        "--mcp-config", MCP_CONFIG_PATH,
        "--strict-mcp-config",
        "--allowedTools", ALLOWED_TOOLS,
        "--disallowedTools", DISALLOWED_TOOLS,
        "--permission-mode", "dontAsk",
        "--append-system-prompt", _system_prompt(),
    ]
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
                log.info("tool_use: %s(%s)", block.get("name"), json.dumps(block.get("input", {}))[:300])
            elif block.get("type") == "tool_result":
                log.info("tool_result: %s", str(block.get("content"))[:300])
    elif etype == "result":
        log.info("result: is_error=%s cost=%s", event.get("is_error"), event.get("total_cost_usd"))


def _run_claude(prompt: str, session_id: str, resume: bool) -> tuple[str | None, str | None]:
    """Runs one headless Claude turn. Returns (answer_text, error_message)."""
    argv = _build_argv(prompt, session_id, resume)
    log.info("exec claude (%s, session=%s)", "resume" if resume else "new", session_id)

    proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd="/app")
    deadline = time.monotonic() + DEADLINE_SECONDS
    answer: str | None = None
    is_error = False

    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            if time.monotonic() > deadline:
                proc.kill()
                return None, "Claude gateway timed out waiting for a response"
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            _log_event(event)
            if event.get("type") == "result":
                answer = event.get("result") or ""
                is_error = bool(event.get("is_error"))
    finally:
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

    if answer is None and proc.returncode not in (0, None):
        stderr = proc.stderr.read()[:500] if proc.stderr else ""
        return None, f"claude exited with code {proc.returncode}: {stderr}"
    if is_error:
        # A "result" event with is_error=true still carries human-readable text
        # in `result` (e.g. "Invalid API key · Fix external API key") - surface
        # it as the error message rather than showing it to the user as a
        # normal, successful answer.
        return None, answer or f"claude reported an error (exit code {proc.returncode})"
    return answer, None


def _query_claude(question: str, session_id: str, context: dict[str, Any], row_limit: int) -> tuple[str | None, str | None]:
    prompt = _build_prompt(question, context, row_limit)
    with _known_sessions_lock:
        seen_before = session_id in _known_sessions

    answer, error = _run_claude(prompt, session_id, resume=seen_before)
    if error and seen_before:
        # Session transcript may be gone (gateway restarted) - fall back to a fresh session
        # under the same id rather than failing the whole turn.
        log.warning("resume failed for session %s (%s), retrying as a new session", session_id, error)
        answer, error = _run_claude(prompt, session_id, resume=False)

    if error is None:
        with _known_sessions_lock:
            _known_sessions.add(session_id)
    return answer, error


class GatewayHandler(BaseHTTPRequestHandler):
    def _reply(self, status: int, payload: dict[str, Any]) -> None:
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self) -> None:
        self._reply(200, {"status": "ok"}) if self.path == "/health" else self._reply(404, {"message": "Not found"})

    def do_POST(self) -> None:
        if self.path != "/api/v1/agent/query":
            self._reply(404, {"message": "Not found"})
            return
        try:
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

            answer, error = _query_claude(question, session_id, context, row_limit)
            if error:
                self._reply(502, {"message": error})
                return
            self._reply(200, {"answer": answer or ""})
        except (ValueError, json.JSONDecodeError) as error:
            self._reply(400, {"message": str(error)})
        except Exception:
            log.exception("Unhandled error while querying Claude")
            self._reply(502, {"message": "Unable to process the request"})

    def log_message(self, _format: str, *_args: Any) -> None:
        return


if __name__ == "__main__":
    render()
    log.info("claude_gateway listening on :%s", PORT)
    ThreadingHTTPServer(("0.0.0.0", PORT), GatewayHandler).serve_forever()
