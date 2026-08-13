"""Filter und Sortierung des Jobs-Screens (FE-Spezifikation §4.5/§4.6).

Zwei Eigenschaften, die die frühere Filtermenge nicht hatte:

**Die Gruppen überschneiden sich nicht und decken alle elf Zustände ab.** Die
alte Menge (`starting`/`running`/`pending`/`complete`/`failed`/`deferred`/
`problem`) zählte an zwei Stellen desselben Screens verschieden — dieselbe
Zeile konnte in zwei Filtern erscheinen.

**Die Staffelung ist der Grund für Bänder.** `TYPE` und `STATUS` wirken
überall, die drei Journal-Filter nur im dritten Band. Eine gestaffelte
Filtermenge braucht einen Ort je Staffel.
"""

from __future__ import annotations

import pytest

from bibi.controller.jobs_view import (
    Segment,
    build_rows,
    sortiere,
    status_gruppe,
    trifft_filter,
)

NOW = 1_000_000.0


def _row(slug="x", *, segment=Segment.SCHEDULE, sched=None, spec=None, **kw):
    from bibi.controller.jobs_view import JobRow
    return JobRow(slug=slug, segment=segment, scheduler=sched or {},
                  spec=spec or {"payload": "echo hi"}, **kw)


# ── Die drei Statusgruppen ──────────────────────────────────────────────────


def test_waiting_covers_what_is_going_to_run():
    for z in ("pending", "deferred", "failed"):
        assert status_gruppe(z, next_fire_at=None) == "waiting", z


def test_complete_with_a_next_date_is_waiting():
    """**Der Unterschied zur alten Menge.** Ein abgeschlossener Job, der wieder
    feuern wird, wartet — er ist nicht „fertig". Ohne diese Regel wäre der
    Normalfall eines Cron-Jobs ein eigener Filterwert, und die Frage „was
    läuft demnächst?" ließe sich nicht stellen."""
    assert status_gruppe("complete", next_fire_at=NOW + 60) == "waiting"


def test_complete_without_a_next_date_is_nothing():
    """Ohne Termin ist ein `complete` weder wartend noch angehalten — es
    gehört in keine der drei Gruppen und erscheint nur ungefiltert."""
    assert status_gruppe("complete", next_fire_at=None) is None


def test_running_covers_what_is_busy():
    for z in ("starting", "running", "awaiting"):
        assert status_gruppe(z, next_fire_at=None) == "running", z


def test_stopped_is_every_terminal_state_except_complete():
    for z in ("error", "inactive", "zombie", "killed"):
        assert status_gruppe(z, next_fire_at=None) == "stopped", z


def test_the_groups_cover_all_eleven_states_without_overlap():
    """Alle elf `models.Status` sind erfasst, keiner doppelt — das ist die
    Eigenschaft, an der die alte Filtermenge scheiterte."""
    from bibi.schedule.models import Status
    gesehen = {}
    for s in Status:
        g = status_gruppe(s.value, next_fire_at=NOW + 60)
        gesehen.setdefault(g, []).append(s.value)
    assert set(gesehen) == {"waiting", "running", "stopped"}
    assert sum(len(v) for v in gesehen.values()) == 11


# ── Filterwirkung ───────────────────────────────────────────────────────────


def test_no_filter_means_no_restriction():
    zeilen = [_row("a"), _row("b")]
    assert all(trifft_filter(z, typ=[], status=[]) for z in zeilen)


def test_type_filters_accept_several_values_at_once():
    """`job` und `app` zugleich sichtbar — die Toggles sind on/off, nicht
    exklusiv."""
    job = _row("j", spec={"payload": "echo hi"})
    app = _row("a", spec={"payload": "echo hi", "app_port": 9100})
    claude = _row("c", spec={"payload": "claude: hallo"})
    treffer = [z.slug for z in (job, app, claude)
               if trifft_filter(z, typ=["job", "app"], status=[])]
    assert treffer == ["j", "a"]


