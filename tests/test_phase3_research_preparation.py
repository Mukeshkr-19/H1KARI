"""Deterministic tests for the Phase 3 browser-research preparation contracts.

These tests cover only ``core.productivity.research``. They perform no I/O,
network, DNS, subprocess, browser, provider, or execution activity, and assert
the absence of those imports.
"""

from __future__ import annotations

import pytest

from core.action_policy import Actor, ActorContext
from core.productivity import research as res
from core.productivity.research import (
    PreparedResearchInput,
    ResearchPreparation,
    ResearchPreparationError,
    ResearchPreparationRegistry,
    ResearchProposalFactory,
)


def _owner(actor_id="actor-1", session_id="session-1"):
    return ActorContext(
        actor_id=actor_id,
        actor=Actor.OWNER,
        session_id=session_id,
        source="text",
    )


def _fixed_clock(value):
    return lambda: value


def _id_factory(values):
    it = iter(values)
    return lambda: next(it)


# --------------------------------------------------------------------------
# Valid proposals
# --------------------------------------------------------------------------


def test_valid_research_proposal():
    factory = ResearchProposalFactory(
        clock=_fixed_clock(1000.0), proposal_id_factory=lambda: "p-res-1"
    )
    prep = factory.prepare(_owner(), "hikari architecture")
    assert isinstance(prep, ResearchPreparation)
    assert prep.proposal.action.value == "browser.research"
    assert prep.proposal.proposal_id == "p-res-1"
    assert prep.proposal.created_at == 1000.0
    assert prep.proposal.expires_at == 1000.0 + res.RESEARCH_PROPOSAL_TTL
    assert prep.input.query == "hikari architecture"
    assert prep.input.domains == ()
    assert prep.input.max_results == res.MAX_RESULTS_DEFAULT


def test_valid_research_proposal_with_domains():
    factory = ResearchProposalFactory(
        clock=_fixed_clock(1000.0), proposal_id_factory=lambda: "p-res-2"
    )
    prep = factory.prepare(
        _owner(),
        "hikari architecture",
        domains=["example.com", "docs.Example.COM"],
        max_results=15,
    )
    assert prep.input.domains == ("example.com", "docs.example.com")
    assert prep.input.max_results == 15
    kinds = {t.kind.value for t in prep.proposal.targets}
    assert kinds == {"web_domain"}
    values = {t.value for t in prep.proposal.targets}
    assert values == {"example.com", "docs.example.com"}
    keys = {f.key for f in prep.proposal.preview_fields}
    assert keys == {"query", "domains", "max_results"}


def test_valid_research_proposal_single_domain_string():
    factory = ResearchProposalFactory(
        clock=_fixed_clock(1000.0), proposal_id_factory=lambda: "p-res-3"
    )
    prep = factory.prepare(_owner(), "query", domains="Example.COM")
    assert prep.input.domains == ("example.com",)
    assert prep.proposal.targets == (
        res.ActionTarget(res.TargetKind.WEB_DOMAIN, "example.com"),
    )


# --------------------------------------------------------------------------
# Domain normalization
# --------------------------------------------------------------------------


def test_domain_lowercasing():
    factory = ResearchProposalFactory(
        clock=_fixed_clock(1000.0), proposal_id_factory=lambda: "p-dom-1"
    )
    prep = factory.prepare(_owner(), "q", domains=["EXAMPLE.COM", "Sub.DoMain.IO"])
    assert prep.input.domains == ("example.com", "sub.domain.io")


def test_domain_idna_encoding():
    factory = ResearchProposalFactory(
        clock=_fixed_clock(1000.0), proposal_id_factory=lambda: "p-dom-2"
    )
    prep = factory.prepare(_owner(), "q", domains=["münchen.de"])
    assert prep.input.domains == ("xn--mnchen-3ya.de",)
    assert prep.proposal.targets[0].value == "xn--mnchen-3ya.de"


def test_domain_idna_encoding_preserves_punycode_equivalence():
    factory = ResearchProposalFactory(
        clock=_fixed_clock(1000.0), proposal_id_factory=lambda: "p-dom-2b"
    )
    prep = factory.prepare(_owner(), "q", domains=["xn--mnchen-3ya.de"])
    assert prep.input.domains == ("xn--mnchen-3ya.de",)
    assert prep.proposal.targets[0].value == "xn--mnchen-3ya.de"


# --------------------------------------------------------------------------
# Duplicate rejection
# --------------------------------------------------------------------------


def test_rejects_duplicate_normalized_domains():
    factory = ResearchProposalFactory(
        clock=_fixed_clock(1000.0), proposal_id_factory=lambda: "p-dup-1"
    )
    with pytest.raises(ResearchPreparationError):
        factory.prepare(_owner(), "q", domains=["example.com", "Example.COM"])


