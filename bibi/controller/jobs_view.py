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
