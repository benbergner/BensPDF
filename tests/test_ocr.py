"""Tests for reading a scan with OCR.

Two things are hard to test here and both are tested rather than assumed.

**Tesseract is a system install, not a dependency.** Most of these tests must run
on a machine that has never heard of it, so the subprocess is faked: a stub binary
resolver plus a canned TSV, which is enough to cover the parsing, the layer
geometry, the reporting and every error path. The few tests that need the real
thing are marked and skipped when it is absent, and CI is expected to skip them.

**A text layer is invisible, so "it worked" needs a definition.** The definition
used is that the text extracts back out of the artifact in reading order, with its
words separated - which is what a text layer is *for*, and which caught a real bug:
placing each word at its own matrix extracted as "theoutputof thisclassifieras",
because an extractor has no spaces to read and infers breaks from font metrics.
"""

import io
import os
import shutil
from pathlib import Path

import pytest
from PIL import Image
from pypdf import PdfReader, PdfWriter
from pypdf.generic import (
    ArrayObject,
    ContentStream,
    FloatObject,
    NameObject,
    NumberObject,
)

from benscore import store
from benspdf import check_text, ocr
from benspdf.tools import ocr as ocr_module

LETTER = (612.0, 792.0)


# --- fixtures --------------------------------------------------------------


def blank(path: Path, *specs, encrypt=None) -> str:
    """Build a PDF of blank pages. Each spec may set `size`, `rotate` or `crop`."""
    writer = PdfWriter()
    for spec in specs or ({},):
        width, height = spec.get("size", LETTER)
        page = writer.add_blank_page(width=width, height=height)
        if "rotate" in spec:
            page[NameObject("/Rotate")] = NumberObject(spec["rotate"])
        if "crop" in spec:
            page[NameObject("/CropBox")] = ArrayObject(
                [FloatObject(value) for value in spec["crop"]]
            )
    if encrypt:
        writer.encrypt(user_password=encrypt, owner_password="owner")

    buffer = io.BytesIO()
    writer.write(buffer)
    path.write_bytes(buffer.getvalue())
    return str(path)


def with_text(path: Path, pages: int = 1) -> str:
    """A PDF whose pages carry a real text layer, well past `_TEXT_CHARS`."""
    source = blank(path.with_suffix(".blank.pdf"), *({},) * pages)
    writer = PdfWriter(clone_from=source)
    for index, page in enumerate(writer.pages):
        overlay = _text_page(f"Page {index + 1}. " + "Readable words here. " * 12)
        page.merge_page(overlay)

    buffer = io.BytesIO()
    writer.write(buffer)
    path.write_bytes(buffer.getvalue())
    return str(path)


def _text_page(text: str):
    """A page with visible text on it, built the way `ocr` builds its layer."""
    scratch = PdfWriter()
    page = scratch.add_blank_page(width=LETTER[0], height=LETTER[1])
    page[NameObject("/Resources")] = ocr_module.DictionaryObject(
        {
            NameObject("/Font"): ocr_module.DictionaryObject(
                {
                    NameObject("/F1"): ocr_module.DictionaryObject(
                        {
                            NameObject("/Type"): NameObject("/Font"),
                            NameObject("/Subtype"): NameObject("/Type1"),
                            NameObject("/BaseFont"): NameObject("/Helvetica"),
                        }
                    )
                }
            )
        }
    )
    stream = ocr_module.DecodedStreamObject()
    stream.set_data(f"BT /F1 12 Tf 1 0 0 1 40 700 Tm ({text}) Tj ET".encode("latin-1"))
    page.replace_contents(stream)

    buffer = io.BytesIO()
    scratch.write(buffer)
    buffer.seek(0)
    return PdfReader(buffer).pages[0]


def image_pdf(path: Path, pages: int = 1, rotate: int = 0) -> str:
    """A PDF whose pages are nothing but a picture: the shape of a scan."""
    image = Image.new("RGB", (850, 1100), "white")
    images = [image] * pages
    image.save(path, save_all=True, append_images=images[1:], resolution=100.0)

    if rotate:
        writer = PdfWriter(clone_from=str(path))
        for page in writer.pages:
            page[NameObject("/Rotate")] = NumberObject(rotate)
        buffer = io.BytesIO()
        writer.write(buffer)
        path.write_bytes(buffer.getvalue())

    return str(path)


