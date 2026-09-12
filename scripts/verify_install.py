#!/usr/bin/env python
"""Check that an installed benspdf-mcp actually serves MCP.

Run this against a real installation - from a local wheel, from TestPyPI, from
PyPI - rather than against the source tree. Passing tests in a checkout say
nothing about whether the built artifact declares its entry point, ships every
module it imports, or reports its own version, and each of those has broken a
release for somebody.

    python scripts/verify_install.py benspdf-mcp
    python scripts/verify_install.py /path/to/venv/bin/benspdf-mcp

It speaks the protocol the way a client does: spawn the command, complete the
handshake, list the tools, then chain two calls so the artifact contract is
exercised end to end. The workspace is redirected to a temp directory, so a run
leaves nothing behind in ~/.benstools/work.

Requires the `mcp` client library, which the package depends on anyway, so any
environment with benspdf-mcp installed can run this.
"""

import asyncio
import os
import shutil
import sys
import tempfile

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

#: Tools the server is expected to expose. A release that silently loses one -
#: a missing import, a module left out of the wheel - still starts and still
#: answers, which is exactly why this is pinned rather than counted.
EXPECTED_TOOLS = {
    "create_test_pdf_file",
    "discard",
    "export",
    "list_artifacts",
    "pdf_check_access",
    "pdf_check_text",
    "pdf_metadata",
    "pdf_page_count",
    "pdf_page_layout",
    "pdf_render_pages",
}

HANDSHAKE_TIMEOUT = 60
CALL_TIMEOUT = 120


def _payload(result):
    """Pull a tool's dict out of a CallToolResult across MCP versions."""
    structured = getattr(result, "structured_content", None) or getattr(
        result, "structuredContent", None
    )
    if structured is None:
        raise AssertionError(f"tool returned no structured content: {result!r}")
    return structured.get("result", structured)


async def verify(command: str, workspace: str) -> None:
    params = StdioServerParameters(
        command=command,
        args=[],
        env=dict(os.environ, BENSTOOLS_WORKSPACE=workspace),
    )

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init = await asyncio.wait_for(session.initialize(), HANDSHAKE_TIMEOUT)
            name = init.server_info.name
            version = init.server_info.version
            print(f"handshake: {name} {version}")

            # The dev fallback in mcp_server.py. Seeing it here means the
            # distribution metadata is missing, so the package is not really
            # installed - importable from a source directory, most likely.
            if version.startswith("0.0.0"):
                raise AssertionError(
                    f"server reports {version}, the source-checkout fallback. "
                    f"The installed distribution has no metadata."
                )

            if not init.instructions:
                raise AssertionError("server sent no instructions block")
            print(f"instructions: {len(init.instructions)} chars")

            listed = await asyncio.wait_for(session.list_tools(), CALL_TIMEOUT)
            found = {tool.name for tool in listed.tools}
            missing = EXPECTED_TOOLS - found
            extra = found - EXPECTED_TOOLS
            if missing:
                raise AssertionError(f"missing tools: {sorted(missing)}")
            print(
                f"tools: {len(found)}" + (f" (+new: {sorted(extra)})" if extra else "")
            )

            made = _payload(
                await asyncio.wait_for(
                    session.call_tool(
                        "create_test_pdf_file",
                        {"num_pages": 3, "title": "verify_install"},
                    ),
                    CALL_TIMEOUT,
                )
            )
            artifact = made["artifact"]
            print(f"create_test_pdf_file: {artifact}")

            # Passing the id back in is the whole artifact contract: the second
            # verb has to resolve an id it did not create.
            counted = _payload(
                await asyncio.wait_for(
                    session.call_tool("pdf_page_count", {"ref": artifact}),
                    CALL_TIMEOUT,
                )
            )
            if counted.get("page_count") != 3:
                raise AssertionError(f"expected 3 pages, got {counted!r}")
            print(f"pdf_page_count({artifact}): 3")

            # Failures have to come back as data. A tool that raises instead
            # leaves the model with a protocol error and nothing to act on.
            missing_file = _payload(
                await asyncio.wait_for(
                    session.call_tool("pdf_page_count", {"ref": "/no/such.pdf"}),
                    CALL_TIMEOUT,
                )
            )
            if not missing_file.get("error"):
                raise AssertionError(
                    f"a missing file should report an error, got {missing_file!r}"
                )
            print(f"error path: {missing_file['error']!r}")


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2

    command = sys.argv[1]
    resolved = shutil.which(command) or command
    if not os.path.exists(resolved):
        print(f"not found: {command}", file=sys.stderr)
        return 2

    workspace = tempfile.mkdtemp(prefix="benspdf-verify-")
    try:
        asyncio.run(verify(resolved, workspace))
    except Exception as exc:
        print(f"\nFAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        shutil.rmtree(workspace, ignore_errors=True)

    print("\nOK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
