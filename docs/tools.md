# Tool reference

Every tool in [BensPDF](../README.md), in detail: what it answers, the fields it
returns, and the judgement calls behind them.

You don't need this to use the tools. An MCP client reads each tool's own
description and calls it for you. This is for reading a result you didn't expect,
or for calling the functions directly from Python.

Every tool takes a `ref`, which is either a path to one of your files (`~` is
expanded, relative paths are resolved) or the id of a workspace artifact from an
earlier tool. Results are dictionaries carrying `success`; on failure, `error`
explains what went wrong instead of raising.

## `pdf_page_count(ref)`

Counts pages, and nothing else, so it stays cheap on a long document: only the
page tree is read.

```python
from benspdf import PDFPageCounterTool

tool = PDFPageCounterTool()
tool("~/Downloads/report.pdf")
```

```python
{'page_count': 12,
 'file_path': '/Users/you/Downloads/report.pdf',
 'file_name': 'report.pdf',
 'file_exists': True,
 'success': True}
```

Missing files and non-PDFs return an `error` key instead of raising, so an agent
can read the reason and recover:

```python
{'error': 'File not found: nope.pdf', 'file_path': '...', 'file_exists': False}
```

## `pdf_metadata(ref)`

Document properties: title, author, subject, keywords, producer, and dates.

A PDF can keep these in two independent places — the old Info dictionary and an
XMP packet — and they often disagree, because files pass through tools that
update one and leave the other stale. So this doesn't merge them. You get a
normalized answer at the top level, `sources` saying which store each value came
from, and `conflicts` listing any field where the two differ, with both values.
Both stores are also returned untouched as `info` and `xmp`.

```python
from benspdf import read_metadata

read_metadata("~/Downloads/report.pdf")
```

```python
{'success': True,
 'title': 'The Real Title',        # XMP wins when both are present
 'author': 'Ada Lovelace',
 'keywords': ['finance', 'q3'],
 'producer': 'Acrobat',
 'created': '2024-01-15T10:30:00+01:00',
 'modified': '2026-03-01T12:00:00+00:00',
 'sources': {'title': 'xmp', 'author': 'info', ...},
 'conflicts': {'title': {'info': 'Stale Title', 'xmp': 'The Real Title'}},
 'has_conflicts': True,
 'info': {'/Title': 'Stale Title', ...},
 'xmp': {'dc_title': {'x-default': 'The Real Title'}, ...}}
```

Dates come back as ISO 8601. Encrypted files say so rather than reporting an
empty result.

## `pdf_check_text(ref)`

Whether you can read a PDF's text or need to OCR it first. Worth running before
extracting text from a file you haven't seen.

```python
from benspdf import check_text

check_text("~/Downloads/contract.pdf")
```

```python
{'success': True,
 'verdict': 'scanned',            # or 'text', 'mixed', 'no_text'
 'needs_ocr': True,
 'has_text_layer': False,
 'summary': 'None of 5 pages examined yielded text, and 5 pages are covered by '
            'a single image, so this looks like a scan and needs OCR before its '
            'text can be read.',
 'page_count': 5,
 'sampled': False,
 'pages': [{'page': 1, 'characters': 0, 'images': 1, 'image_coverage': 1.0,
            'has_text': False, 'looks_scanned': True}, ...],
 'producer': 'Canon iR-ADV C5550',
 'producer_suggests_scanner': True}
```

A page counts as scanned when one image covers most of it. That distinction
matters: a brochure page with three photos and a heading has images and barely
any text too, and it is not a scan. Coverage comes from the page's own geometry
rather than from decoding the images, so this stays fast on a big scan.

Long documents are sampled — up to 10 pages spread across the file, ends
included — which keeps a 2000-page scan as cheap as a short one and makes the
answer an estimate. `sampled` tells you when that happened, and `pages` lists
what was actually read.

`text_excerpt` shows you the first of the text itself, so you can tell real text
from text that came out of some earlier OCR as gibberish.

Run this on a copy you made with `pdf_ocr` and it says `text`, not `scanned`, even
though the scanned image is still there underneath. Those pages are marked `ocred:
true`, which is worth knowing when you read them: the text is what OCR made of the
page, so it can hold recognition errors. `ocred_pages` counts them, and a document
you only OCR'd part of comes back `mixed`, with the rest still needing a pass.

