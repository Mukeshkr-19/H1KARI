# Third-Party Notices and Distribution Gate

H1KARI does not vendor dependency source, model weights, datasets, or frontend
`node_modules` in this repository. Installers resolve the exact manifests and
locks listed below. This file is the Phase 0 notice manifest; a packaged release
must include the license texts and notices for the exact artifacts it distributes.

## Python environment

The supported macOS arm64/Python 3.12 dependency set is recorded in:

- `requirements-macos-arm64-py312.lock`
- `requirements-dev-macos-arm64-py312.lock`

Package licenses remain available in each installed distribution's metadata.
Native PortAudio is installed separately and remains subject to its upstream
license. Other platforms are not represented as reproducible Phase 0 targets.

## Frontend environment

The exact npm graph is `hikari-frontend/package-lock.json`. The deterministic
license-family and artifact index is `docs/FRONTEND_THIRD_PARTY_INPUT.md`.

Important distribution obligations include:

- Next.js and React-family packages: retain their shipped license text.
- `sharp`: Apache-2.0.
- prebuilt `@img/sharp-libvips-*`: LGPL-3.0-or-later package declaration;
  distributors must include the applicable LGPL text and satisfy source/relinking
  obligations for the exact binary package.
- `axe-core`: MPL-2.0 plus its bundled third-party notice.
- CC-BY, CC0, Python-2.0, and other content-license entries: retain the exact
  attribution or license evidence identified in the generated index.

H1KARI currently publishes source, not a prebuilt frontend binary. A release
artifact is blocked until its own notice bundle is generated from the installed
artifact set and reviewed.

## Downloaded models

No model weights are tracked. Exact sources, reviewed revisions, licenses,
download behavior, and redistribution dispositions are recorded in
`docs/MODEL_PROVENANCE.md`. Phase 0 permits opt-in runtime downloads only; it
does not authorize H1KARI to redistribute those weights.

## Hosted services

Provider and external-service data flows, retention evidence, and disable paths
are recorded in `docs/PROVIDER_PROVENANCE.md`. API access does not place provider
software or model weights under the H1KARI project license.

## H1KARI project license

No project license has been selected. That decision belongs to the repository
owner after this provenance record is reviewed.
