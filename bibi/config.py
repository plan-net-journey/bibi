"""Knoten-Konfiguration: ``~/.config/bibi/env`` (DESIGN §4.10).

Drei host-/team-private Parameter, die das Repo bewusst NICHT enthält:
``BIBI_SCHEDULER_URL``, ``BIBI_ROLE``, ``BIBI_REMOTE``. Geschrieben von
``bibi-ctrl init``, gelesen u. a. von ``bibi-ctrl status`` und (später)
``bibi-ctrl daemon install``.
"""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlparse

# Reihenfolge = Abfrage-/Schreibreihenfolge. Werte sind die Defaults für init.
KEYS: dict[str, str] = {
    "BIBI_SCHEDULER_URL": "http://localhost:8769",
    "BIBI_ROLE": "synchronizer",
    "BIBI_REMOTE": "",
    # Pfad/Name des claude-Binaries (claude-Jobs). Default "claude" = via PATH;
    # absoluter Pfad nötig, wenn claude nicht auf dem (Service-)PATH liegt.
    "BIBI_CLAUDE_BIN": "claude",
    # Menschlich gewählter Anzeigename für den Connected-Clients/Nodes-Screen
    # (Team-Registry, §4.2/A12) — Default leer = socket.gethostname(). Gilt für
    # JEDEN --connect-Knoten (Client oder Worker), nicht nur die Worker-Rolle,
    # trotz des historischen Namens (PLAN-34: BIBI_WORKER_NAME war irreführend,
    # BIBI_NODE_NAME passt zu BIBI_NODE_ID unten). Registry-Kollisionsschutz ist
    # NICHT mehr der Grund, ihn zu setzen — das übernimmt seit dem node_id-Fix
    # (Bibi4-Iteration) node_id als Registry-Schlüssel; Grund heute: ein
    # sprechendes Label statt eines rohen/opaken Hostnamens (z. B. im Docker-
    # Container). Für die Worker-Rolle bleibt derselbe Wert zusätzlich die
    # Job-Claim-Identität (``jobs.worker``-Spalte, ``worker.py``) — dort weiter
    # unter dem internen Namen ``worker_name`` geführt, s. PLAN-34 Entscheidung 1.
    "BIBI_NODE_NAME": "",
    # Von außen erreichbarer Hostname für App-Adressen (PLAN-22 Befund 6) —
    # Default leer = Ableitung über public_host() (BIBI_SCHEDULER_URL-Hostname,
    # sonst localhost). Nötig für jeden Knoten, der App-Typ-Jobs (app_port)
    # dispatcht und dessen Adresse einem Remote-Browser gemeldet werden soll.
    "BIBI_PUBLIC_HOST": "",
    # BIBI_STATUS_POLL_INTERVAL / BIBI_JOB_STATUS_POLL_INTERVAL: entfernt in
    # PLAN-36 Stufe 36.3 — das FE pollt nicht mehr, alle Regionen hängen am
    # Event-Bus (/-/events); der Collector-Takt ist ein Engine-Internum
    # (daemon/bus.py), kein Konfigurationswert. Pre-1.0, kein Backcompat.
    # Stabile, generierte Knoten-Identität für den Connected-Clients-Screen
    # (Bibi4-Iteration, User-Fund: derselbe physische Client tauchte je nach
    # Netzwerk mit unterschiedlichem BIBI_NODE_NAME/Hostname auf, alte
    # Registry-Einträge blieben stale liegen) — unabhängig von IP/Hostname,
    # einmalig generiert (node_id() unten), danach nie mehr geändert. Anders
    # als jeder andere Wert hier NIE interaktiv abgefragt (init_cmd.py
    # special-cased das) — ein Mensch soll nie eine UUID eintippen müssen.
    "BIBI_NODE_ID": "",
    # Die Hostnamen, unter denen dieser Knoten schon einmal gelaufen ist (#144)
    # — komma-getrennt, waechst bei jedem Start um den aktuellen Namen.
    #
    # **Sie erweitern das Nachschlagen, nicht das Pinnen.** Geschrieben wird
    # seit #88 nur noch unter `BIBI_NODE_ID`; die Liste haelt den Bestand
    # erreichbar, der noch Hostnamen traegt (auf dem Mac rund 130 Zeilen unter
    # zwei Namen, weil er `Air2024.local` und `Mac.fritz.box` im Wechsel
    # fuehrt).
    "BIBI_NODE_ALIASES": "",
    # Startschlüssel für den allerersten Heartbeat dieses Knotens
    # (m.rau/bibi#141, Nodes.md §3.3). **Der einzige Wert hier, der sich selbst
    # wieder löscht:** nach dem ersten erfolgreichen Heartbeat schreibt der
    # Client die env ohne ihn zurück. Er ist ein Startschlüssel, kein Zugang —
    # ihn liegenzulassen machte ihn genau zu dem Dauergeheimnis, dessen
    # Abschaffung (BIBI_CONNECT_SECRET) der Anlass für seine Bauform war.
    "BIBI_BOOTSTRAP_TOKEN": "",
}

