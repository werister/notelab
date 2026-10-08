# NoteLab evaluation report — 2026-10-08

A full hands-on evaluation: the whole test/eval battery was run, all four skills were
exercised end-to-end, and every testable README claim was checked against real output.

**Bottom line.** NoteLab works as advertised, and in a couple of places overdelivers.
Nothing failed. The only issues found were documentation drift — stale numbers and one
behavioural gotcha — all fixed in the same change set as this report. No runtime code
needed changing.

- **Environment:** Python 3.12.13, offline (no NotebookLM account, no agent CLIs installed).
- **Commit under test:** `042e8af` (main) plus the doc fixes landed alongside this report.
- **How to reproduce:** `pip install -e ".[dev]" && pytest -q && scripts/verify.sh`.

---

## 1. Automated suite and the verify checklist

| Check | Result |
|---|---|
| `pytest -q` | **353 passed, 3 skipped** in ~5s |
| `scripts/verify.sh` | **all green** — imports, offline tests, demo, code index, both guard refusals, skills frontmatter |

The 3 skips are expected and honest: one live Laya test (needs `notelab[laya]`), one live
semantic test (needs `MISTRAL_API_KEY`), and the unreadable-file test (skipped when running
as root, since root ignores POSIX permissions).

## 2. Skills, exercised end-to-end

### code-questions (no account needed) — works, and overdelivers
Indexed a real repo and ran every documented operation:

- `index` — recorded the snapshot with `version = git commit`, as advertised.
- `find --kind def` — returned the exact verbatim source with a `repo@commit/path:line` locator.
- `find --kind callers` — **AST-accurate**, and names the *enclosing* function/method of each call site.
- `find --kind subclasses` — correctly found `MistralModelNotFoundError` as a subclass of
  `SemanticExpansionError`. (Querying a base that is not itself defined in the index, e.g.
  `Exception`, returns no matches — correct, documented "direct subclasses" behaviour, not a bug.)
- `find --kind text` — ranked full-text snippets.
- `versions` and `diff` — the cross-version diff reported files/functions added, removed and changed.

### nlmx guard — works, and overdelivers
The headline safety claim holds. Both unsafe operations are refused with a clear, actionable
reason and a non-zero exit code — and each refusal **names the correct alternative command**,
which is more helpful than the README promises:

- `--source-ids` → refused, points to `nlm source content <id>`.
- `chat start` → refused, points to one-shot `nlmx query`.

### skill-evolution — works
`scripts/evolve.py --init` runs the guided setup and ends in the exact duel command plus its
live-run budget (6 / 12 / 24 runs for smoke / standard / deep), exactly as documented.

### research-loop and nlm-skill — content verified
Both require a live `nlm` account, so they were not driven live. Their instructions are
self-consistent and accurate against the current tool surface; `research-loop` now also carries
the `project_kickstart` / diverse-library workflow so it matches the reframed README.

### Supporting checks
- **A/B harness:** an authoritative/diverse arm (paper + gov + repo) beat two low-authority
  blogs by a 0.29 margin with a "good" verdict.
- **Catalog full-text search:** hyphenated `robo-advisor` matches (not parsed as a NOT operator),
  and singular `aggregator` matches plural `aggregators` via porter stemming — the two behaviours
  the code comments describe are real.
- **Strategy memory:** replays the prior winner and emits an authority-leaning mutation; tunes
  weights toward the winning signals.
- **install-skills.sh:** installs all four skills (+ the guard with `--with-guard`) to the right
  per-tool directories.
- **Graceful degradation:** of 11 known adversarial runners, none are installed here; all are
  reported skipped, never failed. The family leaderboard returns empty rather than crashing.

## 3. Source-kind diversity (the headline feature)

Verified end-to-end. On a diverse "start a RAG project" set (paper + docs + repo + video + two
discussions) versus six blogs:

| | old (domain-only) | new (kind-aware) |
|---|---|---|
| diverse set | diversity 1.000 | diversity 0.917 |
| six blogs | diversity 1.000 | diversity 0.500 |

The old scorer rated both identically (the blind spot); the new one separates them, and the
`project_kickstart` preset favours the diverse library by about +0.25 composite / +0.67 diversity.
Reports expose `type_mix`, `type_diversity` and `domain_diversity`, so the number is auditable.

## 4. Where it overdelivers

- **The code index.** Sold as a "supporting feature", but it is genuinely strong: AST-accurate
  callers that name the enclosing scope, content-deduplicated versioning, and a real
  function-level cross-version diff — all with zero model tokens.
- **The guard refusals.** Each refusal names the correct alternative command, not just "refused".

## 5. Issues found (all documentation drift — fixed)

1. **Stale score claim.** The transformer-attention example claimed `0.79 overall, 0.89 diversity
   (10 sources across 10 domains)`. The kind-diversity signal correctly scores that blog-heavy set
   lower (~0.63); the README now shows the accurate figure and the real `type_mix`, framed to show
   the signal doing its job.
2. **Stale test counts.** README and `docs/EVALUATIONS.md` said `348 tests / 2 skipped`; the actual
   figure is `353 / 3`.
3. **Code-index default version — behavioural gotcha.** The default version is the *most recently
   indexed* snapshot (insertion order), not the newest commit. Backfilling an older commit after a
   newer one makes queries silently return the older source. The `code-questions` skill now
   documents this and recommends passing `--version` explicitly when backfilling.
4. **Skill / README sync.** The `research-loop` skill did not mention `project_kickstart` or the
   diverse-library workflow; it now does.

## 6. Open observation (not fixed — flagged)

`skill-evolution/scripts/evolve.py --init` writes a scaffolded challenger entry into the *tracked*
battery JSON, which dirties the working tree on an otherwise read-only "just show me" run. Harmless,
but writing to a copy (or printing the scaffold) would be cleaner. Left as a follow-up.

---

*Method: every claim marked "works" above was reproduced by running the tool and reading its real
output in an offline sandbox. Claims requiring a live NotebookLM account or installed agent CLIs
were verified for internal consistency only and are labelled as such.*
