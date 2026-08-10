"""Stufe 4.4 — Volle Schedule-Liste (PLAN-4 §4.4, Ebene 2).

Quick-Spalten slug/status/last/next. Die frühere Archiv-Klapp-Logik für
abgelaufene One-shots ist mit PLAN-14 Stufe 14.6 vollständig durch das
Registrierungs-Drei-Gruppen-Modell ersetzt (Aktiv/Archive/Journal).
PLAN-23 Befund 2 verfeinert das nochmal: ein
abgeschlossener oneshot (`at:`) mit noch vorhandener MD landet jetzt NICHT
mehr in „Aktiv", sondern im Archive (nicht mehr erneut startbar, s. Befund
3) — anders als PLAN-14 14.6 das ursprünglich vorsah."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bibi.controller import render
from bibi.daemon import roles
from bibi.daemon.app import create_app


def _sched(slug, *, kind="job", trigger="now", last_status="pending",
           last_run_at=None, last_run_id=None, next_fire_at=None, oneshot=False,
           payload="echo hi", app_port=None, active=True) -> dict:
    return {"slug": slug, "kind": kind, "trigger": trigger,
            "last_status": last_status, "last_run_at": last_run_at,
            "last_run_id": last_run_id, "next_fire_at": next_fire_at,
            "oneshot": oneshot, "payload": payload, "app_port": app_port,
            "active": active}







def test_until_none_is_dash():
    assert render._until(None, 300.0) == "—"


def test_until_future_is_in_x():
    # Seit #122 traegt die Zelle ihren Anker, damit der Browser weiterzaehlen
    # kann. Die Regel selbst steht unveraendert in `_until_text()`.
    assert render._until_text(60) == "in 1 min"
    assert 'data-dur="until" data-at="360.0"' in render._until(360.0, 300.0)


def test_until_past_is_asap():
    assert render._until_text(-200) == "asap"
    assert ">asap<" in render._until(100.0, 300.0)


def test_until_exactly_now_is_asap():
    # ts == now zählt als fällig, nicht als Zukunft (kein "in 0s").
    assert render._until_text(0) == "asap"
    assert ">asap<" in render._until(300.0, 300.0)


# ── Time-Toggle (Bibi4-Iteration, User-Fund: "Time: abs./rel./both") ────────


# ── Route ────────────────────────────────────────────────────────────────────


# ── Stufe 4.x — KIND-Spalte, letzter-Lauf-Status, Self-Poll, Handles ─────────







