"""bibi.job — Signale für Job-Autoren.

Schreibt BIBI:{...}-Zeilen auf stdout. Der Wrapper parst und verarbeitet sie.
"""
import json
import sys


def _emit(payload: dict) -> None:
    print(f"BIBI:{json.dumps(payload, separators=(',', ':'))}", flush=True)


def running() -> None:
    """Job meldet sich als laufend (Optional — Wrapper-Default nach Spawn)."""
    _emit({"name": "running"})


def activity() -> None:
    """Reiner Herzschlag ohne sichtbaren Output — hält den silence_timeout am
    Leben, ohne dass der Job selbst etwas zu loggen hat (z. B. pro HTTP-Request
    einer HITL-App, die während ``awaiting`` sonst nichts mehr ausgibt)."""
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

    Beispiel:
        raise bibi.job.Deferred(seconds=300)   # in 5 min neu starten
    """
    def __init__(self, seconds: int = 60) -> None:
        self.seconds = seconds
        _emit({"name": "deferred", "seconds": seconds})
        super().__init__(f"deferred for {seconds}s")
