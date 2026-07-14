# Security Policy

## Report a vulnerability

Use GitHub's private security-advisory flow for this repository. Do not include
credentials, private memories, voice samples, database files, or exploit details
in a public issue.

Include the affected commit, reproduction steps using synthetic data, expected
impact, and any safe workaround. Reports are triaged by severity and verified
before a public fix or disclosure is prepared.

## Supported code

- `main` is the public stable baseline.
- `develop` is the current pre-release integration branch.
- Historical branches are unsupported unless the same behavior exists on one of
  those branches.

Security support covers project-owned source. Provider services, downloaded
models, operating-system commands, and package-manager artifacts also remain
subject to their upstream security policies.

## Security boundaries

- Runtime state and credentials belong outside the checkout under `HIKARI_HOME`.
- Guests and unknown speakers must not gain owner-memory access.
- Model or tool output is data, never authorization.
- Side effects require the existing feature-specific guard until migration to the
  central action-policy path is complete.
- No vulnerability report should require a live private brain or real identity.
