"""
BensPDF - Local PDF tools for AI agents

Framework-agnostic PDF processing tools that work with MCP protocol.

The shared artifact layer these verbs sit on is ``benscore``, imported from
there rather than re-exported here: it is domain-neutral by design and does not
belong to the PDF package.
"""

from .tools.check_access import check_access
from .tools.check_text import check_text
from .tools.create_test_pdf import create_test_pdf, create_test_pdf_bytes
from .tools.metadata import read_metadata
from .tools.page_count import PDFPageCounterTool, create_tool
from .tools.page_layout import read_page_layout
from .tools.render_pages import render_pages

__all__ = [
    # Tools, one module each under benspdf.tools
    "PDFPageCounterTool",
    "create_tool",
    "read_metadata",
    "check_text",
    "check_access",
    "read_page_layout",
    "render_pages",
    "create_test_pdf",
    "create_test_pdf_bytes",
]
