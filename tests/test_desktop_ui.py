"""Exercise the desktop form and its worker handoff with an owned fake session."""

import subprocess
import time
from contextlib import suppress
from threading import Event, get_ident

import pytest

tk = pytest.importorskip("tkinter")

from rmr import desktop_ui
from rmr.desktop_config import build_public_url, generate_url_key
from rmr.desktop_runtime import SessionStatus


class _FakeController:
    def __init__(self):
        self.current = SessionStatus()
        self.starts = []
        self.stops = 0
        self.ui_thread = get_ident()

    def status(self):
        return self.current

    def start(self, settings):
        assert get_ident() != self.ui_thread
        self.starts.append(settings)
        self.current = SessionStatus(server="online", tunnel="online")

    def stop(self):
        assert get_ident() != self.ui_thread
        self.stops += 1
        self.current = SessionStatus()


@pytest.fixture
def root():
    try:
        window = tk.Tk()
    except tk.TclError:
        pytest.skip("A desktop display is required for Tkinter tests.")
    window.withdraw()
    try:
        yield window
    finally:
        with suppress(tk.TclError):
            window.destroy()


@pytest.fixture
def repo(tmp_path):
    path = tmp_path / "demo"
    path.mkdir()
    subprocess.run(["git", "init", "--quiet"], cwd=path, timeout=5, check=True)
    return path


def _wait(root, condition):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        root.update()
        if condition():
            return
        time.sleep(0.01)
    pytest.fail("Tkinter did not reach the expected state.")


def _window_closed(root):
    with suppress(tk.TclError):
        return not root.winfo_exists()
    return True


def test_desktop_form_copy_start_stop_and_persist(root, repo, tmp_path, monkeypatch):
    controller = _FakeController()
    path = tmp_path / "profile" / "settings.json"
    window = desktop_ui.DesktopWindow(root, controller, path)
    assert not controller.starts

    subfolder = repo / "src"
    subfolder.mkdir()
    monkeypatch.setattr(
        desktop_ui.filedialog, "askdirectory", lambda **_: str(subfolder)
    )
    window._choose_directory()
    window.fields["domain"].set("https://demo.example/")
    window.fields["port"].set("8181")
    window.start()
    _wait(
        root,
        lambda: (
            "subdirectory" in window.field_errors["project_root"].get()
            and window._start_button.instate(["!disabled"])
        ),
    )
    assert not controller.starts

    window.fields["project_root"].set(str(repo))
    key = generate_url_key()
    window.fields["url_key"].set(key)
    assert str(window._key_entry.cget("show")) == "*"
    assert str(window._url_entry.cget("show")) == "*"
    window.visible.set(True)
    window._toggle_visibility()
    assert str(window._url_entry.cget("show")) == ""
    clipboard = []
    monkeypatch.setattr(root, "clipboard_clear", clipboard.clear)
    monkeypatch.setattr(root, "clipboard_append", clipboard.append)
    window._copy_url()
    assert clipboard == [f"https://demo.example/mcp/{key}"]
    assert key not in window.notice.get()

    window.start()
    _wait(root, lambda: window.tunnel_status.get() == "Online")
    assert controller.starts[0].port == 8181
    assert all(widget.instate(["disabled"]) for widget in window._session_fields)

    window.stop()
    _wait(root, lambda: window._start_button.instate(["!disabled"]))
    assert controller.stops == 1
    window._rotate_key()
    assert window.fields["url_key"].get() != key
    assert "previous URL" in window.rotation_notice.get()
    rotated_url = window.url.get()
    window.close()
    _wait(root, lambda: _window_closed(root))
    persisted = desktop_ui.load_settings(path)
    assert persisted is not None
    assert build_public_url(persisted) == rotated_url


def test_close_during_pending_start_never_launches(root, repo, tmp_path, monkeypatch):
    controller = _FakeController()
    begun = Event()
    release = Event()
    save = desktop_ui.save_settings

    def slow_save(path, settings):
        begun.set()
        assert release.wait(5)
        save(path, settings)

    monkeypatch.setattr(desktop_ui, "save_settings", slow_save)
    window = desktop_ui.DesktopWindow(root, controller, tmp_path / "settings.json")
    window.fields["project_root"].set(str(repo))
    window.fields["domain"].set("demo.example")
    window.start()
    try:
        _wait(root, begun.is_set)
        window.close()
        root.update()
        assert not _window_closed(root)
        assert not controller.starts
    finally:
        release.set()
    _wait(root, lambda: _window_closed(root))
    assert not controller.starts
    assert controller.stops == 1
