# PLAN-11 — Routing-Refactor: Wrapper entkernen + bibi.job-Modul

_TDD throughout: erst der rote Test, dann die Implementierung bis grün._

---

## Vorbedingung: Stufe 11.0 — Hygiene

### 11.0a — Slow-Test-Marker einführen

**Problem:** `uv run pytest` läuft 2:15 min. Ursache: ~40 Tests in
`test_worker.py` und `test_container_claude.py` spawnen echte Prozesse / Docker.

**Lösung:**

`tests/conftest.py` — `--slow`-Option ergänzen:

```python
def pytest_addoption(parser):
    parser.addoption("--slow", action="store_true", default=False,
                     help="run slow tests (subprocess/Docker)")

def pytest_collection_modifyitems(config, items):
    if not config.getoption("--slow"):
        skip = pytest.mark.skip(reason="use --slow to run")
        for item in items:
            if "slow" in item.keywords:
                item.add_marker(skip)
```

Tests markieren mit `@pytest.mark.slow`:
- `test_worker.py` — alle Tests (echte git-Repos, `time.sleep`, Prozesse)
- `test_container_claude.py` — alle Tests (Docker-Container)

`pyproject.toml` — Marker registrieren:
```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
markers = ["slow: echte Prozesse/Docker — nur mit --slow"]
```

**Ziel-Timings:**
- `uv run pytest` (ohne `--slow`) → < 15 s
- `uv run pytest --slow` → volle Suite

---

### 11.0b — 9 Failing Tests

**Analyse:**

| Test | Ursache | Fix |
|---|---|---|
| `test_bands_membership_terminal_excluded` | `_ACTIVE_STATES` in `render.py` enthält terminale Zustände (`killed`, `error`, `zombie`, `inactive`) | `render.py`: `_ACTIVE_STATES = ("running", "awaiting", "failed", "deferred")` |
| `test_feed_maint_banner_visible_when_on` | Text "⚠ Dispatch pausiert" ≠ spec "Wartungsmodus aktiv" | `render.py`: Text im `maintbanner`-Span korrigieren |
| `test_kill_pending_is_409` | `report_status(status="killed")` akzeptiert `pending` → liefert nicht `"invalid"` | Transition-Guard in `job_db.py` prüfen: `pending → killed` muss verboten sein |
| `test_schedule_detail_page_renders_runs` | Template generiert kein `hx-get="/-/ui/run/{id}/output"` für Runs | `render.py`: `schedule_detail_inner` — Toggle-Attribut für nicht-jüngsten Run sicherstellen |
| `test_schedule_detail_action_bar_with_job` | Action-Bar-Markup fehlt oder geändert | `render.py`: Action-Bar-Render prüfen und reparieren |
| `test_schedule_detail_route` | "Output ↓" aus Template entfernt | `render.py`: Label wiederherstellen |
| `test_top_run_output_inline_older_keep_toggle` | `hx-get` für ältere Runs nicht im HTML | Wie `page_renders_runs` |
| `test_execution_detail_meta` | "exit 0" / "Dauer 12 s" nicht im HTML | `render.py`: `execution_detail_page` — Zusammenfassung "exit 0" + berechnete Dauer wieder anzeigen |
| `test_execution_detail_duration_from_timestamps` | Wie oben | Wie oben |

**Vorgehen:** Tests laufen lassen, einen nach dem anderen rot → grün. Keine neuen Abstraktionen.

Nach 11.0a und 11.0b: `uv run pytest` → **0 failed, < 15 s**.
Nach `uv run pytest --slow` → **0 failed**.

---

## Stufe 11.1 — `bibi.job`-Modul (kein „SDK")

**Was:** Ein importierbares `bibi.job`-Modul, das Job-Autoren von der Protokoll-Kenntnis befreit.
Statt `print('BIBI:{"name":"running"}')` von Hand: einfache Funktionen + eine Exception.

**Neue Datei: `bibi/job.py`**

```python
"""bibi.job — Signale für Job-Autoren.

Schreibt BIBI:{...}-Zeilen auf stdout. Der Wrapper parst und verarbeitet sie.
"""
import json, sys

def _emit(payload: dict) -> None:
    print(f"BIBI:{json.dumps(payload, separators=(',', ':'))}", flush=True)

def running() -> None:
    """Job meldet sich als laufend (Optional — Wrapper-Default nach Spawn)."""
    _emit({"name": "running"})

def awaiting(input_request: str, *, input_format: str = "text",
             port: int | None = None) -> None:
    """Job wartet auf menschliche Eingabe (HITL)."""
    p = {"name": "awaiting", "input_request": input_request,
         "input_format": input_format}
    if port is not None:
        p["port"] = port
    _emit(p)

def app_register(port: int, prefix: str | None = None) -> None:
    """Job meldet seinen HTTP-Server-Port. Wrapper trägt Traefik-Route ein."""
    p = {"name": "app_register", "port": port}
    if prefix is not None:
        p["prefix"] = prefix
    _emit(p)

class Deferred(Exception):
    """Wirft der Job diese Exception, gilt er als zurückgestellt (nicht gescheitert).

    Beispiel:
        raise bibi.job.Deferred(seconds=300)   # in 5 min neu starten
    """
    def __init__(self, seconds: int = 60) -> None:
        self.seconds = seconds
        super().__init__(f"deferred for {seconds}s")
```

