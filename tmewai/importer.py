"""Facebook JSON export scanning, parsing and import.

Ported from the Dart Tmewai app:

* ``archive_inspector.dart`` - ZIP validation and bounded reads, no extraction
* ``facebook_json_importer.dart`` - file classification and the four JSON shapes
* ``import_coverage_report.dart`` - per-file coverage classification
* ``tables.dart`` / ``event_search_schema.dart`` - storage and FTS5 triggers

Standard library only. Nothing in this module reads or writes the network, and
the archive is never extracted to disk.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import coverage
from .folds import sql_fold_expression

IMPORTER_VERSION = "tmewai-py-1.0"
SCHEMA_PATH = Path(__file__).with_name("schema.sql")

# ArchiveInspectionLimits
MAX_ARCHIVE_BYTES = 600 * 1024 * 1024
MAX_ENTRIES = 250_000
MAX_ENTRY_BYTES = 512 * 1024 * 1024
MAX_TOTAL_EXPANDED_BYTES = 8 * 1024 * 1024 * 1024
MAX_PATH_LENGTH = 1024

# FacebookJsonImporter budgets
MAX_JSON_BYTES = 512 * 1024 * 1024
MAX_JSON_FILE_BYTES = 64 * 1024 * 1024
MAX_RECORDS = 1_000_000
MAX_ATTACHMENT_HASH_BYTES = 8 * 1024 * 1024
MAX_ATTACHMENT_HASH_TOTAL_BYTES = 64 * 1024 * 1024

MAX_REACTION_TOKEN_LENGTH = 64
MAX_POST_TEXT_LENGTH = 100_000
MAX_PERSON_NAME_LENGTH = 512
MAX_THREAD_PARTICIPANTS = 256
MAX_ATTACHMENTS_PER_RECORD = 100
MAX_ATTACHMENT_PATH_LENGTH = 1024

MIN_EPOCH_MILLIS = -62_135_596_800_000
MAX_EPOCH_MILLIS = 253_402_300_799_999
MIN_EPOCH_SECONDS = -62_135_596_800
MAX_EPOCH_SECONDS = 253_402_300_799

# Facebook launched in February 2004. A message `timestamp_ms` below this point
# cannot be a millisecond value from an export, so a seconds value written into
# the milliseconds field is recovered (and reported) instead of being stored as
# a 1970 date. Nothing else is guessed: a value in neither range stays invalid.
_FACEBOOK_FIRST_SECONDS = 1_073_000_000
_FACEBOOK_FIRST_MILLIS = 1_073_000_000_000

WARNING_TIMESTAMP_SECONDS_INFERRED = "timestamp_seconds_inferred"
WARNING_TIMESTAMP_MILLIS_INFERRED = "timestamp_millis_inferred"
WARNING_ATTACHMENT_PATH_REJECTED = "attachment_path_rejected"
WARNING_ATTACHMENT_MISSING = "attachment_missing"
WARNING_ATTACHMENT_HASH_LIMIT = "attachment_hash_size_limit"
WARNING_ATTACHMENT_COUNT_LIMIT = "attachment_count_limit"
WARNING_KNOWN_FOLDERS_NOT_IMPORTED = "known_folders_not_imported"

_COMMENT_FILE_PATTERN = re.compile(
    r"(^|/)comments?(_[0-9]+)?\.json$"
    r"|(^|/)your_comments(_[0-9]+)?\.json$"
    r"|(^|/)comments/[^/]+\.json$"
)
_REACTION_FILE_PATTERN = re.compile(
    r"(^|/)likes_and_reactions(_[0-9]+)?\.json$"
    r"|(^|/)likes_and_reactions/[^/]+\.json$"
    r"|(^|/)reactions(_[0-9]+)?\.json$"
)
_POST_FILE_PATTERN = re.compile(
    r"(^|/)your_posts(_[0-9]+)?\.json$"
    r"|(^|/)your_posts__[^/]+\.json$"
    r"|(^|/)posts/your_posts[^/]*\.json$"
)

_MESSAGE_MEDIA_FIELDS = {
    "photos": "image",
    "gifs": "image",
    "videos": "video",
    "audio_files": "audio",
    "files": "file",
}

_ATTACHMENT_KINDS = {
    "media", "image", "video", "audio", "gif", "sticker", "external_context",
    "place", "event", "note", "link", "album", "poll",
}

# Dart file kinds, with their safe reason codes.
KIND_MESSAGE = "message"
KIND_COMMENT = "comment"
KIND_REACTION = "reaction"
KIND_POST = "post"

_KIND_CODES = {
    KIND_MESSAGE: ("malformed_message_json", "unsupported_message_json",
                   "invalid_message_record", "invalid_message_timestamp"),
    KIND_COMMENT: ("malformed_comments_json", "unsupported_comments_json",
                   "invalid_comment_record", "invalid_comment_timestamp"),
    KIND_REACTION: ("malformed_reactions_json", "unsupported_reactions_json",
                    "invalid_reaction_record", "invalid_reaction_timestamp"),
    KIND_POST: ("malformed_posts_json", "unsupported_posts_json",
                "invalid_post_record", "invalid_post_timestamp"),
}

_LIST_KEYS = {
    KIND_MESSAGE: ("messages",),
    KIND_COMMENT: ("comments_v2", "comments"),
    KIND_REACTION: ("reactions_v2", "reactions"),
    KIND_POST: ("posts",),
}


class ArchiveError(Exception):
    """A safe, coded refusal; ``code`` never contains a host path or content."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class ArchiveEntry:
    relative_path: str
    size: int
    is_media: bool
    is_json: bool
    media_type: str | None
    crc: int
    json_sha256: str | None = None


@dataclass
class Scan:
    archive_path: str
    archive_bytes: int
    entries: list[ArchiveEntry]
    json_entries: dict[str, bytes]
    zip_infos: dict[str, zipfile.ZipInfo]
    total_expanded_bytes: int
    fingerprint: str
    warnings: list[str] = field(default_factory=list)


@dataclass
class Record:
    event_type: str
    source_path: str
    source_locator: str
    sender_name: str | None
    text: str | None
    timestamp_raw: str | None
    timestamp_utc: str | None
    timezone_status: str
    conversation_key: str | None = None
    conversation_title: str | None = None
    metadata: dict[str, str] | None = None


@dataclass
class FileOutcome:
    path: str
    status: str  # parsed | unsupported | skipped | failed
    error_code: str | None = None
    records_parsed: int = 0
    records_skipped: int = 0


