"""Die Container-Tests müssen ihre Voraussetzung messen, nicht raten (m.rau/bibi#86).

Beide Container-Tests setzen auf **echter UID-Durchreichung in Bind-Mounts**
auf: sie starten mit ``--user <host-uid>:0`` und erwarten, im gemounteten
Verzeichnis schreiben zu können. Das ist keine Eigenschaft des Repos, sondern
der Docker-Installation — und ihr ``skipif`` prüfte bisher nur, *ob* Docker
läuft.

**Wie sich das zeigte:** am 2026-07-31 scheiterten beide Tests auf dem Mac
(``bash: line 1: sudo_out.txt: Permission denied``), am 2026-08-01 waren sie
grün — ohne dass ein einziger Commit die Tests oder ``exec_backend`` berührt
hätte. Geändert hat sich Docker Desktop, nicht der Code.

Genau das ist der Grund für diese Prüfung: eine Voraussetzung, die sich ohne
Zutun ändert, muss gemessen werden. Sonst ist die Suite heute grün, morgen rot
und in beiden Fällen ohne Aussage.
"""

from __future__ import annotations

from pathlib import Path

from tests._docker import container_skip_reason


def test_reason_is_none_or_a_sentence():
    """Entweder es geht — oder es steht da, warum nicht."""
    reason = container_skip_reason()
    assert reason is None or (isinstance(reason, str) and reason.strip())


def test_missing_binary_is_named_not_crashed(monkeypatch, tmp_path: Path):
    """Ein fehlendes docker darf nie eine Exception aus der Sammlung werfen.

    Der ``skipif``-Ausdruck wird beim Einsammeln ausgewertet — wirft er, ist
    nicht ein Test rot, sondern die ganze Datei nicht einsammelbar.
    """
    reason = container_skip_reason(docker_bin=str(tmp_path / "gibt-es-nicht"))
    assert reason is not None
    assert "docker" in reason.lower()


def test_reason_mentions_the_capability_when_passthrough_fails(monkeypatch):
    """Scheitert die Durchreichung, muss der Grund sie benennen.

    „kein Docker" wäre hier gelogen — Docker läuft ja. Wer das liest, soll
    wissen, dass es an der Mount-Semantik hängt und nicht an der Installation.
    """
    import tests._docker as mod
    monkeypatch.setattr(mod, "_probe_uid_passthrough", lambda *a, **k: False)
    monkeypatch.setattr(mod, "_probe_docker", lambda *a, **k: True)
    monkeypatch.setattr(mod, "_probe_image", lambda *a, **k: True)
    reason = container_skip_reason(cached=False)
    assert reason is not None
    assert "uid" in reason.lower() or "durchreich" in reason.lower()
