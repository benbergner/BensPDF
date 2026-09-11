# BensPDF

Ben's PDF tools for AI agents, exposed over the [Model Context Protocol](https://modelcontextprotocol.io) (MCP).

Your PDFs are read on your own machine and never uploaded. Works with Claude
Desktop, VS Code, Kiro, Cursor, the ChatGPT desktop app, and any other MCP
client, or fully offline with a local Ollama model.

## Tools

| Tool | What it does |
| --- | --- |
| `pdf_page_count` | Counts the pages in a PDF |
| `pdf_metadata` | Reads document properties: title, author, dates, producer |
| `pdf_check_text` | Says whether a PDF is readable text or a scan that needs OCR |
| `pdf_page_layout` | Page sizes, orientation, rotation and page boxes |
| `pdf_check_access` | Encryption, and what the file permits: printing, copying, editing |
| `create_test_pdf_file` | Generates a throwaway PDF, handy for trying things out |
| `export` | Saves results to a real location on disk |
| `list_artifacts` | Lists recent temporary results |
| `discard` | Deletes temporary results now |

## Where results go

When a tool makes a new PDF, it goes into a scratch folder instead of your own
folders, and you get back a short id like `art_a1b2c3d4.pdf`. Tools accept those
ids anywhere they accept a file path, so several steps can be chained together.

`export` is the only tool that writes into your folders, so nothing shows up
until you ask for it. Each time the server starts it clears out scratch files
older than 7 days. Set `BENSPDF_WORKSPACE` to put the scratch folder somewhere
other than `~/.benspdf/work`.

## Setup

