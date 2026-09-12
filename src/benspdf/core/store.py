"""
The artifact store: a scratch folder plus ids for the files in it.

Why this exists
---------------
Tools need somewhere to put output that is not the user's disk. Without it,
every operation has to invent an output path, chaining two operations litters
the user's folders with intermediates, and there is no way to preview a result
before committing to it.

With it, a verb takes a reference and returns an artifact id. Ids can be passed
straight into the next verb. Nothing reaches the user's real filesystem until
``export_artifact`` is called, which is the only function here that writes
outside the workspace.

Artifacts are ordinary files. ``art_a1b2c3d4.pdf`` lives at
``<workspace>/art_a1b2c3d4.pdf`` and can be opened by any library. The only
differences from a normal output file are where it lives and that it expires.
"""

import os
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Union

from .errors import ArtifactNotFound, ExportConflict

#: Prefix that marks a reference as a workspace artifact rather than a file path.
ARTIFACT_PREFIX = "art_"

#: Artifacts older than this are removed by :func:`prune`.
DEFAULT_MAX_AGE_DAYS = 7

#: Total workspace size cap enforced by :func:`prune`, oldest evicted first.
DEFAULT_MAX_BYTES = 2_000_000_000

#: Environment variable that relocates the workspace.
WORKSPACE_ENV_VAR = "BENSPDF_WORKSPACE"

# Deliberately strict: an 8 character hex body with an optional extension. A
# loose check would let a real file named "art_notes.pdf" in the current
# directory be mistaken for an artifact.
_ARTIFACT_RE = re.compile(r"^art_[0-9a-f]{8}(\.[A-Za-z0-9]+)?$")

Ref = Union[str, Path]


def workspace() -> Path:
    """Return the workspace directory, creating it if needed.

    Read from the environment on every call rather than cached at import, so
    tests and users can relocate it without reimporting.
    """
    raw = os.environ.get(WORKSPACE_ENV_VAR)
    base = Path(raw).expanduser() if raw else Path.home() / ".benspdf" / "work"
    base.mkdir(parents=True, exist_ok=True)
    return base


def is_artifact(ref: Ref) -> bool:
    """True if ``ref`` looks like a workspace artifact id rather than a path."""
    return bool(_ARTIFACT_RE.match(str(ref).strip()))


def _normalize_suffix(suffix: Optional[str]) -> str:
    if not suffix:
        return ""
    suffix = str(suffix).strip().lower()
    return suffix if suffix.startswith(".") else f".{suffix}"


def _new_id(suffix: Optional[str] = ".pdf") -> str:
    return f"{ARTIFACT_PREFIX}{uuid.uuid4().hex[:8]}{_normalize_suffix(suffix)}"


def resolve(ref: Ref) -> Path:
    """Turn an artifact id *or* a filesystem path into a readable path.

    This is why every verb can take a single ``ref`` parameter: the first call
    in a chain gets a path from the user, and every later call gets an id.

    Args:
        ref: An artifact id like ``art_a1b2c3d4.pdf`` or any file path
            (``~`` is expanded, relative paths are resolved).

    Returns:
        Path to an existing file.

    Raises:
        ArtifactNotFound: The id is well formed but the artifact is gone,
            usually because it expired.
        FileNotFoundError: The path does not exist.
        IsADirectoryError: The path is a directory.
    """
    text = str(ref).strip()

    if is_artifact(text):
        path = workspace() / text
        if not path.exists():
            raise ArtifactNotFound(
                f"Artifact '{text}' no longer exists. Workspace artifacts expire "
                f"after {DEFAULT_MAX_AGE_DAYS} days. Re-run the operation from the "
                f"original source file."
            )
        return path

    path = Path(text).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"File not found: {ref}")
    if path.is_dir():
        raise IsADirectoryError(f"Expected a file but got a directory: {ref}")
    return path


def artifact_path(artifact_id: str) -> Path:
    """Where an artifact lives, without requiring that it still does.

    ``resolve`` is the right call for reading, since it insists the file exists.
    This one is for *reporting*: a verb that has just produced an artifact returns
    both the id, which other tools take, and the path, which a person can open.
    Without the path, looking at a result means knowing the workspace convention
    and assembling the path by hand.
    """
    return workspace() / str(artifact_id).strip()


def save(data: bytes, suffix: str = ".pdf") -> str:
    """Store new content in the workspace and return its artifact id.

    Args:
        data: File contents.
        suffix: File extension for the artifact, with or without the leading dot.

    Returns:
        An artifact id such as ``art_a1b2c3d4.pdf``.
    """
    artifact_id = _new_id(suffix)
    (workspace() / artifact_id).write_bytes(data)
    return artifact_id


