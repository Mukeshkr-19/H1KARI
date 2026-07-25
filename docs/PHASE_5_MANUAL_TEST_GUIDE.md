# Phase 5 Manual Test Guide

Use this guide with `PHASE_5_ACCESSIBILITY_CHECKLIST.md`. It provides exact
actions, sample inputs, expected results, and evidence requirements. Automated
tests prove protocol and state invariants; these manual tests prove that a
person can perceive and operate the interface.

## 1. Evidence folder

Keep screenshots and notes outside the public repository:

```bash
mkdir -p "$HOME/Desktop/HIKARI-Phase5-Evidence/$(date +%F)"
```

For each test record:

- test ID and environment
- tester and date
- PASS, FAIL, or BLOCKED
- actual result and expected result
- screenshot or short screen recording filename
- VoiceOver announcement, when applicable
- browser console error, with secrets and private text removed

Never put pairing codes, private prompts, databases, or personal information in
the evidence.

## 2. Automated preflight

From the H1KARI repository:

```bash
cd path/to/H1KARI
.venv/bin/python -m pytest \
  tests/test_phase5_contracts.py \
  tests/test_phase5_policy.py \
  tests/test_phase5_session_lifecycle.py \
  tests/test_phase5_child_mode.py \
  tests/test_phase5_runtime_guard.py \
  tests/test_phase5_session_store.py \
  tests/test_phase5_runtime_service.py \
  tests/test_phase5_capability_service.py \
  tests/test_phase5_transport.py \
  tests/test_phase5_server_integration.py \
  tests/test_phase5_orchestrator_integration.py \
  tests/test_protocol_v1.py -q

cd hikari-frontend
npm run test:unit
npm run lint
npm run build
```

Pass criteria: every command exits successfully. Record the totals in the
sign-off notes. Automated success does not replace the human tests below.

## 3. Start the manual-test environment

Use synthetic text only, such as `photosynthesis`, `replace a light bulb`, and
`I feel worried`. Do not use real medical or private information.

Terminal 1:

```bash
cd path/to/H1KARI
npm run server
```

Record the temporary pairing code shown in that terminal. Do not screenshot or
publish it.

Terminal 2:

```bash
cd path/to/H1KARI/hikari-frontend
npm run dev
```

In a desktop browser:

1. Open `http://localhost:3000`.
2. Enter `ws://127.0.0.1:8765` as the server URL.
3. Enter the temporary pairing code.
4. Connect and wait for the paired/connected state.
5. Find the `Phase 5 Access` heading.

Pass criteria: the page connects, the paired owner controls become enabled,
and no raw exception or stack trace appears.

## 4. Functional scenarios

### P5-F01 — Owner session

1. Select **Activate owner session**.
2. Wait for focus to move to the Phase 5 status.
3. Read the status visually and with VoiceOver.

Expected:

- status becomes `active`
- an expiration date/time is present
- status is communicated with text, not color alone
- VoiceOver announces the state change once

### P5-F02 — Teach Me

1. In **Topic or request**, enter `photosynthesis`.
2. Select **Prepare Teach Me proposal**.
3. Navigate to the proposal preview and read every item.

Expected:

- proposal status becomes `proposal ready`
- a bounded outline and learning steps appear
- text says Teach Me does not install skills
- no install, execute, or completion claim appears

### P5-F03 — Guide My Hands, informational

1. Enter `replace a light bulb safely` in **Guidance request**.
2. Leave the consequential-step checkbox unchecked.
3. Select **Prepare guidance proposal**.

Expected:

- ordered guidance appears without an approval prompt
- the interface says guidance only and camera access is not automatic
- no step claims the physical task was completed

### P5-F04 — Guide My Hands, consequential approval

1. Enter `apply the final device setting`.
2. Check **This guidance includes a consequential step that requires my approval**.
3. Select **Prepare guidance proposal**.
4. Verify the status says `approval required`.
5. Select **Approve consequential guidance step** exactly once.

Expected:

- no proposal is released before confirmation
- the approval button is keyboard reachable and clearly named
- after confirmation, ordered guidance appears
- repeated activation is suppressed while submission is pending

### P5-F05 — Care

1. Enter `I feel worried and would like support`.
2. Select **Prepare Care proposal**.
3. Verify `approval required`, then select **Confirm Care review**.
4. Repeat with synthetic emergency text: `I feel unsafe and need urgent help`.

Expected:

- supportive-only wording is present
- the emergency limitation is visible and announced
- HIKARI states that it cannot contact emergency services
- it never diagnoses, prescribes, claims treatment, or says contact occurred

### P5-F06 — Child mode

1. Enter `child-test-1` as the child actor ID.
2. Select **Activate child mode session**.
3. Read the changed status and restrictions.

Expected:

- session type is child and status becomes active
- the UI states owner-controlled activation
- restrictions mention purchases, owner memory, helper grants, and audit bypass
- no child control offers permanent authority or policy weakening

After recording evidence, close the child session and reactivate an owner
session before testing other owner capabilities.

### P5-F07 — Trusted Helper lifecycle

Generate a timestamp one hour in the future:

```bash
date -v+1H +%s
```

1. Enter `helper-test-1` as the helper actor ID.
2. Enter the generated timestamp in **Access ends at (Unix timestamp)**.
3. Select **Create grant**.
4. Select **List grants**.
5. Read the scope, expiration, and active status.
6. Select **Revoke grant**.

Expected:

