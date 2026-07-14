"""System agent Spotify search must not duplicate clipboard restore."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from agents.system import SystemAgent


class TestSystemAgentMusicFlow(unittest.TestCase):
    @patch("agents.system.osascript_disabled", return_value=False)
    @patch("agents.system.subprocess.run")
    @patch("agents.system.subprocess.Popen")
    @patch("agents.system.time.sleep", return_value=None)
    def test_play_search_reports_once(self, _sleep, popen, run, _disabled):
        popen.return_value = MagicMock()
        run.return_value = MagicMock(returncode=0, stdout="")
        agent = SystemAgent()
        result = agent.control_music("play test song")

        self.assertEqual(result, "Searching Spotify for 'test song'...")
        self.assertEqual(popen.call_count, 2)
        self.assertEqual(popen.return_value.communicate.call_count, 2)
