"""
BensPDF - Local PDF tools for AI agents

Framework-agnostic PDF processing tools that work with MCP protocol.
"""

from .pdf_tools import PDFPageCounterTool, create_tool
from .utils import create_test_pdf

__all__ = [
    # Tools
    "PDFPageCounterTool",
    "create_tool",
    # Utilities
    "create_test_pdf",
]
