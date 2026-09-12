"""
benscore: the shared artifact layer every domain package builds on.

Core is a scratch folder plus ids for the files in it. Verbs take a reference
(an artifact id or a path), do their work, and return a new artifact id. Nothing
touches the user's real filesystem until ``export_artifact`` is called.

Nothing here knows what a PDF is. That is the point: a second domain package -
spreadsheets, images, whatever comes next - shares this one workspace, so an
artifact produced by one domain's verb can be passed straight into another's.
Which is also why the names here carry no domain: the import path
(``benscore``), the workspace variable (``BENSTOOLS_WORKSPACE``) and the base
error (``BensToolsError``) are what every future domain has to live with.

It ships inside the ``benspdf-mcp`` distribution today. Moving it into its own
distribution later is a packaging change only, invisible to anything that
imports it.
"""

from .errors import ArtifactNotFound, BensToolsError, ExportConflict
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
    "BensToolsError",
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