def content_of(artifact: str, index: int = 0) -> str:
    """A page's whole content stream as text. Merging leaves `/Contents` as an array
    of streams - the page's own, then the layer's - so both have to be read."""
    contents = PdfReader(store.resolve(artifact)).pages[index]["/Contents"]
    streams = contents if isinstance(contents, ArrayObject) else [contents]
    return b"\n".join(stream.get_object().get_data() for stream in streams).decode(
        "latin-1"
    )


def operands_of(artifact: str, operator: bytes, index: int = 0) -> list:
    """Every use of one content stream operator, as lists of numbers.

    Assertions go through this rather than through the stream's text because pypdf
    reformats the numbers when it writes the merged page - "36.00" comes back as
    "36" - and a test should not break when that formatting changes.
    """
    page = PdfReader(store.resolve(artifact)).pages[index]
    stream = ContentStream(page["/Contents"], None)
    return [
        [float(value) if isinstance(value, (int, float)) else value for value in used]
        for used, name in stream.operations
        if name == operator
    ]


#: One line of tesseract TSV per word. Levels 1-4 (page, block, paragraph, line)
#: carry no text and a confidence of -1, exactly as tesseract emits them, because
#: the parser's job is to ignore them.
_TSV_HEADER = (
    "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\t"
    "left\ttop\twidth\theight\tconf\ttext"
)


def tsv(*words, page_px=(850, 1100)) -> str:
    """Build tesseract TSV. Each word is (left, top, width, height, conf, text,
    line) with `line` defaulting to 1."""
    rows = [_TSV_HEADER, f"1\t1\t0\t0\t0\t0\t0\t0\t{page_px[0]}\t{page_px[1]}\t-1\t"]
    for index, word in enumerate(words, start=1):
        left, top, width, height, conf, text = word[:6]
        line = word[6] if len(word) > 6 else 1
        rows.append(
            f"5\t1\t1\t1\t{line}\t{index}\t{left}\t{top}\t{width}\t{height}\t"
            f"{conf}\t{text}"
        )
    return "\n".join(rows) + "\n"


#: A plausible page: two lines, six words, all confident.
SAMPLE = tsv(
    (100, 100, 60, 20, 96.0, "Invoice", 1),
    (170, 100, 40, 20, 95.0, "number", 1),
    (220, 100, 50, 20, 91.0, "4021", 1),
    (100, 140, 70, 20, 88.0, "Amount", 2),
    (180, 140, 30, 20, 93.0, "due", 2),
    (220, 140, 60, 20, 42.0, "£19.40", 2),
)


@pytest.fixture
def fake_tesseract(monkeypatch):
    """Pretend tesseract is installed and returns whatever TSV a test asks for.

    Returns a setter, so a test can change the TSV without re-patching.
    """
    state = {"tsv": SAMPLE, "calls": []}

    monkeypatch.setattr(ocr_module, "find_tesseract", lambda: "/fake/tesseract")
    monkeypatch.setattr(ocr_module, "tesseract_version", lambda binary: "5.5.2")
    monkeypatch.setattr(
        ocr_module, "installed_languages", lambda binary: ["eng", "osd"]
    )

    def run(binary, png, lang, dpi):
        state["calls"].append({"lang": lang, "dpi": dpi, "bytes": len(png)})
        return state["tsv"]

    monkeypatch.setattr(ocr_module, "_run_tesseract", run)
    return state


@pytest.fixture
def capped(monkeypatch):
    """Lower the per-call page cap, so a test about truncation stays a short PDF.

    The real cap is deliberately high enough that a whole scan is one call, and a
    test that built a document past it would spend its time rendering pages rather
    than checking what the result says.
    """
    monkeypatch.setattr(ocr_module, "_MAX_PAGES", 10)


needs_tesseract = pytest.mark.skipif(
    ocr_module.find_tesseract() is None,
    reason="tesseract is a system install and is not present",
)


# --- reading ---------------------------------------------------------------


