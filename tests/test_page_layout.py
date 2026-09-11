"""Tests for measuring page geometry.

The cases that matter came out of a 141 file corpus: A4 pages that disagree by a
millimetre and must still group as one size, landscape boxes that present as
portrait because of `/Rotate`, and CropBoxes that decide what a reader actually
sees. Plus one pypdf trap — reading a box accessor creates that box on the page,
so "does this file declare a TrimBox" has to be answered before measuring.
"""

import io
import json
import random
from pathlib import Path

from pypdf import PdfWriter
from pypdf._page import PageObject
from pypdf.errors import LimitReachedError
from pypdf.generic import ArrayObject, FloatObject, NameObject, NumberObject

from benspdf import read_page_layout
from benspdf.tools import page_layout as page_layout_module

A4 = (595.28, 841.89)
LETTER = (612.0, 792.0)


def build(path, *specs) -> str:
    """Build a PDF from page specs.

    Each spec is a dict: ``size`` (width, height) in points, plus optional
    ``rotate``, ``crop``, ``trim``, ``bleed``, ``art`` (left, bottom, right, top)
    and ``user_unit``.
    """
    writer = PdfWriter()
    for spec in specs:
        width, height = spec.get("size", LETTER)
        page = writer.add_blank_page(width=width, height=height)

        if "rotate" in spec:
            page[NameObject("/Rotate")] = NumberObject(spec["rotate"])
        if "user_unit" in spec:
            page[NameObject("/UserUnit")] = NumberObject(spec["user_unit"])
        for name, key in (
            ("crop", "/CropBox"),
            ("trim", "/TrimBox"),
            ("bleed", "/BleedBox"),
            ("art", "/ArtBox"),
        ):
            if name in spec:
                page[NameObject(key)] = ArrayObject(
                    [FloatObject(value) for value in spec[name]]
                )

    buffer = io.BytesIO()
    writer.write(buffer)
    path.write_bytes(buffer.getvalue())
    return str(path)


def page(size=LETTER, **extra):
    return {"size": size, **extra}


class TestUniformDocument:
    """The common case: every page the same, answered in one group."""

    def test_groups_all_pages_into_one_size(self, tmp_path):
        pdf = build(tmp_path / "letter.pdf", *[page(LETTER)] * 4)

        data = read_page_layout(pdf)

        assert data["success"] is True
        assert data["uniform"] is True
        assert data["page_count"] == 4
        assert len(data["sizes"]) == 1

        size = data["sizes"][0]
        assert size["paper"] == "Letter"
        assert size["orientation"] == "portrait"
        assert size["page_count"] == 4
        assert size["pages"] == "1-4"
        assert (size["width_pt"], size["height_pt"]) == (612.0, 792.0)
        assert (size["width_in"], size["height_in"]) == (8.5, 11.0)

    def test_no_per_page_rows_unless_asked(self, tmp_path):
        """A 300 page document should not return 300 rows to say "all A4"."""
        pdf = build(tmp_path / "quiet.pdf", *[page(A4)] * 12)

        data = read_page_layout(pdf)

        assert "pages" not in data
        assert data["sizes"][0]["pages"] == "1-12", "the group carries the range"

    def test_summary_names_the_size(self, tmp_path):
        pdf = build(tmp_path / "a4.pdf", *[page(A4)] * 3)

        summary = read_page_layout(pdf)["summary"]

        assert "All 3 pages" in summary
        assert "A4 portrait" in summary
        assert "210 x 297 mm" in summary

    def test_single_page_document_reads_naturally(self, tmp_path):
        pdf = build(tmp_path / "one.pdf", page(A4))

        summary = read_page_layout(pdf)["summary"]

        assert summary.startswith("The only page is A4 portrait")


