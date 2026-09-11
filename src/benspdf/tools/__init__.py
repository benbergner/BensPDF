"""
One module per tool, named after the verb it implements.

`metadata.py` holds `pdf_metadata`, `page_count.py` holds `pdf_page_count`,
`create_test_pdf.py` holds `create_test_pdf_file`. The modules here own the actual
work; the `@mcp.tool()` functions in `mcp_server.py` stay thin wrappers that
resolve a `ref` and call in, so the logic is testable without going through MCP.

Module names drop the `pdf_` prefix that most tool names carry — `tools.metadata`
already says it. The prefix exists on the MCP surface for retrieval, where the
selector only sees a flat list of names, and that argument doesn't apply to an
import path.

Nothing is re-exported here. Public names are listed once, in the top level
`benspdf` package, so adding a verb means editing one place rather than two.
"""
