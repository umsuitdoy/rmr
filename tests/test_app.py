"""Unit tests for RMR FastMCP Application and Entrypoint (TASK-05)."""

import asyncio
import os
import unittest
from unittest.mock import MagicMock, patch

from rmr.app import (
    SERVER_INSTRUCTIONS,
    build_parser,
    create_app,
    main,
)


class TestApp(unittest.TestCase):
    def test_create_app_initialization(self):
        app = create_app()
        self.assertEqual(app.name, "rmr")
        self.assertEqual(app.instructions, SERVER_INSTRUCTIONS)
        self.assertIn("RMR", app.instructions)
        self.assertIn("Tier 1 (Orientation): Call get_project_overview", app.instructions)
        self.assertIn("Tier 2 (Investigation): Use investigate_codebase", app.instructions)
        self.assertIn("Tier 3 (Evidence): Call inspect_evidence", app.instructions)

    def test_ping_tool_registered_and_executable(self):
        app = create_app()

        async def _verify_tool():
            tools = await app.list_tools()
            tool_names = [t.name for t in tools]
            self.assertIn("ping", tool_names)

            ping_tool = next(t for t in tools if t.name == "ping")
            self.assertIs(getattr(ping_tool.annotations, "read_only_hint", getattr(ping_tool.annotations, "readOnlyHint", None)), True)
            self.assertIs(getattr(ping_tool.annotations, "destructive_hint", getattr(ping_tool.annotations, "destructiveHint", None)), False)
            self.assertIs(getattr(ping_tool.annotations, "idempotent_hint", getattr(ping_tool.annotations, "idempotentHint", None)), True)
            self.assertIs(getattr(ping_tool.annotations, "open_world_hint", getattr(ping_tool.annotations, "openWorldHint", None)), False)

            result = await app.call_tool("ping")
            is_err = getattr(result, "is_error", getattr(result, "isError", False))
            self.assertFalse(is_err)
            self.assertIn('"status"', result.content[0].text)
            self.assertIn('"ok"', result.content[0].text)
            self.assertIn('"RMR Read-Only MCP"', result.content[0].text)

        asyncio.run(_verify_tool())

    def test_rmr_tools_registered_in_app(self):
        app = create_app()

        async def _verify():
            tools = await app.list_tools()
            tool_names = {t.name for t in tools}
            expected_tools = {
                "ping",
                "get_project_overview",
                "investigate_codebase",
                "inspect_evidence",
            }
            self.assertEqual(tool_names, expected_tools)

            tool_by_name = {t.name: t for t in tools}

            for t in tool_by_name.values():
                self.assertIs(getattr(t.annotations, "read_only_hint", getattr(t.annotations, "readOnlyHint", None)), True)
                self.assertIs(getattr(t.annotations, "destructive_hint", getattr(t.annotations, "destructiveHint", None)), False)
                self.assertIs(getattr(t.annotations, "idempotent_hint", getattr(t.annotations, "idempotentHint", None)), True)
                self.assertIs(getattr(t.annotations, "open_world_hint", getattr(t.annotations, "openWorldHint", None)), False)

        asyncio.run(_verify())

    def test_build_parser_defaults(self):
        with patch.dict(os.environ, {}, clear=True):
            parser = build_parser()
            args = parser.parse_args([])
            self.assertEqual(args.transport, "stdio")
            self.assertEqual(args.host, "127.0.0.1")
            self.assertEqual(args.port, 8000)
            self.assertEqual(args.path, "/mcp/v1")

    def test_build_parser_custom_args(self):
        parser = build_parser()
        args = parser.parse_args(
            [
                "--transport",
                "streamable-http",
                "--host",
                "0.0.0.0",
                "--port",
                "9090",
                "--path",
                "/custom/mcp",
            ]
        )
        self.assertEqual(args.transport, "streamable-http")
        self.assertEqual(args.host, "0.0.0.0")
        self.assertEqual(args.port, 9090)
        self.assertEqual(args.path, "/custom/mcp")

    def test_build_parser_env_variables(self):
        env_vars = {
            "RMR_TRANSPORT": "sse",
            "RMR_HOST": "192.168.1.10",
            "RMR_PORT": "7000",
            "RMR_PATH": "/sse/endpoint",
        }
        with patch.dict(os.environ, env_vars, clear=True):
            parser = build_parser()
            args = parser.parse_args([])
            self.assertEqual(args.transport, "sse")
            self.assertEqual(args.host, "192.168.1.10")
            self.assertEqual(args.port, 7000)
            self.assertEqual(args.path, "/sse/endpoint")

    def test_build_parser_invalid_port_fallback(self):
        with patch.dict(os.environ, {"RMR_PORT": "not-an-int"}, clear=True):
            parser = build_parser()
            args = parser.parse_args([])
            self.assertEqual(args.port, 8000)

    @patch("rmr.app.create_app")
    def test_main_runs_stdio(self, mock_create_app):
        mock_app = MagicMock()
        mock_create_app.return_value = mock_app

        main(["--transport", "stdio"])

        mock_create_app.assert_called_once()
        mock_app.run.assert_called_once_with(transport="stdio")

    @patch("rmr.app.create_app")
    def test_main_runs_http_transports(self, mock_create_app):
        mock_app = MagicMock()
        mock_create_app.return_value = mock_app

        main(
            [
                "--transport",
                "streamable-http",
                "--host",
                "0.0.0.0",
                "--port",
                "8080",
                "--path",
                "/mcp/v1",
            ]
        )

        mock_create_app.assert_called_once()
        mock_app.run.assert_called_once_with(
            transport="streamable-http",
            host="0.0.0.0",
            port=8080,
            path="/mcp/v1",
        )

    @patch("rmr.app.create_app")
    def test_main_handles_keyboard_interrupt(self, mock_create_app):
        mock_app = MagicMock()
        mock_app.run.side_effect = KeyboardInterrupt()
        mock_create_app.return_value = mock_app

        # Should exit gracefully without re-raising KeyboardInterrupt
        main(["--transport", "stdio"])
        mock_app.run.assert_called_once_with(transport="stdio")

    @patch("rmr.app.create_app")
    def test_main_handles_cancelled_error(self, mock_create_app):
        mock_app = MagicMock()
        mock_app.run.side_effect = asyncio.CancelledError()
        mock_create_app.return_value = mock_app

        # Should exit gracefully without re-raising CancelledError
        main(["--transport", "streamable-http"])
        mock_app.run.assert_called_once()

    @patch("rmr.app.create_app")
    def test_main_handles_exception_group_keyboard_interrupt(self, mock_create_app):
        mock_app = MagicMock()
        mock_app.run.side_effect = BaseExceptionGroup(
            "unhandled", [KeyboardInterrupt()]
        )
        mock_create_app.return_value = mock_app

        main(["--transport", "stdio"])
        mock_app.run.assert_called_once()

    @patch("rmr.app.create_app")
    def test_main_raises_unhandled_exception_group(self, mock_create_app):
        mock_app = MagicMock()
        mock_app.run.side_effect = BaseExceptionGroup(
            "unhandled", [RuntimeError("fatal error")]
        )
        mock_create_app.return_value = mock_app

        with self.assertRaises(BaseExceptionGroup):
            main(["--transport", "stdio"])


if __name__ == "__main__":
    unittest.main()
