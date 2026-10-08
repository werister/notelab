#!/usr/bin/env bash
# One command, one checklist: is this install working? Offline, no NotebookLM account.
set -u
cd "$(dirname "$0")/.."
PY="${PYTHON:-python3}"; [ -x .venv/bin/python ] && PY=.venv/bin/python
fail=0
check() { # name, command...
  local name="$1"; shift
  if out=$("$@" 2>&1); then printf '  \033[32mPASS\033[0m  %s\n' "$name"
  else printf '  \033[31mFAIL\033[0m  %s\n' "$name"; echo "$out" | tail -5 | sed 's/^/        /'; fail=1; fi
}
refuses() { # the guard must exit non-zero AND say why
  out=$("$@" 2>&1); rc=$?; [ $rc -ne 0 ] && echo "$out" | grep -q '\[nlmx\]'
}

echo "NoteLab verify"
check "engine imports (stdlib only)"        "$PY" -c "import notelab.server"
check "offline test suite"                  "$PY" -m pytest -q -o addopts= -x
check "self-improving loop demo"            "$PY" examples/self_improving_loop.py
check "code index: exact lookup + versions"  "$PY" -m pytest -q -o addopts= tests/test_codeindex.py
check "guard refuses --source-ids"          refuses ./nlmx notebook query nb "q" --source-ids s1
check "guard refuses chat start"            refuses ./nlmx chat start nb
check "skills present with frontmatter"     bash -c 'for s in nlm-skill research-loop skill-evolution code-questions; do head -1 skills/$s/SKILL.md | grep -q "^---" || exit 1; done'
if command -v nlm >/dev/null; then
  check "nlm on PATH (needed for live research)" nlm --version
else
  echo "  SKIP  nlm not installed (live research only; see README: Go live)"
fi
[ $fail -eq 0 ] && echo "All good." || { echo "Something failed. See above."; exit 1; }
