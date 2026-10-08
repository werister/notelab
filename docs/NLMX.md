# `nlmx` — the guarded wrapper

How the guard works, the evidence behind it, and why it wraps `nlm` instead of forking it. Back to the [README](../README.md).


## The investigation

Three agentic CLIs were each given the same nlm task, independently, on isolated notebooks, and their outputs were verified against ground truth (`nlm source list`) rather than trusted at face value.

### 1. Install gap: none of the three had nlm wired in correctly out of the box

| Client | `nlm setup add <x>` (MCP) | `nlm skill install <x>` (skill file) |
|---|---|---|
| Claude Code | **Silent no-op** — PATH-detection bug looks for a `claude` binary that isn't on PATH even inside a live Claude Code session | Works — correct convention, auto-discovered |
| Cursor | Writes a real config, but points at a bare `notebooklm-mcp` command not resolvable from the app's inherited PATH | Works, but Cursor's own agent couldn't confirm the skill directory is actually auto-loaded |
| Copilot CLI | **Wrong target** — writes VS Code's `.vscode/mcp.json`, not the standalone CLI's real config | Never installed at all — nlm has no real integration with the standalone Copilot CLI |

Root cause for the Claude/Cursor failures: the underlying `notebooklm-mcp` binary exists but isn't on the PATH any GUI-launched or restricted-shell process inherits. Fixed by pointing every MCP config at the absolute binary path instead of a bare command.

### 2. `research import` is not idempotent

`nlm research start` → `research status` → `research import` is a three-step state machine — sources never land in the notebook without the explicit `import` call. Confirmed, independently, three separate times on isolated notebooks: **calling `import` a second time on an already-imported task duplicates every source**, with a byte-identical "✓ Imported N sources" success message both times. There is no textual signal that anything went wrong — the only way to know is to independently count sources before and after.

### 3. `--source-ids` on `notebook query` is broken

Documented as "Comma-separated source IDs to query (default: all)". In practice, passing it **deselects every source instead of scoping to the one given** — `sources_used: []`, `"No sources are currently selected"`. Reproduced directly, consistently. Omitting the flag (querying all sources) works fine.

### 4. Generation commands need `--confirm`/`-y` or they silently block/no-op
Documented upstream, but easy for an agent to forget in a non-interactive context — confirmed it fails safe on EOF (doesn't hang forever) but still needs the flag to actually do anything.

## The fix: `nlmx`

A single-file Python wrapper (stdlib only) that delegates everything to real `nlm` unchanged except:

```
nlmx research import <notebook> [task_id]   # refuses a repeat import via a local ledger, unless --force
nlmx research run "<query>" --notebook-id <id> [--mode fast|deep]   # new composite: start→poll→import, exactly once
nlmx notebook query <notebook> "<question>" --source-ids <id>   # refused outright — points at the real workaround
nlmx <type> create <notebook> ...           # auto-injects --confirm if you forget it
nlmx chat start ...                         # refused outright — chat start is an unusable REPL for an agent
nlmx <anything else>                        # pure passthrough to `nlm`
```

## Install

```bash
scripts/install-skills.sh claude --with-guard   # skills + nlmx, see README
# or just the guard:
cp nlmx ~/.local/bin/nlmx && chmod +x ~/.local/bin/nlmx
```

The `nlm-skill` file is upstream's own, with one added section pointing agents at `nlmx` and explaining why, backed by the evidence above.

## Why a wrapper instead of a fork

`nlm` already implements ~30 working subcommands, auth/cookie handling, and retry logic. Reimplementing that to fix two behaviors would mean owning all of it. This wraps it instead — pure passthrough for everything that already works, guards only where the docs alone can't stop a silent corruption.
