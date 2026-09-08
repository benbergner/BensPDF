# BensPDF

**Local PDF tools for AI agents - MCP-powered and privacy-focused.**

A collection of clean, simple tools for processing PDF files locally. Built on the Model Context Protocol (MCP) for universal compatibility with Claude Desktop, OpenAI, Cursor, Ollama, and any MCP-compatible client.

## Features

- **MCP Protocol**: Universal standard - works with Claude Desktop, OpenAI, Cursor, and more
- **Local Processing**: All PDF processing happens on your machine - no data sent to external APIs
- **Privacy-Focused**: Perfect for sensitive documents
- **Simple**: Easy to understand, easy to extend

## Installation

If you just want to use the tools in an MCP client, you don't need to install anything. Install [uv](https://docs.astral.sh/uv/getting-started/installation/) once (`brew install uv`), point your client at `uvx benspdf-mcp`, and uv fetches the package and a suitable Python on first run. See the client sections below.

For development:

```bash
git clone https://github.com/yourusername/benspdf.git
cd benspdf

conda create -n benspdf python=3.11
conda activate benspdf
pip install -e .
```

## Quick Start

### Interactive CLI with Ollama (Recommended for Local Processing)

```bash
# Uses the first model you have installed
python mcp_cli.py

# Or pick one explicitly
python mcp_cli.py --model llama3.1
BENSPDF_MODEL=llama3.1 python mcp_cli.py
```

Example session:
```
You: How many pages are in ~/Downloads/cv_bbergner.pdf?
[Using tool: count_pdf_pages]
[Result: 2 pages in cv_bbergner.pdf]
Assistant: The PDF /Users/bbergner/Downloads/cv_bbergner.pdf has 2 pages.
```

### With Claude Desktop

Add to your Claude Desktop configuration (`~/Library/Application Support/Claude/claude_desktop_config.json`):

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

Then restart Claude Desktop and ask: "How many pages are in document.pdf?"

### With Kiro (or Cursor / Continue.dev)

Any IDE that speaks MCP over stdio works with no hosting or tunnel. For Kiro, add `.kiro/settings/mcp.json` in your workspace (or `~/.kiro/settings/mcp.json` to enable it everywhere):

```json
{
  "mcpServers": {
    "benspdf": {
      "command": "uvx",
      "args": ["benspdf-mcp"],
      "disabled": false,
      "autoApprove": ["count_pdf_pages"]
    }
  }
}
```

Then ask in chat: "How many pages are in ~/Downloads/report.pdf?"

Pin a version with `["benspdf-mcp@0.1.0"]`. If you're working from a clone instead, use `"command": "python", "args": ["-m", "benspdf.mcp_server"]` with the full path to your environment's Python, since a bare `python` resolves through `PATH` and may not be the environment you installed into.

### With the ChatGPT desktop app (or Codex CLI / IDE extension)

These read a local Codex config and support stdio servers, so no hosting or tunnel is needed. Note the format is TOML, not JSON.

In the desktop app: Settings → MCP servers → Add server → STDIO → enter the command → Save → Restart. Type `/mcp` in the composer to list connected servers.

Or edit `~/.codex/config.toml` (project-scoped `.codex/config.toml` also works for trusted projects):

```toml
[mcp_servers.benspdf]
command = "uvx"
args = ["benspdf-mcp"]
```

The ChatGPT desktop app, Codex CLI, and Codex IDE extension share this configuration, so one entry covers all three.

### With ChatGPT in the browser (chatgpt.com)

