"""
HIKARI v2.0 - Research Agent
Web search, news, world awareness, real-time information
"""

import os
import json
import time
import hashlib
import feedparser
import requests
from typing import TYPE_CHECKING, Optional, Dict, Any, List
from datetime import datetime, timedelta

from agents.base import BaseAgent
from dotenv import load_dotenv

if TYPE_CHECKING:
    from core.brain import HikariBrain

load_dotenv()

NEWS_API_KEY = os.getenv("NEWS_API_KEY")


class ResearchAgent(BaseAgent):
    """Handles web search, news, and world awareness"""

    def __init__(self, memory=None, *, eager_legacy_brain: bool = False):
        super().__init__("research", "Web search, news, and real-time information")
        self._memory_override = memory
        self._legacy_brain_allowed = bool(eager_legacy_brain)
        self._brain: Optional["HikariBrain"] = None
        if self._legacy_brain_allowed:
            from core.brain import HikariBrain

            self._brain = HikariBrain()
        self._news_cache = {}
        self._cache_time = 0
        self._cache_ttl = 600  # 10 minutes

        self.register_tool("search_web", self.search_web)
        self.register_tool("get_news", self.get_news)
        self.register_tool("get_weather", self.get_weather)
        self.register_tool("get_time", self.get_time)
        self.register_tool("get_date", self.get_date)

    def _get_brain(self) -> "HikariBrain":
        if not self._legacy_brain_allowed:
            raise RuntimeError(
                "legacy HikariBrain is not available while Brain v2 policy is enabled"
            )
        if self._brain is None:
            from core.brain import HikariBrain

            self._brain = HikariBrain()
        return self._brain

    def _defer_to_brain_v2_personal_recall(self, user_input: str) -> bool:
        """Orchestrator owns personal recall; research must not load legacy brain to screen."""
        if self._legacy_brain_allowed:
            brain = self._get_brain()
            return bool(
                brain.is_personal_memory_question(user_input)
                or brain.has_local_person_match(user_input)
            )
        from core.brain_v2.recall_intent import should_skip_external_research

        return should_skip_external_research(user_input)

    def handle(self, user_input: str, context: str = "") -> Optional[str]:
        lowered = user_input.lower()

        if self._defer_to_brain_v2_personal_recall(user_input):
            return None

        if "news" in lowered or "headlines" in lowered:
            return self.get_news()
        if "weather" in lowered or "how hot" in lowered or "how cold" in lowered:
            location = self._resolve_weather_location(lowered)
            return self.get_weather(location)
        # Only respond to time if NOT part of another command (like "open facetime")
        if (
            "time" in lowered
            and "what" in lowered
            or lowered.strip() == "time"
            or "the time" in lowered
        ):
            return self.get_time()
        if "date" in lowered or "today" in lowered or "day" in lowered:
            return self.get_date()
        if any(
            w in lowered
            for w in ["search", "find", "look up", "what is", "who is", "tell me about"]
        ):
            query = self._extract_query(lowered)
            return self.search_web(query)

        return None

    def can_handle(self, user_input: str) -> float:
        if self._defer_to_brain_v2_personal_recall(user_input):
            return 0.05
        lowered = user_input.lower()
        if any(
            w in lowered
            for w in [
                "news",
                "weather",
                "time",
                "date",
                "search",
                "find",
                "what is",
                "who is",
                "today",
            ]
        ):
            return 0.85
        return 0.2

    def search_web(self, query: str) -> str:
        """Search the web for information"""
        if not query:
            return "What would you like me to search for?"

        # Use DuckDuckGo instant answer API (free, no key needed)
        try:
            url = f"https://api.duckduckgo.com/?q={query}&format=json"
            response = requests.get(url, timeout=10)
            data = response.json()

            if data.get("Abstract"):
                return f"{data['Abstract']}\n\nSource: {data.get('AbstractURL', 'Unknown')}"
            elif data.get("RelatedTopics"):
                topics = data["RelatedTopics"][:3]
                results = [t.get("Text", "") for t in topics if t.get("Text")]
                if results:
                    return "Here's what I found:\n" + "\n".join(
                        f"- {r}" for r in results
                    )

            return f"I searched for '{query}' but didn't find instant results. Try asking me something more specific."
        except Exception as e:
            return f"Search failed: {str(e)}"

    def get_news(self, category: str = "general") -> str:
        """Get latest news headlines"""
        now = time.time()
        cache_key = f"news_{category}"

        if cache_key in self._news_cache and now - self._cache_time < self._cache_ttl:
            return self._news_cache[cache_key]

        try:
            # Use RSS feeds for news (free, no API key needed)
            feeds = {
                "general": "https://feeds.bbci.co.uk/news/rss.xml",
                "tech": "https://feeds.bbci.co.uk/news/technology/rss.xml",
                "science": "https://feeds.bbci.co.uk/news/science_and_environment/rss.xml",
                "business": "https://feeds.bbci.co.uk/news/business/rss.xml",
            }

            feed_url = feeds.get(category, feeds["general"])
            feed = feedparser.parse(feed_url)

            if not feed.entries:
                return "Couldn't fetch news right now. Try again later."

            headlines = []
            for entry in feed.entries[:8]:
                title = entry.get("title", "")
                headlines.append(f"- {title}")

            result = f"Here are the latest {category} headlines:\n" + "\n".join(
                headlines
            )
            self._news_cache[cache_key] = result
            self._cache_time = now
            return result

        except Exception as e:
            return f"News fetch failed: {str(e)}"

    def get_weather(self, location: str = "") -> str:
        """Get weather information"""
        api_key = os.getenv("WEATHER_API_KEY")
        if not api_key:
            return (
                "Weather API key not configured. Set WEATHER_API_KEY in your local environment file."
            )

        if not location:
            return "Which city would you like weather for?"

        try:
            params = {"q": location, "appid": api_key, "units": "metric"}
            response = requests.get(
                "http://api.openweathermap.org/data/2.5/weather",
                params=params,
                timeout=10,
            )
            data = response.json()

            if data.get("cod") == 200:
                city = data["name"]
                temp_c = round(data["main"]["temp"], 1)
                temp_f = round((temp_c * 9 / 5) + 32, 1)
                desc = data["weather"][0]["description"]
                humidity = data["main"]["humidity"]
                wind = data["wind"]["speed"]

                return (
                    f"Weather in {city}: {desc}, {temp_c}°C ({temp_f}°F), "
                    f"humidity {humidity}%, wind {wind} m/s"
                )
            else:
                return f"Couldn't find weather for {location}."
        except Exception:
            return f"Weather service is unavailable right now for {location}."

    def get_time(self) -> str:
        """Get current time"""
        now = datetime.now()
        return f"The time is {now.strftime('%I:%M %p').lstrip('0')}"

    def get_date(self) -> str:
        """Get current date"""
        now = datetime.now()
        return f"Today is {now.strftime('%A, %B %d, %Y')}"

    def get_morning_briefing(self) -> str:
        """Generate a morning briefing with weather, news, and schedule"""
        parts = []

        # Greeting
        hour = datetime.now().hour
        if hour < 12:
            parts.append("Good morning! Here's your briefing:")
        elif hour < 17:
            parts.append("Good afternoon! Here's your update:")
        else:
            parts.append("Good evening! Here's your update:")

        # Date
        parts.append(self.get_date())

        # News
        news = self.get_news()
        parts.append("\n" + news)

        return "\n".join(parts)

    def _extract_location(self, text: str) -> str:
        """Extract explicit city from weather query."""
        for prefix in (
            "weather in",
            "weather for",
            "temperature in",
            "forecast for",
            "forecast in",
        ):
            if prefix in text:
                loc = text.split(prefix, 1)[-1].strip()
                return loc.rstrip("?.! ").split(" and ")[0].strip()
        return ""

    def _resolve_weather_location(self, text: str) -> str:
        explicit = self._extract_location(text)
        if explicit:
            try:
                from core.brain_v2.location_phrases import (
                    is_meta_or_deferred_location_phrase,
                    is_valid_place_name,
                )

                if is_meta_or_deferred_location_phrase(
                    explicit
                ) or not is_valid_place_name(explicit):
                    explicit = ""
            except ImportError:
                pass
        if explicit:
            return explicit
        deferred_phrases = (
            "outside",
            "here",
            "right now",
            "weather now",
            "how hot",
            "how cold",
            "city im in",
            "city i'm in",
            "city i am in",
            "where i am",
            "where i'm",
            "the city",
            "this city",
            "same city",
        )
        if any(phrase in text for phrase in deferred_phrases):
            try:
                from core.brain_v2.session_context import get_session_current_place

                session_place = get_session_current_place()
                if session_place:
                    return session_place
            except ImportError:
                pass
            if self._legacy_brain_allowed:
                return self._get_brain().get_current_location() or ""
        return ""

    def _extract_query(self, text: str) -> str:
        """Extract search query from text"""
        for prefix in [
            "search for",
            "find",
            "look up",
            "what is",
            "who is",
            "tell me about",
        ]:
            if prefix in text:
                return text.split(prefix)[-1].strip()
        return text
