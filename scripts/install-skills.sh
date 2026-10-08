#!/usr/bin/env bash
# Install NoteLab's agent skills (and optionally the nlmx guard).
# Usage: scripts/install-skills.sh [claude|cursor|all] [--with-guard] [--link]
#   --link  symlink instead of copy, so `git pull` updates the skills
set -eu
cd "$(dirname "$0")/.."
target="${1:-claude}"; shift || true
guard=0; mode=cp
for a in "$@"; do case "$a" in --with-guard) guard=1;; --link) mode=link;; *) echo "unknown flag $a"; exit 2;; esac; done

dest_for() { case "$1" in claude) echo "$HOME/.claude/skills";; cursor) echo "$HOME/.cursor/skills";; *) echo "unknown target $1 (claude|cursor|all)" >&2; exit 2;; esac; }
targets=$target; [ "$target" = all ] && targets="claude cursor"

for t in $targets; do
  dest=$(dest_for "$t"); mkdir -p "$dest"
  for s in nlm-skill research-loop skill-evolution code-questions; do
    rm -rf "${dest:?}/$s"
    if [ "$mode" = link ]; then ln -s "$PWD/skills/$s" "$dest/$s"; else cp -R "skills/$s" "$dest/$s"; fi
    echo "installed $s -> $dest/$s"
  done
done

if [ $guard -eq 1 ]; then
  mkdir -p "$HOME/.local/bin"; cp nlmx "$HOME/.local/bin/nlmx"; chmod +x "$HOME/.local/bin/nlmx"
  echo "installed nlmx -> ~/.local/bin/nlmx (make sure it is on PATH)"
fi
echo; echo "Next: start a new agent session and ask: \"Use the research-loop skill: what tools does notelab give me?\""
echo "Check your setup: scripts/verify.sh"