DAEMON_PORT_DEFAULT = 8769


def daemon_port() -> int:
    """Lauschport des Daemons: ``BIBI_DAEMON_PORT`` env > **tatsächlicher Port
    eines hier laufenden Daemons** (``data/daemon-port.json``) > Port aus
    ``BIBI_SCHEDULER_URL`` (env oder ``~/.config/bibi/env``) > Default 8769.

    Ohne den ``BIBI_SCHEDULER_URL``-Fallback liefen ``bibi-ctrl job``/
    ``daemon status`` ohne ``--port``-Flag an per ``init`` konfigurierten
    Instanzen (z. B. Port 8780) vorbei — silent gegen einen Fremdprozess
    am Default-Port statt gegen den eigentlich gemeinten Daemon.

    Die Portdatei (m.rau/bibi#45) ist die einzige Stufe, die kein
    *Konfigurations*wert ist, sondern ein **Live-Befund**: sie existiert nur,
    solange ein Daemon in diesem Checkout läuft, und trägt den Port, den er
    wirklich bekommen hat. Deshalb steht sie vor ``BIBI_SCHEDULER_URL`` — was
    tatsächlich lauscht, schlägt eine Vermutung aus der Config.

    Sie steht dagegen bewusst **hinter** ``BIBI_DAEMON_PORT``: das ist der
    explizite „sprich mit DIESEM Daemon"-Override (s. ``scheduler_base_url()``),
    und ein Mehrfach-Instanz-Setup, das zwei Daemons auf einen Checkout legt,
    hängt genau daran. Beide würden dieselbe Datei schreiben — der Override
    bleibt dort der verlässliche Weg, und er wird durch diese Stufe nicht
    schwächer.

    **Wer einen Port festschreibt statt einen laufenden zu finden, nimmt
    :func:`configured_daemon_port`** — die Portdatei beantwortet „wo lauscht
    es gerade", nicht „wo soll es künftig lauschen".
    """
    raw = os.environ.get("BIBI_DAEMON_PORT", "").strip()
    if raw:
        try:
            return int(raw)
        except ValueError:
            pass

    # Lazy: ``portfile`` zieht ``bibi.repo`` (git-Aufruf) nach — das gehört nicht
    # in den Import-Pfad jedes Moduls, das nur ``config`` braucht.
    from bibi.daemon import portfile
    live = portfile.read_port()
    if live:
        return live

    scheduler_url = (os.environ.get("BIBI_SCHEDULER_URL", "").strip()
                      or read_env().get("BIBI_SCHEDULER_URL", "").strip())
    if scheduler_url:
        port = urlparse(scheduler_url).port
        if port:
            return port

    return DAEMON_PORT_DEFAULT


