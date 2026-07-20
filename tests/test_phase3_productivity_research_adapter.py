"""Deterministic tests for the Phase 3 browser research adapter.

Covering ``core.productivity.adapters.research``.
"""

from __future__ import annotations

import ast
import json
import pathlib
import urllib.request
from typing import Any

import pytest

from core.productivity.action_inputs import BrowserResearchAdapterInput
from core.productivity.action_results import BrowserSearchResult
from core.productivity.adapters.research import (
    BrowserResearchAdapter,
    BrowserResearchAdapterResult,
    StrictRedirectHandler,
    _check_json_depth_and_bounds,
    production_research_runner,
)
from core.productivity.execution import AdapterResultStatus


def _valid_input(
    query: str = "python async",
    domains: tuple[str, ...] = ("example.com",),
    max_results: int = 10,
) -> BrowserResearchAdapterInput:
    inp = BrowserResearchAdapterInput(query, domains, max_results)
    inp.validate()
    return inp


# --------------------------------------------------------------------------
# Exact domain and subdomain acceptance & off-domain filtering
# --------------------------------------------------------------------------


def test_exact_domain_and_subdomain_accepted():
    def fake_runner(query, domains, max_results):
        return [
            {
                "title": "Exact Domain",
                "url": "https://example.com/page1",
                "snippet": "Snippet 1",
            },
            {
                "title": "Sub Domain",
                "url": "https://docs.example.com/page2",
                "snippet": "Snippet 2",
            },
            {
                "title": "Deep Sub Domain",
                "url": "https://api.v1.example.com/page3",
                "snippet": "Snippet 3",
            },
        ]

    adapter = BrowserResearchAdapter(fake_runner)
    inp = _valid_input(domains=("example.com",))
    res = adapter(inp)

    assert res.status is AdapterResultStatus.SUCCESS
    assert res.code == ""
    assert isinstance(res.result, BrowserSearchResult)
    assert len(res.result.items) == 3
    assert res.result.items[0].domain == "example.com"
    assert res.result.items[1].domain == "docs.example.com"
    assert res.result.items[2].domain == "api.v1.example.com"


def test_valid_off_domain_links_filtered_out():
    def fake_runner(query, domains, max_results):
        return [
            {
                "title": "Valid Item",
                "url": "https://example.com/valid",
                "snippet": "Valid snippet",
            },
            {
                "title": "Suffix Escape Item",
                "url": "https://notexample.com/escape",
                "snippet": "Escape snippet",
            },
            {
                "title": "Different TLD Item",
                "url": "https://example.org/escape",
                "snippet": "Escape snippet",
            },
        ]

    adapter = BrowserResearchAdapter(fake_runner)
    inp = _valid_input(domains=("example.com",))
    res = adapter(inp)

    assert res.status is AdapterResultStatus.SUCCESS
    assert isinstance(res.result, BrowserSearchResult)
    assert len(res.result.items) == 1
    assert res.result.items[0].url == "https://example.com/valid"


# --------------------------------------------------------------------------
# Malformed entry failure (fails whole adapter call)
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "malformed_entries",
    [
        [12345],  # non-dict
        [{"url": "https://example.com/page"}],  # missing title
        [{"title": "Title"}],  # missing url
        [{"title": 123, "url": "https://example.com/page"}],  # non-string title
        [{"title": "Title", "url": 456}],  # non-string url
        [{"title": "Title", "url": "https://example.com/", "snippet": 789}],  # non-string snippet
        [{"title": "Title\x01Control", "url": "https://example.com/"}],  # control char in title
    ],
)
def test_malformed_entries_fail_adapter_call(malformed_entries):
    adapter = BrowserResearchAdapter(lambda q, d, m: malformed_entries)
    inp = _valid_input()
    res = adapter(inp)

    assert res.status is AdapterResultStatus.FAILED
    assert res.code == "failed"
    assert res.result is None


# --------------------------------------------------------------------------
# Excessive raw entries bounding
# --------------------------------------------------------------------------


def test_excessive_raw_entries_fail_adapter_call():
    oversized = [
        {"title": f"Title {i}", "url": f"https://example.com/page{i}"}
        for i in range(150)
    ]
    adapter = BrowserResearchAdapter(lambda q, d, m: oversized)
    inp = _valid_input()
    res = adapter(inp)

    assert res.status is AdapterResultStatus.FAILED
    assert res.code == "failed"
    assert res.result is None


# --------------------------------------------------------------------------
# JSON depth validation
# --------------------------------------------------------------------------


def test_excessive_json_depth_check():
    # Build a dictionary nested 12 levels deep
    nested = "value"
    for _ in range(12):
        nested = {"nested": nested}

    assert not _check_json_depth_and_bounds(nested, max_depth=10)

    # Shallow dictionary is valid
    shallow = {"a": {"b": "c"}}
    assert _check_json_depth_and_bounds(shallow, max_depth=10)


