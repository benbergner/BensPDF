"""
BensPDF - Local PDF tools for AI agents

Framework-agnostic PDF processing tools that work with MCP protocol.
"""

from . import core
from .pdf_tools import PDFPageCounterTool, create_tool
from .utils import create_test_pdf, create_test_pdf_bytes

__all__ = [
    # Tools
    "PDFPageCounterTool",
    "create_tool",
    # Utilities
    "create_test_pdf",
    "create_test_pdf_bytes",
    # Shared artifact layer
    "core",
]
