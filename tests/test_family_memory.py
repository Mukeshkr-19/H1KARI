"""Family memory: store, recall, routing priority."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


class _FakeMemory:
    def __init__(self):
        self.facts = {}
        self.conversations = []

    def store_fact(self, key, value):
        self.facts[key] = {"value": value}

    def get_fact(self, key, default=None):
        f = self.facts.get(key)
        if f:
            return f.get("value", default)
        return default

    def search_conversations(self, query):
        q = query.lower()
        return [
            c
            for c in self.conversations
            if q in c.get("user", "").lower() or q in c.get("ai", "").lower()
        ]

    def add_conversation(self, user_input, ai_response, context=""):
        self.conversations.append({"user": user_input, "ai": ai_response})


class TestFamilyMemory(unittest.TestCase):
    def setUp(self):
        from core.memory import MemorySystem

        self.mem = _FakeMemory()

    def test_sister_extraction_and_recall(self):
        from core.family_memory import ingest_family_statement, answer_family_question

        text = (
            "okay maya is my sister and she is studing cs in "
            "north city university okay remember this"
        )
        rel = ingest_family_statement(text, self.mem)
        self.assertEqual(rel, "sister")

        ans = answer_family_question("who is my sister?", self.mem)
        self.assertIsNotNone(ans)
        self.assertIn("Maya", ans)
        self.assertNotIn("Juliana Hatfield", ans)

    def test_full_name_update(self):
        from core.family_memory import ingest_family_statement, answer_family_question

        ingest_family_statement("maya is my sister", self.mem)
        ingest_family_statement(
            "her full name is Mayabeth",
            self.mem,
            last_relation="sister",
        )
        ans = answer_family_question("my sister name?", self.mem)
        self.assertIn("Mayabeth", ans)

    def test_study_recall(self):
        from core.family_memory import ingest_family_statement, answer_family_question

        ingest_family_statement(
            "maya is my sister and she is studying cs in north city university",
            self.mem,
        )
        ans = answer_family_question("where does my sister study?", self.mem)
        self.assertIsNotNone(ans)
        self.assertIn("North City", ans)

    def test_do_u_know_named_primary_sister(self):
        from core.family_memory import ingest_family_statement, answer_household_memory

        ingest_family_statement("maya is my sister", self.mem)
        ingest_family_statement(
            "her full name is Mayabeth",
            self.mem,
            last_relation="sister",
        )
        ans = answer_household_memory(
            "do u know alex's sister",
            self.mem,
            primary_user="Alex",
        )
        self.assertIsNotNone(ans)
        self.assertIn("Mayabeth", ans)
        self.assertNotIn("don't have", ans.lower())

    def test_do_u_know_my_sister(self):
        from core.family_memory import ingest_family_statement, answer_household_memory

        ingest_family_statement("maya is my sister", self.mem)
        ingest_family_statement(
            "her full name is Mayabeth",
            self.mem,
            last_relation="sister",
        )
        ans = answer_household_memory("do u know my sister ?", self.mem)
        self.assertIsNotNone(ans)
        self.assertTrue(ans.lower().startswith("yes"))
        self.assertIn("Mayabeth", ans)

    def test_research_skips_do_u_know_sister(self):
        from agents.research import ResearchAgent

        agent = ResearchAgent(eager_legacy_brain=True)
        self.assertLess(agent.can_handle("do u know alex's sister"), 0.15)
        self.assertLess(agent.can_handle("who is alex sister"), 0.15)
        self.assertGreater(agent.can_handle("who is ada lovelace"), 0.7)

    def test_who_is_named_primary_sister_skips_web(self):
        from core.family_memory import ingest_family_statement, answer_household_memory
        from agents.research import ResearchAgent

        ingest_family_statement(
            "maya is my sister",
            self.mem,
        )
        ingest_family_statement(
            "her full name is Mayabeth",
            self.mem,
            last_relation="sister",
        )
        ans = answer_household_memory(
            "who is alex sister",
            self.mem,
            primary_user="Alex",
        )
        self.assertIsNotNone(ans)
        self.assertIn("sister", ans.lower())

        agent = ResearchAgent(eager_legacy_brain=True)
        self.assertLess(agent.can_handle("who is alex sister"), 0.15)

    def test_who_is_saved_relative_fuzzy_typo(self):
        from core.family_memory import ingest_family_statement, answer_household_memory

        ingest_family_statement("maya is my sister", self.mem)
        ingest_family_statement(
            "her full name is Mayabeth",
            self.mem,
            last_relation="sister",
        )
        ans = answer_household_memory("who is mayabth ?", self.mem)
        self.assertIsNotNone(ans)
        self.assertTrue("Maya" in ans or "Mayabeth" in ans)

    def test_family_question_does_not_mutate_name(self):
        from core.family_memory import ingest_family_statement, answer_family_question

        ingest_family_statement("maya is my sister", self.mem)
        rel = ingest_family_statement("who is my sister?", self.mem)
        self.assertIsNone(rel)

        ans = answer_family_question("my sister name?", self.mem)
        self.assertIn("Maya", ans)
        self.assertNotIn("Who", ans)

    def test_research_agent_skips_family(self):
        from agents.research import ResearchAgent

        agent = ResearchAgent(eager_legacy_brain=True)
        self.assertLess(agent.can_handle("who is my sister?"), 0.2)

    def test_memory_agent_prioritizes_family(self):
        from agents.memory_agent import MemoryAgent
        from core.memory import MemorySystem

        agent = MemoryAgent(MemorySystem())
        self.assertGreater(agent.can_handle("who is my sister?"), 0.9)

    def test_name_containing_mad_not_angry_emotion(self):
        from core.personality import get_emotional_iq

        eq = get_emotional_iq()
        emotions = eq.detect_emotion("madeline is my sister")
        self.assertLess(emotions.get("angry", 0), 0.5)

    def test_orchestrator_confirms_pronoun_family_update(self):
        from core.brain import HikariBrain
        from core.orchestrator import Orchestrator
        from tests.test_brain_memory import FakeNeural

        orch = Orchestrator()
        orch.memory = _FakeMemory()
        orch.agents["memory"].memory = orch.memory
        orch.neural_memory_enabled = False
        orch.legacy_memory_enabled = True
        orch.brain_v2_enabled = False
        orch.brain_v2 = None
        orch._brain_v2_session = None
        orch.brain = HikariBrain(FakeNeural())

        first = orch.process_input(
            "maya is my sister and she is studying cs in north city university remember this"
        )
        self.assertIn("remember your sister Maya", first)

        update = orch.process_input("her full name is Mayabeth")
        self.assertIn("remember your sister Mayabeth", update)

        answer = orch.process_input("my sister name?")
        self.assertTrue("Mayabeth" in answer or "Maya" in answer)
        rec = orch.memory.get_fact("family:sister")
        self.assertEqual(rec.get("full_name"), "Mayabeth")

    def test_user_profile_fact_dedupe_handles_dict_facts(self):
        from core.user_profile import UserProfile

        profile = UserProfile()
        profile._save = lambda: None
        profile.profile = {
            "facts": {
                "personal": [
                    {
                        "fact": "NCU is North City University thats where i study",
                        "learned_at": "test",
                        "confidence": 1.0,
                    }
                ]
            }
        }

        profile.extract_info_from_conversation(
            "where do i study ? where do i live ?",
            "",
        )

        facts = profile.get_facts("personal")
        self.assertTrue(all(isinstance(f, str) for f in facts))


if __name__ == "__main__":
    unittest.main()
