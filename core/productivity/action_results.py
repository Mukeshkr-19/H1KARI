"""Immutable Phase 3 read-result contracts.

Provides side-effect-free, content-free repr, bounded result structures for
browser research and calendar read actions.
"""

from __future__ import annotations

import ipaddress
import re
import unicodedata
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timedelta

RESEARCH_RESULTS_MAX = 20
RESEARCH_TITLE_MAX = 500
RESEARCH_URL_MAX = 2048
RESEARCH_SNIPPET_MAX = 2000
RESEARCH_DOMAIN_MAX = 253
RESEARCH_QUERY_MAX = 2000

CALENDAR_READ_EVENTS_MAX = 100
CALENDAR_TITLE_MAX = 500
CALENDAR_LABEL_MAX = 200
CALENDAR_LOCATION_MAX = 500

_DOMAIN_LABEL_RE = re.compile(
    r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z0-9][a-z0-9-]{0,61}[a-z0-9]$"
)


class ActionResultContractError(ValueError):
    """Fixed action result contract failure without reflected content."""

    def __init__(self, message: object = "action result contract failed") -> None:
        super().__init__("action result contract failed")


def _valid_text(
    value: object, min_len: int, max_len: int, *, allow_newline_tab: bool = False
) -> bool:
    if not isinstance(value, str) or not (min_len <= len(value) <= max_len):
        return False
    if value.strip() == "":
        return False
    for char in value:
        if allow_newline_tab and char in "\n\t":
            continue
        if ord(char) < 32 or ord(char) == 127:
            return False
        if unicodedata.category(char) == "Cf":
            return False
    return True


def _parse_and_validate_https_url(url: object) -> str | None:
    """Validate a research URL and return its canonical hostname, or None.

    Rejects non-HTTPS, credentials, fragments, single-label hosts, localhost,
    .local, IP literals, non-canonical casing, ports other than omitted or 443,
    and trailing-dot hosts.
    """
    if not isinstance(url, str) or not (1 <= len(url) <= RESEARCH_URL_MAX):
        return None
    if not url.startswith("https://"):
        return None
    if "#" in url or "@" in url:
        return None
    if not _valid_text(url, 1, RESEARCH_URL_MAX, allow_newline_tab=False):
        return None

    try:
        split = urllib.parse.urlsplit(url)
    except Exception:
        return None

    if split.scheme != "https" or split.fragment or split.username or split.password:
        return None

    netloc = split.netloc
    if not netloc:
        return None

    # Parse port if explicitly present
    if ":" in netloc:
        host_part, _, port_part = netloc.rpartition(":")
        if not host_part or port_part != "443":
            return None
        host = host_part
    else:
        host = netloc

    # Host checks
    if not host or host.endswith(".") or host != host.lower():
        return None

    # Reject IP literals (v4 or v6)
    try:
        ipaddress.ip_address(host.strip("[]"))
        return None
    except ValueError:
        pass

    # Reject single label, localhost, and .local
    if "." not in host or host == "localhost" or host.endswith(".local") or host.endswith(".localhost"):
        return None

    # Enforce multi-label domain format
    if not _DOMAIN_LABEL_RE.fullmatch(host):
        return None

    return host


def _usable_tzinfo(dt: object) -> bool:
    if not isinstance(dt, datetime):
        return False
    try:
        offset = dt.utcoffset()
    except Exception:
        return False
    return offset is not None and isinstance(offset, timedelta)


@dataclass(frozen=True, repr=False)
class BrowserSearchResultItem:
    """Immutable single item from a browser research action."""

    title: str
    url: str
    domain: str
    snippet: str | None = None

    def __post_init__(self) -> None:
        if not _valid_text(self.title, 1, RESEARCH_TITLE_MAX, allow_newline_tab=False):
            raise ActionResultContractError()
        hostname = _parse_and_validate_https_url(self.url)
        if hostname is None:
            raise ActionResultContractError()
        if not _valid_text(self.domain, 1, RESEARCH_DOMAIN_MAX, allow_newline_tab=False):
            raise ActionResultContractError()
        if self.domain != hostname:
            raise ActionResultContractError()
        if self.snippet is not None and not _valid_text(
            self.snippet, 0, RESEARCH_SNIPPET_MAX, allow_newline_tab=True
        ):
            raise ActionResultContractError()

    def __repr__(self) -> str:
        return "BrowserSearchResultItem(...)"


@dataclass(frozen=True, repr=False)
class BrowserSearchResult:
    """Immutable container of browser research results."""

    query: str
    items: tuple[BrowserSearchResultItem, ...]

    def __post_init__(self) -> None:
        if not _valid_text(self.query, 1, RESEARCH_QUERY_MAX, allow_newline_tab=False):
            raise ActionResultContractError()
        if not isinstance(self.items, tuple):
            raise ActionResultContractError()
        if len(self.items) > RESEARCH_RESULTS_MAX:
            raise ActionResultContractError()
        for item in self.items:
            if not isinstance(item, BrowserSearchResultItem):
                raise ActionResultContractError()

    def __repr__(self) -> str:
        return f"BrowserSearchResult(items={len(self.items)})"


@dataclass(frozen=True, repr=False)
class CalendarEventItem:
    """Immutable single event item from a calendar read action."""

    title: str
    start: datetime
    end: datetime
    calendar_label: str
    location: str | None = None

    def __post_init__(self) -> None:
        if not _valid_text(self.title, 1, CALENDAR_TITLE_MAX, allow_newline_tab=False):
            raise ActionResultContractError()
        if not _valid_text(
            self.calendar_label, 1, CALENDAR_LABEL_MAX, allow_newline_tab=False
        ):
            raise ActionResultContractError()
        if self.location is not None and not _valid_text(
            self.location, 0, CALENDAR_LOCATION_MAX, allow_newline_tab=True
        ):
            raise ActionResultContractError()

        try:
            if not _usable_tzinfo(self.start) or not _usable_tzinfo(self.end):
                raise ActionResultContractError()
            if self.start >= self.end:
                raise ActionResultContractError()
        except ActionResultContractError:
            raise
        except Exception:
            raise ActionResultContractError() from None

    def __repr__(self) -> str:
        return "CalendarEventItem(...)"


@dataclass(frozen=True, repr=False)
class CalendarReadResult:
    """Immutable container of calendar read events."""

    events: tuple[CalendarEventItem, ...]
    calendar_label: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.events, tuple):
            raise ActionResultContractError()
        if len(self.events) > CALENDAR_READ_EVENTS_MAX:
            raise ActionResultContractError()
        for event in self.events:
            if not isinstance(event, CalendarEventItem):
                raise ActionResultContractError()
        if self.calendar_label is not None and not _valid_text(
            self.calendar_label, 1, CALENDAR_LABEL_MAX, allow_newline_tab=False
        ):
            raise ActionResultContractError()

    def __repr__(self) -> str:
        return f"CalendarReadResult(events={len(self.events)})"
