"""Tests for MCP server functionality."""

from pathlib import Path

import pytest

from benspdf import create_test_pdf, create_test_pdf_bytes
from benspdf.core import store
from benspdf.mcp_server import INSTRUCTIONS, mcp

EXPECTED_TOOLS = {
    # core
    "export",
    "list_artifacts",
    "discard",
    # pdf
    "pdf_page_count",
    "pdf_metadata",
    "pdf_check_text",
    "pdf_page_layout",
    "create_test_pdf_file",
}


class TestMCPServer:
    """Test MCP server tools."""

    @pytest.mark.asyncio
    async def test_list_tools(self):
        """Test that tools are registered."""
        tools = await mcp.list_tools()
        assert {t.name for t in tools} == EXPECTED_TOOLS


class TestToolDescriptions:
    """Descriptions are context the model pays for on every turn.

    The contract shared by all tools is stated once in the server instructions,
    so a description covers only what is specific to its tool. These guard that
    split, which is otherwise easy to undo one helpful paragraph at a time.
    """

    #: Roughly 300 tokens. The longest description today is well under half this.
    MAX_DESCRIPTION_CHARS = 1200

    def test_instructions_carry_the_shared_contract(self):
        assert "artifact" in INSTRUCTIONS, "how a ref works"
        assert "success" in INSTRUCTIONS, "the result shape"
        assert "export" in INSTRUCTIONS, "the only tool that writes to disk"

    @pytest.mark.asyncio
    async def test_descriptions_stay_lean(self):
        for tool in await mcp.list_tools():
            assert len(tool.description) <= self.MAX_DESCRIPTION_CHARS, (
                f"{tool.name} description is {len(tool.description)} chars; move "
                f"anything shared into the server instructions"
            )

    @pytest.mark.asyncio
    async def test_descriptions_open_with_their_summary_line(self):
        """A docstring opening on the line after its quotes reads as blank.

        Clients and the bundled CLI show the first line as the tool's one line
        purpose, so a leading newline costs the tool its label.
        """
        for tool in await mcp.list_tools():
            first = tool.description.split("\n")[0]
            assert first.strip(), f"{tool.name} has no summary line"
            assert first == first.strip(), f"{tool.name} summary line is indented"

    @pytest.mark.asyncio
    async def test_descriptions_do_not_repeat_the_shared_contract(self):
        """Seven copies of the result shape is six too many."""
        for tool in await mcp.list_tools():
            assert "success: True" not in tool.description, tool.name
            assert "error: Error message" not in tool.description, tool.name

    @pytest.mark.asyncio
    async def test_pdf_page_count_tool(self, tmp_path, call_tool):
        """Test counting pages via MCP tool."""
        test_pdf = create_test_pdf(tmp_path / "test.pdf", num_pages=5)

        data = await call_tool(mcp, "pdf_page_count", {"ref": str(test_pdf)})

        assert data["page_count"] == 5
        assert data["success"] is True

    @pytest.mark.asyncio
    async def test_pdf_page_count_accepts_an_artifact_id(self, call_tool):
        """A workspace artifact id works anywhere a path does."""
        artifact = store.save(create_test_pdf_bytes(num_pages=4), ".pdf")

        data = await call_tool(mcp, "pdf_page_count", {"ref": artifact})

        assert data["success"] is True
        assert data["page_count"] == 4

    @pytest.mark.asyncio
    async def test_pdf_page_count_error_handling(self, call_tool):
        """Test error handling in MCP tool."""
        data = await call_tool(mcp, "pdf_page_count", {"ref": "/nonexistent/file.pdf"})

        assert "error" in data
        assert data["file_exists"] is False

    @pytest.mark.asyncio
    async def test_expired_artifact_gives_actionable_error(self, call_tool):
        """An expired id should tell the model to re-run, not just fail."""
        data = await call_tool(mcp, "pdf_page_count", {"ref": "art_deadbeef.pdf"})

        assert data["success"] is False
        assert "Re-run" in data["error"]


class TestPdfMetadataTool:
    """pdf_metadata over the MCP boundary. Field level behaviour is in
    test_metadata.py; this covers the wiring."""

    @pytest.mark.asyncio
    async def test_reads_metadata_from_a_path(self, tmp_path, call_tool):
        test_pdf = create_test_pdf(tmp_path / "titled.pdf", title="Annual Report")

        data = await call_tool(mcp, "pdf_metadata", {"ref": str(test_pdf)})

        assert data["success"] is True
        assert data["title"] == "Annual Report"
        assert data["producer"] == "BensPDF Test Utility"
        assert data["sources"]["title"] == "info"

    @pytest.mark.asyncio
    async def test_accepts_an_artifact_id(self, call_tool):
        artifact = store.save(
            create_test_pdf_bytes(num_pages=2, title="From Id"), ".pdf"
        )

        data = await call_tool(mcp, "pdf_metadata", {"ref": artifact})

        assert data["success"] is True
        assert data["title"] == "From Id"

    @pytest.mark.asyncio
    async def test_missing_file_reports_cleanly(self, call_tool):
        data = await call_tool(mcp, "pdf_metadata", {"ref": "/nonexistent/file.pdf"})

        assert data["success"] is False
        assert data["file_exists"] is False

    @pytest.mark.asyncio
    async def test_expired_artifact_gives_actionable_error(self, call_tool):
        data = await call_tool(mcp, "pdf_metadata", {"ref": "art_deadbeef.pdf"})

        assert data["success"] is False
        assert "Re-run" in data["error"]


