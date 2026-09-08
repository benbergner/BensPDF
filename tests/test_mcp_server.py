"""Tests for MCP server functionality."""

import pytest
import asyncio
from benspdf.mcp_server import mcp
from benspdf import create_test_pdf


class TestMCPServer:
    """Test MCP server tools."""
    
    @pytest.mark.asyncio
    async def test_list_tools(self):
        """Test that tools are registered."""
        tools = await mcp.list_tools()
        assert len(tools) == 2
        tool_names = [t.name for t in tools]
        assert 'count_pdf_pages' in tool_names
        assert 'create_test_pdf_file' in tool_names
    
    @pytest.mark.asyncio
    async def test_count_pdf_pages_tool(self, tmp_path):
        """Test counting pages via MCP tool."""
        # Create test PDF
        test_pdf = create_test_pdf(tmp_path / "test.pdf", num_pages=5)
        
        # Call MCP tool
        result = await mcp.call_tool('count_pdf_pages', {'pdf_path': str(test_pdf)})
        
        # Handle both old and new MCP API
        if isinstance(result, tuple):
            content, structured = result
            data = structured['result']
        else:
            data = result.structured_content['result']
        
        # Check result
        assert data['page_count'] == 5
        assert data['success'] is True
    
    @pytest.mark.asyncio
    async def test_create_test_pdf_tool(self, tmp_path):
        """Test creating PDF via MCP tool."""
        output_path = str(tmp_path / "mcp_created.pdf")
        
        # Call MCP tool
        result = await mcp.call_tool('create_test_pdf_file', {
            'output_path': output_path,
            'num_pages': 7,
            'title': 'MCP Test PDF'
        })
        
        # Handle both old and new MCP API
        if isinstance(result, tuple):
            content, structured = result
            data = structured['result']
        else:
            data = result.structured_content['result']
        
        # Check result
        assert data['success'] is True
        assert data['num_pages'] == 7
        assert 'pdf_path' in data
    
    @pytest.mark.asyncio
    async def test_count_pdf_pages_error_handling(self):
        """Test error handling in MCP tool."""
        # Try to count pages in non-existent file
        result = await mcp.call_tool('count_pdf_pages', {'pdf_path': '/nonexistent/file.pdf'})
        
        # Handle both old and new MCP API
        if isinstance(result, tuple):
            content, structured = result
            data = structured['result']
        else:
            data = result.structured_content['result']
        
        # Should not crash, but return error result
        assert 'error' in data
        assert data['file_exists'] is False