## `pdf_check_access(ref)`

Encryption and permissions, which are one question rather than two: a PDF has
nowhere to keep restrictions except inside its encryption dictionary, so an
unencrypted file cannot forbid anything.

```python
from benspdf import check_access

check_access("~/Downloads/contract.pdf")
```

```python
{'success': True,
 'encrypted': True,
 'needs_password': False,
 'restricted': True,
 'restrictions': ['modify', 'annotate', 'fill_forms', 'assemble'],
 'permissions': {'print': True, 'print_high_quality': True, 'copy': True,
                 'modify': False, 'annotate': False, 'fill_forms': False,
                 'assemble': False, 'accessibility': True},
 'permissions_valid': None,
 'encryption': {'algorithm': 'AES-128', 'version': 4, 'revision': 4,
                'key_bits': 128, 'encrypts_metadata': True},
 'summary': 'Encrypted with AES-128, but it opens without a password. It asks '
            'viewers to disallow editing the content, annotating, filling in '
            'forms and reorganizing pages. Those bits are a request to viewers, '
            'not a lock: the file is already open, so nothing enforces them.'}
```

`encrypted` and `needs_password` are deliberately separate, because encryption
sounds like a locked door and usually isn't one. Most encrypted PDFs open without
anyone typing a password; they just carry restrictions.

Those restrictions are a request to viewers, not a lock. A file that declares its
text may not be copied will still hand its text to `pdf_check_text` — and to most
other software. So the summary states the restriction *and* that nothing enforces
it, which is worth repeating rather than telling someone an action is impossible.

`permissions_valid` is `None` unless there was a real check to run. Only AES-256
stores a signed copy of the permission bits; pypdf reports `True` for weaker
encryption, meaning "nothing to verify", which is not the same as verified.

This is also the one tool that still answers for a password-protected file. The
encryption dictionary isn't itself encrypted, so permissions are readable even
when the pages aren't — where the other tools can only report the encryption and
stop.

## `pdf_page_layout(ref, pages=None)`

Page sizes, orientation, rotation and the page boxes.

```python
from benspdf import read_page_layout

read_page_layout("~/Downloads/thesis.pdf")
```

```python
{'success': True,
 'page_count': 213,
 'uniform': False,
 'summary': 'Page sizes vary: 211 pages are A4 portrait and 2 pages are A4 '
            'landscape, rotated 90°. The most common is 595.3 x 841.9 pt, '
            '210 x 297 mm.',
 'sizes': [{'paper': 'A4', 'orientation': 'portrait', 'rotation': 0,
            'width_pt': 595.3, 'height_pt': 841.9,
            'width_mm': 210.0, 'height_mm': 297.0,
            'width_in': 8.27, 'height_in': 11.69,
            'page_count': 211, 'pages': '1-211', 'exact_sizes_vary': False},
           {'paper': 'A4', 'orientation': 'landscape', 'rotation': 90, ...}],
 'has_print_boxes': False}
```

Pages are grouped by shape rather than listed one by one, so a 300-page document
answers in one entry and each group carries its page numbers as a range like
`"1-16,18"`. Sizes within 3pt of a known paper are named and grouped together,
because real A4 pages measure 595.2 x 841.6 in one file and 597.6 x 840.0 in the
next; `exact_sizes_vary` tells you when a group isn't perfectly uniform.

Reported sizes are the page as a reader sees it: the CropBox clipped to the
MediaBox, with `/Rotate` applied. So a landscape box turned 90° reads as portrait,
which is why a scan can look "wrong" in one viewer and fine in another.

`pages` adds per-page rows with the raw boxes, for print questions or a single odd
page:

```python
read_page_layout("~/Downloads/thesis.pdf", "212")
# {'page': 212, 'width_pt': 841.9, 'height_pt': 595.3, 'rotation': 90,
#  'paper': 'A4', 'orientation': 'landscape',
#  'boxes': {'media': [0.0, 0.0, 595.28, 841.89]}, ...}
```

