"""Tests for extracting the text a PDF already carries.

Extraction itself is pypdf's, so what is tested here is everything around it: the
character budget that keeps a long document from filling a context, the artifact
that is the way out of that budget, the two signals behind calling a page's text
unreliable, and the fallback that keeps layout mode from returning a blank page.

PDFs are built by hand with pypdf, drawing text with an explicit font, because
the font dictionary *is* the evidence for half of what is tested: a face with no
`/ToUnicode` map is what makes extracted text suspect, and that cannot be
expressed by a rendering library that writes sensible files.
"""

import io
import json
from pathlib import Path

from pypdf import PdfWriter
from pypdf.generic import (
    DecodedStreamObject,
    DictionaryObject,
    NameObject,
    NumberObject,
)

from benscore import store
from benspdf import extract_text
from benspdf.tools import extract_text as extract_module

#: Long enough that a handful of pages clears a lowered character budget.
PARAGRAPH = "Lorem ipsum dolor sit amet consectetur adipiscing elit " * 3


def standard_font() -> DictionaryObject:
    """Helvetica: no embedded file, no map needed, nothing to be suspicious of."""
    return DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )


def unmapped_font(with_map: bool = False) -> DictionaryObject:
    """An embedded subset that does not say what its glyph codes mean.

    The shape the measurements came from: a subset prefix, a named base encoding,
    an embedded file, and no `/ToUnicode`. `with_map` adds the map back, which is
    the whole difference between text to trust and text to question.
    """
    outlines = DecodedStreamObject()
    outlines.set_data(b"\x00")
    descriptor = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/FontDescriptor"),
            NameObject("/FontName"): NameObject("/AAAAAA+Private"),
            NameObject("/Flags"): NumberObject(32),
            NameObject("/FontFile2"): outlines,
        }
    )
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/TrueType"),
            NameObject("/BaseFont"): NameObject("/AAAAAA+Private"),
            NameObject("/Encoding"): NameObject("/MacRomanEncoding"),
            NameObject("/FontDescriptor"): descriptor,
        }
    )
    if with_map:
        mapping = DecodedStreamObject()
        mapping.set_data(b"% a CMap, never parsed by anything under test")
        font[NameObject("/ToUnicode")] = mapping
    return font


def build(path, *bodies, font=None, encrypt=None) -> str:
    """A PDF with one page per body, each drawing that body's text."""
    writer = PdfWriter()
    for body in bodies or (PARAGRAPH,):
        page = writer.add_blank_page(width=612, height=792)
        if body is None:  # a page with nothing on it at all
            continue
        stream = DecodedStreamObject()
        stream.set_data(
            b"BT /F1 12 Tf 72 720 Td (" + body.encode("latin-1") + b") Tj ET"
        )
        page[NameObject("/Contents")] = stream
        page[NameObject("/Resources")] = DictionaryObject(
            {
                NameObject("/Font"): DictionaryObject(
                    {NameObject("/F1"): font() if font else standard_font()}
                )
            }
        )
    if encrypt:
        writer.encrypt(user_password=encrypt, owner_password="owner")

    buffer = io.BytesIO()
    writer.write(buffer)
    path.write_bytes(buffer.getvalue())
    return str(path)