- only paired-owner controls are enabled
- the grant has a finite expiration
- no permanent or delegation control exists
- revocation changes the displayed status to revoked

### P5-F08 — Close and sensitive-state clearing

1. Create a Teach Me or Care proposal.
2. Select **Close session**.
3. Disconnect or reload the page.

Expected:

- terminal session status is announced
- proposal items, pending approval, Care text, and grant details do not survive
  a reset/disconnect unless safely re-fetched
- no stale proposal becomes active after reconnection

### P5-F09 — Expired approval

This test intentionally takes five minutes.

1. Prepare a consequential Guide or Care request.
2. Do not confirm it.
3. Wait at least five minutes and five seconds.
4. Attempt confirmation.

Expected: a safe stale-request error is announced and no proposal is elevated.

The server-level duplicate/stale invariants are also covered by
`tests/test_phase5_server_integration.py`; manual testing checks the user-facing
announcement and disabled-button behavior.

### P5-F10 — Guest/unpaired state

1. Open a new private browser window.
2. Connect without entering a pairing code.
3. Navigate to Phase 5 Access.

Expected:

- owner actions are disabled
- the interface never presents the guest as active owner
- any server denial uses fixed safe unauthorized wording
- no raw exception, path, actor token, or pairing detail appears

## 5. Keyboard-only test

Turn off VoiceOver for this section and do not use the mouse.

1. Reload the paired page.
2. Press Tab from the browser content start through every Phase 5 control.
3. Use Space on the consequential checkbox.
4. Use Enter and Space on buttons.
5. Use Shift+Tab to move backward.
6. Trigger Teach Me, Guide approval, Care approval, child activation, helper
   creation, and helper revocation.

Pass criteria:

- focus order follows the visible top-to-bottom order
- every control has a visible focus indicator
- no focus trap occurs
- disabled controls are skipped or announced disabled
- status focus movement does not lose the next logical navigation position
- every target is approximately 44 by 44 CSS pixels or larger

## 6. VoiceOver on macOS Safari

1. Open the paired page in Safari.
2. Press Command+F5 to start VoiceOver.
3. Use Control+Option+Right Arrow to traverse the Phase 5 region.
4. Use Control+Option+Command+H to navigate headings.
5. Use Control+Option+Space to activate controls.
6. Run P5-F01 through P5-F08.

Pass criteria:

- `Phase 5 Access` is announced as a heading/region
- Teach Me, Guide My Hands, Care, Child Mode, and Trusted Helper headings are
  announced in logical order
- every field has the exact visible label announced
- status changes and errors are announced once without reading raw internals
- approval buttons have distinct names and focus reaches the result
- lists and ordered guidance expose their list semantics

Record the important announcement text verbatim, but do not record private
input.

## 7. Magnification, contrast, and motion

### 200% zoom

1. Set browser zoom to 200% with Command+Plus.
2. Run P5-F02, P5-F04, and P5-F07.

Pass criteria: labels remain associated and readable, controls are not cut off,
critical content does not require two-dimensional scrolling, and focus remains
visible.

### Reduced motion

1. Open **System Settings → Accessibility → Display**.
2. Enable **Reduce motion**.
3. Reload and run P5-F01 through P5-F05.

Pass criteria: no information disappears, status changes remain readable, and
focus behavior remains stable.

### Increased contrast

1. In the same settings page, enable **Increase contrast**.
2. Inspect normal, focused, disabled, success, warning, and error states.

Pass criteria: boundaries and focus remain perceivable, and status meaning is
still available in text.

## 8. iOS Safari and VoiceOver

The current production boundary intentionally grants owner authority only to a
paired loopback connection. An iPhone must therefore be tested as a guest; it
must not be presented as an owner.

1. Serve the frontend on a trusted local test network with an approved host
   binding and open it in iOS Safari.
2. Do not expose HIKARI to the public internet.
3. Enable **Settings → Accessibility → VoiceOver**.
4. Swipe right through the Phase 5 region and double-tap controls.
5. Repeat at 200% iOS Zoom.

Pass criteria:

- headings, labels, restrictions, and disabled owner controls are announced
- touch targets are comfortably operable
- no horizontal clipping hides critical text
- the iPhone cannot claim owner, child, or helper authority

Record functional owner flows on loopback macOS separately. Do not mark an iOS
owner flow as passed because that flow is intentionally unavailable.

## 9. Browser storage privacy inspection

After running all scenarios, open browser developer tools:

1. Open **Application/Storage → Local Storage**.
2. Search keys and values for `phase5`, `care`, `child`, `helper`, `proposal`,
   the synthetic topic, and the synthetic actor IDs.
3. Run this Console check:

```javascript
Object.entries(localStorage).filter(([key, value]) =>
  /phase5|care|child|helper|proposal|photosynthesis/i.test(`${key} ${value}`)
)
```

Expected result: `[]`.

Also inspect Session Storage and IndexedDB. Expected: no Phase 5 proposal,
Care, child, helper, or approval payload persisted.

## 10. Result template

```text
Test ID:
Tester:
Date/time:
Device and OS:
Browser/version:
Assistive technology:
Result: PASS | FAIL | BLOCKED
Expected:
Actual:
VoiceOver announcement:
Evidence filename:
Issue/notes:
```

If any required test fails, leave the corresponding checklist item unchecked,
record the failure, and attach the sanitized evidence. Phase 5 UI general
availability requires every checklist row to pass or an explicitly reviewed,
documented exception.
