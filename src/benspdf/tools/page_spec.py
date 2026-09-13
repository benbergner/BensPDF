"""
The `pages` argument, shared by every verb that takes one.

The exception to one-module-per-verb in this package: `pdf_page_layout`,
`pdf_render_pages`, `pdf_ocr` and `pdf_extract_text` already take the same range
syntax, and `pdf_split`, `pdf_rotate` and `pdf_delete_pages` all will. One parser means one
syntax, one set of error messages, and one place to fix a bug in either.
"""

from typing import Any, List, Sequence, Set

#: The forms accepted, quoted in every error message so a caller can recover.
FORMS = '"1-20", "3", "1,5,9-12", or "all"'


def parse_pages(spec: Any, page_count: int) -> Set[int]:
    """Read a page range spec into a set of 1-based page numbers.

    Out of range numbers are clipped to the document rather than refused, since a
    caller asking for "1-100" of a 12 page file has made their intent clear. A
    spec that selects nothing at all is an error, because silently returning
    nothing looks like a tool that does not work.

    Args:
        spec: A range string, or anything that reads as one.
        page_count: How many pages the document has.

    Returns:
        The selected page numbers, 1-based.

    Raises:
        ValueError: The spec cannot be read, or selects no pages.
    """
    text = str(spec).strip().lower()
    if not text:
        raise ValueError(f"pages was empty. Use a range like {FORMS}.")

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
                f"like {FORMS}."
            ) from None

        if start > end:
            start, end = end, start
        wanted.update(range(max(1, start), min(page_count, end) + 1))

    if not wanted:
        raise ValueError(
            f"pages={spec!r} selects no pages; the document has {page_count}."
        )
    return wanted


def format_ranges(numbers: Sequence[int]) -> str:
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
