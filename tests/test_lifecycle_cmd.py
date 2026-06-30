"""Integrationstests für `bibi-ctrl close|done|delete` (PLAN-1 §1.3)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from bibi import case_store, state
from bibi.ctrl import main

import pytest
pytestmark = pytest.mark.slow


def _sh(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True,
                          capture_output=True, text=True).stdout


def _origin_head(origin: Path) -> str:
    return _sh(origin, "log", "-1", "--pretty=%s").strip()


def _local_head(root: Path) -> str:
    return _sh(root, "log", "-1", "--pretty=%s").strip()


def _activate(root: Path, topic: str) -> Path:
    """Case anlegen, cwd hineinparken, Display-Mirror setzen (wie /open)."""
    folder = case_store.create_case(topic)
    state.set_path(f"case/{folder.name}")
    os.chdir(folder)
    return folder


# --- close ---

def test_close_requires_active_case(repo_with_origin, capsys):
    root, _ = repo_with_origin  # cwd = root, kein aktiver Case
    rc = main(["close"])
    assert rc == 2
    assert "open" in capsys.readouterr().err


def test_close_pauses_clears_path_unparks(repo_with_origin, capsys):
    root, origin = repo_with_origin
    folder = _activate(root, "Alpha")
    rc = main(["close", "--push"])
    assert rc == 0
    assert case_store.get_status(folder) == "paused"
    assert "path" not in state.read()                 # Mirror geleert
    assert _local_head(root) == f"close: {folder.name}"
    assert f"cd: {root.resolve()}" in capsys.readouterr().out  # un-park


def test_close_push_gating(repo_with_origin):
    root, origin = repo_with_origin
    folder = _activate(root, "Alpha")
    main(["close"])  # auto_sync off
    assert _origin_head(origin) == "init"             # nicht gepusht
    assert case_store.get_status(folder) == "paused"  # lokal trotzdem pausiert


# --- done ---

def test_done_requires_active_case(repo_with_origin):
    root, _ = repo_with_origin
    assert main(["done"]) == 2


def test_done_closes_final(repo_with_origin):
    root, origin = repo_with_origin
    folder = _activate(root, "Alpha")
    rc = main(["done", "--push"])
    assert rc == 0
    assert case_store.get_status(folder) == "closed"
    assert _origin_head(origin) == f"done: {folder.name}"
    assert "path" not in state.read()


# --- delete ---

def test_delete_requires_active_case(repo_with_origin):
    root, _ = repo_with_origin
    assert main(["delete"]) == 2


def test_delete_tracked_folder_and_unparks(repo_with_origin, capsys):
    root, origin = repo_with_origin
    folder = _activate(root, "Alpha")
    main(["save", "--push"])          # Case erst tracken (wie nach /save)
    capsys.readouterr()
    rc = main(["delete", "--push"])
    assert rc == 0
    assert not folder.exists()                        # Ordner weg
    assert _origin_head(origin) == f"delete: {folder.name}"
    assert "path" not in state.read()
    assert f"cd: {root.resolve()}" in capsys.readouterr().out


def test_delete_untracked_case_is_ok(repo_with_origin):
    root, origin = repo_with_origin
    folder = _activate(root, "Alpha")  # nie gespeichert → untracked
    rc = main(["delete"])
    assert rc == 0
    assert not folder.exists()                        # trotzdem entfernt
    assert _origin_head(origin) == "init"             # kein Commit nötig


def test_delete_push_gating(repo_with_origin):
    root, origin = repo_with_origin
    folder = _activate(root, "Alpha")
    main(["save", "--push"])          # origin trägt jetzt "save: <name>"
    main(["delete"])                  # auto_sync off → delete nicht gepusht
    assert not folder.exists()                        # lokal entfernt
    assert _origin_head(origin) == f"save: {folder.name}"   # delete nicht auf origin
    assert _local_head(root) == f"delete: {folder.name}"    # lokal committed
