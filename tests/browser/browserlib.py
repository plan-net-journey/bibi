"""Die Browser-Ebene: echte Daemons, echter Browser (#84).

**Warum es diese Ebene gibt.** Am 2026-08-08 gingen drei Releases raus, zwei
davon Reparaturen am Vorgänger. Vier der behobenen Fehler teilen eine
Eigenschaft: sie entstehen erst im Browser und sind durch alle 2750 Tests
gekommen — ein htmx-Swap, der eine `EventSource` zurücklässt (`#82`), eine
Filterwahl, die einen Seitenwechsel nicht überlebt (`#83`), ein Abonnement, das
sich im Sekundentakt neu aufbaut (`#77`), ein Durchreicher, der Stille für Tod
hält (`#78`). Die Suite prüft FE-Verhalten an 19 Stellen und durchweg gegen
Zeichenketten (`assert 'data-job="j7"' in html`). Das ist für server-gerendertes
HTML richtig und trägt weit — es sieht per Bauart nichts, was erst im Browser
entsteht.

**Was hier echt ist und warum.** Kein `TestClient`, kein ASGI-In-Process: die
Daemons laufen als eigene Prozesse mit echtem uvicorn, weil genau die Naht
zwischen zwei Prozessen die Fehler trug (SSE-Ströme, Sockets, Timeouts). Ein
`TestClient` hat keine offenen Verbindungen, die man leaken könnte — er wäre
gegen `#82` per Bauart blind. Denselben Schluss zog `81ea6dd` schon beim Bau:
der serverseitige Zähler-Test lief 112 Sekunden und traf keine Aussage, weil
`TestClient.stream()` den Generator erst beim Lesen startet.

**Kosten.** Ein Daemon-Start liegt bei ein bis zwei Sekunden, ein Browser-Start
darunter; die Ebene ist damit Minuten statt Sekunden und läuft deshalb nicht im
Engine-CI mit, sondern in ihrem eigenen Job (`browser-ci.sh`). Der Engine-CI
behält seine 70 Sekunden, und das ist der Grund, warum er bei jedem Push
angestoßen wird.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

#: Frist für „der Daemon ist oben". Großzügig: der erste Start in einem frischen
#: Repo legt die SQLite an, scannt den Vault und feuert `startup`-Jobs.
_START_FRIST_S = 30.0

#: Der Startbefehl. `bibi.ctrl.main` statt des Konsolen-Skripts `bibi-ctrl`:
#: derselbe Einstiegspunkt, aber ohne die Annahme, dass das venv des Testlaufs
#: seine Skripte im PATH hat (unter `uv run` stimmt das, unter `pytest` aus
#: einer IDE nicht immer).
_BOOT = "import sys; from bibi.ctrl import main; sys.exit(main(sys.argv[1:]))"


def _freier_port() -> int:
    """Eine Portnummer, die gerade frei war.

    Bewusst nicht `--port auto`: der Daemon legt die Nummer dann in seiner
    Portdatei ab, und die zu lesen hieße, auf sie zu warten — ein zweites
    Warteproblem neben dem, das `_warte_auf_daemon()` ohnehin löst. Das
    Restrisiko (jemand belegt den Port zwischen `close()` und `bind()`) trägt
    ein Testlauf, keine Produktion.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _hole(url: str, *, timeout: float = 5.0) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:  # noqa: S310
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def _post(url: str, *, timeout: float = 5.0, headers: dict | None = None,
          daten: bytes = b"") -> tuple[int, str]:
    req = urllib.request.Request(url, method="POST", data=daten,  # noqa: S310
                                 headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


@dataclass
class Knoten:
    """Ein laufender Daemon — Adresse, Repo, Prozess."""

    url: str
    root: Path
    proc: subprocess.Popen
    log: Path
    rollen: str

    def get(self, pfad: str) -> tuple[int, str]:
        return _hole(self.url + pfad)

    def post(self, pfad: str, **kw) -> tuple[int, str]:
        return _post(self.url + pfad, **kw)

    def get_json(self, pfad: str):
        """``GET`` und JSON — leere Liste, wenn die Route nichts Brauchbares
        liefert (eine Rolle, die es hier nicht gibt, antwortet ``404``)."""
        code, body = self.get(pfad)
        if code != 200:
            return []
        try:
            return json.loads(body)
        except ValueError:
            return []

    def post_json(self, pfad: str, nutzlast: dict) -> tuple[int, str]:
        return _post(self.url + pfad, daten=json.dumps(nutzlast).encode(),
                     headers={"content-type": "application/json"})

    def ausgabe(self) -> str:
        """Was der Daemon auf stdout/stderr geschrieben hat — für Fehlermeldungen.

        Ein Browser-Test, der scheitert, scheitert oft am Daemon und nicht am
        Browser. Ohne diese Zeilen steht in der Assertion nur, dass eine Seite
        leer war."""
        try:
            return self.log.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return "(kein Log)"


@dataclass
class Fabrik:
    """Startet Daemons und räumt sie am Ende des Tests wieder ab."""

    tmp: Path
    knoten: list[Knoten] = field(default_factory=list)

    def repo(self, name: str) -> Path:
        """Ein Team-Repo, wie `tests/conftest.py::_init_repo` es baut.

        Bewusst nachgebaut statt importiert: `tests/` ist kein Paket, und ein
        `sys.path`-Kunstgriff für vier Zeilen wäre teurer als die vier Zeilen.
        """
        root = self.tmp / name
        (root / ".claude").mkdir(parents=True)
        (root / "vault" / "case").mkdir(parents=True)
        (root / "pyproject.toml").write_text(
            '[project]\nname = "t"\nversion = "0.0.0"\n', encoding="utf-8")
        (root / ".gitignore").write_text(".claude/.state.md\ndata/\n", encoding="utf-8")
        for args in (("init", "-q", "-b", "trunk"),
                     ("config", "user.name", "Test"),
                     ("config", "user.email", "test@example.com"),
                     ("add", "-A"), ("commit", "-q", "-m", "init")):
            subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)
        return root

    def starte(self, root: Path, *, rollen: str = "--synchronizer --scheduler "
                                                  "--worker --controller",
               env: dict | None = None, scheduler_ist_selbst: bool = False) -> Knoten:
        port = _freier_port()
        umgebung = {k: v for k, v in os.environ.items() if not k.startswith("BIBI_")}
        # Dieselbe Isolation wie `tests/conftest.py`: kein Zugriff auf die
        # Konfiguration des ausführenden Nutzers, keine geerbten Credentials.
        umgebung["XDG_CONFIG_HOME"] = str(self.tmp / f"cfg-{port}")
        Path(umgebung["XDG_CONFIG_HOME"]).mkdir(parents=True, exist_ok=True)
        if scheduler_ist_selbst:
            # `BIBI_SCHEDULER_URL` auf die **eigene** Adresse — genau die
            # Konstellation, die `d2c03bc` auf sarasate vorfand und für das
            # Abonnement entschärfte. Für den Controller hat sie eine zweite,
            # unabhängige Wirkung: `_output_stream_url()` prüft nur, *ob* eine
            # Scheduler-Adresse gesetzt ist, nicht ob sie woanders hinzeigt —
            # die Output-Box bekommt dadurch einen eigenen Strom auf den
            # Durchreicher dieses Knotens. Erst hier setzbar: die Adresse
            # enthält den Port, und den kennt erst diese Zeile.
            umgebung["BIBI_SCHEDULER_URL"] = f"http://127.0.0.1:{port}"
        umgebung.update(env or {})
        log = self.tmp / f"daemon-{port}.log"
        fh = log.open("wb")
        proc = subprocess.Popen(
            [sys.executable, "-c", _BOOT, "daemon", "run",
             "--host", "127.0.0.1", "--port", str(port), *rollen.split()],
            cwd=root, env=umgebung, stdout=fh, stderr=subprocess.STDOUT)
        k = Knoten(url=f"http://127.0.0.1:{port}", root=root, proc=proc,
                   log=log, rollen=rollen)
        self.knoten.append(k)
        _warte_auf_daemon(k)
        _warte_auf_bus(k)
        return k

    def raeume_ab(self) -> None:
        for k in self.knoten:
            if k.proc.poll() is None:
                k.proc.terminate()
        for k in self.knoten:
            try:
                k.proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                k.proc.kill()
                k.proc.wait(timeout=5)


