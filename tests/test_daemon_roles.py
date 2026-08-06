"""Rollen-Auflösung & Invarianten des Daemons (DESIGN §4.2, PLAN-2 §2.1)."""

from __future__ import annotations

from bibi.daemon import roles


def test_parse_role_env_splits_and_trims():
    assert roles.parse_role_env("worker, synchronizer") == {"worker", "synchronizer"}
    assert roles.parse_role_env("") == set()
    assert roles.parse_role_env("  scheduler ") == {"scheduler"}


def test_parse_role_env_ignores_unknown():
    # Unbekannte Tokens werden verworfen (defensiv), bekannte bleiben.
    assert roles.parse_role_env("worker,bogus") == {"worker"}


def test_resolve_synchronizer_defaults_to_pull():
    r = roles.resolve({"synchronizer"})
    assert r.synchronizer and not r.scheduler and not r.worker
    assert r.pull and not r.push  # ohne --push: nur Pull (uni-direktional, §4.3)


def test_resolve_push_implies_pull():
    r = roles.resolve({"synchronizer"}, push=True)
    assert r.push and r.pull  # --push schließt --pull ein (§4.3)


def test_resolve_connect_modifier():
    r = roles.resolve({"synchronizer"}, connect=True)
    assert r.connect


def test_validate_scheduler_excludes_connect():
    errs = roles.validate(roles.resolve({"scheduler"}, connect=True))
    assert any("connect" in e.lower() for e in errs)


def test_validate_clean_synchronizer_has_no_errors():
    assert roles.validate(roles.resolve({"synchronizer"})) == []


def test_all_roles_startable():
    # Ab Stufe 3.6 ist auch der connect-Modifikator startbar (Worker-Verbund).
    assert roles.unsupported(roles.resolve({"scheduler"})) == []
    assert roles.unsupported(roles.resolve({"worker"})) == []
    assert roles.unsupported(roles.resolve({"synchronizer"})) == []
    assert roles.unsupported(roles.resolve({"worker"}, connect=True)) == []


def test_no_role_is_rejected():
    # Bis zum 2026-08-06 hieß dieser Test ``test_no_role_is_valid_but_idle`` und
    # behauptete das Gegenteil. Er kodierte das Modell vor der Entscheidung von
    # m.rau am 2026-08-05 (m.rau/bibi#163): „Es dürfte keinen Node jemals geben,
    # der nicht die Synchronizer-Rolle hat." Ein Daemon ohne jede Rolle ist damit
    # kein Leerlauf, sondern eine Fehlkonfiguration.
    r = roles.resolve(set())
    assert any("synchronizer" in e.lower() for e in roles.validate(r))


# --- m.rau/bibi#163: die synchronizer-Invariante steht in der Prüfung ---------


def test_validate_rejects_roles_without_synchronizer():
    # Getragen wurde die Regel bisher allein vom Default in config.py — wer
    # BIBI_ROLE ausdrücklich anders setzt, kam ohne Beschwerde durch.
    errs = roles.validate(roles.resolve({"worker"}))
    assert any("synchronizer" in e.lower() for e in errs)


def test_validate_accepts_worker_with_synchronizer():
    assert roles.validate(roles.resolve({"worker", "synchronizer"})) == []


# --- m.rau/bibi#174: Profile als Eingabe, Rollen als Innenleben --------------


def test_profile_client_is_synchronizer_and_controller():
    assert roles.profile_roles("client") == "synchronizer,controller"


def test_profile_worker_has_no_controller():
    # Ein Worker führt nichts aus, was jemand ansieht — eine Oberfläche dort
    # wäre ein zweiter Ort zum Nachsehen ohne Inhalt.
    assert roles.profile_roles("worker") == "synchronizer,worker"


def test_profile_scheduler_has_no_controller():
    # Entscheidung m.rau, 2026-08-06 (Abnahme des v0.7.2-Plans): der Scheduler
    # ist Backend. sarasate hat die Rolle am 2026-08-04 aus demselben Grund
    # abgegeben.
    assert roles.profile_roles("scheduler") == "synchronizer,scheduler"


def test_profile_scheduler_worker_carries_both():
    assert roles.profile_roles("scheduler+worker") == "synchronizer,scheduler,worker"


def test_with_ui_adds_the_controller_to_a_scheduler():
    # Der Erstknoten eines Teams: ohne Client sähe sonst niemand etwas.
    assert roles.profile_roles("scheduler", with_ui=True) == \
        "synchronizer,scheduler,controller"
    assert roles.profile_roles("scheduler+worker", with_ui=True) == \
        "synchronizer,scheduler,worker,controller"


def test_with_ui_on_a_client_changes_nothing():
    # Ein Client trägt controller ohnehin — das Flag ist idempotent, nicht ein
    # Fehler. Wer es aus Gewohnheit mitgibt, bekommt keinen Abbruch.
    assert roles.profile_roles("client", with_ui=True) == roles.profile_roles("client")


def test_every_profile_contains_synchronizer():
    # Die Invariante aus #163 ist genau das, was aus 32 rechnerischen
    # Kombinationen vier sinnvolle macht — sie muss für jedes Profil gelten,
    # sonst widerspricht das Mapping der Prüfung, die es benutzt.
    for name in roles.PROFILES:
        assert "synchronizer" in roles.profile_roles(name).split(",")


def test_every_profile_passes_validate():
    for name in roles.PROFILES:
        active = roles.parse_role_env(roles.profile_roles(name))
        assert roles.validate(roles.resolve(active)) == [], name


def test_unknown_profile_raises_and_names_the_known_ones():
    try:
        roles.profile_roles("host")
    except ValueError as exc:
        assert "host" in str(exc)
        assert "scheduler+worker" in str(exc)   # die Liste steht in der Meldung
    else:
        raise AssertionError("unbekanntes Profil muss abgelehnt werden")


def test_profile_connect_says_what_follows_from_the_profile():
    # `connect` ist keine Vorliebe, sondern folgt aus der Knotenart: ein
    # Scheduler darf es nie tragen (Invariante), ein Worker ohne Scheduler hat
    # niemanden, der ihm Aufträge gibt.
    assert roles.PROFILE_CONNECT["scheduler"] == "never"
    assert roles.PROFILE_CONNECT["scheduler+worker"] == "never"
    assert roles.PROFILE_CONNECT["worker"] == "required"
    assert roles.PROFILE_CONNECT["client"] == "optional"


def test_profile_connect_covers_every_profile():
    assert set(roles.PROFILE_CONNECT) == set(roles.PROFILES)
