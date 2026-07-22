# Phase 4 Completion Evidence

Phase 4 establishes a bounded, local-first path for pairing a remote guest,
offering a task reference to the desktop owner, transferring one image, and
running an explicitly requested vision analysis. It does not make a paired
device an owner and does not transfer grants, approvals, or execution tickets.

## Delivered boundaries

- Pairing uses short-lived, one-use challenges. Device sessions are opaque,
  revocable records and remain non-authoritative display references.
- Remote transports remain guests. Actor and session identity are derived from
  the transport, and desktop handoff acceptance performs fresh policy
  evaluation against a frozen preview.
- Visual-transfer JSON carries only bounded metadata. One PNG or JPEG binary
  frame, no larger than 1 MiB or 4096 by 4096 pixels, is accepted only on the
  exact paired connection and accepted handoff scope.
- Image bytes are held only in bounded memory and are removed on success,
  failure, cancellation, expiry, or disconnect. Hashes are receipts, never
  authorization.
- OCR is explicit, cancellable, local-only, and confidence-aware. Observation
  content is absent from logs, audit metadata, errors, and live status regions.
- Browser camera access occurs only after the user starts an accepted-handoff
  analysis and then presses **Start camera**. Permission requests can be
  cancelled, activity is visible, audio is disabled, and tracks are stopped on
  capture, cancellation, failure, stale completion, or unmount.

## Optional description capability

The local description adapter boundary is implemented and accepts only an
explicitly injected, reviewed local analyzer. No description engine is selected
or discovered automatically. The default server therefore fails closed for
`describe`, and the frontend marks that option unavailable. Enabling it later
requires an explicit reviewed local engine; it must not introduce downloads,
cloud fallback, provider selection, or fabricated confidence.

## Scope exclusions

- no silent or continuous capture
- no screen capture or legacy desktop-awareness integration
- no microphone capture
- no image bytes, base64, data URLs, filenames, or paths in JSON
- no automatic upload, provider call, model download, or cloud egress
- no portable mobile authority
- no Phase 5 learning/care experiences or Phase 6 hardware control

## Verification

The release gate includes protocol validation, pairing and handoff isolation,
visual-transfer lifecycle and cleanup, vision runtime and adapter tests, camera
privacy and accessibility contracts, frontend unit/lint/build checks, import and
CLI isolation, provenance scans, and repository-wide Phase 3 and Phase 4
regressions. Tests use injected clocks, IDs, runners, and browser fakes; they do
not access a real camera or execute a real OCR or description engine.

The Phase 4 exit conditions in `docs/HIKARI_MASTER_PLAN.md` are met: capture is
explicit and cancellable, uncertainty is communicated, and mobile pairing does
not broaden desktop permissions.
