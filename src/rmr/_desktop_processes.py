"""Manage process handles and verify startup of a Desktop session."""

import json
import os
import re
import socket
import subprocess
from dataclasses import dataclass, field
from http.client import HTTPConnection, HTTPException
from threading import Event
from typing import BinaryIO

from mcp.types import LATEST_PROTOCOL_VERSION, InitializeResult, JSONRPCResponse

from rmr.tunnel import LOOPBACK

_STOP_SECONDS = 2.0
_NGROK_ERROR = re.compile(rb"\bERR_NGROK_[0-9]{1,5}\b")
NGROK_AUTH_ERRORS = {
    "ERR_NGROK_105",
    "ERR_NGROK_106",
    "ERR_NGROK_107",
    "ERR_NGROK_4018",
}
_INITIALIZE = json.dumps(
    {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": LATEST_PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "rmr-desktop", "version": "1.0"},
        },
    }
).encode()


def port_available(port: int) -> bool:
    with socket.socket() as probe:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            probe.bind((LOOPBACK, port))
        except OSError:
            return False
    return True


def mcp_ready(port: int, path: str, timeout: float) -> bool:
    # Connect directly to loopback so proxy environment variables cannot see the key.
    connection = HTTPConnection(LOOPBACK, port, timeout=timeout)
    session_headers = None
    try:
        connection.request(
            "POST",
            path,
            _INITIALIZE,
            {
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
            },
        )
        with connection.getresponse() as response:
            if response.status != 200:
                return False
            content_type = response.headers.get_content_type()
            if content_type == "application/json":
                payload = response.read(65536)
            elif content_type == "text/event-stream":
                payload = b""
                for _ in range(16):
                    line = response.readline(65536)
                    if line.startswith(b"data:"):
                        payload = line[5:].strip()
                        break
                    if not line:
                        return False
            else:
                return False
            reply = JSONRPCResponse.model_validate_json(payload)
            result = InitializeResult.model_validate(reply.result).model_dump(
                by_alias=True
            )
            if session_id := response.getheader("Mcp-Session-Id"):
                session_headers = {
                    "Mcp-Session-Id": session_id,
                    "MCP-Protocol-Version": result["protocolVersion"],
                }
            return reply.id == 1 and result["serverInfo"]["name"] == "rmr"
    except (OSError, HTTPException, ValueError):
        return False
    finally:
        connection.close()
        if session_headers is not None:
            try:
                connection.request("DELETE", path, headers=session_headers)
                connection.getresponse().close()
            except (OSError, HTTPException):
                pass  # The probe result is independent of session cleanup.
            finally:
                connection.close()


def spawn(
    command: list[str], *, env: dict[str, str] | None = None, capture: bool = False
) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        command,
        env=env,
        shell=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
        stderr=subprocess.STDOUT if capture else subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )


@dataclass
class NgrokLog:
    ready: Event = field(default_factory=Event)
    error_code: str | None = None

    def read(self, stdout: BinaryIO, public_base: str) -> None:
        # Keep only readiness and a fixed-format error code. Raw output can contain secrets.
        with stdout:
            while line := stdout.readline(65536):
                if code := _NGROK_ERROR.search(line):
                    self.error_code = code.group().decode("ascii")
                try:
                    record = json.loads(line)
                except (ValueError, RecursionError):
                    continue
                if (
                    isinstance(record, dict)
                    and record.get("msg") == "started tunnel"
                    and record.get("url") in (public_base, f"{public_base}/")
                ):
                    self.ready.set()


def is_running(process: subprocess.Popen[bytes] | None) -> bool:
    return process is not None and process.poll() is None


def stop_process(process: subprocess.Popen[bytes] | None) -> bool:
    if process is None or process.poll() is not None:
        return True
    try:
        process.terminate()
        try:
            process.wait(timeout=_STOP_SECONDS)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=_STOP_SECONDS)
    except (OSError, subprocess.TimeoutExpired):
        return process.poll() is not None
    return True
