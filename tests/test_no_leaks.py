"""Guards against leaking secrets or personal identity into a public repo.

The scanner (scripts/scan_leaks.py) checks tracked files for key/token shapes, home-directory
paths, real email addresses, private IPs and notebook-image URLs. Personal terms (your username,
name, hostnames) are
checked too when .local/leak-terms.txt exists, but that file is git-ignored, so CI only runs the
generic checks."""
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "scan_leaks.py"


def _load():
    spec = importlib.util.spec_from_file_location("scan_leaks", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_tracked_files_have_no_secrets_or_identity():
    if not (ROOT / ".git").exists():
        pytest.skip("not a git checkout")
    r = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True, cwd=ROOT, env=dict(os.environ))
    assert r.returncode == 0, "leak scan found something:\n" + r.stdout


@pytest.mark.parametrize("name,sample", [
    ("private-key", "-----BEGIN " + "RSA PRIVATE KEY-----"),
    ("aws-key-id", "AKIA" + "ABCDEFGHIJKLMNOP"),
    ("github-token", "ghp_" + "a" * 36),
    ("api-key-sk", "sk-" + "ant-" + "a1" * 15),
    ("jwt", "eyJ" + "a" * 12 + "." + "b" * 12 + "." + "c" * 12),
    ("secret-assignment", 'api_key = "' + "x" * 20 + '"'),
    ("home-path", "/Users/" + "alice/project"),
    ("email", "alice" + "@" + "corp.com"),
    ("email", "{a, b}" + "@" + "corp.com"),
    ("private-ip", "192.168" + ".1.20"),
    ("notebooklm-img", "notebooklm/" + "AKY" + "WMX_" + "a" * 20),
])
def test_scanner_catches_planted_samples(name, sample):
    """Self-test: if a pattern rots, this fails instead of the scan silently passing."""
    mod = _load()
    rx = dict(mod.PATTERNS)[name]
    assert rx.search(sample), f"pattern {name} no longer matches its sample"


@pytest.mark.parametrize("sample", ["noreply@github.com", "x@users.noreply.github.com", "/Users/<you>/x", "/home/user/x"])
def test_scanner_ignores_known_public_or_placeholder_values(sample):
    mod = _load()
    for name, rx in mod.PATTERNS:
        for m in rx.finditer(sample):
            assert m.group(0) in mod.PUBLIC_LITERALS, f"{name} flagged {sample!r}"