def configured_daemon_port() -> int:
    """Wie :func:`daemon_port`, aber **ohne** die Portdatei-Stufe (m.rau/bibi#15).

    Für alles, was einen Port *festschreibt* statt einen laufenden zu finden —
    konkret: die Autostart-Unit. Der Unterschied ist keine Feinheit, sondern ein
    Fehler, der ohne diese Trennung entstünde: läuft während eines
    ``daemon install`` gerade ein Sitzungs-Daemon (m.rau/bibi#45), lieferte
    ``daemon_port()`` dessen **flüchtigen** Port — und der stünde danach
    dauerhaft in der Unit, obwohl ihn nie jemand gewählt hat und er beim
    nächsten Sitzungsstart schon ein anderer wäre.

    Live-Befund ist eine gute Auskunft über *jetzt* und eine schlechte über
    *künftig*. Deshalb zwei Funktionen statt eines Flags: der Aufrufer muss
    sich entscheiden, welche der beiden Fragen er stellt.
    """
    raw = os.environ.get("BIBI_DAEMON_PORT", "").strip()
    if raw:
        try:
            return int(raw)
        except ValueError:
            pass
    scheduler_url = (os.environ.get("BIBI_SCHEDULER_URL", "").strip()
                      or read_env().get("BIBI_SCHEDULER_URL", "").strip())
    if scheduler_url:
        port = urlparse(scheduler_url).port
        if port:
            return port
    return DAEMON_PORT_DEFAULT


def scheduler_base_url() -> str:
    """Basis-URL des Schedulers — anders als :func:`daemon_port` (nur der Port)
    liefert diese Funktion Host **und** Port.

    ``BIBI_DAEMON_PORT`` (env, lokal-explizit) > ``BIBI_SCHEDULER_URL`` (env
    oder ``~/.config/bibi/env``, volle URL inkl. Host) > ``http://localhost:8769``.

    PLAN-13 Stufe 13.0 (2026-07-17): ``bibi-ctrl job``/``at`` sprachen bisher
    immer ``127.0.0.1:{daemon_port()}`` an — auch auf einem reinen Client-
    Knoten, dessen ``BIBI_SCHEDULER_URL`` korrekt auf einen entfernten Host
    zeigt. Läuft dort zufällig ein eigener lokaler Daemon auf demselben Port
    (z. B. Client-Rolle auf Port 8780), landet der Befehl nicht bei
    "Connection refused", sondern beim eigenen, falschen (Nicht-Scheduler-)
    Daemon — Root Cause einer Session, die lange raten musste, wo der
    Scheduler tatsächlich läuft, obwohl die Antwort in der eigenen Config
    stand. ``BIBI_DAEMON_PORT`` bleibt Vorrang, weil es explizit "sprich mit
    MEINEM eigenen Daemon" bedeutet (von ``bibi-ctrl daemon`` selbst gesetzt,
    s. ``daemon_cmd.py``) — ein reiner Lokalitäts-Override, kein Federations-
    Ziel."""
    raw = os.environ.get("BIBI_DAEMON_PORT", "").strip()
    if raw:
        try:
            return f"http://127.0.0.1:{int(raw)}"
        except ValueError:
            pass

    scheduler_url = (os.environ.get("BIBI_SCHEDULER_URL", "").strip()
                      or read_env().get("BIBI_SCHEDULER_URL", "").strip())
    if scheduler_url:
        return scheduler_url.rstrip("/")

    return f"http://localhost:{DAEMON_PORT_DEFAULT}"


