"""Two-phase confirm tokens.

What makes the two-phase confirmation real is that a token cannot be redeemed in the
turn that issued it: redeeming always means a later turn, and a later turn always
means the user sent a message in between. That check needs something that identifies
"this turn". It used to be _PROCESS_NONCE, which worked only because this server was
a stdio subprocess of a single `claude -p` - one process was one turn. The server is
now shared by every turn (see _start_mcp_sidecar in claude_gateway/gateway_server.py),
so the process nonce is now constant and would refuse *every* confirmation forever.
Each `claude -p` still opens its own MCP session, so the session takes over the role
the process used to play - and it does so under stdio too, where one session is still
one process is still one turn.
"""

from __future__ import annotations

import json
import os
import secrets
import time
import weakref
from typing import Any

from superset_mcp.app import mcp
from superset_mcp.config import CREATE_TOKEN_PATH, CREATE_TOKEN_TTL

_PROCESS_NONCE = secrets.token_hex(8)
_session_nonces: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


def _turn_nonce() -> str:
    try:
        session = mcp.get_context().session
    except Exception:
        return _PROCESS_NONCE
    try:
        return _session_nonces.setdefault(session, secrets.token_hex(8))
    except TypeError:
        # Not weak-referenceable: fall back rather than lose the guard entirely.
        return _PROCESS_NONCE


def _load_tokens(path: str) -> dict[str, dict[str, Any]]:
    try:
        with open(path, encoding="utf-8") as handle:
            tokens = json.load(handle)
    except (OSError, ValueError):
        return {}
    now = time.time()
    return {t: rec for t, rec in tokens.items() if rec.get("expires_at", 0) > now}


def _save_tokens(path: str, tokens: dict[str, dict[str, Any]]) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(tokens, handle)
    os.replace(tmp, path)


def issue_create_token(kind: str, payload: dict[str, Any]) -> str:
    tokens = _load_tokens(CREATE_TOKEN_PATH)
    token = secrets.token_hex(8)
    tokens[token] = {
        "kind": kind,
        "payload": payload,
        "nonce": _turn_nonce(),
        "expires_at": time.time() + CREATE_TOKEN_TTL,
    }
    _save_tokens(CREATE_TOKEN_PATH, tokens)
    return token


def consume_create_token(token: str, kind: str) -> dict[str, Any]:
    """Redeems a token, or raises ValueError.

    Kept as a raise rather than a `success=False` result: it is the guard that stops
    a chart being written without the user having answered, so it has to reach the
    assistant as a tool error, not as data it could summarise away.
    """
    tokens = _load_tokens(CREATE_TOKEN_PATH)
    record = tokens.get(token)
    if not record:
        raise ValueError("Invalid or expired confirm_token. Start over without a token.")
    if record["kind"] != kind:
        raise ValueError(f"Token is for {record['kind']}, not {kind}.")
    if record["nonce"] == _turn_nonce():
        raise ValueError(
            "confirm_token was issued in THIS turn. You must show the preview to the "
            "user and ask them to confirm; only call again once they say yes in a "
            "later turn."
        )
    tokens.pop(token, None)
    _save_tokens(CREATE_TOKEN_PATH, tokens)
    return record["payload"]
