"""Deterministic tests for pure Phase 3 read-result contracts.

Covering ``core.productivity.action_results``.
"""

from __future__ import annotations

import ast
import pathlib
from datetime import datetime, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo

import pytest

from core.productivity import action_results as ar
from core.productivity.action_results import (
    ActionResultContractError,
    BrowserSearchResult,
    BrowserSearchResultItem,
    CalendarEventItem,
    CalendarReadResult,
)

UTC = timezone.utc
NY = ZoneInfo("America/New_York")


def _item(i=1):
    return BrowserSearchResultItem(
        title=f"Result Title {i}",
        url=f"https://example.com/item-{i}",
        domain="example.com",
        snippet=f"Snippet for item {i}",
    )


def _event(i=1):
    start = datetime(2026, 7, 20, 10, 0, 0, 123456, tzinfo=UTC)
    end = start + timedelta(hours=1)
    return CalendarEventItem(
        title=f"Event {i}",
        start=start,
        end=end,
        calendar_label="Work",
        location=f"Room {i}",
    )


# --------------------------------------------------------------------------
# Fixed exception message
# --------------------------------------------------------------------------


def test_action_result_contract_error_fixed_message():
    err1 = ActionResultContractError()
    assert str(err1) == "action result contract failed"

    err2 = ActionResultContractError("CUSTOM_SECRET_MESSAGE")
    assert str(err2) == "action result contract failed"
    assert "CUSTOM_SECRET_MESSAGE" not in str(err2)


# --------------------------------------------------------------------------
# Valid construction & port 443
# --------------------------------------------------------------------------


def test_browser_search_result_valid():
    item1 = _item(1)
    item2 = _item(2)
    res = BrowserSearchResult("python tutorial", (item1, item2))
    assert res.query == "python tutorial"
    assert len(res.items) == 2
    assert res.items[0] is item1
    assert res.items[1] is item2


def test_browser_search_result_accepts_explicit_port_443():
    item = BrowserSearchResultItem(
        title="Title",
        url="https://example.com:443/search?q=test",
        domain="example.com",
    )
    assert item.url == "https://example.com:443/search?q=test"
    assert item.domain == "example.com"


def test_calendar_read_result_valid():
    ev1 = _event(1)
    ev2 = _event(2)
    res = CalendarReadResult((ev1, ev2), calendar_label="Work")
    assert len(res.events) == 2
    assert res.events[0].start.microsecond == 123456
    assert res.events[1].start.microsecond == 123456
    assert res.calendar_label == "Work"


# --------------------------------------------------------------------------
# Bounds (max and max + 1)
# --------------------------------------------------------------------------


def test_browser_search_result_max_bounds():
    valid_items = tuple(_item(i) for i in range(ar.RESEARCH_RESULTS_MAX))
    res = BrowserSearchResult("query", valid_items)
    assert len(res.items) == ar.RESEARCH_RESULTS_MAX

    too_many = tuple(_item(i) for i in range(ar.RESEARCH_RESULTS_MAX + 1))
    with pytest.raises(ActionResultContractError) as exc:
        BrowserSearchResult("query", too_many)
    assert str(exc.value) == "action result contract failed"


def test_calendar_read_result_max_bounds():
    valid_events = tuple(_event(i) for i in range(ar.CALENDAR_READ_EVENTS_MAX))
    res = CalendarReadResult(valid_events)
    assert len(res.events) == ar.CALENDAR_READ_EVENTS_MAX

    too_many = tuple(_event(i) for i in range(ar.CALENDAR_READ_EVENTS_MAX + 1))
    with pytest.raises(ActionResultContractError) as exc:
        CalendarReadResult(too_many)
    assert str(exc.value) == "action result contract failed"