@dataclass
class AttachmentRef:
    source_locator: str
    relative_path: str
    media_type: str
    size_bytes: int
    content_hash: str | None = None


@dataclass
class ParseResult:
    records: list[Record]
    outcomes: dict[str, FileOutcome]
    attachments: list[AttachmentRef]
    warnings: list[str]
    skipped_records: int
    supported_files: int
    unsupported_json_files: int
    known_not_imported_json_files: int


# ---------------------------------------------------------------------------
# Archive scan: validate every entry, never extract
# ---------------------------------------------------------------------------


def _path_rejection(path: str, max_path_length: int = MAX_PATH_LENGTH) -> str | None:
    """Reject an unsafe path after normalization, not before it.

    Validation runs on the exact form that will be stored, because trimming a
    segment (``".. "`` -> ``".."``) or a leading space can otherwise turn a
    path that passed the guard into a traversal or an absolute path.
    """
    if not path or len(path) > max_path_length:
        return "invalid_entry_path"
    if "\x00" in path:
        return "invalid_entry_path"
    normalized = _normalize_path(path)
    if normalized.startswith("/") or re.match(r"^[A-Za-z]:", normalized):
        return "absolute_entry_path"
    segments = normalized.split("/")
    if any(segment in ("..", ".") for segment in segments):
        return "path_traversal"
    if any(not segment and index != len(segments) - 1 for index, segment in enumerate(segments)):
        return "invalid_entry_path"
    return None


def _normalize_path(path: str) -> str:
    return "/".join(segment.strip() for segment in path.replace("\\", "/").split("/"))


def normalize_archive_relative_path(
    path: str, max_path_length: int = MAX_PATH_LENGTH
) -> str | None:
    """Normalize a path quoted by export content, or None if it escapes."""
    if _path_rejection(path, max_path_length) is not None:
        return None
    return _normalize_path(path)


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    return (info.external_attr >> 16) & 0o170000 == 0o120000


def scan_archive(path: str | Path) -> Scan:
    """Validate a ZIP and read its JSON entries in memory, without extracting."""
    archive_path = str(path)
    try:
        archive_bytes = Path(archive_path).stat().st_size
    except OSError as exc:
        raise ArchiveError("archive_unreadable", "The selected archive could not be opened.") from exc
    if archive_bytes <= 0:
        raise ArchiveError("archive_empty", "The selected file is empty.")
    if archive_bytes > MAX_ARCHIVE_BYTES:
        raise ArchiveError(
            "archive_size_limit",
            "The archive exceeds the configured compressed-size limit.",
        )

    warnings: list[str] = []
    entries: list[ArchiveEntry] = []
    json_entries: dict[str, bytes] = {}
    zip_infos: dict[str, zipfile.ZipInfo] = {}
    seen_names: set[str] = set()
    expanded = 0
    collected_json_bytes = 0
    json_count = 0
    media_count = 0
    ignored_count = 0

    try:
        with zipfile.ZipFile(archive_path) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_ENTRIES:
                raise ArchiveError(
                    "entry_count_limit",
                    "The archive contains more entries than the configured limit.",
                )
            for info in infos:
                if _is_symlink(info):
                    raise ArchiveError(
                        "symbolic_link_rejected",
                        "Archives containing symbolic links are not supported.",
                    )
                rejection = _path_rejection(info.filename)
                if rejection is not None:
                    raise ArchiveError(rejection, _rejection_message(rejection))
                if info.is_dir():
                    continue
                normalized = _normalize_path(info.filename)
                if normalized.lower() in seen_names:
                    raise ArchiveError(
                        "duplicate_entry",
                        "The archive contains duplicate or ambiguous file paths.",
                    )
                seen_names.add(normalized.lower())
                if info.flag_bits & 0x1:
                    raise ArchiveError(
                        "archive_encrypted", "The archive is password protected."
                    )
                size = info.file_size
                if size < 0 or size > MAX_ENTRY_BYTES:
                    raise ArchiveError(
                        "entry_size_limit",
                        "An archive entry exceeds the configured per-file size limit.",
                    )
                expanded += size
                if expanded > MAX_TOTAL_EXPANDED_BYTES:
                    raise ArchiveError(
                        "expanded_size_limit",
                        "The archive exceeds the configured total expanded-size limit.",
                    )

                lower = normalized.lower()
                is_json = lower.endswith(".json")
                is_media = coverage.is_media_path(lower)
                sniffed_json_sha: str | None = None
                if is_json:
                    json_count += 1
                    if size > MAX_JSON_FILE_BYTES:
                        raise ArchiveError(
                            "json_file_size_limit",
                            "A supported JSON file exceeds the configured per-file read limit.",
                        )
                    if size > MAX_JSON_BYTES or collected_json_bytes + size > MAX_JSON_BYTES:
                        raise ArchiveError(
                            "json_payload_size_limit",
                            "Supported JSON files exceed the configured import-read limit.",
                        )
                    try:
                        data = archive.read(info)
                    except (RuntimeError, zipfile.BadZipFile) as exc:
                        raise ArchiveError(
                            "archive_corrupt", "An archive entry could not be read."
                        ) from exc
                    if len(data) != size:
                        raise ArchiveError(
                            "archive_corrupt",
                            "An archive entry did not match its declared size.",
                        )
                    collected_json_bytes += len(data)
                    json_entries[normalized] = data
                    sniffed_json_sha = hashlib.sha256(data).hexdigest()
                elif is_media:
                    media_count += 1
                else:
                    ignored_count += 1

                zip_infos[normalized] = info
                entries.append(
                    ArchiveEntry(
                        relative_path=normalized,
                        size=size,
                        is_media=is_media,
                        is_json=is_json,
                        media_type=(
                            "json"
                            if is_json
                            else coverage.media_kind_for_path(normalized)
                            if is_media
                            else "file"
                        ),
                        crc=info.CRC,
                        json_sha256=sniffed_json_sha,
                    )
                )
    except zipfile.BadZipFile as exc:
        raise ArchiveError(
            "archive_invalid", "The selected file is not a supported, intact ZIP archive."
        ) from exc
    except OSError as exc:
        raise ArchiveError(
            "archive_unreadable", "The selected archive could not be opened."
        ) from exc

    if json_count == 0:
        warnings.append("no_json_files")
    if ignored_count > 0:
        warnings.append("unsupported_files_present")

    fingerprint_lines = sorted(
        f"{entry.relative_path}\t{entry.size}\t{entry.crc}\t{entry.json_sha256 or ''}"
        for entry in entries
    )
    fingerprint = hashlib.sha256("\n".join(fingerprint_lines).encode("utf-8")).hexdigest()

    return Scan(
        archive_path=archive_path,
        archive_bytes=archive_bytes,
        entries=entries,
        json_entries=json_entries,
        zip_infos=zip_infos,
        total_expanded_bytes=expanded,
        fingerprint=fingerprint,
        warnings=warnings,
    )


