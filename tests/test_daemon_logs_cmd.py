"""``bibi-ctrl daemon logs`` — Aktivitätslog als Klartext (PLAN-5 §5.1)."""

from __future__ import annotations

import argparse
import json

from bibi.ctrl import daemon_cmd


def _args(follow=False, lines=40):
    return argparse.Namespace(follow=follow, lines=lines)


def test_logs_renders_jsonl_as_human(team_repo, capsys):
    log_dir = team_repo / "data" / "daemon-log"
    log_dir.mkdir(parents=True)
    (log_dir / "daemon.jsonl").write_text(
        json.dumps({"ts": "2026-06-27T08:30:00+00:00", "level": "INFO",
                    "role": "scheduler", "event": "scheduler.rescan",
                    "msg": "", "inserted": 2}) + "\n"
        + json.dumps({"ts": "2026-06-27T08:30:01+00:00", "level": "INFO",
                      "role": "worker", "event": "worker.pickup",
                      "slug": "echo", "run_id": "r1"}) + "\n",
        encoding="utf-8",
    )
    rc = daemon_cmd.logs(_args())
    out = capsys.readouterr().out
    assert rc == 0
    assert "scheduler.rescan" in out and "inserted=2" in out
    assert "worker.pickup" in out and "slug=echo" in out


def test_logs_tail_limits_lines(team_repo, capsys):
    log_dir = team_repo / "data" / "daemon-log"
    log_dir.mkdir(parents=True)
    rows = "\n".join(
        json.dumps({"ts": "2026-06-27T08:30:00+00:00", "level": "INFO",
                    "role": "daemon", "event": f"e{i}", "msg": ""})
        for i in range(10)
    )
    (log_dir / "daemon.jsonl").write_text(rows + "\n", encoding="utf-8")
    daemon_cmd.logs(_args(lines=3))
    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) == 3 and "e9" in out[-1] and "e7" in out[0]


def test_logs_missing_file_returns_1(team_repo, capsys):
    rc = daemon_cmd.logs(_args())
    assert rc == 1
    assert "kein Aktivitätslog" in capsys.readouterr().err
