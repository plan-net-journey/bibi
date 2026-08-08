"""Wrapper: app-Typ, stdout-Signale, Traefik-Labels (PLAN-11.3)."""

from __future__ import annotations

import time
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from bibi import wrapper
from bibi.wrapper import exec_backend
from bibi.daemon import job_db

pytestmark = pytest.mark.slow


# ── Registry ─────────────────────────────────────────────────────────────────


def test_job_in_registry_is_hitl_capable():
    assert "job" in wrapper.REGISTRY
    h = wrapper.REGISTRY["job"]
    assert h.long_lived is True
    assert h.supports_hitl is True


def test_job_argv_uses_cmd():
    argv = wrapper.REGISTRY["job"].build_command(
        {"BIBI_JOB_CMD": "uvicorn myapp:app --port 8081"}
    )
    assert argv == ["bash", "-c", "uvicorn myapp:app --port 8081"]


def test_job_argv_empty_cmd():
    argv = wrapper.REGISTRY["job"].build_command({})
    assert argv == ["bash", "-c", ""]


# ── Traefik-Labels in exec_backend ───────────────────────────────────────────


def _base_env(tmp_path: Path, job_type: str = "app") -> dict:
    return {
        "BIBI_EXEC_MODE": "container",
        "BIBI_JOB_TYPE": job_type,
        "BIBI_JOB_ID": "deadbeef",
        "BIBI_WORKTREE": str(tmp_path),
        "BIBI_APP_PORT": "8081",
        "BIBI_APP_PREFIX": "/myapp",
        # isoliert vom echten ~/.local/share/bibi der Testmaschine (build_exec()
        # legt das an — s. exec_backend._data_home()):
        "BIBI_DATA_HOME": str(tmp_path / "data-home"),
    }


def test_app_container_adds_network(tmp_path):
    env = _base_env(tmp_path)
    with patch.object(exec_backend, "resolve_docker_bin", return_value="docker"):
        spec = exec_backend.build_exec(["uvicorn", "app:app"], env)
    assert "--network" in spec.argv
    idx = spec.argv.index("--network")
    assert spec.argv[idx + 1] == exec_backend.BIBI_NETWORK


def test_app_container_adds_traefik_labels(tmp_path):
    env = _base_env(tmp_path)
    with patch.object(exec_backend, "resolve_docker_bin", return_value="docker"):
        spec = exec_backend.build_exec(["uvicorn", "app:app"], env)
    labels = {}
    args = spec.argv
    for i, a in enumerate(args):
        if a == "-l" and i + 1 < len(args):
            k, _, v = args[i + 1].partition("=")
            labels[k] = v
    assert labels.get("traefik.enable") == "true"
    assert "PathPrefix(`/myapp/`)" in labels.get(
        "traefik.http.routers.bibi-deadbeef-app.rule", "")
    assert labels.get(
        "traefik.http.services.bibi-deadbeef-app.loadbalancer.server.port") == "8081"


def test_app_container_has_no_wrapper_route_label(tmp_path):
    # PLAN-11.5: der Wrapper hat seit 11.3 keinen HTTP-Server mehr — die
    # /-/job/{id}/…-Route gehört nicht mehr ins Image, der Worker-Daemon
    # serviert sie direkt.
    env = _base_env(tmp_path)
    with patch.object(exec_backend, "resolve_docker_bin", return_value="docker"):
        spec = exec_backend.build_exec(["uvicorn", "app:app"], env)
    labels = {}
    args = spec.argv
    for i, a in enumerate(args):
        if a == "-l" and i + 1 < len(args):
            k, _, v = args[i + 1].partition("=")
            labels[k] = v
    assert not any("wrapper" in k for k in labels)
    assert not any("/-/job/" in v for v in labels.values())


def test_job_container_no_traefik_labels(tmp_path):
    env = {
        "BIBI_EXEC_MODE": "container",
        "BIBI_JOB_TYPE": "job",
        "BIBI_JOB_ID": "aabbccdd",
        "BIBI_WORKTREE": str(tmp_path),
        "BIBI_DATA_HOME": str(tmp_path / "data-home"),
    }
    with patch.object(exec_backend, "resolve_docker_bin", return_value="docker"):
        spec = exec_backend.build_exec(["bash", "-c", "echo hi"], env)
    labels = {spec.argv[i + 1].partition("=")[0]
              for i, a in enumerate(spec.argv) if a == "-l"}
    assert "--network" not in spec.argv
    assert not any("traefik" in k for k in labels)


