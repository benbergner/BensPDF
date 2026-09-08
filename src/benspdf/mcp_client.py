"""
MCP client for connecting to the BensPDF MCP server.

Spawns the server as a local subprocess and talks to it over stdio, so
PDF processing stays on this machine.
"""

import os
import sys
from typing import List, Dict, Any, Optional
from mcp import ClientSession
from mcp.client.stdio import stdio_client, StdioServerParameters


class MCPClient:
    """Simple MCP client that connects to MCP servers."""
    
    def __init__(self, server_command: str = None):
        """
        Initialize MCP client.
        
        Args:
            server_command: Command to start the MCP server (defaults to conda python)
        """
        if server_command is None:
            # Reuse the interpreter running this client, so the server is
            # guaranteed to have the same dependencies installed.
            server_command = f"{sys.executable} -m benspdf.mcp_server"
        
        self.server_command = server_command
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
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.close()
    
    async def connect(self):
        """Connect to the MCP server."""
        # Parse command
        parts = self.server_command.split()
        command = parts[0]
        args = parts[1:] if len(parts) > 1 else []
        
        # Create server parameters
        server_params = StdioServerParameters(
            command=command,
            args=args
        )
        
        # Start server and connect (keep contexts alive)
        self._server_ctx = stdio_client(server_params)
        self._read, self._write = await self._server_ctx.__aenter__()
        
        self._session_ctx = ClientSession(self._read, self._write)
        self.session = await self._session_ctx.__aenter__()
        
        await self.session.initialize()
        
        # Get available tools
        tools_result = await self.session.list_tools()
        self.tools = tools_result if isinstance(tools_result, list) else tools_result.tools
        
        print(f"✓ Connected to MCP server")
        print(f"✓ Found {len(self.tools)} tools:")
        for tool in self.tools:
            print(f"  - {tool.name}")
    
    async def close(self):
        """Close the connection."""
        if self._session_ctx:
            await self._session_ctx.__aexit__(None, None, None)
        if self._server_ctx:
            await self._server_ctx.__aexit__(None, None, None)
    
    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
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
        
        # Handle different MCP API versions
        if isinstance(result, tuple):
            # MCP v2 returns tuple (content, structured)
            content, structured = result
            return structured.get('result', {})
        elif hasattr(result, 'structuredContent'):
            # MCP v1 with camelCase
            return result.structuredContent.get('result', {})
        elif hasattr(result, 'structured_content'):
            # MCP v1 with snake_case
            return result.structured_content.get('result', {})
        else:
            # Fallback
            return {}


MODEL_ENV_VAR = "BENSPDF_MODEL"


def resolve_model(requested: Optional[str], available: List[str]) -> str:
    """
    Pick which Ollama model to use.

    Precedence: the requested model, then $BENSPDF_MODEL, then the first
    model installed locally.

    Args:
        requested: Explicitly requested model, or None
        available: Model names installed locally, as reported by Ollama

    Returns:
        The name of the model to use, including its tag

    Raises:
        ValueError: If nothing is installed, or the requested model isn't
            among the installed ones.
    """
    if not available:
        raise ValueError(
            "No Ollama models installed. Pull one first, e.g.:\n"
            "   ollama pull llama3.1"
        )

    choice = requested or os.environ.get(MODEL_ENV_VAR)
    if choice is None:
        return available[0]

    # Accept both "llama3.1" and the fully tagged "llama3.1:latest".
    for name in available:
        if name == choice or name.split(":")[0] == choice:
            return name

    raise ValueError(
        f"Model {choice!r} is not installed.\n"
        f"   Available: {', '.join(available)}\n"
        f"   Pull it with: ollama pull {choice}"
    )
