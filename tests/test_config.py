"""Tests für bibi.config (~/.config/bibi/env-IO)."""

from __future__ import annotations

from pathlib import Path

import pytest

from bibi import config, repo


# ── m.rau/bibi#52: eine Konfiguration gehört zu einem Repo ──────────────────
#
# Vorher: ``BIBI_CONFIG_PATH`` > ``XDG_CONFIG_HOME`` > ``~/.config``. Drei Stufen,
# von denen die erste für die häufigste Knotenart keinen Träger hatte — ein
# Client bekommt per m.rau/bibi#180 keine Unit, in der die Variable stehen könnte.
# Ein zweites Team-Repo auf derselben Maschine ließ sich damit konfigurieren,
# aber nicht betreiben: der Daemon las beim Start die Konfiguration des ersten.


def test_env_path_is_repo_local(cfg_home: Path):
    assert config.env_path() == cfg_home / "data" / "env"


def test_env_path_ignores_xdg(cfg_home: Path, monkeypatch: pytest.MonkeyPatch):
    """``XDG_CONFIG_HOME`` ist keine Stufe mehr, auch nicht als Fallback."""
    monkeypatch.setenv("XDG_CONFIG_HOME", "/woanders")
    assert config.env_path() == cfg_home / "data" / "env"


def test_env_path_ignores_legacy_override(cfg_home: Path, monkeypatch: pytest.MonkeyPatch):
    """``BIBI_CONFIG_PATH`` ist ersatzlos entfallen und darf nicht nachwirken.

    Eine Variable, die irgendwo noch in einer Unit oder einem Profil steht, würde
    den Knoten sonst still an seiner eigenen Konfiguration vorbeilenken — genau
    die Klasse Fehler, gegen die diese Änderung angetreten ist.
    """
    monkeypatch.setenv("BIBI_CONFIG_PATH", "/woanders/env")
    assert config.env_path() == cfg_home / "data" / "env"


def test_distributed_env_path_follows(cfg_home: Path):
    """Das Host-Bundle wohnt neben der Konfiguration desselben Knotens."""
    assert config.distributed_env_path() == cfg_home / "data" / "distributed-env"


def test_read_env_outside_repo_is_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Ohne Repo gibt es keinen Knoten — und damit keine Konfiguration.

    Für einen *Leser* ist das kein Fehler, sondern dieselbe Lage wie eine noch
    nicht angelegte Datei: leeres Dict, Defaults greifen. Vorher fiel er hier auf
    ``~/.config/bibi/env`` zurück und las die Datei des ausführenden Nutzers
    samt Credentials — der Grund, aus dem die Testsuite ein autouse-Fixture
    dagegen brauchte.
    """
    monkeypatch.chdir(tmp_path)
    repo._root_of.cache_clear()
    assert config.read_env() == {}


def test_write_env_outside_repo_refuses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Für einen *Schreiber* ist dieselbe Lage sehr wohl ein Fehler.

    Ein stiller Schreibversuch irgendwohin wäre schlimmer als ein Abbruch: er
    legte eine Konfiguration an, die kein Knoten je liest.
    """
    monkeypatch.chdir(tmp_path)
    repo._root_of.cache_clear()
    with pytest.raises(config.KeinRepoError):
        config.write_env({"BIBI_ROLE": "synchronizer"})


def test_read_env_missing_file(cfg_home: Path):
    assert config.read_env() == {}


def test_write_then_read_roundtrip(cfg_home: Path):
    values = {
        "BIBI_SCHEDULER_URL": "http://sarasate:8769",
        "BIBI_ROLE": "worker,synchronizer",
        "BIBI_REMOTE": "https://example/repo.git",
        "BIBI_CLAUDE_BIN": "/home/u/.local/bin/claude",
        "BIBI_NODE_NAME": "sarasate-client",
        "BIBI_PUBLIC_HOST": "sarasate.tail9f9173.ts.net",
        "BIBI_NODE_ID": "abc123",
        # #144 — dieselbe Regel wie eine Zeile tiefer: mit Wert, obwohl er auf
        # einem Knoten mit nur einem Hostnamen genau einen Eintrag traegt.
        "BIBI_NODE_ALIASES": "Air2024.local,Mac.fritz.box",
        # m.rau/bibi#141 — hier mit Wert, obwohl er im Betrieb meist leer ist:
        # der Roundtrip soll jeden Schluessel tragen, nicht die haeufigsten.
        "BIBI_BOOTSTRAP_TOKEN": "7f3a9c21e4b8d05f",
    }
    config.write_env(values)
    assert config.read_env() == values


