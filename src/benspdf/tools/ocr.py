"""
Reading a scan: the work behind `pdf_ocr`.

This is the *reading* path for a document with no text layer. It renders each
page, hands the pixels to tesseract, and returns what tesseract read — plus, on
request, a copy of the PDF with a real text layer grafted on, which is searchable,
extractable, and legible to a local text model in a way an image never is.

Four decisions shape the file:

**The text layer goes onto the original page, not over a re-render of it.**
Tesseract will happily write a searchable PDF itself, and it is one flag. But that
PDF contains *our raster*, not the user's page: measured on a 5 page form, three
pages came back as 1.1 MB against 0.1 MB for the whole original, and any vector
content on the page had become pixels. So the render is used for recognition only
and thrown away, and what is written is the original page with an invisible text
layer merged on top — the "sandwich" ocrmypdf makes, without requiring ocrmypdf
and, through it, Ghostscript.

**One system binary, resolved when the tool is called.** `uvx benspdf-mcp` installs
a wheel; it cannot install tesseract. So the verb is always registered, does no
work at import time, and looks for the binary on the first call — reporting its
absence with the install command for the platform it is running on and pointing at
`pdf_render_pages` as the fallback. Nothing here imports a Python OCR wrapper:
tesseract reads a PNG on stdin and writes TSV on stdout, which is all we need.

**Confidence is reported, because OCR is the one verb here that can be confidently
wrong.** Every other tool in this package either knows an answer or fails. OCR
returns plausible words that were never on the page, and silence about that would
be the worst failure mode in the library. Tesseract gives a per word confidence, so
each page reports how many words it found, their mean confidence, and how many
landed below `_LOW_CONFIDENCE` — the numbers a caller needs to decide whether to
trust the text or go and look at the page with `pdf_render_pages`.

**A page that already has text is skipped, not OCRed.** Grafting a second text
layer onto a page that has one doubles its extractable text, and the duplicate
interleaves with the original rather than sitting after it. Verified: a page whose
own layer holds 4447 characters extracted 8880 after a graft. So pages are checked
first and skipped unless `force` says otherwise.

That check is a character threshold, which is enough for a page whose text a human
put there but not for our own layer: a long document is OCRed a range at a time, and
a sparse page carrying nothing but the six words we recognized last round falls under
any sensible threshold and would be grafted twice. So a grafted page is marked with
`LAYER_MARKER`, and a marked page is skipped however little text it holds. The
marker is private page data, which readers ignore, and it survives the clone that
each round starts from.

Measured accuracy, tesseract 5.5.2 against the real text layer of a dense two
column paper: 0.97 character and 0.92 word similarity at 200 dpi, and *no
improvement* at 300 — which is why the default is 200 and not the 300 that OCR
guides usually recommend. Two column reading order came out right. What it loses is
ligatures, mathematical symbols, and anything handwritten.
"""

import io
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, NamedTuple, Optional, Sequence, Tuple

import pypdfium2 as pdfium
from pypdf import PdfReader, PdfWriter
from pypdf.errors import FileNotDecryptedError
from pypdf.generic import (
    BooleanObject,
    ContentStream,
    DecodedStreamObject,
    DictionaryObject,
    NameObject,
)

from benscore import artifact_path, err, ok, save
from .page_spec import format_ranges, parse_pages
from .render_pages import fit_dpi

#: Most pages one call will OCR. High enough that a whole document - a contract, a
#: form, a scanned chapter - is usually one call, because the alternative is a caller
#: chaining ranges and getting that wrong. It is the time budget below, not this
#: number, that keeps a call inside what a client will wait for.
_MAX_PAGES = 50

#: How long one call may spend recognizing before it stops and reports where it got
#: to. Measured on this machine at 200 dpi: 0.25 s for a sparse page, 1.0 s for dense
#: body text, 2.3 s for a noisy two column scan - a tenfold spread, which is why a
#: page count cannot be the real limit. MCP clients commonly time out at 60 s, and a
#: timeout returns *nothing*: no text, no artifact, no way to continue. Stopping
#: ourselves well short of that turns the worst case from a lost call into a partial
#: result that says which pages are left.
_TIME_BUDGET_S = 40.0

#: Default resolution. Measured: 200 dpi matched 300 dpi character for character on
#: printed text and took 20% less time, and tesseract's own accuracy notes put the
#: floor for body text around 200. Below 150 it degrades fast.
_DEFAULT_DPI = 200
_MIN_DPI = 72
_MAX_DPI = 600

_PT_PER_INCH = 72.0

#: Word confidence, 0-100, below which a word is reported as doubtful. Tesseract's
#: own convention: on clean printed text nearly everything lands above 90, and the
#: words it invents cluster well below 60.
_LOW_CONFIDENCE = 60.0

#: Characters on a page before its existing text counts as a text layer worth
#: keeping. Same threshold `pdf_check_text` uses, deliberately, so the two verbs
#: cannot disagree about which pages need OCR.
_TEXT_CHARS = 100

