"""``bibi-ctrl sync …`` — Git-Abgleich (PLAN-1 §1.5, §4.9; Neufassung PLAN-25
Befund 8, committet seit PLAN-30 Ebene 5 nichts mehr — s. u.).

- ``sync on|off``      — auto_sync-Flag (stehende Push-Zustimmung) umschalten
- ``sync``            — manueller, (fast) immer wirksamer Abgleich:
                        0. Eskalierte Job-Branches zuerst klären (PLAN-30
                           Ebene 3) — derselbe Konflikt, den der Sweep sonst
                           quarantänt, bekommt jetzt die neue Chance, die nur
                           ein anwesender Mensch geben kann. Konflikt → im
                           Tree lassen + Marker anzeigen (KI-Auflösung).
                        1. Immer integrieren (fetch + rebase), geschützt durch
                           den Idle-Fenster-Guard (PLAN-30 Ebene 4): würde der
                           Pull eine gerade bearbeitete Datei anfassen, wird
                           der GESAMTE Versuch übersprungen, nicht nur ein
                           Teil. Sonst: Konflikt → im Tree lassen +
                           ``sync_conflict`` (KI-Auflösung).
                        2. Immer pushen, unabhängig von ``auto_sync`` — der
                           ``/sync``-Aufruf ist die Freigabe für den Push.
                        3. Dirty Änderungen — egal ob im aktiven Projekt oder
                           in fremden Cases — werden nur noch ANGEZEIGT, nie
                           committet (PLAN-30 Ebene 5, löst Befund 2: das
                           bisherige automatische Cluster-Committen fremder
                           Cases war ein vierter, unverorteter Interaktions-
                           Modus, der sich nicht an Nebenbedingung 0 hielt).
                           Committen ist ausschließlich ``/save``s Aufgabe.
- ``sync continue``   — nach KI-Auflösung der Marker den offenen Rebase ODER
                        Job-Branch-Merge fortsetzen + push (erkennt selbst,
                        welcher der beiden offen ist — ein Werkzeug für beide
                        Konfliktarten, PLAN-30 Ebene 3).
- ``sync abort``      — offenen Rebase ODER Job-Branch-Merge abbrechen
- ``sync hook-stop``  — Hintergrund: bei auto_sync transient committen + push
- ``sync hook-start`` — Hintergrund: bei auto_sync pullen; Konflikt-Warnung surface
"""

from __future__ import annotations

import argparse
import sys

from bibi import git_ops, repo, state
from bibi.daemon import mergeback, merge_quarantine


def _toggle_on(_: argparse.Namespace) -> int:
    state.set_auto_sync(True)
    print("auto_sync → on")
    return 0


def _toggle_off(_: argparse.Namespace) -> int:
    state.set_auto_sync(False)
    print("auto_sync → off")
    return 0


def _print_conflicts() -> None:
    for f in git_ops.conflicted_files():
        print(f"  {f}", file=sys.stderr)


def _resolve_stuck_merge_branches() -> int | None:
    """PLAN-30 Ebene 3: eskalierte Job-Branches (Ebene-2-Quarantäne) zuerst
    klären, bevor der übrige ``/sync``-Ablauf startet — ein anwesender Mensch
    IST die neue Chance, auf die die Quarantäne sonst wartet (``force=True``).

    Löst genau EINEN Branch pro ``/sync``-Aufruf auf (nicht alle automatisch
    hintereinander) — nach einer heiklen Konflikt-Auflösung soll nicht
    unbeaufsichtigt der nächste Versuch lostrudeln. ``None`` = nichts zu tun,
    weiter im normalen Ablauf. ``int`` = Exitcode, ``run_sync()`` kehrt sofort
    zurück (offener Konflikt ODER erledigt, aber noch mehr Branches hängen)."""
    root = repo.root()
    stuck = merge_quarantine.escalated(root)
    if not stuck:
        return None
    branch = stuck[0]
    slug = branch.removeprefix("agent/")
    res = mergeback.merge_back(repo_root=root, slug=slug, force=True, keep_conflict=True)
    if res.status == "conflict":
        print(f"⚠ Merge-Konflikt beim Auflösen von {branch} — Marker auflösen, "
              f"dann `bibi-ctrl sync continue` (oder `sync abort`).", file=sys.stderr)
        _print_conflicts()
        return 1
    if res.status == "merged":
        print(f"{branch}: gemergt")
    elif res.status == "up_to_date":
        print(f"{branch}: bereits aktuell (kein Merge nötig)")
    else:  # "blocked"/"error" — force umgeht nur die Quarantäne-Vorprüfung,
        # keinen echten Fehler (z. B. Modus A, sehr unwahrscheinlich hier, da
        # escalated() nur echte Fehlschläge listet, aber trunk könnte sich
        # zwischen escalated() und dem Versuch minimal bewegt haben).
        print(f"{branch}: {res.status} — {res.detail}", file=sys.stderr)
    remaining = merge_quarantine.escalated(root)
    if remaining:
        print(f"{len(remaining)} weitere(r) hängende(r) Branch(es) — "
              f"erneut `/sync` ausführen: {', '.join(remaining)}")
        return 0
    return None  # alles geklärt — weiter im normalen Ablauf


