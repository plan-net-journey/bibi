"""Repo-/Vault-Hygiene (PLAN-5 §5.2) — reine Checks + doctor-CLI."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from bibi import hygiene, repo
from bibi.ctrl import doctor_cmd


# ── reine Checks ──────────────────────────────────────────────────────────────

def test_large_unmanaged_flags_big_non_lfs_only():
    files = [
        ("vault/a.png", 900_000, False),   # groß + nicht LFS → Befund
        ("vault/b.png", 900_000, True),    # groß, aber LFS → ok
        ("README.md", 2_000, False),       # klein → ok
    ]
    findings = hygiene.check_large_unmanaged(files)
    assert [f.path for f in findings] == ["vault/a.png"]
    assert findings[0].kind == "large-unmanaged"


def test_data_committed_flags_vault_data_paths():
    paths = [
        "vault/case/news/data/2026-06-27.ndjson",  # Sammeldaten → Befund
        "vault/case/news/README.md",               # ok
        "data/jobs.sqlite",                        # root-runtime (nicht vault/) → ok
    ]
    findings = hygiene.check_data_committed(paths)
    assert [f.path for f in findings] == ["vault/case/news/data/2026-06-27.ndjson"]


def test_lfs_finding():
    assert hygiene.git_lfs_finding(True) == []
    assert hygiene.git_lfs_finding(False)[0].kind == "lfs-missing"


def test_conventions_finding():
    assert hygiene.conventions_finding(True) == []
    f = hygiene.conventions_finding(False)
    assert f and f[0].kind == "conventions-missing"
    assert f[0].path == "vault/CONVENTIONS.md"


# ── PLAN-13 Stufe 13.3: job-doctor-Checks ─────────────────────────────────────


def test_orphan_worktrees_flags_unknown_slug_only():
    findings = hygiene.check_orphan_worktrees(
        worktree_slugs=["Runner", "gone-job"],
        known_slugs={"Runner", "Witz"},
    )
    assert [f.path for f in findings] == ["data/worktrees/gone-job"]
    assert findings[0].kind == "orphan-worktree"


def test_orphan_worktrees_deactivated_but_known_slug_is_not_orphan():
    # Ein pausierter/deaktivierter Slug hat noch eine jobs-Zeile — kein Befund.
    findings = hygiene.check_orphan_worktrees(
        worktree_slugs=["paused-job"],
        known_slugs={"paused-job"},
    )
    assert findings == []


def test_orphan_worktrees_empty_input_no_findings():
    assert hygiene.check_orphan_worktrees([], set()) == []


def test_invalid_schedules_reports_parser_errors():
    from bibi.schedule.parser import ParseResult
    errors = [
        ParseResult(schedule_ref="vault/case/x/Broken.md", error="Frontmatter braucht `job:`"),
    ]
    findings = hygiene.check_invalid_schedules(errors)
    assert len(findings) == 1
    assert findings[0].kind == "invalid-schedule"
    assert findings[0].path == "vault/case/x/Broken.md"
    assert findings[0].detail == "Frontmatter braucht `job:`"


def test_invalid_schedules_empty_input_no_findings():
    assert hygiene.check_invalid_schedules([]) == []


# ── doctor-CLI gegen ein echtes Mini-Repo ─────────────────────────────────────

def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


@pytest.fixture
def gitrepo(tmp_path: Path, monkeypatch):
    root = tmp_path / "r"
    root.mkdir()
    _git(root, "init", "-q", "-b", "trunk")
    _git(root, "config", "user.email", "t@e.x")
    _git(root, "config", "user.name", "t")
    (root / "README.md").write_text("hi\n", encoding="utf-8")
    # Repo-Invariant: jedes bibi-team-Repo führt vault/CONVENTIONS.md (sonst Befund).
    (root / "vault").mkdir()
    (root / "vault" / "CONVENTIONS.md").write_text("# conventions\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "init")
    monkeypatch.chdir(root)
    repo._root_of.cache_clear()
    yield root
    repo._root_of.cache_clear()


def _args():
    import argparse
    return argparse.Namespace()


def test_doctor_clean_repo(gitrepo: Path, capsys, monkeypatch):
    monkeypatch.setattr(hygiene, "git_lfs_installed", lambda: True)
    assert doctor_cmd.run(_args()) == 0
    assert "keine Hygiene-Probleme" in capsys.readouterr().out


def test_doctor_flags_large_unmanaged_blob(gitrepo: Path, capsys, monkeypatch):
    monkeypatch.setattr(hygiene, "git_lfs_installed", lambda: True)
    big = gitrepo / "vault" / "huge.bin"
    big.parent.mkdir(parents=True, exist_ok=True)
    big.write_bytes(b"x" * (600 * 1024))  # 600 KiB, kein LFS (kein .gitattributes)
    _git(gitrepo, "add", "-A")
    _git(gitrepo, "commit", "-q", "-m", "big")
    rc = doctor_cmd.run(_args())
    out = capsys.readouterr().out
    assert rc == 1 and "large-unmanaged" in out and "vault/huge.bin" in out


def test_doctor_flags_missing_conventions(gitrepo: Path, capsys, monkeypatch):
    monkeypatch.setattr(hygiene, "git_lfs_installed", lambda: True)
    _git(gitrepo, "rm", "-q", "vault/CONVENTIONS.md")
    _git(gitrepo, "commit", "-q", "-m", "drop conventions")
    rc = doctor_cmd.run(_args())
    out = capsys.readouterr().out
    assert rc == 1 and "conventions-missing" in out and "vault/CONVENTIONS.md" in out


def test_doctor_flags_committed_data(gitrepo: Path, capsys, monkeypatch):
    monkeypatch.setattr(hygiene, "git_lfs_installed", lambda: True)
    d = gitrepo / "vault" / "case" / "news" / "data"
    d.mkdir(parents=True)
    (d / "feed.ndjson").write_text('{"x":1}\n', encoding="utf-8")
    _git(gitrepo, "add", "-A")
    _git(gitrepo, "commit", "-q", "-m", "data")
    rc = doctor_cmd.run(_args())
    out = capsys.readouterr().out
    assert rc == 1 and "data-committed" in out


def test_doctor_flags_orphan_worktree(gitrepo: Path, capsys, monkeypatch):
    monkeypatch.setattr(hygiene, "git_lfs_installed", lambda: True)
    (gitrepo / "data" / "worktrees" / "gone-job").mkdir(parents=True)
    rc = doctor_cmd.run(_args())
    out = capsys.readouterr().out
    assert rc == 1
    assert "orphan-worktree" in out and "data/worktrees/gone-job" in out


def test_doctor_ignores_worktree_with_known_slug(gitrepo: Path, capsys, monkeypatch):
    monkeypatch.setattr(hygiene, "git_lfs_installed", lambda: True)
    (gitrepo / "data" / "worktrees" / "Runner").mkdir(parents=True)
    d = gitrepo / "vault" / "case" / "20260717.Test-aaaaaaaa"
    d.mkdir(parents=True)
    (d / "Runner.md").write_text('---\nschedule: "*/5 * * * *"\njob: "echo hi"\n---\n',
                                 encoding="utf-8")
    from bibi.daemon import job_db as jdb
    conn = jdb.connect(gitrepo / "data" / "jobs.sqlite")
    jdb.rescan(conn, vault_root=gitrepo / "vault" / "case")
    conn.close()
    rc = doctor_cmd.run(_args())
    out = capsys.readouterr().out
    assert rc == 0
    assert "orphan-worktree" not in out


def test_doctor_flags_invalid_schedule(gitrepo: Path, capsys, monkeypatch):
    monkeypatch.setattr(hygiene, "git_lfs_installed", lambda: True)
    d = gitrepo / "vault" / "case" / "20260717.Test-aaaaaaaa"
    d.mkdir(parents=True)
    (d / "Broken.md").write_text("---\nschedule: \"*/5 * * * *\"\n---\n", encoding="utf-8")  # kein job:
    rc = doctor_cmd.run(_args())
    out = capsys.readouterr().out
    assert rc == 1
    assert "invalid-schedule" in out and "Broken.md" in out


# ── PLAN-15: html-placeholder-tag ─────────────────────────────────────────────


def test_html_placeholder_tag_flags_bare_placeholder():
    findings = hygiene.check_html_placeholder_tags("x.md", "Text mit <cutoff> drin.\n")
    assert len(findings) == 1
    assert findings[0].kind == "html-placeholder-tag"
    assert findings[0].path == "x.md:1"


def test_html_placeholder_tag_flags_script_looking_fragment_too():
    # Kein "sieht nach echtem HTML aus"-Sonderfall — beides gleich riskant.
    findings = hygiene.check_html_placeholder_tags("x.md", "<script>alert(1)\n")
    assert findings and findings[0].kind == "html-placeholder-tag"


def test_html_placeholder_tag_ignores_autolinks():
    findings = hygiene.check_html_placeholder_tags(
        "x.md", "Siehe <https://example.com> und <mailto:a@b.de>.\n")
    assert findings == []


def test_html_placeholder_tag_ignores_fenced_code():
    text = "vorher\n```\n<cutoff>\n```\nnachher\n"
    assert hygiene.check_html_placeholder_tags("x.md", text) == []


def test_html_placeholder_tag_ignores_backtick_escaped():
    # Die empfohlene Lösung selbst darf nicht erneut anschlagen.
    findings = hygiene.check_html_placeholder_tags("x.md", "Text mit `<cutoff>` drin.\n")
    assert findings == []


def test_html_placeholder_tag_ignores_double_backtick_escaped():
    # Bug live gefunden (2026-07-18): doppelte Backticks (die Markdown-Syntax,
    # um einen wörtlichen Backtick IM Code-Span zu zeigen, z. B. in
    # CONVENTIONS.md selbst) wurden vom einfachen Backtick-Scrubbing nicht
    # erkannt — `` `<cutoff>` `` schlug fälschlich als Fund durch.
    findings = hygiene.check_html_placeholder_tags(
        "x.md", "Schreib es als `` `<cutoff>` `` statt roh.\n")
    assert findings == []


def test_html_placeholder_tag_ignores_indented_code():
    assert hygiene.check_html_placeholder_tags("x.md", "    <cutoff>\n") == []


def test_html_placeholder_tag_multiple_on_one_line():
    findings = hygiene.check_html_placeholder_tags("x.md", "<from> bis <to>\n")
    assert len(findings) == 2


# ── PLAN-15: markdown-hardwrap ────────────────────────────────────────────────


def test_hardwrap_flags_two_consecutive_prose_lines_as_one_finding():
    text = "Erste Zeile eines Absatzes\nzweite Zeile desselben Absatzes\n"
    findings = hygiene.check_markdown_hardwrap("x.md", text)
    assert len(findings) == 1
    assert findings[0].kind == "markdown-hardwrap"
    assert findings[0].path == "x.md:1-2"


def test_hardwrap_five_line_paragraph_is_one_finding_not_four():
    text = "\n".join(f"Zeile {i} desselben Absatzes" for i in range(1, 6)) + "\n"
    findings = hygiene.check_markdown_hardwrap("x.md", text)
    assert len(findings) == 1
    assert findings[0].path == "x.md:1-5"
    assert "5 Zeilen" in findings[0].detail


def test_hardwrap_single_line_paragraph_is_ok():
    text = "Ein Absatz, beliebig lang, aber eine einzige physische Zeile.\n\nZweiter Absatz.\n"
    assert hygiene.check_markdown_hardwrap("x.md", text) == []


def test_hardwrap_ignores_lists_tables_blockquotes():
    text = (
        "- Listenpunkt eins\n- Listenpunkt zwei\n\n"
        "| a | b |\n| - | - |\n\n"
        "> Zitatzeile eins\n> Zitatzeile zwei\n"
    )
    assert hygiene.check_markdown_hardwrap("x.md", text) == []


def test_hardwrap_ignores_fenced_code_and_frontmatter():
    text = "---\nkey: val\nnoch eine\n---\n\n```\ncode zeile eins\ncode zeile zwei\n```\n"
    assert hygiene.check_markdown_hardwrap("x.md", text) == []


def test_hardwrap_two_separate_paragraphs_are_two_findings():
    text = (
        "Absatz eins Zeile eins\nAbsatz eins Zeile zwei\n\n"
        "Absatz zwei Zeile eins\nAbsatz zwei Zeile zwei\n"
    )
    findings = hygiene.check_markdown_hardwrap("x.md", text)
    assert [f.path for f in findings] == ["x.md:1-2", "x.md:4-5"]


# ── PLAN-15: doctor-CLI liest jetzt Datei-Inhalte unter vault/ ───────────────


def test_doctor_flags_placeholder_tag_in_vault_md(gitrepo: Path, capsys, monkeypatch):
    monkeypatch.setattr(hygiene, "git_lfs_installed", lambda: True)
    (gitrepo / "vault" / "note.md").write_text("Bitte <cutoff> ersetzen.\n", encoding="utf-8")
    rc = doctor_cmd.run(_args())
    out = capsys.readouterr().out
    assert rc == 1
    assert "html-placeholder-tag" in out and "note.md:1" in out


def test_doctor_flags_hardwrapped_paragraph_in_vault_md(gitrepo: Path, capsys, monkeypatch):
    monkeypatch.setattr(hygiene, "git_lfs_installed", lambda: True)
    (gitrepo / "vault" / "note.md").write_text(
        "Erste Zeile eines Absatzes\nzweite Zeile desselben Absatzes\n", encoding="utf-8")
    rc = doctor_cmd.run(_args())
    out = capsys.readouterr().out
    assert rc == 1
    assert "markdown-hardwrap" in out and "note.md:1-2" in out


def test_doctor_ignores_backtick_escaped_placeholder_in_vault_md(gitrepo: Path, capsys, monkeypatch):
    monkeypatch.setattr(hygiene, "git_lfs_installed", lambda: True)
    (gitrepo / "vault" / "note.md").write_text("Bitte `<cutoff>` ersetzen.\n", encoding="utf-8")
    assert doctor_cmd.run(_args()) == 0
