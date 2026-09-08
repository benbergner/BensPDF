"""
Utility functions for LocalPDF
"""

from typing import Optional
from pathlib import Path
from pypdf import PdfWriter


def create_test_pdf(
    output_path: str,
    num_pages: int = 3,
    title: Optional[str] = None
) -> str:
    """
    Create a simple test PDF with specified number of pages.
    
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
    writer = PdfWriter()
    
    # Add empty pages
    for i in range(num_pages):
        writer.add_blank_page(width=612, height=792)  # US Letter size
    
    # Add metadata if title provided
    if title:
        writer.add_metadata({
            "/Title": title,
            "/Producer": "LocalPDF Test Utility"
        })
    
    # Ensure parent directory exists
    path = Path(output_path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    
    # Write PDF
    with open(path, "wb") as f:
        writer.write(f)
    
    return str(path)
