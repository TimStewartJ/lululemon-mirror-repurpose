import json
import pathlib
import socket
import struct
import sys
import threading
import unittest

TOOLS = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))

import webview_devtools
from webview_devtools import DevToolsError


PROC_NET_UNIX = """Num       RefCount Protocol Flags    Type St Inode Path
0000000000000000: 00000002 00000000 00010000 0001 01  6307 @jdwp-control
0000000000000000: 00000002 00000000 00010000 0001 01 14561 @webview_devtools_remote_2604
0000000000000000: 00000003 00000000 00000000 0001 03 14570 @webview_devtools_remote_2604
0000000000000000: 00000002 00000000 00010000 0001 01 15002 @webview_devtools_remote_1188
0000000000000000: 00000002 00000000 00010000 0001 01  9911 @chrome_devtools_remote
0000000000000000: 00000002 00000000 00010000 0001 01  9912 /dev/socket/webview_devtools_remote_7
"""


def server_frame(opcode, payload, final=True):
    """An unmasked frame, as a server sends it."""
    header = bytearray([(0x80 if final else 0) | opcode])
    if len(payload) < 126:
        header.append(len(payload))
    elif len(payload) < 65536:
        header.append(126)
        header += struct.pack(">H", len(payload))
    else:
        header.append(127)
        header += struct.pack(">Q", len(payload))
    return bytes(header) + payload


class FakeDevTools:
    """A local DevTools endpoint: a page list and one scripted WebSocket.

    ``respond(request)`` returns the raw frames to send for each
    ``Runtime.evaluate`` the client makes.
    """

    def __init__(self, respond=None, *, pages=None, accept=None, upgrade_status="101 Switching Protocols"):
        self.respond = respond or (lambda request: [])
        self.accept = accept
        self.upgrade_status = upgrade_status
        self.requests = []
        self.control_frames = []
        self.failures = []
        self._stopping = threading.Event()
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.bind(("127.0.0.1", 0))
        self._listener.listen(4)
        # Closing a listener does not wake accept() everywhere; poll instead.
        self._listener.settimeout(0.05)
        self.port = self._listener.getsockname()[1]
        self.pages = pages if pages is not None else [
            {
                "type": "page",
                "url": "http://127.0.0.1:8787/dashboard/custom.html",
                "webSocketDebuggerUrl": f"ws://127.0.0.1:{self.port}/devtools/page/1",
            },
            {"type": "service_worker", "url": "http://127.0.0.1:8787/sw.js"},
        ]
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def close(self):
        self._stopping.set()
        self._thread.join(timeout=5)
        self._listener.close()

    def __enter__(self):
        return self

    def __exit__(self, *_exception):
        self.close()

    def _serve(self):
        while not self._stopping.is_set():
            try:
                connection, _ = self._listener.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            with connection:
                # Short reads, so a test that ends mid-conversation stops at once.
                connection.settimeout(0.05)
                try:
                    self._handle(connection)
                except (ConnectionError, OSError):
                    pass
                except Exception as error:  # Surface server-side assertions in the test.
                    self.failures.append(error)

    def _receive(self, connection, count):
        while True:
            try:
                return connection.recv(count)
            except socket.timeout:
                if self._stopping.is_set():
                    raise ConnectionError("the test is over") from None

    def _read_exactly(self, connection, count):
        data = b""
        while len(data) < count:
            chunk = self._receive(connection, count - len(data))
            if not chunk:
                raise ConnectionError("client went away")
            data += chunk
        return data

    def _read_client_frame(self, connection):
        """(opcode, payload) of one client frame; clients must mask."""
        first, second = self._read_exactly(connection, 2)
        if not second & 0x80:
            raise AssertionError("The client sent an unmasked frame")
        length = second & 0x7F
        if length == 126:
            (length,) = struct.unpack(">H", self._read_exactly(connection, 2))
        elif length == 127:
            (length,) = struct.unpack(">Q", self._read_exactly(connection, 8))
        mask = self._read_exactly(connection, 4)
        payload = self._read_exactly(connection, length)
        return first & 0x0F, bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))

    def _handle(self, connection):
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self._receive(connection, 4096)
            if not chunk:
                return
            head += chunk
        lines = head.decode("latin-1").split("\r\n")
        path = lines[0].split(" ")[1]
        headers = {
            name.strip().lower(): value.strip()
            for name, _, value in (line.partition(":") for line in lines[1:] if line)
        }
        if path == "/json":
            body = json.dumps(self.pages).encode("utf-8")
            connection.sendall(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                + f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode("ascii")
                + body
            )
            return
        accept = self.accept or webview_devtools.accept_key(headers["sec-websocket-key"])
        connection.sendall(
            f"HTTP/1.1 {self.upgrade_status}\r\nUpgrade: websocket\r\n"
            f"Connection: Upgrade\r\nSec-WebSocket-Accept: {accept}\r\n\r\n".encode("ascii")
        )
        while True:
            opcode, payload = self._read_client_frame(connection)
            if opcode == webview_devtools.OPCODE_CLOSE:
                return
            if opcode != webview_devtools.OPCODE_TEXT:
                self.control_frames.append((opcode, payload))
                continue
            request = json.loads(payload.decode("utf-8"))
            self.requests.append(request)
            for reply in self.respond(request):
                connection.sendall(reply)