def test_app_without_prefix_skips_app_router(tmp_path):
    env = _base_env(tmp_path)
    del env["BIBI_APP_PREFIX"]
    with patch.object(exec_backend, "resolve_docker_bin", return_value="docker"):
        spec = exec_backend.build_exec(["uvicorn", "app:app"], env)
    labels = {args[i + 1].partition("=")[0]: args[i + 1].partition("=")[2]
              for i, a in enumerate(spec.argv) if a == "-l"
              for args in [spec.argv]}
    assert not any("app.rule" in k for k in labels)


# ── run_app — end-to-end ──────────────────────────────────────────────────────


def test_run_app_complete(tmp_path):
    """App-Typ startet, Prozess endet → exit 0, Output geschrieben."""
    out_path = tmp_path / "output.jsonl"
    env = {
        "BIBI_JOB_TYPE": "job",
        "BIBI_JOB_ID": "testjob",
        "BIBI_OUTPUT_PATH": str(out_path),
        "BIBI_WORKTREE": str(tmp_path),
        "BIBI_JOB_CMD": "echo 'app done'",
    }
    code = wrapper.run_app(env)
    assert code == 0
    from bibi.wrapper.output import lines
    assert "app done" in lines(out_path)


def _seed_job(db_path: Path, job_id: str) -> None:
    c = job_db.connect(db_path)
    c.execute(
        "INSERT INTO jobs (id, slug, schedule_ref, kind, payload, status, attempts, "
        "backoff, silence_timeout, hitl_timeout) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (job_id, job_id, f"{job_id}.md", "job", "x", "running", 1, "fixed", 3600, 172800),
    )
    c.close()


def test_run_app_bibi_awaiting_written_to_db(tmp_path):
    """Job sendet awaiting-Signal → DB-Status awaiting + demand gesetzt."""
    import sys as _sys
    db_path = tmp_path / "jobs.sqlite"
    _seed_job(db_path, "aw1")

    script = tmp_path / "awaiting_job.py"
    script.write_text(
        "import bibi.job\n"
        "bibi.job.awaiting('Bitte eingeben', input_format='text')\n"
    )

    out = tmp_path / "output.jsonl"
    env = {
        "BIBI_JOB_TYPE": "job",
        "BIBI_JOB_ID": "aw1",
        "BIBI_OUTPUT_PATH": str(out),
        "BIBI_WORKTREE": str(tmp_path),
        "BIBI_JOB_CMD": f"{_sys.executable} {script}",
        "BIBI_SCHEDULER_DB_PATH": str(db_path),
    }
    wrapper.run_app(env)

    c2 = job_db.connect(db_path)
    row = c2.execute("SELECT status FROM jobs WHERE id='aw1'").fetchone()
    demand = job_db.get_demand(c2, "aw1")
    c2.close()
    assert row["status"] == "awaiting"
    assert demand is not None
    assert demand["input_request"] == "Bitte eingeben"


def test_run_app_deferred_via_bibi_job(tmp_path):
    """bibi.job.Deferred → BIBI:deferred-Signal → status=deferred in DB."""
    import sys as _sys
    db_path = tmp_path / "jobs.sqlite"
    _seed_job(db_path, "d2")

    script = tmp_path / "defer_job.py"
    script.write_text("import bibi.job\nraise bibi.job.Deferred(seconds=60)\n")

    out = tmp_path / "output.jsonl"
    env = {
        "BIBI_JOB_TYPE": "job",
        "BIBI_JOB_ID": "d2",
        "BIBI_OUTPUT_PATH": str(out),
        "BIBI_WORKTREE": str(tmp_path),
        "BIBI_JOB_CMD": f"{_sys.executable} {script}",
        "BIBI_SCHEDULER_DB_PATH": str(db_path),
    }
    wrapper.run_app(env)

    c2 = job_db.connect(db_path)
    row = c2.execute("SELECT status FROM jobs WHERE id='d2'").fetchone()
    c2.close()
    assert row["status"] == "deferred"


