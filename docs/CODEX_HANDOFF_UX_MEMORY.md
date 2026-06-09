# Codex handoff: memory UX (natural recall + save ask)

## Branch

`brain/ux-natural-recall-guest`

## Summary for owner

1. **No permission hunting** - owner is not sent to `--brain-v2-pending` for normal teaching.
2. **Explicit ask** - personal facts trigger: "Save in long-term memory or session only?"
3. **Short voice replies** - no `From reviewed memory:` prefixes.
4. **Guest mode** - `i am <name> talking to you` works; guest visit recall for owner.
5. **`Remember this:`** - still immediate long-term save (power-user fast path).

## New files

- `core/brain_v2/natural_replies.py`
- `core/brain_v2/memory_save_prompt.py`
- `tests/test_memory_save_prompt.py`
- `tests/test_memory_save_prompt_integration.py`
- `docs/OWNER_PRIVATE_QA_SCRIPT.md`

## Key behavior (fake fixtures only)

```
Owner: I live in City A
HIKARI: Got it - "I live in City A". Should I save that in long-term memory,
        or keep it for this session only? Say "save in memory" or "session only".

Owner: save in memory
HIKARI: Saved in long-term memory.

Owner: Remember this: I prefer Topic A
HIKARI: Got it. I will remember that in Brain v2.   # no ask

Owner: I am in City B
HIKARI: Got it. I will use that as your current location for this session.  # no ask
```

## Verification (all gates must pass before merge)

Every command below must exit **0**. Warnings on stderr are acceptable only when the command still exits 0.

```bash
cd H1KARI
git diff --check
.venv/bin/python -m pytest tests -q
.venv/bin/python hikari.py --doctor
.venv/bin/python hikari.py --brain-v2-eval
.venv/bin/python scripts/brain_live_qa.py
.venv/bin/python tests/privacy_scan.py
```

**Expected:** full pytest pass, doctor exit 0, brain-v2-eval 8/8, live QA ALL PASSED, privacy PASS.

Owner runs `docs/OWNER_PRIVATE_QA_SCRIPT.md` with real private facts locally before merge to `main` (never commit those facts).

## Do not

- Commit owner private names/places in tests or docs.
- Revert the save-vs-session ask - it replaces confusing pending/auto-trust friction.