class TestReading:
    """Pages in, text and confidences out."""

    def test_reads_a_scan(self, tmp_path, fake_tesseract):
        data = ocr(image_pdf(tmp_path / "scan.pdf"))

        assert data["success"] is True
        assert data["pages_read"] == 1
        assert data["words"] == 6
        assert data["pages"][0]["text"] == "Invoice number 4021\nAmount due £19.40"

    def test_lines_come_back_as_lines(self, tmp_path, fake_tesseract):
        """Tesseract's line grouping is kept: it is what makes the text readable."""
        data = ocr(image_pdf(tmp_path / "scan.pdf"))

        assert data["pages"][0]["text"].count("\n") == 1

    def test_reports_confidence_per_page_and_overall(self, tmp_path, fake_tesseract):
        data = ocr(image_pdf(tmp_path / "scan.pdf"))
        page = data["pages"][0]

        assert page["mean_confidence"] == pytest.approx(84.2, abs=0.05)
        assert page["low_confidence_words"] == 1  # the 42.0 one
        assert data["mean_confidence"] == page["mean_confidence"]
        assert data["low_confidence_words"] == 1

    def test_low_confidence_is_named_in_the_summary(self, tmp_path, fake_tesseract):
        """The one thing this verb must never do is present a guess as a reading."""
        data = ocr(image_pdf(tmp_path / "scan.pdf"))

        assert "below 60" in data["summary"]
        assert "uncertain" in data["summary"]

    def test_structural_tsv_rows_are_not_words(self, tmp_path, fake_tesseract):
        """TSV has a row per layout level; only the word rows are words."""
        data = ocr(image_pdf(tmp_path / "scan.pdf"))

        assert data["words"] == 6  # not 7, with the page-level row counted

    def test_boxes_outside_the_page_are_dropped(self, tmp_path, fake_tesseract):
        fake_tesseract["tsv"] = tsv(
            (100, 100, 60, 20, 96.0, "real"),
            (100000, 100, 60, 20, 96.0, "elsewhere"),
            (-40, 100, 60, 20, 96.0, "negative"),
        )

        data = ocr(image_pdf(tmp_path / "scan.pdf"))

        assert data["words"] == 1
        assert data["pages"][0]["text"] == "real"

    def test_a_page_that_reads_as_nothing_says_so(self, tmp_path, fake_tesseract):
        fake_tesseract["tsv"] = tsv()

        data = ocr(image_pdf(tmp_path / "scan.pdf"))

        assert data["pages"][0]["words"] == 0
        assert "handwriting" in data["pages"][0]["note"]
        assert "handwriting" in data["summary"]

    def test_language_and_resolution_reach_tesseract(self, tmp_path, fake_tesseract):
        ocr(image_pdf(tmp_path / "scan.pdf"), lang="eng", dpi=300)

        assert fake_tesseract["calls"][0]["lang"] == "eng"
        assert fake_tesseract["calls"][0]["dpi"] == 300

    def test_resolution_is_clamped_not_refused(self, tmp_path, fake_tesseract):
        data = ocr(image_pdf(tmp_path / "scan.pdf"), dpi=5000)

        assert data["dpi"] == ocr_module._MAX_DPI

    def test_one_bad_page_is_not_a_bad_document(self, tmp_path, fake_tesseract):
        calls = {"n": 0}

        def flaky(binary, png, lang, dpi):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("tesseract exited 1")
            return SAMPLE

        ocr_module._run_tesseract = flaky
        try:
            data = ocr(image_pdf(tmp_path / "scan.pdf", pages=2))
        finally:
            pass  # monkeypatch on the fixture restores the original

        assert data["success"] is True
        assert data["pages_read"] == 1
        assert data["failed"][0]["page"] == 1
        assert "could not be read" in data["summary"]


# --- pages that already have text ------------------------------------------