def test_rejects_duplicate_after_idna_normalization():
    factory = ResearchProposalFactory(
        clock=_fixed_clock(1000.0), proposal_id_factory=lambda: "p-dup-2"
    )
    with pytest.raises(ResearchPreparationError):
        factory.prepare(_owner(), "q", domains=["münchen.de", "xn--mnchen-3ya.de"])


# --------------------------------------------------------------------------
# Malformed destinations
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "domain",
    [
        "192.168.1.1",
        "10.0.0.1",
        "127.0.0.1",
        "::1",
        "[::1]",
        "example.com:8080",
        "https://example.com",
        "http://example.com",
        "user:pass@example.com",
        "example.com/path",
        "*.example.com",
        "example.*.com",
        "-example.com",
        "example..com",
        "",
        "example com",
    ],
)
def test_rejects_malformed_domain(domain):
    factory = ResearchProposalFactory(
        clock=_fixed_clock(1000.0), proposal_id_factory=lambda: "p-bad-dom"
    )
    with pytest.raises(ResearchPreparationError):
        factory.prepare(_owner(), "q", domains=[domain])


# --------------------------------------------------------------------------
# Text bounds
# --------------------------------------------------------------------------


def test_rejects_empty_query():
    factory = ResearchProposalFactory(
        clock=_fixed_clock(1000.0), proposal_id_factory=lambda: "p-empty-q"
    )
    with pytest.raises(ResearchPreparationError):
        factory.prepare(_owner(), "")


def test_rejects_overlong_query():
    factory = ResearchProposalFactory(
        clock=_fixed_clock(1000.0), proposal_id_factory=lambda: "p-long-q"
    )
    with pytest.raises(ResearchPreparationError):
        factory.prepare(_owner(), "x" * (res.QUERY_MAX + 1))


def test_rejects_overlong_domain():
    factory = ResearchProposalFactory(
        clock=_fixed_clock(1000.0), proposal_id_factory=lambda: "p-long-dom"
    )
    with pytest.raises(ResearchPreparationError):
        factory.prepare(_owner(), "q", domains=["x" * 260])


def test_rejects_too_many_domains():
    factory = ResearchProposalFactory(
        clock=_fixed_clock(1000.0), proposal_id_factory=lambda: "p-many-dom"
    )
    with pytest.raises(ResearchPreparationError):
        factory.prepare(_owner(), "q", domains=[f"d{i}.example.com" for i in range(res.DOMAINS_MAX + 1)])


def test_accepts_max_results_boundary():
    factory = ResearchProposalFactory(
        clock=_fixed_clock(1000.0), proposal_id_factory=lambda: "p-max-1"
    )
    prep = factory.prepare(_owner(), "q", max_results=res.MAX_RESULTS_MAX)
    assert prep.input.max_results == res.MAX_RESULTS_MAX


def test_rejects_max_results_too_high():
    factory = ResearchProposalFactory(
        clock=_fixed_clock(1000.0), proposal_id_factory=lambda: "p-max-2"
    )
    with pytest.raises(ResearchPreparationError):
        factory.prepare(_owner(), "q", max_results=res.MAX_RESULTS_MAX + 1)


def test_rejects_zero_max_results():
    factory = ResearchProposalFactory(
        clock=_fixed_clock(1000.0), proposal_id_factory=lambda: "p-max-3"
    )
    with pytest.raises(ResearchPreparationError):
        factory.prepare(_owner(), "q", max_results=0)


def test_rejects_negative_max_results():
    factory = ResearchProposalFactory(
        clock=_fixed_clock(1000.0), proposal_id_factory=lambda: "p-max-4"
    )
    with pytest.raises(ResearchPreparationError):
        factory.prepare(_owner(), "q", max_results=-1)


def test_rejects_boolean_max_results():
    factory = ResearchProposalFactory(
        clock=_fixed_clock(1000.0), proposal_id_factory=lambda: "p-max-5"
    )
    with pytest.raises(ResearchPreparationError):
        factory.prepare(_owner(), "q", max_results=True)  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# Unicode Cf (format characters)
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "\u202eReversed",
        "Zero\u200bWidth",
        "Soft\u00adHyphen",
    ],
)
def test_rejects_unicode_cf_in_query(text):
    factory = ResearchProposalFactory(
        clock=_fixed_clock(1000.0), proposal_id_factory=lambda: "p-cf-q"
    )
    with pytest.raises(ResearchPreparationError):
        factory.prepare(_owner(), text)


