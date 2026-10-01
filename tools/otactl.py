#!/usr/bin/env python3
"""Provision and operate the authenticated Mirror OTA supervisor."""

from __future__ import annotations

import argparse
import datetime
import hashlib
import hmac
import http.client
import ipaddress
import json
import os
import pathlib
import re
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request

from mirrorctl import Device, free_tcp_port


REPO = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_TOKEN_FILE = REPO / ".secrets" / "mirror-ota.json"
DEFAULT_BOOTSTRAP_FILE = REPO / ".secrets" / "mirror-ota-bootstrap.txt"
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
TERMINAL_STATES = {
    "succeeded",
    "rolled_back",
    "failed",
    "recovery_required",
}


class OtaClientError(RuntimeError):
    pass


class OtaUnreachableError(OtaClientError):
    """The supervisor did not answer; it may be busy, restarting or gone."""


def canonical_request(
    method: str,
    path: str,
    counter: str,
    nonce: str,
    body_sha256: str,
) -> str:
    return "\n".join(
        (
            method.upper(),
            path,
            counter,
            nonce.lower(),
            body_sha256.lower(),
        )
    )


def signed_headers(
    token: str,
    method: str,
    path: str,
    body_sha256: str,
) -> dict[str, str]:
    # Windows wall-clock resolution can be much coarser than one nanosecond.
    counter = str(time.time_ns() + secrets.randbits(30))
    nonce = secrets.token_hex(16)
    canonical = canonical_request(method, path, counter, nonce, body_sha256)
    signature = hmac.new(
        token.encode("utf-8"),
        canonical.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return {
        "Authorization": f"MirrorOTA {signature}",
        "X-Ota-Counter": counter,
        "X-Ota-Nonce": nonce,
        "X-Ota-Content-Sha256": body_sha256,
        "Accept": "application/json",
    }


class OtaClient:
    def __init__(self, host: str, token: str, port: int = 8791):
        address = ipaddress.ip_address(host)
        if address.version != 4 or not (address.is_private or address.is_loopback):
            raise OtaClientError("OTA host must be a private or loopback IPv4 address")
        if not token:
            raise OtaClientError("OTA token is missing")
        self.base_url = f"http://{address}:{port}"
        self.token = token
        # Called with a line of text whenever a transaction moves on.
        self.progress = None

    def request(
        self,
        method: str,
        path: str,
        body: bytes = b"",
        *,
        content_type: str | None = None,
        extra_headers: dict[str, str] | None = None,
        timeout: int = 30,
    ) -> dict:
        body_hash = hashlib.sha256(body).hexdigest()
        headers = signed_headers(self.token, method, path, body_hash)
        if content_type:
            headers["Content-Type"] = content_type
        if extra_headers:
            headers.update(extra_headers)
        request = urllib.request.Request(
            self.base_url + path,
            data=body if method.upper() in {"POST", "PUT"} else None,
            headers=headers,
            method=method.upper(),
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = response.read()
        except urllib.error.HTTPError as error:
            payload = error.read()
            try:
                message = json.loads(payload.decode("utf-8")).get("error", "")
            except (UnicodeDecodeError, json.JSONDecodeError):
                message = payload.decode("utf-8", errors="replace")
            raise OtaClientError(
                f"OTA request failed ({error.code}): {message or error.reason}"
            ) from error
        except urllib.error.URLError as error:
            raise OtaUnreachableError(
                f"Unable to reach OTA supervisor: {error.reason}"
            ) from error
        except (OSError, http.client.HTTPException) as error:
            # A reset or a reply cut short: the supervisor went away mid-answer.
            raise OtaUnreachableError(
                f"Lost the connection to the OTA supervisor: {error or type(error).__name__}"
            ) from error
        try:
            return json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise OtaClientError("OTA supervisor returned invalid JSON") from error

    def status(self) -> dict:
        return self.request("GET", "/api/v1/status")

    def push(self, apk: pathlib.Path) -> dict:
        body = apk.read_bytes()
        response = self.request(
            "PUT",
            "/api/v1/update",
            body,
            content_type="application/vnd.android.package-archive",
            timeout=90,
        )
        result = self.wait(response.get("transactionId"))
        if result.get("state") != "succeeded":
            raise OtaClientError(
                f"OTA update ended in {result.get('state')}: {result.get('message', '')}"
            )
        return result

    def rollback(self) -> dict:
        response = self.request("POST", "/api/v1/rollback")
        result = self.wait(response.get("transactionId"))
        if result.get("state") != "rolled_back":
            raise OtaClientError(
                f"OTA rollback ended in {result.get('state')}: {result.get('message', '')}"
            )
        return result

    def wait(self, transaction_id: str | None, timeout_seconds: int = 240) -> dict:
        """Poll until the transaction ends, and return how it ended.

        Android can stop the supervisor's process while an update is being
        installed. The supervisor restarts and carries on from its saved
        state, so losing the connection for a while is not a failure.
        """
        deadline = time.monotonic() + timeout_seconds
        unreachable: OtaUnreachableError | None = None
        reported = None
        while time.monotonic() < deadline:
            try:
                status = self.status()
            except OtaUnreachableError as error:
                if unreachable is None:
                    self._report(f"The supervisor is not answering; still waiting ({error})")
                unreachable = error
                time.sleep(2)
                continue
            if unreachable is not None:
                self._report("The supervisor is answering again")
                unreachable = None
            if transaction_id and status.get("transactionId") != transaction_id:
                raise OtaClientError("OTA transaction identifier changed unexpectedly")
            if not status.get("active") and status.get("state") in TERMINAL_STATES:
                return status
            step = (status.get("state"), status.get("message"))
            if step != reported:
                reported = step
                self._report(f"{step[0]}: {step[1]}" if step[1] else str(step[0]))
            time.sleep(2)
        if unreachable is not None:
            raise OtaClientError(
                "Lost contact with the OTA supervisor before the transaction ended "
                f"({unreachable}). Run the status command to see how it ended; "
                "do not push again until it answers."
            )
        raise OtaClientError("Timed out waiting for OTA transaction")

    def _report(self, text: str) -> None:
        if self.progress is not None:
            self.progress(text)


def raw_request(
    base_url: str,
    method: str,
    path: str,
    body: bytes = b"",
    headers: dict[str, str] | None = None,
) -> dict:
    request = urllib.request.Request(
        base_url + path,
        data=body if method.upper() in {"POST", "PUT"} else None,
        headers={"Accept": "application/json", **(headers or {})},
        method=method.upper(),
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise OtaClientError(f"OTA provisioning failed ({error.code}): {detail}") from error
    except urllib.error.URLError as error:
        raise OtaClientError(f"Unable to reach OTA supervisor: {error.reason}") from error


def wifi_address(device: Device) -> str:
    output = device.shell("ip", "-4", "addr", "show", "wlan0")
    match = re.search(r"\binet\s+(\d+\.\d+\.\d+\.\d+)/", output)
    if not match:
        raise OtaClientError("Mirror has no Wi-Fi IPv4 address")
    return match.group(1)


def write_secret_file(path: pathlib.Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    if os.name != "nt":
        os.chmod(temporary, 0o600)
    temporary.replace(path)


def bootstrap_token(path: pathlib.Path) -> str:
    if path.is_file():
        token = path.read_text(encoding="utf-8").strip()
        if len(token) < 40:
            raise OtaClientError("OTA bootstrap token is invalid")
        return token
    token = secrets.token_urlsafe(32)
    write_secret_file(path, token + "\n")
    return token


def build_supervisor(bootstrap_file: pathlib.Path) -> dict:
    token = bootstrap_token(bootstrap_file)
    token_sha256 = hashlib.sha256(token.encode("utf-8")).hexdigest()
    gradle = REPO / ("gradlew.bat" if os.name == "nt" else "gradlew")
    try:
        subprocess.run(
            [
                str(gradle),
                ":android:ota-updater:assembleRelease",
                f"-PmirrorOtaBootstrapTokenSha256={token_sha256}",
                "--no-daemon",
            ],
            cwd=REPO,
            check=True,
        )
    except subprocess.CalledProcessError as error:
        raise OtaClientError("OTA supervisor release build failed") from error
    apk = (
        REPO
        / "android"
        / "ota-updater"
        / "build"
        / "outputs"
        / "apk"
        / "release"
        / "ota-updater-release.apk"
    )
    if not apk.is_file():
        raise OtaClientError("OTA supervisor release APK was not produced")
    return {
        "apk": str(apk),
        "bootstrapTokenFile": str(bootstrap_file),
        "bootstrapTokenSha256": token_sha256,
    }


def provision(
    serial: str | None,
    token_file: pathlib.Path,
    bootstrap_file: pathlib.Path,
    host: str | None,
    *,
    recover: bool = False,
) -> dict:
    device = Device(serial)
    resolved_host = host or wifi_address(device)
    bootstrap_secret = bootstrap_token(bootstrap_file)
    local_port = free_tcp_port()
    local = f"tcp:{local_port}"
    device.command("forward", local, "tcp:8791")
    try:
        base_url = f"http://127.0.0.1:{local_port}"
        bootstrap_status = raw_request(base_url, "GET", "/api/v1/bootstrap")
        if not bootstrap_status.get("deviceOwner"):
            raise OtaClientError("OTA supervisor is not the Android device owner")
        provisioned = raw_request(
            base_url,
            "POST",
            "/api/v1/provision/recover" if recover else "/api/v1/provision",
            headers={"X-Ota-Bootstrap-Token": bootstrap_secret},
        )
        token = provisioned.get("token")
        if not isinstance(token, str) or len(token) < 40:
            raise OtaClientError("OTA supervisor returned an invalid token")
        config = {
            "host": resolved_host,
            "port": int(provisioned.get("port", 8791)),
            "token": token,
            "fingerprint": device.property("ro.build.fingerprint"),
            "createdAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "recovered": recover,
            "confirmed": False,
        }
        write_secret_file(token_file, json.dumps(config, indent=2) + "\n")
        headers = signed_headers(token, "POST", "/api/v1/provision/confirm", EMPTY_SHA256)
        raw_request(
            base_url,
            "POST",
            "/api/v1/provision/confirm",
            headers=headers,
        )
        config["confirmed"] = True
        write_secret_file(token_file, json.dumps(config, indent=2) + "\n")
        return {
            "provisioned": True,
            "recovered": recover,
            "host": resolved_host,
            "port": config["port"],
            "tokenFile": str(token_file),
        }
    finally:
        try:
            device.command("forward", "--remove", local)
        except Exception:
            pass


def load_config(path: pathlib.Path, host: str | None) -> tuple[OtaClient, dict]:
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise OtaClientError(f"Unable to read OTA token file: {path}") from error
    resolved_host = host or config.get("host")
    if not isinstance(resolved_host, str):
        raise OtaClientError("OTA host is missing")
    token = config.get("token")
    if not isinstance(token, str):
        raise OtaClientError("OTA token is missing")
    return OtaClient(resolved_host, token, int(config.get("port", 8791))), config


def deprovision(serial: str | None, token_file: pathlib.Path) -> dict:
    _, config = load_config(token_file, None)
    device = Device(serial)
    local_port = free_tcp_port()
    local = f"tcp:{local_port}"
    device.command("forward", local, "tcp:8791")
    try:
        client = OtaClient("127.0.0.1", config["token"], local_port)
        return client.request(
            "POST",
            "/api/v1/deprovision",
            extra_headers={"X-Ota-Confirm": "CLEAR_DEVICE_OWNER"},
        )
    finally:
        try:
            device.command("forward", "--remove", local)
        except Exception:
            pass


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--token-file", type=pathlib.Path, default=DEFAULT_TOKEN_FILE)
    parser.add_argument(
        "--bootstrap-file",
        type=pathlib.Path,
        default=DEFAULT_BOOTSTRAP_FILE,
    )
    parser.add_argument("--host")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("build-supervisor")
    provision_parser = subparsers.add_parser("provision")
    provision_parser.add_argument("--serial")
    recover_parser = subparsers.add_parser("recover-token")
    recover_parser.add_argument("--serial")
    deprovision_parser = subparsers.add_parser("deprovision")
    deprovision_parser.add_argument("--serial")
    subparsers.add_parser("status")
    push_parser = subparsers.add_parser("push")
    push_parser.add_argument("apk", type=pathlib.Path)
    subparsers.add_parser("rollback")

    args = parser.parse_args()
    if args.command == "build-supervisor":
        result = build_supervisor(args.bootstrap_file)
    elif args.command in {"provision", "recover-token"}:
        result = provision(
            args.serial,
            args.token_file,
            args.bootstrap_file,
            args.host,
            recover=args.command == "recover-token",
        )
    elif args.command == "deprovision":
        result = deprovision(args.serial, args.token_file)
    else:
        client, _ = load_config(args.token_file, args.host)
        client.progress = lambda text: print(text, file=sys.stderr, flush=True)
        if args.command == "status":
            result = client.status()
        elif args.command == "push":
            if not args.apk.is_file():
                raise OtaClientError(f"APK does not exist: {args.apk}")
            result = client.push(args.apk)
        elif args.command == "rollback":
            result = client.rollback()
        else:
            raise AssertionError(args.command)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    try:
        main()
    except OtaClientError as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)
