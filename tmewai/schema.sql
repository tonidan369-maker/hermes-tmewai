-- tmewai schema: the seven Dart tables, snake_case, plus the FTS5 index.
--
-- Placeholders of the form {{fold(EXPR)}} are rendered by importer.py from the
-- single folding table in folds.py, so the triggers and the query normalizer
-- always use the same normalisation.

CREATE TABLE IF NOT EXISTS imports (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    platform           TEXT    NOT NULL,
    format             TEXT    NOT NULL,
    source_fingerprint TEXT    NOT NULL,
    started_at         TEXT    NOT NULL,
    completed_at       TEXT,
    importer_version   TEXT    NOT NULL,
    status             TEXT    NOT NULL,
    coverage_json      TEXT
);

CREATE TABLE IF NOT EXISTS source_files (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    import_id     INTEGER NOT NULL REFERENCES imports(id) ON DELETE CASCADE,
    relative_path TEXT    NOT NULL,
    media_type    TEXT,
    content_hash  TEXT,
    parse_status  TEXT    NOT NULL,
    error_code    TEXT,
    UNIQUE(import_id, relative_path)
);
CREATE INDEX IF NOT EXISTS source_files_import_id ON source_files(import_id);

CREATE TABLE IF NOT EXISTS people (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    platform           TEXT NOT NULL,
    platform_person_id TEXT,
    display_name       TEXT NOT NULL,
    UNIQUE(platform, platform_person_id)
);

-- A Facebook export carries no stable person id, so the folded display name IS
-- the identity (DECISIONS.md). The UNIQUE above cannot enforce that because
-- SQLite treats every NULL platform_person_id as distinct, which let a second
-- import insert a second 'Layla' row and orphan the first one's events.
CREATE UNIQUE INDEX IF NOT EXISTS people_identity ON people(platform, display_name);

CREATE TABLE IF NOT EXISTS conversations (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    import_id              INTEGER NOT NULL REFERENCES imports(id) ON DELETE CASCADE,
    source_conversation_id TEXT,
    title                  TEXT
);
CREATE INDEX IF NOT EXISTS conversations_import_id ON conversations(import_id);

CREATE TABLE IF NOT EXISTS events (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    import_id            INTEGER NOT NULL REFERENCES imports(id) ON DELETE CASCADE,
    source_file_id       INTEGER NOT NULL REFERENCES source_files(id) ON DELETE CASCADE,
    source_locator       TEXT    NOT NULL,
    platform             TEXT    NOT NULL,
    event_type           TEXT    NOT NULL,
    timestamp_raw        TEXT,
    timestamp_normalized TEXT,
    timezone_status      TEXT    NOT NULL,
    actor_id             INTEGER REFERENCES people(id) ON DELETE SET NULL,
    conversation_id      INTEGER REFERENCES conversations(id) ON DELETE SET NULL,
    text_content         TEXT,
    metadata_json        TEXT,
    UNIQUE(import_id, source_locator)
);
CREATE INDEX IF NOT EXISTS events_import_id ON events(import_id);
CREATE INDEX IF NOT EXISTS events_timestamp_normalized ON events(timestamp_normalized);
CREATE INDEX IF NOT EXISTS events_event_type ON events(event_type);
CREATE INDEX IF NOT EXISTS events_actor_id ON events(actor_id);

-- Metadata only: what was approved and how much, never the excerpt text.
CREATE TABLE IF NOT EXISTS ai_consents (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    provider_id    TEXT    NOT NULL,
    policy_version TEXT    NOT NULL,
    scope_summary  TEXT    NOT NULL,
    granted_at     TEXT    NOT NULL,
    event_count    INTEGER NOT NULL,
    byte_count     INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS attachments (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id      INTEGER REFERENCES events(id) ON DELETE CASCADE,
    source_file_id INTEGER NOT NULL REFERENCES source_files(id) ON DELETE CASCADE,
    relative_path TEXT,
    media_type    TEXT    NOT NULL,
    size_bytes    INTEGER NOT NULL,
    content_hash  TEXT
);
CREATE INDEX IF NOT EXISTS attachments_event_id ON attachments(event_id);

CREATE VIRTUAL TABLE IF NOT EXISTS event_search USING fts5(
    event_id UNINDEXED,
    text_content,
    source_locator,
    tokenize = 'unicode61 remove_diacritics 2'
);

CREATE TRIGGER IF NOT EXISTS events_search_after_insert
AFTER INSERT ON events BEGIN
    INSERT INTO event_search (event_id, text_content, source_locator)
    VALUES (
        new.id,
        {{fold(new.text_content)}},
        {{fold(new.source_locator)}}
    );
END;

CREATE TRIGGER IF NOT EXISTS events_search_after_update
AFTER UPDATE ON events BEGIN
    DELETE FROM event_search WHERE event_id = old.id;
    INSERT INTO event_search (event_id, text_content, source_locator)
    VALUES (
        new.id,
        {{fold(new.text_content)}},
        {{fold(new.source_locator)}}
    );
END;

CREATE TRIGGER IF NOT EXISTS events_search_after_delete
AFTER DELETE ON events BEGIN
    DELETE FROM event_search WHERE event_id = old.id;
END;
