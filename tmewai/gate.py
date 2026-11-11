"""Bounded evidence bundle and consent gate, ported from ``ai_gateway.dart``.

The gateway has no provider client: this module builds the exact bounded
bundle, records the consent, and stops. It never dispatches, never opens a
socket and never imports a network library.
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from typing import Any, Iterable

MAX_EVENTS = 20
MAX_TOTAL_CHARS = 4000
# A hard ceiling on the UTF-8 bytes in one bundle. 4000 BMP characters cannot
# exceed it, so the brief's character bound is unchanged; astral text can, and
# a byte-ceiling keeps a "bounded" bundle bounded in bytes too.
MAX_TOTAL_BYTES = 16_000
MAX_LOCATOR_LENGTH = 2048


class ConsentRequired(Exception):
    """Raised when a dispatch is attempted without an explicit grant."""


class DispatchNotImplemented(Exception):
    """This version has no provider client, so nothing is ever sent."""


def is_relative_locator(locator: str) -> bool:
    if not locator or len(locator) > MAX_LOCATOR_LENGTH:
        return False
    if locator.startswith("/") or locator.startswith("\\"):
        return False
    if re.match(r"^[A-Za-z]:", locator):
        return False
    return not any(segment == ".." for segment in re.split(r"[/\\]", locator))


def build_bundle(
    conn: sqlite3.Connection,
    event_ids: Iterable[int],
    provider_id: str,
    policy_version: str,
    max_events: int = MAX_EVENTS,
    max_total_chars: int = MAX_TOTAL_CHARS,
    max_total_bytes: int = MAX_TOTAL_BYTES,
) -> dict[str, Any]:
    """Select at most ``max_events`` excerpts within the char and byte caps.

    Selection walks the caller's order, so the bundle is explainable. Repeated
    ids are counted once, rows that do not exist, carry blank text, or whose
    locator could reveal a host path are skipped. The consent row stores only
    provider, policy, counts and bytes.
    """
    if max_events < 1 or max_total_chars < 1 or max_total_bytes < 1:
        raise ValueError("AI evidence limits must be positive.")
    if not provider_id or not policy_version:
        raise ValueError("provider_id and policy_version are required.")

    wanted: list[int] = []
    seen: set[int] = set()
    for value in event_ids:
        if not isinstance(value, int) or isinstance(value, bool):
            continue
        if value in seen:
            continue
        seen.add(value)
        wanted.append(value)
        if len(wanted) >= max_events:
            break

    rows_by_id: dict[int, dict[str, Any]] = {}
    if wanted:
        placeholders = ",".join("?" for _ in wanted)
        rows = conn.execute(
            "SELECT e.id, e.text_content, e.source_locator, e.event_type, "
            "e.timestamp_normalized, p.display_name AS person "
            "FROM events e LEFT JOIN people p ON p.id = e.actor_id "
            f"WHERE e.id IN ({placeholders}) ORDER BY e.id ASC",
            wanted,
        ).fetchall()
        # Name the columns explicitly: a caller's own connection may not set
        # row_factory = sqlite3.Row (connect() does, a bare sqlite3.connect()
        # does not), and then row["id"] raises TypeError.
        columns = ("id", "text_content", "source_locator", "event_type",
                   "timestamp_normalized", "person")
        rows_by_id = {row[0]: dict(zip(columns, row)) for row in rows}

    selected: list[dict[str, Any]] = []
    excerpts: list[str] = []
    chars = 0
    byte_count = 0

    for event_id in wanted:
        row = rows_by_id.get(event_id)
        if row is None:
            continue
        text = row["text_content"]
        if not text or not text.strip():
            continue
        locator = row["source_locator"]
        if not is_relative_locator(locator):
            continue
        text_bytes = len(text.encode("utf-8"))
        # Skip what does not fit and keep walking: a single huge event used to
        # abort the whole bundle (a 5000-char first message returned 0 events
        # while five short ones would have fit).
        if chars + len(text) > max_total_chars:
            continue
        if byte_count + text_bytes > max_total_bytes:
            continue
        if len(selected) >= max_events:
            break

        selected.append(
            {
                "id": row["id"],
                "ts": row["timestamp_normalized"],
                "type": row["event_type"],
                "person": row["person"],
                "source_locator": locator,
                "text": text,
            }
        )
        excerpts.append(text)
        chars += len(text)
        byte_count += text_bytes

    scope_summary = f"events:{len(selected)}; chars:{chars}; bytes:{byte_count}"
    granted_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    consent_id = conn.execute(
        "INSERT INTO ai_consents (provider_id, policy_version, scope_summary, "
        "granted_at, event_count, byte_count) VALUES (?, ?, ?, ?, ?, ?)",
        (
            provider_id,
            policy_version,
            scope_summary,
            granted_at,
            len(selected),
            byte_count,
        ),
    ).lastrowid
    conn.commit()

    return {
        "consent_id": consent_id,
        "provider_id": provider_id,
        "policy_version": policy_version,
        "scope_summary": scope_summary,
        "event_count": len(selected),
        "char_count": chars,
        "byte_count": byte_count,
        "excerpts": selected,
        "limits": {
            "max_events": max_events,
            "max_chars": max_total_chars,
            "max_bytes": max_total_bytes,
        },
        "dispatched": False,
        "notice": (
            "Bundle built locally for the agent. Nothing was sent to the "
            "provider; review the excerpts before sharing them."
        ),
    }


def dispatch(*, bundle: dict[str, Any], consent_provider: str | None,
             consent_policy: str | None) -> None:
    """Refuse to dispatch: consent alone is not a provider client."""
    if (
        not consent_provider
        or consent_provider != bundle.get("provider_id")
        or consent_policy != bundle.get("policy_version")
    ):
        raise ConsentRequired("Explicit consent for this provider and policy is required.")
    raise DispatchNotImplemented(
        "AI dispatch is not implemented: no provider client exists and no "
        + "network request is made."
    )


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
