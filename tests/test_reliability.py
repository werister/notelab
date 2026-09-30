import sqlite3
from pathlib import Path

from notelab.research.evals import RunnerSpec
from notelab.research.reliability import filter_known_unreliable


def _make_mml_ledger(path: Path, notes: list[tuple[str, str, str]]) -> None:
    """notes: list of (agent, note_text, written_at ISO string)."""
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE agent_notes (id INTEGER PRIMARY KEY, agent TEXT, note_text TEXT, "
        "written_at TEXT, task_context TEXT, tags TEXT, written_by TEXT, confidence REAL)"
    )
    for agent, note_text, written_at in notes:
        conn.execute(
            "INSERT INTO agent_notes (agent, note_text, written_at) VALUES (?, ?, ?)",
            (agent, note_text, written_at),
        )
    conn.commit()
    conn.close()


def test_provider_with_recent_quota_note_is_marked_unavailable(tmp_path):
    ledger = tmp_path / "mml-ledger.db"
    _make_mml_ledger(ledger, [
        ("codex", "hit monthly usage limit, try again Oct 29", "2026-09-30T10:00:00+00:00"),
    ])
    specs = [RunnerSpec("codex", "codex", ["exec", "{prompt}"], available=True,
                        path="/usr/bin/codex", provider="codex")]
    out = filter_known_unreliable(specs, mml_ledger_path=ledger, now_iso="2026-09-30T12:00:00+00:00")
    assert out[0].available is False
    assert "quota" in (out[0].skipped_reason or "").lower()


def test_provider_with_no_notes_is_left_untouched(tmp_path):
    ledger = tmp_path / "mml-ledger.db"
    _make_mml_ledger(ledger, [])
    specs = [RunnerSpec("muse", "muse", ["exec", "{prompt}"], available=True,
                        path="/usr/bin/muse", provider="muse")]
    out = filter_known_unreliable(specs, mml_ledger_path=ledger, now_iso="2026-09-30T12:00:00+00:00")
    assert out[0].available is True


def test_missing_ledger_file_is_a_noop_not_an_error(tmp_path):
    specs = [RunnerSpec("codex", "codex", ["exec", "{prompt}"], available=True,
                        path="/usr/bin/codex", provider="codex")]
    out = filter_known_unreliable(specs, mml_ledger_path=tmp_path / "does-not-exist.db",
                                  now_iso="2026-09-30T12:00:00+00:00")
    assert out[0].available is True


def test_old_quota_note_past_the_window_is_ignored(tmp_path):
    ledger = tmp_path / "mml-ledger.db"
    _make_mml_ledger(ledger, [
        ("codex", "hit monthly usage limit", "2026-08-01T10:00:00+00:00"),
    ])
    specs = [RunnerSpec("codex", "codex", ["exec", "{prompt}"], available=True,
                        path="/usr/bin/codex", provider="codex")]
    out = filter_known_unreliable(specs, mml_ledger_path=ledger, now_iso="2026-09-30T12:00:00+00:00")
    assert out[0].available is True
