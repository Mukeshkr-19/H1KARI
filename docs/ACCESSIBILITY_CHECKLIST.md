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

## Phase 2 voice-companion and document-workflow

### Automated Checks

- Verify that the microphone button exposes appropriate `aria-label` and `aria-pressed` or `aria-live` state transitions for visible microphone inactive/listening/stopped states.
- Verify that both interim and final captions containers use semantic HTML elements and that captions not relying only on color are enforced.
- Verify that touch-target sizing meets or exceeds 44x44 CSS pixels for all interactive voice and document controls.
- Verify that global CSS includes proper focus visibility (such as `:focus-visible` outline styles).
- Verify that CSS animations and transitions respect reduced-motion behavior.

### Manual Checks

- **Microphone States**: Manually verify the visible microphone inactive/listening/stopped states change correctly when toggled or updated.
- **No Indefinite Listening**: Confirm there is no indefinite “listening” state after disconnect or failure (e.g., simulating a network disconnect or STT provider timeout).
- **Permission Denial**: Manually trigger browser permission denial for the microphone, and verify that understandable error text is displayed and focus is managed.
- **Captions Legibility**: Verify clear interim and final captions are readable, display in a high-contrast format, and do not overlap.
- **Captions Visual Design**: Ensure captions not relying only on color use text styles or descriptive labels to differentiate speaker/state transitions.
- **Fallback Support**: Confirm keyboard fallback after voice failure behaves correctly, letting users type and navigate without interruption.
- **Keyboard-only Document Actions**: Perform keyboard-only document prepare, confirm, cancel, and follow-up actions using only Tab, Space, and Enter keys.
- **Explicit Confirmation Wording**: Verify explicit confirmation wording clearly describes the action before final document or provider egress.
- **Confirmation Focus Placement**: Confirm confirmation focus placement is set onto the primary action or confirmation dialog when activated.
- **Cancellation Feedback**: Verify cancellation feedback is announced to the user and the task state is cleanly updated.
- **State Preservation**: Ensure preserved task state after recoverable failure remains intact, allowing the user to retry without losing input.
- **Screen-Reader Announcements**: Verify screen-reader announcement expectations are met (e.g., VoiceOver on macOS reads new live messages, status, and error states).
- **VoiceOver Manual Check**: Perform VoiceOver manual checks for the entire voice session and document submission workflow.
- **Zoom Verification**: Test at 200% browser zoom to ensure no controls are hidden, text is not clipped, and the layout remains responsive.
- **Reduced Motion**: Verify reduced-motion behavior by ensuring all sound/waveform animations are stopped when preferences are set to reduce motion.
