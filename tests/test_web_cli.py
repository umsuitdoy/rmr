import signal
import subprocess
from unittest.mock import Mock

import pytest

from rmr import web_cli
from rmr.desktop_runtime import SessionController, SessionStatus


@pytest.fixture
def terminal(tmp_path, monkeypatch):
    repo = tmp_path / "repo with spaces"
    subprocess.run(["git", "init", "--quiet", str(repo)], check=True)
    data_dir = tmp_path / "private"
    monkeypatch.setattr(web_cli, "desktop_data_dir", lambda: data_dir)
    controller = Mock(spec=SessionController)
    controller.status.return_value = SessionStatus("online", "online")
    monkeypatch.setattr(web_cli, "SessionController", lambda **kwargs: controller)
    monkeypatch.setattr(web_cli, "sleep", lambda _: signal.raise_signal(signal.SIGINT))
    return ["--repo", str(repo), "--domain", "example.ngrok-free.app"], data_dir, controller


def test_prints_ready_url_reuses_key_and_supports_rotation(terminal, capsys):
    args, _, controller = terminal
    assert web_cli.main(args) == 0
    url = capsys.readouterr().out.strip()
    assert url.startswith("https://example.ngrok-free.app/mcp/")
    assert controller.start.call_args.kwargs == {"wait": False}
    controller.stop.assert_called_once_with()

    assert web_cli.main([*args, "--port", "8001"]) == 0
    assert capsys.readouterr().out.strip() == url
    assert controller.start.call_args.args[0].port == 8001

    assert web_cli.main([*args, "--rotate-key"]) == 0
    rotated_url = capsys.readouterr().out.strip()
    assert rotated_url.startswith("https://example.ngrok-free.app/mcp/")
    assert rotated_url != url


def test_early_interrupt_stops_session_without_printing_url(terminal, capsys):
    args, data_dir, controller = terminal
    controller.status.side_effect = [SessionStatus(server="starting"), SessionStatus()]
    original_handler = signal.getsignal(signal.SIGINT)

    assert web_cli.main(args) == 0
    assert not capsys.readouterr().out
    assert not (data_dir / "terminal-settings.json").exists()
    controller.stop.assert_called_once_with()
    assert signal.getsignal(signal.SIGINT) is original_handler


def test_startup_failure_preserves_the_last_successful_url(terminal, capsys):
    args, data_dir, controller = terminal
    assert web_cli.main(args) == 0
    capsys.readouterr()
    settings_path = data_dir / "terminal-settings.json"
    previous_settings = settings_path.read_bytes()
    controller.status.return_value = SessionStatus(error="ngrok did not start")

    assert web_cli.main([*args, "--rotate-key"]) == 1
    captured = capsys.readouterr()
    assert not captured.out
    assert "ngrok did not start" in captured.err
    assert settings_path.read_bytes() == previous_settings