@pytest.mark.parametrize(
    "domain",
    [
        "ex\u202eample.com",
        "zero\u200bwidth.com",
        "soft\u00adhyphen.com",
    ],
)
def test_rejects_unicode_cf_in_domain(domain):
    factory = ResearchProposalFactory(
        clock=_fixed_clock(1000.0), proposal_id_factory=lambda: "p-cf-dom"
    )
    with pytest.raises(ResearchPreparationError):
        factory.prepare(_owner(), "q", domains=[domain])


def test_accepts_normal_unicode_query():
    factory = ResearchProposalFactory(
        clock=_fixed_clock(1000.0), proposal_id_factory=lambda: "p-unicode-1"
    )
    prep = factory.prepare(_owner(), "Café Réunion — Équipe")
    assert prep.input.query == "Café Réunion — Équipe"


# --------------------------------------------------------------------------
# Invalid factory output
# --------------------------------------------------------------------------


def test_rejects_invalid_proposal_id():
    factory = ResearchProposalFactory(
        clock=_fixed_clock(1000.0), proposal_id_factory=lambda: "BAD ID!"
    )
    with pytest.raises(ResearchPreparationError):
        factory.prepare(_owner(), "q")


def test_rejects_non_string_proposal_id():
    factory = ResearchProposalFactory(
        clock=_fixed_clock(1000.0), proposal_id_factory=lambda: 12345
    )
    with pytest.raises(ResearchPreparationError):
        factory.prepare(_owner(), "q")


def test_rejects_nan_clock():
    factory = ResearchProposalFactory(
        clock=lambda: float("nan"), proposal_id_factory=lambda: "p-nan"
    )
    with pytest.raises(ResearchPreparationError):
        factory.prepare(_owner(), "q")


def test_rejects_boolean_clock():
    factory = ResearchProposalFactory(
        clock=lambda: True, proposal_id_factory=lambda: "p-bool"
    )
    with pytest.raises(ResearchPreparationError):
        factory.prepare(_owner(), "q")


def test_rejects_clock_exception():
    factory = ResearchProposalFactory(
        clock=lambda: (_ for _ in ()).throw(RuntimeError("boom")),
        proposal_id_factory=lambda: "p-exc",
    )
    with pytest.raises(ResearchPreparationError):
        factory.prepare(_owner(), "q")


def test_factory_rejects_bad_ttl():
    with pytest.raises(ValueError):
        ResearchProposalFactory(
            clock=_fixed_clock(1000.0),
            proposal_id_factory=lambda: "x",
            ttl_seconds=0,
        )
    with pytest.raises(ValueError):
        ResearchProposalFactory(
            clock=_fixed_clock(1000.0),
            proposal_id_factory=lambda: "x",
            ttl_seconds=901,
        )


def test_factory_rejects_non_callable():
    with pytest.raises(TypeError):
        ResearchProposalFactory(clock="nope", proposal_id_factory=lambda: "x")


# --------------------------------------------------------------------------
# TTL
# --------------------------------------------------------------------------


def test_proposal_ttl_at_most_fifteen_minutes():
    factory = ResearchProposalFactory(
        clock=_fixed_clock(5000.0), proposal_id_factory=lambda: "p-ttl-1"
    )
    prep = factory.prepare(_owner(), "q")
    assert prep.proposal.expires_at - prep.proposal.created_at == 900.0
    assert prep.proposal.expires_at <= prep.proposal.created_at + 900.0


def test_proposal_is_expired_after_ttl():
    factory = ResearchProposalFactory(
        clock=_fixed_clock(5000.0), proposal_id_factory=lambda: "p-ttl-2"
    )
    prep = factory.prepare(_owner(), "q")
    assert prep.proposal.is_expired(5000.0 + 900.0) is True
    assert prep.proposal.is_expired(5000.0 + 899.0) is False


# --------------------------------------------------------------------------
# Registry capacity
# --------------------------------------------------------------------------


def _sample_input() -> PreparedResearchInput:
    return PreparedResearchInput("sample query", (), 10)


def test_registry_put_and_get():
    reg = ResearchPreparationRegistry()
    actor = _owner()
    item = _sample_input()
    reg.put(actor, "p-reg-1", item)
    assert reg.get(actor, "p-reg-1") is item


def test_registry_capacity_limit():
    reg = ResearchPreparationRegistry(limit=2)
    actor = _owner()
    reg.put(actor, "p-1", _sample_input())
    reg.put(actor, "p-2", _sample_input())
    with pytest.raises(ResearchPreparationError):
        reg.put(actor, "p-3", _sample_input())


def test_registry_rejects_invalid_limit():
    with pytest.raises(ValueError):
        ResearchPreparationRegistry(limit=0)
    with pytest.raises(ValueError):
        ResearchPreparationRegistry(limit=65)
    with pytest.raises(ValueError):
        ResearchPreparationRegistry(limit=True)  # type: ignore[arg-type]


