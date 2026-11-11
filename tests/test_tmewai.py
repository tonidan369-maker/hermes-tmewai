"""Stdlib unittest suite for the tmewai Hermes plugin."""

from __future__ import annotations

import binascii
import json
import os
import sqlite3
import struct
import sys
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tmewai import gate, importer  # noqa: E402
from tmewai.folds import (  # noqa: E402
    DIGIT_FOLDS,
    PRESENTATION_FOLDS,
    SEARCH_TEXT_FOLDS,
    fold_text,
    match_query,
    sql_fold_expression,
)

DEMO_ZIP = ROOT / "reference" / "demo" / "facebook-export.zip"

ROOT_DIR = "your_activity_across_facebook"
THREAD = f"{ROOT_DIR}/messages/inbox/ليلى_100"


def make_zip(directory: str | Path, entries: list[tuple[str, bytes | str]], name: str = "export.zip") -> Path:
    path = Path(directory) / name
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for entry_name, data in entries:
            payload = data.encode("utf-8") if isinstance(data, str) else data
            archive.writestr(entry_name, payload)
    return path


def write_zip(path: Path, entries) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for entry_name, data in entries:
            payload = data.encode("utf-8") if isinstance(data, str) else data
            archive.writestr(entry_name, payload)
    return path


def raw_zip(name: bytes, data: bytes) -> bytes:
    """A ZIP with an arbitrary filename byte string (legacy, UTF-8 flag clear)."""
    crc = binascii.crc32(data) & 0xFFFFFFFF
    local = struct.pack(
        "<4sHHHHHIIIHH",
        b"PK\x03\x04", 20, 0, 0, 0, 0, crc, len(data), len(data), len(name), 0,
    ) + name + data
    central = struct.pack(
        "<4sHHHHHHIIIHHHHHII",
        b"PK\x01\x02", 20, 20, 0, 0, 0, 0, crc, len(data), len(data),
        len(name), 0, 0, 0, 0, 0, 0,
    ) + name
    eocd = struct.pack(
        "<4sHHHHIIH", b"PK\x05\x06", 0, 0, 1, 1, len(central), len(local), 0
    )
    return local + central + eocd


def person_thread(title: str, messages: list[dict]) -> str:
    return json.dumps(
        {
            "participants": [{"name": title}, {"name": "Hadi Test"}],
            "messages": messages,
            "title": title,
            "thread_path": f"inbox/{title}_100",
        },
        ensure_ascii=False,
    )


def synthetic_entries() -> list[tuple[str, str]]:
    return [
        (
            f"{THREAD}/message_1.json",
            person_thread(
                "ليلى",
                [
                    {
                        "sender_name": "ليلى",
                        "timestamp_ms": 1_700_000_000_000,
                        "content": "مَرْحَبًا يا شام",
                    },
                    {
                        "sender_name": "Hadi Test",
                        "timestamp_ms": 1_700_000_001_000,
                        "content": "hello world",
                    },
                    {
                        "sender_name": "ليلى",
                        "timestamp_ms": 1_700_000_002_000,
                        "photos": [{"uri": f"{THREAD}/photos/p.jpg"}],
                    },
                ],
            ),
        ),
        (
            f"{ROOT_DIR}/comments/comments.json",
            json.dumps(
                {
                    "comments_v2": [
                        {
                            "timestamp": 1_700_000_100,
                            "data": [
                                {
                                    "comment": {
                                        "timestamp": 1_700_000_101,
                                        "comment": "شَيْئًا جميلاً",
                                        "author": "سمر",
                                    }
                                }
                            ],
                        },
                        {
                            "timestamp": 1_700_000_200,
                            "data": [
                                {
                                    "comment": {
                                        "comment": "flat second comment",
                                        "author": "خالد",
                                    }
                                }
                            ],
                        },
                    ]
                },
                ensure_ascii=False,
            ),
        ),
        (
            f"{ROOT_DIR}/likes_and_reactions/likes_and_reactions_1.json",
            json.dumps(
                {
                    "reactions_v2": [
                        {
                            "timestamp": 1_700_000_300,
                            "data": [
                                {
                                    "reaction": {
                                        "reaction": "👍",
                                        "actor": "عمر",
                                    }
                                },
                                {
                                    "reaction": {
                                        "reaction": "❤️",
                                        "actor": "نور",
                                    }
                                },
                            ],
                        }
                    ]
                },
                ensure_ascii=False,
            ),
        ),
        (
            f"{ROOT_DIR}/posts/your_posts_1.json",
            json.dumps(
                [
                    {"timestamp": 1_700_000_400, "data": [{"post": "منشور أول"}]},
                    {
                        "timestamp": 1_700_000_500,
                        "data": [{"post": "منشور بصورة"}],
                        "attachments": [
                            {
                                "data": [
                                    {
                                        "media": {
                                            "uri": f"{ROOT_DIR}/posts/media/1.jpg"
                                        }
                                    }
                                ]
                            }
                        ],
                    },
                ],
                ensure_ascii=False,
            ),
        ),
        (f"{THREAD}/photos/p.jpg", b"\xff\xd8\xff\xe0fake-jpeg"),
        (f"{ROOT_DIR}/posts/media/1.jpg", b"\xff\xd8\xff\xe0fake-jpeg-2"),
        (f"{ROOT_DIR}/ads_information/ads_and_topics/ads_interests.json", "{not json"),
        ("notes/random.json", json.dumps({"unknown": True})),
    ]


