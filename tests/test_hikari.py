"""
HIKARI v2.0 - Test Suite
Tests for all core components
"""

import sys
import os
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestAIRouter(unittest.TestCase):
    """Test the multi-provider AI router"""

    def test_router_initialization(self):
        from core.router import AIRouter

        router = AIRouter()
        self.assertIsNotNone(router)
        self.assertIn("google", router.providers)
        self.assertIn("groq", router.providers)
        self.assertIn("openrouter", router.providers)

    def test_task_classification(self):
        from core.router import AIRouter

        router = AIRouter()

        self.assertEqual(router._classify_task("hello"), "greeting")
        self.assertEqual(router._classify_task("what time is it"), "time_date")
        self.assertEqual(router._classify_task("weather in London"), "weather")
        self.assertEqual(router._classify_task("write a python function"), "coding")
        self.assertEqual(router._classify_task("calculate 2+2"), "math")
        self.assertEqual(router._classify_task("explain quantum physics"), "analysis")

    def test_quality_mapping(self):
        from core.router import AIRouter

        router = AIRouter()

        self.assertEqual(router._get_quality_level("greeting"), "fast")
        self.assertEqual(router._get_quality_level("chat"), "balanced")
        self.assertEqual(router._get_quality_level("coding"), "smart")
        self.assertEqual(router._get_quality_level("reasoning"), "smart")

    def test_provider_selection(self):
        from core.router import AIRouter

        router = AIRouter()
        provider = router._select_provider("fast")
        if provider:
            self.assertIn(provider, router.providers)

    def test_empty_input(self):
        from core.router import AIRouter

        router = AIRouter()
        result = router.generate("")
        self.assertIsNone(result)

    def test_usage_stats(self):
        from core.router import AIRouter

        router = AIRouter()
        stats = router.get_usage_stats()
        self.assertIsInstance(stats, dict)

    def test_router_redacts_api_keys_from_logs(self):
        from core.router import _redact_sensitive_text

        text = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            "gemini:generateContent?key=secret-google-key"
        )
        redacted = _redact_sensitive_text(text)
        self.assertNotIn("secret-google-key", redacted)
        self.assertIn("key=[REDACTED]", redacted)


class TestMemorySystem(unittest.TestCase):
    """Test the memory system"""

    def test_memory_initialization(self):
        from core.memory import MemorySystem

        memory = MemorySystem()
        self.assertIsNotNone(memory)

    def test_add_conversation(self):
        from core.memory import MemorySystem

        memory = MemorySystem()
        memory.add_conversation("Hello", "Hi there!")
        conversations = memory.get_recent_conversations()
        self.assertTrue(len(conversations) > 0)

    def test_store_and_retrieve_fact(self):
        from core.memory import MemorySystem

        memory = MemorySystem()
        memory.store_fact("test_key", "test_value")
        self.assertEqual(memory.get_fact("test_key"), "test_value")

    def test_preferences(self):
        from core.memory import MemorySystem

        memory = MemorySystem()
        memory.set_preference("location", "New York")
        self.assertEqual(memory.get_preference("location"), "New York")

    def test_context_building(self):
        from core.memory import MemorySystem

        memory = MemorySystem()
        memory.add_conversation("Q1", "A1")
        memory.add_conversation("Q2", "A2")
        context = memory.get_context_for_prompt(limit=2)
        self.assertIn("Q1", context)
        self.assertIn("A2", context)


class TestSkillSystem(unittest.TestCase):
    """Test the skill system"""

    def test_skill_registry(self):
        from skills.skill_system import SkillRegistry, register_builtin_skills

        registry = SkillRegistry()
        register_builtin_skills(registry)
        self.assertGreater(len(registry.skills), 0)

    def test_calculator_skill(self):
        from skills.skill_system import SkillRegistry, register_builtin_skills

        registry = SkillRegistry()
        register_builtin_skills(registry)

        calc = registry.skills.get("calculator")
        self.assertIsNotNone(calc)
        self.assertGreater(calc.can_handle("calculate 2+2"), 0.5)
        result = calc.execute(expression="2+2")
        self.assertIn("4", result)

    def test_joke_skill(self):
        from skills.skill_system import SkillRegistry, register_builtin_skills

        registry = SkillRegistry()
        register_builtin_skills(registry)

        joke = registry.skills.get("joke")
        self.assertIsNotNone(joke)
        self.assertGreater(joke.can_handle("tell me a joke"), 0.9)
        result = joke.execute()
        self.assertTrue(len(result) > 0)

    def test_timer_skill(self):
        from skills.skill_system import SkillRegistry, register_builtin_skills

        registry = SkillRegistry()
        register_builtin_skills(registry)

        timer = registry.skills.get("timer")
        self.assertIsNotNone(timer)
        self.assertGreater(timer.can_handle("set a timer"), 0.5)


class TestFileAgent(unittest.TestCase):
    """Test the file agent"""

    def test_path_whitelist(self):
        from agents.files import FileAgent

        agent = FileAgent()
        # Should deny access to system directories
        allowed, _ = agent._is_path_allowed("/etc/passwd")
        self.assertFalse(allowed)

    def test_file_info(self):
        import tempfile

        from agents.files import FileAgent

        with tempfile.TemporaryDirectory() as tmp:
            docs = Path(tmp) / "Documents"
            docs.mkdir()
            agent = FileAgent(whitelist=[str(docs)])
            result = agent.file_info(str(docs))
        self.assertIn("Name:", result)


