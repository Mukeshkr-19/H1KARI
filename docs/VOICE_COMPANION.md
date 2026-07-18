# Voice companion overlay

The voice companion is a **UI and voice-experience layer** only. It does not replace Brain v2, procedural memory, or episode storage.

**Off by default.** Enable explicitly on the server and in the frontend build:

| Surface | Variable | Value |
|--------|----------|--------|
| WebSocket server | `HIKARI_VOICE_COMPANION` | `1` |
| Next.js frontend | `NEXT_PUBLIC_HIKARI_VOICE_COMPANION` | `1` |

When disabled, voice text still reaches the orchestrator via `type: "voice"` or typed `type: "message"`, but the server emits **no** `companion_update` events and the overlay/settings UI stay hidden.

**Voice-only (this phase):** the companion overlay, captions, and `companion_update` WebSocket lifecycle run **only for voice turns** (`type: "voice"`). Typed chat (`type: "message"`) continues to receive normal `response` payloads with **no** companion events and **no** overlay activation.

## What it does

- Shows a small on-screen companion during **active voice interaction** (listening, thinking, speaking, or error). The current **speaking** state presents the assistant caption in the overlay only; it does not yet guarantee audible TTS output.
- Reflects session state: hidden, idle, listening, thinking, speaking, or error (idle/hidden = overlay off).
- Displays **live, ephemeral captions** near the companion (role, text, final/interim flag, timestamp). Captions are **not persisted** by the companion layer.
- Streams state over the existing WebSocket server as `companion_update` events. Caption text in those events is capped at **500 characters**; the normal `response` channel always carries the **full** orchestrator text.

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

There is **no** arbitrary pet upload, custom pet generation, or user-created pet assets.

## Privacy

- **UI preferences only** (companion type and presentation) may be stored in the browser and, when connected, synced to local HIKARI server/private config (`HIKARI_COMPANION_PREFS_PATH` on the server).
- **Captions and conversation text are not** stored in companion preference files, public repo files, or static assets.
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
- Starting a new voice session clears any prior caption before listening.

## State machine

Normal voice turns follow the transition matrix starting from `hidden` or `idle` through `listening`. **Operational** transitions bypass the matrix only for lifecycle resets such as `hide()`. Rejected transitions never emit companion payloads.

## Phase 2 planned work

The following Phase 2 voice features are planned but not yet available in this build:

- Audible text-to-speech (TTS) during the `speaking` state.
- Voice control for the Phase 1 document flow (prepare, confirm, follow-up,
  cancel, and reconnect without a keyboard).

## Intentionally later

- Rich sprite/3D assets and advanced lip-sync.
- Dedicated frontend unit test runner in CI (requires `npm install` in `hikari-frontend`).
- Multi-device companion sync beyond per-connection WebSocket state.
