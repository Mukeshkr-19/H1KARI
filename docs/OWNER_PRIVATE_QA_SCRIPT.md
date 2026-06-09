# Owner private QA script (copy-paste)

No more hunting `--brain-v2-pending` for normal facts. HIKARI **asks you** before saving.

## How memory works now

| You say | HIKARI does |
|---------|-------------|
| `Remember this: ...` | Saves to **long-term memory** immediately (no ask) |
| `I am in <CITY>` | **Session only** (trip city - no ask) |
| Any other personal fact | **Asks:** save in long-term memory or session only? |
| `save in memory` / `save it` | Saves the last fact to long-term memory |
| `session only` | Keeps last fact for this chat only |

Voice answers are natural - no `From reviewed memory:` prefix.

---

## A. Teach with ask flow (recommended)

| # | You say | Expect HIKARI |
|---|---------|---------------|
| A1 | `My real name is <LEGAL> but you can call me <PREFERRED>` | Asks save vs session |
| A2 | `save in memory` | Saved in long-term memory |
| A3 | `what is my name?` | `<PREFERRED>` naturally |
| A4 | `I am a rising senior and I will be graduating in May 2027` | Asks save vs session |
| A5 | `save in memory` | Saved (not pending mystery) |
| A6 | `when do I graduate?` | May 2027 / rising senior |

**Fast path (skip ask):** `Remember this: I live in <HOME_CITY>`

---

## B. Session-only path

| # | You say | Expect |
|---|---------|--------|
| B1 | `I prefer cold coffee` | Asks save vs session |
| B2 | `session only` | Okay - this session only |
| B3 | `what do I prefer?` | cold coffee (this session) |
| B4 | *(new chat / back to owner)* | May not recall - that is correct for session-only |

---

## C. Trip city (auto session)

| # | You say | Expect |
|---|---------|--------|
| C1 | `I am in <TRIP_CITY>` | Session location for this chat - no ask |
| C2 | `where am I?` | You're in `<TRIP_CITY>` for this session |
| C3 | `where do I live?` | `<HOME_CITY>` from long-term memory |

---

## D. Guest mode

| # | You say | Expect |
|---|---------|--------|
| D1 | `i am <GUEST> talking to you` | Short guest mode hi |
| D2 | `i am your owners sister` | Not stored in owner memory |
| D3 | `back to owner` | Back to you, `<PREFERRED>` |
| D4 | `did my sister talk to you?` | Yes - guest visited |

---

## E. Must NOT happen

- Guest intro stored as owner pending memory
- Robotic `From reviewed memory:` in voice/chat
- Silent auto-save without ask (unless `Remember this:` or trip city)

---

## Paste-back for Codex

```
Commit: <git rev-parse --short HEAD>
A1-A6: PASS/FAIL
B1-B4: PASS/FAIL
C1-C3: PASS/FAIL
D1-D4: PASS/FAIL
E: PASS/FAIL
Notes:
```
