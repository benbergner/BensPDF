"""Tests for checking whether a PDF has a usable text layer.

The cases that matter are the ones where a single signal lies: a scan with a
little stamped-on text, a brochure page whose photos and short heading look like
a scan until you measure them, a text document with a blank divider page, and a
document long enough that only part of it is read.

PDFs here are built by hand rather than with a rendering library, since the
detection only reads content streams and resource dictionaries. Image data is
never decoded, so the fixtures carry one byte of it; what matters is the
transformation each image is drawn with, which is written out explicitly.
"""

import io
import json
import random
from pathlib import Path

from pypdf import PdfWriter
from pypdf._page import PageObject
from pypdf.errors import LimitReachedError, PdfReadError
from pypdf.generic import (
    ArrayObject,
    DecodedStreamObject,
    DictionaryObject,
    FloatObject,
    NameObject,
    NumberObject,
)

from benspdf import check_text
from benspdf.tools import check_text as check_text_module

#: Long enough to clear the "this is a real text layer" threshold.
PARAGRAPH = "Lorem ipsum dolor sit amet consectetur adipiscing elit sed do " * 4


def new_writer() -> PdfWriter:
    return PdfWriter()


def blank_page(writer: PdfWriter) -> None:
    """A page with no text and no images."""
    writer.add_blank_page(width=612, height=792)


def font_resource() -> DictionaryObject:
    """One Type 1 font, enough for a content stream to draw text with."""
    return DictionaryObject(
        {
            NameObject("/F1"): DictionaryObject(
                {
                    NameObject("/Type"): NameObject("/Font"),
                    NameObject("/Subtype"): NameObject("/Type1"),
                    NameObject("/BaseFont"): NameObject("/Helvetica"),
                }
            )
        }
    )


def image_xobject() -> DecodedStreamObject:
    """A minimal image XObject. Its pixels are never read, only its dictionary."""
    image = DecodedStreamObject()
    image.set_data(b"\x00")
    image[NameObject("/Type")] = NameObject("/XObject")
    image[NameObject("/Subtype")] = NameObject("/Image")
    image[NameObject("/Width")] = NumberObject(1)
    image[NameObject("/Height")] = NumberObject(1)
    return image


def add_page(writer: PdfWriter, body: bytes, xobjects=None) -> None:
    """A US Letter page drawing ``body``, with a font and any given XObjects."""
    page = writer.add_blank_page(width=612, height=792)
    stream = DecodedStreamObject()
    stream.set_data(body)
    page[NameObject("/Contents")] = stream

    resources = {NameObject("/Font"): font_resource()}
    if xobjects:
        resources[NameObject("/XObject")] = DictionaryObject(xobjects)
    page[NameObject("/Resources")] = DictionaryObject(resources)


def draw_text(text: str, x: int = 72, y: int = 720) -> bytes:
    return f"BT /F1 12 Tf {x} {y} Td ({text}) Tj ET".encode()


def text_page(writer: PdfWriter, text: str = PARAGRAPH) -> None:
    """A page whose content stream draws real, extractable text."""
    add_page(writer, draw_text(text))


def image_page(writer: PdfWriter, text: str = "") -> None:
    """A scanned-looking page: one image covering it, plus optional stamped text.

    The optional text is the scanner-stamp case — a page number or a date banner
    over an otherwise unreadable image.
    """
    body = b"q 612 0 0 792 0 0 cm /Im1 Do Q"
    if text:
        body += b" " + draw_text(text, y=40)
    add_page(writer, body, {NameObject("/Im1"): image_xobject()})


def illustrated_page(writer: PdfWriter, text: str = "A slide title") -> None:
    """The lookalike: three modest photos and a heading, as a brochure or slide.

    Has images and too little text to read, exactly like a scan, and is not one.
    Each image covers about a twelfth of the page.
    """
    body = (
        b"q 200 0 0 150 20 600 cm /Im1 Do Q "
        b"q 200 0 0 150 240 600 cm /Im2 Do Q "
        b"q 200 0 0 150 20 400 cm /Im3 Do Q " + draw_text(text)
    )
    add_page(
        writer,
        body,
        {
            NameObject("/Im1"): image_xobject(),
            NameObject("/Im2"): image_xobject(),
            NameObject("/Im3"): image_xobject(),
        },
    )