#: Private page key marking a page this tool has already given a text layer, so a
#: second pass recognizes it however few words it holds. A page dictionary may carry
#: keys a reader does not know, which readers ignore, so this costs the file nothing.
#:
#: Public, because it is a contract rather than an implementation detail:
#: `pdf_check_text` reads it to keep from calling a page we made searchable a scan
#: that needs OCR. Both verbs share `_TEXT_CHARS` and that is not enough - a page
#: holding six recognized words is under any sensible threshold, so the only reliable
#: signal that a page has been read is the one written when it was.
LAYER_MARKER = "/BensPDFOCR"

#: Advance width of every glyph in the invisible layer, in units of the font size.
#: Courier is monospaced, so this is exact rather than an average, which means the
#: horizontal scale that fits a word to its box can be computed rather than
#: guessed. ocrmypdf takes the same approach with a uniform-width glyphless font.
_COURIER_ADVANCE = 0.6

#: The invisible layer is written in a base-14 font with a single byte encoding, so
#: text it cannot encode is kept out of the PDF and reported instead of mangled.
#: Latin scripts are unaffected; Greek, Cyrillic and CJK would need an embedded CID
#: font, which is the work to do when a non-Latin language is asked for.
_LAYER_ENCODING = "cp1252"

#: Environment variable that overrides where tesseract is looked for. For an
#: install that is not on the PATH the server sees, which is most GUI MCP clients.
TESSERACT_ENV_VAR = "BENSPDF_TESSERACT"

#: Places to look after the PATH. GUI applications routinely launch a server with a
#: minimal PATH that has neither Homebrew prefix on it.
_BINARY_CANDIDATES = (
    "/opt/homebrew/bin/tesseract",  # Homebrew, Apple silicon
    "/usr/local/bin/tesseract",  # Homebrew, Intel; also /usr/local installs
    "/opt/local/bin/tesseract",  # MacPorts
    "/usr/bin/tesseract",  # Linux distributions
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
)

#: How long one page of recognition may take. A page is seconds; a minute means
#: something is wrong, and a hung subprocess would hang the server with it.
_PAGE_TIMEOUT_S = 120

#: What `output` accepts. "text" is the default because the text is what a model
#: reads and the PDF costs a second pass over every page.
_OUTPUTS = ("text", "pdf", "both")

#: Share of multi-character words that must be taller than wide before a page is
#: taken to have been read sideways. Measured on the same page rendered upright and
#: sideways: 1.6% of words were tall upright, 98.4% sideways, so the two cases are
#: as far apart as a signal gets and the threshold sits between them.
_VERTICAL_SHARE = 0.7

#: Words needed before that share means anything. A caption of two words that both
#: happen to be tall is not a sideways page.
_VERTICAL_SAMPLE = 12


class Word(NamedTuple):
    """One recognized word: its box in image pixels, its confidence, its text."""

    left: int
    top: int
    width: int
    height: int
    confidence: float
    text: str
    line: Tuple[int, int, int]  # block, paragraph, line - for rebuilding lines


class Layer(NamedTuple):
    """A page's recognized words, with the render they were measured in.

    The pixel size travels with the words because it is the only way back to page
    coordinates, and the render itself is discarded as soon as tesseract has read
    it.
    """

    words: List[Word]
    page_px: Tuple[int, int]


# --- the verb --------------------------------------------------------------


