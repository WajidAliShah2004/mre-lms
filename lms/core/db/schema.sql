-- LMS schema. SQLite, WAL mode.
--
-- Design rules that the tests enforce:
--   * artifacts.sha256 is UNIQUE — the same bytes are never filed twice.
--   * actions_log is append-only — there is no UPDATE or DELETE path to it.
--   * processed(sha256, stage) is the idempotency ledger — re-running any
--     stage on the same bytes is a no-op, so a crash mid-pipeline is safe
--     to simply re-run.
--   * corrections is never pruned. It is the training signal for the next
--     engagement and the only record of where the classifier was wrong.
--
-- Full, untruncated values always live here. Filenames are lossy by design
-- (200-char cap); the database is not.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;
PRAGMA synchronous = NORMAL;

-- ---------------------------------------------------------------------------
-- artifacts — one row per distinct set of bytes that entered the system.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS artifacts (
    id              INTEGER PRIMARY KEY,
    sha256          TEXT    NOT NULL UNIQUE,
    source          TEXT    NOT NULL,          -- email | imessage | photo | transcript | attachment
    source_ref      TEXT,                      -- message-id, chat guid, or source filename
    parent_id       INTEGER REFERENCES artifacts(id),   -- attachments point at their email
    original_name   TEXT,
    mime_type       TEXT,
    byte_size       INTEGER,
    doc_date        TEXT,                      -- the DOCUMENT's date, YYYY-MM-DD, may be NULL
    ingested_at     TEXT    NOT NULL,          -- ISO8601, America/New_York
    filed_path      TEXT,                      -- NULL until filing succeeds
    status          TEXT    NOT NULL DEFAULT 'INGESTED'
                    CHECK (status IN ('INGESTED','CLASSIFIED','FILED',
                                      'QUARANTINED','SUSPECTED_PHISHING','DUPLICATE'))
);

CREATE INDEX IF NOT EXISTS idx_artifacts_status  ON artifacts(status);
CREATE INDEX IF NOT EXISTS idx_artifacts_parent  ON artifacts(parent_id);
CREATE INDEX IF NOT EXISTS idx_artifacts_docdate ON artifacts(doc_date);