def nested_image_page(writer: PdfWriter, matrix=None) -> None:
    """A page whose image sits inside a Form XObject rather than on the page.

    ``matrix`` sets the form's /Matrix, which scales everything the form draws.
    """
    form = DecodedStreamObject()
    form.set_data(b"q 612 0 0 792 0 0 cm /Im1 Do Q")
    form[NameObject("/Type")] = NameObject("/XObject")
    form[NameObject("/Subtype")] = NameObject("/Form")
    form[NameObject("/Resources")] = DictionaryObject(
        {
            NameObject("/XObject"): DictionaryObject(
                {NameObject("/Im1"): image_xobject()}
            )
        }
    )
    if matrix:
        form[NameObject("/Matrix")] = ArrayObject(
            [FloatObject(value) for value in matrix]
        )

    add_page(writer, b"/Fm1 Do", {NameObject("/Fm1"): form})


def save(writer: PdfWriter, path, info=None, password=None) -> str:
    """Write a built document out and return its path."""
    if info:
        writer.add_metadata(info)
    if password:
        writer.encrypt(password)
    buffer = io.BytesIO()
    writer.write(buffer)
    path.write_bytes(buffer.getvalue())
    return str(path)


def build(path, *builders, info=None, password=None) -> str:
    """Build a PDF from page builder functions, in order."""
    writer = new_writer()
    for builder in builders:
        builder(writer)
    return save(writer, path, info=info, password=password)


class TestVerdicts:
    """The four answers, and the cases that distinguish them."""

    def test_text_document_needs_no_ocr(self, tmp_path):
        pdf = build(tmp_path / "text.pdf", text_page, text_page, text_page)

        data = check_text(pdf)

        assert data["success"] is True
        assert data["verdict"] == "text"
        assert data["needs_ocr"] is False
        assert data["has_text_layer"] is True
        assert data["pages_with_text"] == 3
        assert data["scanned_pages"] == 0

    def test_scan_needs_ocr(self, tmp_path):
        pdf = build(tmp_path / "scan.pdf", image_page, image_page)

        data = check_text(pdf)

        assert data["verdict"] == "scanned"
        assert data["needs_ocr"] is True
        assert data["has_text_layer"] is False
        assert data["scanned_pages"] == 2

    def test_document_with_both_kinds_of_page_is_mixed(self, tmp_path):
        pdf = build(tmp_path / "mixed.pdf", text_page, image_page, text_page)

        data = check_text(pdf)

        assert data["verdict"] == "mixed"
        assert data["needs_ocr"] is True
        assert data["has_text_layer"] is True
        assert data["pages_with_text"] == 2
        assert data["scanned_pages"] == 1

    def test_blank_pages_are_not_a_scan(self, tmp_path):
        """No text and no images: OCR has nothing to work on, so don't ask for it."""
        pdf = build(tmp_path / "blank.pdf", blank_page, blank_page)

        data = check_text(pdf)

        assert data["verdict"] == "no_text"
        assert data["needs_ocr"] is False
        assert data["has_text_layer"] is False
        assert data["scanned_pages"] == 0

    def test_a_blank_page_does_not_make_a_text_document_mixed(self, tmp_path):
        """Divider pages are normal. Only image pages mean part of it needs OCR."""
        pdf = build(tmp_path / "divider.pdf", text_page, blank_page, text_page)

        data = check_text(pdf)

        assert data["verdict"] == "text"
        assert data["needs_ocr"] is False

    def test_document_with_no_pages_says_so(self, tmp_path):
        pdf = build(tmp_path / "empty.pdf")

        data = check_text(pdf)

        assert data["success"] is True
        assert data["page_count"] == 0
        assert data["verdict"] == "no_text"
        assert data["mean_characters_per_page"] == 0.0
        assert "no pages" in data["summary"]

    def test_scanner_stamp_does_not_count_as_a_text_layer(self, tmp_path):
        """A few characters over a page image is still an unreadable page."""
        pdf = build(
            tmp_path / "stamped.pdf",
            lambda writer: image_page(writer, text="Page 1 of 2"),
            lambda writer: image_page(writer, text="Page 2 of 2"),
        )

        data = check_text(pdf)

        assert data["verdict"] == "scanned"
        assert data["needs_ocr"] is True
        assert data["characters_sampled"] > 0, "the stamp text was found"
        assert data["pages_with_text"] == 0, "but it is below the threshold"
        assert data["pages"][0]["characters"] < data["text_threshold_chars"]