def ocr(
    pdf_path: str,
    pages: Optional[str] = None,
    lang: str = "eng",
    dpi: int = _DEFAULT_DPI,
    output: str = "text",
    force: bool = False,
) -> Dict[str, Any]:
    """Read a PDF's pages with OCR, optionally writing a searchable copy.

    Args:
        pdf_path: Path to an existing PDF file. Artifact ids are resolved by the
            caller, not here.
        pages: Which pages, as "1-10", "3", "1,5,9-12" or "all". Defaults to the
            first `_MAX_PAGES` pages.
        lang: Tesseract language code, or several joined by "+" as tesseract
            spells it ("deu", "eng+deu"). Must be installed alongside tesseract.
        dpi: Resolution to recognize at, clamped to a sane range.
        output: "text" for the text and its confidences, "pdf" for a searchable
            copy of the document, "both" for both.
        force: OCR pages that already carry text instead of skipping them.

    Returns:
        A result dict: ``pages`` with one entry per page (its number, text, word
        count and confidences), ``artifact`` when a PDF was asked for, and a
        ``summary``. On failure, a reason and what to do about it.
    """
    if output not in _OUTPUTS:
        return err(
            f"output={output!r} is not one of {', '.join(_OUTPUTS)}.",
            file_path=str(pdf_path),
        )

    binary = find_tesseract()
    if binary is None:
        return err(_missing_binary_message(), tesseract_installed=False)

    path = Path(pdf_path).expanduser().resolve()
    if not path.exists():
        return err(
            f"File not found: {pdf_path}", file_path=str(path), file_exists=False
        )

    installed = installed_languages(binary)
    missing = [code for code in str(lang).split("+") if code and code not in installed]
    if installed and missing:
        return err(
            _missing_language_message(missing, installed),
            file_path=str(path),
            language=lang,
            languages_installed=installed,
        )

    try:
        reader = PdfReader(str(path))
        page_count = len(reader.pages)
    except FileNotDecryptedError:
        return err(
            f"{path.name} is encrypted and needs a password, so its pages cannot be "
            f"read. pdf_check_access reports its encryption and permissions without "
            f"opening it.",
            file_path=str(path),
            file_name=path.name,
            encrypted=True,
        )
    except Exception as exc:  # noqa: BLE001 - return a reason, never raise
        return err(
            f"Could not read {path.name} as a PDF: {type(exc).__name__}: {exc}",
            file_path=str(path),
            file_name=path.name,
        )

    if page_count == 0:
        return err(
            f"{path.name} has no pages to read.",
            file_path=str(path),
            file_name=path.name,
            page_count=0,
        )

    try:
        wanted = (
            sorted(parse_pages(pages, page_count))
            if pages
            else list(range(1, page_count + 1))
        )
    except ValueError as exc:
        return err(
            str(exc),
            file_path=str(path),
            file_name=path.name,
            page_count=page_count,
        )

    selected = wanted[:_MAX_PAGES]

    try:
        document = pdfium.PdfDocument(str(path))
    except Exception as exc:  # noqa: BLE001
        return err(
            f"Could not open {path.name} for rendering: {type(exc).__name__}: {exc}",
            file_path=str(path),
            file_name=path.name,
        )

    recognized: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    failed: List[Dict[str, Any]] = []
    layers: Dict[int, Layer] = {}

    attempted: List[int] = []
    out_of_time = False
    started = time.monotonic()

    try:
        for position, number in enumerate(selected):
            # Checked before the page rather than after, and never before the first:
            # a call that returns nothing because one page was slow is worse than a
            # call that overruns its budget by one page. The estimate is the mean of
            # the pages already read, which is the only per page cost we can know.
            if position and _out_of_time(started, len(recognized) + len(failed)):
                out_of_time = True
                break
            attempted.append(number)

            existing = _existing_text_chars(reader, number)
            ours = _has_our_layer(reader, number)
            if (ours or existing >= _TEXT_CHARS) and not force:
                record: Dict[str, Any] = {
                    "page": number,
                    "existing_text_chars": existing,
                }
                if ours:
                    record["ocred_already"] = True
                skipped.append(record)
                continue
            try:
                entry, layer = _ocr_one(document, binary, number, lang, dpi)
            except Exception as exc:  # noqa: BLE001 - one bad page, not a bad document
                failed.append({"page": number, "error": f"{type(exc).__name__}: {exc}"})
                continue
            recognized.append(entry)
            layers[number] = layer
    finally:
        try:
            document.close()
        except Exception:  # noqa: BLE001 - nothing left to do about it
            pass

    remaining = [number for number in wanted if number not in set(attempted)]

    if not recognized:
        return _nothing_read(path, page_count, attempted, skipped, failed, lang)

    result: Dict[str, Any] = {
        "file_path": str(path),
        "file_name": path.name,
        "page_count": page_count,
        "language": lang,
        "dpi": _clamp_dpi(dpi),
        "tesseract_version": tesseract_version(binary),
        "pages": recognized,
        "pages_read": len(recognized),
        "truncated": bool(remaining),
        "skipped": skipped,
        "failed": failed,
    }
    if remaining:
        result["pages_remaining"] = format_ranges(remaining)
        # Which limit was hit changes what a caller should do: another call reads the
        # next pages, but a document this slow will need several, and saying so beats
        # letting them discover it one call at a time.
        result["stopped_for"] = "time" if out_of_time else "page_limit"

    words = sum(entry["words"] for entry in recognized)
    result["words"] = words
    result["mean_confidence"] = (
        round(
            sum(entry["mean_confidence"] * entry["words"] for entry in recognized)
            / words,
            1,
        )
        if words
        else 0.0
    )
    result["low_confidence_words"] = sum(
        entry["low_confidence_words"] for entry in recognized
    )

    if output in ("pdf", "both"):
        try:
            result.update(_write_searchable(path, layers))
        except Exception as exc:  # noqa: BLE001 - the text is still worth returning
            result["pdf_error"] = (
                f"The text was read but the searchable PDF could not be built: "
                f"{type(exc).__name__}: {exc}"
            )

    if output == "pdf":
        # The text was the means, not the answer. Keep the per page numbers, drop
        # the transcription: it would double the size of a result nobody asked to
        # read, and the artifact carries it.
        for entry in result["pages"]:
            entry.pop("text", None)

    result["summary"] = _summary(
        result, wanted=wanted, remaining=remaining, output=output, force=force
    )
    return ok(**result)


# --- one page --------------------------------------------------------------


