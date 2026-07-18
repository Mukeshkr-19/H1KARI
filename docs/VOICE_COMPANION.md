# Voice companion overlay

The voice companion is a **UI and voice-experience layer** only. It does not replace Brain v2, procedural memory, or episode storage.

**Off by default.** Enable explicitly on the server and in the frontend build:

| Surface | Variable | Value |
|--------|----------|--------|
| WebSocket server | `HIKARI_VOICE_COMPANION` | `1` |
| Next.js frontend | `NEXT_PUBLIC_HIKARI_VOICE_COMPANION` | `1` |

When disabled, voice text still reaches the orchestrator via `type: "voice"` or typed `type: "message"`, but the server emits **no** `companion_update` events and the overlay/settings UI stay hidden.

**Voice-only (companion overlay):** the companion overlay, captions, and `companion_update` WebSocket lifecycle run **only for voice turns** (`type: "voice"`). Typed chat (`type: "message"`) continues to receive normal `response` payloads with **no** companion events and **no** overlay activation.

## What it does

- Shows a small on-screen companion during **active voice interaction** (listening, thinking, speaking, or error).
- Reflects session state: hidden, idle, listening, thinking, speaking, or error (idle/hidden = overlay off).
- Displays **live, ephemeral captions** near the companion (role, text, final/interim flag, timestamp). Captions are **not persisted** by the companion layer and remain available whether spoken output is on or off.
- Optional **Speak responses** preference (default **off**) uses the browser `SpeechSynthesis` API for audible replies during an active voice session, and for document explanations that belong to a document task started by voice. Ordinary typed chat is never spoken.
- Streams state over the existing WebSocket server as `companion_update` events. Caption text in those events is capped at **500 characters**; the normal `response` channel always carries the **full** orchestrator text.

## Spoken output (opt-in)

| Item | Behavior |
|------|----------|
| Default | Speak responses is **off** |
| Persistence | Only the boolean preference and a bounded speech-rate value are stored in browser UI prefs |
| Not persisted | Spoken text, captions, transcripts, document paths, responses |
| Engine | Browser `SpeechSynthesis` (vendor-controlled) |
| Locality | Browser/vendor processing and retention **cannot be guaranteed** by H1KARI |
| When speech runs | Preference on **and** (active voice-session reply **or** voice-started document explanation) |
| Failure | Bounded generic message; captions/text continue; document task and explanation state are preserved |
| Stop | Settings/overlay **Stop speaking**, voice command `stop speaking`, mic cancel, new voice turn, disconnect, unmount, or synthesis failure |
| Repeat | Voice command `repeat response` uses only the latest bounded in-memory voice response (cleared on disconnect/unmount; never localStorage) |
| Slower | Voice command `speak slower` reduces rate within **0.7–1.4** (step 0.1) |

Speech-control commands are handled locally during an active voice capture and are **not** sent as normal chat.

## Voice document controls

Deterministic voice commands can drive the existing document flow without inventing path/provider values:

- Prepare / review: `prepare document <path> with provider <name> [fallback <name>]`
- Confirm only while pending: `confirm document` or `explain this document` (bare `yes` is rejected)
- Cancel for an active document task: `cancel document`
- Follow-up with durable task context: `document follow-up [task <id>]: <question>`

Unmatched speech continues on the ordinary voice/chat path. Document-control transcripts are not added to chat history.

## Voice turn wire order

For a voice text turn (after optional `{ type: "voice", listening: true }`):

1. `companion_update` — `listening` (only when entering listening from hidden/idle; repeated listening signals in one capture do not emit duplicate listening events)
2. `companion_update` — `thinking` with user caption
3. `companion_update` — `speaking` with assistant caption (truncated to 500 chars in payload)
4. `response` — full orchestrator text (uncapped)
5. `companion_update` — `idle`

## Allowed choices

| Setting | Allowed values |
|--------|----------------|
| Companion type | `cat`, `dog`, `bird` |
| Presentation | `male`, `female`, `non-binary` |
| Speak responses | `off` (default), `on` |
| Speech rate | `0.7`–`1.4` inclusive |

There is **no** arbitrary pet upload, custom pet generation, or user-created pet assets.

## Privacy

- **UI preferences only** (companion type, presentation, speak-responses boolean, speech rate) may be stored in the browser. Companion type and presentation may sync to local HIKARI server/private config (`HIKARI_COMPANION_PREFS_PATH`) when connected. Speak-responses and speech rate stay in the browser preference blob only.
- **Captions, spoken text, and conversation text are not** stored in companion preference files, public repo files, or static assets.
- Companion WebSocket events are **not** coupled to Brain v2 internals; orchestrator `process_input` may still record memory under existing HIKARI rules for the same user text.
- Use generic demo strings in tests and docs only.

## WebSocket events

- **Outbound (voice only):** `companion_update` with `companion.state`, optional `companion.caption`, optional `companion.preferences`.
- **Outbound (all chat):** `response` with full assistant text.
- **Inbound:** `companion_preferences` with `companion_type` and `presentation` (validated; invalid values return `companion_preferences_error`).

## Frontend

- Components: `hikari-frontend/src/components/VoiceCompanionOverlay.tsx`, `CompanionSettings.tsx`
- Helpers: `hikari-frontend/src/utils/companion/` (tracked; not under repo-root `lib/` ignore)
- Overlay visibility: only while an explicit **voice session** is active (`voiceSessionActive`) — never during typed chat, even if the header orb shows loading/thinking.
- During capture, the header microphone remains operable as **Stop listening**. It is disabled while a submitted voice turn is awaiting a response, and `startListening()` still rejects re-entry so only one `SpeechRecognition` instance exists.
- When the companion UI is enabled, browser interim recognition results appear as bounded local captions. Only a complete final transcript is submitted to the server.
- On server `companion_update` idle/hidden, or after speech-recognition error (bounded reset), the overlay hides and voice-only caption state clears.
- Starting a new voice session cancels any prior spoken output and clears any prior caption before listening.

## State machine

Normal voice turns follow the transition matrix starting from `hidden` or `idle` through `listening`. **Operational** transitions bypass the matrix only for lifecycle resets such as `hide()`. Rejected transitions never emit companion payloads.

## Intentionally later

- Rich sprite/3D assets and advanced lip-sync.
- Multi-device companion sync beyond per-connection WebSocket state.