Accepts `"1-20"`, `"3"`, `"1,5,9-12"` or `"all"`, capped at 100 rows.

## `pdf_render_pages(ref, pages=None, dpi=150, view=True)`

Renders pages to PNG images. This is for *seeing* a page, not reading it.

```python
from benspdf import render_pages

render_pages("~/Downloads/scan.pdf", "1-2")
```

```python
{'success': True,
 'page_count': 5,
 'format': 'png',
 'dpi': 150,
 'pages': [{'page': 1, 'artifact': 'art_bf52d4d1.png',
            'path': '/Users/you/.benstools/work/art_bf52d4d1.png',
            'width_px': 1275, 'height_px': 1651, 'dpi': 150,
            'size_bytes': 295904}, ...],
 'artifacts': ['art_bf52d4d1.png', 'art_9c1e77a0.png'],
 'rendered': 2,
 'truncated': False,
 'summary': 'Rendered 2 pages of scan.pdf (page 1-2) as PNG at 150 dpi, '
            '1275 x 1651 px. Pass the artifact ids to export to save them.'}
```

Over MCP, the images come back in the response as well, so the model can actually
look at them — the first few only, since an image costs roughly a thousand tokens.
`view=False` skips them for bulk work. Everything is saved as an artifact either
way, so `export` can write the ones you want to keep.

What it's for: checking a change landed (did a redaction remove the content or just
cover it, did a split cut where you meant), previews, and pages where the
appearance *is* the content — handwriting, signatures, charts, checkbox state.

For *reading* a scan, OCR is the better path: a text layer is searchable, cheap to
re-read, and works with a local text model, which no image does. Render when OCR
isn't available or would mangle what matters.

Every page is drawn in full, so the limits are explicit and always reported: 20 pages
per call, and a resolution that keeps the bitmap to a sane size. A poster-sized page
at 150 dpi runs to tens of megapixels, so it comes back at whatever dpi fits, with
`dpi_reduced_from` and a note in the summary saying so.

The page it draws is the page `pdf_page_layout` measures — CropBox, with `/Rotate`
applied — so a rotated page renders the way it presents.

## `pdf_ocr(ref, pages=None, lang="eng", dpi=200, output="text", force=False)`

Reads pages that carry no text, and can hand back a copy of the document with a real
text layer added.

```python
from benspdf import ocr

ocr("~/Downloads/scan.pdf", output="both")
```

```python
{'success': True,
 'file_name': 'scan.pdf',
 'page_count': 1,
 'language': 'eng',
 'dpi': 200,
 'tesseract_version': '5.5.2',
 'pages': [{'page': 1,
            'text': 'Quarterly Report\nRevenue rose 12 percent in the third '
                    'quarter,\ndriven by renewals in the enterprise segment.\n'
                    'Costs were flat.',
            'chars': 125, 'words': 20,
            'mean_confidence': 96.7, 'low_confidence_words': 0, 'dpi': 200}],
 'pages_read': 1,
 'truncated': False,
 'skipped': [],
 'failed': [],
 'words': 20,
 'mean_confidence': 96.7,
 'low_confidence_words': 0,
 'artifact': 'art_c87d9159.pdf',
 'path': '/Users/you/.benstools/work/art_c87d9159.pdf',
 'size_bytes': 79944,
 'text_layer_pages': '1',
 'summary': 'Read 1 page of scan.pdf (page 1) with OCR at 200 dpi in eng: 20 words, '
            'mean confidence 96.7 out of 100. A searchable copy of scan.pdf is '
            'artifact art_c87d9159.pdf, with the text layer on page 1; pass it to '
            'export to keep it.'}
```

`output` picks what you get: `"text"` for the text alone, `"pdf"` for the searchable
copy as an artifact, `"both"` for both. The copy is your original document with an
invisible text layer laid over each page. The scan itself is untouched, so the pages
still look exactly as they did and the file barely grows, but the text now selects,
searches and copies like any other PDF's.

### Installing tesseract