def test_registry_rejects_bad_actor_or_id():
    reg = ResearchPreparationRegistry()
    with pytest.raises(ResearchPreparationError):
        reg.put(_owner(actor_id="bad id"), "p-x", _sample_input())
    with pytest.raises(ResearchPreparationError):
        reg.get(_owner(), "BAD ID!")


# --------------------------------------------------------------------------
# Cross-session isolation
# --------------------------------------------------------------------------


def test_registry_cross_session_isolation():
    reg = ResearchPreparationRegistry()
    owner_a = _owner(session_id="session-a")
    owner_b = _owner(session_id="session-b")
    item = _sample_input()
    reg.put(owner_a, "p-shared", item)
    assert reg.get(owner_b, "p-shared") is None
    assert reg.get(owner_a, "p-shared") is item


def test_registry_cross_actor_isolation():
    reg = ResearchPreparationRegistry()
    a = _owner(actor_id="actor-a")
    b = _owner(actor_id="actor-b")
    item = _sample_input()
    reg.put(a, "p-x", item)
    assert reg.get(b, "p-x") is None


# --------------------------------------------------------------------------
# Cleanup
# --------------------------------------------------------------------------


def test_registry_remove():
    reg = ResearchPreparationRegistry()
    actor = _owner()
    reg.put(actor, "p-rm-1", _sample_input())
    reg.remove(actor, "p-rm-1")
    assert reg.get(actor, "p-rm-1") is None


def test_registry_clear_session():
    reg = ResearchPreparationRegistry()
    a1 = _owner(session_id="s1")
    a2 = _owner(session_id="s2")
    reg.put(a1, "p-1", _sample_input())
    reg.put(a1, "p-2", _sample_input())
    reg.put(a2, "p-3", _sample_input())
    reg.clear_session("actor-1", "s1")
    assert reg.get(a1, "p-1") is None
    assert reg.get(a1, "p-2") is None
    assert reg.get(a2, "p-3") is not None


# --------------------------------------------------------------------------
# Repr redaction
# --------------------------------------------------------------------------


def test_prepared_input_repr_excludes_content():
    inp = PreparedResearchInput("secret query 12345", ("secret.example.com",), 19)
    text = repr(inp)
    assert "secret query 12345" not in text
    assert "secret.example.com" not in text
    assert "19" not in text
    assert text == "PreparedResearchInput(...)"


def test_preparation_repr_excludes_content():
    factory = ResearchProposalFactory(
        clock=_fixed_clock(2000.0), proposal_id_factory=lambda: "p-rep-1"
    )
    prep = factory.prepare(
        _owner(),
        "Distinctive Research Query 98765",
        domains=["distinctive.example.com"],
        max_results=12,
    )
    text = repr(prep)
    for forbidden in (
        "Distinctive Research Query 98765",
        "distinctive.example.com",
        "12",
        "p-rep-1",
        "actor-1",
        "session-1",
    ):
        assert forbidden not in text


def test_proposal_inside_preparation_has_content_free_repr():
    factory = ResearchProposalFactory(
        clock=_fixed_clock(2000.0), proposal_id_factory=lambda: "p-rep-2"
    )
    prep = factory.prepare(
        _owner(),
        "Distinctive Research Query 98765",
        domains=["distinctive.example.com"],
        max_results=12,
    )
    text = repr(prep.proposal)
    forbidden = (
        "p-rep-2",
        "Distinctive Research Query 98765",
        "distinctive.example.com",
        "12",
        "actor-1",
        "session-1",
        "2000.0",
    )
    for value in forbidden:
        assert value not in text
    assert text == "ActionProposal(targets=1, preview_fields=3)"


def test_proposal_user_preview_excludes_actor_ids():
    factory = ResearchProposalFactory(
        clock=_fixed_clock(2000.0), proposal_id_factory=lambda: "p-prev-1"
    )
    prep = factory.prepare(_owner(), "query")
    preview = prep.proposal.user_preview()
    serialized = str(preview)
    assert "actor-1" not in serialized
    assert "session-1" not in serialized


# --------------------------------------------------------------------------
# Preview fields
# --------------------------------------------------------------------------


def test_preview_includes_query_domains_and_max_results():
    factory = ResearchProposalFactory(
        clock=_fixed_clock(1000.0), proposal_id_factory=lambda: "p-prev-2"
    )
    prep = factory.prepare(
        _owner(),
        "hikari architecture",
        domains=["example.com"],
        max_results=15,
    )
    fields = {f.key: f for f in prep.proposal.preview_fields}
    assert fields["query"].label == "Query"
    assert fields["query"].value == "hikari architecture"
    assert fields["domains"].label == "Allowed domains"
    assert fields["domains"].value == "example.com"
    assert fields["max_results"].label == "Maximum results"
    assert fields["max_results"].value == "15"


