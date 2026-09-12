"""
BensPDF core: the shared artifact layer every domain package builds on.

Core is a scratch folder plus ids for the files in it. Verbs take a reference
(an artifact id or a path), do their work, and return a new artifact id. Nothing
touches the user's real filesystem until ``export_artifact`` is called.

For now core lives inside the ``benspdf`` package. It is deliberately
domain-neutral so it can move into its own distribution later without changing
how it is used.
"""

from .errors import ArtifactNotFound, BensPDFError, ExportConflict
from .results import err, ok
from .store import (
    ARTIFACT_PREFIX,
    artifact_path,
    DEFAULT_MAX_AGE_DAYS,
    DEFAULT_MAX_BYTES,
    WORKSPACE_ENV_VAR,
    discard,
    export_artifact,
    is_artifact,
    list_recent,
    prune,
    resolve,
    save,
    save_path,
    workspace,
)

__all__ = [
    # Errors
    "BensPDFError",
    "ArtifactNotFound",
    "ExportConflict",
    # Result shape
    "ok",
    "err",
    # Store
    "workspace",
    "artifact_path",
    "resolve",
    "save",
    "save_path",
    "export_artifact",
    "is_artifact",
    "list_recent",
    "discard",
    "prune",
    # Constants
    "ARTIFACT_PREFIX",
    "DEFAULT_MAX_AGE_DAYS",
    "DEFAULT_MAX_BYTES",
    "WORKSPACE_ENV_VAR",
]
