"""End-to-end tests for the bundled stdlib MCP server — no SDK, no network.

Drives the server through the actual JSON-RPC protocol (initialize, tools/list,
tools/call) to prove it fully loads and answers on its own.
"""
import json
import tempfile
from pathlib import Path

from notelab._tools import ToolLogic, build_stdio_server, tool_names
from notelab.mcp_stdio import StdioMCPServer
from notelab.storage import Store


def _tmp_logic():
    """ToolLogic wired to a temp store and a stub adapter (no nlmx / network)."""
    tmp = tempfile.mkdtemp()
    store = Store(Path(tmp) / "e2e.db")

    class StubAdapter:
        def list_sources(self, notebook_id):
            return []

        def source_content(self, sid):
            return ""

    return ToolLogic(adapter=StubAdapter(), store=store)


def test_missing_nlmx_is_clear_iserror_not_a_crash(monkeypatch):
    """README promise: tools degrade with a clear, actionable error when nlmx
    isn't installed — pinned at the JSON-RPC layer, hermetic (no PATH/HOME)."""
    from notelab.nlmx_adapter import NlmxError

    def no_nlmx():
        raise NlmxError("nlmx not found. Install it (`cp nlmx ~/.local/bin/nlmx "
                        "&& chmod +x`) or set NLMX_BIN.")

    monkeypatch.setattr("notelab.nlmx_adapter._resolve_nlmx", no_nlmx)
    tmp = tempfile.mkdtemp()
    srv = build_stdio_server(ToolLogic(store=Store(Path(tmp) / "e2e.db")))
    resp = srv.handle({"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                       "params": {"name": "research_score_notebook",
                                 "arguments": {"notebook_id": "nb1"}}})
    assert resp["result"]["isError"] is True
    text = resp["result"]["content"][0]["text"]
    assert "nlmx not found" in text and "NLMX_BIN" in text


def test_bundled_server_builds_with_all_fourteen_tools():
    srv = build_stdio_server(_tmp_logic())
    assert isinstance(srv, StdioMCPServer)
    assert set(srv.tools) == set(tool_names())
    assert len(srv.tools) == 14
    assert "research_ingest_codebase" in srv.tools


def test_initialize_handshake():
    srv = build_stdio_server(_tmp_logic())
    resp = srv.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert resp["result"]["serverInfo"]["name"] == "notelab"
    assert "protocolVersion" in resp["result"]
    assert resp["result"]["capabilities"]["tools"] is not None


def test_initialized_notification_gets_no_response():
    srv = build_stdio_server(_tmp_logic())
    assert srv.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_tools_list_returns_specs():
    srv = build_stdio_server(_tmp_logic())
    resp = srv.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    tools = resp["result"]["tools"]
    names = {t["name"] for t in tools}
    assert "research_run_experiment" in names
    assert "catalog_search" in names
    assert all(t["description"] and t["inputSchema"]["type"] == "object" for t in tools)


def test_tools_call_suggest_prompt_roundtrip():
    srv = build_stdio_server(_tmp_logic())
    resp = srv.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                       "params": {"name": "research_suggest_prompt",
                                  "arguments": {"question": "quantum error correction"}}})
    payload = json.loads(resp["result"]["content"][0]["text"])
    assert payload["question"] == "quantum error correction"
    assert payload["suggested_prompt"] is None
    assert payload["based_on_prior_winners"] == 0


def test_tools_call_score_notebook_with_stub_adapter():
    srv = build_stdio_server(_tmp_logic())
    resp = srv.handle({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                       "params": {"name": "research_score_notebook",
                                  "arguments": {"notebook_id": "nb1"}}})
    payload = json.loads(resp["result"]["content"][0]["text"])
    assert payload["n_sources"] == 0
    assert payload["quality_score"] == 0.0


def test_tools_call_research_assess_roundtrip():
    srv = build_stdio_server(_tmp_logic())
    resp = srv.handle({"jsonrpc": "2.0", "id": 41, "method": "tools/call",
                       "params": {"name": "research_assess",
                                  "arguments": {"notebook_id": "nb1", "question": "x"}}})
    payload = json.loads(resp["result"]["content"][0]["text"])
    assert payload["mode"] == "fast"
    assert payload["n_sources"] == 0


def test_tools_call_adversarial_run_detects_or_skips():
    srv = build_stdio_server(_tmp_logic())
    resp = srv.handle({"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                       "params": {"name": "research_adversarial_run",
                                  "arguments": {"prompt": "hi", "runners": ["copilot"]}}})
    payload = json.loads(resp["result"]["content"][0]["text"])
    assert "results" in payload
    assert payload["results"][0]["runner"] == "copilot"
    assert payload["results"][0]["available"] in (True, False)


def test_unknown_tool_is_json_rpc_error():
    srv = build_stdio_server(_tmp_logic())
    resp = srv.handle({"jsonrpc": "2.0", "id": 6, "method": "tools/call",
                       "params": {"name": "no_such_tool", "arguments": {}}})
    assert resp["error"]["code"] == -32602


def test_unknown_method_is_json_rpc_error():
    srv = build_stdio_server(_tmp_logic())
    resp = srv.handle({"jsonrpc": "2.0", "id": 7, "method": "frobnicate"})
    assert resp["error"]["code"] == -32601


def test_tool_exception_returned_as_iserror_content():
    logic = _tmp_logic()
    srv = build_stdio_server(logic)

    class BoomAdapter:
        def list_sources(self, nb):
            raise RuntimeError("nlmx down")

    logic._adapter = BoomAdapter()
    resp = srv.handle({"jsonrpc": "2.0", "id": 8, "method": "tools/call",
                       "params": {"name": "research_score_notebook",
                                  "arguments": {"notebook_id": "x"}}})
    assert resp["result"]["isError"] is True
    assert "nlmx down" in resp["result"]["content"][0]["text"]
