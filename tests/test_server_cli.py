"""server.py's main() argument handling -- --help must print usage and return,
not silently fall through to starting the server. Without this, `nlm-research-mcp
--help` produces no output and hangs waiting on stdin, because the only arg check
was for --sdk."""
import sys
import types

import pytest

from notelab import server
from notelab._tools import _BINDINGS, _DESCRIPTIONS

try:
    import mcp  # noqa: F401
    _HAS_MCP = True
except ImportError:
    _HAS_MCP = False


def test_help_prints_usage_and_returns(capsys):
    import sys
    old_argv = sys.argv
    try:
        sys.argv = ["nlm-research-mcp", "--help"]
        server.main()
    finally:
        sys.argv = old_argv
    out = capsys.readouterr().out
    assert "usage: nlm-research-mcp" in out
    assert "--sdk" in out


def test_short_help_flag_also_works(capsys):
    import sys
    old_argv = sys.argv
    try:
        sys.argv = ["nlm-research-mcp", "-h"]
        server.main()
    finally:
        sys.argv = old_argv
    out = capsys.readouterr().out
    assert "usage: nlm-research-mcp" in out


def test_build_server_returns_bundled_stdio():
    from notelab._tools import tool_names
    from notelab.mcp_stdio import StdioMCPServer
    srv = server.build_server()
    assert isinstance(srv, StdioMCPServer)
    assert len(srv.tools) == len(tool_names())
    assert "research_export" in srv.tools


class _FakeSdkApp:
    """Stand-in for FastMCP/MCPServer: records tool bindings."""

    def __init__(self, name):
        self.name = name
        self.bound = {}

    def tool(self, name=None, description=None):
        def deco(fn):
            self.bound[name] = (description, fn)
            return fn

        return deco


def _stub_sdk(monkeypatch, leaf):
    """Pretend the official SDK is installed, exposing `leaf` (1.x FastMCP or
    2.x MCPServer) only — the other import path must fail naturally."""
    pkg = types.ModuleType("mcp")
    pkg.__path__ = []
    sub = types.ModuleType("mcp.server")
    sub.__path__ = []
    mod = types.ModuleType(f"mcp.server.{leaf[1]}")
    setattr(mod, leaf[0], _FakeSdkApp)
    monkeypatch.setitem(sys.modules, "mcp", pkg)
    monkeypatch.setitem(sys.modules, "mcp.server", sub)
    monkeypatch.setitem(sys.modules, f"mcp.server.{leaf[1]}", mod)


def test_sdk_missing_exits_with_clear_message():
    if _HAS_MCP:
        pytest.skip("mcp SDK is installed here; the missing-path is untestable")
    with pytest.raises(SystemExit) as exc:
        server._try_build_sdk_server()
    assert "not installed" in str(exc.value)
    assert "--sdk" in str(exc.value)


def test_sdk_fastmcp_binds_all_tools(monkeypatch):
    _stub_sdk(monkeypatch, ("FastMCP", "fastmcp"))
    app = server._try_build_sdk_server()
    assert set(app.bound) == set(_BINDINGS)
    for name, (description, fn) in app.bound.items():
        assert description == _DESCRIPTIONS[name]
        assert callable(fn)


def test_sdk_falls_back_to_mcpserver_2x(monkeypatch):
    _stub_sdk(monkeypatch, ("MCPServer", "mcpserver"))
    app = server._try_build_sdk_server()
    assert set(app.bound) == set(_BINDINGS)