def run_sync(_: argparse.Namespace) -> int:
    """Manueller Abgleich — Neufassung PLAN-25 Befund 8: cluster-committen
    (case-fremde Änderungen) → integrieren (immer) → pushen (immer). Nur
    dirty Änderungen im aktiven Projekt bleiben unangetastet (→ ``/save``).

    PLAN-30 Ebene 3: eskalierte Job-Branch-Konflikte werden VOR diesem Ablauf
    geklärt (``_resolve_stuck_merge_branches()``) — ein sauberer Tree danach,
    bevor Pull/Push überhaupt starten."""
    if git_ops.is_rebase_in_progress():
        print("Rebase offen — Marker auflösen, dann `bibi-ctrl sync continue` "
              "(oder `sync abort`).", file=sys.stderr)
        _print_conflicts()
        return 1
    if git_ops.is_merge_in_progress():
        print("Merge offen (Job-Branch-Konflikt) — Marker auflösen, dann "
              "`bibi-ctrl sync continue` (oder `sync abort`).", file=sys.stderr)
        _print_conflicts()
        return 1

    rc = _resolve_stuck_merge_branches()
    if rc is not None:
        return rc

    # PLAN-30 Ebene 5: /sync committet nichts mehr, auch keine fremden Cases
    # (das war Befund 2 — /sync verhielt sich für Nicht-aktive Cases wie ein
    # eigener, ungefragter vierter Interaktions-Modus). Committen ist
    # ausschließlich /saves Aufgabe. Dirty Änderungen werden nur noch
    # angezeigt, für JEDEN Case (nicht mehr nur den aktiven) — reine
    # Information, kein Risiko, kein Schreibzugriff.
    active_case_rel = state.get_path()
    other_cases, caseless, active_dirty = git_ops.cluster_dirty_paths(
        git_ops.dirty_paths(), case_dir_name=repo.case_dir_name(),
        active_case_rel=active_case_rel)

    branch = git_ops.current_branch()
    # guard_live_paths (Ebene 4/5): der Pull-Schritt ist ein ganz normaler
    # automatischer Schreibvorgang wie der Merge-back-Sweep, nur manuell
    # angestoßen — überspringt den GESAMTEN Pull-Versuch, wenn er eine gerade
    # bearbeitete Datei anfassen würde.
    ok, kind = git_ops.integrate(branch, keep_conflict=True, guard_live_paths=True)
    if not ok:
        if kind == "conflict":
            state.set_sync_conflict(True)
            print("⚠ Merge-Konflikt — Marker auflösen, dann `bibi-ctrl sync continue`.",
                  file=sys.stderr)
            _print_conflicts()
        elif kind == "live_edit":
            print("Pull übersprungen — Zieldatei wird gerade bearbeitet, "
                  "gleich nochmal `/sync` versuchen.", file=sys.stderr)
        else:
            print(f"Abgleich fehlgeschlagen: {kind}", file=sys.stderr)
        return 1
    print("integrated")

    pok, pmsg = git_ops.push(branch)
    if not pok:
        print(f"push fehlgeschlagen: {pmsg}", file=sys.stderr)
        return 1
    state.set_sync_conflict(False)
    print("push ok")

    if other_cases or caseless:
        print("Unfertige Änderungen außerhalb des aktiven Projekts (nicht angefasst) "
              "— dort `/save` ausführen:", file=sys.stderr)
        for case_rel in sorted(other_cases):
            print(f"  {case_rel}", file=sys.stderr)
        if caseless:
            print("  (weitere, case-lose Änderungen)", file=sys.stderr)
    if active_dirty:
        print("Änderungen im aktiven Projekt (nicht angefasst) — `/save` ausführen:",
              file=sys.stderr)
        for p in active_dirty:
            print(f"  {p}", file=sys.stderr)

    print("sync ok")
    return 0