def _rejection_message(code: str) -> str:
    if code == "absolute_entry_path":
        return "The archive contains an absolute file path."
    if code == "path_traversal":
        return "The archive contains a path traversal entry."
    return "The archive contains an invalid entry path."


# ---------------------------------------------------------------------------
# Parsing the four Facebook JSON shapes
# ---------------------------------------------------------------------------


def _timestamp_millis(kind: str, raw_timestamp: Any) -> tuple[int | None, str | None]:
    """Normalize one export timestamp, reporting an inferred unit.

    Returns ``(epoch_millis, warning_code)``. A value in neither field's range -
    including a string, a float or a boolean - stays invalid; it is never
    guessed into a date.
    """
    if not isinstance(raw_timestamp, int) or isinstance(raw_timestamp, bool):
        return None, None
    if kind == KIND_MESSAGE:
        if _FACEBOOK_FIRST_MILLIS <= raw_timestamp <= MAX_EPOCH_MILLIS:
            return raw_timestamp, None
        if _FACEBOOK_FIRST_SECONDS <= raw_timestamp < _FACEBOOK_FIRST_MILLIS:
            return raw_timestamp * 1000, WARNING_TIMESTAMP_SECONDS_INFERRED
        return None, None
    if MIN_EPOCH_SECONDS <= raw_timestamp <= MAX_EPOCH_SECONDS:
        return raw_timestamp * 1000, None
    if MIN_EPOCH_MILLIS <= raw_timestamp <= MAX_EPOCH_MILLIS:
        return raw_timestamp, WARNING_TIMESTAMP_MILLIS_INFERRED
    return None, None