def save_path(src: Ref, suffix: Optional[str] = None) -> str:
    """Copy an existing file into the workspace and return its artifact id.

    Needed because some libraries only write to a path and never hand back
    bytes - ocrmypdf today, ffmpeg later.

    Args:
        src: Path to an existing file, or an artifact id to duplicate.
        suffix: Extension override. Defaults to the source file's extension.

    Returns:
        An artifact id.
    """
    source = resolve(src)
    artifact_id = _new_id(suffix or source.suffix)
    shutil.copy2(source, workspace() / artifact_id)
    return artifact_id


def export_artifact(ref: Ref, dest: Ref, overwrite: bool = False) -> Path:
    """Copy an artifact out of the workspace to a real path.

    The only function in the library that writes outside the workspace. Keeping
    it as a single explicit step is what makes every mutation reviewable: until
    this is called, the user's files are untouched.

    Args:
        ref: Artifact id or path to copy.
        dest: Destination file path. Parent directories are created.
        overwrite: Allow replacing an existing file.

    Returns:
        The destination path.

    Raises:
        ExportConflict: ``dest`` exists and ``overwrite`` is False.
    """
    source = resolve(ref)
    target = Path(str(dest)).expanduser().resolve()

    if target.exists() and not overwrite:
        raise ExportConflict(
            f"{target} already exists. Pass overwrite=True to replace it, or "
            f"choose a different destination."
        )

    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return target


def list_recent(limit: int = 20) -> List[Dict[str, Any]]:
    """Return recent artifacts, newest first.

    Exists for recovery: when a conversation loses track of an id, the model can
    look it up instead of redoing the work.
    """
    now = time.time()
    entries = []
    for path in workspace().iterdir():
        if path.is_file() and is_artifact(path.name):
            stat = path.stat()
            entries.append(
                (
                    stat.st_mtime,
                    {
                        "artifact": path.name,
                        "path": str(path),
                        "size_bytes": stat.st_size,
                        "age_seconds": int(now - stat.st_mtime),
                    },
                )
            )

    # Sort on the full-precision mtime, not the truncated age, so artifacts
    # created within the same second still order correctly.
    entries.sort(key=lambda item: item[0], reverse=True)
    return [entry for _mtime, entry in entries[:limit]]


def discard(refs: Union[Ref, Sequence[Ref]]) -> Dict[str, Any]:
    """Delete artifacts from the workspace immediately.

    Refuses anything that is not an artifact id, so this can never delete one of
    the user's own files.
    """
    # str is itself a Sequence, so this check has to come first.
    items: Sequence[Ref] = [refs] if isinstance(refs, (str, Path)) else refs

    removed: List[str] = []
    skipped: List[str] = []

    for ref in items:
        text = str(ref).strip()
        if not is_artifact(text):
            skipped.append(text)
            continue
        path = workspace() / text
        if path.exists():
            path.unlink()
            removed.append(text)
        else:
            skipped.append(text)

    return {"removed": removed, "skipped": skipped}


def prune(
    max_age_days: Optional[int] = DEFAULT_MAX_AGE_DAYS,
    max_bytes: Optional[int] = DEFAULT_MAX_BYTES,
) -> Dict[str, Any]:
    """Remove expired and over-cap artifacts. Called once at server startup.

    Deliberately not a background thread: a function that runs at boot is enough
    and needs no lifecycle management. Only ever touches files in the workspace
    whose names match the artifact pattern.

    Args:
        max_age_days: Delete artifacts older than this. None disables.
        max_bytes: Cap total workspace size, evicting oldest first. None disables.

    Returns:
        Dict with ``removed`` (ids), ``removed_count`` and ``freed_bytes``.
    """
    surviving = []
    removed: List[str] = []
    freed = 0
    now = time.time()

    for path in workspace().iterdir():
        if not (path.is_file() and is_artifact(path.name)):
            continue
        stat = path.stat()
        expired = (
            max_age_days is not None and (now - stat.st_mtime) > max_age_days * 86400
        )
        if expired:
            freed += stat.st_size
            removed.append(path.name)
            path.unlink(missing_ok=True)
        else:
            surviving.append((path, stat))

    if max_bytes is not None:
        total = sum(stat.st_size for _, stat in surviving)
        surviving.sort(key=lambda item: item[1].st_mtime)  # oldest first
        for path, stat in surviving:
            if total <= max_bytes:
                break
            total -= stat.st_size
            freed += stat.st_size
            removed.append(path.name)
            path.unlink(missing_ok=True)

    return {
        "removed": removed,
        "removed_count": len(removed),
        "freed_bytes": freed,
    }
