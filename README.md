# bibi

**Eine Engine für Team-Repositories, in denen Markdown die Steuerung ist.**

Ein bibi-Team-Repo ist ein git-Repository mit einem Vault aus Markdown-Dateien. Was darin steht, ist nicht nur Dokumentation: Eine Markdown-Datei mit einem `schedule:`-Feld im Frontmatter *ist* ein wiederkehrender Job, eine mit `at:` ein einmaliger. bibi liest den Vault, plant die Jobs, führt sie aus — als Shell-Kommando oder als Claude-Code-Sitzung —, hält mehrere Rechner über git synchron und zeigt das Ganze in einer schlichten Weboberfläche.

Die Engine selbst ist ein Python-Paket mit einem Kommando: `bibi-ctrl`. Sie enthält keine Inhalte. Die Inhalte leben im Team-Repo.

## Los geht es nicht hier

**Der Einstieg ist [`bibi-team`](https://github.com/plan-net-journey/bibi-team)** — ein GitHub-Template für ein leeres Team-Repo, mit Vault-Struktur, Konventionen und einer `INSTALL.md`, die von vorn anfängt. Dieses Repo hier ist die Abhängigkeit, nicht der Arbeitsplatz.

```bash
# Template auf GitHub anklicken oder:
gh repo create mein-team --template plan-net-journey/bibi-team --private
cd mein-team && uv venv && uv pip install .
bibi-ctrl init
```

## Wie das im Alltag aussieht

Ein Vorgang heißt hier **Case** — ein Ordner unter `vault/case/` mit einem `README.md` darin. Er wird geöffnet, bearbeitet, gespeichert und abgeschlossen:

```bash
bibi-ctrl open "Kundenanfrage Q3"   # legt den Case an, parkt die Sitzung darauf
bibi-ctrl save                      # Status ins README, committen, ggf. pushen
bibi-ctrl done                      # abschließen
```

Ein Job entsteht, indem man eine Markdown-Datei schreibt:

```markdown
---
slug: tagesbericht
schedule: "0 7 * * *"
soul: Data
---

Lies die Journaleinträge von gestern und schreibe eine Zusammenfassung
nach `vault/memo/Bericht/`.
```

Mehr braucht es nicht. `bibi-ctrl rescan` erfasst sie, der Scheduler übernimmt.

## Die Verben

| | |
|---|---|
| `init` | Repo einrichten, Rollen und Remote festlegen |
| `status` `statusline` | Was ist gerade aktiv |
| `open` `save` `close` `done` `delete` | der Case-Zyklus |
| `sync` | git-Abgleich; `on`/`off`, bei Konflikt `continue`/`abort` |
| `daemon` | `run`/`install`/`uninstall`/`status`/`logs` |
| `job` | Jobs listen, zeigen, `start`/`kill`/`reset`, `rescan` |
| `run` | einen Job sofort lokal ausführen, ohne Scheduler |
| `at` | einmaligen Termin anlegen |
| `doctor` | Hygiene prüfen: LFS, große Blobs, Sammeldaten, Markdown-Konventionen |
| `mergeback` | unmergte `agent/*`-Branches zusammenführen |
| `soul` | Persona wechseln (`.claude/souls/*.SOUL.md`) |
| `protocol` | Turn-Logging im aktiven Case |
| `bootstrap-token` | Startschlüssel für den ersten Client |

## Das Rollenmodell

Eine Binary, vier Rollen, frei kombinierbar (`BIBI_ROLE` oder Flags):

- **`synchronizer`** — hält das Repo per git mit dem Remote im Gleichstand
- **`scheduler`** — hält die Job-Datenbank, plant und verteilt
- **`worker`** — führt Jobs aus
- **`controller`** — serviert die Weboberfläche unter `/-/`

Dazu der Modifikator **`--connect`**: ein Client hängt sich an einen entfernten Scheduler. `scheduler` und `connect` schließen sich aus.

Typische Zuschnitte: ein Server fährt `scheduler,worker,synchronizer`; ein Arbeitsplatz `synchronizer,controller` — mit `--connect`, wenn er sich einem Server anschließen soll, ohne wenn er allein arbeitet. **Ohne Server geht mehr, als man denkt:** Case-Zyklus, `bibi-ctrl run`, Vault, Oberfläche und `doctor` brauchen keinen. Nur zeitgesteuerte Ausführung und Verteilung über mehrere Rechner brauchen einen.

## Sicherheit — bitte vor dem Betrieb lesen

**bibi hat keine Benutzeranmeldung.** Ein Teil der Scheduler-Schnittstelle verlangt, dass der zugreifende Rechner vorher freigegeben wurde; ein größerer Teil verlangt es nicht. Eine Anmeldung von Personen gibt es nirgends — sie ist entworfen (`DESIGN §4.7`, als Reverse-Proxy mit OAuth2), aber nicht gebaut.

**Der Daemon bindet auf `0.0.0.0`.** Er lauscht auf jedem Netzwerkinterface und schützt sich nicht selbst.

Daraus folgt eine Betriebsbedingung, keine Empfehlung: **Der Port gehört in ein privates Netz** — VPN, Tailnet, Firewall — und nicht ins offene Internet. Wer bibi öffentlich erreichbar macht, gibt Operator-Zugriff ohne Login. Die Entwicklung dieses Projekts läuft unter genau dieser Annahme; sie ist der Grund, warum die Authentifizierung zurückgestellt wurde.

## Zur Sprache

**Dokumentation und Codekommentare sind durchgehend deutsch.** Das ist eine Entscheidung, kein Versehen und kein unfertiger Zustand. Die Oberfläche und die Skill-Texte sind englisch, ebenso `vault/CONVENTIONS.md` im Team-Repo. Wer hier mitliest und Deutsch nicht spricht: die Struktur ist in den Bezeichnern, und die sind englisch.

## `skills/` ist eine Vendoring-Quelle, keine Begleitdoku

Die `SKILL.md`-Dateien unter `skills/` sind nicht die Beschreibung eines Verhaltens, sondern die **kanonische Quelle**, aus der jedes Team-Repo seine `.claude/skills/` zieht (`/library use`, `/library sync`; siehe `library.yaml` im Team-Repo). Ein veralteter Skill-Text bleibt deshalb nicht in diesem Repo — er wird beim nächsten Sync in alle Instanzen kopiert und gilt dort als der aktuelle Stand.

**Wer das Verhalten eines Kommandos ändert, zieht seinen `SKILL.md`-Text im selben Commit mit.** Fällt das auseinander, entsteht kein Doku-Rückstand, sondern eine Regression mit Zeitzünder — sie schlägt erst zu, wenn jemand guten Glaubens vendort.

Präzedenzfall: PLAN-38 (`3a2daea`, 27.07.2026) stellte `/run` auf in-place gegen den Live-Checkout um und machte es Client-only, ließ `skills/bibi-run/SKILL.md` aber auf dem Stand vom 24.07. Der bibi-team-Backport `4932b6b` hob den Blueprint einen Tag später „auf kanonischen Stand" — und trug damit die abgeschaffte Worktree-Isolation dorthin zurück, wo sie neuen Teams als gültig erklärt wurde. Repariert mit `f500543`.

## Sync-Strategie

`git_ops.integrate()` kennt bei echter Divergenz zwei Strategien (`strategy="rebase"|"merge"`, Default `"rebase"`): Rebase für den interaktiven `sync`-Pfad — lineare Historie, ein Mensch löst einen Konflikt tatsächlich auf. Merge für den unbeaufsichtigten Hintergrund-Pull des Synchronizers (`daemon/synchronizer.py::_default_pull`): robuster gegen botgenerierte Commit-Historie, bei der ein Rebase an einem Zwischenschritt scheitern kann, obwohl der Endstand konfliktfrei mergen würde.

Ein dirty Working Tree ist dabei **kein** Konflikt, sondern ein eigener Ausgang (`dirty`): git beginnt den Rebase gar nicht erst, es gibt nichts aufzulösen.

## Entwicklung

```bash
uv venv && uv pip install -e .
uv run pytest -q          # schnelle Suite
uv run pytest -q --slow   # vollständig, mit echten Prozessen
```

Entwicklungszweig ist `dev`, Releases entstehen auf `master` und tragen einen Tag. Team-Repos pinnen auf einen Tag, nicht auf einen Branch — ein Branch wandert, ein Tag nicht.

**Engine-Arbeit ist test-zuerst**, und zwar mit dem Rot-Schritt: Test schreiben, fehlschlagen sehen, dann implementieren. Ein Test, der nie rot war, ist eine Behauptung und kein Nachweis. Verbindlich für alles, was Verhalten ändert; weicher für reine Darstellung.

## Issue-Tracking

Bugs und Change Requests laufen gebündelt für Engine, Blueprint und Instanzen. Welchem Repo eine Änderung gehört, ist ein **Label** (`repo:engine`, `repo:team`, `repo:notes`), kein Ablageort — die Zuordnung ergibt sich oft erst aus der Analyse und darf offen bleiben oder mehrfach gesetzt werden.

Auf der Dringlichkeitsachse gilt eine Regel: Wer ein `P` trägt, ist triagiert; wer keins trägt, wartet auf Triage. `P1` heißt *wichtig*, nicht *als nächstes* — die Reihenfolge steht im Umsetzungsplan, nicht in der Priorität.

## Lizenz

MIT — der vollständige Text steht in [`LICENSE`](LICENSE), er passt auf eine Bildschirmseite. Copyright bei Michael Rau und Plan.Net Journey GmbH & Co. KG. Wer die Engine weitergibt oder verändert, legt Copyright-Hinweis und Lizenztext bei; das ist die einzige Bedingung, die MIT stellt.
