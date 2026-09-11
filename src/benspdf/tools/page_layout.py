"""
The shape of a PDF's pages: the work behind `pdf_page_layout`.

Answers "what size is this, and is it all the same?". Cheap, because page
geometry lives in the page tree — no content stream is parsed and no page is
rendered — so every page is measured rather than sampled.

Three things shape the file:

**Group first, list second.** 131 of 141 real documents have one page shape, and
the other 10 have exactly two. A row per page would be almost entirely repetition,
and a 3000 page document would flood the caller's context to say "it's all A4". So
the answer is a list of size groups, each carrying the page numbers it covers as a
range string. Per page rows exist, but only for a `pages` range the caller asks
for.

**Group with a tolerance, not by exact numbers.** Real A4 pages measure
595.2x841.6, 595.5x842.2, 597.6x840.0. Grouping on exact dimensions split 13 of
those 141 documents into two or three groups that a person would call one
uniform A4 document, so sizes within 3pt of a known paper are named and grouped
together, and the group reports the exact size its pages actually have.

**The page a reader sees.** That is the CropBox, intersected with the MediaBox as
the spec requires, with `/Rotate` applied — so a portrait page rotated 90 degrees
is reported as landscape, which is what anyone looking at it would say. The raw
boxes are still there in the per page detail for print production questions.
"""

from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from pypdf import PdfReader
from pypdf.errors import FileNotDecryptedError

from ..core import err, ok

#: Known paper sizes in points, as (short edge, long edge). Orientation is worked
#: out separately, so each entry covers both.
_PAPERS: Tuple[Tuple[str, float, float], ...] = (
    ("A0", 2383.94, 3370.39),
    ("A1", 1683.78, 2383.94),
    ("A2", 1190.55, 1683.78),
    ("A3", 841.89, 1190.55),
    ("A4", 595.28, 841.89),
    ("A5", 419.53, 595.28),
    ("A6", 297.64, 419.53),
    ("B4", 708.66, 1000.63),
    ("B5", 498.90, 708.66),
    ("Letter", 612.0, 792.0),
    ("Legal", 612.0, 1008.0),
    ("Tabloid", 792.0, 1224.0),
    ("Executive", 521.86, 756.0),
    ("Half Letter", 396.0, 612.0),
)

#: How far a page may be from a known size and still be called by its name. Three
#: points is about a millimetre: enough to absorb the rounding real producers
#: introduce, too little to reach the next size in the table.
_PAPER_TOLERANCE_PT = 3.0

#: Cap on per page rows, so `pages="all"` on a long document cannot flood the
#: context window. The groups already describe every page.
_MAX_DETAIL_PAGES = 100

#: Boxes that are neither inheritable nor defaulted by the spec, so their presence
#: is meaningful: they say this file was prepared for print.
_PRINT_BOXES = (("trim", "/TrimBox"), ("bleed", "/BleedBox"), ("art", "/ArtBox"))

_MM_PER_PT = 25.4 / 72.0

#: Dimensions within this many points of each other count as the same size.
_SAME_SIZE_PT = 0.5


