# HIKARI Provenance Inventory

Status: WP-001 inventory complete; remediation open
Baseline: `develop` at `30e545e`
Reviewed: 2026-07-13

## Purpose and rules

This is the Phase 0 inventory for shipped components, dependencies, models, assets, prompts, and external services. It records evidence and unresolved provenance without selecting a final project license.

- `requirements.txt`, `requirements-dev.txt`, and `hikari-frontend/package-lock.json` remain the machine-readable dependency sources.
- A dependency or asset is not approved merely because it is already present.
- Hosted model names describe configuration, not bundled weights or redistribution rights.
- Unknown origin, license, or data handling is a release blocker until resolved.
- The final HIKARI project license requires a separate owner decision after remediation.

## Repository and component inventory

| Component | Paths | Runtime boundary | Provenance state |
|---|---|---|---|
| CLI and orchestration | `hikari.py`, `core/orchestrator.py`, `core/router.py` | local Python plus optional hosted/local model calls | project code; initial clean-room release commit `a3837d5` |
| Brain v2 and neural memory | `core/brain_v2/`, `core/neural_memory/` | local SQLite and private runtime storage | project code; private databases are not shipped |
| Agents and actions | `agents/`, action modules under `core/` | network and macOS side effects | project code; central policy consolidation remains planned |
| Voice and speaker identity | `core/voice.py`, `core/speaker_auth.py`, voice services | microphone, local model downloads, macOS speech | project code with third-party model obligations listed below |
| Pairing server | `core/server.py` | local HTTP and WebSocket listener | project code; QR generation is third-party |
| Background services | `services/`, login-agent scripts | microphone, launchd, macOS commands | project code; optional runtime paths differ in dependency coverage |
| Frontend | `hikari-frontend/` | Next.js web client | project code plus locked npm graph and template residue |
| Tests and release checks | `tests/`, `scripts/brain_live_qa.py`, `core/doctor.py` | local-only verification | project code and synthetic fixtures |

There is no root `LICENSE`, `COPYING`, or `NOTICE` file. That absence is intentional until dependency, model, prompt, and asset obligations are resolved and the owner chooses the final license.

## Python dependency inventory

### Direct shipped imports

| Declaration | Shipped use | Local metadata license | Review state |
|---|---|---|---|
| `feedparser>=6.0.0` | news feeds | BSD-2-Clause | direct dependency; lower bound is not reproducible |
| `litellm==1.84.0` | provider routing | MIT | direct dependency; local environment is 1.83.0 |
| `numpy>=1.26.0` | audio and voice features | multiple permissive/content notices | direct dependency; lower bound is not reproducible |
| `openai==2.30.0` | OpenAI-compatible/provider routing | Apache-2.0 | direct dependency |
| `PyAudio==0.2.14` | microphone audio | MIT | direct dependency; native PortAudio provenance must be recorded by installer/platform |
| `python-dotenv==1.2.2` | local configuration | BSD-3-Clause | direct dependency; local environment is 1.0.1 |
| `qrcode[pil]>=7.4.2` | pairing QR image | BSD family | direct dependency; Pillow is an extra but is not explicitly pinned |
| `requests==2.33.0` | research and provider HTTP | Apache-2.0 | direct dependency; local environment is 2.32.3 |
| `SpeechRecognition==3.13.0` | microphone recognition fallback | BSD family | direct dependency; external recognizer behavior must remain explicit |
| `torch>=2.0.0` | speaker identity model | BSD-3-Clause | direct dependency; unbounded model/runtime footprint |
| `speechbrain>=1.0.0` | speaker identity model | unresolved locally | declared but absent from the verified environment |
| `websockets>=12.0` | pairing server | BSD-3-Clause | direct dependency; lower bound permits major-version drift |
| `openai-whisper>=20231117` | local speech recognition | MIT | direct dependency; distribution name is protected by a regression check |
| `faster-whisper==1.2.1` | optional local speech recognition in daemon services | MIT metadata | direct optional dependency; now explicitly declared |

