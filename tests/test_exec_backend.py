"""Exec-Backend: Host vs. Container (PLAN-8 Slice A)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from bibi.wrapper import exec_backend, output, run_job

pytestmark = pytest.mark.slow


# ── reine argv-Konstruktion (kein Docker) ────────────────────────────────────

def test_host_mode_is_child_argv_with_worktree_cwd():
    spec = exec_backend.build_exec(
        ["bash", "-c", "echo hi"], {"BIBI_WORKTREE": "/wt"})
    assert spec.argv == ["bash", "-c", "echo hi"]
    assert spec.cwd == "/wt"


def test_host_mode_uses_job_cwd_when_set():
    spec = exec_backend.build_exec(
        ["bash", "-c", "echo hi"],
        {"BIBI_WORKTREE": "/wt", "BIBI_JOB_CWD": "/wt/vault/case/foo"})
    assert spec.cwd == "/wt/vault/case/foo"


def test_host_mode_falls_back_to_worktree_without_job_cwd():
    spec = exec_backend.build_exec(
        ["bash", "-c", "echo hi"], {"BIBI_WORKTREE": "/wt"})
    assert spec.cwd == "/wt"


def test_container_mode_wraps_in_docker_run(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(exec_backend, "_host_uid", lambda: 1000)
    data_home = tmp_path / "data-home"
    env = {"BIBI_EXEC_MODE": "container", "BIBI_WORKTREE": "/wt",
           "BIBI_JOB_ID": "abc123", "BIBI_DOCKER_BIN": "/d/docker",
           "BIBI_JOB_IMAGE": "img:1", "PATH": "/usr/bin",
           "BIBI_DATA_HOME": str(data_home)}
    spec = exec_backend.build_exec(["claude", "-p", "x"], env)
    assert spec.cwd is None
    assert spec.argv[:15] == [
        "/d/docker", "run", "--rm", "--name", "bibi-abc123",
        "--user", "1000:0",
        "-v", "/wt:/workspace", "-w", "/workspace",
        "-e", "HOME=/root",
        "-v", f"{data_home}:/root/.local/share/bibi"]
    assert spec.argv[-3:] == ["img:1", "claude", "-p", "x"][-3:]
    assert spec.argv[-4:] == ["img:1", "claude", "-p", "x"]
    # docker-bin-Dir vorne im PATH (Cred-Helper)
    assert spec.env["PATH"].startswith("/d" + os.pathsep)
    # generischer Daten-Mount legt sein Ziel an, falls es noch fehlt (User-Fund
    # 2026-07-14: gmail-transfer verlor externen State ohne diesen Mount).
    assert data_home.is_dir()


def test_container_mode_uses_job_cwd_as_workdir_subpath(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(exec_backend, "_host_uid", lambda: 1000)
    data_home = tmp_path / "data-home"
    env = {"BIBI_EXEC_MODE": "container", "BIBI_WORKTREE": "/wt",
           "BIBI_JOB_CWD": "/wt/vault/case/foo",
           "BIBI_JOB_ID": "abc123", "BIBI_DOCKER_BIN": "/d/docker",
           "BIBI_JOB_IMAGE": "img:1", "PATH": "/usr/bin",
           "BIBI_DATA_HOME": str(data_home)}
    spec = exec_backend.build_exec(["claude", "-p", "x"], env)
    assert spec.argv[:15] == [
        "/d/docker", "run", "--rm", "--name", "bibi-abc123",
        "--user", "1000:0",
        "-v", "/wt:/workspace", "-w", "/workspace/vault/case/foo",
        "-e", "HOME=/root",
        "-v", f"{data_home}:/root/.local/share/bibi"]


def test_container_mode_mounts_repo_root_when_worktree_is_repo_root(
    monkeypatch, tmp_path: Path):
    # User-Fund 2026-07-14 (bibi-ctrl test / in_place): build_exec() braucht
    # KEINE Änderung, um "dirty trunk containerisiert testen" zu unterstützen —
    # es behandelt BIBI_WORKTREE ohnehin als reinen, opaken Pfad. _run_wrapper()
    # setzt bei in_place=True einfach BIBI_WORKTREE=repo_root statt eines
    # frischen Worktree-Pfads; dieser Test dokumentiert/beweist genau das:
    # der Live-Checkout landet unverändert als Mount-Quelle im argv.
    monkeypatch.setattr(exec_backend, "_host_uid", lambda: 1000)
    repo_root = tmp_path / "live-checkout"
    repo_root.mkdir()
    data_home = tmp_path / "data-home"
    env = {"BIBI_EXEC_MODE": "container", "BIBI_WORKTREE": str(repo_root),
           "BIBI_JOB_ID": "abc123", "BIBI_DOCKER_BIN": "/d/docker",
           "BIBI_JOB_IMAGE": "img:1", "PATH": "/usr/bin",
           "BIBI_DATA_HOME": str(data_home)}
    spec = exec_backend.build_exec(["bash", "-c", "echo hi"], env)
    assert f"{repo_root}:/workspace" in spec.argv


def test_container_mode_runs_as_mapped_host_user(tmp_path: Path):
    # PLAN-24 Befund 5: "arbitrary UID"-Konvention statt dauerhaft root — GID
    # immer 0 ("root"-Gruppe, passwortloses sudo im Image), UID = Host-UID
    # (im Bind-Mount geschriebene Dateien gehören dadurch dem Host-User).
    env = {"BIBI_EXEC_MODE": "container", "BIBI_WORKTREE": "/wt",
           "BIBI_JOB_ID": "j", "BIBI_DOCKER_BIN": "/d/docker",
           "BIBI_DATA_HOME": str(tmp_path / "data-home")}
    spec = exec_backend.build_exec(["sh"], env)
    assert "--user" in spec.argv
    i = spec.argv.index("--user")
    assert spec.argv[i + 1] == f"{os.getuid()}:0"
    assert "HOME=/root" in spec.argv


def test_container_passes_bibi_job_id(tmp_path: Path):
    # User-Fund 2026-07-21 (Bibi4 Batch 6, "Reset Test (Container)"): BIBI_*
    # wird standardmäßig NICHT in den Container durchgereicht (nur die
    # statische _CONTAINER_ENV-Liste + Nicht-BIBI_-Keys) — jedes Skript, das
    # sich per BIBI_JOB_ID scopen wollte (External-job-data-Konvention,
    # bibi.job.data_dir()), sah im Container immer den "adhoc"-Fallback statt
    # der echten, stabilen Job-ID. RESET konnte das entsprechende Verzeichnis
    # deshalb nie treffen (job_db.wipe_job_data() globbt nach der echten ID).
    spec = exec_backend.build_exec(["sh"], {
        "BIBI_EXEC_MODE": "container", "BIBI_WORKTREE": "/wt",
        "BIBI_JOB_ID": "realjobid123", "BIBI_DOCKER_BIN": "/d/docker",
        "BIBI_DATA_HOME": str(tmp_path / "data-home")})
    assert "BIBI_JOB_ID" in spec.argv
    i = spec.argv.index("BIBI_JOB_ID")
    assert spec.argv[i - 1] == "-e"   # nur Name, Wert kommt aus spec.env
    assert spec.env["BIBI_JOB_ID"] == "realjobid123"


def test_container_passes_api_key_only_when_set(tmp_path: Path):
    base = {"BIBI_EXEC_MODE": "container", "BIBI_WORKTREE": "/wt",
            "BIBI_JOB_ID": "j", "BIBI_DOCKER_BIN": "/d/docker",
            "BIBI_DATA_HOME": str(tmp_path / "data-home")}
    without = exec_backend.build_exec(["sh"], base)
    assert "ANTHROPIC_API_KEY" not in without.argv
    with_key = exec_backend.build_exec(["sh"], {**base, "ANTHROPIC_API_KEY": "sk-x"})
    assert "ANTHROPIC_API_KEY" in with_key.argv
    i = with_key.argv.index("ANTHROPIC_API_KEY")
    assert with_key.argv[i - 1] == "-e"   # nur Name, Wert vom Host


def test_container_passes_oauth_token(tmp_path: Path):
    # claude-code Abo-Auth via CLAUDE_CODE_OAUTH_TOKEN (sk-ant-oat…), nicht API-Key.
    spec = exec_backend.build_exec(["claude"], {
        "BIBI_EXEC_MODE": "container", "BIBI_WORKTREE": "/wt", "BIBI_JOB_ID": "j",
        "BIBI_DOCKER_BIN": "/d/docker", "CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat-x",
        "BIBI_DATA_HOME": str(tmp_path / "data-home")})
    assert "CLAUDE_CODE_OAUTH_TOKEN" in spec.argv
    i = spec.argv.index("CLAUDE_CODE_OAUTH_TOKEN")
    assert spec.argv[i - 1] == "-e"   # -e CLAUDE_CODE_OAUTH_TOKEN (Wert vom Host)


def test_default_image_when_unset(tmp_path: Path):
    spec = exec_backend.build_exec(
        ["sh"], {"BIBI_EXEC_MODE": "container", "BIBI_WORKTREE": "/wt",
                 "BIBI_JOB_ID": "j", "BIBI_DOCKER_BIN": "/d/docker",
                 "BIBI_DATA_HOME": str(tmp_path / "data-home")})
    assert exec_backend.DEFAULT_IMAGE in spec.argv


# ── Generischer Daten-Mount (User-Fund 2026-07-14) ───────────────────────────

def test_container_mode_mounts_data_home(tmp_path: Path):
    data_home = tmp_path / "data-home"
    env = {"BIBI_EXEC_MODE": "container", "BIBI_WORKTREE": "/wt",
           "BIBI_JOB_ID": "j", "BIBI_DOCKER_BIN": "/d/docker",
           "BIBI_DATA_HOME": str(data_home)}
    spec = exec_backend.build_exec(["sh"], env)
    assert "-v" in spec.argv
    mounts = [spec.argv[i + 1] for i, a in enumerate(spec.argv) if a == "-v"]
    assert f"{data_home}:/root/.local/share/bibi" in mounts


def test_container_mode_creates_data_home_if_missing(tmp_path: Path):
    data_home = tmp_path / "nested" / "data-home"
    assert not data_home.exists()
    env = {"BIBI_EXEC_MODE": "container", "BIBI_WORKTREE": "/wt",
           "BIBI_JOB_ID": "j", "BIBI_DOCKER_BIN": "/d/docker",
           "BIBI_DATA_HOME": str(data_home)}
    exec_backend.build_exec(["sh"], env)
    assert data_home.is_dir()


def test_data_home_default_is_xdg_local_share_bibi():
    # Ohne BIBI_DATA_HOME-Override (Normalfall) — dieselbe Konvention wie die
    # Vault-Scripts (Path.home()/".local/share/bibi"/<subsystem>), sonst
    # würde derselbe Pfad im Host- und Container-Modus auseinanderlaufen.
    assert exec_backend._data_home({}) == Path.home() / ".local" / "share" / "bibi"


def test_host_mode_does_not_touch_data_home():
    # Host-Modus braucht keinen Mount (echtes Dateisystem) — build_exec() darf
    # dort nichts anlegen/anfassen.
    spec = exec_backend.build_exec(["echo", "hi"], {"BIBI_WORKTREE": "/wt"})
    assert "-v" not in spec.argv


# ── PLAN-24 Befund 5: Job-Image-Persistenz ───────────────────────────────────

def test_container_mode_uses_rm_without_persist_flag(tmp_path: Path):
    env = {"BIBI_EXEC_MODE": "container", "BIBI_WORKTREE": "/wt",
           "BIBI_JOB_ID": "j", "BIBI_DOCKER_BIN": "/d/docker",
           "BIBI_DATA_HOME": str(tmp_path / "data-home")}
    spec = exec_backend.build_exec(["sh"], env)
    assert "--rm" in spec.argv


def test_container_mode_omits_rm_with_persist_flag(tmp_path: Path):
    # Der Container muss nach dem Lauf noch existieren, damit `docker commit`
    # ihn snapshotten kann — finalize_container() räumt danach selbst auf.
    env = {"BIBI_EXEC_MODE": "container", "BIBI_WORKTREE": "/wt",
           "BIBI_JOB_ID": "j", "BIBI_DOCKER_BIN": "/d/docker",
           "BIBI_JOB_IMAGE_PERSIST": "1",
           "BIBI_DATA_HOME": str(tmp_path / "data-home")}
    spec = exec_backend.build_exec(["sh"], env)
    assert "--rm" not in spec.argv
    assert spec.argv[:2] == ["/d/docker", "run"]


def test_job_image_tag_shape():
    assert exec_backend.job_image_tag("hitl-test-app") == "bibi-job-hitl-test-app:latest"


def test_job_image_tag_sanitizes_unsafe_characters():
    # Docker-Repo-Namen erlauben nur lowercase + [a-z0-9._-] — Slugs (z. B.
    # explizites slug:-Frontmatter) könnten anderes enthalten.
    assert exec_backend.job_image_tag("MySlug With Spaces!") == "bibi-job-myslug-with-spaces:latest"
    assert exec_backend.job_image_tag("---") == "bibi-job-job:latest"  # nichts Sicheres übrig


def test_finalize_container_noop_without_persist_flag(monkeypatch):
    calls: list = []
    monkeypatch.setattr(exec_backend.subprocess, "run", lambda *a, **kw: calls.append(a))
    exec_backend.finalize_container({"BIBI_JOB_ID": "j", "BIBI_JOB_SLUG": "s"})
    assert calls == []


def test_finalize_container_commits_and_removes(monkeypatch):
    calls: list[list[str]] = []

    def fake_run(argv, **kw):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0)
    monkeypatch.setattr(exec_backend.subprocess, "run", fake_run)
    monkeypatch.setattr(exec_backend, "resolve_docker_bin", lambda env: "/usr/bin/docker")

    exec_backend.finalize_container({
        "BIBI_JOB_IMAGE_PERSIST": "1", "BIBI_JOB_ID": "abc123", "BIBI_JOB_SLUG": "myjob",
    })

    assert calls[0] == ["/usr/bin/docker", "commit", "bibi-abc123", "bibi-job-myjob:latest"]
    assert calls[1] == ["/usr/bin/docker", "rm", "-f", "bibi-abc123"]


def test_finalize_container_missing_slug_is_noop(monkeypatch):
    calls: list = []
    monkeypatch.setattr(exec_backend.subprocess, "run", lambda *a, **kw: calls.append(a))
    exec_backend.finalize_container({"BIBI_JOB_IMAGE_PERSIST": "1", "BIBI_JOB_ID": "abc123"})
    assert calls == []


def test_container_name():
    assert exec_backend.container_name("deadbeef") == "bibi-deadbeef"


# ── ZOMBIE-Fix: stop_container() — Gegenstück zu worker._docker(["stop", …])
# für Terminierungen, die der Wrapper selbst entscheidet (silence/wall_time/
# deferred/SIGTERM), s. _terminate_proc() in bibi/wrapper/__init__.py ──────

def test_stop_container_calls_docker_stop_in_container_mode(monkeypatch):
    calls: list[list[str]] = []

    def fake_run(argv, **kw):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0)
    monkeypatch.setattr(exec_backend.subprocess, "run", fake_run)
    monkeypatch.setattr(exec_backend, "resolve_docker_bin", lambda env: "/usr/bin/docker")

    exec_backend.stop_container({"BIBI_EXEC_MODE": "container", "BIBI_JOB_ID": "abc123"})

    assert calls == [["/usr/bin/docker", "stop", "bibi-abc123"]]


def test_stop_container_noop_in_host_mode(monkeypatch):
    calls: list = []
    monkeypatch.setattr(exec_backend.subprocess, "run", lambda *a, **kw: calls.append(a))
    exec_backend.stop_container({"BIBI_EXEC_MODE": "host", "BIBI_JOB_ID": "abc123"})
    exec_backend.stop_container({"BIBI_JOB_ID": "abc123"})  # Default ist host
    assert calls == []


def test_stop_container_noop_without_job_id(monkeypatch):
    calls: list = []
    monkeypatch.setattr(exec_backend.subprocess, "run", lambda *a, **kw: calls.append(a))
    exec_backend.stop_container({"BIBI_EXEC_MODE": "container"})
    assert calls == []


# ── Smoke gegen echtes Docker ────────────────────────────────────────────────

def _docker_ok() -> bool:
    bin_ = exec_backend.resolve_docker_bin(dict(os.environ))
    env = dict(os.environ)
    env["PATH"] = str(Path(bin_).parent) + os.pathsep + env.get("PATH", "")
    try:
        r = subprocess.run([bin_, "version", "--format", "{{.Server.Version}}"],
                           capture_output=True, text=True, env=env, timeout=15)
        return r.returncode == 0 and bool(r.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        return False


docker = pytest.mark.skipif(not _docker_ok(), reason="kein laufendes Docker")


@docker
def test_smoke_container_job_writes_workspace_and_output(tmp_path: Path):
    wt = tmp_path / "wt"
    wt.mkdir()
    out = tmp_path / "output.jsonl"
    env = {
        **os.environ,
        "BIBI_EXEC_MODE": "container",
        "BIBI_JOB_TYPE": "job",
        "BIBI_JOB_CMD": "echo hallo && echo neu > probe.txt && echo fertig",
        "BIBI_JOB_IMAGE": "bash:5",
        "BIBI_WORKTREE": str(wt),
        "BIBI_OUTPUT_PATH": str(out),
        "BIBI_JOB_ID": "smoke" + os.urandom(3).hex(),
        # isoliert vom echten ~/.local/share/bibi der Testmaschine:
        "BIBI_DATA_HOME": str(tmp_path / "data-home"),
    }
    code = run_job(env)
    assert code == 0
    # Output gepumpt (Host fängt die Container-stdout):
    assert output.lines(out, "out") == ["hallo", "fertig"]
    # Container schrieb in /workspace ⇒ Host-Worktree:
    assert (wt / "probe.txt").read_text().strip() == "neu"


@docker
def test_smoke_container_job_persists_data_home_across_runs(tmp_path: Path):
    # Der eigentliche Zweck des Mounts (User-Fund 2026-07-14): ein Job, der
    # unter ~/.local/share/bibi/... schreibt (die Vault-Konvention für
    # externe, worktree-übergreifende Job-Daten), muss das im NÄCHSTEN --rm-
    # Container-Lauf wiederfinden — vorher ging es mit jedem --rm verloren.
    #
    # BIBI_JOB_IMAGE bewusst NICHT bash:5 (anders als der Nachbar-Smoke-Test
    # oben, der nur /workspace anfasst): der Schreibzugriff unter /root
    # funktioniert nur, weil build_exec() den Container als beliebige
    # Host-UID mit GID 0 laufen lässt (PLAN-24 Befund 5) UND das Image dafür
    # /root gruppenschreibbar gemacht hat (bibi/docker/bibi-base/Dockerfile,
    # `chmod -R g+rwX /root`) — im unveränderten bash:5 bleibt /root
    # `drwx------ root:root`, jeder Schreibversuch scheitert live reproduziert
    # mit "Permission denied", unabhängig vom Mount selbst. bibi-base:dev ist
    # DEFAULT_IMAGE (der Fall, den echte Jobs ohne BIBI_JOB_IMAGE-Override
    # nutzen) und bereits lokal vorhanden.
    wt = tmp_path / "wt"
    wt.mkdir()
    data_home = tmp_path / "data-home"
    base_env = {
        **os.environ,
        "BIBI_EXEC_MODE": "container",
        "BIBI_JOB_TYPE": "job",
        "BIBI_JOB_IMAGE": "bibi-base:dev",
        "BIBI_WORKTREE": str(wt),
        "BIBI_DATA_HOME": str(data_home),
    }

    # Absoluter Pfad (nicht relativ zu /workspace!) — genau wie ein echtes
    # Vault-Skript per Path.home()/".local/share/bibi"/... auflöst, sobald
    # HOME=/root im Container gilt.
    env1 = {**base_env,
            "BIBI_JOB_CMD": "mkdir -p /root/.local/share/bibi/probe && "
                            "echo lauf1 >> /root/.local/share/bibi/probe/state.txt",
            "BIBI_OUTPUT_PATH": str(tmp_path / "out1.jsonl"),
            "BIBI_JOB_ID": "smoke" + os.urandom(3).hex()}
    assert run_job(env1) == 0

    env2 = {**base_env,
            "BIBI_JOB_CMD": "echo lauf2 >> /root/.local/share/bibi/probe/state.txt && "
                            "cat /root/.local/share/bibi/probe/state.txt",
            "BIBI_OUTPUT_PATH": str(tmp_path / "out2.jsonl"),
            "BIBI_JOB_ID": "smoke" + os.urandom(3).hex()}
    assert run_job(env2) == 0

    state = (data_home / "probe" / "state.txt").read_text()
    assert state.splitlines() == ["lauf1", "lauf2"]
