#!/usr/bin/env python3
"""Scan for secrets and personal identity before (and after) the repo goes public. Stdlib only.

  scripts/scan_leaks.py                # tracked files at HEAD
  scripts/scan_leaks.py --history      # + every line ever added, commit messages and authors
                                       #   across remote branches and fetched PR refs
                                       #   (add --include-local to also scan local branches)

Generic shapes (keys, tokens, home paths, emails, private IPs, notebook-image URLs)
live here. Anything personal
(your username, name, employer, hostnames) must NOT be committed: put one term per line in
.local/leak-terms.txt (git-ignored; or point LEAK_TERMS at a file) and it is checked too. Matches are masked in the output.
Exit status 1 if anything is found. Known false positives go in scripts/leak-allowlist.txt
(one `substring` per line; a finding whose path or masked text contains it is ignored)."""
import os, re, subprocess, sys

ROOT = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True).stdout.strip() or "."
PATTERNS = [
    ("private-key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("aws-key-id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github-token", re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})\b")),
    ("api-key-sk", re.compile(r"\bsk-(?:ant-|sp-|proj-)?[A-Za-z0-9][A-Za-z0-9._-]{24,}")),
    ("google-api-key", re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b")),
    ("slack-token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),
    ("secret-assignment", re.compile(r"(?i)\b(api[_-]?key|secret|passwd|password|token|bearer)\b\s*[:=]\s*['\"][A-Za-z0-9_\-./+=]{16,}['\"]")),
    # Bearer image URLs from a private NotebookLM notebook (lh3.../notebooklm/<token>).
    # Anyone with the link can fetch the image, so they must never be committed.
    # Split literals so this pattern does not match its own source line.
    ("notebooklm-img", re.compile(r"notebooklm/" + r"AKY" + r"WMX")),
    ("home-path", re.compile(r"(?:/Users/|/home/|C:\\Users\\)(?!<|\$|\{|you\b|user\b|name\b|me\b|USER|username|\.\.\.|\*|Shared|runner\b)[A-Za-z0-9._-]+")),
    ("email", re.compile(r"(?:\b[A-Za-z0-9._%+-]+|\{[^{}]*\})@(?!(?:users\.noreply\.github\.com|example\.|noreply\.|anthropic\.com))[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
    ("private-ip", re.compile(r"\b(?:10\.\d{1,3}|192\.168|172\.(?:1[6-9]|2\d|3[01])|100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7]))\.\d{1,3}\.\d{1,3}\b")),
    ("env-file-ref", re.compile(r"(?i)\b[A-Z0-9_]*(?:API_KEY|SECRET|TOKEN)\s*=\s*[A-Za-z0-9_\-.]{20,}")),
]
PUBLIC_LITERALS = {"noreply@github.com"}  # GitHub's own web-merge committer address
SKIP_SUFFIX = (".png", ".jpg", ".jpeg", ".gif", ".mp4", ".mp3", ".wav", ".pdf", ".ico", ".woff", ".woff2", ".lock", ".gz", ".zip")


def mask(s):
    s = s.strip()
    return s[:4] + "…" + "*" * min(len(s) - 4, 6) if len(s) > 8 else "***"


def load_lines(path):
    try:
        return [l.strip() for l in open(path, encoding="utf-8") if l.strip() and not l.startswith("#")]
    except OSError:
        return []


TERMS = [t.lower() for t in load_lines(os.environ.get("LEAK_TERMS") or os.path.join(ROOT, ".local", "leak-terms.txt"))]
ALLOW = load_lines(os.path.join(ROOT, "scripts", "leak-allowlist.txt"))
findings = []


def check(where, path, lineno, text):
    if path.lower().endswith(SKIP_SUFFIX):
        return
    for name, rx in PATTERNS:
        for m in rx.finditer(text):
            if m.group(0) in PUBLIC_LITERALS:
                continue
            found = mask(m.group(0))
            if not any(a in path or a in found for a in ALLOW):
                findings.append((name, where, path, lineno, found))
    low = text.lower()
    for t in TERMS:
        if t in low and not any(a in path for a in ALLOW):
            findings.append(("personal-term", where, path, lineno, mask(t)))


def git(*args):
    return subprocess.run(["git", "-C", ROOT, *args], capture_output=True, text=True, errors="replace").stdout


def scan_tree():
    for path in git("ls-files").splitlines():
        if path.lower().endswith(SKIP_SUFFIX):
            continue
        try:
            with open(os.path.join(ROOT, path), encoding="utf-8", errors="replace") as f:
                for i, line in enumerate(f, 1):
                    check("HEAD", path, i, line)
        except OSError:
            pass
        check("HEAD", path, 0, path)  # file names can leak too


def scan_history():
    refs = [r for r in git("for-each-ref", "--format=%(refname)", "refs/remotes", "refs/prs", *(["refs/heads"] if "--include-local" in sys.argv else [])).split() if not r.endswith("/HEAD")]
    if not refs:
        print("note: no remote/PR refs to scan", file=sys.stderr)
        return
    meta = git("log", "--format=%H%x1f%an%x1f%ae%x1f%cn%x1f%ce%x1f%B%x1e", *refs)
    for rec in meta.split("\x1e"):
        parts = rec.strip("\n").split("\x1f")
        if len(parts) < 6:
            continue
        sha = parts[0][:8]
        for idx, label in ((1, "author-name"), (2, "author-email"), (3, "committer-name"), (4, "committer-email"), (5, "message")):
            for ln in parts[idx].splitlines() or [""]:
                check(f"commit {sha}", label, 0, ln)
    sha = path = ""
    for line in git("log", "-p", "--no-color", "--format=@@commit %H", *refs).splitlines():
        if line.startswith("@@commit "):
            sha = line.split()[1][:8]
        elif line.startswith("+++ b/"):
            path = line[6:]
        elif line.startswith("+") and not line.startswith("+++"):
            check(f"commit {sha}", path, 0, line[1:])


def main():
    scan_tree()
    if "--history" in sys.argv:
        scan_history()
    seen = set()
    for f in findings:
        if f in seen:
            continue
        seen.add(f)
        name, where, path, lineno, found = f
        print(f"{name:18s} {where:16s} {path}{':' + str(lineno) if lineno else ''}  [{found}]")
    print(f"\n{len(seen)} finding(s)" + ("" if TERMS else "  (no .local/leak-terms.txt: personal-term check skipped)"))
    return 1 if seen else 0


if __name__ == "__main__":
    sys.exit(main())