def test_status_filter_uses_the_groups():
    laeuft = _row("l", sched={"row_status": "running"})
    haengt = _row("h", sched={"row_status": "error"})
    assert trifft_filter(laeuft, typ=[], status=["running"])
    assert not trifft_filter(haengt, typ=[], status=["running"])


def test_status_filter_does_not_touch_the_journal_band():
    """`STATUS` wirkt auf Band 1 und 2 — im Journal steht Historie, und die
    hat keinen laufenden Zustand, den man filtern könnte."""
    alt = _row("alt", segment=Segment.JOURNAL, sched={"row_status": "complete"})
    assert trifft_filter(alt, typ=[], status=["running"])


# ── Sortierung ──────────────────────────────────────────────────────────────


def test_sorting_happens_inside_each_band():
    """Die Bänder sind eine Klassifikation, keine Sortierordnung — eine
    Sortierung über sie hinweg zerstörte die Aussage der Gruppierung."""
    zeilen = [_row("b"), _row("a", segment=Segment.ADHOC), _row("c")]
    sortiert = sortiere(zeilen, nach="slug", richtung="asc")
    assert [z.slug for z in sortiert] == ["b", "c", "a"]


def test_sorting_by_slug_both_ways():
    zeilen = [_row("b"), _row("a"), _row("c")]
    assert [z.slug for z in sortiere(zeilen, nach="slug", richtung="asc")] == ["a", "b", "c"]
    assert [z.slug for z in sortiere(zeilen, nach="slug", richtung="desc")] == ["c", "b", "a"]


def test_sorting_by_percent_uses_the_number_not_the_text():
    """`24H` sortiert nach dem Prozentwert. Als Text wäre „100%" kleiner als
    „74%" — das ist der Grund, warum die Kennzahl eine Zahl ist und kein
    Bild."""
    from bibi.controller.jobs_view import Quote
    hoch = _row("hoch", quote=Quote(complete=96, expected=96, manual=0))
    tief = _row("tief", quote=Quote(complete=71, expected=96, manual=0))
    assert [z.slug for z in sortiere([tief, hoch], nach="24h", richtung="desc")] \
        == ["hoch", "tief"]


def test_rows_without_a_value_sort_last_either_way():
    """Ein Strich ist kein Wert. Er gehört ans Ende — auch bei aufsteigender
    Sortierung, sonst füllt er die erste Bildschirmhöhe."""
    from bibi.controller.jobs_view import Quote
    mit = _row("mit", quote=Quote(complete=1, expected=1, manual=0))
    ohne = _row("ohne", quote=None)
    for richtung in ("asc", "desc"):
        assert [z.slug for z in sortiere([ohne, mit], nach="24h", richtung=richtung)][-1] \
            == "ohne", richtung


# ── #31: ein Job ohne Zustand wartet ───────────────────────────────────────


def test_a_job_without_a_state_counts_as_waiting():
    """**Konsistent zu `complete` mit gesetztem `next`** (Vorgabe m.rau).

    Ein Job, den der Scheduler noch nie ausgeführt hat, trägt gar keinen
    Status. Er fiel damit durch jeden `STATUS`-Filter — auch durch `waiting`,
    obwohl genau das seine Lage beschreibt: er hat noch nichts getan und wird
    es tun. Die Regel steht schon für `complete` mit Termin da; sie hier nicht
    zu ziehen, machte den häufigeren Fall zur Ausnahme."""
    assert status_gruppe(None, next_fire_at=1_000_000.0) == "waiting"


def test_a_job_without_a_state_and_without_a_date_stays_out():
    """Die Gegenprobe, und sie zieht dieselbe Grenze wie bei `complete`: ohne
    Termin wartet nichts auf etwas. Ein `adhoc`-Job, der nie lief, ist nicht
    „wartend" — er ist unbenutzt, und das ist keine Filtergruppe."""
    assert status_gruppe(None, next_fire_at=None) is None