class TestExtraction:
    """Text out, with the page it came from."""

    def test_every_page_by_default(self, tmp_path):
        pdf = build(tmp_path / "three.pdf", "one", "two", "three")
        result = extract_text(pdf)
        assert result["success"] is True
        assert [entry["page"] for entry in result["pages"]] == [1, 2, 3]
        assert [entry["text"] for entry in result["pages"]] == ["one", "two", "three"]

    def test_each_page_carries_its_own_number(self, tmp_path):
        """The reason to extract per page: a quote can name where it came from."""
        pdf = build(tmp_path / "cite.pdf", "front matter", "the quotable sentence")
        result = extract_text(pdf, pages="2")
        assert result["pages"] == [
            {
                "page": 2,
                "characters": len("the quotable sentence"),
                "words": 3,
                "text": "the quotable sentence",
            }
        ]

    def test_counts_are_reported_per_page_and_in_total(self, tmp_path):
        pdf = build(tmp_path / "counts.pdf", "one two", "three four five")
        result = extract_text(pdf)
        assert [entry["words"] for entry in result["pages"]] == [2, 3]
        assert result["words"] == 5
        assert result["characters"] == len("one two") + len("three four five")
        assert result["pages_read"] == 2

    def test_a_page_range_can_be_asked_for(self, tmp_path):
        pdf = build(tmp_path / "four.pdf", "a", "b", "c", "d")
        result = extract_text(pdf, pages="2,4")
        assert [entry["page"] for entry in result["pages"]] == [2, 4]

    def test_a_page_with_no_text_is_named_not_dropped(self, tmp_path):
        pdf = build(tmp_path / "gap.pdf", "text here", None, "and here")
        result = extract_text(pdf)
        assert [entry["page"] for entry in result["pages"]] == [1, 2, 3]
        assert result["pages"][1]["characters"] == 0
        assert result["empty_pages"] == "2"
        assert "pdf_ocr" in result["summary"]

    def test_a_document_with_no_text_says_where_to_go_next(self, tmp_path):
        """Extraction worked; there was nothing to extract. That is an answer."""
        pdf = build(tmp_path / "scan.pdf", None, None)
        result = extract_text(pdf)
        assert result["success"] is True
        assert result["characters"] == 0
        assert "pdf_check_text" in result["summary"]
        assert "pdf_ocr" in result["summary"]

    def test_nothing_is_written_to_disk_by_default(self, tmp_path, isolated_workspace):
        pdf = build(tmp_path / "quiet.pdf", "text")
        extract_text(pdf)
        assert not isolated_workspace.exists() or not list(isolated_workspace.iterdir())


class TestCharacterBudget:
    """One call returns what a client can hold, and says what it left."""

    def test_a_long_document_stops_and_names_the_rest(self, tmp_path, monkeypatch):
        monkeypatch.setattr(extract_module, "_MAX_INLINE_CHARS", 100)
        pdf = build(tmp_path / "long.pdf", *([PARAGRAPH] * 6))
        result = extract_text(pdf)
        assert result["truncated"] is True
        assert result["stopped_for"] == "characters"
        assert result["pages_read"] < 6
        assert result["pages_remaining"].endswith("6")

    def test_the_summary_spells_out_both_ways_to_continue(self, tmp_path, monkeypatch):
        monkeypatch.setattr(extract_module, "_MAX_INLINE_CHARS", 100)
        pdf = build(tmp_path / "long.pdf", *([PARAGRAPH] * 6))
        summary = extract_text(pdf)["summary"]
        assert f'pages="{extract_text(pdf)["pages_remaining"]}"' in summary
        assert 'output="txt"' in summary

    def test_a_later_range_picks_up_where_it_stopped(self, tmp_path, monkeypatch):
        monkeypatch.setattr(extract_module, "_MAX_INLINE_CHARS", 100)
        pdf = build(tmp_path / "long.pdf", *([PARAGRAPH] * 6))
        first = extract_text(pdf)
        second = extract_text(pdf, pages=first["pages_remaining"])
        assert second["pages"][0]["page"] == first["pages"][-1]["page"] + 1

    def test_a_whole_page_is_returned_or_none_of_it(self, tmp_path, monkeypatch):
        """Half a page is not quotable, so the budget cuts between pages."""
        monkeypatch.setattr(extract_module, "_MAX_INLINE_CHARS", 10)
        pdf = build(tmp_path / "long.pdf", PARAGRAPH, PARAGRAPH)
        result = extract_text(pdf)
        assert result["pages"][0]["text"] == PARAGRAPH.strip()

    def test_the_budget_is_reported_so_a_caller_can_plan(self, tmp_path):
        pdf = build(tmp_path / "short.pdf", "text")
        assert extract_text(pdf)["character_budget"] == extract_module._MAX_INLINE_CHARS

    def test_a_short_document_is_not_truncated(self, tmp_path):
        pdf = build(tmp_path / "short.pdf", "text")
        result = extract_text(pdf)
        assert result["truncated"] is False
        assert "pages_remaining" not in result
        assert "stopped_for" not in result