# ── node_id() — stabile Knoten-Identität für Connected Clients (Bibi4-Iteration) ─


def test_node_id_generates_and_persists_when_missing(cfg_home: Path):
    val = config.node_id()
    assert val and len(val) == 32  # uuid4().hex
    assert config.read_env()["BIBI_NODE_ID"] == val


def test_node_id_stable_across_calls(cfg_home: Path):
    first = config.node_id()
    second = config.node_id()
    assert first == second


def test_node_id_preserves_other_existing_keys(cfg_home: Path):
    config.write_env({"BIBI_ROLE": "worker", "BIBI_NODE_NAME": "sarasate-client"})
    config.node_id()
    env = config.read_env()
    assert env["BIBI_ROLE"] == "worker"
    assert env["BIBI_NODE_NAME"] == "sarasate-client"


def test_write_env_keeps_unknown_keys(cfg_home: Path):
    """Hieß bis m.rau/bibi#51 ``test_write_env_only_known_keys`` und prüfte das
    Gegenteil: dass ein unbekannter Schlüssel verworfen wird.

    Die Umkehrung ist Absicht. Der alte Test hielt ein Implementierungsdetail
    fest — sein Beispielwert hieß ``GARBAGE``, und die Sorge dahinter war Müll
    in der Konfiguration. Nur ist ``write_env()`` nicht der Ort, das zu
    beurteilen: ``CONVENTIONS.md`` erklärt ``BIBI_JOB_ENV_*`` ausdrücklich zum
    legitimen Inhalt genau dieser Datei und nennt als Weg dorthin ein
    ``>>``-Append. Was dort steht, hat jemand hingeschrieben; die Engine kann
    nicht wissen, was ein Team sonst noch anhängt.
    """
    config.write_env({"BIBI_ROLE": "worker", "BIBI_JOB_ENV_TOKEN": "x"})
    env = config.read_env()
    assert env["BIBI_JOB_ENV_TOKEN"] == "x"
    assert env["BIBI_ROLE"] == "worker"
    # fehlende bekannte Keys werden weiterhin leer geschrieben
    assert env["BIBI_REMOTE"] == ""


def test_write_env_permissions_0600(cfg_home: Path):
    p = config.write_env({"BIBI_ROLE": "worker"})
    assert (p.stat().st_mode & 0o777) == 0o600


def test_read_env_ignores_comments_and_blanks(cfg_home: Path):
    p = config.env_path()
    p.parent.mkdir(parents=True)
    p.write_text("# Kommentar\n\nBIBI_ROLE=worker\n  \nBIBI_REMOTE = x \n", encoding="utf-8")
    env = config.read_env()
    assert env["BIBI_ROLE"] == "worker"
    assert env["BIBI_REMOTE"] == "x"  # getrimmt


# ── PLAN-32 Stufe 32.2/32.3: Credential-Distribution ─────────────────────────


def test_distributable_config_filters_by_prefix():
    env = {"BIBI_JOB_ENV_ANTHROPIC_API_KEY": "sk-x", "BIBI_SCHEDULER_URL": "http://h",
          "BIBI_JOB_ENV_FOO": "bar"}
    assert config.distributable_config(env) == {
        "BIBI_JOB_ENV_ANTHROPIC_API_KEY": "sk-x", "BIBI_JOB_ENV_FOO": "bar"}


def test_distributable_config_excludes_empty_values():
    assert config.distributable_config({"BIBI_JOB_ENV_FOO": ""}) == {}