class FoldingTests(unittest.TestCase):
    def test_reference_fold_example(self):
        self.assertEqual(
            fold_text("مَرْحَبًا إنَّ شَيْئًا"),
            "مرحبا ان شيئا",
        )

    def test_alef_forms_taa_marbuta_and_tatweel(self):
        self.assertEqual(fold_text("أَحْمَد إِبْرَاهِيم آدم"), "احمد ابراهيم ادم")
        self.assertEqual(fold_text("مدرسة"), "مدرسه")
        self.assertEqual(fold_text("مـــرحبا"), "مرحبا")
        self.assertEqual(fold_text("مصطفى"), "مصطفي")

    def test_table_has_same_codepoints_as_reference(self):
        # search_text.dart declares 29 entries (the brief's "30" counts one
        # more than the reference table actually contains).
        self.assertEqual(len(SEARCH_TEXT_FOLDS), 29)
        self.assertEqual(len({codepoint for codepoint, _ in SEARCH_TEXT_FOLDS}), 29)

    def test_sql_expression_is_generated_from_same_table(self):
        expression = sql_fold_expression("new.text_content")
        self.assertEqual(
            expression.count("replace("),
            len(PRESENTATION_FOLDS) + len(SEARCH_TEXT_FOLDS) + len(DIGIT_FOLDS))
        self.assertIn("COALESCE(new.text_content, '')", expression)
        # The hamza-alef fold is present with the exact replacement character.
        self.assertIn("replace(", expression)
        self.assertIn("'\u0623'", expression)
        self.assertIn("'\u0627'", expression)

    def test_digits_fold_so_ascii_queries_find_arabic_numerals(self):
        self.assertEqual(fold_text("سنة ٢٠٢٠"), "سنه 2020")
        self.assertEqual(fold_text("١٥٣"), "153")
        self.assertEqual(fold_text("۱۴۰۰"), "1400")  # Persian digits

    def test_presentation_forms_fold_to_plain_letters(self):
        # Text pasted out of PDFs/Office uses contextual forms and the lam-alef
        # ligature; both have to reach the same token as typed Arabic.
        self.assertEqual(fold_text("ﻻ ﻳﻮﺟﺪ"), fold_text("لا يوجد"))
        self.assertEqual(fold_text("ﻣﺮﺣﺒﺎ"), fold_text("مرحبا"))

    def test_python_and_sql_folding_agree(self):
        """The trigger expression and the query normalizer must never drift."""
        samples = ["أَحْمَد إِبْرَاهِيم آدم", "مدرسة", "مـــرحبا", "ﻻ ﻳﻮﺟﺪ",
                   "سنة ٢٠٢٠", "۱۴۰۰", "hello 123", "", "مصطفى"]
        conn = sqlite3.connect(":memory:")
        expression = sql_fold_expression("?")
        for sample in samples:
            sql_value = conn.execute(f"SELECT {expression}", (sample,)).fetchone()[0]
            self.assertEqual(sql_value, fold_text(sample), sample)
        conn.close()

    def test_existing_index_is_rebuilt_when_fold_sql_changes(self):
        """An archive imported before the fold table grew must not stay stale."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = Path(tmp.name) / "old.db"
        conn = sqlite3.connect(db)
        self.addCleanup(conn.close)
        conn.row_factory = sqlite3.Row
        importer.ensure_schema(conn)

        import_id = conn.execute(
            "INSERT INTO imports (platform, format, source_fingerprint, started_at, "
            "importer_version, status) VALUES ('facebook','json','fp','2026-01-01','1.0',"
            "'completed')").lastrowid
        source_file = conn.execute(
            "INSERT INTO source_files (import_id, relative_path, parse_status) "
            "VALUES (?, 'messages/inbox/a/message_1.json', 'parsed')",
            (import_id,)).lastrowid
        conn.execute(
            "INSERT INTO events (import_id, source_file_id, source_locator, platform, "
            "event_type, timestamp_normalized, timezone_status, text_content) "
            "VALUES (?, ?, 'l1', 'facebook', 'message', '2020-01-01', 'naive', ?)",
            (import_id, source_file, "سنة ٢٠٢٠ كانت صعبة"))
        conn.commit()

        # Pretend the row was indexed by the older, digit-blind fold chain.
        conn.execute("DELETE FROM event_search")
        conn.execute("INSERT INTO event_search (event_id, text_content, source_locator) "
                     "VALUES (1, 'سنه ٢٠٢٠ كانت صعبه', 'l1')")
        conn.execute("DROP TRIGGER IF EXISTS events_search_after_insert")
        conn.execute(
            "CREATE TRIGGER events_search_after_insert AFTER INSERT ON events BEGIN "
            "INSERT INTO event_search (event_id, text_content, source_locator) "
            "VALUES (new.id, COALESCE(new.text_content, ''), new.source_locator); END")
        conn.commit()

        importer.ensure_schema(conn)

        hits = conn.execute("SELECT COUNT(*) AS n FROM event_search "
                            "WHERE event_search MATCH ?", (match_query("2020"),)).fetchone()["n"]
        self.assertEqual(hits, 1)

    def test_match_query_quotes_operators_and_folds(self):
        self.assertEqual(match_query("مرحبا"), '"مرحبا"')
        self.assertEqual(match_query("  شَيْئًا  "), '"شيئا"')
        expression = match_query('hello OR "world" NEAR(x)')
        self.assertNotIn(" OR ", expression)
        self.assertEqual(expression, '"hello" AND "OR" AND "world" AND "NEAR" AND "x"')
        # Quoted tokens can never be read as FTS5 operators.
        self.assertTrue(expression.startswith('"hello"'))
        self.assertIn('"NEAR"', expression)


class ArchiveSafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def assertRejected(self, entries, code):
        path = make_zip(self.tmp.name, entries)
        with self.assertRaises(importer.ArchiveError) as caught:
            importer.scan_archive(path)
        self.assertEqual(caught.exception.code, code)

    def test_rejects_path_traversal(self):
        self.assertRejected([("../evil.json", "{}")], "path_traversal")

    def test_rejects_absolute_path(self):
        self.assertRejected([("/etc/passwd.json", "{}")], "absolute_entry_path")

    def test_rejects_windows_drive_path(self):
        self.assertRejected([("C:/Users/me/export.json", "{}")], "absolute_entry_path")

    def test_rejects_duplicate_entries(self):
        self.assertRejected(
            [("a.json", "{}"), ("A.json", "{}")], "duplicate_entry"
        )

    def test_rejects_symlink_entry(self):
        path = Path(self.tmp.name) / "link.zip"
        with zipfile.ZipFile(path, "w") as archive:
            info = zipfile.ZipInfo("a.json")
            info.external_attr = 0o120777 << 16
            archive.writestr(info, "{}")
        with self.assertRaises(importer.ArchiveError) as caught:
            importer.scan_archive(path)
        self.assertEqual(caught.exception.code, "symbolic_link_rejected")

    def test_rejects_non_zip_and_missing_file(self):
        bogus = Path(self.tmp.name) / "bogus.zip"
        bogus.write_bytes(b"not a zip")
        with self.assertRaises(importer.ArchiveError) as caught:
            importer.scan_archive(bogus)
        self.assertEqual(caught.exception.code, "archive_invalid")
        with self.assertRaises(importer.ArchiveError) as caught:
            importer.scan_archive(Path(self.tmp.name) / "missing.zip")
        self.assertEqual(caught.exception.code, "archive_unreadable")

    def test_scan_never_extracts_and_never_writes_db(self):
        path = make_zip(self.tmp.name, synthetic_entries())
        with mock.patch.dict(
            os.environ, {"HERMES_HOME": self.tmp.name}, clear=False
        ):
            from tmewai import _handle_scan

            payload = json.loads(_handle_scan({"path": str(path)}))
        self.assertEqual(payload["entries"], len(synthetic_entries()))
        self.assertEqual(payload["json_files"], 6)
        self.assertFalse((Path(self.tmp.name) / "plugin-data" / "tmewai" / "archive.db").exists())
        self.assertEqual(sorted(p.name for p in Path(self.tmp.name).iterdir()), ["export.zip"])


class ImportAndSearchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.zip_path = make_zip(self.tmp.name, synthetic_entries())
        self.conn = sqlite3.connect(Path(self.tmp.name) / "archive.db")
        self.conn.row_factory = sqlite3.Row
        importer.ensure_schema(self.conn)
        self.addCleanup(self.conn.close)
        self.result = importer.ingest(self.conn, self.zip_path)

    def search(self, query, **kwargs):
        from tmewai import _handle_search

        with mock.patch.dict(
            os.environ,
            {"TMEWAI_DB_PATH": str(Path(self.tmp.name) / "archive.db")},
            clear=False,
        ):
            return json.loads(_handle_search({"query": query, **kwargs}))

    def test_files_by_status_accounts_for_every_source_file(self):
        by_status = self.result["files_by_status"]
        self.assertEqual(sum(by_status.values()), 8)
        self.assertEqual(by_status.get("parsed"), 4)
        self.assertEqual(by_status.get("unsupported"), 4)

    def test_import_counts(self):
        self.assertEqual(self.result["status"], "imported")
        self.assertEqual(self.result["inserted"]["source_files"], 8)
        self.assertEqual(self.result["events_by_type"]["message"], 2)
        self.assertEqual(self.result["events_by_type"]["photo"], 1)
        self.assertEqual(self.result["events_by_type"]["comment"], 2)
        self.assertEqual(self.result["events_by_type"]["reaction"], 2)
        self.assertEqual(self.result["events_by_type"]["post"], 2)
        self.assertEqual(self.result["inserted"]["attachments"], 2)

    def test_every_entry_has_exactly_one_source_file_row(self):
        rows = self.conn.execute(
            "SELECT relative_path, COUNT(*) AS n FROM source_files "
            "GROUP BY relative_path"
        ).fetchall()
        self.assertEqual(len(rows), 8)
        self.assertTrue(all(row["n"] == 1 for row in rows))
        with zipfile.ZipFile(self.zip_path) as archive:
            names = {info.filename.replace("\\", "/") for info in archive.infolist()}
        stored = {row["relative_path"] for row in rows}
        self.assertEqual(stored, names)

    def test_unparseable_files_are_recorded_not_dropped(self):
        statuses = {
            row["relative_path"]: (row["parse_status"], row["error_code"])
            for row in self.conn.execute(
                "SELECT relative_path, parse_status, error_code FROM source_files"
            )
        }
        malformed = statuses[f"{ROOT_DIR}/ads_information/ads_and_topics/ads_interests.json"]
        self.assertEqual(malformed[0], "unsupported")
        self.assertEqual(malformed[1], "malformed_message_json")
        unknown = statuses["notes/random.json"]
        self.assertEqual(unknown[0], "unsupported")

    def test_search_folds_arabic_harakat(self):
        result = self.search("مرحبا")
        self.assertEqual(result["count"], 1)
        hit = result["rows"][0]
        self.assertEqual(hit["type"], "message")
        self.assertIn("مرحبا", result["folded"])
        self.assertIn("مرحبا", hit["snippet"])
        self.assertTrue(hit["source_locator"].startswith(f"{THREAD}/message_1.json#/"))
        stored = self.conn.execute(
            "SELECT text_content FROM events WHERE id = ?", (hit["id"],)
        ).fetchone()[0]
        self.assertIn("مَرْحَبًا", stored, "stored text must keep its diacritics")

    def test_search_folds_hamza_on_yaa(self):
        result = self.search("شيئا")
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["rows"][0]["type"], "comment")
        self.assertIn("شيئا", result["folded"])

    def test_search_event_type_and_date_filters(self):
        self.assertEqual(self.search("hello", event_type="comment")["count"], 0)
        self.assertEqual(self.search("hello", event_type="message")["count"], 1)
        in_range = self.search("hello", after="2023-11-14", before="2023-11-15")
        self.assertEqual(in_range["count"], 1)
        out_of_range = self.search("hello", after="2024-01-01")
        self.assertEqual(out_of_range["count"], 0)

    def test_comments_v2_nested_and_flat_both_parse(self):
        texts = [
            row[0]
            for row in self.conn.execute(
                "SELECT text_content FROM events WHERE event_type = 'comment' "
                "ORDER BY id"
            )
        ]
        self.assertEqual(texts, ["شَيْئًا جميلاً", "flat second comment"])

    def test_reaction_metadata_is_allowlisted(self):
        rows = self.conn.execute(
            "SELECT metadata_json FROM events WHERE event_type = 'reaction'"
        ).fetchall()
        self.assertEqual(
            sorted(json.loads(row[0])["reaction"] for row in rows),
            sorted(["👍", "❤️"]),
        )

    def test_post_metadata_has_counts_not_raw_record(self):
        row = self.conn.execute(
            "SELECT metadata_json FROM events WHERE event_type = 'post' "
            "AND text_content = 'منشور بصورة'"
        ).fetchone()
        metadata = json.loads(row[0])
        self.assertEqual(metadata["attachments"], "1")
        self.assertEqual(metadata["attachment_types"], "media")

    def test_attachments_reference_existing_source_files(self):
        rows = self.conn.execute(
            "SELECT a.relative_path, a.media_type, a.size_bytes, f.id "
            "FROM attachments a JOIN source_files f ON f.id = a.source_file_id"
        ).fetchall()
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row["media_type"] == "image" for row in rows))
        self.assertTrue(all(row["size_bytes"] > 0 for row in rows))

    def test_second_ingest_adds_zero_rows(self):
        before = importer._table_totals(self.conn)
        second = importer.ingest(self.conn, self.zip_path)
        after = importer._table_totals(self.conn)
        self.assertEqual(second["status"], "reused")
        self.assertEqual(second["inserted"]["events"], 0)
        self.assertEqual(before, after)

    def test_ensure_schema_is_drift_stable(self):
        # A second schema check must not silently rebuild the index: a sentinel
        # row survives only when the generated trigger SQL still matches.
        self.conn.execute(
            "INSERT INTO event_search (event_id, text_content, source_locator) "
            "VALUES (999999, 'sentinel', 'sentinel')"
        )
        self.conn.commit()
        importer.ensure_schema(self.conn)
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM event_search WHERE event_id = 999999"
            ).fetchone()[0],
            1,
        )

    def test_fts_triggers_cover_update_and_delete(self):
        row = self.conn.execute(
            "SELECT id FROM events WHERE text_content = 'hello world'"
        ).fetchone()
        self.conn.execute(
            "UPDATE events SET text_content = 'changed text' WHERE id = ?",
            (row["id"],),
        )
        self.conn.commit()
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM event_search WHERE event_search MATCH '\"hello\"'"
            ).fetchone()[0],
            0,
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM event_search WHERE event_search MATCH '\"changed\"'"
            ).fetchone()[0],
            1,
        )
        self.conn.execute("DELETE FROM events WHERE id = ?", (row["id"],))
        self.conn.commit()
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM event_search WHERE event_id = ?", (row["id"],)
            ).fetchone()[0],
            0,
        )


class PersonIdentityTests(unittest.TestCase):
    """A second archive must not fork the people table.

    Facebook exports carry no person id, so the display name is the identity.
    Before people_identity existed, each import inserted its own row per name and
    every event of the newer import was invisible to archive_person.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "archive.db"
        self.conn = sqlite3.connect(self.db)
        self.conn.row_factory = sqlite3.Row
        importer.ensure_schema(self.conn)
        self.addCleanup(self.conn.close)

    def _zip(self, name: str, extra: str) -> Path:
        entries = synthetic_entries()
        entries.append((f"{THREAD}/message_2.json",
                        person_thread("ليلى", [{"sender_name": "Hadi Test",
                                                "timestamp_ms": 1_700_000_500_000,
                                                "content": extra}])))
        return make_zip(self.tmp.name, entries, name=name)

    def test_second_import_reuses_person_rows(self):
        first = importer.ingest(self.conn, self._zip("a.zip", "from A"))
        second = importer.ingest(self.conn, self._zip("b.zip", "from B"))
        self.assertEqual(first["status"], "imported")
        self.assertEqual(second["status"], "imported")
        self.assertEqual(second["inserted"]["people"], 0)

        duplicates = self.conn.execute(
            "SELECT display_name, COUNT(*) AS n FROM people "
            "GROUP BY platform, display_name HAVING n > 1").fetchall()
        self.assertEqual(duplicates, [])

    def test_both_imports_hang_off_one_person_row(self):
        importer.ingest(self.conn, self._zip("a.zip", "from A"))
        importer.ingest(self.conn, self._zip("b.zip", "from B"))
        rows = self.conn.execute(
            "SELECT COUNT(DISTINCT actor_id) AS ids FROM events "
            "WHERE actor_id IS NOT NULL").fetchone()
        named = self.conn.execute(
            "SELECT COUNT(*) AS n FROM people").fetchone()
        self.assertEqual(rows["ids"], named["n"])

        person = self.conn.execute(
            "SELECT id FROM people WHERE display_name = 'Hadi Test'").fetchall()
        self.assertEqual(len(person), 1)
        events = self.conn.execute(
            "SELECT COUNT(*) AS n FROM events WHERE actor_id = ?",
            (person[0]["id"],)).fetchone()["n"]
        self.assertGreaterEqual(events, 2)

    def test_existing_duplicates_are_merged_on_open(self):
        """Databases created before the index get repaired, not just blocked."""
        self.conn.executescript("DROP INDEX IF EXISTS people_identity")
        keep = self.conn.execute(
            "INSERT INTO people (platform, platform_person_id, display_name) "
            "VALUES ('facebook', NULL, 'ليلى')").lastrowid
        other = self.conn.execute(
            "INSERT INTO people (platform, platform_person_id, display_name) "
            "VALUES ('facebook', NULL, 'ليلى')").lastrowid
        import_id = self.conn.execute(
            "INSERT INTO imports (platform, format, source_fingerprint, started_at, "
            "importer_version, status) VALUES ('facebook', 'json', 'fp', '2024-01-01', "
            "'1.0', 'completed')").lastrowid
        source_file = self.conn.execute(
            "INSERT INTO source_files (import_id, relative_path, parse_status) "
            "VALUES (?, 'messages/inbox/ليلى_100/message_1.json', 'parsed')",
            (import_id,)).lastrowid
        for actor in (keep, other):
            self.conn.execute(
                "INSERT INTO events (import_id, source_file_id, source_locator, platform, "
                "event_type, timestamp_normalized, timezone_status, actor_id) "
                "VALUES (?, ?, ?, 'facebook', 'message', '2024-01-01', 'naive', ?)",
                (import_id, source_file, f"legacy-{actor}", actor))
        self.conn.commit()

        importer.ensure_schema(self.conn)

        left = self.conn.execute(
            "SELECT id FROM people WHERE display_name = 'ليلى'").fetchall()
        self.assertEqual(len(left), 1)
        self.assertEqual(left[0]["id"], keep)
        migrated = self.conn.execute(
            "SELECT COUNT(*) AS n FROM events WHERE actor_id = ?", (keep,)).fetchone()["n"]
        self.assertEqual(migrated, 2)


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        messages = [
            {
                "sender_name": "ليلى",
                "timestamp_ms": 1_700_000_000_000 + index,
                "content": f"EXCERPT-{index:03d} " + ("نص طويل " * 60),
            }
            for index in range(40)
        ]
        path = make_zip(
            self.tmp.name,
            [(f"{THREAD}/message_1.json", person_thread("ليلى", messages))],
        )
        self.conn = sqlite3.connect(Path(self.tmp.name) / "db.sqlite")
        self.conn.row_factory = sqlite3.Row
        importer.ensure_schema(self.conn)
        self.addCleanup(self.conn.close)
        importer.ingest(self.conn, path)

    def test_bundle_is_bounded_and_consent_has_no_excerpt_text(self):
        ids = [
            row[0]
            for row in self.conn.execute(
                "SELECT id FROM events ORDER BY id LIMIT 50"
            )
        ]
        self.assertEqual(len(ids), 40)
        bundle = gate.build_bundle(self.conn, ids, "openai", "policy-v1")
        self.assertLessEqual(len(bundle["excerpts"]), 20)
        self.assertLessEqual(bundle["char_count"], 4000)
        self.assertLessEqual(
            sum(len(excerpt["text"]) for excerpt in bundle["excerpts"]), 4000
        )
        self.assertTrue(all(excerpt["id"] in ids for excerpt in bundle["excerpts"]))
        self.assertFalse(bundle["dispatched"])

        consents = self.conn.execute("SELECT * FROM ai_consents").fetchall()
        self.assertEqual(len(consents), 1)
        consent = consents[0]
        serialized = json.dumps(dict(consent), ensure_ascii=False)
        self.assertNotIn("EXCERPT-", serialized)
        self.assertNotIn("نص طويل", serialized)
        self.assertEqual(consent["event_count"], len(bundle["excerpts"]))
        self.assertEqual(consent["byte_count"], bundle["byte_count"])
        self.assertEqual(consent["scope_summary"].count(";"), 2)

    def test_dispatch_without_matching_consent_is_refused(self):
        bundle = gate.build_bundle(self.conn, [], "openai", "policy-v1")
        with self.assertRaises(gate.ConsentRequired):
            gate.dispatch(
                bundle=bundle, consent_provider=None, consent_policy=None
            )
        with self.assertRaises(gate.DispatchNotImplemented):
            gate.dispatch(
                bundle=bundle, consent_provider="openai", consent_policy="policy-v1"
            )


class HandlerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = str(Path(self.tmp.name) / "archive.db")
        self.env = mock.patch.dict(
            os.environ, {"TMEWAI_DB_PATH": self.db_path}, clear=False
        )
        self.env.start()
        self.addCleanup(self.env.stop)
        zip_path = make_zip(self.tmp.name, synthetic_entries())
        from tmewai import _handle_ingest

        self.ingest = json.loads(_handle_ingest({"path": str(zip_path)}))

    def test_ingest_returns_expected_payload(self):
        self.assertEqual(self.ingest["status"], "imported")
        self.assertGreater(self.ingest["inserted"]["events"], 0)
        self.assertIn("coverage", self.ingest)
        self.assertEqual(self.ingest["totals"]["imports"], 1)

    def test_person_exact_and_substring(self):
        from tmewai import _handle_person

        exact = json.loads(_handle_person({"name": "ليلى"}))
        self.assertTrue(exact["person"]["name"] == "ليلى")
        self.assertEqual(exact["match"], "exact")
        self.assertGreaterEqual(exact["events"], 2)
        self.assertEqual(len(exact["recent_events"]), min(10, exact["events"]))

        substring = json.loads(_handle_person({"name": "سمر"}))
        self.assertEqual(substring["match"], "exact")

        missing = json.loads(_handle_person({"name": "لا أحد"}))
        self.assertFalse(missing["found"])

    def test_overview_totals_and_imports(self):
        from tmewai import _handle_overview

        overview = json.loads(_handle_overview({}))
        self.assertEqual(overview["totals"]["events"], self.ingest["inserted"]["events"])
        self.assertEqual(overview["totals"]["source_files"], 8)
        self.assertEqual(len(overview["imports"]), 1)
        self.assertIn("message", overview["totals"]["by_event_type"])
        self.assertTrue(overview["top_correspondents"])

    def test_evidence_handler_writes_consent(self):
        from tmewai import _handle_evidence

        ids = [
            row[0]
            for row in sqlite3.connect(self.db_path).execute(
                "SELECT id FROM events ORDER BY id"
            )
        ]
        payload = json.loads(
            _handle_evidence(
                {
                    "event_ids": ids,
                    "provider_id": "local-test",
                    "policy_version": "v1",
                }
            )
        )
        self.assertLessEqual(len(payload["excerpts"]), 20)
        conn = sqlite3.connect(self.db_path)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM ai_consents").fetchone()[0], 1)

    def test_register_registers_six_tools_with_read_only_flags(self):
        import tmewai

        calls = {}

        class Context:
            def register_tool(self, **kwargs):
                calls[kwargs["name"]] = kwargs

        tmewai.register(Context())
        self.assertEqual(
            sorted(calls),
            [
                "archive_evidence",
                "archive_ingest",
                "archive_overview",
                "archive_person",
                "archive_scan",
                "archive_search",
            ],
        )
        for name in ("archive_scan", "archive_search", "archive_person", "archive_overview"):
            self.assertTrue(calls[name]["read_only"], name)
        self.assertFalse(calls["archive_ingest"]["read_only"])
        self.assertFalse(calls["archive_evidence"]["read_only"])
        self.assertTrue(all(call["toolset"] == "tmewai" for call in calls.values()))
        self.assertTrue(
            all(callable(call["handler"]) and isinstance(call["schema"], dict) for call in calls.values())
        )


