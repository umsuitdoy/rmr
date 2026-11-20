"""Test the Desktop session's keyed HTTP endpoint and ngrok policy."""

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from http.client import HTTPConnection
from pathlib import Path

import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

from rmr.app import main
from rmr.desktop_config import build_mcp_path, generate_url_key
from rmr.tunnel import LOOPBACK, build_ngrok_policy

PUBLIC_DOMAIN = "example.test"


def test_ngrok_policy_guards_only_the_exact_keyed_path():
    path = build_mcp_path(generate_url_key())

    assert build_ngrok_policy(path) == {
        "on_http_request": [
            {
                "expressions": [f"req.url.raw_path != '{path}'"],
                "actions": [
                    {
                        "type": "custom-response",
                        "config": {"status_code": 404, "body": "Not Found"},
                    }
                ],
            }
        ]
    }


@pytest.mark.parametrize("path", ["/mcp", "/mcp/", "/mcp/key/extra", "/other/key"])
def test_ngrok_policy_refuses_noncanonical_paths(path):
    with pytest.raises(ValueError):
        build_ngrok_policy(path)


@pytest.mark.parametrize("key_kind", ["missing", "invalid"])
def test_session_rejects_missing_or_invalid_key_without_disclosing_it(
    key_kind, monkeypatch, capsys
):
    invalid_key = f"{generate_url_key()}/invalid"
    monkeypatch.delenv("RMR_SESSION_URL_KEY", raising=False)
    if key_kind == "invalid":
        monkeypatch.setenv("RMR_SESSION_URL_KEY", invalid_key)

    with pytest.raises(SystemExit) as exc:
        main(["--session-config", "registry.json"])

    assert exc.value.code == 2
    error = capsys.readouterr().err
    assert "RMR_SESSION_URL_KEY" in error
    assert invalid_key not in error
    assert "RMR_SESSION_URL_KEY" not in os.environ


def _wait_for_listener(server: subprocess.Popen, port: int) -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and server.poll() is None:
        try:
            with socket.create_connection((LOOPBACK, port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.05)
    pytest.fail("Desktop session did not listen on loopback before the timeout.")


def test_keyed_session_initializes_lists_tools_and_does_not_log_key(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "--quiet"], cwd=repo, check=True, timeout=5)
    config = tmp_path / "registry.json"
    config.write_text(
        json.dumps({"projects": {"sample": {"root_path": str(repo)}}}),
        encoding="utf-8",
    )
    with socket.socket() as sock:
        sock.bind((LOOPBACK, 0))
        port = sock.getsockname()[1]

    key = generate_url_key()
    path = build_mcp_path(key)
    output = tmp_path / "server.log"
    env = {
        **os.environ,
        "RMR_SESSION_URL_KEY": key,
        "RMR_SESSION_DOMAIN": PUBLIC_DOMAIN,
        "FASTMCP_LOG_LEVEL": "INFO",
    }
    # Desktop mode uses its own route and loopback bind regardless of legacy flags.
    command = [
        sys.executable,
        "-m",
        "rmr.app",
        "--session-config",
        str(config),
        "--port",
        str(port),
        "--host",
        "0.0.0.0",
        "--path",
        "/legacy",
    ]
    with (
        output.open("wb") as log,
        subprocess.Popen(
            command, env=env, stdout=log, stderr=subprocess.STDOUT
        ) as server,
    ):
        try:
            _wait_for_listener(server, port)
            encoded_key = f"%{ord(key[0]):02X}{key[1:]}"
            for wrong_path in (
                "/",
                "/mcp",
                "/mcp/v1",
                "/legacy",
                f"/mcp/{'x' * 32}",
                f"{path}/",
                f"/mcp/{encoded_key}",
            ):
                conn = HTTPConnection(LOOPBACK, port, timeout=2)
                try:
                    conn.request("POST", wrong_path)
                    response = conn.getresponse()
                    assert response.status == 404
                    assert response.getheader("Location") is None
                    assert key.encode("ascii") not in response.read()
                finally:
                    conn.close()

            async def verify_mcp():
                transport = StreamableHttpTransport(
                    url=f"http://{LOOPBACK}:{port}{path}",
                    headers={
                        "Host": PUBLIC_DOMAIN,
                        "Origin": f"https://{PUBLIC_DOMAIN}",
                    },
                )
                async with asyncio.timeout(10), Client(transport) as client:
                    tools = await client.list_tools()
                assert {tool.name for tool in tools} == {
                    "get_project_overview",
                    "investigate_codebase",
                    "inspect_evidence",
                }

            asyncio.run(verify_mcp())
        finally:
            if server.poll() is None:
                server.terminate()
                try:
                    server.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait(timeout=5)

    logs = output.read_text(encoding="utf-8", errors="replace")
    assert f"http://{LOOPBACK}:{port}" in logs
    assert key not in logs