class TestExistingText:
    """OCRing a page that can already be read makes it worse, not better."""

    def test_a_text_page_is_skipped(self, tmp_path, fake_tesseract):
        data = ocr(with_text(tmp_path / "text.pdf"))

        assert data["success"] is True
        assert data["pages_read"] == 0
        assert data["skipped"][0]["page"] == 1
        assert "already carries a text layer" in data["summary"]
        assert not fake_tesseract["calls"]  # and nothing was spent finding out

    def test_skipping_everything_is_success_not_failure(self, tmp_path, fake_tesseract):
        """The document is readable as it stands, which is what was asked."""
        data = ocr(with_text(tmp_path / "text.pdf", pages=2))

        assert data["success"] is True
        assert data["pages"] == []
        assert "force=True" in data["summary"]

    def test_force_reads_it_anyway(self, tmp_path, fake_tesseract):
        data = ocr(with_text(tmp_path / "text.pdf"), force=True)

        assert data["pages_read"] == 1
        assert data["skipped"] == []

    def test_a_scan_is_not_mistaken_for_a_text_page(self, tmp_path, fake_tesseract):
        data = ocr(image_pdf(tmp_path / "scan.pdf"))

        assert data["skipped"] == []
        assert data["pages_read"] == 1

    def test_a_page_we_already_ocred_is_skipped_next_time(
        self, tmp_path, fake_tesseract
    ):
        """A long document is OCRed a range at a time, so rounds can overlap.

        The character count cannot catch this: the words we recognized are all the
        page has, and six of them is far under the threshold a real text layer
        clears. Without the marker the page gets a second layer and every word in
        it twice.
        """
        first = ocr(image_pdf(tmp_path / "scan.pdf"), output="pdf")
        again = ocr(store.resolve(first["artifact"]), output="pdf")

        assert first["text_layer_pages"] == "1"
        assert again["pages_read"] == 0
        assert again["skipped"][0]["ocred_already"] is True
        assert again["skipped"][0]["existing_text_chars"] < ocr_module._TEXT_CHARS

    def test_force_grafts_over_our_own_layer(self, tmp_path, fake_tesseract):
        """`force` means force, even against a layer this tool wrote."""
        first = ocr(image_pdf(tmp_path / "scan.pdf"), output="pdf")
        again = ocr(store.resolve(first["artifact"]), output="pdf", force=True)

        assert again["pages_read"] == 1
        assert again["text_layer_pages"] == "1"

    def test_pdf_check_text_agrees_the_copy_no_longer_needs_ocr(
        self, tmp_path, fake_tesseract
    ):
        """The two verbs have to agree, and a shared threshold is not enough.

        Running pdf_check_text on a searchable copy reported verdict "scanned" and
        `needs_ocr: True`, because the recognized words are under `_TEXT_CHARS` and
        the page image is still there underneath - so every signal it reads still
        said scan. Seen on a real 25 page document that was in fact fully readable.
        """
        made = ocr(image_pdf(tmp_path / "scan.pdf", pages=3), output="pdf")

        checked = check_text(str(store.resolve(made["artifact"])))

        assert checked["verdict"] == "text"
        assert checked["needs_ocr"] is False
        assert checked["ocred_pages"] == 3
        assert "pdf_ocr" in checked["summary"]

    def test_pdf_check_text_still_wants_the_pages_we_did_not_reach(
        self, tmp_path, fake_tesseract, capped
    ):
        """Stopping early has to leave a document that says it is half done."""
        made = ocr(image_pdf(tmp_path / "scan.pdf", pages=12), output="pdf")

        checked = check_text(str(store.resolve(made["artifact"])))

        assert checked["verdict"] == "mixed"
        assert checked["needs_ocr"] is True
        assert checked["scanned_pages"] >= 1


# --- the text layer --------------------------------------------------------