def test_no_domains_preview_when_allowlist_empty():
    factory = ResearchProposalFactory(
        clock=_fixed_clock(1000.0), proposal_id_factory=lambda: "p-prev-3"
    )
    prep = factory.prepare(_owner(), "q")
    keys = [f.key for f in prep.proposal.preview_fields]
    assert keys == ["query", "max_results"]
    assert prep.proposal.targets == ()


def test_query_preview_not_truncated():
    # Defect 1 regression: the accepted query must be previewed in full.
    factory = ResearchProposalFactory(
        clock=_fixed_clock(1000.0), proposal_id_factory=lambda: "p-prev-4"
    )
    long_query = "x" * res.QUERY_MAX  # exactly the accepted maximum
    prep = factory.prepare(_owner(), long_query)
    fields = {f.key: f for f in prep.proposal.preview_fields}
    assert fields["query"].truncated is False
    assert fields["query"].value == long_query
    assert len(fields["query"].value) == res.QUERY_MAX


# --------------------------------------------------------------------------
# Invalid registry item
# --------------------------------------------------------------------------


def test_registry_rejects_arbitrary_object():
    reg = ResearchPreparationRegistry()
    with pytest.raises(ResearchPreparationError):
        reg.put(_owner(), "p-bad-item", object())


def test_registry_rejects_non_prepared_types():
    reg = ResearchPreparationRegistry()
    for bad in (None, "string", 123, [], {}, _owner()):
        with pytest.raises(ResearchPreparationError):
            reg.put(_owner(), "p-bad-item", bad)


# --------------------------------------------------------------------------
# Infinite clocks and TTL inputs
# --------------------------------------------------------------------------


def test_rejects_infinite_clock():
    factory = ResearchProposalFactory(
        clock=lambda: float("inf"), proposal_id_factory=lambda: "p-inf-1"
    )
    with pytest.raises(ResearchPreparationError):
        factory.prepare(_owner(), "q")


def test_rejects_negative_infinite_clock():
    factory = ResearchProposalFactory(
        clock=lambda: float("-inf"), proposal_id_factory=lambda: "p-inf-2"
    )
    with pytest.raises(ResearchPreparationError):
        factory.prepare(_owner(), "q")


def test_factory_rejects_infinite_ttl():
    with pytest.raises(ValueError):
        ResearchProposalFactory(
            clock=_fixed_clock(1000.0),
            proposal_id_factory=lambda: "x",
            ttl_seconds=float("inf"),
        )
    with pytest.raises(ValueError):
        ResearchProposalFactory(
            clock=_fixed_clock(1000.0),
            proposal_id_factory=lambda: "x",
            ttl_seconds=float("nan"),
        )


# --------------------------------------------------------------------------
# Control characters and NUL
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "query",
    [
        "hello\x00world",
        "hello\x01world",
        "hello\x7fworld",
        "hello\nworld",
    ],
)
def test_rejects_control_chars_in_query(query):
    factory = ResearchProposalFactory(
        clock=_fixed_clock(1000.0), proposal_id_factory=lambda: "p-ctrl-q"
    )
    with pytest.raises(ResearchPreparationError):
        factory.prepare(_owner(), query)


@pytest.mark.parametrize(
    "domain",
    [
        "ex\x00ample.com",
        "ex\x01ample.com",
        "ex\x7fample.com",
    ],
)
def test_rejects_control_chars_in_domain(domain):
    factory = ResearchProposalFactory(
        clock=_fixed_clock(1000.0), proposal_id_factory=lambda: "p-ctrl-dom"
    )
    with pytest.raises(ResearchPreparationError):
        factory.prepare(_owner(), "q", domains=[domain])


# --------------------------------------------------------------------------
# Defect 1: Query preview shows the exact accepted query (no truncation)
# --------------------------------------------------------------------------


def test_query_preview_shows_full_accepted_query_at_max():
    factory = ResearchProposalFactory(
        clock=_fixed_clock(1000.0), proposal_id_factory=lambda: "p-d1-1"
    )
    long_query = "x" * res.QUERY_MAX
    prep = factory.prepare(_owner(), long_query)
    fields = {f.key: f for f in prep.proposal.preview_fields}
    assert fields["query"].truncated is False
    assert fields["query"].value == long_query
    assert len(fields["query"].value) == res.QUERY_MAX


# --------------------------------------------------------------------------
# Defect 2: Consistent domain count and aggregate-preview bounds
# --------------------------------------------------------------------------