class TestResearchAgent(unittest.TestCase):
    """Test the research agent"""

    def test_time(self):
        from agents.research import ResearchAgent

        agent = ResearchAgent()
        result = agent.get_time()
        self.assertIn("time", result.lower())

    def test_date(self):
        from agents.research import ResearchAgent

        agent = ResearchAgent()
        result = agent.get_date()
        self.assertTrue(len(result) > 0)

    def test_can_handle(self):
        from agents.research import ResearchAgent

        agent = ResearchAgent()
        self.assertGreater(agent.can_handle("what's the weather"), 0.7)
        self.assertGreater(agent.can_handle("latest news"), 0.7)
        self.assertLess(agent.can_handle("write code"), 0.3)


class TestCodenameAuth(unittest.TestCase):
    """Test codename authentication"""

    def test_correct_codename(self):
        from security.auth import CodenameAuth

        auth = CodenameAuth("test-codename")
        self.assertTrue(auth.verify("test-codename"))

    def test_wrong_codename(self):
        from security.auth import CodenameAuth

        auth = CodenameAuth("test-codename")
        self.assertFalse(auth.verify("wrong"))

    def test_lockout(self):
        from security.auth import CodenameAuth

        auth = CodenameAuth("test-codename")
        for _ in range(5):
            auth.verify("wrong")
        self.assertTrue(auth.locked)


class TestScheduler(unittest.TestCase):
    """Test the scheduler"""

    def test_scheduler_init(self):
        from core.scheduler import Scheduler

        scheduler = Scheduler()
        self.assertIsNotNone(scheduler)

    def test_add_task(self):
        from core.scheduler import Scheduler

        scheduler = Scheduler()
        scheduler.add_task("test", 60, lambda: "done")
        self.assertEqual(len(scheduler.tasks), 1)

    def test_task_status(self):
        from core.scheduler import Scheduler

        scheduler = Scheduler()
        scheduler.add_task("test", 60, lambda: "done")
        status = scheduler.get_status()
        self.assertIn("tasks", status)


class TestOrchestrator(unittest.TestCase):
    """Test the orchestrator"""

    def test_orchestrator_init(self):
        from core.orchestrator import Orchestrator

        orch = Orchestrator()
        self.assertIsNotNone(orch)
        self.assertEqual(len(orch.agents), 6)
        self.assertIsNotNone(orch.router)
        self.assertIsNotNone(orch.memory)

    def test_process_input_empty(self):
        from core.orchestrator import Orchestrator

        orch = Orchestrator()
        result = orch.process_input("")
        self.assertIsNone(result)

    def test_process_input_exit(self):
        from core.orchestrator import Orchestrator

        orch = Orchestrator()
        result = orch.process_input("exit")
        self.assertIsNotNone(result)

    def test_process_input_codename(self):
        from core.orchestrator import Orchestrator

        orch = Orchestrator()
        result = orch.process_input("change-me")
        self.assertIsNotNone(result)
        self.assertTrue(orch.authenticated)


class TestDoctor(unittest.TestCase):
    """Test the doctor/status checker."""

    def test_private_match_detection(self):
        from core.doctor import _tracked_private_matches

        from core.path_literals import ENV_FILE

        matches = _tracked_private_matches(
            [
                "README.md",
                ENV_FILE,
                "data/voice_auth.json",
                "docs/WORK_DONE.md",
            ]
        )

        self.assertEqual(matches, [ENV_FILE, "data/voice_auth.json", "docs/WORK_DONE.md"])

    def test_format_checks(self):
        from core.doctor import Check, format_checks

        output = format_checks(
            [
                Check("One", "ok", "good"),
                Check("Two", "warn", "careful"),
            ]
        )

        self.assertIn("[OK] One: good", output)
        self.assertIn("[WARN] Two: careful", output)
        self.assertIn("OK WITH WARNINGS", output)

    def test_collect_quick_checks(self):
        from core.doctor import collect_checks

        checks = collect_checks(full=False)
        names = {check.name for check in checks}

        self.assertIn("Python version", names)
        self.assertIn("Git status", names)
        self.assertIn("Repo root", names)
        self.assertIn("Public Git privacy scan", names)
        self.assertIn("Tracked duplicate scan", names)

    def test_doctor_clean_clone_private_folder_message(self):
        from pathlib import Path
        from unittest.mock import patch

        from core.doctor import _check_private_layout, _check_neural_memory_readonly

        missing = Path("/tmp/h1kari-missing-private-for-test")
        with patch("core.doctor.PRIVATE_ROOT", missing), patch(
            "core.doctor.EXPECTED_BRAIN_TARGET", missing / "live-brain"
        ):
            layout = _check_private_layout()
            neural = _check_neural_memory_readonly()

        self.assertFalse(missing.exists())
        private = next(c for c in layout if c.name == "Private data folder")
        self.assertEqual(private.status, "warn")
        self.assertIn("optional", private.detail.lower())
        self.assertIn("h1kari", private.detail.lower())
        self.assertNotIn("Live brain DB", {c.name for c in layout})
        self.assertEqual(neural.status, "ok")
        self.assertIn("optional", neural.detail.lower())

    def test_install_cli_script_uses_hikari_home(self):
        from pathlib import Path

        script = Path(__file__).resolve().parent.parent / "scripts" / "install-hikari-cli.sh"
        text = script.read_text(encoding="utf-8")
        self.assertIn("HIKARI_HOME", text)
        self.assertIn("REPO_ROOT", text)
        self.assertIn("Darwin", text)

    def test_install_sh_identifies_h1kari_repo(self):
        from pathlib import Path

        script = Path(__file__).resolve().parent.parent / "install.sh"
        text = script.read_text(encoding="utf-8")
        self.assertIn("H1KARI setup", text)
        self.assertIn("hikari / Hikari", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
