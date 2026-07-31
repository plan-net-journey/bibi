"""Das Versionsfeld im Nodes-Screen — Fehlerbericht m.rau, 2026-07-31.

„unzulässiger Ref: ''", obwohl im Feld ``v0.3.0`` stand. Ursache: die Route
deklarierte ``version: str = ""``, und ein einfacher Default ist in FastAPI ein
**Query**-Parameter — htmx packt die Werte von ``hx-include`` bei einem POST
aber in den **Body**. Der Wert kam nie an; das Feld hat nie funktioniert.

Dass es niemandem auffiel, hat einen benennbaren Grund: die vorhandenen Tests
prüften nur das gerenderte HTML (``'hx-post="…" in html``), nie einen echten
POST. Diese Datei schließt genau diese Lücke — sie schickt, was der Browser
schickt.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bibi import repo
from bibi.controller import render
from bibi.daemon import deploy, roles
from bibi.daemon.app import create_app


@pytest.fixture()
def ui(team_repo: Path, monkeypatch: pytest.MonkeyPatch):
    """Nodes-Screen ohne echten git/uv-Zugriff — hier steht der Transportweg
    des Werts auf dem Prüfstand, nicht das Setzen selbst."""
    seen: list[str] = []
    monkeypatch.setattr(deploy, "set_expected_version",
                        lambda ref, *a, **kw: (seen.append(ref),
                                               {"ok": True, "changed": False,
                                                "ref": ref, "note": "ok"})[1])
    monkeypatch.setattr(deploy, "available_refs", lambda *a, **kw: [])
    app = create_app(roles.resolve({"controller", "scheduler"}))
    with TestClient(app) as c:
        yield c, seen


# ── Der Fehler ──────────────────────────────────────────────────────────────


def test_version_arrives_from_the_body(ui):
    """Genau das, was htmx bei ``hx-post`` + ``hx-include`` schickt."""
    c, seen = ui
    r = c.post("/-/ui/clients/expected-version", data={"version": "v0.4.0"})
    assert r.status_code == 200
    assert seen == ["v0.4.0"]


def test_version_from_the_query_still_works(ui):
    # Fallback, damit ein Aufruf per curl beide Wege kennt.
    c, seen = ui
    c.post("/-/ui/clients/expected-version", params={"version": "v0.4.0"})
    assert seen == ["v0.4.0"]


def test_body_wins_over_query(ui):
    c, seen = ui
    c.post("/-/ui/clients/expected-version?version=v0.1.0",
           data={"version": "v0.4.0"})
    assert seen == ["v0.4.0"]


def test_deploy_flag_stays_in_the_query(ui):
    # Der Knopf „Setzen + Ausrollen" trägt ?deploy=true in der URL, der Wert im
    # Body — beide Wege müssen nebeneinander funktionieren.
    c, seen = ui
    r = c.post("/-/ui/clients/expected-version?deploy=true",
               data={"version": "v0.4.0"})
    assert r.status_code == 200
    assert seen == ["v0.4.0"]


def test_whitespace_is_stripped(ui):
    c, seen = ui
    c.post("/-/ui/clients/expected-version", data={"version": "  v0.4.0  "})
    assert seen == ["v0.4.0"]


def test_an_empty_field_still_reports_the_error(ui):
    # Leer bleibt leer — dann ist „unzulässiger Ref" die richtige Antwort und
    # nicht mehr ein Symptom des Transportwegs.
    c, seen = ui
    c.post("/-/ui/clients/expected-version", data={"version": ""})
    assert seen == [""]


def test_a_post_without_a_body_does_not_crash(ui):
    c, seen = ui
    r = c.post("/-/ui/clients/expected-version")
    assert r.status_code == 200
    assert seen == [""]


# ── Die Auswahlliste ────────────────────────────────────────────────────────


@pytest.fixture
def fake_ls_remote(team_repo: Path, monkeypatch: pytest.MonkeyPatch):
    """``git ls-remote`` ersetzen, ohne git im Rest des Prozesses lahmzulegen.

    Ersetzt wird der **Name** ``subprocess`` innerhalb von ``deploy``, nicht
    dessen ``run``-Attribut: letzteres ist dasselbe Modulobjekt, das auch
    ``repo._root_of()`` benutzt — ein Patch darauf nimmt der Test-Suite ihr git
    (zweimal in dieser Sitzung so hineingelaufen).
    """
    calls: list[dict] = []

    import subprocess as _sp

    class _Shim:
        stdout = ""
        raises = None
        #: Der Produktionscode fängt ``subprocess.SubprocessError`` — der Shim
        #: muss dieselbe Klasse tragen, sonst prüft der Test einen Fehlerpfad,
        #: den es so nicht gibt.
        SubprocessError = _sp.SubprocessError
        TimeoutExpired = _sp.TimeoutExpired

        @classmethod
        def run(cls, *_a, **kw):
            calls.append(kw)
            if cls.raises:
                raise cls.raises
            return _LsRemote(cls.stdout)

    repo.root()  # lru_cache füllen, solange git noch erreichbar ist
    monkeypatch.setattr(deploy, "subprocess", _Shim)
    _Shim.calls = calls
    return _Shim


def test_refs_are_sorted_newest_first(fake_ls_remote, team_repo: Path):
    _write_dep(team_repo, "http://sarasate:3000/m.rau/bibi.git", "v0.3.0")
    fake_ls_remote.stdout = ("a\trefs/tags/v0.2.0\n"
                             "b\trefs/tags/v0.10.0\n"
                             "c\trefs/tags/v0.9.0\n"
                             "d\trefs/tags/v0.3.0\n")
    # v0.10.0 gehört ÜBER v0.9.0 — eine alphabetische Liste hätte genau hier
    # den ersten Fehler gemacht.
    assert deploy.available_refs(force=True) == [
        "v0.10.0", "v0.9.0", "v0.3.0", "v0.2.0"]


def test_non_version_tags_go_last(fake_ls_remote, team_repo: Path):
    _write_dep(team_repo, "http://sarasate:3000/m.rau/bibi.git", "v0.3.0")
    fake_ls_remote.stdout = "a\trefs/tags/v0.2.0\nb\trefs/tags/latest\n"
    assert deploy.available_refs(force=True) == ["v0.2.0", "latest"]


def test_refs_are_cached(fake_ls_remote, team_repo: Path):
    # Der Nodes-Screen rendert bei jedem Heartbeat neu — ein ls-remote pro
    # Durchlauf wäre eine Netzwerkrunde für eine Liste, die sich pro Release
    # einmal ändert.
    _write_dep(team_repo, "http://sarasate:3000/m.rau/bibi.git", "v0.3.0")
    fake_ls_remote.stdout = "a\trefs/tags/v0.2.0\n"
    deploy.available_refs(force=True, now=1000.0)
    deploy.available_refs(now=1010.0)
    deploy.available_refs(now=1200.0)
    assert len(fake_ls_remote.calls) == 1
    deploy.available_refs(now=2000.0)   # TTL abgelaufen
    assert len(fake_ls_remote.calls) == 2


def test_refs_survive_an_unreachable_remote(fake_ls_remote, team_repo: Path):
    # Die Liste ist Komfort; ein unerreichbares Remote darf den Screen nicht
    # kosten.
    _write_dep(team_repo, "http://gibt-es-nicht:3000/x.git", "v0.3.0")
    fake_ls_remote.raises = OSError("weg")
    assert deploy.available_refs(force=True) == []


def test_refs_without_a_dependency_url(fake_ls_remote, team_repo: Path):
    assert deploy.available_refs(force=True) == []
    assert fake_ls_remote.calls == []   # gar nicht erst losgelaufen


def test_ls_remote_never_asks_for_a_password(fake_ls_remote, team_repo: Path):
    # Ohne GIT_TERMINAL_PROMPT=0 bliebe der Aufruf auf einem Knoten ohne
    # Zugangsdaten an einer Passwortabfrage hängen — und der Screen mit ihm.
    _write_dep(team_repo, "http://sarasate:3000/m.rau/bibi.git", "v0.3.0")
    deploy.available_refs(force=True)
    kw = fake_ls_remote.calls[0]
    assert kw["env"]["GIT_TERMINAL_PROMPT"] == "0"
    assert kw["timeout"] > 0


def test_dependency_url_is_read_from_the_pin(team_repo: Path):
    _write_dep(team_repo, "http://sarasate:3000/m.rau/bibi.git", "v0.3.0")
    assert deploy.dependency_url() == "http://sarasate:3000/m.rau/bibi.git"
    assert deploy.current_ref() == "v0.3.0"


# ── Die Darstellung ─────────────────────────────────────────────────────────


def test_form_offers_a_datalist(team_repo: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(deploy, "available_refs",
                        lambda *a, **kw: ["v0.4.0", "v0.3.0"])
    html = render._expected_version_form(None)
    assert 'list="engine-refs"' in html
    assert '<datalist id="engine-refs">' in html
    assert '<option value="v0.4.0">' in html
    # Weiterhin ein freies Textfeld — sonst ginge das Branch-Pinning verloren.
    assert '<input name="version"' in html
    assert "<select" not in html


def test_form_without_refs_has_no_empty_datalist(team_repo: Path,
                                                 monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(deploy, "available_refs", lambda *a, **kw: [])
    html = render._expected_version_form(None)
    assert "datalist" not in html
    assert '<input name="version"' in html


class _LsRemote:
    def __init__(self, stdout: str) -> None:
        self.stdout = stdout
        self.stderr = ""
        self.returncode = 0


def _write_dep(root: Path, url: str, ref: str) -> None:
    (root / "pyproject.toml").write_text(
        '[project]\nname = "t"\nversion = "0.0.0"\ndependencies = [\n'
        f'  "bibi[daemon] @ git+{url}@{ref}",\n]\n', encoding="utf-8")