class TestPdfCheckTextTool:
    """pdf_check_text over the MCP boundary. The verdict logic and the page
    sampling live in test_check_text.py; this covers the wiring."""

    @pytest.mark.asyncio
    async def test_reads_a_path(self, tmp_path, call_tool):
        """Blank test pages have no text and no images, so: nothing to OCR."""
        test_pdf = create_test_pdf(tmp_path / "blank.pdf", num_pages=3)

        data = await call_tool(mcp, "pdf_check_text", {"ref": str(test_pdf)})

        assert data["success"] is True
        assert data["verdict"] == "no_text"
        assert data["needs_ocr"] is False
        assert data["pages_examined"] == 3
        assert data["sampled"] is False

    @pytest.mark.asyncio
    async def test_accepts_an_artifact_id(self, call_tool):
        artifact = store.save(create_test_pdf_bytes(num_pages=2), ".pdf")

        data = await call_tool(mcp, "pdf_check_text", {"ref": artifact})

        assert data["success"] is True
        assert data["page_count"] == 2

    @pytest.mark.asyncio
    async def test_missing_file_reports_cleanly(self, call_tool):
        data = await call_tool(mcp, "pdf_check_text", {"ref": "/nonexistent/file.pdf"})

        assert data["success"] is False
        assert data["file_exists"] is False

    @pytest.mark.asyncio
    async def test_expired_artifact_gives_actionable_error(self, call_tool):
        data = await call_tool(mcp, "pdf_check_text", {"ref": "art_deadbeef.pdf"})

        assert data["success"] is False
        assert "Re-run" in data["error"]


class TestPdfPageLayoutTool:
    """pdf_page_layout over the MCP boundary. Geometry lives in
    test_page_layout.py; this covers the wiring and the pages argument."""

    @pytest.mark.asyncio
    async def test_groups_pages_from_a_path(self, tmp_path, call_tool):
        test_pdf = create_test_pdf(tmp_path / "letter.pdf", num_pages=4)

        data = await call_tool(mcp, "pdf_page_layout", {"ref": str(test_pdf)})

        assert data["success"] is True
        assert data["uniform"] is True
        assert data["sizes"][0]["paper"] == "Letter"
        assert data["sizes"][0]["pages"] == "1-4"
        assert "pages" not in data, "detail is opt in"

    @pytest.mark.asyncio
    async def test_pages_argument_adds_detail(self, tmp_path, call_tool):
        test_pdf = create_test_pdf(tmp_path / "detail.pdf", num_pages=6)

        data = await call_tool(
            mcp, "pdf_page_layout", {"ref": str(test_pdf), "pages": "2-3"}
        )

        assert [row["page"] for row in data["pages"]] == [2, 3]

    @pytest.mark.asyncio
    async def test_accepts_an_artifact_id(self, call_tool):
        artifact = store.save(create_test_pdf_bytes(num_pages=2), ".pdf")

        data = await call_tool(mcp, "pdf_page_layout", {"ref": artifact})

        assert data["success"] is True
        assert data["page_count"] == 2

    @pytest.mark.asyncio
    async def test_missing_file_reports_cleanly(self, call_tool):
        data = await call_tool(mcp, "pdf_page_layout", {"ref": "/nonexistent/file.pdf"})

        assert data["success"] is False
        assert data["file_exists"] is False

    @pytest.mark.asyncio
    async def test_expired_artifact_gives_actionable_error(self, call_tool):
        data = await call_tool(mcp, "pdf_page_layout", {"ref": "art_deadbeef.pdf"})

        assert data["success"] is False
        assert "Re-run" in data["error"]


class TestCreateTestPdfTool:
    """create_test_pdf_file produces an artifact by default."""

    @pytest.mark.asyncio
    async def test_returns_an_artifact(self, call_tool):
        data = await call_tool(mcp, "create_test_pdf_file", {"num_pages": 3})

        assert data["success"] is True
        assert store.is_artifact(data["artifact"])
        assert (
            "pdf_path" not in data
        ), "nothing should be written without an output_path"

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

        data = await call_tool(mcp, "export", {"refs": [artifact], "dest": str(dest)})

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

        data = await call_tool(mcp, "export", {"refs": [artifact], "dest": str(dest)})

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

        counted = await call_tool(mcp, "pdf_page_count", {"ref": artifact})
        assert counted["page_count"] == 6

        dest = tmp_path / "kept.pdf"
        exported = await call_tool(
            mcp, "export", {"refs": [artifact], "dest": str(dest)}
        )

        assert exported["success"] is True
        assert dest.exists()
