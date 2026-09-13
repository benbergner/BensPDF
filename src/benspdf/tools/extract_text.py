"""
The text of a document that has one: the work behind `pdf_extract_text`.

This is the *cheap* reading path, and the one to reach for first. A PDF with a
text layer already holds its characters; extracting them is milliseconds a page
and exact, where OCR rasterizes the page and guesses at text that was never in
doubt. `pdf_check_text` says which path a file needs, this verb walks the cheap
one, and `pdf_ocr` walks the other.

Four decisions shape the file:

**The text comes back per page, not as one blob.** Extraction is per page anyway,
and a page number beside the text is what lets an answer cite page 84 rather than
paste a wall of prose, so it is free. Measured on a 213 page book: 614,855
characters, 33.7 ms a page.

**There is a character budget, because that book is roughly 154,000 tokens.**
Handing a caller the whole of it costs more context than most clients have, and
the tool cannot know it went too far after the fact. So a call returns at most
`_MAX_INLINE_CHARS` of text and says which pages it stopped before, the same
shape `pdf_ocr` uses for its own limits. `output="txt"` is the way out: the whole
extraction goes into an artifact and only the counts come back, so a long
document is one call when the text is destined for a file rather than a context.

**Text that will not decode is reported, and saying so takes two signals.** A PDF
maps bytes to glyphs, and only a `/ToUnicode` map says which *characters* those
glyphs are; without one, extraction falls back to the font's encoding and can
return plausible-looking rubbish. The font evidence alone is far too noisy to
report: across 142 real PDFs, flagging any font without a `/ToUnicode` hit 38
files, and the narrow version of that rule - no map, a named or absent encoding,
an embedded non-standard-14 face - still hit 9, of which 7 extracted perfectly
clean prose. Requiring the text to *also* show it, with at least
`_ODD_MIN_CHARS` undecodable characters making up `_ODD_SHARE` of the page,
leaves 9 pages of the one document that genuinely mangles them (1.1% to 5.7%
odd characters each) and nothing else in the corpus. So a flagged page means
what it says.

**Reading order is pypdf's by default; `layout=True` keeps the spacing.** Layout
mode reconstructs the page as a grid of characters, which is what a form or a
table wants and what body text does not: measured on the same first pages, a
two-column form came back 1.1 times longer and much easier to read, an insurance
form 13.7 times longer and almost all whitespace, and dense justified prose had
its words split ("b olic regression") by the spacing it preserved. Default off,
available per call, and the character budget applies either way - so the cost of
asking for it is pages, not a surprise.

Layout mode also fails outright on some pages, returning nothing where plain
extraction returns a page of text, so a page is read both ways when it is asked
for and falls back to prose rather than coming back blank. `_page_text` has the
measurements.
"""

import time
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from pypdf import PdfReader
from pypdf.errors import FileNotDecryptedError

from benscore import artifact_path, err, ok, save
from .page_spec import format_ranges, parse_pages

#: Characters of text one call returns inline. Roughly 12,500 tokens, which a
#: client can hold alongside a conversation; the 213 page document measured above
#: is twelve times that. It is a budget rather than a per page truncation because
#: half a page is not quotable: a page is either here or named as remaining.
_MAX_INLINE_CHARS = 50_000

#: How long one call may spend extracting before it stops and reports where it
#: got to. At the measured 33.7 ms a page that is around 600 pages, so it binds
#: only on documents where the character budget already has, or on `output="txt"`
#: where there is no character budget to bind. MCP clients commonly give up at
#: 60 s and a timeout returns nothing at all, so stopping short of it turns the
#: worst case into a partial result that says what is left.
_TIME_BUDGET_S = 20.0

#: What `output` accepts. "text" is the default because the text is the answer;
#: "txt" writes it to an artifact and returns the counts, which is what a long
#: document or a file destined for the disk wants.
_OUTPUTS = ("text", "txt", "both")

#: Unicode categories of a character that did not decode: control, private use,
#: unassigned, surrogate. Newlines, carriage returns and tabs are control
#: characters too and are the ones extraction legitimately produces, so they are
#: excluded by `_ODD_EXPECTED` rather than by category.
_ODD_CATEGORIES = frozenset({"Cc", "Co", "Cn", "Cs"})

