"""``bibi-ctrl init`` — Claude-Auth-Token-Hinweis (Next-steps-Punkt aus dem
Bibi4-Case: weder ``doctor`` noch ``init`` prüften das bisher ab)."""

from __future__ import annotations

import argparse
from pathlib import Path

from bibi.ctrl import init_cmd


def _args(force: bool = False) -> argparse.Namespace:
    return argparse.Namespace(force=force)


def test_init_hints_when_claude_auth_missing(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.setenv("BIBI_CONFIG_PATH", str(tmp_path / "env"))
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr("builtins.input", lambda *_: "")  # alle Prompts: Default übernehmen
    rc = init_cmd.run(_args())
    assert rc == 0
    out = capsys.readouterr().out
    assert "CLAUDE_CODE_OAUTH_TOKEN" in out and "ANTHROPIC_API_KEY" in out


def test_init_no_hint_when_claude_auth_present(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.setenv("BIBI_CONFIG_PATH", str(tmp_path / "env"))
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-test-token")
    monkeypatch.setattr("builtins.input", lambda *_: "")
    rc = init_cmd.run(_args())
    assert rc == 0
    out = capsys.readouterr().out
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in out


# ── BIBI_NODE_ID — nie abgefragt, stabil über mehrere init-Läufe (Bibi4-Iteration) ─


def test_init_never_prompts_for_node_id(tmp_path: Path, monkeypatch):
    from bibi import config
    monkeypatch.setenv("BIBI_CONFIG_PATH", str(tmp_path / "env"))
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-test-token")
    calls: list[str] = []

    def _tracking_prompt(label: str, default: str) -> str:
        calls.append(label)
        return default
    monkeypatch.setattr(init_cmd, "_prompt", _tracking_prompt)
    rc = init_cmd.run(_args())
    assert rc == 0
    # Ein _prompt()-Aufruf pro KEYS-Eintrag außer BIBI_NODE_ID (special-cased,
    # nie über _prompt()) — dieselbe Zahl beweist, dass der continue-Zweig
    # tatsächlich greift, nicht nur, dass kein Label dafür existiert.
    assert len(calls) == len(config.KEYS) - 1
    node_id = config.read_env()["BIBI_NODE_ID"]
    assert node_id and len(node_id) == 32


def test_init_force_rerun_keeps_same_node_id(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("BIBI_CONFIG_PATH", str(tmp_path / "env"))
    monkeypatch.setattr("builtins.input", lambda *_: "")
    init_cmd.run(_args())
    from bibi import config
    first = config.read_env()["BIBI_NODE_ID"]
    init_cmd.run(_args(force=True))  # zweiter Lauf, force=True überschreibt die Datei
    second = config.read_env()["BIBI_NODE_ID"]
    assert first == second
