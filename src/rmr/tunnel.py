"""Protect the MCP route used by a Desktop session."""

from secrets import compare_digest

import uvicorn
from fastmcp import FastMCP
from starlette.responses import Response
from starlette.types import ASGIApp, Receive, Scope, Send

from rmr.desktop_config import build_mcp_path

LOOPBACK = "127.0.0.1"


def build_ngrok_policy(mcp_path: str) -> dict:
    """Reject requests outside the exact keyed MCP route at ngrok."""
    if build_mcp_path(mcp_path.rpartition("/")[2]) != mcp_path:
        raise ValueError("Expected a canonical keyed MCP path.")
    return {
        "on_http_request": [
            {
                "expressions": [f"req.url.raw_path != '{mcp_path}'"],
                "actions": [
                    {
                        "type": "custom-response",
                        "config": {"status_code": 404, "body": "Not Found"},
                    }
                ],
            }
        ]
    }


class _ExactMcpPath:
    """Guard the original URL bytes before Starlette routing and redirects."""

    def __init__(self, app: ASGIApp, mcp_path: str) -> None:
        self.app = app
        self.raw_path = mcp_path.encode("ascii")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and not compare_digest(
            scope.get("raw_path", b""), self.raw_path
        ):
            await Response("Not Found", status_code=404)(scope, receive, send)
            return
        await self.app(scope, receive, send)


def run_keyed_session(
    app: FastMCP, *, port: int, mcp_path: str, public_domain: str | None
) -> None:
    """Run the scoped FastMCP app without logging its credential-bearing URL."""
    allowed_hosts = [LOOPBACK, public_domain] if public_domain else [LOOPBACK]
    allowed_origins = [f"https://{public_domain}"] if public_domain else []
    http_app = app.http_app(
        path=mcp_path,
        transport="streamable-http",
        host_origin_protection=True,
        allowed_hosts=allowed_hosts,
        allowed_origins=allowed_origins,
    )
    http_app.debug = False
    # FastMCP.run logs the full endpoint URL. Serve its ASGI app directly.
    uvicorn.run(
        _ExactMcpPath(http_app, mcp_path),
        host=LOOPBACK,
        port=port,
        access_log=False,
        lifespan="on",
        timeout_graceful_shutdown=2,
        ws="none",
    )
