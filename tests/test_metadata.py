"""Tests for reading PDF metadata.

The interesting cases are all about the two stores: a PDF keeps its properties in
an Info dictionary and/or an XMP packet, and the point of this verb is to report
both rather than silently pick one.
"""

import io

import pytest
from pypdf import PdfWriter

from benspdf import read_metadata

XMP_TEMPLATE = """<?xpacket begin='' id='W5M0MpCehiHzreSzNTczkc9d'?>
<x:xmpmeta xmlns:x='adobe:ns:meta/'>
 <rdf:RDF xmlns:rdf='http://www.w3.org/1999/02/22-rdf-syntax-ns#'>
  <rdf:Description rdf:about=''
    xmlns:dc='http://purl.org/dc/elements/1.1/'
    xmlns:xmp='http://ns.adobe.com/xap/1.0/'
    xmlns:pdf='http://ns.adobe.com/pdf/1.3/'>
{body}
  </rdf:Description>
 </rdf:RDF>
</x:xmpmeta>
<?xpacket end='w'?>"""


def xmp_packet(**fields: str) -> bytes:
    """Build an XMP packet from a few well known fields."""
    elements = {
        "title": "<dc:title><rdf:Alt><rdf:li xml:lang='x-default'>{}</rdf:li>"
        "</rdf:Alt></dc:title>",
        "author": "<dc:creator><rdf:Seq><rdf:li>{}</rdf:li></rdf:Seq></dc:creator>",
        "description": "<dc:description><rdf:Alt><rdf:li xml:lang='x-default'>{}"
        "</rdf:li></rdf:Alt></dc:description>",
        "producer": "<pdf:Producer>{}</pdf:Producer>",
        "creator_tool": "<xmp:CreatorTool>{}</xmp:CreatorTool>",
        "created": "<xmp:CreateDate>{}</xmp:CreateDate>",
        "modified": "<xmp:ModifyDate>{}</xmp:ModifyDate>",
    }
    body = [elements[name].format(value) for name, value in fields.items()]
    return XMP_TEMPLATE.format(body="\n".join(body)).encode()


def keywords_packet(*keywords: str) -> bytes:
    """An XMP packet whose only field is a dc:subject keyword bag."""
    items = "".join(f"<rdf:li>{word}</rdf:li>" for word in keywords)
    body = f"<dc:subject><rdf:Bag>{items}</rdf:Bag></dc:subject>"
    return XMP_TEMPLATE.format(body=body).encode()


def write_pdf(path, info=None, xmp=None, password=None):
    """Write a one page PDF carrying the given Info dict and/or XMP packet."""
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    if info:
        writer.add_metadata(info)
    if xmp:
        writer.xmp_metadata = xmp
    if password:
        writer.encrypt(password)

    buffer = io.BytesIO()
    writer.write(buffer)
    path.write_bytes(buffer.getvalue())
    return str(path)