def _iso_utc(epoch_millis: int) -> str | None:
    try:
        return datetime.fromtimestamp(
            epoch_millis / 1000, tz=timezone.utc
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (OverflowError, OSError, ValueError):
        return None


def _name_key(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip()).lower()


def _thread_segment_matches_name(segment: str, name: str) -> bool:
    key = _name_key(name)
    if not key:
        return False
    slug = re.sub(r"\s+", " ", segment.replace("_", " ")).strip().lower()
    if slug == key:
        return True
    if not slug.startswith(key + " "):
        return False
    return bool(re.match(r"^[0-9]+$", slug[len(key) + 1:]))


class _PeopleIndex:
    """Names the archive states about itself; never invented."""

    def __init__(self) -> None:
        self._display_by_key: dict[str, str] = {}
        self.owner_name: str | None = None

    def add(self, name: Any) -> None:
        if not isinstance(name, str):
            return
        trimmed = name.strip()
        if not trimmed or len(trimmed) > MAX_PERSON_NAME_LENGTH:
            return
        key = _name_key(trimmed)
        if key:
            self._display_by_key.setdefault(key, trimmed)

    def display_name(self, name: str) -> str | None:
        return self._display_by_key.get(_name_key(name))

    def absorb(self, path: str, decoded: Any) -> None:
        lower = path.lower()
        if any(token in lower for token in ("friends", "followers", "following")):
            self._absorb_people_list(decoded)
        if "profile" in lower:
            if self.owner_name is None:
                self.owner_name = _profile_name_of(decoded)
            self.add(self.owner_name)
        if isinstance(decoded, dict):
            participants = decoded.get("participants")
            if isinstance(participants, list):
                for item in participants[:MAX_THREAD_PARTICIPANTS]:
                    if isinstance(item, dict):
                        self.add(item.get("name"))

    def _absorb_people_list(self, decoded: Any) -> None:
        items: list[Any] | None = None
        if isinstance(decoded, list):
            items = decoded
        elif isinstance(decoded, dict):
            for key in (
                "friends_v2", "friends", "followers_v2", "followers",
                "following_v2", "following",
            ):
                value = decoded.get(key)
                if isinstance(value, list):
                    items = value
                    break
        if items is None:
            return
        for item in items[:100_000]:
            self.add(item.get("name") if isinstance(item, dict) else item)


def _profile_name_of(decoded: Any) -> str | None:
    if not isinstance(decoded, dict):
        return None
    profile = decoded.get("profile_v2", decoded.get("profile"))
    if isinstance(profile, dict):
        name = profile.get("name")
        if isinstance(name, str):
            return name
        if isinstance(name, dict) and isinstance(name.get("full_name"), str):
            return name["full_name"]
    name = decoded.get("name")
    if isinstance(name, str):
        return name
    if isinstance(name, dict) and isinstance(name.get("full_name"), str):
        return name["full_name"]
    return None


@dataclass
class _ThreadPeople:
    names: list[str]
    title: str | None

    @staticmethod
    def from_decoded(decoded: Any) -> "_ThreadPeople | None":
        if not isinstance(decoded, dict):
            return None
        names: list[str] = []
        raw = decoded.get("participants")
        if isinstance(raw, list):
            for item in raw[:MAX_THREAD_PARTICIPANTS]:
                name = item.get("name") if isinstance(item, dict) else None
                if (
                    isinstance(name, str)
                    and name.strip()
                    and len(name) <= MAX_PERSON_NAME_LENGTH
                ):
                    names.append(name)
        title = decoded.get("title")
        if not names and not isinstance(title, str):
            return None
        return _ThreadPeople(
            names=names,
            title=title
            if isinstance(title, str) and len(title) <= MAX_PERSON_NAME_LENGTH
            else None,
        )


def _resolve_sender(
    raw_sender: Any,
    thread: _ThreadPeople | None,
    file_path: str,
    people: _PeopleIndex,
) -> str | None:
    if raw_sender is not None:
        return raw_sender if isinstance(raw_sender, str) else None
    if thread is None or not thread.names:
        return None
    if thread.title is not None:
        title_key = _name_key(thread.title)
        for name in thread.names:
            if _name_key(name) == title_key:
                return people.display_name(name) or name
    segments = file_path.split("/")
    folder = segments[-2] if len(segments) >= 2 else ""
    if folder:
        for name in thread.names:
            if _thread_segment_matches_name(folder, name):
                return people.display_name(name) or name
    if len(thread.names) == 1:
        only = thread.names[0]
        return people.display_name(only) or only
    return None


def kind_for_path(path: str) -> str:
    if _POST_FILE_PATTERN.search(path):
        return KIND_POST
    if _REACTION_FILE_PATTERN.search(path):
        return KIND_REACTION
    if _COMMENT_FILE_PATTERN.search(path):
        return KIND_COMMENT
    return KIND_MESSAGE


def _list_keys_for(kind: str) -> tuple[str, ...]:
    return _LIST_KEYS[kind]


def _entry_list_of(kind: str, decoded: Any) -> list[Any] | None:
    if kind != KIND_MESSAGE:
        if isinstance(decoded, list):
            return decoded
        if isinstance(decoded, dict):
            for key in _list_keys_for(kind):
                if isinstance(decoded.get(key), list):
                    return decoded[key]
        return None
    if isinstance(decoded, dict):
        for key in _list_keys_for(kind):
            if isinstance(decoded.get(key), list):
                return decoded[key]
    return None


def _list_key_in(kind: str, decoded: Any) -> str:
    if isinstance(decoded, dict):
        for key in _list_keys_for(kind):
            if isinstance(decoded.get(key), list):
                return key
    return _list_keys_for(kind)[0]


def _sniff_kind(decoded: Any) -> str | None:
    entries: list[Any] | None = None
    if isinstance(decoded, list):
        entries = decoded
    elif isinstance(decoded, dict):
        for kind in (KIND_MESSAGE, KIND_COMMENT, KIND_REACTION, KIND_POST):
            for key in _list_keys_for(kind):
                if isinstance(decoded.get(key), list):
                    entries = decoded[key]
                    break
            if entries is not None:
                break
    if not entries:
        return None
    first = entries[0]
    if not isinstance(first, dict):
        return None
    data = first.get("data")
    if isinstance(data, list) and data:
        item = data[0]
        if isinstance(item, dict):
            if "reaction" in item:
                return KIND_REACTION
            if "comment" in item:
                return KIND_COMMENT
            if "post" in item:
                return KIND_POST
            if "text" in item:
                return KIND_COMMENT
    if "sender_name" in first or "content" in first:
        return KIND_MESSAGE
    if "title" in first:
        return KIND_POST
    if "reaction" in first:
        return KIND_REACTION
    return None


def _record_count_of(kind: str, entries: list[Any]) -> int:
    if kind not in (KIND_REACTION, KIND_COMMENT):
        return len(entries)
    count = 0
    for entry in entries:
        if isinstance(entry, dict) and isinstance(entry.get("data"), list):
            count += len(entry["data"])
        else:
            count += 1
    return count


def _has_items(value: Any) -> bool:
    return isinstance(value, list) and bool(value)


def _typed_payload(entry: dict[str, Any], text: str | None) -> tuple[str, dict[str, str]] | None:
    """The brief's event type for a message whose own text is empty."""
    if text is not None and text.strip():
        return None
    if entry.get("unsent") is True or entry.get("is_unsent") is True:
        return "other", {"payload": "message_deleted"}
    if "call_duration" in entry:
        metadata = {"payload": "message_call"}
        seconds = entry.get("call_duration")
        if isinstance(seconds, int) and not isinstance(seconds, bool) and seconds > 0:
            metadata["call_duration"] = str(seconds)
        return "other", metadata
    if isinstance(entry.get("share"), dict):
        return "other", {"payload": "message_link"}
    if _has_items(entry.get("photos")):
        return "photo", {"payload": "message_photo"}
    if _has_items(entry.get("videos")):
        return "other", {"payload": "message_video"}
    if _has_items(entry.get("gifs")):
        return "other", {"payload": "message_gif"}
    if _has_items(entry.get("audio_files")):
        return "other", {"payload": "message_audio"}
    if _has_items(entry.get("files")):
        return "other", {"payload": "message_file"}
    if isinstance(entry.get("sticker"), dict):
        return "other", {"payload": "message_sticker"}
    if entry.get("poll") is not None:
        return "other", {"payload": "message_poll"}
    if entry.get("plan") is not None:
        return "other", {"payload": "message_plan"}
    return "other", {"payload": "message_empty"}


def _message_media_references(entry: dict[str, Any]) -> list[tuple[str, str]]:
    references: list[tuple[str, str]] = []
    for field_name, media_type in _MESSAGE_MEDIA_FIELDS.items():
        items = entry.get(field_name)
        if not isinstance(items, list):
            continue
        for item in items:
            uri = item.get("uri") if isinstance(item, dict) else None
            if isinstance(uri, str):
                references.append((uri, media_type))
    sticker = entry.get("sticker")
    if isinstance(sticker, dict) and isinstance(sticker.get("uri"), str):
        references.append((sticker["uri"], "image"))
    return references


def _post_text_of(data: Any) -> tuple[str | None, bool]:
    if data is None:
        return None, True
    if not isinstance(data, list):
        return None, False
    parts: list[str] = []
    for item in data:
        if not isinstance(item, dict):
            return None, False
        post = item.get("post")
        if post is None:
            continue
        if not isinstance(post, str):
            return None, False
        if post:
            parts.append(post)
    return ("\n".join(parts) if parts else None), True


def _attachments_of(attachments: Any) -> tuple[int, set[str], list[tuple[str, str]]]:
    if not isinstance(attachments, list):
        return 0, set(), []
    types: set[str] = set()
    references: list[tuple[str, str]] = []
    for attachment in attachments:
        if not isinstance(attachment, dict):
            continue
        data = attachment.get("data")
        if not isinstance(data, list):
            continue
        for item in data:
            if not isinstance(item, dict):
                continue
            for key in item:
                if key in _ATTACHMENT_KINDS:
                    types.add(key)
            media = item.get("media")
            uri = media.get("uri") if isinstance(media, dict) else None
            if isinstance(uri, str):
                references.append((uri, coverage.media_kind_for_path(uri)))
            for key in ("image", "video", "audio"):
                typed = item.get(key)
                typed_uri = typed.get("uri") if isinstance(typed, dict) else None
                if isinstance(typed_uri, str):
                    references.append((typed_uri, key))
    return len(attachments), types, references


def _collect_attachment(
    attachments: list[AttachmentRef],
    entries_by_path: dict[str, ArchiveEntry],
    warnings: set[str],
    source_locator: str,
    source_path: str,
    raw_path: str,
    media_type: str,
) -> None:
    normalized = normalize_archive_relative_path(
        raw_path, max_path_length=MAX_ATTACHMENT_PATH_LENGTH
    )
    if normalized is None:
        warnings.add(WARNING_ATTACHMENT_PATH_REJECTED)
        return
    entry = entries_by_path.get(normalized)
    if entry is None:
        segments = source_path.split("/")
        for depth in range(1, len(segments)):
            entry = entries_by_path.get("/".join(segments[:depth]) + "/" + normalized)
            if entry is not None:
                break
    if entry is None:
        warnings.add(WARNING_ATTACHMENT_MISSING)
        return
    attachments.append(
        AttachmentRef(
            source_locator=source_locator,
            relative_path=entry.relative_path,
            media_type=media_type,
            size_bytes=entry.size,
        )
    )


def _parse_comments(
    outcomes_records: list[Record],
    entries: list[Any],
    file_path: str,
    list_key: str,
    warnings: set[str],
) -> tuple[int, int]:
    parsed = skipped = 0
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            skipped += 1
            warnings.add(_KIND_CODES[KIND_COMMENT][2])
            continue
        data = entry.get("data")
        if isinstance(data, list):
            for data_index, item in enumerate(data):
                comment = item.get("comment") if isinstance(item, dict) else None
                if not isinstance(comment, dict):
                    skipped += 1
                    warnings.add(_KIND_CODES[KIND_COMMENT][2])
                    continue
                timestamp = comment.get("timestamp", entry.get("timestamp"))
                if comment.get("timestamp") is None:
                    timestamp = entry.get("timestamp")
                locator = f"{file_path}#/{list_key}/{index}/data/{data_index}/comment"
                if _append_comment(
                    outcomes_records, comment, timestamp, file_path, locator, warnings
                ):
                    parsed += 1
                else:
                    skipped += 1
        else:
            locator = f"{file_path}#/{list_key}/{index}"
            if _append_comment(
                outcomes_records,
                entry,
                entry.get("timestamp"),
                file_path,
                locator,
                warnings,
            ):
                parsed += 1
            else:
                skipped += 1
    return parsed, skipped


def _append_comment(
    records: list[Record],
    value: dict[str, Any],
    raw_timestamp: Any,
    file_path: str,
    source_locator: str,
    warnings: set[str],
) -> bool:
    sender = value.get("author")
    text = value.get("comment")
    if text is None:
        text = value.get("text")
    if (
        (sender is not None and not isinstance(sender, str))
        or (text is not None and not isinstance(text, str))
        or (isinstance(sender, str) and len(sender) > 4096)
        or (isinstance(text, str) and len(text) > 100_000)
    ):
        warnings.add(_KIND_CODES[KIND_COMMENT][2])
        return False
    epoch_millis, unit_warning = _timestamp_millis(KIND_COMMENT, raw_timestamp)
    if unit_warning is not None:
        warnings.add(unit_warning)
    if epoch_millis is None and raw_timestamp is not None:
        warnings.add(_KIND_CODES[KIND_COMMENT][3])
    timestamp_utc = _iso_utc(epoch_millis) if epoch_millis is not None else None
    records.append(
        Record(
            event_type="comment",
            source_path=file_path,
            source_locator=source_locator,
            sender_name=sender,
            text=text,
            timestamp_raw=str(raw_timestamp) if epoch_millis is not None else None,
            timestamp_utc=timestamp_utc,
            timezone_status=(
                "utc"
                if timestamp_utc is not None
                else "invalid"
                if raw_timestamp is not None
                else "unknown"
            ),
        )
    )
    return True


def _parse_reactions(
    records: list[Record],
    entries: list[Any],
    file_path: str,
    list_key: str,
    warnings: set[str],
) -> tuple[int, int]:
    parsed = skipped = 0
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict) or not isinstance(entry.get("data"), list):
            skipped += 1
            warnings.add(_KIND_CODES[KIND_REACTION][2])
            continue
        data = entry["data"]
        raw_timestamp = entry.get("timestamp")
        epoch_millis, unit_warning = _timestamp_millis(KIND_REACTION, raw_timestamp)
        if unit_warning is not None:
            warnings.add(unit_warning)
        if epoch_millis is None and raw_timestamp is not None:
            warnings.add(_KIND_CODES[KIND_REACTION][3])
        timestamp_utc = _iso_utc(epoch_millis) if epoch_millis is not None else None
        for data_index, item in enumerate(data):
            reaction = item.get("reaction") if isinstance(item, dict) else None
            token = reaction.get("reaction") if isinstance(reaction, dict) else None
            actor = reaction.get("actor") if isinstance(reaction, dict) else None
            if (
                not isinstance(token, str)
                or not token
                or len(token) > MAX_REACTION_TOKEN_LENGTH
                or (actor is not None and not isinstance(actor, str))
            ):
                skipped += 1
                warnings.add(_KIND_CODES[KIND_REACTION][2])
                continue
            records.append(
                Record(
                    event_type="reaction",
                    source_path=file_path,
                    source_locator=f"{file_path}#/{list_key}/{index}/data/{data_index}",
                    sender_name=actor,
                    text=None,
                    timestamp_raw=(
                        str(raw_timestamp) if epoch_millis is not None else None
                    ),
                    timestamp_utc=timestamp_utc,
                    timezone_status=(
                        "utc"
                        if timestamp_utc is not None
                        else "invalid"
                        if raw_timestamp is not None
                        else "unknown"
                    ),
                    metadata={"reaction": token},
                )
            )
            parsed += 1
    return parsed, skipped


