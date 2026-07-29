"""Tests für `bibi-ctrl statusline` — die Claude-Code-Statusleiste.

Das git-Segment braucht ein echtes Repo (Fixtures aus conftest); der aktive
Case kommt über die Park-Marke der Session (`session_id` aus dem Payload), weil
die Statusleiste in einem Subprozess ohne Sicht auf das Bash-cwd läuft — ohne
`session_id` bleibt der `path:`-Mirror in `.state.md` als Fallback.
"""

from __future__ import annotations

import io
import json

import pytest

from bibi import case_store, frontmatter, repo, state
from bibi.ctrl import main, statusline_cmd

pytestmark = pytest.mark.slow


def _render(**payload):
    return statusline_cmd.render(payload)


def _park_mirror(folder) -> None:
    """Display-Mirror `path:` auf den (vault-relativen) Case setzen."""
    state.set_path(str(folder.resolve().relative_to(repo.vault().resolve())))


# --- aktiver Case: über die Park-Marke der Session, Mirror nur als Fallback ---

def test_case_shown_from_session_park_marker(repo_with_origin, monkeypatch):
    """Die Leiste läuft ohne Sicht aufs Bash-cwd — die ``session_id`` im
    Payload ist ihr einziger Zugang zum aktiven Case."""
    monkeypatch.setenv("BIBI_SESSION_ID", "sess-A")
    folder = case_store.create_case("Alpha Feature")
    _park_mirror(folder)

    # frischer Leisten-Prozess: nichts adoptiert, keine Session in der Umgebung
    monkeypatch.delenv("BIBI_SESSION_ID")
    state.adopt_session(None)
    assert "AlphaFeature" in _render(session_id="sess-A")


def test_case_label_drops_date_and_hash(repo_with_origin, monkeypatch):
    monkeypatch.setenv("BIBI_SESSION_ID", "sess-A")
    folder = case_store.create_case("Alpha Feature")
    _park_mirror(folder)
    out = _render(session_id="sess-A")
    assert folder.name not in out  # nicht der volle 20260624.…-deadbeef-Name
    assert "AlphaFeature" in out


def test_case_falls_back_to_mirror_without_session_id(repo_with_origin, monkeypatch):
    """Trägt das Payload keine ``session_id``, bleibt der `.state.md`-Mirror —
    das alte Verhalten, damit die Leiste nie leer ausgeht."""
    monkeypatch.setenv("BIBI_SESSION_ID", "sess-A")
    folder = case_store.create_case("Alpha Feature")
    _park_mirror(folder)

    monkeypatch.delenv("BIBI_SESSION_ID")
    state.adopt_session(None)
    assert "AlphaFeature" in _render()


def test_no_case_segment_without_any_active_case(repo_with_origin):
    assert "proto:" not in _render(session_id="sess-unbekannt")


# --- git-Segment: tree × sync, orthogonal, happy path kollabiert zu "clean" ---

def test_clean_synced_collapses_to_clean(repo_with_origin):
    out = _render()
    assert "clean" in out
    assert "modified" not in out


def test_modified_tree_shown(repo_with_origin):
    root, _ = repo_with_origin
    (root / "vault" / "dirty.md").write_text("x", encoding="utf-8")
    assert "modified" in _render()


def test_branch_shown(repo_with_origin):
    assert "trunk" in _render()


# --- model / ctx kommen aus Claudes Payload ---

def test_model_and_ctx_from_payload(repo_with_origin):
    out = statusline_cmd.render({
        "model": {"display_name": "Sonnet 4.6"},
        "context_window": {"used_percentage": 42.0},
    })
    assert "Sonnet 4.6" in out
    assert "ctx:42%" in out


def test_no_ctx_segment_when_absent(repo_with_origin):
    assert "ctx:" not in _render()


# --- sync-Segment aus dem Repo-State ---

def test_sync_off_by_default(repo_with_origin):
    assert "sync:off" in _render()


def test_sync_on(repo_with_origin):
    state.set_auto_sync(True)
    assert "sync:on" in _render()


def test_sync_conflict_overrides_on(repo_with_origin):
    state.set_auto_sync(True)
    state.set_sync_conflict(True)
    out = _render()
    assert "sync:!conflict" in out
    assert "sync:on" not in out


# --- PLAN-30 Ebene 3: sync:!stuck(N) aus derselben Quarantäne-Liste (Ebene 2) ---

def test_sync_stuck_shown_when_branches_escalated(repo_with_origin):
    from bibi.daemon import merge_quarantine
    root, _ = repo_with_origin
    for trunk_sha in ("s1", "s2", "s3"):
        merge_quarantine.record_failure(root, "agent/stuck", trunk_sha=trunk_sha)
    out = _render()
    assert "sync:!stuck(1)" in out
    assert "sync:off" not in out


def test_sync_conflict_overrides_stuck(repo_with_origin):
    from bibi.daemon import merge_quarantine
    root, _ = repo_with_origin
    for trunk_sha in ("s1", "s2", "s3"):
        merge_quarantine.record_failure(root, "agent/stuck", trunk_sha=trunk_sha)
    state.set_sync_conflict(True)
    out = _render()
    assert "sync:!conflict" in out
    assert "sync:!stuck" not in out


def test_sync_stuck_not_shown_below_threshold(repo_with_origin):
    from bibi.daemon import merge_quarantine
    root, _ = repo_with_origin
    merge_quarantine.record_failure(root, "agent/almost", trunk_sha="s1")
    out = _render()
    assert "sync:!stuck" not in out
    assert "sync:off" in out


# --- proto-Segment: nur bei aktivem Case (über den Mirror), Werte off/on/dbg ---

def test_proto_on_when_case_active(repo_with_origin):
    folder = case_store.create_case("Statusfall")
    frontmatter.patch(folder / "README.md", protocol="./protocol.json")
    _park_mirror(folder)
    assert "proto:on" in _render()


def test_proto_dbg_when_debug(repo_with_origin):
    folder = case_store.create_case("Debugfall")
    frontmatter.patch(folder / "README.md", protocol="./protocol.json+debug")
    _park_mirror(folder)
    assert "proto:dbg" in _render()


def test_proto_off_when_case_active_without_protocol(repo_with_origin):
    folder = case_store.create_case("Stiller Fall")
    _park_mirror(folder)
    assert "proto:off" in _render()


def test_no_proto_segment_when_no_case(repo_with_origin):
    assert "proto:" not in _render()


# --- main(): liest stdin-JSON, druckt die Zeile, crasht nie ---

def test_main_reads_stdin_json(repo_with_origin, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"model": {"display_name": "Opus"}})))
    rc = main(["statusline"])
    assert rc == 0
    assert "Opus" in capsys.readouterr().out


def test_main_handles_bad_json(repo_with_origin, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO("kein json"))
    rc = main(["statusline"])
    assert rc == 0  # robust: niemals crashen
    capsys.readouterr()