Install [uv](https://docs.astral.sh/uv/getting-started/installation/)
once, then point your client at `uvx benspdf-mcp` and uv fetches the package,
plus a suitable Python, on first run.

```bash
# macOS and Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

On macOS, `brew install uv` works too.

Add the server to your client with one of the configs below, then restart the
server from your client's UI. Once connected, just ask in plain language:

> How many pages are in ~/Downloads/report.pdf?

### Claude Desktop

Edit `claude_desktop_config.json`, which lives at
`~/Library/Application Support/Claude/` on macOS and `%APPDATA%\Claude\` on
Windows. You can also open it from **Settings → Developer → Edit Config**.

```json
{
  "mcpServers": {
    "benspdf": {
      "command": "uvx",
      "args": ["benspdf-mcp"]
    }
  }
}
```

### VS Code

`.vscode/mcp.json` in your workspace, or the same file in your user profile.
Note VS Code uses `servers` rather than `mcpServers`, and wants an explicit
`type`.

```json
{
  "servers": {
    "benspdf": {
      "type": "stdio",
      "command": "uvx",
      "args": ["benspdf-mcp"]
    }
  }
}
```

### Kiro

`.kiro/settings/mcp.json` in your workspace, or `~/.kiro/settings/mcp.json` to
enable it everywhere. `autoApprove` skips the confirmation prompt for tools you
trust.

```json
{
  "mcpServers": {
    "benspdf": {
      "command": "uvx",
      "args": ["benspdf-mcp"],
      "autoApprove": ["pdf_page_count"]
    }
  }
}
```

### Cursor, Continue.dev, and most other clients

Same shape as the Claude Desktop config above, in whatever file the client uses.

### ChatGPT desktop app, Codex CLI, Codex IDE extension

All three are Codex clients and share one config file, `~/.codex/config.toml`,
so adding the server once covers all of them.

```toml
[mcp_servers.benspdf]
command = "uvx"
args = ["benspdf-mcp"]
```

## Fully offline with Ollama

The clients above keep your PDFs local, but they answer using a hosted model.
Pair the tools with a local model instead and nothing leaves your machine.

You'll need [Ollama](https://ollama.com) with a model pulled, plus this package:

```bash
pip install benspdf-mcp ollama
ollama pull llama3.1
```

Then run the bundled CLI:

```bash
benspdf-cli                        # uses the first model you have
benspdf-cli --model llama3.1       # or pick one
BENSPDF_MODEL=llama3.1 benspdf-cli # or set it once
```

`python -m benspdf.cli` does the same thing, handy from a source checkout.

```
You: how many pages in ~/Downloads/report.pdf?
[Using tool: pdf_page_count]
[Result: 12 pages in report.pdf]
Assistant: The PDF has 12 pages.
```

## The tools in detail

### `pdf_page_count(ref)`

Counts pages. Accepts a file path (`~` is expanded, relative paths are resolved)
or an artifact id from an earlier tool.

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

### `pdf_metadata(ref)`

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

### `pdf_check_text(ref)`

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
any text too, and it is not a scan. Coverage is measured from the page's
transformation matrices, so no image data is decoded.

Long documents are sampled — up to 10 pages spread across the file, ends
included — which keeps a 2000-page scan as cheap as a short one and makes the
answer an estimate. `sampled` tells you when that happened, and `pages` lists
what was actually read.

`text_excerpt` is there for the case a character count can't catch: text that
exists but came out of OCR as gibberish.

### `pdf_check_access(ref)`

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

`encrypted` and `needs_password` are deliberately separate. Encryption sounds like
a locked door, and usually isn't one: of 141 files here, 8 were encrypted and none
needed a password. They open silently and simply carry restrictions.

Those restrictions are a request to viewers, not a lock. Six of those 8 files
declare that text may not be copied, and `pdf_check_text` reads their text without
resistance. So the summary states the restriction and its advisory nature
together, and it's worth passing that on rather than telling someone an action is
impossible.

`permissions_valid` is `None` unless there was a real check to run. Only AES-256
stores a signed copy of the permission bits; pypdf reports `True` for weaker
encryption, meaning "nothing to verify", which is not the same as verified.

This is also the one tool that still answers for a password-protected file. The
encryption dictionary isn't itself encrypted, so permissions are readable even
when the pages aren't — where the other tools can only report the encryption and
stop.

### `pdf_page_layout(ref, pages=None)`

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

### `create_test_pdf_file(output_path=None, num_pages=3, title=None)`

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

### `export(refs, dest, name=None, overwrite=False)`

Saves one or more results where you want them. For a single result, `dest` can be
a full file path. For several, it's a folder, and `name` sets the filenames, e.g.
`"page_{n:03d}{ext}"`.

Existing files are never replaced unless you pass `overwrite=True`.

### `list_artifacts(limit=20)`

Recent scratch results, newest first, with their size and age.

### `discard(refs)`

Deletes scratch results now. Only takes ids, never file paths, so it can't remove
your own files.

## Development

```bash
git clone https://github.com/benbergner/BensPDF.git
cd BensPDF

conda env create -f environment.yml
conda activate benspdf
pip install -e .

python -m pytest tests/ -v
```

The package is laid out as the server (`mcp_server.py`), one module per tool
under `tools/`, the shared artifact layer (`core/`), a client that talks to the
server over stdio (`mcp_client.py`), the Ollama chat CLI built on that client
(`cli.py`), and model selection (`models.py`).

Adding a tool means two things:

1. The logic goes in `src/benspdf/tools/<verb>.py`, named after the verb, and its
   public name gets listed in `src/benspdf/__init__.py`.
2. A thin wrapper in `src/benspdf/mcp_server.py`, decorated with `@mcp.tool()`,
   resolves the `ref` and calls it.

Keeping those separate means the logic is testable without going through MCP. The
wrapper's type hints and docstring become the schema the model sees, so the
docstring is worth writing carefully. `pdf_metadata` over `tools/metadata.py` is
the pattern to copy. Restart the server in your client afterwards to pick up the
change.

Descriptions are context the model pays for on every turn, so keep them to what
is specific to the tool: the question it answers, what it does not do and what to
use instead, and any field whose name doesn't explain it. Skip the list of
returned fields — results are self-describing dicts. Anything shared by all tools
goes in `INSTRUCTIONS` in `mcp_server.py`, which the server sends once.

If a tool produces a file, use the helpers in `src/benspdf/core/` instead of
writing to disk yourself:

- `core.resolve(ref)` takes a file path or a scratch id and gives you a path to read
- `core.save(data, ".pdf")` stores a result and returns its id
- `core.ok(...)` and `core.err(...)` keep the result shape the same across tools

`create_test_pdf_file` in `mcp_server.py` is a short working example.

## License

Apache 2.0
