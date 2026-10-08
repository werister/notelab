# Scope

NoteLab is small on purpose. Ideas are welcome and judged against this bar.

## The bar

An issue stays open if it clears **both**:

1. **Observed failure.** It describes something that went wrong in a real session: what you ran, what happened, what you expected. "It would be nicer if…" does not clear this.
2. **Fits the design.** It keeps the engine stdlib-only, tests offline, scores decomposable, and destructive operations gated (see [CONTRIBUTING.md](CONTRIBUTING.md)).

Size is irrelevant: a one-word fix and a new feature face the same bar.

## Out of scope

- Reimplementing `nlm`'s auth or NotebookLM API. NoteLab wraps `nlm`; it does not replace it.
- Third-party runtime dependencies in the engine.
- Black-box quality verdicts without per-signal breakdowns.
- Silent or unconfirmed source deletion.
- Guards for behaviours that have not been reproduced and checked against ground truth.

## Unclear issues

One round of questions. No reply in 14 days, closed.
