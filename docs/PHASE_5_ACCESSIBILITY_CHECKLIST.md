# Phase 5 Accessibility Checklist

This checklist is **required human release evidence**. It does **not** claim that
representative-user testing has already been performed.

Follow the exact setup, test cases, expected results, and evidence template in
[`PHASE_5_MANUAL_TEST_GUIDE.md`](PHASE_5_MANUAL_TEST_GUIDE.md). Do not check an
item here until its mapped manual test passes.

Mark each item with date, tester, environment, and pass/fail notes.

## Environments

- [ ] Desktop Safari + VoiceOver (macOS)
- [ ] iOS Safari + VoiceOver
- [ ] Keyboard-only desktop Chromium/Firefox
- [ ] Screen magnification (macOS Zoom / iOS Zoom)
- [ ] Prefers-reduced-motion enabled
- [ ] High-contrast / increased contrast mode

## Keyboard-only navigation

- [ ] Tab order reaches every Phase 5 control in logical order
- [ ] All actions use native `button` / `input` / `textarea` controls
- [ ] Visible focus ring remains on every interactive control
- [ ] Enter/Space activates buttons
- [ ] No keyboard trap inside Phase 5 panels

## Screen readers

- [ ] Panel landmark/heading structure announced (`Phase 5 Access` and subpanel headings)
- [ ] Every input has a visible associated label
- [ ] Status region announces session state, approval state, expiration, and revocation
- [ ] Error region announces safe fixed error text only (no raw exception text)
- [ ] Focus moves to status/proposal/approval result when those update

## Visual / motion / contrast

- [ ] Status is not conveyed by color alone (text labels present)
- [ ] Touch targets are at least consistent with existing project controls (~44px)
- [ ] Reduced motion does not break readability or focus behavior
- [ ] Magnification to 200% keeps labels and controls usable without horizontal clipping of critical text

## Cognitive clarity

- [ ] Teach Me clearly states skills are not installed
- [ ] Guide My Hands presents uncertainty and ordered steps without implying completion
- [ ] Care states supportive-only framing and emergency limitation prominently
- [ ] Child Mode explains restricted capabilities and owner-controlled activation
- [ ] Trusted Helper shows scope, “access ends at”, revoke control, and no permanent/delegation option

## Functional accessibility scenarios

- [ ] Child-mode activation announces restricted status
- [ ] Teach Me proposal review is reachable and readable
- [ ] Guide approval flow requires explicit correlated confirm control
- [ ] Care limitation is announced and never claims contact occurred
- [ ] Helper grant creation and revocation are owner-only and announced
- [ ] Expired/revoked session clears sensitive proposal content from UI state
- [ ] Stale/duplicate approval responses do not elevate authority
- [ ] Unpaired/guest denial is announced with safe unauthorized messaging
- [ ] Privacy inspection: no sensitive proposal/care/child/helper details persisted to `localStorage`

## Sign-off

| Item | Tester | Date | Result | Notes |
|------|--------|------|--------|-------|
| Keyboard-only | | | | |
| VoiceOver macOS/iOS | | | | |
| Magnification | | | | |
| Reduced motion | | | | |
| High contrast | | | | |
| Cognitive clarity | | | | |
| Scenario matrix | | | | |

Human release gate: all rows must be completed before Phase 5 UI is marked generally available.