class TestScanLookalikes:
    """Images plus little text is not enough. It has to be one image, page sized.

    This is the whole reason coverage is measured: on real files the naive rule
    called every brochure and slide deck a scan.
    """

    def test_illustrated_page_is_not_a_scan(self, tmp_path):
        pdf = build(tmp_path / "brochure.pdf", illustrated_page, illustrated_page)

        data = check_text(pdf)

        assert data["scanned_pages"] == 0
        assert data["needs_ocr"] is False
        assert data["verdict"] == "no_text"
        assert data["pages"][0]["images"] == 3
        assert data["pages"][0]["image_coverage"] < data["scan_coverage_threshold"]

    def test_illustrated_pages_do_not_drag_a_text_document_into_mixed(self, tmp_path):
        pdf = build(tmp_path / "deck.pdf", text_page, illustrated_page, text_page)

        data = check_text(pdf)

        assert data["verdict"] == "text"
        assert data["needs_ocr"] is False

    def test_a_page_sized_image_is_a_scan(self, tmp_path):
        pdf = build(tmp_path / "scan.pdf", image_page)

        entry = check_text(pdf)["pages"][0]

        assert entry["image_coverage"] == 1.0
        assert entry["looks_scanned"] is True


class TestImageGeometry:
    """Coverage comes from the content stream's matrices, so follow them properly."""

    def test_image_inside_a_form_xobject_is_found(self, tmp_path):
        pdf = build(tmp_path / "nested.pdf", nested_image_page)

        data = check_text(pdf)

        assert data["pages"][0]["images"] == 1
        assert data["pages"][0]["image_coverage"] == 1.0
        assert data["verdict"] == "scanned"

    def test_a_form_matrix_scales_what_the_form_draws(self, tmp_path):
        """Halving both axes inside the form leaves a quarter of the page covered."""
        pdf = build(
            tmp_path / "scaled.pdf",
            lambda writer: nested_image_page(writer, matrix=[0.5, 0, 0, 0.5, 0, 0]),
        )

        entry = check_text(pdf)["pages"][0]

        assert entry["image_coverage"] == 0.25
        assert entry["looks_scanned"] is False, "a quarter page image is not a scan"

    def test_declared_but_undrawn_images_do_not_count(self, tmp_path):
        """An XObject left in the resources by an editor is not on the page."""
        writer = new_writer()
        add_page(
            writer,
            draw_text(PARAGRAPH),
            {NameObject("/Im1"): image_xobject()},
        )
        pdf = save(writer, tmp_path / "leftover.pdf")

        entry = check_text(pdf)["pages"][0]

        assert entry["images"] == 0
        assert entry["image_coverage"] == 0.0

    def test_unparseable_content_falls_back_to_declared_images(
        self, tmp_path, monkeypatch
    ):
        """Without geometry, an image page with no text at all is still a scan."""
        pdf = build(tmp_path / "fallback.pdf", image_page)

        def unwalkable(*args, **kwargs):
            raise PdfReadError("cannot parse content stream")

        monkeypatch.setattr(check_text_module, "ContentStream", unwalkable)

        entry = check_text(pdf)["pages"][0]

        assert entry["image_coverage"] is None, "geometry is unknown, not zero"
        assert entry["images"] == 1, "counted from the resource dictionary instead"
        assert entry["looks_scanned"] is True


class TestPageDetail:
    """The per page evidence behind the verdict."""

    def test_each_examined_page_is_reported(self, tmp_path):
        pdf = build(tmp_path / "detail.pdf", text_page, image_page)

        data = check_text(pdf)

        assert [entry["page"] for entry in data["pages"]] == [1, 2]

        first, second = data["pages"]
        assert first["characters"] >= data["text_threshold_chars"]
        assert first["images"] == 0
        assert first["image_coverage"] == 0.0
        assert first["has_text"] is True
        assert first["looks_scanned"] is False

        assert second["images"] == 1
        assert second["image_coverage"] == 1.0
        assert second["has_text"] is False
        assert second["looks_scanned"] is True

    def test_counts_and_means_cover_the_sample(self, tmp_path):
        pdf = build(tmp_path / "counts.pdf", text_page, text_page)

        data = check_text(pdf)

        total = sum(entry["characters"] for entry in data["pages"])
        assert data["characters_sampled"] == total
        assert data["mean_characters_per_page"] == round(total / 2, 1)
        assert data["unreadable_pages"] == 0


