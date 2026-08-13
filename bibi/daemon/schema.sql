-- Job-DB-Schema v1 (DESIGN §5.4/§5.5/§1.4; PLAN-3 §3.1).
--
-- Zwei Tabellen:
--   jobs    — Live-Registry: ein Eintrag je Schedule mit aktuellem Lifecycle-
--             Zustand (§5.4). Schlüssel ist der Slug (stabil); `schedule_ref`
--             trägt den MD-Pfad fürs Reconcile.
--   journal — append-only Lauf-Historie (§1.4). OUTPUT-FREI: `output_ref` zeigt
--             auf die output.jsonl beim Worker, kein Blob in der DB (anders als
--             bibi3' stdout_blob). `host`/`worker` first-class (föderierte A13-Sicht).
--
-- Zeitstempel sind REAL (Unix-Epoch); Trigger-Rohwerte bleiben als Text.

CREATE TABLE IF NOT EXISTS jobs (
    id              TEXT PRIMARY KEY,          -- Scheduler-vergebene Hash-ID (§4.4)
    slug            TEXT NOT NULL UNIQUE,
    -- Job-Identität: md5(slug), ordnerübergreifend (v20, Zustandsmodell §6).
    -- Anders als `id` nicht vergeben, sondern abgeleitet — deshalb auf jedem
    -- Knoten derselbe Wert, ohne dass die Knoten sich abstimmen. Das ist der
    -- Join-Schlüssel der kombinierten Lauf-Liste. Ein gepinnter Lauf trägt den
    -- uid seines *Basis*-Slugs, nicht den seines Suffix-Slugs.
    job_uid         TEXT,
    schedule_ref    TEXT NOT NULL,             -- MD-Pfad relativ zum Vault
    kind            TEXT NOT NULL,             -- job | claude | app (§5.3)
    payload         TEXT NOT NULL,             -- Shell-Cmd | Prompt | Entrypoint

    -- Trigger (§5.2)
    schedule        TEXT,                      -- cron | now | startup | never
    at_iso          TEXT,                      -- ISO 8601 (naive local), Anzeige
    next_fire_at    REAL,                      -- berechnet (croniter/at)

    -- Scheduler-Auswahl (§4.4)
    priority        INTEGER NOT NULL DEFAULT 0,
    enqueued_at     REAL,

    -- claude/app-Felder (§5.3)
    model           TEXT,
    soul            TEXT,
    session         TEXT,
    app_port        INTEGER,
    app_prefix      TEXT,
    exec_mode       TEXT,
    image           TEXT,
    docker_args     TEXT,                      -- JSON-Liste, roher `docker run`-Escape-Hatch (§7.6a)

    -- Lifecycle-Stellschrauben (§5.5) — vom Worker ausgewertet (Stufe 3.5)
    attempts        INTEGER NOT NULL DEFAULT 1,
    backoff         TEXT NOT NULL DEFAULT 'fixed',
    silence_timeout INTEGER NOT NULL DEFAULT 3600,
    wall_time       INTEGER,
    defer_time      INTEGER,
    defer_max       INTEGER,
    error_time      INTEGER,

    -- Registrierung (PLAN-14 Stufe 14.5): ist die MD noch im Vault entdeckt?
    -- Orthogonal zum Lifecycle-Status — ein `error`-Job kann sein MD genauso
    -- verlieren wie ein `pending`-Job. rescan() markiert verschwundene Slugs
    -- als inactive statt die Zeile zu löschen (Journal-Historie bleibt erreichbar).
    active          INTEGER NOT NULL DEFAULT 1,
    -- Slug-Kollision (#142): JSON-Liste der MDs, die denselben Slug
    -- beanspruchen; NULL = kein Konflikt. Ein eigener Zustand neben `active`,
    -- nicht in ihm: `active` sagt "MD im Vault vorhanden", und bei einer
    -- Kollision sind *beide* vorhanden. Solange die Spalte gefüllt ist, ist der
    -- Job nicht startbar — welche der beiden MDs gälte, ist nicht entscheidbar.
    conflict_refs   TEXT,

    -- Live-Zustand (§5.4/§5.5)
    status          TEXT NOT NULL DEFAULT 'pending',
    reason          TEXT,
    attempt         INTEGER NOT NULL DEFAULT 0,
    fire            INTEGER NOT NULL DEFAULT 0, -- Zähler je Trigger (cron-Recurrence, §5.2)
    -- Die Lauf-Attribute (#129): `job_full_view()` als JSON, geschrieben bei
    -- START in derselben Transaktion wie das Status-UPDATE, unveränderlich für
    -- die Dauer des Laufs. Die übrigen Spec-Spalten dieser Zeile folgen jedem
    -- Rescan — sie sind eine Projektion der MD, kein Speicher. Wer zur Laufzeit
    -- wissen will, womit *dieser* Lauf läuft, liest hier. Eine Spalte statt 26
    -- duplizierter: jedes neue Attribut kostet sonst zwei Migrationen.
    run_snapshot    TEXT,
    deferred_at     REAL,                      -- erster Defer-Zeitpunkt (§5.5 defer_max)
    -- Beginn des *aktuellen* Versuchs. Gesetzt bei jeder Reservierung, geräumt
    -- beim Zustandswechsel — und genau deshalb der Ort, aus dem `exec_runtime`
    -- die Netto-Zeit dieses Versuchs bezieht (#167). Ein eigenes Feld dafür
    -- brauchte es nicht: dieses trägt den Wert bereits so lange, wie er
    -- gebraucht wird.
    locked_at       REAL,
    -- Der **initiale** Start des Jobs, nicht der des Versuchs (#166). Ein Retry
    -- und ein Resume lassen ihn stehen; neu gesetzt wird er nur, wo ein neuer
    -- Aufenthalt beginnt (aus `pending` und `complete`). `finished_at −
    -- started_at` ist damit die Brutto-Zeit, die Aufenthaltsdauer im Scheduler.
    started_at      REAL,
    finished_at     REAL,
    -- Kumulierte **Netto**-Zeit über alle Versuche (#167, v26): bei einem neuen
    -- Aufenthalt auf 0, danach summiert jeder beendete Versuch seine eigene
    -- Dauer auf. Sie fällt mit der Brutto-Zeit nur im Einfachfall zusammen —
    -- einem Job ohne Retry und ohne Deferral —, und genau dort fällt ein Fehler
    -- nicht auf.
    exec_runtime    REAL,
    exit_code       INTEGER,
    host            TEXT,
    worker          TEXT,
    output_ref      TEXT,                      -- referenziert output.jsonl (§1.4)
    -- (v21) Worktree-Commit des laufenden/blockierten Laufs. Der Slot hält ihn,
    -- solange der Lauf dort steht: unter der Archivierungsregel A2 wandert ein
    -- terminaler Fehler erst auf START/RESET ins Journal, und bis dahin gäbe es
    -- sonst keinen Ort für die Verbindung Lauf ↔ Vault-Wirkung.
    commit_sha      TEXT,
    branch          TEXT,                      -- agent/<slug>
    pid             INTEGER,                   -- Wrapper-PID (v9, Orphan-Erkennung §10.2)
    pid_started_at  TEXT,                      -- Prozess-Startzeit opak (PID-Recycling-Guard)
    app_url         TEXT,                      -- HITL-Eingabe-Endpunkt der App (v10, §10.4)
    last_ping_at    REAL,                      -- letzter Ping-Timestamp (v11, Zombie-Timeout §2.5)
    demand          TEXT,                      -- HITL-Demand JSON (v11, §11.2)
    pinned_host     TEXT,                      -- (v15, PLAN-28) NULL = jeder Worker; gesetzt =
                                                -- nur dieser Host darf reservieren (reserve_next())

    created_at      REAL,
    updated_at      REAL
);

CREATE INDEX IF NOT EXISTS jobs_dispatch_idx
    ON jobs (status, priority DESC, enqueued_at ASC);

CREATE TABLE IF NOT EXISTS journal (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id        TEXT NOT NULL,               -- slug:trial, konstant über Retries
    slug          TEXT NOT NULL,
    job_uid       TEXT,                        -- (v20) geerbt aus jobs.job_uid — nicht
                                               -- erneut aus dem eigenen Slug abgeleitet,
                                               -- sonst verlöre ein gepinnter Lauf den
                                               -- Bezug zu seinem Basis-Job.
    kind          TEXT NOT NULL,
    status        TEXT NOT NULL,
    reason        TEXT,
    started_at    REAL,
    finished_at   REAL,
    exit_code     INTEGER,
    exec_runtime  REAL,
    host          TEXT,
    worker        TEXT,
    output_ref    TEXT,                        -- referenziert, enthält nicht (§1.4)
    commit_sha    TEXT,                        -- Worktree-Commit des Laufs (v6, F7-Link)
    branch        TEXT,                        -- agent/<slug> (v6)
    payload       TEXT,                        -- Shell-Cmd | Prompt (v12, Ausgabefilter PLAN-12)
    snapshot      TEXT NOT NULL DEFAULT '{}',
    archived_at   REAL NOT NULL,
    -- Ausführungs-Domäne (§1.4): 'scheduled' (disponiert, über den Scheduler) vs.
    -- 'local' (CLI bibi-ctrl run, /run vor PLAN-28 — umgeht den Scheduler
    -- vollständig, kein jobs-Eintrag). Föderierte A13-Sicht.
    domain        TEXT NOT NULL DEFAULT 'scheduled',
    -- (v16, PLAN-28) Spiegelt jobs.pinned_host zum Schreibzeitpunkt — /run
    -- über run_pinned() bekommt jetzt domain='scheduled' (echte jobs-Zeile,
    -- volle Lifecycle), bleibt aber über pinned_host als "meine eigene
    -- /run-Historie" von echten Team-Queue-Läufen unterscheidbar
    -- (/-/run/journal filtert domain='local' OR pinned_host IS NOT NULL).
    pinned_host   TEXT
);

CREATE INDEX IF NOT EXISTS journal_slug_idx
    ON journal (slug, archived_at DESC);

-- (v20) Der Zugriffspfad der Lauf-Liste: alle Läufe eines Jobs, jüngste zuerst.
CREATE INDEX IF NOT EXISTS journal_job_uid_idx
    ON journal (job_uid, archived_at DESC);

-- Append-only Lifecycle-Übergänge (Schema v14) — anders als journal (nur der
-- Terminal-Übergang) jeder Statuswechsel, inkl. running/awaiting/failed/
-- deferred/pending. Grundlage für rückblickende Zeitreihen (z. B. "wie viele
-- Jobs waren je Status über die letzten 24h aktiv") — journal allein kann das
-- nicht: es hat nur eine Zeile pro Lauf mit Endstatus, Zwischenzustände wie
-- eine mehrstündige awaiting-Phase gehen dort verloren.
CREATE TABLE IF NOT EXISTS transitions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id      TEXT NOT NULL,
    slug        TEXT NOT NULL,
    from_status TEXT,                          -- NULL bei Neuanlage (erster Zustand)
    to_status   TEXT NOT NULL,
    ts          REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS transitions_ts_idx
    ON transitions (ts);

-- Scheduler-Zustand als key/value (Schema v2). Hält u. a. den Fairness-Cursor
-- `dispatcher_offset` (§4.4) — ein read-modify-write, das in der Reservierungs-
-- Transaktion (BEGIN IMMEDIATE) mit der Job-Auswahl serialisiert wird.
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- Open-Trust-Connect-Gate (PLAN-32 Stufe 32.1, Schema v18). Freischaltung
-- eines per Heartbeat gemeldeten Knotens ist eine Host-Entscheidung, kein
-- Client-Selbstbericht — bewusst hier statt im In-Memory-WorkerRegistry-Dict,
-- sonst würde ein Host-Neustart alle Freischaltungen löschen.
CREATE TABLE IF NOT EXISTS approved_nodes (
    node_id    TEXT PRIMARY KEY,
    status     TEXT NOT NULL DEFAULT 'pending',   -- pending | approved | blocked
    updated_at REAL NOT NULL
);

-- Startschlüssel für den ersten Client (m.rau/bibi#141, Schema v22). Er löst
-- den Deadlock, den die Schranke an approve/block erst erzeugt: ein frischer
-- Scheduler hat null approved-Knoten, und ohne Host-FE ist niemand
-- berechtigt, den ersten freizugeben.
--
-- Ausdrücklich kein Wiedergänger des abgeschafften BIBI_CONNECT_SECRET —
-- einer, einmal, befristet. `expires_at` steht deshalb in der Zeile und nicht
-- im Aufrufer: das Einlösen ist ein einziges DELETE mit beiden Bedingungen,
-- und dazwischen kann nichts passieren. Eine eingelöste Zeile wird gelöscht,
-- nicht markiert; nachlesbar bleibt der Vorgang über `connect.bootstrapped`.
CREATE TABLE IF NOT EXISTS bootstrap_tokens (
    token      TEXT PRIMARY KEY,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL
);
