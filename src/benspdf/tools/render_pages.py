"""
Turning pages into images: the work behind `pdf_render_pages`.

This verb is about *seeing*, not reading. For reading a scan, `pdf_ocr` is the
better path — it yields a text layer that is searchable, reusable across turns,
and legible to a local text model, which an image never is. Rendering earns its
place where no text layer can help: checking that a mutation did what it claimed
(above all that a redaction removed content rather than covering it), previews,
and pages whose *appearance* is the content — handwriting, signatures, charts,
checkbox state, stamps.

It is also the first verb here that produces artifacts rather than answers, so it
is the first real use of the artifact store: every rendered page is saved and
returned as an id, and nothing reaches the user's disk until `export` is called.

Rasterizing is the one operation in this package that cannot be made cheap by
sampling, so the limits are explicit rather than clever. Three of them, all
observed rather than guessed:

* **A page cap.** Rendering is per page work with no shortcut, so a 213 page
  document renders its first `_MAX_PAGES` and says it stopped.
* **A pixel cap.** One page in a 141 file corpus is 34 x 49 inches, which at the
  default resolution is 37.8 megapixels and 113 MB of bitmap for a single page.
  Resolution is reduced per page to keep the longest edge under `_MAX_EDGE_PX`,
  and the page reports the resolution it actually got.
* **Nothing is downscaled silently.** Where a limit bites, the result says so.
"""

import io
from pathlib import Path
from typing import Any, Dict, List, Optional

import pypdfium2 as pdfium

from benscore import artifact_path, err, ok, save
from .page_spec import format_ranges, parse_pages

#: Most pages one call will render. Bulk work is a loop of calls, deliberately,
#: so the caller sees the cost.
_MAX_PAGES = 20

#: Default resolution. 150 dpi puts A4 at 1241 x 1754, which is comfortably above
#: what vision models keep after their own downscaling and still fine for a print
#: preview.
_DEFAULT_DPI = 150

#: Resolution bounds. Below 36 nothing is legible; above 600 the memory cost grows
#: faster than the usefulness.
_MIN_DPI = 36
_MAX_DPI = 600

#: Longest edge in pixels, whatever the resolution asked for. This is a memory
#: guard, not a quality choice: 4000 px on the long edge of an A4 is about 350 dpi.
_MAX_EDGE_PX = 4000

_PT_PER_INCH = 72.0


