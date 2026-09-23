"""
nlm-research-mcp — the MCP server entry point.

By default this runs the **bundled, stdlib-only** stdio MCP server
(`_tools.build_stdio_server`) — no third-party dependency, so `pip install .` gives a
runnable server that fully loads on its own without linking any external MCP repo.

If the official `mcp` SDK is installed you may opt into it with `--sdk`; that path
tolerates both the 1.x API (`FastMCP`) and the 2.x API (`MCPServer`). Either way the
same 13 tools from `_tools.py` are exposed.
"""
from __future__ import annotations

import sys

from ._tools import ToolLogic, build_stdio_server, _BINDINGS, _DESCRIPTIONS


def build_server():
    """Return the bundled stdlib stdio server with all tools registered."""
    return build_stdio_server()


def _try_build_sdk_server():
    """Best-effort: bind the same tools onto the official SDK if present.
    Returns a runnable object or raises SystemExit with a clear message."""
    logic = ToolLogic()
    try:
        from mcp.server.fastmcp import FastMCP  # mcp 1.x
        mcp = FastMCP("notelab")
        for public_name, method_name in _BINDINGS.items():
            mcp.tool(name=public_name, description=_DESCRIPTIONS[public_name])(
                getattr(logic, method_name))
        return mcp
    except ModuleNotFoundError:
        pass
    try:
        from mcp.server.mcpserver import MCPServer  # mcp 2.x
        mcp = MCPServer("notelab")
        for public_name, method_name in _BINDINGS.items():
            mcp.tool(name=public_name, description=_DESCRIPTIONS[public_name])(
                getattr(logic, method_name))
        return mcp
    except ModuleNotFoundError as e:
        raise SystemExit(
            "The official MCP SDK is not installed (tried mcp 1.x FastMCP and 2.x "
            "MCPServer). Run without --sdk to use the bundled stdlib server, or "
            "`pip install 'nlm-research-mcp[mcp]'`."
        ) from e


def main():  # pragma: no cover
    if "--sdk" in sys.argv[1:]:
        _try_build_sdk_server().run()
    else:
        build_stdio_server().serve_forever()


if __name__ == "__main__":  # pragma: no cover
    main()
