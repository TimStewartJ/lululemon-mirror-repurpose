"""A stand-in for the Mirror's companion, for the validation suite.

It listens on this computer, answers what Mirror Home sends the way the
companion would, as scripted by a check, and keeps every request so that the
check can see what the Mirror sent. See docs/assistant.md for the protocol.
"""

from __future__ import annotations

import http.server
import json
import threading
import time


class FakeCompanion:
    """Runs until closed; use as a context manager."""

    def __init__(self, key: str = "suite-companion-key"):
        self.key = key
        self.requests: list[dict] = []
        # Answers for the requests to come, first in first out; then the default.
        self.answers: list[dict] = []
        self.default_answer = {"reply": "Done.", "ignored": False, "listen": False, "acted": []}
        self.health = {
            "ok": True,
            "name": "mirror-companion",
            "version": "suite",
            "model": "scripted",
            "brain": {"ready": True, "detail": ""},
            "stt": {"ready": True, "model": "none", "device": "cpu", "detail": ""},
            "mirror": {"reachable": True, "version": "", "detail": ""},
            "busy": False,
            "uptimeSeconds": 1,
        }
        self._lock = threading.Lock()
        self._closed = False
        companion = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_arguments):
                pass

            def _answer(self, status: int, body: dict) -> None:
                raw = json.dumps(body).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(raw)))
                # A connection kept open would outlive a companion that was closed.
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(raw)
                self.close_connection = True

            def _handle(self, method: str) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                request = {
                    "at": time.time(),
                    "method": method,
                    "path": self.path,
                    "headers": {name.lower(): value for name, value in self.headers.items()},
                    "body": body,
                }
                with companion._lock:
                    companion.requests.append(request)
                if self.headers.get("Authorization") != "Bearer " + companion.key:
                    self._answer(401, {"error": "Unauthorized"})
                    return
                if (method, self.path) == ("GET", "/v1/health"):
                    self._answer(200, companion.health)
                elif (method, self.path) == ("POST", "/v1/event"):
                    self._answer(202, {"accepted": True})
                elif method == "POST" and self.path in ("/v1/utterance", "/v1/ask"):
                    with companion._lock:
                        answer = dict(companion.answers.pop(0) if companion.answers else companion.default_answer)
                    time.sleep(float(answer.pop("delaySeconds", 0)))
                    status = int(answer.pop("status", 200))
                    answer.setdefault("id", self.headers.get("X-Mirror-Utterance", ""))
                    self._answer(status, answer)
                else:
                    self._answer(404, {"error": "Not found"})

            def do_GET(self):  # noqa: N802 - the base class's naming
                self._handle("GET")

            def do_POST(self):  # noqa: N802
                self._handle("POST")

        self._server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    @property
    def emulator_address(self) -> str:
        """Where an Android emulator finds this computer's loopback."""
        return f"http://10.0.2.2:{self.port}"

    def script(self, *answers: dict) -> None:
        with self._lock:
            self.answers.extend(dict(answer) for answer in answers)

    def sent(self, path: str | None = None, since: int = 0) -> list[dict]:
        """The requests so far, in order; only those to ``path`` if one is given."""
        with self._lock:
            requests = list(self.requests[since:])
        return [request for request in requests if path is None or request["path"] == path]

    def count(self) -> int:
        with self._lock:
            return len(self.requests)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)

    def __enter__(self) -> "FakeCompanion":
        return self

    def __exit__(self, *_exception) -> None:
        self.close()