**Tests (`tests/test_bibi_job.py`):**

```python
# TDD: erst diese Tests schreiben, dann bibi/job.py implementieren
def test_running_writes_signal(capsys):
    bibi.job.running()
    assert capsys.readouterr().out.strip() == 'BIBI:{"name":"running"}'

def test_awaiting_includes_format(capsys):
    bibi.job.awaiting("Wie viele?", input_format="number")
    out = capsys.readouterr().out
    payload = json.loads(out.split("BIBI:", 1)[1])
    assert payload["name"] == "awaiting"
    assert payload["input_format"] == "number"

def test_deferred_is_exception():
    d = bibi.job.Deferred(seconds=120)
    assert d.seconds == 120
    assert isinstance(d, Exception)

def test_app_register_emits_port(capsys):
    bibi.job.app_register(port=9100)
    payload = json.loads(capsys.readouterr().out.split("BIBI:", 1)[1])
    assert payload == {"name": "app_register", "port": 9100}
```

**Wrapper-Seite:** `bibi/wrapper/__init__.py` — `Deferred`-Exception abfangen (nach
dem Subprocess-Exit) und als `BIBI:{"name":"deferred",...}` in job_db schreiben.
Der Wrapper importiert `bibi.job.Deferred`, um denselben Typ zu erkennen.

---

## Stufe 11.2 — job_db erweitern

**Neue Felder** (non-breaking, NULL-Default):

```sql
ALTER TABLE jobs ADD COLUMN last_ping_at REAL;      -- Zombie-Timeout (§2.5)
ALTER TABLE jobs ADD COLUMN demand       TEXT;       -- HITL-Demand JSON
```

`app_url` existiert bereits (PLAN-10, Schema v10).

**Neue Funktionen in `job_db.py`:**

```python
def touch_ping(conn, job_id: str) -> bool:
    """Setzt last_ping_at = now. Gibt False zurück wenn Job nicht existiert."""

def set_demand(conn, job_id: str, demand: dict) -> None:
    """Schreibt aktuellen HITL-Demand (überschreibt)."""

def get_demand(conn, job_id: str) -> dict | None:
    """Liest aktuellen HITL-Demand."""
```

**Tests (TDD):**

```python
def test_touch_ping_updates_timestamp(job_db_conn):
    ...

def test_get_demand_returns_none_if_not_set(job_db_conn):
    ...
```

Migration: Schema v11 (analog zu bisherigen `_mig_*`-Funktionen).

---

## Stufe 11.3 — Wrapper entkernen

**Was entfällt:**
- `bibi/wrapper/server.py` — gelöscht
- `BIBI_WRAPPER_PORT` — Env-Var entfällt
- HTTP-Aufrufe von Job an `/-/signal/*` — ersetzt durch stdout `BIBI:{...}`

**Was bleibt (und wächst):**
- `bibi/wrapper/__init__.py` / `bibi/wrapper/exec_backend.py` — liest stdout-Pipe,
  filtert `BIBI:`-Zeilen heraus, parst JSON, schreibt direkt in job_db

**Signal-Parser (neuer Code):**

```python
def _parse_bibi_line(line: str) -> dict | None:
    """Gibt None zurück wenn Zeile kein BIBI-Signal ist."""
    if not line.startswith("BIBI:"):
        return None
    try:
        return json.loads(line[5:])
    except json.JSONDecodeError:
        return None

def _handle_signal(conn, job_id: str, signal: dict) -> None:
    name = signal.get("name")
    if name == "running":
        job_db.report_status(conn, job_id, status="running")
    elif name == "awaiting":
        job_db.report_status(conn, job_id, status="awaiting")
        job_db.set_demand(conn, job_id, signal)
    elif name == "app_register":
        job_db.set_app_port(conn, job_id, signal["port"])
    elif name == "deferred":
        job_db.report_status(conn, job_id, status="deferred",
                             defer_time=signal.get("defer_time", 60))
```

**Tests (TDD):**

