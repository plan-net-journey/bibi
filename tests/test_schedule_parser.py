"""MD → ScheduleSpec (DESIGN §5.2/§5.3; PLAN-3 §3.1; PLAN-10 Stufe 10.0)."""

from __future__ import annotations

from pathlib import Path

from bibi.schedule.models import Kind
from bibi.schedule.parser import parse_text

P = Path("vault/case/hello/README.md")


def _parse(text: str, path: Path = P):
    return parse_text(text, schedule_ref=path.as_posix(), path=path)


def test_skip_when_no_trigger():
    r = _parse("---\njob: echo hi\n---\nbody")
    assert r.is_skip and r.spec is None and r.error is None


def test_job_with_cron():
    r = _parse('---\nschedule: "0 9 * * *"\njob: "echo hi"\n---\n')
    assert r.is_ok
    assert r.spec.kind is Kind.JOB
    assert r.spec.payload == "echo hi"
    assert r.spec.schedule == "0 9 * * *"
    assert r.spec.at is None


def test_job_claude_prefix_with_model():
    # `job: claude: <prompt>` — Prefix-Expansion, Kind bleibt JOB.
    r = _parse('---\nschedule: now\njob: "claude: Antworte hallo"\nmodel: claude-haiku-4-5-20251001\n---\n')
    assert r.is_ok
    assert r.spec.kind is Kind.JOB
    assert r.spec.payload == "claude: Antworte hallo"
    assert r.spec.schedule == "now"
    assert r.spec.model == "claude-haiku-4-5-20251001"


def test_job_claude_prefix_default_model():
    r = _parse('---\nschedule: never\njob: "claude: x"\n---\n')
    assert r.is_ok
    assert r.spec.kind is Kind.JOB
    assert r.spec.model == "claude-sonnet-4-6"


def test_old_claude_key_rejected():
    # `claude:` als eigener Frontmatter-Key ist nicht mehr gültig.
    r = _parse('---\nschedule: now\nclaude: "Antworte hallo"\n---\n')
    assert r.is_error
    assert "job:" in r.error


def test_special_schedules_not_cron_validated():
    for val in ("now", "startup", "never"):
        r = _parse(f'---\nschedule: {val}\njob: "echo hi"\n---\n')
        assert r.is_ok, val
        assert r.spec.schedule == val
    # `autostart` wird seit m.rau/bibi#50 beim Parsen zu `startup` aufgelöst,
    # damit alle nachgelagerten Stellen genau einen Wert kennen müssen — siehe
    # test_autostart_is_an_alias_of_startup unten.
    r = _parse('---\nschedule: autostart\njob: "echo hi"\n---\n')
    assert r.is_ok and r.spec.schedule == "startup"


def test_at_iso_normalised_to_naive():
    r = _parse('---\nat: "2026-07-01T09:00:00"\njob: "echo hi"\n---\n')
    assert r.is_ok
    assert r.spec.at.startswith("2026-07-01T09:00")
    assert r.spec.schedule is None


def test_error_both_schedule_and_at():
    r = _parse('---\nschedule: now\nat: "2026-07-01T09:00"\njob: "x"\n---\n')
    assert r.is_error and "genau einen" in r.error


def test_error_bad_cron():
    r = _parse('---\nschedule: "not a cron"\njob: "x"\n---\n')
    assert r.is_error and "cron" in r.error.lower()


def test_error_bad_at():
    r = _parse('---\nat: "not-a-date"\njob: "x"\n---\n')
    assert r.is_error and "at:" in r.error


def test_error_no_type():
    r = _parse('---\nschedule: now\n---\n')
    assert r.is_error and "job:" in r.error


def test_error_multiple_types_now_impossible():
    # Mit nur noch `job:` als Key sind mehrere Typen unmöglich.
    # `claude:` als Key ist kein Typ mehr → kein Error über multiple types, sondern kein Typ.
    r = _parse('---\nschedule: now\nclaude: "y"\n---\n')
    assert r.is_error  # kein job: → kein Typ


