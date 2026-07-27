# Owner private QA script (copy-paste)

Use your real names/places locally only. Never commit these facts to the repo.

## How memory works

| You say | HIKARI does |
|---------|-------------|
| Owner facts without remember (name, home, school, relations, preferences, etc.) | **Episode only** — notes the turn; does **not** durable-save or queue for review |
| `Remember this: ...` / `Remember this in my brain: ...` / `Save this as a memory: ...` | **Durable save** when safe (auto-accept); otherwise review queue |
| Standalone `Remember this in my brain.` after a recent fact | Saves that prior fact (anaphoric) |
| `I am in <TRIP_CITY>` | **Session only** (trip city, not home) |
| `Remember this:` + third-party / sensitive facts | **Queued for review** |
| Guest intro (`i am X talking to you`) | Guest mode - not owner memory |

Voice answers are natural - no `From reviewed memory:` prefix.

---

## A. Core facts (use Remember this)

| # | You say | Expect |
|---|---------|--------|
| A1 | `Remember this: My real name is <LEGAL> but you can call me <PREFERRED>` | Got it - legal + preferred saved |
| A2 | `what is my real name?` | `<LEGAL>` |
| A3 | `what is my name?` | `<PREFERRED>` |
| A4 | `Remember this: I live in <HOME_CITY>` | Saved |
| A5 | `where do I live?` | `<HOME_CITY>` naturally |
| A6 | `Remember this: I am doing my bachelors in <MAJOR> at <SCHOOL>` | Saved |
| A7 | `Remember this: I am a rising senior and I will be graduating in May 2027` | Saved |

---

## B. Session vs long-term

| # | You say | Expect |
|---|---------|--------|
| B1 | `I am in <TRIP_CITY>` | Session location (no ask) |
| B2 | `where am I?` | `<TRIP_CITY>` for this session |
| B3 | `where do I live?` | Still `<HOME_CITY>` (after A4) |

---

## C. Guest mode

| # | You say | Expect |
|---|---------|--------|
| C1 | `i am <GUEST> talking to you` | Short guest hi |
| C2 | `back to owner` | Back to you, `<PREFERRED>` |
| C3 | `did my sister talk to you?` | Guest visited (if she said she is your sister) |

---

## D. Must NOT happen

- Durable save of casual owner facts without an explicit remember/save command
- "Save in memory or session only?" on any owner fact
- Legal name includes "But You Can Call"
- Guest intro in pending queue as owner memory

---

## Private QA result template

```
A1-A7: PASS/FAIL
B1-B3: PASS/FAIL
C1-C3: PASS/FAIL
D: PASS/FAIL
Notes:
```
