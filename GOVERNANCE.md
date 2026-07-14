# Project Governance

H1KARI is currently maintainer-led. The repository owner has final responsibility
for product scope, safety boundaries, releases, and the project license.

Contributors may propose changes through focused branches or pull requests.
Maintainers evaluate them against the canonical plan, observed behavior, tests,
privacy, accessibility, provenance, and rollback requirements. Compatibility is
preserved unless a documented safety issue requires containment.

`develop` is the pre-release integration branch. `main` changes only through an
explicit release decision after all required gates pass. Security fixes may be
prioritized, but they do not bypass review or verification.

The project license remains an owner decision after the Phase 0 provenance audit.
Until a license is added, the public source is visible but no additional reuse
permission is granted beyond applicable law.