class TestTextLayer:
    """The searchable copy: an invisible layer on the original page."""

    def test_text_extracts_back_out_of_the_artifact(self, tmp_path, fake_tesseract):
        data = ocr(image_pdf(tmp_path / "scan.pdf"), output="pdf")

        extracted = PdfReader(store.resolve(data["artifact"])).pages[0].extract_text()

        assert "Invoice" in extracted
        assert "4021" in extracted

    def test_words_stay_separate_words(self, tmp_path, fake_tesseract):
        """The bug this file exists to keep fixed: no spaces, no word boundaries."""
        data = ocr(image_pdf(tmp_path / "scan.pdf"), output="pdf")

        extracted = PdfReader(store.resolve(data["artifact"])).pages[0].extract_text()

        assert "Invoice number" in extracted
        assert "Invoicenumber" not in extracted

    def test_the_layer_is_invisible(self, tmp_path, fake_tesseract):
        """Text render mode 3. Without it the artifact has words printed over the
        scan, which looks like a rendering bug and hides the page."""
        data = ocr(image_pdf(tmp_path / "scan.pdf"), output="pdf")

        assert "3 Tr" in content_of(data["artifact"])

    def test_the_original_page_is_kept(self, tmp_path, fake_tesseract):
        """The point of grafting rather than re-embedding a render: the page's own
        content survives, and the file does not grow by an order of magnitude."""
        source = image_pdf(tmp_path / "scan.pdf")
        data = ocr(source, output="pdf")

        assert data["size_bytes"] < 2 * Path(source).stat().st_size
        page = PdfReader(store.resolve(data["artifact"])).pages[0]
        assert page.images  # the scan itself, still there

    def test_every_page_survives_even_when_one_was_read(self, tmp_path, fake_tesseract):
        data = ocr(image_pdf(tmp_path / "scan.pdf", pages=3), pages="2", output="pdf")

        assert len(PdfReader(store.resolve(data["artifact"])).pages) == 3
        assert data["text_layer_pages"] == "2"

    def test_no_blank_page_is_left_behind(self, tmp_path, fake_tesseract):
        """The overlay is scaffolding, built in a throwaway writer. A page count
        that grows is the symptom of it leaking into the output."""
        source = image_pdf(tmp_path / "scan.pdf", pages=2)
        data = ocr(source, output="pdf")

        assert len(PdfReader(store.resolve(data["artifact"])).pages) == 2

    def test_output_text_writes_no_artifact(self, tmp_path, fake_tesseract):
        data = ocr(image_pdf(tmp_path / "scan.pdf"), output="text")

        assert "artifact" not in data

    def test_output_pdf_leaves_the_transcription_out(self, tmp_path, fake_tesseract):
        """Asked for a PDF, not for the text: the artifact carries it."""
        data = ocr(image_pdf(tmp_path / "scan.pdf"), output="pdf")

        assert "text" not in data["pages"][0]
        assert data["pages"][0]["words"] == 6

    def test_output_both_gives_both(self, tmp_path, fake_tesseract):
        data = ocr(image_pdf(tmp_path / "scan.pdf"), output="both")

        assert data["pages"][0]["text"]
        assert data["artifact"]

    def test_an_unknown_output_is_refused(self, tmp_path, fake_tesseract):
        data = ocr(image_pdf(tmp_path / "scan.pdf"), output="sideways")

        assert data["success"] is False
        assert "text, pdf, both" in data["error"]

    def test_words_the_layer_cannot_encode_are_reported_not_mangled(
        self, tmp_path, fake_tesseract
    ):
        fake_tesseract["tsv"] = tsv(
            (100, 100, 60, 20, 96.0, "Total"),
            (170, 100, 60, 20, 96.0, "日本語"),
        )

        data = ocr(image_pdf(tmp_path / "scan.pdf"), output="both")

        assert data["unencodable_words"] == 1
        assert "日本語" in data["pages"][0]["text"]  # the text still has it
        assert "cannot encode" in data["summary"]

    def test_a_failed_pdf_still_returns_the_text(
        self, tmp_path, fake_tesseract, monkeypatch
    ):
        monkeypatch.setattr(
            ocr_module,
            "_write_searchable",
            lambda source, layers: (_ for _ in ()).throw(OSError("disk full")),
        )

        data = ocr(image_pdf(tmp_path / "scan.pdf"), output="both")

        assert data["success"] is True
        assert data["pages"][0]["text"]
        assert "disk full" in data["pdf_error"]
        assert "could not be built" in data["summary"]


