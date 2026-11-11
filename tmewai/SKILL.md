# tmewai — local social-archive index

Use this skill when the user wants to work with **their own Facebook JSON
export** offline: scanning it, importing it, searching it, or preparing a
small evidence bundle for an AI provider.

## When to use it

- "Scan my Facebook export before importing it" / «افحص أرشيف فيسبوك قبل الاستيراد»
- "Import my archive" / «استورد أرشيفي»
- "Search my messages for …" / «ابحث في رسائلي عن …»
- "What do I have from …?" / «شو عندي من …؟»
- "Who is … in my archive?" / «من هي …؟»
- "Give me the most relevant messages about …" / «أعطني الرسائل الأهم عن …»

Do not use it for live Facebook data, scraping, or anything that needs the
network. The archive is the only source.

## Workflow

1. **Scan first.** `archive_scan(path)` validates the ZIP and classifies every
   file without touching the database. Show the user the coverage counts and
   any unclassified sample before importing.
2. **Ingest once.** `archive_ingest(path)` imports atomically. Running it again
   on the same archive is a no-op (`status: "reused"`, zero inserted rows).
3. **Explore.** `archive_search(query, event_type?, after?, before?, limit?)`,
   `archive_person(name)`, `archive_overview()`.
4. **Evidence only on request.** `archive_evidence(event_ids, provider_id,
   policy_version)` returns at most 20 excerpts / 4000 characters and records
   consent metadata (never excerpt text). It dispatches nothing; the user must
   review the bundle before sharing it.

## Search notes

Queries are folded like the index: Arabic harakat are removed, alef forms are
unified, taa marbuta becomes haa, alef maqsura becomes yaa, and tatweel is
removed. So `مرحبا` finds `مَرْحَبًا`, and `شيئا` finds `شَيْئًا`. Stored event
text keeps its original spelling; only the search index is folded.

## Privacy rules to state to the user

- Everything stays local. The plugin makes no network calls in scan, import,
  search or evidence paths.
- `archive_evidence` only prepares a bundle and writes a metadata-only consent
  row. Nothing is sent anywhere.
- Media files are referenced by path, size and hash; their bytes are never
  copied out of the archive.