def render_pages(
    pdf_path: str,
    pages: Optional[str] = None,
    dpi: int = _DEFAULT_DPI,
) -> Dict[str, Any]:
    """Render pages of a PDF to PNG artifacts.

    Args:
        pdf_path: Path to an existing PDF file. Artifact ids are resolved by the
            caller, not here.
        pages: Which pages, as "1-20", "3", "1,5,9-12" or "all". Defaults to the
            first `_MAX_PAGES` pages.
        dpi: Resolution, clamped to a sane range and reduced per page if the
            result would be enormous.

    Returns:
        A result dict: ``pages`` with one entry per rendered page (its number,
        artifact id and pixel size), ``artifacts`` as a flat list ready for
        `export`, and a ``summary``.
    """
    path = Path(pdf_path).expanduser().resolve()

    if not path.exists():
        return err(
            f"File not found: {pdf_path}",
            file_path=str(path),
            file_exists=False,
        )

    requested_dpi = _clamp_dpi(dpi)

    try:
        document = pdfium.PdfDocument(str(path))
        page_count = len(document)
    except Exception as exc:  # noqa: BLE001 - return a reason, never raise
        return err(
            _read_error(path, exc),
            file_path=str(path),
            file_name=path.name,
            encrypted=_looks_encrypted(exc),
        )

    if page_count == 0:
        return err(
            f"{path.name} has no pages to render.",
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

    selected, truncated = wanted[:_MAX_PAGES], len(wanted) > _MAX_PAGES

    rendered: List[Dict[str, Any]] = []
    failed: List[Dict[str, Any]] = []

    for number in selected:
        try:
            rendered.append(_render_one(document, number, requested_dpi))
        except Exception as exc:  # noqa: BLE001 - one bad page, not a bad document
            failed.append({"page": number, "error": f"{type(exc).__name__}: {exc}"})

    try:
        document.close()
    except Exception:  # noqa: BLE001 - nothing left to do about it
        pass

    if not rendered:
        return err(
            f"None of the {len(selected)} requested pages of {path.name} could be "
            f"rendered.",
            file_path=str(path),
            file_name=path.name,
            page_count=page_count,
            failed=failed,
        )

    return ok(
        file_path=str(path),
        file_name=path.name,
        page_count=page_count,
        format="png",
        dpi=requested_dpi,
        pages=rendered,
        artifacts=[entry["artifact"] for entry in rendered],
        rendered=len(rendered),
        truncated=truncated,
        failed=failed,
        summary=_summary(
            rendered=rendered,
            wanted=wanted,
            truncated=truncated,
            failed=failed,
            requested_dpi=requested_dpi,
            file_name=path.name,
        ),
    )


# --- one page --------------------------------------------------------------


def _render_one(document: Any, number: int, dpi: int) -> Dict[str, Any]:
    """Rasterize one page and store it as a PNG artifact.

    The page pdfium draws is the page a reader sees — CropBox, with ``/Rotate``
    applied — which is the same page `pdf_page_layout` measures. Verified against
    it on rotated and cropped files, so the two verbs cannot disagree about what
    "the page" is.
    """
    page = document[number - 1]
    width_pt, height_pt = page.get_size()
    effective_dpi = _fit_dpi(width_pt, height_pt, dpi)

    bitmap = page.render(scale=effective_dpi / _PT_PER_INCH)
    image = bitmap.to_pil()

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    data = buffer.getvalue()

    artifact = save(data, ".png")
    entry: Dict[str, Any] = {
        "page": number,
        "artifact": artifact,
        # Both, deliberately: other tools take the id, a person opens the path.
        "path": str(artifact_path(artifact)),
        "width_px": image.width,
        "height_px": image.height,
        "dpi": effective_dpi,
        "size_bytes": len(data),
    }
    if effective_dpi != dpi:
        entry["dpi_reduced_from"] = dpi
    return entry


def _clamp_dpi(dpi: Any) -> int:
    """Keep the requested resolution inside the range that makes sense."""
    try:
        value = int(dpi)
    except (TypeError, ValueError):
        return _DEFAULT_DPI
    return max(_MIN_DPI, min(_MAX_DPI, value))


def _fit_dpi(width_pt: float, height_pt: float, dpi: int) -> int:
    """Reduce resolution for a page whose render would be enormous.

    Large format pages are the case: a 34 x 49 inch plan at 150 dpi is 37.8
    megapixels, which is 113 MB of bitmap before it is even encoded.
    """
    longest_pt = max(abs(width_pt), abs(height_pt))
    if longest_pt <= 0:
        return dpi

    ceiling = int(_MAX_EDGE_PX * _PT_PER_INCH / longest_pt)
    return max(_MIN_DPI, min(dpi, ceiling))


def _read_error(path: Path, exc: Exception) -> str:
    """Turn a pdfium failure into something a caller can act on."""
    if _looks_encrypted(exc):
        return (
            f"{path.name} is encrypted and needs a password, so its pages cannot "
            f"be rendered. pdf_check_access reports its encryption and permissions "
            f"without opening it."
        )
    return f"Could not read {path.name} as a PDF: {type(exc).__name__}: {exc}"


def _looks_encrypted(exc: Exception) -> bool:
    """Is this failure a password problem?

    pdfium reports the reason in the message rather than in the exception type,
    so this reads the text. Wrong only in the direction of a less specific error
    message.
    """
    return "password" in str(exc).lower()


# --- the sentence ----------------------------------------------------------


def _summary(
    *,
    rendered: List[Dict[str, Any]],
    wanted: List[int],
    truncated: bool,
    failed: List[Dict[str, Any]],
    requested_dpi: int,
    file_name: str,
) -> str:
    """One sentence: what was rendered, at what size, and where the limits bit."""
    numbers = format_ranges([entry["page"] for entry in rendered])
    first = rendered[0]
    count = len(rendered)
    subject = "1 page" if count == 1 else f"{count} pages"

    parts = [
        f"Rendered {subject} of {file_name} (page {numbers}) as PNG at "
        f"{first['dpi']} dpi, {first['width_px']} x {first['height_px']} px."
    ]

    reduced = [entry for entry in rendered if "dpi_reduced_from" in entry]
    if reduced:
        pages = format_ranges([entry["page"] for entry in reduced])
        parts.append(
            f"Page {pages} was too large to render at {requested_dpi} dpi and was "
            f"reduced to keep it under {_MAX_EDGE_PX} px on the longest edge."
        )

    if truncated:
        parts.append(
            f"{len(wanted)} pages were asked for and {_MAX_PAGES} is the limit per "
            f"call, so the rest were skipped; ask for a later range to continue."
        )

    if failed:
        pages = format_ranges([entry["page"] for entry in failed])
        parts.append(f"Page {pages} could not be rendered.")

    parts.append(
        "Pass the artifact ids to export to keep them, or open a page's path to "
        "look at it."
    )
    return " ".join(parts)
