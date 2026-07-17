# HIKARI Provenance Inventory

Status: WP-001 inventory and Phase 0 remediation complete
Baseline: Phase 0 closure candidate based on `develop` at `9efc2cf`
Reviewed: 2026-07-14

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
| Agents and project-authored actions | `agents/`, action modules under `core/` except the adapted paths listed below | network and macOS side effects | project code; Phase 0 policy interface is `core/action_policy.py` |
| JARVIS-adapted planning and actions | `core/task_planner.py`, `core/action_system.py`, `core/desktop_awareness.py`, `core/mac_integration.py` | planning, desktop observation, and macOS side effects | portions adapted from `ethanplusai/jarvis` at reviewed revision `df3044fcf238c8e270c2ecd32302cea159435c48`; subject to the upstream personal, educational, and non-commercial license reproduced in `THIRD_PARTY_NOTICES.md`; commercial release is blocked without separate permission or clean-room replacement |
| Voice and speaker identity | `core/voice.py`, `core/speaker_auth.py`, voice services | microphone, local model downloads, macOS speech | project code with third-party model obligations listed below |
| Pairing server | `core/server.py` | local HTTP and WebSocket listener | project code; QR generation is third-party |
| Background services | `services/`, login-agent scripts | microphone, launchd, macOS commands | project code; supported daemon and tray imports are declared |
| Frontend | `hikari-frontend/` | Next.js web client | project code plus locked npm graph and template residue |
| Tests and release checks | `tests/`, `scripts/brain_live_qa.py`, `core/doctor.py` | local-only verification | project code and synthetic fixtures |

There is no root `LICENSE` or `COPYING` file. That absence is intentional: the
owner has not selected a project license. Dependency, model, and service evidence
is recorded in `THIRD_PARTY_NOTICES.md`, `docs/MODEL_PROVENANCE.md`, and
`docs/PROVIDER_PROVENANCE.md` without implying that H1KARI itself is licensed for
redistribution.

## Python dependency inventory

### Direct shipped imports

| Declaration | Shipped use | Local metadata license | Review state |
|---|---|---|---|
| `feedparser>=6.0.0` | news feeds | BSD-2-Clause | direct dependency; exact supported version is in the platform lock |
| `litellm==1.84.0` | provider routing | MIT | direct dependency; exact in manifest and platform lock |
| `numpy>=1.26.0` | audio and voice features | multiple permissive/content notices | direct dependency; exact supported version is in the platform lock |
| `openai==2.30.0` | OpenAI-compatible/provider routing | Apache-2.0 | direct dependency |
| `PyAudio==0.2.14` | microphone audio | MIT | direct dependency; native PortAudio provenance must be recorded by installer/platform |
| `python-dotenv==1.2.2` | local configuration | BSD-3-Clause | direct dependency; exact in manifest and platform lock |
| `qrcode[pil]>=7.4.2` | pairing QR image | BSD family | direct dependency; qrcode and Pillow are exact in the platform lock |
| `requests==2.33.0` | research and provider HTTP | Apache-2.0 | direct dependency; exact in manifest and platform lock |
| `rumps==0.4.0` (macOS) | supported menu-bar mode | BSD-3-Clause | Darwin-only direct dependency; PyObjC Cocoa boundary is exact in the platform lock |
| `SpeechRecognition==3.13.0` | microphone recognition fallback | BSD family | direct dependency; external recognizer behavior must remain explicit |
| `torch>=2.0.0` | speaker identity model | BSD-3-Clause | direct dependency; exact supported version is in the platform lock |
| `speechbrain>=1.0.0` | speaker identity model | Apache-2.0 | direct dependency; exact supported version is in the platform lock |
| `websockets>=12.0` | pairing server | BSD-3-Clause | direct dependency; exact supported version is in the platform lock |
| `openai-whisper>=20231117` | local speech recognition | MIT | direct dependency; distribution name is protected by a regression check |
| `faster-whisper==1.2.1` | optional local speech recognition in daemon services | MIT metadata | direct optional dependency; now explicitly declared |

`cohere` is reached through the HTTP router, not the Cohere Python package.
Environment-specific `pip freeze` output is not a release manifest; the supported
macOS arm64/Python 3.12 graph is the reviewed platform lock.

### Declaration problems

`requirements.txt` has 16 direct runtime dependencies, one of which is Darwin-only.
Resolver-owned transitive packages are represented by the exact supported platform
lock rather than duplicated as direct requirements.

- The unrelated Graphite `whisper==1.1.10` package was removed. HIKARI calls OpenAI Whisper's `load_model`, and a regression check now prevents the ambiguous distribution name from returning.
- `faster-whisper==1.2.1` is now declared for the optional service paths that import it.
- Existing shared environments are not authoritative and may contain stale packages;
  supported installs are rebuilt from the corrected manifests and locks.
- The unused `beautifulsoup4`, `cohere`, `parameterized`, `pyttsx3`, `PyYAML`, `types-requests`, and `wikipedia` declarations were removed and are protected by a regression check.
- Explicit transitive pins were removed from the direct manifest after static import
  review. The macOS arm64/Python 3.12 runtime and development locks are the exact
  reproducible target; other platforms remain unsupported until separately locked.