def public_host() -> str:
    """Von außen erreichbarer Hostname dieses Knotens für App-Adressen (§
    PLAN-22 Befund 6 — löst die zuvor an drei Stellen hartkodierte
    ``127.0.0.1``-Adresse ab, die auf einem Remote-Host wie sarasate tot war).

    Stufen: ``BIBI_PUBLIC_HOST`` (env > ``~/.config/bibi/env``) > ``localhost``.

    Früher gab es eine Zwischenstufe, die ohne explizites ``BIBI_PUBLIC_HOST``
    den Hostnamen aus ``BIBI_SCHEDULER_URL`` borgte — entfernt (Bibi4-
    Iteration, User-Fund: ein Client zeigte den Hostnamen seines Schedulers
    statt seines eigenen). Sie half laut ihrer eigenen ursprünglichen Doku nie
    dem Host-Rolle-Fall (der braucht Stufe 1 ohnehin zwingend) und war für
    einen echten Remote-Client schlicht falsch — sie borgte die Adresse eines
    FREMDEN Knotens. Ohne explizites ``BIBI_PUBLIC_HOST`` bleibt es jetzt beim
    reinen ``localhost``-Default, kein Rätselraten mehr.
    """
    explicit = (os.environ.get("BIBI_PUBLIC_HOST", "").strip()
                or read_env().get("BIBI_PUBLIC_HOST", "").strip())
    if explicit:
        return explicit

    return "localhost"


class KeinRepoError(RuntimeError):
    """Hier ist kein Team-Repo — also gibt es auch keinen Knoten (m.rau/bibi#52).

    Für einen *Leser* ist das kein Fehler: :func:`read_env` fängt ihn ab und
    liefert ein leeres Dict, dieselbe Lage wie eine noch nicht angelegte Datei.
    Für einen *Schreiber* schon — eine Konfiguration irgendwohin zu legen, wo sie
    kein Knoten je liest, ist schlimmer als ein Abbruch.
    """


def env_path() -> Path:
    """Pfad zu ``env``: ``<repo>/data/env``. Eine Stufe, kein Fallback.

    **Eine Konfiguration gehört zu einem Repo** (m.rau/bibi#52). Ein Knoten *ist*
    ein Team-Repo — die Registry schlüsselt ohnehin darauf, und wer zwei Repos
    auf einer Maschine betreibt, betreibt zwei Knoten.

    Vorher standen hier drei Stufen: ``BIBI_CONFIG_PATH`` > ``XDG_CONFIG_HOME``
    > ``~/.config``. Sie sind ersatzlos entfallen, und zwar zusammen mit dem
    Problem, das sie lösen sollten. Der Reihe nach, weil jede ihren eigenen
    Grund hatte und keiner davon trug:

    * ``BIBI_CONFIG_PATH`` war für **supervisierte** Knoten gedacht — der alte
      Docstring nannte den Träger selbst, *„ein Pfad, direkt in der jeweiligen
      systemd-Unit sichtbar"*. Ein Client bekommt per m.rau/bibi#180 keine Unit.
      Für die häufigste Knotenart gab es damit keinen Ort, an dem die Variable
      überdauert; ein zweites Team-Repo ließ sich konfigurieren, aber nicht
      betreiben, weil der Daemon beim Start wieder die Datei des ersten las.
    * ``XDG_CONFIG_HOME`` und ``~/.config`` legten die Konfiguration **pro
      Nutzer** ab, während sie pro Repo gilt. Der Release-Plan ``v0.5.0`` hatte
      genau das am 2026-07-31 notiert — *„``BIBI_NODE_ID`` … ist damit pro Nutzer
      statt pro Repo, während die Registry darauf schlüsselt"* — und
      zurückgestellt, weil es „erst mit dem Host scharf" werde. Es wurde sechs
      Tage später scharf, ohne Host.

    Nebenbei fällt eine Schwäche weg, die niemand als solche geführt hat: der
    ``~/.config``-Fallback ließ Testläufe die echte Konfiguration des
    ausführenden Nutzers lesen, samt Credentials — die Suite brauchte dagegen ein
    autouse-Fixture (``tests/conftest.py``).

    :raises KeinRepoError: wenn das Arbeitsverzeichnis in keinem Repo liegt.
    """
    from bibi import repo  # lazy: repo zieht git/subprocess nach
    root = repo.root_or_none()
    if root is None:
        raise KeinRepoError(
            "hier ist kein Team-Repo — eine Knoten-Konfiguration gehört in eines "
            "(m.rau/bibi#52). In ein Team-Repo wechseln oder eins anlegen."
        )
    return root / "data" / "env"


