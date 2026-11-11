# Changelog

All notable changes to the tmewai plugin. Version numbers follow `plugin.yaml`.

## 1.0.1

Two data-correctness fixes, both reproduced against a real SQLite index before
the change and covered by regression tests after it.

### Fixed — one person row per name across imports

A Facebook export carries no stable person id, so the folded display name is the
identity. `UNIQUE(platform, platform_person_id)` could not enforce that because
SQLite treats every `NULL` as distinct, so each import inserted its own row per
name. `archive_person` resolves `rows[0]`, so after a second import it reported
only the older import's events: on a 12-conversation plus 30-conversation pair it
showed 274 of 920 events and the rest were invisible.

- `schema.sql`: `people_identity` unique index on `(platform, display_name)`.
- `ensure_schema`: merges pre-existing duplicate rows, repointing `events.actor_id`
  to the oldest id before the extras are deleted, so old databases repair
  themselves on the next open.
- `ingest`: reuses the existing person row instead of inserting a second one.

### Fixed — Arabic search missed digits and contextual forms

- Text stored as `سنة ٢٠٢٠` returned zero hits for a `2020` query. Arabic-Indic
  (`٠-٩`) and Persian (`۰-۹`) digits now fold to ASCII, in both directions.
- Text stored with presentation forms (`ﻻ ﻳﻮﺟﺪ`, typical of PDF and Office pastes)
  did not match `لا يوجد`. `U+FE70..U+FEFF` now expands through its NFKC
  decomposition before the letter folds.
- The new tables feed the Python query normalizer and the SQL trigger chain from
  the same source, and a test asserts both produce identical output for a sample
  set. Existing archives get their FTS index rebuilt by `ensure_schema`'s drift
  check.
- Import throughput on a 25k-event fixture moves from 13.2k to 7.6k events/s;
  700k events still import in roughly 92 seconds.

### Tests

57 → 61 tests. New coverage: person identity across imports, migration of a
database that already holds duplicates, digit and presentation-form folding,
Python/SQL fold agreement, and stale-index rebuild.

## 1.0.0

First release: scan, ingest (idempotent, content-fingerprinted), search with
Arabic folding, person and overview reports, and the bounded consented evidence
bundle. Standard library only, no network.
