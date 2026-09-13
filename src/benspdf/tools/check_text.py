"""
Whether a PDF's pages carry real text: the work behind `pdf_check_text`.

One question — *can I read this, or does it need OCR first?* — that no single
value in a PDF answers. A file has no "scanned" flag. What it has is a set of
weak signals: how much text a page yields, whether the page is really one big
image, and what tool wrote it. So this module gathers those signals, reports each
one, and puts a verdict on top rather than making the caller assemble it.

Three decisions shape the rest of the file:

**Sampling, not walking.** Text extraction is the expensive part, so a fixed
number of pages spread across the document is examined instead of all of them.
That makes a 2000-page scan cost the same as a 10-page one, at the price of an
estimate rather than a census. The estimate is labelled as one: ``sampled`` says
whether pages were skipped and ``pages`` lists exactly which were read.

**How much of the page an image covers, not how many images it has.** The naive
signature for a scan — has images, has little text — flags every slide deck and
brochure, because a page with three photos and a short heading looks exactly like
that. What actually distinguishes a scan is that *one* image covers the whole
page. Measured on real files, scans land at 0.99–1.04 of the page area (drawn
slightly over the edge) while graphic-heavy designed pages top out around 0.44,
so the two groups are far apart and a threshold in between separates them.

**Geometry, not pixels.** Coverage is computed from the content stream's
transformation matrices, so no image data is ever decoded — pypdf's
``page.images`` would decode, which is the cost this verb exists to avoid.

On failure this module returns a reason rather than raising, and it means it: a
PDF parser fed a damaged file throws almost anything, including exceptions from
zlib and from pypdf's own hierarchy that are *not* ``PdfReadError``. Catching a
list of expected types let roughly 3% of a corrupted corpus escape as tracebacks,
so both the document and per-page boundaries catch broadly and name the exception
type in the message, which keeps a bug in this file visible instead of disguising
it as a bad PDF.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from pypdf import PdfReader
from pypdf.errors import FileNotDecryptedError
from pypdf.generic import ContentStream

from benscore import err, ok
from .metadata import read_metadata
from .ocr import LAYER_MARKER

#: How many pages to examine, however long the document is.
_MAX_SAMPLED_PAGES = 10

#: Characters on a page before its text counts as a real text layer. Scans often
#: carry a little text — a scanner stamp, a stapled-on cover page, a page number
#: — and treating any text at all as a text layer would call those readable.
#:
#: It is a threshold, so it cannot recognize a sparse page that genuinely holds
#: nothing but two lines: a title page, a form, a chapter opener. That is the one
#: case where this verb has better evidence than a count — see `LAYER_MARKER`.
_TEXT_CHARS = 100

#: Share of the page a single image must cover for the page to look like a scan
#: rather than an illustrated one. Measured scans sit at 0.99 and above, designed
#: pages with photos below 0.45, so anything in this gap works; the midpoint just
#: leaves room for scans with a margin around the image.
_SCAN_COVERAGE = 0.6

#: How far to follow Form XObjects looking for images. Scanners usually place the
#: page image directly, but some wrap it a level or two deep.
_FORM_DEPTH = 3

#: Characters of extracted text to return, so the caller can see whether the text
#: layer is words or OCR gibberish.
_EXCERPT_CHARS = 200

#: Lowercased substrings in Producer or Creator that suggest a scanner or MFP
#: wrote the file. A hint that supports the page evidence, never a verdict on its
#: own: plenty of scans are re-saved by something else, losing the trail.
_SCANNER_HINTS = (
    "scan",
    "canon",
    "epson",
    "xerox",
    "ricoh",
    "brother",
    "kyocera",
    "konica",
    "lexmark",
    "imagerunner",
    "workcentre",
    "paperport",
    "fujitsu",
)

#: Substrings that suggest text on the page was produced by OCR, which matters
#: because such text is a guess and can be subtly wrong.
_OCR_HINTS = (
    "ocr",
    "abbyy",
    "finereader",
    "tesseract",
    "readiris",
    "omnipage",
)


def check_text(pdf_path: str) -> Dict[str, Any]:
    """Decide whether a PDF has a usable text layer or needs OCR.

    Args:
        pdf_path: Path to an existing PDF file. Artifact ids are resolved by the
            caller, not here.

    Returns:
        A result dict. On success: ``verdict`` and ``needs_ocr`` as the answer,
        ``summary`` as a sentence, and the evidence behind them — per page
        character and image counts, totals, a text excerpt, and the Producer and
        Creator hints.
    """
    path = Path(pdf_path).expanduser().resolve()

    if not path.exists():
        return err(
            f"File not found: {pdf_path}",
            file_path=str(path),
            file_exists=False,
        )

    try:
        reader = PdfReader(str(path))
        page_count = len(reader.pages)
        examined = [
            _examine(reader.pages[index], index) for index in _sample(page_count)
        ]
    except FileNotDecryptedError:
        return err(
            f"{path.name} is encrypted and needs a password, so its pages cannot "
            f"be read. pdf_check_access reports its encryption and permissions "
            f"without opening it.",
            file_path=str(path),
            file_name=path.name,
            encrypted=True,
        )
    except Exception as exc:  # noqa: BLE001 - see the module docstring
        return err(
            f"Could not read {path.name} as a PDF: {type(exc).__name__}: {exc}",
            file_path=str(path),
            file_name=path.name,
        )

    pages = [entry for entry, _text in examined]
    excerpt = _first_excerpt(examined)

    pages_with_text = sum(1 for entry in pages if entry["has_text"])
    scanned_pages = sum(1 for entry in pages if entry["looks_scanned"])
    ocred_pages = sum(1 for entry in pages if entry.get("ocred"))
    unreadable_pages = sum(1 for entry in pages if "error" in entry)
    characters = sum(entry["characters"] for entry in pages)

    verdict = _verdict(pages_with_text, scanned_pages)
    producer, creator_tool = _writing_tools(str(path))
    suggests_scanner = _matches(_SCANNER_HINTS, producer, creator_tool)
    suggests_ocr = _matches(_OCR_HINTS, producer, creator_tool)

    return ok(
        file_path=str(path),
        file_name=path.name,
        page_count=page_count,
        verdict=verdict,
        needs_ocr=verdict in ("scanned", "mixed"),
        has_text_layer=pages_with_text > 0,
        summary=_summary(
            verdict=verdict,
            page_count=page_count,
            pages=pages,
            pages_with_text=pages_with_text,
            scanned_pages=scanned_pages,
            ocred_pages=ocred_pages,
            suggests_ocr=suggests_ocr,
            suggests_scanner=suggests_scanner,
        ),
        sampled=len(pages) < page_count,
        pages_examined=len(pages),
        pages=pages,
        pages_with_text=pages_with_text,
        scanned_pages=scanned_pages,
        ocred_pages=ocred_pages,
        unreadable_pages=unreadable_pages,
        characters_sampled=characters,
        mean_characters_per_page=round(characters / len(pages), 1) if pages else 0.0,
        text_excerpt=excerpt,
        producer=producer,
        creator_tool=creator_tool,
        producer_suggests_scanner=suggests_scanner,
        producer_suggests_ocr=suggests_ocr,
        text_threshold_chars=_TEXT_CHARS,
        scan_coverage_threshold=_SCAN_COVERAGE,
    )


# --- which pages to look at ------------------------------------------------


def _sample(page_count: int) -> List[int]:
    """Pick up to ``_MAX_SAMPLED_PAGES`` page indices, evenly spread.

    First and last page are always included. Front matter and back matter are
    where a document is least representative of itself — a scanned report with a
    born-digital cover page, or text pages followed by scanned appendices — so
    the ends are worth more than a contiguous run from the start.
    """
    if page_count <= _MAX_SAMPLED_PAGES:
        return list(range(page_count))

    step = (page_count - 1) / (_MAX_SAMPLED_PAGES - 1)
    return sorted({round(index * step) for index in range(_MAX_SAMPLED_PAGES)})


# --- what one page says ----------------------------------------------------


def _examine(page: Any, index: int) -> Tuple[Dict[str, Any], str]:
    """Read one page's text and the images drawn on it.

    Returns the page entry plus its extracted text, which the caller uses for the
    excerpt and then drops — returning the full text of every sampled page would
    turn a cheap check into a partial extraction (that is `pdf_extract_text`).
    """
    entry: Dict[str, Any] = {"page": index + 1}
    text = ""

    try:
        text = (page.extract_text() or "").strip()
    except FileNotDecryptedError:
        raise  # a whole-document problem; the caller reports it
    except Exception as exc:  # noqa: BLE001 - one bad page, not a bad document
        entry["error"] = f"text extraction failed: {type(exc).__name__}: {exc}"

    try:
        images, coverage = _page_images(page)
    except FileNotDecryptedError:
        raise
    except Exception:  # noqa: BLE001 - unknown geometry, not zero geometry
        images, coverage = 0, None

    ocred = _ocred_here(page)

    entry["characters"] = len(text)
    entry["images"] = images
    entry["image_coverage"] = coverage
    # A page `pdf_ocr` has read carries text whatever the count says, and is not a
    # scan awaiting OCR however much it still looks like one - the image is still
    # there, because the text layer went on top of it rather than replacing it.
    entry["has_text"] = bool(text) if ocred else len(text) >= _TEXT_CHARS
    entry["looks_scanned"] = not ocred and _looks_scanned(len(text), images, coverage)
    if ocred:
        entry["ocred"] = True
    return entry, text


def _ocred_here(page: Any) -> bool:
    """Has `pdf_ocr` already given this page a text layer?

    The one signal here that is a fact rather than an inference. Without it a page
    holding the six words OCR found on it reads as 0 of 100 characters over a
    full-page image - which is the description of a scan, and would tell a caller to
    OCR a page that has just been OCRed.
    """
    try:
        return LAYER_MARKER in page
    except Exception:  # noqa: BLE001 - see the module docstring
        return False


def _looks_scanned(characters: int, images: int, coverage: Optional[float]) -> bool:
    """Is this page a picture of a page rather than a page?

    Wants both halves: an image big enough to *be* the page, and too little text
    to read. Coverage is what keeps illustrated pages out — a brochure spread has
    images and a short heading too, it just has no single image covering the page.

    ``coverage`` is None when the content stream could not be walked. The fallback
    is deliberately strict, asking for no text at all rather than merely little,
    since without geometry there is nothing to tell a scan from a photo page.
    """
    if characters >= _TEXT_CHARS:
        return False
    if coverage is None:
        return images > 0 and characters == 0
    return coverage >= _SCAN_COVERAGE


# --- images, measured without decoding them --------------------------------

#: An unscaled, unmoved coordinate system: [a, b, c, d, e, f].
_IDENTITY = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)

_Matrix = Tuple[float, float, float, float, float, float]


def _page_images(page: Any) -> Tuple[int, Optional[float]]:
    """How many images a page draws, and the largest one's share of the page.

    Counts what the content stream *draws* rather than what the resource
    dictionary declares, because the two differ: an unused XObject left behind by
    an editing tool would otherwise count as a page image. If the content stream
    cannot be walked, falls back to the declared count with unknown coverage.

    Measured against the CropBox, which is the page as anyone actually sees it.
    Rare — one file in a 141 file corpus — but when a CropBox trims a page to a
    third of its MediaBox, a scan filling the visible page measures 0.29 against
    the MediaBox and gets missed. pypdf falls back to the MediaBox itself when
    there is no CropBox.
    """
    box = page.cropbox
    page_area = abs(float(box.width) * float(box.height))

    areas: List[float] = []
    try:
        _walk(
            contents=page.get_contents(),
            resources=page.get("/Resources"),
            pdf=getattr(page, "pdf", None),
            ctm=_IDENTITY,
            depth=0,
            seen=set(),
            areas=areas,
        )
    except FileNotDecryptedError:
        raise
    except Exception:  # noqa: BLE001 - fall back to what the resources declare
        return _declared_images(page.get("/Resources"), depth=0, seen=set()), None

    if not page_area:
        return len(areas), None
    return len(areas), round(max(areas, default=0.0) / page_area, 3)


def _walk(
    *,
    contents: Any,
    resources: Any,
    pdf: Any,
    ctm: _Matrix,
    depth: int,
    seen: Set[int],
    areas: List[float],
) -> None:
    """Collect the drawn area of every image in a content stream.

    Follows the three operators that matter for geometry — ``cm`` concatenates a
    transformation, ``q``/``Q`` save and restore it — and on ``Do`` either records
    an image's area or steps into a Form XObject, carrying the transformation with
    it. Everything else is skipped: this is not a renderer.

    An image is drawn as the unit square put through the current matrix, so the
    determinant of that matrix *is* the area it lands on, in page units. That
    holds for rotation and skew as well, which is why the determinant is used
    rather than multiplying a width by a height.
    """
    xobjects = _xobjects(resources)
    stream = ContentStream(contents, pdf)
    stack: List[_Matrix] = []

    for operands, operator in stream.operations:
        if operator == b"q":
            stack.append(ctm)
        elif operator == b"Q":
            if stack:
                ctm = stack.pop()
        elif operator == b"cm" and len(operands) == 6:
            ctm = _concat(tuple(float(value) for value in operands), ctm)
        elif operator == b"Do" and operands:
            xobject = _deref(xobjects.get(operands[0]))
            if not isinstance(xobject, dict):
                continue

            subtype = xobject.get("/Subtype")
            if subtype == "/Image":
                areas.append(abs(ctm[0] * ctm[3] - ctm[1] * ctm[2]))
            elif subtype == "/Form" and depth < _FORM_DEPTH:
                number = getattr(xobjects.get(operands[0]), "idnum", None)
                if number is not None:
                    if number in seen:
                        continue  # a form that draws itself; once is enough
                    seen.add(number)

                matrix = xobject.get("/Matrix")
                inner = ctm
                if isinstance(matrix, list) and len(matrix) == 6:
                    inner = _concat(tuple(float(value) for value in matrix), ctm)

                _walk(
                    contents=xobject,
                    # A form without its own resources inherits the parent's.
                    resources=xobject.get("/Resources") or resources,
                    pdf=pdf,
                    ctm=inner,
                    depth=depth + 1,
                    seen=seen,
                    areas=areas,
                )


def _declared_images(resources: Any, depth: int, seen: Set[int]) -> int:
    """Count the image XObjects a resource dictionary declares.

    The fallback for a content stream that will not parse. Cheaper and more
    forgiving than the walk, but it counts images the page may never draw and it
    says nothing about how large they are.
    """
    xobjects = _xobjects(resources)

    count = 0
    for name in list(xobjects.keys()):
        reference = xobjects.get(name)

        number = getattr(reference, "idnum", None)
        if number is not None:
            if number in seen:
                continue
            seen.add(number)

        xobject = _deref(reference)
        if not isinstance(xobject, dict):
            continue

        subtype = xobject.get("/Subtype")
        if subtype == "/Image":
            count += 1
        elif subtype == "/Form" and depth < _FORM_DEPTH:
            count += _declared_images(xobject.get("/Resources"), depth + 1, seen)

    return count


def _xobjects(resources: Any) -> Dict[Any, Any]:
    """The /XObject sub-dictionary of a resource dictionary, or an empty one."""
    resources = _deref(resources)
    if not isinstance(resources, dict):
        return {}
    xobjects = _deref(resources.get("/XObject"))
    return xobjects if isinstance(xobjects, dict) else {}


def _concat(inner: Sequence[float], outer: Sequence[float]) -> _Matrix:
    """Matrix product, PDF order: ``inner`` applies first, then ``outer``."""
    a, b, c, d, e, f = inner
    A, B, C, D, E, F = outer
    return (
        a * A + b * C,
        a * B + b * D,
        c * A + d * C,
        c * B + d * D,
        e * A + f * C + E,
        e * B + f * D + F,
    )


def _deref(obj: Any) -> Any:
    """Follow an indirect reference, if that is what this is."""
    resolve = getattr(obj, "get_object", None)
    return resolve() if callable(resolve) else obj


def _first_excerpt(examined: List[Tuple[Dict[str, Any], str]]) -> Optional[str]:
    """A short excerpt from the first page that had real text.

    Present so the caller can tell a clean text layer from OCR that came out as
    gibberish. A character count cannot make that distinction, and "has text" is
    the wrong answer when the text is unusable.
    """
    for entry, text in examined:
        if entry["has_text"]:
            excerpt = " ".join(text.split())
            if len(excerpt) > _EXCERPT_CHARS:
                return excerpt[:_EXCERPT_CHARS].rstrip() + "…"
            return excerpt
    return None


# --- the answer ------------------------------------------------------------


def _verdict(pages_with_text: int, scanned_pages: int) -> str:
    """Turn the page evidence into one of four words.

    Note what is *not* here: a page that is neither readable nor scan-like does
    not count against a text layer. Blank dividers, vector-only charts and photo
    pages are all normal in born-digital documents, and letting them dilute the
    result would report a mix wherever a document had a full page picture.
    """
    if scanned_pages and pages_with_text:
        return "mixed"
    if scanned_pages:
        return "scanned"
    if pages_with_text:
        return "text"
    return "no_text"


def _summary(
    *,
    verdict: str,
    page_count: int,
    pages: List[Dict[str, Any]],
    pages_with_text: int,
    scanned_pages: int,
    ocred_pages: int,
    suggests_ocr: bool,
    suggests_scanner: bool,
) -> str:
    """One sentence a person can read, plus the caveats that belong with it."""
    examined = len(pages)

    if verdict == "text":
        parts = [
            f"{_pages(pages_with_text)} of the {examined} examined "
            f"{_agree(pages_with_text, 'carries', 'carry')} an extractable text "
            f"layer, so the text can be read directly."
        ]
        if ocred_pages:
            # Worth saying plainly: the text reads like any other text layer, and it
            # is a machine's reading of a picture, which anyone quoting it should
            # know. It also stops a caller OCRing a document that is already done.
            whose = (
                "All of them"
                if ocred_pages == pages_with_text
                else f"{_pages(ocred_pages)} of those"
            )
            parts.append(
                f"{whose} got that layer from pdf_ocr, so the text is recognition "
                f"output rather than the document's own and can hold recognition "
                f"errors; running OCR again would need force=True."
            )
        elif suggests_ocr:
            parts.append(
                "The writing tool suggests that text came from OCR, so it may "
                "contain recognition errors — check the excerpt."
            )
    elif verdict == "scanned":
        parts = [
            f"None of {_pages(examined)} examined yielded text, and "
            f"{_pages(scanned_pages)} {_agree(scanned_pages, 'is', 'are')} covered "
            f"by a single image, so this looks like a scan and needs OCR before "
            f"its text can be read."
        ]
    elif verdict == "mixed":
        parts = [
            f"{_pages(pages_with_text)} of the {examined} examined "
            f"{_agree(pages_with_text, 'carries', 'carry')} text while "
            f"{_pages(scanned_pages)} {_agree(scanned_pages, 'is', 'are')} covered "
            f"by a single image, so this document mixes both and OCR would only "
            f"help the scanned pages."
        ]
        if ocred_pages:
            # The half-finished case, which a range at a time makes common: naming
            # what is left keeps the next call from starting over.
            parts.append(
                f"{_pages(ocred_pages)} of the readable ones were read by pdf_ocr "
                f"already, so OCR the rest and pass the searchable copy back as the "
                f"ref so one document ends up carrying every layer."
            )
    elif examined:
        parts = [
            f"None of {_pages(examined)} examined yielded text, and none is "
            f"covered by a single image either, so the pages are probably blank, "
            f"drawn as vector graphics, or pictures with no writing on them. OCR "
            f"is unlikely to help."
        ]
    else:
        parts = ["The document has no pages to examine."]

    if verdict != "scanned" and suggests_scanner:
        parts.append("The writing tool looks like a scanner or office copier.")

    if examined < page_count:
        parts.append(
            f"Based on {_pages(examined)} sampled across {page_count}, so pages "
            f"in between could differ."
        )

    return " ".join(parts)


def _pages(count: int) -> str:
    """Render a page count, so a one page document reads as "1 page"."""
    return "1 page" if count == 1 else f"{count} pages"


def _agree(count: int, singular: str, plural: str) -> str:
    """Pick the verb form that agrees with a count."""
    return singular if count == 1 else plural


def _writing_tools(pdf_path: str) -> Tuple[Optional[str], Optional[str]]:
    """The Producer and Creator strings, as supporting evidence.

    Reads through `pdf_metadata` rather than off the Info dictionary directly, so
    a file whose properties live only in its XMP packet still contributes a hint,
    and so the ``/Creator`` is the application, ``/Author`` is the person trap
    stays solved in exactly one place. Costs a second parse of the document's
    trailer; the alternative was a worse copy of that module.
    """
    metadata = read_metadata(pdf_path)
    if not metadata.get("success"):
        return None, None
    return metadata.get("producer"), metadata.get("creator_tool")


def _matches(hints: Tuple[str, ...], *values: Optional[str]) -> bool:
    """True if any hint appears in any of the given strings."""
    haystack = " ".join(value.lower() for value in values if value)
    return any(hint in haystack for hint in hints)