class TestLayerGeometry:
    """Where the words land.

    The composed position of a word is awkward to assert on: pypdf merges the layer
    under a `cm` operator, and its text visitor reports the text matrix without
    composing that, so an extracted coordinate is in the layer's space rather than
    the page's. So the two halves are checked separately and exactly - the transform
    as a mapping, the layer as a matrix per line - and the composition of them was
    checked by eye during development, by giving the layer a colour and rendering it
    over the scan.
    """

    def test_a_word_lands_where_it_was_read(self, fake_tesseract, tmp_path):
        """A 200 dpi render of a 612pt page is 1700px, so a pixel is 0.36pt: a word
        at pixel (100, 100) that is 20px tall starts 36pt in, with its baseline
        792 - 120 * 0.36 = 748.8pt up."""
        fake_tesseract["tsv"] = tsv((100, 100, 60, 20, 96.0, "Corner"))

        data = ocr(image_pdf(tmp_path / "scan.pdf"), output="pdf")

        assert operands_of(data["artifact"], b"Tm") == [
            [1.0, 0.0, 0.0, 1.0, 36.0, 748.8]
        ]

    def test_a_line_is_stretched_to_the_width_it_occupied(
        self, fake_tesseract, tmp_path
    ):
        """Six characters of Courier at 7.2pt advance 0.6 * 7.2 * 6 = 25.92pt, and
        the line measured 60px = 21.6pt, so it is set at 83.3% of its natural
        width. This is what makes selecting a word select that word."""
        fake_tesseract["tsv"] = tsv((100, 100, 60, 20, 96.0, "Corner"))

        data = ocr(image_pdf(tmp_path / "scan.pdf"), output="pdf")

        assert operands_of(data["artifact"], b"Tf") == [["/F1", 7.2]]
        assert operands_of(data["artifact"], b"Tz") == [[83.3]]

    def test_the_transform_maps_the_view_onto_the_page(self):
        """The rendered view's corners have to land on the page's corners, and which
        corner goes where is the whole of what `/Rotate` means. A page 400 wide and
        600 tall, displayed at each rotation."""
        box = (0.0, 0.0, 400.0, 600.0)

        def corners(rotation, view):
            matrix = ocr_module._to_page_space(box, rotation)
            a, b, c, d, e, f = matrix
            return [
                (round(a * x + c * y + e, 1), round(b * x + d * y + f, 1))
                for x, y in view
            ]

        # Upright: the view is the page, corner for corner.
        assert corners(0, [(0, 0), (400, 0), (400, 600), (0, 600)]) == [
            (0.0, 0.0),
            (400.0, 0.0),
            (400.0, 600.0),
            (0.0, 600.0),
        ]

        # A quarter turn clockwise for the reader: the view is 600 x 400, and its
        # bottom left corner is the page's bottom right.
        assert corners(90, [(0, 0), (600, 0), (600, 400), (0, 400)]) == [
            (400.0, 0.0),
            (400.0, 600.0),
            (0.0, 600.0),
            (0.0, 0.0),
        ]

        # Upside down: opposite corners.
        assert corners(180, [(0, 0), (400, 0), (400, 600), (0, 600)]) == [
            (400.0, 600.0),
            (0.0, 600.0),
            (0.0, 0.0),
            (400.0, 0.0),
        ]

        # A quarter turn the other way.
        assert corners(270, [(0, 0), (600, 0), (600, 400), (0, 400)]) == [
            (0.0, 600.0),
            (0.0, 0.0),
            (400.0, 0.0),
            (400.0, 600.0),
        ]

    def test_the_transform_starts_at_the_visible_box(self):
        """The render covers the CropBox; the page's coordinates start at the
        MediaBox. The difference is an offset, and forgetting it puts a cropped
        page's layer in the margin."""
        matrix = ocr_module._to_page_space((50.0, 60.0, 500.0, 700.0), 0)

        assert matrix == (1.0, 0.0, 0.0, 1.0, 50.0, 60.0)

    def test_a_rotated_page_is_merged_through_that_transform(
        self, fake_tesseract, tmp_path
    ):
        """The integration half: the layer really is merged under the matrix, and an
        upright page really does skip it."""
        fake_tesseract["tsv"] = tsv((100, 100, 60, 20, 96.0, "Corner"))

        upright = ocr(image_pdf(tmp_path / "a.pdf"), output="pdf")
        turned = ocr(image_pdf(tmp_path / "b.pdf", rotate=90), output="pdf")

        assert [0.0, 1.0, -1.0, 0.0, 612.0, 0.0] in operands_of(
            turned["artifact"], b"cm"
        )
        assert [1.0, 0.0, 0.0, 1.0, 0.0, 0.0] in operands_of(upright["artifact"], b"cm")

    def test_a_cropped_page_is_merged_through_that_offset(
        self, fake_tesseract, tmp_path
    ):
        fake_tesseract["tsv"] = tsv((0, 0, 60, 20, 96.0, "Corner"))
        source = image_pdf(tmp_path / "scan.pdf")

        writer = PdfWriter(clone_from=source)
        writer.pages[0][NameObject("/CropBox")] = ArrayObject(
            [FloatObject(value) for value in (50, 60, 500, 700)]
        )
        buffer = io.BytesIO()
        writer.write(buffer)
        (tmp_path / "cropped.pdf").write_bytes(buffer.getvalue())

        data = ocr(str(tmp_path / "cropped.pdf"), output="pdf")

        assert [1.0, 0.0, 0.0, 1.0, 50.0, 60.0] in operands_of(data["artifact"], b"cm")

    def test_a_sideways_page_gets_no_layer_but_keeps_its_text(
        self, tmp_path, fake_tesseract
    ):
        """Tesseract reads a page that displays sideways, and reports its boxes
        transposed without saying so. A layer from those would be scattered."""
        fake_tesseract["tsv"] = tsv(
            *(
                (100, 100 + 30 * index, 20, 60, 95.0, f"word{index}", index + 1)
                for index in range(20)
            )
        )

        data = ocr(image_pdf(tmp_path / "scan.pdf"), output="both")

        assert data["pages"][0]["words"] == 20  # the text is fine
        assert data["text_layer_skipped"] == "1"
        assert "read sideways" in data["summary"]

    def test_upright_text_is_not_taken_for_sideways(self, tmp_path, fake_tesseract):
        fake_tesseract["tsv"] = tsv(
            *(
                (100, 100 + 30 * index, 60, 20, 95.0, f"word{index}", index + 1)
                for index in range(20)
            )
        )

        data = ocr(image_pdf(tmp_path / "scan.pdf"), output="both")

        assert "text_layer_skipped" not in data
        assert data["text_layer_pages"] == "1"