def _ocr_one(
    document: Any, binary: str, number: int, lang: str, dpi: int
) -> Tuple[Dict[str, Any], Layer]:
    """Render one page, recognize it, and report what came back.

    The render is thrown away deliberately: it exists so tesseract has pixels to
    read, and the text layer is grafted onto the original page rather than onto
    this. `pdf_render_pages` is the verb for keeping an image.
    """
    page = document[number - 1]
    width_pt, height_pt = page.get_size()
    effective_dpi = fit_dpi(width_pt, height_pt, _clamp_dpi(dpi))

    image = page.render(scale=effective_dpi / _PT_PER_INCH).to_pil()
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")

    page_px = (image.width, image.height)
    words = _parse_tsv(
        _run_tesseract(binary, buffer.getvalue(), lang, effective_dpi),
        page_px=page_px,
    )
    text = _page_text(words)
    confidences = [word.confidence for word in words]

    entry: Dict[str, Any] = {
        "page": number,
        "text": text,
        "chars": len(text),
        "words": len(words),
        "mean_confidence": (
            round(sum(confidences) / len(confidences), 1) if confidences else 0.0
        ),
        "low_confidence_words": sum(
            1 for value in confidences if value < _LOW_CONFIDENCE
        ),
        "dpi": effective_dpi,
    }
    if effective_dpi != _clamp_dpi(dpi):
        entry["dpi_reduced_from"] = _clamp_dpi(dpi)
    if not words:
        entry["note"] = (
            "Nothing was recognized on this page. It may be blank, or its content "
            "may be a photograph, a drawing, or handwriting, which OCR cannot read."
        )
    return entry, Layer(words, page_px)


def _run_tesseract(binary: str, png: bytes, lang: str, dpi: int) -> str:
    """Recognize one page image, returning tesseract's TSV.

    Image in on stdin, TSV out on stdout, so no temporary files are written and no
    Python OCR wrapper is needed. `--dpi` is passed because tesseract otherwise
    guesses the resolution from the image and warns; telling it what we rendered at
    removes both the guess and the warning.
    """
    completed = subprocess.run(
        [binary, "-", "stdout", "-l", lang, "--dpi", str(dpi), "tsv"],
        input=png,
        capture_output=True,
        timeout=_PAGE_TIMEOUT_S,
    )
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", "replace").strip().splitlines()
        raise RuntimeError(
            f"tesseract exited {completed.returncode}: "
            f"{detail[-1] if detail else 'no output'}"
        )
    return completed.stdout.decode("utf-8", "replace")


def _parse_tsv(tsv: str, page_px: Tuple[int, int]) -> List[Word]:
    """Read tesseract's TSV into words, dropping everything that is not one.

    TSV carries a row per layout level - page, block, paragraph, line, word - and
    only the word rows have text. Rows with a confidence of -1 are the structural
    ones; rows whose text is whitespace are line breaks tesseract chose to record.
    Boxes outside the rendered page are dropped rather than clamped: a box that
    cannot be placed is a recognition artifact, not text.
    """
    width_px, height_px = page_px
    words: List[Word] = []

    for line in tsv.splitlines()[1:]:  # first line is the header
        fields = line.split("\t")
        if len(fields) < 12:
            continue
        text = fields[11]
        if not text.strip():
            continue
        try:
            block, paragraph, row = int(fields[2]), int(fields[3]), int(fields[4])
            left, top = int(fields[6]), int(fields[7])
            width, height = int(fields[8]), int(fields[9])
            confidence = float(fields[10])
        except ValueError:
            continue
        if confidence < 0 or width <= 0 or height <= 0:
            continue
        if left < 0 or top < 0 or left + width > width_px or top + height > height_px:
            continue
        words.append(
            Word(left, top, width, height, confidence, text, (block, paragraph, row))
        )

    return words


def _page_text(words: Sequence[Word]) -> str:
    """Rebuild the page's text, one line per line tesseract found.

    Reading order is tesseract's, which is the part of its layout analysis worth
    trusting: on a two column paper it interleaved nothing.
    """
    lines: List[str] = []
    current: List[str] = []
    key: Optional[Tuple[int, int, int]] = None

    for word in words:
        if word.line != key:
            if current:
                lines.append(" ".join(current))
            current, key = [], word.line
        current.append(word.text)

    if current:
        lines.append(" ".join(current))
    return "\n".join(lines)


def _out_of_time(started: float, read: int) -> bool:
    """Would one more page take this call past its time budget?

    `read` counts the pages actually recognized, not the ones skipped, because a
    skipped page costs nothing and would make the estimate look cheaper than the work
    ahead. With nothing read yet there is nothing to estimate from, so the answer is
    no: the budget is there to stop a long document, not to refuse a short one.
    """
    elapsed = time.monotonic() - started
    if read < 1:
        return elapsed >= _TIME_BUDGET_S
    return elapsed + (elapsed / read) > _TIME_BUDGET_S


def _existing_text_chars(reader: PdfReader, number: int) -> int:
    """How much text a page already carries, so a text page is not OCRed twice."""
    try:
        return len((reader.pages[number - 1].extract_text() or "").strip())
    except Exception:  # noqa: BLE001 - a page we cannot read is a page to OCR
        return 0


def _has_our_layer(reader: PdfReader, number: int) -> bool:
    """Did an earlier call already put a text layer on this page?

    The character count cannot answer this: a page holding only the handful of words
    we recognized last round reads as untouched, and a second range that overlaps it
    would graft over our own work. The marker is exact where the count is a heuristic.
    """
    try:
        return LAYER_MARKER in reader.pages[number - 1]
    except Exception:  # noqa: BLE001 - unreadable means unmarked, so OCR it
        return False


