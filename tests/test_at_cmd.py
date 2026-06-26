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
    assert "at" in fm and fm["claude"] == "Antworte mit hallo"
    assert "job" not in fm


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
