"""Persistent owner chat sessions remain private, resumable, and non-authoritative."""

from __future__ import annotations

import os
from pathlib import Path
import threading
from unittest.mock import MagicMock

import pytest

from core.action_policy import Actor, ActorContext
from core.conversation_sessions import (
    ConversationSessionError,
    ConversationSessionStore,
    create_conversation_session_store,
)
from tests.test_conversation_context import _wrapper_orchestrator


OWNER = "local-owner"


def _factory(tmp_path: Path, *, clock=None):
    counter = iter(range(1, 10_000))
    return ConversationSessionStore(
        tmp_path / "private" / "sessions.db",
        session_id_factory=lambda: f"chat_{next(counter):024d}",
        clock=clock or (lambda: 1_700_000_000.0),
    )


def test_import_and_factory_definition_do_not_create_default_runtime(monkeypatch, tmp_path):
    runtime = tmp_path / "runtime"
    monkeypatch.setenv("HIKARI_HOME", str(runtime))

    assert not runtime.exists()
    assert callable(create_conversation_session_store)
    assert not runtime.exists()


def test_store_permissions_and_content_safe_reprs(tmp_path):
    store = _factory(tmp_path)
    record = store.create(owner_id=OWNER, title="Private launch discussion")
    turn = store.append_turn(
        owner_id=OWNER,
        session_id=record.session_id,
        user_text="private transcript marker",
        assistant_text="private response marker",
        source="text",
    )

    db = tmp_path / "private" / "sessions.db"
    assert (os.stat(db.parent).st_mode & 0o777) == 0o700
    assert (os.stat(db).st_mode & 0o777) == 0o600
    assert "Private launch" not in repr(record)
    assert "private transcript" not in repr(turn)
    assert str(db) not in repr(store)


def test_round_trip_autotitles_and_preserves_dialogue(tmp_path):
    store = _factory(tmp_path)
    record = store.create(owner_id=OWNER)
    stored = store.append_turn(
        owner_id=OWNER,
        session_id=record.session_id,
        user_text="Compare the Atlas laptop with the Nova laptop",
        assistant_text="Atlas has better battery life.",
        source="voice",
    )

    loaded = store.load_turns(owner_id=OWNER, session_id=record.session_id)
    refreshed = store.get(owner_id=OWNER, session_id=record.session_id)

    assert loaded == (stored,)
    assert refreshed is not None
    assert refreshed.title == "Compare the Atlas laptop with the Nova laptop"
    assert refreshed.turn_count == 1


@pytest.mark.parametrize(
    "secret",
    (
        "".join(("s", "k", "-", "abcdefghijklmnopqrstuvwxyz123456")),
        "".join(("Bear", "er ", "abcdefghijklmnopqrstuvwxyz.123456")),
        "".join(("api", "_key=", "super-secret-value-12345")),
    ),
)
def test_obvious_credentials_are_not_persisted(tmp_path, secret):
    store = _factory(tmp_path)
    record = store.create(owner_id=OWNER)
    store.append_turn(
        owner_id=OWNER,
        session_id=record.session_id,
        user_text=f"Use {secret} for this request",
        assistant_text="I will not retain that credential.",
        source="text",
    )

    loaded = store.load_turns(owner_id=OWNER, session_id=record.session_id)
    raw_db = (tmp_path / "private" / "sessions.db").read_bytes()
    assert secret not in loaded[0].user_text
    assert secret.encode() not in raw_db
    assert "[private credential omitted]" in loaded[0].user_text


def test_exact_owner_scope_prevents_cross_owner_disclosure_or_mutation(tmp_path):
    store = _factory(tmp_path)
    record = store.create(owner_id=OWNER, title="Owner only")
    store.append_turn(
        owner_id=OWNER,
        session_id=record.session_id,
        user_text="owner content",
        assistant_text="owner response",
        source="text",
    )

    assert store.get(owner_id="other-owner", session_id=record.session_id) is None
    assert store.load_turns(owner_id="other-owner", session_id=record.session_id) == ()
    assert not store.rename(
        owner_id="other-owner", session_id=record.session_id, title="stolen"
    )
    assert not store.archive(owner_id="other-owner", session_id=record.session_id)
    assert not store.delete(owner_id="other-owner", session_id=record.session_id)
    assert store.get(owner_id=OWNER, session_id=record.session_id).title == "Owner only"


def test_archive_blocks_append_until_explicit_restore(tmp_path):
    store = _factory(tmp_path)
    record = store.create(owner_id=OWNER)
    assert store.archive(owner_id=OWNER, session_id=record.session_id)

    with pytest.raises(ConversationSessionError, match="conversation not found"):
        store.append_turn(
            owner_id=OWNER,
            session_id=record.session_id,
            user_text="new input",
            assistant_text="new output",
            source="text",
        )

    assert store.latest(owner_id=OWNER) is None
    assert store.unarchive(owner_id=OWNER, session_id=record.session_id)
    assert store.append_turn(
        owner_id=OWNER,
        session_id=record.session_id,
        user_text="restored input",
        assistant_text="restored output",
        source="text",
    ).sequence == 1


