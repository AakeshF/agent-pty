"""Unit-level tests for the MCP wrapper.

Verifies that all expected tools are registered with the MCP server.
The tools delegate to Pty.* which has its own integration coverage,
so we don't re-test the underlying behavior here — just the wrapper.
"""

import asyncio

import pytest

from agent_pty.mcp import mcp


EXPECTED_TOOLS = {
    "pty_spawn",
    "pty_send",
    "pty_snapshot",
    "pty_wait_for",
    "pty_list",
    "pty_kill",
}


def _list_tools():
    return asyncio.run(mcp.list_tools())


def test_all_expected_tools_registered():
    tools = _list_tools()
    names = {t.name for t in tools}
    missing = EXPECTED_TOOLS - names
    assert not missing, f"missing core pty tools: {missing}"


def test_each_tool_has_description():
    tools = _list_tools()
    for tool in tools:
        assert tool.description, f"{tool.name} missing description"
        assert len(tool.description) > 30, f"{tool.name} description too brief"


def test_spawn_tool_schema_marks_optional_params():
    tools = {t.name: t for t in _list_tools()}
    schema = tools["pty_spawn"].inputSchema
    required = set(schema.get("required", []))
    assert "name" in required
    assert "cmd" not in required
    assert "cwd" not in required


def test_wait_for_tool_has_timeout_default():
    tools = {t.name: t for t in _list_tools()}
    schema = tools["pty_wait_for"].inputSchema
    props = schema["properties"]
    assert props["timeout"].get("default") == 10.0


@pytest.mark.parametrize(
    "tool_name,required_param",
    [
        ("pty_send", "text"),
        ("pty_snapshot", "name"),
        ("pty_wait_for", "pattern"),
        ("pty_kill", "name"),
    ],
)
def test_tool_required_params(tool_name, required_param):
    tools = {t.name: t for t in _list_tools()}
    schema = tools[tool_name].inputSchema
    assert required_param in schema.get("required", [])
