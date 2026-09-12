"""
MCP Server for BensPDF

Exposes PDF tools via the Model Context Protocol (MCP).
Works with Claude Desktop, OpenAI, Cursor, and any MCP-compatible client.

Tools take a `ref`, which is either a path to one of the user's files or the id
of a temporary workspace artifact produced by an earlier tool. Results stay in
the workspace until `export` is called, so chained operations never litter the
user's folders and nothing is written until they ask for it.

That contract is stated once in `INSTRUCTIONS` below rather than in every tool
description, since the descriptions are what a model pays for on every turn.
Each tool then covers only what is specific to it: the question it answers, what
it deliberately does not do, and any field whose meaning its name does not give
away. Field lists are left out — results are dicts with self-describing keys, and
the model reads them moments later anyway.
"""

import json
from importlib.metadata import PackageNotFoundError, version as _pkg_version
from typing import Any, Dict, List, Optional

from mcp.server import MCPServer
from mcp.server.mcpserver import Image
from mcp.types import CallToolResult, TextContent

import benscore
from benscore import tools as core_tools
from benspdf import (
    PDFPageCounterTool,
    check_access,
    check_text,
    read_metadata,
    read_page_layout,
    render_pages,
)
from benspdf.tools.create_test_pdf import create_test_pdf_bytes

try:
    _VERSION = _pkg_version("benspdf-mcp")
except PackageNotFoundError:  # running from a source checkout
    _VERSION = "0.0.0.dev0"

#: The contract every tool shares, sent once with the server rather than repeated
#: in seven descriptions. Each tool still calls its `ref` a path or artifact id,
#: because not every client passes these instructions to the model, and that one
#: fact is the only part a model cannot recover from a result.
INSTRUCTIONS = """BensPDF reads and edits PDFs on the user's own machine. \
Nothing is uploaded.

Every tool follows the same contract:

- A `ref` is either a path to one of the user's files (~ is expanded) or the id \
of a workspace artifact from an earlier tool, like "art_a1b2c3d4.pdf". The two \
are interchangeable, which is what lets tools be chained.
- Results are dictionaries carrying `success`. On failure, `error` explains what \
went wrong and usually what to do about it, so read it instead of retrying \
blindly.
- A tool that produces a file stores it as a temporary artifact and returns its \
id, alongside the `path` it currently sits at. Pass the id to other tools; offer \
the path when the user wants to look at the file themselves.
- `export` is the only tool that writes into the user's folders, so nothing is \
saved where they keep things until it is called, and artifacts expire after a \
week."""

#: How many rendered pages come back as viewable images. Images cost roughly a
#: thousand tokens each, so the rest stay artifacts the caller can export or
#: render again in a narrower range.
MAX_INLINE_IMAGES = 5

# Create MCP server
mcp = MCPServer("benspdf", version=_VERSION, instructions=INSTRUCTIONS)


def _as_result(
    payload: Dict[str, Any], images: Optional[List[str]] = None
) -> CallToolResult:
    """Wrap a tool payload, optionally with images the model can actually see.

    Returning a dict gets a tool structured output and nothing to look at.
    Returning image content gets something to look at and no structured output.
    A `CallToolResult` carries both, in the same ``{"result": ...}`` shape every
    other tool here produces, so the payload convention does not change just
    because this one verb has pictures.
    """
    blocks: List[Any] = []

    for artifact in images or []:
        try:
            data = benscore.resolve(artifact).read_bytes()
        except (benscore.ArtifactNotFound, OSError):
            continue  # the artifact id is still in the payload
        blocks.append(Image(data=data, format="png").to_image_content())

    blocks.append(
        TextContent(type="text", text=json.dumps(payload, indent=2, default=str))
    )
    return CallToolResult(content=blocks, structured_content={"result": payload})


# Register the shared core tools (export, list_artifacts, discard)
core_tools.register(mcp)

# Initialize the tool
pdf_counter = PDFPageCounterTool()


@mcp.tool()
def pdf_page_count(ref: str) -> Dict[str, Any]:
    """Count the pages in a PDF. Answers "how many pages is this?".

    Reads only the document's page tree, so it stays cheap on large files. It
    does not read page text, page sizes, or document properties.

    Args:
        ref: PDF file path, or a workspace artifact id.
    """
    try:
        resolved = benscore.resolve(ref)
    except benscore.ArtifactNotFound as exc:
        return benscore.err(str(exc), file_exists=False)
    except (FileNotFoundError, IsADirectoryError) as exc:
        return benscore.err(str(exc), file_path=str(ref), file_exists=False)

    return pdf_counter.count_pages(str(resolved))


