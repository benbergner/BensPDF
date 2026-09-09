"""
MCP Server for BensPDF

Exposes PDF tools via the Model Context Protocol (MCP).
Works with Claude Desktop, OpenAI, Cursor, and any MCP-compatible client.

Tools take a `ref`, which is either a path to one of the user's files or the id
of a temporary workspace artifact produced by an earlier tool. Results stay in
the workspace until `export` is called, so chained operations never litter the
user's folders and nothing is written until they ask for it.
"""

from importlib.metadata import PackageNotFoundError, version as _pkg_version
from typing import Any, Dict, Optional

from mcp.server import MCPServer

from benspdf import PDFPageCounterTool
from benspdf import core
from benspdf.core import tools as core_tools
from benspdf.utils import create_test_pdf_bytes

try:
    _VERSION = _pkg_version("benspdf-mcp")
except PackageNotFoundError:  # running from a source checkout
    _VERSION = "0.0.0.dev0"

# Create MCP server
mcp = MCPServer("benspdf", version=_VERSION)

# Register the shared core tools (export, list_artifacts, discard)
core_tools.register(mcp)

# Initialize the tool
pdf_counter = PDFPageCounterTool()


@mcp.tool()
def count_pdf_pages(pdf_path: str) -> Dict[str, Any]:
    """
    Count the number of pages in a PDF document.

    Use this tool when the user asks about the number of pages in a PDF file.
    The tool returns the exact page count and file information.

    Args:
        pdf_path: Path to a PDF file (absolute or relative, ~ is expanded), or
            the id of a workspace artifact from an earlier tool
            (e.g. "art_a1b2c3d4.pdf")

    Returns:
        Dictionary with:
        - page_count: Number of pages in the PDF
        - file_name: Name of the PDF file
        - file_path: Absolute path to the file
        - success: True if successful
        - error: Error message if failed
    """
    try:
        resolved = core.resolve(pdf_path)
    except core.ArtifactNotFound as exc:
        return core.err(str(exc), file_exists=False)
    except (FileNotFoundError, IsADirectoryError) as exc:
        return core.err(str(exc), file_path=str(pdf_path), file_exists=False)

    return pdf_counter.count_pages(str(resolved))


@mcp.tool()
def create_test_pdf_file(
    output_path: Optional[str] = None,
    num_pages: int = 3,
    title: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Create a test PDF, handy for trying the other tools without hunting for a file.

    By default the PDF is created as a temporary workspace artifact and its id is
    returned, so it can be passed straight to another tool. Pass `output_path`
    only if the user wants the file saved to a specific location.

    Args:
        output_path: Optional path to also save the PDF to. Omit to keep it as a
            temporary artifact.
        num_pages: Number of pages to create (default: 3)
        title: Optional title for the PDF metadata

    Returns:
        Dictionary with:
        - artifact: Workspace artifact id for the new PDF
        - num_pages: Number of pages created
        - pdf_path: Absolute path, only present if output_path was given
    """
    try:
        data = create_test_pdf_bytes(num_pages=num_pages, title=title)
        artifact = core.save(data, ".pdf")
    except (OSError, ValueError) as exc:
        return core.err(f"Could not create the test PDF: {exc}")

    result: Dict[str, Any] = {"artifact": artifact, "num_pages": num_pages}

    if output_path:
        try:
            result["pdf_path"] = str(
                core.export_artifact(artifact, output_path, overwrite=True)
            )
        except OSError as exc:
            return core.err(f"Could not write to {output_path}: {exc}", **result)

    return core.ok(**result)


def main() -> None:
    """Run the MCP server over stdio. Used by the console script."""
    # Clear out expired and over-cap artifacts once at boot. Cheaper and simpler
    # than a background reaper, and good enough for a per-user workspace.
    try:
        core.prune()
    except OSError:
        pass  # housekeeping must never stop the server from starting

    mcp.run()


if __name__ == "__main__":
    main()
