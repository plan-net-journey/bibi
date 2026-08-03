"""Job Detail: der Weg von der URL zurueck zum Job (FE-Spezifikation §5).

Die URL traegt den `job_uid` (`/-/jobs/{job_uid}`), und der ist ein md5 —
nicht umkehrbar. Der Weg zurueck fuehrt deshalb ueber die bekannten Slugs:
wer in Frage kommt, wird gehasht und verglichen. Das klingt nach Umweg, ist
aber genau die Eigenschaft, die `job_uid` ueberhaupt brauchbar macht — er ist
deterministisch, also auf beiden Seiten ohne Absprache berechenbar
(Zustandsmodell §6).

Warum nicht einfach `SELECT slug FROM jobs WHERE job_uid=?`: das findet nur,
was eine Job-Zeile hat. Ein Lauf aus der Historie, dessen MD geloescht wurde
(`dropped`), hat keine — und in Bestandszeilen ist `job_uid` ohnehin `NULL`,
weil die Migration bewusst ohne Backfill lief. Die Kandidatenliste aus Slugs
deckt beide Faelle ab, ohne eine zweite Wahrheit einzufuehren.

Reine Funktion, keine Datenbank, kein HTTP — dieselbe Trennung wie beim
Jobs-Screen (`jobs_view.build_rows()`).
"""

from __future__ import annotations

import pytest

from bibi.controller import jobs_view
from bibi.schedule.models import job_uid


def test_finds_the_slug_behind_a_job_uid():
    assert jobs_view.slug_for(job_uid("EngineCI"), ["daily-digest", "EngineCI"]) == "EngineCI"


def test_returns_none_when_nothing_matches():
    """Ein unbekannter `job_uid` ist ein 404, kein Absturz und kein leerer
    Screen: die URL kann aus einem Lesezeichen stammen, dessen Job es nicht
    mehr gibt."""
    assert jobs_view.slug_for(job_uid("weg"), ["EngineCI"]) is None


def test_finds_a_homeless_run_from_the_journal():
    """Der Fall, der die Datenbank-Abfrage nicht koennte: die MD ist geloescht,
    es gibt keine Job-Zeile mehr — aber die Laeufe stehen im Journal, und ihr
    Slug genuegt. Ohne das waere der Archive-Screen ein Weg ins Nichts."""
    assert jobs_view.slug_for(job_uid("Runner-Container"),
                              ["EngineCI", "Runner-Container"]) == "Runner-Container"


def test_a_pinned_run_resolves_to_its_base_job():
    """Ein lokaler Lauf traegt einen Slug mit Zufallssuffix
    (`EngineCI-46ec57c7`). Er gehoert zum Basis-Job und darf keine eigene
    Detailseite bekommen — sonst zerfiele ein Job in so viele Seiten, wie er
    lokale Laeufe hatte (live: 252 Pseudo-Slugs fuer 33 echte Jobs)."""
    kandidaten = ["EngineCI", "EngineCI-46ec57c7"]
    assert jobs_view.slug_for(job_uid("EngineCI"), kandidaten) == "EngineCI"


def test_the_first_match_wins_and_it_is_stable():
    """Zwei MDs mit demselben Slug ergeben denselben `job_uid` — das ist die
    beabsichtigte Kollision (`duplicate`, Zustandsmodell §6). Die Aufloesung
    muss davon unbeeindruckt genau einen Slug liefern, sonst haengt die
    Detailseite von der Reihenfolge der Kandidaten ab."""
    assert jobs_view.slug_for(job_uid("Runner"), ["Runner", "Runner"]) == "Runner"


def test_duplicates_in_the_candidate_list_do_not_matter():
    kandidaten = ["a", "b", "a", "EngineCI", "b"]
    assert jobs_view.slug_for(job_uid("EngineCI"), kandidaten) == "EngineCI"


@pytest.mark.parametrize("kaputt", ["", "nicht-hex", "x" * 32])
def test_a_malformed_uid_is_simply_unknown(kaputt):
    """Kein Sonderweg fuer Unsinn: was nicht passt, passt nicht — und der
    Aufrufer macht daraus ein 404 wie bei jedem anderen unbekannten Job."""
    assert jobs_view.slug_for(kaputt, ["EngineCI"]) is None


# ── die Route ────────────────────────────────────────────────────────────────


class _FakeClient:
    """Genügend Scheduler für einen Screen — der Rest kommt lokal."""

    def status(self) -> dict:
        return {"now": 1_000_000.0, "roles": ["scheduler"]}

    def schedules(self):
        return []

    def journal(self, **_):
        return []

    def run_journal(self, **_):
        return []

    def jobs(self, **_):
        return []


@pytest.fixture
def client(team_repo):
    from fastapi.testclient import TestClient

    from bibi.daemon import roles
    from bibi.daemon.app import create_app
    app = create_app(roles.resolve({"controller"}), controller_client=_FakeClient())
    with TestClient(app) as c:
        yield c, team_repo


def _md(root, rel: str, body: str) -> None:
    p = root / "vault" / "case" / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")


def test_the_slug_link_from_the_jobs_screen_is_not_a_dead_end(client):
    """Der Befund, der diesen Screen ausgeloest hat: der Jobs-Screen verlinkt
    jeden Slug auf `/-/jobs/{job_uid}`, und das antwortete mit 404. Ein toter
    Link ist schlimmer als eine fehlende Route — er sieht aus wie ein Weg."""
    c, root = _md_job(client)
    r = c.get(f"/-/jobs/{job_uid('EngineCI')}")
    assert r.status_code == 200
    assert "EngineCI" in r.text


def _md_job(client):
    c, root = client
    _md(root, "ci/EngineCI.md",
        '---\nslug: EngineCI\nschedule: "0 * * * *"\njob: "pytest -q"\n---\n')
    from bibi.daemon import job_db
    conn = job_db.connect()
    try:
        job_db.rescan(conn, vault_root=root / "vault" / "case")
    finally:
        conn.close()
    return c, root


def test_an_unknown_job_uid_is_a_404(client):
    c, _ = client
    assert c.get(f"/-/jobs/{job_uid('gibt-es-nicht')}").status_code == 404


def test_the_page_carries_the_shell(client):
    """App-Bar und Header stehen auf **jedem** Screen und jeder Unterseite
    (FE-Spezifikation §1) — sie sind der Rahmen, nicht Screen-Inhalt."""
    c, _ = _md_job(client)
    text = c.get(f"/-/jobs/{job_uid('EngineCI')}").text
    for teil in ("Feed", "Jobs", "Archive", "Nodes", "Live", "Log"):
        assert teil in text
    assert "CLIENT" in text and "SCHEDULER" in text
