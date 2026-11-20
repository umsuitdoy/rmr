"""Read call relationships from a CodeGraph index in the selected repo."""

import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from time import monotonic
from typing import Literal

from pydantic import BaseModel, ValidationError

from rmr.core.path_guard import resolve_project_file, resolve_read_path

_BRIDGE = Path(__file__).with_name("codegraph_bridge.cjs").resolve()
_TIMEOUT = 10
_MAX_OUTPUT_BYTES = 256 * 1024


class _Location(BaseModel, strict=True):
    name: str
    kind: str
    file: str
    line: int


class _RelatedLocation(_Location):
    relation: Literal["calls", "instantiates"]
    call_line: int | None


class _Definition(BaseModel, strict=True):
    definition: _Location
    related: list[_RelatedLocation]


class _ImpactHop(_Location):
    relation: str | None = None


class _Affected(_Location):
    depth: int
    path: list[_ImpactHop]


class _ImpactDefinition(BaseModel, strict=True):
    definition: _Location
    affected: list[_Affected]


class _ImpactTrace(BaseModel, strict=True):
    status: Literal["ok", "not_found", "unsupported_version"]
    definitions: list[_ImpactDefinition] = []
    ambiguous: bool = False
    truncated: bool = False


class _Trace(BaseModel, strict=True):
    status: Literal["ok", "not_found", "unsupported_version"]
    definitions: list[_Definition] = []
    truncated: bool = False


def _index_path(root: Path) -> Path:
    database = root / ".codegraph" / "codegraph.db"
    if resolve_project_file(root, database) != database:
        raise ValueError("CodeGraph requires a local .codegraph/codegraph.db in this repository.")
    # SQLite can open sidecar files as part of a read-only WAL connection.
    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = database.with_name(database.name + suffix)
        try:
            resolved = resolve_read_path(root, sidecar, allow_missing=True)
        except (OSError, RuntimeError, ValueError):
            raise ValueError("CodeGraph index sidecars must remain in the repository.") from None
        if resolved != sidecar:
            raise ValueError("CodeGraph index sidecars cannot be redirected.")
    return database


def _read_bridge(args: list[str], install: Path) -> bytes:
    env = {
        **os.environ, "CODEGRAPH_DIR": ".codegraph", "CODEGRAPH_TELEMETRY": "0",
        "NODE_OPTIONS": "", "NODE_PATH": "",
    }
    deadline = monotonic() + _TIMEOUT
    with subprocess.Popen(
        args, cwd=install, env=env, shell=False, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    ) as process, ThreadPoolExecutor(max_workers=1) as reader:
        assert process.stdout is not None
        pending = reader.submit(process.stdout.read, _MAX_OUTPUT_BYTES + 1)
        try:
            output = pending.result(timeout=max(0, deadline - monotonic()))
            if len(output) > _MAX_OUTPUT_BYTES:
                raise ValueError("CodeGraph exceeded the output limit. Reduce max_results.")
            if process.wait(timeout=max(0, deadline - monotonic())):
                raise ValueError("CodeGraph could not query the repository index.")
            return output
        except (TimeoutError, subprocess.TimeoutExpired):
            raise ValueError("CodeGraph query timed out.") from None
        finally:
            if process.poll() is None:
                process.kill()


def _scoped_location(root: Path, item: _Location) -> Path | None:
    if not item.file or item.line < 1:
        return None
    return resolve_project_file(root, Path(item.file))


def _query(
    root: Path,
    mode: Literal["callers", "callees", "impact"],
    symbol: str,
    definition_file: Path | None,
    max_results: int,
    depth: int = 2,
) -> _Trace | _ImpactTrace:
    if not symbol or len(symbol) > 512 or "\0" in symbol:
        raise ValueError("CodeGraph requires a symbol name of 1 to 512 characters.")
    if definition_file is not None and not definition_file.is_file():
        raise ValueError("subpath must name a definition file for callers, callees, or impact.")

    database = _index_path(root)
    local_app_data = Path(os.environ.get("LOCALAPPDATA", ""))
    if os.name != "nt" or not local_app_data.is_absolute():
        raise ValueError("CodeGraph tracing requires the official Windows CodeGraph installation.")
    install = local_app_data / "codegraph" / "current"
    node = install / "node.exe"
    if not node.is_file() or not (install / "lib" / "package.json").is_file():
        raise ValueError("Install CodeGraph 1.6.2 with the official Windows installer to use call tracing.")

    file_arg = definition_file.relative_to(root).as_posix() if definition_file is not None else ""
    args = [
        str(node), "--liftoff-only", "--disable-warning=ExperimentalWarning", str(_BRIDGE),
        str(install), str(root), str(database), mode, symbol, file_arg, str(max_results), str(depth),
    ]
    try:
        trace = (_ImpactTrace if mode == "impact" else _Trace).model_validate_json(_read_bridge(args, install))
    except (OSError, ValidationError):
        raise ValueError("CodeGraph could not return a compatible response.") from None
    if trace.status == "unsupported_version":
        raise ValueError("CodeGraph tracing requires version 1.6.2.")

    return trace


def trace_calls(root: Path, mode: Literal["callers", "callees"], symbol: str,
                definition_file: Path | None, max_results: int) -> dict:
    """Run one direct call query and expose only validated locations."""
    trace = _query(root, mode, symbol, definition_file, max_results)
    definitions = []
    for group in trace.definitions:
        file = _scoped_location(root, group.definition)
        if file is None or (definition_file is not None and file != definition_file):
            continue
        definition = group.definition.model_dump()
        definition["file"] = file.relative_to(root).as_posix()
        related = []
        for item in group.related:
            related_file = _scoped_location(root, item)
            if related_file is not None:
                related.append({**item.model_dump(), "file": related_file.relative_to(root).as_posix()})
        definitions.append({"definition": definition, mode: related})

    result = {"symbol": symbol, "definitions": definitions, "truncated": trace.truncated}
    if not definitions:
        result["message"] = "No matching symbol definition in the requested scope."
    return result


def trace_impact(root: Path, symbol: str, definition_file: Path | None,
                 max_results: int, depth: int) -> dict:
    """Return bounded impact chains only when every location is inside the project."""
    trace = _query(root, "impact", symbol, definition_file, max_results, depth)
    definitions = []
    for group in trace.definitions:
        file = _scoped_location(root, group.definition)
        if file is None or (definition_file is not None and file != definition_file):
            continue
        definition = {**group.definition.model_dump(), "file": file.relative_to(root).as_posix()}
        affected = []
        for item in group.affected:
            item_file = _scoped_location(root, item)
            hops = []
            for hop in item.path:
                hop_file = _scoped_location(root, hop)
                if hop_file is None:
                    break
                hops.append({**hop.model_dump(exclude_none=True), "file": hop_file.relative_to(root).as_posix()})
            if item_file is not None and len(hops) == len(item.path):
                affected.append({**item.model_dump(exclude={"path"}), "file": item_file.relative_to(root).as_posix(), "path": hops})
        definitions.append({"definition": definition, "affected": affected})
    result = {"symbol": symbol, "depth": depth, "ambiguous": trace.ambiguous,
              "truncated": trace.truncated, "definitions": definitions}
    if not definitions:
        result["message"] = "No matching symbol definition in the requested scope."
    return result