class NoNetworkTests(unittest.TestCase):
    def test_no_network_library_or_scheme_in_plugin_sources(self):
        forbidden = ("import socket", "urllib", "requests", "http")
        for path in sorted((ROOT / "tmewai").glob("*.py")):
            text = path.read_text(encoding="utf-8")
            for token in forbidden:
                self.assertNotIn(token, text, f"{token!r} found in {path.name}")


@unittest.skipUnless(DEMO_ZIP.exists(), "demo export missing")
class DemoArchiveTests(unittest.TestCase):
    def test_demo_import_counts_and_idempotency(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = sqlite3.connect(Path(tmp) / "demo.db")
            conn.row_factory = sqlite3.Row
            importer.ensure_schema(conn)
            try:
                started = time.perf_counter()
                first = importer.ingest(conn, DEMO_ZIP)
                elapsed = time.perf_counter() - started
                self.assertLess(elapsed, 10.0)
                self.assertEqual(first["status"], "imported")

                with zipfile.ZipFile(DEMO_ZIP) as archive:
                    entry_count = len([i for i in archive.infolist() if not i.is_dir()])
                self.assertEqual(first["inserted"]["source_files"], entry_count)
                self.assertEqual(first["inserted"]["source_files"], 244)

                by_type = first["events_by_type"]
                self.assertGreater(by_type.get("message", 0), 0)
                self.assertGreater(by_type.get("comment", 0), 0)
                self.assertGreater(by_type.get("reaction", 0), 0)
                self.assertGreater(by_type.get("post", 0), 0)

                before = importer._table_totals(conn)
                second = importer.ingest(conn, DEMO_ZIP)
                after = importer._table_totals(conn)
                self.assertEqual(second["status"], "reused")
                self.assertEqual(before, after)
                self.assertEqual(second["inserted"]["events"], 0)

                # Search works on the real archive's own Arabic wording. The
                # demo fixture predates the vocalized example words, so the
                # explicit harakat folding proof lives in FoldingTests and in
                # ImportAndSearchTests against a vocalized fixture.
                hits = conn.execute(
                    "SELECT e.id FROM event_search JOIN events e ON e.id = event_search.event_id "
                    "WHERE event_search MATCH ?",
                    (match_query("حفلة"),),
                ).fetchall()
                self.assertTrue(hits, "expected an Arabic hit in the demo archive")

                # Every archive file has exactly one source_files row.
                groups = conn.execute(
                    "SELECT COUNT(*) AS n FROM source_files GROUP BY relative_path "
                    "HAVING COUNT(*) != 1"
                ).fetchall()
                self.assertEqual(groups, [])
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM source_files").fetchone()[0],
                    entry_count,
                )
            finally:
                conn.close()


