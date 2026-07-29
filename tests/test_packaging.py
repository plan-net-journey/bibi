"""Package-Daten, die über reinen Python-Code hinausgehen (PLAN-24 Befund 2).

`docker/bibi-base/Dockerfile` lag ursprünglich als Geschwister-Ordner neben
`bibi/` und wurde bei `pip install`/`uv sync` NICHT mitinstalliert
(`[tool.hatch.build.targets.wheel] packages = ["bibi"]` schließt nur den
Python-Baum ein). Ein Knoten, der `bibi` nur als Abhängigkeit installiert
(kein Editable-Checkout), hatte das Dockerfile zur Laufzeit schlicht nicht —
Auto-Provisioning (Befund 1) liefe ins Leere. Fix: Dockerfile liegt jetzt
unter `bibi/docker/bibi-base/Dockerfile`, also innerhalb des gepackten Baums.
"""
from __future__ import annotations

from pathlib import Path

import bibi


def test_bibi_base_dockerfile_ships_with_the_package():
    dockerfile = Path(bibi.__file__).resolve().parent / "docker" / "bibi-base" / "Dockerfile"
    assert dockerfile.is_file()
    assert "FROM node:20-slim" in dockerfile.read_text(encoding="utf-8")