def _warte_auf_daemon(k: Knoten) -> None:
    ende = time.time() + _START_FRIST_S
    while time.time() < ende:
        if k.proc.poll() is not None:
            raise RuntimeError(
                f"Daemon ({k.rollen}) ist beim Start gestorben, Exit "
                f"{k.proc.returncode}:\n{k.ausgabe()}")
        try:
            if _hole(k.url + "/-/health", timeout=1.0)[0] == 200:
                return
        except OSError:
            pass
        time.sleep(0.15)
    raise RuntimeError(f"Daemon ({k.rollen}) war nach {_START_FRIST_S:.0f}s nicht "
                       f"erreichbar:\n{k.ausgabe()}")


#: Wie lange ein frischer Knoten braucht, bis der Collector Änderungen *meldet*.
#:
#: **Der Collector vergleicht, er beobachtet nicht** — er braucht einen
#: Vorzustand. ``Collector._loop()`` schläft erst ein Intervall, tickt dann zum
#: ersten Mal und setzt am Ende dieses Tickts ``_primed = True``; gemeldet wird
#: ab dem zweiten. Bis dahin sind ``_jobs``-Schnappschuss und ``_journal_max``
#: einfach der Ist-Stand, und das ist richtig so: sonst meldete jeder
#: Daemon-Start jede vorhandene Zeile als frische Änderung.
#:
#: **Warum das hier steht und nicht in jedem Test.** Ein Lauf, der *vor* dem
#: zweiten Tick startet und endet, hinterlässt keinen Vorher-Zustand, gegen den
#: sich etwas vergleichen ließe — der Wechsel fällt ins Priming und erzeugt kein
#: einziges Ereignis. Der Test misst dann nicht das FE, sondern seine eigene
#: Eile, und sieht dabei exakt so aus wie ein kaputter Bus: eine Seite, die sich
#: nicht bewegt. Genau diese Verwechslung hat beim Bau dieser Ebene eine halbe
#: Stunde gekostet.
_BUS_BEREIT_S = 2.5