def test_config_version_stable_and_order_independent():
    v1 = config.config_version({"BIBI_JOB_ENV_A": "1", "BIBI_JOB_ENV_B": "2"})
    v2 = config.config_version({"BIBI_JOB_ENV_B": "2", "BIBI_JOB_ENV_A": "1"})
    assert v1 == v2


def test_config_version_changes_when_value_changes():
    v1 = config.config_version({"BIBI_JOB_ENV_A": "1"})
    v2 = config.config_version({"BIBI_JOB_ENV_A": "2"})
    assert v1 != v2


def test_read_distributed_env_empty_when_no_file(cfg_home: Path):
    assert config.read_distributed_env() == {}
    assert config.distributed_config_version() is None


def test_write_then_read_distributed_env_roundtrip(cfg_home: Path):
    config.write_distributed_env({"BIBI_JOB_ENV_ANTHROPIC_API_KEY": "sk-x"}, version="v1")
    env = config.read_distributed_env()
    assert env["BIBI_JOB_ENV_ANTHROPIC_API_KEY"] == "sk-x"
    assert config.distributed_config_version() == "v1"


def test_write_distributed_env_permissions_0600(cfg_home: Path):
    p = config.write_distributed_env({"BIBI_JOB_ENV_X": "y"}, version="v1")
    assert (p.stat().st_mode & 0o777) == 0o600


def test_write_distributed_env_lives_next_to_main_env(cfg_home: Path):
    # Entscheidung 4: zweite, env vorgelagerte Datei — erbt automatisch
    # BIBI_CONFIG_PATHs Mehrfach-Instanz-Trennung (env_path().parent).
    p = config.write_distributed_env({"BIBI_JOB_ENV_X": "y"}, version="v1")
    assert p.parent == config.env_path().parent


def test_write_distributed_env_replaces_not_merges(cfg_home: Path):
    config.write_distributed_env({"BIBI_JOB_ENV_A": "1", "BIBI_JOB_ENV_B": "2"}, version="v1")
    config.write_distributed_env({"BIBI_JOB_ENV_A": "1"}, version="v2")
    env = config.read_distributed_env()
    assert "BIBI_JOB_ENV_B" not in env


# ── m.rau/bibi#51: write_env() darf nicht verlieren, was es nicht kennt ──────
#
# ``read_env()`` liest jede ``KEY=VALUE``-Zeile, ``write_env()`` schrieb nur
# ``KEYS`` zurück. Lesen und Schreiben hatten verschiedene Vorstellungen davon,
# was in dieser Datei stehen darf — wer schrieb, verlor, was er nicht kannte.
#
# Betroffen sind die ``BIBI_JOB_ENV_*``-Werte: laut CONVENTIONS.md legitimer
# Inhalt genau dieser Datei, und der dokumentierte Weg, ein Credential auf einen
# Host zu bringen, ist ein ``>>``-Append daran.


def test_write_env_preserves_unknown_keys(cfg_home: Path):
    p = config.env_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        "BIBI_ROLE=synchronizer\n"
        "BIBI_JOB_ENV_GITEA_TOKEN=geheim\n"
        "BIBI_JOB_ENV_GITEA_USER=wer\n",
        encoding="utf-8",
    )
    config.write_env({"BIBI_ROLE": "synchronizer,controller"})
    danach = config.read_env()
    assert danach["BIBI_ROLE"] == "synchronizer,controller"
    assert danach["BIBI_JOB_ENV_GITEA_TOKEN"] == "geheim"
    assert danach["BIBI_JOB_ENV_GITEA_USER"] == "wer"


def test_node_id_selfheal_preserves_credentials(cfg_home: Path):
    """Der gefährlichere der zwei Wege: kein ``init``, kein Daemon-Neustart.

    ``node_id()`` soll einem Bestandsknoten das manuelle ``init`` *ersparen* —
    und richtete dabei denselben Schaden an.
    """
    p = config.env_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("BIBI_JOB_ENV_TOKEN=geheim\n", encoding="utf-8")
    config.node_id()
    assert config.read_env().get("BIBI_JOB_ENV_TOKEN") == "geheim"