def read_env(path: Path | None = None) -> dict[str, str]:
    """``env`` parsen (``KEY=VALUE`` je Zeile). Fehlt die Datei: leeres Dict.

    Robust gegen Kommentare (``#``) und Leerzeilen; Werte werden getrimmt.

    **Ohne Repo leer statt laut** (m.rau/bibi#52): ein Leser, der zufällig
    außerhalb eines Team-Repos läuft, ist in derselben Lage wie einer, dessen
    Datei noch nicht angelegt ist — Defaults greifen. Das Gegenstück
    :func:`write_env` lässt denselben Fall bewusst durch.
    """
    if path is None:
        try:
            path = env_path()
        except KeinRepoError:
            return {}
    p = path
    if not p.exists():
        return {}
    out: dict[str, str] = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip()
    return out


def write_env(values: dict[str, str], path: Path | None = None) -> Path:
    """``env`` atomar schreiben (KEYS in Reihenfolge, Fremdes erhalten). Mode 0600.

    **Was nicht in ``KEYS`` steht, wird trotzdem bewahrt** (m.rau/bibi#51).
    ``read_env()`` liest jede ``KEY=VALUE``-Zeile; schriebe diese Funktion nur
    ``KEYS`` zurück, hätten Lesen und Schreiben verschiedene Vorstellungen davon,
    was in der Datei stehen darf — und wer schreibt, verlöre, was er nicht kennt.

    Betroffen waren in der Praxis die ``BIBI_JOB_ENV_*``-Werte: laut
    ``CONVENTIONS.md`` legitimer Inhalt genau dieser Datei, und der dokumentierte
    Weg, ein Credential auf einen Host zu bringen, ist ein ``>>``-Append daran.
    Der gefährlichere der beiden Auslöser war nicht ``init``, sondern
    :func:`node_id` — die self-healing-Funktion, die ein manuelles ``init``
    ausdrücklich *ersparen* soll und dabei denselben Schaden anrichtete: kein
    Kommando, kein Neustart, kein Zutun.

    Vorrang hat ``values``: wer einen fremden Schlüssel mitgibt, setzt ihn. Das
    ist der Weg, auf dem ``init`` Credentials beim Umzug einer Konfiguration
    mitnimmt (m.rau/bibi#52).
    """
    p = path or env_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    fremd = {k: v for k, v in read_env(p).items() if k not in KEYS}
    fremd.update({k: v for k, v in values.items() if k not in KEYS})
    lines = ["# bibi-Knoten-Konfiguration — von `bibi-ctrl init` erzeugt (DESIGN §4.10).",
             "# Host-/team-privat; nie ins Repo committen.", ""]
    for key in KEYS:
        lines.append(f"{key}={values.get(key, '')}")
    if fremd:
        lines += ["", "# Nicht von `init` verwaltet, hier aber zu Hause (CONVENTIONS.md):",
                  "# BIBI_JOB_ENV_* und was sonst jemand angehängt hat."]
        lines += [f"{k}={v}" for k, v in fremd.items()]
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    tmp.chmod(0o600)
    tmp.replace(p)
    return p


def node_id() -> str:
    """Stabile, generierte Knoten-Identität (``BIBI_NODE_ID``, s. ``KEYS``-
    Kommentar) — self-healing: fehlt der Wert (Bestandsknoten von vor dieser
    Änderung, oder ein ``bibi-ctrl init`` ohne diesen Schlüssel), wird er beim
    ersten Zugriff generiert und in dieselbe ``env``-Datei zurückgeschrieben,
    kein manuelles Neu-``init`` auf bereits konfigurierten Knoten nötig."""
    import uuid
    existing = read_env()
    val = existing.get("BIBI_NODE_ID", "").strip()
    if val:
        return val
    new_id = uuid.uuid4().hex
    existing["BIBI_NODE_ID"] = new_id
    write_env(existing)
    return new_id