class TestSampling:
    """Cheap by default: long documents are estimated, and say so."""

    def test_short_document_is_read_in_full(self, tmp_path):
        pdf = build(tmp_path / "short.pdf", *([text_page] * 4))

        data = check_text(pdf)

        assert data["page_count"] == 4
        assert data["pages_examined"] == 4
        assert data["sampled"] is False

    def test_long_document_is_sampled_across_its_whole_length(self, tmp_path):
        pdf = build(tmp_path / "long.pdf", *([text_page] * 40))

        data = check_text(pdf)

        assert data["page_count"] == 40
        assert data["pages_examined"] == 10
        assert data["sampled"] is True

        numbers = [entry["page"] for entry in data["pages"]]
        assert numbers[0] == 1, "the first page is always read"
        assert numbers[-1] == 40, "and so is the last"
        assert numbers == sorted(numbers)

    def test_sampling_is_disclosed_in_the_summary(self, tmp_path):
        pdf = build(tmp_path / "disclose.pdf", *([text_page] * 40))

        data = check_text(pdf)

        assert "sampled" in data["summary"]
        assert "40" in data["summary"]

    def test_a_scan_at_the_end_is_still_caught(self, tmp_path):
        """Text pages up front then scanned appendices: the ends are sampled."""
        builders = [text_page] * 30 + [image_page] * 10
        pdf = build(tmp_path / "appendix.pdf", *builders)

        data = check_text(pdf)

        assert data["verdict"] == "mixed"
        assert data["needs_ocr"] is True


class TestTextExcerpt:
    """A character count cannot tell readable text from OCR gibberish."""

    def test_excerpt_comes_from_the_first_page_with_text(self, tmp_path):
        pdf = build(tmp_path / "excerpt.pdf", image_page, text_page)

        data = check_text(pdf)

        assert data["text_excerpt"].startswith("Lorem ipsum")

    def test_excerpt_is_truncated(self, tmp_path):
        pdf = build(tmp_path / "longtext.pdf", lambda w: text_page(w, PARAGRAPH * 4))

        data = check_text(pdf)

        assert len(data["text_excerpt"]) <= 201, "200 characters plus an ellipsis"
        assert data["text_excerpt"].endswith("…")

    def test_no_excerpt_when_there_is_no_text(self, tmp_path):
        pdf = build(tmp_path / "noexcerpt.pdf", image_page)

        data = check_text(pdf)

        assert data["text_excerpt"] is None


class TestWritingToolHints:
    """Producer and Creator are evidence, not the verdict."""

    def test_scanner_producer_is_flagged(self, tmp_path):
        pdf = build(
            tmp_path / "canon.pdf",
            image_page,
            info={"/Producer": "Canon iR-ADV C5550 Scanner"},
        )

        data = check_text(pdf)

        assert data["producer"] == "Canon iR-ADV C5550 Scanner"
        assert data["producer_suggests_scanner"] is True

    def test_ocr_producer_warns_that_text_may_be_wrong(self, tmp_path):
        pdf = build(
            tmp_path / "abbyy.pdf",
            text_page,
            info={"/Producer": "ABBYY FineReader 15"},
        )

        data = check_text(pdf)

        assert data["verdict"] == "text"
        assert data["producer_suggests_ocr"] is True
        assert "OCR" in data["summary"]

    def test_creator_is_read_too(self, tmp_path):
        """/Creator is the authoring application, so it carries the same hint."""
        pdf = build(
            tmp_path / "creator.pdf",
            image_page,
            info={"/Creator": "Epson Scan 2", "/Author": "Ada Lovelace"},
        )

        data = check_text(pdf)

        assert data["creator_tool"] == "Epson Scan 2"
        assert data["producer_suggests_scanner"] is True

    def test_an_ordinary_producer_is_not_flagged(self, tmp_path):
        pdf = build(
            tmp_path / "word.pdf",
            text_page,
            info={"/Producer": "Microsoft Word for Microsoft 365"},
        )

        data = check_text(pdf)

        assert data["producer_suggests_scanner"] is False
        assert data["producer_suggests_ocr"] is False

    def test_absent_properties_are_null_not_an_error(self, tmp_path):
        """A file that names no authoring tool still gets a verdict."""
        pdf = build(tmp_path / "bare.pdf", text_page)

        data = check_text(pdf)

        assert data["success"] is True
        assert data["creator_tool"] is None
        assert data["producer_suggests_scanner"] is False
        assert data["producer_suggests_ocr"] is False
        assert data["verdict"] == "text"


class TestCropBox:
    """Coverage is measured against the page anyone actually sees."""

    def test_a_cropped_page_measures_against_its_cropbox(self, tmp_path):
        """An image filling the visible page is a scan, whatever the MediaBox says.

        Rare in the wild — one file in a 141 file corpus — but a CropBox trimming
        a page to a third of its MediaBox would drop a real scan to 0.33 coverage
        and hide it.
        """
        writer = new_writer()
        add_page(
            writer,
            b"q 306 0 0 396 0 0 cm /Im1 Do Q",  # fills the CropBox, a quarter of the MediaBox
            {NameObject("/Im1"): image_xobject()},
        )
        writer.pages[0][NameObject("/CropBox")] = ArrayObject(
            [FloatObject(v) for v in (0, 0, 306, 396)]
        )
        pdf = save(writer, tmp_path / "cropped.pdf")

        entry = check_text(pdf)["pages"][0]

        assert entry["image_coverage"] == 1.0
        assert entry["looks_scanned"] is True


