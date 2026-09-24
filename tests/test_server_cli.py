"""server.py's main() argument handling -- --help must print usage and return,
not silently fall through to starting the server (found live: a blind
third-party test ran `nlm-research-mcp --help` and got no output, just a hang
waiting on stdin, since the only arg check was for --sdk)."""
from notelab import server


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
