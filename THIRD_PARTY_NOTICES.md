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

## Adapted JARVIS portions

Portions of the following tracked files are adapted from the JARVIS Voice AI
Assistant project:

- `core/task_planner.py`
- `core/action_system.py`
- `core/desktop_awareness.py`
- `core/mac_integration.py`

Upstream source: https://github.com/ethanplusai/jarvis

The upstream `main` revision reviewed for this notice was
`df3044fcf238c8e270c2ecd32302cea159435c48`. The notice below is copied verbatim
from that revision's `LICENSE` file and applies to the adapted portions listed
above:

```text
JARVIS Voice AI Assistant
Copyright (c) 2026 Ethan Rogers

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to use,
copy, modify, and run the Software for personal, non-commercial purposes,
subject to the following conditions:

1. PERSONAL USE: You may use, copy, modify, and run the Software for personal,
   educational, and non-commercial purposes without restriction.

2. COMMERCIAL USE PROHIBITED WITHOUT LICENSE: You may NOT use the Software,
   or any derivative work based on the Software, for commercial purposes
   without obtaining a commercial license. Commercial purposes include, but
   are not limited to:
   - Selling the Software or any derivative work
   - Using the Software as part of a paid product or service
   - Using the Software to generate revenue, directly or indirectly
   - Offering the Software as a hosted service (SaaS)

3. ATTRIBUTION: All copies or substantial portions of the Software must
   include this license notice and the above copyright notice.

4. COMMERCIAL LICENSING: For commercial use, licensing inquiries, or
   partnership opportunities, visit: https://ethanplus.ai

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

Use and redistribution of those adapted portions remain subject to the
upstream license, including its attribution and non-commercial-use limits.
A commercial H1KARI release containing them is blocked unless the owner obtains
separate commercial permission or replaces them with independently developed,
clean-room code.

## H1KARI project license

No project license has been selected. That decision belongs to the repository
owner after this provenance record is reviewed.