def run_continue(_: argparse.Namespace) -> int:
    """PLAN-30 Ebene 3: erkennt selbst, ob ein Job-Branch-Merge (Requirement 2)
    oder ein Pull-Rebase (Requirement 3) offen ist — ein Werkzeug für beide
    Konfliktarten, kein zweiter Befehl nötig."""
    if git_ops.is_merge_in_progress():
        root = repo.root()
        ok, log, kind = git_ops.continue_merge_and_push()
        for line in log:
            print(line)
        if not ok:
            if kind == "conflict":
                _print_conflicts()
            return 1
        # Der gerade abgeschlossene Merge räumt seine eigene Quarantäne-Zeile
        # nicht selbst auf (git_ops.py kennt merge_quarantine bewusst nicht,
        # keine Rückabhängigkeit daemon → git_ops) — gegen die jetzt aktuelle
        # unmerged-Liste prunen holt das nach, ohne den Branch-Namen hier
        # erneut ermitteln zu müssen (derselbe Mechanismus wie remerge_all()).
        merge_quarantine.prune(root, keep_branches=set(
            mergeback.unmerged_agent_branches(repo_root=root)))
        remaining = merge_quarantine.escalated(root)
        if remaining:
            print(f"{len(remaining)} weitere(r) hängende(r) Branch(es) — "
                  f"erneut `/sync` ausführen: {', '.join(remaining)}")
        return 0
    if not git_ops.is_rebase_in_progress():
        print("kein Rebase/Merge im Gange.")
        return 0
    ok, log, kind = git_ops.continue_rebase_and_push()
    for line in log:
        print(line)
    if ok:
        state.set_sync_conflict(False)
        return 0
    if kind == "conflict":
        _print_conflicts()
    return 1


def run_abort(_: argparse.Namespace) -> int:
    """PLAN-30 Ebene 3: erkennt selbst, welche Konfliktart offen ist (s.
    ``run_continue()``)."""
    if git_ops.is_merge_in_progress():
        git_ops.abort_merge()
        print("merge abgebrochen.")
        return 0
    if not git_ops.is_rebase_in_progress():
        print("kein Rebase/Merge im Gange.")
        return 0
    git_ops.abort_rebase()
    print("rebase abgebrochen.")
    return 0


def run_hook_stop(_: argparse.Namespace) -> int:
    """Stop-Hook: bei auto_sync transient committen + integrieren + push. Immer 0."""
    if not state.get_auto_sync():
        return 0
    _, _, kind = git_ops.commit_and_push(None, git_ops.auto_commit_message(), do_push=True)
    if kind == "conflict":
        state.set_sync_conflict(True)
    return 0


def run_hook_start(_: argparse.Namespace) -> int:
    """SessionStart-Hook: bei auto_sync pullen; Konflikt-Warnung surfacen."""
    if state.get_auto_sync() and not git_ops.is_dirty():
        ok, kind = git_ops.integrate(git_ops.current_branch())
        if not ok and kind == "conflict":
            state.set_sync_conflict(True)
    if state.get_sync_conflict():
        print("⚠ sync conflict — `/sync` ausführen, um den Konflikt aufzulösen.",
              file=sys.stderr)
        return 1
    return 0


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("sync", help="Git-Abgleich (on|off|continue|abort|hook-* oder manuell)")
    p.set_defaults(func=run_sync)
    ssub = p.add_subparsers(dest="sync_cmd")
    ssub.add_parser("on").set_defaults(func=_toggle_on)
    ssub.add_parser("off").set_defaults(func=_toggle_off)
    ssub.add_parser("continue").set_defaults(func=run_continue)
    ssub.add_parser("abort").set_defaults(func=run_abort)
    ssub.add_parser("hook-stop").set_defaults(func=run_hook_stop)
    ssub.add_parser("hook-start").set_defaults(func=run_hook_start)
