"""Tests for rasterizing pages to images.

The interesting parts are the limits, because rasterizing is the one operation
here that cannot be made cheap by sampling: a per-call page cap, and a resolution
that gets reduced for pages whose render would be enormous. Both come from real
files — one page in a 141 file corpus is 34 x 49 inches, which is 37.8 megapixels
and 113 MB of bitmap at the default resolution.

Also checked: pdfium and `pdf_page_layout` agree on what "the page" is, since one
renders it and the other measures it.
"""

import io
import json
import random
from pathlib import Path

from PIL import Image as PILImage
from pypdf import PdfWriter
from pypdf.generic import ArrayObject, FloatObject, NameObject, NumberObject

from benspdf import read_page_layout, render_pages
from benspdf.core import store
from benspdf.tools import render_pages as render_module

LETTER = (612.0, 792.0)
A4 = (595.28, 841.89)


def build(path, *specs, encrypt=None) -> str:
    """Build a PDF. Each spec is a dict with `size` and optional `rotate`/`crop`."""
    writer = PdfWriter()
    for spec in specs or ({},):
        width, height = spec.get("size", LETTER)
        page = writer.add_blank_page(width=width, height=height)
        if "rotate" in spec:
            page[NameObject("/Rotate")] = NumberObject(spec["rotate"])
        if "crop" in spec:
            page[NameObject("/CropBox")] = ArrayObject(
                [FloatObject(value) for value in spec["crop"]]
            )
    if encrypt:
        writer.encrypt(user_password=encrypt, owner_password="owner")

    buffer = io.BytesIO()
    writer.write(buffer)
    path.write_bytes(buffer.getvalue())
    return str(path)


def opened(artifact: str) -> PILImage.Image:
    """Open a rendered artifact as an image."""
    return PILImage.open(store.resolve(artifact))


class TestRendering:
    """A page in, a PNG artifact out."""

    def test_renders_every_page_by_default(self, tmp_path):
        pdf = build(tmp_path / "three.pdf", {}, {}, {})

        data = render_pages(pdf)

        assert data["success"] is True
        assert data["rendered"] == 3
        assert [entry["page"] for entry in data["pages"]] == [1, 2, 3]
        assert data["truncated"] is False
        assert data["format"] == "png"

    def test_each_page_becomes_a_readable_png_artifact(self, tmp_path):
        pdf = build(tmp_path / "one.pdf")

        entry = render_pages(pdf)["pages"][0]

        assert store.is_artifact(entry["artifact"])
        assert entry["artifact"].endswith(".png")
        image = opened(entry["artifact"])
        assert image.format == "PNG"
        assert image.size == (entry["width_px"], entry["height_px"])

    def test_each_page_reports_a_path_that_can_be_opened(self, tmp_path):
        """The id is for other tools; the path is for a person looking at it.

        Without this, seeing a render means knowing the workspace layout, or
        exporting first just to have something to double click.
        """
        pdf = build(tmp_path / "openable.pdf")

        entry = render_pages(pdf)["pages"][0]

        assert entry["path"] == str(store.artifact_path(entry["artifact"]))
        assert PILImage.open(entry["path"]).format == "PNG"

    def test_artifacts_are_listed_flat_for_export(self, tmp_path):
        pdf = build(tmp_path / "two.pdf", {}, {})

        data = render_pages(pdf)

        assert data["artifacts"] == [entry["artifact"] for entry in data["pages"]]
        assert len(data["artifacts"]) == 2

    def test_nothing_is_written_to_the_users_disk(self, tmp_path):
        """Rendering produces artifacts; only export writes where the user lives.

        The workspace directory here is the test fixture's, not the tool's doing.
        """
        pdf = build(tmp_path / "quiet.pdf")

        render_pages(pdf)

        beside_the_pdf = sorted(p.name for p in tmp_path.iterdir() if p.is_file())
        assert beside_the_pdf == ["quiet.pdf"], "no images next to the source"

    def test_a_page_range_can_be_asked_for(self, tmp_path):
        pdf = build(tmp_path / "five.pdf", *[{}] * 5)

        assert [e["page"] for e in render_pages(pdf, "2-3")["pages"]] == [2, 3]
        assert [e["page"] for e in render_pages(pdf, "1,4")["pages"]] == [1, 4]
        assert [e["page"] for e in render_pages(pdf, "all")["pages"]] == [1, 2, 3, 4, 5]


