"""Reine Commit-Cluster-Logik für `/sync` (PLAN-25 Befund 8) — keine
Git-Aufrufe, volle Testbarkeit ohne echtes Repo (anders als test_git_ops.py/
test_sync_cmd.py, die echte Git-IO brauchen und deshalb @pytest.mark.slow
sind)."""

from __future__ import annotations

from bibi import git_ops


def test_cluster_active_case_paths_are_isolated():
    paths = ["vault/case/20260101.a-abc/README.md", "vault/case/20260101.a-abc/notes.md"]
    other, caseless, active = git_ops.cluster_dirty_paths(
        paths, case_dir_name="case", active_case_rel="case/20260101.a-abc")
    assert other == {}
    assert caseless == []
    assert active == paths


def test_cluster_other_case_becomes_its_own_group():
    paths = ["vault/case/20260101.a-abc/x.md", "vault/case/20260202.b-def/y.md"]
    other, caseless, active = git_ops.cluster_dirty_paths(
        paths, case_dir_name="case", active_case_rel="case/20260101.a-abc")
    assert other == {"case/20260202.b-def": ["vault/case/20260202.b-def/y.md"]}
    assert active == ["vault/case/20260101.a-abc/x.md"]
    assert caseless == []


def test_cluster_multiple_other_cases_grouped_separately():
    paths = [
        "vault/case/20260202.b-def/y.md",
        "vault/case/20260303.c-ghi/z.md",
        "vault/case/20260202.b-def/sub/w.md",
    ]
    other, caseless, active = git_ops.cluster_dirty_paths(
        paths, case_dir_name="case", active_case_rel=None)
    assert other == {
        "case/20260202.b-def": ["vault/case/20260202.b-def/y.md",
                                "vault/case/20260202.b-def/sub/w.md"],
        "case/20260303.c-ghi": ["vault/case/20260303.c-ghi/z.md"],
    }
    assert caseless == []
    assert active == []


def test_cluster_no_active_case_treats_all_cases_as_other():
    # PLAN-25 Befund 8, Punkt 1: "egal ob mit oder ohne aktives Projekt" —
    # ohne geparkten Case ist kein Case-Ordner "aktiv", also wird jeder zum
    # eigenen Cluster.
    paths = ["vault/case/20260101.a-abc/x.md"]
    other, caseless, active = git_ops.cluster_dirty_paths(
        paths, case_dir_name="case", active_case_rel=None)
    assert other == {"case/20260101.a-abc": ["vault/case/20260101.a-abc/x.md"]}
    assert active == []


def test_cluster_non_vault_and_memo_attach_are_caseless():
    paths = ["pyproject.toml", ".claude/settings.json", "vault/memo/x.md", "vault/attach/y.png"]
    other, caseless, active = git_ops.cluster_dirty_paths(
        paths, case_dir_name="case", active_case_rel=None)
    assert other == {}
    assert active == []
    assert caseless == paths


def test_cluster_respects_custom_case_dir_name():
    # bibi3-Kompat: case_dir = "project" statt "case" (pyproject [tool.bibi]).
    paths = ["vault/project/20260101.a-abc/x.md"]
    other, caseless, active = git_ops.cluster_dirty_paths(
        paths, case_dir_name="project", active_case_rel="project/20260101.a-abc")
    assert other == {}
    assert active == paths
    assert caseless == []


def test_cluster_empty_input():
    other, caseless, active = git_ops.cluster_dirty_paths(
        [], case_dir_name="case", active_case_rel=None)
    assert other == {} and caseless == [] and active == []
