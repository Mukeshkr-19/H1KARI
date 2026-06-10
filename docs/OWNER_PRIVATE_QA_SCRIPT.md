# Owner private QA script (copy-paste)

Use your real names/places locally only. Never commit these facts to the repo.

## How memory works

| You say | HIKARI does |
|---------|-------------|
| Owner facts (name, home, school, relations, preferences, etc.) | **Auto-saves** locally — quiet "Got it" (no ask) |
| `Remember this: ...` | **Auto-saves** immediately |
| `I am in <TRIP_CITY>` | **Session only** (trip city, not home) |
| Third-party / sensitive facts | **Queued for review** (no save-vs-session ask) |
| Guest intro (`i am X talking to you`) | Guest mode - not owner memory |

Voice answers are natural - no `From reviewed memory:` prefix.

---

## A. Core facts (should NOT ask)

| # | You say | Expect |
|---|---------|--------|
| A1 | `My real name is <LEGAL> but you can call me <PREFERRED>` | Got it - legal + preferred saved (no ask) |
| A2 | `what is my real name?` | `<LEGAL>` |
| A3 | `what is my name?` | `<PREFERRED>` |
| A4 | `I live in <HOME_CITY>` | Saved (no ask) |
| A5 | `where do I live?` | `<HOME_CITY>` naturally |
| A6 | `I am doing my bachelors in <MAJOR> at <SCHOOL>` | Saved (no ask) |
| A7 | `I am a rising senior and I will be graduating in May 2027` | Saved (no ask) |

---

## B. Session vs long-term

| # | You say | Expect |
|---|---------|--------|
| B1 | `I am in <TRIP_CITY>` | Session location (no ask) |
| B2 | `where am I?` | `<TRIP_CITY>` for this session |
| B3 | `where do I live?` | Still `<HOME_CITY>` |

---

## C. Guest mode

| # | You say | Expect |
|---|---------|--------|
| C1 | `i am <GUEST> talking to you` | Short guest hi |
| C2 | `back to owner` | Back to you, `<PREFERRED>` |
| C3 | `did my sister talk to you?` | Guest visited (if she said she is your sister) |

---

## D. Must NOT happen

- "Save in memory or session only?" on any owner fact
- Legal name includes "But You Can Call"
- Guest intro in pending queue as owner memory

---

## Paste-back for Codex

```
A1-A7: PASS/FAIL
B1-B3: PASS/FAIL
C1-C3: PASS/FAIL
D: PASS/FAIL
Notes:
```