def _parse_posts(
    records: list[Record],
    entries: list[Any],
    file_path: str,
    warnings: set[str],
    attachments: list[AttachmentRef],
    entries_by_path: dict[str, ArchiveEntry],
) -> tuple[int, int]:
    parsed = skipped = 0
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            skipped += 1
            warnings.add(_KIND_CODES[KIND_POST][2])
            continue
        text, text_valid = _post_text_of(entry.get("data"))
        attachment_count, attachment_types, references = _attachments_of(
            entry.get("attachments")
        )
        if (
            not text_valid
            or (entry.get("attachments") is not None and not isinstance(entry.get("attachments"), list))
            or (text is not None and len(text) > MAX_POST_TEXT_LENGTH)
            or (text is None and attachment_count == 0)
        ):
            skipped += 1
            warnings.add(_KIND_CODES[KIND_POST][2])
            continue

        raw_timestamp = entry.get("timestamp")
        epoch_millis, unit_warning = _timestamp_millis(KIND_POST, raw_timestamp)
        if unit_warning is not None:
            warnings.add(unit_warning)
        if epoch_millis is None and raw_timestamp is not None:
            warnings.add(_KIND_CODES[KIND_POST][3])

        source_locator = f"{file_path}#/posts/{index}"
        for count, (raw_path, media_type) in enumerate(references):
            if count >= MAX_ATTACHMENTS_PER_RECORD:
                warnings.add(WARNING_ATTACHMENT_COUNT_LIMIT)
                break
            _collect_attachment(
                attachments,
                entries_by_path,
                warnings,
                source_locator,
                file_path,
                raw_path,
                media_type,
            )

        metadata = None
        if attachment_count:
            metadata = {"attachments": str(attachment_count)}
            if attachment_types:
                metadata["attachment_types"] = ",".join(sorted(attachment_types))
        timestamp_utc = _iso_utc(epoch_millis) if epoch_millis is not None else None
        records.append(
            Record(
                event_type="post",
                source_path=file_path,
                source_locator=source_locator,
                sender_name=None,
                text=text,
                timestamp_raw=str(raw_timestamp) if epoch_millis is not None else None,
                timestamp_utc=timestamp_utc,
                timezone_status=(
                    "utc"
                    if timestamp_utc is not None
                    else "invalid"
                    if raw_timestamp is not None
                    else "unknown"
                ),
                metadata=metadata,
            )
        )
        parsed += 1
    return parsed, skipped


