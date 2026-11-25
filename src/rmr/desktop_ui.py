"""Tkinter launcher for a single scoped RMR Desktop session."""

import tkinter as tk
from pathlib import Path
from queue import Empty, SimpleQueue
from threading import Event, Thread
from tkinter import filedialog, ttk

from pydantic import ValidationError

from rmr.desktop_config import (
    DesktopSettings,
    build_mcp_path,
    generate_url_key,
    load_settings,
    normalize_domain,
    save_settings,
)
from rmr.desktop_runtime import SessionController, SessionStatus, desktop_data_dir

_FIELDS = (
    ("project_root", "Repository root"),
    ("domain", "ngrok domain"),
    ("port", "Local port"),
    ("url_key", "URL key"),
)
_STATE_LABELS = {
    "stopped": "Stopped",
    "starting": "Starting",
    "online": "Online",
    "stopping": "Stopping",
}


class DesktopWindow:
    """Own widgets and relay worker results on Tk's event loop."""

    def __init__(
        self, root: tk.Tk, controller: SessionController, settings_path: Path
    ) -> None:
        self.root = root
        self.controller = controller
        self.settings_path = settings_path
        self._form_job: Thread | None = None
        self._stop_job: Thread | None = None
        self._cancel = Event()
        self._closing = False
        self._messages: SimpleQueue[tuple[str, str]] = SimpleQueue()
        self._saved_values: dict[str, str] = {}
        self._operation_error = ""

        settings = load_settings(settings_path)
        if settings is not None:
            self._saved_values = {
                "project_root": str(settings.project_root),
                "domain": settings.domain,
                "port": str(settings.port),
                "url_key": settings.url_key.get_secret_value(),
            }
        defaults = self._saved_values or {"port": "8000", "url_key": generate_url_key()}
        self._initial_key = defaults["url_key"]
        self.fields = {
            name: tk.StringVar(root, value=defaults.get(name, ""))
            for name, _ in _FIELDS
        }
        self.field_errors = {name: tk.StringVar(root) for name, _ in _FIELDS}
        self.url = tk.StringVar(root)
        self.visible = tk.BooleanVar(root, value=False)
        self.notice = tk.StringVar(root)
        self.rotation_notice = tk.StringVar(root)
        self.error = tk.StringVar(root)
        self.server_status = tk.StringVar(root)
        self.tunnel_status = tk.StringVar(root)
        self._session_fields: list[ttk.Widget] = []

        root.title("RMR Desktop")
        root.minsize(610, 370)
        root.protocol("WM_DELETE_WINDOW", self.close)
        self._build_form()
        for name, variable in self.fields.items():
            variable.trace_add(
                "write", lambda *_, field=name: self._field_changed(field)
            )
        self._refresh_url()
        self._poll()

    def _build_form(self) -> None:
        panel = ttk.Frame(self.root, padding=16)
        panel.pack(fill="both", expand=True)
        panel.columnconfigure(1, weight=1)

        for index, (name, label) in enumerate(_FIELDS):
            row = index * 2
            ttk.Label(panel, text=label).grid(
                row=row, column=0, sticky="w", padx=(0, 12)
            )
            entry = ttk.Entry(panel, textvariable=self.fields[name], width=46)
            entry.grid(row=row, column=1, sticky="ew")
            self._session_fields.append(entry)
            ttk.Label(
                panel,
                textvariable=self.field_errors[name],
                foreground="#a02222",
                wraplength=450,
            ).grid(row=row + 1, column=1, columnspan=2, sticky="w", pady=(0, 4))
            if name == "url_key":
                entry.configure(show="*")
                self._key_entry = entry

        browse = ttk.Button(panel, text="Browse…", command=self._choose_directory)
        browse.grid(row=0, column=2, padx=(8, 0), sticky="ew")
        rotate = ttk.Button(panel, text="Rotate key", command=self._rotate_key)
        rotate.grid(row=6, column=2, padx=(8, 0), sticky="ew")
        self._session_fields.extend((browse, rotate))

        ttk.Checkbutton(
            panel,
            text="Show key and URL",
            variable=self.visible,
            command=self._toggle_visibility,
        ).grid(row=8, column=1, sticky="w", pady=(0, 8))
        ttk.Label(panel, text="MCP URL").grid(row=9, column=0, sticky="w")
        self._url_entry = ttk.Entry(
            panel, textvariable=self.url, state="readonly", show="*"
        )
        self._url_entry.grid(row=9, column=1, sticky="ew")
        self._copy_button = ttk.Button(panel, text="Copy URL", command=self._copy_url)
        self._copy_button.grid(row=9, column=2, padx=(8, 0), sticky="ew")
        ttk.Label(panel, textvariable=self.rotation_notice, wraplength=450).grid(
            row=10, column=1, columnspan=2, sticky="w"
        )
        ttk.Label(panel, textvariable=self.notice).grid(row=11, column=1, sticky="w")

        actions = ttk.Frame(panel)
        actions.grid(row=12, column=1, columnspan=2, sticky="w", pady=(8, 12))
        self._start_button = ttk.Button(actions, text="Start", command=self.start)
        self._start_button.pack(side="left")
        self._stop_button = ttk.Button(actions, text="Stop", command=self.stop)
        self._stop_button.pack(side="left", padx=(8, 0))
        ttk.Label(panel, text="MCP Server").grid(row=13, column=0, sticky="w")
        ttk.Label(panel, textvariable=self.server_status).grid(
            row=13, column=1, sticky="w"
        )
        ttk.Label(panel, text="ngrok Tunnel").grid(row=14, column=0, sticky="w")
        ttk.Label(panel, textvariable=self.tunnel_status).grid(
            row=14, column=1, sticky="w"
        )
        ttk.Label(
            panel, textvariable=self.error, foreground="#a02222", wraplength=550
        ).grid(row=15, column=0, columnspan=3, sticky="w", pady=(6, 0))

    def _choose_directory(self) -> None:
        directory = filedialog.askdirectory(
            parent=self.root,
            title="Select Git repository root",
            mustexist=True,
            initialdir=self.fields["project_root"].get() or str(Path.home()),
        )
        if directory:
            self.fields["project_root"].set(directory)

    def _rotate_key(self) -> None:
        self.fields["url_key"].set(generate_url_key())

    def _toggle_visibility(self) -> None:
        masking = "" if self.visible.get() else "*"
        self._key_entry.configure(show=masking)
        self._url_entry.configure(show=masking)

    def _field_changed(self, name: str) -> None:
        self.field_errors[name].set("")
        self.notice.set("")
        if name in {"domain", "url_key"}:
            self._refresh_url()

    def _refresh_url(self) -> None:
        key = self.fields["url_key"].get()
        try:
            domain = normalize_domain(self.fields["domain"].get())
            url = f"https://{domain}{build_mcp_path(key)}"
        except ValueError:
            url = ""
        self.url.set(url)
        self._copy_button.configure(state="normal" if url else "disabled")
        self.rotation_notice.set(
            "Starting with a new key invalidates the previous URL. Update the URL in your Custom App."
            if key != self._initial_key
            else ""
        )

    def _copy_url(self) -> None:
        url = self.url.get()
        if url:
            self.root.clipboard_clear()
            self.root.clipboard_append(url)
            self.notice.set("Copied MCP URL.")

    def _values(self) -> dict[str, str]:
        return {name: variable.get() for name, variable in self.fields.items()}

    def _run_form(self, values: dict[str, str], *, launch: bool) -> None:
        try:
            settings = DesktopSettings.model_validate(values)
        except ValidationError as exc:
            for issue in exc.errors(
                include_input=False, include_context=False, include_url=False
            ):
                self._messages.put((str(issue["loc"][0]), issue["msg"]))
            return
        except OSError:
            self._messages.put(("project_root", "Could not read the selected directory."))
            return
        if launch and self._cancel.is_set():
            return
        try:
            save_settings(self.settings_path, settings)
        except OSError:
            self._messages.put(("error", "Could not save RMR Desktop settings."))
            return
        self._saved_values = values
        if launch and not self._cancel.is_set():
            try:
                self.controller.start(settings)
            except Exception:  # noqa: BLE001 - keep unexpected worker details out of UI logs
                self._messages.put(("error", "Could not start the RMR session."))

    def _launch_form(self, values: dict[str, str], *, launch: bool) -> None:
        self._form_job = Thread(
            target=self._run_form, args=(values,), kwargs={"launch": launch}
        )
        self._form_job.start()

    def _stop_session(self) -> None:
        try:
            self.controller.stop()
        except Exception:  # noqa: BLE001 - keep unexpected worker details out of UI logs
            self._messages.put(("error", "Could not stop the RMR session. Please try again."))

    def start(self) -> None:
        if (
            self._closing
            or self._cancel.is_set()
            or self._busy(self.controller.status())
        ):
            return
        for issue in self.field_errors.values():
            issue.set("")
        self._operation_error = ""
        values = self._values()
        if not values["project_root"].strip():
            self.field_errors["project_root"].set(
                "Select a Git repository root before starting."
            )
            return
        self._launch_form(values, launch=True)
        self._render(self.controller.status())

    def stop(self) -> None:
        self._cancel.set()
        self._render(self.controller.status())

    def close(self) -> None:
        if self._closing:
            return
        self._closing = True
        values = self._values()
        if (
            not self._busy(self.controller.status())
            and values != self._saved_values
            and values["project_root"].strip()
            and self.url.get()
        ):
            self._launch_form(values, launch=False)
        self.stop()

    @staticmethod
    def _active(status: SessionStatus) -> bool:
        return status.server != "stopped" or status.tunnel != "stopped"

    def _form_running(self) -> bool:
        return self._form_job is not None and self._form_job.is_alive()

    def _busy(self, status: SessionStatus) -> bool:
        return self._form_running() or self._active(status)

    def _redact(self, message: str) -> str:
        key = self.fields["url_key"].get()
        url = self.url.get()
        if url:
            message = message.replace(url, "[URL redacted]")
        return message.replace(key, "[key redacted]") if key else message

    def _poll(self) -> None:
        while True:
            try:
                field, message = self._messages.get_nowait()
            except Empty:
                break
            if field in self.field_errors:
                self.field_errors[field].set(self._redact(message))
            else:
                self._operation_error = self._redact(message)

        status = self.controller.status()
        form_running = self._form_running()
        if self._cancel.is_set():
            # Wait for Start to claim the controller or finish its local work before Stop.
            if self._stop_job is None and (not form_running or self._active(status)):
                self._stop_job = Thread(target=self._stop_session)
                self._stop_job.start()
            if (
                self._stop_job is not None
                and not self._stop_job.is_alive()
                and not form_running
            ):
                self._stop_job = None
                self._cancel.clear()
                status = self.controller.status()
                if self._closing and not self._active(status):
                    self.root.destroy()
                    return
                self._closing = False

        self._render(status)
        self.root.after(100, self._poll)

    def _render(self, status: SessionStatus) -> None:
        busy = self._busy(status)
        stopping = self._cancel.is_set()
        locked = busy or stopping or self._closing
        state = "disabled" if locked else "normal"
        for widget in self._session_fields:
            widget.configure(state=state)
        self._start_button.configure(state=state)
        self._stop_button.configure(
            state="normal" if busy and not stopping else "disabled"
        )
        server = status.server
        if server == "stopped" and self._form_running() and not stopping:
            server = "starting"
        self.server_status.set(_STATE_LABELS[server])
        self.tunnel_status.set(_STATE_LABELS[status.tunnel])
        self.error.set(self._redact(status.error or self._operation_error))


def main() -> None:
    """Open the desktop launcher without starting a tunnel."""
    root = tk.Tk()
    DesktopWindow(root, SessionController(), desktop_data_dir() / "settings.json")
    root.mainloop()
