<!-- mcp-name: io.github.benbergner/benspdf -->

<div align="center">

<h1>BensPDF</h1>

<p><strong>PDF tools for AI agents. Your files never leave your machine.</strong></p>

<p>
<a href="https://pypi.org/project/benspdf-mcp/"><img src="https://img.shields.io/pypi/v/benspdf-mcp?style=flat-square&logo=pypi&logoColor=white&label=PyPI" alt="PyPI"></a>
<a href="https://pypi.org/project/benspdf-mcp/"><img src="https://img.shields.io/pypi/pyversions/benspdf-mcp?style=flat-square&logo=python&logoColor=white" alt="Python versions"></a>
<a href="https://github.com/benbergner/BensPDF/blob/main/LICENSE"><img src="https://img.shields.io/pypi/l/benspdf-mcp?style=flat-square" alt="License"></a>
<a href="https://registry.modelcontextprotocol.io/v0/servers?search=benspdf"><img src="https://img.shields.io/badge/MCP-registry-1f6feb?style=flat-square" alt="MCP registry"></a>
</p>

<p><strong>Install in one click</strong></p>

<p>
<a href="https://vscode.dev/redirect/mcp/install?name=benspdf&config=%7B%22type%22%3A%22stdio%22%2C%22command%22%3A%22uvx%22%2C%22args%22%3A%5B%22benspdf-mcp%22%5D%7D"><img src="https://img.shields.io/badge/VS_Code-Install-0098FF?style=for-the-badge&logo=visualstudiocode&logoColor=white" alt="Install in VS Code"></a>
&nbsp;
<a href="https://cursor.com/en/install-mcp?name=benspdf&config=eyJuYW1lIjoiYmVuc3BkZiIsImNvbW1hbmQiOiJ1dngiLCJhcmdzIjpbImJlbnNwZGYtbWNwIl19"><img src="https://img.shields.io/badge/Cursor-Install-000000?style=for-the-badge&logo=cursor&logoColor=white" alt="Install in Cursor"></a>
</p>

</div>

---

Ben's PDF tools for AI agents, exposed over the [Model Context Protocol](https://modelcontextprotocol.io) (MCP).

> [!NOTE]
> Your PDFs are read on your own machine and never uploaded. With a hosted model
> your questions still reach that model; pair the tools with a local Ollama model
> and nothing leaves the machine at all.

Works with Claude Desktop, Claude Code, VS Code, Kiro, Cursor, the ChatGPT
desktop app, and any other MCP client. The one-click buttons above need
[uv](https://docs.astral.sh/uv/getting-started/installation/); for every other
client, or to do it by hand, see [Setup](#setup).

## Tools

| Tool | What it does |
| --- | --- |
| `pdf_page_count` | Counts the pages in a PDF |
| `pdf_metadata` | Reads document properties: title, author, dates, producer |
| `pdf_check_text` | Says whether a PDF is readable text or a scan that needs OCR |
| `pdf_page_layout` | Page sizes, orientation, rotation and page boxes |
| `pdf_check_access` | Encryption, and what the file permits: printing, copying, editing |
| `pdf_render_pages` | Renders pages to images, so a page can be looked at |
| `create_test_pdf_file` | Generates a throwaway PDF, handy for trying things out |
| `export` | Saves results to a real location on disk |
| `list_artifacts` | Lists recent temporary results |
| `discard` | Deletes temporary results now |

## Where results go

When a tool makes a new PDF, it goes into a scratch folder instead of your own
folders, and you get back a short id like `art_a1b2c3d4.pdf`. Tools accept those
ids anywhere they accept a file path, so several steps can be chained together.

Results carry the artifact's `path` as well as its id, so you can open a rendered
page or an intermediate file straight away without exporting it first.

`export` is the only tool that writes into your folders, so nothing shows up
until you ask for it. Each time the server starts it clears out scratch files
older than 7 days. Set `BENSTOOLS_WORKSPACE` to put the scratch folder somewhere
other than `~/.benstools/work`.

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

### Claude Code

One command, no config file. Add `--scope user` to enable it everywhere rather
than just the current project.

```bash
claude mcp add benspdf -- uvx benspdf-mcp
```

Check it connected with `claude mcp list`.

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

## Reference

Each tool's own description tells your client what it does and when to use it, so
in normal use there is nothing to look up. If you want the detail — every field a
tool returns, and the reasoning behind the answers it gives — see the
[tool reference](https://github.com/benbergner/BensPDF/blob/main/docs/tools.md).

To work on the code, see
[CONTRIBUTING.md](https://github.com/benbergner/BensPDF/blob/main/CONTRIBUTING.md).

## License

Apache 2.0