-- ---------------------------------------------------------------------------
-- classifications — the model's answer, kept with enough provenance to
-- reproduce or blame it. prompt_hash + model together identify exactly what
-- produced this row, so a bad prompt revision can be found and re-run.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS classifications (
    id              INTEGER PRIMARY KEY,
    artifact_id     INTEGER NOT NULL REFERENCES artifacts(id),
    domain          TEXT    NOT NULL CHECK (domain IN ('PERSONAL','BUSINESS','MIXED')),
    entity_id       TEXT    NOT NULL,          -- validated against entities.yaml in code
    category        TEXT    NOT NULL,
    subcategory     TEXT,
    urgency         TEXT    NOT NULL CHECK (urgency IN ('CRITICAL','HIGH','NORMAL','LOW','NONE')),
    confidence      REAL    NOT NULL CHECK (confidence >= 0.0 AND confidence <= 1.0),
    requires_reply  INTEGER NOT NULL DEFAULT 0,
    due_date        TEXT,
    counterparty    TEXT,
    descriptor      TEXT,
    amount_cents    INTEGER,                   -- NULL = no amount found
    currency        TEXT    DEFAULT 'USD',
    rationale       TEXT,                      -- <=200 chars, for the review queue
    model           TEXT    NOT NULL,
    prompt_hash     TEXT    NOT NULL,
    decided_by      TEXT    NOT NULL DEFAULT 'model'
                    CHECK (decided_by IN ('model','rule','human')),
    created_at      TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_class_artifact ON classifications(artifact_id);
CREATE INDEX IF NOT EXISTS idx_class_entity   ON classifications(entity_id);
CREATE INDEX IF NOT EXISTS idx_class_conf     ON classifications(confidence);

-- ---------------------------------------------------------------------------
-- tasks — the task ledger (v2, D-066). One durable record per obligation,
-- however many sources mention it. One list, filtered by person or entity at
-- read time.
--
-- The fields are the addendum's §2 list (Sept 28), in its order where it can
-- be kept; core/pipeline/ledger.py maps each one. The state machine lives in
-- ledger.py too: the CHECK below only refuses names that do not exist, the
-- code refuses moves that are not allowed.
--
-- Databases created before v2 are rebuilt in place by core/db/migrate.py,
-- which runs before this file on every connect.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tasks (
    id                  INTEGER PRIMARY KEY,   -- the task ID; stable, never reused
    -- One obligation, one key. NULL when the evidence is too thin to say two
    -- mentions are the same obligation (then they stay two tasks).
    event_key           TEXT    UNIQUE,
    -- source and link: the FIRST source. Every source is in task_sources.
    source              TEXT    NOT NULL DEFAULT 'manual',
    source_link         TEXT,
    source_artifact     INTEGER REFERENCES artifacts(id),
    received_at         TEXT,
    owner               TEXT,                  -- one accountable owner
    entity_id           TEXT,
    domain              TEXT    CHECK (domain IS NULL
                                       OR domain IN ('PERSONAL','BUSINESS','MIXED')),
    person              TEXT,
    title               TEXT    NOT NULL,      -- the addendum's "subject"
    detail              TEXT,
    requested_outcome   TEXT,
    extraction_confidence REAL  CHECK (extraction_confidence IS NULL OR
                                       (extraction_confidence >= 0.0 AND
                                        extraction_confidence <= 1.0)),
    urgency             TEXT    NOT NULL DEFAULT 'NORMAL',   -- the priority
    due_date            TEXT,
    -- "A detected due date is provisional until verified against the source."
    due_date_verified   INTEGER NOT NULL DEFAULT 0 CHECK (due_date_verified IN (0,1)),
    assigned_agent      TEXT,
    permitted_tools     TEXT    NOT NULL DEFAULT '[]',       -- JSON array
    sensitivity         TEXT    NOT NULL DEFAULT 'normal'    -- spec §5.6
                        CHECK (sensitivity IN ('normal','high','private')),
    -- Approval-matrix level (addendum §3): A observe, B prepare,
    -- C routine execution, D exception. NULL = not assessed, treated as D.
    action_risk         TEXT    CHECK (action_risk IS NULL
                                       OR action_risk IN ('A','B','C','D')),
    approval_policy     TEXT,
    approver            TEXT,
    status              TEXT    NOT NULL DEFAULT 'DETECTED'
                        CHECK (status IN (
                            'DETECTED','CLASSIFIED','TRIAGED','DELEGATED',
                            'IN_PROGRESS','PREPARED','AWAITING_APPROVAL',
                            'EXECUTED','VERIFIED','CLOSED',
                            'NEEDS_CLARIFICATION','BLOCKED','FAILED',
                            'DUPLICATE','CANCELED')),
    status_changed_at   TEXT,
    retry_count         INTEGER NOT NULL DEFAULT 0 CHECK (retry_count >= 0),
    artifact_links      TEXT    NOT NULL DEFAULT '[]',       -- JSON array
    verification_evidence TEXT,
    -- DONE | DISMISSED | DENIED | DUPLICATE | FAILED. Why it ended, which the
    -- status alone does not say: DONE and DISMISSED are both "no longer open"
    -- and only one of them is a defect report (see pipeline/tasks.py).
    final_outcome       TEXT,
    duplicate_of        INTEGER REFERENCES tasks(id),
    mirrored_reminder   TEXT,                  -- Apple Reminders id, one-way
    created_at          TEXT    NOT NULL,
    completed_at        TEXT,
    CHECK (status != 'DUPLICATE' OR duplicate_of IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_due    ON tasks(due_date);
CREATE INDEX IF NOT EXISTS idx_tasks_entity ON tasks(entity_id);
CREATE INDEX IF NOT EXISTS idx_tasks_owner  ON tasks(owner);

-- ---------------------------------------------------------------------------
-- task_sources — every source event that is evidence for a task. A forwarded
-- email, the invoice attached to it and the bank line that pays it are three
-- rows here and one row in tasks.
--
-- source_key is the idempotency key for the SOURCE EVENT ("artifact:<sha256>",
-- "imessage:<guid>"): re-reading the same message is a no-op. The ledger only
-- stores links; it never opens, moves or rewrites a source (R8).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS task_sources (
    id              INTEGER PRIMARY KEY,
    task_id         INTEGER NOT NULL REFERENCES tasks(id),
    source_key      TEXT    NOT NULL UNIQUE,
    source          TEXT    NOT NULL,
    source_link     TEXT,
    artifact_id     INTEGER REFERENCES artifacts(id),
    received_at     TEXT,
    confidence      REAL,
    seen_at         TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_task_sources_task ON task_sources(task_id);

-- ---------------------------------------------------------------------------
-- task_dependencies — "dependencies". A task waits on the tasks it lists.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS task_dependencies (
    task_id         INTEGER NOT NULL REFERENCES tasks(id),
    depends_on      INTEGER NOT NULL REFERENCES tasks(id),
    created_at      TEXT    NOT NULL,
    PRIMARY KEY (task_id, depends_on),
    CHECK (task_id != depends_on)
);

-- ---------------------------------------------------------------------------
-- task_events — the task's audit trail. Append-only like actions_log, and for
-- the same reason. "Every action stores the exact document version,
-- destination, recipient and amount where applicable": those are columns
-- here, not prose in `detail`, so they can be compared before execution.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS task_events (
    id              INTEGER PRIMARY KEY,
    task_id         INTEGER NOT NULL REFERENCES tasks(id),
    ts              TEXT    NOT NULL,
    actor           TEXT    NOT NULL,          -- human | ingest | an agent's name
    event           TEXT    NOT NULL,          -- CREATED | TRANSITION | EVIDENCE | ...
    from_status     TEXT,
    to_status       TEXT,
    detail          TEXT,
    doc_version     TEXT,                      -- sha256 of the exact document acted on
    destination     TEXT,
    recipient       TEXT,
    amount_cents    INTEGER,
    approval_ref    TEXT
);

CREATE INDEX IF NOT EXISTS idx_task_events_task ON task_events(task_id);

CREATE TRIGGER IF NOT EXISTS task_events_no_update
BEFORE UPDATE ON task_events
BEGIN
    SELECT RAISE(ABORT, 'task_events is append-only');
END;

CREATE TRIGGER IF NOT EXISTS task_events_no_delete
BEFORE DELETE ON task_events
BEGIN
    SELECT RAISE(ABORT, 'task_events is append-only');
END;

-- ---------------------------------------------------------------------------
-- actions_log — append-only. Every side effect the system takes, including
-- the ones it declined to take. Triggers below make append-only real rather
-- than a convention.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS actions_log (
    id          INTEGER PRIMARY KEY,
    ts          TEXT    NOT NULL,
    action      TEXT    NOT NULL,
    artifact_id INTEGER REFERENCES artifacts(id),
    detail      TEXT,
    approved_by TEXT,                          -- NULL unless a human tapped approve
    undo_token  TEXT,                          -- set for reversible actions
    undo_expires_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_actions_ts ON actions_log(ts);

CREATE TRIGGER IF NOT EXISTS actions_log_no_update
BEFORE UPDATE ON actions_log
BEGIN
    SELECT RAISE(ABORT, 'actions_log is append-only');
END;

CREATE TRIGGER IF NOT EXISTS actions_log_no_delete
BEFORE DELETE ON actions_log
BEGIN
    SELECT RAISE(ABORT, 'actions_log is append-only');
END;

-- ---------------------------------------------------------------------------
-- corrections — every time Matthew overrides the system. Never pruned.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS corrections (
    id          INTEGER PRIMARY KEY,
    artifact_id INTEGER REFERENCES artifacts(id),
    field       TEXT    NOT NULL,
    old_value   TEXT,
    new_value   TEXT,
    ts          TEXT    NOT NULL
);

-- ---------------------------------------------------------------------------
-- processed — idempotency ledger. (sha256, stage) is the whole point.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS processed (
    sha256      TEXT    NOT NULL,
    stage       TEXT    NOT NULL,              -- ingest | ocr | classify | file | notify
    ts          TEXT    NOT NULL,
    PRIMARY KEY (sha256, stage)
);

-- ---------------------------------------------------------------------------
-- duplicate_refs — a re-fed file is not filed again, but the fact that it
-- arrived again is worth keeping (it usually means a resend or a chase).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS duplicate_refs (
    id              INTEGER PRIMARY KEY,
    sha256          TEXT    NOT NULL,
    seen_at         TEXT    NOT NULL,
    source          TEXT    NOT NULL,
    source_ref      TEXT,
    original_name   TEXT
);

CREATE INDEX IF NOT EXISTS idx_dupe_sha ON duplicate_refs(sha256);
