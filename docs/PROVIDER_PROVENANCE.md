# Provider and External-Service Provenance

Status: Phase 0 data-flow and disable-path record

Reviewed: 2026-07-14

`core/router.py` is the text-generation provider configuration authority.
`core/vision/cloud.py` separately owns explicit Phase 4 image-analysis gateway
configuration. A provider is available only when its named environment key
exists; cloud vision additionally requires an exact `*_VISION_MODEL`. Removing a
key disables that hosted provider without changing memory or task storage.

## Model providers

| Provider | Configured model identity | Data sent | Retention/training evidence | Disable and rollback |
|---|---|---|---|---|
| OmniRoute local gateway | text aliases plus optional exact `OMNIROUTE_VISION_MODEL` | text requests use the text router; one bounded image and fixed instruction pass through loopback and then upstream only after explicit Cloud Vision acknowledgement | [OmniRoute](https://github.com/diegosouzapw/OmniRoute) is local-first routing software; upstream provider retention, training, quotas, and terms still apply to every routed request | unset `OMNIROUTE_VISION_MODEL` to disable image egress, or unset `OMNIROUTE_API_KEY`/stop the gateway to disable all use; only loopback HTTP `/v1` is accepted |
| 9Router local gateway | text combo `free-forever` plus optional exact `NINEROUTER_VISION_MODEL` combo | text requests use the text router; one bounded image and fixed instruction pass through loopback and then upstream only after explicit Cloud Vision acknowledgement | [9Router](https://github.com/decolua/9router) is local routing software; upstream provider retention, training, quotas, and terms still apply; the configured combo must independently guarantee image input | unset `NINEROUTER_VISION_MODEL` to disable image egress, or unset `NINEROUTER_API_KEY`/stop the gateway to disable all use; only loopback HTTP `/v1` is accepted |
| Google Gemini Developer API | `gemini-2.5-flash` | system instructions, context, user text, generation settings | [paid-service data controls and limited abuse logging](https://ai.google.dev/gemini-api/docs/zdr); free and paid terms differ | unset `GOOGLE_AI_STUDIO_KEY`; select another configured provider |
| GroqCloud | `llama-3.3-70b-versatile` | message history and generation settings | [inference is not retained by default; reliability/abuse logs may be retained up to 30 days; ZDR is available](https://console.groq.com/docs/your-data) | unset `GROQ_API_KEY` |
| OpenRouter | `meta-llama/llama-3.3-70b-instruct:free`, `deepseek/deepseek-r1:free` | message history and generation settings, then onward to a selected upstream | [prompt logging is opt-in, metadata is retained, and upstream policies vary](https://openrouter.ai/docs/guides/privacy/data-collection); H1KARI does not currently request per-call ZDR | unset `OPENROUTER_API_KEY`; do not send private content until a later policy wrapper enforces endpoint constraints |
| Cerebras Inference | `llama-3.1-8b` | message history and generation settings | [Cerebras privacy policy](https://cerebras.ai/privacy-policy/) is the reviewed provider source; no H1KARI-specific contractual retention setting is assumed | unset `CEREBRAS_API_KEY`; treat sensitive-content use as unsupported in Phase 0 |
| NVIDIA API Catalog | `meta/llama-3.3-70b-instruct` | message history and generation settings | provider/model terms depend on the selected API Catalog endpoint; no endpoint-specific retention commitment is encoded by H1KARI | unset `NVIDIA_API_KEY`; treat sensitive-content use as unsupported in Phase 0 |
| Cohere SaaS API | `command-r7b-12-2024` | system preamble, user text, generation settings | [default SaaS logging is generally 30 days; approved ZDR accounts are not logged](https://cohere.com/enterprise-data-commitments) | unset `COHERE_API_KEY`; the legacy v1 endpoint remains a compatibility path, not a release recommendation |
| Ollama local API | `gemma4:e4b`, `gemma4:31b` | messages remain on the local Ollama service | [local prompts are not visible to Ollama](https://ollama.com/privacy); each downloaded model tag still carries its own upstream model license | stop Ollama or remove the local model; absent models fail without cloud credential escalation |

Configured model names describe the current source, not a promise of future
provider availability. Model/provider upgrades require a new provenance review.

## Research and utility services

| Service | Data sent | Credential | Failure/disable path |
|---|---|---|---|
| DuckDuckGo Instant Answer API | user search query | none | network failure returns a bounded unavailable result; remove or disable research use |
| BBC RSS | no conversation content; fixed public feed request | none | feed failure returns no stories |
| OpenWeather | requested location and API key over HTTPS | `WEATHER_API_KEY` | unset the key; errors redact provider URL and credential data |
| Google Speech Recognition | captured audio only when Google speech is explicitly selected or a Google-only service entrypoint is launched | library-managed service access | choose a local backend or text mode; initialization and voice status disclose the egress |
| Browser Web Speech API | captured audio when the browser microphone button is activated | none (browser-controlled) | locality, provider, retention, and training behavior are browser/vendor-controlled and cannot be guaranteed by H1KARI; do not start voice capture or use text-only mode to avoid audio egress |
| Browser SpeechSynthesis API | spoken reply text when the user enables Speak responses | none (browser-controlled) | locality, provider, retention, and training behavior are browser/vendor-controlled and cannot be guaranteed by H1KARI; leave Speak responses off (default) to avoid spoken-output egress; captions and text remain available |

## Phase 0 policy

- Hosted providers are optional and receive no authority over local actions.
- Provider output is untrusted data.
- Private memory, files, audio, and identity data must not be routed to a provider
  whose applicable retention and training controls have not been selected by the
  user.
- Provider-neutral grants and runtime enforcement belong to Phase 1; Phase 0 does
  not claim that documentation alone enforces those future controls.