# --- the text layer --------------------------------------------------------


def _write_searchable(source: Path, layers: Dict[int, "Layer"]) -> Dict[str, Any]:
    """Copy the document with an invisible text layer on every page that has one.

    Every page of the original is kept, OCRed or not, so the artifact is the whole
    document rather than a subset of it that happens to be searchable.
    """
    writer = PdfWriter(clone_from=str(source))

    grafted: List[int] = []
    ungrafted: List[int] = []
    unencodable = 0
    for number, layer in layers.items():
        if not layer.words:
            continue
        if _reads_vertically(layer.words):
            ungrafted.append(number)
            continue
        page = writer.pages[number - 1]
        placed, dropped = _graft(page, layer)
        unencodable += dropped
        if placed:
            # Marked, so a later call over an overlapping range recognizes the page
            # as done rather than grafting a second copy of these words onto it.
            page[NameObject(LAYER_MARKER)] = BooleanObject(True)
            grafted.append(number)

    buffer = io.BytesIO()
    writer.write(buffer)
    data = buffer.getvalue()
    artifact = save(data, ".pdf")

    result: Dict[str, Any] = {
        "artifact": artifact,
        # Both, deliberately: other tools take the id, a person opens the path.
        "path": str(artifact_path(artifact)),
        "artifacts": [artifact],
        "size_bytes": len(data),
        "text_layer_pages": format_ranges(grafted),
    }
    if unencodable:
        result["unencodable_words"] = unencodable
    if ungrafted:
        result["text_layer_skipped"] = format_ranges(ungrafted)
    return result


def _reads_vertically(words: Sequence[Word]) -> bool:
    """Did the text run down the render rather than across it?

    A page can be laid out so that it *displays* sideways - the content upright in
    its own coordinates, `/Rotate` turning it - and tesseract reads such a page
    anyway, rotating each text line as it goes. What it does not do is tell us: the
    boxes come back transposed, tall and narrow, one per sideways word. Grafting a
    layer from those would scatter squashed text across the page, so the box shapes
    are read as the signal tesseract does not give.

    Words of one character are ignored, since "I" and "." are taller than wide
    whichever way up the page is.
    """
    shapes = [word for word in words if len(word.text.strip()) > 1]
    if len(shapes) < _VERTICAL_SAMPLE:
        return False

    tall = sum(1 for word in shapes if word.height > word.width)
    return tall / len(shapes) >= _VERTICAL_SHARE


def _graft(page: Any, layer: "Layer") -> Tuple[int, int]:
    """Merge an invisible text layer onto `page`.

    The layer is built in the coordinate space of the *rendered* page - upright,
    origin at its bottom left corner, which is where tesseract's boxes live - and
    then transformed into the page's own space. That transform is the fiddly part
    and it has two halves: `/Rotate`, because the render is upright and the page's
    own content may not be, and the CropBox, because the render covers the visible
    box while the page's coordinates start at the MediaBox.

    Returns the number of words placed and the number dropped as unencodable.
    """
    box = _visible_box(page)
    rotation = _rotation(page)
    width, height = box[2] - box[0], box[3] - box[1]
    # The render is the visible box with /Rotate applied, so at 90 or 270 degrees
    # the rendered view is the box on its side.
    view_pt = (height, width) if rotation in (90, 270) else (width, height)

    ops, placed, dropped = _layer_ops(layer, view_pt)
    if not placed:
        return 0, dropped

    overlay = _overlay_page(ops, view_pt)
    page.merge_transformed_page(overlay, _to_page_space(box, rotation), over=True)
    return placed, dropped


def _layer_ops(
    layer: "Layer", view_pt: Tuple[float, float]
) -> Tuple[List[str], int, int]:
    """Build the content stream for the invisible layer.

    Text render mode 3 is the whole trick: the glyphs are positioned, measured and
    extractable, and nothing is painted.

    **A line at a time, not a word at a time.** Placing each word at its own box
    with its own matrix looked obviously right and produced text that extracted as
    "theoutputof thisclassifieras": an extractor has no space characters to read, so
    it infers word breaks by comparing gaps against font metrics, and the metrics of
    a stretched monospaced font are not what it expects. Writing a whole line as one
    string with real spaces removes the guess. Tesseract's own line grouping is what
    makes this possible, and it is the part of its layout analysis worth trusting.

    Within a line, the gaps between words are padded with however many space
    characters they measure, so the monospaced layout still tracks where the words
    actually sit. The line as a whole is then scaled horizontally to fit the width
    it occupied on the page, which keeps selection close enough to be usable.

    The baseline is taken as the bottom of the line's boxes. Tesseract gives no
    baseline and its boxes include descenders, so a line sits a fraction of its
    height low - invisible, and immaterial to a layer whose job is to be searchable.
    """
    view_width, view_height = view_pt
    px_width, _px_height = layer.page_px
    if px_width <= 0 or view_width <= 0:
        return [], 0, 0

    # One factor for both axes: the render is the view at a uniform scale.
    scale = view_width / px_width

    ops = ["BT", "3 Tr"]
    placed = dropped = 0

    for words in _lines(layer.words):
        usable = [word for word in words if _encodable(word.text) is not None]
        dropped += len(words) - len(usable)
        if not usable:
            continue

        left = min(word.left for word in usable)
        right = max(word.left + word.width for word in usable)
        bottom = max(word.top + word.height for word in usable)
        height = _median(sorted(word.height for word in usable))
        if height <= 0 or right <= left:
            continue

        text = _line_text(usable, char_px=_COURIER_ADVANCE * height)
        size = height * scale
        natural = _COURIER_ADVANCE * size * len(text)
        if natural <= 0:
            continue

        # Horizontal scale as a percentage, clamped: a line tesseract read as far
        # more characters than fit would otherwise ask for a scale near zero.
        stretch = max(1.0, min(1000.0, 100.0 * ((right - left) * scale) / natural))

        ops.append(
            f"/F1 {size:.2f} Tf {stretch:.1f} Tz "
            f"1 0 0 1 {left * scale:.2f} {view_height - bottom * scale:.2f} Tm "
            f"({_escape(text)}) Tj"
        )
        placed += len(usable)

    ops.append("ET")
    return ops, placed, dropped


