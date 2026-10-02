"""
Cross-repo reliability filter: skip adversarial runners that [redacted-private-project]'s swarm
dispatcher has ALREADY discovered are currently unreliable (quota-exhausted,
consistently garbled/empty output), instead of notelab re-discovering the same
failures live every time.

Both repos are plain SQLite, no server, no shared package. This reads [redacted-private-project]'s
`agent_notes` table directly (read-only) -- the loosest possible coupling that still
shares real data. If [redacted-private-project]'s ledger doesn't exist (a machine without [redacted-private-project]
checked out, or a fresh install), this is a silent no-op: notelab must work standalone.

NOTE on the ledger's time column: the real [redacted-private-project] ledger (verified live against
~/.local/state/[redacted-private-project]/swarm-runs.db) stores `timestamp` as a REAL unix-epoch
float, not a `written_at` TEXT/ISO column. Callers/tests that build their own mock
ledger with a `written_at` TEXT column (ISO 8601) are also supported -- we detect
whichever of the two columns the table actually has and parse accordingly, so this
module works against both the real ledger and any ISO-based test fixture.
"""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .evals import RunnerSpec

DEFAULT_MML_LEDGER = Path.home() / ".local/state/[redacted-private-project]/swarm-runs.db"

# how recent a quota-exhaustion note has to be to still count -- monthly quotas reset,
# so a note from last month says nothing about right now.
_QUOTA_NOTE_WINDOW = timedelta(days=14)

# Verified against real note text in [redacted-private-project]'s live ledger, e.g.
# "race task 'auto-pick-live-demo': quota_exhausted, 8255ms" -- "quota" alone covers
# that phrasing; "usage limit" / "monthly limit" cover the plainer prose form.
_QUOTA_HINTS = re.compile(r"quota|usage limit|monthly limit", re.I)


def _parse_iso(s: str) -> datetime:
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _parse_epoch(value) -> datetime:
    return datetime.fromtimestamp(float(value), tz=timezone.utc)


def _time_column(conn: sqlite3.Connection) -> tuple[str, str]:
    """Return (column_name, kind) where kind is 'iso' or 'epoch'. Raises sqlite3.Error
    if agent_notes has neither -- callers already catch sqlite3.Error as a no-op."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(agent_notes)").fetchall()}
    if "written_at" in cols:
        return "written_at", "iso"
    if "timestamp" in cols:
        return "timestamp", "epoch"
    raise sqlite3.Error("agent_notes has neither 'written_at' nor 'timestamp' column")


def filter_known_unreliable(
    specs: list[RunnerSpec],
    mml_ledger_path: Path | str = DEFAULT_MML_LEDGER,
    now_iso: str | None = None,
) -> list[RunnerSpec]:
    """Return specs with .available/.skipped_reason overridden to False for any
    provider whose most recent [redacted-private-project] agent_notes entry, within the last
    `_QUOTA_NOTE_WINDOW`, mentions a quota/usage-limit failure.

    Never raises: a missing/unreadable ledger, or a provider with no notes at all,
    leaves that spec's availability exactly as detect_runners() already set it.
    """
    path = Path(mml_ledger_path)
    if not path.exists():
        return specs
    now = _parse_iso(now_iso) if now_iso else datetime.now(timezone.utc)

    quota_flagged: set[str] = set()
    try:
        conn = sqlite3.connect(str(path))
        conn.row_factory = sqlite3.Row
        try:
            time_col, time_kind = _time_column(conn)
            rows = conn.execute(
                f"SELECT agent, note_text, {time_col} AS ts FROM agent_notes "
                f"ORDER BY {time_col} DESC"
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return specs

    seen: set[str] = set()
    for row in rows:
        agent = row["agent"]
        if agent in seen:
            continue  # only the MOST RECENT note per agent decides current status
        seen.add(agent)
        try:
            written = _parse_iso(row["ts"]) if time_kind == "iso" else _parse_epoch(row["ts"])
        except (ValueError, TypeError):
            continue
        if now - written > _QUOTA_NOTE_WINDOW:
            continue
        if _QUOTA_HINTS.search(row["note_text"] or ""):
            quota_flagged.add(agent)

    out = []
    for spec in specs:
        if _spec_matches_any(spec, quota_flagged):
            out.append(_mark_unavailable(spec))
        else:
            out.append(spec)
    return out


def _spec_matches_any(spec: RunnerSpec, agents: set[str]) -> bool:
    """True if any flagged ledger agent identifies this spec: an exact match on
    spec.name/spec.provider, or a family-prefix match ('<name>-'/'<provider>-') --
    the real ledger records model variants (e.g. 'kilo-auto', 'cline-glm-5-3'),
    never the bare family name, so prefix matching is required without ever
    hardcoding specific variant ids."""
    keys = {k for k in (spec.name, spec.provider) if k}
    for agent in agents:
        if agent in keys:
            return True
        if any(agent.startswith(f"{key}-") for key in keys):
            return True
    return False


def _mark_unavailable(spec: RunnerSpec) -> RunnerSpec:
    return RunnerSpec(spec.name, spec.bin, list(spec.args_template),
                      available=False, path=spec.path, provider=spec.provider,
                      skipped_reason="recent quota exhaustion reported in [redacted-private-project]'s agent ledger")