_ODD_EXPECTED = "\n\r\t"

#: Undecodable characters before a page's text is called into question, as a
#: count and as a share of the page. Both, because either alone misfires: three
#: stray characters in a dense page is a symbol the font spells oddly, and 100%
#: of a six character page is a caption. Measured to flag the pages that are
#: really mangled and no others - see the module docstring.
_ODD_MIN_CHARS = 3
_ODD_SHARE = 0.01

#: Share of the plain text that layout mode has to reproduce before its version
#: is the one returned. Measured: where layout mode works it returns *more*
#: characters than plain (1.1 to 13.7 times, the extra being alignment), and where
#: it fails it returns almost none, so the two cases sit either side of anything
#: in this range and the halfway mark needs no more precision than that.
_LAYOUT_FLOOR = 0.5

#: Page break in the artifact. A form feed is what plain text has always used
#: for one, so `text.split("\f")` gives the pages back and a reader shows the
#: break rather than the character.
_PAGE_BREAK = "\f"

#: Base font names that need no embedded file and no `/ToUnicode` map, because
#: every reader already knows them. Matched as a prefix, so "Helvetica-Bold"
#: counts.
_STANDARD_14 = (
    "Courier",
    "Helvetica",
    "Times",
    "Symbol",
    "ZapfDingbats",
    "Arial",  # not standard 14, but universally substituted for Helvetica
)


# --- the verb --------------------------------------------------------------


