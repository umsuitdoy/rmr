# **Test**

*User prompt: @Rmr Overview of this project?*

Response: **RMR** (v0.1.0) is a Python-based **Read-Only Code Intelligence MCP Server** built on fastmcp that exposes a single local Git repository at a time to web MCP clients (such as Gemini Custom Apps) over an authenticated ngrok tunnel.

### **Core Architecture (src/rmr/)**

The codebase is organized into 69 total files (including a comprehensive pytest suite) split across four main layers:

| Layer | Key Modules | Responsibility |
| :---- | :---- | :---- |
| **MCP Tools** (tools/) | overview\_tool.py, investigate\_tool.py, evidence\_tool.py, memory\_tools.py | Exposes the tiered code-intelligence tools: Tier 1 project overview, Tier 2 codebase investigation, Tier 3 exact evidence extraction, plus memory tooling. |
| **Inspectors** (inspectors/) | ast\_outline.py, symbol\_jedi.py, search\_cli.py, codegraph.py, codegraph\_bridge.cjs, file\_reader.py, git\_cli.py | Backends for AST outlines, Jedi symbol lookup, text/structural search (ripgrep with Python fallback; ast-grep), CodeGraph call/impact analysis, line reading with SHA-256 hashes, and Git diffs. |
| **Core & Schemas** (core/, schemas.py) | path\_guard.py, registry.py, truncator.py, schemas.py | Enforces path security boundaries, manages project registration, truncates oversized outputs, and defines Pydantic models. |
| **Memory** (memory/) | frontmatter\_io.py, memory\_search.py, memory\_writer.py | Handles structured Markdown/frontmatter knowledge storage and search (paired with global-knowledge/). |
| **Server & Launchers** | app.py, tunnel.py, web\_cli.py, desktop\_ui.py, desktop\_runtime.py, desktop\_config.py, \_desktop\_processes.py, start\_rmr.vbs | Runs the MCP server, manages ngrok tunnels with URL-path key authentication, and provides both a cross-platform CLI and a Windows Tkinter desktop GUI. |

### **Tech Stack & Entry Points**

> * **Runtime & Packaging:** Python \>=3.11, managed with uv and built with hatchling (pyproject.toml).  
> * **Core Dependencies:** fastmcp, pydantic, pyyaml, python-frontmatter, jedi.  
> * **External / Optional Integrations:** ngrok CLI (required for web access), ripgrep (rg) for fast text search, ast-grep (sg) for AST pattern matching, and CodeGraph 1.6.2 (Windows) for caller/callee/impact tracing.  
> * **CLI Scripts:**  
  * rmr-web (rmr.web\_cli:main): Starts the server and ngrok tunnel from the terminal.  
  * rmr-desktop (rmr.desktop\_ui:main): Launches the Windows Tkinter GUI (also runnable via start\_rmr.vbs).  
  * rmr-server (rmr.app:main): Runs the MCP server directly.

### **Testing & Current State**

> * **Tests (tests/):** 31 test files covering core security invariants (test\_security\_invariants.py, test\_path\_guard.py), keyed MCP auth (test\_keyed\_mcp.py), all inspectors, tools, memory modules, CLI, and desktop UI/runtime.  
> * **Working Tree:** Located at E:/Tools/rmr \- Copy, currently marked dirty (is\_dirty: true, head commit 0000000).

Would you like to dive into a specific subsystem—such as the security/auth model, the CodeGraph bridge, or the memory layer?

---

*User prompt: @Rmr Tell me all about tools and what they do*

Response: RMR organizes its MCP tools into a **3-tier read-only analysis workflow** (get\_project\_overview, investigate\_codebase, inspect\_evidence), alongside a local health-check tool (ping) and two **memory tools** (search\_knowledge, save\_memory\_entry) implemented in src/rmr/tools/memory\_tools.py that are currently not registered by default in app.py.

