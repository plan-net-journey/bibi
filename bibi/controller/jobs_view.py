"""Die Zeilen des Jobs-Screens — reine Klassifikation, keine Ein-/Ausgabe.

Der Screen führt **Jobs**, nicht Läufe: eine Zeile je Slug, auch wenn dieser
auf beiden Seiten existiert. Das ist der Normalfall — live haben 13 von 13
aktiven Scheduler-Schedules eine lokale MD. Zwei Zeilen je Slug erzeugten 54
statt 36 und rissen bei jeder Sortierung außer der nach Slug auseinander.

Hier steht nur die Entscheidung, welche Zeile es überhaupt gibt und wo sie
hingehört. Die Daten kommen fertig herein, die Darstellung passiert woanders.
Diese Trennung ist Absicht: die Klassifikation ist der schwierige Teil und
soll ohne Datenbank, ohne HTTP und ohne Fixtures prüfbar sein.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

#: Trigger-Werte, die „ruht" bedeuten. Ein Job mit einem davon erscheint im
#: Screen **gar nicht** — außer er hat Läufe im Journal, dann steht er in
#: Segment 3. Das ist die Ablösung des früheren Versteck-Tricks über ungültige
#: `schedule:`-Syntax: wer eine MD aus dem Blick nehmen will, sagt es, statt
#: einen Parser-Fehler zu erzeugen.
RUHT = {None, "", "~", "-", "never"}

#: „wird gerufen" — die Interaktion ist vorgesehen. `on_demand` ist die
#: kanonische Form (der Parser löst `adhoc`/`ad-hoc` darauf auf), beide werden
#: hier akzeptiert, weil dieser Screen auch ungeparste Discovery-Daten sieht.
GERUFEN = {"adhoc", "ad-hoc", "on_demand"}


class Segment(Enum):
    """Die drei Bänder des Screens.

    Sie sind eine Klassifikation, keine Sortierordnung: die Sortierung wirkt
    innerhalb eines Bandes, nicht über die Bänder hinweg.
    """

    #: Hat einen erwarteten nächsten Lauf — Cron, `startup`, offener Oneshot.
    SCHEDULE = "schedule"
    #: Wird gerufen, nicht geplant. Dienste stehen hier mit, sie sind kein
    #: eigener Typ — nur Jobs, die lange laufen.
    ADHOC = "adhoc"
    #: Kommt in 1 und 2 nicht vor, hat aber Historie: gelöschte MDs, ruhende
    #: Jobs mit Läufen, abgeschlossene Oneshots.
    JOURNAL = "journal"


@dataclass
class JobRow:
    """Eine Zeile: ein Slug, zwei Seiten."""

    slug: str
    segment: Segment
    #: Beziehung zwischen den Speichern, wenn sie vom Normalfall abweicht:
    #: `new` · `modified` · `deleted` · `dropped` · `duplicate`. Der Normalfall
    #: — beide Seiten kennen ihn — trägt bewusst `None`, sonst hätte jede Zeile
    #: ein Etikett und keines fiele mehr auf.
    relation: str | None = None
    #: Zustand in der Scheduler-DB (leer, wenn der Host ihn nicht kennt).
    scheduler: dict = field(default_factory=dict)
    #: Zustand in der lokalen Job-DB.
    local: dict = field(default_factory=dict)
    #: Die MD(s), die diesen Slug beanspruchen. Mehr als eine heißt
    #: `duplicate` — dann sind die Pfade die einzige brauchbare Auskunft.
    paths: tuple[str, ...] = ()
    #: Rohwerte für Anzeige und Filter.
    spec: dict = field(default_factory=dict)
    #: Die 24H-Kennzahl. ``None``, solange sie nicht berechnet wurde — sie
    #: braucht das Journal, und das holt der Aufrufer.
    quote: "Quote | None" = None
    #: Ein Termin statt eines Rhythmus (``at:``). Steht an der **Zeile** und
    #: nicht nur im Segment, weil die Bänderung abschaltbar ist
    #: (m.rau/bibi#134): trägt die Zeile ihre Gruppe selbst — ``@`` für den
    #: Oneshot, ein ``next`` für den Rhythmus, keins von beidem für ``adhoc``
    #: —, ist die Bänderung nur noch eine Darstellungsform und darf weg, ohne
    #: dass dabei Information verlorengeht.
    oneshot: bool = False


def _trigger(eintrag: dict) -> str | None:
    """Der Trigger — unter beiden Namen, unter denen er auftritt.

    Die Discovery nennt ihn ``schedule`` (so heisst das Frontmatter-Feld),
    ``/-/schedule`` nennt ihn ``trigger``. Fuer Zeilen, die es nur beim Host
    gibt, ist die zweite Form die einzige Quelle — live abgenommen 2026-08-03.
    """
    s = eintrag.get("schedule")
    if s is None and "trigger" in eintrag:
        s = eintrag.get("trigger")
    return s.strip() if isinstance(s, str) else s


def _ist_oneshot(eintrag: dict) -> bool:
    """Ein Termin statt eines Rhythmus.

    Zwei Formen, weil zwei Quellen: die Discovery liest ``at:`` aus dem
    Frontmatter, ``/-/schedule`` meldet stattdessen ``oneshot`` (dort ist
    ``at`` schon in ``trigger`` aufgegangen). Für eine Zeile, die es nur beim
    Scheduler gibt, ist die zweite Form die einzige.
    """
    return bool(eintrag.get("at")) or bool(eintrag.get("oneshot"))


def _segment_fuer(lokal: dict | None, sched: dict | None, hat_historie: bool) -> Segment | None:
    """Wohin die Zeile gehört — oder ``None``, wenn es keine geben soll.

    Die Reihenfolge der Prüfungen ist die Regel selbst: erst „ruht" (und wird
    damit unsichtbar), dann „wird gerufen", dann „hat einen Termin". Ein
    abgeschlossener Oneshot fällt aus dem ersten Band heraus, weil sein Termin
    verbraucht ist.
    """
    quelle = lokal or sched or {}
    trigger = _trigger(quelle)

    # `active=0`: der Host fuehrt ihn nicht mehr aus. Das ist Historie, nicht
    # das, was kommt — sonst staenden in Segment 1 Jobs, die seit Tagen tot
    # sind (live: 16 von 29 Schedules).
    if lokal is None and sched is not None and not sched.get("active", 1):
        return Segment.JOURNAL if hat_historie else None

    if _ist_oneshot(quelle):
        # `status: done` heißt: der Termin ist verbraucht, ein Lauf kommt nicht
        # mehr. Dann gehört er zur Historie, nicht zu dem, was kommt.
        return Segment.JOURNAL if quelle.get("status") == "done" else Segment.SCHEDULE

    if trigger in GERUFEN:
        return Segment.ADHOC

    if trigger in RUHT:
        # Sichtbar nur über die Historie — und auch dort nur, wenn es sie gibt.
        return Segment.JOURNAL if hat_historie else None

    if lokal is None and sched is None:
        return Segment.JOURNAL if hat_historie else None

    # Alles Übrige hat einen Termin: Cron, `startup`, `autostart`.
    return Segment.SCHEDULE


def build_rows(
    *, local: list[dict], scheduler: list[dict], journal: list[dict], now: float,
    local_runs: dict[str, dict] | None = None,
) -> list[JobRow]:
    """Aus drei Quellen eine Zeile je Slug.

    ``local`` sind die entdeckten MDs, ``scheduler`` die Einträge der
    Scheduler-DB, ``journal`` die archivierten Läufe (nur zur Frage, ob es
    Historie gibt). ``local_runs`` bringt den Zustand der lokalen Job-DB je
    Slug mit.
    """
    local_runs = local_runs or {}

    # Mehrere MDs auf denselben Slug: das ist ein Fehler im Vault, kein
    # Zustand des Jobs — aber er wird genau hier sichtbar.
    nach_slug: dict[str, list[dict]] = {}
    for md in local:
        nach_slug.setdefault(md["slug"], []).append(md)

    sched_nach_slug = {s["slug"]: s for s in scheduler}
    mit_historie = {j["slug"] for j in journal}

    zeilen: list[JobRow] = []
    for slug in sorted(set(nach_slug) | set(sched_nach_slug) | mit_historie):
        mds = nach_slug.get(slug, [])
        lokal = mds[0] if mds else None
        sched = sched_nach_slug.get(slug)
        segment = _segment_fuer(lokal, sched, slug in mit_historie)
        if segment is None:
            continue

        if len(mds) > 1:
            relation = "duplicate"
        elif lokal is not None and sched is None:
            # Ohne Scheduler-Eintrag, aber mit Historie: der Host hat ihn
            # archiviert, die MD liegt noch da.
            relation = "dropped" if slug in mit_historie else "new"
        elif lokal is None and sched is not None:
            relation = "deleted"
        elif lokal is None and sched is None:
            relation = "dropped"
        elif (lokal or {}).get("git_status") in ("modified", "new"):
            # Beide Seiten kennen ihn, die lokale MD weicht ab. Woher das kommt,
            # weiss nur git — deshalb reist `git_status` aus der Discovery mit
            # (Befund m.rau, 2026-08-03: "erscheint kein Chip modified").
            relation = "modified"
        else:
            relation = None

        zeilen.append(JobRow(
            slug=slug,
            segment=segment,
            relation=relation,
            scheduler=sched or {},
            local=local_runs.get(slug, {}),
            paths=tuple(md.get("repo_path", "") for md in mds if md.get("repo_path")),
            spec=lokal or sched or {},
            oneshot=_ist_oneshot(lokal or sched or {}),
        ))
    return zeilen


@dataclass(frozen=True)
class Quote:
    """Die 24H-Kennzahl: ``complete / expected + manual = %``.

    Sie ersetzt Chart, Sparkline und Heatmap zugleich — und leistet etwas, das
    keins davon konnte: sie trennt *„da ist etwas passiert"* (Zähler unter
    Nenner) von *„da habe ich etwas gemacht"* (`+n` im Nenner). Ein Chart zeigt
    beides als Ausschlag und kann die Richtung nicht benennen.

    Dass es eine Zahl ist und kein Bild, ist der zweite Gewinn: sortierbar,
    filterbar, summierbar. Die Bandkopfzeile trägt die Summe.
    """

    complete: int
    expected: int
    manual: int

    @property
    def prozent(self) -> int | None:
        """``None``, wenn nichts erwartet und nichts gelaufen ist.

        Nicht 0 %: das hieße, der Job habe versagt. Er hatte nur nichts zu tun.
        """
        nenner = self.expected + self.manual
        if nenner == 0 and self.complete == 0:
            return None
        if nenner == 0:
            return 100
        # **Immer abgerundet.** Eine Erfolgsquote soll nie mehr behaupten als
        # erreicht wurde: 99,6 % duerfen nicht als "alles gut" erscheinen,
        # wenn ein Lauf fehlt, und dieselbe Begruendung gilt eine Stufe
        # tiefer genauso.
        #
        # Die FE-Spezifikation §4.4 rechnet in ihren Beispielen uneinheitlich
        # (71/96 = 73,96 -> "74 %", aber 2/3 = 66,67 -> "66 %"). Von den
        # beiden moeglichen Regeln ist Abrunden die ehrlichere und die
        # einzige, die ohne Sonderfall bei 100 % auskommt.
        return int(self.complete * 100 / nenner)

    def __str__(self) -> str:
        p = self.prozent
        if p is None:
            return "—"
        return f"{self.complete}/{self.expected}+{self.manual} {p}%"


def quote_24h(*, runs: list[dict], expected: int, manual: int,
              now: float | None = None) -> Quote:
    """Kennzahl der letzten 24 Stunden.

    ``runs`` sind Journal-Zeilen dieses Jobs; gezählt wird, was *erfolgreich*
    war — ein fehlgeschlagener Lauf ist kein erledigter. ``expected`` kommt aus
    dem Trigger (wie oft hätte er feuern sollen), ``manual`` sind die von Hand
    ausgelösten Starts.
    """
    def _wann(r: dict) -> float:
        # `archived_at` steht in der Datenbank, die HTTP-Antwort von
        # `/-/journal` liefert `finished_at`. Ohne diesen Rueckfall zaehlte die
        # Kennzahl ueberall 0, obwohl die Laeufe vorlagen (live 2026-08-03).
        return r.get("archived_at") or r.get("finished_at") or 0.0

    grenze = (now if now is not None else 0.0) - 86400
    aktuell = [r for r in runs if _wann(r) >= grenze] if now else runs
    fertig = sum(1 for r in aktuell if r.get("status") == "complete")
    return Quote(complete=fertig, expected=expected, manual=manual)


def erwartete_laeufe(trigger: str | None) -> int:
    """Wie oft dieser Trigger in 24 Stunden feuern sollte.

    Der Nenner der 24H-Kennzahl. Ohne Rhythmus — `adhoc`, `never`, `startup`,
    ein Oneshot — gibt es keine Erwartung; dann besteht der Nenner allein aus
    den von Hand ausgelösten Starts.

    Ein unparsbarer Ausdruck ergibt 0 statt eines Fehlers: er darf den Screen
    nicht kippen, und eine Zeile ohne Erwartung zeigt schlicht einen Strich.
    """
    if not isinstance(trigger, str) or not trigger.strip():
        return 0
    wert = trigger.strip()
    if wert in RUHT or wert in GERUFEN or wert in {"startup", "autostart", "now"}:
        return 0
    try:
        import croniter
        from datetime import datetime, timedelta
        # Eine Sekunde vor Mitternacht starten: `get_next()` ueberspringt den
        # Startzeitpunkt selbst, und `0 * * * *` verlore sonst die Feuerung um
        # 00:00 — 23 statt 24.
        start = datetime(2026, 1, 1) - timedelta(seconds=1)
        it = croniter.croniter(wert, start)
        ende = datetime(2026, 1, 2) - timedelta(seconds=1)
        n = 0
        while n < 2000:                       # Deckel gegen Sekundentakt-Ausdrücke
            if it.get_next(datetime) >= ende:
                break
            n += 1
        return n
    except Exception:  # noqa: BLE001 — ein kaputter Ausdruck ist keine Erwartung
        return 0


#: Die drei Statusgruppen. Sie decken alle elf ``models.Status`` ab und
#: überschneiden sich nicht — anders als die frühere Filtermenge
#: (`starting`/`running`/`pending`/`complete`/`failed`/`deferred`/`problem`),
#: die an zwei Stellen desselben Screens verschieden zählte.
_WAITING = {"pending", "deferred", "failed"}
_RUNNING = {"starting", "running", "awaiting"}
#: Alle terminalen außer `complete` — ein blockierter Slot, der auf START oder
#: RESET wartet.
_STOPPED = {"error", "inactive", "zombie", "killed"}


def status_gruppe(status: str | None, *, next_fire_at: float | None) -> str | None:
    """Welcher Filtergruppe ein Slot-Zustand angehört.

    ``complete`` ist der Sonderfall und der Grund, warum ``next_fire_at``
    hineingereicht wird: **ein abgeschlossener Job, der wieder feuern wird,
    wartet.** Er ist nicht „fertig" — fertig ist ein Job nie, solange sein
    Rhythmus gilt. Ohne diese Regel wäre der Normalfall eines Cron-Jobs ein
    eigener Filterwert, und die Frage „was läuft demnächst?" ließe sich gar
    nicht stellen.

    Ein ``complete`` **ohne** Termin gehört in keine Gruppe: es wartet auf
    nichts und ist auch nicht angehalten. Es erscheint nur ungefiltert.
    """
    if status in _WAITING:
        return "waiting"
    if status in _RUNNING:
        return "running"
    if status in _STOPPED:
        return "stopped"
    if status == "complete":
        return "waiting" if next_fire_at else None
    return None


def trifft_filter(row: JobRow, *, typ: list[str], status: list[str],
                  journal: list[str]) -> bool:
    """Ob eine Zeile die aktuelle Filterauswahl übersteht.

    Alle Toggles sind on/off und mehrfach wählbar — man kann `job` und `app`
    zugleich sehen. **Keine Auswahl heißt keine Einschränkung**, nicht „nichts
    anzeigen".

    Die Staffelung ist der Grund für Bänder statt einer flachen Liste: `TYPE`
    wirkt überall, `STATUS` nur auf die ersten beiden Bänder (im Journal steht
    Historie, die keinen laufenden Zustand hat), die drei Journal-Filter nur
    auf das dritte.
    """
    from bibi.schedule.models import display_kind

    if typ:
        art = display_kind(row.spec.get("payload"), row.spec.get("app_port"))
        if art not in typ:
            return False

    if status and row.segment is not Segment.JOURNAL:
        g = status_gruppe(row.scheduler.get("row_status") or row.scheduler.get("status"),
                          next_fire_at=row.scheduler.get("next_fire_at"))
        if g not in status:
            return False

    if journal and row.segment is Segment.JOURNAL:
        passt = False
        if "dropped" in journal and row.relation in ("dropped", "deleted"):
            passt = True
        if "oneshot" in journal and row.spec.get("at"):
            passt = True
        if "local" in journal and row.local:
            passt = True
        if not passt:
            return False

    return True


#: Sortierschlüssel je Spalte. ``None`` bedeutet „kein Wert" und landet immer
#: am Ende — ein Strich ist keine Zahl, und er soll nicht die erste
#: Bildschirmhöhe füllen.
def _sortwert(row: JobRow, nach: str):
    if nach == "slug":
        return row.slug.lower()
    if nach == "type":
        from bibi.schedule.models import display_kind
        return display_kind(row.spec.get("payload"), row.spec.get("app_port"))
    if nach == "status":
        return row.scheduler.get("row_status") or row.scheduler.get("status") or None
    if nach == "last":
        return row.scheduler.get("last_run_at")
    if nach == "next":
        return row.scheduler.get("next_fire_at")
    if nach == "24h":
        # Nach dem Prozentwert, nicht nach dem Text: als Zeichenkette wäre
        # "100%" kleiner als "74%".
        return row.quote.prozent if row.quote else None
    return None


def sortiere(rows: list[JobRow], *, nach: str, richtung: str = "asc") -> list[JobRow]:
    """Sortiert **innerhalb jedes Bandes**, nicht über die Bänder hinweg.

    Die Bänder sind eine Klassifikation, keine Sortierordnung; eine Sortierung
    über sie hinweg zerstörte genau die Aussage, für die es sie gibt.

    Zeilen ohne Wert stehen immer am Ende, unabhängig von der Richtung.
    """
    umgekehrt = richtung == "desc"
    reihenfolge = {Segment.SCHEDULE: 0, Segment.ADHOC: 1, Segment.JOURNAL: 2}

    aus: list[JobRow] = []
    for seg in (Segment.SCHEDULE, Segment.ADHOC, Segment.JOURNAL):
        drin = [r for r in rows if r.segment is seg]
        # Getrennt statt ueber einen zusammengesetzten Schluessel: der muesste
        # Zahlen und Zeichenketten in derselben Position vergleichen, und das
        # geht in Python nicht.
        mit = [r for r in drin if _sortwert(r, nach) is not None]
        ohne = [r for r in drin if _sortwert(r, nach) is None]
        mit.sort(key=lambda r: _sortwert(r, nach), reverse=umgekehrt)
        aus.extend(mit + ohne)
    return aus


def slug_for(job_uid_gesucht: str, kandidaten) -> str | None:
    """Der Weg von der URL zurück zum Job: ``job_uid`` → Slug.

    ``job_uid`` ist ``md5(slug)`` und damit nicht umkehrbar — gesucht wird
    deshalb vorwärts: jeden bekannten Slug hashen und vergleichen. Das ist
    kein Umweg, sondern genau die Eigenschaft, die den Schlüssel brauchbar
    macht: er ist deterministisch und ohne Absprache auf jeder Seite
    berechenbar (Zustandsmodell §6).

    **Warum nicht ``SELECT slug FROM jobs WHERE job_uid=?``:** das findet nur,
    was eine Job-Zeile hat. Ein Lauf aus der Historie, dessen MD gelöscht
    wurde (``dropped``), hat keine — und in Bestandszeilen ist die Spalte
    ohnehin ``NULL``, weil die Migration bewusst ohne Backfill lief. Eine
    Kandidatenliste aus Slugs deckt beide Fälle ab, ohne eine zweite Wahrheit
    einzuführen; wer die Kandidaten beschafft, entscheidet der Aufrufer.

    ``None``, wenn nichts passt — daraus macht der Aufrufer ein 404. Das gilt
    auch für offensichtlichen Unsinn: ein Sonderweg für kaputte Eingaben
    brächte einen zweiten Ausgang für dieselbe Antwort.
    """
    from bibi.schedule.models import job_uid as _uid

    gesehen: set[str] = set()
    for slug in kandidaten:
        if not slug or slug in gesehen:
            continue
        gesehen.add(slug)
        if _uid(slug) == job_uid_gesucht:
            return slug
    return None


#: Slot-Zustände, die **keinen** eigenen Lauf halten — je aus einem anderen
#: Grund, und das ist der Punkt: nicht eine Statusliste entscheidet, sondern die
#: Archivierungsregel (Zustandsmodell §3). ``pending`` hat noch keinen Lauf
#: (§5.1: „bekommt keine Zeile"), ``complete`` ist nach A1 bereits im Journal —
#: eine Zeile aus dem Slot wäre derselbe Lauf ein zweites Mal —, und ``done``
#: ist ein verbrauchter Slot, kein Lauf. Alles andere hält einen Lauf, den es
#: bis zu seiner Archivierung **nirgendwo sonst** gibt.
OHNE_EIGENEN_LAUF = frozenset({"pending", "complete", "done"})


def slot_run(slot: dict, *, src: str, now: float) -> dict | None:
    """Der Lauf, der im Slot steht — als Zeile der Lauf-Liste (m.rau/bibi#131).

    Die Liste führt **jeden** Lauf, auch den noch nicht archivierten. Vorher
    hing er an der Slot-Kopfzeile und die Liste führte „ausschließlich
    archivierte Läufe" — zwei Orte für dieselbe Sache, zwischen denen ein Lauf
    beim Terminalwerden hin- und herrutschte. Jetzt gibt es einen Ort, und die
    Marke sagt, dass dieser Lauf noch im Slot steht.

    ``None`` heißt „hier steht kein Lauf", nicht „hier ist nichts": ein
    ``pending``-Slot ist ein reservierter Platz und steht in der Kachel.
    """
    from bibi.daemon import job_db

    # `row_status` zuerst — in der Scheduler-Antwort heißt das Feld so, und
    # `status` ist dort `None` (Befund bei der Abnahme, 2026-08-03).
    status = slot.get("row_status") or slot.get("status")
    if not status or status in OHNE_EIGENEN_LAUF:
        return None
    started = slot.get("started_at")
    if started is None:
        # Ein Zustand allein macht keinen Lauf. `RESET` räumt `started_at`
        # ausdrücklich (`report_status()`), und ohne diese Prüfung erschiene
        # danach eine Zeile für einen Lauf, den es nie gab.
        return None
    finished = slot.get("finished_at")
    return {
        "run_id": job_db.run_id_for(slot.get("slug") or "", slot.get("id") or "",
                                    slot.get("fire") or 0),
        # Der Bezug zwischen Kachel und Zeile: die Kachel gehört zu der Zeile,
        # die ihre Marke trägt (§5.1).
        "in_slot": True,
        "src": src,
        "job_id": slot.get("id"),
        "slug": slot.get("slug"),
        "status": status,
        "reason": slot.get("reason"),
        "started_at": started,
        "finished_at": finished,
        # Wonach die Liste sortiert und gruppiert. `finished_at` fehlt, solange
        # der Lauf läuft — ohne diesen Rückfall fiele gerade der eine Lauf aus
        # den Tagesgruppen, den man sucht (`by_day()` überspringt, was keinen
        # Zeitstempel hat). Er steht damit oben, weil er der jüngste ist, und
        # nicht durch eine Sonderregel.
        "sort_at": finished if finished is not None else started,
        "exit_code": slot.get("exit_code"),
        # Gegen das Ende gemessen, wo es eines gibt, sonst gegen jetzt. Ein
        # blockierter Lauf steht unter A2 tagelang im Slot, und seine Laufzeit
        # darf dabei nicht mitwachsen — genau der Fehler, den `exec_runtime`
        # beim Scheduler macht (`6d 1h` für einen Drei-Sekunden-Lauf,
        # m.rau/bibi#123).
        "exec_runtime": (finished if finished is not None else now) - started,
        "commit_sha": slot.get("commit_sha"),
        "host": slot.get("host"),
        "worker": slot.get("worker"),
        "output_ref": slot.get("output_ref"),
    }


@dataclass
class Tile:
    """Eine Slot-Kachel: was ich auf dieser Seite tun kann (FE §5.1.1).

    Zustand und die drei Verben, sonst nichts. Was *geschehen* ist, steht
    unten in der Liste — die Trennung nach Aufgabe ist der Grund, warum es
    für den Output nur noch einen Ort gibt.
    """

    quelle: str
    host: str | None
    slot: dict
    #: Aus der Zeile gelesen, nicht geraten. ``None`` heißt „diese Seite meldet
    #: keinen Zustand" und wird nicht zu ``pending`` ergänzt.
    status: str | None
    aktionen: frozenset
    #: Ende des jüngsten Laufs **dieser Seite**, ``None`` wenn dort noch keiner
    #: lief. Der Client-Slot zeigt damit zurück, wo der Scheduler-Slot nach vorn
    #: zeigt (``next_fire_at``) — siehe ``render._slot_kachel()``.
    last_at: float | None = None
    #: Port der App, falls es eine ist (m.rau/bibi#104). Gehört an die Kachel
    #: und nicht an die Seite, weil erst ``host`` daneben eine Adresse ergibt:
    #: derselbe Port meint auf zwei Knoten zwei verschiedene Dienste. Der
    #: frühere Weg über ``config.public_host()`` konnte das nicht — er kennt
    #: nur den Knoten, der die Seite *rendert*, nicht den, der die App *fährt*.
    app_port: int | None = None
    #: Warum diese Seite gerade nichts anbietet, oder ``None`` (m.rau/bibi#146).
    #: Gesetzt heißt: **Kachel zeigen, aber gesperrt** — ein Element, das fehlt,
    #: ist von einem, das es nie gab, nicht zu unterscheiden. Der Text wird
    #: angezeigt und nicht nur als ``title`` gehängt: auf einem Touch-Gerät gibt
    #: es kein Hover.
    disabled: str | None = None


@dataclass
class RunList:
    """Der untere Teil des Screens: **eine** Liste über beide Quellen.

    ``counts`` ist der Ersatz für die frühere Faltung. Die entstand gegen ein
    echtes Problem — ``gmail-transfer`` hat 1064 Scheduler-Läufe gegen wenige
    lokale, der erste lokale stünde unerreichbar weit unten. Die Zählung löst
    dasselbe und leistet mehr: sie zeigt, *dass* es lokale gibt, ohne dass man
    eine Gruppe finden und aufklappen muss.
    """

    tiles: list[Tile]
    runs: list[dict]
    #: Läufe je Herkunft (``S``/``C``), Gesamtzahl — nicht der geladene
    #: Ausschnitt.
    counts: dict[str, int]


def build_run_list(*, scheduler_slot: dict | None, client_slot: dict | None,
                   scheduler_runs: list, client_runs: list, now: float,
                   scheduler_host: str | None = None, client_host: str | None = None,
                   scheduler_total: int | None = None,
                   client_total: int | None = None,
                   oneshot: bool = False,
                   scheduler_offline: bool = False,
                   client_slug: str | None = None,
                   app_port: int | None = None) -> RunList:
    """Kacheln und die eine Lauf-Liste (FE §5.1–§5.3, m.rau/bibi#131).

    **Oben, die Kacheln: was ich tun kann. Unten, die Liste: was geschehen
    ist.** Die frühere Fassung hatte je Quelle eine faltbare Gruppe, den Slot in
    ihrer Kopfzeile und den laufenden Output daran hängend — das ergab zwei
    Orte für dieselbe Sache, zwischen denen ein Lauf beim Terminalwerden hin-
    und herrutschte.

    Eine **Kachel** fehlt genau dann, wenn es dort keinen Slot gibt — nicht,
    wenn er leer ist. Eine Seite, die nur Läufe hat (``bibi-ctrl run`` legt
    Pseudo-Jobs mit Zufallssuffix an, der Basis-Slug bekommt dort nie eine
    Zeile), bekommt deshalb keine Kachel: es gibt keinen Platz zu bedienen.
    Ihre Läufe gehen trotzdem nicht verloren — sie stehen in derselben Liste,
    und die Zählung nennt sie.

    **Ein Oneshot hat keinen lokalen Platz** (FE §5.1.1, Zustandsmodell §5): er
    läuft nicht lokal, sein Termin gehört dem Scheduler. Verhindert wird das
    nirgends (``run_pinned()`` kennt keinen Oneshot-Ausschluss, Zustandsmodell
    §8 Nr. 12 ist offen), die Kachel entstünde also, sobald jemand ``bibi-ctrl
    run`` auf einem ``at``-Slug aufruft — und böte einen Platz an, den es nicht
    gibt.

    **Sie fehlt deshalb nicht, sie ist gesperrt** (m.rau/bibi#146, dreht die
    Entscheidung vom 2026-08-05 um): ein Element, das fehlt, ist von einem, das
    es nie gab, nicht zu unterscheiden — wer es sucht, prüft zuerst, ob er im
    falschen Screen ist. Dasselbe gilt für ``scheduler_offline``: der Zustand
    des Hosts ist dann *unbekannt*, nicht *leer*. Die **Läufe** bleiben in
    beiden Fällen in der Liste; es fehlt der Platz zum Bedienen, nicht die
    Historie.

    **``client_slug`` ist der Platz ohne Zeile** (m.rau/bibi#87). Auf einem
    reinen Client gibt es zu einem Job oft gar keine ``jobs``-Zeile: Basis-
    Zeilen legt nur ``job_db.rescan()`` an, und der Rescanner hängt an der
    ``scheduler``-Rolle. Ohne Zeile fehlte die Kachel — und damit der einzige
    Weg, den Job hier zu starten, obwohl seine MD im Vault liegt und
    ``bibi-ctrl run`` ihn ausführt. **Genau der Fall, für den ``""`` als
    Zustand modelliert ist:** „ein Client-Job kann echt noch nie gelaufen
    sein", START nutzbar, KILL/RESET tot.

    Der Slug ist dabei die **Kennung** der Kachel, nicht bloß eine Beschriftung:
    alle vier Client-Verben gehen über ``/-/run`` bzw. ``/-/run/live/*`` und
    nehmen ohnehin einen Slug. Die Job-ID war dort immer nur ein Umweg, um an
    ihn zu kommen.
    """
    from bibi.schedule import slot as slot_mod

    tiles: list[Tile] = []
    runs: list[dict] = []
    counts: dict[str, int] = {}
    # **Client zuerst** (m.rau/bibi#147): links steht, was dieser Knoten selbst
    # weiß, rechts, was der Scheduler sagt. FE §2 führte die Regel bisher nur
    # für den Header; sie gilt ab jetzt überall, und die Kacheln erben ihre
    # Reihenfolge aus dieser Schleife.
    for quelle, src, zeile, journal, host, gesamt in (
        ("CLIENT", "C", client_slot, client_runs, client_host, client_total),
        ("SCHEDULER", "S", scheduler_slot, scheduler_runs, scheduler_host, scheduler_total),
    ):
        gesperrt: str | None = None
        if oneshot and quelle == "CLIENT":
            # Kein lokaler Platz — aber die Kachel bleibt und sagt es
            # (m.rau/bibi#146). Die Läufe unten kommen ohnehin mit.
            zeile, gesperrt = None, "oneshots never run locally"
        elif scheduler_offline and quelle == "SCHEDULER":
            # Der Host ist weg: sein Zustand ist unbekannt, nicht leer, und
            # keins seiner Verben käme an. Die Kachel bleibt trotzdem stehen —
            # dieselbe Entscheidung wie beim gedimmten Header (FE §2).
            zeile, gesperrt = None, "scheduler offline"
        if gesperrt is not None:
            tiles.append(Tile(quelle=quelle, host=host, slot={}, status=None,
                              aktionen=frozenset(), disabled=gesperrt,
                              app_port=app_port))
        elif zeile is None and quelle == "CLIENT" and client_slug:
            # **Ein Platz ohne Zeile** (m.rau/bibi#87). Kein Zustand zu lesen,
            # kein Lauf zu beenden — aber startbar, und das ist der Sinn der
            # Kachel. Der Slug steht als `id`, weil der Client-Slot slug-basiert
            # bedient wird; er ist damit nicht bloss Beschriftung, sondern die
            # Kennung, an der der Knopf haengt.
            #
            # **Hinter `gesperrt`, nicht davor**: ein Oneshot hat auch dann
            # keinen lokalen Platz, wenn seine MD hier liegt (FE §5.1.1) — sonst
            # entstuenden zwei Kacheln, eine gesperrte und eine startbare.
            tiles.append(Tile(quelle=quelle, host=host,
                              slot={"id": client_slug}, status="",
                              aktionen=slot_mod.actions(""),
                              app_port=app_port))
        status = None
        if zeile is not None:
            # `row_status` zuerst: so heißt das Feld in den Scheduler-Zeilen aus
            # `/-/schedule`, wo `status` schlicht `None` ist.
            status = zeile.get("row_status") or zeile.get("status")
            try:
                aktionen = slot_mod.actions(status) if status else frozenset()
            except ValueError:
                # Ein Zustand, den das Modell nicht kennt: keine Knöpfe, aber
                # die Kachel bleibt — sie sagt dann wenigstens, was dort steht.
                aktionen = frozenset()
            # Der jüngste Lauf dieser Seite. Aus dem Journal, nicht aus der
            # Slot-Zeile: die ist bei `pending` ausgeräumt (`report_status()`
            # nullt `finished_at`), und genau dort wird die Angabe gebraucht.
            enden = [r.get("finished_at") for r in journal
                     if r.get("finished_at") is not None]
            tiles.append(Tile(quelle=quelle, host=host, slot=zeile,
                              status=status, aktionen=aktionen,
                              last_at=max(enden) if enden else None,
                              app_port=app_port))
        eigene = [{**r, "src": src, "sort_at": r.get("finished_at")} for r in journal]
        im_slot = slot_run(zeile, src=src, now=now) if zeile is not None else None
        if im_slot is not None:
            # Ein Lauf kann in **beiden** Speichern stehen — nach einem KILL ist
            # das der Normalfall: der Lauf ist archiviert, der Slot trägt seinen
            # Zustand weiter, bis jemand START oder RESET drückt. Dann gibt es
            # trotzdem nur **eine** Zeile, und sie bekommt die Marke: der Lauf
            # ist derselbe, egal wo er liegt. Der archivierte Eintrag bleibt
            # dabei der Träger — nur er hat die Journal-ID, über die sein Output
            # erreichbar ist.
            doppelt = next((r for r in eigene if r.get("run_id") == im_slot["run_id"]), None)
            if doppelt is not None:
                doppelt["in_slot"] = True
            else:
                eigene.insert(0, im_slot)
                if gesamt is not None:
                    gesamt += 1
        runs += eigene
        counts[src] = gesamt if gesamt is not None else len(eigene)
    # Fix nach der Lauf-Zeit sortiert, absteigend (§5.3). Nicht nach
    # `archived_at`: unter A2 laufen beide beliebig weit auseinander, und die
    # Tagestrennlinien sollen sagen, *wann etwas lief*, nicht wann jemand
    # aufgeräumt hat.
    runs.sort(key=lambda r: r.get("sort_at") or 0, reverse=True)
    return RunList(tiles=tiles, runs=runs, counts=counts)


#: Wie viele neue Einträge ein ``LOAD MORE`` mindestens bringen soll, und wie
#: viele leere Tage er dafür höchstens überspringt (FE §5.3).
MEHR_EINTRAEGE = 10
MEHR_LEERE_TAGE = 30

#: Läufe je Quelle, die eine Abfrage höchstens holt. **Das ist die echte
#: Grenze der Lauf-Liste** — ein zeitbasiertes Pruning gibt es nicht (das
#: einzige ``DELETE FROM journal`` löscht eine Zeile per ID), und der
#: gestrichene Archive-Screen behauptete mit ``pruned after 3 months`` eine
#: Schranke, die es nie gab. Diese hier gibt es.
RUN_LIMIT = 500


def naechstes_fenster(runs: list, *, aktuell: int, jetzt: float,
                      ts_key: str = "sort_at") -> int | None:
    """Das nächste Zeitfenster in Tagen — oder ``None``, wenn nichts folgt.

    **Der Knopf erweitert um eine Menge, nicht um einen Tag.** Er nimmt Tage
    dazu, bis :data:`MEHR_EINTRAEGE` neue zusammenkommen oder
    :data:`MEHR_LEERE_TAGE` am Stück nichts brachten. Ohne das erste verspricht
    er „mehr" und liefert an einem ruhigen Tag eine einzige Zeile; ohne das
    zweite läuft er bei einem Job mit langer Pause bis zum ältesten Lauf durch
    und lädt dann alles auf einmal.
    """
    aelter = sorted(
        (jetzt - (r.get(ts_key) or 0)) / 86_400
        for r in runs if (r.get(ts_key) or 0) and (jetzt - r[ts_key]) / 86_400 > aktuell)
    if not aelter:
        # Kein Knopf. Einer, der nichts mehr lädt, ist schlimmer als keiner —
        # er sieht aus wie ein Weg.
        return None
    neu = 0
    fenster = aktuell
    for abstand in aelter:
        if abstand - fenster > MEHR_LEERE_TAGE:
            # Die Lücke ist zu groß: hier ist Schluss, auch wenn noch etwas
            # käme. Der nächste Klick geht weiter.
            return aktuell + MEHR_LEERE_TAGE
        neu += 1
        fenster = abstand
        if neu >= MEHR_EINTRAEGE:
            break
    # Aufgerundet, damit der Tag des letzten Treffers vollständig hineinfällt.
    return max(aktuell + 1, int(fenster) + 1)


def im_fenster(runs: list, *, tage: int, jetzt: float,
               ts_key: str = "sort_at") -> list[dict]:
    """Die Läufe der letzten ``tage`` — geschnitten nach der **Lauf**-Zeit.

    Dieselbe Größe, nach der auch sortiert und gruppiert wird. Nach
    ``archived_at`` geschnitten fiele ein Lauf aus dem Fenster, weil ihn jemand
    spät abgeräumt hat, und die Reichweiten-Angabe stimmte nicht mehr mit den
    Tagestrennlinien überein.

    **Der Lauf im Slot bleibt immer drin**, egal wie alt er ist. Er ist kein
    Historieneintrag, sondern der aktuelle Zustand: unter A2 steht ein
    blockierter Lauf, bis ein Mensch ihn abräumt, und das kann Monate dauern.
    Live gefunden — ``Runner`` trug ``killed · by_user`` in der Kachel, während
    die Liste den Lauf nicht führte (31 Tage alt, Fenster 30). Die Kachel zeigte
    damit auf eine Zeile, die es nicht gab.
    """
    grenze = jetzt - tage * 86_400
    return [r for r in runs
            if r.get("in_slot") is True or (r.get(ts_key) or 0) >= grenze]


def gefiltert(runs: list, *, status: list[str], src: list[str]) -> list[dict]:
    """Zustands- und Herkunftsfilter (FE §5.3).

    Beide sind on/off und mehrfach wählbar; **kein Filter heißt alle**, nicht
    keine. Der Herkunftsfilter ist der Ersatz für die frühere Faltung — er
    leistet dasselbe und zeigt zusätzlich, *dass* es die andere Seite gibt.
    """
    aus = runs
    if status:
        aus = [r for r in aus if (r.get("status") or "") in status]
    if src:
        aus = [r for r in aus if r.get("src") in src]
    return aus


def by_day(runs: list, *, ts_key: str = "finished_at") -> list[tuple[str, list]]:
    """Einträge nach Tag gruppieren, jüngster Tag zuerst (FE-Spezifikation §5.3).

    ``ts_key`` benennt das Zeitfeld — die Lauf-Liste gruppiert nach
    ``finished_at``, der Feed nach ``last_changed``. Dasselbe Idiom, ein Ort.

    **Sortiert wird nach ``finished_at``, nicht nach ``archived_at``.** Unter
    der Archivierungsregel A2 laufen beide beliebig weit auseinander: ein
    terminaler Lauf bleibt im Slot stehen, bis ihn jemand abräumt. Nach dem
    Aufräum-Zeitpunkt sortiert, erschiene ein tagealter Lauf ganz oben unter
    dem heutigen Datum — die Tagestrennlinien sollen aber sagen, *wann etwas
    lief*, nicht wann jemand aufgeräumt hat.

    Tagesgruppen statt einer gleichförmigen Endlosliste, weil sie einen
    greifbaren Anker geben („was lief gestern?").
    """
    import datetime as _dt

    nach_tag: dict[str, list] = {}
    for r in sorted(runs, key=lambda x: x.get(ts_key) or 0, reverse=True):
        ts = r.get(ts_key)
        if ts is None:
            continue
        tag = _dt.datetime.fromtimestamp(ts).strftime("%d/%m/%Y")
        nach_tag.setdefault(tag, []).append(r)
    return sorted(nach_tag.items(),
                  key=lambda kv: kv[1][0].get(ts_key) or 0, reverse=True)
