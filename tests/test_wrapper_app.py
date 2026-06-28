"""Wrapper: app-Typ, Wrapper-HTTP-Server, Traefik-Labels (PLAN-9 Slice 9.0/9.1)."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import patch

import httpx2 as httpx
import pytest

from bibi import wrapper
from bibi.wrapper import exec_backend
from bibi.wrapper.server import WrapperState, make_app, start_server


# ── Registry ─────────────────────────────────────────────────────────────────


def test_app_in_registry():
    assert "app" in wrapper.REGISTRY
    h = wrapper.REGISTRY["app"]
    assert h.long_lived is True
    assert h.supports_hitl is True


def test_app_argv_uses_entrypoint():
    argv = wrapper.REGISTRY["app"].build_command(
        {"BIBI_APP_ENTRYPOINT": "uvicorn myapp:app --port 8081"}
    )
    assert argv == ["bash", "-c", "uvicorn myapp:app --port 8081"]


def test_app_argv_empty_entrypoint():
    argv = wrapper.REGISTRY["app"].build_command({})
    assert argv == ["bash", "-c", ""]


# ── WrapperState ─────────────────────────────────────────────────────────────


def test_wrapper_state_initial():
    s = WrapperState(job_id="abc123")
    assert s.status == "running"
    assert s.demand is None
    assert s.job_id == "abc123"


def test_wrapper_state_set_status():
    s = WrapperState(job_id="x")
    s.status = "awaiting"
    assert s.status == "awaiting"


def test_wrapper_state_snapshot():
    s = WrapperState(job_id="j1")
    snap = s.snapshot()
    assert snap["job_id"] == "j1"
    assert snap["status"] == "running"
    assert snap["demand"] is None


# ── FastAPI-App (sync, kein echter Server) ───────────────────────────────────


def test_status_endpoint_known_job():
    state = WrapperState(job_id="abc")
    app = make_app(state)
    from fastapi.testclient import TestClient
    client = TestClient(app)
    r = client.get("/-/job/abc/status")
    assert r.status_code == 200
    data = r.json()
    assert data["job_id"] == "abc"
    assert data["status"] == "running"


def test_status_endpoint_unknown_job():
    state = WrapperState(job_id="abc")
    app = make_app(state)
    from fastapi.testclient import TestClient
    client = TestClient(app)
    r = client.get("/-/job/other/status")
    assert r.status_code == 404


# ── Wrapper-HTTP-Server (echter Port, Daemon-Thread) ─────────────────────────


def _wait_for(url: str, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            httpx.get(url, timeout=0.5)
            return
        except Exception:
            time.sleep(0.1)
    raise TimeoutError(f"Server nicht erreichbar: {url}")


def test_start_server_serves_status(free_tcp_port):
    state = WrapperState(job_id="srv1")
    server = start_server(state, port=free_tcp_port)
    try:
        _wait_for(f"http://127.0.0.1:{free_tcp_port}/-/job/srv1/status")
        r = httpx.get(f"http://127.0.0.1:{free_tcp_port}/-/job/srv1/status")
        assert r.status_code == 200
        assert r.json()["status"] == "running"
    finally:
        server.should_exit = True


# ── Traefik-Labels in exec_backend ───────────────────────────────────────────


def _base_env(tmp_path: Path, job_type: str = "app") -> dict:
    return {
        "BIBI_EXEC_MODE": "container",
        "BIBI_JOB_TYPE": job_type,
        "BIBI_JOB_ID": "deadbeef",
        "BIBI_WORKTREE": str(tmp_path),
        "BIBI_APP_PORT": "8081",
        "BIBI_APP_PREFIX": "/myapp",
        "BIBI_WRAPPER_PORT": "8080",
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
    # Wrapper-Router
    assert "PathPrefix(`/-/job/deadbeef/`)" in labels.get(
        "traefik.http.routers.bibi-deadbeef-wrapper.rule", "")
    assert labels.get(
        "traefik.http.services.bibi-deadbeef-wrapper.loadbalancer.server.port") == "8080"
    # App-Router
    assert "PathPrefix(`/myapp/`)" in labels.get(
        "traefik.http.routers.bibi-deadbeef-app.rule", "")
    assert labels.get(
        "traefik.http.services.bibi-deadbeef-app.loadbalancer.server.port") == "8081"


def test_job_container_no_traefik_labels(tmp_path):
    env = {
        "BIBI_EXEC_MODE": "container",
        "BIBI_JOB_TYPE": "job",
        "BIBI_JOB_ID": "aabbccdd",
        "BIBI_WORKTREE": str(tmp_path),
    }
    with patch.object(exec_backend, "resolve_docker_bin", return_value="docker"):
        spec = exec_backend.build_exec(["bash", "-c", "echo hi"], env)
    # -l-Argumente extrahieren (nur echte Label-Values, nicht tmp_path-Inhalt)
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
    # Kein App-Router ohne Prefix
    assert not any("app.rule" in k for k in labels)


# ── run_app — end-to-end (echter Subprozess) ─────────────────────────────────


def test_run_app_complete(tmp_path, free_tcp_port):
    """App-Typ startet, HTTP-Server antwortet, Prozess endet → exit 0."""
    out_path = tmp_path / "output.jsonl"
    env = {
        "BIBI_JOB_TYPE": "app",
        "BIBI_JOB_ID": "testjob",
        "BIBI_OUTPUT_PATH": str(out_path),
        "BIBI_WORKTREE": str(tmp_path),
        "BIBI_APP_ENTRYPOINT": "echo 'app done'",
        "BIBI_WRAPPER_PORT": str(free_tcp_port),
    }
    code = wrapper.run_app(env)
    assert code == 0
    from bibi.wrapper.output import lines
    assert "app done" in lines(out_path)


# ── Slice 9.1: /-/signal/* + GET /-/job/{id}/input ───────────────────────────


def test_signal_awaiting_sets_state():
    state = WrapperState(job_id="j1")
    app = make_app(state)
    from fastapi.testclient import TestClient
    client = TestClient(app)
    r = client.post("/-/signal/awaiting", json={"prompt": "Weitermachen?", "choices": ["ja", "nein"]})
    assert r.status_code == 200
    assert state.status == "awaiting"
    assert state.demand is not None
    assert state.demand["prompt"] == "Weitermachen?"
    assert state.demand["choices"] == ["ja", "nein"]
    assert state.demand["mediated"] is False  # kein input_path → nicht mediated


def test_signal_awaiting_with_input_path_is_mediated():
    state = WrapperState(job_id="j2")
    app = make_app(state)
    from fastapi.testclient import TestClient
    client = TestClient(app)
    r = client.post("/-/signal/awaiting", json={"prompt": "Auswahl", "input_path": "/input"})
    assert r.status_code == 200
    assert state.demand["mediated"] is True
    assert state.demand["input_path"] == "/input"


def test_signal_running_clears_demand():
    state = WrapperState(job_id="j3")
    app = make_app(state)
    from fastapi.testclient import TestClient
    client = TestClient(app)
    client.post("/-/signal/awaiting", json={"prompt": "Warte"})
    assert state.status == "awaiting"
    r = client.post("/-/signal/running")
    assert r.status_code == 200
    assert state.status == "running"
    assert state.demand is None


def test_get_input_returns_demand():
    state = WrapperState(job_id="j4")
    app = make_app(state)
    from fastapi.testclient import TestClient
    client = TestClient(app)
    client.post("/-/signal/awaiting", json={"prompt": "Frage", "choices": ["a", "b"]})
    r = client.get("/-/job/j4/input")
    assert r.status_code == 200
    data = r.json()
    assert data["prompt"] == "Frage"
    assert data["choices"] == ["a", "b"]
    assert data["mediated"] is False


def test_get_input_not_found_when_not_awaiting():
    state = WrapperState(job_id="j5")
    app = make_app(state)
    from fastapi.testclient import TestClient
    client = TestClient(app)
    r = client.get("/-/job/j5/input")
    assert r.status_code == 404


def test_get_input_wrong_job():
    state = WrapperState(job_id="j6")
    app = make_app(state)
    from fastapi.testclient import TestClient
    client = TestClient(app)
    client.post("/-/signal/awaiting", json={"prompt": "x"})
    r = client.get("/-/job/other/input")
    assert r.status_code == 404


def test_signal_awaiting_calls_scheduler(free_tcp_port):
    """POST /-/signal/awaiting meldet 'awaiting' an den Scheduler."""
    import json
    from http.server import BaseHTTPRequestHandler, HTTPServer
    import threading

    received: list[dict] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            received.append({"path": self.path, "body": json.loads(body)})
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"result":"ok"}')

        def log_message(self, *args):
            pass  # still output unterdrücken

    srv = HTTPServer(("127.0.0.1", free_tcp_port), Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        state = WrapperState(job_id="sched1", scheduler_url=f"http://127.0.0.1:{free_tcp_port}")
        app = make_app(state)
        from fastapi.testclient import TestClient
        client = TestClient(app)
        client.post("/-/signal/awaiting", json={"prompt": "Warte"})
        import time
        deadline = time.monotonic() + 3.0
        while not received and time.monotonic() < deadline:
            time.sleep(0.05)
        assert any(r["path"] == "/- /scheduler/status/sched1" or
                   "scheduler/status/sched1" in r["path"] for r in received), received
        assert received[0]["body"]["status"] == "awaiting"
    finally:
        srv.shutdown()


def test_signal_running_calls_scheduler(free_tcp_port):
    """POST /-/signal/running meldet 'running' an den Scheduler."""
    import json
    from http.server import BaseHTTPRequestHandler, HTTPServer
    import threading

    received: list[dict] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            received.append({"path": self.path, "body": json.loads(body)})
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"result":"ok"}')

        def log_message(self, *args):
            pass

    srv = HTTPServer(("127.0.0.1", free_tcp_port), Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        state = WrapperState(job_id="sched2", scheduler_url=f"http://127.0.0.1:{free_tcp_port}")
        app = make_app(state)
        from fastapi.testclient import TestClient
        client = TestClient(app)
        client.post("/-/signal/running")
        import time
        deadline = time.monotonic() + 3.0
        while not received and time.monotonic() < deadline:
            time.sleep(0.05)
        assert any("scheduler/status/sched2" in r["path"] for r in received), received
        assert received[0]["body"]["status"] == "running"
    finally:
        srv.shutdown()


def test_wrapper_state_no_scheduler_url_no_crash():
    """Ohne scheduler_url läuft report() ohne Fehler durch."""
    state = WrapperState(job_id="j7")
    state.report("awaiting")  # darf nicht werfen