- The supported tray path now declares `rumps`; its PyObjC Cocoa dependencies are
  recorded in both platform locks.

The unreferenced `services/hikari_always_on.py` prototype was removed. It depended
on undeclared `openwakeword` behavior and a nonexistent custom model artifact. The
supported `--daemon` and `--tray` entrypoints remain.

## Frontend dependency inventory

Direct declarations are Next.js, React, React DOM, ESLint, Tailwind CSS, TypeScript, their type/config packages, and the optional Darwin SWC binary. The lockfile is version 3 and is the exact transitive source for this baseline.

| Finding | Evidence | Disposition |
|---|---|---|
| Direct runtime versions | Next 15.5.18; React and React DOM 19.1.0 in the lock | retain pending frontend build and vulnerability gates |
| Lock license families | 317 MIT, 29 Apache-2.0, 17 ISC, 12 MPL-2.0, 9 LGPL-3.0-or-later, plus BSD, BlueOak, CC, Python-2.0, and 0BSD entries | exact generated input is in `docs/FRONTEND_THIRD_PARTY_INPUT.md`; distribution rules are in `THIRD_PARTY_NOTICES.md` |
| Native image binaries | `sharp` and platform `libvips` packages enter through Next | source release is clear; packaged binaries remain blocked until their exact LGPL notice/source/relinking bundle is reviewed |
| Overrides | `js-yaml`, `postcss`, and `tar` are security-pinned in `package.json` | keep until audit proves the parent graph no longer needs them |
| Reproducibility | exact npm lock and generated third-party input | `npm ci`, lint, build, audit, and notice-input checks are release gates |

Registry license fields are evidence pointers, not a substitute for reviewing the license files shipped in resolved packages.

## Model and provider inventory

| Model or service | Location/use | Bundled? | Provenance and release state |
|---|---|---:|---|
| OpenAI Whisper `base` | `core/voice.py` | no; downloaded at runtime | reviewed hash, MIT evidence, cache, size, egress, and disable behavior are in `docs/MODEL_PROVENANCE.md` |
| faster-whisper `base` | daemon service | no; downloaded at runtime | runtime is pinned to the reviewed model revision; record is in `docs/MODEL_PROVENANCE.md` |
| `speechbrain/spkrec-ecapa-voxceleb` | `core/speaker_auth.py` | no; downloaded at runtime | runtime is pinned to the reviewed revision; license, training-data, biometric, cache, and authorization limits are in `docs/MODEL_PROVENANCE.md` |
| Ollama models | `core/router.py` | no | local tags and per-model upstream-license caveat are in `docs/PROVIDER_PROVENANCE.md` |
| Google, Groq, OpenRouter, Cerebras, NVIDIA, Cohere | `core/router.py` | no | model ids, egress, retention evidence, and disable paths are in `docs/PROVIDER_PROVENANCE.md` |
| DuckDuckGo, BBC feeds, OpenWeather | `agents/research.py` | no | data sent, credential use, HTTPS transport, and bounded failure/disable paths are in `docs/PROVIDER_PROVENANCE.md` |

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

No fonts are tracked or imported by the current frontend source. The starter README
reference to Geist is informational residue, not a shipped font asset.

## Native platform inventory

The standard library and native macOS commands avoid additional package provenance but still create capability and platform obligations. Current code invokes `osascript`, `open`, `say`, `pbcopy`, `pbpaste`, `pmset`, `screencapture`, `memory_pressure`, `df`, `uptime`, and `launchctl`. These are platform dependencies, not redistributable project assets. Their callers require policy, permission, timeout, compatibility, and failure tests.

## WP-001 findings and next actions

1. ~~Remove the unrelated `whisper==1.1.10` distribution and declare every supported optional runtime import.~~
2. ~~Lock and verify the supported macOS arm64/Python 3.12 runtime and development graphs; keep other platforms explicitly unsupported until separately locked.~~
3. ~~Make the selected voice backend and model download behavior explicit during initialization.~~
4. ~~Pin reviewed voice model revisions and document model, training-data, cache, biometric, and redistribution boundaries.~~
5. ~~Remove unused or unknown-origin assets and the dead wake-word prototype.~~
6. ~~Generate the frontend third-party input, publish source-distribution notices, and block prebuilt artifacts pending exact binary notice review.~~
7. ~~Record hosted provider model ids, terms, egress, retention, and disable/rollback behavior; remove the conflicting unused provider config.~~
8. ~~Keep the project license undecided pending owner approval and state that boundary in public governance documents.~~
9. ~~Record the JARVIS-adapted planner, action, desktop-awareness, and macOS-integration portions and reproduce their upstream notice.~~ Commercial release of those portions remains blocked without separate permission or clean-room replacement.

WP-001 remediation is complete for the Phase 0 source release. Every shipped
dependency and asset has a provenance record or exact manifest source, supported
runtime downloads are pinned and disclosed, and unknown distribution rights fail
closed. The adapted JARVIS portions remain limited by their upstream license;
their notice is recorded, but commercial distribution is not cleared. This record
does not select a H1KARI project license or authorize a future binary artifact
without its artifact-specific notice review.
