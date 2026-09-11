#!/usr/bin/env python
"""
BensPDF MCP CLI - Chat with Ollama using MCP tools

This provides a CLI interface that uses MCP protocol for tool access.

Run it as `benspdf-cli` once the package is installed, or with
`python -m benspdf.cli` from a source checkout.
"""

import argparse
import asyncio
import json
import sys

from benspdf.models import MODEL_ENV_VAR, resolve_model


async def run_chat(requested_model=None):
    print("=" * 70)
    print("BensPDF MCP CLI - Chat with Ollama + MCP Tools")
    print("=" * 70)
    print()
    
    # Check if Ollama is available
    try:
        import ollama
        models = ollama.list()
        available = [m['model'] for m in models['models']]
        print("✓ Ollama connected")
        print(f"  Available models: {available}")
    except ImportError:
        print("❌ ollama package not installed")
        print("   Install with: pip install ollama")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Cannot connect to Ollama: {e}")
        print("   Make sure Ollama is running: ollama serve")
        sys.exit(1)

    try:
        model = resolve_model(requested_model, available)
    except ValueError as e:
        sys.exit(str(e))
    print(f"  Using model: {model}")
    print()
    
    # Import MCP client
    from benspdf.mcp_client import MCPClient, MCPToolError
    
    # Connect to MCP server
    print("Connecting to MCP server...")
    async with MCPClient() as mcp_client:
        print()
        print("Available tools:")
        for tool in mcp_client.tools:
            # First line only, and stripped: a docstring that opens on the line
            # after its quotes would otherwise print as blank.
            summary = (tool.description or "").strip().splitlines()
            print(f"  - {tool.name}: {summary[0] if summary else ''}")
        print()
        
        print("Commands:")
        print("  - Type your question (e.g., 'How many pages in file.pdf?')")
        print("  - 'quit' or 'exit' - Exit the CLI")
        print()
        print("-" * 70)
        
        # Build tools schema for Ollama
        tools_schema = []
        for tool in mcp_client.tools:
            tools_schema.append({
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.input_schema
                }
            })
        
        # Chat history with system prompt
        messages = [{
            "role": "system",
            "content": """You are a helpful assistant with access to PDF tools. When a user asks about PDF files:

1. Use the available tools to get the information
2. After receiving tool results, provide a direct, concise answer
3. Don't explain how you got the information or provide code examples
4. Just answer the user's question directly

For example:
- User: "How many pages in file.pdf?"
- You: [Use pdf_page_count tool]
- You: "The PDF file.pdf has X pages."

Be direct and helpful."""
        }]
        
        while True:
            try:
                # Get user input
                user_input = input("\nYou: ").strip()
                
                if not user_input:
                    continue
                
                # Handle special commands
                if user_input.lower() in ['quit', 'exit', 'q']:
                    print("\nGoodbye!")
                    break
                
                # Send to Ollama
                messages.append({"role": "user", "content": user_input})
                
                response = ollama.chat(
                    model=model,
                    messages=messages,
                    tools=tools_schema
                )
                
                # Check if model wants to use tool
                if response.get("message", {}).get("tool_calls"):
                    # Add assistant message
                    messages.append(response["message"])
                    
                    # Execute each tool call via MCP
                    tool_results = []
                    for call in response["message"]["tool_calls"]:
                        tool_name = call["function"]["name"]
                        arguments = call["function"]["arguments"]
                        
                        print(f"\n[Using tool: {tool_name}]")
                        
                        # Execute tool via MCP. A failed call is reported back
                        # to the model rather than ending the turn, so it can
                        # explain itself or try again.
                        try:
                            result = await mcp_client.call_tool(tool_name, arguments)
                        except MCPToolError as e:
                            result = {"success": False, "error": str(e)}
                        tool_results.append(result)
                        
                        if result.get("success") or "page_count" in result:
                            if "page_count" in result:
                                print(f"[Result: {result['page_count']} pages in {result['file_name']}]")
                            else:
                                print(f"[Result: {result}]")
                        else:
                            print(f"[Error: {result.get('error', result)}]")
                        
                        # Send result back to model with clear instruction
                        messages.append({
                            "role": "tool",
                            "content": json.dumps(result)
                        })
                    
                    # Get final response with clear context
                    messages.append({
                        "role": "user",
                        "content": "Based on the tool result above, please provide a direct answer to my original question."
                    })
                    
                    # Get final response
                    final_response = ollama.chat(
                        model=model,
                        messages=messages
                    )
                    
                    print(f"\nAssistant: {final_response['message']['content']}")
                    messages.append(final_response["message"])
                
                else:
                    # Model responded directly
                    content = response.get("message", {}).get("content", "")
                    print(f"\nAssistant: {content}")
                    messages.append(response["message"])
            
            except KeyboardInterrupt:
                print("\n\nGoodbye!")
                break
            except Exception as e:
                print(f"\nError: {e}")
                import traceback
                traceback.print_exc()

def main() -> None:
    """Parse arguments and start the chat loop. Used by the console script."""
    parser = argparse.ArgumentParser(
        prog="benspdf-cli",
        description="Chat with a local Ollama model that can call BensPDF's MCP tools.",
    )
    parser.add_argument(
        "--model",
        help=(
            "Ollama model to use (e.g. llama3.1). Defaults to "
            f"${MODEL_ENV_VAR}, or the first installed model."
        ),
    )
    args = parser.parse_args()
    asyncio.run(run_chat(args.model))


if __name__ == "__main__":
    main()