class TestTimeBudget:
    """The limit that binds when the character budget does not."""

    def test_a_slow_document_stops_on_time(self, tmp_path, monkeypatch):
        monkeypatch.setattr(extract_module, "_TIME_BUDGET_S", 0.0)
        pdf = build(tmp_path / "slow.pdf", "a", "b", "c")
        result = extract_text(pdf)
        assert result["stopped_for"] == "time"
        assert result["pages_read"] == 1
        assert "second budget" in result["summary"]

    def test_the_first_page_is_always_read(self, tmp_path, monkeypatch):
        """A budget is there to stop a long document, not to refuse a short one."""
        monkeypatch.setattr(extract_module, "_TIME_BUDGET_S", 0.0)
        pdf = build(tmp_path / "slow.pdf", "the only page")
        result = extract_text(pdf)
        assert result["pages"][0]["text"] == "the only page"
        assert result["truncated"] is False


class TestTextFile:
    """`output="txt"`: the whole extraction as an artifact, counts in the result."""

    def test_the_text_becomes_an_artifact(self, tmp_path):
        pdf = build(tmp_path / "book.pdf", "one", "two")
        result = extract_text(pdf, output="txt")
        assert result["artifact"].endswith(".txt")
        assert store.resolve(result["artifact"]).read_text() == "one\ftwo"
        assert result["size_bytes"] == len("one\ftwo")

    def test_the_path_can_be_opened_by_a_person(self, tmp_path):
        pdf = build(tmp_path / "book.pdf", "one")
        result = extract_text(pdf, output="txt")
        assert Path(result["path"]).read_text() == "one"
        assert result["artifacts"] == [result["artifact"]]

    def test_the_text_is_left_out_of_the_result(self, tmp_path):
        pdf = build(tmp_path / "book.pdf", "one", "two")
        result = extract_text(pdf, output="txt")
        assert all("text" not in entry for entry in result["pages"])
        assert result["text_omitted_pages"] == "1-2"
        assert result["characters"] == len("onetwo")

    def test_no_character_budget_applies_to_the_file(self, tmp_path, monkeypatch):
        """The budget protects a context, and a file is not one."""
        monkeypatch.setattr(extract_module, "_MAX_INLINE_CHARS", 10)
        pdf = build(tmp_path / "book.pdf", *([PARAGRAPH] * 6))
        result = extract_text(pdf, output="txt")
        assert result["pages_read"] == 6
        assert result["truncated"] is False

    def test_both_returns_the_text_and_the_file(self, tmp_path):
        pdf = build(tmp_path / "book.pdf", "one", "two")
        result = extract_text(pdf, output="both")
        assert [entry["text"] for entry in result["pages"]] == ["one", "two"]
        assert store.resolve(result["artifact"]).read_text() == "one\ftwo"

    def test_both_keeps_the_budget_on_the_inline_text_only(self, tmp_path, monkeypatch):
        monkeypatch.setattr(extract_module, "_MAX_INLINE_CHARS", 100)
        pdf = build(tmp_path / "book.pdf", *([PARAGRAPH] * 4))
        result = extract_text(pdf, output="both")
        assert result["pages_read"] == 4
        assert result["truncated"] is False
        assert "text" in result["pages"][0]
        assert "text" not in result["pages"][-1]
        assert result["text_omitted_pages"].endswith("4")

    def test_the_summary_names_the_artifact_and_the_page_break(self, tmp_path):
        pdf = build(tmp_path / "book.pdf", "one")
        result = extract_text(pdf, output="txt")
        assert result["artifact"] in result["summary"]
        assert "form feed" in result["summary"]


