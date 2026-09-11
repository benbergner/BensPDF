"""
Reading a PDF's own description of itself: title, author, dates, producer.

A PDF can carry that description in two independent places:

* the **Info dictionary**, the original PDF 1.x mechanism, and
* an **XMP packet**, the RDF/XML store that PDF 2.0 moves to, deprecating most
  Info keys in the process.

They disagree in practice, not just in theory, because real files pass through
toolchains that update one store and leave the other stale. So this module never
collapses them into a single view: it reports both verbatim, a normalized answer
with the store each value came from, and an explicit list of the fields where the
two disagree. That disagreement is often the interesting part of a provenance
question, and flattening would silently pick a winner.

The normalized answer prefers XMP when a field is present in both, following the
direction the spec moved in.
"""

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from pypdf import PdfReader
from pypdf.errors import FileNotDecryptedError

from ..core import err, ok

#: Malformed PDFs raise almost anything while poking at individual fields, so this
#: catches broadly on purpose: one bad date should cost that date, not the whole
#: result. Listing expected types instead let pypdf errors outside the
#: ``PdfReadError`` branch of its hierarchy — ``LimitReachedError``,
#: ``DependencyError`` — escape as tracebacks.
_PDF_ERRORS = Exception

#: Normalized field names, in the order a person would read them.
_FIELDS = (
    "title",
    "author",
    "subject",
    "keywords",
    "creator_tool",
    "producer",
    "created",
    "modified",
)

#: ``keywords`` is a list, everything else is a scalar. Kept separate so an
#: absent value is an empty list rather than None.
_LIST_FIELDS = frozenset({"keywords"})

#: Timestamps, which compare by instant rather than by rendered string.
_DATE_FIELDS = frozenset({"created", "modified"})