@mcp.tool()
def pdf_metadata(ref: str) -> Dict[str, Any]:
    """Read a PDF's document properties: title, author, dates, producer, keywords.

    Answers "who made this, when, and with what". It does not count pages (use
    pdf_page_count) and says nothing about whether the pages hold readable text
    (use pdf_check_text).

    A PDF can store these fields in two independent places, the legacy Info
    dictionary and an XMP packet, and the two often disagree. The normalized
    answer is at the top level, preferring XMP, with `sources` naming the store
    each value came from and `conflicts` listing every field where the two differ,
    both values included. Both stores also come back verbatim as `info` and `xmp`.
    When `has_conflicts` is true, say so rather than quoting one value as fact.

    Args:
        ref: PDF file path, or a workspace artifact id.
    """
    try:
        resolved = benscore.resolve(ref)
    except benscore.ArtifactNotFound as exc:
        return benscore.err(str(exc), file_exists=False)
    except (FileNotFoundError, IsADirectoryError) as exc:
        return benscore.err(str(exc), file_path=str(ref), file_exists=False)

    return read_metadata(str(resolved))


@mcp.tool()
def pdf_check_text(ref: str) -> Dict[str, Any]:
    """Check whether a PDF has a text layer, looks scanned, or needs OCR.

    Answers "can I read this, or is it a picture of a document?". Worth running
    before extracting text from a file you have not seen.

    `verdict` is "text", "scanned", "mixed", or "no_text" — the last meaning
    nothing readable but nothing scan-like either, so blank, vector-only, or
    illustrated pages that OCR cannot help. `needs_ocr` follows from it, `summary`
    is a sentence worth quoting, and the per page evidence behind the verdict
    comes back alongside, including each page's `image_coverage` (the largest
    image's share of the page area).

    Samples up to 10 pages spread across the document, so a true `sampled` means
    the answer is an estimate for the pages in between. It does not return the
    text (use pdf_extract_text) and it does not run OCR.

    Args:
        ref: PDF file path, or a workspace artifact id.
    """
    try:
        resolved = benscore.resolve(ref)
    except benscore.ArtifactNotFound as exc:
        return benscore.err(str(exc), file_exists=False)
    except (FileNotFoundError, IsADirectoryError) as exc:
        return benscore.err(str(exc), file_path=str(ref), file_exists=False)

    return check_text(str(resolved))


@mcp.tool()
def pdf_check_access(ref: str) -> Dict[str, Any]:
    """Check a PDF's encryption and what it permits: printing, copying, editing.

    Answers "is this locked, and what am I allowed to do with it?".

    `encrypted` and `needs_password` are separate answers and the difference
    matters: most encrypted files have no user password, so they open silently and
    only carry restrictions. `permissions` gives a boolean per action,
    `restrictions` lists what is denied, and an unencrypted file permits
    everything — a PDF has nowhere to keep restrictions but its encryption
    dictionary.

    Treat restrictions as what the file asks of viewers, not as enforcement: once a
    document is open nothing stops the bits being ignored, and a file that denies
    copying still yields its text to pdf_extract_text. Report them as the author's
    intent, never as an action being impossible.

    Worth reaching for when another tool reports a file as encrypted; this one
    still answers, since the encryption dictionary is readable when the pages are
    not.

    Args:
        ref: PDF file path, or a workspace artifact id.
    """
    try:
        resolved = benscore.resolve(ref)
    except benscore.ArtifactNotFound as exc:
        return benscore.err(str(exc), file_exists=False)
    except (FileNotFoundError, IsADirectoryError) as exc:
        return benscore.err(str(exc), file_path=str(ref), file_exists=False)

    return check_access(str(resolved))