def value_reply(request, value, kind="string"):
    return server_frame(webview_devtools.OPCODE_TEXT, json.dumps({
        "id": request["id"],
        "result": {"result": {"type": kind, "value": value}},
    }).encode("utf-8"))


class FramingTest(unittest.TestCase):
    def test_accept_key_matches_the_rfc_6455_example(self):
        self.assertEqual(
            "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=",
            webview_devtools.accept_key("dGhlIHNhbXBsZSBub25jZQ=="),
        )

    def test_masked_text_frame_matches_the_rfc_6455_example(self):
        self.assertEqual(
            bytes.fromhex("818537fa213d7f9f4d5158"),
            webview_devtools.encode_frame(
                webview_devtools.OPCODE_TEXT, b"Hello", bytes.fromhex("37fa213d")
            ),
        )

    def test_frame_length_grows_through_the_three_encodings(self):
        mask = b"\x00\x00\x00\x00"
        short = webview_devtools.encode_frame(webview_devtools.OPCODE_TEXT, b"a" * 125, mask)
        medium = webview_devtools.encode_frame(webview_devtools.OPCODE_TEXT, b"a" * 126, mask)
        long = webview_devtools.encode_frame(webview_devtools.OPCODE_TEXT, b"a" * 65536, mask)
        self.assertEqual(bytes([0x81, 0x80 | 125]), short[:2])
        self.assertEqual(bytes([0x81, 0x80 | 126, 0x00, 0x7E]), medium[:4])
        self.assertEqual(bytes([0x81, 0x80 | 127]) + struct.pack(">Q", 65536), long[:10])
        self.assertEqual(2 + 4 + 125, len(short))
        self.assertEqual(4 + 4 + 126, len(medium))
        self.assertEqual(10 + 4 + 65536, len(long))

    def test_a_mask_must_be_four_bytes(self):
        with self.assertRaises(ValueError):
            webview_devtools.encode_frame(webview_devtools.OPCODE_TEXT, b"", b"\x00")


class SocketDiscoveryTest(unittest.TestCase):
    def test_lists_each_debuggable_webview_once(self):
        self.assertEqual(
            ["webview_devtools_remote_1188", "webview_devtools_remote_2604"],
            webview_devtools.devtools_sockets(PROC_NET_UNIX),
        )

    def test_handles_device_line_endings(self):
        self.assertEqual(
            ["webview_devtools_remote_1188", "webview_devtools_remote_2604"],
            webview_devtools.devtools_sockets(PROC_NET_UNIX.replace("\n", "\r\n")),
        )

    def test_no_debuggable_webview(self):
        self.assertEqual([], webview_devtools.devtools_sockets("Num RefCount Protocol\n"))


