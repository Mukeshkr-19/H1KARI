import assert from "node:assert/strict";
import { describe, it } from "node:test";
import {
  RESEARCH_DOMAINS_MAX,
  RESEARCH_DOMAIN_MAX,
  RESEARCH_MAX_RESULTS_DEFAULT,
  RESEARCH_MAX_RESULTS_MAX,
  RESEARCH_QUERY_MAX,
  createEmptyResearchFields,
  createResearchRequestId,
  hasResearchUnicodeFormatChars,
  isValidResearchRequestId,
  mapResearchValidationMessage,
  parseResearchDomainsText,
  researchCodePointLength,
  researchResponseMatchesRequest,
  validateResearchFields,
} from "./researchProposal";
import { encodeProductivityResearchPrepare } from "./productivityProtocol";

function sample(overrides: Record<string, unknown> = {}) {
  return {
    query: "What changed in the latest release?",
    domainsText: "example.com\n docs.example.com ",
    maxResults: "12",
    ...overrides,
  };
}

describe("researchProposal", () => {
  it("validates and freezes bounded research fields", () => {
    const result = validateResearchFields(sample());
    assert.equal(result.ok, true);
    if (!result.ok) {
      return;
    }
    assert.equal(result.fields.query, "What changed in the latest release?");
    assert.deepEqual(result.fields.domains, ["example.com", "docs.example.com"]);
    assert.equal(result.fields.maxResults, 12);
    assert.throws(() => {
      (result.fields as { query: string }).query = "nope";
    }, TypeError);
  });

  it("accepts research without domains and applies default max results", () => {
    const result = validateResearchFields(
      sample({ domainsText: "", maxResults: String(RESEARCH_MAX_RESULTS_DEFAULT) }),
    );
    assert.equal(result.ok, true);
    if (!result.ok) {
      return;
    }
    assert.equal(result.fields.domains, undefined);
    assert.equal(result.fields.maxResults, RESEARCH_MAX_RESULTS_DEFAULT);
  });

  it("preserves exact query content without truncation or rewriting", () => {
    const query = "  keep leading and trailing  ";
    const result = validateResearchFields(sample({ query, domainsText: "" }));
    assert.equal(result.ok, true);
    if (!result.ok) {
      return;
    }
    assert.equal(result.fields.query, query);
  });

  it("rejects whitespace-only query under Python strip parity", () => {
    assert.equal(validateResearchFields(sample({ query: "   " })).ok, false);
    assert.equal(validateResearchFields(sample({ query: "\u0085" })).ok, false);
    const blank = validateResearchFields(sample({ query: " \t\n " }));
    assert.equal(blank.ok, false);
    if (!blank.ok) {
      assert.equal(blank.code, "query_blank");
      assert.equal(blank.field, "query");
    }
  });

  it("rejects empty oversized and control-bearing query without truncation", () => {
    assert.equal(validateResearchFields(sample({ query: "" })).ok, false);
    const tooLong = validateResearchFields(
      sample({ query: "q".repeat(RESEARCH_QUERY_MAX + 1) }),
    );
    assert.equal(tooLong.ok, false);
    if (!tooLong.ok) {
      assert.equal(tooLong.code, "query_too_long");
      assert.equal(tooLong.field, "query");
    }
    const controls = validateResearchFields(sample({ query: "bad\u0000query" }));
    assert.equal(controls.ok, false);
    if (!controls.ok) {
      assert.equal(controls.code, "query_invalid_controls");
    }
    const cf = validateResearchFields(sample({ query: "bad\u200bquery" }));
    assert.equal(cf.ok, false);
    if (!cf.ok) {
      assert.equal(cf.code, "query_invalid_controls");
    }
  });

  it("uses Unicode code-point length for text bounds including emoji", () => {
    const emojiQuery = "🔍".repeat(RESEARCH_QUERY_MAX);
    assert.equal(researchCodePointLength(emojiQuery), RESEARCH_QUERY_MAX);
    assert.equal(validateResearchFields(sample({ query: emojiQuery })).ok, true);
    assert.equal(
      validateResearchFields(sample({ query: `${emojiQuery}🔍` })).ok,
      false,
    );
  });

  it("rejects domain count aggregate and per-domain bounds", () => {
    const domains = Array.from(
      { length: RESEARCH_DOMAINS_MAX + 1 },
      (_, index) => `site${index}.example.com`,
    ).join("\n");
    const tooMany = validateResearchFields(sample({ domainsText: domains }));
    assert.equal(tooMany.ok, false);
    if (!tooMany.ok) {
      assert.equal(tooMany.code, "domains_too_many");
      assert.equal(tooMany.field, "domainsText");
    }
    const tooLong = validateResearchFields(
      sample({ domainsText: "a".repeat(RESEARCH_DOMAIN_MAX + 1) }),
    );
    assert.equal(tooLong.ok, false);
    if (!tooLong.ok) {
      assert.equal(tooLong.code, "domain_too_long");
    }
  });

  it("rejects duplicate domains and control-bearing domain lines", () => {
    const duplicate = validateResearchFields(
      sample({ domainsText: "Example.COM\nexample.com" }),
    );
    assert.equal(duplicate.ok, false);
    if (!duplicate.ok) {
      assert.equal(duplicate.code, "domains_duplicate");
    }
    const controls = validateResearchFields(
      sample({ domainsText: "bad\u0000.example.com" }),
    );
    assert.equal(controls.ok, false);
    if (!controls.ok) {
      assert.equal(controls.code, "domain_invalid_controls");
    }
  });

  it("rejects invalid and out-of-range max results", () => {
    const invalid = validateResearchFields(sample({ maxResults: "10.5" }));
    assert.equal(invalid.ok, false);
    if (!invalid.ok) {
      assert.equal(invalid.code, "max_results_invalid");
      assert.equal(invalid.field, "maxResults");
    }
    const low = validateResearchFields(sample({ maxResults: "0" }));
    assert.equal(low.ok, false);
    if (!low.ok) {
      assert.equal(low.code, "max_results_out_of_range");
    }
    const high = validateResearchFields(
      sample({ maxResults: String(RESEARCH_MAX_RESULTS_MAX + 1) }),
    );
    assert.equal(high.ok, false);
    if (!high.ok) {
      assert.equal(high.code, "max_results_out_of_range");
    }
  });

  it("rejects unknown fields and non-string values", () => {
    assert.equal(validateResearchFields({ extra: true }).ok, false);
    assert.equal(validateResearchFields({ query: 1, domainsText: "", maxResults: "10" }).ok, false);
  });

  it("maps field-specific validation messages", () => {
    assert.equal(mapResearchValidationMessage("query_blank"), "Enter a research query.");
    assert.equal(
      mapResearchValidationMessage("domains_too_many"),
      "Enter at most 16 allowed domains.",
    );
    assert.equal(
      mapResearchValidationMessage("unknown-code"),
      "The research request could not be validated.",
    );
  });

  it("parses domains one per line and ignores blank lines", () => {
    assert.deepEqual(parseResearchDomainsText("a.example.com\n\nb.example.com"), [
      "a.example.com",
      "b.example.com",
    ]);
  });

  it("creates canonical request ids and matches responses exactly", () => {
    const requestId = createResearchRequestId();
    assert.match(requestId, /^research-/);
    assert.equal(isValidResearchRequestId(requestId), true);
    assert.equal(isValidResearchRequestId("Bad ID"), false);
    assert.equal(researchResponseMatchesRequest(requestId, requestId), true);
    assert.equal(researchResponseMatchesRequest(requestId, "other-id"), false);
    assert.equal(researchResponseMatchesRequest(null, requestId), false);
  });

  it("detects Unicode format characters without rewriting", () => {
    assert.equal(hasResearchUnicodeFormatChars("plain"), false);
    assert.equal(hasResearchUnicodeFormatChars("bad\u200btext"), true);
  });

  it("creates empty fields with default max results", () => {
    const fields = createEmptyResearchFields();
    assert.equal(fields.query, "");
    assert.equal(fields.domainsText, "");
    assert.equal(fields.maxResults, String(RESEARCH_MAX_RESULTS_DEFAULT));
  });

  it("encodes exact prepare messages and rejects malformed input", () => {
    const requestId = "research-req-1";
    const encoded = encodeProductivityResearchPrepare({
      type: "productivity_research_prepare",
      request_id: requestId,
      query: "Latest release notes",
      domains: ["example.com"],
      max_results: 5,
    });
    assert.ok(encoded);
    assert.equal(encoded.type, "productivity_research_prepare");
    assert.equal(encoded.request_id, requestId);
    assert.equal(encoded.query, "Latest release notes");
    assert.deepEqual(encoded.domains, ["example.com"]);
    assert.equal(encoded.max_results, 5);

    const defaultMax = encodeProductivityResearchPrepare({
      type: "productivity_research_prepare",
      request_id: requestId,
      query: "Latest release notes",
    });
    assert.ok(defaultMax);
    assert.equal("domains" in defaultMax, false);
    assert.equal("max_results" in defaultMax, false);

    assert.equal(
      encodeProductivityResearchPrepare({
        type: "productivity_research_prepare",
        request_id: "Bad ID",
        query: "q",
      }),
      null,
    );
    assert.equal(
      encodeProductivityResearchPrepare({
        type: "productivity_research_prepare",
        request_id: requestId,
        query: "q",
        proposal_id: "prop-1",
      }),
      null,
    );
  });
});
