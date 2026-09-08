"""
MCP Server for BensPDF

Exposes PDF tools via the Model Context Protocol (MCP).
Works with Claude Desktop, OpenAI, Cursor, and any MCP-compatible client.
"""

from importlib.metadata import PackageNotFoundError, version as _pkg_version
from typing import Dict, Any
from mcp.server import MCPServer
from benspdf import PDFPageCounterTool, create_test_pdf

try:
    _VERSION = _pkg_version("benspdf-mcp")
except PackageNotFoundError:  # running from a source checkout
    _VERSION = "0.0.0.dev0"

# Create MCP server
mcp = MCPServer("benspdf", version=_VERSION)

# Initialize the tool
pdf_counter = PDFPageCounterTool()


@mcp.tool()
def count_pdf_pages(pdf_path: str) -> Dict[str, Any]:
    """
    Count the number of pages in a PDF document.
    
    Use this tool when the user asks about the number of pages in a PDF file.
    The tool returns the exact page count and file information.
    
    Args:
        pdf_path: Path to the PDF file (absolute or relative path, ~ is expanded)
    
    Returns:
        Dictionary with:
        - page_count: Number of pages in the PDF
        - file_name: Name of the PDF file
        - file_path: Absolute path to the file
        - success: True if successful
        - error: Error message if failed
    """
    return pdf_counter.count_pages(pdf_path)


@mcp.tool()
def create_test_pdf_file(
    output_path: str,
    num_pages: int = 3,
    title: str = None
) -> Dict[str, Any]:
    """
    Create a test PDF file with specified number of pages.
    
    Args:
        output_path: Path where to save the PDF
        num_pages: Number of pages to create (default: 3)
        title: Optional title for the PDF metadata
    
    Returns:
        Dictionary with the absolute path to the created PDF file
    """
    path = create_test_pdf(output_path, num_pages=num_pages, title=title)
    return {
        "success": True,
        "pdf_path": path,
        "num_pages": num_pages
    }


def main() -> None:
    """Run the MCP server over stdio. Used by the console script."""
    mcp.run()


if __name__ == "__main__":
    main()
