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


def _config_path(tmp_path: Path) -> Path:
    return tmp_path / "cfg" / "env"


def test_claude_auth_missing_without_any_token(team_repo, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("BIBI_CONFIG_PATH", str(_config_path(tmp_path)))
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    _seed_claude_job(team_repo)
    doctor_cmd.run(argparse.Namespace())
    assert "claude-auth-missing" in capsys.readouterr().out


def test_claude_auth_present_via_process_env(team_repo, tmp_path, monkeypatch, capsys):
    # Regressions-Anker: der bisherige, bereits funktionierende Fall darf
    # durch den Fix nicht kaputtgehen.
    monkeypatch.setenv("BIBI_CONFIG_PATH", str(_config_path(tmp_path)))
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-fake")
    _seed_claude_job(team_repo)
    doctor_cmd.run(argparse.Namespace())
    assert "claude-auth-missing" not in capsys.readouterr().out


def test_claude_auth_present_via_bare_name_in_config_file(team_repo, tmp_path, monkeypatch, capsys):
    # Der live reproduzierte Bug selbst: bare CLAUDE_CODE_OAUTH_TOKEN korrekt
    # in ~/.config/bibi/env gesetzt (nicht im Prozess-Environment) — token_present
    # las vorher NUR os.environ, meldete claude-auth-missing trotzdem.
    cfg_path = _config_path(tmp_path)
    monkeypatch.setenv("BIBI_CONFIG_PATH", str(cfg_path))
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
    monkeypatch.setenv("BIBI_CONFIG_PATH", str(cfg_path))
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
    monkeypatch.setenv("BIBI_CONFIG_PATH", str(cfg_path))
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
    monkeypatch.setenv("BIBI_CONFIG_PATH", str(_config_path(tmp_path)))
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    doctor_cmd.run(argparse.Namespace())
    assert "claude-auth-missing" not in capsys.readouterr().out
