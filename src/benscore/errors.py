"""
Exception types for the shared core layer.

These exist so the tool layer can tell different failures apart and give the
model an actionable message. An expired artifact needs "re-run from source",
a missing file needs "check the path" - they are not the same problem.
"""


class BensToolsError(Exception):
    """Base class for all errors raised by the core layer and the tools on it."""


class ArtifactNotFound(BensToolsError):
    """A workspace artifact id was referenced but no longer exists."""


class ExportConflict(BensToolsError):
    """Export would overwrite an existing file and overwrite was not allowed."""
