"""Wrapper: output.jsonl + Typ-Registry (DESIGN §4.5/§7.5; PLAN-3 §3.3)."""

from __future__ import annotations

from pathlib import Path

import pytest

from bibi import wrapper
from bibi.wrapper import output

pytestmark = pytest.mark.slow


# ── output.py ────────────────────────────────────────────────────────────────


def test_append_and_read(tmp_path: Path):
    p = tmp_path / "output.jsonl"
    output.append(p, "out", "hallo", t=1.0)
    output.append(p, "err", "ups", t=2.0)
    evs = output.read_events(p)
    assert evs == [
        {"t": 1.0, "s": "out", "line": "hallo"},
        {"t": 2.0, "s": "err", "line": "ups"},
    ]


def test_lines_filter(tmp_path: Path):
    p = tmp_path / "output.jsonl"
    output.append(p, "out", "a")
    output.append(p, "err", "b")
    output.append(p, "out", "c")
    assert output.lines(p, "out") == ["a", "c"]
    assert output.lines(p, "err") == ["b"]
    assert output.lines(p) == ["a", "b", "c"]


def test_append_collapses_newlines(tmp_path: Path):
    p = tmp_path / "output.jsonl"
    output.append(p, "out", "line1\r\nline2")
    assert output.lines(p) == ["line1  line2"]   # eine physische Zeile
    assert len(output.read_events(p)) == 1


def test_read_missing_is_empty(tmp_path: Path):
    assert output.read_events(tmp_path / "nope.jsonl") == []


# ── registry / run_job ───────────────────────────────────────────────────────


def test_registry_has_job_and_claude():
    assert "job" in wrapper.REGISTRY
    assert "claude" in wrapper.REGISTRY


def test_claude_argv_uses_model():
    argv = wrapper.REGISTRY["claude"].build_command(
        {"BIBI_JOB_PROMPT": "hi", "BIBI_JOB_MODEL": "claude-haiku-4-5-20251001"}
    )
    assert argv[:3] == ["claude", "-p", "hi"]
    assert "--model" in argv and "claude-haiku-4-5-20251001" in argv


def test_claude_argv_default_model():
    argv = wrapper.REGISTRY["claude"].build_command({"BIBI_JOB_PROMPT": "hi"})
    assert "claude-sonnet-4-6" in argv


def test_claude_argv_bin_override():
    argv = wrapper.REGISTRY["claude"].build_command(
        {"BIBI_JOB_PROMPT": "hi", "BIBI_CLAUDE_BIN": "/x/fakeclaude"})
    assert argv[0] == "/x/fakeclaude"


def test_claude_argv_container_ignores_host_bin():
    # Im Container liegt claude auf dem Image-PATH; der Host-BIBI_CLAUDE_BIN wäre dort
    # ungültig (Cannot find module). Also immer ``claude``.
    argv = wrapper.REGISTRY["claude"].build_command(
        {"BIBI_JOB_PROMPT": "hi", "BIBI_CLAUDE_BIN": "/Users/x/.local/bin/claude",
         "BIBI_EXEC_MODE": "container"})
    assert argv[0] == "claude"


def test_claude_argv_session_resume():
    argv = wrapper.REGISTRY["claude"].build_command(
        {"BIBI_JOB_PROMPT": "hi", "BIBI_JOB_SESSION": "sess-1"})
    assert "--resume" in argv and "sess-1" in argv


def test_claude_argv_no_session_no_resume():
    argv = wrapper.REGISTRY["claude"].build_command({"BIBI_JOB_PROMPT": "hi"})
    assert "--resume" not in argv


def test_claude_argv_permission_mode_only_in_container():
    # Host (Default): keine Permission-Übersteuerung (Nutzer-Settings gelten).
    host = wrapper.REGISTRY["claude"].build_command({"BIBI_JOB_PROMPT": "hi"})
    assert "--permission-mode" not in host
    # Container: acceptEdits (schreibt headless ohne Prompt, geht als root).
    cont = wrapper.REGISTRY["claude"].build_command(
        {"BIBI_JOB_PROMPT": "hi", "BIBI_EXEC_MODE": "container"})
    assert "--permission-mode" in cont and "acceptEdits" in cont


# ── PLAN-12 Stufe 12.2 — Streaming-Default ──────────────────────────────────


def test_claude_argv_streams_by_default():
    argv = wrapper.REGISTRY["claude"].build_command({"BIBI_JOB_PROMPT": "hi"})
    i = argv.index("--output-format")
    assert argv[i + 1] == "stream-json"
    # PFLICHT bei --print --output-format stream-json (live verifiziert,
    # test_container_claude.py) — die CLI bricht sonst mit "requires --verbose" ab.
    assert "--verbose" in argv


def test_claude_argv_stream_json_coexists_with_resume_and_permission_mode():
    argv = wrapper.REGISTRY["claude"].build_command(
        {"BIBI_JOB_PROMPT": "hi", "BIBI_JOB_SESSION": "sess-1",
         "BIBI_EXEC_MODE": "container"})
    assert "--output-format" in argv and "stream-json" in argv and "--verbose" in argv
    assert "--resume" in argv and "sess-1" in argv


# ── Follow-up PLAN-14 — Token-Level-Streaming (--include-partial-messages) ───


def test_claude_argv_includes_partial_messages_by_default():
    argv = wrapper.REGISTRY["claude"].build_command({"BIBI_JOB_PROMPT": "hi"})
    assert "--include-partial-messages" in argv
    # nur zusammen mit --print (-p) + --output-format stream-json gültig (CLI-Doku)
    assert "-p" in argv
    i = argv.index("--output-format")
    assert argv[i + 1] == "stream-json"


