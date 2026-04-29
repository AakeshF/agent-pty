"""End-to-end MCP smoke: launches agent-pty-mcp as a subprocess, talks to it
over stdio JSON-RPC the same way Claude Code would, exercises a real flow."""
import asyncio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main():
    params = StdioServerParameters(
        command="/home/aakeshf/projects/agent-pty/.venv/bin/agent-pty-mcp",
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as sess:
            await sess.initialize()
            tools = await sess.list_tools()
            print(f"server reports {len(tools.tools)} tools: "
                  f"{sorted(t.name for t in tools.tools)}")

            r = await sess.call_tool("pty_spawn", {
                "name": "smoke",
                "cmd": "bash --norc --noprofile",
            })
            print(f"spawn → {r.content[0].text!r}")

            await sess.call_tool("pty_send", {
                "name": "smoke",
                "text": "echo mcp-smoke-marker\n",
            })

            r = await sess.call_tool("pty_wait_for", {
                "name": "smoke",
                "pattern": "mcp-smoke-marker",
                "timeout": 3.0,
            })
            assert "mcp-smoke-marker" in r.content[0].text
            print("wait_for matched ✓")

            r = await sess.call_tool("pty_list", {})
            print(f"list → {r.content[0].text!r}")

            await sess.call_tool("pty_kill", {"name": "smoke"})
            print("kill ✓")

            print("\nMCP server smoke test passed.")


asyncio.run(main())
