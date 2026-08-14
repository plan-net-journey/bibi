"""Lauf-Attribute (#40) — was **dieser eine Lauf** über sich weiß.

**Der Befund ist eine Lücke in der Mitte von drei Schichten.** Sichtbar war
nur die erste: `/-/jobs/{uid}/attrs` zeigt die Konfiguration des **Jobs** — und
die ist per Konstruktion für jeden seiner Läufe identisch, also genau die
Sache, die einen Unterschied zwischen zwei Läufen nicht erklären kann. Die
zweite Schicht (was der Lauf hatte) und die dritte (was zur Laufzeit entstand)
lagen abgelegt und ungesehen.

Die Erhebung dazu steht in `20260811.Lauf-Attribute.md` im Bibi5-Case. Ihr
Kernbefund bestimmt, was diese Seite behaupten darf: der Snapshot friert zum
**Archivierungs**zeitpunkt ein, nicht zur Startzeit. Ein Unterschied zwischen
Snapshot und heutiger Job-Konfiguration heißt deshalb „dieser Lauf hatte einen
anderen Wert" — nicht „der Lauf hat ihn gesetzt".
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bibi.controller import render
from bibi.daemon import roles
from bibi.daemon.app import create_app
from bibi.schedule.models import job_uid

NOW = 1_000_000.0


def _snapshot(**kw):
    basis = {"slug": "EngineCI", "kind": "job", "payload": "pytest -q",
             "schedule": "0 * * * *", "attempts": 1, "backoff": "fixed",
             "model": "opus", "silence_timeout": 3600}
    return {**basis, **kw}


def _lauf(**kw):
    """Eine Journal-Zeile, wie `get_journal()` sie liefert — mit Snapshot."""
    basis = {
        "id": 7, "run_id": "EngineCI:3", "slug": "EngineCI", "kind": "job",
        "status": "complete", "reason": None,
        "started_at": NOW - 120, "finished_at": NOW - 60, "archived_at": NOW - 60,
        "exit_code": 0, "exec_runtime": 60.0,
        "host": "sarasate", "worker": "w1",
        "output_ref": "data/x/output.jsonl",
        "commit_sha": "a1b2c3d4e5", "branch": "agent/EngineCI",
        "domain": "scheduled", "pinned_host": None,
        "snapshot": json.dumps(_snapshot()),
    }
    return {**basis, **kw}


# ── Die Tabelle trennt die drei Herkünfte ──────────────────────────────────


def test_the_view_names_all_three_layers():
    """Eine Tabelle mit einer Spalte „woher", nicht zwei getrennte Ansichten:
    die eigentliche Frage — *warum ging dieser Lauf anders aus* — wird damit in
    einem Blick beantwortet statt in zweien."""
    html = render.run_attrs_page_v5(slug="EngineCI", lauf=_lauf(),
                                    job_spec=_snapshot(), now=NOW)
    assert "SOURCE" in html
    for herkunft in ("job", "run", "runtime"):
        assert f'>{herkunft}<' in html, herkunft


def test_a_value_the_run_had_differently_is_marked_as_run():
    """Der Test, der die Spalte an einen Fall bindet, in dem sie etwas aussagt:
    der Lauf trug `attempts: 3`, der Job trägt heute `1`."""
    html = render.run_attrs_page_v5(
        slug="EngineCI", lauf=_lauf(snapshot=json.dumps(_snapshot(attempts=3))),
        job_spec=_snapshot(attempts=1), now=NOW)
    zeile = [z for z in html.split("attr-row") if ">attempts<" in z]
    assert zeile, "attempts fehlt in der Tabelle"
    assert ">3<" in zeile[0]
    assert ">run<" in zeile[0], "abweichender Wert muss als `run` ausgewiesen sein"


def test_an_unchanged_value_stays_inherited():
    html = render.run_attrs_page_v5(slug="EngineCI", lauf=_lauf(),
                                    job_spec=_snapshot(), now=NOW)
    zeile = [z for z in html.split("attr-row") if ">attempts<" in z]
    assert zeile and ">job<" in zeile[0]


def test_runtime_values_come_from_the_journal_row():
    """Die dritte Schicht: was **während** des Laufs entstand. Sie stand
    abgelegt und nirgends — heute führte der Weg dahin über das Journal-JSONL
    und `ps`."""
    html = render.run_attrs_page_v5(slug="EngineCI", lauf=_lauf(),
                                    job_spec=_snapshot(), now=NOW)
    for feld in ("host", "worker", "exit_code", "commit_sha", "branch",
                 "output_ref", "run_id"):
        assert feld in html, feld
    assert "sarasate" in html and "a1b2c3d4e5" in html


def test_the_page_says_what_the_source_column_cannot_know():
    """**Der Vorbehalt steht auf der Seite, nicht in einer Fußnote im Code.**

    Der Snapshot friert beim Archivieren ein, nicht beim Start — und nach
    Regel A2 wächst der Abstand beliebig. Ein `run` kann deshalb auch heißen:
    der Job hat sich danach geändert. Eine Spalte, die das verschweigt,
    behauptet mehr, als die Ablage hergibt."""
    html = render.run_attrs_page_v5(slug="EngineCI", lauf=_lauf(),
                                    job_spec=_snapshot(), now=NOW)
    assert "archiv" in html.lower()


def test_the_view_is_a_still_image_not_bus_bound():
    """Standbild, nicht Bus-gebunden: ob die Ansicht live mitwächst, hängt an
    Welle 4 und soll dort nicht vorweggenommen werden.

    Gemeint ist die **Attribut-Region**, nicht die Seite: der gemeinsame
    Header trägt seit jeher `data-bus="feedstatus"`, und das ist die Hülle
    und kein Bestandteil dieses Screens (FE §1)."""
    html = render.run_attrs_page_v5(slug="EngineCI", lauf=_lauf(),
                                    job_spec=_snapshot(), now=NOW)
    region = html.split('class="attrs-head"', 1)[-1]
    assert "data-bus" not in region


def test_the_way_back_leads_to_the_job():
    html = render.run_attrs_page_v5(slug="EngineCI", lauf=_lauf(),
                                    job_spec=_snapshot(), now=NOW)
    assert f'/-/jobs/{job_uid("EngineCI")}' in html


# ── Der Weg dorthin: aus der Lauf-Liste ────────────────────────────────────


def test_an_archived_run_offers_a_way_to_its_attributes():
    """FE §5.3 führt jeden Lauf als Zeile — die Attribut-Ansicht **je Lauf**
    gehört dorthin und nicht in einen weiteren Screen."""
    zeile = render._run_zeile({"id": 7, "run_id": "EngineCI:3", "src": "S",
                               "status": "complete", "sort_at": NOW},
                              basis=f"/-/jobs/{job_uid('EngineCI')}")
    assert f'/-/jobs/{job_uid("EngineCI")}/runs/7/attrs' in zeile


def test_a_run_still_in_the_slot_offers_its_own_way():
    """**Das Ticket ist eingelöst, und der Test dreht sich mit** (#182).

    Hier stand die Gegenrichtung: *„Kein toter Knopf. Ein Lauf im Slot hat
    keine Journal-Zeile und damit keinen Snapshot — die Ansicht gäbe es erst ab
    der Archivierung. Das ist eine Lücke der Ablage, nicht der Anzeige, und sie
    ist als eigenes Ticket festgehalten."* Dieses Ticket ist `#182`.

    **Die Diagnose war zur Hälfte falsch, und die falsche Hälfte hat die Lücke
    ein halbes Release länger offengehalten.** Richtig war: keine Journal-Zeile.
    Falsch war der Schluss *„und damit keinen Snapshot"* — er stand in
    `jobs.run_snapshot`, seit `#129` und seit `#164` verlässlich genullt. Die
    Ansicht las nur eine der beiden Quellen, und der Kommentar hat das als
    Eigenschaft der Ablage beschrieben statt als Eigenschaft des Lesers.

    Der Weg unterscheidet sich weiterhin, und das bleibt richtig: über
    `slot/<quelle>/<job_id>`, weil dieselbe Job-ID auf beiden Seiten einen
    anderen Job meint.
    """
    zeile = render._run_zeile({"job_id": "abc", "run_id": "EngineCI:4",
                               # **`C`, nicht `local`** (#193, `v0.8.14`): den
                               # Wert `local` trägt keine Zeile dieses Systems,
                               # und der gerenderte Link war deshalb im Betrieb
                               # ein 404 — an genau dem Weg, den dieser Test
                               # bestätigt hat.
                               "src": "C", "in_slot": True, "status": "running"},
                              basis=f"/-/jobs/{job_uid('EngineCI')}")
    assert "/slot/client/abc/attrs" in zeile, zeile
    assert "/runs/" not in zeile, (
        "der Slot-Lauf wird über eine Journal-ID adressiert, die er nicht hat")


# ── Die Route ──────────────────────────────────────────────────────────────


class _FakeClient:
    def __init__(self, status: dict, *, entry: dict | None = None) -> None:
        self._status = status
        self._entry = entry or {}

    def status(self) -> dict:
        return self._status

    def schedules(self):
        return []

    def journal(self, **_):
        return []

    def journal_entry(self, jid: int):
        return self._entry

    def jobs(self, **_):
        return []


@pytest.fixture
def app_with(team_repo: Path):
    def _make(status: dict, *, entry: dict | None = None):
        return create_app(roles.resolve({"controller"}),
                          controller_client=_FakeClient(status, entry=entry))
    return _make


def _md(team_repo: Path, slug="EngineCI"):
    """Eine entdeckte Job-MD — ohne sie löst `job_uid` auf keinen Slug auf."""
    p = team_repo / "vault" / "case" / "ci" / f"{slug}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f'---\nslug: {slug}\nschedule: "0 * * * *"\njob: "pytest -q"\n---\n',
                 encoding="utf-8")
    from bibi.daemon import job_db
    conn = job_db.connect()
    try:
        job_db.rescan(conn, vault_root=team_repo / "vault" / "case")
    finally:
        conn.close()


def test_the_route_answers_for_a_run_of_the_scheduler(app_with, team_repo: Path):
    _md(team_repo)
    app = app_with({"roles": ["scheduler", "connect"]}, entry=_lauf())
    with TestClient(app) as c:
        r = c.get(f"/-/jobs/{job_uid('EngineCI')}/runs/7/attrs")
        assert r.status_code == 200
        assert "sarasate" in r.text


def test_an_unknown_run_is_a_404(app_with, team_repo: Path):
    _md(team_repo)
    app = app_with({"roles": ["scheduler", "connect"]}, entry={})
    with TestClient(app) as c:
        assert c.get(f"/-/jobs/{job_uid('EngineCI')}/runs/999/attrs").status_code == 404