def test_claude_argv_partial_messages_coexists_with_container_permission_mode():
    argv = wrapper.REGISTRY["claude"].build_command(
        {"BIBI_JOB_PROMPT": "hi", "BIBI_EXEC_MODE": "container"})
    assert "--include-partial-messages" in argv
    assert "--permission-mode" in argv and "acceptEdits" in argv


# ── PLAN-12 Stufe 12.3 — soul:-Frontmatter wirkt jetzt ──────────────────────


def test_claude_argv_appends_soul_prompt_when_file_matches(tmp_path: Path):
    souls = tmp_path / ".claude" / "souls"
    souls.mkdir(parents=True)
    (souls / "12.Data.SOUL.md").write_text("Du bist Data.", encoding="utf-8")
    argv = wrapper.REGISTRY["claude"].build_command(
        {"BIBI_JOB_PROMPT": "hi", "BIBI_JOB_SOUL": "data", "BIBI_WORKTREE": str(tmp_path)})
    i = argv.index("--append-system-prompt")
    assert argv[i + 1] == "Du bist Data."


def test_claude_argv_no_souls_dir_no_flag(tmp_path: Path):
    argv = wrapper.REGISTRY["claude"].build_command(
        {"BIBI_JOB_PROMPT": "hi", "BIBI_JOB_SOUL": "data", "BIBI_WORKTREE": str(tmp_path)})
    assert "--append-system-prompt" not in argv


def test_claude_argv_souls_dir_without_match_no_flag(tmp_path: Path):
    souls = tmp_path / ".claude" / "souls"
    souls.mkdir(parents=True)
    (souls / "01.Rook.SOUL.md").write_text("Du bist Rook.", encoding="utf-8")
    argv = wrapper.REGISTRY["claude"].build_command(
        {"BIBI_JOB_PROMPT": "hi", "BIBI_JOB_SOUL": "data", "BIBI_WORKTREE": str(tmp_path)})
    assert "--append-system-prompt" not in argv


def test_claude_argv_no_soul_no_flag(tmp_path: Path):
    souls = tmp_path / ".claude" / "souls"
    souls.mkdir(parents=True)
    (souls / "12.Data.SOUL.md").write_text("Du bist Data.", encoding="utf-8")
    argv = wrapper.REGISTRY["claude"].build_command(
        {"BIBI_JOB_PROMPT": "hi", "BIBI_WORKTREE": str(tmp_path)})
    assert "--append-system-prompt" not in argv


def test_claude_argv_soul_multiple_candidates_deterministic_first_sorted(tmp_path: Path):
    souls = tmp_path / ".claude" / "souls"
    souls.mkdir(parents=True)
    (souls / "12.Data.SOUL.md").write_text("erste", encoding="utf-8")
    (souls / "99.Data.SOUL.md").write_text("zweite", encoding="utf-8")
    argv = wrapper.REGISTRY["claude"].build_command(
        {"BIBI_JOB_PROMPT": "hi", "BIBI_JOB_SOUL": "data", "BIBI_WORKTREE": str(tmp_path)})
    i = argv.index("--append-system-prompt")
    assert argv[i + 1] == "erste"


def test_run_job_claude_via_stub(tmp_path: Path):
    # claude-Pfad end-to-end ohne echtes claude — Stub-Binary echot.
    fake = tmp_path / "fakeclaude"
    fake.write_text("#!/bin/sh\necho claude-hallo\n", encoding="utf-8")
    fake.chmod(0o755)
    out = tmp_path / "output.jsonl"
    env = {
        "BIBI_JOB_TYPE": "claude", "BIBI_OUTPUT_PATH": str(out),
        "BIBI_JOB_PROMPT": "Antworte hallo", "BIBI_CLAUDE_BIN": str(fake),
    }
    assert wrapper.run_job(env) == 0
    assert output.lines(out, "out") == ["claude-hallo"]


def test_run_job_executes_and_captures(tmp_path: Path):
    out = tmp_path / "output.jsonl"
    env = {
        "BIBI_JOB_TYPE": "job",
        "BIBI_OUTPUT_PATH": str(out),
        "BIBI_JOB_CMD": "echo hallo && echo fertig",
    }
    code = wrapper.run_job(env)
    assert code == 0
    assert output.lines(out, "out") == ["hallo", "fertig"]


def test_run_job_captures_stderr_and_exit(tmp_path: Path):
    out = tmp_path / "output.jsonl"
    env = {
        "BIBI_JOB_TYPE": "job",
        "BIBI_OUTPUT_PATH": str(out),
        "BIBI_JOB_CMD": "echo oops 1>&2; exit 3",
    }
    code = wrapper.run_job(env)
    assert code == 3
    assert output.lines(out, "err") == ["oops"]


def test_run_job_runs_in_worktree(tmp_path: Path):
    wt = tmp_path / "wt"
    wt.mkdir()
    out = tmp_path / "output.jsonl"
    env = {
        "BIBI_JOB_TYPE": "job",
        "BIBI_OUTPUT_PATH": str(out),
        "BIBI_WORKTREE": str(wt),
        "BIBI_JOB_CMD": "pwd",
    }
    wrapper.run_job(env)
    assert output.lines(out, "out")[0].endswith("/wt")


def test_unknown_type_raises(tmp_path: Path):
    with pytest.raises(KeyError):
        wrapper.run_job({"BIBI_JOB_TYPE": "bogus", "BIBI_OUTPUT_PATH": str(tmp_path / "o")})