class TestTolerantGrouping:
    """Real A4 is not exactly A4, and must not fragment the answer.

    Exact grouping split 13 of 141 corpus documents into two or three groups that
    a person would call one uniform A4 document.
    """

    def test_sizes_within_a_few_points_group_together(self, tmp_path):
        pdf = build(
            tmp_path / "nearly.pdf",
            page((595.2, 841.6)),
            page((595.5, 842.2)),
            page((597.6, 840.0)),
        )

        data = read_page_layout(pdf)

        assert data["uniform"] is True
        assert len(data["sizes"]) == 1
        assert data["sizes"][0]["paper"] == "A4"
        assert data["sizes"][0]["page_count"] == 3

    def test_a_group_admits_its_pages_are_not_identical(self, tmp_path):
        pdf = build(tmp_path / "vary.pdf", page((595.2, 841.6)), page((597.6, 840.0)))

        assert read_page_layout(pdf)["sizes"][0]["exact_sizes_vary"] is True

    def test_identical_pages_do_not_claim_to_vary(self, tmp_path):
        pdf = build(tmp_path / "same.pdf", *[page(A4)] * 3)

        assert read_page_layout(pdf)["sizes"][0]["exact_sizes_vary"] is False

    def test_the_reported_size_is_one_the_pages_really_have(self, tmp_path):
        """Not an average of the group: the most common exact size in it."""
        pdf = build(
            tmp_path / "mode.pdf",
            page((595.2, 841.6)),
            page((595.2, 841.6)),
            page((597.6, 840.0)),
        )

        size = read_page_layout(pdf)["sizes"][0]

        assert (size["width_pt"], size["height_pt"]) == (595.2, 841.6)

    def test_a_different_size_is_a_different_group(self, tmp_path):
        """Tolerance must not reach the next entry in the table."""
        pdf = build(tmp_path / "mixed.pdf", page(A4), page(LETTER))

        data = read_page_layout(pdf)

        assert data["uniform"] is False
        assert {size["paper"] for size in data["sizes"]} == {"A4", "Letter"}

    def test_unnamed_sizes_are_grouped_by_their_dimensions(self, tmp_path):
        """A 16:9 presentation is not a paper size, and still groups."""
        pdf = build(tmp_path / "deck.pdf", *[page((960.0, 540.0))] * 3)

        size = read_page_layout(pdf)["sizes"][0]

        assert size["paper"] is None
        assert size["orientation"] == "landscape"
        assert size["page_count"] == 3
        assert "960 x 540 pt" in read_page_layout(pdf)["summary"]


class TestNonUniformDocument:
    """Several shapes: ordered by how much of the document they cover."""

    def test_groups_are_ordered_by_page_count(self, tmp_path):
        pdf = build(tmp_path / "order.pdf", page(LETTER), *[page(A4)] * 3)

        data = read_page_layout(pdf)

        assert [size["paper"] for size in data["sizes"]] == ["A4", "Letter"]
        assert [size["page_count"] for size in data["sizes"]] == [3, 1]

    def test_page_numbers_come_back_as_ranges(self, tmp_path):
        pdf = build(
            tmp_path / "ranges.pdf",
            page(A4),
            page(LETTER),
            page(A4),
            page(A4),
            page(A4),
        )

        sizes = {
            size["paper"]: size["pages"] for size in read_page_layout(pdf)["sizes"]
        }

        assert sizes["A4"] == "1,3-5"
        assert sizes["Letter"] == "2"

    def test_summary_lists_the_shapes(self, tmp_path):
        pdf = build(tmp_path / "varied.pdf", *([page(A4)] * 3 + [page(LETTER)]))

        summary = read_page_layout(pdf)["summary"]

        assert summary.startswith("Page sizes vary:")
        assert "3 pages are A4 portrait" in summary
        assert "1 page is Letter portrait" in summary

    def test_summary_does_not_list_every_shape(self, tmp_path):
        pdf = build(
            tmp_path / "many.pdf",
            page(A4),
            page(LETTER),
            page((419.53, 595.28)),
            page((960, 540)),
            page((300, 300)),
        )

        summary = read_page_layout(pdf)["summary"]

        assert "plus 2 more" in summary


class TestRotation:
    """`/Rotate` decides which way up a page arrives."""

    def test_a_rotated_landscape_box_presents_as_portrait(self, tmp_path):
        """The corpus is full of these: scans stored landscape, turned by /Rotate."""
        pdf = build(tmp_path / "turned.pdf", page((841.89, 595.28), rotate=90))

        size = read_page_layout(pdf)["sizes"][0]

        assert (size["width_pt"], size["height_pt"]) == (595.3, 841.9)
        assert size["orientation"] == "portrait"
        assert size["rotation"] == 90
        assert size["paper"] == "A4"

    def test_rotation_is_part_of_the_group_key(self, tmp_path):
        """Landscape by design and landscape by rotation are different situations."""
        pdf = build(
            tmp_path / "both.pdf",
            page((841.89, 595.28)),
            page(A4, rotate=90),
        )

        data = read_page_layout(pdf)

        assert len(data["sizes"]) == 2
        assert {size["rotation"] for size in data["sizes"]} == {0, 90}
        assert all(size["orientation"] == "landscape" for size in data["sizes"])

    def test_the_summary_mentions_rotation(self, tmp_path):
        pdf = build(tmp_path / "rot.pdf", page(A4, rotate=270))

        assert "rotated 270°" in read_page_layout(pdf)["summary"]

    def test_negative_rotation_is_normalized(self, tmp_path):
        """-90 is a normal way to write 270."""
        pdf = build(tmp_path / "neg.pdf", page(A4, rotate=-90))

        assert read_page_layout(pdf)["sizes"][0]["rotation"] == 270

    def test_a_rotation_that_is_not_a_right_angle_is_ignored(self, tmp_path):
        """/Rotate must be a multiple of 90; broken files disagree."""
        pdf = build(tmp_path / "odd.pdf", page(A4, rotate=45))

        size = read_page_layout(pdf)["sizes"][0]

        assert size["rotation"] == 0
        assert size["orientation"] == "portrait"


