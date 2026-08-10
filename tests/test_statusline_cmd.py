"""Tests für `bibi-ctrl statusline` — die Claude-Code-Statusleiste.

Das git-Segment braucht ein echtes Repo (Fixtures aus conftest); der aktive
Case kommt über die Park-Marke der Session (`session_id` aus dem Payload), weil
die Statusleiste in einem Subprozess ohne Sicht auf das Bash-cwd läuft — ohne
`session_id` bleibt sie ohne Case-Segment (m.rau/bibi#99).
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


def _park(folder) -> None:
    """Den (vault-relativen) Case als Park-Marke der Session setzen."""
    state.set_path(str(folder.resolve().relative_to(repo.vault().resolve())))


# --- aktiver Case: ausschließlich über die Park-Marke der Session ---

def test_case_shown_from_session_park_marker(repo_with_origin, monkeypatch):
    """Die Leiste läuft ohne Sicht aufs Bash-cwd — die ``session_id`` im
    Payload ist ihr einziger Zugang zum aktiven Case."""
    monkeypatch.setenv("BIBI_SESSION_ID", "sess-A")
    folder = case_store.create_case("Alpha Feature")
    _park(folder)

    # frischer Leisten-Prozess: nichts adoptiert, keine Session in der Umgebung
    monkeypatch.delenv("BIBI_SESSION_ID")
    state.adopt_session(None)
    assert "AlphaFeature" in _render(session_id="sess-A")


def test_case_label_drops_date_and_hash(repo_with_origin, monkeypatch):
    monkeypatch.setenv("BIBI_SESSION_ID", "sess-A")
    folder = case_store.create_case("Alpha Feature")
    _park(folder)
    out = _render(session_id="sess-A")
    assert folder.name not in out  # nicht der volle 20260624.…-deadbeef-Name
    assert "AlphaFeature" in out


def test_no_case_segment_without_session_id(repo_with_origin, monkeypatch):
    """Ohne ``session_id`` im Payload zeigt die Leiste **keinen** Case.

    Bis m.rau/bibi#99 fiel sie hier auf den `path:`-Mirror in `.state.md`
    zurück. Der Zweig war im Betrieb nie erreichbar — Claude Code liefert die
    ``session_id`` immer, sie steht nicht in der dokumentierten Liste der
    optional fehlenden Payload-Felder (erhoben 2026-08-01: 44 von 44 echten
    Renders trugen sie). Erreichbar war er nur für Aufrufe von Hand, und dort
    zeigte er bei parallelen Sessions den Case einer *anderen*.
    """
    monkeypatch.setenv("BIBI_SESSION_ID", "sess-A")
    folder = case_store.create_case("Alpha Feature")
    _park(folder)

    monkeypatch.delenv("BIBI_SESSION_ID")
    state.adopt_session(None)
    out = _render()
    assert "AlphaFeature" not in out
    assert "proto:" not in out


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


def test_conflict_tree_shown(repo_with_origin):
    """#114: working_tree_status() kann jetzt "conflict" liefern — die
    Statusline darf dabei nicht mit KeyError absaufen (_TREE_COLOR/_git_segment)."""
    import subprocess
    root, _ = repo_with_origin
    subprocess.run(["git", "checkout", "-q", "-b", "side"], cwd=root, check=True)
    (root / "geteilt.md").write_text("side\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "side"], cwd=root, check=True)
    subprocess.run(["git", "checkout", "-q", "trunk"], cwd=root, check=True)
    (root / "geteilt.md").write_text("trunk\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "trunk"], cwd=root, check=True)
    subprocess.run(["git", "merge", "side"], cwd=root, capture_output=True, check=False)
    assert "conflict" in _render()


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


# --- proto-Segment: nur bei aktivem Case (über die Park-Marke), Werte off/on/dbg ---

def test_proto_on_when_case_active(repo_with_origin, monkeypatch):
    monkeypatch.setenv("BIBI_SESSION_ID", "sess-A")
    folder = case_store.create_case("Statusfall")
    frontmatter.patch(folder / "README.md", protocol="./protocol.json")
    _park(folder)
    assert "proto:on" in _render(session_id="sess-A")


def test_proto_dbg_when_debug(repo_with_origin, monkeypatch):
    monkeypatch.setenv("BIBI_SESSION_ID", "sess-A")
    folder = case_store.create_case("Debugfall")
    frontmatter.patch(folder / "README.md", protocol="./protocol.json+debug")
    _park(folder)
    assert "proto:dbg" in _render(session_id="sess-A")


def test_proto_off_when_case_active_without_protocol(repo_with_origin, monkeypatch):
    monkeypatch.setenv("BIBI_SESSION_ID", "sess-A")
    folder = case_store.create_case("Stiller Fall")
    _park(folder)
    assert "proto:off" in _render(session_id="sess-A")


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


# --- Upgrade-Aufforderung: hart über alles (m.rau/bibi#94) ---

def test_upgrade_notice_leads_the_line(repo_with_origin, monkeypatch):
    """Wartet ein Upgrade, steht die Aufforderung **vor** allem anderen.

    „Hart über alles" heißt Vorrang, nicht Einreihen: eingereiht zwischen
    Branch und ctx% wäre sie ein Segment unter sechsen und damit genau so
    übersehbar wie der Zustand, den sie melden soll.
    """
    from bibi import upgrade_notice
    monkeypatch.setattr(upgrade_notice, "pending",
                        lambda *a, **kw: {"expected": "v0.6.0",
                                          "running": "v0.5.3"})
    line = _render(model={"display_name": "Opus"})
    assert "UPGRADE" in line
    assert line.index("UPGRADE") < line.index("Opus")


def test_no_upgrade_notice_when_current(repo_with_origin, monkeypatch):
    """Ohne wartendes Upgrade bleibt die Leiste, wie sie war — sonst wäre der
    Hinweis dauerhaft da und würde nach dem zweiten Mal überlesen."""
    from bibi import upgrade_notice
    monkeypatch.setattr(upgrade_notice, "pending", lambda *a, **kw: None)
    assert "UPGRADE" not in _render(model={"display_name": "Opus"})


def test_statusline_survives_a_broken_upgrade_check(repo_with_origin, monkeypatch):
    """Die Leiste darf an der Aufforderung nicht scheitern — sie ist eine
    Beigabe, und eine leere Leiste wäre der teurere Fehler."""
    from bibi import upgrade_notice

    def boom(*a, **kw):
        raise RuntimeError("kaputt")

    monkeypatch.setattr(upgrade_notice, "pending", boom)
    assert "Opus" in _render(model={"display_name": "Opus"})
