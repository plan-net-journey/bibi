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


# ── Slice 9.2: POST /-/job/{id}/input (Proxy-Modus) ──────────────────────────


def _make_app_server(*, job_id: str, port: int,
                     response_code: int = 200,
                     response_body: bytes = b'{"ok":true}') -> tuple:
    """Startet einen Mini-HTTP-Server, der als App-Stub fungiert.
    Gibt (server, received_list) zurück."""
    import json as _json
    from http.server import BaseHTTPRequestHandler, HTTPServer
    import threading

    received: list[dict] = []
    _code = response_code
    _body = response_body

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            data = self.rfile.read(length)
            received.append({"path": self.path, "body": _json.loads(data or b"null")})
            self.send_response(_code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(_body)

        def log_message(self, *args):
            pass

    srv = HTTPServer(("127.0.0.1", port), Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, received


def test_proxy_input_transitions_to_running(free_tcp_port):
    """FE POST → Wrapper → App 200 → state=running, Demand gelöscht."""
    srv, received = _make_app_server(job_id="px1", port=free_tcp_port)
    try:
        state = WrapperState(job_id="px1", app_port=free_tcp_port)
        state.demand = {"prompt": "Weitermachen?", "choices": ["ja"],
                        "input_path": "/input", "mediated": True}
        state.status = "awaiting"
        app = make_app(state)
        from fastapi.testclient import TestClient
        client = TestClient(app)
        r = client.post("/-/job/px1/input", json={"answer": "ja"})
        assert r.status_code == 200
        assert state.status == "running"
        assert state.demand is None
        assert len(received) == 1
        assert received[0]["path"] == "/input"
        assert received[0]["body"]["answer"] == "ja"
    finally:
        srv.shutdown()


def test_proxy_input_not_awaiting_returns_409():
    state = WrapperState(job_id="px2", app_port=9999)
    state.status = "running"
    app = make_app(state)
    from fastapi.testclient import TestClient
    client = TestClient(app)
    r = client.post("/-/job/px2/input", json={"answer": "x"})
    assert r.status_code == 409


def test_proxy_input_not_mediated_returns_409():
    state = WrapperState(job_id="px3", app_port=9999)
    state.demand = {"prompt": "x", "choices": None, "input_path": None, "mediated": False}
    state.status = "awaiting"
    app = make_app(state)
    from fastapi.testclient import TestClient
    client = TestClient(app)
    r = client.post("/-/job/px3/input", json={"answer": "x"})
    assert r.status_code == 409


def test_proxy_input_wrong_job_returns_404():
    state = WrapperState(job_id="px4", app_port=9999)
    app = make_app(state)
    from fastapi.testclient import TestClient
    client = TestClient(app)
    r = client.post("/-/job/other/input", json={})
    assert r.status_code == 404


def test_proxy_input_app_error_propagates(free_tcp_port):
    """App antwortet 422 → Wrapper gibt 422 weiter, state bleibt awaiting."""
    srv, _ = _make_app_server(job_id="px5", port=free_tcp_port,
                              response_code=422, response_body=b'{"detail":"invalid"}')
    try:
        state = WrapperState(job_id="px5", app_port=free_tcp_port)
        state.demand = {"prompt": "x", "choices": None,
                        "input_path": "/input", "mediated": True}
        state.status = "awaiting"
        app = make_app(state)
        from fastapi.testclient import TestClient
        client = TestClient(app, raise_server_exceptions=False)
        r = client.post("/-/job/px5/input", json={"answer": "falsch"})
        assert r.status_code == 422
        assert state.status == "awaiting"  # kein Übergang bei Fehler
        assert state.demand is not None
    finally:
        srv.shutdown()


def test_proxy_input_no_app_port_returns_503():
    state = WrapperState(job_id="px6")  # kein app_port
    state.demand = {"prompt": "x", "choices": None,
                    "input_path": "/input", "mediated": True}
    state.status = "awaiting"
    app = make_app(state)
    from fastapi.testclient import TestClient
    client = TestClient(app, raise_server_exceptions=False)
    r = client.post("/-/job/px6/input", json={})
    assert r.status_code == 503


def test_run_app_passes_app_port_to_state(tmp_path, free_tcp_port):
    """run_app liest BIBI_APP_PORT aus env und reicht es an WrapperState weiter."""
    out_path = tmp_path / "output.jsonl"
    app_port = free_tcp_port + 1  # anderer Port, nicht belegt nötig — nur Initialisierung prüfen
    env = {
        "BIBI_JOB_TYPE": "app",
        "BIBI_JOB_ID": "appport1",
        "BIBI_OUTPUT_PATH": str(out_path),
        "BIBI_WORKTREE": str(tmp_path),
        "BIBI_APP_ENTRYPOINT": "echo done",
        "BIBI_WRAPPER_PORT": str(free_tcp_port),
        "BIBI_APP_PORT": str(app_port),
    }
    captured_states: list = []
    original_start = __import__("bibi.wrapper.server", fromlist=["start_server"]).start_server

    def mock_start(state, *, port):
        captured_states.append(state)
        return original_start(state, port=port)

    with patch("bibi.wrapper.server.start_server", side_effect=mock_start):
        wrapper.run_app(env)

    assert captured_states, "start_server wurde nicht aufgerufen"
    assert captured_states[0].app_port == app_port


# ── Slice 9.3: Zombie-Timeout + /ping + Activity-Tracking ────────────────────


def test_wrapper_state_touch_resets_timer():
    state = WrapperState(job_id="z1", hitl_timeout=10)
    import time
    time.sleep(0.05)
    assert state.idle_seconds > 0.0
    state.touch()
    assert state.idle_seconds < 0.05  # frisch resettet


def test_wrapper_state_idle_seconds_increases():
    state = WrapperState(job_id="z2")
    import time
    time.sleep(0.05)
    assert state.idle_seconds >= 0.04


def test_ping_endpoint_resets_timer():
    state = WrapperState(job_id="z3", hitl_timeout=10)
    import time
    app = make_app(state)
    from fastapi.testclient import TestClient
    client = TestClient(app)
    time.sleep(0.05)
    r = client.post("/-/job/z3/ping")
    assert r.status_code == 200
    assert state.idle_seconds < 0.05


def test_ping_wrong_job_returns_404():
    state = WrapperState(job_id="z4")
    app = make_app(state)
    from fastapi.testclient import TestClient
    client = TestClient(app)
    r = client.post("/-/job/other/ping")
    assert r.status_code == 404


def test_hitl_monitor_kills_on_timeout(tmp_path, free_tcp_port):
    """HITL-Monitor terminiert App-Child bei Timeout; Scheduler wird als zombie gemeldet."""
    import json
    from http.server import BaseHTTPRequestHandler, HTTPServer
    import threading
    import time

    received: list[dict] = []

    class SchedHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            received.append(json.loads(body))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"result":"ok"}')

        def log_message(self, *args):
            pass

    sched_srv = HTTPServer(("127.0.0.1", free_tcp_port), SchedHandler)
    t = threading.Thread(target=sched_srv.serve_forever, daemon=True)
    t.start()
    try:
        out_path = tmp_path / "output.jsonl"
        env = {
            "BIBI_JOB_TYPE": "app",
            "BIBI_JOB_ID": "zombie1",
            "BIBI_OUTPUT_PATH": str(out_path),
            "BIBI_WORKTREE": str(tmp_path),
            # App läuft 30 s — wird aber durch HITL-Timeout davor beendet
            "BIBI_APP_ENTRYPOINT": "sleep 30",
            "BIBI_WRAPPER_PORT": str(free_tcp_port + 1),
            "BIBI_SCHEDULER_URL": f"http://127.0.0.1:{free_tcp_port}",
            "BIBI_HITL_TIMEOUT": "1",  # 1 Sekunde Timeout für den Test
        }
        # Direkt _hitl_monitor-Funktionalität über run_app testen
        import time as _time
        start = _time.monotonic()
        # Damit der Monitor feuert, muss der State "awaiting" sein.
        # Wir setzen den State nach dem Start manuell via Patch.
        from unittest.mock import patch
        original_start_server = __import__(
            "bibi.wrapper.server", fromlist=["start_server"]).start_server
        captured = []

        def mock_start(state, *, port):
            captured.append(state)
            return original_start_server(state, port=port)

        with patch("bibi.wrapper.server.start_server", side_effect=mock_start):
            # run_app in einem Thread starten, damit wir den State manipulieren können
            result = [None]

            def run():
                result[0] = wrapper.run_app(env)

            runner = threading.Thread(target=run)
            runner.start()
            # Kurz warten bis State-Objekt verfügbar
            deadline = _time.monotonic() + 3.0
            while not captured and _time.monotonic() < deadline:
                _time.sleep(0.05)
            assert captured, "start_server nicht aufgerufen"
            state = captured[0]
            state.status = "awaiting"  # HITL-Timeout-Bedingung aktivieren
            state._last_activity = state._last_activity - 2.0  # schon abgelaufen
            runner.join(timeout=10.0)

        elapsed = _time.monotonic() - start
        assert elapsed < 10.0, f"run_app hat zu lang gedauert: {elapsed:.1f}s"
        # Zombie-Meldung an Scheduler
        deadline = _time.monotonic() + 3.0
        while not received and _time.monotonic() < deadline:
            _time.sleep(0.05)
        assert any(r.get("status") == "zombie" for r in received), received
        assert any(r.get("reason") == "activity_timeout" for r in received), received
    finally:
        sched_srv.shutdown()


def test_run_app_touches_on_stdout(tmp_path, free_tcp_port):
    """stdout-Zeilen des Childs rufen state.touch() auf."""
    import time
    from unittest.mock import patch
    original_start_server = __import__(
        "bibi.wrapper.server", fromlist=["start_server"]).start_server
    captured = []

    def mock_start(state, *, port):
        captured.append(state)
        return original_start_server(state, port=port)

    out_path = tmp_path / "output.jsonl"
    env = {
        "BIBI_JOB_TYPE": "app",
        "BIBI_JOB_ID": "touch1",
        "BIBI_OUTPUT_PATH": str(out_path),
        "BIBI_WORKTREE": str(tmp_path),
        "BIBI_APP_ENTRYPOINT": "echo activity",
        "BIBI_WRAPPER_PORT": str(free_tcp_port),
        "BIBI_HITL_TIMEOUT": "60",
    }
    with patch("bibi.wrapper.server.start_server", side_effect=mock_start):
        wrapper.run_app(env)
    assert captured
    # idle_seconds nach dem Lauf ist klein — touch() wurde aufgerufen
    assert captured[0].idle_seconds < 2.0


def test_worker_sets_bibi_hitl_timeout(tmp_path):
    """_run_wrapper setzt BIBI_HITL_TIMEOUT in die Wrapper-Env."""
    import subprocess as subprocess_mod
    from bibi.daemon import worker as _worker
    captured_env: list[dict] = []
    _orig_popen = subprocess_mod.Popen  # vor dem Patch sichern

    def mock_popen(argv, **kwargs):
        captured_env.append(kwargs.get("env") or {})
        return _orig_popen(
            ["python", "-c", "import sys; sys.exit(0)"],
            stdin=subprocess_mod.DEVNULL, stdout=subprocess_mod.PIPE,
            stderr=subprocess_mod.PIPE,
            env=kwargs.get("env"), cwd=None,
            start_new_session=False,
        )

    with (
        patch("bibi.daemon.worker.subprocess.Popen", side_effect=mock_popen),
        patch("bibi.daemon.worker.worktree.prepare", return_value=tmp_path),
        patch("bibi.daemon.worker.worktree.commit", return_value="abc123"),
    ):
        out_path = tmp_path / "output.jsonl"
        out_path.touch()
        _worker._run_wrapper(
            job_id="ht1", slug="testslug", kind="app", payload="echo x",
            app_port=8081, app_prefix="/app", hitl_timeout=3600,
            repo_root=tmp_path, work_dir=tmp_path / "wt",
        )

    assert captured_env, "Popen wurde nicht aufgerufen"
    assert captured_env[0].get("BIBI_HITL_TIMEOUT") == "3600", captured_env[0]