`cohere` is reached through the HTTP router, not the Cohere Python package. The shared environment currently has `faster-whisper` 1.2.1 and `ctranslate2` 4.7.1, both reporting MIT metadata.

### Declaration problems

`requirements.txt` now has 15 direct runtime dependencies. Resolver-owned transitive packages are no longer presented as an incomplete lock.

- The unrelated Graphite `whisper==1.1.10` package was removed. HIKARI calls OpenAI Whisper's `load_model`, and a regression check now prevents the ambiguous distribution name from returning.
- `faster-whisper==1.2.1` is now declared for the optional service paths that import it.
- The existing shared development environment still records both Whisper distributions. Do not uninstall one in place because their files overlap; rebuild the environment from the corrected manifest during reproducibility work.
- The unused `beautifulsoup4`, `cohere`, `parameterized`, `pyttsx3`, `PyYAML`, `types-requests`, and `wikipedia` declarations were removed and are protected by a regression check.
- Explicit transitive pins were removed after static import review and a successful no-install resolver check. A platform/Python-specific lock is still required for reproducible installation.
- The shared environment differs from exact direct declarations for `litellm`, `python-dotenv`, and `requests`; `speechbrain` is missing. `pip check` still passes, which proves dependency compatibility only, not manifest reproducibility.

Optional undeclared imports remain in the menu-bar and always-on prototypes: `rumps` and `openwakeword`. They are not approved dependencies; their feature boundaries and model provenance must be repaired or removed before those paths are release-ready.

## Frontend dependency inventory

Direct declarations are Next.js, React, React DOM, ESLint, Tailwind CSS, TypeScript, their type/config packages, and the optional Darwin SWC binary. The lockfile is version 3 and is the exact transitive source for this baseline.

| Finding | Evidence | Disposition |
|---|---|---|
| Direct runtime versions | Next 15.5.18; React and React DOM 19.1.0 in the lock | retain pending frontend build and vulnerability gates |
| Lock license families | 317 MIT, 29 Apache-2.0, 17 ISC, 12 MPL-2.0, 9 LGPL-3.0-or-later, plus BSD, BlueOak, CC, Python-2.0, and 0BSD entries | generate notices and review redistribution obligations before release |
| Native image binaries | `sharp` and platform `libvips` packages enter through Next | verify binary redistribution and notice requirements |
| Overrides | `js-yaml`, `postcss`, and `tar` are security-pinned in `package.json` | keep until audit proves the parent graph no longer needs them |
| Reproducibility | frontend `node_modules` is absent from the worktree | run `npm ci`, lint, build, and audit on the frontend work package |

Registry license fields are evidence pointers, not a substitute for reviewing the license files shipped in resolved packages.

## Model and provider inventory

| Model or service | Location/use | Bundled? | Provenance and release state |
|---|---|---:|---|
| OpenAI Whisper `base` | `core/voice.py` | no; downloaded at runtime | code package reports MIT; expected cache and offline readiness are visible through `hikari.py --voice-status`; weight origin, checksum, size, and user disclosure still need a model record |
| faster-whisper `base` | always-on and wake daemon services | no; downloaded at runtime | direct dependency; expected Hugging Face cache and offline readiness are visible through `hikari.py --voice-status`; conversion/runtime and weight record still required |
| `speechbrain/spkrec-ecapa-voxceleb` | `core/speaker_auth.py` | no; downloaded at runtime | exact model id is recorded; model-card license, training-data limits, cache/checksum, and biometric disclosure must be reviewed |
| Ollama models | `config/providers.yaml` | no | local provider strings only; exact tags, upstream model licenses, sizes, and availability are unverified |
| Google, Groq, OpenRouter, Cerebras, DeepSeek, NVIDIA, Cohere | `config/providers.yaml` and `core/router.py` | no | hosted-service configuration; model names, terms, retention, regions, and data egress require provider records before release claims |
| DuckDuckGo, BBC feeds, OpenWeather | `agents/research.py` | no | external endpoints; request fields, terms, rate limits, retention, and failure behavior require service records |

