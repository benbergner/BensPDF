"""Tests for MCP server functionality."""

from pathlib import Path

import pytest

from benspdf import create_test_pdf, create_test_pdf_bytes
from benspdf.core import store
from benspdf.mcp_server import mcp

EXPECTED_TOOLS = {
    # core
    "export",
    "list_artifacts",
    "discard",
    # pdf
    "count_pdf_pages",
    "create_test_pdf_file",
}


class TestMCPServer:
    """Test MCP server tools."""

    @pytest.mark.asyncio
    async def test_list_tools(self):
        """Test that tools are registered."""
        tools = await mcp.list_tools()
        assert {t.name for t in tools} == EXPECTED_TOOLS

    @pytest.mark.asyncio
    async def test_count_pdf_pages_tool(self, tmp_path, call_tool):
        """Test counting pages via MCP tool."""
        test_pdf = create_test_pdf(tmp_path / "test.pdf", num_pages=5)

        data = await call_tool(mcp, "count_pdf_pages", {"pdf_path": str(test_pdf)})

        assert data["page_count"] == 5
        assert data["success"] is True

    @pytest.mark.asyncio
    async def test_count_pdf_pages_accepts_an_artifact_id(self, call_tool):
        """A workspace artifact id works anywhere a path does."""
        artifact = store.save(create_test_pdf_bytes(num_pages=4), ".pdf")

        data = await call_tool(mcp, "count_pdf_pages", {"pdf_path": artifact})

        assert data["success"] is True
        assert data["page_count"] == 4

    @pytest.mark.asyncio
    async def test_count_pdf_pages_error_handling(self, call_tool):
        """Test error handling in MCP tool."""
        data = await call_tool(
            mcp, "count_pdf_pages", {"pdf_path": "/nonexistent/file.pdf"}
        )

        assert "error" in data
        assert data["file_exists"] is False

    @pytest.mark.asyncio
    async def test_expired_artifact_gives_actionable_error(self, call_tool):
        """An expired id should tell the model to re-run, not just fail."""
        data = await call_tool(mcp, "count_pdf_pages", {"pdf_path": "art_deadbeef.pdf"})

        assert data["success"] is False
        assert "Re-run" in data["error"]


class TestCreateTestPdfTool:
    """create_test_pdf_file produces an artifact by default."""

    @pytest.mark.asyncio
    async def test_returns_an_artifact(self, call_tool):
        data = await call_tool(mcp, "create_test_pdf_file", {"num_pages": 3})

        assert data["success"] is True
        assert store.is_artifact(data["artifact"])
        assert "pdf_path" not in data, "nothing should be written without an output_path"

    @pytest.mark.asyncio
    async def test_also_writes_when_given_a_path(self, tmp_path, call_tool):
        """Test creating PDF via MCP tool."""
        output_path = str(tmp_path / "mcp_created.pdf")

        data = await call_tool(
            mcp,
            "create_test_pdf_file",
            {"output_path": output_path, "num_pages": 7, "title": "MCP Test PDF"},
        )

        assert data["success"] is True
        assert data["num_pages"] == 7
        assert "pdf_path" in data
        assert Path(data["pdf_path"]).exists()