def test_error_bad_priority_type():
    r = _parse('---\nschedule: now\njob: "x"\npriority: "high"\n---\n')
    assert r.is_error and "priority" in r.error


def test_bool_priority_rejected():
    r = _parse('---\nschedule: now\njob: "x"\npriority: true\n---\n')
    assert r.is_error and "bool" in r.error


def test_error_bad_backoff():
    r = _parse('---\nschedule: now\njob: "x"\nbackoff: wild\n---\n')
    assert r.is_error and "backoff" in r.error


# Die Slug-Ableitung steht seit #143 vollständig in `test_slug_from_filename.py`.
# Hier standen bis `v0.8.2` drei Tests für drei Stufen — Override gewinnt,
# README nimmt den Ordnernamen, sonst der Stamm. Es gibt nur noch eine Regel,
# und sie gehört an einen Ort: zwei Testdateien für dieselbe Aussage wären
# genau die Doppelung, die eine Regel wieder verhandelbar macht.


def test_slug_from_stem_for_named_file():
    r = _parse('---\nschedule: now\njob: "x"\n---\n', path=Path("vault/case/daily.md"))
    assert r.spec.slug == "daily"


def test_lifecycle_knobs_parsed():
    r = _parse('---\nschedule: now\njob: "x"\nattempts: 3\nwall_time: 30\nsilence_timeout: 60\nbackoff: exponential\n---\n')
    assert r.spec.attempts == 3
    assert r.spec.wall_time == 30
    assert r.spec.silence_timeout == 60
    assert r.spec.backoff == "exponential"


def test_defer_time_and_error_time_parsed():
    # error_time ist das Pendant zu defer_time für den Fehlerfall (Retry-
    # Backoff-Basis, s. job.py Failed(seconds=…)) — beide optional, None
    # ohne Frontmatter-Angabe.
    r = _parse('---\nschedule: now\njob: "x"\ndefer_time: 15\nerror_time: 10\n---\n')
    assert r.spec.defer_time == 15
    assert r.spec.error_time == 10


def test_defer_time_and_error_time_default_none():
    r = _parse('---\nschedule: now\njob: "x"\n---\n')
    assert r.spec.defer_time is None
    assert r.spec.error_time is None


def test_wall_time_default_none():
    # Bibi4-Iteration, User-Fund: "wall_time Default muss doch None sein ...
    # sonst laufen Apps in die Default Wall Time und wir wollen bei Apps den
    # Zombie Status (48h) verwenden" — wall_time ist jetzt genau wie
    # defer_time/error_time ein None-Sentinel (Praesenz-basiert), nicht mehr
    # IMMER gecoerct. Betraf zuvor JEDEN Kind-Typ gleichermassen (keine
    # app_port-Ausnahme wie bei silence_timeout) und killte eine App ohne
    # explizites wall_time: nach 1h, obwohl sie aktiv Output produzierte.
    r = _parse('---\nschedule: now\njob: "x"\n---\n')
    assert r.spec.wall_time is None


def test_defer_max_still_has_real_default():
    # defer_max behaelt seinen eigenstaendigen globalen Default (unveraendert,
    # anders als wall_time jetzt) und wird wie silence_timeout IMMER gecoerct.
    from bibi.schedule.models import DEFAULT_DEFER_MAX
    r = _parse('---\nschedule: now\njob: "x"\n---\n')
    assert r.spec.defer_max == DEFAULT_DEFER_MAX == 1200


def test_wall_time_and_defer_max_explicit_overrides_win():
    r = _parse('---\nschedule: now\njob: "x"\nwall_time: 5\ndefer_max: 7\n---\n')
    assert r.spec.wall_time == 5
    assert r.spec.defer_max == 7


def test_silence_timeout_default_1h_for_claude_payload():
    # User-Feedback 2026-07-04: silence_timeout/hitl_timeout zusammengelegt —
    # claude-Payloads (Batch, kein HITL) bekommen den kurzen Default.
    r = _parse('---\nschedule: now\njob: "claude: x"\n---\n')
    assert r.spec.silence_timeout == 3600


