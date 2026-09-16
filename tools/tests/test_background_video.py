import contextlib
import hashlib
import io
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

TOOLS = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))

import background_video  # noqa: E402
from background_video import (  # noqa: E402
    BOOTSTRAP_HEADER,
    CHUNK_SIZE,
    BackgroundVideoClient,
    BackgroundVideoClientError,
    BackgroundVideoConnectionError,
    _json_call,
    bootstrap_available,
    client_from_config,
    confirm_bootstrap,
    main,
    normalize_id,
    pair,
    parse_schedule_slot,
    perform_delete,
    provision,
    read_bootstrap_secret,
    redact_config,
    resolve_video_id,
    run_provision,
    run_push,
    run_schedule,
    validate_host,
    validate_video_file,
    write_secret_file,
)


class FakeResponse:
    def __init__(self, status, payload_bytes, reason="reason"):
        self.status = status
        self.reason = reason
        self._payload = payload_bytes

    def read(self):
        return self._payload


class FakeConnection:
    """Stand-in for http.client.HTTPConnection that never touches a socket."""

    def __init__(self, host, port, timeout=None):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.headers = {}
        self.sent_chunks = []
        self.request_calls = []
        self.closed = False
        self.method = None
        self.path = None
        self.response = FakeResponse(200, b"{}")

    def putrequest(self, method, path):
        self.method = method
        self.path = path

    def putheader(self, key, value):
        self.headers[key] = value

    def endheaders(self):
        pass

    def send(self, chunk):
        self.sent_chunks.append(chunk)

    def request(self, method, path, body=None, headers=None):
        self.method = method
        self.path = path
        self.headers.update(headers or {})
        self.request_calls.append((method, path, body, headers))

    def getresponse(self):
        return self.response

    def close(self):
        self.closed = True


def refuse_to_prompt(_message):
    raise AssertionError("prompt should not be called when confirmation is skipped")


class HostAndIdValidationTest(unittest.TestCase):
    def test_validate_host_accepts_private_and_loopback_ipv4(self):
        for host in ("127.0.0.1", "10.0.0.5", "192.168.1.20", "172.16.0.4"):
            self.assertEqual(host, str(validate_host(host)))

    def test_validate_host_rejects_public_ipv4(self):
        with self.assertRaisesRegex(BackgroundVideoClientError, "private or loopback"):
            validate_host("8.8.8.8")

    def test_validate_host_rejects_ipv6(self):
        with self.assertRaisesRegex(BackgroundVideoClientError, "private or loopback"):
            validate_host("::1")

    def test_validate_host_rejects_garbage(self):
        with self.assertRaisesRegex(BackgroundVideoClientError, "Invalid host address"):
            validate_host("not-a-host")

    def test_normalize_id_accepts_lowercase_64_hex(self):
        digest = "a" * 64
        self.assertEqual(digest, normalize_id(digest))
        self.assertEqual(digest, normalize_id(digest.upper()))

    def test_normalize_id_rejects_malformed_values(self):
        for bad in ("short", "g" * 64, "a" * 63, "a" * 65, "../../etc/passwd"):
            with self.assertRaises(BackgroundVideoClientError):
                normalize_id(bad)