_UNDECODABLE = object()


def _decode_json(raw: bytes) -> Any:
    """Decode one JSON entry, tolerating a BOM and refusing hostile input.

    A UTF-8 BOM is legal input for many producers and is ignored. Deep nesting
    raises ``RecursionError`` and an oversized integer literal raises
    ``ValueError`` from the decoder; both are treated as malformed JSON for that
    one file instead of aborting the whole import.
    """
    try:
        return json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        return _UNDECODABLE


def parse_scan(scan: Scan) -> ParseResult:
    """Parse every JSON entry in ``scan`` into normalized records."""
    records: list[Record] = []
    outcomes: dict[str, FileOutcome] = {}
    attachments: list[AttachmentRef] = []
    warnings: set[str] = set(scan.warnings)
    entries_by_path = {entry.relative_path: entry for entry in scan.entries}
    people = _PeopleIndex()
    supported_files = 0
    unsupported_json_files = 0
    known_not_imported_json_files = 0
    skipped_records = 0

    for entry in scan.entries:
        if not entry.is_json:
            continue
        path = entry.relative_path
        raw = scan.json_entries[path]
        kind = kind_for_path(path)
        decoded = _decode_json(raw)
        if decoded is _UNDECODABLE:
            unsupported_json_files += 1
            warnings.add(_KIND_CODES[kind][0])
            outcomes[path] = FileOutcome(path, "unsupported", _KIND_CODES[kind][0])
            continue

        people.absorb(path, decoded)

        entries = _entry_list_of(kind, decoded)
        if entries is None:
            sniffed = _sniff_kind(decoded)
            if sniffed is not None:
                kind = sniffed
                entries = _entry_list_of(kind, decoded)
        if entries is None:
            known_root = coverage.category_for_path(path) is not None
            if known_root:
                known_not_imported_json_files += 1
            else:
                unsupported_json_files += 1
            warnings.add(_KIND_CODES[kind][1])
            outcomes[path] = FileOutcome(
                path,
                "unsupported",
                _KIND_CODES[kind][1],
            )
            continue

        if len(records) + _record_count_of(kind, entries) > MAX_RECORDS:
            raise ArchiveError(
                "record_count_limit",
                "The supported import exceeds its configured record limit.",
            )

        supported_files += 1
        title = decoded.get("title") if isinstance(decoded, dict) and isinstance(decoded.get("title"), str) else None
        parent_path = path.rsplit("/", 1)[0] if "/" in path else path
        thread = (
            _ThreadPeople.from_decoded(decoded)
            if kind == KIND_MESSAGE and isinstance(decoded, dict)
            else None
        )
        list_key = _list_key_in(kind, decoded)

        if kind == KIND_POST:
            parsed, skipped = _parse_posts(
                records, entries, path, warnings, attachments, entries_by_path
            )
        elif kind == KIND_COMMENT:
            parsed, skipped = _parse_comments(
                records, entries, path, list_key, warnings
            )
        elif kind == KIND_REACTION:
            parsed, skipped = _parse_reactions(
                records, entries, path, list_key, warnings
            )
        else:
            parsed = skipped = 0
            for index, value in enumerate(entries):
                if not isinstance(value, dict):
                    skipped += 1
                    warnings.add(_KIND_CODES[kind][2])
                    continue
                raw_timestamp = value.get("timestamp_ms")
                sender = value.get("sender_name")
                text = value.get("content")
                if (
                    (sender is not None and not isinstance(sender, str))
                    or (text is not None and not isinstance(text, str))
                    or (isinstance(sender, str) and len(sender) > 4096)
                    or (isinstance(text, str) and len(text) > 100_000)
                ):
                    skipped += 1
                    warnings.add(_KIND_CODES[kind][2])
                    continue
                epoch_millis, unit_warning = _timestamp_millis(kind, raw_timestamp)
                if unit_warning is not None:
                    warnings.add(unit_warning)
                timestamp_utc = _iso_utc(epoch_millis) if epoch_millis is not None else None
                if epoch_millis is None and raw_timestamp is not None:
                    warnings.add(_KIND_CODES[kind][3])

                source_locator = f"{path}#/{list_key}/{index}"
                sender_name = _resolve_sender(sender, thread, path, people)
                payload = _typed_payload(value, text)
                records.append(
                    Record(
                        event_type=payload[0] if payload else "message",
                        source_path=path,
                        source_locator=source_locator,
                        sender_name=sender_name,
                        text=text,
                        timestamp_raw=(
                            str(raw_timestamp) if epoch_millis is not None else None
                        ),
                        timestamp_utc=timestamp_utc,
                        timezone_status=(
                            "utc"
                            if timestamp_utc is not None
                            else "invalid"
                            if raw_timestamp is not None
                            else "unknown"
                        ),
                        conversation_key=parent_path,
                        conversation_title=title,
                        metadata=payload[1] if payload else None,
                    )
                )
                parsed += 1
                for count, (raw_path, media_type) in enumerate(
                    _message_media_references(value)
                ):
                    if count >= MAX_ATTACHMENTS_PER_RECORD:
                        warnings.add(WARNING_ATTACHMENT_COUNT_LIMIT)
                        break
                    _collect_attachment(
                        attachments,
                        entries_by_path,
                        warnings,
                        source_locator,
                        path,
                        raw_path,
                        media_type,
                    )

        skipped_records += skipped
        outcomes[path] = FileOutcome(
            path, "parsed", None, records_parsed=parsed, records_skipped=skipped
        )

    if not scan.json_entries or supported_files == 0:
        warnings.add("no_supported_files_found")
    if unsupported_json_files > 0:
        warnings.add("unsupported_json_files_present")
    if known_not_imported_json_files > 0:
        warnings.add(WARNING_KNOWN_FOLDERS_NOT_IMPORTED)

    _hash_attachments(scan, attachments, warnings)

    return ParseResult(
        records=records,
        outcomes=outcomes,
        attachments=attachments,
        warnings=sorted(warnings),
        skipped_records=skipped_records,
        supported_files=supported_files,
        unsupported_json_files=unsupported_json_files,
        known_not_imported_json_files=known_not_imported_json_files,
    )