def read_page_layout(pdf_path: str, pages: Optional[str] = None) -> Dict[str, Any]:
    """Measure every page of a PDF and group the results by shape.

    Args:
        pdf_path: Path to an existing PDF file. Artifact ids are resolved by the
            caller, not here.
        pages: Optional range for per page detail, like "1-20", "3", "1,5,9-12"
            or "all". Omit for the size groups only.

    Returns:
        A result dict. On success: ``sizes`` (one entry per distinct shape, most
        pages first), ``uniform``, a ``summary`` sentence, and ``pages`` with per
        page rows when a range was asked for.
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
        measured = [_measure(page, index) for index, page in enumerate(reader.pages)]
    except FileNotDecryptedError:
        return err(
            f"{path.name} is encrypted and needs a password, so its pages cannot "
            f"be measured. pdf_check_access reports its encryption and permissions "
            f"without opening it.",
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

    wanted: Set[int] = set()
    if pages is not None:
        try:
            wanted = _parse_pages(pages, page_count)
        except ValueError as exc:
            return err(
                str(exc),
                file_path=str(path),
                file_name=path.name,
                page_count=page_count,
            )

    groups = _group(measured)
    detail = [entry for entry in measured if entry["page"] in wanted]
    truncated = len(detail) > _MAX_DETAIL_PAGES

    result: Dict[str, Any] = {
        "file_path": str(path),
        "file_name": path.name,
        "page_count": page_count,
        "uniform": len(groups) == 1,
        "summary": _summary(groups, page_count, measured),
        "sizes": groups,
        "unreadable_pages": sum(1 for entry in measured if "error" in entry),
        "has_print_boxes": any(
            any(name in entry["boxes"] for name, _key in _PRINT_BOXES)
            for entry in measured
        ),
    }

    if pages is not None:
        result["pages"] = detail[:_MAX_DETAIL_PAGES]
        result["detail_truncated"] = truncated
        if truncated:
            result["summary"] += (
                f" Per page detail is capped at {_MAX_DETAIL_PAGES} rows; ask for a "
                f"narrower range to see the rest."
            )

    return ok(**result)


# --- one page --------------------------------------------------------------


def _measure(page: Any, index: int) -> Dict[str, Any]:
    """Measure one page: its visible size, rotation, and the boxes it declares.

    Which boxes a file *explicitly* declares has to be recorded before any of
    pypdf's box accessors are touched. Reading ``page.cropbox`` writes the
    resolved rectangle back onto the page, so a later ``"/CropBox" in page`` would
    answer True for a file that never had one — turning an inherited default into
    a claim about the document.
    """
    entry: Dict[str, Any] = {"page": index + 1}

    try:
        declared = {name: key in page for name, key in _PRINT_BOXES}
        declared["crop"] = "/CropBox" in page

        media = _rect(page.mediabox)
        visible = _visible_box(page, media)
        unit = _user_unit(page)
        rotation = _rotation(page)

        width, height = visible[2] - visible[0], visible[3] - visible[1]
        if rotation in (90, 270):
            width, height = height, width

        boxes: Dict[str, List[float]] = {"media": media}
        if declared["crop"]:
            boxes["crop"] = _rect(page.cropbox)
        for name, _key in _PRINT_BOXES:
            if declared[name]:
                boxes[name] = _rect(getattr(page, f"{name}box"))

        entry.update(
            {
                "width_pt": round(width, 1),
                "height_pt": round(height, 1),
                "rotation": rotation,
                "paper": _paper(width, height),
                "orientation": _orientation(width, height),
                "boxes": boxes,
                **_physical(width, height, unit),
            }
        )
        if unit != 1.0:
            entry["user_unit"] = unit
    except Exception as exc:  # noqa: BLE001 - one bad page, not a bad document
        entry.update(
            {
                "error": f"could not be measured: {type(exc).__name__}: {exc}",
                "width_pt": None,
                "height_pt": None,
                "rotation": None,
                "paper": None,
                "orientation": None,
                "boxes": {},
            }
        )

    return entry


def _visible_box(page: Any, media: List[float]) -> List[float]:
    """The CropBox clipped to the MediaBox, which is the page a reader sees.

    The spec says a CropBox reaching outside the MediaBox is clipped to it, and
    files do reach outside. A degenerate result falls back to the MediaBox rather
    than reporting a page of zero width.
    """
    crop = _rect(page.cropbox)
    clipped = [
        max(crop[0], media[0]),
        max(crop[1], media[1]),
        min(crop[2], media[2]),
        min(crop[3], media[3]),
    ]
    if clipped[2] - clipped[0] <= 0 or clipped[3] - clipped[1] <= 0:
        return media
    return clipped


def _rect(box: Any) -> List[float]:
    """A pypdf rectangle as [left, bottom, right, top], normalized.

    Corners can be given in either order — a box written top-down is legal and
    common enough — so they are sorted rather than trusted.
    """
    left, right = sorted((float(box.left), float(box.right)))
    bottom, top = sorted((float(box.bottom), float(box.top)))
    return [round(left, 2), round(bottom, 2), round(right, 2), round(top, 2)]


def _rotation(page: Any) -> int:
    """The page's rotation, normalized to 0, 90, 180 or 270.

    ``/Rotate`` must be a multiple of 90 but is not always: negative values are
    normal (-90 means 270), and other values appear in broken files, where the
    least misleading reading is no rotation at all.
    """
    try:
        value = int(page.rotation) % 360
    except (TypeError, ValueError):
        return 0
    return value if value in (0, 90, 180, 270) else 0


def _user_unit(page: Any) -> float:
    """The page's user unit, which scales points to physical size.

    Almost always 1. Large format drawings use it to escape the 200 inch limit on
    a PDF page, and ignoring it would report a plan as a fraction of its size.
    """
    try:
        unit = float(page.user_unit)
    except (TypeError, ValueError):
        return 1.0
    return unit if unit > 0 else 1.0


def _physical(width: float, height: float, unit: float) -> Dict[str, float]:
    """Millimetres and inches, the units a person actually asked in."""
    return {
        "width_mm": round(width * _MM_PER_PT * unit, 1),
        "height_mm": round(height * _MM_PER_PT * unit, 1),
        "width_in": round(width / 72.0 * unit, 2),
        "height_in": round(height / 72.0 * unit, 2),
    }


def _paper(width: float, height: float) -> Optional[str]:
    """The name of this size, if it is a size anyone has a name for."""
    short, long = sorted((width, height))
    for name, paper_short, paper_long in _PAPERS:
        if (
            abs(short - paper_short) <= _PAPER_TOLERANCE_PT
            and abs(long - paper_long) <= _PAPER_TOLERANCE_PT
        ):
            return name
    return None


def _orientation(width: float, height: float) -> str:
    if abs(width - height) <= _SAME_SIZE_PT:
        return "square"
    return "landscape" if width > height else "portrait"


# --- grouping --------------------------------------------------------------


def _group(measured: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Collapse the measured pages into one entry per distinct shape.

    Keyed on the paper name where there is one, so pages a millimetre apart stay
    together, and on rounded dimensions otherwise. Rotation is part of the key:
    a page that presents as landscape because it was rotated is a different
    situation from one that was made landscape, and the difference is exactly what
    someone debugging a sideways printout is looking for.
    """
    buckets: Dict[Tuple[Any, ...], List[Dict[str, Any]]] = {}
    for entry in measured:
        if "error" in entry:
            continue
        key = (
            entry["paper"] or (round(entry["width_pt"]), round(entry["height_pt"])),
            entry["orientation"],
            entry["rotation"],
        )
        buckets.setdefault(key, []).append(entry)

    groups = []
    for members in buckets.values():
        # Report a size the pages really have, rather than an average of them.
        sizes = Counter((entry["width_pt"], entry["height_pt"]) for entry in members)
        (width, height), _count = sizes.most_common(1)[0]
        first = members[0]
        unit = first.get("user_unit", 1.0)

        groups.append(
            {
                "paper": first["paper"],
                "orientation": first["orientation"],
                "rotation": first["rotation"],
                "width_pt": width,
                "height_pt": height,
                **_physical(width, height, unit),
                "page_count": len(members),
                "pages": _ranges([entry["page"] for entry in members]),
                "exact_sizes_vary": len(sizes) > 1,
            }
        )

    groups.sort(key=lambda group: (-group["page_count"], group["pages"]))
    return groups


