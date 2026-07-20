"""Source contracts for Phase 3 browser-research frontend primitives."""

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
HELPERS = (
    REPO_ROOT
    / "hikari-frontend"
    / "src"
    / "utils"
    / "productivity"
    / "researchProposal.ts"
)
HELPER_TESTS = (
    REPO_ROOT
    / "hikari-frontend"
    / "src"
    / "utils"
    / "productivity"
    / "researchProposal.test.ts"
)
PROTOCOL = (
    REPO_ROOT
    / "hikari-frontend"
    / "src"
    / "utils"
    / "productivity"
    / "productivityProtocol.ts"
)
COMPONENT = (
    REPO_ROOT
    / "hikari-frontend"
    / "src"
    / "components"
    / "ResearchProposalForm.tsx"
)
PAGE = REPO_ROOT / "hikari-frontend" / "src" / "app" / "page.tsx"
PACKAGE = REPO_ROOT / "hikari-frontend" / "package.json"


def _helpers() -> str:
    assert HELPERS.is_file()
    return HELPERS.read_text(encoding="utf-8")


def _tests() -> str:
    assert HELPER_TESTS.is_file()
    return HELPER_TESTS.read_text(encoding="utf-8")


def _component() -> str:
    assert COMPONENT.is_file()
    return COMPONENT.read_text(encoding="utf-8")


def _page() -> str:
    assert PAGE.is_file()
    return PAGE.read_text(encoding="utf-8")


def test_research_files_exist():
    assert HELPERS.is_file()
    assert HELPER_TESTS.is_file()
    assert COMPONENT.is_file()
    assert "encodeProductivityResearchPrepare" in PROTOCOL.read_text(encoding="utf-8")


def test_helpers_define_bounds_and_validation():
    text = _helpers()
    assert "RESEARCH_QUERY_MAX = 2000" in text
    assert "RESEARCH_DOMAIN_MAX = 253" in text
    assert "RESEARCH_DOMAINS_MAX = 16" in text
    assert "RESEARCH_MAX_RESULTS_MAX = 20" in text
    assert "export function validateResearchFields(" in text
    assert "export function researchCodePointLength(" in text
    assert "export function isBlankResearchQuery(" in text
    assert "hasResearchUnicodeFormatChars" in text
    assert "\\p{Cf}" in text
    assert "\\p{White_Space}" in text
    assert "query_blank" in text
    assert "domains_duplicate" in text
    assert "researchResponseMatchesRequest" in text


def test_helpers_exclude_side_effects_and_sensitive_sinks():
    text = _helpers()
    for banned in (
        "fetch(",
        "WebSocket",
        "localStorage",
        "sessionStorage",
        "setTimeout",
        "console.",
        "provider",
        "browser access",
    ):
        assert banned not in text


def test_encoder_emits_exact_prepare_type():
    text = PROTOCOL.read_text(encoding="utf-8")
    assert 'type: "productivity_research_prepare"' in text
    assert "export function encodeProductivityResearchPrepare(" in text
    assert "validateResearchFields" in text
    assert "isValidResearchRequestId" in text


def test_component_is_labelled_and_privacy_safe():
    text = _component()
    assert 'htmlFor={queryId}' in text
    assert 'htmlFor={domainsId}' in text
    assert 'htmlFor={maxResultsId}' in text
    assert "<textarea" in text
    assert 'type="button"' in text
    assert "disabled={locked" in text or "disabled={locked}" in text
    assert "validationMessageId" in text
    assert 'activeField === "query" ? validationMessageId' in text
    assert "disabled={submitDisabled}" in text
    assert "disabled={pending}" in text
    assert 'role="alert"' in text
    assert "Prepare research" in text
    assert "autoFocus" not in text
    assert "localStorage" not in text
    assert "sessionStorage" not in text
    assert "addMessage" not in text
    assert "maxLength" not in text


def test_page_wires_prepare_pending_clear_and_privacy():
    text = _page()
    assert "<ResearchProposalForm" in text
    assert "encodeProductivityResearchPrepare(" in text
    assert "submitResearchPrepare" in text
    assert "researchPendingRef" in text
    assert "researchRequestIdRef" in text
    assert "researchResponseMatchesRequest" in text
    assert "createResearchRequestId" in text
    assert "clearResearchForm" in text
    assert "clearResearchForm()" in text
    submit_start = text.index("const submitResearchPrepare")
    submit_end = text.index("const resetResearchForm")
    submit_block = text[submit_start:submit_end]
    assert "addMessage(" not in submit_block
    assert "localStorage" not in submit_block
    assert "sessionStorage" not in submit_block
    assert "JSON.stringify(encoded)" in submit_block
    assert "request_id: requestId" in submit_block
    apply_start = text.index("const applyProductivityMessage")
    apply_end = text.index("const confirmProductivityAction")
    apply_block = text[apply_start:apply_end]
    assert "researchPendingRef.current" in apply_block
    assert "researchResponseMatchesRequest" in apply_block
    assert "setResearchPrepareError(message.code)" in apply_block


def test_page_blocks_cross_form_prepare_while_research_pending():
    text = _page()
    assert "productivityPreparePending" in text
    email_submit_start = text.index("const submitEmailDraftPrepare")
    email_submit_end = text.index("const resetEmailDraftForm")
    email_submit = text[email_submit_start:email_submit_end]
    calendar_submit_start = text.index("const submitCalendarPrepare")
    calendar_submit_end = text.index("const resetCalendarForm")
    calendar_submit = text[calendar_submit_start:calendar_submit_end]
    assert "if (isProductivityPreparePending())" in email_submit
    assert "if (isProductivityPreparePending())" in calendar_submit


def test_unit_tests_cover_required_behaviors():
    text = _tests()
    for needle in (
        "validates and freezes bounded research fields",
        "preserves exact query content without truncation or rewriting",
        "rejects whitespace-only query under Python strip parity",
        "rejects duplicate domains and control-bearing domain lines",
        "rejects domain count aggregate and per-domain bounds",
        "rejects invalid and out-of-range max results",
        "creates canonical request ids and matches responses exactly",
        "encodes exact prepare messages and rejects malformed input",
        "rejects unknown fields and non-string values",
        "maps field-specific validation messages",
    ):
        assert needle in text


def test_unit_script_includes_research_suite():
    package = PACKAGE.read_text(encoding="utf-8")
    assert "researchProposal.test.ts" in package
    assert "researchProposal.test.js" in package