# --------------------------------------------------------------------------
# Strict URL host, port, casing, and IP literal rejections
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_url",
    [
        "http://example.com",  # non-HTTPS
        "https://localhost/",  # localhost
        "https://myhost.local/",  # .local
        "https://127.0.0.1/",  # loopback IPv4
        "https://[::1]/",  # loopback IPv6
        "https://169.254.1.1/",  # link-local IPv4
        "https://10.0.0.1/",  # private range 10.x
        "https://172.16.0.1/",  # private range 172.16.x
        "https://192.168.1.1/",  # private range 192.168.x
        "https://example.com:bad/",  # malformed nonnumeric port
        "https://example.com:80/",  # non-443 port
        "https://example.com:8080/",  # non-443 port
        "https://example.com./",  # trailing-dot host
        "https://EXAMPLE.COM/",  # uppercase host
        "https://ExAmPlE.com/",  # mixed case host
        "https://user:pass@example.com",  # credentials
        "https://example.com#section",  # fragment
        "https://singlelabelhost",  # single label host
        "ftp://example.com",  # wrong scheme
    ],
)
def test_rejects_strict_malformed_urls(bad_url):
    with pytest.raises(ActionResultContractError) as exc:
        BrowserSearchResultItem("Title", bad_url, "example.com")
    assert str(exc.value) == "action result contract failed"


def test_rejects_mismatched_domain_label():
    # URL host is example.com, domain is other.com
    with pytest.raises(ActionResultContractError) as exc:
        BrowserSearchResultItem("Title", "https://example.com/item", "other.com")
    assert str(exc.value) == "action result contract failed"

    # Casing mismatch
    with pytest.raises(ActionResultContractError) as exc:
        BrowserSearchResultItem("Title", "https://example.com/item", "EXAMPLE.COM")
    assert str(exc.value) == "action result contract failed"


# --------------------------------------------------------------------------
# Whitespace-only field rejections
# --------------------------------------------------------------------------


@pytest.mark.parametrize("blank", ["", "   ", "\t\n  "])
def test_rejects_whitespace_only_fields(blank):
    # Query blank
    with pytest.raises(ActionResultContractError) as exc:
        BrowserSearchResult(blank, (_item(1),))
    assert str(exc.value) == "action result contract failed"

    # Title blank
    with pytest.raises(ActionResultContractError):
        BrowserSearchResultItem(blank, "https://example.com/", "example.com")

    # Domain blank
    with pytest.raises(ActionResultContractError):
        BrowserSearchResultItem("Title", "https://example.com/", blank)

    # Snippet blank
    with pytest.raises(ActionResultContractError):
        BrowserSearchResultItem(
            "Title", "https://example.com/", "example.com", snippet=blank
        )

    # Calendar title blank
    with pytest.raises(ActionResultContractError):
        CalendarEventItem(
            blank,
            datetime(2026, 7, 20, 10, 0, tzinfo=UTC),
            datetime(2026, 7, 20, 11, 0, tzinfo=UTC),
            "Work",
        )

    # Calendar label blank
    with pytest.raises(ActionResultContractError):
        CalendarEventItem(
            "Title",
            datetime(2026, 7, 20, 10, 0, tzinfo=UTC),
            datetime(2026, 7, 20, 11, 0, tzinfo=UTC),
            blank,
        )

    # Location blank
    with pytest.raises(ActionResultContractError):
        CalendarEventItem(
            "Title",
            datetime(2026, 7, 20, 10, 0, tzinfo=UTC),
            datetime(2026, 7, 20, 11, 0, tzinfo=UTC),
            "Work",
            location=blank,
        )

    # CalendarReadResult label blank
    with pytest.raises(ActionResultContractError):
        CalendarReadResult((_event(1),), calendar_label=blank)


# --------------------------------------------------------------------------
# Datetime tests for CalendarEventItem
# --------------------------------------------------------------------------


class _NaiveOffsetTZ(tzinfo):
    def utcoffset(self, dt):
        return None


class _RaisingTZ(tzinfo):
    def utcoffset(self, dt):
        raise RuntimeError("BOOM_TZ_SECRET")