```python
def test_bibi_line_parsed_correctly():
    sig = _parse_bibi_line('BIBI:{"name":"running"}')
    assert sig == {"name": "running"}

def test_non_bibi_line_returns_none():
    assert _parse_bibi_line("normale Ausgabe") is None

def test_awaiting_signal_sets_demand(job_db_conn):
    _handle_signal(conn, "j1", {"name": "awaiting", "input_request": "?",
                                "input_format": "text"})
    assert job_db.get_demand(conn, "j1")["input_request"] == "?"

def test_deferred_exception_caught_by_wrapper(tmp_path):
    # Wrapper fängt bibi.job.Deferred ab, schreibt DEFERRED in DB
    ...
```

**Bestehende Tests:** `POST /-/signal/awaiting`-HTTP-Calls durch stdout-Assertions ersetzen.

---

## Stufe 11.4 — Worker: ping → DB, app_register → Traefik

**`POST /-/job/{id}/ping` (Worker-Route, bereits in `app.py`):**

```python
@app.post("/-/job/{id}/ping")
def job_ping(id: str):
    with job_db.connect(worker.db_path) as conn:
        ok = job_db.touch_ping(conn, id)
    return {"ok": ok}
```

Wrapper liest `last_ping_at` aus DB für Zombie-Timeout-Prüfung (statt In-Memory-Timer).

**`app_register`-Signal → Traefik:**

Worker pollt job_db auf `app_port`-Änderungen (oder reagiert auf ein DB-Notification-Polling).
Bei neuem `app_port`:

```python
def _register_app_route(job_id: str, port: int) -> None:
    """Traefik-Route für Job-App registrieren (Docker-Label setzen oder
    File-Provider-Config schreiben — je nach exec_mode)."""
    ...

def _deregister_app_route(job_id: str) -> None:
    """Route beim Job-Ende entfernen."""
    ...
```

**Tests (TDD):**

```python
def test_ping_writes_last_ping_at(client):
    jid = _seed_status("running")
    r = client.post(f"/-/job/{jid}/ping")
    assert r.status_code == 200
    with job_db.connect(db_path) as conn:
        assert job_db.get_job(conn, jid)["last_ping_at"] is not None

def test_app_register_signal_triggers_traefik_call(monkeypatch):
    calls = []
    monkeypatch.setattr("bibi.daemon.worker._register_app_route",
                        lambda jid, port: calls.append((jid, port)))
    # Worker verarbeitet DB-Eintrag mit app_port → ruft _register_app_route auf
    ...
```

---

## Stufe 11.5 — Traefik-Labels bereinigen

**`bibi/wrapper/exec_backend.py`:**

Traefik-Label für `PathPrefix(/-/job/{id}/)` → Wrapper Port 8080 entfernen.
Nur App-Content-Label bleibt (und wird jetzt dynamisch via `_register_app_route` gesetzt,
nicht mehr statisch beim `docker run`).

**Test:**

```python
def test_exec_backend_has_no_wrapper_job_route_label():
    labels = build_traefik_labels(job_id="abc", app_port=None)
    assert not any("/-/job/" in v for v in labels.values())
```

---

## Stufe 11.6 — DESIGN.md §7.5 aktualisieren

- Wrapper als reiner Subprocess-Supervisor dokumentieren (kein HTTP-Server)
- Worker als kanonischer API-Server für alle `/-/job/{id}/`-Routen
- `bibi.job`-Modul als Job-Authoring-Schnittstelle dokumentieren
- `BIBI:{...}` stdout-Protokoll als kanonisch festschreiben
- Changelog-Eintrag für PLAN-11

---

## Aufwand-Schätzung

| Stufe | Aufwand | Risiko |
|---|---|---|
| 11.0a slow-Marker | ~1h | minimal |
| 11.0b 9 failing tests | ~2h | minimal (Template-Drift ist Fleißarbeit) |
| 11.1 bibi.job | ~1h | minimal |
| 11.2 job_db | ~1h | minimal (NULL-Default-Migration) |
| 11.3 Wrapper entkernen | ~3h | mittel (bestehende Tests umschreiben) |
| 11.4 Worker ping + app_register | ~2h | mittel (Traefik-Integration) |
| 11.5 Traefik-Labels | ~0.5h | minimal |
| 11.6 DESIGN.md | ~1h | minimal |

Unabhängig deploybar: 11.0 → 11.1 → 11.2 → 11.3 → 11.4 → 11.5 → 11.6.
Jede Stufe kann einzeln reviewed und gemergt werden.

---

## Baseline (vor PLAN-11)

`uv run pytest`: **9 failed, 683 passed** (1 warning, 2:15 min)
`uv run pytest --slow` (nach 11.0a): volle Suite inkl. Prozess-Tests