# --- page selection --------------------------------------------------------


class TestPageSelection:
    """Which pages, and how many at once."""

    def test_a_range_selects_pages(self, tmp_path, fake_tesseract):
        data = ocr(image_pdf(tmp_path / "scan.pdf", pages=5), pages="2,4")

        assert [entry["page"] for entry in data["pages"]] == [2, 4]

    def test_the_per_call_limit_is_reported(self, tmp_path, fake_tesseract, capped):
        data = ocr(image_pdf(tmp_path / "scan.pdf", pages=12), pages="all")

        assert data["pages_read"] == ocr_module._MAX_PAGES == 10
        assert data["truncated"] is True
        assert data["pages_remaining"] == "11-12"
        assert data["stopped_for"] == "page_limit"
        assert "page 11-12 was not read" in data["summary"]
        assert 'pages="11-12"' in data["summary"]

    def test_a_whole_scan_is_usually_one_call(self, tmp_path, fake_tesseract):
        """The real cap, unpatched. A caller chaining ranges is the thing that goes
        wrong - it produced three copies each searchable on a third in a real client -
        so the limit is set high enough that most documents never reach it."""
        data = ocr(image_pdf(tmp_path / "scan.pdf", pages=25), pages="all")

        assert data["pages_read"] == 25
        assert data["truncated"] is False

    def test_a_slow_document_stops_on_time_and_says_where_it_got_to(
        self, tmp_path, fake_tesseract, monkeypatch
    ):
        """Per page cost varies about tenfold - a sparse page against a noisy two
        column scan - so a page count cannot bound how long a call takes. A client
        that gives up waiting returns nothing at all: no text, no artifact, no way to
        continue. So the budget stops us first and reports the pages left."""
        monkeypatch.setattr(ocr_module, "_TIME_BUDGET_S", 0.0)

        data = ocr(image_pdf(tmp_path / "scan.pdf", pages=6), pages="all")

        assert data["pages_read"] == 1  # never fewer: a budget of nothing still reads
        assert data["truncated"] is True
        assert data["pages_remaining"] == "2-6"
        assert data["stopped_for"] == "time"
        assert "second budget" in data["summary"]
        assert "page 2-6 was not read" in data["summary"]

    def test_a_document_inside_the_budget_is_not_cut_short(
        self, tmp_path, fake_tesseract
    ):
        data = ocr(image_pdf(tmp_path / "scan.pdf", pages=4), pages="all")

        assert data["pages_read"] == 4
        assert "stopped_for" not in data
        assert "budget" not in data["summary"]

    def test_continuing_a_searchable_copy_says_to_pass_the_artifact_back(
        self, tmp_path, fake_tesseract, capped
    ):
        """The one instruction a caller cannot guess, so the summary has to give it.

        Asking for a later range of the *original* is the obvious reading of "the
        rest were not read", and it leaves you with one copy per range, each carrying
        only its own text layer and no verb in this package that merges them. Watched
        happen with a real client, which is why the summary now names the artifact.
        """
        data = ocr(
            image_pdf(tmp_path / "scan.pdf", pages=12), pages="all", output="pdf"
        )

        assert f"ref={data['artifact']}" in data["summary"]
        assert 'pages="11-12"' in data["summary"]

    def test_a_finished_document_is_not_told_to_continue(
        self, tmp_path, fake_tesseract
    ):
        data = ocr(image_pdf(tmp_path / "scan.pdf", pages=2), pages="all", output="pdf")

        assert data["truncated"] is False
        assert "call pdf_ocr again" not in data["summary"]
        assert "was not read" not in data["summary"]

    def test_an_unreadable_range_says_so(self, tmp_path, fake_tesseract):
        data = ocr(image_pdf(tmp_path / "scan.pdf"), pages="sideways")

        assert data["success"] is False
        assert "page or page range" in data["error"]


# --- tesseract itself ------------------------------------------------------


