"""
Exception types for the BensPDF core layer.

These exist so the tool layer can tell different failures apart and give the
model an actionable message. An expired artifact needs "re-run from source",
a missing file needs "check the path" - they are not the same problem.
"""


class BensPDFError(Exception):
    """Base class for all BensPDF errors."""


class ArtifactNotFound(BensPDFError):
    """A workspace artifact id was referenced but no longer exists."""


class ExportConflict(BensPDFError):
    """Export would overwrite an existing file and overwrite was not allowed."""
