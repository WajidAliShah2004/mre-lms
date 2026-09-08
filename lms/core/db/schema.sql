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
-- tasks — the to-do list. One list, filtered by person or entity at read time.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tasks (
    id                  INTEGER PRIMARY KEY,
    title               TEXT    NOT NULL,
    detail              TEXT,
    due_date            TEXT,
    entity_id           TEXT,
    person              TEXT,
    urgency             TEXT    NOT NULL DEFAULT 'NORMAL',
    status              TEXT    NOT NULL DEFAULT 'OPEN'
                        CHECK (status IN ('OPEN','DONE','DISMISSED')),
    source_artifact     INTEGER REFERENCES artifacts(id),
    mirrored_reminder   TEXT,                  -- Apple Reminders id, one-way
    created_at          TEXT    NOT NULL,
    completed_at        TEXT
);

CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_due    ON tasks(due_date);

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