class TestTesseractAvailability:
    """The system install: absent, elsewhere, or missing a language."""

    def test_absence_is_reported_with_a_way_out(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ocr_module, "find_tesseract", lambda: None)

        data = ocr(image_pdf(tmp_path / "scan.pdf"))

        assert data["success"] is False
        assert data["tesseract_installed"] is False
        assert "install" in data["error"]
        assert "pdf_render_pages" in data["error"]  # the fallback
        assert ocr_module.TESSERACT_ENV_VAR in data["error"]

    def test_the_binary_is_looked_for_on_every_call(self, tmp_path, monkeypatch):
        """Not cached, so installing tesseract and retrying works without a
        restart. A cache would make the first error permanent for the session."""
        monkeypatch.delenv(ocr_module.TESSERACT_ENV_VAR, raising=False)
        monkeypatch.setattr(shutil, "which", lambda name: None)
        monkeypatch.setattr(ocr_module, "_BINARY_CANDIDATES", ())

        assert ocr_module.find_tesseract() is None

        stub = tmp_path / "tesseract"
        stub.write_text("#!/bin/sh\n")
        stub.chmod(0o755)
        monkeypatch.setenv(ocr_module.TESSERACT_ENV_VAR, str(stub))

        assert ocr_module.find_tesseract() == str(stub)

    def test_a_known_location_is_used_when_the_path_has_nothing(
        self, tmp_path, monkeypatch
    ):
        """GUI clients launch the server with a minimal PATH that has no Homebrew
        prefix on it, which is the common way for an install to look absent."""
        monkeypatch.delenv(ocr_module.TESSERACT_ENV_VAR, raising=False)
        monkeypatch.setattr(shutil, "which", lambda name: None)

        stub = tmp_path / "tesseract"
        stub.write_text("#!/bin/sh\n")
        monkeypatch.setattr(ocr_module, "_BINARY_CANDIDATES", (str(stub),))

        assert ocr_module.find_tesseract() == str(stub)

    def test_a_missing_language_is_refused_not_guessed(self, tmp_path, fake_tesseract):
        """Reading German with English data returns confident nonsense, which is
        worse than an error saying which language is missing."""
        data = ocr(image_pdf(tmp_path / "scan.pdf"), lang="deu")

        assert data["success"] is False
        assert "no data for deu" in data["error"]
        assert data["languages_installed"] == ["eng", "osd"]
        assert "install" in data["error"]

    def test_several_languages_are_allowed(self, tmp_path, fake_tesseract):
        data = ocr(image_pdf(tmp_path / "scan.pdf"), lang="eng+osd")

        assert data["success"] is True

    def test_an_unlistable_install_is_tried_anyway(
        self, tmp_path, fake_tesseract, monkeypatch
    ):
        """An empty language list means "could not tell", never "none installed"."""
        monkeypatch.setattr(ocr_module, "installed_languages", lambda binary: [])

        data = ocr(image_pdf(tmp_path / "scan.pdf"), lang="deu")

        assert data["success"] is True


# --- files that will not cooperate -----------------------------------------


class TestBadInput:
    """A reason, never a traceback."""

    def test_a_missing_file(self, tmp_path, fake_tesseract):
        data = ocr(str(tmp_path / "nope.pdf"))

        assert data["success"] is False
        assert data["file_exists"] is False

    def test_a_file_that_is_not_a_pdf(self, tmp_path, fake_tesseract):
        path = tmp_path / "not.pdf"
        path.write_bytes(b"this is not a PDF at all")

        data = ocr(str(path))

        assert data["success"] is False
        assert "as a PDF" in data["error"]

    def test_an_encrypted_file_names_the_tool_that_can_read_it(
        self, tmp_path, fake_tesseract
    ):
        data = ocr(blank(tmp_path / "locked.pdf", encrypt="secret"))

        assert data["success"] is False
        assert data["encrypted"] is True
        assert "pdf_check_access" in data["error"]


# --- against the real thing ------------------------------------------------


@needs_tesseract
class TestRealTesseract:
    """The handful of things a fake cannot answer. Skipped where tesseract is not
    installed, which includes CI."""

    def test_it_reads_text_off_a_rendered_page(self, tmp_path):
        """End to end: real words, rendered as pixels, read back."""
        source = with_text(tmp_path / "text.pdf")

        data = ocr(source, force=True, output="both")

        assert data["success"] is True
        assert "Readable" in data["pages"][0]["text"]
        assert data["mean_confidence"] > 60
        extracted = PdfReader(store.resolve(data["artifact"])).pages[0].extract_text()
        assert "Readable words" in extracted

    def test_it_reports_its_version_and_languages(self):
        binary = ocr_module.find_tesseract()

        assert ocr_module.tesseract_version(binary)[0].isdigit()
        assert "eng" in ocr_module.installed_languages(binary)

    def test_a_blank_page_reads_as_nothing_rather_than_failing(self, tmp_path):
        data = ocr(image_pdf(tmp_path / "blank.pdf"))

        assert data["success"] is True
        assert data["pages"][0]["words"] == 0
