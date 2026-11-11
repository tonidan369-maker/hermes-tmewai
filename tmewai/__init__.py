"""Hermes plugin ``tmewai``: a local social-archive index.

Registers six tools over one SQLite database. Standard library only; there is
no network code anywhere in this package.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Callable

from . import gate, importer
from .folds import fold_text, match_query

TOOLSET = "tmewai"
PLUGIN_DATA_DIR = Path("plugin-data") / "tmewai"
EVENT_TYPES = ("message", "comment", "reaction", "post", "photo", "checkin", "other")

_SCAN_SCHEMA = {
    "type": "object",
    "properties": {
        "path": {
            "type": "string",
            "description": "Path to a Facebook JSON export ZIP. It is read in place and never extracted.",
        }
    },
    "required": ["path"],
    "additionalProperties": False,
}

_INGEST_SCHEMA = {
    "type": "object",
    "properties": {
        "path": {"type": "string", "description": "Path to the export ZIP."},
        "platform": {
            "type": "string",
            "description": "Source platform label stored with the import.",
            "default": "facebook",
        },
    },
    "required": ["path"],
    "additionalProperties": False,
}

_SEARCH_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "Search text. Arabic harakat, alef forms, taa marbuta and tatweel are folded.",
        },
        "event_type": {
            "type": "string",
            "enum": list(EVENT_TYPES),
            "description": "Optional stored event type filter.",
        },
        "after": {
            "type": "string",
            "description": "Inclusive lower bound: YYYY-MM-DD or ISO 8601 UTC.",
        },
        "before": {
            "type": "string",
            "description": "Inclusive upper bound: YYYY-MM-DD or ISO 8601 UTC.",
        },
        "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": 200,
            "default": 20,
        },
    },
    "required": ["query"],
    "additionalProperties": False,
}

_PERSON_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {
            "type": "string",
            "description": "Display name to resolve: exact match first, then substring.",
        }
    },
    "required": ["name"],
    "additionalProperties": False,
}

_OVERVIEW_SCHEMA = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}

_EVIDENCE_SCHEMA = {
    "type": "object",
    "properties": {
        "event_ids": {
            "type": "array",
            "items": {"type": "integer"},
            "description": "Event ids to consider; at most 20 excerpts are kept.",
        },
        "provider_id": {"type": "string"},
        "policy_version": {"type": "string"},
    },
    "required": ["event_ids", "provider_id", "policy_version"],
    "additionalProperties": False,
}


def _db_path() -> Path:
    override = os.environ.get("TMEWAI_DB_PATH")
    if override:
        return Path(override).expanduser()
    hermes_home = os.environ.get("HERMES_HOME")
    base = Path(hermes_home).expanduser() if hermes_home else Path.home() / ".hermes"
    return base / PLUGIN_DATA_DIR / "archive.db"


def connect() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    importer.ensure_schema(conn)
    return conn


def _ok(value: Any, *, indent: int | None = 2) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        indent=indent,
        separators=(",", ":") if indent is None else None,
    )


def _error(code: str, message: str) -> str:
    return _ok({"status": "error", "code": code, "message": message})


def _handle_scan(args: dict[str, Any]) -> str:
    path = args.get("path")
    if not isinstance(path, str) or not path:
        return _error("invalid_arguments", "path is required.")
    try:
        scan = importer.scan_archive(path)
        parsed = importer.parse_scan(scan)
    except importer.ArchiveError as exc:
        return _error(exc.code, exc.message)
    except OSError:
        return _error("archive_unreadable", "The selected archive could not be opened.")
    return _ok(importer.scan_report(scan, parsed))


def _handle_ingest(args: dict[str, Any]) -> str:
    path = args.get("path")
    platform = args.get("platform") or "facebook"
    if not isinstance(path, str) or not path:
        return _error("invalid_arguments", "path is required.")
    if not isinstance(platform, str) or not platform.strip():
        return _error("invalid_arguments", "platform must be a non-empty string.")
    try:
        conn = connect()
    except OSError as exc:
        return _error("database_unavailable", str(exc))
    try:
        result = importer.ingest(conn, path, platform.strip())
    except importer.ArchiveError as exc:
        return _error(exc.code, exc.message)
    except sqlite3.Error as exc:
        return _error("database_error", str(exc))
    finally:
        conn.close()
    return _ok(result)


def _bound(value: str, *, upper: bool) -> str:
    if len(value) == 10 and value[4] == "-" and value[7] == "-":
        return value + ("T23:59:59Z" if upper else "T00:00:00Z")
    return value


def _handle_search(args: dict[str, Any]) -> str:
    query = args.get("query")
    if not isinstance(query, str) or not query.strip():
        return _error("invalid_arguments", "query is required.")
    event_type = args.get("event_type")
    after = args.get("after")
    before = args.get("before")
    limit = args.get("limit", 20)
    if not isinstance(limit, int) or isinstance(limit, bool):
        limit = 20
    limit = max(1, min(200, limit))

    expression = match_query(query)
    if not expression:
        return _ok({"query": query, "folded": fold_text(query), "count": 0, "rows": []}, indent=None)

    sql = (
        "SELECT e.id AS id, e.timestamp_normalized AS ts, e.event_type AS type, "
        "e.source_locator AS locator, e.text_content AS text, "
        "e.metadata_json AS metadata_json, p.display_name AS person, "
        "snippet(event_search, 1, '[', ']', ' … ', 14) AS snip "
        "FROM event_search "
        "JOIN events e ON e.id = event_search.event_id "
        "LEFT JOIN people p ON p.id = e.actor_id "
        "WHERE event_search MATCH ? "
        "AND (? IS NULL OR e.event_type = ?) "
        "AND (? IS NULL OR e.timestamp_normalized >= ?) "
        "AND (? IS NULL OR e.timestamp_normalized <= ?) "
        "ORDER BY (e.timestamp_normalized IS NULL), "
        "e.timestamp_normalized DESC, e.id DESC LIMIT ?"
    )
    params = (
        expression,
        event_type, event_type,
        _bound(after, upper=False) if isinstance(after, str) and after else None,
        _bound(after, upper=False) if isinstance(after, str) and after else None,
        _bound(before, upper=True) if isinstance(before, str) and before else None,
        _bound(before, upper=True) if isinstance(before, str) and before else None,
        limit,
    )
    try:
        conn = connect()
        try:
            rows = conn.execute(sql, params).fetchall()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        return _error("database_error", str(exc))

    results = []
    for row in rows:
        snippet = row["snip"] or ""
        if not snippet:
            snippet = (row["text"] or "")[:280]
        if not snippet and row["metadata_json"]:
            metadata = json.loads(row["metadata_json"])
            snippet = metadata.get("reaction") or metadata.get("payload") or ""
        results.append(
            {
                "id": row["id"],
                "ts": row["ts"],
                "type": row["type"],
                "person": row["person"],
                "snippet": snippet,
                "source_locator": row["locator"],
            }
        )
    return _ok(
        {
            "query": query,
            "folded": fold_text(query),
            "count": len(results),
            "rows": results,
        },
        indent=None,
    )


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _handle_person(args: dict[str, Any]) -> str:
    name = args.get("name")
    if not isinstance(name, str) or not name.strip():
        return _error("invalid_arguments", "name is required.")
    name = name.strip()
    try:
        conn = connect()
        try:
            rows = conn.execute(
                "SELECT id, display_name FROM people WHERE display_name = ? "
                "ORDER BY id LIMIT 2",
                (name,),
            ).fetchall()
            match_kind = "exact"
            if not rows:
                match_kind = "substring"
                rows = conn.execute(
                    "SELECT id, display_name FROM people "
                    "WHERE display_name LIKE ? ESCAPE '\\' "
                    "ORDER BY length(display_name), display_name, id LIMIT 20",
                    (f"%{_escape_like(name)}%",),
                ).fetchall()
            if not rows:
                return _ok({"name": name, "found": False, "matches": 0})

            person = rows[0]
            person_id = person["id"]
            totals = {
                row["event_type"]: row["count"]
                for row in conn.execute(
                    "SELECT event_type, COUNT(*) AS count FROM events "
                    "WHERE actor_id = ? GROUP BY event_type ORDER BY count DESC",
                    (person_id,),
                ).fetchall()
            }
            bounds = conn.execute(
                "SELECT MIN(timestamp_normalized) AS first, "
                "MAX(timestamp_normalized) AS last, COUNT(*) AS total "
                "FROM events WHERE actor_id = ?",
                (person_id,),
            ).fetchone()
            conversations = [
                {
                    "conversation_id": row["conversation_id"],
                    "title": row["title"],
                    "events": row["count"],
                }
                for row in conn.execute(
                    "SELECT c.id AS conversation_id, c.title, COUNT(*) AS count "
                    "FROM events e JOIN conversations c ON c.id = e.conversation_id "
                    "WHERE e.actor_id = ? "
                    "GROUP BY c.id ORDER BY count DESC, c.id LIMIT 10",
                    (person_id,),
                ).fetchall()
            ]
            recent = [
                {
                    "id": row["id"],
                    "ts": row["ts"],
                    "type": row["type"],
                    "snippet": row["snippet"] or _reaction_snippet(row["metadata_json"]),
                    "source_locator": row["locator"],
                }
                for row in conn.execute(
                    "SELECT id, timestamp_normalized AS ts, event_type AS type, "
                    "substr(text_content, 1, 280) AS snippet, source_locator AS locator, "
                    "metadata_json FROM events WHERE actor_id = ? "
                    "ORDER BY (timestamp_normalized IS NULL), "
                    "timestamp_normalized DESC, id DESC LIMIT 10",
                    (person_id,),
                ).fetchall()
            ]
        finally:
            conn.close()
    except sqlite3.Error as exc:
        return _error("database_error", str(exc))

    return _ok(
        {
            "person": {"id": person_id, "name": person["display_name"]},
            "match": match_kind,
            "matches": len(rows),
            "totals": totals,
            "events": bounds["total"],
            "date_range": {"first": bounds["first"], "last": bounds["last"]},
            "top_conversations": conversations,
            "recent_events": recent,
        }
    )


def _reaction_snippet(metadata_json: str | None) -> str:
    if not metadata_json:
        return ""
    try:
        metadata = json.loads(metadata_json)
    except json.JSONDecodeError:
        return ""
    return metadata.get("reaction") or metadata.get("payload") or ""


def _handle_overview(args: dict[str, Any]) -> str:
    try:
        conn = connect()
        try:
            by_type = {
                row["event_type"]: row["count"]
                for row in conn.execute(
                    "SELECT event_type, COUNT(*) AS count FROM events "
                    "GROUP BY event_type ORDER BY count DESC, event_type"
                ).fetchall()
            }
            bounds = conn.execute(
                "SELECT MIN(timestamp_normalized) AS first, "
                "MAX(timestamp_normalized) AS last, COUNT(*) AS total FROM events"
            ).fetchone()
            correspondents = [
                {"name": row["name"], "events": row["count"]}
                for row in conn.execute(
                    "SELECT p.display_name AS name, COUNT(*) AS count "
                    "FROM events e JOIN people p ON p.id = e.actor_id "
                    "GROUP BY p.id ORDER BY count DESC, p.display_name LIMIT 10"
                ).fetchall()
            ]
            conversations = [
                {
                    "conversation_id": row["id"],
                    "title": row["title"],
                    "events": row["count"],
                }
                for row in conn.execute(
                    "SELECT c.id, c.title, COUNT(e.id) AS count "
                    "FROM conversations c LEFT JOIN events e ON e.conversation_id = c.id "
                    "GROUP BY c.id ORDER BY count DESC, c.id LIMIT 10"
                ).fetchall()
            ]
            imports = [
                {
                    "id": row["id"],
                    "platform": row["platform"],
                    "format": row["format"],
                    "status": row["status"],
                    "started_at": row["started_at"],
                    "completed_at": row["completed_at"],
                    "fingerprint": row["source_fingerprint"][:16],
                    "events": row["events"],
                    "files": row["files"],
                }
                for row in conn.execute(
                    "SELECT i.id, i.platform, i.format, i.status, i.started_at, "
                    "i.completed_at, i.source_fingerprint, "
                    "(SELECT COUNT(*) FROM events e WHERE e.import_id = i.id) AS events, "
                    "(SELECT COUNT(*) FROM source_files f WHERE f.import_id = i.id) AS files "
                    "FROM imports i ORDER BY i.id"
                ).fetchall()
            ]
            people_total = conn.execute("SELECT COUNT(*) FROM people").fetchone()[0]
            files_total = conn.execute("SELECT COUNT(*) FROM source_files").fetchone()[0]
        finally:
            conn.close()
    except sqlite3.Error as exc:
        return _error("database_error", str(exc))

    return _ok(
        {
            "totals": {
                "events": bounds["total"],
                "people": people_total,
                "source_files": files_total,
                "by_event_type": by_type,
            },
            "date_range": {"first": bounds["first"], "last": bounds["last"]},
            "top_correspondents": correspondents,
            "top_conversations": conversations,
            "imports": imports,
        }
    )


def _handle_evidence(args: dict[str, Any]) -> str:
    event_ids = args.get("event_ids")
    provider_id = args.get("provider_id")
    policy_version = args.get("policy_version")
    if not isinstance(event_ids, list) or not all(
        isinstance(value, int) and not isinstance(value, bool) for value in event_ids
    ):
        return _error("invalid_arguments", "event_ids must be a list of integers.")
    if not isinstance(provider_id, str) or not provider_id.strip():
        return _error("invalid_arguments", "provider_id is required.")
    if not isinstance(policy_version, str) or not policy_version.strip():
        return _error("invalid_arguments", "policy_version is required.")
    try:
        conn = connect()
        try:
            bundle = gate.build_bundle(
                conn,
                event_ids[:500],
                provider_id.strip(),
                policy_version.strip(),
            )
        finally:
            conn.close()
    except sqlite3.Error as exc:
        return _error("database_error", str(exc))
    return _ok(bundle)


def _register(
    ctx: Any,
    name: str,
    schema: dict[str, Any],
    handler: Callable[[dict[str, Any]], str],
    description: str,
    read_only: bool,
) -> None:
    """Register a tool, passing ``read_only`` when the host accepts it."""
    kwargs = {
        "name": name,
        "toolset": TOOLSET,
        "schema": schema,
        "handler": handler,
        "description": description,
    }
    try:
        ctx.register_tool(read_only=read_only, **kwargs)
    except TypeError:
        ctx.register_tool(**kwargs)


def register(ctx: Any) -> None:
    """Register the six archive tools with Hermes."""
    _register(
        ctx,
        "archive_scan",
        _SCAN_SCHEMA,
        _handle_scan,
        "Scan a Facebook JSON export ZIP without importing: validate paths, "
        "classify every file and report coverage. Never writes the database.",
        True,
    )
    _register(
        ctx,
        "archive_ingest",
        _INGEST_SCHEMA,
        _handle_ingest,
        "Scan and import a Facebook JSON export ZIP in one transaction. "
        "Idempotent: the same archive is reused instead of duplicated.",
        False,
    )
    _register(
        ctx,
        "archive_search",
        _SEARCH_SCHEMA,
        _handle_search,
        "Full-text search over imported events with Arabic folding, optional "
        "event-type and date filters.",
        True,
    )
    _register(
        ctx,
        "archive_person",
        _PERSON_SCHEMA,
        _handle_person,
        "Resolve a person by display name and return totals, date range, top "
        "conversations and recent events.",
        True,
    )
    _register(
        ctx,
        "archive_overview",
        _OVERVIEW_SCHEMA,
        _handle_overview,
        "Return archive totals, date range, top correspondents, top "
        "conversations and the imports list.",
        True,
    )
    _register(
        ctx,
        "archive_evidence",
        _EVIDENCE_SCHEMA,
        _handle_evidence,
        "Build a bounded evidence bundle (20 events / 4000 chars), record "
        "consent metadata only, and dispatch nothing.",
        False,
    )