class TestExportTool:
    """export is the only tool that writes to the user's filesystem."""

    @pytest.mark.asyncio
    async def test_single_artifact_to_a_file_path(self, tmp_path, call_tool):
        artifact = store.save(b"%PDF-1.4", ".pdf")
        dest = tmp_path / "out" / "final.pdf"

        data = await call_tool(
            mcp, "export", {"refs": [artifact], "dest": str(dest)}
        )

        assert data["success"] is True
        assert data["count"] == 1
        assert dest.read_bytes() == b"%PDF-1.4"

    @pytest.mark.asyncio
    async def test_many_artifacts_into_a_directory(self, tmp_path, call_tool):
        artifacts = [store.save(f"page{i}".encode(), ".pdf") for i in range(3)]

        data = await call_tool(
            mcp,
            "export",
            {
                "refs": artifacts,
                "dest": str(tmp_path / "pages"),
                "name": "page_{n:03d}{ext}",
            },
        )

        assert data["success"] is True
        assert data["count"] == 3
        assert (tmp_path / "pages" / "page_001.pdf").read_bytes() == b"page0"
        assert (tmp_path / "pages" / "page_003.pdf").read_bytes() == b"page2"

    @pytest.mark.asyncio
    async def test_accepts_a_bare_string_ref(self, tmp_path, call_tool):
        """Models pass a single string instead of a list often enough."""
        artifact = store.save(b"%PDF-1.4", ".pdf")

        data = await call_tool(
            mcp, "export", {"refs": artifact, "dest": str(tmp_path / "one.pdf")}
        )

        assert data["success"] is True

    @pytest.mark.asyncio
    async def test_refuses_to_overwrite(self, tmp_path, call_tool):
        artifact = store.save(b"new", ".pdf")
        dest = tmp_path / "existing.pdf"
        dest.write_bytes(b"original")

        data = await call_tool(
            mcp, "export", {"refs": [artifact], "dest": str(dest)}
        )

        assert data["success"] is False
        assert "overwrite=True" in data["error"]
        assert dest.read_bytes() == b"original"

    @pytest.mark.asyncio
    async def test_overwrites_when_told(self, tmp_path, call_tool):
        artifact = store.save(b"new", ".pdf")
        dest = tmp_path / "existing.pdf"
        dest.write_bytes(b"original")

        data = await call_tool(
            mcp,
            "export",
            {"refs": [artifact], "dest": str(dest), "overwrite": True},
        )

        assert data["success"] is True
        assert dest.read_bytes() == b"new"

    @pytest.mark.asyncio
    async def test_bad_template_explains_itself(self, tmp_path, call_tool):
        artifact = store.save(b"x", ".pdf")

        data = await call_tool(
            mcp,
            "export",
            {
                "refs": [artifact],
                "dest": str(tmp_path / "out"),
                "name": "{nope}.pdf",
            },
        )

        assert data["success"] is False
        assert "{n}" in data["error"]

    @pytest.mark.asyncio
    async def test_missing_artifact_reports_cleanly(self, tmp_path, call_tool):
        data = await call_tool(
            mcp,
            "export",
            {"refs": ["art_deadbeef.pdf"], "dest": str(tmp_path / "out.pdf")},
        )

        assert data["success"] is False
        assert "no longer exists" in data["error"]


class TestWorkspaceTools:
    """list_artifacts and discard."""

    @pytest.mark.asyncio
    async def test_list_artifacts(self, call_tool):
        artifact = store.save(b"x", ".pdf")

        data = await call_tool(mcp, "list_artifacts", {})

        assert data["success"] is True
        assert [entry["artifact"] for entry in data["artifacts"]] == [artifact]

    @pytest.mark.asyncio
    async def test_discard(self, call_tool):
        artifact = store.save(b"x", ".pdf")

        data = await call_tool(mcp, "discard", {"refs": [artifact]})

        assert data["success"] is True
        assert data["count"] == 1
        assert store.list_recent() == []

    @pytest.mark.asyncio
    async def test_discard_will_not_touch_user_files(self, tmp_path, call_tool):
        victim = tmp_path / "precious.pdf"
        victim.write_bytes(b"%PDF-1.4")

        data = await call_tool(mcp, "discard", {"refs": [str(victim)]})

        assert data["count"] == 0
        assert victim.exists()


class TestChaining:
    """The point of the artifact layer: results flow between tools."""

    @pytest.mark.asyncio
    async def test_create_then_count_then_export(self, tmp_path, call_tool):
        created = await call_tool(mcp, "create_test_pdf_file", {"num_pages": 6})
        artifact = created["artifact"]

        counted = await call_tool(mcp, "count_pdf_pages", {"pdf_path": artifact})
        assert counted["page_count"] == 6

        dest = tmp_path / "kept.pdf"
        exported = await call_tool(
            mcp, "export", {"refs": [artifact], "dest": str(dest)}
        )

        assert exported["success"] is True
        assert dest.exists()
