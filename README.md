# BensPDF

Ben's PDF tools for AI agents, exposed over the [Model Context Protocol](https://modelcontextprotocol.io) (MCP).

Your PDFs are read on your own machine and never uploaded. Works with Claude
Desktop, VS Code, Kiro, Cursor, the ChatGPT desktop app, and any other MCP
client, or fully offline with a local Ollama model.

## Tools

| Tool | What it does |
| --- | --- |
| `count_pdf_pages` | Counts the pages in a PDF |
| `create_test_pdf_file` | Generates a throwaway PDF, handy for trying things out |

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
      "autoApprove": ["count_pdf_pages"]
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
python mcp_cli.py                        # uses the first model you have
python mcp_cli.py --model llama3.1       # or pick one
BENSPDF_MODEL=llama3.1 python mcp_cli.py # or set it once
```

```
You: how many pages in ~/Downloads/report.pdf?
[Using tool: count_pdf_pages]
[Result: 12 pages in report.pdf]
Assistant: The PDF has 12 pages.
```

## The tools in detail

### `count_pdf_pages(pdf_path)`

Counts pages. `~` is expanded and relative paths are resolved.

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

### `create_test_pdf_file(output_path, num_pages=3, title=None)`

Generates a PDF with numbered pages, so you can try the other tools without
hunting for a file.

```python
from benspdf import create_test_pdf

create_test_pdf("demo.pdf", num_pages=5, title="Demo")
# '/absolute/path/to/demo.pdf'
```

## Development

```bash
git clone https://github.com/benbergner/BensPDF.git
cd BensPDF

conda env create -f environment.yml
conda activate benspdf
pip install -e .

python -m pytest tests/ -v
```

Adding a tool means writing a function and decorating it with `@mcp.tool()` in
`src/benspdf/mcp_server.py`. Its type hints and docstring become the schema the
model sees, so the docstring is worth writing carefully. Restart the server in
your client afterwards to pick up the change.

## License

Apache 2.0
