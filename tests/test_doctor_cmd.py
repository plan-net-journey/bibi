"""Tests für ``bibi-ctrl doctor`` (PLAN-5 §5.2) — Fokus: Doctor-ClaudeAuth-Bug
(Case 20260621.Bibi4-870bd9db, live gefunden 2026-07-24). ``run()`` selbst
hatte bislang keine eigene Testdatei — genau die Lücke, durch die der Bug
unbemerkt blieb."""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from bibi import config
from bibi.ctrl import doctor_cmd


def _seed_claude_job(repo_root: Path) -> None:
    p = repo_root / "vault" / "case" / "a" / "README.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('---\nschedule: now\njob: "claude: x"\n---\n', encoding="utf-8")


def _config_path(_tmp_path: Path) -> Path:
    """Die Knoten-Konfiguration des Team-Repos, in dem der Test steht.

    Nahm bis m.rau/bibi#52 einen tmp-Pfad und stellte ihn per
    ``BIBI_CONFIG_PATH`` zu. Die Variable ist entfallen — die Konfiguration
    liegt jetzt in ``<repo>/data/env``, und ``team_repo`` parkt das cwd bereits
    dorthin. Das Argument bleibt für die Aufrufer stehen, wird aber nicht mehr
    gebraucht.
    """
    return config.env_path()


def test_claude_auth_missing_without_any_token(team_repo, tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    _seed_claude_job(team_repo)
    doctor_cmd.run(argparse.Namespace())
    assert "claude-auth-missing" in capsys.readouterr().out


def test_claude_auth_present_via_process_env(team_repo, tmp_path, monkeypatch, capsys):
    # Regressions-Anker: der bisherige, bereits funktionierende Fall darf
    # durch den Fix nicht kaputtgehen.
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-fake")
    _seed_claude_job(team_repo)
    doctor_cmd.run(argparse.Namespace())
    assert "claude-auth-missing" not in capsys.readouterr().out


def test_claude_auth_present_via_bare_name_in_config_file(team_repo, tmp_path, monkeypatch, capsys):
    # Der live reproduzierte Bug selbst: bare CLAUDE_CODE_OAUTH_TOKEN korrekt
    # in ~/.config/bibi/env gesetzt (nicht im Prozess-Environment) — token_present
    # las vorher NUR os.environ, meldete claude-auth-missing trotzdem.
    cfg_path = _config_path(tmp_path)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text("CLAUDE_CODE_OAUTH_TOKEN=sk-fake\n", encoding="utf-8")
    _seed_claude_job(team_repo)
    doctor_cmd.run(argparse.Namespace())
    assert "claude-auth-missing" not in capsys.readouterr().out


def test_claude_auth_present_via_prefixed_name_in_config_file(team_repo, tmp_path, monkeypatch, capsys):
    # PLAN-32 Stufe 32.0s empfohlene Form (BIBI_JOB_ENV_-Präfix) — vorher
    # ebenfalls unsichtbar für token_present, obwohl worker.py::_exec_config()
    # sie längst korrekt nutzt.
    cfg_path = _config_path(tmp_path)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text("BIBI_JOB_ENV_CLAUDE_CODE_OAUTH_TOKEN=sk-fake\n", encoding="utf-8")
    _seed_claude_job(team_repo)
    doctor_cmd.run(argparse.Namespace())
    assert "claude-auth-missing" not in capsys.readouterr().out


def test_claude_auth_present_via_distributed_env(team_repo, tmp_path, monkeypatch, capsys):
    # PLAN-32 Stufe 32.2: vom Host verteiltes Bundle — niedrigste Präzedenz,
    # aber genauso real nutzbar für den Job-Exec-Pfad wie ein lokaler Wert.
    cfg_path = _config_path(tmp_path)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    config.write_distributed_env(
        {"BIBI_JOB_ENV_ANTHROPIC_API_KEY": "sk-fake"}, version="v1")
    _seed_claude_job(team_repo)
    doctor_cmd.run(argparse.Namespace())
    assert "claude-auth-missing" not in capsys.readouterr().out


def test_claude_auth_finding_absent_without_claude_jobs(team_repo, tmp_path, monkeypatch, capsys):
    # check_missing_claude_auth() feuert nur, wenn das Vault claude:-Jobs
    # enthält — reines Host-/App-Setup ohne Claude-Nutzung bleibt still.
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    doctor_cmd.run(argparse.Namespace())
    assert "claude-auth-missing" not in capsys.readouterr().out


# ── Credential-Drift: Faktensammlung ────────────────────────────────

def test_fingerprint_is_short_stable_and_not_the_value():
    fp = doctor_cmd._fingerprint("gho_supersecret")
    assert fp is not None
    assert len(fp) == 12
    assert "supersecret" not in fp
    assert fp == doctor_cmd._fingerprint("gho_supersecret")
    assert fp != doctor_cmd._fingerprint("gho_othersecret")
    assert doctor_cmd._fingerprint(None) is None
    assert doctor_cmd._fingerprint("") is None


def test_keychain_value_none_without_security_binary(monkeypatch):
    """Auf Nicht-macOS existiert der Ort nicht — kein Fund, kein Fehler."""
    monkeypatch.setattr(doctor_cmd.shutil, "which", lambda _: None)
    assert doctor_cmd._keychain_value("svc", "acct") is None


def test_credential_pairs_accepts_both_env_spellings(monkeypatch):
    """Die Verteilweg-Seite trägt den Namen mit oder ohne BIBI_JOB_ENV_-Präfix
    (PLAN-32); beide müssen gefunden werden."""
    monkeypatch.setattr(doctor_cmd.repo, "credential_checks",
                        lambda: [{"env": "GITEA_TOKEN", "keychain_service": "s",
                                  "keychain_account": "a"}])
    monkeypatch.setattr(doctor_cmd, "_keychain_value", lambda s, a: "same")

    prefixed = doctor_cmd._credential_pairs({"BIBI_JOB_ENV_GITEA_TOKEN": "same"})
    assert prefixed[0].env_fp == prefixed[0].keychain_fp

    bare = doctor_cmd._credential_pairs({"GITEA_TOKEN": "same"})
    assert bare[0].env_fp == bare[0].keychain_fp
