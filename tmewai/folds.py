"""Arabic search folding, ported from the Tmewai Dart app.

``search_text.dart`` defines one table of search-only folds. The same table
feeds (a) the Python query normalizer and (b) the SQL ``replace()`` chain used
by the ``event_search`` triggers, so the index and the query cannot drift.

Stored event text is never rewritten; folding applies to the search index copy
and to the query only.
"""

from __future__ import annotations

import re

# Ordered exactly as the Dart map declares them. Empty replacements remove the
# codepoint; non-empty replacements fold it to the stated character.
SEARCH_TEXT_FOLDS: tuple[tuple[int, str], ...] = (
    (0x064B, ""),  # fathatan
    (0x064C, ""),  # dammatan
    (0x064D, ""),  # kasratan
    (0x064E, ""),  # fatha
    (0x064F, ""),  # damma
    (0x0650, ""),  # kasra
    (0x0651, ""),  # shadda
    (0x0652, ""),  # sukun
    (0x0653, ""),  # maddah above
    (0x0654, ""),  # hamza above
    (0x0655, ""),  # hamza below
    (0x0656, ""),  # subscript alef
    (0x0657, ""),  # inverted damma
    (0x0658, ""),  # mark noon ghunna
    (0x0659, ""),  # zwarakay
    (0x065A, ""),  # v above
    (0x065B, ""),  # inverted small v
    (0x065C, ""),  # dot below
    (0x065D, ""),  # reversed damma
    (0x065E, ""),  # fatha with two dots
    (0x065F, ""),  # wavy hamza below
    (0x0670, ""),  # superscript alef
    (0x0623, "\u0627"),  # alef with hamza above -> alef
    (0x0625, "\u0627"),  # alef with hamza below -> alef
    (0x0622, "\u0627"),  # alef with madda -> alef
    (0x0671, "\u0627"),  # alef wasla -> alef
    (0x0649, "\u064A"),  # alef maqsura -> yaa
    (0x0629, "\u0647"),  # taa marbuta -> haa
    (0x0640, ""),  # tatweel
)

_FOLD_MAP = {codepoint: replacement for codepoint, replacement in SEARCH_TEXT_FOLDS}


def fold_text(value: str | None) -> str:
    """Apply the fold table to ``value``, leaving every other rune untouched."""
    if not value:
        return ""
    return "".join(_FOLD_MAP.get(ord(char), char) for char in value)


def sql_fold_expression(column: str) -> str:
    """SQL expression applying exactly ``SEARCH_TEXT_FOLDS`` to ``column``."""
    expression = f"COALESCE({column}, '')"
    for codepoint, replacement in SEARCH_TEXT_FOLDS:
        source = _sql_literal(chr(codepoint))
        target = _sql_literal(replacement)
        expression = f"replace({expression}, {source}, {target})"
    return expression


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)


def match_query(query: str) -> str:
    """Turn user text into a safe FTS5 MATCH expression.

    Arabic harakat and spelling variants are folded first. Each remaining
    token is quoted as an FTS5 string and ANDed, so punctuation or FTS5
    operators in the user's query can never become query syntax.
    """
    tokens = _TOKEN_RE.findall(fold_text(query))
    return " AND ".join('"' + token.replace('"', '""') + '"' for token in tokens)
