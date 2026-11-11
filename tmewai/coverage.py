"""Per-file coverage classification, ported from the Dart import report.

Every archive file gets exactly one reason code. Codes are content-free: they
carry no file text and no host path, so a report can be stored or shown
without leaking the archive.
"""

from __future__ import annotations

from typing import Iterable

# Documented Facebook export roots, in the order the Dart coverage report lists
# them. First two path segments only, matched as whole segments.
IMPORT_CATEGORIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("ads_information", ("ads_information", "ads")),
    ("friends_and_followers", ("friends_and_followers", "friends")),
    ("personal_information", ("personal_information", "profile_information")),
    ("activity", ("your_activity_across_facebook", "your_facebook_activity")),
    ("your_places", ("your_places",)),
    ("logged_information", ("logged_information",)),
    ("security_and_login_information", ("security_and_login_information",)),
    ("preferences", ("preferences", "settings")),
    ("facebook_profile_information", ("facebook_profile_information",)),
    (
        "apps_and_websites",
        ("apps_and_websites_off_your_facebook_activity", "apps_and_websites"),
    ),
    ("connections", ("connections",)),
    ("messages", ("messages",)),
    ("photos_and_videos", ("photos_and_videos", "videos")),
    ("posts", ("posts", "your_posts")),
    ("comments", ("comments", "your_comments")),
    ("likes_and_reactions", ("likes_and_reactions",)),
    ("groups", ("groups",)),
    ("events", ("events",)),
    ("stories", ("stories",)),
    ("search", ("search",)),
)

_CATEGORY_BY_ROOT = {
    root: code for code, roots in IMPORT_CATEGORIES for root in roots
}

# Reason codes (the Dart ImportCoverageReason vocabulary).
SUPPORTED = "supported"
MALFORMED_JSON = "malformed_json"
UNSUPPORTED_JSON_SHAPE = "unsupported_json_shape"
UNSUPPORTED_MEDIA = "unsupported_media"
UNKNOWN_EXTENSION = "unknown_extension"
KNOWN_NOT_IMPORTED = "known_not_imported"
BLOCKED_UNSAFE_PATH = "blocked_unsafe_path"
OVERSIZED_ARCHIVE = "oversized_archive"
INVALID_ARCHIVE = "invalid_archive"

# Parser codes that mean "valid JSON, no model for this shape". Under a
# documented export root those become known_not_imported, exactly as the Dart
# report does; malformed and blocked files keep their own reason.
_UNSUPPORTED_SHAPE_CODES = frozenset(
    {
        UNSUPPORTED_JSON_SHAPE,
        UNKNOWN_EXTENSION,
        "unsupported_message_json",
        "unsupported_comments_json",
        "unsupported_reactions_json",
        "unsupported_posts_json",
    }
)

# These specific codes are emitted by the Facebook parser; they are reasons
# too, and must survive into source_files.error_code.
BY_CODE = {
    "malformed_message_json",
    "malformed_comments_json",
    "malformed_reactions_json",
    "malformed_posts_json",
    "unsupported_message_json",
    "unsupported_comments_json",
    "unsupported_reactions_json",
    "unsupported_posts_json",
    "invalid_message_record",
    "invalid_comment_record",
    "invalid_reaction_record",
    "invalid_post_record",
    "invalid_message_timestamp",
    "invalid_comment_timestamp",
    "invalid_reaction_timestamp",
    "invalid_post_timestamp",
}

# One general coverage reason per parser code family: the report speaks the
# documented reason vocabulary while `source_files.error_code` keeps the exact
# parser code.
_REASON_FOR_PARSER_CODE = {
    code: MALFORMED_JSON
    for code in BY_CODE
    if code.startswith("malformed_")
}
_REASON_FOR_PARSER_CODE.update(
    {code: UNSUPPORTED_JSON_SHAPE for code in BY_CODE if code.startswith("unsupported_")}
)

IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".avif",
}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm", ".avi"}
AUDIO_EXTENSIONS = {".mp3", ".m4a", ".wav", ".ogg", ".opus", ".aac"}


def category_for_path(relative_path: str) -> str | None:
    """The documented export root a path sits under, or None."""
    segments = relative_path.replace("\\", "/").split("/")[:2]
    for segment in segments:
        code = _CATEGORY_BY_ROOT.get(segment.strip().lower())
        if code is not None:
            return code
    return None