OCR needs [tesseract](https://github.com/tesseract-ocr/tesseract), which is a program
rather than a Python package, so installing this one doesn't bring it:

```bash
brew install tesseract            # macOS
sudo apt install tesseract-ocr    # Debian, Ubuntu
winget install UB-Mannheim.TesseractOCR   # Windows
```

You only need it if you want OCR. It's looked for when you call `pdf_ocr`, not at
startup, so every other tool works without it and installing it later needs no
reinstall or restart. Until it's there you get an error saying so, with the command
for your platform and a pointer to `pdf_render_pages` in the meantime. If yours lives
somewhere unusual, set `BENSPDF_TESSERACT` to its full path.

Languages are installed separately. `lang="deu"` on a machine with only English gives
you an error naming the languages you do have, rather than quietly reading German as
English and handing back plausible nonsense. `brew install tesseract-lang` adds all of
them; on Debian it's one package per language, like `tesseract-ocr-deu`. Several at
once is `lang="eng+deu"`.

### How much to trust it

On clean printed text it's accurate enough to read and quote. Faint photocopies and
phone photos come back weaker, and handwriting, signatures, text inside charts and
mathematical notation are beyond it — for those, look at the page with
`pdf_render_pages`.

Check `mean_confidence`, out of 100, alongside `low_confidence_words` out of `words`.
High nineties is normal for printed text. It's the number that tells you when to look
at the page yourself, because unreliable OCR still reads fluently: a page scanned in
upside down comes back as confident-sounding nonsense with a confidence score in the
forties.

`dpi` defaults to 200, the sweet spot for printed text. Raise it when small print
comes back badly; higher settings mostly buy time rather than accuracy.

A page stored sideways is read normally, and its text comes back with the rest.
Placing a layer on it needs the page upright first, so it's listed in
`text_layer_skipped`.

### Pages it skips

A page that already has text is left as it is and listed in `skipped` with how much
text it holds, since its text can be read directly. `force=True` OCRs it anyway,
which is what you want when the text that's there came out of some other OCR as
gibberish — `pdf_check_text` shows you the difference.

A page that fails is listed in `failed` with its reason, and the rest of the document
comes back as usual.

### Long documents

Most documents are one call. A call reads up to 50 pages and spends up to 40 seconds
recognizing, whichever runs out first, then hands back what it has along with the
pages it did not reach — so a long or slow scan comes back in a few rounds rather
than in one wait your MCP client won't sit through. Pages vary a lot: a clean form
page is quick, a dense or noisy scan can take ten times as long.

When a call stops early you get `truncated: True`, `pages_remaining` naming what is
left, and a summary spelling out the next call. Each call adds its text layer to the
copy you hand it, so continue by passing the previous result back in and the layers
accumulate in one document:

```python
first = ocr("book.pdf", pages="all", output="pdf")
first["pages_remaining"]                  # '38-90'
second = ocr(first["path"], pages="38-90", output="pdf")
second["pages_remaining"]                 # '81-90'
third = ocr(second["path"], pages="81-90", output="pdf")
# third["artifact"] is book.pdf with all 90 pages searchable
```

Over MCP you pass the artifact id along instead of the path, and the model can run
the loop itself. Ranges may overlap: a page that already has its layer is skipped,
so `pages="30-90"` in the second call would have worked just as well.

## `create_test_pdf_file(output_path=None, num_pages=3, title=None)`

Generates a PDF so you can try the other tools without hunting for a file. It
stays in the scratch folder unless you give it `output_path`.

```python
{'success': True, 'artifact': 'art_55aceb59.pdf', 'num_pages': 3}
```

From Python, `create_test_pdf` writes straight to a path:

```python
from benspdf import create_test_pdf

create_test_pdf("demo.pdf", num_pages=5, title="Demo")
# '/absolute/path/to/demo.pdf'
```

## `export(refs, dest, name=None, overwrite=False)`

Saves one or more results where you want them. For a single result, `dest` can be
a full file path. For several, it's a folder, and `name` sets the filenames, e.g.
`"page_{n:03d}{ext}"`.

Existing files are never replaced unless you pass `overwrite=True`.

## `list_artifacts(limit=20)`

Recent scratch results, newest first, with their size and age.

## `discard(refs)`

Deletes scratch results now. Only takes ids, never file paths, so it can't remove
your own files.