class TestFailures:
    """Failures return a reason instead of raising."""

    def test_missing_file(self, tmp_path):
        data = check_text(str(tmp_path / "nope.pdf"))

        assert data["success"] is False
        assert "not found" in data["error"].lower()
        assert data["file_exists"] is False

    def test_not_a_pdf(self, tmp_path):
        not_pdf = tmp_path / "notes.txt"
        not_pdf.write_text("This is not a PDF")

        data = check_text(str(not_pdf))

        assert data["success"] is False
        assert "error" in data

    def test_encrypted_file_explains_itself(self, tmp_path):
        pdf = build(tmp_path / "locked.pdf", text_page, password="pw")

        data = check_text(pdf)

        assert data["success"] is False
        assert "encrypted" in data["error"].lower()
        assert data["encrypted"] is True

    def test_a_pypdf_error_outside_pdfreaderror_is_still_handled(
        self, tmp_path, monkeypatch
    ):
        """Half of pypdf's exceptions are not PdfReadError.

        LimitReachedError, DependencyError (an AES file with no `cryptography`
        installed), ParseError and PageSizeNotDefinedError all sit on the other
        branch of the hierarchy. Catching a list of expected types let these out as
        tracebacks on ~3% of a corrupted corpus.
        """
        pdf = build(tmp_path / "limits.pdf", text_page)

        def over_the_limit(*args, **kwargs):
            raise LimitReachedError("too many objects")

        monkeypatch.setattr(check_text_module, "PdfReader", over_the_limit)

        data = check_text(pdf)

        assert data["success"] is False
        assert "LimitReachedError" in data["error"], "name the type, don't hide it"

    def test_corrupted_files_never_raise(self, tmp_path):
        """Deterministic stand-in for the fuzz run: mangle a PDF many ways."""
        source = Path(
            build(tmp_path / "source.pdf", text_page, image_page)
        ).read_bytes()
        rng = random.Random(0)
        target = tmp_path / "mangled.pdf"

        for trial in range(40):
            data = bytearray(source)
            mode = trial % 4
            if mode == 0:  # truncated
                data = data[: rng.randint(1, len(data))]
            elif mode == 1:  # bytes flipped
                for _ in range(rng.randint(1, 40)):
                    data[rng.randrange(len(data))] = rng.randrange(256)
            elif mode == 2:  # a chunk spliced out
                start = rng.randrange(len(data))
                del data[start : start + rng.randint(1, 800)]
            else:  # not a PDF at all
                data = bytearray(rng.randbytes(rng.randint(1, 500)))

            target.write_bytes(bytes(data))
            result = check_text(str(target))  # the assertion is that this returns

            assert result["success"] in (True, False)
            if not result["success"]:
                assert result["error"]

    def test_one_unreadable_page_does_not_fail_the_check(self, tmp_path, monkeypatch):
        """A page pypdf chokes on costs that page, not the whole answer.

        Forced rather than built: a page malformed enough to break extraction
        cannot be written by pypdf, which quietly extracts nothing instead.
        """
        pdf = build(tmp_path / "flaky.pdf", text_page, text_page, text_page)

        original = PageObject.extract_text
        seen = []

        def flaky(self, *args, **kwargs):
            seen.append(self)
            if len(seen) == 2:
                raise PdfReadError("malformed content stream")
            return original(self, *args, **kwargs)

        monkeypatch.setattr(PageObject, "extract_text", flaky)

        data = check_text(pdf)

        assert data["success"] is True
        assert data["pages_examined"] == 3
        assert data["unreadable_pages"] == 1
        assert "malformed content stream" in data["pages"][1]["error"]
        assert data["pages"][1]["characters"] == 0
        assert data["verdict"] == "text", "the readable pages still answer"


class TestJsonSafety:
    """Results cross an MCP boundary, so every value has to serialize."""

    def test_result_is_json_serializable(self, tmp_path):
        pdf = build(
            tmp_path / "json.pdf",
            text_page,
            image_page,
            blank_page,
            info={"/Producer": "Canon Scanner"},
        )

        data = check_text(pdf)

        json.dumps(data)  # raises TypeError on pypdf objects