| Tool | Tier | Access | Registered In | Primary Role |
| :---- | :---- | :---- | :---- | :---- |
| **get\_project\_overview** | Tier 1 | Read-only | Local & Session apps | Orient on project structure, entry docs, ADRs, and Git state |
| **investigate\_codebase** | Tier 2 | Read-only | Local & Session apps | Search, outline, trace symbols, and analyze call/impact graphs |
| **inspect\_evidence** | Tier 3 | Read-only | Local & Session apps | Extract exact code lines, symbols, or Git diffs with SHA-256 hashes |
| **ping** | Utility | Read-only | Local app only (create\_app) | Server readiness check ({"status": "ok", "system": "RMR Read-Only MCP"}) |
| **search\_knowledge** | Tier 4 | Read-only | *Unregistered* (register\_memory\_tools) | Query project and global Markdown knowledge entries |
| **save\_memory\_entry** | Tier 5 | Write | *Unregistered* (register\_memory\_tools) | Save draft knowledge, ADR, or review records (status="proposed") |

### **Tier 1: get\_project\_overview (src/rmr/tools/overview\_tool.py)**

Provides high-level repository orientation before digging into individual files.

> * **Parameters:** project\_id (optional), subpath (optional).  
> * **Behavior:**  
  * **Without project\_id:** Lists all registered projects with their project\_id, root\_path, language, and entry\_docs.  
  * **With project\_id:** Returns the working tree state (tree\_state), up to **200** project file paths (optionally scoped by subpath, with a files\_truncated flag), previews of entry\_docs (up to **40 lines / 4 KB** per file, or .md listings for directories), and parsed frontmatter summaries of any ADRs under .rmr/adr/\*.md.

### **Tier 2: investigate\_codebase (src/rmr/tools/investigate\_tool.py)**

Locates code structures, references, and call relationships across seven modes. Every call enforces path-traversal guards (resolve\_read\_path), normalizes file paths relative to the project root, and returns tree\_state.

> * **Parameters:** project\_id, mode, query (default ""), subpath (optional), max\_results (default 30), depth (1–5, default 2, used by impact).  
> * **Modes:**  
  * **outline:** Extracts class/function signatures and docstrings via AST parsing (ast\_outline.py).  
  * **symbol:** Resolves a symbol's definition and references using **Jedi** (symbol\_jedi.py). Requires query.  
  * **grep:** Runs text/regex search using **ripgrep** (rg) with a Python fallback (search\_cli.py). Requires query.  
  * **ast:** Runs structural syntax-pattern matching via **ast-grep** (sg) using the project's configured language. Requires query.  
  * **callers / callees:** Traces direct call relationships for a symbol via a local **CodeGraph** index (codegraph.py). Use subpath to disambiguate duplicate symbol names.  
  * **impact:** Computes affected dependents up to depth hops (1–5) in a CodeGraph-indexed repo.

### **Tier 3: inspect\_evidence (src/rmr/tools/evidence\_tool.py)**

Extracts verifiable source snippets and diffs, attaching a **SHA-256 hash** (content\_sha256) and current tree\_state to every response.

> * **Parameters:** project\_id, targets (non-empty list of EvidenceTarget objects).  
> * **Target Kinds:**  
  * **file\_lines:** Reads a specific 1-indexed line range (start\_line to end\_line) from path.  
  * **symbol:** Extracts the full source body of a named class or function (symbol\_name) in path.  
  * **git\_diff:** Computes a Git diff for path between base\_ref (default HEAD) and target\_ref (default WORKTREE), truncated at **200 lines / 8 KB** while hashing the raw diff.

### **Utility & Memory Tools**

> * **ping (src/rmr/app.py):** Registered only when running the local create\_app() server (omitted from single-repo web/desktop sessions created by create\_session\_app()). Returns a static health JSON payload.  
> * **search\_knowledge (src/rmr/tools/memory\_tools.py):** Searches both project-local memory and global-knowledge/ (or RMR\_GLOBAL\_KNOWLEDGE\_DIR), supporting filters for query, category, status, include\_history, and max\_results (default 50). Flags any status="proposed" entry with a review warning.  
> * **save\_memory\_entry (src/rmr/tools/memory\_tools.py):** The only write tool in the codebase (readOnlyHint: False). Persists a new Markdown entry (knowledge, ADR, or review record) scoped to either project or global memory, always forced to status="proposed" so it requires manual review and a Git commit before becoming verified.

---

