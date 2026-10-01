#!/usr/bin/env python3
"""Inspect the dashboard page inside Mirror Home's WebView over DevTools.

Debuggable builds of Mirror Home enable WebView debugging, which exposes the
Chrome DevTools protocol on a local socket that ``adb forward`` can reach.
This is a small standard-library client for it: enough of RFC 6455 to carry
JSON messages, and ``Runtime.evaluate`` to read what the page is showing.
The WebView on a MIRROR is Chromium 44, so only long-standing protocol
methods are used.
"""

from __future__ import annotations

import base64
import hashlib
import http.client
import json
import os
import re
import socket
import struct


WEBSOCKET_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
OPCODE_CONTINUATION = 0x0
OPCODE_TEXT = 0x1
OPCODE_CLOSE = 0x8
OPCODE_PING = 0x9
OPCODE_PONG = 0xA
MAX_MESSAGE_BYTES = 16 * 1024 * 1024
SOCKET_PATTERN = re.compile(r"@(webview_devtools_remote_\d+)\s*$", re.MULTILINE)


class DevToolsError(RuntimeError):
    pass


def devtools_sockets(proc_net_unix: str) -> list[str]:
    """Abstract socket names of debuggable WebViews, from /proc/net/unix."""
    return sorted(set(SOCKET_PATTERN.findall(proc_net_unix)))


def encode_frame(opcode: int, payload: bytes, mask: bytes) -> bytes:
    """A final client frame; clients must mask everything they send."""
    if len(mask) != 4:
        raise ValueError("A WebSocket mask is four bytes")
    header = bytearray([0x80 | opcode])
    length = len(payload)
    if length < 126:
        header.append(0x80 | length)
    elif length < 65536:
        header.append(0x80 | 126)
        header += struct.pack(">H", length)
    else:
        header.append(0x80 | 127)
        header += struct.pack(">Q", length)
    masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
    return bytes(header) + mask + masked


def accept_key(key: str) -> str:
    digest = hashlib.sha1((key + WEBSOCKET_GUID).encode("ascii")).digest()
    return base64.b64encode(digest).decode("ascii")


class WebSocket:
    """A client connection that exchanges whole text messages."""

    def __init__(self, host: str, port: int, path: str, *, timeout: float = 15.0):
        self._socket = socket.create_connection((host, port), timeout=timeout)
        self._buffer = b""
        try:
            self._upgrade(host, port, path)
        except BaseException:
            self._socket.close()
            raise

    def _upgrade(self, host: str, port: int, path: str) -> None:
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        )
        self._socket.sendall(request.encode("ascii"))
        head = self._read_until(b"\r\n\r\n").decode("latin-1")
        status_line = head.split("\r\n", 1)[0]
        if " 101 " not in f"{status_line} ":
            raise DevToolsError(f"WebSocket upgrade refused: {status_line}")
        headers = {
            name.strip().lower(): value.strip()
            for name, _, value in (
                line.partition(":") for line in head.split("\r\n")[1:] if line
            )
        }
        if headers.get("sec-websocket-accept") != accept_key(key):
            raise DevToolsError("WebSocket upgrade returned the wrong accept key")

    def send_text(self, text: str) -> None:
        self._socket.sendall(encode_frame(OPCODE_TEXT, text.encode("utf-8"), os.urandom(4)))

    def receive_text(self) -> str:
        message = bytearray()
        while True:
            final, opcode, payload = self._read_frame()
            if opcode == OPCODE_PING:
                self._socket.sendall(encode_frame(OPCODE_PONG, payload, os.urandom(4)))
                continue
            if opcode == OPCODE_PONG:
                continue
            if opcode == OPCODE_CLOSE:
                raise DevToolsError("The page closed the DevTools connection")
            if opcode not in (OPCODE_TEXT, OPCODE_CONTINUATION):
                raise DevToolsError(f"Unexpected WebSocket opcode {opcode}")
            message += payload
            if len(message) > MAX_MESSAGE_BYTES:
                raise DevToolsError("DevTools message is too large")
            if final:
                return message.decode("utf-8")

    def close(self) -> None:
        try:
            self._socket.sendall(encode_frame(OPCODE_CLOSE, b"", os.urandom(4)))
        except OSError:
            pass
        finally:
            self._socket.close()

    def _read_frame(self) -> tuple[bool, int, bytes]:
        first, second = self._read_exactly(2)
        length = second & 0x7F
        if length == 126:
            (length,) = struct.unpack(">H", self._read_exactly(2))
        elif length == 127:
            (length,) = struct.unpack(">Q", self._read_exactly(8))
        if length > MAX_MESSAGE_BYTES:
            raise DevToolsError("DevTools frame is too large")
        mask = self._read_exactly(4) if second & 0x80 else b""
        payload = self._read_exactly(length)
        if mask:
            payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        return bool(first & 0x80), first & 0x0F, payload

    def _read_exactly(self, count: int) -> bytes:
        while len(self._buffer) < count:
            chunk = self._socket.recv(65536)
            if not chunk:
                raise DevToolsError("DevTools connection ended unexpectedly")
            self._buffer += chunk
        data, self._buffer = self._buffer[:count], self._buffer[count:]
        return data

    def _read_until(self, marker: bytes) -> bytes:
        while marker not in self._buffer:
            chunk = self._socket.recv(65536)
            if not chunk:
                raise DevToolsError("DevTools connection ended during the upgrade")
            self._buffer += chunk
            if len(self._buffer) > 65536:
                raise DevToolsError("WebSocket upgrade response is too large")
        head, _, self._buffer = self._buffer.partition(marker)
        return head