def node_aliases() -> tuple[str, ...]:
    """Die Hostnamen, unter denen dieser Knoten schon einmal lief (`#144`).

    **Nur lesend.** Eingetragen wird in :func:`record_hostname`, und die
    Trennung ist Absicht: ``pin_lookup_ids()`` ruft diese Funktion in jeder
    Datenbankabfrage. Wäre das Eintragen hier eingebaut — nach dem Vorbild von
    :func:`node_id`, das sich beim ersten Zugriff selbst heilt —, schriebe
    jede Abfrage in die Konfigurationsdatei.

    **Der wichtigere Grund ist aber die Zusage:** wer nachschlägt, fragt oft
    unter einem *fremden* Namen (dafür ist der ``host``-Parameter da). Ein
    Nachschlagen, das einträgt, machte aus jeder Fremdanfrage einen eigenen
    Namen — und damit aus der Pin-Zusage eine Selbstbedienung.
    """
    roh = read_env().get("BIBI_NODE_ALIASES", "")
    return tuple(dict.fromkeys(t.strip() for t in roh.split(",") if t.strip()))


def record_hostname(name: str | None = None) -> tuple[str, ...]:
    """Den Namen, unter dem dieser Knoten gerade läuft, in die Liste aufnehmen.

    **Gerufen beim Start eines Daemons, nicht beim Nachschlagen** — die Liste
    wächst damit ausschließlich aus dem *eigenen Lauf* und **nie** aus der
    Datenbank. Das ist die eine Zeile, an der die Pin-Zusage hängt (`#144`,
    Weg 1, Entscheidung m.rau 2026-08-12): ein Alias, der aus einer
    ``jobs``-Zeile stammte, wäre der Weg, auf dem ein fremder Name in die
    eigene Menge käme — unbemerkt, weil er danach aussähe wie ein eigener.

    **Zwei Rechner, die je einmal ``Air.local`` hießen, erben einander
    trotzdem nicht:** jeder führt seine eigene ``env``-Datei, und ein Name
    kommt nur hinein, wenn *dieser* Prozess ihn getragen hat.

    Idempotent: derselbe Name zweimal verlängert die Liste nicht. Ohne das
    wüchse sie bei jedem Daemon-Start um einen Eintrag, und eine Liste, die
    nur wächst, ist irgendwann eine ``IN``-Klausel mit tausend Parametern.
    """
    import socket
    jetzt = (name or socket.gethostname() or "").strip()
    bekannt = list(node_aliases())
    if not jetzt or jetzt in bekannt:
        return tuple(bekannt)
    bekannt.append(jetzt)
    existing = read_env()
    existing["BIBI_NODE_ALIASES"] = ",".join(bekannt)
    write_env(existing)
    return tuple(bekannt)


# ── PLAN-32 Stufe 32.2/32.3: Credential-Distribution (Host → Client) ────────
#
# Allowlist ist eine Namenskonvention, kein zweites Verzeichnis (Entscheidung
# 3): jeder ``BIBI_JOB_ENV_*``-Wert im eigenen ``env`` ist automatisch
# verteilbar — dieselbe Menge, die ``worker.py::_exec_config()`` für
# Job-Injection bereits liest. Auf dem Client landen empfangene Werte in
# einer ZWEITEN, dem eigentlichen ``env`` vorgelagerten Datei (Entscheidung
# 4) — Herkunft bleibt sichtbar, ein lokal in ``env`` gesetzter gleichnamiger
# Wert gewinnt immer (dortiges Merge in ``worker.py::_exec_config()``).

