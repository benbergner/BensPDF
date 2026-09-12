"""
Shared test fixtures.

Every test gets its own throwaway workspace so the suite never reads or writes
the real one at ~/.benstools/work.
"""

from typing import Any, Dict

import pytest

from benscore.store import WORKSPACE_ENV_VAR


@pytest.fixture(autouse=True)
def isolated_workspace(tmp_path, monkeypatch):
    """Point the artifact workspace at a temp directory for the duration of a test."""
    workspace = tmp_path / "workspace"
    monkeypatch.setenv(WORKSPACE_ENV_VAR, str(workspace))
    return workspace


@pytest.fixture
def call_tool():
    """Call an MCP tool and return its result payload.

    Hides the difference between MCP API versions, which return either a
    (content, structured) tuple or an object with structured_content.
    """

    async def _call(mcp, name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        result = await mcp.call_tool(name, args)
        if isinstance(result, tuple):
            _content, structured = result
            return structured["result"]
        return result.structured_content["result"]

    return _call