def _lines(words: Sequence[Word]) -> List[List[Word]]:
    """Group words into the lines tesseract found, keeping its order."""
    lines: List[List[Word]] = []
    key: Optional[Tuple[int, int, int]] = None

    for word in words:
        if word.line != key or not lines:
            lines.append([])
            key = word.line
        lines[-1].append(word)

    return lines


def _line_text(words: Sequence[Word], char_px: float) -> str:
    """One line's words, separated by as many spaces as the gaps measure.

    At least one space between words, always: the gap between two words on a
    justified line can measure less than a character, and losing it would join them
    into a word that was never on the page.
    """
    parts = [words[0].text]

    for previous, word in zip(words, words[1:]):
        gap = word.left - (previous.left + previous.width)
        spaces = 1 if char_px <= 0 else max(1, round(gap / char_px))
        parts.append(" " * spaces + word.text)

    return "".join(parts)


def _median(values: Sequence[int]) -> float:
    """Middle value of a sorted sequence. Used for a line's font size, where one
    tall box - a capital, a bracket, a stray mark - should not set the size."""
    if not values:
        return 0.0
    middle = len(values) // 2
    if len(values) % 2:
        return float(values[middle])
    return (values[middle - 1] + values[middle]) / 2.0


def _overlay_page(ops: Sequence[str], view_pt: Tuple[float, float]) -> Any:
    """A single page carrying the layer's content stream and nothing else.

    Built in a throwaway writer and read back, rather than appended to the document
    being written and removed again: the overlay is scaffolding, and this way none
    of it - not the stream, not the font - can survive into the output as an orphan.
    """
    scratch = PdfWriter()
    page = scratch.add_blank_page(width=view_pt[0], height=view_pt[1])
    page[NameObject("/Resources")] = DictionaryObject(
        {
            NameObject("/Font"): DictionaryObject(
                {
                    NameObject("/F1"): DictionaryObject(
                        {
                            NameObject("/Type"): NameObject("/Font"),
                            NameObject("/Subtype"): NameObject("/Type1"),
                            NameObject("/BaseFont"): NameObject("/Courier"),
                            NameObject("/Encoding"): NameObject("/WinAnsiEncoding"),
                        }
                    )
                }
            )
        }
    )
    stream = DecodedStreamObject()
    stream.set_data("\n".join(ops).encode(_LAYER_ENCODING, "replace"))
    # Through ContentStream rather than the raw stream: it is what a page's contents
    # are, and the one shape `replace_contents` accepts for data we produced here.
    page.replace_contents(ContentStream(stream, None))

    buffer = io.BytesIO()
    scratch.write(buffer)
    buffer.seek(0)
    return PdfReader(buffer).pages[0]


def _encodable(text: str) -> Optional[str]:
    """The word, if the invisible layer's encoding can carry it; None otherwise."""
    try:
        text.encode(_LAYER_ENCODING)
    except UnicodeEncodeError:
        return None
    return text


def _escape(text: str) -> str:
    """Escape a PDF literal string: backslash first, then the parentheses."""
    return text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def _visible_box(page: Any) -> Tuple[float, float, float, float]:
    """The page as a reader sees it: CropBox clipped to MediaBox, as left/bottom/
    right/top in the page's own coordinates.

    Same rule `pdf_page_layout` measures and `pdf_render_pages` renders, so all
    three verbs agree on what "the page" is.
    """
    media = page.mediabox
    try:
        crop = page.cropbox
    except Exception:  # noqa: BLE001 - a malformed CropBox is no CropBox
        crop = media

    left = max(float(media.left), float(crop.left))
    bottom = max(float(media.bottom), float(crop.bottom))
    right = min(float(media.right), float(crop.right))
    top = min(float(media.top), float(crop.top))

    if right <= left or top <= bottom:  # nonsense CropBox, trust the MediaBox
        return (
            float(media.left),
            float(media.bottom),
            float(media.right),
            float(media.top),
        )
    return left, bottom, right, top


