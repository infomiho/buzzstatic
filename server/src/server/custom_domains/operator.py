from __future__ import annotations

import json
import logging
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.parse import urlsplit

from .errors import ClaimConflict, ClaimNotFound

logger = logging.getLogger(__name__)

TRANSITIONS_PATH = "/operator/domain-transitions"
CANCEL_SUFFIX = "/cancel"


class OperatorServer:
    """Private listener for operator-only custom-domain transition actions."""

    def __init__(
        self,
        operator_token: str,
        port: int,
        handoff_provider: Callable[[], list[dict[str, Any]]],
        cancel_provider: Callable[[int], dict[str, Any]],
        host: str = "0.0.0.0",
    ):
        self._operator_tokens = tuple(
            token.strip() for token in operator_token.split(",") if token.strip()
        )
        if not self._operator_tokens:
            raise ValueError("Operator token must not be empty")
        self._handoff_provider = handoff_provider
        self._cancel_provider = cancel_provider
        self._server = ThreadingHTTPServer((host, port), self._handler())
        self._server.daemon_threads = True
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        return self._server.server_port

    def start(self) -> None:
        if self._thread:
            return
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="buzz-custom-domain-operator",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        if not self._thread:
            self._server.server_close()
            return
        self._server.shutdown()
        self._thread.join(timeout=5)
        self._server.server_close()
        self._thread = None

    def _authorized(self, authorization: str | None) -> bool:
        if not authorization:
            return False
        return any(
            secrets.compare_digest(authorization, f"Bearer {token}")
            for token in self._operator_tokens
        )

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        operator = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                if not self._authenticate():
                    return
                if self._path() != TRANSITIONS_PATH:
                    self._send_error_json(404, "Not Found")
                    return
                logger.info("Custom-domain operator transition list read")
                try:
                    transitions = operator._handoff_provider()
                except Exception:
                    logger.exception("Could not read custom-domain operator transitions")
                    self._send_error_json(500, "Could not read transitions")
                    return
                self._send_json(200, {"transitions": transitions})

            def do_POST(self) -> None:
                if not self._authenticate():
                    return
                path = self._path()
                if path == TRANSITIONS_PATH:
                    self._send_method_not_allowed("GET")
                    return
                claim_id = self._cancel_claim_id(path)
                if claim_id is None:
                    self._send_error_json(404, "Not Found")
                    return
                try:
                    result = operator._cancel_provider(claim_id)
                except ClaimNotFound as exc:
                    self._send_error_json(404, str(exc))
                    return
                except ClaimConflict as exc:
                    self._send_error_json(409, str(exc))
                    return
                except Exception:
                    logger.exception(
                        "Custom-domain operator transition cancellation failed for claim %d",
                        claim_id,
                    )
                    self._send_error_json(500, "Could not cancel transition")
                    return
                logger.info(
                    "Custom-domain operator transition cancelled for claim %d",
                    claim_id,
                )
                self._send_json(200, result)

            def do_HEAD(self) -> None:
                self._reject_method()

            def do_PUT(self) -> None:
                self._reject_method()

            def do_PATCH(self) -> None:
                self._reject_method()

            def do_DELETE(self) -> None:
                self._reject_method()

            def do_OPTIONS(self) -> None:
                self._reject_method()

            def log_message(self, format: str, *args: Any) -> None:
                return

            def _path(self) -> str:
                return urlsplit(self.path).path

            @staticmethod
            def _cancel_claim_id(path: str) -> int | None:
                prefix = f"{TRANSITIONS_PATH}/"
                if not path.startswith(prefix) or not path.endswith(CANCEL_SUFFIX):
                    return None
                raw_claim_id = path[len(prefix) : -len(CANCEL_SUFFIX)]
                if not raw_claim_id.isdigit() or str(int(raw_claim_id)) != raw_claim_id:
                    return None
                claim_id = int(raw_claim_id)
                return claim_id if claim_id > 0 else None

            def _authenticate(self) -> bool:
                if operator._authorized(self.headers.get("Authorization")):
                    return True
                self._send_error_json(401, "Unauthorized", {"WWW-Authenticate": "Bearer"})
                return False

            def _reject_method(self) -> None:
                if not self._authenticate():
                    return
                path = self._path()
                if path == TRANSITIONS_PATH:
                    self._send_method_not_allowed("GET")
                elif self._cancel_claim_id(path) is not None:
                    self._send_method_not_allowed("POST")
                else:
                    self._send_error_json(404, "Not Found")

            def _send_method_not_allowed(self, allow: str) -> None:
                self._send_error_json(405, "Method Not Allowed", {"Allow": allow})

            def _send_error_json(
                self, status: int, detail: str, headers: dict[str, str] | None = None
            ) -> None:
                self._send_json(status, {"detail": detail}, headers)

            def _send_json(
                self,
                status: int,
                payload: Any,
                headers: dict[str, str] | None = None,
            ) -> None:
                body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode() + b"\n"
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", "no-store")
                for name, value in (headers or {}).items():
                    self.send_header(name, value)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(body)

        return Handler