class TestSuspectText:
    """Text that will not decode is reported, and it takes two signals."""

    def test_unmapped_fonts_and_odd_characters_together(self, tmp_path):
        pdf = build(tmp_path / "bad.pdf", "\x01\x02\x03\x04 hello", font=unmapped_font)
        result = extract_text(pdf)
        assert result["pages"][0]["text_suspect"] is True
        assert result["pages"][0]["unmappable_characters"] == 4
        assert result["suspect_pages"] == "1"

    def test_the_summary_says_what_to_do_about_it(self, tmp_path):
        pdf = build(tmp_path / "bad.pdf", "\x01\x02\x03\x04 hello", font=unmapped_font)
        summary = extract_text(pdf)["summary"]
        assert "unreliable" in summary
        assert "pdf_render_pages" in summary

    def test_a_font_with_a_character_map_is_trusted(self, tmp_path):
        """Same odd characters, one `/ToUnicode` away from being the file's own."""
        pdf = build(
            tmp_path / "mapped.pdf",
            "\x01\x02\x03\x04 hello",
            font=lambda: unmapped_font(with_map=True),
        )
        result = extract_text(pdf)
        assert "text_suspect" not in result["pages"][0]
        assert "suspect_pages" not in result

    def test_clean_text_from_an_unmapped_font_is_not_flagged(self, tmp_path):
        """The rule that flagged 9 files on font evidence alone, 7 of them wrongly."""
        pdf = build(tmp_path / "fine.pdf", PARAGRAPH, font=unmapped_font)
        result = extract_text(pdf)
        assert "text_suspect" not in result["pages"][0]

    def test_a_standard_font_needs_no_map(self, tmp_path):
        pdf = build(tmp_path / "helv.pdf", "\x01\x02\x03\x04 hello")
        result = extract_text(pdf)
        assert "text_suspect" not in result["pages"][0]

    def test_one_stray_character_is_not_evidence(self, tmp_path):
        """Under the count threshold: a symbol the font spells oddly, not damage."""
        pdf = build(tmp_path / "one.pdf", "\x01 hello there", font=unmapped_font)
        result = extract_text(pdf)
        assert "text_suspect" not in result["pages"][0]

    def test_a_few_odd_characters_in_a_long_page_are_not_evidence(self, tmp_path):
        """Over the count, under the share."""
        pdf = build(
            tmp_path / "share.pdf", "\x01\x02\x03" + PARAGRAPH * 4, font=unmapped_font
        )
        result = extract_text(pdf)
        assert "text_suspect" not in result["pages"][0]

    def test_newlines_and_tabs_are_not_odd_characters(self):
        assert extract_module._odd_characters("a\nb\tc\r\n") == 0


class TestLayout:
    """Spacing on request, and prose when spacing loses the text."""

    def test_layout_mode_is_reported(self, tmp_path):
        pdf = build(tmp_path / "form.pdf", "label    value")
        assert extract_text(pdf, layout=True)["mode"] == "layout"
        assert extract_text(pdf)["mode"] == "plain"

    def test_layout_keeps_the_page_indentation(self):
        """The point of asking for it: the columns a form lines its values up in."""

        class Page:
            def extract_text(self, extraction_mode="plain"):
                return (
                    "  label      value"
                    if extraction_mode == "layout"
                    else "label value"
                )

        assert extract_module._page_text(Page(), layout=True) == (
            "  label      value",
            False,
        )
        assert extract_module._page_text(Page(), layout=False) == ("label value", False)

    def test_a_page_layout_mode_loses_falls_back_to_prose(self):
        """Measured on real files: 11 of 300 pages lost most of their text this way."""

        class Page:
            def extract_text(self, extraction_mode="plain"):
                return "" if extraction_mode == "layout" else "the whole page of text"

        assert extract_module._page_text(Page(), layout=True) == (
            "the whole page of text",
            True,
        )
        assert extract_module._page_text(Page(), layout=False) == (
            "the whole page of text",
            False,
        )

    def test_the_fallback_is_named_per_page_and_in_the_summary(
        self, tmp_path, monkeypatch
    ):
        pdf = build(tmp_path / "form.pdf", "one", "two")

        def only_plain(self, extraction_mode="plain", **kwargs):
            return "" if extraction_mode == "layout" else "prose"

        monkeypatch.setattr("pypdf._page.PageObject.extract_text", only_plain)
        result = extract_text(pdf, layout=True)
        assert [entry["mode"] for entry in result["pages"]] == ["plain", "plain"]
        assert result["pages_read_as_plain"] == "1-2"
        assert "came back as prose" in result["summary"]

    def test_trailing_padding_is_dropped(self):
        """Layout mode pads every line to the page width; that is not text."""
        assert extract_module._clean("word          \n\nmore   ") == "word\n\nmore"