class VideoFileValidationTest(unittest.TestCase):
    def test_rejects_non_mp4_extension(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "clip.mov"
            path.write_bytes(b"data")
            with self.assertRaisesRegex(BackgroundVideoClientError, "\\.mp4"):
                validate_video_file(path)

    def test_rejects_empty_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "clip.mp4"
            path.write_bytes(b"")
            with self.assertRaisesRegex(BackgroundVideoClientError, "empty"):
                validate_video_file(path)

    def test_rejects_oversized_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "clip.mp4"
            path.write_bytes(b"x" * 4096)
            with mock.patch("background_video.MAX_VIDEO_BYTES", 1024):
                with self.assertRaisesRegex(BackgroundVideoClientError, "limit"):
                    validate_video_file(path)

    def test_rejects_missing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "missing.mp4"
            with self.assertRaisesRegex(BackgroundVideoClientError, "does not exist"):
                validate_video_file(path)


class WriteSecretFilePosixTest(unittest.TestCase):
    """POSIX: the restrictive mode must be set atomically at creation time
    (via os.open's mode argument) instead of a wider-open write followed by
    a later chmod, which leaves a window where the secret is readable."""

    def test_creates_temp_file_with_mode_0600_via_a_single_open_call(self):
        with tempfile.TemporaryDirectory() as directory:
            target = pathlib.Path(directory) / "secrets" / "cfg.json"

            with mock.patch("background_video.os.name", "posix"), mock.patch(
                "background_video.os.open", wraps=background_video.os.open
            ) as open_mock, mock.patch(
                "background_video.os.chmod"
            ) as chmod:
                write_secret_file(target, "{}\n")

            self.assertTrue(target.is_file())
            self.assertEqual("{}\n", target.read_text(encoding="utf-8"))
            open_mock.assert_called_once()
            _, flags, mode = open_mock.call_args[0]
            self.assertTrue(flags & os.O_CREAT)
            self.assertTrue(flags & os.O_EXCL)
            self.assertTrue(flags & os.O_WRONLY)
            self.assertEqual(0o600, mode)
            # No separate chmod call: the mode must come from creation itself.
            chmod.assert_not_called()

    @unittest.skipUnless(os.name == "posix", "permission bits are only meaningful on POSIX")
    def test_real_file_mode_is_0600_on_posix(self):
        with tempfile.TemporaryDirectory() as directory:
            target = pathlib.Path(directory) / "cfg.json"
            write_secret_file(target, "{}\n")
            mode = os.stat(target).st_mode & 0o777
            self.assertEqual(0o600, mode)

    def test_open_failure_propagates_and_leaves_no_leftover_temp_file(self):
        with tempfile.TemporaryDirectory() as directory:
            target = pathlib.Path(directory) / "cfg.json"
            with mock.patch("background_video.os.name", "posix"), mock.patch(
                "background_video.os.open", side_effect=OSError("disk full")
            ):
                with self.assertRaises(OSError):
                    write_secret_file(target, "super-secret-content\n")
            self.assertFalse(target.exists())
            self.assertEqual([], list(pathlib.Path(directory).glob("*.tmp")))

    def test_replace_failure_cleans_up_the_already_written_temp_file(self):
        with tempfile.TemporaryDirectory() as directory:
            target = pathlib.Path(directory) / "cfg.json"
            with mock.patch("background_video.os.name", "posix"), mock.patch.object(
                pathlib.Path, "replace", autospec=True, side_effect=OSError("cross-device link")
            ):
                with self.assertRaises(OSError):
                    write_secret_file(target, "super-secret-content\n")
            self.assertFalse(target.exists())
            self.assertEqual([], list(pathlib.Path(directory).glob("*.tmp")))

    def test_leaves_no_leftover_temp_file_on_success(self):
        with tempfile.TemporaryDirectory() as directory:
            target = pathlib.Path(directory) / "cfg.json"
            with mock.patch("background_video.os.name", "posix"):
                write_secret_file(target, "{}\n")
            leftovers = list(pathlib.Path(directory).glob("*.tmp"))
            self.assertEqual([], leftovers)


class WriteSecretFileWindowsHardeningTest(unittest.TestCase):
    """Windows secrets are created inside a pre-restricted private directory."""

    def test_applies_icacls_before_writing_secret_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            target = pathlib.Path(directory) / "cfg.json"
            observed_content_when_icacls_ran = []

            def fake_icacls(args, capture_output, text, check):
                temp_path = type(target)(args[1])
                observed_content_when_icacls_ran.append(
                    "<directory>"
                    if temp_path.is_dir()
                    else temp_path.read_text(encoding="utf-8")
                )
                return subprocess.CompletedProcess(
                    args, 0, stdout="Successfully processed 1 files", stderr=""
                )

            with mock.patch("background_video.os.name", "nt"), mock.patch.dict(
                os.environ, {"USERNAME": "tester", "USERDOMAIN": "TESTDOM"}, clear=False
            ), mock.patch(
                "background_video.subprocess.run", side_effect=fake_icacls
            ) as run:
                write_secret_file(target, "super-secret-content\n")

            self.assertTrue(target.is_file())
            self.assertEqual("super-secret-content\n", target.read_text(encoding="utf-8"))
            self.assertEqual(2, run.call_count)
            directory_command = run.call_args_list[0].args[0]
            file_command = run.call_args_list[1].args[0]
            self.assertEqual("icacls", directory_command[0])
            self.assertIn("/inheritance:r", directory_command)
            self.assertIn("/grant:r", directory_command)
            self.assertEqual("TESTDOM\\tester:(OI)(CI)F", directory_command[-1])
            self.assertEqual("TESTDOM\\tester:F", file_command[-1])
            self.assertEqual(["<directory>", ""], observed_content_when_icacls_ran)
            self.assertEqual([target], list(type(target)(directory).iterdir()))

    def test_icacls_failure_raises_and_cleans_up_temp_file(self):
        with tempfile.TemporaryDirectory() as directory:
            target = pathlib.Path(directory) / "cfg.json"

            def fake_icacls(args, capture_output, text, check):
                return subprocess.CompletedProcess(
                    args, 1, stdout="", stderr="Access is denied."
                )

            with mock.patch("background_video.os.name", "nt"), mock.patch.dict(
                os.environ, {"USERNAME": "tester"}, clear=False
            ), mock.patch("background_video.subprocess.run", side_effect=fake_icacls):
                with self.assertRaisesRegex(
                    BackgroundVideoClientError, "Access is denied"
                ) as raised:
                    write_secret_file(target, "super-secret-content\n")

            self.assertNotIn("super-secret-content", str(raised.exception))
            self.assertFalse(target.exists())
            self.assertEqual([], list(type(target)(directory).iterdir()))

    def test_icacls_missing_executable_raises_and_cleans_up(self):
        with tempfile.TemporaryDirectory() as directory:
            target = pathlib.Path(directory) / "cfg.json"
            with mock.patch("background_video.os.name", "nt"), mock.patch.dict(
                os.environ, {"USERNAME": "tester"}, clear=False
            ), mock.patch(
                "background_video.subprocess.run",
                side_effect=FileNotFoundError("icacls not found"),
            ):
                with self.assertRaisesRegex(BackgroundVideoClientError, "Unable to invoke icacls"):
                    write_secret_file(target, "super-secret-content\n")
            self.assertFalse(target.exists())
            self.assertEqual([], list(type(target)(directory).iterdir()))

    def test_missing_username_env_raises_before_any_subprocess_call(self):
        with tempfile.TemporaryDirectory() as directory:
            target = pathlib.Path(directory) / "cfg.json"
            with mock.patch("background_video.os.name", "nt"), mock.patch.dict(
                os.environ, {"USERNAME": ""}, clear=False
            ), mock.patch("background_video.subprocess.run") as run:
                with self.assertRaisesRegex(BackgroundVideoClientError, "Unable to determine"):
                    write_secret_file(target, "super-secret-content\n")
            run.assert_not_called()
            self.assertFalse(target.exists())
            self.assertEqual([], list(type(target)(directory).iterdir()))


class RedactConfigTest(unittest.TestCase):
    def test_redact_config_strips_token_only(self):
        config = {
            "host": "127.0.0.1",
            "port": 8787,
            "token": "super-secret",
            "clientId": "abc",
            "clientName": "Living Room",
        }
        redacted = redact_config(config)
        self.assertNotIn("token", redacted)
        self.assertEqual("127.0.0.1", redacted["host"])
        self.assertEqual("abc", redacted["clientId"])


class ClientFromConfigTest(unittest.TestCase):
    def test_missing_host_raises(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "cfg.json"
            path.write_text(json.dumps({"token": "t" * 40}), encoding="utf-8")
            with self.assertRaisesRegex(BackgroundVideoClientError, "host"):
                client_from_config(path)

    def test_missing_token_raises(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "cfg.json"
            path.write_text(json.dumps({"host": "127.0.0.1"}), encoding="utf-8")
            with self.assertRaisesRegex(BackgroundVideoClientError, "token"):
                client_from_config(path)

    def test_host_and_port_overrides_are_applied(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "cfg.json"
            path.write_text(
                json.dumps({"host": "127.0.0.1", "port": 8787, "token": "t" * 40}),
                encoding="utf-8",
            )
            client, _ = client_from_config(path, host_override="10.0.0.9", port_override=9000)
            self.assertEqual("10.0.0.9", client.host)
            self.assertEqual(9000, client.port)


class JsonCallTest(unittest.TestCase):
    def test_surfaces_error_message_from_json_body(self):
        payload = json.dumps({"error": "pairing code expired"}).encode("utf-8")

        def factory(host, port, timeout=None):
            connection = FakeConnection(host, port, timeout)
            connection.response = FakeResponse(409, payload, reason="Conflict")
            return connection

        with mock.patch("background_video.http.client.HTTPConnection", side_effect=factory):
            with self.assertRaisesRegex(BackgroundVideoClientError, "pairing code expired"):
                _json_call("127.0.0.1", 8787, "GET", "/api/v1/background-videos")

    def test_wraps_connection_reset_cleanly(self):
        class BrokenConnection(FakeConnection):
            def request(self, *args, **kwargs):
                raise ConnectionResetError("reset by peer")

        with mock.patch(
            "background_video.http.client.HTTPConnection",
            side_effect=lambda host, port, timeout=None: BrokenConnection(host, port, timeout),
        ):
            with self.assertRaisesRegex(BackgroundVideoClientError, "Unable to reach") as raised:
                _json_call("127.0.0.1", 8787, "GET", "/api/v1/background-videos")
        # Transport-level failures are a distinguishable subtype so callers
        # (like run_provision) can tell an ambiguous outcome apart from a
        # definitive server response.
        self.assertIsInstance(raised.exception, BackgroundVideoConnectionError)

    def test_http_error_status_is_not_a_connection_error(self):
        payload = json.dumps({"error": "not authorized"}).encode("utf-8")

        def factory(host, port, timeout=None):
            connection = FakeConnection(host, port, timeout)
            connection.response = FakeResponse(401, payload, reason="Unauthorized")
            return connection

        with mock.patch("background_video.http.client.HTTPConnection", side_effect=factory):
            with self.assertRaises(BackgroundVideoClientError) as raised:
                _json_call("127.0.0.1", 8787, "GET", "/api/v1/background-videos")
        # A real (if unhappy) response from the server is a definitive
        # outcome, not a connection-level ambiguity.
        self.assertNotIsInstance(raised.exception, BackgroundVideoConnectionError)

    def test_rejects_non_json_success_body(self):
        def factory(host, port, timeout=None):
            connection = FakeConnection(host, port, timeout)
            connection.response = FakeResponse(200, b"not json", reason="OK")
            return connection

        with mock.patch("background_video.http.client.HTTPConnection", side_effect=factory):
            with self.assertRaisesRegex(BackgroundVideoClientError, "invalid JSON"):
                _json_call("127.0.0.1", 8787, "GET", "/api/v1/background-videos")


class PairTest(unittest.TestCase):
    def test_pair_returns_identity_without_touching_network(self):
        with mock.patch(
            "background_video._json_call",
            return_value={
                "token": "server-issued-token",
                "clientId": "client-1",
                "clientName": "Living Room",
            },
        ) as call:
            result = pair("192.168.1.20", "123456", "My Laptop", port=8787)

        self.assertEqual("server-issued-token", result["token"])
        self.assertEqual("192.168.1.20", result["host"])
        self.assertEqual("client-1", result["clientId"])
        call.assert_called_once()
        args, kwargs = call.call_args
        self.assertEqual("192.168.1.20", args[0])
        self.assertEqual(8787, args[1])
        self.assertEqual("POST", args[2])
        self.assertEqual("/api/v1/pair", args[3])
        self.assertEqual("123456", args[4]["code"])

    def test_pair_rejects_public_host_before_any_request(self):
        with mock.patch("background_video._json_call") as call:
            with self.assertRaises(BackgroundVideoClientError):
                pair("8.8.8.8", "123456", "My Laptop")
        call.assert_not_called()

    def test_pair_raises_when_token_missing_from_response(self):
        with mock.patch("background_video._json_call", return_value={"clientId": "c1"}):
            with self.assertRaisesRegex(BackgroundVideoClientError, "token"):
                pair("127.0.0.1", "123456", "My Laptop")


class ProvisionTest(unittest.TestCase):
    def test_read_bootstrap_secret_rejects_short_values(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "bootstrap.txt"
            path.write_text("short-secret\n", encoding="utf-8")
            with self.assertRaisesRegex(BackgroundVideoClientError, "32-256"):
                read_bootstrap_secret(path)

    def test_read_bootstrap_secret_rejects_oversized_values(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "bootstrap.txt"
            path.write_text("x" * 400, encoding="utf-8")
            with self.assertRaisesRegex(BackgroundVideoClientError, "32-256"):
                read_bootstrap_secret(path)

    def test_read_bootstrap_secret_rejects_missing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "missing-bootstrap.txt"
            with self.assertRaisesRegex(BackgroundVideoClientError, "Unable to read"):
                read_bootstrap_secret(path)

    def test_read_bootstrap_secret_strips_whitespace_and_accepts_valid_length(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "bootstrap.txt"
            secret = "s" * 40
            path.write_text(f"  {secret}  \n", encoding="utf-8")
            self.assertEqual(secret, read_bootstrap_secret(path))

    def test_provision_posts_to_bootstrap_path_with_secret_header_and_empty_body(self):
        with tempfile.TemporaryDirectory() as directory:
            bootstrap_file = pathlib.Path(directory) / "bootstrap.txt"
            secret = "s" * 48
            bootstrap_file.write_text(secret, encoding="utf-8")

            with mock.patch(
                "background_video._json_call",
                return_value={
                    "token": "server-issued-token",
                    "clientId": "client-9",
                    "clientName": "Bedroom",
                },
            ) as call:
                result = provision("192.168.1.30", bootstrap_file, port=8787)

            self.assertEqual("server-issued-token", result["token"])
            self.assertEqual("192.168.1.30", result["host"])
            self.assertEqual("client-9", result["clientId"])
            call.assert_called_once()
            args, kwargs = call.call_args
            self.assertEqual("192.168.1.30", args[0])
            self.assertEqual(8787, args[1])
            self.assertEqual("POST", args[2])
            self.assertEqual("/api/v1/background-videos/bootstrap", args[3])
            self.assertEqual({}, args[4])
            self.assertEqual({BOOTSTRAP_HEADER: secret}, kwargs["headers"])

    def test_provision_rejects_public_host_before_reading_secret_or_calling(self):
        with tempfile.TemporaryDirectory() as directory:
            bootstrap_file = pathlib.Path(directory) / "bootstrap.txt"
            bootstrap_file.write_text("s" * 48, encoding="utf-8")
            with mock.patch("background_video._json_call") as call, mock.patch.object(
                pathlib.Path,
                "read_text",
                autospec=True,
                side_effect=AssertionError("secret should not be read for an invalid host"),
            ):
                with self.assertRaises(BackgroundVideoClientError):
                    provision("8.8.8.8", bootstrap_file)
            call.assert_not_called()

    def test_provision_rejects_short_secret_before_calling(self):
        with tempfile.TemporaryDirectory() as directory:
            bootstrap_file = pathlib.Path(directory) / "bootstrap.txt"
            bootstrap_file.write_text("too-short", encoding="utf-8")
            with mock.patch("background_video._json_call") as call:
                with self.assertRaisesRegex(BackgroundVideoClientError, "32-256"):
                    provision("127.0.0.1", bootstrap_file)
            call.assert_not_called()

    def test_provision_raises_when_token_missing_from_response(self):
        with tempfile.TemporaryDirectory() as directory:
            bootstrap_file = pathlib.Path(directory) / "bootstrap.txt"
            bootstrap_file.write_text("s" * 48, encoding="utf-8")
            with mock.patch("background_video._json_call", return_value={"clientId": "c1"}):
                with self.assertRaisesRegex(BackgroundVideoClientError, "token"):
                    provision("127.0.0.1", bootstrap_file)

    def test_main_provision_saves_redacted_identity_and_never_prints_secret_or_token(self):
        with tempfile.TemporaryDirectory() as directory:
            bootstrap_file = pathlib.Path(directory) / "bootstrap.txt"
            secret = "s" * 48
            bootstrap_file.write_text(secret, encoding="utf-8")
            config_path = pathlib.Path(directory) / "cfg.json"

            def fake_json_call(host, port, method, path, payload=None, *, headers=None, timeout=30):
                if path == "/api/v1/background-videos/bootstrap":
                    self.assertEqual({BOOTSTRAP_HEADER: secret}, headers)
                    return {
                        "token": "server-issued-token",
                        "clientId": "client-9",
                        "clientName": "Bedroom",
                    }
                if path == "/api/v1/background-videos/bootstrap/confirm":
                    self.assertEqual({"Authorization": "Bearer server-issued-token"}, headers)
                    return {"confirmed": True}
                raise AssertionError(f"unexpected path: {path}")

            with mock.patch("background_video._json_call", side_effect=fake_json_call):
                buffer = io.StringIO()
                with contextlib.redirect_stdout(buffer):
                    exit_code = main(
                        [
                            "--config",
                            str(config_path),
                            "--host",
                            "127.0.0.1",
                            "provision",
                            "--bootstrap-file",
                            str(bootstrap_file),
                        ]
                    )

            self.assertEqual(0, exit_code)
            output = buffer.getvalue()
            self.assertNotIn("server-issued-token", output)
            self.assertNotIn(secret, output)
            self.assertIn("clientId", output)
            self.assertIn('"confirmed": true', output)

            saved = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual("server-issued-token", saved["token"])
            self.assertEqual("client-9", saved["clientId"])
            self.assertEqual("127.0.0.1", saved["host"])

    def test_main_provision_leaves_config_saved_when_confirmation_is_not_acknowledged(self):
        with tempfile.TemporaryDirectory() as directory:
            bootstrap_file = pathlib.Path(directory) / "bootstrap.txt"
            secret = "s" * 48
            bootstrap_file.write_text(secret, encoding="utf-8")
            config_path = pathlib.Path(directory) / "cfg.json"

            def fake_json_call(host, port, method, path, payload=None, *, headers=None, timeout=30):
                if path == "/api/v1/background-videos/bootstrap":
                    return {
                        "token": "pending-token",
                        "clientId": "client-9",
                        "clientName": "Bedroom",
                    }
                if path == "/api/v1/background-videos/bootstrap/confirm":
                    return {"confirmed": False}
                raise AssertionError(f"unexpected path: {path}")

            with mock.patch("background_video._json_call", side_effect=fake_json_call):
                buffer = io.StringIO()
                with contextlib.redirect_stderr(buffer):
                    exit_code = main(
                        [
                            "--config",
                            str(config_path),
                            "--host",
                            "127.0.0.1",
                            "provision",
                            "--bootstrap-file",
                            str(bootstrap_file),
                        ]
                    )

            self.assertEqual(1, exit_code)
            error_output = buffer.getvalue()
            self.assertIn("not acknowledged", error_output)
            self.assertNotIn(secret, error_output)
            self.assertNotIn("pending-token", error_output)

            # The bootstrap phase already succeeded, so the pending token must
            # remain saved -- re-running provision recovers and confirms it.
            self.assertTrue(config_path.exists())
            saved = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual("pending-token", saved["token"])

    def test_main_provision_requires_host(self):
        with tempfile.TemporaryDirectory() as directory:
            bootstrap_file = pathlib.Path(directory) / "bootstrap.txt"
            bootstrap_file.write_text("s" * 48, encoding="utf-8")
            config_path = pathlib.Path(directory) / "cfg.json"
            buffer = io.StringIO()
            with contextlib.redirect_stderr(buffer):
                exit_code = main(
                    [
                        "--config",
                        str(config_path),
                        "provision",
                        "--bootstrap-file",
                        str(bootstrap_file),
                    ]
                )
            self.assertEqual(1, exit_code)
            self.assertIn("--host is required", buffer.getvalue())
            self.assertFalse(config_path.exists())


class ConfirmBootstrapTest(unittest.TestCase):
    def test_confirm_posts_to_confirm_path_with_bearer_token_and_empty_body(self):
        with mock.patch(
            "background_video._json_call", return_value={"confirmed": True}
        ) as call:
            result = confirm_bootstrap("127.0.0.1", 8787, "new-token-value")

        self.assertTrue(result["confirmed"])
        call.assert_called_once()
        args, kwargs = call.call_args
        self.assertEqual("127.0.0.1", args[0])
        self.assertEqual(8787, args[1])
        self.assertEqual("POST", args[2])
        self.assertEqual("/api/v1/background-videos/bootstrap/confirm", args[3])
        self.assertEqual({}, args[4])
        self.assertEqual({"Authorization": "Bearer new-token-value"}, kwargs["headers"])

    def test_confirm_raises_when_server_does_not_acknowledge(self):
        with mock.patch("background_video._json_call", return_value={"confirmed": False}):
            with self.assertRaisesRegex(BackgroundVideoClientError, "not acknowledged"):
                confirm_bootstrap("127.0.0.1", 8787, "token")

    def test_confirm_raises_when_confirmed_key_missing(self):
        with mock.patch("background_video._json_call", return_value={}):
            with self.assertRaisesRegex(BackgroundVideoClientError, "not acknowledged"):
                confirm_bootstrap("127.0.0.1", 8787, "token")

    def test_confirm_rejects_public_host_before_calling(self):
        with mock.patch("background_video._json_call") as call:
            with self.assertRaises(BackgroundVideoClientError):
                confirm_bootstrap("8.8.8.8", 8787, "token")
        call.assert_not_called()

    def test_confirm_requires_a_token(self):
        with mock.patch("background_video._json_call") as call:
            with self.assertRaises(BackgroundVideoClientError):
                confirm_bootstrap("127.0.0.1", 8787, "")
        call.assert_not_called()


class BootstrapAvailableTest(unittest.TestCase):
    def test_queries_the_public_bootstrap_endpoint_and_returns_availability(self):
        with mock.patch(
            "background_video._json_call", return_value={"available": True}
        ) as call:
            self.assertTrue(bootstrap_available("127.0.0.1", 8787))
        call.assert_called_once()
        args, kwargs = call.call_args
        self.assertEqual("127.0.0.1", args[0])
        self.assertEqual(8787, args[1])
        self.assertEqual("GET", args[2])
        self.assertEqual("/api/v1/background-videos/bootstrap", args[3])

    def test_returns_false_when_available_key_is_missing_or_falsy(self):
        with mock.patch("background_video._json_call", return_value={}):
            self.assertFalse(bootstrap_available("127.0.0.1", 8787))

    def test_rejects_public_host_before_calling(self):
        with mock.patch("background_video._json_call") as call:
            with self.assertRaises(BackgroundVideoClientError):
                bootstrap_available("8.8.8.8", 8787)
        call.assert_not_called()

    def test_connection_failure_propagates_rather_than_reporting_unavailable(self):
        with mock.patch(
            "background_video._json_call",
            side_effect=BackgroundVideoConnectionError("reset by peer"),
        ):
            with self.assertRaises(BackgroundVideoConnectionError):
                bootstrap_available("127.0.0.1", 8787)


class RunProvisionTest(unittest.TestCase):
    IDENTITY = {
        "host": "127.0.0.1",
        "port": 8787,
        "token": "pending-token",
        "clientId": "client-1",
        "clientName": "Living Room",
    }

    def test_saves_config_before_confirming_and_returns_confirmed_result(self):
        calls = []

        def fake_provision(host, bootstrap_file, *, port):
            calls.append("provision")
            return dict(self.IDENTITY)

        def fake_save_config(path, config):
            calls.append("save_config")
            self.assertEqual("pending-token", config["token"])

        def fake_confirm(host, port, token):
            calls.append("confirm_bootstrap")
            self.assertEqual("pending-token", token)
            return {"confirmed": True}

        with tempfile.TemporaryDirectory() as directory:
            config_path = pathlib.Path(directory) / "cfg.json"
            with mock.patch(
                "background_video.provision", side_effect=fake_provision
            ), mock.patch(
                "background_video.save_config", side_effect=fake_save_config
            ), mock.patch(
                "background_video.confirm_bootstrap", side_effect=fake_confirm
            ):
                result = run_provision(
                    "127.0.0.1",
                    pathlib.Path("bootstrap.txt"),
                    config_path,
                    port=8787,
                )

        # Ordering guard: credentials must be persisted before confirmation
        # is attempted, so a lost/failed confirm never strands them.
        self.assertEqual(["provision", "save_config", "confirm_bootstrap"], calls)
        self.assertTrue(result["confirmed"])
        self.assertEqual("pending-token", result["token"])

    def test_does_not_save_when_bootstrap_phase_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = pathlib.Path(directory) / "cfg.json"
            with mock.patch(
                "background_video.provision",
                side_effect=BackgroundVideoClientError("Bootstrap secret must be 32-256 chars"),
            ), mock.patch("background_video.save_config") as save, mock.patch(
                "background_video.confirm_bootstrap"
            ) as confirm:
                with self.assertRaises(BackgroundVideoClientError):
                    run_provision(
                        "127.0.0.1",
                        pathlib.Path("bootstrap.txt"),
                        config_path,
                        port=8787,
                    )
            save.assert_not_called()
            confirm.assert_not_called()
            self.assertFalse(config_path.exists())

    def test_host_or_port_mismatch_skips_recovery_and_runs_fresh_bootstrap(self):
        # A saved config for a *different* mirror must never be treated as a
        # recoverable token for this one -- phase-one always runs fresh.
        with tempfile.TemporaryDirectory() as directory:
            config_path = pathlib.Path(directory) / "cfg.json"
            other_mirror = dict(self.IDENTITY)
            other_mirror["host"] = "192.168.1.5"
            config_path.write_text(json.dumps(other_mirror), encoding="utf-8")

            with mock.patch(
                "background_video.provision", return_value=dict(self.IDENTITY)
            ) as provision_mock, mock.patch(
                "background_video.save_config"
            ) as save, mock.patch(
                "background_video.confirm_bootstrap", return_value={"confirmed": True}
            ) as confirm:
                result = run_provision(
                    "127.0.0.1", pathlib.Path("bootstrap.txt"), config_path, port=8787
                )

            provision_mock.assert_called_once()
            save.assert_called_once()
            confirm.assert_called_once_with("127.0.0.1", 8787, "pending-token")
            self.assertTrue(result["confirmed"])

    def test_recovers_and_confirms_saved_token_without_reattempting_bootstrap(self):
        # A previously saved-but-unconfirmed token for this exact mirror must
        # be confirmed directly; phase-one must not run again at all.
        with tempfile.TemporaryDirectory() as directory:
            config_path = pathlib.Path(directory) / "cfg.json"
            config_path.write_text(json.dumps(self.IDENTITY), encoding="utf-8")

            with mock.patch("background_video.provision") as provision_mock, mock.patch(
                "background_video.save_config"
            ) as save, mock.patch(
                "background_video.confirm_bootstrap", return_value={"confirmed": True}
            ) as confirm:
                result = run_provision(
                    "127.0.0.1", pathlib.Path("bootstrap.txt"), config_path, port=8787
                )

            provision_mock.assert_not_called()
            save.assert_not_called()
            confirm.assert_called_once_with("127.0.0.1", 8787, "pending-token")
            self.assertTrue(result["confirmed"])
            self.assertEqual("pending-token", result["token"])

    def test_config_remains_saved_when_confirmation_fails_and_retry_recovers(self):
        # Models the reviewed regression exactly: phase-one succeeds and the
        # config is saved, but the confirmation response is lost (a
        # connection-level failure, so the outcome is ambiguous). A retry
        # must recover by confirming the *saved* token -- not by re-running
        # phase-one, which the server may no longer allow for a consumed
        # secret.
        with tempfile.TemporaryDirectory() as directory:
            bootstrap_file = pathlib.Path(directory) / "bootstrap.txt"
            bootstrap_file.write_text("s" * 48, encoding="utf-8")
            config_path = pathlib.Path(directory) / "cfg.json"

            # First attempt: the bootstrap exchange succeeds but the
            # confirmation response is lost (a transport-level failure).
            with mock.patch(
                "background_video.provision", return_value=dict(self.IDENTITY)
            ) as provision_mock, mock.patch(
                "background_video.confirm_bootstrap",
                side_effect=BackgroundVideoConnectionError(
                    "Unable to reach background-video service"
                ),
            ):
                with self.assertRaises(BackgroundVideoConnectionError):
                    run_provision("127.0.0.1", bootstrap_file, config_path, port=8787)
            provision_mock.assert_called_once()

            # The pending token must remain saved so a retry can recover it.
            self.assertTrue(config_path.exists())
            saved = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual("pending-token", saved["token"])
            self.assertNotIn("confirmed", saved)

            # Retry: the server is idempotent for the already-confirmed (or
            # still-pending) token and returns confirmed=True; phase-one must
            # not run again.
            with mock.patch(
                "background_video.provision"
            ) as provision_mock, mock.patch(
                "background_video.save_config"
            ) as save_mock, mock.patch(
                "background_video.confirm_bootstrap", return_value={"confirmed": True}
            ) as confirm:
                result = run_provision("127.0.0.1", bootstrap_file, config_path, port=8787)

            self.assertTrue(result["confirmed"])
            confirm.assert_called_once_with("127.0.0.1", 8787, "pending-token")
            provision_mock.assert_not_called()
            save_mock.assert_not_called()

    def test_ambiguous_confirmation_failure_preserves_saved_config_and_does_not_retry_bootstrap(
        self,
    ):
        with tempfile.TemporaryDirectory() as directory:
            config_path = pathlib.Path(directory) / "cfg.json"
            config_path.write_text(json.dumps(self.IDENTITY), encoding="utf-8")
            original_bytes = config_path.read_bytes()

            with mock.patch("background_video.provision") as provision_mock, mock.patch(
                "background_video.save_config"
            ) as save, mock.patch(
                "background_video.confirm_bootstrap",
                side_effect=BackgroundVideoConnectionError("reset by peer"),
            ):
                with self.assertRaisesRegex(BackgroundVideoClientError, "left untouched"):
                    run_provision(
                        "127.0.0.1", pathlib.Path("bootstrap.txt"), config_path, port=8787
                    )

            provision_mock.assert_not_called()
            save.assert_not_called()
            self.assertEqual(original_bytes, config_path.read_bytes())

    def test_definitive_rejection_falls_through_to_fresh_bootstrap_when_still_available(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = pathlib.Path(directory) / "cfg.json"
            config_path.write_text(json.dumps(self.IDENTITY), encoding="utf-8")

            new_identity = dict(self.IDENTITY)
            new_identity["token"] = "fresh-token"

            confirm_calls = []

            def fake_confirm(host, port, token):
                confirm_calls.append(token)
                if token == "pending-token":
                    raise BackgroundVideoClientError(
                        "Bootstrap confirmation was not acknowledged by the mirror"
                    )
                return {"confirmed": True}

            with mock.patch(
                "background_video.provision", return_value=new_identity
            ) as provision_mock, mock.patch(
                "background_video.save_config"
            ) as save, mock.patch(
                "background_video.confirm_bootstrap", side_effect=fake_confirm
            ), mock.patch(
                "background_video.bootstrap_available", return_value=True
            ) as availability:
                result = run_provision(
                    "127.0.0.1", pathlib.Path("bootstrap.txt"), config_path, port=8787
                )

            availability.assert_called_once_with("127.0.0.1", 8787)
            provision_mock.assert_called_once()
            save.assert_called_once()
            self.assertEqual(["pending-token", "fresh-token"], confirm_calls)
            self.assertTrue(result["confirmed"])
            self.assertEqual("fresh-token", result["token"])

    def test_definitive_rejection_reraises_when_bootstrap_no_longer_available(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = pathlib.Path(directory) / "cfg.json"
            config_path.write_text(json.dumps(self.IDENTITY), encoding="utf-8")
            original_bytes = config_path.read_bytes()

            with mock.patch("background_video.provision") as provision_mock, mock.patch(
                "background_video.save_config"
            ) as save, mock.patch(
                "background_video.confirm_bootstrap",
                side_effect=BackgroundVideoClientError(
                    "Bootstrap confirmation was not acknowledged by the mirror"
                ),
            ), mock.patch(
                "background_video.bootstrap_available", return_value=False
            ) as availability:
                with self.assertRaisesRegex(BackgroundVideoClientError, "not acknowledged"):
                    run_provision(
                        "127.0.0.1", pathlib.Path("bootstrap.txt"), config_path, port=8787
                    )

            availability.assert_called_once_with("127.0.0.1", 8787)
            provision_mock.assert_not_called()
            save.assert_not_called()
            self.assertEqual(original_bytes, config_path.read_bytes())
            saved_after = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual("pending-token", saved_after["token"])


class UploadStreamingTest(unittest.TestCase):
    def test_upload_streams_exact_content_length_without_reading_whole_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "clip.mp4"
            content = bytes((i % 256 for i in range(CHUNK_SIZE * 2 + 12345)))
            path.write_bytes(content)
            digest = hashlib.sha256(content).hexdigest()
            response_payload = json.dumps(
                {"video": {"id": digest}, "duplicate": False}
            ).encode("utf-8")

            connections = []

            def factory(host, port, timeout=None):
                connection = FakeConnection(host, port, timeout)
                connection.response = FakeResponse(201, response_payload, reason="Created")
                connections.append(connection)
                return connection

            with mock.patch(
                "background_video.http.client.HTTPConnection", side_effect=factory
            ), mock.patch.object(
                pathlib.Path,
                "read_bytes",
                autospec=True,
                side_effect=AssertionError("upload must not read the whole file into memory"),
            ):
                client = BackgroundVideoClient("127.0.0.1", "token123", 8787)
                progress_calls = []
                result = client.upload(
                    path, progress=lambda *values: progress_calls.append(values)
                )

            self.assertEqual(1, len(connections))
            connection = connections[0]
            self.assertEqual(str(len(content)), connection.headers["Content-Length"])
            self.assertEqual("video/mp4", connection.headers["Content-Type"])
            self.assertEqual("Bearer token123", connection.headers["Authorization"])
            self.assertEqual("PUT", connection.method)
            self.assertIn("clip.mp4", connection.path)
            self.assertGreaterEqual(len(connection.sent_chunks), 2)
            for chunk in connection.sent_chunks:
                self.assertLessEqual(len(chunk), CHUNK_SIZE)
            self.assertEqual(content, b"".join(connection.sent_chunks))
            self.assertEqual(digest, result["video"]["id"])
            self.assertTrue(connection.closed)
            self.assertTrue(progress_calls)
            self.assertEqual(100, progress_calls[-1][0])

    def test_upload_rejects_oversized_file_before_opening_connection(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "clip.mp4"
            path.write_bytes(b"x" * 4096)
            with mock.patch("background_video.MAX_VIDEO_BYTES", 1024), mock.patch(
                "background_video.http.client.HTTPConnection"
            ) as connection_class:
                client = BackgroundVideoClient("127.0.0.1", "token123", 8787)
                with self.assertRaisesRegex(BackgroundVideoClientError, "limit"):
                    client.upload(path)
            connection_class.assert_not_called()


class RunPushTest(unittest.TestCase):
    def _video_file(self, directory, content=b"video-bytes"):
        path = pathlib.Path(directory) / "clip.mp4"
        path.write_bytes(content)
        return path, hashlib.sha256(content).hexdigest()

    def test_hash_mismatch_fails_and_does_not_activate(self):
        with tempfile.TemporaryDirectory() as directory:
            path, _digest = self._video_file(directory)
            client = mock.Mock()
            client.upload.return_value = {"video": {"id": "0" * 64}, "duplicate": False}

            with self.assertRaisesRegex(BackgroundVideoClientError, "does not match"):
                run_push(client, path)

            client.activate.assert_not_called()

    def test_activates_by_default_using_the_verified_remote_id(self):
        with tempfile.TemporaryDirectory() as directory:
            path, digest = self._video_file(directory)
            client = mock.Mock()
            client.upload.return_value = {"video": {"id": digest}, "duplicate": False}
            client.activate.return_value = {"activated": True}

            result = run_push(client, path)

            client.activate.assert_called_once_with(digest)
            self.assertEqual({"activated": True}, result["activation"])
            self.assertEqual(digest, result["localSha256"])

    def test_skips_activation_when_requested(self):
        with tempfile.TemporaryDirectory() as directory:
            path, digest = self._video_file(directory)
            client = mock.Mock()
            client.upload.return_value = {"video": {"id": digest}, "duplicate": False}

            result = run_push(client, path, activate=False)

            client.activate.assert_not_called()
            self.assertNotIn("activation", result)


class PerformDeleteTest(unittest.TestCase):
    def test_confirmation_guard_blocks_delete_when_declined(self):
        client = mock.Mock()
        video_id = "a" * 64
        with self.assertRaisesRegex(BackgroundVideoClientError, "cancelled"):
            perform_delete(client, video_id, assume_yes=False, prompt=lambda _message: False)
        client.delete.assert_not_called()

    def test_confirmation_guard_allows_delete_when_accepted(self):
        client = mock.Mock()
        client.delete.return_value = {"deleted": True, "id": "a" * 64}
        video_id = "a" * 64
        result = perform_delete(client, video_id, assume_yes=False, prompt=lambda _message: True)
        client.delete.assert_called_once_with(video_id)
        self.assertTrue(result["deleted"])

    def test_yes_flag_skips_prompt_entirely(self):
        client = mock.Mock()
        client.delete.return_value = {"deleted": True}
        video_id = "b" * 64
        perform_delete(client, video_id, assume_yes=True, prompt=refuse_to_prompt)
        client.delete.assert_called_once_with(video_id)

    def test_malformed_id_rejected_before_any_client_call(self):
        client = mock.Mock()
        with self.assertRaises(BackgroundVideoClientError):
            perform_delete(client, "not-a-valid-id", assume_yes=True)
        client.delete.assert_not_called()


class ScheduleTest(unittest.TestCase):
    FLOWERS = "43c66c53" + "a" * 56
    SEASONS = "b0698aae" + "b" * 56
    SEASONS_TWIN = "b0698aaf" + "c" * 56
    CATALOG = {
        "videos": [
            {"id": FLOWERS, "name": "luminous-flowers-180s.mp4"},
            {"id": SEASONS, "name": "four-seasons-luminous-120s.mp4"},
            {"id": SEASONS_TWIN, "name": "twin.mp4"},
        ],
        "effectiveId": SEASONS,
        "schedule": {
            "enabled": True,
            "slots": [
                {"start": "06:00", "videoId": FLOWERS},
                {"start": "19:00", "videoId": SEASONS},
            ],
        },
    }

    def test_resolves_names_full_ids_and_unique_prefixes(self):
        self.assertEqual(self.FLOWERS, resolve_video_id(self.CATALOG, "luminous-flowers-180s.mp4"))
        self.assertEqual(self.FLOWERS, resolve_video_id(self.CATALOG, self.FLOWERS.upper()))
        self.assertEqual(self.FLOWERS, resolve_video_id(self.CATALOG, "43c66c"))
        for bad, message in (("b0698a", "more than one"), ("ffffff", "no video"),
                             ("43c66", "at least 6"), ("../x", "at least 6")):
            with self.subTest(value=bad):
                with self.assertRaisesRegex(BackgroundVideoClientError, message):
                    resolve_video_id(self.CATALOG, bad)

    def test_slots_need_a_24_hour_time_and_a_video(self):
        self.assertEqual(
            {"start": "19:00", "videoId": self.SEASONS},
            parse_schedule_slot(self.CATALOG, "19:00=b0698aae"),
        )
        for bad in ("7:00=43c66c", "24:00=43c66c", "06:00", "06:00=nope"):
            with self.subTest(value=bad):
                with self.assertRaises(BackgroundVideoClientError):
                    parse_schedule_slot(self.CATALOG, bad)

    def test_client_uses_schedule_routes(self):
        client = BackgroundVideoClient("127.0.0.1", "t" * 40)
        with mock.patch("background_video._json_call", return_value={}) as call:
            slots = [{"start": "06:00", "videoId": self.FLOWERS}]
            client.update_schedule(True, slots)
            client.resume_schedule()
        first, second = call.call_args_list
        self.assertEqual(("PUT", "/api/v1/background-videos/schedule",
                          {"enabled": True, "slots": slots}), first.args[2:5])
        self.assertEqual(("POST", "/api/v1/background-videos/schedule/resume", {}), second.args[2:5])
        self.assertIn("Authorization", first.kwargs["headers"])

    def test_set_resolves_every_slot_before_turning_the_schedule_on(self):
        client = mock.Mock()
        client.list_videos.return_value = self.CATALOG
        client.update_schedule.return_value = self.CATALOG

        result = run_schedule(client, "set", ["06:00=luminous-flowers-180s.mp4", "19:00=b0698aae"])

        client.update_schedule.assert_called_once_with(True, [
            {"start": "06:00", "videoId": self.FLOWERS},
            {"start": "19:00", "videoId": self.SEASONS},
        ])
        self.assertEqual(self.SEASONS, result["effectiveId"])
        self.assertEqual(self.CATALOG["schedule"], result["schedule"])

        client.update_schedule.reset_mock()
        with self.assertRaises(BackgroundVideoClientError):
            run_schedule(client, "set", ["06:00=luminous-flowers-180s.mp4", "19:00=ffffff"])
        client.update_schedule.assert_not_called()

    def test_off_and_on_keep_saved_times_and_resume_ends_a_hold(self):
        client = mock.Mock()
        client.list_videos.return_value = self.CATALOG
        client.update_schedule.return_value = self.CATALOG
        client.resume_schedule.return_value = self.CATALOG

        run_schedule(client, "off")
        client.update_schedule.assert_called_once_with(False, self.CATALOG["schedule"]["slots"])
        run_schedule(client, "resume")
        client.resume_schedule.assert_called_once_with()

        client.list_videos.return_value = {"schedule": {"enabled": False, "slots": []}}
        with self.assertRaisesRegex(BackgroundVideoClientError, "No saved times"):
            run_schedule(client, "on")

    def test_main_schedule_set_prints_the_resulting_schedule(self):
        config = {"host": "127.0.0.1", "port": 8787, "token": "super-secret-token"}
        client = mock.Mock()
        client.list_videos.return_value = self.CATALOG
        client.update_schedule.return_value = self.CATALOG
        buffer = io.StringIO()
        with mock.patch(
            "background_video.client_from_config", return_value=(client, config)
        ), contextlib.redirect_stdout(buffer):
            exit_code = main(["schedule", "set", "06:00=43c66c", "19:00=b0698aae"])

        self.assertEqual(0, exit_code)
        output = json.loads(buffer.getvalue())
        self.assertEqual("19:00", output["schedule"]["slots"][1]["start"])
        self.assertNotIn("super-secret-token", buffer.getvalue())


class MainStatusTest(unittest.TestCase):
    def test_status_output_never_includes_the_token(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = pathlib.Path(directory) / "cfg.json"
            config = {
                "host": "127.0.0.1",
                "port": 8787,
                "token": "super-secret-token",
                "clientId": "abc123",
                "clientName": "Living Room",
            }
            config_path.write_text(json.dumps(config), encoding="utf-8")

            fake_client = mock.Mock()
            fake_client.list_videos.return_value = {"videos": [], "activeId": None}

            buffer = io.StringIO()
            with mock.patch(
                "background_video.client_from_config",
                return_value=(fake_client, config),
            ), contextlib.redirect_stdout(buffer):
                exit_code = main(["--config", str(config_path), "status"])

            self.assertEqual(0, exit_code)
            output = buffer.getvalue()
            self.assertNotIn("super-secret-token", output)
            self.assertIn("clientId", output)
            fake_client.list_videos.assert_called_once()

    def test_list_alias_behaves_like_status(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = pathlib.Path(directory) / "cfg.json"
            config = {"host": "127.0.0.1", "port": 8787, "token": "t" * 40}
            config_path.write_text(json.dumps(config), encoding="utf-8")

            fake_client = mock.Mock()
            fake_client.list_videos.return_value = {"videos": []}

            buffer = io.StringIO()
            with mock.patch(
                "background_video.client_from_config",
                return_value=(fake_client, config),
            ), contextlib.redirect_stdout(buffer):
                exit_code = main(["--config", str(config_path), "list"])

            self.assertEqual(0, exit_code)
            fake_client.list_videos.assert_called_once()

    def test_main_reports_client_errors_without_traceback(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = pathlib.Path(directory) / "missing-cfg.json"
            buffer = io.StringIO()
            with contextlib.redirect_stderr(buffer):
                exit_code = main(["--config", str(config_path), "status"])
            self.assertEqual(1, exit_code)
            self.assertIn("error:", buffer.getvalue())


if __name__ == "__main__":
    unittest.main()