def _warte_auf_bus(k: Knoten) -> None:
    """Warten, bis der Collector dieses Knotens meldebereit ist.

    Gemessen an der Betriebszeit, die der Daemon selbst nennt (``/-/status``),
    nicht an einem ``sleep`` ab dem Popen-Aufruf: zwischen Prozessstart und
    Anwendungsstart liegt der Import von uvicorn und FastAPI, und der ist auf
    einer belasteten Maschine kein fester Betrag.
    """
    ende = time.time() + _START_FRIST_S
    while time.time() < ende:
        code, body = _hole(k.url + "/-/status", timeout=2.0)
        if code == 200:
            try:
                s = json.loads(body)
                if float(s["now"]) - float(s["started_at"]) >= _BUS_BEREIT_S:
                    return
            except (ValueError, KeyError, TypeError):
                return          # kein Zeitstempel: nicht daran aufhängen
        time.sleep(0.2)
    raise RuntimeError(f"Knoten ({k.rollen}) meldete keine Betriebszeit:\n{k.ausgabe()}")


def job_md(root: Path, slug: str, *, payload: str = "job: echo hi",
           schedule: str = "adhoc") -> Path:
    """Eine Job-MD im Vault — die einzige Art, wie die Engine Arbeit erfährt."""
    ordner = root / "vault" / "case" / "x"
    ordner.mkdir(parents=True, exist_ok=True)
    p = ordner / f"{slug}.md"
    p.write_text(f"---\nslug: {slug}\nschedule: {schedule}\n{payload}\n---\n",
                 encoding="utf-8")
    return p


