# tmewai

A Hermes plugin that turns a **Facebook JSON export ZIP** into a local,
searchable SQLite index. It is a Python-stdlib port of the Tmewai Dart app's
import model: the same seven tables, the same Arabic folding table, the same
FTS5 triggers, the same ZIP safety rules, and the same four JSON shapes
(messages, comments including `comments_v2`, likes/reactions, posts).

Everything is local. The plugin never fetches anything, never extracts the
archive to disk, and never sends an evidence bundle anywhere.

## What it does

- **Validates without extracting.** Absolute paths, `..`, drive paths, NUL
  bytes, duplicate/ambiguous paths and symlinks are rejected before anything is
  read. JSON entries are read in memory; media is only ever stat'ed and
  optionally hashed within a byte budget.
- **Records every file.** Every non-directory ZIP entry gets exactly one
  `source_files` row with a parse status and a content-free reason code, so no
  file is silently dropped. A malformed or hostile JSON entry (invalid UTF-8,
  a BOM, deep nesting, an oversized integer) becomes one `unsupported` row plus
  a warning; it never aborts the import.
- **Folds Arabic for search only.** Harakat, alef variants, taa marbuta, alef
  maqsura and tatweel are folded in the FTS5 index and in queries from one
  shared table. Stored event text keeps its original spelling.
- **Imports idempotently.** A completed import is matched by a content
  fingerprint; importing the same archive again reuses it and inserts zero new
  rows.
- **Bounds evidence.** At most 20 events and 4000 characters, with a consent row
  that stores provider, policy, counts and bytes only — never excerpt text.

## Tools

| Tool | Arguments | Writes DB? | Behaviour |
|---|---|---|---|
| `archive_scan` | `path` | no | Validate and classify every entry; coverage counts, categories, up to 20 unclassified paths. |
| `archive_ingest` | `path`, `platform`=`facebook` | yes | Scan + import in one transaction; returns inserted counts, per-status file counts, event-type counts, totals. |
| `archive_search` | `query`, `event_type`?, `after`?, `before`?, `limit`=20 | no | Folded FTS5 search joined to events/people; compact rows with id, ts, type, person, snippet, source_locator. |
| `archive_person` | `name` | no | Exact then substring name resolution; totals, date range, top conversations, 10 recent events. |
| `archive_overview` | — | no | Totals by type, date range, top 10 correspondents, top 10 conversations, imports list. |
| `archive_evidence` | `event_ids`, `provider_id`, `policy_version` | consent row | Bounded bundle (20 events / 4000 chars) plus one metadata-only `ai_consents` row; dispatches nothing. |

`archive_scan`, `archive_search`, `archive_person` and `archive_overview` are
registered read-only so they can run unattended. The registration helper passes
`read_only=True` when the host accepts it and retries without it otherwise.

## Enable it in Hermes

1. Copy the plugin directory into the Hermes plugins directory. The directory
   name must stay `tmewai` (it must match `plugin.yaml: name`):

   ```bash
   cp -R tmewai "${HERMES_HOME:-$HOME/.hermes}/plugins/tmewai"
   ```

   `HERMES_HOME` defaults to `~/.hermes` when it is unset.

2. Add the plugin to `plugins.enabled` in `$HERMES_HOME/config.yaml` (keep any
   entries already there):

   ```yaml
   plugins:
     enabled:
       - tmewai
   ```

3. Validate the manifest, then restart the gateway:

   ```bash
   hermes plugins validate tmewai
   hermes gateway restart
   ```

4. The six tools appear in the `tmewai` toolset: `archive_scan`,
   `archive_ingest`, `archive_search`, `archive_person`, `archive_overview`,
   `archive_evidence`.

No API keys, environment variables or network access are required. The database
lives at `$HERMES_HOME/plugin-data/tmewai/archive.db`
(`~/.hermes/plugin-data/tmewai/archive.db` when `HERMES_HOME` is unset).
`TMEWAI_DB_PATH` overrides the full database path.

Requirements: Python 3.10+ with SQLite compiled with FTS5 (the standard CPython
build has it). No third-party packages.

## Measured performance

Measured on this host (Python 3.14.9, SQLite 3.53.1, Linux x86-64) with
`make demo` and `make big`; both runs use `scripts/ingest_archive.py`, which
calls the real tool handlers and imports each archive twice.

### Bundled demo — `reference/demo/facebook-export.zip`

244 entries, 63 JSON, 181 JPEG, 197,292 bytes:

| Measure | Result |
|---|---|
| First import | **0.373 s**, `status: "imported"` |
| Second import | **0.037 s**, `status: "reused"`, zero rows added |
| `source_files` | 244 (63 `parsed`, 181 `unsupported` media) |
| Events | 3,314 (`message` 2,414; `reaction` 400; `comment` 260; `post` 240) |
| Attachments | 291 |
| People / conversations | 325 / 60 |
| Database / peak RSS | 2.4 MB / 27.9 MB |

### Generated large archive — 400 conversations, 2,000 posts, 6,000 reactions, 3,000 comments

`python3 reference/fixtures/make_export.py build/big-export` produces 1,806
entries — 405 JSON (403 parsed, 2 known but unsupported shapes) and 1,401
images — in 1,482,745 bytes:

| Measure | Result |
|---|---|
| First import | **2.663 s**, `status: "imported"` |
| Second import | **0.316 s**, `status: "reused"`, zero rows added |
| `source_files` | 1,806 (403 `parsed`, 1,401 `unsupported_media`, 2 `known_not_imported`) |
| Events | 26,765 (`message` 15,765; `reaction` 6,000; `comment` 3,000; `post` 2,000) |
| Attachments | 2,126 |
| People / conversations | 385 / 400 |
| Database / peak RSS | 17.7 MB / 54.2 MB |
| Coverage reasons | `supported` 403, `unsupported_media` 1,401, `known_not_imported` 2 |

Both runs report `"idempotent": true`, which the script computes by comparing
every table's row count before and after the second ingest.

## Tests

```bash
make test        # or: python3 -m unittest discover -s tests -v
```

54 tests cover folding, all four JSON shapes, ZIP refusals, one-row-per-file
coverage, FTS trigger upkeep, idempotency, evidence bounds and the no-network
grep. The hardening cases added after review include traversal hidden by
whitespace padding, BOM-prefixed JSON, deep nesting, oversized integers,
second-vs-millisecond timestamps, duplicate evidence ids, whitespace-only
evidence text and the UTF-8 byte ceiling. See `DECISIONS.md` for the decisions
and fixture discrepancies, and `../REPORT.md` for the measured review record.
