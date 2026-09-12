"""
MCP client for connecting to the BensPDF MCP server.

Spawns the server as a local subprocess and talks to it over stdio, so
PDF processing stays on this machine.
"""

import shlex
import sys
from types import TracebackType
from typing import List, Dict, Any, Optional, Sequence, Type, Union
from mcp import ClientSession
from mcp.client.stdio import stdio_client, StdioServerParameters


class MCPToolError(RuntimeError):
    """Raised when the server reports that a tool call failed."""


class MCPClient:
    """Simple MCP client that connects to MCP servers."""

    def __init__(self, server_command: Optional[Union[str, Sequence[str]]] = None):
        """
        Initialize MCP client.

        Args:
            server_command: Command to start the MCP server, as a list of
                arguments. A string is accepted too and split like a shell
                would. Defaults to running the server with this same
                interpreter.
        """
        if server_command is None:
            # Reuse the interpreter running this client, so the server is
            # guaranteed to have the same dependencies installed. Kept as a
            # list because sys.executable may contain spaces.
            server_command = [sys.executable, "-m", "benspdf.mcp_server"]
        elif isinstance(server_command, str):
            server_command = shlex.split(server_command)

        self.server_command = list(server_command)
        if not self.server_command:
            raise ValueError("server_command must not be empty")
        self.session: Optional[ClientSession] = None
        self.tools: List[Any] = []
        self._read = None
        self._write = None
        self._server_ctx = None
        self._session_ctx = None

    async def __aenter__(self):
        """Async context manager entry."""
        await self.connect()
        return self

    async def __aexit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc_val: Optional[BaseException],
        exc_tb: Optional[TracebackType],
    ) -> None:
        """
        Async context manager exit.

        The exception is deliberately not forwarded to the streams and session
        underneath. `stdio_client` is an @asynccontextmanager wrapping an anyio
        task group, so throwing the exception in at its yield point makes the
        group re-raise it as an ExceptionGroup, hiding the original error.
        Returning None here lets the real exception propagate untouched.
        """
        await self.close()

    async def connect(self):
        """Connect to the MCP server."""
        # Create server parameters
        server_params = StdioServerParameters(
            command=self.server_command[0],
            args=self.server_command[1:],
        )

        # Start server and connect (keep contexts alive)
        self._server_ctx = stdio_client(server_params)
        self._read, self._write = await self._server_ctx.__aenter__()

        self._session_ctx = ClientSession(self._read, self._write)
        self.session = await self._session_ctx.__aenter__()

        await self.session.initialize()

        # Get available tools
        tools_result = await self.session.list_tools()
        self.tools = (
            tools_result if isinstance(tools_result, list) else tools_result.tools
        )

        print("✓ Connected to MCP server")
        print(f"✓ Found {len(self.tools)} tools:")
        for tool in self.tools:
            print(f"  - {tool.name}")

    async def close(self) -> None:
        """
        Close the session and shut the server subprocess down.

        Safe to call more than once, and safe to call on a client that was
        never connected.
        """
        # Detach first, so a failed teardown can't leave a half-closed context
        # behind for a later call to trip over.
        session_ctx, self._session_ctx = self._session_ctx, None
        server_ctx, self._server_ctx = self._server_ctx, None
        self.session = None
        self._read = None
        self._write = None

        try:
            if session_ctx is not None:
                await session_ctx.__aexit__(None, None, None)
        finally:
            # Runs even if the session teardown raised, so the subprocess
            # never outlives the client.
            if server_ctx is not None:
                await server_ctx.__aexit__(None, None, None)

    async def call_tool(
        self, tool_name: str, arguments: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Call a tool on the MCP server.

        Args:
            tool_name: Name of the tool to call
            arguments: Tool arguments

        Returns:
            Tool result
        """
        if not self.session:
            raise RuntimeError("Not connected to MCP server")

        result = await self.session.call_tool(tool_name, arguments)

        # A failed call carries no structured payload, just a message saying
        # what went wrong (unknown tool, bad arguments, an exception in the
        # tool). Surface it instead of returning something empty.
        if getattr(result, "is_error", False) or getattr(result, "isError", False):
            raise MCPToolError(
                f"{tool_name} failed: {_result_text(result) or 'no details given'}"
            )

        # snake_case on current versions, camelCase on older ones.
        structured = getattr(result, "structured_content", None)
        if structured is None:
            structured = getattr(result, "structuredContent", None)

        if not isinstance(structured, dict):
            raise MCPToolError(
                f"{tool_name} returned no structured result "
                f"(got {type(structured).__name__}). "
                f"Server said: {_result_text(result) or '<nothing>'}"
            )

        # Tools wrap their payload under "result"; tolerate ones that don't.
        payload = structured.get("result", structured)
        return payload if isinstance(payload, dict) else {"result": payload}


def _result_text(result: Any) -> str:
    """Pull the human-readable text out of a tool result, for error messages."""
    parts = []
    for block in getattr(result, "content", None) or []:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    return "\n".join(parts).strip()
