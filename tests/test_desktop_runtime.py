"""Verify ownership, readiness, cancellation, and cleanup of Desktop sessions."""

import json
import socket
import subprocess
from io import BytesIO
from pathlib import Path
from threading import Event, Thread
from time import monotonic, sleep
from unittest.mock import Mock

import pytest

from rmr import desktop_runtime as runtime
from rmr.desktop_config import DesktopSettings, build_mcp_path


class FakeChild:
    def __init__(self, name, stops, *, output=b""):
        self.name = name
        self.stops = stops
        self.stdout = BytesIO(output) if name == "ngrok" else None
        self.returncode = None

    def poll(self):
        return self.returncode

    def terminate(self):
        self.stops.append(self.name)
        self.returncode = -15

    def wait(self, *, timeout):
        return self.returncode


def tunnel_log(url):
    return (json.dumps({"msg": "started tunnel", "url": url}) + "\n").encode()


def wait_for(predicate):
    deadline = monotonic() + 4
    while monotonic() < deadline:
        if predicate():
            return
        sleep(0.01)
    pytest.fail("Session did not reach the expected state")


@pytest.fixture
def settings(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True, timeout=5)
    (repo / "README.md").write_text("Demo", encoding="utf-8")
    return DesktopSettings(
        project_root=repo, domain="example.ngrok.app", url_key="x" * 32
    )


@pytest.fixture
def controller(tmp_path):
    session = runtime.SessionController(
        data_dir=tmp_path / "private", startup_timeout=0.5
    )
    yield session
    session.stop()


@pytest.fixture
def launcher(monkeypatch, settings):
    monkeypatch.setattr(runtime.shutil, "which", lambda _: "fake-ngrok")
    monkeypatch.setattr(runtime, "port_available", lambda _: True)
    monkeypatch.setattr(runtime, "mcp_ready", Mock(return_value=True))
    stops = []
    server = FakeChild("server", stops)
    ngrok = FakeChild("ngrok", stops, output=tunnel_log(f"https://{settings.domain}"))
    spawn = Mock(side_effect=[server, ngrok])
    monkeypatch.setattr(runtime, "spawn", spawn)
    return spawn, server, ngrok, stops


def test_starts_scoped_server_before_tunnel_and_stops_owned_processes(
    controller, settings, launcher
):
    spawn, _, _, stops = launcher
    controller.start(settings)
    assert controller.status() == runtime.SessionStatus("online", "online")

    server_call, tunnel_call = spawn.call_args_list
    server_command = server_call.args[0]
    tunnel_command = tunnel_call.args[0]
    registry = Path(server_command[server_command.index("--session-config") + 1])
    policy = Path(tunnel_command[tunnel_command.index("--traffic-policy-file") + 1])
    session_dir = registry.parent
    assert session_dir == policy.parent
    assert registry.is_file() and policy.is_file()
    assert json.loads(registry.read_text(encoding="utf-8")) == {
        "projects": {
            "project": {
                "root_path": str(settings.project_root),
                "language": "python",
                "entry_docs": ["README.md"],
            }
        }
    }
    assert build_mcp_path(settings.url_key) in policy.read_text(encoding="utf-8")
    assert all(settings.url_key.get_secret_value() not in arg for arg in server_command)
    assert all(settings.url_key.get_secret_value() not in arg for arg in tunnel_command)
    assert (
        server_call.kwargs["env"]["RMR_SESSION_URL_KEY"]
        == settings.url_key.get_secret_value()
    )
    assert server_call.kwargs["env"]["RMR_SESSION_DOMAIN"] == settings.domain
    assert tunnel_command[2] == f"127.0.0.1:{settings.port}"
    assert "--inspect=false" in tunnel_command

    controller.start(settings)
    assert spawn.call_count == 2
    controller.stop()
    controller.stop()
    assert stops == ["ngrok", "server"]
    assert not session_dir.exists()
    assert controller.status() == runtime.SessionStatus()


def test_port_in_use_opens_neither_child(controller, settings, launcher, monkeypatch):
    spawn, _, _, _ = launcher
    monkeypatch.setattr(runtime, "port_available", lambda _: False)
    controller.start(settings)
    spawn.assert_not_called()
    assert controller.status().server == "stopped"
    assert "port" in controller.status().error
    assert "in use" in controller.status().error


def test_server_requires_mcp_initialize_before_starting_ngrok(
    controller, settings, launcher, monkeypatch
):
    spawn, _, _, stops = launcher
    monkeypatch.setattr(runtime, "mcp_ready", lambda *_: False)
    controller.start(settings)
    assert spawn.call_count == 1
    assert stops == ["server"]
    assert "MCP initialization" in controller.status().error


