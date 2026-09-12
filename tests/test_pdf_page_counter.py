"""
Tests for PDFPageCounterTool
"""

import pytest
import tempfile
from pathlib import Path
from benspdf import PDFPageCounterTool, create_test_pdf


class TestCreateTestPdf:
    """Test cases for create_test_pdf utility."""

    def setup_method(self):
        """Set up test fixtures."""
        self.temp_dir = tempfile.mkdtemp()

    def teardown_method(self):
        """Clean up test fixtures."""
        import shutil

        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_create_single_page_pdf(self):
        """Test creating a single-page PDF."""
        pdf_path = Path(self.temp_dir) / "single.pdf"
        result_path = create_test_pdf(str(pdf_path), num_pages=1)

        assert Path(result_path).exists()
        assert Path(result_path).stat().st_size > 0

    def test_create_multiple_page_pdf(self):
        """Test creating a multi-page PDF."""
        pdf_path = Path(self.temp_dir) / "multi.pdf"
        result_path = create_test_pdf(str(pdf_path), num_pages=5)

        assert Path(result_path).exists()

        # Verify page count using the tool
        tool = PDFPageCounterTool()
        result = tool(result_path)
        assert result["success"] is True
        assert result["page_count"] == 5

    def test_create_pdf_with_metadata(self):
        """Test creating a PDF with metadata."""
        pdf_path = Path(self.temp_dir) / "with_metadata.pdf"
        result_path = create_test_pdf(str(pdf_path), num_pages=2, title="Test Document")

        assert Path(result_path).exists()


class TestPDFPageCounterTool:
    """Test cases for PDFPageCounterTool."""

    def setup_method(self):
        """Set up test fixtures."""
        self.tool = PDFPageCounterTool()
        self.temp_dir = tempfile.mkdtemp()

    def teardown_method(self):
        """Clean up test fixtures."""
        import shutil

        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_tool_creation(self):
        """Test tool can be created."""
        assert self.tool is not None
        assert hasattr(self.tool, "count_pages")

    def test_file_not_found(self):
        """Test handling of non-existent file."""
        result = self.tool("nonexistent.pdf")
        assert "error" in result
        assert result["file_exists"] is False

    def test_not_a_pdf(self):
        """Test handling of non-PDF file."""
        # Create a temporary text file
        temp_path = Path(self.temp_dir) / "test.txt"
        temp_path.write_text("This is not a PDF")

        result = self.tool(str(temp_path))
        assert "error" in result
        assert "Not a PDF file" in result["error"]
        assert result["file_exists"] is True

    def test_valid_pdf_single_page(self):
        """Test with a valid single-page PDF."""
        pdf_path = Path(self.temp_dir) / "single_page.pdf"
        create_test_pdf(str(pdf_path), num_pages=1)

        result = self.tool(str(pdf_path))
        assert result["success"] is True
        assert result["page_count"] == 1
        assert result["file_exists"] is True

    def test_valid_pdf_multiple_pages(self):
        """Test with a valid multi-page PDF."""
        pdf_path = Path(self.temp_dir) / "multi_page.pdf"
        create_test_pdf(str(pdf_path), num_pages=5)

        result = self.tool(str(pdf_path))
        assert result["success"] is True
        assert result["page_count"] == 5
        assert result["file_exists"] is True

    def test_path_expansion(self):
        """Test that paths are properly expanded."""
        result = self.tool("~/nonexistent.pdf")
        # Should expand ~ to home directory
        assert "/Users/" in result["file_path"] or "/home/" in result["file_path"]


class TestConvenienceFunctions:
    """Test convenience functions."""

    def test_create_tool(self):
        """Test create_tool function."""
        from benspdf import create_tool

        tool = create_tool()
        assert isinstance(tool, PDFPageCounterTool)