def test_accepts_max_domain_count_boundary():
    factory = ResearchProposalFactory(
        clock=_fixed_clock(1000.0), proposal_id_factory=lambda: "p-d2-1"
    )
    domains = [f"d{i}.example.com" for i in range(res.DOMAINS_MAX)]
    prep = factory.prepare(_owner(), "q", domains=domains)
    assert len(prep.input.domains) == res.DOMAINS_MAX


def test_rejects_domain_count_over_limit_boundary():
    factory = ResearchProposalFactory(
        clock=_fixed_clock(1000.0), proposal_id_factory=lambda: "p-d2-2"
    )
    with pytest.raises(ResearchPreparationError):
        factory.prepare(
            _owner(),
            "q",
            domains=[f"d{i}.example.com" for i in range(res.DOMAINS_MAX + 1)],
        )


def _max_length_domain(idx: int) -> str:
    """Construct a unique valid domain of exactly 253 characters."""
    first_char = chr(ord("a") + idx)  # a-p for idx 0-15
    return ".".join(
        [
            first_char + "b" * 61 + "c",  # 63 chars
            "d" + "e" * 61 + "f",          # 63 chars
            "g" + "h" * 61 + "i",          # 63 chars
            "j" + "k" * 59 + "l",          # 61 chars
        ]
    )


def test_accepts_max_length_domains_at_aggregate_preview_capacity():
    factory = ResearchProposalFactory(
        clock=_fixed_clock(1000.0), proposal_id_factory=lambda: "p-d2-3"
    )
    domains = [_max_length_domain(i) for i in range(res.DOMAINS_MAX)]
    assert all(len(d) == res.DOMAIN_MAX for d in domains)
    prep = factory.prepare(_owner(), "q", domains=domains)
    fields = {f.key: f for f in prep.proposal.preview_fields}
    assert fields["domains"].truncated is False
    # 16 * 253 + 15 * 2 = 4078 == DOMAINS_PREVIEW_MAX
    assert len(fields["domains"].value) == res.DOMAINS_PREVIEW_MAX


def test_aggregate_preview_overflow_rejected_without_valueerror_leak():
    factory = ResearchProposalFactory(
        clock=_fixed_clock(1000.0), proposal_id_factory=lambda: "p-d2-4"
    )
    original = res.DOMAINS_PREVIEW_MAX
    res.DOMAINS_PREVIEW_MAX = 5
    try:
        with pytest.raises(ResearchPreparationError) as exc:
            factory.prepare(_owner(), "q", domains=["example.com"])
        msg = str(exc.value)
        assert "ValueError" not in msg
        assert "PreviewField" not in msg
        assert msg == "invalid research input"
        assert exc.value.__cause__ is None
    finally:
        res.DOMAINS_PREVIEW_MAX = original


# --------------------------------------------------------------------------
# Defect 3: OverflowError normalization in _now_and_id
# --------------------------------------------------------------------------


def test_rejects_enormous_clock_without_overflow_leak():
    factory = ResearchProposalFactory(
        clock=lambda: 10**10000, proposal_id_factory=lambda: "p-d3-1"
    )
    with pytest.raises(ResearchPreparationError) as exc:
        factory.prepare(_owner(), "q")
    msg = str(exc.value)
    assert "OverflowError" not in msg
    assert "too large" not in msg
    assert msg == "research preparation failed"
    assert exc.value.__cause__ is None


# --------------------------------------------------------------------------
# Defect 4: IDNA-2003 scope-change rejection
# --------------------------------------------------------------------------


def test_rejects_idna_spelling_change_fass_de():
    # faß.de → fass.de under IDNA-2003: registrable spelling changes.
    factory = ResearchProposalFactory(
        clock=_fixed_clock(1000.0), proposal_id_factory=lambda: "p-d4-1"
    )
    with pytest.raises(ResearchPreparationError):
        factory.prepare(_owner(), "q", domains=["faß.de"])


def test_rejects_idna_separator_change_ideographic_fullstop():
    # example。com (U+3002) → example.com: separator semantics change.
    factory = ResearchProposalFactory(
        clock=_fixed_clock(1000.0), proposal_id_factory=lambda: "p-d4-2"
    )
    with pytest.raises(ResearchPreparationError):
        factory.prepare(_owner(), "q", domains=["example。com"])


def test_rejects_idna_separator_change_fullwidth_fullstop():
    # example．com (U+FF0E) → example.com: separator semantics change.
    factory = ResearchProposalFactory(
        clock=_fixed_clock(1000.0), proposal_id_factory=lambda: "p-d4-3"
    )
    with pytest.raises(ResearchPreparationError):
        factory.prepare(_owner(), "q", domains=["example．com"])


