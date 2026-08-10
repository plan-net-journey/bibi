"""#112: der Live-Log ist bei ruhigem System vollstaendig leer — ununterscheidbar
von einem abgerissenen Strom, einem Filter, der zu viel wegnimmt, oder einem
Screen, der nie geladen hat.

Kein Browsertest (die Browser-Ebene ist fuer diese Suite optional), aber auch
kein reiner String-Grep auf den JS-Quelltext: ``tests/assets/log_js_harness.js``
fuehrt ``_LOG_JS`` gegen echte (gestubbte) ``document``/``EventSource``/Timer-
Objekte aus, in Node via ``vm.createContext`` — dieselbe Instanz-Ausfuehrung,
die auch im Browser passiert, nur ohne Rendering. Der Nachweis, dass es *im
Betrieb* auch tatsaechlich sichtbar wird (nicht nur in dieser Simulation),
gehoert in den Akzeptanz-Durchgang."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from bibi.controller.render import _LOG_JS

_HARNESS = Path(__file__).parent / "assets" / "log_js_harness.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="Node.js nicht installiert")


def _run(js_source: str) -> dict:
    proc = subprocess.run(["node", str(_HARNESS)], input=js_source,
                          capture_output=True, text=True, check=True)
    return json.loads(proc.stdout)


def test_log_box_shows_an_idle_marker_after_a_quiet_period():
    result = _run(_LOG_JS)
    assert result == {
        "quiet_but_fresh_shows_nothing": True,
        "idle_marker_appears_after_25s": True,
        "idle_marker_clears_on_message": True,
        "idle_marker_returns_after_activity": True,
    }, result