class TestInfoDictionary:
    """A PDF with only the legacy Info dictionary."""

    def test_reads_the_common_fields(self, tmp_path):
        pdf = write_pdf(
            tmp_path / "info.pdf",
            info={
                "/Title": "Quarterly Report",
                "/Author": "Ada Lovelace",
                "/Subject": "Numbers",
                "/Creator": "Word",
                "/Producer": "Acrobat",
            },
        )

        data = read_metadata(pdf)

        assert data["success"] is True
        assert data["title"] == "Quarterly Report"
        assert data["author"] == "Ada Lovelace"
        assert data["subject"] == "Numbers"
        assert data["creator_tool"] == "Word"
        assert data["producer"] == "Acrobat"
        assert data["has_info"] is True
        assert data["has_xmp"] is False

    def test_creator_is_the_application_not_the_author(self, tmp_path):
        """/Creator is the authoring tool; /Author is the person."""
        pdf = write_pdf(
            tmp_path / "creator.pdf",
            info={"/Author": "Ada Lovelace", "/Creator": "Word"},
        )

        data = read_metadata(pdf)

        assert data["author"] == "Ada Lovelace"
        assert data["creator_tool"] == "Word"

    def test_comma_separated_keywords_become_a_list(self, tmp_path):
        pdf = write_pdf(
            tmp_path / "keywords.pdf", info={"/Keywords": "finance, q3 , report"}
        )

        data = read_metadata(pdf)

        assert data["keywords"] == ["finance", "q3", "report"]

    def test_dates_are_iso_8601(self, tmp_path):
        pdf = write_pdf(
            tmp_path / "dates.pdf",
            info={
                "/CreationDate": "D:20200101120000+00'00'",
                "/ModDate": "D:20210615093000+02'00'",
            },
        )

        data = read_metadata(pdf)

        assert data["created"] == "2020-01-01T12:00:00+00:00"
        assert data["modified"] == "2021-06-15T09:30:00+02:00"

    def test_unparseable_date_keeps_the_raw_value(self, tmp_path):
        """One bad date should cost that date, not the whole result."""
        pdf = write_pdf(
            tmp_path / "baddate.pdf",
            info={"/Title": "Fine", "/CreationDate": "not a date"},
        )

        data = read_metadata(pdf)

        assert data["success"] is True
        assert data["title"] == "Fine"
        assert data["created"] is None
        assert data["info"]["/CreationDate"] == "not a date"

    def test_absent_fields_are_null_and_keywords_is_a_list(self, tmp_path):
        pdf = write_pdf(tmp_path / "bare.pdf")

        data = read_metadata(pdf)

        assert data["success"] is True
        assert data["title"] is None
        assert data["author"] is None
        assert data["created"] is None
        assert data["keywords"] == []
        assert data["sources"]["title"] is None
        assert data["has_xmp"] is False


class TestXmp:
    """A PDF carrying an XMP packet."""

    def test_reads_the_common_fields(self, tmp_path):
        pdf = write_pdf(
            tmp_path / "xmp.pdf",
            xmp=xmp_packet(
                title="XMP Report",
                author="Grace Hopper",
                description="About things",
                producer="XMP Producer",
                creator_tool="XMP Tool",
            ),
        )

        data = read_metadata(pdf)

        assert data["title"] == "XMP Report"
        assert data["author"] == "Grace Hopper"
        assert data["subject"] == "About things"
        assert data["producer"] == "XMP Producer"
        assert data["creator_tool"] == "XMP Tool"
        assert data["has_xmp"] is True

    def test_language_alternative_is_flattened(self, tmp_path):
        """dc:title is a lang map in the raw store but one string in the answer."""
        pdf = write_pdf(tmp_path / "lang.pdf", xmp=xmp_packet(title="Flattened"))

        data = read_metadata(pdf)

        assert data["title"] == "Flattened"
        assert data["xmp"]["dc_title"] == {"x-default": "Flattened"}

    def test_keyword_bag_becomes_a_list(self, tmp_path):
        pdf = write_pdf(tmp_path / "bag.pdf", xmp=keywords_packet("alpha", "beta"))

        data = read_metadata(pdf)

        assert data["keywords"] == ["alpha", "beta"]

    def test_dates_are_iso_8601(self, tmp_path):
        pdf = write_pdf(
            tmp_path / "xmpdates.pdf",
            xmp=xmp_packet(created="2024-01-15T10:30:00+01:00"),
        )

        data = read_metadata(pdf)

        # pypdf normalizes XMP dates to UTC, so the offset is folded in.
        assert data["created"] == "2024-01-15T09:30:00+00:00"

    def test_malformed_packet_does_not_fail_the_read(self, tmp_path):
        pdf = write_pdf(
            tmp_path / "broken.pdf",
            info={"/Title": "Still readable"},
            xmp=b"<x:xmpmeta this is not xml",
        )

        data = read_metadata(pdf)

        assert data["success"] is True
        assert data["title"] == "Still readable"
        assert data["has_xmp"] is False


