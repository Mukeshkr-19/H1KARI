# Frontend Accessibility Checklist

Automated tests protect labels, live regions, named icon buttons, navigation state,
focus visibility, reduced motion, and companion choice semantics. Before a release,
complete this representative manual flow in the production build.

## Keyboard

- Starting at the browser chrome, use Tab and Shift-Tab through Server URL, Pairing
  Code, Connect, microphone, chat input, Send message, quick prompts, primary
  navigation, companion choices, file shortcuts, and Disconnect.
- Confirm every focused control has a visible focus indicator and Enter/Space activates
  buttons exactly once.
- Confirm no focus trap appears after pairing, reconnect, microphone errors, tab
  changes, or disconnect.

## VoiceOver on macOS

- Confirm the pairing page announces the HIKARI heading, Server URL label, Pairing Code
  label, Connect state, and the polite Connecting status without exposing the code.
- After pairing, confirm connection state, Primary navigation current page, conversation
  log additions, HIKARI typing status, received message text, microphone name/state,
  companion caption, and Disconnect are announced in a useful order.
- Confirm decorative orbs, status dots, and SVG icons are skipped.

## Visual and motion

- At 200% browser zoom, complete pairing, send a message, open every primary page, and
  disconnect without horizontal loss of controls or clipped text.
- Enable reduced motion in macOS and confirm pulse, bounce, speaking, and transition
  effects no longer repeat.
- Check text, focus rings, disabled controls, errors, and connection states in both the
  normal and high-contrast display settings available on the test Mac.

## Voice and failure flow

- Confirm the microphone control has a clear accessible name before, during, and after
  capture, and that denial/unavailable/error states do not leave the UI stuck.
- Confirm reconnect, invalid pairing, pairing lockout, unsupported protocol, malformed
  server message, and disconnect states remain operable by keyboard and understandable
  with VoiceOver.

Record browser version, macOS version, assistive settings, result, and any exception in
the release evidence. Do not record pairing codes, conversation text, voice samples, or
other private runtime data.

## Phase 1 document flow

- Select one text document and provider, then confirm that the review screen announces
  the exact immutable document/provider snapshot before any read or provider request.
- Confirm document status and explanation updates are announced through the live region
  without moving keyboard focus.
- While a document task is queued, running, interrupted, or verifying, confirm Cancel is
  visible, keyboard operable, and remains available during a slow provider response.
- Reconnect to a task and ask one follow-up. Confirm stale, malformed, or unrelated task
  events do not replace the active task's state or explanation.
- At 200% zoom, verify the document controls, confirmation details, errors, status,
  explanation, follow-up input, and Cancel control remain visible and operable.
