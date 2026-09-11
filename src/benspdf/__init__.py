"""
BensPDF - Local PDF tools for AI agents

Framework-agnostic PDF processing tools that work with MCP protocol.
"""

from . import core
from .tools.check_text import check_text
from .tools.create_test_pdf import create_test_pdf, create_test_pdf_bytes
from .tools.metadata import read_metadata
from .tools.page_count import PDFPageCounterTool, create_tool

__all__ = [
    # Tools, one module each under benspdf.tools
    "PDFPageCounterTool",
    "create_tool",
    "read_metadata",
    "check_text",
    "create_test_pdf",
    "create_test_pdf_bytes",
    # Shared artifact layer
    "core",
]
