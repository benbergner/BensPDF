"""
Counting the pages in a PDF: the implementation behind `pdf_page_count`.

Reads the document's page tree only, so it stays cheap on large files.
Processes PDFs locally - no external APIs needed.
"""

from typing import Dict, Any
from pathlib import Path
from pypdf import PdfReader


class PDFPageCounterTool:
    """
    PDF page counter tool.

    Simple, focused, easy to understand and extend.
    Processes PDFs locally for maximum privacy.
    """

    def __init__(self):
        """Initialize the PDF page counter tool."""
        pass  # No initialization needed - no API keys required

    def __call__(self, pdf_path: str) -> Dict[str, Any]:
        """
        Count pages in a PDF file.

        Args:
            pdf_path: Path to the PDF file (absolute or relative)

        Returns:
            Dict with 'page_count' and metadata

        Example:
            >>> tool = PDFPageCounterTool()
            >>> result = tool("document.pdf")
            >>> print(result["page_count"])
        """
        return self.count_pages(pdf_path)

    def count_pages(self, pdf_path: str) -> Dict[str, Any]:
        """
        Count pages in a PDF file.

        Args:
            pdf_path: Path to the PDF file (absolute or relative)

        Returns:
            Dict with 'page_count', 'file_path', and 'file_exists' keys
        """
        # Resolve path
        path = Path(pdf_path).expanduser().resolve()

        # Check if file exists
        if not path.exists():
            return {
                "error": f"File not found: {pdf_path}",
                "file_path": str(path),
                "file_exists": False,
            }

        # Check if it's a PDF
        if not str(path).lower().endswith(".pdf"):
            return {
                "error": f"Not a PDF file: {pdf_path}",
                "file_path": str(path),
                "file_exists": True,
            }

        try:
            # Read PDF and count pages
            reader = PdfReader(str(path))
            page_count = len(reader.pages)

            return {
                "page_count": page_count,
                "file_path": str(path),
                "file_name": path.name,
                "file_exists": True,
                "success": True,
            }

        except Exception as e:
            return {
                "error": f"Failed to read PDF: {str(e)}",
                "file_path": str(path),
                "file_exists": True,
                "success": False,
            }


def create_tool() -> PDFPageCounterTool:
    """Create a PDF page counter tool instance."""
    return PDFPageCounterTool()