class TestResolution:
    """dpi controls the size, within limits that protect memory."""

    def test_dpi_sets_the_pixel_size(self, tmp_path):
        pdf = build(tmp_path / "letter.pdf")

        at_72 = render_pages(pdf, dpi=72)["pages"][0]
        at_144 = render_pages(pdf, dpi=144)["pages"][0]

        assert (at_72["width_px"], at_72["height_px"]) == (612, 792)
        assert at_144["width_px"] == 2 * at_72["width_px"]

    def test_dpi_is_clamped_to_a_sane_range(self, tmp_path):
        pdf = build(tmp_path / "clamp.pdf")

        assert render_pages(pdf, dpi=1)["dpi"] == 36
        assert render_pages(pdf, dpi=5000)["dpi"] == 600

    def test_a_huge_page_has_its_resolution_reduced(self, tmp_path):
        """34 x 49 inches at 150 dpi would be 37.8 Mpx and 113 MB of bitmap."""
        pdf = build(tmp_path / "plan.pdf", {"size": (2480, 3508)})

        entry = render_pages(pdf, dpi=150)["pages"][0]

        assert entry["dpi"] < 150
        assert entry["dpi_reduced_from"] == 150
        assert max(entry["width_px"], entry["height_px"]) <= 4000

    def test_the_reduction_is_reported_not_silent(self, tmp_path):
        pdf = build(tmp_path / "plan.pdf", {"size": (2480, 3508)})

        summary = render_pages(pdf, dpi=150)["summary"]

        assert "too large" in summary
        assert "4000 px" in summary

    def test_an_ordinary_page_keeps_the_dpi_it_asked_for(self, tmp_path):
        pdf = build(tmp_path / "normal.pdf")

        entry = render_pages(pdf, dpi=150)["pages"][0]

        assert entry["dpi"] == 150
        assert "dpi_reduced_from" not in entry


class TestPageCap:
    """Rasterizing cannot be sampled, so the page count is capped per call."""

    def test_a_long_document_stops_at_the_cap(self, tmp_path):
        pdf = build(tmp_path / "long.pdf", *[{}] * 25)

        data = render_pages(pdf, "all", dpi=36)

        assert data["rendered"] == 20
        assert data["truncated"] is True
        assert [entry["page"] for entry in data["pages"]] == list(range(1, 21))

    def test_the_cap_explains_how_to_continue(self, tmp_path):
        pdf = build(tmp_path / "long.pdf", *[{}] * 25)

        summary = render_pages(pdf, "all", dpi=36)["summary"]

        assert "20 is the limit per call" in summary
        assert "later range" in summary

    def test_a_later_range_picks_up_where_it_stopped(self, tmp_path):
        pdf = build(tmp_path / "long.pdf", *[{}] * 25)

        data = render_pages(pdf, "21-25", dpi=36)

        assert [entry["page"] for entry in data["pages"]] == [21, 22, 23, 24, 25]
        assert data["truncated"] is False


class TestGeometryAgreement:
    """pdfium draws the page pdf_page_layout measures. They must not disagree."""

    def test_a_rotated_page_renders_as_it_presents(self, tmp_path):
        """A landscape box with /Rotate 90 is a portrait page, and renders that way."""
        pdf = build(tmp_path / "turned.pdf", {"size": (841.89, 595.28), "rotate": 90})

        measured = read_page_layout(pdf)["sizes"][0]
        entry = render_pages(pdf, dpi=72)["pages"][0]

        assert measured["orientation"] == "portrait"
        assert entry["height_px"] > entry["width_px"], "rendered portrait too"
        assert abs(entry["width_px"] - measured["width_pt"]) <= 1
        assert abs(entry["height_px"] - measured["height_pt"]) <= 1

    def test_a_cropped_page_renders_its_cropbox(self, tmp_path):
        pdf = build(tmp_path / "cropped.pdf", {"crop": (0, 0, 306, 396)})

        measured = read_page_layout(pdf)["sizes"][0]
        entry = render_pages(pdf, dpi=72)["pages"][0]

        assert (measured["width_pt"], measured["height_pt"]) == (306.0, 396.0)
        assert abs(entry["width_px"] - 306) <= 1
        assert abs(entry["height_px"] - 396) <= 1