The web UI does not read local config and only connects to MCP servers over public HTTPS. It requires a paid plan, Developer Mode (Settings → Apps & Connectors → Advanced Settings), and a custom connector pointing at an HTTPS `/mcp` endpoint — which means hosting the server or tunnelling it (OpenAI's Secure MCP Tunnel, ngrok, or Cloudflare Tunnel).

Since BensPDF reads files from the machine the server runs on, prefer the desktop app or any other local client instead.

### With OpenAI API

```python
from openai import OpenAI
import asyncio
from benspdf.mcp_client import MCPClient

async def main():
    client = OpenAI()
    
    # Connect to MCP server
    async with MCPClient() as mcp:
        # Get tools in OpenAI format
        tools = []
        for tool in mcp.tools:
            tools.append({
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.input_schema
                }
            })
        
        # Use with OpenAI
        response = client.chat.completions.create(
            model="gpt-4",
            messages=[{"role": "user", "content": "How many pages in doc.pdf?"}],
            tools=tools
        )

asyncio.run(main())
```

### Direct Python Usage

```python
from benspdf import PDFPageCounterTool, create_test_pdf

# Create tool
tool = PDFPageCounterTool()

# Count pages
result = tool("document.pdf")
print(f"Pages: {result['page_count']}")

# Create test PDF
test_pdf = create_test_pdf("test.pdf", num_pages=5)
```

## MCP Integration

BensPDF exposes tools via MCP (Model Context Protocol), the universal standard for AI agent tool integration.

**Supported Clients:**
- ✅ Claude Desktop
- ✅ ChatGPT desktop app, Codex CLI, Codex IDE extension (local stdio)
- ✅ ChatGPT web (requires a hosted HTTPS endpoint)
- ✅ Kiro
- ✅ Cursor IDE
- ✅ Continue.dev
- ✅ Ollama (via mcp_cli.py)
- ✅ Any MCP-compatible client

**Available Tools:**

1. `count_pdf_pages` - Count pages in a PDF
2. `create_test_pdf_file` - Create test PDFs

## Architecture

```
┌─────────────────┐
│   AI Client     │ (Claude Desktop, Ollama, Cursor, etc.)
│                 │
└────────┬────────┘
         │ MCP Protocol
         ▼
┌─────────────────┐
│  BensPDF MCP    │
│     Server      │
│                 │
│  - count_pages  │
│  - create_pdf   │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  PDFPageCounter │
│      Tool       │
│                 │
│  Local PDF      │
│  Processing     │
└─────────────────┘
```

## Why MCP?

**Universal Compatibility**: One integration works with all MCP clients - no need for framework-specific adapters.

**Future-Proof**: MCP is becoming the standard for tool integration across AI platforms.

**Privacy**: All processing happens locally through the MCP server.

## Development

### Running Tests

```bash
# Run all tests
python -m pytest tests/ -v

# Run specific tests
python -m pytest tests/test_mcp_server.py -v
```

### Project Structure

```
benspdf/
├── src/benspdf/
│   ├── pdf_tools.py        # Core PDF tool
│   ├── mcp_server.py       # MCP server
│   ├── mcp_client.py       # MCP client
│   └── utils.py            # Utilities
├── tests/
│   ├── test_pdf_page_counter.py
│   └── test_mcp_server.py
├── mcp_cli.py              # Interactive CLI
└── README.md
```

## Examples

### Example 1: Count pages in a PDF

```bash
# Via CLI
python mcp_cli.py
> How many pages in document.pdf?
```

### Example 2: Create test PDFs

```python
from benspdf import create_test_pdf

# Create 5-page test PDF
pdf = create_test_pdf("test.pdf", num_pages=5, title="Test Document")
```

### Example 3: Use with MCP client

```python
from benspdf.mcp_client import MCPClient
import asyncio

async def main():
    async with MCPClient() as client:
        result = await client.call_tool('count_pdf_pages', {
            'pdf_path': 'document.pdf'
        })
        print(f"Pages: {result['page_count']}")

asyncio.run(main())
```

### Example 4: Your own fully-local agent loop

Nothing leaves your machine: the model runs in Ollama, the MCP server runs as a
local subprocess, and the PDF is read on disk.

```python
import asyncio, ollama
from benspdf.mcp_client import MCPClient, resolve_model

async def main():
    installed = [m['model'] for m in ollama.list()['models']]
    model = resolve_model(None, installed)  # or resolve_model("llama3.1", installed)

    async with MCPClient() as client:
        tools = [
            {"type": "function", "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.input_schema,
            }}
            for t in client.tools
        ]

        reply = ollama.chat(
            model=model,
            messages=[{"role": "user", "content": "How many pages in document.pdf?"}],
            tools=tools,
        )

        for call in reply["message"].get("tool_calls", []):
            result = await client.call_tool(
                call["function"]["name"], call["function"]["arguments"]
            )
            print(result)

asyncio.run(main())
```

See `mcp_cli.py` for the same loop with conversation history and error handling.

## License

MIT

## Contributing

Contributions welcome! BensPDF is designed to be simple and extensible. Feel free to add more PDF tools following the same pattern.