def extract_text(
    pdf_path: str,
    pages: Optional[str] = None,
    output: str = "text",
    layout: bool = False,
) -> Dict[str, Any]:
    """Extract a PDF's existing text, page by page.

    Args:
        pdf_path: Path to an existing PDF file. Artifact ids are resolved by the
            caller, not here.
        pages: Which pages, as "1-10", "3", "1,5,9-12" or "all". Defaults to the
            whole document, which the character and time budgets then limit.
        output: "text" for the text in the result, "txt" for it as an artifact
            with only the counts in the result, "both" for both.
        layout: Preserve the page's spacing instead of reading it as prose. For
            forms and tables; costs characters and can split words.

    Returns:
        A result dict: ``pages`` with one entry per page (its number, character
        and word counts, and its text), the totals, ``artifact`` when a file was
        asked for, and a ``summary``. On failure, a reason and what to do about
        it.
    """
    if output not in _OUTPUTS:
        return err(
            f"output={output!r} is not one of {', '.join(_OUTPUTS)}.",
            file_path=str(pdf_path),
        )

    path = Path(pdf_path).expanduser().resolve()
    if not path.exists():
        return err(
            f"File not found: {pdf_path}", file_path=str(path), file_exists=False
        )

    try:
        reader = PdfReader(str(path))
        page_count = len(reader.pages)
    except FileNotDecryptedError:
        return err(
            f"{path.name} is encrypted and needs a password, so its text cannot be "
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

    read: List[Dict[str, Any]] = []
    texts: Dict[int, str] = {}
    failed: List[Dict[str, Any]] = []
    inline_chars = 0
    inline_full = output == "txt"
    omitted: List[int] = []
    stopped_for: Optional[str] = None
    started = time.monotonic()

    for position, number in enumerate(wanted):
        # Checked before the page and never before the first: a call that returns
        # nothing because one page was slow is worse than one that overruns by a
        # page. The estimate is the mean of the pages already read, which is the
        # only per page cost that can be known.
        if position and _out_of_time(started, len(read)):
            stopped_for = "time"
            break
        # The character budget stops the call only when the text is the product.
        # With an artifact to fill, the rest of the document is still worth
        # reading; those pages come back with their counts and no text.
        if inline_full and output == "text":
            stopped_for = "characters"
            break

        try:
            text, fell_back = _page_text(reader.pages[number - 1], layout=layout)
        except FileNotDecryptedError:
            # Encryption discovered mid-document: a whole-file problem, and every
            # further page would fail the same way.
            return err(
                f"{path.name} is encrypted and needs a password to read page "
                f"{number}. pdf_check_access reports its encryption and permissions "
                f"without opening it.",
                file_path=str(path),
                file_name=path.name,
                page_count=page_count,
                encrypted=True,
            )
        except Exception as exc:  # noqa: BLE001 - one bad page, not a bad document
            failed.append({"page": number, "error": f"{type(exc).__name__}: {exc}"})
            continue

        entry: Dict[str, Any] = {
            "page": number,
            "characters": len(text),
            "words": len(text.split()),
        }
        if fell_back:
            entry["mode"] = "plain"
        odd = _odd_characters(text)
        if odd >= _ODD_MIN_CHARS and odd >= _ODD_SHARE * len(text):
            # The font evidence is asked for second because it is the expensive
            # half: the text signal has already narrowed this to a page worth
            # looking into.
            if _unmapped_fonts(reader.pages[number - 1]):
                entry["text_suspect"] = True
                entry["unmappable_characters"] = odd

        if inline_full:
            omitted.append(number)
        else:
            entry["text"] = text
            inline_chars += len(text)
            inline_full = inline_chars >= _MAX_INLINE_CHARS

        texts[number] = text
        read.append(entry)

    attempted = {entry["page"] for entry in read} | {entry["page"] for entry in failed}
    remaining = [number for number in wanted if number not in attempted]

    if not read:
        return _nothing_read(path, page_count, wanted, failed)

    characters = sum(entry["characters"] for entry in read)
    result: Dict[str, Any] = {
        "file_path": str(path),
        "file_name": path.name,
        "page_count": page_count,
        "mode": "layout" if layout else "plain",
        "pages": read,
        "pages_read": len(read),
        "characters": characters,
        "words": sum(entry["words"] for entry in read),
        "truncated": bool(remaining),
        "failed": failed,
        "character_budget": _MAX_INLINE_CHARS,
    }

    empty = [entry["page"] for entry in read if not entry["characters"]]
    if empty:
        result["empty_pages"] = format_ranges(empty)

    suspect = [entry["page"] for entry in read if entry.get("text_suspect")]
    if suspect:
        result["suspect_pages"] = format_ranges(suspect)

    plain = [entry["page"] for entry in read if entry.get("mode") == "plain"]
    if plain:
        result["pages_read_as_plain"] = format_ranges(plain)

    if omitted:
        # Read, counted, and deliberately not here: the artifact carries this
        # text. Named as a range so a caller who wants a piece of it inline knows
        # exactly what to ask for.
        result["text_omitted_pages"] = format_ranges(omitted)

    if remaining:
        result["pages_remaining"] = format_ranges(remaining)
        # Which budget stopped the call changes what to do: more characters than
        # one call returns is answered by output="txt" or a narrower range, while
        # a document this slow will take several calls whatever is asked for.
        result["stopped_for"] = stopped_for or "characters"

    if output in ("txt", "both"):
        try:
            result.update(_write_text_file(texts))
        except Exception as exc:  # noqa: BLE001 - the text is still worth returning
            result["txt_error"] = (
                f"The text was extracted but the file could not be written: "
                f"{type(exc).__name__}: {exc}"
            )

    result["summary"] = _summary(result, remaining=remaining, output=output)
    return ok(**result)


# --- one page --------------------------------------------------------------


def _page_text(page: Any, *, layout: bool) -> Tuple[str, bool]:
    """The page's text, and whether layout mode had to be given up on.

    Layout mode places characters on a grid, and a page whose font metrics it
    cannot work out comes back empty or nearly so: measured across 300 pages of
    real documents, 11 lost more than half their text that way and 7 of those lost
    all of it, title pages among them.
    Handing that back would be the worst kind of failure here - a caller asks for
    nicer spacing and silently receives a blank page. So when layout mode is asked
    for, both are read and the plain text wins whenever the laid out version has
    lost most of it. That doubles what layout mode costs, which is the cheaper
    half of a cheap verb, and it is why the fallback is per page rather than a
    property of the document.
    """
    plain = _clean(page.extract_text(extraction_mode="plain") or "")
    if not layout:
        return plain, False

    laid_out = _clean(page.extract_text(extraction_mode="layout") or "")
    if len(laid_out) < _LAYOUT_FLOOR * len(plain):
        return plain, True
    return laid_out, False


def _clean(text: str) -> str:
    """Drop trailing whitespace, which layout mode produces by the page.

    It pads every line to the width of the page, and padding is characters a
    caller pays for and cannot read. Leading whitespace stays: that is the
    indentation asking for layout mode was about.
    """
    return "\n".join(line.rstrip() for line in text.splitlines()).strip("\n")


def _odd_characters(text: str) -> int:
    """Characters that did not decode into anything a reader can show.

    The text half of the two signals behind `text_suspect`. Cheap enough to run
    on every page - it is one pass over characters that have already been
    extracted - which is why it is the half that runs first.
    """
    return sum(
        1
        for character in text
        if character not in _ODD_EXPECTED
        and unicodedata.category(character) in _ODD_CATEGORIES
    )


def _unmapped_fonts(page: Any) -> bool:
    """Does the page draw with a font that cannot say what its glyphs mean?

    A `/ToUnicode` map is the only authoritative statement of which characters a
    font's glyph codes stand for. Without one, pypdf reads the encoding instead,
    which for an embedded subset with a name-only encoding is a guess.

    Deliberately not a report on its own: plenty of files pass this test and
    extract perfectly (see the module docstring), and the resource dictionary
    lists every font the page *may* draw with rather than the ones it does. It
    exists to confirm a page whose text has already shown damage.
    """
    for font in _fonts(page):
        if "/ToUnicode" in font:
            continue
        base = str(_deref(font.get("/BaseFont")) or "")
        # An embedded subset is prefixed "ABCDEF+"; the name after it is what
        # tells a standard face from a custom one.
        if "+" in base:
            base = base.split("+", 1)[1]
        if base.startswith(_STANDARD_14):
            continue
        encoding = _deref(font.get("/Encoding"))
        if encoding is not None and not isinstance(encoding, str):
            # A dictionary encoding spells out its differences from a base
            # encoding, which is a real statement about the glyph codes.
            continue
        if _embedded(font):
            return True
    return False


def _fonts(page: Any) -> List[Any]:
    """The font dictionaries the page's resources declare.

    Only the top level ones. A composite font delegates to a CIDFont that holds
    the embedded file, but the map, the base name and the encoding all live on the
    parent, so the parent is what there is to judge - and `_embedded` follows the
    delegation when it needs to.
    """
    try:
        resources = _deref(_deref(page).get("/Resources")) or {}
        declared = _deref(resources.get("/Font")) or {}
        return [
            font
            for font in (_deref(value) for value in declared.values())
            if hasattr(font, "get")
        ]
    except Exception:  # noqa: BLE001 - a page whose fonts cannot be read is unjudged
        return []


def _embedded(font: Any) -> bool:
    """Does the file carry this font's outlines, rather than naming a system one?

    An embedded face with no character map is the case that extracts badly: the
    glyph codes are the subsetter's own, and nothing in the file says what they
    mean.
    """
    try:
        descriptor = _deref(font.get("/FontDescriptor"))
        if descriptor is None:
            for child in _deref(font.get("/DescendantFonts")) or []:
                descriptor = _deref(_deref(child).get("/FontDescriptor"))
                if descriptor is not None:
                    break
        if descriptor is None:
            return False
        return any(
            key in descriptor for key in ("/FontFile", "/FontFile2", "/FontFile3")
        )
    except Exception:  # noqa: BLE001 - unreadable descriptor, no claim made
        return False


def _deref(obj: Any) -> Any:
    """Follow an indirect reference, if that is what this is."""
    resolve = getattr(obj, "get_object", None)
    return resolve() if callable(resolve) else obj


def _out_of_time(started: float, read: int) -> bool:
    """Would one more page take this call past its time budget?

    With nothing read yet there is nothing to estimate from, so the answer is no:
    the budget is there to stop a long document, not to refuse a short one.
    """
    elapsed = time.monotonic() - started
    if read < 1:
        return elapsed >= _TIME_BUDGET_S
    return elapsed + (elapsed / read) > _TIME_BUDGET_S


# --- the file --------------------------------------------------------------


def _write_text_file(texts: Dict[int, str]) -> Dict[str, Any]:
    """Save the extracted text as one .txt artifact, pages separated by a form feed."""
    body = _PAGE_BREAK.join(texts[number] for number in sorted(texts))
    data = body.encode("utf-8")
    artifact = save(data, ".txt")
    return {
        "artifact": artifact,
        # Both, deliberately: other tools take the id, a person opens the path.
        "path": str(artifact_path(artifact)),
        "artifacts": [artifact],
        "size_bytes": len(data),
        "text_file_pages": format_ranges(sorted(texts)),
    }


# --- when nothing was read -------------------------------------------------


def _nothing_read(
    path: Path,
    page_count: int,
    wanted: Sequence[int],
    failed: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    """No page produced an entry, which only happens when every one of them failed."""
    return err(
        f"None of the {len(wanted)} requested pages of {path.name} could be read: "
        f"{failed[0]['error'] if failed else 'no pages were reached'}.",
        file_path=str(path),
        file_name=path.name,
        page_count=page_count,
        pages=[],
        pages_read=0,
        failed=list(failed),
    )


# --- the sentence ----------------------------------------------------------


def _summary(
    result: Dict[str, Any],
    *,
    remaining: Sequence[int],
    output: str,
) -> str:
    """One sentence: what came out, what to distrust in it, and what to do next."""
    read = result["pages"]
    numbers = format_ranges([entry["page"] for entry in read])
    count = len(read)
    subject = "1 page" if count == 1 else f"{count} pages"
    mode = " keeping the page's spacing" if result["mode"] == "layout" else ""

    parts = [
        f"Extracted the text of {subject} of {result['file_name']} (page {numbers})"
        f"{mode}: {result['characters']} characters, {result['words']} words."
    ]

    if not result["characters"]:
        parts.append(
            "None of those pages carries any text, so this is a scan or a "
            "vector-only document: pdf_check_text says which, and pdf_ocr reads a "
            "scan."
        )
    elif "empty_pages" in result:
        parts.append(
            f"Page {result['empty_pages']} holds no text at all, so it is blank or "
            f"an image; pdf_ocr reads a scanned page and pdf_render_pages shows one."
        )

    if "pages_read_as_plain" in result:
        parts.append(
            f"Page {result['pages_read_as_plain']} came back as prose instead, "
            f"because laying it out lost most of its text."
        )

    if "suspect_pages" in result:
        parts.append(
            f"Page {result['suspect_pages']} draws with fonts that carry no character "
            f"map and its text came back with characters that did not decode, so read "
            f"that text as unreliable: pdf_render_pages shows what the page really "
            f"says, and pdf_ocr can re-read it with force=True."
        )

    if "artifact" in result:
        parts.append(
            f"The text of page {result['text_file_pages']} is artifact "
            f"{result['artifact']}, pages separated by a form feed; pass it to export "
            f"to keep it."
        )

    if "text_omitted_pages" in result:
        parts.append(
            f"The text of page {result['text_omitted_pages']} is in that file rather "
            f'than here; ask for those pages with output="text" to read them '
            f"directly."
        )

    if remaining:
        why = (
            f"This call reached its {int(_TIME_BUDGET_S)} second budget, which is "
            f"there because a client that gives up waiting returns nothing at all"
            if result.get("stopped_for") == "time"
            else f"That is the {_MAX_INLINE_CHARS} characters one call returns, which "
            f"is about as much as a client can hold"
        )
        how = (
            ', or output="txt" for the whole document as a file.'
            if output == "text"
            else ", which writes a second text file rather than adding to this one."
        )
        parts.append(
            f"{why}, so page {result['pages_remaining']} was not read. Ask for "
            f'pages="{format_ranges(remaining)}" to continue{how}'
        )

    if result["failed"]:
        numbers = format_ranges([entry["page"] for entry in result["failed"]])
        parts.append(f"Page {numbers} could not be read.")

    return " ".join(parts)
