# Decisions

Port first, invent last. Where the brief and the reference/fixture disagree,
the conflict is recorded here with the measured numbers.

## Layout

- The plugin package is `tmewai/` (`plugin.yaml`, `__init__.py`, `schema.sql`,
  `folds.py`, `importer.py`, `coverage.py`, `gate.py`).
- Tests live at the repository root in `tests/`, so
  `python3 -m unittest discover -s tests -v` works verbatim from this
  directory.
- `reference/` is untouched.

## Fold table: 29 codepoints, not 30

`search_text.dart` declares **29** entries, not 30. The table is ported
exactly as written (21 harakat/marks, superscript alef, 4 alef forms, alef
maqsura, taa marbuta, tatweel). `folds.py` is the single source for both the
Python query normalizer and the generated SQL `replace()` chain in the FTS5
triggers. `ensure_schema` compares the stored trigger token stream with the
generated one and, if the folds ever change, drops the triggers, clears
`event_search` and backfills it from `events` in the same schema step.

## Event types use the brief's vocabulary

The Dart importer stores `message_photo`, `message_link`, `message_call`, etc.
The brief fixes the stored vocabulary at `message`, `comment`, `reaction`,
`post`, `photo`, `checkin`, `other`. Mapping:

- text message -> `message`
- message whose only payload is photos -> `photo`
- every other typed message payload (video, gif, audio, file, link, sticker,
  call, deleted, poll, plan, empty) -> `other`, with the original Dart type kept
  in `events.metadata_json` under `"payload"` (e.g. `message_link`)
- `checkin` is reserved; none of the four supported JSON shapes produces it.
  Posts with place attachments stay `post`.

## Fixture discrepancies (measured, not assumed)

The bundled `reference/demo/facebook-export.zip` was enumerated directly:
**244 entries, 0 directories, 63 JSON, 181 JPEG**.

1. **`source_files` is 244, not "285+".** One row per entry
   (`UNIQUE(import_id, relative_path)`) and "second ingest adds zero new rows"
   are both explicit requirements; padding or duplicating rows to reach 285
   would break both. The import writes 244 rows: 63 `parsed`, 181
   `unsupported` media. No row is dropped and no row is invented.
2. **Conversations are 60, not 62.** The archive has 60 `message_1.json`
   files. Each becomes one conversation (parent folder key + exported title).
3. **The demo archive contains no `مرحبا` or `شيئا`** (zero occurrences of
   either, vocalized or not). Criterion 4 is proven twice instead:
   - `FoldingTests` asserts `مَرْحَبًا إنَّ شَيْئًا` -> `مرحبا ان شيئا` and the
     SQL chain is generated from the same table;
   - `ImportAndSearchTests` imports a vocalized message archive and asserts
     `archive_search("مرحبا")` returns it while the stored text keeps its
     harakat, and `archive_search("شيئا")` finds `شَيْئًا`.
   Demo search is also shown live with `حفلة` -> `حفله` (taa marbuta fold).

## Idempotency

- Fingerprint: SHA-256 over sorted `path \t size \t crc32 \t json-sha256`
  lines. It is content-based (CRC catches same-size edits) and independent of
  ZIP entry order and compression.
- A second ingest of the same `platform` + fingerprint finds the completed
  import and returns `status: "reused"` with zero inserted rows. It does not
  create a second `imports` row, so "adds zero rows" is literal.
- Within one import, `UNIQUE(import_id, source_locator)` still backs every
  event; locators are generated per record (`file#/messages/12`, `…#/posts/3`).

## Storage details

- Timestamps are ISO 8601 UTC strings (`YYYY-MM-DDTHH:MM:SSZ`), which keeps
  date filters lexicographic. `timezone_status` is `utc`, `invalid` or
  `unknown`.
- `source_files.media_type` uses the small stable vocabulary `json`, `image`,
  `video`, `audio`, `file` rather than MIME types.
- People: exports state names, not ids, so `platform_person_id` is NULL and
  names are de-duplicated within an import by trimmed display name. Names are
  not merged across different imports (the Dart app's "no merging by name"
  rule).
- Attachments keep only path, media type, size and an optional SHA-256. Media
  is read only when it is <= 8 MiB and within the 64 MiB total hashing budget;
  bytes are discarded immediately and never stored.
- Ported bounds: 250k entries, 512 MiB per entry, 8 GiB expanded, 512 MiB /
  64 MiB JSON budgets, 1M records, 100 attachments per record, 64-char
  reaction token, 100k-char post text.

## FTS5

- `event_search` is a standalone FTS5 table (`unicode61 remove_diacritics 2`)
  written by three triggers, exactly as in `event_search_schema.dart`.
- Queries fold first, then every token is quoted and ANDed, so user text can
  never inject FTS5 operators. Snippets come from the folded index copy, which
  is why a matched demo snippet displays `حفله` for stored `حفلة`.
- `event_search` can be rebuilt from `events` at any time; stored text is never
  rewritten by folding.

## Evidence gate

- The explicit `archive_evidence` call is the consent; `provider_id` and
  `policy_version` are required. One `ai_consents` row is written per call,
  even when the bundle is empty.