def _hash_attachments(
    scan: Scan, attachments: list[AttachmentRef], warnings: set[str]
) -> None:
    """Hash small referenced entries in memory; never extract or copy bytes."""
    if not attachments:
        return
    wanted = {ref.relative_path for ref in attachments}
    hashed: dict[str, str | None] = {}
    used = 0
    try:
        with zipfile.ZipFile(scan.archive_path) as archive:
            for path in sorted(wanted):
                info = scan.zip_infos.get(path)
                if info is None:
                    continue
                if (
                    info.file_size > MAX_ATTACHMENT_HASH_BYTES
                    or used + info.file_size > MAX_ATTACHMENT_HASH_TOTAL_BYTES
                ):
                    hashed[path] = None
                    warnings.add(WARNING_ATTACHMENT_HASH_LIMIT)
                    continue
                try:
                    data = archive.read(info)
                except (RuntimeError, zipfile.BadZipFile, OSError):
                    hashed[path] = None
                    warnings.add(WARNING_ATTACHMENT_HASH_LIMIT)
                    continue
                used += len(data)
                hashed[path] = hashlib.sha256(data).hexdigest()
    except (OSError, zipfile.BadZipFile):
        warnings.add(WARNING_ATTACHMENT_HASH_LIMIT)
        return
    for ref in attachments:
        ref.content_hash = hashed.get(ref.relative_path)


# ---------------------------------------------------------------------------
# Schema and ingest
# ---------------------------------------------------------------------------


