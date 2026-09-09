"""
Tests for the core artifact store.
"""

import os
import time
from pathlib import Path

import pytest

from benspdf import core
from benspdf.core import store
from benspdf.core.errors import ArtifactNotFound, ExportConflict


class TestWorkspace:
    """The workspace location and its creation."""

    def test_uses_env_var(self, isolated_workspace):
        assert store.workspace() == isolated_workspace

    def test_created_on_demand(self, isolated_workspace):
        assert not isolated_workspace.exists()
        store.workspace()
        assert isolated_workspace.is_dir()

    def test_env_var_read_on_every_call(self, tmp_path, monkeypatch):
        relocated = tmp_path / "elsewhere"
        monkeypatch.setenv(store.WORKSPACE_ENV_VAR, str(relocated))
        assert store.workspace() == relocated


class TestIsArtifact:
    """Telling artifact ids apart from file paths."""

    def test_accepts_artifact_ids(self):
        assert store.is_artifact("art_a1b2c3d4.pdf")
        assert store.is_artifact("art_00000000")
        assert store.is_artifact("  art_deadbeef.png  ")

    def test_rejects_paths_and_lookalikes(self):
        # A real file the user happens to have named art_notes.pdf must not be
        # mistaken for a workspace artifact.
        assert not store.is_artifact("art_notes.pdf")
        assert not store.is_artifact("art_a1b2c3d4z.pdf")
        assert not store.is_artifact("/tmp/art_a1b2c3d4.pdf")
        assert not store.is_artifact("report.pdf")
        assert not store.is_artifact("")


class TestResolve:
    """resolve() is why every verb can take a single ref parameter."""

    def test_resolves_a_path(self, tmp_path):
        target = tmp_path / "real.pdf"
        target.write_bytes(b"%PDF-1.4")
        assert store.resolve(str(target)) == target

    def test_resolves_an_artifact_id(self):
        artifact = store.save(b"%PDF-1.4", ".pdf")
        assert store.resolve(artifact).read_bytes() == b"%PDF-1.4"

    def test_expands_user_home(self):
        with pytest.raises(FileNotFoundError):
            store.resolve("~/definitely-not-here-9f3a.pdf")

    def test_missing_artifact_is_actionable(self):
        with pytest.raises(ArtifactNotFound) as excinfo:
            store.resolve("art_deadbeef.pdf")
        message = str(excinfo.value)
        assert "expire" in message
        assert "Re-run" in message

    def test_missing_path_raises_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            store.resolve("/nonexistent/nope.pdf")

    def test_directory_rejected(self, tmp_path):
        with pytest.raises(IsADirectoryError):
            store.resolve(str(tmp_path))


class TestSave:
    """Putting new content into the workspace."""

    def test_returns_usable_id(self, isolated_workspace):
        artifact = store.save(b"hello", ".txt")
        assert store.is_artifact(artifact)
        assert artifact.endswith(".txt")
        assert (isolated_workspace / artifact).read_bytes() == b"hello"

    def test_ids_are_unique(self):
        ids = {store.save(b"x", ".pdf") for _ in range(20)}
        assert len(ids) == 20

    def test_suffix_normalized(self):
        assert store.save(b"x", "PDF").endswith(".pdf")
        assert store.save(b"x", ".PNG").endswith(".png")

    def test_save_path_copies_a_file(self, tmp_path):
        source = tmp_path / "source.pdf"
        source.write_bytes(b"%PDF-1.4")

        artifact = store.save_path(str(source))

        assert artifact.endswith(".pdf")
        assert store.resolve(artifact).read_bytes() == b"%PDF-1.4"
        assert source.exists(), "the original must not be moved"


class TestExportArtifact:
    """The only function that writes outside the workspace."""

    def test_writes_to_destination(self, tmp_path):
        artifact = store.save(b"%PDF-1.4", ".pdf")
        dest = tmp_path / "out" / "result.pdf"

        written = store.export_artifact(artifact, dest)

        assert written == dest.resolve()
        assert dest.read_bytes() == b"%PDF-1.4"

    def test_refuses_to_overwrite_by_default(self, tmp_path):
        artifact = store.save(b"new", ".pdf")
        dest = tmp_path / "existing.pdf"
        dest.write_bytes(b"original")

        with pytest.raises(ExportConflict):
            store.export_artifact(artifact, dest)

        assert dest.read_bytes() == b"original"

    def test_overwrites_when_asked(self, tmp_path):
        artifact = store.save(b"new", ".pdf")
        dest = tmp_path / "existing.pdf"
        dest.write_bytes(b"original")

        store.export_artifact(artifact, dest, overwrite=True)

        assert dest.read_bytes() == b"new"

    def test_source_survives_export(self, tmp_path):
        artifact = store.save(b"%PDF-1.4", ".pdf")
        store.export_artifact(artifact, tmp_path / "copy.pdf")
        assert store.resolve(artifact).exists()