class PageTest(unittest.TestCase):
    def open(self, server):
        pages = webview_devtools.list_pages(server.port)
        page = webview_devtools.Page(server.port, pages[0])
        self.addCleanup(page.close)
        return page

    def test_lists_only_pages(self):
        with FakeDevTools() as server:
            pages = webview_devtools.list_pages(server.port)
        self.assertEqual(
            ["http://127.0.0.1:8787/dashboard/custom.html"],
            [page["url"] for page in pages],
        )

    def test_evaluates_an_expression_by_value(self):
        with FakeDevTools(lambda request: [value_reply(request, "9:41")]) as server:
            page = self.open(server)
            self.assertEqual("http://127.0.0.1:8787/dashboard/custom.html", page.url)
            self.assertEqual("9:41", page.evaluate("clock()"))
            self.assertEqual(
                {
                    "id": 1,
                    "method": "Runtime.evaluate",
                    "params": {"expression": "clock()", "returnByValue": True},
                },
                server.requests[0],
            )
            self.assertEqual([], server.failures)

    def test_numbers_requests_and_ignores_other_messages(self):
        def respond(request):
            event = json.dumps({"method": "Runtime.executionContextCreated"}).encode("utf-8")
            stale = json.dumps({"id": request["id"] + 100, "result": {}}).encode("utf-8")
            return [
                server_frame(webview_devtools.OPCODE_TEXT, event),
                server_frame(webview_devtools.OPCODE_TEXT, stale),
                value_reply(request, request["id"], "number"),
            ]

        with FakeDevTools(respond) as server:
            page = self.open(server)
            self.assertEqual([1, 2, 3], [page.evaluate("id") for _ in range(3)])

    def test_reassembles_fragments_and_answers_pings(self):
        def respond(request):
            body = json.dumps({
                "id": request["id"],
                "result": {"result": {"type": "string", "value": "fragmented"}},
            }).encode("utf-8")
            return [
                server_frame(webview_devtools.OPCODE_TEXT, body[:10], final=False),
                server_frame(webview_devtools.OPCODE_PING, b"still there?"),
                server_frame(webview_devtools.OPCODE_CONTINUATION, body[10:25], final=False),
                server_frame(webview_devtools.OPCODE_PONG, b""),
                server_frame(webview_devtools.OPCODE_CONTINUATION, body[25:]),
            ]

        with FakeDevTools(respond) as server:
            page = self.open(server)
            self.assertEqual("fragmented", page.evaluate("x"))
            # A second exchange proves the pong was read before the next request.
            self.assertEqual("fragmented", page.evaluate("x"))
            self.assertEqual(
                (webview_devtools.OPCODE_PONG, b"still there?"),
                server.control_frames[0],
            )

    def test_carries_messages_longer_than_a_two_byte_length(self):
        text = "\u00e9" * 40_000
        with FakeDevTools(lambda request: [value_reply(request, request["params"]["expression"])]) as server:
            page = self.open(server)
            self.assertEqual(text, page.evaluate(text))
            self.assertEqual([], server.failures)

    def test_reports_an_exception_from_the_android_6_webview(self):
        def respond(request):
            return [server_frame(webview_devtools.OPCODE_TEXT, json.dumps({
                "id": request["id"],
                "result": {
                    "result": {"type": "object", "description": "ReferenceError: nope is not defined"},
                    "wasThrown": True,
                },
            }).encode("utf-8"))]

        with FakeDevTools(respond) as server:
            page = self.open(server)
            with self.assertRaisesRegex(DevToolsError, "ReferenceError: nope is not defined"):
                page.evaluate("nope")

    def test_reports_an_exception_from_a_current_browser(self):
        def respond(request):
            return [server_frame(webview_devtools.OPCODE_TEXT, json.dumps({
                "id": request["id"],
                "result": {
                    "result": {"type": "object", "description": "TypeError: x is null"},
                    "exceptionDetails": {"text": "Uncaught"},
                },
            }).encode("utf-8"))]

        with FakeDevTools(respond) as server:
            page = self.open(server)
            with self.assertRaisesRegex(DevToolsError, "TypeError: x is null"):
                page.evaluate("x.y")

    def test_reports_a_refused_request(self):
        def respond(request):
            return [server_frame(webview_devtools.OPCODE_TEXT, json.dumps({
                "id": request["id"],
                "error": {"code": -32601, "message": "'Runtime.evaluate' wasn't found"},
            }).encode("utf-8"))]

        with FakeDevTools(respond) as server:
            page = self.open(server)
            with self.assertRaisesRegex(DevToolsError, "wasn't found"):
                page.evaluate("1")

    def test_reports_a_page_that_closes_the_connection(self):
        with FakeDevTools(lambda request: [server_frame(webview_devtools.OPCODE_CLOSE, b"")]) as server:
            page = self.open(server)
            with self.assertRaisesRegex(DevToolsError, "closed the DevTools connection"):
                page.evaluate("1")

    def test_reports_a_dropped_connection(self):
        def respond(request):
            raise ConnectionError("gone")

        with FakeDevTools(respond) as server:
            page = self.open(server)
            with self.assertRaisesRegex(DevToolsError, "ended unexpectedly"):
                page.evaluate("1")

    def test_a_page_someone_else_is_debugging_has_no_socket(self):
        with self.assertRaisesRegex(DevToolsError, "already being debugged"):
            webview_devtools.Page(1, {"type": "page", "url": "http://127.0.0.1:8787/"})

    def test_rejects_an_upgrade_with_the_wrong_accept_key(self):
        with FakeDevTools(accept="bm90IHRoZSByaWdodCBrZXk=") as server:
            with self.assertRaisesRegex(DevToolsError, "wrong accept key"):
                webview_devtools.Page(server.port, webview_devtools.list_pages(server.port)[0])

    def test_rejects_a_refused_upgrade(self):
        with FakeDevTools(upgrade_status="403 Forbidden") as server:
            with self.assertRaisesRegex(DevToolsError, "upgrade refused: HTTP/1.1 403 Forbidden"):
                webview_devtools.Page(server.port, webview_devtools.list_pages(server.port)[0])

    def test_reports_an_unreachable_page_list(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as unused:
            unused.bind(("127.0.0.1", 0))
            port = unused.getsockname()[1]
        with self.assertRaisesRegex(DevToolsError, "Unable to list DevTools pages"):
            webview_devtools.list_pages(port, timeout=0.5)

    def test_reports_a_page_list_that_is_not_served(self):
        class NotFound(FakeDevTools):
            def _handle(self, connection):
                self._receive(connection, 4096)
                connection.sendall(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")

        with NotFound() as server:
            with self.assertRaisesRegex(DevToolsError, "page list returned 404"):
                webview_devtools.list_pages(server.port)


if __name__ == "__main__":
    unittest.main()