class TestFailures:
    """Failures return a reason instead of raising."""

    def test_missing_file(self, tmp_path):
        data = render_pages(str(tmp_path / "nope.pdf"))

        assert data["success"] is False
        assert data["file_exists"] is False

    def test_not_a_pdf(self, tmp_path):
        not_pdf = tmp_path / "notes.txt"
        not_pdf.write_text("This is not a PDF")

        data = render_pages(str(not_pdf))

        assert data["success"] is False
        assert "error" in data

    def test_encrypted_file_points_at_check_access(self, tmp_path):
        """pdfium reports the password problem in the message, not the type."""
        pdf = build(tmp_path / "locked.pdf", encrypt="letmein")

        data = render_pages(pdf)

        assert data["success"] is False
        assert data["encrypted"] is True
        assert "pdf_check_access" in data["error"]

    def test_a_bad_page_range_explains_the_accepted_forms(self, tmp_path):
        pdf = build(tmp_path / "ranges.pdf")

        data = render_pages(pdf, "page seven")

        assert data["success"] is False
        assert "1,5,9-12" in data["error"]

    def test_one_unrenderable_page_does_not_fail_the_rest(self, tmp_path, monkeypatch):
        pdf = build(tmp_path / "flaky.pdf", {}, {}, {})

        original = render_module._render_one
        seen = []

        def flaky(document, number, dpi):
            seen.append(number)
            if number == 2:
                raise RuntimeError("pdfium said no")
            return original(document, number, dpi)

        monkeypatch.setattr(render_module, "_render_one", flaky)

        data = render_pages(pdf)

        assert data["success"] is True
        assert data["rendered"] == 2
        assert data["failed"] == [{"page": 2, "error": "RuntimeError: pdfium said no"}]
        assert "Page 2 could not be rendered" in data["summary"]

    def test_all_pages_failing_is_a_failure(self, tmp_path, monkeypatch):
        pdf = build(tmp_path / "broken.pdf")

        def always_fails(document, number, dpi):
            raise RuntimeError("nope")

        monkeypatch.setattr(render_module, "_render_one", always_fails)

        data = render_pages(pdf)

        assert data["success"] is False
        assert data["failed"][0]["page"] == 1

    def test_corrupted_files_never_raise(self, tmp_path):
        source = Path(build(tmp_path / "source.pdf", {}, {})).read_bytes()
        rng = random.Random(0)
        target = tmp_path / "mangled.pdf"

        for trial in range(40):
            data = bytearray(source)
            mode = trial % 4
            if mode == 0:
                data = data[: rng.randint(1, len(data))]
            elif mode == 1:
                for _ in range(rng.randint(1, 40)):
                    data[rng.randrange(len(data))] = rng.randrange(256)
            elif mode == 2:
                start = rng.randrange(len(data))
                del data[start : start + rng.randint(1, 800)]
            else:
                data = bytearray(rng.randbytes(rng.randint(1, 500)))

            target.write_bytes(bytes(data))
            result = render_pages(str(target), dpi=36)

            assert result["success"] in (True, False)
            if not result["success"]:
                assert result["error"]


class TestJsonSafety:
    """Results cross an MCP boundary, so every value has to serialize."""

    def test_result_is_json_serializable(self, tmp_path):
        pdf = build(tmp_path / "json.pdf", {}, {"size": A4, "rotate": 90})

        json.dumps(render_pages(pdf, "all", dpi=72))