class TestListRecent:
    """Recovery when an artifact id is lost mid-conversation."""

    def test_newest_first(self):
        first = store.save(b"1", ".pdf")
        time.sleep(0.01)
        second = store.save(b"2", ".pdf")

        listed = [entry["artifact"] for entry in store.list_recent()]

        assert listed.index(second) < listed.index(first)

    def test_respects_limit(self):
        for _ in range(5):
            store.save(b"x", ".pdf")
        assert len(store.list_recent(limit=3)) == 3

    def test_reports_size_and_age(self):
        store.save(b"12345", ".pdf")
        entry = store.list_recent()[0]
        assert entry["size_bytes"] == 5
        assert entry["age_seconds"] >= 0

    def test_ignores_foreign_files(self, isolated_workspace):
        store.workspace()
        (isolated_workspace / "notes.txt").write_text("not an artifact")
        assert store.list_recent() == []


class TestDiscard:
    """Manual cleanup, and the guarantee it cannot touch user files."""

    def test_removes_artifacts(self):
        artifact = store.save(b"x", ".pdf")
        result = store.discard(artifact)

        assert result["removed"] == [artifact]
        with pytest.raises(ArtifactNotFound):
            store.resolve(artifact)

    def test_accepts_a_list(self):
        artifacts = [store.save(b"x", ".pdf") for _ in range(3)]
        result = store.discard(artifacts)
        assert sorted(result["removed"]) == sorted(artifacts)

    def test_never_deletes_a_real_path(self, tmp_path):
        victim = tmp_path / "precious.pdf"
        victim.write_bytes(b"%PDF-1.4")

        result = store.discard(str(victim))

        assert result["removed"] == []
        assert result["skipped"] == [str(victim)]
        assert victim.exists(), "discard must refuse anything that is not an artifact id"

    def test_unknown_id_is_skipped_not_an_error(self):
        result = store.discard("art_deadbeef.pdf")
        assert result["removed"] == []
        assert result["skipped"] == ["art_deadbeef.pdf"]


class TestPrune:
    """Startup housekeeping."""

    def _age(self, artifact: str, days: float) -> None:
        path = store.resolve(artifact)
        past = time.time() - days * 86400
        os.utime(path, (past, past))

    def test_removes_expired_artifacts(self):
        old = store.save(b"old", ".pdf")
        fresh = store.save(b"fresh", ".pdf")
        self._age(old, days=10)

        result = store.prune(max_age_days=7, max_bytes=None)

        assert result["removed"] == [old]
        assert result["freed_bytes"] == 3
        assert store.resolve(fresh).exists()

    def test_age_disabled(self):
        old = store.save(b"old", ".pdf")
        self._age(old, days=100)

        result = store.prune(max_age_days=None, max_bytes=None)

        assert result["removed_count"] == 0

    def test_enforces_size_cap_oldest_first(self):
        oldest = store.save(b"a" * 100, ".pdf")
        self._age(oldest, days=3)
        middle = store.save(b"b" * 100, ".pdf")
        self._age(middle, days=2)
        newest = store.save(b"c" * 100, ".pdf")
        self._age(newest, days=1)

        result = store.prune(max_age_days=None, max_bytes=150)

        assert oldest in result["removed"]
        assert middle in result["removed"]
        assert store.resolve(newest).exists()

    def test_leaves_foreign_files_alone(self, isolated_workspace):
        store.workspace()
        bystander = isolated_workspace / "important.txt"
        bystander.write_text("not mine to delete")

        store.prune(max_age_days=0, max_bytes=0)

        assert bystander.exists()

    def test_empty_workspace_is_fine(self):
        assert store.prune()["removed_count"] == 0


class TestPublicSurface:
    """The names domain packages are expected to import."""

    def test_reexported_from_core(self):
        for name in ("resolve", "save", "save_path", "export_artifact", "ok", "err"):
            assert hasattr(core, name), name

    def test_round_trip_through_core(self, tmp_path):
        artifact = core.save(b"%PDF-1.4", ".pdf")
        written = core.export_artifact(artifact, tmp_path / "out.pdf")
        assert Path(written).read_bytes() == b"%PDF-1.4"
