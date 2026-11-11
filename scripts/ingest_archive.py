#!/usr/bin/env python3
"""Ingest one archive twice and print the measured result as JSON.

This is the small piece of tooling behind `make demo` and `make big`. It is not
part of the plugin; the handlers stay the only public interface.
"""

from __future__ import annotations

import argparse
import json
import resource
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tmewai import importer  # noqa: E402

TABLES = (
    "imports",
    "source_files",
    "people",
    "conversations",
    "events",
    "ai_consents",
    "attachments",
)


def totals(conn: sqlite3.Connection) -> dict[str, int]:
    return {
        table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in TABLES
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("archive")
    parser.add_argument("--db", required=True)
    parser.add_argument("--label", default="archive")
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="Delete the --db file first so the first ingest is a real import.",
    )
    args = parser.parse_args()

    archive = Path(args.archive)
    db = Path(args.db)
    db.parent.mkdir(parents=True, exist_ok=True)
    if args.fresh and db.exists():
        db.unlink()

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        importer.ensure_schema(conn)

        started = time.perf_counter()
        first = importer.ingest(conn, archive)
        first_seconds = time.perf_counter() - started

        after_first = totals(conn)
        started = time.perf_counter()
        second = importer.ingest(conn, archive)
        second_seconds = time.perf_counter() - started
        after_second = totals(conn)

        report = {
            "label": args.label,
            "archive": str(archive),
            "archive_bytes": archive.stat().st_size,
            "db": str(db),
            "db_bytes": db.stat().st_size,
            "first": {
                "status": first["status"],
                "seconds": round(first_seconds, 3),
                "inserted": first["inserted"],
                "files_by_status": first["files_by_status"],
                "events_by_type": first["events_by_type"],
                "coverage": {
                    "counts": first["coverage"]["counts"],
                    "reasons": first["coverage"]["reasons"],
                    "categories": first["coverage"]["categories"],
                },
            },
            "second": {
                "status": second["status"],
                "seconds": round(second_seconds, 3),
                "inserted": second["inserted"],
                "row_delta": {
                    table: after_second[table] - after_first[table]
                    for table in TABLES
                },
            },
            "totals": after_second,
            "peak_rss_mb": round(
                resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1
            ),
        }
        ok = second["status"] == "reused" and all(
            value == 0 for value in second["inserted"].values()
        ) and all(
            after_first[table] == after_second[table] for table in TABLES
        )
        report["idempotent"] = ok
    finally:
        conn.close()

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["idempotent"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
