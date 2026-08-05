"""Worker-Verbund: Registry, /-/worker, Secret-Auth, RemoteScheduler (PLAN-3 §3.6)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bibi.daemon import roles
from bibi.daemon.app import create_app
from bibi.daemon.scheduler_client import RemoteScheduler
from bibi.daemon.worker_registry import WorkerRegistry


# ── WorkerRegistry (rein) ────────────────────────────────────────────────────


def test_registry_heartbeat_and_list():
    reg = WorkerRegistry()
    reg.heartbeat("w1", "h1", "trunk", now=100)
    lst = reg.list(now=100)
    assert len(lst) == 1
    assert lst[0]["worker"] == "w1" and lst[0]["host"] == "h1"
    assert lst[0]["git_status"] == "trunk" and lst[0]["stale"] is False


def test_registry_stale_after_timeout():
    reg = WorkerRegistry()
    reg.heartbeat("w1", "h1", now=0)
    assert reg.list(now=1000, stale_after=60)[0]["stale"] is True
    assert reg.fresh_count(now=1000, stale_after=60) == 0


def test_registry_update_keeps_connected_at():
    reg = WorkerRegistry()
    reg.heartbeat("w1", "h1", now=0)
    reg.heartbeat("w1", "h2", now=10)
    lst = reg.list(now=10)
    assert len(lst) == 1
    assert lst[0]["host"] == "h2" and lst[0]["connected_at"] == 0


def test_registry_stale_workers():
    reg = WorkerRegistry()
    reg.heartbeat("dead", "h", now=0)
    reg.heartbeat("alive", "h", now=100)
    assert reg.stale_workers(now=100, stale_after=60) == {"dead"}


# ── node_id-Rekeying (Bibi4-Iteration, Connected-Clients-Screen) ────────────
# User-Fund: derselbe physische Client tauchte je nach Netzwerk mit anderem
# worker-Namen auf, alte Registry-Einträge blieben stale liegen. node_id ist
# jetzt der eigentliche Schlüssel, worker nur noch Anzeigename.


def test_registry_keys_by_node_id_not_worker_name():
    reg = WorkerRegistry()
    reg.heartbeat("air2024", "mac", "trunk", node_id="stable-uuid", now=0)
    # gleicher physischer Client, anderer Netzwerk-Hostname beim nächsten Beat:
    reg.heartbeat("air-home", "mac", "trunk", node_id="stable-uuid", now=10)
    lst = reg.list(now=10)
    assert len(lst) == 1  # eine Zeile, nicht zwei
    assert lst[0]["worker"] == "air-home"  # jüngster Anzeigename gewinnt
    assert lst[0]["connected_at"] == 0  # aber der ursprüngliche connected_at bleibt


def test_registry_different_node_ids_stay_separate_even_with_same_worker_name():
    reg = WorkerRegistry()
    reg.heartbeat("client", "h1", node_id="uuid-a", now=0)
    reg.heartbeat("client", "h2", node_id="uuid-b", now=0)
    assert len(reg.list(now=0)) == 2


def test_registry_falls_back_to_worker_name_key_without_node_id():
    # Rückwärtskompatibel: ein Heartbeat ohne node_id (älterer Client) verhält
    # sich wie vor dieser Änderung — worker-Name selbst ist der Schlüssel.
    reg = WorkerRegistry()
    reg.heartbeat("w1", "h1", now=0)
    reg.heartbeat("w1", "h2", now=10)
    assert len(reg.list(now=10)) == 1


def test_registry_stores_git_user():
    reg = WorkerRegistry()
    reg.heartbeat("w1", "h1", node_id="uuid-a", git_user="m.rau", now=0)
    assert reg.list(now=0)[0]["git_user"] == "m.rau"


def test_registry_stores_role():
    # Bibi4-Iteration, User-Fund: "Client Übersicht braucht die Rollen je
    # Client" — derselbe Präzedenzfall wie git_user/node_id.
    reg = WorkerRegistry()
    reg.heartbeat("w1", "h1", node_id="uuid-a", role="synchronizer,controller", now=0)
    assert reg.list(now=0)[0]["role"] == "synchronizer,controller"


def test_sweeper_reconciles_no_process(tmp_path: Path):
    import secrets
    import time as _t

    from bibi.daemon import job_db
    from bibi.daemon.sweeper import Sweeper
    p = tmp_path / "j.sqlite"
    conn = job_db.connect(p)
    jid = secrets.token_hex(4)
    conn.execute(
        "INSERT INTO jobs (id, slug, schedule_ref, kind, payload, status, worker, "
        "enqueued_at, next_fire_at) VALUES (?,?,?,?,?, 'running', 'gone', ?, 0)",
        (jid, "x", "x.md", "job", "e", _t.time()))
    conn.close()
    reg = WorkerRegistry()
    reg.heartbeat("gone", "h", now=0)  # last_heartbeat=0 ⇒ stale gegen now
    sw = Sweeper(db_path=p, registry=reg, autorun=False)
    assert sw.tick_once()["no_process"] == 1
    conn = job_db.connect(p)
    row = conn.execute("SELECT status, reason FROM jobs WHERE id=?", (jid,)).fetchone()
    assert row["status"] == "killed" and row["reason"] == "no_process"
    conn.close()


def test_sweeper_never_reconciles_local_worker_even_if_registry_entry_stale(tmp_path: Path):
    # Live-Fund 2026-07-11 (sarasate Host+Client): ein fremder --connect-Knoten
    # kann zufällig unter demselben Namen wie der co-located lokale Worker
    # heartbeaten (Hostname-Kollision) und dessen Registry-Eintrag veralten
    # lassen — das darf den eigenen, echt laufenden lokalen Job NIE killen.
    import secrets
    import time as _t

    from bibi.daemon import job_db
    from bibi.daemon.sweeper import Sweeper
    p = tmp_path / "j.sqlite"
    conn = job_db.connect(p)
    jid = secrets.token_hex(4)
    conn.execute(
        "INSERT INTO jobs (id, slug, schedule_ref, kind, payload, status, worker, "
        "enqueued_at, next_fire_at) VALUES (?,?,?,?,?, 'running', 'sarasate', ?, 0)",
        (jid, "x", "x.md", "job", "e", _t.time()))
    conn.close()
    reg = WorkerRegistry()
    reg.heartbeat("sarasate", "h", now=0)  # veralteter Fremd-Heartbeat, gleicher Name
    # pid_check_interval hoch: hier wird ausschließlich der REGISTRY-Pfad
    # geprüft. Die PID-basierte Prüfung (m.rau/bibi#38) ist ein anderer
    # Mechanismus und würde diese Zeile zu Recht abräumen — sie trägt keine
    # gültige PID. Der Unterschied ist der Kern beider Wege: die Registry kann
    # sich täuschen (zwei Knoten unter demselben Namen), eine tote PID nicht.
    sw = Sweeper(db_path=p, registry=reg, autorun=False, local_worker_name="sarasate")
    assert sw.tick_once()["no_process"] == 0
    conn = job_db.connect(p)
    row = conn.execute("SELECT status FROM jobs WHERE id=?", (jid,)).fetchone()
    assert row["status"] == "running"
    conn.close()


def test_sweeper_still_reconciles_other_stale_workers_when_local_name_set(tmp_path: Path):
    # local_worker_name schützt nur den einen Namen — echte Fremd-Orphans
    # anderer Worker werden weiterhin normal reconciliert.
    import secrets
    import time as _t

    from bibi.daemon import job_db
    from bibi.daemon.sweeper import Sweeper
    p = tmp_path / "j.sqlite"
    conn = job_db.connect(p)
    jid = secrets.token_hex(4)
    conn.execute(
        "INSERT INTO jobs (id, slug, schedule_ref, kind, payload, status, worker, "
        "enqueued_at, next_fire_at) VALUES (?,?,?,?,?, 'running', 'gone', ?, 0)",
        (jid, "x", "x.md", "job", "e", _t.time()))
    conn.close()
    reg = WorkerRegistry()
    reg.heartbeat("gone", "h", now=0)
    sw = Sweeper(db_path=p, registry=reg, autorun=False, local_worker_name="sarasate")
    assert sw.tick_once()["no_process"] == 1
    conn = job_db.connect(p)
    row = conn.execute("SELECT status, reason FROM jobs WHERE id=?", (jid,)).fetchone()
    assert row["status"] == "killed" and row["reason"] == "no_process"
    conn.close()


# ── /-/worker-Routen (Scheduler-Rolle) ───────────────────────────────────────


@pytest.fixture
def sched(team_repo: Path):
    app = create_app(roles.resolve({"scheduler"}))
    with TestClient(app) as c:
        yield c


def test_worker_heartbeat_then_listed(sched):
    r = sched.post("/-/worker", json={"worker": "w1", "host": "air", "git_status": "trunk"})
    assert r.status_code == 200
    workers = sched.get("/-/worker").json()
    assert any(w["worker"] == "w1" and w["host"] == "air" for w in workers)


def test_status_includes_workers(sched):
    sched.post("/-/worker", json={"worker": "w2", "host": "box"})
    status = sched.get("/-/status").json()
    assert any(w["worker"] == "w2" for w in status["workers"])


def test_worker_heartbeat_passes_node_id_and_git_user_through(sched):
    r = sched.post("/-/worker", json={
        "worker": "air2024", "host": "mac", "git_status": "trunk",
        "node_id": "stable-uuid", "git_user": "m.rau",
    })
    assert r.status_code == 200
    workers = sched.get("/-/worker").json()
    w = next(w for w in workers if w["worker"] == "air2024")
    assert w["node_id"] == "stable-uuid" and w["git_user"] == "m.rau"
    # gleiche node_id, anderer Anzeigename (Netzwerkwechsel) -> dieselbe Zeile
    sched.post("/-/worker", json={
        "worker": "air-home", "host": "mac", "node_id": "stable-uuid",
    })
    workers = sched.get("/-/worker").json()
    assert sum(1 for w in workers if w["node_id"] == "stable-uuid") == 1


def test_worker_heartbeat_passes_role_through(sched):
    r = sched.post("/-/worker", json={
        "worker": "air2024", "host": "mac", "node_id": "stable-uuid",
        "role": "synchronizer,controller",
    })
    assert r.status_code == 200
    workers = sched.get("/-/worker").json()
    w = next(w for w in workers if w["worker"] == "air2024")
    assert w["role"] == "synchronizer,controller"


# ── Shared-Secret-Auth (§1.3) ────────────────────────────────────────────────


def test_secret_required_when_configured(team_repo: Path, monkeypatch):
    # PLAN-32 Stufe 32.1: /-/worker nutzt den Shared-Secret-Gate nicht mehr
    # (Open-Trust-Connect-Gate ersetzt ihn, s. Tests unten) — /-/scheduler/next
    # bleibt unverändert secret-gated (Worker-Dispatch, kein Client-Connect-Pfad).
    monkeypatch.setenv("BIBI_CONNECT_SECRET", "s3cret")
    app = create_app(roles.resolve({"scheduler"}))
    with TestClient(app) as c:
        assert c.post("/-/scheduler/next").status_code == 401
        h = {"X-Bibi-Secret": "s3cret"}
        assert c.post("/-/scheduler/next", headers=h).status_code == 204


def test_no_secret_means_open(sched):
    # ohne konfiguriertes Secret bleibt der Verbund offen (Loopback/Trust-Netz)
    assert sched.post("/-/scheduler/next").status_code == 204


# ── Open-Trust-Connect-Gate (PLAN-32 Stufe 32.1) ─────────────────────────────


def test_worker_heartbeat_ignores_connect_secret(team_repo: Path, monkeypatch):
    # /-/worker akzeptiert auch mit konfiguriertem BIBI_CONNECT_SECRET jeden
    # Aufrufer ohne Header — der Gate ist jetzt node_id-basiert, nicht mehr
    # secret-basiert.
    monkeypatch.setenv("BIBI_CONNECT_SECRET", "s3cret")
    app = create_app(roles.resolve({"scheduler"}))
    with TestClient(app) as c:
        r = c.post("/-/worker", json={"worker": "w", "host": "h", "node_id": "n1"})
        assert r.status_code == 200


def test_worker_heartbeat_without_node_id_always_accepted(sched):
    # Rückwärtskompatibilität: ein Client vor dieser Änderung schickt keine
    # node_id mit — kann nicht individuell gebannt werden, gilt implizit als
    # "approved" (kein Sicherheitsverlust ggü. dem vorherigen fail-open Default).
    r = sched.post("/-/worker", json={"worker": "old-client", "host": "h"})
    assert r.status_code == 200


def test_worker_heartbeat_unknown_node_id_becomes_pending(sched, team_repo: Path):
    from bibi.daemon import job_db
    r = sched.post("/-/worker", json={"worker": "w", "host": "h", "node_id": "brand-new"})
    assert r.status_code == 200
    conn = job_db.connect()
    try:
        assert job_db.node_approval_status(conn, "brand-new") == "pending"
    finally:
        conn.close()


def test_worker_heartbeat_blocked_node_rejected(sched):
    sched.post("/-/worker", json={"worker": "w", "host": "h", "node_id": "n2"})
    assert sched.post("/-/worker/n2/block").status_code == 200
    r = sched.post("/-/worker", json={"worker": "w", "host": "h", "node_id": "n2"})
    assert r.status_code == 401


def test_worker_approve_then_heartbeat_accepted(sched):
    sched.post("/-/worker", json={"worker": "w", "host": "h", "node_id": "n3"})
    sched.post("/-/worker/n3/block")
    assert sched.post("/-/worker/n3/approve").status_code == 200
    r = sched.post("/-/worker", json={"worker": "w", "host": "h", "node_id": "n3"})
    assert r.status_code == 200


def test_approval_status_survives_across_connections(sched, team_repo: Path):
    # Der ganze Zweck von job_db statt In-Memory-Registry: eine frische
    # Connection (analog einem Host-Neustart) darf die Freischaltung nicht
    # verlieren.
    from bibi.daemon import job_db
    sched.post("/-/worker/n4/block")
    conn = job_db.connect()
    try:
        assert job_db.node_approval_status(conn, "n4") == "blocked"
    finally:
        conn.close()


# ── RemoteScheduler (HTTP-Mapping, _post gemockt) ────────────────────────────


def test_remote_next_maps_200_and_204(monkeypatch):
    rs = RemoteScheduler("http://x")
    monkeypatch.setattr(rs, "_post", lambda p, pl: (204, None))
    assert rs.next() is None
    monkeypatch.setattr(rs, "_post", lambda p, pl: (200, {"id": "j"}))
    assert rs.next(worker="w")["id"] == "j"


def test_remote_report_maps_codes(monkeypatch):
    rs = RemoteScheduler("http://x")
    monkeypatch.setattr(rs, "_post", lambda p, pl: (200, None))
    assert rs.report("id", status="complete") == "ok"
    monkeypatch.setattr(rs, "_post", lambda p, pl: (409, None))
    assert rs.report("id", status="complete") == "invalid"
    monkeypatch.setattr(rs, "_post", lambda p, pl: (404, None))
    assert rs.report("id", status="complete") == "not_found"


def test_remote_report_omits_none_fields(monkeypatch):
    rs = RemoteScheduler("http://x")
    captured: dict = {}

    def fake(path, payload):
        captured.update(payload)
        return (200, None)

    monkeypatch.setattr(rs, "_post", fake)
    rs.report("id", status="complete", reason=None, exit_code=0)
    assert "reason" not in captured  # None weggelassen
    assert captured["exit_code"] == 0 and captured["status"] == "complete"


def test_remote_schedules_gets_schedule_list(monkeypatch):
    # PLAN-17 Befund 2 Punkt 3: Jobs-Screen-Remote-Seite braucht einen GET-
    # Wrapper (next/report/register sind reine POST-Verben für den Dispatch-Pfad).
    rs = RemoteScheduler("http://x")
    monkeypatch.setattr(rs, "_get", lambda p: {"schedules": [{"slug": "a"}]})
    assert rs.schedules() == [{"slug": "a"}]


def test_remote_schedules_empty_on_bad_shape(monkeypatch):
    rs = RemoteScheduler("http://x")
    monkeypatch.setattr(rs, "_get", lambda p: None)
    assert rs.schedules() == []


# ── RemoteScheduler.register() Rückgabewert (PLAN-32 Stufe 32.1/32.2) ───────


def test_remote_register_returns_response_body(monkeypatch):
    rs = RemoteScheduler("http://x")
    monkeypatch.setattr(rs, "_post", lambda p, pl: (200, {"config_version": "v1"}))
    assert rs.register("w", "h") == {"config_version": "v1"}


def test_remote_register_raises_on_non_200(monkeypatch):
    # Ein "blocked"-Knoten (401) darf nicht still als Erfolg durchgehen —
    # Heartbeat._beat()s try/except muss das als Fehlschlag erkennen.
    rs = RemoteScheduler("http://x")
    monkeypatch.setattr(rs, "_post", lambda p, pl: (401, {"detail": "blocked"}))
    with pytest.raises(RuntimeError):
        rs.register("w", "h")


# ── Config-Bundle-Distribution über den Heartbeat (PLAN-32 Stufe 32.2) ─────


def test_worker_heartbeat_response_always_carries_config_version(sched):
    r = sched.post("/-/worker", json={"worker": "w", "host": "h"})
    assert r.status_code == 200
    assert "config_version" in r.json()


def test_worker_heartbeat_omits_bundle_when_version_unchanged(sched):
    sched.post("/-/worker", json={"worker": "w", "host": "h", "node_id": "n1"})
    sched.post("/-/worker/n1/approve")
    v1 = sched.post("/-/worker", json={"worker": "w", "host": "h", "node_id": "n1"}).json()
    r = sched.post("/-/worker", json={
        "worker": "w", "host": "h", "node_id": "n1",
        "client_config_version": v1["config_version"]})
    assert "config_bundle" not in r.json()


def test_worker_heartbeat_omits_bundle_when_pending_not_approved(sched, monkeypatch):
    # Ein pending-Knoten (noch nicht freigeschaltet, erster Heartbeat) bekommt
    # nie ein Bundle, selbst bei abweichender Version.
    monkeypatch.setenv("BIBI_JOB_ENV_FOO", "secret")
    r = sched.post("/-/worker", json={
        "worker": "w", "host": "h", "node_id": "n-pending", "client_config_version": "stale"})
    assert "config_bundle" not in r.json()


def test_worker_heartbeat_includes_bundle_when_approved_and_version_differs(sched, monkeypatch):
    monkeypatch.setenv("BIBI_JOB_ENV_FOO", "secret")
    sched.post("/-/worker", json={"worker": "w", "host": "h", "node_id": "n2"})
    sched.post("/-/worker/n2/approve")
    r = sched.post("/-/worker", json={
        "worker": "w", "host": "h", "node_id": "n2", "client_config_version": "stale"})
    assert r.json()["config_bundle"] == {"BIBI_JOB_ENV_FOO": "secret"}


def test_sweeper_reaps_local_job_without_live_pid(tmp_path: Path):
    """Das Gegenstück (m.rau/bibi#38): PID-basiert wird der lokale Worker sehr
    wohl geprüft — vorher geschah das nur beim Daemon-Start, was bei
    ``Restart=always`` praktisch „nie" bedeutete.

    Der Unterschied zum Registry-Pfad ist nicht Willkür: die Registry kann sich
    täuschen (zwei Knoten unter demselben Namen), eine tote PID nicht.
    """
    import secrets
    import time as _t

    from bibi.daemon import job_db
    from bibi.daemon.sweeper import Sweeper
    p = tmp_path / "j.sqlite"
    conn = job_db.connect(p)
    jid = secrets.token_hex(4)
    conn.execute(
        "INSERT INTO jobs (id, slug, schedule_ref, kind, payload, status, worker, "
        "enqueued_at, next_fire_at) VALUES (?,?,?,?,?, 'running', 'me', ?, 0)",
        (jid, "x", "x.md", "job", "e", _t.time()))
    conn.close()
    # pid_check_interval=0 erzwingt den Check im ersten Tick — im Betrieb
    # kommt er nach 45 s, weil der Startzeitpunkt bereits von
    # _scheduler_startup() abgedeckt ist.
    sw = Sweeper(db_path=p, autorun=False, local_worker_name="me", pid_check_interval=0)
    assert sw.tick_once()["no_pid"] == 1
    conn = job_db.connect(p)
    row = conn.execute("SELECT status, reason FROM jobs WHERE id=?", (jid,)).fetchone()
    assert (row["status"], row["reason"]) == ("killed", "no_process")
    conn.close()


def test_sweeper_leaves_starting_jobs_alone(tmp_path: Path):
    """``starting`` heißt *gerade im Setup* — Worktree anlegen, Container
    aufräumen, Image bauen. Das darf Minuten dauern und hat per Konstruktion
    noch keine PID. Ein laufender Sweep, der es abräumte, würde genau die Jobs
    töten, die gerade starten (m.rau/bibi#38).
    """
    import secrets
    import time as _t

    from bibi.daemon import job_db
    from bibi.daemon.sweeper import Sweeper
    p = tmp_path / "j.sqlite"
    conn = job_db.connect(p)
    jid = secrets.token_hex(4)
    conn.execute(
        "INSERT INTO jobs (id, slug, schedule_ref, kind, payload, status, worker, "
        "enqueued_at, next_fire_at) VALUES (?,?,?,?,?, 'starting', 'me', ?, 0)",
        (jid, "x", "x.md", "job", "e", _t.time()))
    conn.close()
    sw = Sweeper(db_path=p, autorun=False, local_worker_name="me", pid_check_interval=0)
    assert sw.tick_once().get("no_pid", 0) == 0
    conn = job_db.connect(p)
    assert conn.execute("SELECT status FROM jobs WHERE id=?",
                        (jid,)).fetchone()["status"] == "starting"
    conn.close()


def test_sweeper_reaps_jobs_of_blocked_node(tmp_path: Path):
    """m.rau/bibi#23: die Ban-Semantik war nur halb gebaut — ein blockierter
    Knoten wird beim Heartbeat abgewiesen, seine laufenden Jobs blieben aber
    stehen. Ein Bann, der laufende Arbeit weiterlaufen lässt, ist keiner.
    """
    import secrets
    import time as _t

    from bibi.daemon import job_db
    from bibi.daemon.sweeper import Sweeper
    p = tmp_path / "j.sqlite"
    conn = job_db.connect(p)
    jid = secrets.token_hex(4)
    conn.execute(
        "INSERT INTO jobs (id, slug, schedule_ref, kind, payload, status, worker, "
        "enqueued_at, next_fire_at) VALUES (?,?,?,?,?, 'running', 'baddie', ?, 0)",
        (jid, "x", "x.md", "job", "e", _t.time()))
    job_db.set_node_approval(conn, "node-bad", "blocked")
    conn.commit()
    conn.close()

    reg = WorkerRegistry()
    reg.heartbeat("baddie", "h", node_id="node-bad", now=_t.time())
    sw = Sweeper(db_path=p, registry=reg, autorun=False, local_worker_name="me",
                 pid_check_interval=0)
    assert sw.tick_once()["banned"] == 1
    conn = job_db.connect(p)
    row = conn.execute("SELECT status, reason FROM jobs WHERE id=?", (jid,)).fetchone()
    assert (row["status"], row["reason"]) == ("killed", "no_process")
    conn.close()


def test_sweeper_leaves_jobs_of_approved_node_alone(tmp_path: Path):
    import secrets
    import time as _t

    from bibi.daemon import job_db
    from bibi.daemon.sweeper import Sweeper
    p = tmp_path / "j.sqlite"
    conn = job_db.connect(p)
    jid = secrets.token_hex(4)
    conn.execute(
        "INSERT INTO jobs (id, slug, schedule_ref, kind, payload, status, worker, "
        "enqueued_at, next_fire_at) VALUES (?,?,?,?,?, 'running', 'goodie', ?, 0)",
        (jid, "x", "x.md", "job", "e", _t.time()))
    job_db.set_node_approval(conn, "node-ok", "approved")
    conn.commit()
    conn.close()

    reg = WorkerRegistry()
    reg.heartbeat("goodie", "h", node_id="node-ok", now=_t.time())
    sw = Sweeper(db_path=p, registry=reg, autorun=False, local_worker_name="me",
                 pid_check_interval=0)
    assert sw.tick_once().get("banned", 0) == 0
    conn = job_db.connect(p)
    assert conn.execute("SELECT status FROM jobs WHERE id=?",
                        (jid,)).fetchone()["status"] == "running"
    conn.close()


# ── Bootstrap-Token (m.rau/bibi#141, Nodes.md §3.3) ─────────────────────────
#
# Der Startschluessel loest den Deadlock, den die Schranke an approve/block
# erst erzeugt: ein frischer Scheduler hat null approved-Knoten, der erste
# Client meldet sich als pending — und niemand ist berechtigt, ihn
# freizugeben. Ohne Host-FE (bibi5 streicht es) bliebe nur ein SQL-Update
# von Hand.
#
# Er ist ausdruecklich **kein** Wiedergaenger von BIBI_CONNECT_SECRET: einer,
# einmal, befristet, fuer genau eine Freigabe — und nur ausgebbar, solange
# der Bootstrap wirklich laeuft.


def test_a_bootstrap_token_is_issued_while_nothing_is_approved(tmp_path: Path):
    from bibi.daemon import job_db
    conn = job_db.connect(tmp_path / "j.sqlite")
    try:
        assert job_db.create_bootstrap_token(conn, now=1000.0)
    finally:
        conn.close()


def test_no_bootstrap_token_once_a_node_is_approved(tmp_path: Path):
    """**Die Sicherung, die den Token vom alten Gate trennt.** Gibt es einen
    freigeschalteten Knoten, ist der Bootstrap vorbei — ab da fuehrt der Weg
    ueber den Nodes-Screen. Damit kann der Token nie zur bequemen Abkuerzung
    werden: er existiert nur in der Lage, fuer die er gebaut ist."""
    from bibi.daemon import job_db
    conn = job_db.connect(tmp_path / "j.sqlite")
    try:
        job_db.set_node_approval(conn, "schon-da", "approved")
        assert job_db.create_bootstrap_token(conn, now=1000.0) is None
    finally:
        conn.close()


def test_redeeming_a_token_approves_that_node(tmp_path: Path):
    from bibi.daemon import job_db
    conn = job_db.connect(tmp_path / "j.sqlite")
    try:
        tok = job_db.create_bootstrap_token(conn, now=1000.0)
        assert job_db.redeem_bootstrap_token(conn, tok, "erster", now=1100.0) is True
        assert job_db.node_approval_status(conn, "erster") == "approved"
    finally:
        conn.close()


def test_a_token_works_exactly_once(tmp_path: Path):
    """Zwei Knoten mit demselben Token sind kein Rennen — der zweite geht leer
    aus. Verbraucht wird in derselben Anweisung, die ihn prueft (ein `DELETE`
    mit `rowcount`), damit dazwischen nichts passieren kann."""
    from bibi.daemon import job_db
    conn = job_db.connect(tmp_path / "j.sqlite")
    try:
        tok = job_db.create_bootstrap_token(conn, now=1000.0)
        assert job_db.redeem_bootstrap_token(conn, tok, "erster", now=1100.0) is True
        assert job_db.redeem_bootstrap_token(conn, tok, "zweiter", now=1100.0) is False
        assert job_db.node_approval_status(conn, "zweiter") == "pending"
    finally:
        conn.close()


def test_an_expired_token_is_worthless(tmp_path: Path):
    """24 Stunden, danach wertlos — anders als das unbefristete Gate, das
    abgeschafft wurde."""
    from bibi.daemon import job_db
    conn = job_db.connect(tmp_path / "j.sqlite")
    try:
        tok = job_db.create_bootstrap_token(conn, now=1000.0)
        spaeter = 1000.0 + 24 * 3600 + 1
        assert job_db.redeem_bootstrap_token(conn, tok, "zu-spaet", now=spaeter) is False
        assert job_db.node_approval_status(conn, "zu-spaet") == "pending"
    finally:
        conn.close()


def test_an_unknown_token_never_approves_anything(tmp_path: Path):
    from bibi.daemon import job_db
    conn = job_db.connect(tmp_path / "j.sqlite")
    try:
        assert job_db.redeem_bootstrap_token(conn, "ausgedacht", "wer-auch-immer",
                                             now=1000.0) is False
    finally:
        conn.close()


def test_a_heartbeat_with_a_valid_token_comes_back_approved(sched, team_repo: Path):
    """Der Weg, der den Deadlock loest: der erste Client schickt seinen
    Startschluessel im Heartbeat mit und ist danach freigeschaltet — ohne dass
    jemand einen Screen aufschlagen musste, den es auf dem Host nicht gibt."""
    from bibi.daemon import job_db
    conn = job_db.connect()
    try:
        tok = job_db.create_bootstrap_token(conn)
    finally:
        conn.close()

    r = sched.post("/-/worker", json={"worker": "erster", "host": "mac",
                                      "node_id": "node-1", "bootstrap_token": tok})
    assert r.status_code == 200

    conn = job_db.connect()
    try:
        assert job_db.node_approval_status(conn, "node-1") == "approved"
    finally:
        conn.close()


def test_a_heartbeat_with_a_spent_token_is_rejected(sched, team_repo: Path):
    """Der zweite Knoten mit demselben Token bekommt `401` — und bleibt
    `pending`. Er wird bewusst **abgewiesen** statt still als pending
    durchgelassen: wer einen Startschluessel vorzeigt, der nicht gilt, soll
    das erfahren und nicht glauben, es haette geklappt."""
    from bibi.daemon import job_db
    conn = job_db.connect()
    try:
        tok = job_db.create_bootstrap_token(conn)
    finally:
        conn.close()

    assert sched.post("/-/worker", json={"worker": "erster", "host": "mac",
                                         "node_id": "node-1",
                                         "bootstrap_token": tok}).status_code == 200
    r = sched.post("/-/worker", json={"worker": "zweiter", "host": "pi",
                                      "node_id": "node-2", "bootstrap_token": tok})
    assert r.status_code == 401

    conn = job_db.connect()
    try:
        assert job_db.node_approval_status(conn, "node-2") == "pending"
    finally:
        conn.close()


def test_a_heartbeat_without_a_token_still_registers_as_pending(sched):
    """Die Gegenprobe: der Token ist ein **zusaetzlicher** Weg, keine neue
    Pflicht. Ein Knoten ohne Startschluessel meldet sich weiterhin an und
    wartet als `pending` auf seine Freigabe — genau wie der zehnte Knoten,
    fuer den der Token nicht gedacht ist."""
    r = sched.post("/-/worker", json={"worker": "spaeter", "host": "pi",
                                      "node_id": "node-9"})
    assert r.status_code == 200
    from bibi.daemon import job_db
    conn = job_db.connect()
    try:
        assert job_db.node_approval_status(conn, "node-9") == "pending"
    finally:
        conn.close()


def test_bootstrapping_is_a_logged_event(sched, team_repo: Path, caplog):
    """**Wer einen Startschluessel einloest, tut etwas Nachlesbares**
    (Nodes.md §3.3, Klasse `E`). Die eingeloeste Zeile wird aus der DB
    geloescht — bliebe der Vorgang auch im Log unsichtbar, waere hinterher
    nicht mehr feststellbar, dass dieser Knoten sich selbst freigeschaltet hat
    und nicht ein Mensch ihn freigab.
    """
    import logging as _log

    from bibi.daemon import job_db
    conn = job_db.connect()
    try:
        tok = job_db.create_bootstrap_token(conn)
    finally:
        conn.close()

    with caplog.at_level(_log.INFO):
        assert sched.post("/-/worker", json={
            "worker": "erster", "host": "mac", "node_id": "node-1",
            "bootstrap_token": tok}).status_code == 200

    treffer = [r for r in caplog.records
               if getattr(r, "bibi", {}).get("event") == "connect.bootstrapped"]
    assert treffer, "kein connect.bootstrapped im Log"
    assert treffer[0].bibi["fields"].get("node_id") == "node-1"


def test_the_cli_prints_a_ready_made_init_line(team_repo: Path, capsys):
    """`bibi-ctrl bootstrap-token` gibt nicht nur den Schluessel aus, sondern
    die Zeile, die der Mensch am anderen Rechner braucht — er steht ohnehin
    gerade in einer Shell, wenn er einen Scheduler aufsetzt."""
    from bibi.ctrl import main
    assert main(["bootstrap-token"]) == 0
    aus = capsys.readouterr().out
    assert "bibi-ctrl init" in aus and "--token" in aus


def test_the_cli_refuses_once_the_bootstrap_is_over(team_repo: Path, capsys):
    """Gibt es einen freigeschalteten Knoten, ist der Startschluessel-Weg
    geschlossen — und der Befehl sagt, wo es stattdessen langgeht. Ohne diese
    Verweigerung waere er genau das bequeme Dauergeheimnis, das mit
    `BIBI_CONNECT_SECRET` abgeschafft wurde."""
    from bibi.ctrl import main
    from bibi.daemon import job_db
    conn = job_db.connect()
    try:
        job_db.set_node_approval(conn, "schon-da", "approved")
        conn.commit()
    finally:
        conn.close()
    assert main(["bootstrap-token"]) != 0
    assert "Nodes" in capsys.readouterr().out


def test_the_printed_line_is_one_the_cli_actually_accepts(team_repo: Path, capsys):
    """**Die Lehre aus m.rau/bibi#151, hier vorbeugend angewandt.** Nodes.md
    §3.3 skizziert `bibi-ctrl init --connect … --token …` — aber `--connect`
    gibt es bei `init` gar nicht, das Flag sitzt an `daemon`. Eine Zeile zum
    Kopieren, die der Parser ablehnt, ist ein toter Weg mit Einladung.

    Deshalb wird sie hier nicht auf Aussehen geprueft, sondern **dem Parser
    vorgelegt**.
    """
    import shlex

    from bibi.ctrl import main
    assert main(["bootstrap-token"]) == 0
    zeile = next(z for z in capsys.readouterr().out.splitlines()
                 if "bibi-ctrl init" in z)
    argv = shlex.split(zeile.strip())[1:]  # ohne das fuehrende "bibi-ctrl"
    assert main(argv) == 0  # wirklich ausgefuehrt, nicht nur geparst


def test_init_stores_the_token_in_the_node_env(team_repo: Path):
    from bibi import config
    from bibi.ctrl import main
    assert main(["init", "--non-interactive", "--scheduler-url", "http://h:8780",
                 "--role", "connect", "--token", "abc123"]) == 0
    assert config.read_env().get("BIBI_BOOTSTRAP_TOKEN") == "abc123"


def test_the_heartbeat_forgets_the_token_after_it_worked(team_repo: Path):
    """**Ein Startschluessel, der liegen bleibt, ist ein Dauergeheimnis** —
    genau das, was mit `BIBI_CONNECT_SECRET` abgeschafft wurde. Nach dem ersten
    erfolgreichen Heartbeat schreibt der Client seine env ohne ihn zurueck.
    """
    from bibi import config
    from bibi.daemon.heartbeat import Heartbeat

    config.write_env({**config.read_env(), "BIBI_BOOTSTRAP_TOKEN": "einmalig"})

    class _OK:
        def register(self, *a, **kw):
            self.gesehen = kw.get("bootstrap_token")
            return {}

    client = _OK()
    hb = Heartbeat(client=client, worker_name="w", role="connect")
    hb._beat()
    assert client.gesehen == "einmalig", "der Token muss im Heartbeat mitreisen"
    assert config.read_env().get("BIBI_BOOTSTRAP_TOKEN") == "", \
        "nach dem ersten Erfolg gehoert er geloescht"


# ── Die Schranke selbst (m.rau/bibi#141) ───────────────────────────────────
#
# Sie kommt bewusst NACH dem Startschluessel: allein erzeugt sie den Deadlock
# erst, den es vorher nicht gab — ein frischer Scheduler koennte seinen ersten
# Client nie freigeben.


#: RFC 5737 TEST-NET-1 — nie ein echter Peer, wie in `test_job_control_approval`.
#: Noetig, weil Starlettes TestClient sich als "testclient" meldet und damit
#: als **lokal** gilt: der reale Angriff kam ueber das Netz, und nur so wird er
#: hier auch nachgestellt.
_FREMD = ("192.0.2.10", 51234)


@pytest.fixture
def fremder(team_repo: Path):
    app = create_app(roles.resolve({"scheduler"}))
    with TestClient(app, client=_FREMD) as c:
        yield c


def test_approve_from_the_network_without_a_header_is_refused(fremder):
    """**Der Kern des Ticketbefunds.** `approve` und `block` waren die
    einzigen schreibenden Routen ihrer Datei ohne Auth-Dependency: wer den
    Scheduler erreichte, konnte sich selbst freischalten — und bekam beim
    naechsten Heartbeat das Config-Bundle, also **alle** verteilten
    Credentials der Flotte.

    Neun Zeilen tiefer stand die Dependency bei `disconnect` laengst, mit
    genau dieser Begruendung im Docstring.
    """
    fremder.post("/-/worker", json={"worker": "fremd", "host": "x",
                                    "node_id": "eindringling"})
    assert fremder.post("/-/worker/eindringling/approve").status_code == 403

    from bibi.daemon import job_db
    conn = job_db.connect()
    try:
        assert job_db.node_approval_status(conn, "eindringling") == "pending"
    finally:
        conn.close()


def test_a_pending_node_cannot_approve_itself(fremder):
    """Die zweite Haelfte desselben Angriffs, mit Header statt ohne: ein
    Knoten, der sich brav gemeldet hat, darf sich trotzdem nicht selbst
    freigeben. `pending` heisst *wartet auf eine Entscheidung*, nicht *darf
    sie selbst treffen*."""
    fremder.post("/-/worker", json={"worker": "fremd", "host": "x",
                                    "node_id": "eindringling"})
    r = fremder.post("/-/worker/eindringling/approve",
                     headers={"X-Bibi-Node-Id": "eindringling"})
    assert r.status_code == 403


def test_block_is_guarded_too(fremder):
    """`block` ist die andere Haelfte des Paars — ungeschuetzt koennte jeder
    jeden aus der Flotte werfen, also einen Denial-of-Service gegen die
    eigenen Knoten fahren."""
    fremder.post("/-/worker", json={"worker": "opfer", "host": "x", "node_id": "brav"})
    assert fremder.post("/-/worker/brav/block").status_code == 403


def test_an_approved_node_may_approve_others(fremder):
    """Die Gegenprobe, damit die Schranke nicht einfach alles zusperrt: wer
    freigeschaltet ist, gibt weitere frei — genau der Weg, den der
    Nodes-Screen des ersten Clients geht, sobald der Bootstrap vorbei ist.
    Ohne diesen Test bliebe unklar, ob die Schranke unterscheidet oder nur
    zusperrt."""
    from bibi.daemon import job_db
    conn = job_db.connect()
    try:
        job_db.set_node_approval(conn, "erster", "approved")
        conn.commit()
    finally:
        conn.close()

    fremder.post("/-/worker", json={"worker": "neu", "host": "y", "node_id": "zweiter"})
    r = fremder.post("/-/worker/zweiter/approve", headers={"X-Bibi-Node-Id": "erster"})
    assert r.status_code == 200

    conn = job_db.connect()
    try:
        assert job_db.node_approval_status(conn, "zweiter") == "approved"
    finally:
        conn.close()


def test_the_local_operator_keeps_the_manual_way(sched):
    """**Bewusst offen gelassen, und deshalb festgehalten:** ein Aufruf vom
    eigenen Rechner ohne Header bleibt erlaubt (`_require_approved_or_local`,
    Nachtrag Befund 4). Gegen einen lokalen Angreifer schuetzt diese Ebene
    ohnehin nicht — er koennte `bibi-ctrl` rufen oder die SQLite schreiben.

    Fuer den Bootstrap ist es die Rueckfallebene hinter dem Startschluessel:
    wer auf dem Host in einer Shell steht, kommt weiterhin durch. Ein Test
    darauf, damit die Freiheit eine Entscheidung bleibt und nicht eines Tages
    unbemerkt zugezogen wird."""
    sched.post("/-/worker", json={"worker": "neu", "host": "y", "node_id": "kandidat"})
    assert sched.post("/-/worker/kandidat/approve").status_code == 200
