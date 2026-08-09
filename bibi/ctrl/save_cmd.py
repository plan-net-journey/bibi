"""``bibi-ctrl save`` — committen + (optional) pushen (PLAN-1 §1.2).

Zwei Geltungsbereiche (A10): mit aktivem Case (``state.get_path()``) werden nur
die fallbezogenen Änderungen committet; ohne aktiven Case das *gesamte* Repo.
``--repo`` erzwingt den Repo-Scope auch bei aktivem Case — seit der Case an der
Session hängt statt am cwd, ist "kein Case aktiv" kein Zufallszustand mehr, aus
dem der Repo-Scope nebenbei herausfällt; wer ihn will, sagt es.
Push folgt der Sync-Matrix (§4.9): ``--push`` oder ``auto_sync on`` → pushen;
sonst committen + integrieren, aber nicht pushen (der Skill fragt nach).

**Der Scope wird vor dem Commit benannt, nicht erst in der Commit-Message**
(m.rau/bibi#97). Bis dahin war die Default-Message (``save: <repo>`` statt
``save: <case>``) der einzige sichtbare Unterschied zwischen den beiden Fällen —
und die liest man hinterher im Log, wenn der Commit schon steht. Ein
Repo-weiter Commit nimmt in dieser Instanz fremde, halbfertige Arbeit mit:
Agent-Jobs und mehrere Sitzungen teilen sich einen Checkout
(``worktree.bgIsolation: "none"``).

**Und in einer Lage verweigert ``save`` die Vermutung** (Exitcode 2): es liegt
eine Park-Marke auf einen existierenden Case, sie gehört nur einer Session-ID,
die es nicht mehr gibt (``state.foreign_parks()``). Das ist kein Randfall,
sondern der Normalfall nach jeder Wiederverbindung. „Nie geparkt" bleibt
dagegen unangetastet ein gewöhnlicher Zustand — Job, Hook, frisches Repo — und
läuft ohne Rückfrage in den Repo-Scope. Wer beide Lagen gleich behandelt, macht
aus einer Warnung eine Belästigung; wer keine unterscheidet, committet still zu
viel.
"""

from __future__ import annotations

import argparse
import sys

from bibi import git_ops, repo, state, sync


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("save", help="aktiven Case oder ganzes Repo committen + (push)")
    p.add_argument("-m", "--message", help="Commit-Message überschreiben")
    p.add_argument("--push", action="store_true",
                   help="pushen unabhängig vom auto_sync-Flag")
    p.add_argument("--repo", action="store_true",
                   help="das ganze Repo committen, auch wenn ein Case aktiv ist")
    p.set_defaults(func=run)


def _dirty_count(scope_rel: str | None) -> int:
    """Wie viele Pfade dieser Commit anfassen würde. Defensiv: die Zahl ist
    Auskunft, kein Vertrag — sie darf ein ``save`` nie kosten."""
    try:
        paths = git_ops.dirty_paths()
    except Exception:  # noqa: BLE001
        return 0
    if scope_rel is None:
        return len(paths)
    prefix = f"vault/{scope_rel}/"
    return sum(1 for p in paths if p.startswith(prefix))


def _refuse_ambiguous_scope() -> int:
    """Der Repo-Scope ist hier eine Vermutung, keine Feststellung — also fragen.

    Ein CLI kann nicht zurückfragen; die Verweigerung **ist** die Frage, und sie
    nennt beide Antworten. Eigener Exitcode 2, damit ein Aufrufer sie von einem
    fehlgeschlagenen Commit (1) unterscheiden kann.
    """
    parks = state.foreign_parks()
    print("save verweigert: kein Case aktiv, aber Park-Marken anderer Sessions "
          "zeigen auf einen Case —", file=sys.stderr)
    for rel, n in sorted(parks.items()):
        marker = "Marke" if n == 1 else "Marken"
        print(f"  {rel}  ({n} {marker})", file=sys.stderr)
    print("Der Repo-Scope wäre hier geraten, nicht festgestellt. Entweder:",
          file=sys.stderr)
    print(f"  bibi-ctrl open \"{sorted(parks)[0].split('/')[-1]}\"   "
          "— den Case wieder parken (dann Case-Scope)", file=sys.stderr)
    print("  bibi-ctrl save --repo                       "
          "— das ganze Repo ist gemeint", file=sys.stderr)
    return 2


def _has_changes_to_gather() -> bool:
    """Gäbe es überhaupt fremde Arbeit einzusammeln? (m.rau/bibi#66)

    **Sperrt im Zweifel**, und das ist die entgegengesetzte Defensivrichtung zu
    :func:`_dirty_count`. Dessen Docstring sagt ausdrücklich, die Zahl sei
    „Auskunft, kein Vertrag — sie darf ein ``save`` nie kosten", weshalb es
    jede Exception zu ``0`` verschluckt.

    Den Guard daran zu hängen wäre die Umkehrung des Schutzes: bei einem
    git-Fehler meldete es „nichts zu tun", der Guard ließe durch, und der
    Repo-Commit sammelte fremde Arbeit ein — in genau der Lage, vor der er
    schützen soll. Antwortet git nicht, gilt hier deshalb „es könnte etwas
    geben".
    """
    try:
        return bool(git_ops.dirty_paths())
    except Exception:  # noqa: BLE001 — kein Befund heißt hier: sperren
        return True


def run(args: argparse.Namespace) -> int:
    path = None if args.repo else state.get_path()  # vault-relativ oder None
    # **Erst fragen, ob es etwas zu entscheiden gibt** (m.rau/bibi#66). Der
    # Guard stand vor dieser Erhebung und verweigerte damit auch dann, wenn der
    # Commit null Dateien angefasst hätte. Seine Begründung — „ein Repo-weiter
    # Commit nimmt fremde, halbfertige Arbeit mit" — setzt voraus, dass es
    # solche Arbeit *gibt*; bei sauberem Baum ist die Menge leer, und er
    # schützt vor einem Schaden, der nicht eintreten kann.
    #
    # Nach jeder Wiederverbindung ist genau das der Normalfall: die Session-ID
    # wechselt, die alte Marke bleibt liegen, und wer nur noch pushen will,
    # zahlt jedes Mal einen Fehlversuch. Der Guard ist damit nicht ausgehebelt,
    # nur auf die Lage eingeschränkt, für die seine Begründung gilt.
    if (path is None and not args.repo and state.foreign_parks()
            and _has_changes_to_gather()):
        return _refuse_ambiguous_scope()

    if path:
        scope = repo.vault() / path
        default_msg = f"save: {scope.name}"
        scope_label = f"case/{scope.name}" if path.startswith("case/") else path
    else:
        scope = None  # ganzes Repo (A10)
        default_msg = f"save: {repo.root().name}"
        scope_label = f"ganzes Repo ({repo.root().name})"

    n = _dirty_count(path)
    print(f"Scope: {scope_label} — {n} Datei(en)")

    message = args.message or default_msg
    do_push = args.push or sync.auto_push_enabled()

    ok, log, kind = git_ops.commit_and_push(scope, message, do_push)
    for line in log:
        print(line)

    if kind == "conflict":
        state.set_sync_conflict(True)
        print("⚠ Merge-Konflikt — KI-Auflösung nötig (/sync).", file=sys.stderr)
    return 0 if ok else 1
