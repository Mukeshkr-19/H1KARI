"""User profile relationship names must preserve multi-word names."""

from __future__ import annotations

import unittest

from core.user_profile import UserProfile


class TestUserProfileNameHandling(unittest.TestCase):
    def setUp(self):
        self.profile = UserProfile()
        self.profile._save = lambda: None
        self.profile.relationships = {}

    def test_multi_word_spouse_name(self):
        self.profile.extract_info_from_conversation(
            "My wife is Jane Doe",
            "",
        )
        rel = self.profile.find_person("Jane Doe")
        self.assertIsNotNone(rel)
        self.assertEqual(rel["name"], "Jane Doe")
        self.assertEqual(rel["relationship"], "spouse")

    def test_normalized_key_collapses_whitespace(self):
        self.profile.add_relationship("Jane  Doe", "friend")
        self.profile.add_relationship("Jane Doe", "colleague")
        self.assertEqual(len(self.profile.relationships), 1)
        stored = self.profile.relationships["jane doe"]
        self.assertEqual(stored["name"], "Jane Doe")
        self.assertEqual(stored["mention_count"], 2)
        self.assertEqual(stored["relationship"], "colleague")