def test_calendar_event_datetime_validation():
    start_utc = datetime(2026, 7, 20, 10, 0, tzinfo=UTC)
    end_utc = datetime(2026, 7, 20, 11, 0, tzinfo=UTC)

    # Naive datetime
    with pytest.raises(ActionResultContractError):
        CalendarEventItem("Title", datetime(2026, 7, 20, 10, 0), end_utc, "Work")

    # Naive tzinfo
    with pytest.raises(ActionResultContractError):
        CalendarEventItem(
            "Title", datetime(2026, 7, 20, 10, 0, tzinfo=_NaiveOffsetTZ()), end_utc, "Work"
        )

    # Raising tzinfo
    with pytest.raises(ActionResultContractError) as exc:
        CalendarEventItem(
            "Title", datetime(2026, 7, 20, 10, 0, tzinfo=_RaisingTZ()), end_utc, "Work"
        )
    assert "BOOM_TZ_SECRET" not in str(exc.value)

    # start >= end
    with pytest.raises(ActionResultContractError):
        CalendarEventItem("Title", end_utc, start_utc, "Work")

    # start == end
    with pytest.raises(ActionResultContractError):
        CalendarEventItem("Title", start_utc, start_utc, "Work")


# --------------------------------------------------------------------------
# Immutability & Repr
# --------------------------------------------------------------------------


def test_immutability():
    item = _item(1)
    with pytest.raises(AttributeError):
        item.title = "New Title"  # type: ignore[misc]

    res = BrowserSearchResult("query", (item,))
    with pytest.raises(AttributeError):
        res.query = "new query"  # type: ignore[misc]

    event = _event(1)
    with pytest.raises(AttributeError):
        event.title = "New Event"  # type: ignore[misc]

    cal_res = CalendarReadResult((event,))
    with pytest.raises(AttributeError):
        cal_res.events = ()  # type: ignore[misc]


def test_repr_content_free():
    item = BrowserSearchResultItem(
        "Secret Research Title 999",
        "https://example.com/secret",
        "example.com",
        "Secret Snippet 888",
    )
    assert repr(item) == "BrowserSearchResultItem(...)"
    assert "Secret Research Title 999" not in repr(item)
    assert "Secret Snippet 888" not in repr(item)

    res = BrowserSearchResult("secret query", (item,))
    assert repr(res) == "BrowserSearchResult(items=1)"
    assert "secret query" not in repr(res)

    event = CalendarEventItem(
        "Secret Calendar Event 777",
        datetime(2026, 7, 20, 10, 0, tzinfo=UTC),
        datetime(2026, 7, 20, 11, 0, tzinfo=UTC),
        "Secret Label",
        "Secret Location",
    )
    assert repr(event) == "CalendarEventItem(...)"
    assert "Secret Calendar Event 777" not in repr(event)

    cal_res = CalendarReadResult((event,), calendar_label="Secret Label")
    assert repr(cal_res) == "CalendarReadResult(events=1)"
    assert "Secret Label" not in repr(cal_res)


# --------------------------------------------------------------------------
# Forbidden side effects check
# --------------------------------------------------------------------------


def test_no_forbidden_imports():
    path = (
        pathlib.Path(__file__).resolve().parents[1]
        / "core"
        / "productivity"
        / "action_results.py"
    )
    forbidden = {
        "subprocess",
        "socket",
        "threading",
        "os",
        "sqlite3",
        "requests",
        "asyncio",
        "logging",
        "smtplib",
        "http",
        "webbrowser",
        "eventkit",
        "applescript",
        "reminders",
        "mcp",
        "network",
        "provider",
    }
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] not in forbidden, (
                    f"action_results.py imports forbidden module {alias.name}"
                )
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                assert node.module.split(".")[0] not in forbidden, (
                    f"action_results.py imports forbidden module {node.module}"
                )