def test_rejects_idna_separator_change_halfwidth_fullstop():
    # example｡com (U+FF61) → example.com: separator semantics change.
    factory = ResearchProposalFactory(
        clock=_fixed_clock(1000.0), proposal_id_factory=lambda: "p-d4-4"
    )
    with pytest.raises(ResearchPreparationError):
        factory.prepare(_owner(), "q", domains=["example｡com"])


def test_accepts_idna_roundtrip_stable_nonascii_domain():
    # münchen.de round-trips exactly: accepted and stored as punycode.
    factory = ResearchProposalFactory(
        clock=_fixed_clock(1000.0), proposal_id_factory=lambda: "p-d4-5"
    )
    prep = factory.prepare(_owner(), "q", domains=["münchen.de"])
    assert prep.input.domains == ("xn--mnchen-3ya.de",)


# --------------------------------------------------------------------------
# Defect 5: PreparedResearchInput canonicalizes direct domains
# --------------------------------------------------------------------------


def test_prepared_input_canonicalizes_mixed_case_direct_domain():
    inp = PreparedResearchInput("q", ("Example.COM",), 10)
    assert inp.domains == ("example.com",)


def test_prepared_input_canonicalizes_nonascii_direct_domain():
    inp = PreparedResearchInput("q", ("münchen.de",), 10)
    assert inp.domains == ("xn--mnchen-3ya.de",)


def test_prepared_input_does_not_retain_noncanonical_domain():
    inp = PreparedResearchInput("q", ("Example.COM",), 10)
    assert inp.domains != ("Example.COM",)
    assert inp.domains == ("example.com",)


# --------------------------------------------------------------------------
# Defect 6: Whitespace-only query rejection
# --------------------------------------------------------------------------


def test_rejects_whitespace_only_query():
    factory = ResearchProposalFactory(
        clock=_fixed_clock(1000.0), proposal_id_factory=lambda: "p-d6-1"
    )
    with pytest.raises(ResearchPreparationError):
        factory.prepare(_owner(), "   ")


def test_rejects_tab_only_query():
    factory = ResearchProposalFactory(
        clock=_fixed_clock(1000.0), proposal_id_factory=lambda: "p-d6-2"
    )
    with pytest.raises(ResearchPreparationError):
        factory.prepare(_owner(), "\t")


def test_accepts_query_with_surrounding_whitespace_unchanged():
    factory = ResearchProposalFactory(
        clock=_fixed_clock(1000.0), proposal_id_factory=lambda: "p-d6-3"
    )
    prep = factory.prepare(_owner(), "  hikari architecture  ")
    assert prep.input.query == "  hikari architecture  "


# --------------------------------------------------------------------------
# Defect 7: Numeric IP variant and single-label rejection
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "domain",
    [
        # Single-label numeric forms (rejected by the dot check).
        "0x7f000001",
        "2130706433",
        # Multi-label forms with single-char labels (rejected by the
        # domain regex, but still numeric IP variants that must be rejected).
        "0x7f.0.0.1",
        "0177.0.0.1",
        "0x7f.0.0",
        "1.2.3",
        # Multi-label forms where every label is 2+ chars and numeric —
        # these pass _DOMAIN_RE and are caught by the numeric-label check.
        "127.01",
        "12.34",
        "0x7f.0x00",
        "0177.0001",
        "00.00",
    ],
)
def test_rejects_numeric_ip_variants(domain):
    factory = ResearchProposalFactory(
        clock=_fixed_clock(1000.0), proposal_id_factory=lambda: "p-d7-1"
    )
    with pytest.raises(ResearchPreparationError):
        factory.prepare(_owner(), "q", domains=[domain])


def test_rejects_single_label_localhost():
    factory = ResearchProposalFactory(
        clock=_fixed_clock(1000.0), proposal_id_factory=lambda: "p-d7-2"
    )
    with pytest.raises(ResearchPreparationError):
        factory.prepare(_owner(), "q", domains=["localhost"])


def test_rejects_single_label_hostname():
    factory = ResearchProposalFactory(
        clock=_fixed_clock(1000.0), proposal_id_factory=lambda: "p-d7-3"
    )
    with pytest.raises(ResearchPreparationError):
        factory.prepare(_owner(), "q", domains=["myhost"])


# --------------------------------------------------------------------------
# Exception redaction
# --------------------------------------------------------------------------


def test_clock_overflow_exception_redacted():
    factory = ResearchProposalFactory(
        clock=lambda: 10**10000, proposal_id_factory=lambda: "p-exc-1"
    )
    with pytest.raises(ResearchPreparationError) as exc:
        factory.prepare(_owner(), "q")
    msg = str(exc.value)
    assert "OverflowError" not in msg
    assert "too large" not in msg
    assert "math" not in msg.lower()
    assert msg == "research preparation failed"
    assert exc.value.__cause__ is None