_JOB_ENV_PREFIX = "BIBI_JOB_ENV_"
#: Interner Marker in der Distributed-Datei, kein Job-Credential — beginnt
#: bewusst nicht mit _JOB_ENV_PREFIX, damit der Präfix-Scan ihn nie injiziert.
_DISTRIBUTED_VERSION_KEY = "__bibi_config_version__"


def distributable_config(env: dict[str, str] | None = None) -> dict[str, str]:
    """Host-Seite: alle ``BIBI_JOB_ENV_*``-Werte aus ``env`` (Default:
    ``read_env()`` gemergt mit ``os.environ``, Prozess-Env gewinnt bei
    Kollision — dieselbe Präzedenz wie ``worker.py::_exec_config()``s
    Job-Injection) — die komplette Distribution-Allowlist, eine reine
    Namens-Prüfung, keine zweite Liste. Beide Quellen zu berücksichtigen ist
    hier bewusst: verteilbar soll exakt sein, was der Präfix-Scan für die
    eigene Job-Injection ohnehin schon nutzt, nicht nur der Datei-Anteil davon."""
    env = {**read_env(), **os.environ} if env is None else env
    return {k: v for k, v in env.items() if k.startswith(_JOB_ENV_PREFIX) and v}


def config_version(bundle: dict[str, str]) -> str:
    """Kurzer, stabiler Hash über die verteilbare Config (Entscheidung 2:
    Hash statt Timestamp — ändert sich genau dann, wenn sich ein Wert
    tatsächlich ändert, immun gegen Uhrzeit-Drift/„berührt-aber-unverändert")."""
    import hashlib
    canonical = "\n".join(f"{k}={bundle[k]}" for k in sorted(bundle))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def distributed_env_path() -> Path:
    """Client-Seite: die zweite, ``env`` vorgelagerte Datei (Entscheidung 4)
    — neben der Haupt-``env`` desselben Knotens (erbt so automatisch
    ``BIBI_CONFIG_PATH``s Mehrfach-Instanz-Trennung, s. ``env_path()``)."""
    return env_path().parent / "distributed-env"


def read_distributed_env(path: Path | None = None) -> dict[str, str]:
    """Client-Seite: zuletzt vom Host empfangenes Bundle + Versionsmarker
    lesen. Fehlt die Datei (noch nie ein Bundle empfangen): leeres Dict —
    dieselbe Robustheit wie ``read_env()``."""
    p = path or distributed_env_path()
    if not p.exists():
        return {}
    out: dict[str, str] = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip()
    return out


def distributed_config_version(path: Path | None = None) -> str | None:
    """Client-Seite: die zuletzt angewandte Version, fürs nächste
    Heartbeat-``client_config_version``-Feld. ``None`` = noch nie empfangen."""
    return read_distributed_env(path).get(_DISTRIBUTED_VERSION_KEY)


def write_distributed_env(bundle: dict[str, str], *, version: str,
                          path: Path | None = None) -> Path:
    """Client-Seite: neues Bundle atomar schreiben (analog ``write_env()``,
    Mode 0600) — komplett ersetzt, nicht gemergt (das Bundle selbst ist schon
    die vollständige, aktuelle Sicht vom Host)."""
    p = path or distributed_env_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# bibi — vom Host verteilte Job-Credentials (PLAN-32 Stufe 32.2).",
             "# Automatisch geschrieben bei jedem Heartbeat mit neuer Version —",
             "# manuelle Änderungen gehen beim nächsten Fetch verloren. Ein lokal",
             "# in ~/.config/bibi/env gesetzter gleichnamiger Wert gewinnt immer.", ""]
    for key in sorted(bundle):
        lines.append(f"{key}={bundle[key]}")
    lines.append(f"{_DISTRIBUTED_VERSION_KEY}={version}")
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    tmp.chmod(0o600)
    tmp.replace(p)
    return p
