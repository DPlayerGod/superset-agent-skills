"""Superset configuration and same-origin bridge for the VDT AI chat."""

from __future__ import annotations

import os
import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from flask import Blueprint, Response, current_app, jsonify, request, send_from_directory
from flask_login import current_user
from jinja2 import ChoiceLoader, FileSystemLoader

AI_CHAT_DIR = "/app/pythonpath"
AGENT_GATEWAY_URL = os.getenv("AGENT_GATEWAY_URL", "http://agent_gateway:8090")


def _require_login() -> Response | None:
    if not current_user.is_authenticated:
        return jsonify({"message": "Authentication required"}), 401
    return None


def FLASK_APP_MUTATOR(app):
    """Install only local UI assets and proxy calls after Superset authentication."""
    app.jinja_loader = ChoiceLoader([FileSystemLoader(f"{AI_CHAT_DIR}/templates"), app.jinja_loader])
    chat = Blueprint("vdt_ai_chat", __name__, url_prefix="/api/v1/vdt-ai-chat")

    @chat.get("/static/<path:filename>")
    def static_asset(filename: str):
        return send_from_directory(f"{AI_CHAT_DIR}/static", filename)

    @chat.post("/query")
    def query():
        denied = _require_login()
        if denied:
            return denied
        payload = request.get_json(silent=True) or {}
        question = payload.get("question")
        if not isinstance(question, str) or not question.strip():
            return jsonify({"message": "question is required"}), 400
        payload["question"] = question.strip()
        payload["context"] = {**(payload.get("context") or {}), "superset_user": current_user.username}
        try:
            outbound = Request(
                f"{AGENT_GATEWAY_URL}/api/v1/agent/query",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(outbound, timeout=120) as response:
                return Response(response.read(), status=response.status, content_type="application/json")
        except HTTPError as error:
            return Response(error.read(), status=error.code, content_type="application/json")
        except URLError:
            current_app.logger.exception("VDT AI gateway is unavailable")
            return jsonify({"message": "AI gateway is unavailable"}), 503

    # The endpoint is protected by the Superset session check above (it may
    # trigger writes via the Claude gateway's MCP tools, gated on that auth).
    # Superset's generic CSRF-token endpoint requires a bearer token, which is
    # not available to an extension running in a normal web session.
    csrf = app.extensions.get("csrf")
    if csrf:
        csrf.exempt(chat)
    app.register_blueprint(chat)
