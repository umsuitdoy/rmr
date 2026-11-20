"""Run a scoped web MCP session from a terminal without Tkinter."""

import argparse
import signal
import sys
from collections.abc import Sequence
from pathlib import Path
from time import sleep
from types import FrameType

from pydantic import ValidationError

from rmr.desktop_config import (
    DesktopSettings,
    build_public_url,
    generate_url_key,
    load_settings,
    save_settings,
)
from rmr.desktop_runtime import SessionController, desktop_data_dir


def _run_session(settings: DesktopSettings, settings_path: Path) -> str | None:
    controller = SessionController(data_dir=settings_path.parent)
    stop_requested = False

    def request_stop(_signum: int, _frame: FrameType | None) -> None:
        nonlocal stop_requested
        stop_requested = True

    handlers = {
        sig: signal.signal(sig, request_stop) for sig in (signal.SIGINT, signal.SIGTERM)
    }
    error = None
    announced = False
    try:
        print("Starting RMR and ngrok. Press Ctrl+C to stop.", file=sys.stderr)
        controller.start(settings, wait=False)
        while not stop_requested:
            status = controller.status()
            if status.error:
                error = status.error
                break
            if status.tunnel == "online" and not announced:
                try:
                    save_settings(settings_path, settings)
                except OSError:
                    error = "Could not save the terminal session settings."
                    break
                if not stop_requested:
                    print(build_public_url(settings), flush=True)
                    announced = True
            sleep(0.1)
    finally:
        try:
            controller.stop()
        finally:
            for sig, handler in handlers.items():
                signal.signal(sig, handler)

    return controller.status().error or error


def main(argv: Sequence[str] | None = None) -> int:
    """Validate CLI settings and run the session in the foreground."""
    parser = argparse.ArgumentParser(
        prog="rmr-web", description="Share one Git worktree with a web MCP client via ngrok."
    )
    parser.add_argument("--repo", required=True, type=Path, help="Root of a Git worktree")
    parser.add_argument("--domain", required=True, help="Your assigned ngrok domain")
    parser.add_argument("--port", type=int, default=8000, help="Local port (default: 8000)")
    parser.add_argument("--rotate-key", action="store_true", help="Generate a new MCP URL key")
    args = parser.parse_args(argv)

    try:
        settings = DesktopSettings(
            project_root=args.repo,
            domain=args.domain,
            port=args.port,
            url_key=generate_url_key(),
        )
    except ValidationError as exc:
        issues = exc.errors(include_input=False, include_context=False, include_url=False)
        parser.error("\n".join(f"{issue['loc'][0]}: {issue['msg']}" for issue in issues))
    except OSError:
        parser.error("Could not read the selected repository.")

    settings_path = desktop_data_dir() / "terminal-settings.json"
    if not args.rotate_key:
        previous = load_settings(settings_path)
        if (
            previous is not None
            and previous.project_root == settings.project_root
            and previous.domain == settings.domain
        ):
            settings.url_key = previous.url_key

    error = _run_session(settings, settings_path)
    if error:
        print(error, file=sys.stderr)
        return 1
    return 0