class TestBothStores:
    """The reason this verb does not merge: the stores disagree."""

    def test_xmp_wins_and_the_source_says_so(self, tmp_path):
        pdf = write_pdf(
            tmp_path / "both.pdf",
            info={"/Title": "Info Title", "/Author": "Info Author"},
            xmp=xmp_packet(title="XMP Title", author="XMP Author"),
        )

        data = read_metadata(pdf)

        assert data["title"] == "XMP Title"
        assert data["sources"]["title"] == "xmp"
        assert data["sources"]["author"] == "xmp"

    def test_conflicts_are_reported_with_both_values(self, tmp_path):
        pdf = write_pdf(
            tmp_path / "conflict.pdf",
            info={"/Title": "Info Title"},
            xmp=xmp_packet(title="XMP Title"),
        )

        data = read_metadata(pdf)

        assert data["has_conflicts"] is True
        assert data["conflicts"]["title"] == {
            "info": "Info Title",
            "xmp": "XMP Title",
        }

    def test_agreement_is_not_a_conflict(self, tmp_path):
        pdf = write_pdf(
            tmp_path / "agree.pdf",
            info={"/Title": "Same Title"},
            xmp=xmp_packet(title="Same Title"),
        )

        data = read_metadata(pdf)

        assert data["has_conflicts"] is False
        assert data["conflicts"] == {}

    def test_the_same_instant_in_two_offsets_is_not_a_conflict(self, tmp_path):
        """Info keeps its offset, XMP is normalized to UTC. Same moment."""
        pdf = write_pdf(
            tmp_path / "sametime.pdf",
            info={"/CreationDate": "D:20240115103000+01'00'"},
            xmp=xmp_packet(created="2024-01-15T10:30:00+01:00"),
        )

        data = read_metadata(pdf)

        assert "created" not in data["conflicts"]

    def test_stores_fall_back_field_by_field(self, tmp_path):
        """A field missing from XMP still gets answered from Info."""
        pdf = write_pdf(
            tmp_path / "partial.pdf",
            info={"/Title": "Info Title", "/Author": "Info Author"},
            xmp=xmp_packet(title="XMP Title"),
        )

        data = read_metadata(pdf)

        assert data["title"] == "XMP Title"
        assert data["sources"]["title"] == "xmp"
        assert data["author"] == "Info Author"
        assert data["sources"]["author"] == "info"

    def test_both_raw_stores_are_returned_unmodified(self, tmp_path):
        pdf = write_pdf(
            tmp_path / "raw.pdf",
            info={"/Title": "Info Title"},
            xmp=xmp_packet(title="XMP Title"),
        )

        data = read_metadata(pdf)

        assert data["info"]["/Title"] == "Info Title"
        assert data["xmp"]["dc_title"] == {"x-default": "XMP Title"}


class TestFailures:
    """Failures return a reason instead of raising."""

    def test_missing_file(self, tmp_path):
        data = read_metadata(str(tmp_path / "nope.pdf"))

        assert data["success"] is False
        assert "not found" in data["error"].lower()
        assert data["file_exists"] is False

    def test_not_a_pdf(self, tmp_path):
        not_pdf = tmp_path / "notes.txt"
        not_pdf.write_text("This is not a PDF")

        data = read_metadata(str(not_pdf))

        assert data["success"] is False
        assert "error" in data

    def test_encrypted_file_explains_itself(self, tmp_path):
        pdf = write_pdf(
            tmp_path / "locked.pdf", info={"/Title": "Secret"}, password="pw"
        )

        data = read_metadata(pdf)

        assert data["success"] is False
        assert "encrypted" in data["error"].lower()
        assert data["encrypted"] is True


class TestJsonSafety:
    """Results cross an MCP boundary, so every value has to serialize."""

    def test_result_is_json_serializable(self, tmp_path):
        import json

        pdf = write_pdf(
            tmp_path / "json.pdf",
            info={
                "/Title": "Info Title",
                "/Keywords": "a, b",
                "/CreationDate": "D:20200101120000+00'00'",
            },
            xmp=xmp_packet(title="XMP Title", created="2024-01-15T10:30:00+01:00"),
        )

        data = read_metadata(pdf)

        json.dumps(data)  # raises TypeError on datetimes or pypdf objects
