"""Safe extraction of standalone ID fields from Telegram text and captions."""

from __future__ import annotations

import re
from dataclasses import dataclass


# A field must start a line and contain exactly the field name ID. The value is
# kept as text so leading zeroes, letters, and hyphens are never lost.
_ID_HEADER = re.compile(
    r"^[ \t]*id[ \t]*(?::|-|=)[ \t]*(?P<value>.*?)[ \t]*$",
    flags=re.IGNORECASE | re.MULTILINE,
)


@dataclass(frozen=True)
class ParsedID:
    value: str | None
    header_count: int
    ambiguous: bool


def parse_id(text: str | None) -> ParsedID:
    """Parse a message and safely reject missing, empty, or ambiguous IDs."""
    if not text:
        return ParsedID(value=None, header_count=0, ambiguous=False)

    matches = list(_ID_HEADER.finditer(text))
    if len(matches) != 1:
        return ParsedID(
            value=None,
            header_count=len(matches),
            ambiguous=len(matches) > 1,
        )

    value = matches[0].group("value").strip()
    if not value:
        return ParsedID(value=None, header_count=1, ambiguous=False)
    return ParsedID(value=value, header_count=1, ambiguous=False)


def extract_id(text: str) -> str | None:
    """Return one valid standalone ID, or None when it must be ignored."""
    return parse_id(text).value