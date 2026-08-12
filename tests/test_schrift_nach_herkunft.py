"""Die Schrift sagt, **woher** ein Wert kommt (#149).

Die Zusage im Prüfumfang der Gestaltungs-Klammer lautet wörtlich: *„Die
Trennung **committet gegen flüchtig** hält feldweise."* Gebaut wurde in
`v0.8.4` etwas anderes — *„Chrome trägt Sans, Werte tragen Monospace"* —, und
das Plan-Memo hat die beiden Sätze gleichgesetzt, ohne den Wechsel zu bemerken.

**Sie sortieren nach verschiedenen Dingen.** *Committet gegen flüchtig* fragt
nach der Herkunft eines Wertes, *Chrome gegen Wert* nach seiner Rolle im
Layout. Für Slugs und Commit-Hashes fällt beides zusammen — deshalb sah das
Ergebnis stimmig aus. Für alles, was tickt, nicht: Runtime, Status, Last und
Next sind Werte (also mono nach der gebauten Regel) und zugleich flüchtig (also
sans nach der zugesagten).

Befund m.rau, 2026-08-12: *„die folgenden Werte kommen weder aus git noch aus
dem Repository und dem Markdown. Sie kommen aus dem System. Deshalb Sans …
Umgekehrt sind diese Werte in Sans formatiert, obwohl sie aus dem Repository,
bzw. git kommen."*

**Diese Tests führen die Felder namentlich.** Der `v0.8.4`-Test prüfte
`table.jobs td` als Ganzes und war grün — er benutzte dieselbe falsche Achse
wie der Code. Ein Test, der nur *irgendeinen* Unterschied zwischen zwei
Elementen verlangt, wiederholt diesen Fehler.
"""

from __future__ import annotations

import re

from bibi.controller import render
from bibi.controller.jobs_view import build_rows

from tests import _css

NOW = 1_000_000.0

#: Woran man die beiden Familien erkennt. Die vollen Stacks stehen im
#: Stylesheet; hier genügt ihr erstes Glied, weil kein drittes existiert.
MONO, SANS = "ui-monospace", "system-ui"


def _familie(kette) -> str:
    """``mono`` / ``sans`` / ``None`` — die aufgelöste Schrift des Elements."""
    wert = _css.aufgeloest(kette, "font-family")
    if wert is None:
        return "keine"
    if MONO in wert:
        return "mono"
    if SANS in wert:
        return "sans"
    return wert


def _nur_text(fragment: str) -> str:
    """Der sichtbare Text eines Kopfes — Auszeichnung raus, Position bleibt."""
    return re.sub(r"<[^>]*>", "", fragment).strip()


def _md(slug, schedule="0 * * * *", **kw):
    return {"slug": slug, "schedule": schedule, "payload": "echo hi",
            "repo_path": f"case/x/{slug}.md", **kw}


# ── Die Jobs-Tabelle ────────────────────────────────────────────────────────


def _jobs_html() -> str:
    return render.jobs_screen(
        build_rows(
            local=[_md("EngineCI")],
            scheduler=[{"slug": "EngineCI", "status": "complete",
                        "schedule": "0 * * * *", "runtime_p90": 42.0,
                        "last_run_at": NOW - 90, "next_fire_at": NOW + 600}],
            journal=[], now=NOW,
            local_runs={"EngineCI": {"status": "error", "exec_runtime": 231.9}}),
        now=NOW)


