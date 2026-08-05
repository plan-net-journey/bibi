"""``bibi-ctrl at`` — One-shot-Schedule anlegen (DESIGN §5.2/§6.3; PLAN-3 §3.3).

Schreibt eine flache ``at:``-MD ins Case-Verzeichnis, sodass der Scheduler sie als
einmaligen Lauf erfasst, und triggert (best-effort) einen Rescan. In-Process bis
auf den Rescan — der trifft den laufenden Daemon per HTTP.

  bibi-ctrl at "<when>" "<prompt>"           # claude-Job (AI-Prompt, Default)
  bibi-ctrl at "<when>" "<cmd>" --job        # Shell-Befehl (job-Typ)

Alle Jobs verwenden ``job:`` als einzigen Frontmatter-Key (PLAN-10 §1, Unified Job
Model). Ohne ``--job`` wird der Prompt als ``claude: <prompt>`` kodiert — der Worker
erkennt das Prefix und setzt ``BIBI_JOB_TYPE=claude``.

``<when>``: ISO 8601, relativ (``+30s``/``+5min``/``+2h``/``+1d``) oder
best-effort natürlichsprachlich (via dateutil).
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import secrets
import sys
import urllib.error
import urllib.request

from dateutil import parser as _date_parser

from bibi import config, repo

_REL = re.compile(r"^\+(\d+)\s*(s|sec|min|m|h|hour|d|day)s?$", re.IGNORECASE)
_UNIT = {"s": "seconds", "sec": "seconds", "min": "minutes", "m": "minutes",
         "h": "hours", "hour": "hours", "d": "days", "day": "days"}


def _to_naive_local(dt: _dt.datetime) -> _dt.datetime:
    return dt.astimezone().replace(tzinfo=None) if dt.tzinfo else dt


def resolve_when(when: str, *, now: _dt.datetime | None = None) -> _dt.datetime:
    """``<when>`` → naiver lokaler ``datetime``. ``ValueError`` bei Parse-Fehler."""
    now = now or _dt.datetime.now()
    s = when.strip()
    m = _REL.match(s)
    if m:
        return now + _dt.timedelta(**{_UNIT[m.group(2).lower()]: int(m.group(1))})
    return _to_naive_local(_date_parser.parse(s))  # ISO / natürlichsprachlich


def _rescan(base_url: str) -> bool:
    """Best-effort POST ``/-/rescan`` an den Scheduler. True bei Erfolg.

    **Erreichbarkeit, nicht Wirkung.** Ein ``True`` heisst „der Scheduler hat
    gescannt", nicht „er hat die eben geschriebene Datei gefunden" — auf einem
    Client sind das zwei verschiedene Aussagen (m.rau/bibi#140), und die
    zweite haengt an der Zustellung, nicht an diesem Aufruf.
    """
    url = f"{base_url}/-/rescan"
    try:
        req = urllib.request.Request(url, method="POST")
        with urllib.request.urlopen(req, timeout=3):  # noqa: S310 (localhost)
            return True
    except (urllib.error.URLError, OSError):
        return False


#: Hostnamen, hinter denen der Scheduler denselben Checkout liest wie dieser
#: Befehl. Alles andere ist eine fremde Maschine mit einem fremden Vault.
_HIER = {"localhost", "127.0.0.1", "::1", "0.0.0.0", ""}


def _scheduler_ist_hier(base_url: str) -> bool:
    """Sieht der Scheduler denselben Vault wie dieser Befehl?

    **Die Frage ist die Adresse, nicht die Rolle.** Ein Knoten kann die
    ``scheduler``-Rolle tragen und trotzdem auf einen fremden Host zeigen (zwei
    Instanzen auf einer Maschine), und umgekehrt ist ein Knoten ohne gesetztes
    ``BIBI_ROLE`` nicht automatisch ein Client — er weiss es nur nicht. Die
    Adresse dagegen beantwortet genau, was hier zaehlt: liegt der Vault, den
    der Scheduler liest, auf dieser Maschine?

    Im Zweifel ``True``, also kein git-Umweg: das ist das Verhalten von vor
    m.rau/bibi#140 und auf einem Ein-Knoten-Setup richtig.
    """
    from urllib.parse import urlparse
    try:
        return (urlparse(base_url).hostname or "") in _HIER
    except ValueError:
        return True


def _hat_remote() -> bool:
    """Gibt es ueberhaupt ein Ziel zum Pushen?

    Ein Team ohne Remote ist ein vorgesehener Fall (DESIGN: hostlose Teams).
    Dort ist eine fehlende Zustellung kein Fehler dieses Befehls, sondern eine
    Eigenschaft der Umgebung — sie gehoert gesagt, nicht bestraft.
    """
    import subprocess

    from bibi import repo
    try:
        p = subprocess.run(["git", "remote"], cwd=repo.root(), check=False,
                           capture_output=True, text=True, timeout=10)
        return bool(p.stdout.strip())
    except Exception:  # noqa: BLE001
        return False


def _zustellen(path) -> bool:
    """Die MD zum Scheduler bringen — ueber den Vault, also ueber git.

    **Warum git und nicht HTTP** (m.rau/bibi#140): der Vault *ist* der
    Transportweg dieses Systems, und der Synchronizer verteilt ihn ohnehin auf
    jeden Knoten. Eine eigene Route waere ein zweiter Weg fuer dieselbe Sache —
    und sie muesste auf dem Scheduler in denselben git-Baum schreiben, den der
    Synchronizer gerade bearbeitet.

    Der Push ist hier **nicht** optional wie beim Synchronizer: ohne ihn feuert
    der One-shot nie, und genau das ist der Fehler, um den es geht.
    """
    from bibi import git_ops, repo
    rel = path.relative_to(repo.root()).as_posix()
    if not git_ops.stage_and_commit_paths([rel], f"at: {path.stem}"):
        return False
    ok, _out, _kind = git_ops.push(git_ops.current_branch() or "trunk")
    return bool(ok)


def run(args: argparse.Namespace) -> int:
    try:
        dt = resolve_when(args.when)
    except (ValueError, OverflowError) as exc:
        print(f"bibi-ctrl at: '{args.when}' nicht als Zeitpunkt lesbar: {exc}", file=sys.stderr)
        return 2
    iso = dt.replace(microsecond=0).isoformat()

    slug = f"{dt:%Y%m%d}.at-{dt:%H%M%S}-{secrets.token_hex(2)}"
    case = repo.case_dir()
    case.mkdir(parents=True, exist_ok=True)
    path = case / f"{slug}.md"

    # json.dumps liefert einen gültigen (YAML-kompatiblen) Doppelquote-Skalar.
    if args.job:
        job_value = args.payload
        typ_display = "job"
    else:
        job_value = f"claude: {args.payload}"
        typ_display = "claude"
    md = (f"---\nat: {iso}\njob: {json.dumps(job_value)}\n---\n"
          f"One-shot via `/at`, geplant für {iso}.\n")
    path.write_text(md, encoding="utf-8")

    # PLAN-13 Stufe 13.0: explizites --port bleibt lokal (Test-Instanz),
    # ohne --port die volle BIBI_SCHEDULER_URL statt blind 127.0.0.1 — sonst
    # läuft der Rescan-Trigger auf einem Client-Knoten mit entferntem
    # Scheduler ins Leere.
    base_url = f"http://127.0.0.1:{args.port}" if args.port else config.scheduler_base_url()

    # Zustellung vor Rescan (m.rau/bibi#140). Ein Rescan auf einer Maschine,
    # die diese Datei nicht hat, findet nichts — und meldete bisher trotzdem
    # Erfolg. Auf dem Scheduler selbst entfaellt der Schritt: dort liest der
    # Daemon denselben Vault, in den eben geschrieben wurde.
    lokal = bool(args.port) or _scheduler_ist_hier(base_url)

    rel = path.relative_to(repo.root()).as_posix()
    eta = dt - _dt.datetime.now()
    eta_s = max(0, int(eta.total_seconds()))
    print(f"one-shot erstellt: {slug}")
    print(f"  at:   {iso}  (in {eta_s // 60}m {eta_s % 60}s)")
    print(f"  typ:  {typ_display}")
    print(f"  pfad: {rel}")

    if not lokal:
        if not _hat_remote():
            # Kein Ziel zum Pushen — das ist kein Fehler dieses Befehls,
            # sondern eine Eigenschaft der Umgebung. Sagen statt scheitern.
            print(f"  zustellung: kein git-Remote — der Scheduler auf {base_url} "
                  "sieht diesen Checkout nicht")
        elif _zustellen(path):
            print("  zustellung: committet + gepusht")
        else:
            # Kein Rescan: er faende auf dem Scheduler nichts und ergaebe mit
            # einem `ok` genau die Falschaussage, um die es hier geht.
            print("  zustellung: FEHLGESCHLAGEN — die MD ist nur lokal und feuert nicht")
            print(f"  der Scheduler liegt auf {base_url} und sieht diesen Checkout nicht.")
            print(f"  von Hand:  git add -- '{rel}' && git commit -m 'at' && git push",
                  file=sys.stderr)
            return 1

    rescanned = _rescan(base_url)
    print(f"  rescan: {'ok' if rescanned else f'Daemon nicht erreichbar ({base_url}) — beim nächsten Rescan/Start erfasst'}")
    print("beobachten: bibi-ctrl job list", file=sys.stderr)
    return 0


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("at", help="One-shot-Schedule anlegen (§6.3, Scheduler)")
    p.add_argument("when", help="ISO 8601 | +30s/+5min/+2h/+1d | natürlichsprachlich")
    p.add_argument("payload", help="Prompt (claude) bzw. Shell-Befehl (--job)")
    p.add_argument("--job", action="store_true", help="job-Typ (Shell) statt claude")
    p.add_argument("--port", type=int, default=0, help="0 = aus BIBI_DAEMON_PORT/Default")
    p.set_defaults(func=run)