class PathNormalizationTests(unittest.TestCase):
    """Validation must run on the path that is actually stored."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def assertRejected(self, entries, code):
        path = make_zip(self.tmp.name, entries)
        with self.assertRaises(importer.ArchiveError) as caught:
            importer.scan_archive(path)
        self.assertEqual(caught.exception.code, code)

    def test_space_padded_segments_cannot_smuggle_traversal(self):
        # ".. " passes a raw segment check but trims to ".." when stored.
        self.assertRejected([(".. /evil.json", "{}")], "path_traversal")
        self.assertRejected([(" . /evil.json", "{}")], "path_traversal")
        self.assertRejected([(" /evil.json", "{}")], "absolute_entry_path")
        self.assertIsNone(importer.normalize_archive_relative_path(".. /x.jpg"))
        self.assertIsNone(importer.normalize_archive_relative_path("photos/.. /x.jpg"))

    def test_no_stored_entry_path_can_start_with_dotdot(self):
        path = make_zip(
            self.tmp.name,
            [("a /b.json", json.dumps({"messages": []}))],
        )
        scan = importer.scan_archive(path)
        stored = [entry.relative_path for entry in scan.entries]
        self.assertEqual(stored, ["a/b.json"])
        for name in stored:
            self.assertFalse(name.startswith("/"))
            self.assertNotIn("\\", name)
            self.assertFalse(any(part in ("..", ".") for part in name.split("/")))

    def test_backslash_entries_and_media_references_are_normalized(self):
        thread = f"{ROOT_DIR}/messages/inbox/x_1"
        path = make_zip(
            self.tmp.name,
            [
                (
                    f"{ROOT_DIR}/messages/inbox/x_1\\message_1.json",
                    person_thread(
                        "X",
                        [
                            {
                                "sender_name": "X",
                                "timestamp_ms": 1_700_000_000_000,
                                "photos": [
                                    {"uri": f"messages\\inbox\\x_1\\photos\\p.jpg"}
                                ],
                            }
                        ],
                    ),
                ),
                (f"{thread}/photos/p.jpg", b"jpeg"),
            ],
        )
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        self.addCleanup(conn.close)
        importer.ensure_schema(conn)
        result = importer.ingest(conn, path)
        self.assertEqual(result["inserted"]["events"], 1)
        self.assertEqual(result["inserted"]["attachments"], 1)
        stored = conn.execute("SELECT relative_path FROM source_files").fetchall()
        self.assertTrue(all("\\" not in row[0] for row in stored))
        self.assertIn(f"{thread}/photos/p.jpg", {row[0] for row in stored})

    def test_non_utf8_legacy_entry_name_is_accepted(self):
        name = (
            b"your_activity_across_facebook/messages/inbox/caf\x82_1/message_1.json"
        )
        payload = json.dumps(
            {
                "participants": [{"name": "Cafe"}],
                "messages": [
                    {"sender_name": "Cafe", "timestamp_ms": 1_700_000_000_000}
                ],
                "title": "Cafe",
            }
        ).encode("utf-8")
        path = Path(self.tmp.name) / "legacy.zip"
        path.write_bytes(raw_zip(name, payload))
        scan = importer.scan_archive(path)
        self.assertEqual(scan.entries[0].relative_path.count("caf\u00e9_1"), 1)
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        importer.ensure_schema(conn)
        result = importer.ingest(conn, path)
        self.assertEqual(result["inserted"]["source_files"], 1)
        self.assertEqual(result["inserted"]["events"], 1)


class MalformedJsonResilienceTests(unittest.TestCase):
    """Hostile JSON must be reported per file, never abort the whole import."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def ingest_one(self, payload: bytes):
        path = make_zip(
            self.tmp.name,
            [(f"{THREAD}/message_1.json", payload)],
            name=f"case-{abs(hash(payload))}.zip",
        )
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        self.addCleanup(conn.close)
        importer.ensure_schema(conn)
        return importer.ingest(conn, path), conn

    def test_deeply_nested_json_is_reported_not_fatal(self):
        result, conn = self.ingest_one(b"[" * 200_000 + b"]" * 200_000)
        self.assertEqual(result["status"], "imported")
        self.assertEqual(result["inserted"]["events"], 0)
        self.assertEqual(result["files_by_status"], {"unsupported": 1})
        self.assertEqual(
            tuple(
                conn.execute(
                    "SELECT parse_status, error_code FROM source_files"
                ).fetchone()
            ),
            ("unsupported", "malformed_message_json"),
        )

    def test_oversized_integer_literal_is_reported_not_fatal(self):
        payload = (
            b'{"messages":[{"sender_name":"A","timestamp_ms":'
            + b"9" * 5_000
            + b',"content":"x"}]}'
        )
        result, conn = self.ingest_one(payload)
        self.assertEqual(result["status"], "imported")
        self.assertEqual(result["inserted"]["events"], 0)
        self.assertEqual(
            conn.execute("SELECT error_code FROM source_files").fetchone()[0],
            "malformed_message_json",
        )

    def test_utf8_bom_is_accepted(self):
        payload = json.dumps(
            {
                "participants": [{"name": "A"}],
                "messages": [
                    {"sender_name": "A", "timestamp_ms": 1_700_000_000_000, "content": "bom"}
                ],
                "title": "A",
            }
        ).encode("utf-8")
        result, conn = self.ingest_one(b"\xef\xbb\xbf" + payload)
        self.assertEqual(result["inserted"]["events"], 1)
        self.assertEqual(
            conn.execute("SELECT text_content FROM events").fetchone()[0], "bom"
        )