@mcp.tool()
def pdf_page_layout(ref: str, pages: Optional[str] = None) -> Dict[str, Any]:
    """Measure a PDF's page sizes, orientation and rotation.

    Answers "what size is this, is it all the same, and why does one page come out
    sideways?". Reads page geometry only, so it stays cheap on long documents.

    `sizes` groups the pages by shape, most pages first, each carrying the page
    numbers it covers as a range like "1-16,18", so a 300 page document answers in
    one entry instead of 300 rows.
    Sizes are the page as a reader sees it: CropBox clipped to MediaBox with
    `/Rotate` applied, so a landscape box rotated 90 degrees reports as portrait.
    Pages within 3pt of a known paper are grouped and named together, since real A4
    varies by a millimetre.

    Pass `pages` for per page rows carrying the raw boxes: "1-20", "3", "1,5,9-12"
    or "all", capped at 100 rows.

    It does not look at page content; for whether the pages hold readable text use
    pdf_check_text.

    Args:
        ref: PDF file path, or a workspace artifact id.
        pages: Optional page range for per page detail. Omit for groups only.
    """
    try:
        resolved = benscore.resolve(ref)
    except benscore.ArtifactNotFound as exc:
        return benscore.err(str(exc), file_exists=False)
    except (FileNotFoundError, IsADirectoryError) as exc:
        return benscore.err(str(exc), file_path=str(ref), file_exists=False)

    return read_page_layout(str(resolved), pages)


@mcp.tool()
def pdf_render_pages(
    ref: str,
    pages: Optional[str] = None,
    dpi: int = 150,
    view: bool = True,
) -> CallToolResult:
    """Render PDF pages to images, so a page can be looked at rather than read.

    Use it to see what a page looks like: checking a change landed (did a redaction
    remove content, did a split cut where intended), previewing, and pages where
    appearance is the content — handwriting, signatures, charts, checkbox state.

    For *reading* a scan prefer pdf_ocr, whose text layer is searchable and needs no
    vision model; render when OCR is unavailable or would mangle what matters.

    Every page is saved as a PNG artifact and its id returned. With `view`, the
    default, the first few images also come back to be looked at, since images are
    expensive in context. Limits are explicit: 20 pages per call, and resolution is
    reduced for a page that would be enormous — both reported, never silent.

    Args:
        ref: PDF file path, or a workspace artifact id.
        pages: Which pages: "1-20", "3", "1,5,9-12" or "all". Defaults to all, up
            to the per-call limit.
        dpi: Resolution, 36 to 600. 150 suits reading and previewing.
        view: Return the images to look at, not just artifact ids.
    """
    try:
        resolved = benscore.resolve(ref)
    except benscore.ArtifactNotFound as exc:
        return _as_result(benscore.err(str(exc), file_exists=False))
    except (FileNotFoundError, IsADirectoryError) as exc:
        return _as_result(benscore.err(str(exc), file_path=str(ref), file_exists=False))

    result = render_pages(str(resolved), pages=pages, dpi=dpi)

    if not (view and result.get("success")):
        return _as_result(result)

    shown = result["pages"][:MAX_INLINE_IMAGES]
    result["images_attached"] = len(shown)
    result["images_omitted"] = len(result["pages"]) - len(shown)
    if result["images_omitted"]:
        result["summary"] += (
            f" {result['images_attached']} of {len(result['pages'])} images are "
            f"shown here; the rest are artifacts."
        )

    return _as_result(result, images=[entry["artifact"] for entry in shown])


@mcp.tool()
def create_test_pdf_file(
    output_path: Optional[str] = None,
    num_pages: int = 3,
    title: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a test PDF, handy for trying the other tools without hunting for one.

    The pages are blank, so this is for exercising tools rather than for anything
    that needs real content.

    Args:
        output_path: Optional path to also write the PDF to. Omit to keep it as a
            temporary artifact.
        num_pages: Number of blank pages to create (default: 3)
        title: Optional title for the PDF metadata
    """
    try:
        data = create_test_pdf_bytes(num_pages=num_pages, title=title)
        artifact = benscore.save(data, ".pdf")
    except (OSError, ValueError) as exc:
        return benscore.err(f"Could not create the test PDF: {exc}")

    result: Dict[str, Any] = {
        "artifact": artifact,
        "path": str(benscore.artifact_path(artifact)),
        "num_pages": num_pages,
    }

    if output_path:
        try:
            result["pdf_path"] = str(
                benscore.export_artifact(artifact, output_path, overwrite=True)
            )
        except OSError as exc:
            return benscore.err(f"Could not write to {output_path}: {exc}", **result)

    return benscore.ok(**result)


def main() -> None:
    """Run the MCP server over stdio. Used by the console script."""
    # Clear out expired and over-cap artifacts once at boot. Cheaper and simpler
    # than a background reaper, and good enough for a per-user workspace.
    try:
        benscore.prune()
    except OSError:
        pass  # housekeeping must never stop the server from starting

    mcp.run()


if __name__ == "__main__":
    main()