# ── Der Browser ─────────────────────────────────────────────────────────────

#: Vor jedem Skript der Seite eingespielt: eine ``EventSource``, die mitzählt,
#: und ein ``XMLHttpRequest``, der seine Ziele mitschreibt.
#:
#: **Warum eine Zählung und keine Beobachtung von außen.** Ob eine SSE-Verbindung
#: noch offen ist, sieht man dem Netzwerkverkehr nicht an — sie ist genau dann
#: still, wenn nichts passiert, und das ist der Normalfall. Serverseitig zu
#: zählen scheitert an derselben Stelle, an der ``81ea6dd`` gescheitert ist. Was
#: ``#82`` behauptet, ist eine Aussage über den **Browser**: eine Box, die
#: verschwindet, schließt ihren Strom. Genau das misst dieser Zähler, und zwar
#: an der Stelle, an der der Fix ansetzt.
#:
#: **Warum auch die Anfragen hier gezählt werden und nicht per ``page.on(
#: "request")``.** Playwrights Sync-API stellt Ereignisse nur zu, solange der
#: Aufrufer *in* einem Playwright-Aufruf steckt; eine Python-Warteschleife mit
#: ``time.sleep()`` friert die Zustellung ein. Eine Liste, die dabei leer
#: bleibt, sieht wie ein FE-Fehler aus und ist ein Messfehler — beim Bau dieser
#: Ebene genau einmal passiert, mit einer halben Stunde Suche am falschen Ende.
#: Ein Zähler in der Seite kann diesen Fehler nicht machen: ihn zu lesen *ist*
#: ein Playwright-Aufruf.
_ZAEHLER_JS = """
(() => {
  window.__bibiXHR = [];
  const oOpen = XMLHttpRequest.prototype.open;
  XMLHttpRequest.prototype.open = function (m, u, ...rest) {
    window.__bibiXHR.push(String(u));
    return oOpen.call(this, m, u, ...rest);
  };
  const Orig = window.EventSource;
  if (!Orig) return;
  window.__bibiES = {offen: 0, gebaut: 0, geoeffnet: 0, neuverbunden: 0, urls: []};
  const Zaehlend = function (url, cfg) {
    const s = new Orig(url, cfg);
    window.__bibiES.gebaut++;
    window.__bibiES.offen++;
    window.__bibiES.urls.push(String(url));
    // `open` feuert bei **jedem** Verbindungsaufbau — beim ersten und bei jedem,
    // den der Browser nach einem Abriss von sich aus nachholt.
    //
    // **Die Unterscheidung ist das Maß für `#78`, und sie zu übersehen kostet
    // einen falschen Befund.** Beim Bau dieser Ebene zählte hier zunächst nur
    // `geoeffnet`, und der Test schlug auf einem gesunden Stand an: eine Box,
    // die der Bus ersetzt, ist ein **neues** Objekt mit einem ersten `open` —
    // ein legitimer Vorgang, der wie ein Abriss aussah. `neuverbunden` zählt
    // deshalb nur das zweite und jedes weitere `open` **desselben** Stroms.
    s.addEventListener('open', () => {
      window.__bibiES.geoeffnet++;
      if (s.__bibiWarOffen) window.__bibiES.neuverbunden++;
      s.__bibiWarOffen = true;
    });
    const zu = s.close.bind(s);
    s.close = function () {
      if (!s.__bibiZu) { s.__bibiZu = true; window.__bibiES.offen--; }
      return zu();
    };
    return s;
  };
  Zaehlend.prototype = Orig.prototype;
  Zaehlend.CONNECTING = 0; Zaehlend.OPEN = 1; Zaehlend.CLOSED = 2;
  window.EventSource = Zaehlend;
})();
"""


def stroeme(page) -> dict:
    """``{offen, gebaut, urls}`` — der Zählerstand der Seite."""
    return page.evaluate(
        "() => window.__bibiES || {offen: 0, gebaut: 0, geoeffnet: 0, "
        "neuverbunden: 0, urls: []}")