def list_pages(port: int, *, host: str = "127.0.0.1", timeout: float = 10.0) -> list[dict]:
    """Pages a forwarded DevTools socket offers, newest protocol or oldest."""
    connection = http.client.HTTPConnection(host, port, timeout=timeout)
    try:
        connection.request("GET", "/json")
        response = connection.getresponse()
        body = response.read()
    except (OSError, http.client.HTTPException) as error:
        raise DevToolsError(f"Unable to list DevTools pages: {error}") from error
    finally:
        connection.close()
    if response.status != 200:
        raise DevToolsError(f"DevTools page list returned {response.status}")
    pages = json.loads(body.decode("utf-8"))
    return [page for page in pages if page.get("type") == "page"]


class Page:
    """One inspectable page; evaluates JavaScript and returns plain values."""

    def __init__(self, port: int, page: dict, *, host: str = "127.0.0.1"):
        url = page.get("webSocketDebuggerUrl") or ""
        match = re.match(r"^ws://[^/]+(/.*)$", url)
        if not match:
            raise DevToolsError("The page is already being debugged or has no socket")
        self.url = page.get("url", "")
        self._socket = WebSocket(host, port, match.group(1))
        self._next_id = 0

    def evaluate(self, expression: str):
        """The expression's value. Objects must be JSON-serialisable."""
        self._next_id += 1
        request_id = self._next_id
        self._socket.send_text(json.dumps({
            "id": request_id,
            "method": "Runtime.evaluate",
            "params": {"expression": expression, "returnByValue": True},
        }))
        while True:
            message = json.loads(self._socket.receive_text())
            if message.get("id") != request_id:
                continue
            if "error" in message:
                raise DevToolsError(f"DevTools refused the request: {message['error']}")
            outcome = message.get("result", {})
            result = outcome.get("result", {})
            if outcome.get("wasThrown") or outcome.get("exceptionDetails"):
                raise DevToolsError(
                    "The page raised: " + str(result.get("description") or result)
                )
            return result.get("value")

    def close(self) -> None:
        self._socket.close()

    def __enter__(self) -> "Page":
        return self

    def __exit__(self, *_exception) -> None:
        self.close()