def _rotation(page: Any) -> int:
    """The page's `/Rotate`, normalized to 0, 90, 180 or 270."""
    try:
        value = int(page.get("/Rotate", 0) or 0)
    except (TypeError, ValueError):
        return 0
    return value % 360 // 90 * 90


def _to_page_space(
    box: Tuple[float, float, float, float], rotation: int
) -> Tuple[float, float, float, float, float, float]:
    """Matrix taking a point in the rendered view to the page's own coordinates.

    The rendered view is upright and starts at the visible box's corner; the page's
    coordinates are unrotated and start at the MediaBox's. Derived per rotation by
    inverting the display rotation, then shifted by the visible box's origin.
    """
    left, bottom, right, top = box
    width, height = right - left, top - bottom

    if rotation == 90:
        matrix = (0.0, 1.0, -1.0, 0.0, width, 0.0)
    elif rotation == 180:
        matrix = (-1.0, 0.0, 0.0, -1.0, width, height)
    elif rotation == 270:
        matrix = (0.0, -1.0, 1.0, 0.0, 0.0, height)
    else:
        matrix = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)

    a, b, c, d, e, f = matrix
    return a, b, c, d, e + left, f + bottom


# --- finding tesseract -----------------------------------------------------


def find_tesseract() -> Optional[str]:
    """Locate the tesseract binary, or None if it is not installed.

    Looked up on every call rather than at import, and deliberately not cached: a
    user who reads the install instructions, installs tesseract and retries gets a
    working tool without restarting the server. Three `stat` calls against a verb
    that spends seconds per page is not a cost worth optimizing.
    """
    override = os.environ.get(TESSERACT_ENV_VAR)
    if override:
        found = shutil.which(override) or (
            override if Path(override).is_file() else None
        )
        if found:
            return found

    return shutil.which("tesseract") or next(
        (path for path in _BINARY_CANDIDATES if Path(path).is_file()), None
    )


