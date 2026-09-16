#!/usr/bin/env python3
"""Cross-platform CLI for the Mirror background-video API.

Talks directly to a paired Mirror's background-video HTTP endpoints:

    POST   /api/v1/pair                                  (unauthenticated)
    GET    /api/v1/background-videos/bootstrap             (unauthenticated)
    POST   /api/v1/background-videos/bootstrap              (X-Background-Video-Bootstrap secret)
    POST   /api/v1/background-videos/bootstrap/confirm      (Bearer token)
    GET    /api/v1/background-videos                      (Bearer token)
    PUT    /api/v1/background-videos/upload/{filename}     (Bearer token)
    POST   /api/v1/background-videos/{sha256}/activate    (Bearer token)
    POST   /api/v1/background-videos/rollback              (Bearer token)
    DELETE /api/v1/background-videos/{sha256}              (Bearer token)
    PUT    /api/v1/background-videos/schedule              (Bearer token)
    POST   /api/v1/background-videos/schedule/resume       (Bearer token)

Credentials returned by ``pair`` are written to a local JSON file (default
``.secrets/mirror-background-video.json``) with restrictive permissions and
are never printed back out by any other command.
"""

from __future__ import annotations

import argparse
import hashlib
import http.client
import ipaddress
import json
import os
import pathlib
import re
import socket
import subprocess
import sys
import urllib.parse


