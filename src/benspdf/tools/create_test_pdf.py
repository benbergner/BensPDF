"""
Generating a throwaway PDF: the implementation behind `create_test_pdf_file`.

Exists so the other tools can be tried without hunting for a real file. The
`_bytes` variant is what the MCP tool uses, since it stores the result as a
workspace artifact rather than writing to the user's disk; the path variant is a
convenience for Python callers and for the test suite.
"""

import io
from pathlib import Path
from typing import Optional

from pypdf import PdfWriter


def create_test_pdf_bytes(num_pages: int = 3, title: Optional[str] = None) -> bytes:
    """
    Build a simple test PDF in memory.

    Args:
        num_pages: Number of blank US Letter pages to create
        title: Optional title for the PDF metadata

    Returns:
        The PDF file contents

    Example:
        >>> data = create_test_pdf_bytes(num_pages=2)
        >>> data.startswith(b"%PDF")
        True
    """
    writer = PdfWriter()

    for _ in range(num_pages):
        writer.add_blank_page(width=612, height=792)  # US Letter size

    if title:
        writer.add_metadata({"/Title": title, "/Producer": "BensPDF Test Utility"})

    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def create_test_pdf(
    output_path: str, num_pages: int = 3, title: Optional[str] = None
) -> str:
    """
    Create a simple test PDF at a given path.

    Args:
        output_path: Path where to save the PDF
        num_pages: Number of pages to create (default: 3)
        title: Optional title for the PDF metadata

    Returns:
        Absolute path to the created PDF file

    Example:
        >>> path = create_test_pdf("test.pdf", num_pages=5)
        >>> print(path)
        /absolute/path/to/test.pdf
    """
    data = create_test_pdf_bytes(num_pages=num_pages, title=title)

    path = Path(output_path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)

    return str(path)
