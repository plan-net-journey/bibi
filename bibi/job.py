"""bibi.job — Signale für Job-Autoren.

Schreibt BIBI:{...}-Zeilen auf stdout. Der Wrapper parst und verarbeitet sie.
"""
import json
import os
import sys
from pathlib import Path


def _emit(payload: dict) -> None:
    print(f"BIBI:{json.dumps(payload, separators=(',', ':'))}", flush=True)


def data_dir(subsystem: str) -> Path:
    """Job-eigenes, worktree-unabhängiges Datenverzeichnis unter
    ``~/.local/share/bibi/<subsystem>/<job_id>/`` (External job data &
    secrets, ``vault/CONVENTIONS.md``) — überlebt den Worktree-Wipe zwischen
    Fires/Retries (anders als alles Gitignorte *im* Worktree, z. B.
    ``vault/case/*/data/``).

    ``BIBI_JOB_ID`` kommt vom Wrapper: stabil über alle Retries EINES Laufs
    und über die gesamte Lebensdauer eines wiederkehrenden Host-Jobs (nur ein
    interner ``fire``-Zähler zählt pro Dispatch hoch, die Zeilen-ID bleibt).
    Genau das macht diesen Pfad zum richtigen RESET-Angriffspunkt: RESET
    räumt ihn generisch per Glob über alle Subsystem-Ordner auf
    (``job_db.wipe_job_data()``), START rührt ihn nie an (Bibi4 Batch 6,
    User-Entscheidung: "RESET wischt, START bewahrt").

    Ohne ``BIBI_JOB_ID`` (adhoc-Aufruf außerhalb eines echten Jobs) fällt der
    Job-Anteil auf ``"adhoc"`` zurück, statt zu crashen."""
    job_id = os.environ.get("BIBI_JOB_ID", "adhoc")
    d = Path.home() / ".local" / "share" / "bibi" / subsystem / job_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def running() -> None:
    """Job meldet sich als laufend (Optional — Wrapper-Default nach Spawn)."""
    _emit({"name": "running"})


def activity() -> None:
    """Reiner Herzschlag ohne sichtbaren Output — hält den silence_timeout am
    Leben, ohne dass der Job selbst etwas zu loggen hat (z. B. pro HTTP-Request
    einer HITL-App, die während ``awaiting`` sonst nichts mehr ausgibt).

    ── Der App-Vertrag (Entscheidung m.rau, 2026-08-08; #76) ─────────────────

    **Wer läuft, gibt etwas aus.** Jede Zeile auf ``stdout``/``stderr`` und
    jedes BIBI-Signal zählt als Aktivität; der Wrapper schreibt den Zeitpunkt
    nach ``jobs.last_ping_at`` fort, und daraus bildet das FE, wann die
    Silence-Frist abläuft. Ein Job, der nichts sagt, gilt nach
    ``silence_timeout`` als Zombie und wird beendet — das ist die Zusage, nicht
    ein Unfall.

    ⚠ **Ungepuffert ausgeben, oder je Eintrag flushen.** Der Wrapper sieht eine
    Zeile erst, wenn sie seine Pipe erreicht. Puffert eine App blockweise, kann
    sie minutenlang arbeiten und protokollieren, ohne dass eine einzige Zeile
    ankommt — aus Sicht der Frist ist sie in dieser Zeit **stumm** und wird
    abgeräumt, obwohl sie nie stillstand. Python: ``print(..., flush=True)``
    oder ``PYTHONUNBUFFERED=1``. Die Falle ist deshalb tückisch, weil sie erst
    unter Last auffällt: kurze Läufe leeren ihren Puffer beim Beenden.

    **Diese Funktion ist der Weg für alles, was keine Zeile erzeugen soll** —
    eine App, die zwischen zwei Requests wartet, hat nichts zu loggen und ist
    trotzdem am Leben. Wer gar nicht über ``stdout`` reden kann, nimmt den
    Notausgang ``POST /-/job/{id}/ping``; er speist dieselbe Spalte."""
    _emit({"name": "activity"})


def awaiting(input_request: str, *, input_format: str = "text",
             port: int | None = None) -> None:
    """Job wartet auf menschliche Eingabe (HITL)."""
    p: dict = {"name": "awaiting", "input_request": input_request,
               "input_format": input_format}
    if port is not None:
        p["port"] = port
    _emit(p)


def app_register(port: int, prefix: str | None = None) -> None:
    """Job meldet seinen HTTP-Server-Port. Wrapper trägt Traefik-Route ein."""
    p: dict = {"name": "app_register", "port": port}
    if prefix is not None:
        p["prefix"] = prefix
    _emit(p)


class Deferred(Exception):
    """Wirft der Job diese Exception, gilt er als zurückgestellt (nicht gescheitert).

    Beim Instanziieren wird automatisch ein BIBI-Signal emittiert, damit der
    Wrapper den Defer erkennt — auch wenn die Exception unbehandelt bleibt.

    ``seconds`` ist optional: ohne Angabe entscheidet der Wrapper anhand des
    Schedule-Frontmatters (``defer_time:``), das selbst einen globalen Default
    hat — nicht ein hier hartkodierter Wert, der jeden Frontmatter-Wert sonst
    stumm überschreiben würde.

    Beispiel:
        raise bibi.job.Deferred(seconds=300)   # in 5 min neu starten
        raise bibi.job.Deferred()              # Frontmatter- bzw. globaler Default
    """
    def __init__(self, seconds: int | None = None) -> None:
        self.seconds = seconds
        payload: dict = {"name": "deferred"}
        if seconds is not None:
            payload["seconds"] = seconds
        _emit(payload)
        msg = f"deferred for {seconds}s" if seconds is not None else "deferred"
        super().__init__(msg)


class Failed(Exception):
    """Wirft der Job diese Exception, um einen Fehlschlag mit expliziter
    Retry-Wartezeit zu signalisieren — das Pendant zu ``Deferred`` für den
    Fehlerfall (statt der aus Backoff-Strategie + Basiswert berechneten
    Verzögerung zählt dann exakt ``seconds``).

    ``seconds`` ist optional: ohne Angabe verhält sich dies wie jede andere
    unbehandelte Exception — der Wrapper berechnet die Wartezeit aus dem
    Schedule-Frontmatter (``error_time:``), das selbst einen globalen Default
    hat, ggf. skaliert durch die ``backoff:``-Strategie.

    Beispiel:
        raise bibi.job.Failed(seconds=10)   # exakt 10s bis zum nächsten Versuch
        raise bibi.job.Failed()             # Frontmatter- bzw. globaler Default
    """
    def __init__(self, seconds: int | None = None) -> None:
        self.seconds = seconds
        payload: dict = {"name": "failed"}
        if seconds is not None:
            payload["seconds"] = seconds
        _emit(payload)
        msg = f"failed, retry in {seconds}s" if seconds is not None else "failed"
        super().__init__(msg)
