# tmewai — build and hardening report

## What was built

A self-contained Hermes plugin, `tmewai/`, that turns a Facebook JSON export ZIP
into a local SQLite index. Python standard library only; no network code; the
archive is never extracted to disk. `reference/` was not modified.

| Path | Purpose |
|---|---|
| `tmewai/plugin.yaml` | Manifest (`provides_tools:` lists the six tools). |
| `tmewai/__init__.py` | `register(ctx)` and the six tool handlers: `archive_scan`, `archive_ingest`, `archive_search`, `archive_person`, `archive_overview`, `archive_evidence`. |
| `tmewai/schema.sql` | Seven tables, indexes, and the FTS5 table + insert/update/delete triggers. |
| `tmewai/folds.py` | The 29-entry Arabic fold table; one source for the Python query normalizer and the SQL trigger chain. |
| `tmewai/importer.py` | ZIP validation/inspection, the four JSON shapes (messages, comments incl. nested `comments_v2`, reactions, posts), attachments, idempotent ingest. |
| `tmewai/coverage.py` | One content-free reason code per archived file; export-root categories. |
| `tmewai/gate.py` | Bounded evidence bundle plus the metadata-only consent row. |
| `tmewai/SKILL.md`, `README.md`, `DECISIONS.md` | Agent usage, operator docs, decisions and measured fixture discrepancies. |
| `tests/test_tmewai.py` | 54 stdlib `unittest` tests. |
| `scripts/ingest_archive.py`, `Makefile` | Reproducible `make test`, `make demo`, `make big`. |

## Verification (actual output)

Host: Python 3.14.9, SQLite 3.53.1, Linux x86-64.

```text
$ python3 -m unittest discover -s tests -v
Ran 54 tests in 2.290s
OK
```

`hermes plugins validate tmewai` passes; the manifest uses `provides_tools:`
(the packaging fix made before this pass).

### Demo — `reference/demo/facebook-export.zip`

```text
$ make demo
archive_bytes 197292, db_bytes 2424832
first  status=imported 0.373s
       source_files=244 people=325 conversations=60 events=3314 attachments=291
       files_by_status: parsed=63 unsupported=181
       events_by_type: message=2414 reaction=400 comment=260 post=240
second status=reused 0.037s, inserted all zero, row_delta all zero
peak_rss_mb 27.9, idempotent true
```

`source_files` is 244, not the brief's "285+": the ZIP has exactly 244 entries
(63 JSON, 181 JPEG, no directories). One row per entry and "second ingest adds
zero rows" are both required, so 285 is unreachable without inventing rows.
Recorded in `DECISIONS.md`.

### Large archive — 400 conversations / 2,000 posts / 6,000 reactions / 3,000 comments

```text
$ make big
{"zip": "build/big-export/facebook-export.zip", "bytes": 1482745,
 "conversations": 400, "posts": 2000, "likes": 6000, "comments": 3000, "images": 1001}
first  status=imported 2.663s
       source_files=1806 people=385 conversations=400 events=26765 attachments=2126
       files_by_status: parsed=403 unsupported=1403
       events_by_type: message=15765 reaction=6000 comment=3000 post=2000
       coverage: supported=403 unsupported_media=1401 known_not_imported=2
second status=reused 0.316s, inserted all zero, row_delta all zero
db_bytes 17670144, peak_rss_mb 54.2, idempotent true
```

Integrity of the resulting `build/big.db` (queried directly, not inferred from
the import result):

```text
zip_entries 1806  source_files 1806  distinct_paths 1806
duplicate_locators 0
fts_rows 26765  events 26765
orphan_attachments 0
events_null_ts 0  events_1970 0  timezone [('utc', 26765)]
```

### Other checks

```text
$ grep -rn "import socket\|urllib\|requests\|http" tmewai/*.py
(no matches, exit 1)
```

## Bugs found and fixed in this pass

Each was reproduced by a failing test before the fix; all are covered by
regression tests now.

1. **Path traversal hidden by whitespace.** The path guard ran before segment
   trimming, so `".. /evil.json"` was accepted and *then* normalized to
   `../evil.json`, which was stored and queryable. The guard now runs on the
   normalized path. (`PathNormalizationTests`)
2. **Deeply nested JSON killed the whole import.** `json.loads` raises
   `RecursionError`; it was uncaught. Now the file becomes one `unsupported`
   row plus a warning.
3. **Oversized integer literals killed the import.** `ValueError` from the
   decoder is now treated as malformed JSON for that file only.
4. **UTF-8 BOM files were silently dropped.** Decoding failed, so the file got
   no `source_files` row and its events vanished. Now decoded with `utf-8-sig`.
5. **Seconds in `timestamp_ms` became 1970 dates.** A value below the
   Facebook-era millisecond floor but valid as seconds is converted and warned
   (`timestamp_seconds_inferred`); values in neither range stay invalid.
6. **13-digit values in seconds fields were rejected.** Comment/reaction/post
   timestamps that are clearly milliseconds are now recovered and warned
   (`timestamp_millis_inferred`).
7. **`files_by_status` undercounted 4x.** It listed only parsed JSON (63)
   against 244 `source_files` rows, which looks like data loss. It now counts
   every row by status.
8. **Coverage vocabulary/`known_not_imported` were wrong.** Parser-specific
   codes leaked into reason counts, and `known_not_imported_files` was
   structurally always 0. Parser codes are mapped to the documented reasons and
   known-root wrong-shape JSON is counted as `known_not_imported`.
9. **Evidence could repeat one event 20 times.** Duplicate ids are now counted
   once; the 20-event cap is a cap on distinct events.
10. **Whitespace-only text counted as evidence.** Blank excerpts are skipped.
11. **No byte ceiling on evidence.** Added a 16,000-byte UTF-8 cap alongside the
    4,000-character and 20-event caps; consent rows were re-checked to contain
    no excerpt text.

Checked and deliberately unchanged (Dart parity, verified against
`facebook_json_importer.dart`): comment attachments are not modelled by the
reference importer either; a reaction whose actor appears only in `title`
("Hala reacted to X") is kept with a NULL actor rather than guessed; posts with
empty `data` and no attachments are skipped exactly as Dart does; directories
get no `source_files` row.

## Limits and next steps

- The large fixture is synthetic. A real multi-GB export needs streaming JSON
  parsing: the current importer holds all JSON bytes and all normalized records
  in memory (54 MB peak for a 1.5 MB archive with 26,765 events). Budget work
  for 1M records before claiming that scale.
- `coverage.blocked_files` is effectively always 0 because blocked archives
  abort before a report exists. If per-file blocking is wanted, it needs a
  design decision first.
- Reaction `title` actors and comment attachments are known gaps; do not add
  either without tests for English and Arabic titles and without changing the
  Dart-parity contract in `DECISIONS.md`.
- Validate on a live Hermes host (plugin discovery, `read_only` support, the
  six tool schemas) and add `hermes plugins validate tmewai` plus the unit suite
  to CI.
- Re-run `make demo` and `make big` after any parser change; both fail loudly if
  a second ingest adds a row or if the fold table and triggers drift.
