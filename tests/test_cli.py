"""Tests for the Ollama chat CLI's own logic.

Only the parts that do not need a model running: the line the CLI prints after a
tool call. It used to read `page_count` whenever it was there, which was right
when counting pages was the only tool and wrong the moment four more verbs began
returning the document's page count as context.
"""

from benspdf.cli import _describe


class TestDescribe:
    """The one line a person watching the CLI actually sees."""

    def test_prefers_the_tools_own_summary(self):
        result = {
            "success": True,
            "page_count": 213,
            "file_name": "thesis.pdf",
            "rendered": 1,
            "summary": "Rendered 1 page of thesis.pdf (page 212) as PNG at 150 dpi.",
        }

        assert _describe(result) == (
            "Result: Rendered 1 page of thesis.pdf (page 212) as PNG at 150 dpi."
        )

    def test_does_not_report_document_length_as_the_answer(self):
        """The bug: a one page render of a 213 page file announced "213 pages"."""
        result = {
            "success": True,
            "page_count": 213,
            "file_name": "thesis.pdf",
            "rendered": 1,
            "summary": "Rendered 1 page.",
        }

        assert "213" not in _describe(result)

    def test_page_count_still_reads_well_without_a_summary(self):
        """pdf_page_count answers with a number, and has no sentence to print."""
        result = {"success": True, "page_count": 12, "file_name": "report.pdf"}

        assert _describe(result) == "Result: 12 pages in report.pdf"

    def test_a_single_page_document_is_not_1_pages(self):
        result = {"success": True, "page_count": 1, "file_name": "ticket.pdf"}

        assert _describe(result) == "Result: 1 page in ticket.pdf"

    def test_a_file_count_for_the_workspace_verbs(self):
        assert _describe(
            {"success": True, "count": 3, "exported": ["a", "b", "c"]}
        ) == ("Result: 3 file(s)")

    def test_an_artifact_id_when_that_is_all_there_is(self):
        assert _describe({"success": True, "artifact": "art_a1b2c3d4.pdf"}) == (
            "Result: art_a1b2c3d4.pdf"
        )

    def test_errors_are_shown_as_errors(self):
        result = {"success": False, "error": "File not found: nope.pdf"}

        assert _describe(result) == "Error: File not found: nope.pdf"

    def test_an_error_key_wins_over_a_summary(self):
        """A failed call must not be announced with a cheerful sentence."""
        result = {"success": False, "error": "encrypted", "summary": "All good"}

        assert _describe(result).startswith("Error:")

    def test_survives_something_that_is_not_a_dict(self):
        assert _describe("odd") == "Result: odd"
