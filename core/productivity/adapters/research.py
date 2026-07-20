"""Bounded Phase 3 browser research adapter.

Executes approved browser research queries via an injected or production HTTPS
runner. Validates all result items into immutable ``BrowserSearchResult`` and
``BrowserSearchResultItem`` objects, enforcing exact domain and subdomain
matching, non-HTTPS rejection, URL safety, and privacy-safe error reporting.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from core.productivity.action_inputs import BrowserResearchAdapterInput
from core.productivity.action_results import (
    RESEARCH_RESULTS_MAX,
    ActionResultContractError,
    BrowserSearchResult,
    BrowserSearchResultItem,
    _parse_and_validate_https_url,
)
from core.productivity.execution import AdapterResult, AdapterResultStatus

RESEARCH_RUNNER_TIMEOUT_SECONDS = 10.0
_MAX_RESPONSE_BYTES = 1024 * 1024  # 1 MB
_MAX_JSON_DEPTH = 10
_MAX_RAW_ENTRIES = 100

_FAILED = AdapterResult(AdapterResultStatus.FAILED, code="failed")


@dataclass(frozen=True, repr=False)
class BrowserResearchAdapterResult(AdapterResult):
    """Bounded result container wrapping AdapterResult and BrowserSearchResult."""

    result: BrowserSearchResult | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.result is not None and not isinstance(self.result, BrowserSearchResult):
            raise ValueError("result must be a BrowserSearchResult")

    def __repr__(self) -> str:
        return "BrowserResearchAdapterResult(...)"


class ResearchRunner(Protocol):
    """Protocol for injected research runners."""

    def __call__(
        self,
        query: str,
        domains: tuple[str, ...],
        max_results: int,
    ) -> Any: ...


def _check_json_depth_and_bounds(
    obj: Any, depth: int = 0, max_depth: int = _MAX_JSON_DEPTH
) -> bool:
    if depth > max_depth:
        return False
    if isinstance(obj, dict):
        if len(obj) > _MAX_RAW_ENTRIES:
            return False
        for val in obj.values():
            if not _check_json_depth_and_bounds(val, depth + 1, max_depth):
                return False
    elif isinstance(obj, list):
        if len(obj) > _MAX_RAW_ENTRIES:
            return False
        for item in obj:
            if not _check_json_depth_and_bounds(item, depth + 1, max_depth):
                return False
    return True


class StrictRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Prevent redirects from escaping the fixed HTTPS research endpoint."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        try:
            split = urllib.parse.urlsplit(newurl)
            if (
                split.scheme == "https"
                and split.hostname == "api.duckduckgo.com"
                and split.path == "/"
                and (split.port is None or split.port == 443)
                and not split.username
                and not split.password
                and not split.fragment
            ):
                return super().redirect_request(req, fp, code, msg, headers, newurl)
        except Exception:
            pass
        return None


def production_research_runner(
    query: str,
    domains: tuple[str, ...],
    max_results: int,
) -> list[dict[str, Any]]:
    """Production research runner fetching instant answers over HTTPS without a shell.

    Performs a single HTTPS GET request with strict timeout, redirect target,
    response size, and JSON depth bounds. Performs no shell execution, browser
    automation, retry, or cookie persistence.
    """
    if not isinstance(query, str) or not query:
        return []
    bounded_max = min(max(1, max_results), RESEARCH_RESULTS_MAX)
    params = urllib.parse.urlencode({"q": query, "format": "json", "no_html": "1"})
    url = f"https://api.duckduckgo.com/?{params}"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "HIKARI/1.0 (Privacy-Preserving Research)"},
    )
    opener = urllib.request.build_opener(StrictRedirectHandler())
    try:
        with opener.open(req, timeout=RESEARCH_RUNNER_TIMEOUT_SECONDS) as response:
            final_url = response.geturl()
            split = urllib.parse.urlsplit(final_url)
            if (
                split.scheme != "https"
                or split.hostname != "api.duckduckgo.com"
                or split.path != "/"
                or (split.port is not None and split.port != 443)
                or split.username
                or split.password
                or split.fragment
            ):
                return []

            if response.status != 200:
                return []
            content_type = response.headers.get("Content-Type", "")
            if "json" not in content_type and "text" not in content_type:
                return []
            body = response.read(_MAX_RESPONSE_BYTES + 1)
            if len(body) > _MAX_RESPONSE_BYTES:
                return []
            data = json.loads(body.decode("utf-8"))
    except Exception:
        return []

    if not isinstance(data, dict):
        return []

    if not _check_json_depth_and_bounds(data):
        return []

    results: list[dict[str, Any]] = []
    abstract_url = data.get("AbstractURL")
    abstract_text = data.get("AbstractText")
    heading = data.get("Heading")
    if isinstance(abstract_url, str) and abstract_url.startswith("https://"):
        results.append({
            "title": heading if isinstance(heading, str) and heading else query,
            "url": abstract_url,
            "snippet": abstract_text if isinstance(abstract_text, str) else None,
        })

    topics = data.get("RelatedTopics")
    if isinstance(topics, list):
        for topic in topics:
            if len(results) >= bounded_max:
                break
            if isinstance(topic, dict):
                first_url = topic.get("FirstURL")
                text = topic.get("Text")
                if isinstance(first_url, str) and first_url.startswith("https://"):
                    results.append({
                        "title": text[:100] if isinstance(text, str) and text else query,
                        "url": first_url,
                        "snippet": text if isinstance(text, str) else None,
                    })

    return results[:bounded_max]


class BrowserResearchAdapter:
    """Bounded Phase 3 browser-research adapter."""

    def __init__(
        self,
        runner: Callable[[str, tuple[str, ...], int], Any] | None = None,
    ) -> None:
        self._runner = runner if runner is not None else production_research_runner

    @staticmethod
    def _is_domain_approved(hostname: str, approved_domains: tuple[str, ...]) -> bool:
        if not approved_domains:
            return True
        for approved in approved_domains:
            if hostname == approved or hostname.endswith("." + approved):
                return True
        return False

    def __call__(self, input_val: object) -> BrowserResearchAdapterResult:
        if not isinstance(input_val, BrowserResearchAdapterInput):
            return BrowserResearchAdapterResult(
                status=AdapterResultStatus.FAILED, code="failed"
            )

        try:
            input_val.validate()
        except Exception:
            return BrowserResearchAdapterResult(
                status=AdapterResultStatus.FAILED, code="failed"
            )

        bounded_max = min(input_val.max_results, RESEARCH_RESULTS_MAX)

        try:
            raw_results = self._runner(
                input_val.query,
                input_val.domains,
                bounded_max,
            )
        except Exception:
            return BrowserResearchAdapterResult(
                status=AdapterResultStatus.FAILED, code="failed"
            )

        if not isinstance(raw_results, (list, tuple)):
            return BrowserResearchAdapterResult(
                status=AdapterResultStatus.FAILED, code="failed"
            )

        if len(raw_results) > _MAX_RAW_ENTRIES:
            return BrowserResearchAdapterResult(
                status=AdapterResultStatus.FAILED, code="failed"
            )

        validated_items: list[BrowserSearchResultItem] = []
        for entry in raw_results:
            if not isinstance(entry, dict):
                return BrowserResearchAdapterResult(
                    status=AdapterResultStatus.FAILED, code="failed"
                )
            if "title" not in entry or "url" not in entry:
                return BrowserResearchAdapterResult(
                    status=AdapterResultStatus.FAILED, code="failed"
                )

            title = entry["title"]
            url = entry["url"]
            snippet = entry.get("snippet")

            if not isinstance(title, str) or not isinstance(url, str):
                return BrowserResearchAdapterResult(
                    status=AdapterResultStatus.FAILED, code="failed"
                )
            if snippet is not None and not isinstance(snippet, str):
                return BrowserResearchAdapterResult(
                    status=AdapterResultStatus.FAILED, code="failed"
                )

            hostname = _parse_and_validate_https_url(url)
            if hostname is None:
                # Unsafe URL -> filter out item
                continue

            if not self._is_domain_approved(hostname, input_val.domains):
                # Off-domain URL -> filter out item
                continue

            try:
                item = BrowserSearchResultItem(
                    title=title,
                    url=url,
                    domain=hostname,
                    snippet=snippet,
                )
                if len(validated_items) < bounded_max:
                    validated_items.append(item)
            except ActionResultContractError:
                return BrowserResearchAdapterResult(
                    status=AdapterResultStatus.FAILED, code="failed"
                )
            except Exception:
                return BrowserResearchAdapterResult(
                    status=AdapterResultStatus.FAILED, code="failed"
                )

        try:
            search_result = BrowserSearchResult(
                query=input_val.query,
                items=tuple(validated_items),
            )
        except ActionResultContractError:
            return BrowserResearchAdapterResult(
                status=AdapterResultStatus.FAILED, code="failed"
            )
        except Exception:
            return BrowserResearchAdapterResult(
                status=AdapterResultStatus.FAILED, code="failed"
            )

        return BrowserResearchAdapterResult(
            status=AdapterResultStatus.SUCCESS,
            code="",
            result=search_result,
        )