class TestBoxes:
    """What a reader sees is the CropBox, clipped to the MediaBox."""

    def test_the_cropbox_decides_the_reported_size(self, tmp_path):
        pdf = build(tmp_path / "crop.pdf", page(LETTER, crop=(0, 0, 306, 396)))

        size = read_page_layout(pdf)["sizes"][0]

        assert (size["width_pt"], size["height_pt"]) == (306.0, 396.0)

    def test_a_cropbox_outside_the_mediabox_is_clipped_to_it(self, tmp_path):
        """The spec says clip, and files really do reach outside."""
        pdf = build(tmp_path / "over.pdf", page(LETTER, crop=(-100, -100, 2000, 2000)))

        size = read_page_layout(pdf)["sizes"][0]

        assert (size["width_pt"], size["height_pt"]) == (612.0, 792.0)

    def test_a_degenerate_cropbox_falls_back_to_the_mediabox(self, tmp_path):
        """Better the whole page than a page of zero width."""
        pdf = build(tmp_path / "empty.pdf", page(LETTER, crop=(700, 800, 750, 850)))

        size = read_page_layout(pdf)["sizes"][0]

        assert (size["width_pt"], size["height_pt"]) == (612.0, 792.0)

    def test_corners_in_the_wrong_order_still_measure(self, tmp_path):
        """A box written top-down is legal."""
        pdf = build(tmp_path / "flipped.pdf", page(LETTER, crop=(306, 396, 0, 0)))

        size = read_page_layout(pdf)["sizes"][0]

        assert (size["width_pt"], size["height_pt"]) == (306.0, 396.0)

    def test_raw_boxes_come_back_in_the_per_page_detail(self, tmp_path):
        pdf = build(
            tmp_path / "raw.pdf",
            page(LETTER, crop=(10, 10, 300, 400), trim=(20, 20, 290, 390)),
        )

        row = read_page_layout(pdf, "1")["pages"][0]

        assert row["boxes"]["media"] == [0.0, 0.0, 612.0, 792.0]
        assert row["boxes"]["crop"] == [10.0, 10.0, 300.0, 400.0]
        assert row["boxes"]["trim"] == [20.0, 20.0, 290.0, 390.0]

    def test_boxes_a_file_does_not_declare_are_not_reported(self, tmp_path):
        """pypdf's accessors default *and write back*, which would invent boxes.

        Reading page.cropbox on a page without one sets /CropBox to the MediaBox,
        so a later presence check answers True. Measuring has to record what the
        file declares before it touches any accessor.
        """
        pdf = build(tmp_path / "plain.pdf", page(LETTER))

        data = read_page_layout(pdf, "1")

        assert set(data["pages"][0]["boxes"]) == {"media"}
        assert data["has_print_boxes"] is False

    def test_print_boxes_are_flagged_for_the_document(self, tmp_path):
        pdf = build(
            tmp_path / "print.pdf", page(LETTER), page(LETTER, bleed=(0, 0, 620, 800))
        )

        assert read_page_layout(pdf)["has_print_boxes"] is True


class TestUserUnit:
    """Large format pages escape the 200 inch limit with /UserUnit."""

    def test_physical_size_is_scaled(self, tmp_path):
        pdf = build(tmp_path / "big.pdf", page(LETTER, user_unit=3))

        row = read_page_layout(pdf, "1")["pages"][0]

        assert row["width_pt"] == 612.0, "points are unchanged"
        assert row["width_in"] == 25.5, "8.5 in x 3"
        assert row["user_unit"] == 3.0

    def test_the_default_unit_is_not_reported(self, tmp_path):
        pdf = build(tmp_path / "normal.pdf", page(LETTER))

        assert "user_unit" not in read_page_layout(pdf, "1")["pages"][0]