def _spalten(html: str) -> dict[str, str]:
    """``{Spaltenbeschriftung: aufgelöste Familie}`` für die Datenzeile.

    **Die Zuordnung läuft über den Kopf, nicht über einen festen Index.** Ein
    Test, der die sechste Spalte prüft, prüft nach dem nächsten Spaltenumbau
    eine andere — genau der Bruch, den `#135` schon einmal verursacht hat, als
    eine Mindestbreite unbemerkt auf die Nachbarspalte wanderte.
    """
    # **Die zweite Kopfzeile, nicht die erste.** Darüber steht die
    # Gruppenzeile (`SCHEDULER`/`CLIENT`), darunter die Filterzeile — beide
    # tragen keine Spaltennamen, und beide würden die Zuordnung verschieben.
    thead = html.split("<tbody", 1)[0]
    zeilen_im_kopf = re.findall(r"<tr[^>]*>.*?</tr>", thead, re.S)
    spaltenzeile = next(z for z in zeilen_im_kopf
                        if "gruppen" not in z and "fltr-kopf" not in z)
    kopf = [_nur_text(k) for k in
            re.findall(r"<th[^>]*>(.*?)</th>", spaltenzeile, re.S)]
    zeile = re.search(r'<tr data-row="[^"]*">(.*?)</tr>', html, re.S).group(0)
    wurzel = _css.BODY + [("table", frozenset({"jobs"})), ("tbody", frozenset())]
    zellen = [kette for kette, _ in _css.ketten(zeile, wurzel)
              if kette[-1][0] == "td" and len(kette) == len(wurzel) + 2]
    return {name: _familie(kette) for name, kette in zip(kopf, zellen)}


def test_committed_columns_stay_monospace():
    """SLUG und TYPE stehen so in der Schedule-MD — sie sind committet."""
    spalten = _spalten(_jobs_html())
    for name in ("SLUG", "TYPE"):
        assert spalten.get(name) == "mono", f"{name}: {spalten.get(name)}"


def test_volatile_columns_carry_the_sans():
    """**Die eigentliche Korrektur.** Keiner dieser Werte steht in einer Datei
    — sie entstehen im Betrieb und ändern sich zwischen zwei Blicken.

    Der Kopf trägt sie seit `#153` als `RUNTIME`, `REL.`, `NEXT/RUN` und
    `LAST/RUN`; die Namen stehen hier, damit ein Umbenennen den Test bricht,
    statt ihn still leerlaufen zu lassen.
    """
    spalten = _spalten(_jobs_html())
    fluechtig = [n for n in spalten if n not in ("SLUG", "TYPE")]
    assert len(fluechtig) >= 5, f"zu wenige Wertspalten gefunden: {spalten}"
    falsch = {n: spalten[n] for n in fluechtig if spalten[n] != "sans"}
    assert not falsch, f"flüchtige Spalten ohne Sans: {falsch}"


def test_number_columns_hold_their_width():
    """**Die Gegenprobe, und sie wird durch den Wechsel wichtiger statt
    unwichtiger.**

    Die Design-Studie hat 39 % Breitenunterschied zwischen den Ziffern der
    System-Sans gemessen. Eine sekündlich fortgeschriebene Zelle in Sans ohne
    `tabular-nums` zappelt sichtbar — bisher schützte sie die Monospace, und
    genau die fällt hier weg.
    """
    css = _css.stylesheet()
    for regel in ("table.jobs td", ".relia-p", ".dur"):
        block = re.search(rf"{re.escape(regel)}\s*\{{[^}}]*\}}", css)
        assert block and "tabular-nums" in block.group(0), regel


# ── Der Header ──────────────────────────────────────────────────────────────


CLIENT = {
    "roles": ["controller"], "hostname": "Mac.fritz.box", "auto_sync": False,
    "started_at": NOW - 48_300,
    "engine": {"running": "v0.8.6", "needs_update": False},
    "connect": {"ok": True, "last_at": NOW - 21, "since": NOW - 4000},
}
GIT = {"branch": "trunk", "tree": "modified", "sync": "synced",
       "commit": "4715f43"}
SCHED = {"hostname": "sarasate", "started_at": NOW - 48_300,
         "workers": [{"worker": "mac"}],
         "job_stats": {"counts": {"complete": 5}, "next_due_at": NOW + 120}}