No model weights, datasets, ONNX files, GGUF files, or audio samples are tracked in the repository.

`hikari.py --voice-status` is a read-only inspection path. It checks package and
expected cache-path metadata without importing model packages, downloading weights,
or reading the speaker enrollment file. It also identifies the Google Speech fallback
as a possible off-device audio-egress path.

## Prompt inventory

| Prompt family | Owned source | Third-party prompt text | State |
|---|---|---|---|
| Core system prompt | `core/orchestrator.py` | none identified | project-authored; not separately versioned |
| Router default prompt | `core/router.py` | none identified | project-authored fallback |
| Personality and emotion context | `core/personality.py`, `core/adaptive_personality.py`, `core/emotional_intelligence.py` | none identified | generated from project rules and local state |
| Brain v2 retrieval context | `core/brain_v2/` | none identified | project-authored; source-linked private content is runtime data, not shipped prompt text |
| Legacy memory context | `core/brain.py`, `core/neural_memory_bridge.py` | none identified | quarantined/compatibility path |
| Speaker context | `core/speaker_context.py` | none identified | project-authored session context |

No imported prompt pack or external prompt file was found. Prompt changes are currently tracked only by Git history; a typed/versioned prompt boundary belongs in the later contract work.

## Asset inventory

| Asset | SHA-256 | Origin evidence | State |
|---|---|---|---|
| `public/icon-192.png` | `7f5cda349164f4f4ab966ebc6249bfaff13680d7ada233545beaff178775d7bc` | project-created in `b32dd17`; no embedded metadata | used by manifest |
| `public/icon-512.png` | `a09baded4838240da4787a0844396e62fe66a2e0a53c8d5b40df47a765d811ac` | project-created in `b32dd17`; no embedded metadata | used by manifest |

The unreferenced `hikari-hero.png` and five create-next-app-style SVG template assets were removed. They had no product caller, and retaining them would preserve unnecessary provenance and trademark questions.
The unknown-origin favicon was also removed; Next metadata now uses the verified project-created 192px icon for browser and Apple icon links.

No fonts are tracked. The frontend README mentions Geist through `next/font`, but the current source has no confirmed local font asset; verify actual build output before claiming or shipping it.

## Native platform inventory

The standard library and native macOS commands avoid additional package provenance but still create capability and platform obligations. Current code invokes `osascript`, `open`, `say`, `pbcopy`, `pbpaste`, `pmset`, `screencapture`, `memory_pressure`, `df`, `uptime`, and `launchctl`. These are platform dependencies, not redistributable project assets. Their callers require policy, permission, timeout, compatibility, and failure tests.

## WP-001 findings and next actions

1. ~~Remove the unrelated `whisper==1.1.10` distribution and declare the faster-whisper runtime path.~~
2. The macOS arm64/Python 3.12 runtime and development locks are verified by a clean 88-package install and all 891 tests. Maintain them from the 15 direct dependencies plus four tested compatibility constraints; artifact hashes and other platform locks remain open.
3. Make the selected voice backend and model download behavior explicit during initialization.
4. Resolve speaker-model code/model/training-data terms before treating voice identity as release-ready.
5. ~~Remove unused template and hero assets and replace the unknown-origin favicon reference.~~
6. Generate a frontend third-party notice input from the exact lock and review non-permissive/content-license families.
7. Record hosted provider model ids, terms, egress, retention, and disable/rollback behavior in provider-neutral records.
8. Keep the project license undecided until these items are resolved and the owner approves it.

WP-001 is complete as an inventory: every current source category has an evidence location and unresolved items are explicit. It does not approve the unresolved dependencies, models, assets, providers, or a final project license.