def test_aggregate_preview_exception_redacted():
    factory = ResearchProposalFactory(
        clock=_fixed_clock(1000.0), proposal_id_factory=lambda: "p-exc-2"
    )
    original = res.DOMAINS_PREVIEW_MAX
    res.DOMAINS_PREVIEW_MAX = 1
    try:
        with pytest.raises(ResearchPreparationError) as exc:
            factory.prepare(_owner(), "q", domains=["example.com"])
        msg = str(exc.value)
        assert "ValueError" not in msg
        assert "PreviewField" not in msg
        assert "preview" not in msg.lower()
        assert msg == "invalid research input"
        assert exc.value.__cause__ is None
    finally:
        res.DOMAINS_PREVIEW_MAX = original


class _IntWithValueErrorFloat(int):
    """int subclass whose __float__ raises ValueError with attacker content."""

    def __float__(self):
        raise ValueError("SECRET_TTL")


class _IntWithPreparationErrorFloat(int):
    """int subclass whose __float__ raises ResearchPreparationError with attacker content."""

    def __float__(self):
        raise ResearchPreparationError("SECRET_CLOCK")


def test_ttl_hostile_float_valueerror_redacted():
    for _ in range(2):
        with pytest.raises(ValueError) as exc:
            ResearchProposalFactory(
                clock=_fixed_clock(1000.0),
                proposal_id_factory=lambda: "p-red-1",
                ttl_seconds=_IntWithValueErrorFloat(300),
            )
        msg = str(exc.value)
        assert msg == "invalid proposal lifetime"
        assert "SECRET_TTL" not in msg
        assert exc.value.__cause__ is None


def test_ttl_hostile_float_preparation_error_redacted():
    with pytest.raises(ValueError) as exc:
        ResearchProposalFactory(
            clock=_fixed_clock(1000.0),
            proposal_id_factory=lambda: "p-red-2",
            ttl_seconds=_IntWithPreparationErrorFloat(300),
        )
    msg = str(exc.value)
    assert msg == "invalid proposal lifetime"
    assert "SECRET_CLOCK" not in msg
    assert exc.value.__cause__ is None


def test_clock_hostile_float_preparation_error_redacted():
    factory = ResearchProposalFactory(
        clock=_fixed_clock(_IntWithPreparationErrorFloat(1000)),
        proposal_id_factory=lambda: "p-red-3",
    )
    for _ in range(2):
        with pytest.raises(ResearchPreparationError) as exc:
            factory.prepare(_owner(), "q")
        msg = str(exc.value)
        assert msg == "research preparation failed"
        assert "SECRET_CLOCK" not in msg
        assert exc.value.__cause__ is None


def test_clock_hostile_float_valueerror_redacted():
    factory = ResearchProposalFactory(
        clock=_fixed_clock(_IntWithValueErrorFloat(1000)),
        proposal_id_factory=lambda: "p-red-4",
    )
    with pytest.raises(ResearchPreparationError) as exc:
        factory.prepare(_owner(), "q")
    msg = str(exc.value)
    assert msg == "research preparation failed"
    assert "SECRET_TTL" not in msg
    assert exc.value.__cause__ is None


def test_factories_remain_deterministic_after_redaction_changes():
    factory = ResearchProposalFactory(
        clock=_fixed_clock(1000.0), proposal_id_factory=lambda: "p-det-1"
    )
    first = factory.prepare(_owner(), "hikari architecture")
    second = factory.prepare(_owner(), "hikari architecture")
    assert first.proposal.proposal_id == second.proposal.proposal_id == "p-det-1"
    assert first.proposal.created_at == second.proposal.created_at == 1000.0
    assert first.proposal.expires_at == second.proposal.expires_at == 1900.0
    assert first.input.query == second.input.query == "hikari architecture"

    other = ResearchProposalFactory(
        clock=_fixed_clock(2000.0),
        proposal_id_factory=lambda: "p-det-2",
        ttl_seconds=120,
    )
    prep_a = other.prepare(_owner(), "q")
    prep_b = other.prepare(_owner(), "q")
    assert prep_a.proposal.created_at == prep_b.proposal.created_at == 2000.0
    assert prep_a.proposal.expires_at == prep_b.proposal.expires_at == 2120.0


# --------------------------------------------------------------------------
# Forbidden side effects
# --------------------------------------------------------------------------


def test_no_forbidden_imports():
    import ast
    import pathlib

    path = (
        pathlib.Path(__file__).resolve().parents[1]
        / "core"
        / "productivity"
        / "research.py"
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
    tree = ast.parse(path.read_text())
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