def read_metadata(pdf_path: str) -> Dict[str, Any]:
    """Read both metadata stores from a PDF.

    Args:
        pdf_path: Path to an existing PDF file. Artifact ids are resolved by the
            caller, not here.

    Returns:
        A result dict. On success: the normalized fields at the top level, plus
        ``sources``, ``conflicts``, ``has_conflicts``, ``has_info``, ``has_xmp``,
        and the raw ``info`` and ``xmp`` stores.
    """
    path = Path(pdf_path).expanduser().resolve()

    if not path.exists():
        return err(
            f"File not found: {pdf_path}",
            file_path=str(path),
            file_exists=False,
        )

    # Both stores are read inside this block on purpose. pypdf opens an encrypted
    # file without complaint and only raises when something is dereferenced, so
    # reading them eagerly here is what turns encryption into a proper error
    # instead of a readable PDF that appears to have no metadata.
    try:
        reader = PdfReader(str(path))
        info_raw = _read_info(reader)
        xmp_raw = _read_xmp(reader)
    except FileNotDecryptedError:
        return err(
            f"{path.name} is encrypted and needs a password, so its metadata "
            f"cannot be read.",
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

    from_info = _from_info(reader, info_raw)
    from_xmp = _from_xmp(xmp_raw)

    values: Dict[str, Any] = {}
    sources: Dict[str, Optional[str]] = {}
    conflicts: Dict[str, Dict[str, Any]] = {}

    for field in _FIELDS:
        info_value = from_info.get(field)
        xmp_value = from_xmp.get(field)

        if (
            _present(info_value)
            and _present(xmp_value)
            and not _equivalent(field, info_value, xmp_value)
        ):
            conflicts[field] = {"info": info_value, "xmp": xmp_value}

        if _present(xmp_value):
            values[field], sources[field] = xmp_value, "xmp"
        elif _present(info_value):
            values[field], sources[field] = info_value, "info"
        else:
            values[field] = [] if field in _LIST_FIELDS else None
            sources[field] = None

    return ok(
        file_path=str(path),
        file_name=path.name,
        **values,
        sources=sources,
        conflicts=conflicts,
        has_conflicts=bool(conflicts),
        has_info=bool(info_raw),
        has_xmp=bool(xmp_raw),
        info=info_raw,
        xmp=xmp_raw,
    )


# --- the two stores, read verbatim -----------------------------------------


def _read_info(reader: PdfReader) -> Dict[str, str]:
    """The Info dictionary as plain strings, so the result stays JSON-safe."""
    try:
        info = reader.metadata
    except FileNotDecryptedError:
        raise  # a whole-document problem, not a bad field; the caller reports it
    except _PDF_ERRORS:
        return {}

    if not info:
        return {}

    raw: Dict[str, str] = {}
    for key, value in info.items():
        try:
            text = _text(value)
        except _PDF_ERRORS:
            continue
        if text is not None:
            raw[str(key)] = text
    return raw


def _read_xmp(reader: PdfReader) -> Dict[str, Any]:
    """The XMP fields we understand, keeping each one's natural shape.

    Language alternatives stay dicts (``{'x-default': 'Title'}``) and ordered
    arrays stay lists, because that is what the packet actually said. Only dates
    are converted, to ISO 8601 strings, since datetimes are not JSON-safe.
    """
    try:
        xmp = reader.xmp_metadata
    except FileNotDecryptedError:
        raise  # as in _read_info: encryption is the caller's to report
    except _PDF_ERRORS:
        return {}

    if xmp is None:
        return {}

    readers = {
        "dc_title": lambda: xmp.dc_title,
        "dc_creator": lambda: xmp.dc_creator,
        "dc_description": lambda: xmp.dc_description,
        "dc_subject": lambda: xmp.dc_subject,
        "pdf_keywords": lambda: xmp.pdf_keywords,
        "pdf_producer": lambda: xmp.pdf_producer,
        "xmp_creator_tool": lambda: xmp.xmp_creator_tool,
        "xmp_create_date": lambda: _iso(xmp.xmp_create_date),
        "xmp_modify_date": lambda: _iso(xmp.xmp_modify_date),
        "custom_properties": lambda: xmp.custom_properties,
    }

    raw: Dict[str, Any] = {}
    for name, read in readers.items():
        try:
            value = read()
        except _PDF_ERRORS:
            continue
        if _present(value):
            raw[name] = value
    return raw


# --- normalizing each store onto the same field names ----------------------


def _from_info(reader: PdfReader, info_raw: Dict[str, str]) -> Dict[str, Any]:
    """Map Info dictionary keys onto the normalized field names.

    Mind the Creator trap: ``/Author`` is the person and ``/Creator`` is the
    authoring application, so ``/Creator`` maps to ``creator_tool`` and *not* to
    ``author``. Lining these up by name instead of by meaning is the usual way
    this mapping goes wrong.
    """
    return {
        "title": _text(info_raw.get("/Title")),
        "author": _text(info_raw.get("/Author")),
        "subject": _text(info_raw.get("/Subject")),
        "keywords": _keywords(info_raw.get("/Keywords")),
        "creator_tool": _text(info_raw.get("/Creator")),
        "producer": _text(info_raw.get("/Producer")),
        "created": _info_date(reader, "creation_date"),
        "modified": _info_date(reader, "modification_date"),
    }


def _from_xmp(xmp_raw: Dict[str, Any]) -> Dict[str, Any]:
    """Map XMP properties onto the normalized field names.

    The counterpart of the Creator trap above: XMP's ``dc:creator`` is the
    author, while ``xmp:CreatorTool`` is the application.
    """
    return {
        "title": _lang_alt(xmp_raw.get("dc_title")),
        "author": _joined(xmp_raw.get("dc_creator")),
        "subject": _lang_alt(xmp_raw.get("dc_description")),
        "keywords": _keywords(xmp_raw.get("dc_subject") or xmp_raw.get("pdf_keywords")),
        "creator_tool": _text(xmp_raw.get("xmp_creator_tool")),
        "producer": _text(xmp_raw.get("pdf_producer")),
        "created": xmp_raw.get("xmp_create_date"),
        "modified": xmp_raw.get("xmp_modify_date"),
    }


# --- coercions -------------------------------------------------------------


def _equivalent(field: str, info_value: Any, xmp_value: Any) -> bool:
    """Do the two stores say the same thing about this field?

    Dates need care. The same instant can be written with different UTC offsets —
    pypdf hands back XMP dates already folded into UTC while Info dates keep the
    offset they were written with — so comparing the rendered strings would report
    a conflict where there is none.
    """
    if field in _DATE_FIELDS:
        info_dt, xmp_dt = _parse_iso(info_value), _parse_iso(xmp_value)
        if info_dt is not None and xmp_dt is not None:
            return info_dt == xmp_dt
    return info_value == xmp_value


def _parse_iso(value: Any) -> Optional[datetime]:
    """Read back a timestamp this module rendered, for comparison only."""
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _present(value: Any) -> bool:
    """True if a value carries information. Empty strings and lists do not."""
    if value is None:
        return False
    if isinstance(value, (str, list, dict, tuple)):
        return len(value) > 0
    return True


def _text(value: Any) -> Optional[str]:
    """Coerce a single value to a stripped string, or None if it says nothing."""
    if value is None:
        return None
    text = value if isinstance(value, str) else str(value)
    text = text.strip()
    return text or None


def _lang_alt(value: Any) -> Optional[str]:
    """Flatten an XMP language alternative to one string.

    ``{'x-default': 'Report'}`` is the common case. A specific language is used
    if there is no default, since some value beats none.
    """
    if isinstance(value, dict):
        if not value:
            return None
        if "x-default" in value:
            return _text(value["x-default"])
        return _text(next(iter(value.values())))
    if isinstance(value, list):
        return _joined(value)
    return _text(value)


def _joined(value: Any) -> Optional[str]:
    """Flatten an XMP ordered array to one string, e.g. two authors to one line."""
    if isinstance(value, list):
        parts = [text for text in (_text(item) for item in value) if text]
        return ", ".join(parts) or None
    return _text(value)


def _keywords(value: Any) -> List[str]:
    """Normalize keywords to a list, whichever store they came from.

    XMP keeps them as an array; the Info dictionary keeps one comma separated
    string. Both become a list so the answer has one shape.
    """
    if value is None:
        return []
    if isinstance(value, list):
        return [text for text in (_text(item) for item in value) if text]
    text = _text(value)
    if not text:
        return []
    return [part.strip() for part in text.split(",") if part.strip()]


def _iso(value: Any) -> Optional[str]:
    """Render a datetime as ISO 8601, treating naive values as UTC.

    pypdf converts XMP dates to UTC and drops the offset, while Info dates keep
    theirs, so assuming UTC here is what makes the two comparable.
    """
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def _info_date(reader: PdfReader, attribute: str) -> Optional[str]:
    """Read a parsed date off the Info dictionary.

    Malformed date strings are common enough to be routine; the unparsed value
    is still reported in the raw ``info`` store, so losing the parse is cheap.
    """
    try:
        return _iso(getattr(reader.metadata, attribute))
    except _PDF_ERRORS:
        return None
