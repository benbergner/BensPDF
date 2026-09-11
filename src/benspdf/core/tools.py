"""
The core MCP tools: getting results out of the workspace, and housekeeping.

Domain packages contribute verbs. This module contributes the three tools that
are not about any particular file type: exporting results the user wants to
keep, listing what is in the workspace, and throwing things away.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from . import store
from .errors import ArtifactNotFound, ExportConflict
from .results import err, ok


def _as_list(refs: Union[str, List[str]]) -> List[str]:
    """Accept a single ref or a list. Models pass bare strings often enough."""
    if isinstance(refs, (str, Path)):
        return [str(refs)]
    return [str(ref) for ref in refs]


def _format_name(template: str, index: int, artifact_id: str) -> str:
    """Expand a filename template.

    Supported placeholders: ``{n}`` (1-based index, format specs like
    ``{n:03d}`` work), ``{ext}``, ``{id}``, ``{stem}``.
    """
    return template.format(
        n=index,
        ext=Path(artifact_id).suffix,
        id=artifact_id,
        stem=Path(artifact_id).stem,
    )


def register(mcp: Any) -> None:
    """Register the core tools on an MCP server."""

    @mcp.tool()
    def export(
        refs: Union[str, List[str]],
        dest: str,
        name: Optional[str] = None,
        overwrite: bool = False,
    ) -> Dict[str, Any]:
        """Save one or more results to a real location on disk.

        The only tool that writes to the user's filesystem, so call it once at the
        end, when they have said where the output should go. Never hand an artifact
        id back as if it were a finished file; artifacts expire.

        Args:
            refs: Artifact id, or list of artifact ids, to export.
            dest: Where to write. For a single artifact this may be a full file
                path. For several artifacts, or when `name` is given, this is a
                directory and is created if missing.
            name: Filename template used when writing into a directory.
                Placeholders: {n} 1-based index (e.g. "page_{n:03d}{ext}"),
                {ext} extension, {id} artifact id, {stem} id without extension.
                Defaults to "{n:03d}{ext}".
            overwrite: Replace existing files instead of failing. Defaults to
                False so nothing is destroyed by accident.
        """
        try:
            refs = _as_list(refs)
            if not refs:
                return err("No artifacts given to export.")

            target = Path(dest).expanduser()
            single_file_target = (
                len(refs) == 1
                and name is None
                and target.suffix != ""
                and not target.is_dir()
            )

            written: List[str] = []

            if single_file_target:
                written.append(
                    str(store.export_artifact(refs[0], target, overwrite=overwrite))
                )
            else:
                template = name or "{n:03d}{ext}"
                for index, ref in enumerate(refs, start=1):
                    artifact_id = Path(str(ref)).name
                    filename = _format_name(template, index, artifact_id)
                    written.append(
                        str(
                            store.export_artifact(
                                ref, target / filename, overwrite=overwrite
                            )
                        )
                    )

            return ok(exported=written, count=len(written))

        except ExportConflict as exc:
            return err(str(exc))
        except ArtifactNotFound as exc:
            return err(str(exc))
        except FileNotFoundError as exc:
            return err(str(exc))
        except (KeyError, IndexError, ValueError) as exc:
            return err(
                f"Invalid name template {name!r}: {exc}. Supported placeholders "
                f"are {{n}}, {{ext}}, {{id}}, {{stem}}."
            )
        except OSError as exc:
            return err(f"Could not write to {dest}: {exc}")

    @mcp.tool()
    def list_artifacts(limit: int = 20) -> Dict[str, Any]:
        """List recent temporary artifacts in the workspace, newest first.

        Useful when you have lost track of an artifact id from earlier in the
        conversation, so you can find it again instead of redoing the work.

        Args:
            limit: Maximum number of artifacts to return.
        """
        try:
            return ok(
                artifacts=store.list_recent(limit=limit),
                workspace=str(store.workspace()),
            )
        except OSError as exc:
            return err(f"Could not read the workspace: {exc}")

    @mcp.tool()
    def discard(refs: Union[str, List[str]]) -> Dict[str, Any]:
        """Delete temporary artifacts from the workspace now.

        Only accepts artifact ids, never file paths, so this cannot delete the
        user's own files. Artifacts expire on their own, so this is only needed
        when the user explicitly asks to clean up.

        Args:
            refs: Artifact id, or list of artifact ids, to delete.
        """
        try:
            result = store.discard(_as_list(refs))
            return ok(**result, count=len(result["removed"]))
        except OSError as exc:
            return err(f"Could not delete artifacts: {exc}")