# --------------------------------------------------------------------------
# Redirect protection
# --------------------------------------------------------------------------


class _FakeHTTPResponse:
    def __init__(self, url: str, status: int = 200, body: bytes = b"{}"):
        self._url = url
        self.status = status
        self.headers = {"Content-Type": "application/json"}
        self._body = body

    def geturl(self):
        return self._url

    def read(self, amt: int = -1):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass


def test_production_runner_redirect_escape_protection(monkeypatch):
    # Simulate urllib returning a redirected URL to an off-target domain
    def fake_open(req, timeout=10.0):
        return _FakeHTTPResponse("https://attacker.com/steal")

    handler = StrictRedirectHandler()
    req = urllib.request.Request("https://api.duckduckgo.com/?q=test")

    # Redirect to attacker.com returns None (blocked!)
    assert (
        handler.redirect_request(req, None, 302, "Found", {}, "https://attacker.com/")
        is None
    )
    # Redirect to non-HTTPS returns None (blocked!)
    assert (
        handler.redirect_request(req, None, 302, "Found", {}, "http://api.duckduckgo.com/")
        is None
    )
    # Redirect to different path returns None (blocked!)
    assert (
        handler.redirect_request(req, None, 302, "Found", {}, "https://api.duckduckgo.com/other")
        is None
    )

    # Test geturl check inside production_research_runner
    import urllib.request as urllib_req

    class FakeOpener:
        def open(self, req, timeout=10.0):
            return _FakeHTTPResponse("https://attacker.com/redirect")

    monkeypatch.setattr(urllib_req, "build_opener", lambda *args: FakeOpener())
    results = production_research_runner("query", (), 10)
    assert results == []


# --------------------------------------------------------------------------
# Rejection of IP literals, localhost, and unsafe URLs
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_url",
    [
        "http://example.com/page",
        "https://127.0.0.1/page",
        "https://[::1]/page",
        "https://169.254.1.1/page",
        "https://localhost/page",
        "https://myhost.local/page",
        "https://user:pass@example.com/page",
        "https://example.com:80/page",
        "https://example.com#fragment",
    ],
)
def test_rejects_unsafe_urls(bad_url):
    def fake_runner(query, domains, max_results):
        return [{"title": "Unsafe Item", "url": bad_url}]

    adapter = BrowserResearchAdapter(fake_runner)
    inp = _valid_input(domains=())
    res = adapter(inp)

    assert res.status is AdapterResultStatus.SUCCESS
    assert isinstance(res.result, BrowserSearchResult)
    assert len(res.result.items) == 0


# --------------------------------------------------------------------------
# Runner errors, exceptions, and invalid output
# --------------------------------------------------------------------------


def test_runner_exception_returns_failed():
    def raising_runner(query, domains, max_results):
        raise RuntimeError("RUNNER_SECRET_BOOM")

    adapter = BrowserResearchAdapter(raising_runner)
    inp = _valid_input()
    res = adapter(inp)

    assert res.status is AdapterResultStatus.FAILED
    assert res.code == "failed"
    assert res.result is None
    assert "RUNNER_SECRET_BOOM" not in str(res)


# --------------------------------------------------------------------------
# Invalid input & zero runner calls
# --------------------------------------------------------------------------


def test_invalid_input_zero_runner_calls():
    calls = []

    def spy_runner(query, domains, max_results):
        calls.append(query)
        return []

    adapter = BrowserResearchAdapter(spy_runner)

    for bad_input in (None, "string input", 123, object()):
        res = adapter(bad_input)
        assert res.status is AdapterResultStatus.FAILED
        assert res.code == "failed"
        assert len(calls) == 0


# --------------------------------------------------------------------------
# Content-free __repr__
# --------------------------------------------------------------------------


def test_repr_content_free():
    res = BrowserResearchAdapterResult(
        status=AdapterResultStatus.SUCCESS,
        code="",
        result=BrowserSearchResult("secret query", ()),
    )
    assert repr(res) == "BrowserResearchAdapterResult(...)"
    assert "secret query" not in repr(res)


# --------------------------------------------------------------------------
# Forbidden side effects check
# --------------------------------------------------------------------------


def test_no_forbidden_imports():
    path = (
        pathlib.Path(__file__).resolve().parents[1]
        / "core"
        / "productivity"
        / "adapters"
        / "research.py"
    )
    forbidden = {
        "subprocess",
        "socket",
        "threading",
        "os",
        "sqlite3",
        "asyncio",
        "logging",
        "smtplib",
        "webbrowser",
        "eventkit",
        "applescript",
        "reminders",
        "mcp",
    }
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] not in forbidden, (
                    f"research.py imports forbidden module {alias.name}"
                )
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                assert node.module.split(".")[0] not in forbidden, (
                    f"research.py imports forbidden module {node.module}"
                )
