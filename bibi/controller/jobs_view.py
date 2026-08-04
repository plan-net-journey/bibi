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
    return bool(eintrag.get("at"))


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


@dataclass
class RunGroup:
    """Eine Quelle des Job-Details: ihr Slot und die Läufe darunter.

    Es gibt bis zu zwei — ``SCHEDULER`` und ``LOCAL``. Der Slot steht in der
    Kopfzeile, unmittelbar über der Liste, in die sein Inhalt wandert: wird ein
    Lauf terminal, rutscht er aus der Kopfzeile in die erste Zeile darunter.
    Die Bewegung bleibt sichtbar, ohne dass eine Sonderzeile in der Tabelle
    steht.
    """

    quelle: str
    host: str | None
    slot: dict
    aktionen: frozenset
    #: Der Zustand des Slots, aus der Zeile gelesen statt geraten. ``None``
    #: heisst „diese Seite hat keinen Platz" — nicht ``pending``.
    slot_status: str | None = None
    runs: list = field(default_factory=list)
    gesamt: int = 0


def build_groups(*, scheduler_slot: dict | None, local_slot: dict | None,
                 scheduler_runs: list, local_runs: list,
                 scheduler_host: str | None = None, local_host: str | None = None,
                 scheduler_total: int | None = None,
                 local_total: int | None = None) -> list[RunGroup]:
    """Die Quell-Gruppen des Job-Details (FE-Spezifikation §5.1).

    **Eine Gruppe fehlt genau dann, wenn es dort keinen Slot gibt** — nicht,
    wenn er leer ist. Das ist der Unterschied zwischen *kein Platz* und
    *freier Platz*, und er ersetzt das ausgegraute Control: eine Seite, die den
    Job gar nicht kennt, zeigt keine Gruppe; eine, die ihn kennt und gerade
    nichts zu tun hat (``adhoc``), zeigt ``pending`` ohne ``next``.

    Ein ``at``-Job hat auf einem Client deshalb keine ``LOCAL``-Gruppe: dort
    kann nie ein Lauf entstehen (Zustandsmodell §5).

    Die Aktionen kommen aus :func:`bibi.schedule.slot.actions` — dieselbe
    Quelle, aus der die Engine ihre Übergänge nimmt. Zwei Listen, die dasselbe
    behaupten, laufen sonst auseinander, und die Oberfläche zeigt einen Knopf,
    den der Scheduler ablehnt.
    """
    from bibi.schedule import slot as slot_mod

    aus: list[RunGroup] = []
    for quelle, zeile, runs, host, gesamt in (
        ("SCHEDULER", scheduler_slot, scheduler_runs, scheduler_host, scheduler_total),
        ("LOCAL", local_slot, local_runs, local_host, local_total),
    ):
        if zeile is None and not runs:
            continue
        if zeile is None:
            # Kennt die Seite, hat aber keinen Platz: so sieht ein Job aus, der
            # lokal nur über `bibi-ctrl run` lief — `run_pinned()` legt je Lauf
            # einen Pseudo-Job mit Zufallssuffix an, der Basis-Slug bekommt dort
            # nie eine Zeile. §5.1 lässt eine Gruppe nur weg, wenn die Seite den
            # Job *nicht kennt* („keine MD, nie gelaufen") — wer gelaufen ist,
            # ist bekannt. Ohne diesen Zweig verschwänden live sämtliche lokalen
            # Läufe (Befund bei der Abnahme, 2026-08-03).
            aus.append(RunGroup(
                quelle=quelle, host=host, slot={}, aktionen=frozenset(),
                runs=list(runs), gesamt=gesamt if gesamt is not None else len(runs)))
            continue
        # `row_status` zuerst: so heisst das Feld in den Scheduler-Zeilen aus
        # `/-/schedule`, wo `status` schlicht `None` ist. Kein Rueckfall auf
        # `pending` — ein geratener Zustand ist im Bild nicht von einem
        # gemeldeten zu unterscheiden (Befund bei der Abnahme, 2026-08-03).
        status = zeile.get("row_status") or zeile.get("status")
        try:
            aktionen = slot_mod.actions(status) if status else frozenset()
        except ValueError:
            # Ein Zustand, den das Modell nicht kennt: keine Knöpfe, aber die
            # Gruppe bleibt. Die Läufe sind echt, auch wenn der Slot unklar ist
            # — sie zu verstecken wäre der falsche Ausgang.
            aktionen = frozenset()
        aus.append(RunGroup(
            quelle=quelle, host=host, slot=zeile, aktionen=aktionen,
            slot_status=status,
            runs=list(runs), gesamt=gesamt if gesamt is not None else len(runs)))
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