def tesseract_version(binary: str) -> str:
    """Tesseract's version, or "unknown" if it will not say."""
    try:
        completed = subprocess.run(
            [binary, "--version"], capture_output=True, timeout=15
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"

    first = completed.stdout.decode("utf-8", "replace").splitlines()
    match = re.search(r"(\d+\.\d+(?:\.\d+)?)", first[0] if first else "")
    return match.group(1) if match else "unknown"


def installed_languages(binary: str) -> List[str]:
    """Language codes tesseract has data for, or an empty list if it will not say.

    An empty list means "could not tell", never "none installed", so the caller
    treats it as a reason to try rather than a reason to refuse.
    """
    try:
        completed = subprocess.run(
            [binary, "--list-langs"], capture_output=True, timeout=15
        )
    except (OSError, subprocess.SubprocessError):
        return []

    if completed.returncode != 0:
        return []

    lines = completed.stdout.decode("utf-8", "replace").splitlines()
    # The first line is "List of available languages ...", the rest are codes.
    return [
        line.strip() for line in lines[1:] if line.strip() and " " not in line.strip()
    ]


def _missing_binary_message() -> str:
    """Why OCR is unavailable, and the command that fixes it on this platform."""
    if sys.platform == "darwin":
        install = "brew install tesseract"
    elif sys.platform.startswith("win"):
        install = (
            "winget install UB-Mannheim.TesseractOCR (or download the installer "
            "from github.com/UB-Mannheim/tesseract/wiki)"
        )
    else:
        install = "sudo apt install tesseract-ocr (or your distribution's equivalent)"

    return (
        f"OCR needs the tesseract program, which is not installed. It is a system "
        f"program rather than a Python package, so installing benspdf-mcp does not "
        f"bring it: {install}. If it is installed somewhere unusual, set "
        f"{TESSERACT_ENV_VAR} to its full path. Until then, pdf_render_pages can "
        f"show the pages as images, which a vision model can read."
    )


def _missing_language_message(missing: Sequence[str], installed: Sequence[str]) -> str:
    """Which language is absent, what is there instead, and how to add it."""
    if sys.platform == "darwin":
        install = "brew install tesseract-lang installs every language"
    elif sys.platform.startswith("win"):
        install = "re-run the tesseract installer and select the languages you need"
    else:
        install = "sudo apt install tesseract-ocr-deu, substituting the code you want"

    return (
        f"tesseract has no data for {', '.join(missing)}. Installed: "
        f"{', '.join(installed)}. To add one, {install}. Recognizing text with the "
        f"wrong language would produce confident nonsense, so this is refused "
        f"rather than attempted."
    )


# --- when nothing was read -------------------------------------------------


def _nothing_read(
    path: Path,
    page_count: int,
    selected: Sequence[int],
    skipped: Sequence[Dict[str, Any]],
    failed: Sequence[Dict[str, Any]],
    lang: str,
) -> Dict[str, Any]:
    """No page was OCRed. Whether that is a failure depends on why.

    Every page already having text is a *success* with nothing to do - the document
    is readable as it stands, which is what the caller wanted to know. Pages that
    failed are a failure.
    """
    common = {
        "file_path": str(path),
        "file_name": path.name,
        "page_count": page_count,
        "language": lang,
        "skipped": list(skipped),
        "failed": list(failed),
    }

    if skipped and not failed:
        numbers = format_ranges([entry["page"] for entry in skipped])
        return ok(
            pages=[],
            pages_read=0,
            **common,
            summary=(
                f"No OCR was needed: page {numbers} of {path.name} already carries a "
                f"text layer, so the text can be read directly. Pass force=True to "
                f"OCR anyway, which is worth doing only when the existing text is "
                f"gibberish."
            ),
        )

    return err(
        f"None of the {len(selected)} requested pages of {path.name} could be read "
        f"with OCR.",
        **common,
    )


# --- the sentence ----------------------------------------------------------


def _summary(
    result: Dict[str, Any],
    *,
    wanted: Sequence[int],
    remaining: Sequence[int],
    output: str,
    force: bool,
) -> str:
    """One sentence: what was read, how much to trust it, and what came back."""
    pages = result["pages"]
    numbers = format_ranges([entry["page"] for entry in pages])
    count = len(pages)
    subject = "1 page" if count == 1 else f"{count} pages"

    parts = [
        f"Read {subject} of {result['file_name']} (page {numbers}) with OCR at "
        f"{result['dpi']} dpi in {result['language']}: {result['words']} words, "
        f"mean confidence {result['mean_confidence']} out of 100."
    ]

    doubtful = result["low_confidence_words"]
    if doubtful:
        share = round(100 * doubtful / result["words"]) if result["words"] else 0
        parts.append(
            f"{doubtful} of them ({share}%) scored below {int(_LOW_CONFIDENCE)}, so "
            f"treat those as uncertain"
            + (
                "; a page with many is worth looking at with pdf_render_pages."
                if share >= 10
                else "."
            )
        )

    empty = [entry["page"] for entry in pages if not entry["words"]]
    if empty:
        parts.append(
            f"Page {format_ranges(empty)} yielded nothing, so it is blank or holds "
            f"something OCR cannot read, such as handwriting or a photograph."
        )

    if result["skipped"]:
        numbers = format_ranges([entry["page"] for entry in result["skipped"]])
        parts.append(
            f"Page {numbers} already carried text and was skipped"
            + ("." if force else "; pass force=True to OCR it anyway.")
        )

    if remaining:
        # Which limit stopped the call changes what to expect next: a page limit
        # means the rest is one more call, a time budget means the document is slow
        # and will take several, and guessing wrong wastes a round trip.
        why = (
            f"{len(wanted)} pages were asked for and {_MAX_PAGES} is the most one "
            f"call reads"
            if result.get("stopped_for") == "page_limit"
            else f"This call reached its {int(_TIME_BUDGET_S)} second budget, which "
            f"is there because a client that gives up waiting returns nothing at all"
        )
        parts.append(
            f"{why}, so page {result['pages_remaining']} was not read."
            # Reading needs nothing but the next range; a text layer needs the artifact
            # too, so that instruction waits until the artifact has been named.
            + (
                f' Ask for pages="{format_ranges(remaining[:_MAX_PAGES])}" to continue.'
                if output == "text"
                else ""
            )
        )

    if result["failed"]:
        numbers = format_ranges([entry["page"] for entry in result["failed"]])
        parts.append(f"Page {numbers} could not be read.")

    if "artifact" in result:
        parts.append(
            f"A searchable copy of {result['file_name']} is artifact "
            f"{result['artifact']}, with the text layer on page "
            f"{result['text_layer_pages']}; pass it to export to keep it."
        )
        if remaining:
            # Naming the artifact as the next `ref` is the whole instruction: called
            # again on the original, each range produces a separate copy carrying only
            # its own layer, and the caller is left with thirds of a searchable
            # document and no verb here that merges them.
            parts.append(
                f"To make the rest searchable, call pdf_ocr again with "
                f"ref={result['artifact']} and pages="
                f'"{format_ranges(remaining[:_MAX_PAGES])}", and keep passing each new '
                f"artifact into the next call: every call adds its text layer to the "
                f"copy it is given, so passing the original again would produce a "
                f"second copy carrying only that range."
            )
        if "unencodable_words" in result:
            parts.append(
                f"{result['unencodable_words']} words could not be written into the "
                f"text layer because its font cannot encode them; they are in the "
                f"text above."
            )
        if "text_layer_skipped" in result:
            parts.append(
                f"Page {result['text_layer_skipped']} was read sideways, so its text "
                f"is above but no layer was added to it: the words' positions on a "
                f"page turned that way cannot be placed accurately. The page needs "
                f"rotating upright first."
            )
    elif "pdf_error" in result:
        parts.append(result["pdf_error"])

    if output == "pdf":
        parts.append(
            "The per page text was left out because output=pdf; ask for text or "
            "both to see it."
        )

    return " ".join(parts)


def _clamp_dpi(dpi: Any) -> int:
    """Keep the requested resolution inside the range that makes sense."""
    try:
        value = int(dpi)
    except (TypeError, ValueError):
        return _DEFAULT_DPI
    return max(_MIN_DPI, min(_MAX_DPI, value))