def test_wrong_ngrok_url_never_reports_online_and_retries_cleanly(
    controller, settings, launcher
):
    spawn, _, ngrok, stops = launcher
    ngrok.stdout = BytesIO(tunnel_log("https://another.ngrok.app"))
    controller.start(settings)
    assert controller.status().tunnel == "stopped"
    assert "Check the domain" in controller.status().error
    assert stops == ["ngrok", "server"]

    spawn.side_effect = [
        FakeChild("server", stops),
        FakeChild("ngrok", stops, output=tunnel_log(f"https://{settings.domain}")),
    ]
    controller.start(settings)
    assert controller.status() == runtime.SessionStatus("online", "online")


def test_ngrok_auth_error_does_not_expose_raw_output(controller, settings, launcher):
    _, _, ngrok, stops = launcher
    mcp_url = f"https://{settings.domain}{build_mcp_path(settings.url_key)}"
    ngrok.stdout = BytesIO(
        f"ERR_NGROK_4018 auth=private-token url={mcp_url}\n".encode()
    )
    controller.start(settings)
    assert stops == ["ngrok", "server"]
    error = controller.status().error
    assert "authentication failed (ERR_NGROK_4018)" in error
    assert "private-token" not in error
    assert mcp_url not in error


@pytest.mark.parametrize(
    "index, label, sibling", [(1, "RMR server", "ngrok"), (2, "ngrok", "server")]
)
def test_unexpected_exit_stops_sibling(
    controller, settings, launcher, index, label, sibling
):
    _, _, _, stops = launcher
    controller.start(settings)
    launcher[index].returncode = 1
    wait_for(lambda: controller.status().server == "stopped")
    assert controller.status().tunnel == "stopped"
    assert f"{label} exited" in controller.status().error
    assert stops == [sibling]


def test_stop_during_server_start_prevents_tunnel(
    controller, settings, launcher, monkeypatch
):
    spawn, _, _, stops = launcher
    probe_entered, release_probe = Event(), Event()

    def wait_for_probe(*_):
        probe_entered.set()
        assert release_probe.wait(3)
        return True

    monkeypatch.setattr(runtime, "mcp_ready", wait_for_probe)
    start = Thread(target=controller.start, args=(settings,))
    stop = Thread(target=controller.stop)
    start.start()
    try:
        assert probe_entered.wait(3)
        assert controller.status().server == "starting"
        stop.start()
        wait_for(lambda: controller.status().server == "stopping")
    finally:
        release_probe.set()
        start.join(timeout=3)
        if stop.ident is not None:
            stop.join(timeout=3)
    assert not start.is_alive() and not stop.is_alive()
    assert spawn.call_count == 1
    assert stops == ["server"]
    assert controller.status() == runtime.SessionStatus()


def test_cleanup_keeps_session_files_until_both_children_exit(
    controller, settings, launcher, monkeypatch
):
    spawn, _, ngrok, _ = launcher
    controller.start(settings)
    registry = Path(spawn.call_args_list[0].args[0][4])
    original_stop = runtime.stop_process
    monkeypatch.setattr(
        runtime,
        "stop_process",
        lambda child: child is not ngrok and original_stop(child),
    )
    controller.stop()
    assert registry.is_file()
    assert controller.status().tunnel == "stopping"

    monkeypatch.setattr(runtime, "stop_process", original_stop)
    controller.stop()
    assert not registry.parent.exists()
    assert controller.status() == runtime.SessionStatus()


def test_cleanup_can_retry_after_file_removal_fails(
    controller, settings, launcher, monkeypatch
):
    spawn, _, _, _ = launcher
    controller.start(settings)
    session_dir = Path(spawn.call_args_list[0].args[0][4]).parent
    with monkeypatch.context() as patch:
        patch.setattr(runtime.shutil, "rmtree", Mock(side_effect=PermissionError))
        controller.stop()
    assert controller.status().server == "stopping"
    assert controller.status().tunnel == "stopped"
    assert session_dir.is_dir()

    controller.stop()
    assert controller.status() == runtime.SessionStatus()
    assert not session_dir.exists()


def test_real_keyed_server_readiness_and_restart(tmp_path, settings, monkeypatch):
    with socket.socket() as listener:
        listener.bind((runtime.LOOPBACK, 0))
        port = listener.getsockname()[1]
    settings = settings.model_copy(update={"port": port})
    controller = runtime.SessionController(
        data_dir=tmp_path / "private", startup_timeout=8
    )
    original_spawn = runtime.spawn
    stops = []

    def start_children(command, **kwargs):
        if kwargs.get("capture"):
            return FakeChild(
                "ngrok", stops, output=tunnel_log(f"https://{settings.domain}")
            )
        return original_spawn(command, **kwargs)

    monkeypatch.setattr(runtime.shutil, "which", lambda _: "fake-ngrok")
    monkeypatch.setattr(runtime, "spawn", start_children)
    for _ in range(2):
        try:
            controller.start(settings)
            assert controller.status() == runtime.SessionStatus("online", "online")
        finally:
            controller.stop()
        assert controller.status() == runtime.SessionStatus()
