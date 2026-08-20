"""Superset configuration and same-origin bridge for the VDT AI chat."""

from __future__ import annotations

import os
import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from socket import timeout as SocketTimeout

from flask import (
    Blueprint,
    Response,
    current_app,
    jsonify,
    request,
    send_from_directory,
    stream_with_context,
)
from flask_login import current_user
from jinja2 import ChoiceLoader, FileSystemLoader

AI_CHAT_DIR = "/app/pythonpath"
AGENT_GATEWAY_URL = os.getenv("AGENT_GATEWAY_URL", "http://agent_gateway:8090")
SQLALCHEMY_DATABASE_URI = os.getenv(
    "SQLALCHEMY_DATABASE_URI",
    "postgresql://super_user:super_pass@postgres:5432/super_db",
)

# Embedded Dashboard & Iframe security configuration
ENABLE_CORS = True
CORS_OPTIONS = {
    "supports_credentials": True,
    "allow_headers": ["*"],
    "resources": ["*"],
    "origins": ["*"],
}

FEATURE_FLAGS = {
    "EMBEDDED_SUPERSET": True,
    "ENABLE_TEMPLATE_PROCESSING": True,
    "EXTRA_CATEGORICAL_COLOR_SCHEMES": True,
}

EXTRA_CATEGORICAL_COLOR_SCHEMES = [
    {
        "id": "redScheme",
        "label": "Red Scheme",
        "colors": ["#E74C3C", "#C0392B", "#FF4D4F", "#D9534F", "#CF1322", "#A8071A", "#820014", "#FF7875"],
    },
    {
        "id": "blueScheme",
        "label": "Blue Scheme",
        "colors": ["#1890FF", "#096DD9", "#0050B3", "#003A8C", "#002766", "#40A9FF", "#69C0FF", "#91D5FF"],
    },
    {
        "id": "greenScheme",
        "label": "Green Scheme",
        "colors": ["#52C41A", "#389E0D", "#237804", "#135200", "#73D13D", "#95DE64", "#B7EB8F", "#D9F7BE"],
    },
    {
        "id": "orangeScheme",
        "label": "Orange Scheme",
        "colors": ["#FA8C16", "#D46B08", "#AD4E00", "#873800", "#FFA940", "#FFC069", "#FFD591", "#FFE7BA"],
    },
    {
        "id": "purpleScheme",
        "label": "Purple Scheme",
        "colors": ["#722ED1", "#531DAB", "#391085", "#22075E", "#9254DE", "#B37FEB", "#D3ADF7", "#EFDBFF"],
    },
    {
        "id": "yellowScheme",
        "label": "Yellow Scheme",
        "colors": ["#FADB14", "#D4B106", "#AD8B00", "#876800", "#FFEC3D", "#FFF566", "#FFFF80", "#FFFBE6"],
    },
]

TALISMAN_ENABLED = False
TALISMAN_CONFIG = {
    "content_security_policy": None,
    "force_https": False,
    "frame_options": None,
}
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE = False
HTTP_HEADERS = {"X-Frame-Options": "ALLOWALL"}
GUEST_ROLE_NAME = "Gamma"

# Custom Security Manager to allow embedded guest tokens & authenticated users to access datasources
from superset.security import SupersetSecurityManager

class CustomSecurityManager(SupersetSecurityManager):
    def can_access_datasource(self, datasource) -> bool:
        try:
            from flask_login import current_user
            if not current_user.is_anonymous:
                return True
        except Exception:
            pass
        return super().can_access_datasource(datasource)

CUSTOM_SECURITY_MANAGER = CustomSecurityManager





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
        response = send_from_directory(f"{AI_CHAT_DIR}/static", filename)
        # Superset serves static files with a one-year max-age. These two are baked
        # into the image and change with every rebuild, under a filename that never
        # changes - so a cached copy silently outlives any number of deploys and the
        # panel keeps running last month's code. "no-cache" still caches; it just
        # forces revalidation, and the ETag turns the usual request into a 304.
        response.headers["Cache-Control"] = "no-cache"
        response.headers.pop("Expires", None)
        return response

    def _outbound_payload():
        """Validates the panel's body and stamps the authenticated username on it.

        Returns (payload, error_response); exactly one of the two is None.
        """
        denied = _require_login()
        if denied:
            return None, denied
        payload = request.get_json(silent=True) or {}
        question = payload.get("question")
        if not isinstance(question, str) or not question.strip():
            return None, (jsonify({"message": "question is required"}), 400)
        payload["question"] = question.strip()
        payload["context"] = {**(payload.get("context") or {}), "superset_user": current_user.username}
        return payload, None

    @chat.post("/stream")
    def stream():
        payload, denied = _outbound_payload()
        if denied:
            return denied

        # Everything below runs after the response has started, so failures can no
        # longer set a status code - they are reported as an SSE `error` event
        # instead, which is the shape the panel already handles.
        def relay():
            def fail(message):
                yield f"data: {json.dumps({'type': 'error', 'message': message}, ensure_ascii=False)}\n\n".encode()

            try:
                outbound = Request(
                    f"{AGENT_GATEWAY_URL}/api/v1/agent/stream",
                    data=json.dumps(payload).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(outbound, timeout=170) as response:
                    # Line at a time, not .read(): buffering the gateway's output
                    # here would undo the streaming it exists to provide.
                    for line in response:
                        yield line
            except HTTPError as error:
                body = json.loads(error.read() or b"{}")
                yield from fail(body.get("message") or f"AI gateway error {error.code}")
            except SocketTimeout:
                current_app.logger.exception("VDT AI gateway timed out")
                yield from fail("AI gateway timed out waiting for a response")
            except URLError:
                current_app.logger.exception("VDT AI gateway is unavailable")
                yield from fail("AI gateway is unavailable")
            except ValueError:
                yield from fail("AI gateway returned a malformed response")

        return Response(
            stream_with_context(relay()),
            mimetype="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                # nginx (and Superset's own proxy layer) buffer proxied responses by
                # default, which would hold the whole stream back and hand it over in
                # one piece at the end - the exact latency this endpoint removes.
                "X-Accel-Buffering": "no",
            },
        )

    @chat.post("/query")
    def query():
        payload, denied = _outbound_payload()
        if denied:
            return denied
        try:
            outbound = Request(
                f"{AGENT_GATEWAY_URL}/api/v1/agent/query",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            # Longer than the gateway's own DEADLINE_SECONDS (claude_gateway/gateway_server.py)
            # so the gateway always times out first and replies with a proper JSON
            # error, instead of this socket giving up first and leaving Flask to
            # serve an HTML error page that the frontend can't JSON.parse().
            with urlopen(outbound, timeout=170) as response:
                return Response(response.read(), status=response.status, content_type="application/json")
        except HTTPError as error:
            return Response(error.read(), status=error.code, content_type="application/json")
        except SocketTimeout:
            current_app.logger.exception("VDT AI gateway timed out")
            return jsonify({"message": "AI gateway timed out waiting for a response"}), 504
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
