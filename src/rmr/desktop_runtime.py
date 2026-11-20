"""Run one repository-scoped MCP server and its ngrok tunnel."""

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import mkdtemp
from threading import Event, Lock, Thread
from time import monotonic

from rmr._desktop_processes import (
    NGROK_AUTH_ERRORS,
    NgrokLog,
    is_running,
    mcp_ready,
    port_available,
    spawn,
    stop_process,
)
from rmr.desktop_config import DesktopSettings, build_mcp_path
from rmr.schemas import ProjectConfig
from rmr.tunnel import LOOPBACK, build_ngrok_policy

_POLL_SECONDS = 0.1
_ENTRY_DOCS = ("README.md", "AGENTS.md", "docs/architecture.md")


def desktop_data_dir() -> Path:
    """Return the private application data location in the user's profile."""
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local"
    else:
        base = os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share"
    return Path(base) / "RMR"


@dataclass(frozen=True)
class SessionStatus:
    """State of the current session without any credentials."""

    server: str = "stopped"
    tunnel: str = "stopped"
    error: str | None = None


class _SessionError(Exception):
    """A fixed message safe for display."""


@dataclass
class _Session:
    cancel: Event = field(default_factory=Event)
    ready: Event = field(default_factory=Event)
    finished: Event = field(default_factory=Event)
    ngrok_log: NgrokLog = field(default_factory=NgrokLog)
    directory: Path | None = None
    server: subprocess.Popen[bytes] | None = None
    tunnel: subprocess.Popen[bytes] | None = None
    log_reader: Thread | None = None