class TestPagesArgument:
    """The `pages` range scopes the per page detail."""

    def test_a_range(self, tmp_path):
        pdf = build(tmp_path / "range.pdf", *[page(A4)] * 8)

        data = read_page_layout(pdf, "2-4")

        assert [row["page"] for row in data["pages"]] == [2, 3, 4]
        assert data["detail_truncated"] is False

    def test_a_list_and_a_single_page(self, tmp_path):
        pdf = build(tmp_path / "list.pdf", *[page(A4)] * 8)

        assert [r["page"] for r in read_page_layout(pdf, "1,5,7-8")["pages"]] == [
            1,
            5,
            7,
            8,
        ]
        assert [r["page"] for r in read_page_layout(pdf, "3")["pages"]] == [3]

    def test_all(self, tmp_path):
        pdf = build(tmp_path / "all.pdf", *[page(A4)] * 5)

        assert len(read_page_layout(pdf, "all")["pages"]) == 5

    def test_a_reversed_range_is_read_as_written_forwards(self, tmp_path):
        pdf = build(tmp_path / "rev.pdf", *[page(A4)] * 6)

        assert [r["page"] for r in read_page_layout(pdf, "4-2")["pages"]] == [2, 3, 4]

    def test_out_of_range_is_clipped_to_the_document(self, tmp_path):
        pdf = build(tmp_path / "clip.pdf", *[page(A4)] * 3)

        assert [r["page"] for r in read_page_layout(pdf, "2-100")["pages"]] == [2, 3]

    def test_a_range_that_selects_nothing_is_an_error(self, tmp_path):
        """Silently returning no rows looks like a broken tool."""
        pdf = build(tmp_path / "none.pdf", *[page(A4)] * 3)

        data = read_page_layout(pdf, "0")

        assert data["success"] is False
        assert "selects no pages" in data["error"]

    def test_nonsense_explains_the_accepted_forms(self, tmp_path):
        pdf = build(tmp_path / "bad.pdf", page(A4))

        data = read_page_layout(pdf, "page seven")

        assert data["success"] is False
        assert "1,5,9-12" in data["error"]

    def test_detail_is_capped(self, tmp_path):
        """`pages="all"` on a long document must not flood the context window."""
        pdf = build(tmp_path / "long.pdf", *[page(A4)] * 120)

        data = read_page_layout(pdf, "all")

        assert len(data["pages"]) == 100
        assert data["detail_truncated"] is True
        assert "capped" in data["summary"]
        assert data["sizes"][0]["page_count"] == 120, "grouping still covers them all"


class TestFailures:
    """Failures return a reason instead of raising."""

    def test_missing_file(self, tmp_path):
        data = read_page_layout(str(tmp_path / "nope.pdf"))

        assert data["success"] is False
        assert data["file_exists"] is False

    def test_not_a_pdf(self, tmp_path):
        not_pdf = tmp_path / "notes.txt"
        not_pdf.write_text("This is not a PDF")

        assert read_page_layout(str(not_pdf))["success"] is False

    def test_encrypted_file_explains_itself(self, tmp_path):
        writer = PdfWriter()
        writer.add_blank_page(width=612, height=792)
        writer.encrypt("pw")
        buffer = io.BytesIO()
        writer.write(buffer)
        pdf = tmp_path / "locked.pdf"
        pdf.write_bytes(buffer.getvalue())

        data = read_page_layout(str(pdf))

        assert data["success"] is False
        assert data["encrypted"] is True

    def test_a_pypdf_error_outside_pdfreaderror_is_still_handled(
        self, tmp_path, monkeypatch
    ):
        pdf = build(tmp_path / "limit.pdf", page(A4))

        def over_the_limit(*args, **kwargs):
            raise LimitReachedError("cycle in /Parent")

        monkeypatch.setattr(page_layout_module, "PdfReader", over_the_limit)

        data = read_page_layout(pdf)

        assert data["success"] is False
        assert "LimitReachedError" in data["error"]

    def test_one_unmeasurable_page_does_not_fail_the_document(
        self, tmp_path, monkeypatch
    ):
        pdf = build(tmp_path / "flaky.pdf", *[page(A4)] * 3)

        original = PageObject.mediabox.fget
        seen = []

        def flaky(self):
            seen.append(self)
            if len(seen) == 2:
                raise ValueError("Expected an array of four values for /MediaBox")
            return original(self)

        monkeypatch.setattr(PageObject, "mediabox", property(flaky))

        data = read_page_layout(pdf, "all")

        assert data["success"] is True
        assert data["unreadable_pages"] == 1
        assert data["sizes"][0]["page_count"] == 2, "the readable pages still group"
        assert "could not be measured" in data["summary"]
        assert "error" in data["pages"][1]

    def test_corrupted_files_never_raise(self, tmp_path):
        source = Path(
            build(tmp_path / "source.pdf", page(A4), page(LETTER))
        ).read_bytes()
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
            result = read_page_layout(str(target), "all")

            assert result["success"] in (True, False)
            if not result["success"]:
                assert result["error"]


class TestJsonSafety:
    """Results cross an MCP boundary, so every value has to serialize."""

    def test_result_is_json_serializable(self, tmp_path):
        pdf = build(
            tmp_path / "json.pdf",
            page(A4, crop=(10, 10, 500, 700), trim=(20, 20, 490, 690), user_unit=2),
            page(LETTER, rotate=90),
        )

        json.dumps(read_page_layout(pdf, "all"))  # raises on pypdf objects
