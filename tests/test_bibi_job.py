"""Tests für bibi.job — Signal-Helfer für Job-Autoren."""
import json
import pytest
import bibi.job


def test_running_writes_signal(capsys):
    bibi.job.running()
    assert capsys.readouterr().out.strip() == 'BIBI:{"name":"running"}'


def test_awaiting_minimal(capsys):
    bibi.job.awaiting("Wie viele?")
    out = capsys.readouterr().out
    payload = json.loads(out.split("BIBI:", 1)[1])
    assert payload["name"] == "awaiting"
    assert payload["input_request"] == "Wie viele?"
    assert payload["input_format"] == "text"


def test_awaiting_includes_format(capsys):
    bibi.job.awaiting("Wie viele?", input_format="number")
    out = capsys.readouterr().out
    payload = json.loads(out.split("BIBI:", 1)[1])
    assert payload["name"] == "awaiting"
    assert payload["input_format"] == "number"


def test_awaiting_with_port(capsys):
    bibi.job.awaiting("Eingabe", port=9100)
    payload = json.loads(capsys.readouterr().out.split("BIBI:", 1)[1])
    assert payload["port"] == 9100


def test_awaiting_without_port_omits_key(capsys):
    bibi.job.awaiting("Eingabe")
    payload = json.loads(capsys.readouterr().out.split("BIBI:", 1)[1])
    assert "port" not in payload


def test_app_register_emits_port(capsys):
    bibi.job.app_register(port=9100)
    payload = json.loads(capsys.readouterr().out.split("BIBI:", 1)[1])
    assert payload == {"name": "app_register", "port": 9100}


def test_app_register_with_prefix(capsys):
    bibi.job.app_register(port=9100, prefix="/my-app")
    payload = json.loads(capsys.readouterr().out.split("BIBI:", 1)[1])
    assert payload["prefix"] == "/my-app"


def test_app_register_without_prefix_omits_key(capsys):
    bibi.job.app_register(port=9100)
    payload = json.loads(capsys.readouterr().out.split("BIBI:", 1)[1])
    assert "prefix" not in payload


def test_deferred_is_exception(capsys):
    d = bibi.job.Deferred(seconds=120)
    assert d.seconds == 120
    assert isinstance(d, Exception)
    # Deferred emittiert automatisch ein BIBI-Signal (für den Wrapper)
    out = capsys.readouterr().out
    payload = json.loads(out.split("BIBI:", 1)[1])
    assert payload == {"name": "deferred", "seconds": 120}


def test_deferred_default_seconds(capsys):
    d = bibi.job.Deferred()
    assert d.seconds == 60
    payload = json.loads(capsys.readouterr().out.split("BIBI:", 1)[1])
    assert payload["seconds"] == 60


def test_deferred_str_representation(capsys):
    d = bibi.job.Deferred(seconds=300)
    capsys.readouterr()  # stdout verwerfen
    assert "300" in str(d)


def test_deferred_is_raisable(capsys):
    with pytest.raises(bibi.job.Deferred) as exc_info:
        raise bibi.job.Deferred(seconds=42)
    capsys.readouterr()  # stdout verwerfen
    assert exc_info.value.seconds == 42