def test_silence_timeout_default_2h_for_plain_job_payload():
    # PLAN-31 Befund 4 (2026-07-17): ein einfacher Job ohne App-Marker
    # bekommt den kurzen Job-Default — vorher fiel er fälschlich auf denselben
    # 48h-Default wie eine echte App zurück, ein hängender Job blieb dadurch
    # bis zu 48h unbemerkt statt zeitnah als Zombie aufzufallen.
    r = _parse('---\nschedule: now\njob: "echo hi"\n---\n')
    assert r.spec.silence_timeout == 2 * 3600


def test_silence_timeout_default_48h_for_app_payload():
    # Echte Apps (`app_port` gesetzt, long-lived, HITL-fähig über run_app)
    # bekommen weiterhin den langen Default — ein Mensch darf bis zu 48h für
    # seine Eingabe brauchen.
    r = _parse('---\nschedule: now\njob: "echo hi"\napp_port: 9100\n---\n')
    assert r.spec.silence_timeout == 48 * 3600


def test_silence_timeout_default_48h_for_app_prefix_payload():
    # app_prefix allein (ohne app_port) muss denselben App-Default auslösen.
    r = _parse('---\nschedule: now\njob: "echo hi"\napp_prefix: "/hitl/"\n---\n')
    assert r.spec.silence_timeout == 48 * 3600


def test_silence_timeout_explicit_overrides_kind_default():
    r = _parse('---\nschedule: now\njob: "claude: x"\nsilence_timeout: 120\n---\n')
    assert r.spec.silence_timeout == 120


def test_docker_args_parses_string_list():
    r = _parse('---\nschedule: now\njob: "echo hi"\ndocker_args:\n  - "--network"\n'
                '  - "gitea_default"\n---\n')
    assert r.is_ok
    assert r.spec.docker_args == ["--network", "gitea_default"]


def test_docker_args_absent_by_default():
    r = _parse('---\nschedule: now\njob: "echo hi"\n---\n')
    assert r.spec.docker_args is None


def test_docker_args_rejects_non_list():
    r = _parse('---\nschedule: now\njob: "echo hi"\ndocker_args: "--privileged"\n---\n')
    assert r.error is not None and "docker_args" in r.error


def test_docker_args_rejects_non_string_entries():
    r = _parse('---\nschedule: now\njob: "echo hi"\ndocker_args:\n  - 8780\n---\n')
    assert r.error is not None and "docker_args" in r.error


# --- Trigger-Schreibweisen (m.rau/bibi#50) -----------------------------------
#
# Entscheidung m.rau 2026-07-31: „geht beides, autostart und startup. Genauso
# wie never und "" oder "~" auch gehen sollte. Und es wäre schön, wenn beim
# Parsen alles nach '#' ignoriert wird."


def test_autostart_is_an_alias_of_startup():
    """`autostart` stand seit jeher in SPECIAL_SCHEDULES und wurde deshalb
    klaglos angenommen — ausgewertet wurde nur `startup` (`job_db.py`). Ein Job
    mit diesem Wert startete also nie und sagte auch nicht warum: die stillste
    Fehlerklasse, die es gibt. Verschärfend, weil `DESIGN.md` §5.3 ihn
    ausdrücklich für langlaufende Server empfiehlt."""
    r = _parse("---\nschedule: autostart\njob: echo hi\n---\n")
    assert r.is_ok and r.spec.schedule == "startup"


def test_empty_and_tilde_read_as_never():
    """Beide liefen bisher in den Fehlerausgang („muss ein nicht-leerer String
    sein") — und genau dieser Ausgang wird benutzt, um einen Job aus der
    Discovery zu nehmen, ohne die MD zu löschen. Als `never` wird aus dem
    Versteck eine Aussage: `doctor` und der Schedules-Screen zeigen Absicht
    statt Defekt, und was in der Fehlerliste bleibt, sind wirklich Fehler."""
    for value in ('""', "~", "''"):
        r = _parse(f"---\nschedule: {value}\njob: echo hi\n---\n")
        assert r.is_ok, f"{value!r} sollte gültig sein"
        assert r.spec.schedule == "never", value