def render_schema() -> str:
    sql = SCHEMA_PATH.read_text(encoding="utf-8")
    pattern = re.compile(r"\{\{fold\((.*?)\)\}\}")
    return pattern.sub(lambda match: sql_fold_expression(match.group(1)), sql)


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Create tables/triggers, rebuilding the index if fold SQL drifted."""
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(render_schema())
    expected_insert = _trigger_sql("events_search_after_insert")
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'trigger' AND name = ?",
        ("events_search_after_insert",),
    ).fetchone()
    if row is not None and _normalize_sql(row[0]) != _normalize_sql(expected_insert):
        _rebuild_search_index(conn)


def _trigger_sql(name: str) -> str:
    match = re.search(
        rf"CREATE TRIGGER IF NOT EXISTS\s+{re.escape(name)}\b.*?END;",
        render_schema(),
        re.IGNORECASE | re.DOTALL,
    )
    return match.group(0) if match else ""


def _normalize_sql(sql: str) -> str:
    # SQLite stores a trigger with its own whitespace and without
    # "IF NOT EXISTS"; compare token streams, not formatting.
    without_guard = re.sub(r"\bif\s+not\s+exists\b", "", sql, flags=re.IGNORECASE)
    return re.sub(r"\s+", "", without_guard).lower().rstrip(";")


def _rebuild_search_index(conn: sqlite3.Connection) -> None:
    for name in (
        "events_search_after_insert",
        "events_search_after_update",
        "events_search_after_delete",
    ):
        conn.execute(f"DROP TRIGGER IF EXISTS {name}")
    conn.execute("DELETE FROM event_search")
    conn.executescript(render_schema())
    conn.execute(
        "INSERT INTO event_search (event_id, text_content, source_locator) "
        "SELECT e.id, "
        + sql_fold_expression("e.text_content")
        + ", "
        + sql_fold_expression("e.source_locator")
        + " FROM events AS e"
    )


def _table_totals(conn: sqlite3.Connection) -> dict[str, int]:
    totals = {}
    for table in (
        "imports", "source_files", "people", "conversations", "events",
        "ai_consents", "attachments",
    ):
        totals[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    return totals


def scan_report(scan: Scan, parsed: ParseResult) -> dict[str, Any]:
    report = coverage.build_report(
        (
            {
                "relative_path": entry.relative_path,
                "is_media": entry.is_media,
                "is_json": entry.is_json,
            }
            for entry in scan.entries
        ),
        {
            path: {
                "status": outcome.status,
                "error_code": outcome.error_code,
                "records_parsed": outcome.records_parsed,
                "records_skipped": outcome.records_skipped,
            }
            for path, outcome in parsed.outcomes.items()
        },
    )
    report.update(
        {
            "archive_name": Path(scan.archive_path).name,
            "archive_bytes": scan.archive_bytes,
            "entries": len(scan.entries),
            "json_files": sum(1 for entry in scan.entries if entry.is_json),
            "media_files": sum(1 for entry in scan.entries if entry.is_media),
            "total_expanded_bytes": scan.total_expanded_bytes,
            "supported_json_files": parsed.supported_files,
            "unsupported_json_files": parsed.unsupported_json_files,
            "known_not_imported_json_files": parsed.known_not_imported_json_files,
            "records": len(parsed.records),
            "records_skipped": parsed.skipped_records,
            "warnings": parsed.warnings,
            "fingerprint": scan.fingerprint,
        }
    )
    return report


def ingest(conn: sqlite3.Connection, path: str | Path, platform: str = "facebook") -> dict[str, Any]:
    """Scan, parse and import an archive atomically and idempotently."""
    started = time.perf_counter()
    scan = scan_archive(path)
    parsed = parse_scan(scan)
    report = scan_report(scan, parsed)
    ensure_schema(conn)

    existing = conn.execute(
        "SELECT id, completed_at FROM imports "
        "WHERE platform = ? AND source_fingerprint = ? AND status = 'completed' "
        "ORDER BY id DESC LIMIT 1",
        (platform, scan.fingerprint),
    ).fetchone()
    if existing is not None:
        return {
            "status": "reused",
            "reused_import_id": existing[0],
            "fingerprint": scan.fingerprint,
            "inserted": {
                "imports": 0, "source_files": 0, "people": 0,
                "conversations": 0, "events": 0, "attachments": 0,
            },
            "skipped": {"duplicate_events": 0, "reason": "archive_already_imported"},
            "files_by_status": _files_by_status(scan, parsed),
            "events_by_type": _events_by_type(parsed),
            "records_parsed": len(parsed.records),
            "records_skipped": parsed.skipped_records,
            "coverage": report,
            "totals": _table_totals(conn),
            "elapsed_seconds": round(time.perf_counter() - started, 3),
        }

    coverage_json = json.dumps(
        {"counts": report["counts"], "reasons": report["reasons"]},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    if len(coverage_json) > 4096:
        coverage_json = json.dumps(
            {"counts": report["counts"]}, ensure_ascii=False, separators=(",", ":")
        )

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    inserted = {
        "imports": 1, "source_files": 0, "people": 0, "conversations": 0,
        "events": 0, "attachments": 0,
    }
    previous_isolation = conn.isolation_level
    conn.isolation_level = None
    try:
        conn.execute("BEGIN IMMEDIATE")
        import_id = conn.execute(
            "INSERT INTO imports (platform, format, source_fingerprint, started_at, "
            "importer_version, status, coverage_json) "
            "VALUES (?, 'json', ?, ?, ?, 'running', ?)",
            (platform, scan.fingerprint, now, IMPORTER_VERSION, coverage_json),
        ).lastrowid

        source_file_ids: dict[str, int] = {}
        for entry in scan.entries:
            outcome = parsed.outcomes.get(entry.relative_path)
            status = outcome.status if outcome else "unsupported"
            error_code = outcome.error_code if outcome else None
            if outcome is None and entry.is_json:
                error_code = coverage.UNSUPPORTED_JSON_SHAPE
            elif outcome is None and entry.is_media:
                error_code = coverage.UNSUPPORTED_MEDIA
            elif outcome is None:
                error_code = coverage.UNKNOWN_EXTENSION
            source_file_ids[entry.relative_path] = conn.execute(
                "INSERT INTO source_files (import_id, relative_path, media_type, "
                "content_hash, parse_status, error_code) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    import_id,
                    entry.relative_path,
                    entry.media_type,
                    entry.json_sha256,
                    status,
                    error_code,
                ),
            ).lastrowid
            inserted["source_files"] += 1

        people_ids: dict[str, int] = {}
        for record in parsed.records:
            name = record.sender_name.strip() if record.sender_name else None
            if not name or not name.strip():
                continue
            name = name.strip()
            if name in people_ids:
                continue
            people_ids[name] = conn.execute(
                "INSERT INTO people (platform, platform_person_id, display_name) "
                "VALUES (?, NULL, ?)",
                (platform, name),
            ).lastrowid
            inserted["people"] += 1

        conversation_ids: dict[str, int] = {}
        for record in parsed.records:
            key = record.conversation_key
            if key is None or key in conversation_ids:
                continue
            conversation_ids[key] = conn.execute(
                "INSERT INTO conversations (import_id, source_conversation_id, title) "
                "VALUES (?, ?, ?)",
                (import_id, key, record.conversation_title),
            ).lastrowid
            inserted["conversations"] += 1

        event_ids: dict[str, int] = {}
        for record in parsed.records:
            event_ids[record.source_locator] = conn.execute(
                "INSERT INTO events (import_id, source_file_id, source_locator, "
                "platform, event_type, timestamp_raw, timestamp_normalized, "
                "timezone_status, actor_id, conversation_id, text_content, "
                "metadata_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    import_id,
                    source_file_ids[record.source_path],
                    record.source_locator,
                    platform,
                    record.event_type,
                    record.timestamp_raw,
                    record.timestamp_utc,
                    record.timezone_status,
                    people_ids.get(record.sender_name.strip())
                    if record.sender_name
                    else None,
                    conversation_ids.get(record.conversation_key)
                    if record.conversation_key is not None
                    else None,
                    record.text,
                    json.dumps(record.metadata, ensure_ascii=False, separators=(",", ":"))
                    if record.metadata
                    else None,
                ),
            ).lastrowid
            inserted["events"] += 1

        for attachment in parsed.attachments:
            event_id = event_ids.get(attachment.source_locator)
            if event_id is None:
                continue
            conn.execute(
                "INSERT INTO attachments (event_id, source_file_id, relative_path, "
                "media_type, size_bytes, content_hash) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    event_id,
                    source_file_ids[attachment.relative_path],
                    attachment.relative_path,
                    attachment.media_type,
                    attachment.size_bytes,
                    attachment.content_hash,
                ),
            )
            inserted["attachments"] += 1

        completed = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        conn.execute(
            "UPDATE imports SET completed_at = ?, status = 'completed' WHERE id = ?",
            (completed, import_id),
        )
        conn.execute("COMMIT")
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.isolation_level = previous_isolation

    return {
        "status": "imported",
        "import_id": import_id,
        "fingerprint": scan.fingerprint,
        "inserted": inserted,
        "skipped": {"duplicate_events": 0, "records_skipped": parsed.skipped_records},
        "files_by_status": _files_by_status(scan, parsed),
        "events_by_type": _events_by_type(parsed),
        "records_parsed": len(parsed.records),
        "records_skipped": parsed.skipped_records,
        "coverage": report,
        "totals": _table_totals(conn),
        "elapsed_seconds": round(time.perf_counter() - started, 3),
    }


def _files_by_status(scan: Scan, parsed: ParseResult) -> dict[str, int]:
    """Count every `source_files` status, including media and other files.

    Counting only parser outcomes used to hide every non-JSON entry, so the
    totals did not add up to the rows the same import inserted.
    """
    counts: dict[str, int] = {}
    for entry in scan.entries:
        outcome = parsed.outcomes.get(entry.relative_path)
        status = outcome.status if outcome is not None else "unsupported"
        counts[status] = counts.get(status, 0) + 1
    return dict(sorted(counts.items()))


def _events_by_type(parsed: ParseResult) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in parsed.records:
        counts[record.event_type] = counts.get(record.event_type, 0) + 1
    return dict(sorted(counts.items()))