def _ranges(numbers: Sequence[int]) -> str:
    """Page numbers as a range string: [1, 2, 3, 5] becomes "1-3,5".

    A group can cover thousands of pages, and a thousand integers is a poor way to
    say "all of them".
    """
    ordered = sorted(numbers)
    if not ordered:
        return ""

    parts: List[str] = []
    start = previous = ordered[0]

    for number in ordered[1:]:
        if number == previous + 1:
            previous = number
        else:
            parts.append(_span(start, previous))
            start = previous = number

    parts.append(_span(start, previous))
    return ",".join(parts)


def _span(start: int, end: int) -> str:
    return str(start) if start == end else f"{start}-{end}"


# --- the pages argument ----------------------------------------------------


def _parse_pages(spec: Any, page_count: int) -> Set[int]:
    """Read a page range spec into a set of 1-based page numbers.

    Out of range numbers are clipped to the document rather than refused, since a
    caller asking for "1-100" of a 12 page file has made their intent clear. A
    spec that selects nothing at all is an error, because silently returning no
    detail looks like a tool that does not work.
    """
    text = str(spec).strip().lower()
    if not text:
        raise ValueError(
            'pages was empty. Use a range like "1-20", "3", "1,5,9-12", or "all".'
        )

    if text == "all":
        return set(range(1, page_count + 1))

    wanted: Set[int] = set()
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            if "-" in part.lstrip("-"):
                start_text, end_text = part.split("-", 1)
                start, end = int(start_text), int(end_text)
            else:
                start = end = int(part)
        except ValueError:
            raise ValueError(
                f"Could not read {part!r} as a page or page range. Use something "
                f'like "1-20", "3", "1,5,9-12", or "all".'
            ) from None

        if start > end:
            start, end = end, start
        wanted.update(range(max(1, start), min(page_count, end) + 1))

    if not wanted:
        raise ValueError(
            f"pages={spec!r} selects no pages; the document has {page_count}."
        )
    return wanted