class SessionController:
    """Own child processes and their private registry and policy for a single session."""

    def __init__(
        self, *, data_dir: Path | None = None, startup_timeout: float = 20.0
    ) -> None:
        self._data_dir = desktop_data_dir() if data_dir is None else Path(data_dir)
        self._startup_timeout = startup_timeout
        self._lock = Lock()
        self._active: _Session | None = None
        self._status = SessionStatus()

    def status(self) -> SessionStatus:
        with self._lock:
            return self._status

    def start(self, settings: DesktopSettings, *, wait: bool = True) -> None:
        with self._lock:
            if self._active is not None:
                return
            run = _Session()
            self._active = run
            self._status = SessionStatus(server="starting")
            Thread(target=self._run_session, args=(run, settings), daemon=True).start()
        if wait:
            run.ready.wait()

    def stop(self) -> None:
        with self._lock:
            run = self._active
            if run is None:
                return
            run.cancel.set()
            self._status = SessionStatus(
                "stopping" if self._status.server != "stopped" else "stopped",
                "stopping" if self._status.tunnel != "stopped" else "stopped",
                self._status.error,
            )
            if run.finished.is_set():
                run.finished.clear()
                Thread(target=self._finish, args=(run, None), daemon=True).start()
        run.finished.wait()

    def _publish(self, run: _Session, status: SessionStatus) -> bool:
        with self._lock:
            if run.cancel.is_set():
                return False
            self._status = status
            return True

    def _prepare(
        self, run: _Session, settings: DesktopSettings, mcp_path: str
    ) -> tuple[Path, Path]:
        try:
            self._data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            run.directory = Path(mkdtemp(prefix="session-", dir=self._data_dir))
            root = settings.project_root
            project = ProjectConfig(
                project_id="project",
                root_path=str(root),
                entry_docs=[doc for doc in _ENTRY_DOCS if (root / doc).is_file()],
            )
            registry = run.directory / "projects.json"
            policy = run.directory / "policy.json"
            projects = {project.project_id: project.model_dump(exclude={"project_id"})}
            registry.write_text(json.dumps({"projects": projects}), encoding="utf-8")
            policy.write_text(
                json.dumps(build_ngrok_policy(mcp_path)), encoding="utf-8"
            )
            return registry, policy
        except (OSError, ValueError):
            raise _SessionError(
                "Could not prepare the private RMR session files."
            ) from None

    def _run_session(self, run: _Session, settings: DesktopSettings) -> None:
        error = None
        try:
            self._launch(run, settings)
            if self._publish(run, SessionStatus("online", "online")):
                run.ready.set()
                while not run.cancel.wait(_POLL_SECONDS):
                    if not is_running(run.server):
                        raise _SessionError(
                            "RMR server exited. The tunnel was stopped."
                        )
                    if not is_running(run.tunnel):
                        raise _SessionError(
                            "ngrok exited. The RMR server was stopped."
                        )
        except _SessionError as exc:
            if not run.cancel.is_set():
                error = str(exc)
        except Exception:  # noqa: BLE001 - Never log an unexpected worker exception containing the URL.
            error = "RMR could not start the session."
        finally:
            self._finish(run, error)

    def _launch(self, run: _Session, settings: DesktopSettings) -> None:
        if run.cancel.is_set():
            return
        ngrok = shutil.which("ngrok")
        if ngrok is None:
            raise _SessionError(
                "ngrok CLI was not found. Install it and add it to PATH."
            )
        if not port_available(settings.port):
            raise _SessionError(f"Local port {settings.port} is in use or unavailable.")

        mcp_path = build_mcp_path(settings.url_key)
        registry, policy = self._prepare(run, settings, mcp_path)
        if run.cancel.is_set():
            return
        try:
            run.server = spawn(
                [
                    sys.executable,
                    "-m",
                    "rmr.app",
                    "--session-config",
                    str(registry),
                    "--port",
                    str(settings.port),
                ],
                env={
                    **os.environ,
                    "RMR_SESSION_URL_KEY": settings.url_key.get_secret_value(),
                    "RMR_SESSION_DOMAIN": settings.domain,
                },
            )
        except OSError:
            raise _SessionError("Could not launch the local RMR server.") from None
        if not self._wait_server(run, settings.port, mcp_path):
            return
        if not self._publish(run, SessionStatus("online", "starting")):
            return
        public_base = f"https://{settings.domain}"
        try:
            run.tunnel = spawn(
                [
                    ngrok,
                    "http",
                    f"{LOOPBACK}:{settings.port}",
                    "--url",
                    public_base,
                    "--traffic-policy-file",
                    str(policy),
                    "--inspect=false",
                    "--log",
                    "stdout",
                    "--log-format",
                    "json",
                    "--log-level",
                    "info",
                ],
                capture=True,
            )
        except OSError:
            raise _SessionError(
                "Could not launch ngrok. Check the CLI installation."
            ) from None
        run.log_reader = Thread(
            target=run.ngrok_log.read,
            args=(run.tunnel.stdout, public_base),
            daemon=True,
        )
        run.log_reader.start()
        self._wait_tunnel(run)

    def _wait_server(self, run: _Session, port: int, path: str) -> bool:
        deadline = monotonic() + self._startup_timeout
        while not run.cancel.is_set():
            if not is_running(run.server):
                if not port_available(port):
                    raise _SessionError(f"Local port {port} is in use or unavailable.")
                raise _SessionError(
                    "RMR server exited before it was ready. Check the selected Git root."
                )
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise _SessionError(
                    "RMR did not respond to MCP initialization in time."
                )
            if mcp_ready(port, path, min(0.4, remaining)) and is_running(run.server):
                return True
            run.cancel.wait(min(_POLL_SECONDS, remaining))
        return False

    def _wait_tunnel(self, run: _Session) -> None:
        deadline = monotonic() + self._startup_timeout
        while not run.cancel.is_set():
            if not is_running(run.server):
                raise _SessionError("RMR server exited while ngrok was starting.")
            if not is_running(run.tunnel):
                run.log_reader.join(timeout=0.2)
                break
            if run.ngrok_log.ready.is_set():
                return
            if run.ngrok_log.error_code in NGROK_AUTH_ERRORS or monotonic() >= deadline:
                break
            run.cancel.wait(_POLL_SECONDS)
        if run.cancel.is_set():
            return
        code = run.ngrok_log.error_code
        if code in NGROK_AUTH_ERRORS:
            raise _SessionError(
                f"ngrok authentication failed ({code}). Check the configured authtoken."
            )
        suffix = f" ({code})" if code else ""
        raise _SessionError(
            f"ngrok did not confirm the tunnel{suffix}. Check the domain and connection."
        )

    def _finish(self, run: _Session, error: str | None) -> None:
        tunnel_stopped = stop_process(run.tunnel)
        server_stopped = stop_process(run.server)
        if not tunnel_stopped or not server_stopped:
            error = "A session process could not be stopped. Try Stop again."
        elif run.directory is not None:
            try:
                shutil.rmtree(run.directory)
            except FileNotFoundError:
                run.directory = None
            except OSError:
                error = "Session files could not be removed. Try Stop again."
            else:
                run.directory = None
        with self._lock:
            cleanup_pending = (
                tunnel_stopped and server_stopped and run.directory is not None
            )
            self._status = SessionStatus(
                "stopped" if server_stopped and not cleanup_pending else "stopping",
                "stopped" if tunnel_stopped else "stopping",
                error,
            )
            if tunnel_stopped and server_stopped and run.directory is None:
                self._active = None
            run.finished.set()
            run.ready.set()
