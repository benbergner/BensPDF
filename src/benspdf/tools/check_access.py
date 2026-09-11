"""
Whether a PDF can be opened and what it claims to forbid: `pdf_check_access`.

These look like two questions and are one, because a PDF has nowhere to keep
restrictions except inside its encryption dictionary. The permission bits are
``/P`` in ``/Encrypt``; there is no way to mark an unencrypted file "no printing".
So "is it encrypted" and "what may I do with it" are the same lookup, and
answering either alone misleads:

* Encryption alone sounds like a locked door. In a 141 file corpus, 8 files were
  encrypted and *not one* needed a password — they open silently and merely carry
  restrictions. Reporting "encrypted" without that is alarming and wrong.
* Permissions alone imply enforcement. Six of those 8 files declare that text may
  not be extracted, and this library's own text tools extract from them without
  resistance, because the bits are a request to viewers, not a lock. Reporting a
  restriction without saying who honours it is worse than saying nothing.

Hence one verb, and a `summary` that states the restriction and its advisory
nature in the same breath.

This is also the one read here that still works on an encrypted file. The
encryption dictionary is itself unencrypted, so a document that needs a password
— where every other tool can only report failure — still answers what it is
encrypted with and what it restricts.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from pypdf import PdfReader
from pypdf.constants import UserAccessPermissions
from pypdf.errors import FileNotDecryptedError

from ..core import err, ok

#: The permission bits worth reporting, in the order a person would ask about
#: them, mapped from pypdf's flags to words rather than spec vocabulary.
#:
#: ``accessibility`` is bit 10, which once controlled extraction by screen
#: readers and is deprecated in PDF 2.0 — it must now be set, and a file that
#: clears it is asking for something no current viewer honours. Reported anyway,
#: because plenty of files in the wild still clear it.
_PERMISSIONS: Tuple[Tuple[str, UserAccessPermissions], ...] = (
    ("print", UserAccessPermissions.PRINT),
    ("print_high_quality", UserAccessPermissions.PRINT_TO_REPRESENTATION),
    ("copy", UserAccessPermissions.EXTRACT),
    ("modify", UserAccessPermissions.MODIFY),
    ("annotate", UserAccessPermissions.ADD_OR_MODIFY),
    ("fill_forms", UserAccessPermissions.FILL_FORM_FIELDS),
    ("assemble", UserAccessPermissions.ASSEMBLE_DOC),
    ("accessibility", UserAccessPermissions.EXTRACT_TEXT_AND_GRAPHICS),
)

#: How each restriction reads in a sentence.
_PHRASES = {
    "print": "printing",
    "print_high_quality": "high quality printing",
    "copy": "copying text",
    "modify": "editing the content",
    "annotate": "annotating",
    "fill_forms": "filling in forms",
    "assemble": "reorganizing pages",
    "accessibility": "accessibility extraction",
}

#: Crypt filter methods, for files that name one instead of implying it from /V.
_FILTER_METHODS = {"/AESV2": "AES-128", "/AESV3": "AES-256", "/None": "none"}


def check_access(pdf_path: str) -> Dict[str, Any]:
    """Report a PDF's encryption and the permissions it declares.

    Args:
        pdf_path: Path to an existing PDF file. Artifact ids are resolved by the
            caller, not here.

    Returns:
        A result dict. Encrypted files are a normal answer here, not a failure:
        ``encrypted``, ``needs_password``, ``permissions`` (one boolean per
        action), ``restrictions`` (the denied ones), ``encryption`` (algorithm and
        version), and a ``summary`` sentence.
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
        encrypted = bool(reader.is_encrypted)
        needs_password = _needs_password(reader)
        encryption = _encryption(reader) if encrypted else None
        permissions = _permissions(reader, encrypted)
        valid = _permissions_valid(reader, (encryption or {}).get("revision"))
    except Exception as exc:  # noqa: BLE001 - return a reason, never raise
        return err(
            f"Could not read {path.name} as a PDF: {type(exc).__name__}: {exc}",
            file_path=str(path),
            file_name=path.name,
        )

    restrictions = [name for name, allowed in permissions.items() if not allowed]

    return ok(
        file_path=str(path),
        file_name=path.name,
        encrypted=encrypted,
        needs_password=needs_password,
        permissions=permissions,
        restrictions=restrictions,
        restricted=bool(restrictions),
        permissions_valid=valid,
        encryption=encryption,
        summary=_summary(
            encrypted=encrypted,
            needs_password=needs_password,
            restrictions=restrictions,
            encryption=encryption,
            valid=valid,
        ),
    )


# --- reading the encryption dictionary --------------------------------------


def _needs_password(reader: PdfReader) -> bool:
    """Can this document's pages be read at all?

    Probed by reading the page tree rather than inspected, because pypdf tries the
    empty password when it opens a file. An encrypted file with no user password
    — the common case, and every encrypted file in the corpus — opens silently, so
    only an attempt distinguishes "locked" from "merely encrypted".
    """
    try:
        len(reader.pages)
    except FileNotDecryptedError:
        return True
    except Exception:  # noqa: BLE001 - a damaged page tree is not a password
        return False
    return False