# --- the sentence ----------------------------------------------------------


def _summary(
    groups: Sequence[Dict[str, Any]],
    page_count: int,
    measured: Sequence[Dict[str, Any]],
) -> str:
    """One sentence covering the shape of the document."""
    unreadable = sum(1 for entry in measured if "error" in entry)

    if not groups:
        if page_count == 0:
            return "The document has no pages."
        return f"None of the {page_count} pages could be measured."

    if len(groups) == 1:
        group = groups[0]
        if page_count == 1:
            subject = "The only page is"
        else:
            subject = f"All {page_count} pages are"
        sentence = f"{subject} {_label(group)} ({_size(group)})."
    else:
        shown = [
            f"{_pages(group['page_count'])} "
            f"{_agree(group['page_count'], 'is', 'are')} {_label(group)}"
            for group in groups[:3]
        ]
        listing = ", ".join(shown[:-1]) + f" and {shown[-1]}"
        rest = len(groups) - len(shown)
        sentence = f"Page sizes vary: {listing}"
        sentence += f", plus {rest} more." if rest > 0 else "."
        sentence += f" The most common is {_size(groups[0])}."

    if unreadable:
        sentence += f" {_pages(unreadable)} could not be measured."
    return sentence


def _label(group: Dict[str, Any]) -> str:
    """How to name a size in a sentence: "A4 portrait", or "960 x 540 pt".

    A size with no name is called by its measurements, and then the parenthetical
    that follows gives only the physical units — otherwise the sentence says the
    same numbers twice.
    """
    name = group["paper"]
    label = f"{name} {group['orientation']}" if name else _points(group)
    if group["rotation"]:
        label += f", rotated {group['rotation']}°"
    return label


def _size(group: Dict[str, Any]) -> str:
    """The measurements to put after a label, in the units that suit it."""
    physical = (
        f"{_trim(group['width_mm'])} x {_trim(group['height_mm'])} mm"
        if group["paper"] and group["paper"][0] in "AB"
        else f"{_trim(group['width_in'])} x {_trim(group['height_in'])} in"
    )
    if not group["paper"]:
        return physical  # the label already carried the points
    return f"{_points(group)}, {physical}"


def _points(group: Dict[str, Any]) -> str:
    return f"{_trim(group['width_pt'])} x {_trim(group['height_pt'])} pt"


def _trim(value: float) -> str:
    """Drop a trailing .0, so pages read as 595 x 842 rather than 595.0 x 842.0."""
    return f"{value:g}"


def _pages(count: int) -> str:
    """Render a page count, so one page reads as "1 page"."""
    return "1 page" if count == 1 else f"{count} pages"


def _agree(count: int, singular: str, plural: str) -> str:
    """Pick the verb form that agrees with a count."""
    return singular if count == 1 else plural
