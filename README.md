# RMR

[![M8ven Verified](https://m8ven.ai/badge/mcp/umsuitdoy/rmr?variant=verified)](https://m8ven.ai/mcp/umsuitdoy/rmr?s=readme)

GitHub connectors and file uploads only show web AI static snapshots—missing uncommitted edits and code relationships. RMR gives web models read-only code intelligence over your live local repository via MCP:
- Smart search: Query AST patterns (ast-grep) and text (ripgrep).
- Deep context: Resolve symbols, callers, and change impact with CodeGraph.
- Live state: Pull uncommitted git diffs and SHA-256 verified excerpts without copy-pasting.

## Requirements

- **Core:** Python 3.11+, Git, and [uv](https://docs.astral.sh/uv/getting-started/installation/).
- **Set up connection:** Install the [ngrok CLI](https://ngrok.com/download) on `PATH`, configure your authtoken, and have a static domain assigned to your ngrok account. See [ngrok agent setup](https://ngrok.com/docs/start).
- **Windows Desktop launcher (optional):** Python with Tkinter and Windows Script Host (Windows only).
- **Optional search tools:** `ripgrep` (`rg`) accelerates text search (RMR falls back to Python when unavailable). `ast-grep` (`sg`) is required for structural AST pattern search.
- **Optional CodeGraph integration:** The official Windows installation of CodeGraph **1.6.2** is required for call tracing (`callers`, `callees`) and `impact` analysis. See [CodeGraph 1.6.2 release](https://github.com/colbymchenry/codegraph/releases/tag/v1.6.2).

## Quickstart

Start a session with **either** [Terminal (Windows, macOS, Linux)](#terminal-windows-macos-linux) **or** [RMR Desktop on Windows](#rmr-desktop-on-windows), then follow [Connect a web client](#connect-a-web-client).

### Terminal (Windows, macOS, Linux)

1. Open a terminal in the RMR directory.

   ```sh
   uv run rmr-web --repo "/path/to/your/repo" --domain your-domain.ngrok-free.app --port 8000
   ```

   *Example:*

   ```sh
   uv run rmr-web --repo "E:/project/rmr_source" --domain abc-xyz-example.ngrok-free.app --port 8000
   ```

2. <a id="mcp-url-example"></a>Copy the full HTTPS MCP URL printed in the terminal (for example, `https://your-domain.ngrok-free.app/mcp/<key>`), then follow [Connect a web client](#connect-a-web-client).
3. Keep the terminal open while using the connector. Press **Ctrl+C** to stop the tunnel and server.
4. Restarting with the last successful repo and domain reuses the same URL key; pass `--rotate-key` to generate a new URL (and update your connector).
   - To view or customize the saved key, edit `"url_key"` in `terminal-settings.json` (`%LOCALAPPDATA%\RMR\terminal-settings.json` on Windows, or `~/.local/share/RMR/terminal-settings.json` on macOS/Linux) using at least 32 URL-safe characters (`A-Z`, `a-z`, `0-9`, `_`, `-`), then restart `rmr-web` with the same `--repo` and `--domain` (without `--rotate-key`).

### RMR Desktop on Windows

1. Run [start_rmr.vbs](start_rmr.vbs). The launcher installs dependencies when needed and opens RMR Desktop.
2. Add your [ngrok domain](https://dashboard.ngrok.com/domains) and create the MCP URL.
3. Follow [Connect a web client](#connect-a-web-client).

### Connect a web client

In Gemini on the web (or another MCP-compatible web client), open **Settings → Connected Apps → Custom apps → Add MCP server**. Availability depends on your account and region.

**NOTICE:** Tool results reach Gemini through ngrok, and ngrok's Traffic Inspector can record the request path containing the key. Choose only repositories you have permission to share through those services. See [What ngrok Traffic Inspector stores](https://ngrok.com/docs/obs/traffic-inspection/http-requests#what-traffic-inspector-stores).

**WARNING:** If someone obtains your full MCP URL, they can send HTTPS requests to explore your repository. RMR authenticates requests using the [key](#mcp-url-example) in the URL, so update your connector whenever you change the key.

## How it works

```text
Web MCP client (e.g. Gemini) ── ngrok ── Streamable HTTP (loopback) ── FastMCP
                                                                          │
                                                                    analysis tools
                                                                          │
                                                                 selected repository
                                                                   ├── source files and Git
                                                                   └── CodeGraph index
```

Each session launched from the terminal or Desktop creates an isolated one-repo registry (with project ID `project`) and exposes only the three read-only analysis tools. All file paths are validated against the selected repository root.

Start with `get_project_overview` to locate relevant files, narrow down with `investigate_codebase`, then call `inspect_evidence` to read exact lines or a Git diff. Repository-specific results include `tree_state`, which records the Git commit and working-tree state.

## MCP tools

- **`get_project_overview`** — Lists the active repository when called without `project_id`. For a specific project, returns the file listing, previews of entry documents (`README.md`, `AGENTS.md`, `docs/architecture.md` if present), architecture decision records (`.rmr/adr/*.md`), and Git `tree_state`.
- **`investigate_codebase`** — Locates code and relationships by `mode`:
  - `outline` — Lists Python classes and functions using Python's AST.
  - `symbol` — Finds Python definitions and references with Jedi.
  - `grep` — Searches text or regular expressions with ripgrep (or Python fallback).
  - `ast` — Searches structural syntax patterns with ast-grep.
  - `callers` / `callees` — Finds direct incoming or outgoing calls via CodeGraph.
  - `impact` — Follows affected dependents across multiple steps via CodeGraph.
- **`inspect_evidence`** — Returns a line range (`file_lines`), a symbol's source (`symbol`), or a Git diff (`git_diff`). File excerpts include a SHA-256 content hash.

## CodeGraph integration

Install the official Windows build of CodeGraph 1.6.2 and run `codegraph init` from the root of the repository you want to analyze. RMR opens that repo's `.codegraph/codegraph.db` in read-only mode. Run `codegraph sync` in the same repo to refresh the index after code changes.

For `callers`, `callees`, and `impact`, pass the symbol name in `query`. Use `subpath` to select the exact definition file when names overlap. If that file has no matching definition, the result is empty. For calls, `line` identifies the definition and `call_line` identifies the call site in the caller's file when the index has it.

For example, after indexing this repository and sharing it in a session, call `investigate_codebase` with these arguments to see which symbols depend on `get_project`:

```json
{
  "project_id": "project",
  "mode": "impact",
  "query": "get_project",
  "subpath": "src/rmr/core/registry.py",
  "depth": 2,
  "max_results": 10
}
```

Impact results group affected symbols by definition and show the shortest dependency path to each. If a group contains overloads, its `definition` is representative and each path starts at the overload it follows. `depth` controls the number of dependency steps (1–5, default 2). `max_results` caps the number of groups and the total number of affected symbols (default 30, maximum 50). `ambiguous` signals multiple matching definition groups, and `truncated` signals that a limit omitted results.

## Result

See an example session output in [RMR-Project-Overview.md](RMR-Project-Overview.md).

## Development checks

Run the automated test suite:

```sh
uv run --extra dev pytest -q
```

To manually verify a web session with a sample repository:

- Confirm only the three analysis tools are listed and that URLs with a missing or incorrect key are rejected.
- Call `get_project_overview`, `investigate_codebase`, and `inspect_evidence` in order and confirm all results stay within the selected repository.
- Stop the session and confirm the URL stops serving requests.
- Restart with the same repo and domain/key to confirm the URL is preserved.
- Stop, rotate the key, restart, and confirm the old URL is rejected.
- Stop the tunnel when finished.

## License

RMR is released under the [MIT License](LICENSE). Third-party tools (`ngrok`, `ripgrep`, `ast-grep`, and [CodeGraph](https://github.com/colbymchenry/codegraph)) are not bundled with RMR; they are installed separately by the user and governed by their own licenses.