def test_silence_monitor_kills_awaiting_job_after_timeout(tmp_path):
    """Job sendet awaiting, danach Stille → silence_timeout → zombie in DB.

    User-Feedback 2026-07-04: silence_timeout/hitl_timeout zusammengelegt —
    der eine Mechanismus greift jetzt auch während awaiting, kein separater
    HITL-Timeout mehr nötig."""
    import sys as _sys
    db_path = tmp_path / "jobs.sqlite"
    _seed_job(db_path, "z1")

    script = tmp_path / "zombie_job.py"
    script.write_text(
        "import bibi.job, time\n"
        "bibi.job.awaiting('test', input_format='text')\n"
        "time.sleep(30)\n"
    )

    out = tmp_path / "output.jsonl"
    env = {
        "BIBI_JOB_TYPE": "job",
        "BIBI_JOB_ID": "z1",
        "BIBI_OUTPUT_PATH": str(out),
        "BIBI_WORKTREE": str(tmp_path),
        "BIBI_JOB_CMD": f"{_sys.executable} {script}",
        "BIBI_SCHEDULER_DB_PATH": str(db_path),
        "BIBI_SILENCE_TIMEOUT": "1",
    }
    start_t = time.time()
    wrapper.run_app(env)
    elapsed = time.time() - start_t
    assert elapsed < 10.0, f"run_app hat zu lang gedauert: {elapsed:.1f}s"

    c2 = job_db.connect(db_path)
    row = c2.execute("SELECT status, reason FROM jobs WHERE id='z1'").fetchone()
    c2.close()
    assert row["status"] == "zombie"
    assert row["reason"] == "silence"

    from bibi.wrapper.output import lines as _out_lines
    phase_lines = _out_lines(out, "phase")
    assert any("silence" in l and "1s" in l for l in phase_lines), phase_lines


def test_activity_signal_extends_deadline_while_awaiting(tmp_path):
    """App pingt per bibi.job.activity() während awaiting → übersteht einen
    silence_timeout, der ohne den Herzschlag längst zugeschlagen hätte."""
    import sys as _sys
    db_path = tmp_path / "jobs.sqlite"
    _seed_job(db_path, "z3")

    script = tmp_path / "pinging_job.py"
    script.write_text(
        "import bibi.job, time\n"
        "bibi.job.awaiting('test', input_format='text')\n"
        "for _ in range(6):\n"
        "    time.sleep(0.4)\n"
        "    bibi.job.activity()\n"
        "bibi.job.running()\n"
    )

    out = tmp_path / "output.jsonl"
    env = {
        "BIBI_JOB_TYPE": "job",
        "BIBI_JOB_ID": "z3",
        "BIBI_OUTPUT_PATH": str(out),
        "BIBI_WORKTREE": str(tmp_path),
        "BIBI_JOB_CMD": f"{_sys.executable} {script}",
        "BIBI_SCHEDULER_DB_PATH": str(db_path),
        "BIBI_SILENCE_TIMEOUT": "1",
    }
    code = wrapper.run_app(env)

    c2 = job_db.connect(db_path)
    row = c2.execute("SELECT status FROM jobs WHERE id='z3'").fetchone()
    c2.close()
    assert code == 0
    assert row["status"] == "complete"


def test_worker_sets_bibi_silence_timeout(tmp_path):
    """_run_wrapper setzt BIBI_SILENCE_TIMEOUT in die Wrapper-Env (detach-Pfad)."""
    import sys
    import subprocess as subprocess_mod
    from bibi.daemon import worker as _worker
    captured_env: list[dict] = []
    _orig_popen = subprocess_mod.Popen

    def mock_popen(argv, **kwargs):
        # PLAN-22 Befund 4 (_free_app_port_host()) ruft vor dem eigentlichen
        # Wrapper-Spawn best-effort `lsof` per subprocess.run() auf — das
        # geht intern ebenfalls über Popen und würde hier sonst fälschlich
        # als captured_env[0] landen. Nur den echten Wrapper-Spawn
        # capturen (python -m bibi.wrapper), alles andere real durchlassen.
        is_wrapper_spawn = (isinstance(argv, list) and len(argv) >= 2
                            and argv[0] == sys.executable and argv[1] == "-m")
        if is_wrapper_spawn:
            captured_env.append(kwargs.get("env") or {})
            return _orig_popen(
                ["python", "-c", "import sys; sys.exit(0)"],
                stdin=subprocess_mod.DEVNULL, stdout=subprocess_mod.PIPE,
                stderr=subprocess_mod.PIPE,
                env=kwargs.get("env"), cwd=None,
                start_new_session=False,
            )
        return _orig_popen(argv, **kwargs)

    with (
        patch("bibi.daemon.worker.subprocess.Popen", side_effect=mock_popen),
        patch("bibi.daemon.worker.worktree.prepare", return_value=tmp_path),
        patch("bibi.daemon.worker.worktree.commit", return_value="abc123"),
    ):
        out_path = tmp_path / "output.jsonl"
        out_path.touch()
        _worker._run_wrapper(
            job_id="ht1", slug="testslug", kind="job", payload="echo x",
            app_port=8081, app_prefix="/app", silence_timeout=172800,
            repo_root=tmp_path, work_dir=tmp_path / "wt",
        )

    assert captured_env, "Popen wurde nicht aufgerufen"
    assert captured_env[0].get("BIBI_SILENCE_TIMEOUT") == "172800", captured_env[0]
