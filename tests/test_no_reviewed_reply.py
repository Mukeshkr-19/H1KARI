"""Helpful no-reviewed-memory replies."""

from __future__ import annotations

from core.brain_v2.no_reviewed_reply import format_no_reviewed_memory_reply
from core.brain_v2.recall_intent import (
    INTENT_EDUCATION,
    INTENT_IDENTITY_SELF,
    is_brain_v2_no_reviewed_memory_answer,
)


def test_identity_no_memory_is_helpful():
    reply = format_no_reviewed_memory_reply("what is my name?", INTENT_IDENTITY_SELF)
    assert "reviewed memory" in reply.lower()
    assert "owner a" in reply.lower()
    assert is_brain_v2_no_reviewed_memory_answer(reply)


def test_education_no_memory_is_helpful():
    reply = format_no_reviewed_memory_reply("what do I study?", INTENT_EDUCATION)
    assert "education" in reply.lower()
    assert "school a" in reply.lower()
    assert is_brain_v2_no_reviewed_memory_answer(reply)


def test_legal_name_no_memory_is_helpful():
    reply = format_no_reviewed_memory_reply(
        "what is my legal name?", INTENT_IDENTITY_SELF
    )
    assert "legal name" in reply.lower()
    assert "person b" in reply.lower()