def test_delete_cascades_transcript_and_is_idempotent(tmp_path):
    store = _factory(tmp_path)
    record = store.create(owner_id=OWNER)
    store.append_turn(
        owner_id=OWNER,
        session_id=record.session_id,
        user_text="delete me",
        assistant_text="deleted",
        source="text",
    )

    assert store.delete(owner_id=OWNER, session_id=record.session_id)
    assert not store.delete(owner_id=OWNER, session_id=record.session_id)
    assert store.get(owner_id=OWNER, session_id=record.session_id) is None
    assert store.load_turns(owner_id=OWNER, session_id=record.session_id) == ()


def test_concurrent_appends_receive_unique_monotonic_sequences(tmp_path):
    store = _factory(tmp_path)
    record = store.create(owner_id=OWNER)
    errors = []

    def append(index):
        try:
            store.append_turn(
                owner_id=OWNER,
                session_id=record.session_id,
                user_text=f"input {index}",
                assistant_text=f"output {index}",
                source="text",
            )
        except Exception as exc:  # pragma: no cover - asserted below
            errors.append(type(exc).__name__)

    threads = [threading.Thread(target=append, args=(index,)) for index in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    loaded = store.load_turns(owner_id=OWNER, session_id=record.session_id)
    assert errors == []
    assert [turn.sequence for turn in loaded] == list(range(1, 21))


def test_listing_is_bounded_ordered_and_archive_aware(tmp_path):
    ticks = iter((1.0, 2.0, 3.0, 4.0, 5.0, 6.0))
    store = _factory(tmp_path, clock=lambda: next(ticks))
    first = store.create(owner_id=OWNER, title="First")
    second = store.create(owner_id=OWNER, title="Second")
    assert store.archive(owner_id=OWNER, session_id=first.session_id)

    assert [item.title for item in store.list_sessions(owner_id=OWNER)] == ["Second"]
    assert [item.title for item in store.list_sessions(
        owner_id=OWNER, include_archived=True
    )] == ["First", "Second"]


def test_orchestrator_restores_old_session_and_persists_text_and_voice(tmp_path):
    store = _factory(tmp_path)
    first = store.create(owner_id=OWNER, title="Atlas planning")
    store.append_turn(
        owner_id=OWNER,
        session_id=first.session_id,
        user_text="Atlas has the better battery.",
        assistant_text="I will use that comparison.",
        source="text",
    )
    second = store.create(owner_id=OWNER, title="Other chat")
    orch = _wrapper_orchestrator()
    observed = []

    def core_reply(text, source="text", context=None):
        observed.append((context.session_id, orch._conversation_packet(text)))
        return f"reply to {text}"

    orch._process_input_core = core_reply
    assert orch.configure_conversation_session(store, first.session_id) == 1
    orch.process_input("Which one had the better battery?", source="voice")
    assert "Atlas has the better battery" in observed[0][1].messages[0]["content"]
    assert store.get(owner_id=OWNER, session_id=first.session_id).turn_count == 2

    assert orch.configure_conversation_session(store, second.session_id) == 0
    orch.process_input("What were we discussing?", source="text")
    assert observed[1][1].messages == ()
    assert store.get(owner_id=OWNER, session_id=second.session_id).turn_count == 1


def test_remote_and_guest_turns_never_enter_owner_session_store(tmp_path):
    store = _factory(tmp_path)
    record = store.create(owner_id=OWNER)
    orch = _wrapper_orchestrator()
    orch._process_input_core = MagicMock(return_value="bounded response")
    orch.configure_conversation_session(store, record.session_id)

    guest = ActorContext("remote-guest", Actor.GUEST, record.session_id, "device")
    orch.process_input("guest private text", source="device", context=guest)

    assert store.load_turns(owner_id=OWNER, session_id=record.session_id) == ()
    orch._record_brain_v2_turn.assert_not_called()


def test_full_transcript_outlives_bounded_context_restore(tmp_path):
    store = _factory(tmp_path)
    record = store.create(owner_id=OWNER)
    for index in range(800):
        if index == 5:
            user = "Compare the Vega One telescope with Vega Two."
            assistant = "Vega One has the wider field of view."
        else:
            user = f"historical question {index}"
            assistant = f"historical answer {index}"
        store.append_turn(
            owner_id=OWNER,
            session_id=record.session_id,
            user_text=user,
            assistant_text=assistant,
            source="text",
        )

    assert store.get(owner_id=OWNER, session_id=record.session_id).turn_count == 800
    assert len(store.load_turns(owner_id=OWNER, session_id=record.session_id)) == 768
    orch = _wrapper_orchestrator()
    assert orch.configure_conversation_session(store, record.session_id) == 768
    packet = orch.conversation_context.compose(
        orch._conversation_scope(orch._default_local_owner_context()),
        "What was historical question 799?",
    )
    assert len(packet.messages) <= 24
    assert sum(len(item["content"]) for item in packet.messages) <= 24_000

    observed = []

    def core_reply(text, source="text", context=None):
        observed.append(orch._conversation_packet(text))
        return "Vega One was the earlier choice."

    orch._process_input_core = core_reply
    orch._record_brain_v2_turn = MagicMock()
    orch.process_input("Which telescope had the wider field of view?")
    combined = "\n".join(item["content"] for item in observed[0].messages)
    assert "Vega One has the wider field of view" in combined


def test_session_source_has_no_network_subprocess_or_brain_dependency():
    source = Path("core/conversation_sessions.py").read_text(encoding="utf-8")
    forbidden = (
        "requests",
        "urllib",
        "httpx",
        "subprocess",
        "socket",
        "core.brain",
        "provider",
    )
    for token in forbidden:
        assert token not in source