class TimestampUnitTests(unittest.TestCase):
    """A value in the wrong unit must never be stored as a 1970 date."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        path = make_zip(
            self.tmp.name,
            [
                (
                    f"{THREAD}/message_1.json",
                    person_thread(
                        "A",
                        [
                            {"sender_name": "A", "timestamp_ms": 1_700_000_000, "content": "seconds"},
                            {"sender_name": "A", "timestamp_ms": 1_700_000_000_000, "content": "millis"},
                            {"sender_name": "A", "content": "missing"},
                            {"sender_name": "A", "timestamp_ms": "1700000000000", "content": "string"},
                        ],
                    ),
                ),
                (
                    f"{ROOT_DIR}/comments/comments.json",
                    json.dumps(
                        {
                            "comments_v2": [
                                {
                                    "timestamp": 1_700_000_000_123,
                                    "data": [
                                        {"comment": {"comment": "ms comment", "author": "B"}}
                                    ],
                                }
                            ]
                        }
                    ),
                ),
            ],
        )
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.addCleanup(self.conn.close)
        importer.ensure_schema(self.conn)
        self.result = importer.ingest(self.conn, path)

    def row(self, text):
        return self.conn.execute(
            "SELECT timestamp_raw, timestamp_normalized, timezone_status "
            "FROM events WHERE text_content = ?",
            (text,),
        ).fetchone()

    def test_message_seconds_are_recovered_instead_of_1970(self):
        row = self.row("seconds")
        self.assertEqual(row["timestamp_raw"], "1700000000")
        self.assertEqual(row["timestamp_normalized"], "2023-11-14T22:13:20Z")
        self.assertEqual(row["timezone_status"], "utc")
        self.assertIn("timestamp_seconds_inferred", self.result["coverage"]["warnings"])

    def test_message_milliseconds_are_unchanged(self):
        row = self.row("millis")
        self.assertEqual(row["timestamp_normalized"], "2023-11-14T22:13:20Z")

    def test_comment_milliseconds_are_recovered(self):
        row = self.row("ms comment")
        self.assertEqual(row["timestamp_normalized"], "2023-11-14T22:13:20Z")
        self.assertIn("timestamp_millis_inferred", self.result["coverage"]["warnings"])

    def test_missing_and_unparseable_stay_distinct(self):
        self.assertEqual(self.row("missing")["timezone_status"], "unknown")
        string = self.row("string")
        self.assertEqual(string["timezone_status"], "invalid")
        self.assertIsNone(string["timestamp_raw"])
        self.assertIsNone(string["timestamp_normalized"])


class CoverageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_known_root_json_is_known_not_imported(self):
        path = make_zip(
            self.tmp.name,
            [
                (
                    "ads_information/ads_and_topics/ads_interests.json",
                    json.dumps({"topics_v2": [{"name": "x"}]}),
                ),
                (
                    "friends_and_followers/friends.json",
                    json.dumps({"friends_v2": [{"name": "Y"}]}),
                ),
                ("notes/random.json", json.dumps({"unknown": True})),
            ],
        )
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        importer.ensure_schema(conn)
        result = importer.ingest(conn, path)
        coverage = result["coverage"]
        self.assertEqual(coverage["counts"]["files"], 3)
        self.assertEqual(sum(coverage["reasons"].values()), 3)
        self.assertEqual(coverage["counts"]["known_not_imported_files"], 2)
        self.assertEqual(coverage["reasons"].get("known_not_imported"), 2)
        self.assertEqual(coverage["reasons"].get("unsupported_json_shape"), 1)
        by_category = {row["category"]: row for row in coverage["categories"]}
        self.assertEqual(by_category["ads_information"]["known_not_imported_files"], 1)
        self.assertEqual(
            by_category["friends_and_followers"]["known_not_imported_files"], 1
        )

    def test_every_entry_gets_exactly_one_reason(self):
        path = make_zip(self.tmp.name, synthetic_entries())
        scan = importer.scan_archive(path)
        parsed = importer.parse_scan(scan)
        report = importer.scan_report(scan, parsed)
        self.assertEqual(report["counts"]["files"], len(scan.entries))
        self.assertEqual(sum(report["reasons"].values()), len(scan.entries))
        self.assertEqual(
            report["counts"]["supported_files"], report["reasons"].get("supported", 0)
        )


class EvidenceBoundaryTests(unittest.TestCase):
    """The gate must bound events, chars and bytes, and never store excerpts."""

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.addCleanup(self.conn.close)
        importer.ensure_schema(self.conn)
        self.conn.execute(
            "INSERT INTO imports (id, platform, format, source_fingerprint, "
            "started_at, importer_version, status) "
            "VALUES (1, 'facebook', 'json', 'f', 'now', 'v', 'completed')"
        )
        self.conn.execute(
            "INSERT INTO source_files (id, import_id, relative_path, parse_status) "
            "VALUES (1, 1, 'a.json', 'parsed')"
        )
        self.ids = []

    def add(self, text):
        event_id = self.conn.execute(
            "INSERT INTO events (import_id, source_file_id, source_locator, "
            "platform, event_type, timezone_status, text_content) "
            "VALUES (1, 1, ?, 'facebook', 'message', 'unknown', ?)",
            (f"a.json#/messages/{len(self.ids)}", text),
        ).lastrowid
        self.ids.append(event_id)
        return event_id

    def consent(self):
        return self.conn.execute(
            "SELECT * FROM ai_consents ORDER BY id DESC LIMIT 1"
        ).fetchone()

    def test_whitespace_only_text_is_skipped(self):
        blank = self.add("  \n\t  ")
        real = self.add("real text")
        bundle = gate.build_bundle(self.conn, [blank, real], "p", "v")
        self.assertEqual([row["id"] for row in bundle["excerpts"]], [real])
        self.assertEqual(bundle["event_count"], 1)
        self.assertEqual(bundle["char_count"], len("real text"))

    def test_duplicate_ids_are_counted_once(self):
        event_id = self.add("only event")
        bundle = gate.build_bundle(self.conn, [event_id] * 50, "p", "v")
        self.assertEqual(bundle["event_count"], 1)
        self.assertEqual(len(bundle["excerpts"]), 1)
        self.assertEqual(self.consent()["event_count"], 1)

    def test_byte_cap_is_enforced(self):
        for index in range(5):
            self.add(f"abcdefghij{index}")
        bundle = gate.build_bundle(
            self.conn,
            self.ids,
            "p",
            "v",
            max_total_chars=4000,
            max_total_bytes=25,
        )
        self.assertEqual(bundle["event_count"], 2)
        self.assertEqual(bundle["byte_count"], 22)
        self.assertLessEqual(bundle["byte_count"], 25)
        self.assertEqual(bundle["limits"]["max_bytes"], 25)
        self.assertLessEqual(self.consent()["byte_count"], 25)

    def test_consent_row_never_contains_excerpt_text(self):
        self.add("SECRET-EXCERPT")
        gate.build_bundle(self.conn, self.ids, "p", "v")
        serialized = json.dumps(dict(self.consent()), ensure_ascii=False)
        self.assertNotIn("SECRET-EXCERPT", serialized)


class LocatorAndShapeTests(unittest.TestCase):
    """Nested/flat/mixed shapes must never collide on one source locator."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_mixed_comment_shapes_have_unique_locators(self):
        path = make_zip(
            self.tmp.name,
            [
                (
                    f"{ROOT_DIR}/comments/comments.json",
                    json.dumps(
                        {
                            "comments_v2": [
                                {"timestamp": 1_700_000_100, "comment": "flat", "author": "A"},
                                {
                                    "timestamp": 1_700_000_200,
                                    "data": [
                                        {"comment": {"comment": "n1", "author": "B"}},
                                        {"comment": {"comment": "n2", "author": "C"}},
                                    ],
                                },
                                {"timestamp": 1_700_000_300, "data": []},
                                {"timestamp": 1_700_000_400, "data": [{"nope": 1}]},
                            ]
                        }
                    ),
                ),
                (
                    f"{ROOT_DIR}/posts/your_posts_1.json",
                    json.dumps(
                        [
                            {"timestamp": 1_700_000_500, "data": []},
                            {
                                "timestamp": 1_700_000_600,
                                "data": [],
                                "attachments": [
                                    {"data": [{"media": {"uri": "missing.jpg"}}]}
                                ],
                            },
                        ]
                    ),
                ),
            ],
        )
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        self.addCleanup(conn.close)
        importer.ensure_schema(conn)
        result = importer.ingest(conn, path)
        self.assertEqual(result["inserted"]["events"], 4)
        self.assertEqual(result["records_skipped"], 2)
        duplicates = conn.execute(
            "SELECT source_locator, COUNT(*) FROM events "
            "GROUP BY source_locator HAVING COUNT(*) > 1"
        ).fetchall()
        self.assertEqual(duplicates, [])
        self.assertIn("attachment_missing", result["coverage"]["warnings"])

    def test_reaction_with_actor_only_in_title_is_kept_without_actor(self):
        path = make_zip(
            self.tmp.name,
            [
                (
                    f"{ROOT_DIR}/likes_and_reactions/likes_and_reactions_1.json",
                    json.dumps(
                        {
                            "reactions_v2": [
                                {
                                    "timestamp": 1_700_000_700,
                                    "title": "Hala reacted to X",
                                    "data": [{"reaction": {"reaction": "\ud83d\udc4d"}}],
                                }
                            ]
                        }
                    ),
                )
            ],
        )
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        self.addCleanup(conn.close)
        importer.ensure_schema(conn)
        result = importer.ingest(conn, path)
        self.assertEqual(result["inserted"]["events"], 1)
        self.assertIsNone(conn.execute("SELECT actor_id FROM events").fetchone()[0])


if __name__ == "__main__":
    unittest.main()
