"""Was die Engine in die Umgebung jedes Jobs schreibt (m.rau/bibi#89, #90).

Zwei Altlasten in derselben Zeile Code, beide klein und beide taeglich
sichtbar.

``VIRTUAL_ENV`` (#89) reist ueber ``os.environ.copy()`` in jeden Job. Den Zweck
aus `#76` — dass ein Job das venv der Engine findet — traegt der ``PATH``
allein; die Variable war die Zugabe. Sie kostet drei Warnungen pro
``BrowserCI``-Lauf, weil jedes ``uv`` in einem fremden Checkout sie meldet, und
jede sieht bei einer Fehlersuche nach einer Spur aus, die keine ist.

``BIBI_WORKER_NAME`` (#90) ist derselbe Schluessel, den ``doctor`` als
``legacy-node-name`` anmahnt, wenn ein Mensch ihn in seine Config setzt — und
**die Engine schreibt ihn selbst in jeden Job**. PLAN-34 hat den
Konfigurations-Schluessel umbenannt und die Laufzeit-Variable unbenannt
gelassen. Betroffen ist nur das geschlossene Paar ``worker.py`` →
``wrapper/__init__.py``, dessen beide Seiten aus demselben Release laufen; die
drei Fallback-Stellen in ``hygiene.py``, ``node_info.py`` und ``daemon_cmd.py``
bleiben unangetastet, sie sind Bestandskompatibilitaet und keine Altlast.
"""

from __future__ import annotations

import os

import pytest

from bibi.daemon import worker


def test_the_job_does_not_inherit_the_session_virtualenv(monkeypatch):
    """**Der Rot-Schritt von #89.**

    Drei Warnungen pro ``BrowserCI``-Lauf, jede davon eine falsche Spur."""
    monkeypatch.setenv("VIRTUAL_ENV", "/pfad/zu/fremdem/venv")
    env = worker.base_job_env()
    assert "VIRTUAL_ENV" not in env, (
        "der Job erbt VIRTUAL_ENV der Sitzung — jedes uv in einem fremden "
        "Checkout warnt darueber (#89)")


def test_the_path_still_carries_the_engine_venv(monkeypatch):
    """Die Gegenprobe: der Zweck aus `#76` bleibt.

    Ohne sie waere der Fix oben auch dann gruen, wenn der Job das venv der
    Engine gar nicht mehr faende — und genau das war `#76`s Anliegen. Der
    ``PATH`` traegt ihn, nicht ``VIRTUAL_ENV``; die Variable war die Zugabe."""
    monkeypatch.setenv("PATH", "/venv/bin:/usr/bin")
    assert "/venv/bin" in worker.base_job_env().get("PATH", "")


def test_the_rest_of_the_environment_is_untouched(monkeypatch):
    """Und sonst bleibt alles stehen: ein Job braucht ``HOME``, ``PATH`` und
    die verteilten Credentials. Gestrichen wird eine Variable, nicht eine
    Klasse davon."""
    monkeypatch.setenv("BIBI_JOB_ENV_TESTWERT", "geheim")
    env = worker.base_job_env()
    assert env.get("BIBI_JOB_ENV_TESTWERT") == "geheim"
    assert env.get("PATH")


def test_the_node_name_travels_under_its_current_key(team_repo, monkeypatch):
    """**Der Rot-Schritt von #90.**

    Die Engine schrieb denselben Schluessel in jeden Job, den ``doctor`` als
    ``legacy-node-name`` anmahnt, sobald ein Mensch ihn in seine Config setzt.
    PLAN-34 hat den Konfigurations-Schluessel umbenannt und die
    Laufzeit-Variable unbenannt gelassen."""
    quelle = (worker.__file__ and
              __import__("pathlib").Path(worker.__file__).read_text(encoding="utf-8"))
    assert 'env["BIBI_NODE_NAME"] = worker_name' in quelle, (
        "die Engine schreibt den Namen weiterhin unter dem Altlast-Schluessel "
        "(#90)")
    assert 'env["BIBI_WORKER_NAME"]' not in quelle


def test_the_wrapper_finds_the_name_under_the_new_key(tmp_path, monkeypatch):
    """Die Gegenprobe zu #90: die Empfaengerseite muss mitziehen.

    Das Paar ist geschlossen — ``worker.py`` schreibt, ``wrapper`` liest, beide
    Seiten laufen aus demselben Release. Zoege nur eine mit, verloere jeder
    Statusbericht seinen Absender."""
    from bibi import wrapper
    gesehen: dict = {}

    def _fang(url_base, job_id, **kw):
        gesehen.update(kw)
        return None

    monkeypatch.setattr(wrapper, "_post_status", _fang)
    wrapper._report_terminal(
        {"BIBI_JOB_ID": "j1", "BIBI_NODE_NAME": "knoten-1",
         "BIBI_SCHEDULER_URL": "http://x"},
        status="complete")
    assert gesehen.get("worker") == "knoten-1", (
        "der Wrapper liest den Namen weiterhin nur unter dem alten "
        "Schluessel — der Statusbericht verliert seinen Absender (#90)")