def anfragen(page, teil: str) -> list[str]:
    """Die von der Seite abgesetzten XHR-Ziele, gefiltert nach ``teil``.

    Nur XHR: Navigationen und die SSE-Ströme stehen bewusst nicht drin — es
    geht um das Nachladen, und das läuft bei htmx durch ``XMLHttpRequest``.
    """
    alle = page.evaluate("() => window.__bibiXHR || []")
    return [u for u in alle if teil in u]


def warte_bis(bedingung, *, frist: float, takt: float = 0.2, was: str = ""):
    """Pollt ``bedingung()`` bis sie wahr ist — sonst ``AssertionError`` mit ``was``.

    Playwrights ``expect_*`` warten auf DOM-Zusagen; hier geht es regelmäßig um
    Zustand außerhalb des DOM (Zählerstände, Prozesse, Journal-Einträge).
    """
    ende = time.time() + frist
    letzter = None
    while time.time() < ende:
        letzter = bedingung()
        if letzter:
            return letzter
        time.sleep(takt)
    raise AssertionError(f"{was} (nach {frist:.0f}s, zuletzt: {letzter!r})")


def paar(fabrik) -> tuple[Knoten, Knoten]:
    """Die Produktions-Topologie im Kleinen: ein Host, ein Client daran.

    **Warum zwei Knoten und nicht einer.** Beide Szenarien, die hier daran
    hängen, gibt es auf einem einzelnen Knoten gar nicht:

    * `#82`/`#78` brauchen eine Output-Box mit ``data-stream``. Die entsteht
      nur, wenn der Lauf **beim Scheduler** liegt: auf dem Host selbst tailt
      der Collector die ``output.jsonl`` und die Box wächst über den globalen
      Bus, ganz ohne zweiten Strom (``_output_stream_url()`` gibt dort
      bewusst ``None``).
    * `#77` ist ein Fehler des **Abonnements** — und ein Scheduler abonniert
      sich seit `d2c03bc` nicht mehr selbst. Ohne Client gibt es nichts, was
      sich zu oft neu verbinden könnte.

    Gibt ``(host, client)`` zurück; der Client ist freigeschaltet.
    """
    host_root = fabrik.repo("host")
    host = fabrik.starte(host_root, rollen="--synchronizer --scheduler --worker")

    client_root = fabrik.repo("client")
    client = fabrik.starte(client_root,
                           rollen="--synchronizer --controller --connect",
                           env={"BIBI_SCHEDULER_URL": host.url})

    # Freischalten. Ohne das weist das Gate jeden Zugriff des Clients ab, das
    # Abonnement kommt nie zustande — und ein Test, der das übersieht, misst
    # einen dauerhaft abgerissenen Strom statt des Verhaltens, das er prüfen
    # will. Der Aufruf geht lokal und ohne Knoten-Header: genau der Weg, den
    # `worker_approve()` dem Host-Operator offenlässt.
    node_id = _knoten_id(client)
    code, body = host.post(f"/-/worker/{node_id}/approve")
    if code != 200:
        raise RuntimeError(f"Freischaltung schlug fehl ({code}): {body}\n"
                           f"{host.ausgabe()}")
    return host, client


def _knoten_id(k: Knoten) -> str:
    """Die Knoten-Identität, wie der Knoten selbst sie nennt.

    Erst wenn sein Heartbeat angekommen ist, kennt der Host sie — deshalb hier
    beim Client erfragt und nicht beim Host gesucht.
    """
    ende = time.time() + _START_FRIST_S
    while time.time() < ende:
        code, body = k.get("/-/status")
        if code == 200:
            try:
                nid = json.loads(body)["node"]["node_id"]
            except (ValueError, KeyError, TypeError):
                nid = None
            if nid:
                return str(nid)
        time.sleep(0.2)
    raise RuntimeError(f"Knoten nannte keine node_id:\n{k.ausgabe()}")


def journal(k: Knoten) -> list[dict]:
    code, body = k.get("/-/run/journal")
    if code != 200:
        return []
    try:
        return json.loads(body)
    except ValueError:
        return []
