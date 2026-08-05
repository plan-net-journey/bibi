"""``bibi-ctrl at`` — One-shot-Schedule anlegen (DESIGN §5.2/§6.3)."""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

import pytest

from bibi import frontmatter, repo
from bibi.ctrl import at_cmd, main


def test_resolve_relative_minutes():
    now = _dt.datetime(2026, 6, 26, 12, 0, 0)
    assert at_cmd.resolve_when("+5min", now=now) == now + _dt.timedelta(minutes=5)
    assert at_cmd.resolve_when("+30s", now=now) == now + _dt.timedelta(seconds=30)
    assert at_cmd.resolve_when("+2h", now=now) == now + _dt.timedelta(hours=2)
    assert at_cmd.resolve_when("+1d", now=now) == now + _dt.timedelta(days=1)


def test_resolve_iso():
    dt = at_cmd.resolve_when("2026-07-01T09:00:00")
    assert dt.year == 2026 and dt.month == 7 and dt.hour == 9


def test_resolve_bad_raises():
    with pytest.raises(ValueError):
        at_cmd.resolve_when("not-a-time")


def test_at_writes_claude_md(team_repo: Path):
    rc = main(["at", "+10min", "Antworte mit hallo"])
    assert rc == 0
    mds = list((team_repo / "vault" / "case").glob("*.at-*.md"))
    assert len(mds) == 1
    fm = frontmatter.read(mds[0])
    assert "at" in fm and fm["job"] == "claude: Antworte mit hallo"
    assert "claude" not in fm


def test_at_job_flag(team_repo: Path):
    rc = main(["at", "+1min", "echo hi", "--job"])
    assert rc == 0
    md = next((team_repo / "vault" / "case").glob("*.at-*.md"))
    fm = frontmatter.read(md)
    assert fm["job"] == "echo hi" and "claude" not in fm


def test_at_slug_format(team_repo: Path):
    main(["at", "2026-07-01T09:00:00", "x"])
    md = next((team_repo / "vault" / "case").glob("*.md"))
    # YYYYmmdd.at-HHMMSS-XXXX
    assert md.stem.startswith("20260701.at-090000-")
    assert len(md.stem.split("-")[-1]) == 4


def test_at_bad_when_returns_2(team_repo: Path, capsys):
    assert main(["at", "garbledegook", "x"]) == 2
    assert "nicht als Zeitpunkt lesbar" in capsys.readouterr().err


def test_at_rescan_best_effort_no_daemon(team_repo: Path, capsys):
    # Kein Daemon läuft → MD wird trotzdem geschrieben, Hinweis statt Fehler.
    rc = main(["at", "+5min", "x", "--port", "59998"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Daemon nicht erreichbar" in out
    assert list((team_repo / "vault" / "case").glob("*.md"))  # MD existiert


def test_at_rescan_uses_scheduler_base_url_without_port(team_repo: Path, capsys, monkeypatch):
    # PLAN-13 Stufe 13.0: ohne --port zielt der Rescan-Trigger auf die volle
    # BIBI_SCHEDULER_URL, nicht mehr blind auf 127.0.0.1 — hier absichtlich
    # ein unerreichbarer Fake-Host, um die tatsächlich verwendete URL in der
    # best-effort-Fehlermeldung zu sehen.
    monkeypatch.setattr(at_cmd.config, "scheduler_base_url",
                        lambda: "http://sarasate.tail9f9173.ts.net:59998")
    rc = main(["at", "+5min", "x"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "sarasate.tail9f9173.ts.net:59998" in out


# ── Auf einem Client erreicht die MD den Scheduler nicht (m.rau/bibi#140) ────
#
# `at` schreibt die Schedule-MD in den **lokalen** Checkout (`repo.case_dir()`),
# schickt den Rescan aber an `config.scheduler_base_url()` — auf einem Client
# also an eine andere Maschine, die diese Datei nie zu Gesicht bekommt. Der
# Rescan gelingt technisch (200), findet nichts, und `_rescan()` meldet
# trotzdem `True`: es prueft nur die Erreichbarkeit.
#
# **Live nachgewiesen am 2026-08-05** (Mac-Client → sarasate): die MD lag auf
# dem Mac, `/srv/bibi-notes/vault/case/` auf sarasate kannte sie nicht, und der
# Job erschien ueber 20 Abfragen in keiner `job list`. Die CLI hatte
# `rescan: ok` gemeldet.
#
# Der Weg zum Scheduler ist der Vault selbst: er ist git, und der Synchronizer
# verteilt ihn. Also gehoert die MD committet und gepusht, bevor rescannt wird
# — und wenn das nicht geht, muss es dastehen statt eines `ok`.


def _als_client(monkeypatch, *, push_ok: bool = True):
    """Ein Knoten ohne `scheduler`-Rolle, dessen Scheduler woanders steht."""
    monkeypatch.setattr(at_cmd.config, "scheduler_base_url",
                        lambda: "http://ein-anderer-knoten:8780")
    monkeypatch.setattr(at_cmd, "_rescan", lambda url: True)   # Host antwortet
    monkeypatch.setattr(at_cmd, "_hat_remote", lambda: True, raising=False)
    gerufen: dict = {}
    monkeypatch.setattr(at_cmd, "_zustellen",
                        lambda p: (gerufen.setdefault("pfad", p), push_ok)[1],
                        raising=False)
    return gerufen


def test_at_on_a_client_delivers_the_md_to_the_scheduler(team_repo: Path,
                                                         capsys, monkeypatch):
    """Die MD muss den Knoten verlassen, sonst feuert sie nie."""
    gerufen = _als_client(monkeypatch)
    rc = main(["at", "+5min", "x"])
    assert rc == 0
    assert "pfad" in gerufen, "die MD wurde nicht zugestellt"


def test_at_on_a_client_does_not_claim_a_success_it_cannot_have(
        team_repo: Path, capsys, monkeypatch):
    """Scheitert die Zustellung, ist `rescan: ok` eine Falschaussage.

    Genau diese Meldung hat den Fehler am 2026-08-05 verdeckt: der Auftrag
    galt als eingeplant und lief nie.
    """
    _als_client(monkeypatch, push_ok=False)
    rc = main(["at", "+5min", "x"])
    out = capsys.readouterr().out
    assert "ok" not in out.split("rescan:")[-1].split("\n")[0], out
    assert rc != 0 or "nicht" in out.lower(), out


def test_at_on_the_scheduler_does_not_need_git(team_repo: Path, monkeypatch):
    """Auf dem Host sieht der Daemon denselben Vault — kein Umweg noetig."""
    monkeypatch.setattr(at_cmd.config, "scheduler_base_url",
                        lambda: "http://localhost:8769")   # der Scheduler ist hier
    monkeypatch.setattr(at_cmd, "_rescan", lambda url: True)
    gerufen: dict = {}
    monkeypatch.setattr(at_cmd, "_zustellen",
                        lambda p: gerufen.setdefault("pfad", p) or True,
                        raising=False)
    rc = main(["at", "+5min", "x"])
    assert rc == 0
    assert "pfad" not in gerufen, "auf dem Scheduler wurde unnoetig gepusht"