def test_a_comment_after_the_cron_expression_is_ignored():
    """Ein Cron-Ausdruck darf seine Erklärung tragen."""
    r = _parse("---\nschedule: \"0 * * * *  # stündlich zur vollen Stunde\"\njob: echo hi\n---\n")
    assert r.is_ok, r.error
    assert r.spec.schedule.strip() == "0 * * * *"


def test_a_comment_after_a_special_value_is_ignored_too():
    r = _parse("---\nschedule: \"never  # entwaffnet, siehe README\"\njob: echo hi\n---\n")
    assert r.is_ok and r.spec.schedule == "never"


def test_a_comment_only_value_is_still_an_error():
    """Nur ein Kommentar ist kein Trigger — nach dem Abschneiden bleibt nichts,
    und „nichts" ist hier nicht `never`, sondern eine unvollständige Angabe.
    Der Unterschied zu `""` ist Absicht: der leere Wert ist eine Aussage, ein
    weggeschnittener Kommentar ein Versehen."""
    r = _parse("---\nschedule: \"# nur ein Kommentar\"\njob: echo hi\n---\n")
    assert r.is_error


def test_the_comment_rule_does_not_touch_the_payload():
    """Nur der Trigger. Eine Ebene höher frässe die Regel Rauten in Prompts,
    Pfaden und docker_args."""
    r = _parse('---\nschedule: never\njob: "echo \'#hashtag\'"\n---\n')
    assert r.is_ok and "#hashtag" in r.spec.payload


# --- bibi5: „wird gerufen" gegen „ruht" (m.rau/bibi#104, #105) ---------------
#
# Beide feuern nicht von selbst — der Unterschied ist die Absicht, und sie
# entscheidet über die Sichtbarkeit: `adhoc` steht immer im Jobs-Screen,
# `never` steht dort gar nicht. Damit hat jede der drei Absichten genau eine
# Schreibweise: „läuft nach Plan" = Cron/`startup`, „wird gerufen" = `adhoc`,
# „ruht" = `never`/leer/`~`/`-`.


def test_adhoc_is_an_alias_of_on_demand():
    """`on_demand` beschreibt die Mechanik, `adhoc` die Absicht — und im
    Jobs-Screen steht die Absicht. Aufgelöst wird beim Parsen, damit alle
    nachgelagerten Stellen weiterhin genau einen Wert kennen müssen; derselbe
    Weg wie bei `autostart` → `startup`.

    Der Bindestrich-Schreibweise `ad-hoc` wird mit aufgenommen, weil sie sich
    beim Tippen von selbst ergibt und ein Fehler dafür nichts erklären würde."""
    for value in ("adhoc", "ad-hoc"):
        r = _parse(f"---\nschedule: {value}\njob: echo hi\n---\n")
        assert r.is_ok, f"{value!r} sollte gültig sein: {r.error}"
        assert r.spec.schedule == "on_demand", value


def test_dash_reads_as_never():
    """`-` ist die fünfte Schreibweise für „ruht" und die einzige, die heute
    noch in den Fehlerausgang läuft — live in fünf Dateien mit `"- # never"`,
    die bei jedem Rescan einen cron-Fehler werfen. Das ist derselbe
    Versteck-Trick, den m.rau/bibi#50 für `""` und `~` bereits legitimiert hat:
    wer eine MD aus dem Blick nehmen will, soll das sagen können, ohne dass
    daraus ein Defekt wird.

    Gequotet werden muss er in YAML ohnehin (`schedule: "-"`), sonst liest der
    Parser einen Listenanfang."""
    for value in ('"-"', '"- # never"', '"-  "'):
        r = _parse(f"---\nschedule: {value}\njob: echo hi\n---\n")
        assert r.is_ok, f"{value!r} sollte gültig sein: {r.error}"
        assert r.spec.schedule == "never", value
