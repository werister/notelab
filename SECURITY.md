# Security Policy

## Reporting a vulnerability

Please report privately, not in public issues or pull requests.

**Preferred:** GitHub private vulnerability reporting:
<https://github.com/werister/notelab/security/advisories/new>

Include the affected file, tool or skill, the version (`pip show notelab`), steps to
reproduce, and the impact you observed. Do not include real credentials or notebook
contents; redact them.

## Scope

NoteLab runs on your machine with your credentials. In scope: the Python package in
`src/notelab`, the `nlmx` guard, `scripts/`, the skills in `skills/`, and the CI workflow.
Out of scope: the upstream `nlm` CLI and NotebookLM itself (report to
[jacob-bd/notebooklm-mcp-cli](https://github.com/jacob-bd/notebooklm-mcp-cli) or Google),
and the agent CLIs NoteLab can drive.

Reports that a guarded behaviour can be bypassed through `nlmx` are especially welcome.

## What NoteLab does and does not protect

- **Does:** refuse the four known-corrupting `nlm` behaviours, keep source deletion behind a confirm gate, keep the engine stdlib-only.
- **Does not:** manage Google authentication (that is `nlm`), sandbox the agent CLIs it spawns, or validate that retrieved sources are true.

## Supported versions

Fixes land on `main` and ship in the next release.