def _header_zeilen() -> dict[str, str]:
    html = render.status_header(CLIENT, GIT, scheduler=SCHED, now=NOW,
                                scheduler_host="sarasate")
    aus: dict[str, str] = {}
    for block in re.findall(r'<div class="hdr-row">.*?</div>', html, re.S):
        label = re.search(r'hdr-label">([^<]+)<', block).group(1)
        kette = next(k for k, m in _css.ketten(block, _css.BODY)
                     if "hdr-value" in (m.get("class") or "").split())
        aus[label] = _familie(kette)
    return aus


def test_the_header_values_follow_their_origin():
    """`project` und `bibi` kommen aus git und aus der Installation — sie
    stehen fest und gehören in Monospace. `clients`, `next job` und
    `heartbeat` entstehen im Betrieb.

    **`heartbeat` ist der Grenzfall und deshalb hier aufgeführt:** die Zeile
    trägt eine Uhrzeit *und* die `auto-sync`-Einstellung. Die Einstellung ist
    committet, die Uhrzeit nicht — die Zeile folgt der Uhrzeit, weil sie der
    Wert ist und `auto-sync` die Beschriftung darin.
    """
    zeilen = _header_zeilen()
    assert zeilen.get("project") == "mono", zeilen
    assert zeilen.get("bibi") == "mono", zeilen
    for label in ("clients", "next job", "heartbeat", "uptime"):
        assert zeilen.get(label) == "sans", f"{label}: {zeilen.get(label)}"


# ── Der Nodes-Screen ────────────────────────────────────────────────────────


def _nodes_spalten() -> dict[str, str]:
    html = render._clients_table([{
        "worker": "mac", "host": "Mac.fritz.box", "port": 8781,
        "role": "controller", "engine": "v0.8.6", "git_user": "m.rau",
        "git_status": "trunk · clean · synced", "git_commit": "4715f43",
        "approval_status": "approved", "connected_at": NOW - 4000,
        "last_heartbeat": NOW - 12,
    }], NOW)
    # **Leere und verschachtelte Koepfe zaehlen mit.** Die Rollen-Matrix
    # rendert `<th><abbr …>S</abbr></th>`; ein Muster, das nur nackten Text
    # nimmt, verliert sie und verschiebt danach jede Zuordnung um drei
    # Spalten — der Test misst dann eine andere Zelle und meldet trotzdem
    # einen Befund.
    kopf = [_nur_text(k) for k in
            re.findall(r"<th[^>]*>(.*?)</th>", html.split("<tbody", 1)[0], re.S)]
    zeile = re.search(r"<tr>(?:(?!</tr>).)*</tr>", html.split("<tbody", 1)[1],
                      re.S).group(0)
    wurzel = _css.BODY + [("table", frozenset()), ("tbody", frozenset())]
    zellen = [kette for kette, _ in _css.ketten(zeile, wurzel)
              if kette[-1][0] == "td" and len(kette) == len(wurzel) + 2]
    return {name: _familie(kette) for name, kette in zip(kopf, zellen)}


def test_the_nodes_table_follows_the_same_rule():
    """**Der Screen, den `v0.8.4` übersehen hat.** Die Nodes-Tabelle trägt
    keine `jobs`-Klasse und damit auch nicht deren Monospace-Regel — seit der
    Umstellung des `body` steht dort *alles* in Sans, Engine-Version, git user
    und Commit-Hash eingeschlossen.

    Er ist in dieser Klammer nie gestaltet worden. Die Schriftumstellung hat
    ihn trotzdem erreicht, weil sie am `body` hängt: **eine globale Änderung
    trifft auch, wo niemand hingesehen hat.**
    """
    spalten = _nodes_spalten()
    for name in ("Engine", "Git user", "Git status"):
        assert spalten.get(name) == "mono", f"{name}: {spalten.get(name)}"
    for name in ("Status", "Connected since", "Last heartbeat"):
        assert spalten.get(name) == "sans", f"{name}: {spalten.get(name)}"