class TestFailures:
    """Every reason a call cannot answer, reported rather than raised."""

    def test_missing_file(self, tmp_path):
        result = extract_text(str(tmp_path / "nope.pdf"))
        assert result["success"] is False
        assert "not found" in result["error"].lower()
        assert result["file_exists"] is False

    def test_not_a_pdf(self, tmp_path):
        path = tmp_path / "notes.txt"
        path.write_text("not a PDF at all")
        result = extract_text(str(path))
        assert result["success"] is False
        assert "could not read" in result["error"].lower()

    def test_encrypted_file_points_at_check_access(self, tmp_path):
        pdf = build(tmp_path / "locked.pdf", "secret", encrypt="pw")
        result = extract_text(pdf)
        assert result["success"] is False
        assert result["encrypted"] is True
        assert "pdf_check_access" in result["error"]

    def test_an_unknown_output_lists_the_ones_that_work(self, tmp_path):
        pdf = build(tmp_path / "text.pdf", "text")
        result = extract_text(pdf, output="markdown")
        assert result["success"] is False
        for accepted in extract_module._OUTPUTS:
            assert accepted in result["error"]

    def test_a_bad_page_range_explains_the_accepted_forms(self, tmp_path):
        pdf = build(tmp_path / "text.pdf", "text")
        result = extract_text(pdf, pages="last")
        assert result["success"] is False
        assert '"1-20"' in result["error"]

    def test_one_bad_page_does_not_fail_the_rest(self, tmp_path, monkeypatch):
        pdf = build(tmp_path / "two.pdf", "one", "two")
        original = extract_module._page_text
        calls = {"n": 0}

        def sometimes(page, *, layout):
            calls["n"] += 1
            if calls["n"] == 1:
                raise ValueError("this page is broken")
            return original(page, layout=layout)

        monkeypatch.setattr(extract_module, "_page_text", sometimes)
        result = extract_text(pdf)
        assert result["success"] is True
        assert result["pages_read"] == 1
        assert result["failed"] == [
            {"page": 1, "error": "ValueError: this page is broken"}
        ]
        assert "could not be read" in result["summary"]

    def test_every_page_failing_is_a_failure(self, tmp_path, monkeypatch):
        pdf = build(tmp_path / "two.pdf", "one", "two")

        def always(page, *, layout):
            raise ValueError("no")

        monkeypatch.setattr(extract_module, "_page_text", always)
        result = extract_text(pdf)
        assert result["success"] is False
        assert result["pages_read"] == 0
        assert len(result["failed"]) == 2

    def test_a_page_with_no_fonts_is_never_suspect(self, tmp_path):
        """The font check has to survive a page it cannot read fonts from."""
        writer = PdfWriter()
        writer.add_blank_page(width=612, height=792)
        buffer = io.BytesIO()
        writer.write(buffer)
        path = tmp_path / "bare.pdf"
        path.write_bytes(buffer.getvalue())
        result = extract_text(str(path))
        assert result["success"] is True
        assert "suspect_pages" not in result


class TestJsonSafety:
    """Results cross a JSON boundary on the way to a client."""

    def test_result_is_json_serializable(self, tmp_path):
        pdf = build(tmp_path / "text.pdf", "one", "\x01\x02\x03\x04 two")
        result = extract_text(pdf, output="both")
        assert json.loads(json.dumps(result))["success"] is True
