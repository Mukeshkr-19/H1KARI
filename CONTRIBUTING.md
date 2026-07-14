# Contributing to H1KARI

H1KARI accepts focused changes that preserve its local-first privacy, reviewed
memory authority, guest isolation, and safe-action boundaries.

## Before changing code

1. Start from current `develop`; never implement directly on `main`.
2. State the user outcome, non-goals, affected data, side effects, and rollback.
3. Reuse existing code or the standard library before adding a dependency.
4. Record the exact source, version, license, data egress, and disable path for
   any dependency, model, service, asset, or copied pattern.

## Required checks

Run focused tests plus the applicable commands in `docs/WORKFLOW.md`. Every
change must also pass `git diff --check` and the public-source privacy scan.
Frontend release candidates must complete `docs/ACCESSIBILITY_CHECKLIST.md`.

Use synthetic identities and data in tests and documentation. Never submit API
keys, conversations, voice samples, personal databases, local paths, or private
operating records.

## Review and integration

Keep each branch coherent and independently reviewable. A maintainer reviews the
complete diff, verifies provenance and privacy, runs integration gates, and
decides whether the change enters `develop`. A merge to `main` requires a
separate release decision.
