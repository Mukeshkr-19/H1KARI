"""CLI safety: casual chat must not trigger system/mac actions."""

import os
import sys
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestCommandIntent(unittest.TestCase):
    def test_call_me_baby_not_phone(self):
        from core.command_intent import (
            is_casual_call_me,
            is_phone_call_command,
            system_agent_confidence,
        )

        text = "call me baby"
        self.assertTrue(is_casual_call_me(text))
        self.assertFalse(is_phone_call_command(text))
        self.assertLess(system_agent_confidence(text), 0.2)

    def test_call_mom_is_phone(self):
        from core.command_intent import is_phone_call_command, system_agent_confidence

        text = "call mom"
        self.assertTrue(is_phone_call_command(text))
        self.assertGreater(system_agent_confidence(text), 0.8)

    def test_open_safari_is_system(self):
        from core.command_intent import is_explicit_system_command

        self.assertTrue(is_explicit_system_command("open Safari"))


class TestMemoryAgentSafety(unittest.TestCase):
    def test_can_handle_call_me_baby_low(self):
        from agents.memory_agent import MemoryAgent
        from core.memory import MemorySystem

        agent = MemoryAgent(MemorySystem())
        self.assertLess(agent.can_handle("call me baby"), 0.2)


class TestSpeakerContext(unittest.TestCase):
    def test_guest_does_not_equal_primary(self):
        from core.speaker_context import SpeakerContext

        ctx = SpeakerContext(primary_user="Alex")
        ctx.update_from_input("I am Maya, Alex's sister.")
        self.assertEqual(ctx.current_speaker, "Maya")
        self.assertTrue(ctx.is_guest_speaker())

    def test_prompt_warns_primary_separate(self):
        from core.speaker_context import SpeakerContext

        ctx = SpeakerContext(primary_user="Alex")
        ctx.update_from_input("I am Maya.")
        self.assertIn("primary user", ctx.prompt_context().lower())

    def test_session_speaker_intro_marks_temporary(self):
        from core.speaker_context import SpeakerContext, is_temporary_speaker_intro

        ctx = SpeakerContext(primary_user="Owner A")
        self.assertTrue(is_temporary_speaker_intro("I am Guest B talking to you now"))
        ctx.update_from_input("I am Guest B talking to you now")
        self.assertEqual(ctx.current_speaker, "Guest B")
        self.assertTrue(ctx.last_was_session_intro)
        self.assertTrue(ctx.is_guest_speaker())

    def test_speaker_reset_clears_guest(self):
        from core.speaker_context import SpeakerContext

        ctx = SpeakerContext(primary_user="Owner A")
        ctx.update_from_input("I am Guest B talking to you now")
        ctx.update_from_input("I'm just testing")
        self.assertIsNone(ctx.current_speaker)
        self.assertFalse(ctx.is_guest_speaker())

    def test_speaker_reset_clears_contact_context(self):
        from core.speaker_context import SpeakerContext

        ctx = SpeakerContext(primary_user="Owner A")
        ctx.note_contact_discussed("partner")
        ctx.note_family_relation("sister")
        ctx.update_from_input("I am Guest B talking to you now")
        self.assertIsNone(ctx.last_contact_kind)
        self.assertIsNone(ctx.last_family_relation)
        ctx.note_contact_discussed("family", "brother")
        ctx.update_from_input("I'm just testing")
        self.assertIsNone(ctx.last_contact_kind)
        self.assertIsNone(ctx.last_family_relation)

    def test_back_to_owner_restores_primary(self):
        from core.speaker_context import SpeakerContext

        ctx = SpeakerContext(primary_user="Owner A")
        ctx.update_from_input("I am Guest B talking to you now")
        ctx.update_from_input("back to owner")
        self.assertEqual(ctx.current_speaker, "Owner A")
        self.assertFalse(ctx.is_guest_speaker())


class TestRouterQuiet(unittest.TestCase):
    def test_router_errors_hidden_when_quiet(self):
        from core.router import AIRouter

        os.environ["HIKARI_QUIET"] = "1"
        os.environ.pop("HIKARI_VERBOSE", None)
        router = AIRouter()
        buf = StringIO()
        with patch("sys.stdout", buf):
            router.generate("hello there")
        self.assertNotIn("[ROUTER]", buf.getvalue())


class TestHikariIdentity(unittest.TestCase):
    def test_self_identity_answer_is_local(self):
        from core.orchestrator import HIKARI_Orchestrator

        orch = HIKARI_Orchestrator.__new__(HIKARI_Orchestrator)

        for text in ("whats ur name", "what is your name?", "who are you"):
            self.assertEqual(orch._handle_special_commands(text), "I'm HIKARI.")

        self.assertEqual(orch._handle_special_commands("whats ur nmae"), "I'm HIKARI.")


class TestAiRuntimeStatus(unittest.TestCase):
    def test_model_and_provider_answers_are_local(self):
        from core.orchestrator import HIKARI_Orchestrator

        class FakeRouter:
            def get_routing_display(self):
                return {
                    "provider": "google",
                    "model": "gemini-2.5-flash",
                    "fallback_labels": [
                        "groq/llama-3.3-70b-versatile",
                        "cerebras/llama-3.1-8b",
                    ],
                    "last_provider": None,
                    "last_model": None,
                }

        orch = HIKARI_Orchestrator.__new__(HIKARI_Orchestrator)
        orch.router = FakeRouter()

        for text in (
            "which model are u using",
            "what model u r using",
            "which model are usin",
            "which provider",
            "provider ?",
            "You: provider ?",
            "fallbacks",
        ):
            text = orch._normalize_user_input_text(text)
            answer = orch._handle_special_commands(text)
            self.assertIn("Build:", answer)
            self.assertIn("Provider: google", answer)
            self.assertIn("Model: gemini-2.5-flash", answer)
            self.assertIn("groq/llama-3.3-70b-versatile", answer)

    def test_missing_provider_failure_is_actionable_and_content_free(self):
        from core.orchestrator import HIKARI_Orchestrator

        class FakeRouter:
            def get_routing_display(self):
                return {"provider": "ollama"}

        orch = HIKARI_Orchestrator.__new__(HIKARI_Orchestrator)
        orch.router = FakeRouter()

        answer = orch._get_ai_unavailable_message()
        self.assertIn("No AI provider is available", answer)
        self.assertIn("OmniRoute", answer)
        self.assertIn("9Router", answer)
        self.assertNotIn("trouble thinking", answer)

    def test_configured_provider_failure_does_not_reflect_raw_errors(self):
        from core.orchestrator import HIKARI_Orchestrator

        class FakeRouter:
            def get_routing_display(self):
                return {"provider": "omniroute"}

        orch = HIKARI_Orchestrator.__new__(HIKARI_Orchestrator)
        orch.router = FakeRouter()

        answer = orch._get_ai_unavailable_message()
        self.assertEqual(
            answer,
            "The configured AI provider is temporarily unavailable. Check the "
            "provider or local gateway and try again.",
        )


class TestUserSummaryCommand(unittest.TestCase):
    def test_what_do_u_know_about_me_is_local_summary(self):
        from core.orchestrator import HIKARI_Orchestrator

        class FakeBrain:
            def summarize_user(self):
                return "What I know about you:\n- Name: Alex"

        orch = HIKARI_Orchestrator.__new__(HIKARI_Orchestrator)
        orch.brain = FakeBrain()
        orch.brain_v2_enabled = False
        orch.brain_v2 = None

        self.assertEqual(
            orch._handle_special_commands("what do u know about me give me full information"),
            "What I know about you:\n- Name: Alex",
        )


if __name__ == "__main__":
    unittest.main()