def is_media_path(relative_path: str) -> bool:
    lowered = relative_path.lower()
    dot = lowered.rfind(".")
    if dot < 0:
        return False
    return lowered[dot:] in (
        IMAGE_EXTENSIONS | VIDEO_EXTENSIONS | AUDIO_EXTENSIONS
    )


def media_kind_for_path(relative_path: str) -> str:
    lowered = relative_path.lower()
    dot = lowered.rfind(".")
    extension = lowered[dot:] if dot >= 0 else ""
    if extension in IMAGE_EXTENSIONS:
        return "image"
    if extension in VIDEO_EXTENSIONS:
        return "video"
    if extension in AUDIO_EXTENSIONS:
        return "audio"
    return "file"


def source_type(relative_path: str) -> str:
    lowered = relative_path.lower()
    if is_media_path(relative_path):
        return "media"
    if lowered.endswith(".json"):
        return "json"
    return "other"


def classify_unparsed(relative_path: str, reason: str) -> str:
    """A path under a documented root is never reported as simply unknown."""
    if (
        category_for_path(relative_path) is not None
        and reason in _UNSUPPORTED_SHAPE_CODES
    ):
        return KNOWN_NOT_IMPORTED
    return reason


def build_report(entries: Iterable[dict], outcomes: dict[str, dict]) -> dict:
    """Build the coverage counts for ``archive_scan`` / ``archive_ingest``.

    ``entries`` are scan entries with ``relative_path``, ``is_media`` and
    ``is_json``. ``outcomes`` maps a path to ``{status, error_code,
    records_parsed, records_skipped}`` for files the parser examined.
    """
    reason_counts: dict[str, int] = {}
    category_rows: dict[str, dict] = {}
    unclassified: list[str] = []
    files = supported = blocked = 0
    records_parsed = records_skipped = 0

    for entry in entries:
        path = entry["relative_path"]
        outcome = outcomes.get(path)
        category = category_for_path(path)

        if outcome is None:
            if entry.get("is_media"):
                reason = UNSUPPORTED_MEDIA
            elif entry.get("is_json"):
                reason = UNSUPPORTED_JSON_SHAPE
            else:
                reason = UNKNOWN_EXTENSION
            reason = classify_unparsed(path, reason)
            status = "unsupported"
        else:
            status = outcome["status"]
            if status == "parsed":
                reason = SUPPORTED
            else:
                raw_reason = outcome.get("error_code") or UNSUPPORTED_JSON_SHAPE
                reason = _REASON_FOR_PARSER_CODE.get(raw_reason, raw_reason)
                reason = classify_unparsed(path, reason)

        files += 1
        reason_counts[reason] = reason_counts.get(reason, 0) + 1
        if status == "parsed":
            supported += 1
        elif reason in (BLOCKED_UNSAFE_PATH, OVERSIZED_ARCHIVE, INVALID_ARCHIVE):
            blocked += 1
        if reason in (UNKNOWN_EXTENSION, UNSUPPORTED_JSON_SHAPE):
            unclassified.append(path)

        parsed = outcome.get("records_parsed", 0) if outcome else 0
        skipped = outcome.get("records_skipped", 0) if outcome else 0
        records_parsed += parsed
        records_skipped += skipped

        if category is not None:
            row = category_rows.setdefault(
                category,
                {
                    "category": category,
                    "files": 0,
                    "supported_files": 0,
                    "known_not_imported_files": 0,
                    "records_parsed": 0,
                    "records_skipped": 0,
                },
            )
            row["files"] += 1
            if status == "parsed":
                row["supported_files"] += 1
            if reason == KNOWN_NOT_IMPORTED:
                row["known_not_imported_files"] += 1
            row["records_parsed"] += parsed
            row["records_skipped"] += skipped

    known_not_imported = reason_counts.get(KNOWN_NOT_IMPORTED, 0)
    unsupported = files - supported - blocked - known_not_imported
    ordered_categories = [
        category_rows[code]
        for code, _ in IMPORT_CATEGORIES
        if code in category_rows
    ]
    return {
        "counts": {
            "files": files,
            "supported_files": supported,
            "unsupported_files": unsupported,
            "known_not_imported_files": known_not_imported,
            "blocked_files": blocked,
            "records_parsed": records_parsed,
            "records_skipped": records_skipped,
        },
        "reasons": dict(sorted(reason_counts.items())),
        "categories": ordered_categories,
        "unclassified_sample": unclassified[:20],
    }