- `scope_summary` is `events:N; chars:C; bytes:B` (no model field, because the
  tool takes none; the Dart gateway's `model` was provider-specific).
- Character accounting uses Python code points; Dart used UTF-16 code units.
  They differ only for astral characters, and the 4000-character bound is
  enforced the same way.
- `gate.dispatch` exists only to prove the gate: it raises
  `DispatchNotImplemented` even with matching consent. Nothing is dispatched.

## Hermes registration

`ctx.register_tool` is called with `name`, `toolset`, `schema`, `handler`,
`description`, and `read_only` for the four non-writing tools. Because this
host has no Hermes package to introspect, a `TypeError` from an older
`register_tool` signature falls back to the same call without `read_only`;
tool behaviour is unchanged. `plugin.yaml: name` is `tmewai`, the description
is pure ASCII English.

## Hardening pass (2026-11-09)

A hostile review of the port found defects the first suite did not cover. Each
was reproduced by a failing test before it was fixed; the regression tests are
in `tests/test_tmewai.py` (suite grew from 34 to 54 tests).

### Entry paths must be judged *after* normalization

`_pathRejection` in the Dart app runs before `_normalizePath`, which trims each
segment. The port inherited that order, so `".. /evil.json"` (a segment with a
trailing space) passed the traversal guard and then normalized to
`../evil.json` — a stored and queryable path that points outside the archive.
The guard now runs on the normalized string (`_path_rejection(_normalize_path(p))`),
matching what is actually stored. Normalization is idempotent, so this is
strictly safer and still accepts every path the Dart app accepts.

### One bad JSON file does not fail the archive

- A UTF-8 BOM is accepted (`utf-8-sig`). Previously a BOM file decoded to a
  `ValueError`, the file was dropped entirely, and its rows vanished with no
  coverage record.
- `json.loads` raises `RecursionError` on deeply nested input and `ValueError`
  on an oversized integer literal. Both are now caught with the decode errors
  and the file becomes exactly one `unsupported` `source_files` row plus a
  warning (`malformed_message_json`, `malformed_comments_json`, ...), instead
  of aborting the import. Coverage reports it as `malformed_json`.
- Non-UTF-8 bytes in a ZIP *name* are decoded as UTF-8 with replacement, which
  keeps one row per entry; the file is then classed `unknown_extension` and
  listed by `archive_scan`, never silently skipped.

### Timestamps

`timestamp_ms` is milliseconds by contract, but a seconds value written there
became a 1970 date with `timezone_status: "utc"`. Rules now:

- Message `timestamp_ms` below 2004-02-01 (in millis) but at/above that instant
  in seconds is multiplied by 1000 and warned as `timestamp_seconds_inferred`.
- Comment/reaction/post `timestamp` is seconds by contract. A 13-digit value is
  interpreted as millis and warned as `timestamp_millis_inferred`.
- Strings, floats and booleans are never coerced; an unparseable or absent
  value leaves `timestamp_utc` NULL with `timezone_status` `invalid`/`unknown`.
- Negative values are accepted only inside the representable epoch range.

These heuristics are export-shape repairs, not guesses about individual rows:
anything outside both ranges stays invalid rather than acquiring a made-up date.

### Coverage

- `files_by_status` in the ingest result is now the count of **all**
  `source_files` rows by status, not just JSON. Before, the demo returned
  `parsed: 63` against 244 `source_files` rows, which reads as data loss.
- Coverage reasons use the documented vocabulary. Parser failures are mapped
  through `_REASON_FOR_PARSER_CODE` (`malformed_*` -> `malformed_json`,
  `unsupported_*` -> `unsupported_json_shape`), and paths under a known export
  root with an unsupported shape become `known_not_imported` instead of
  `unsupported_json_shape`. `known_not_imported_files` was structurally always
  0 before.
- `archive_scan` and `archive_ingest` report the same coverage shape.

### Evidence gate

- At most 20 **distinct** events. Repeating one id no longer produces 20 copies
  of the same excerpt while claiming `events: 20`.
- Whitespace-only `text_content` is not evidence; such rows are skipped.
- A 16,000-byte UTF-8 ceiling is applied in addition to 4,000 characters and
  20 events. 4,000 BMP characters cannot exceed it, so the brief's character
  bound is unchanged; astral text can, and "bounded" should bound bytes too.
- Consent rows still contain only provider, policy, counts and bytes; the new
  tests assert no excerpt text reaches `scope_summary` or any other column.

### Deliberately unchanged (parity with the Dart importer)

- Comment attachments are not modelled. `facebook_json_importer.dart` reads
  media only from messages and posts, and the schema classifies comments as
  `comment`; inventing comment attachment rows would change the port.
- A reaction whose actor appears only in `title` ("Hala reacted to X") still
  has `sender_name` NULL. Extracting it safely needs a locale-aware phrase
  parser; the title is preserved in the event text/metadata. If a real export
  turns out to need it, add a parser with tests for at least English and Arabic
  titles rather than a regex over the raw string.
- Directories still produce no `source_files` row: the brief defines one row
  per *file*, and the Dart inspector skips directory entries before recording.