REPO = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = REPO / ".secrets" / "mirror-background-video.json"
DEFAULT_BOOTSTRAP_FILE = REPO / ".secrets" / "mirror-background-video-bootstrap.txt"
DEFAULT_PORT = 8787
MAX_VIDEO_BYTES = 256 * 1024 * 1024
CHUNK_SIZE = 1024 * 1024
JSON_TIMEOUT_SECONDS = 30
PAIR_TIMEOUT_SECONDS = 30
BOOTSTRAP_HEADER = "X-Background-Video-Bootstrap"
MIN_BOOTSTRAP_SECRET_LENGTH = 32
MAX_BOOTSTRAP_SECRET_LENGTH = 256
# Per-socket-operation timeout (not a total-duration timeout); large uploads
# stay within this because each 1 MiB chunk send/recv resets the clock.
UPLOAD_TIMEOUT_SECONDS = 180
ID_PATTERN = re.compile(r"^[0-9a-f]{64}$")
SCHEDULE_TIME_PATTERN = re.compile(r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")


class BackgroundVideoClientError(RuntimeError):
    pass


class BackgroundVideoConnectionError(BackgroundVideoClientError):
    """A request failed at the transport level (timeout, reset, DNS, refused,
    ...), so whether the server actually processed it is unknown.

    Distinguished from the base class -- which also covers *definitive*
    server responses such as HTTP error statuses or an explicit "not
    acknowledged" body -- so callers can tell an ambiguous outcome apart from
    one the server has clearly answered, and avoid retrying non-idempotent
    actions (like re-running phase-one bootstrap) when the outcome is
    unknown.
    """


def validate_host(host: str) -> ipaddress.IPv4Address:
    if not host:
        raise BackgroundVideoClientError("Host is required")
    try:
        address = ipaddress.ip_address(host)
    except ValueError as error:
        raise BackgroundVideoClientError(f"Invalid host address: {host}") from error
    if address.version != 4 or not (address.is_private or address.is_loopback):
        raise BackgroundVideoClientError(
            f"Host must be a private or loopback IPv4 address: {host}"
        )
    return address


def normalize_id(value: str) -> str:
    candidate = (value or "").strip().lower()
    if not ID_PATTERN.fullmatch(candidate):
        raise BackgroundVideoClientError(
            f"Invalid background-video id (expected 64 lowercase hex characters): {value!r}"
        )
    return candidate


def validate_video_file(path: pathlib.Path) -> int:
    if not path.is_file():
        raise BackgroundVideoClientError(f"File does not exist: {path}")
    if path.suffix.lower() != ".mp4":
        raise BackgroundVideoClientError(f"Only .mp4 files are supported: {path}")
    size = path.stat().st_size
    if size <= 0:
        raise BackgroundVideoClientError(f"File is empty: {path}")
    if size > MAX_VIDEO_BYTES:
        raise BackgroundVideoClientError(
            f"File exceeds the {MAX_VIDEO_BYTES // (1024 * 1024)} MiB limit: {path}"
        )
    return size


def sha256_file(path: pathlib.Path) -> str:
    """Stream the file through SHA-256 without ever holding it all in RAM."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(CHUNK_SIZE)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _decode_response(response: http.client.HTTPResponse, data: bytes) -> dict:
    text = data.decode("utf-8", errors="replace") if data else ""
    if response.status >= 400:
        message = text
        if text:
            try:
                parsed = json.loads(text)
                if isinstance(parsed, dict) and "error" in parsed:
                    message = parsed["error"]
            except json.JSONDecodeError:
                pass
        raise BackgroundVideoClientError(
            f"Request failed ({response.status} {response.reason}): {message or response.reason}"
        )
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as error:
        raise BackgroundVideoClientError("Service returned invalid JSON") from error
    if not isinstance(parsed, dict):
        raise BackgroundVideoClientError("Service returned an unexpected JSON payload")
    return parsed


def _json_call(
    host: str,
    port: int,
    method: str,
    path: str,
    payload: dict | None = None,
    *,
    headers: dict[str, str] | None = None,
    timeout: int = JSON_TIMEOUT_SECONDS,
) -> dict:
    body = None
    request_headers = {"Accept": "application/json"}
    if headers:
        request_headers.update(headers)
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    connection = http.client.HTTPConnection(host, port, timeout=timeout)
    try:
        connection.request(method, path, body=body, headers=request_headers)
        response = connection.getresponse()
        data = response.read()
    except (http.client.HTTPException, OSError) as error:
        # A transport-level failure -- unlike an HTTP error response -- means
        # we don't know whether the server processed the request.
        raise BackgroundVideoConnectionError(
            f"Unable to reach background-video service at {host}:{port}: {error}"
        ) from error
    finally:
        connection.close()
    return _decode_response(response, data)


def pair(
    host: str,
    code: str,
    name: str,
    *,
    port: int = DEFAULT_PORT,
    time_zone: str | None = None,
    utc_offset_minutes: int | None = None,
    timeout: int = PAIR_TIMEOUT_SECONDS,
) -> dict:
    address = validate_host(host)
    if not code:
        raise BackgroundVideoClientError("Pairing code is required")
    payload: dict = {"code": code, "name": name or socket.gethostname()}
    if time_zone:
        payload["timeZone"] = time_zone
    if utc_offset_minutes is not None:
        payload["utcOffsetMinutes"] = utc_offset_minutes
    response = _json_call(str(address), port, "POST", "/api/v1/pair", payload, timeout=timeout)
    token = response.get("token")
    if not isinstance(token, str) or not token:
        raise BackgroundVideoClientError("Pairing response did not include a token")
    return {
        "host": str(address),
        "port": port,
        "token": token,
        "clientId": response.get("clientId"),
        "clientName": response.get("clientName"),
    }


def read_bootstrap_secret(path: pathlib.Path) -> str:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as error:
        raise BackgroundVideoClientError(
            f"Unable to read bootstrap secret file: {path}"
        ) from error
    secret = raw.strip()
    if not (MIN_BOOTSTRAP_SECRET_LENGTH <= len(secret) <= MAX_BOOTSTRAP_SECRET_LENGTH):
        raise BackgroundVideoClientError(
            f"Bootstrap secret must be {MIN_BOOTSTRAP_SECRET_LENGTH}-"
            f"{MAX_BOOTSTRAP_SECRET_LENGTH} characters: {path}"
        )
    return secret


def provision(
    host: str,
    bootstrap_file: pathlib.Path,
    *,
    port: int = DEFAULT_PORT,
    timeout: int = PAIR_TIMEOUT_SECONDS,
) -> dict:
    """Phase 1 of the unattended bootstrap: exchange a deployment secret for
    a pending client token, identical in shape to what ``pair`` produces.

    The server treats this call as idempotent for a given secret -- calling
    it again before confirmation returns the same pending token, which lets
    ``run_provision`` recover cleanly after a lost response or a failed
    confirmation.
    """
    address = validate_host(host)
    secret = read_bootstrap_secret(bootstrap_file)
    response = _json_call(
        str(address),
        port,
        "POST",
        "/api/v1/background-videos/bootstrap",
        {},
        headers={BOOTSTRAP_HEADER: secret},
        timeout=timeout,
    )
    token = response.get("token")
    if not isinstance(token, str) or not token:
        raise BackgroundVideoClientError("Bootstrap response did not include a token")
    return {
        "host": str(address),
        "port": port,
        "token": token,
        "clientId": response.get("clientId"),
        "clientName": response.get("clientName"),
    }


def confirm_bootstrap(
    host: str,
    port: int,
    token: str,
    *,
    timeout: int = PAIR_TIMEOUT_SECONDS,
) -> dict:
    """Phase 2 of the unattended bootstrap: acknowledge the pending token so
    the server retires it from the recoverable/idempotent bootstrap window."""
    address = validate_host(host)
    if not token:
        raise BackgroundVideoClientError("A token is required to confirm bootstrap provisioning")
    response = _json_call(
        str(address),
        port,
        "POST",
        "/api/v1/background-videos/bootstrap/confirm",
        {},
        headers={"Authorization": f"Bearer {token}"},
        timeout=timeout,
    )
    if response.get("confirmed") is not True:
        raise BackgroundVideoClientError(
            "Bootstrap confirmation was not acknowledged by the mirror"
        )
    return response


def bootstrap_available(
    host: str, port: int = DEFAULT_PORT, *, timeout: int = PAIR_TIMEOUT_SECONDS
) -> bool:
    """Query whether the mirror's unattended bootstrap is still open.

    Used by ``run_provision`` to decide whether it is safe to fall back to a
    fresh phase-one bootstrap exchange after a saved token is *definitively*
    rejected during confirmation. A connection failure here is deliberately
    left to propagate rather than being treated as "unavailable" -- if we
    can't even ask, it isn't safe to guess.
    """
    address = validate_host(host)
    response = _json_call(
        str(address), port, "GET", "/api/v1/background-videos/bootstrap", timeout=timeout
    )
    return bool(response.get("available"))


def _load_recoverable_config(config_path: pathlib.Path, host: str, port: int) -> dict | None:
    """Return a previously saved config if it already targets *host*/*port*
    and still carries a token, else ``None``.

    This lets ``run_provision`` recognise "we already ran phase-one and saved
    credentials for this exact mirror, but never confirmed" and attempt
    recovery via ``confirm_bootstrap`` before considering a brand-new
    phase-one exchange (which the server may reject outright once a secret
    has been consumed).
    """
    try:
        config = load_config(config_path)
    except BackgroundVideoClientError:
        return None
    if config.get("host") != host:
        return None
    try:
        existing_port = int(config.get("port", DEFAULT_PORT))
    except (TypeError, ValueError):
        return None
    if existing_port != port:
        return None
    token = config.get("token")
    if not isinstance(token, str) or not token:
        return None
    return config


def run_provision(
    host: str,
    bootstrap_file: pathlib.Path,
    config_path: pathlib.Path,
    *,
    port: int = DEFAULT_PORT,
) -> dict:
    """Fetch (or recover) a pending bootstrap token, persist it immediately,
    then confirm it.

    Recovery-first: if ``config_path`` already holds a saved token for this
    exact host/port (from an earlier, interrupted run), that token is
    confirmed *before* ever attempting a new phase-one bootstrap exchange.
    This matters because phase-one is not safely repeatable once a secret has
    already been fully consumed server-side -- only the as-yet-unconfirmed
    window is idempotent. Three outcomes are handled explicitly:

    * The saved token confirms successfully -- return immediately without
      touching the bootstrap secret at all.
    * Confirmation is refused *definitively* (a real response was received,
      e.g. an auth rejection or an explicit "not acknowledged") -- phase-one
      may only be retried if the mirror reports its bootstrap window is
      still open (``bootstrap_available``); otherwise the original error is
      re-raised untouched.
    * Confirmation fails ambiguously (connection reset, timeout, ...) -- we
      cannot tell whether the server actually confirmed it. The saved config
      is left exactly as-is and a clear, non-retrying error is raised: retry
      ``provision`` again once connectivity is restored, which will replay
      this same recovery path against the same saved token.

    Only when there is no recoverable saved token does this fall through to
    a fresh phase-one exchange: credentials are saved to ``config_path`` as
    soon as that bootstrap exchange succeeds and *before* confirmation is
    attempted, so a lost/failed confirmation there is, in turn, recoverable
    by a subsequent run through this same recovery path.
    """
    address = str(validate_host(host))
    existing = _load_recoverable_config(config_path, address, port)
    if existing is not None:
        token = existing["token"]
        try:
            confirmation = confirm_bootstrap(address, port, token)
        except BackgroundVideoConnectionError as error:
            raise BackgroundVideoClientError(
                "Could not reach the mirror to confirm the previously saved "
                "bootstrap token; the saved credentials were left untouched. "
                "Retry 'provision' once the mirror is reachable again."
            ) from error
        except BackgroundVideoClientError:
            if not bootstrap_available(address, port):
                raise
            # The mirror still has bootstrap open, so the saved token was
            # never actually confirmed server-side (or has expired): fall
            # through to a fresh phase-one exchange below.
        else:
            result = dict(existing)
            result["confirmed"] = confirmation.get("confirmed", False)
            return result

    identity = provision(host, bootstrap_file, port=port)
    save_config(config_path, identity)
    confirmation = confirm_bootstrap(identity["host"], identity["port"], identity["token"])
    result = dict(identity)
    result["confirmed"] = confirmation.get("confirmed", False)
    return result


def default_progress(percent: int, sent: int, total: int) -> None:
    sys.stderr.write(f"\rUploading: {percent:3d}% ({sent}/{total} bytes)")
    sys.stderr.flush()
    if percent >= 100:
        sys.stderr.write("\n")
        sys.stderr.flush()


class BackgroundVideoClient:
    def __init__(self, host: str, token: str, port: int = DEFAULT_PORT):
        address = validate_host(host)
        if not token:
            raise BackgroundVideoClientError("Background-video token is missing")
        self.host = str(address)
        self.port = port
        self.token = token

    def _auth_headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self.token}"}
        if extra:
            headers.update(extra)
        return headers

    def list_videos(self) -> dict:
        return _json_call(
            self.host,
            self.port,
            "GET",
            "/api/v1/background-videos",
            headers=self._auth_headers(),
        )

    def activate(self, video_id: str) -> dict:
        normalized = normalize_id(video_id)
        return _json_call(
            self.host,
            self.port,
            "POST",
            f"/api/v1/background-videos/{normalized}/activate",
            {},
            headers=self._auth_headers(),
        )

    def rollback(self) -> dict:
        return _json_call(
            self.host,
            self.port,
            "POST",
            "/api/v1/background-videos/rollback",
            {},
            headers=self._auth_headers(),
        )

    def update_schedule(self, enabled: bool, slots: list[dict]) -> dict:
        return _json_call(
            self.host,
            self.port,
            "PUT",
            "/api/v1/background-videos/schedule",
            {"enabled": enabled, "slots": slots},
            headers=self._auth_headers(),
        )

    def resume_schedule(self) -> dict:
        return _json_call(
            self.host,
            self.port,
            "POST",
            "/api/v1/background-videos/schedule/resume",
            {},
            headers=self._auth_headers(),
        )

    def delete(self, video_id: str) -> dict:
        normalized = normalize_id(video_id)
        return _json_call(
            self.host,
            self.port,
            "DELETE",
            f"/api/v1/background-videos/{normalized}",
            headers=self._auth_headers(),
        )

    def upload(self, path: pathlib.Path, *, progress=None) -> dict:
        size = validate_video_file(path)
        encoded_name = urllib.parse.quote(path.name, safe="")
        headers = self._auth_headers(
            {
                "Content-Type": "video/mp4",
                "Content-Length": str(size),
            }
        )
        connection = http.client.HTTPConnection(
            self.host, self.port, timeout=UPLOAD_TIMEOUT_SECONDS
        )
        try:
            connection.putrequest("PUT", f"/api/v1/background-videos/upload/{encoded_name}")
            for key, value in headers.items():
                connection.putheader(key, value)
            connection.endheaders()
            sent = 0
            last_percent = -1
            with path.open("rb") as handle:
                while True:
                    chunk = handle.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    connection.send(chunk)
                    sent += len(chunk)
                    percent = min(100, int(sent * 100 / size))
                    if progress is not None and percent != last_percent:
                        progress(percent, sent, size)
                        last_percent = percent
            response = connection.getresponse()
            data = response.read()
        except (http.client.HTTPException, OSError) as error:
            raise BackgroundVideoClientError(f"Upload failed: {error}") from error
        finally:
            connection.close()
        return _decode_response(response, data)


def run_push(
    client: BackgroundVideoClient,
    path: pathlib.Path,
    *,
    activate: bool = True,
    progress=None,
) -> dict:
    validate_video_file(path)
    local_hash = sha256_file(path)
    result = client.upload(path, progress=progress)
    video = result.get("video") if isinstance(result, dict) else None
    remote_id = video.get("id") if isinstance(video, dict) else None
    if remote_id != local_hash:
        raise BackgroundVideoClientError(
            f"Uploaded video id {remote_id!r} does not match local SHA-256 {local_hash!r}"
        )
    result["localSha256"] = local_hash
    if activate:
        result["activation"] = client.activate(remote_id)
    return result


def confirm(prompt: str) -> bool:
    try:
        answer = input(f"{prompt} [y/N]: ")
    except EOFError:
        return False
    return answer.strip().lower() in {"y", "yes"}


def resolve_video_id(catalog: dict, value: str) -> str:
    """Accept a full id, a unique id prefix of at least 6 characters, or an exact file name."""
    candidate = value.strip()
    videos = catalog.get("videos") or []
    named = [video.get("id") for video in videos if video.get("name") == candidate]
    if len(named) == 1:
        return named[0]
    lowered = candidate.lower()
    if not re.fullmatch(r"[0-9a-f]{6,64}", lowered):
        raise BackgroundVideoClientError(
            f"{value!r} is not a video name or an id prefix of at least 6 hex characters"
        )
    matches = [video.get("id") for video in videos if str(video.get("id", "")).startswith(lowered)]
    if len(matches) != 1:
        problem = "matches no video" if not matches else "matches more than one video"
        raise BackgroundVideoClientError(f"{value!r} {problem}")
    return matches[0]


def parse_schedule_slot(catalog: dict, value: str) -> dict:
    start, separator, video = value.partition("=")
    if not separator or not SCHEDULE_TIME_PATTERN.fullmatch(start):
        raise BackgroundVideoClientError(
            f"{value!r} must look like HH:MM=VIDEO using a 24-hour time"
        )
    return {"start": start, "videoId": resolve_video_id(catalog, video)}


def run_schedule(client: BackgroundVideoClient, action: str, slot_values=()) -> dict:
    if action == "resume":
        catalog = client.resume_schedule()
    elif action == "set":
        catalog = client.list_videos()
        slots = [parse_schedule_slot(catalog, value) for value in slot_values]
        catalog = client.update_schedule(True, slots)
    elif action in ("on", "off"):
        current = client.list_videos().get("schedule") or {}
        slots = [
            {"start": slot.get("start"), "videoId": slot.get("videoId")}
            for slot in current.get("slots") or []
        ]
        if action == "on" and not slots:
            raise BackgroundVideoClientError("No saved times; use 'schedule set' first")
        catalog = client.update_schedule(action == "on", slots)
    elif action == "show":
        catalog = client.list_videos()
    else:
        raise AssertionError(f"Unhandled schedule action: {action}")
    return {"schedule": catalog.get("schedule"), "effectiveId": catalog.get("effectiveId")}


def perform_delete(
    client: BackgroundVideoClient,
    video_id: str,
    *,
    assume_yes: bool,
    prompt=confirm,
) -> dict:
    normalized = normalize_id(video_id)
    if not assume_yes and not prompt(f"Delete background video {normalized}?"):
        raise BackgroundVideoClientError("Deletion cancelled; pass --yes to skip confirmation")
    return client.delete(normalized)


def _restrict_permissions_windows(
    path: pathlib.Path, *, inherit_children: bool = False
) -> None:
    """Apply a current-user-only DACL to *path* via ``icacls``.

    Any failure to invoke or run ``icacls`` is surfaced as an explicit error
    rather than silently leaving the file with default (often inheritable,
    broader-than-intended) permissions.
    """
    username = os.environ.get("USERNAME")
    if not username:
        raise BackgroundVideoClientError(
            "Unable to determine the current Windows user to restrict file permissions"
        )
    domain = os.environ.get("USERDOMAIN")
    account = f"{domain}\\{username}" if domain else username
    grant = f"{account}:{'(OI)(CI)' if inherit_children else ''}F"
    try:
        result = subprocess.run(
            ["icacls", str(path), "/inheritance:r", "/grant:r", grant],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as error:
        raise BackgroundVideoClientError(
            f"Unable to invoke icacls to restrict permissions on {path}: {error}"
        ) from error
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise BackgroundVideoClientError(
            f"icacls failed to restrict permissions on {path}: {detail}"
        )


def write_secret_file(path: pathlib.Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary_directory = None
    # Never build on top of a stale temp file (and its permissions/content)
    # left behind by a prior failed run.
    temporary.unlink(missing_ok=True)
    try:
        if os.name == "nt":
            temporary_directory = path.parent / (
                "." + path.name + "." + os.urandom(12).hex() + ".tmp"
            )
            temporary_directory.mkdir(exist_ok=False)
            _restrict_permissions_windows(
                temporary_directory, inherit_children=True
            )
            temporary = temporary_directory / path.name
            temporary.touch(mode=0o600, exist_ok=False)
            _restrict_permissions_windows(temporary)
            temporary.write_text(content, encoding="utf-8")
        else:
            # Pass the restrictive mode to the creating syscall itself so
            # the file is never briefly world/group-readable between
            # "write" and a later chmod.
            file_descriptor = os.open(
                str(temporary), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
            )
            with os.fdopen(file_descriptor, "w", encoding="utf-8") as handle:
                handle.write(content)
        temporary.replace(path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    finally:
        if temporary_directory is not None and temporary_directory.exists():
            temporary_directory.rmdir()


def save_config(path: pathlib.Path, config: dict) -> None:
    write_secret_file(path, json.dumps(config, indent=2) + "\n")


def load_config(path: pathlib.Path) -> dict:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as error:
        raise BackgroundVideoClientError(
            f"Unable to read config file: {path} (run 'pair' first)"
        ) from error
    try:
        config = json.loads(raw)
    except json.JSONDecodeError as error:
        raise BackgroundVideoClientError(f"Config file is not valid JSON: {path}") from error
    if not isinstance(config, dict):
        raise BackgroundVideoClientError(f"Config file must contain a JSON object: {path}")
    return config


def redact_config(config: dict) -> dict:
    return {key: value for key, value in config.items() if key != "token"}


def client_from_config(
    path: pathlib.Path,
    host_override: str | None = None,
    port_override: int | None = None,
) -> tuple[BackgroundVideoClient, dict]:
    config = load_config(path)
    host = host_override or config.get("host")
    if not isinstance(host, str) or not host:
        raise BackgroundVideoClientError("Config is missing a host; pass --host or run 'pair'")
    token = config.get("token")
    if not isinstance(token, str) or not token:
        raise BackgroundVideoClientError("Config is missing a token; run 'pair' first")
    port = port_override if port_override is not None else int(config.get("port", DEFAULT_PORT))
    return BackgroundVideoClient(host, token, port), config


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="background_video.py",
        description="CLI for the Mirror background-video API",
    )
    parser.add_argument(
        "--config",
        type=pathlib.Path,
        default=DEFAULT_CONFIG_PATH,
        help=f"Path to the credential file (default: {DEFAULT_CONFIG_PATH})",
    )
    parser.add_argument("--host", help="Override the background-video host (required for 'pair')")
    parser.add_argument(
        "--port",
        type=int,
        help=f"Override the background-video port (default: {DEFAULT_PORT})",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    pair_parser = subparsers.add_parser("pair", help="Pair with a mirror using a pairing code")
    pair_parser.add_argument("--code", required=True, help="Pairing code shown on the mirror")
    pair_parser.add_argument("--name", default=socket.gethostname(), help="Friendly client name")
    pair_parser.add_argument("--time-zone", dest="time_zone", help="IANA time zone, e.g. America/Los_Angeles")
    pair_parser.add_argument(
        "--utc-offset-minutes", dest="utc_offset_minutes", type=int, help="UTC offset in minutes"
    )

    provision_parser = subparsers.add_parser(
        "provision", help="One-time unattended bootstrap using a deployment secret"
    )
    provision_parser.add_argument(
        "--bootstrap-file",
        type=pathlib.Path,
        default=DEFAULT_BOOTSTRAP_FILE,
        help=f"Path to the bootstrap secret file (default: {DEFAULT_BOOTSTRAP_FILE})",
    )

    subparsers.add_parser("status", aliases=["list"], help="Show pairing status and catalog")

    push_parser = subparsers.add_parser("push", help="Upload an MP4 and activate it")
    push_parser.add_argument("path", type=pathlib.Path)
    push_parser.add_argument(
        "--no-activate", action="store_true", help="Upload without activating the video"
    )

    activate_parser = subparsers.add_parser("activate", help="Activate an already uploaded video")
    activate_parser.add_argument("id")

    subparsers.add_parser("rollback", help="Roll back to the previous background video")

    delete_parser = subparsers.add_parser("delete", help="Delete a background video")
    delete_parser.add_argument("id")
    delete_parser.add_argument("--yes", action="store_true", help="Skip the confirmation prompt")

    schedule_parser = subparsers.add_parser(
        "schedule", help="Show or change the time-of-day video schedule"
    )
    schedule_actions = schedule_parser.add_subparsers(dest="schedule_action", required=True)
    schedule_actions.add_parser("show", help="Show the schedule and what the mirror shows now")
    set_parser = schedule_actions.add_parser(
        "set", help="Replace the schedule and turn it on, e.g. 06:00=VIDEO 19:00=VIDEO"
    )
    set_parser.add_argument(
        "slots",
        nargs="+",
        metavar="HH:MM=VIDEO",
        help="24-hour start time and a video name, full id, or unique id prefix",
    )
    schedule_actions.add_parser("on", help="Turn the saved schedule on")
    schedule_actions.add_parser("off", help="Turn the schedule off but keep its times")
    schedule_actions.add_parser("resume", help="End a manual choice and follow the schedule")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "pair":
            if not args.host:
                raise BackgroundVideoClientError("--host is required for 'pair'")
            paired = pair(
                args.host,
                args.code,
                args.name,
                port=args.port or DEFAULT_PORT,
                time_zone=args.time_zone,
                utc_offset_minutes=args.utc_offset_minutes,
            )
            save_config(args.config, paired)
            display = redact_config(paired)
            display["configFile"] = str(args.config)
            print(json.dumps(display, indent=2))
            return 0

        if args.command == "provision":
            if not args.host:
                raise BackgroundVideoClientError("--host is required for 'provision'")
            provisioned = run_provision(
                args.host,
                args.bootstrap_file,
                args.config,
                port=args.port or DEFAULT_PORT,
            )
            display = redact_config(provisioned)
            display["configFile"] = str(args.config)
            print(json.dumps(display, indent=2))
            return 0

        client, config = client_from_config(args.config, args.host, args.port)
        if args.command in ("status", "list"):
            result = {"config": redact_config(config), "catalog": client.list_videos()}
        elif args.command == "push":
            result = run_push(
                client,
                args.path,
                activate=not args.no_activate,
                progress=default_progress,
            )
        elif args.command == "activate":
            result = client.activate(args.id)
        elif args.command == "rollback":
            result = client.rollback()
        elif args.command == "delete":
            result = perform_delete(client, args.id, assume_yes=args.yes)
        elif args.command == "schedule":
            result = run_schedule(client, args.schedule_action, getattr(args, "slots", ()))
        else:
            raise AssertionError(f"Unhandled command: {args.command}")
    except BackgroundVideoClientError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
