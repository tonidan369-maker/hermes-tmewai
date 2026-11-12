# tmewai — your own social archive, searchable by your own agent

A **Hermes Agent plugin** that turns a **Facebook JSON export** (Meta's
`your_activity_across_facebook.zip`) into a local SQLite index your assistant can
search — messages, comments, reactions, posts — with **Arabic-aware folding**,
**per-file coverage accounting**, and an **evidence gate** that bounds what ever
leaves the index.

No network. No cloud. No API key. Standard library only.

```
archive_scan -> archive_ingest -> archive_search / archive_person / archive_overview -> archive_evidence
```

## Why it exists

Everyone's social history is locked in a ZIP that no assistant can read. Dumping
it into a chat is both useless (too big) and unsafe (private). This plugin keeps
the data on your disk and gives the model a *narrow* interface: folded search,
aggregates, and a capped evidence bundle — 20 events / 4000 characters — with a
consent row that records provider, policy, counts and bytes, **never excerpt
text**.

## Numbers (measured on this repo, not estimated)

| Archive | Entries | Events | First import | Second import |
|---|---|---|---|---|
| `reference/demo/facebook-export.zip` | 244 | 3,314 | **0.37 s** | 0.04 s, `reused`, 0 new rows |
| generated 400-conversation archive | 1,806 | 26,765 | **2.66 s** | 0.32 s, `reused`, 0 new rows |

Peak RSS 54 MB on the large archive; DB 17.7 MB. Full breakdown in
[`REPORT.md`](REPORT.md).

## Demo

A 23-second screen recording — ingest, folded search, evidence bundle — rendered
from the real run, no mock-ups: [docs/demo.mp4](docs/demo.mp4) ·
[release download](https://github.com/tonidan369-maker/hermes-tmewai/releases/download/v1.0.0/tmewai-demo.mp4)

## Install

```bash
hermes plugins install https://github.com/tonidan369-maker/hermes-tmewai#tmewai
```

Manual path: copy `tmewai/` into `$HERMES_HOME/plugins/tmewai`, add `tmewai` to
`plugins.enabled` in `config.yaml`, then restart the gateway (exact steps in
`tmewai/README.md`).

## Layout

```
tmewai/            the plugin (plugin.yaml, __init__.py, importer.py, coverage.py,
                   gate.py, folds.py, schema.sql, SKILL.md, README.md)
tests/             stdlib unittest suite (61 tests, no network)
scripts/           ingest_archive.py — the real handlers, no mocks
reference/         demo export + fixture generator
Makefile           make test | make demo | make big
```

## Guarantees, and how they are checked

- **Nothing is extracted to disk.** ZIP entries are validated in memory; absolute
  paths, `..`, drive paths, NUL bytes, duplicate/ambiguous names and symlinks are
  rejected before a byte is read. Tested in `tests/`.
- **No file is silently dropped.** Every ZIP entry gets exactly one
  `source_files` row with a status and a content-free reason code.
- **Imports are idempotent.** Content fingerprint match gives `status: reused`
  and zero rows.
- **A person keeps one identity across imports.** Names are the only identity a
  Facebook export offers, so `(platform, display_name)` is unique and an older
  database with duplicate rows is merged on open.
- **Arabic search folds the shapes that actually differ.** Harakat, hamza and
  taa-marbuta variants, Arabic-Indic digits (`٢٠٢٠` matches `2020`) and the
  presentation forms that PDF/Office pastes carry (`ﻻ` matches `لا`).
- **Evidence is bounded in three dimensions** — events, characters and bytes —
  and the consent row cannot carry excerpt text.
- **No network.** A test greps the module sources for `socket`, `urllib`,
  `requests`, `http` imports and fails if one appears.

## License

MIT.