def _encryption(reader: PdfReader) -> Dict[str, Any]:
    """What the file is encrypted with, named the way a person would name it.

    Deliberately omits ``/O`` and ``/U``: those are password verification hashes,
    and a result that gets pasted into a chat log is no place for them.
    """
    details: Dict[str, Any] = {
        "algorithm": None,
        "version": None,
        "revision": None,
        "key_bits": None,
        "encrypts_metadata": True,
    }

    raw: Any
    try:
        raw = reader.trailer["/Encrypt"].get_object()
    except Exception:  # noqa: BLE001 - encrypted, but the dictionary is unreadable
        return details
    if not hasattr(raw, "get"):
        return details

    version = _int(raw.get("/V"))
    revision = _int(raw.get("/R"))
    length = _int(raw.get("/Length"))

    details.update(
        {
            "algorithm": _algorithm(raw, version, length),
            "version": version,
            "revision": revision,
            "key_bits": 256 if version == 5 else length,
            "encrypts_metadata": raw.get("/EncryptMetadata", True) is not False,
        }
    )
    return details


def _algorithm(raw: Any, version: Optional[int], length: Optional[int]) -> str:
    """Name the cipher, which /V only half determines.

    From ``/V`` 4 onwards the file names a crypt filter instead, so AES-128 and
    RC4 both present as ``/V 4`` and only the filter's method tells them apart.
    """
    if version == 5:
        return "AES-256"

    if version == 4:
        named = _crypt_filter_method(raw)
        if named:
            return named

    if version in (1, 2, 3, 4):
        return f"RC4 {length or 40}-bit"
    return f"unrecognized (/V {version})"


def _crypt_filter_method(raw: Any) -> Optional[str]:
    """The method of the default crypt filter, if the file names one."""
    try:
        filters = raw["/CF"].get_object()
        name = raw.get("/StmF", "/StdCF")
        method = filters[str(name)].get_object().get("/CFM")
    except Exception:  # noqa: BLE001 - fall back to guessing from /V
        return None

    if method in _FILTER_METHODS:
        return _FILTER_METHODS[str(method)]
    if method == "/V2":
        return None  # RC4, so let the key length name it
    return None


def _permissions(reader: PdfReader, encrypted: bool) -> Dict[str, bool]:
    """One boolean per action, true meaning allowed.

    An unencrypted file permits everything, and says so explicitly rather than
    with nulls: "can I print this" deserves True, not "not applicable".
    """
    if not encrypted:
        return {name: True for name, _flag in _PERMISSIONS}

    try:
        flags = reader.user_access_permissions
    except Exception:  # noqa: BLE001
        flags = None

    if flags is None:
        return {name: True for name, _flag in _PERMISSIONS}

    return {name: bool(flags & flag) for name, flag in _PERMISSIONS}


def _permissions_valid(reader: PdfReader, revision: Optional[int]) -> Optional[bool]:
    """Whether the permission bits passed a real integrity check.

    Only AES-256 (revision 5 or 6) carries ``/Perms``, an encrypted copy of ``/P``
    that can be verified. pypdf reports True for weaker encryption too, meaning
    "nothing to check" — reported here as None instead, because a True that stands
    for an absent check is worse than an admitted unknown. False means the bits
    disagree with their signed copy and may have been altered.
    """
    if revision not in (5, 6):
        return None
    try:
        return reader.are_permissions_valid
    except Exception:  # noqa: BLE001
        return None


def _int(value: Any) -> Optional[int]:
    """A dictionary value as a plain int, or None if it is not one.

    ``/P`` is a signed 32-bit integer and is normally negative, since the reserved
    high bits are all set. Nothing here needs it, but the same coercion serves
    ``/V``, ``/R`` and ``/Length``.
    """
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


# --- the sentence ----------------------------------------------------------


def _summary(
    *,
    encrypted: bool,
    needs_password: bool,
    restrictions: List[str],
    encryption: Optional[Dict[str, Any]],
    valid: Optional[bool],
) -> str:
    """One sentence, which has to carry the caveat as well as the fact."""
    if not encrypted:
        return "Not encrypted, and nothing about it is restricted."

    algorithm = (encryption or {}).get("algorithm") or "an unrecognized cipher"
    parts = []

    if needs_password:
        parts.append(
            f"Encrypted with {algorithm} and needs a password to open, so its "
            f"pages cannot be read."
        )
    else:
        parts.append(f"Encrypted with {algorithm}, but it opens without a password.")

    if restrictions:
        listed = _listed([_PHRASES[name] for name in restrictions])
        parts.append(f"It asks viewers to disallow {listed}.")
        if not needs_password:
            parts.append(
                "Those bits are a request to viewers, not a lock: the file is "
                "already open, so nothing enforces them."
            )
    else:
        parts.append("It restricts nothing.")

    if valid is False:
        parts.append(
            "The permission bits failed their integrity check, so they may have "
            "been altered since the file was signed."
        )

    if encryption and encryption.get("encrypts_metadata") is False:
        parts.append("Its metadata is left unencrypted.")

    return " ".join(parts)


def _listed(phrases: List[str]) -> str:
    """Join phrases the way a sentence would: "a, b and c"."""
    if len(phrases) == 1:
        return phrases[0]
    return ", ".join(phrases[:-1]) + f" and {phrases[-1]}"
