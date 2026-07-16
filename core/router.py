"""
HIKARI v2.0 - Multi-Provider AI Router
Routes requests across Google, Groq, OpenRouter, Cerebras, DeepSeek, NVIDIA
Smart fallback chain with usage tracking
"""

import os
import time
import random
import hashlib
import logging
import contextlib
import io
import re
from typing import Optional, Dict, Any, Tuple, Callable, Sequence
from dataclasses import dataclass, field
from collections import defaultdict
from dotenv import load_dotenv

from core.quiet import is_quiet

if is_quiet():
    for logger_name in ("LiteLLM", "litellm"):
        logging.getLogger(logger_name).setLevel(logging.ERROR)
        logging.getLogger(logger_name).disabled = True

try:
    import litellm

    if is_quiet():
        litellm.suppress_debug_info = True
        litellm.set_verbose = False
    LITELLM_AVAILABLE = True
except ImportError:
    LITELLM_AVAILABLE = False

try:
    import openai

    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False

import requests

load_dotenv()


def _router_log(*args, **kwargs) -> None:
    if not is_quiet():
        safe_args = tuple(_redact_sensitive_text(arg) for arg in args)
        print(*safe_args, **kwargs)


def _redact_sensitive_text(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    text = re.sub(r"([?&]key=)[^&\s)]+", r"\1[REDACTED]", value)
    text = re.sub(
        r"(Authorization['\"]?\s*[:=]\s*['\"]?Bearer\s+)[^'\"\s,)}]+",
        r"\1[REDACTED]",
        text,
        flags=re.I,
    )
    return text


# Provider configurations
PROVIDER_CONFIGS = {
    "google": {
        "api_key_env": "GOOGLE_AI_STUDIO_KEY",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "models": {
            "fast": "gemini-2.5-flash",
            "balanced": "gemini-2.5-flash",
            "smart": "gemini-2.5-flash",
        },
        "rate_limit_rpm": 15,
        "max_tokens_per_min": 250000,
        "use_direct_api": True,  # Use direct REST API instead of LiteLLM
    },
    "groq": {
        "api_key_env": "GROQ_API_KEY",
        "base_url": "https://api.groq.com/openai/v1",
        "models": {
            "fast": "llama-3.3-70b-versatile",
            "balanced": "llama-3.3-70b-versatile",
            "smart": "llama-3.3-70b-versatile",
        },
        "rate_limit_rpm": 30,
        "max_tokens_per_min": 12000,
    },
    "openrouter": {
        "api_key_env": "OPENROUTER_API_KEY",
        "base_url": "https://openrouter.ai/api/v1",
        "models": {
            "fast": "meta-llama/llama-3.3-70b-instruct:free",
            "balanced": "meta-llama/llama-3.3-70b-instruct:free",
            "smart": "deepseek/deepseek-r1:free",
        },
        "rate_limit_rpm": 20,
        "max_tokens_per_min": 50000,
    },
    "cerebras": {
        "api_key_env": "CEREBRAS_API_KEY",
        "base_url": "https://api.cerebras.ai/v1",
        "models": {
            "fast": "llama-3.1-8b",
            "balanced": "llama-3.1-8b",
            "smart": "llama-3.1-8b",
        },
        "rate_limit_rpm": 30,
        "max_tokens_per_min": 60000,
    },
    "nvidia": {
        "api_key_env": "NVIDIA_API_KEY",
        "base_url": "https://integrate.api.nvidia.com/v1",
        "models": {
            "fast": "meta/llama-3.3-70b-instruct",
            "balanced": "meta/llama-3.3-70b-instruct",
            "smart": "meta/llama-3.3-70b-instruct",
        },
        "rate_limit_rpm": 40,
        "max_tokens_per_min": 80000,
    },
    "cohere": {
        "api_key_env": "COHERE_API_KEY",
        "base_url": None,
        "models": {
            "fast": "command-r7b-12-2024",
            "balanced": "command-r7b-12-2024",
            "smart": "command-r7b-12-2024",
        },
        "rate_limit_rpm": 20,
        "max_tokens_per_min": 40000,
    },
    "ollama": {
        "api_key_env": None,
        "base_url": "http://localhost:11434",
        "models": {
            "fast": "gemma4:e4b",
            "balanced": "gemma4:e4b",
            "smart": "gemma4:31b",
        },
        "rate_limit_rpm": 99999,
        "max_tokens_per_min": 999999,
        "local": True,
    },
}

# Task classification to model quality mapping
TASK_QUALITY_MAP = {
    "greeting": "fast",
    "quick_fact": "fast",
    "time_date": "fast",
    "weather": "fast",
    "chat": "balanced",
    "general_qa": "balanced",
    "coding": "smart",
    "reasoning": "smart",
    "math": "smart",
    "analysis": "smart",
    "file_analysis": "smart",
    "research": "smart",
}

# Fallback chain order - OLLAMA IS LAST RESORT (local free fallback only when ALL others fail)
FALLBACK_CHAIN = [
    "groq",  # Fastest
    "cerebras",  # Fast
    "google",  # Best quality
    "openrouter",  # Many models
    "nvidia",  # NVIDIA API
    "cohere",  # Command R
    "ollama",  # LAST - local Gemma 4 fallback when everything else fails
]


@dataclass
class ProviderStatus:
    name: str
    available: bool = False
    requests_today: int = 0
    tokens_today: int = 0
    last_error: Optional[str] = None
    last_request_time: float = 0
    consecutive_failures: int = 0
    cooldown_until: float = 0


@dataclass(frozen=True)
class GenerationResult:
    text: Optional[str]
    provider: Optional[str]
    model: Optional[str]
    attempted_providers: Tuple[str, ...]


class AIRouter:
    """Multi-provider AI router with smart fallback and usage tracking"""

    def __init__(self):
        self.providers: Dict[str, ProviderStatus] = {}
        self.usage_stats: Dict[str, Dict[str, int]] = defaultdict(
            lambda: {"requests": 0, "tokens": 0, "failures": 0}
        )
        self._last_provider: Optional[str] = None
        self._last_model: Optional[str] = None
        self._init_providers()

    def _init_providers(self):
        for name in FALLBACK_CHAIN:
            config = PROVIDER_CONFIGS[name]
            api_key = (
                os.getenv(config["api_key_env"]) if config.get("api_key_env") else None
            )

            # Ollama is always available (local)
            is_ollama = name == "ollama"

            self.providers[name] = ProviderStatus(
                name=name,
                available=bool(api_key) or is_ollama,
            )
            if is_ollama:
                _router_log(f"[ROUTER] {name}: local (always available)")
            elif api_key:
                _router_log(f"[ROUTER] {name}: configured")
            else:
                _router_log(f"[ROUTER] {name}: not configured (skipping)")

    def _get_api_key(self, provider: str) -> Optional[str]:
        config = PROVIDER_CONFIGS[provider]
        # Ollama doesn't need an API key
        if config.get("local"):
            return "local"
        return os.getenv(config["api_key_env"])

    def _classify_task(self, user_input: str) -> str:
        lower = user_input.lower()
        if any(
            w in lower
            for w in [
                "hi",
                "hello",
                "hey",
                "good morning",
                "good evening",
                "good night",
            ]
        ):
            return "greeting"
        if any(w in lower for w in ["time", "date", "day", "today"]):
            return "time_date"
        if any(w in lower for w in ["weather", "temperature", "rain", "sunny"]):
            return "weather"
        if any(
            w in lower
            for w in [
                "code",
                "function",
                "debug",
                "python",
                "javascript",
                "program",
                "script",
            ]
        ):
            return "coding"
        if any(
            w in lower
            for w in ["analyze", "compare", "evaluate", "why", "how does", "explain"]
        ):
            return "analysis"
        if any(
            w in lower for w in ["calculate", "math", "solve", "equation", "formula"]
        ):
            return "math"
        if any(w in lower for w in ["think", "reason", "logic", "argue", "prove"]):
            return "reasoning"
        if any(
            w in lower for w in ["file", "document", "read", "summarize", "search in"]
        ):
            return "file_analysis"
        if any(w in lower for w in ["search", "news", "find", "look up", "what is"]):
            return "research"
        return "general_qa"

    def _get_quality_level(self, task_type: str) -> str:
        return TASK_QUALITY_MAP.get(task_type, "balanced")

    def _select_provider(self, quality: str) -> Optional[str]:
        """Select best available provider for given quality level"""
        candidates = []
        for name in FALLBACK_CHAIN:
            status = self.providers[name]
            if not status.available:
                continue
            if status.consecutive_failures >= 3:
                continue
            if time.time() < status.cooldown_until:
                continue
            candidates.append(name)

        if not candidates:
            return None

        # Prioritize fast cloud providers - ollama is only for fallback
        if quality == "fast":
            priority = ["groq", "cerebras", "google", "openrouter"]
        elif quality == "balanced":
            priority = ["google", "groq", "openrouter", "cerebras"]
        else:  # smart
            priority = ["google", "openrouter", "nvidia", "cohere"]

        for p in priority:
            if p in candidates:
                return p

        # If all priority providers fail, return None (will trigger fallback chain)
        return None

    def _build_messages(
        self, system_prompt: str, user_input: str, context: str = ""
    ) -> list:
        messages = [{"role": "system", "content": system_prompt}]
        if context:
            messages.append(
                {
                    "role": "system",
                    "content": f"Context from previous conversation:\n{context}",
                }
            )
        messages.append({"role": "user", "content": user_input})
        return messages

    def _call_litellm(
        self,
        provider: str,
        model: str,
        messages: list,
        max_tokens: int = 500,
        temperature: float = 0.7,
    ) -> Optional[str]:
        if not LITELLM_AVAILABLE:
            return None

        config = PROVIDER_CONFIGS[provider]
        api_key = self._get_api_key(provider)

        try:
            # For Google, use direct REST API (LiteLLM has credential issues)
            if provider == "google":
                return self._call_google_direct(
                    model, messages, max_tokens, temperature, api_key
                )

            if is_quiet():
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    response = litellm.completion(
                        model=f"{provider}/{model}",
                        messages=messages,
                        max_tokens=max_tokens,
                        temperature=temperature,
                        api_key=api_key,
                        api_base=config.get("base_url"),
                        timeout=30,
                    )
            else:
                response = litellm.completion(
                    model=f"{provider}/{model}",
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    api_key=api_key,
                    api_base=config.get("base_url"),
                    timeout=30,
                )
            if response and hasattr(response, "choices") and response.choices:
                content = response.choices[0].message.content
                if content:
                    return content.strip()
            return None
        except Exception as e:
            _router_log(f"[ROUTER] LiteLLM error for {provider}/{model}: {e}")
            return None

    def _call_google_direct(
        self,
        model: str,
        messages: list,
        max_tokens: int,
        temperature: float,
        api_key: str,
    ) -> Optional[str]:
        """Call Google AI Studio generateContent over HTTPS (no google-genai package)."""
        if not api_key:
            return None

        system_instruction = None
        contents: list = []
        for msg in messages:
            role = msg.get("role", "user")
            text = (msg.get("content") or "").strip()
            if not text:
                continue
            if role == "system":
                system_instruction = text
                continue
            if role == "assistant":
                contents.append({"role": "model", "parts": [{"text": text}]})
            else:
                contents.append({"role": "user", "parts": [{"text": text}]})

        if not contents:
            return None

        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:generateContent?key={api_key}"
        )

        payload: Dict[str, Any] = {
            "contents": contents,
            "generationConfig": {
                "maxOutputTokens": max_tokens,
                "temperature": temperature,
            },
        }
        if system_instruction:
            payload["systemInstruction"] = {"parts": [{"text": system_instruction}]}

        try:
            response = requests.post(url, json=payload, timeout=60)
            if response.status_code == 200:
                result = response.json()
                if "candidates" in result and result["candidates"]:
                    parts = result["candidates"][0].get("content", {}).get("parts") or []
                    if parts and "text" in parts[0]:
                        return parts[0]["text"].strip()
            elif response.status_code == 429:
                _router_log("[ROUTER] Google rate limited (429)")
            else:
                _router_log(
                    f"[ROUTER] Google HTTP {response.status_code}: {response.text[:200]}"
                )
            return None
        except Exception as e:
            _router_log(f"[ROUTER] Google REST error: {e}")
            return None

    def _call_direct_api(
        self,
        provider: str,
        model: str,
        messages: list,
        max_tokens: int = 500,
        temperature: float = 0.7,
    ) -> Optional[str]:
        config = PROVIDER_CONFIGS[provider]
        api_key = self._get_api_key(provider)

        if provider == "cohere":
            return self._call_cohere(model, messages, max_tokens)

        # Handle Ollama local provider
        if provider == "ollama":
            return self._call_ollama(model, messages, max_tokens, temperature)

        url = f"{config['base_url']}/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        if provider == "openrouter":
            headers["HTTP-Referer"] = "https://hikari-assistant.com"
            headers["X-Title"] = "HIKARI Assistant"

        payload = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }

        try:
            response = requests.post(url, json=payload, headers=headers, timeout=30)
            if response.status_code == 200:
                result = response.json()
                if "choices" in result and result["choices"]:
                    return result["choices"][0]["message"]["content"].strip()
            elif response.status_code == 429:
                _router_log(f"[ROUTER] Rate limited on {provider}")
                self.providers[provider].cooldown_until = time.time() + 5
            elif response.status_code == 402:
                _router_log(f"[ROUTER] Credits exhausted on {provider}")
                self.providers[provider].consecutive_failures += 1
            else:
                _router_log(
                    f"[ROUTER] HTTP {response.status_code} from {provider}: {response.text[:200]}"
                )
            return None
        except requests.exceptions.Timeout:
            _router_log(f"[ROUTER] Timeout on {provider}")
            return None
        except Exception as e:
            _router_log(f"[ROUTER] Request error on {provider}: {e}")
            return None

    def _call_cohere(
        self, model: str, messages: list, max_tokens: int
    ) -> Optional[str]:
        api_key = self._get_api_key("cohere")
        if not api_key:
            return None

        try:
            response = requests.post(
                "https://api.cohere.ai/v1/chat",
                json={
                    "model": model,
                    "message": messages[-1]["content"],
                    "max_tokens": max_tokens,
                    "temperature": 0.7,
                    "preamble": messages[0]["content"] if messages else "",
                },
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                timeout=30,
            )
            if response.status_code == 200:
                return response.json()["text"].strip()
            return None
        except Exception as e:
            _router_log(f"[ROUTER] Cohere error: {e}")
            return None

    def _call_ollama(
        self, model: str, messages: list, max_tokens: int, temperature: float
    ) -> Optional[str]:
        """Call local Ollama API"""
        try:
            url = "http://localhost:11434/api/chat"
            payload = {
                "model": model,
                "messages": messages,
                "stream": False,
                "options": {
                    "temperature": temperature,
                    "num_predict": max_tokens,
                },
            }
            response = requests.post(url, json=payload, timeout=120)
            if response.status_code == 200:
                result = response.json()
                if "message" in result:
                    msg = result["message"]
                    # Handle Gemma 4 thinking mode
                    content = msg.get("content", "")
                    thinking = msg.get("thinking", "")
                    # Return thinking + content if thinking exists
                    if thinking:
                        return (
                            f"{content}\n\n[Thinking: {thinking[:500]}...]"
                            if len(thinking) > 500
                            else f"{content}\n\n[Thinking: {thinking}]"
                        )
                    return content.strip() if content else None
                return None
            else:
                _router_log(
                    f"[ROUTER] Ollama error: {response.status_code} - {response.text[:200]}"
                )
            return None
        except requests.exceptions.ConnectionError:
            _router_log("[ROUTER] Ollama not running. Start with: ollama run gemma4:e4b")
            return None
        except Exception as e:
            _router_log(f"[ROUTER] Ollama error: {e}")
            return None

    def generate(
        self,
        user_input: str,
        system_prompt: str = "You are HIKARI, a helpful and concise AI assistant. Keep responses brief and friendly.",
        context: str = "",
        max_tokens: int = 500,
        temperature: float = 0.7,
    ) -> Optional[str]:
        """Main generation method with smart routing and fallback"""

        if not user_input or not user_input.strip():
            return None

        task_type = self._classify_task(user_input)
        quality = self._get_quality_level(task_type)

        _router_log(f"[ROUTER] Task: {task_type}, Quality: {quality}")

        messages = self._build_messages(system_prompt, user_input, context)

        # Try primary provider
        provider = self._select_provider(quality)
        if not provider:
            _router_log("[ROUTER] No providers available")
            return None

        config = PROVIDER_CONFIGS[provider]
        model = config["models"][quality]
        self._last_provider = provider
        self._last_model = model

        _router_log(f"[ROUTER] Trying {provider}/{model}")

        response = self._try_generate(
            provider, model, messages, max_tokens, temperature
        )

        # Fallback chain
        if not response:
            _router_log(f"[ROUTER] Primary provider {provider} failed, trying fallbacks...")
            for fallback_name in FALLBACK_CHAIN:
                if fallback_name == provider:
                    continue
                fallback_status = self.providers[fallback_name]
                if not fallback_status.available:
                    continue
                if fallback_status.consecutive_failures >= 3:
                    continue

                fallback_config = PROVIDER_CONFIGS[fallback_name]
                fallback_model = fallback_config["models"][quality]
                _router_log(f"[ROUTER] Trying fallback: {fallback_name}/{fallback_model}")

                response = self._try_generate(
                    fallback_name, fallback_model, messages, max_tokens, temperature
                )
                if response:
                    provider = fallback_name
                    model = fallback_model
                    self._last_provider = provider
                    self._last_model = model
                    break

        if response:
            self.providers[provider].requests_today += 1
            self.providers[provider].consecutive_failures = 0
            self.providers[provider].last_request_time = time.time()
            self.usage_stats[provider]["requests"] += 1
            self.usage_stats[provider]["tokens"] += len(response.split())
            _router_log(f"[ROUTER] Success with {provider}/{model}")
        else:
            _router_log("[ROUTER] All providers failed")

        return response

    def generate_document(
        self,
        user_input: str,
        *,
        allowed_providers: Sequence[str],
        before_provider_call: Callable[[str], bool],
        system_prompt: str = "You are HIKARI, a helpful and concise AI assistant. Keep responses brief and friendly.",
        context: str = "",
        max_tokens: int = 500,
        temperature: float = 0.7,
    ) -> GenerationResult:
        """Generate with request-scoped provider permission and routing evidence."""
        if not user_input or not user_input.strip() or not allowed_providers:
            return GenerationResult(None, None, None, ())

        quality = self._get_quality_level(self._classify_task(user_input))
        messages = self._build_messages(system_prompt, user_input, context)
        candidates = []
        for provider in allowed_providers:
            if provider in candidates:
                continue
            status = self.providers.get(provider)
            if not status or not status.available:
                continue
            if status.consecutive_failures >= 3 or time.time() < status.cooldown_until:
                continue
            candidates.append(provider)

        attempted = []
        for provider in candidates:
            model = PROVIDER_CONFIGS[provider]["models"][quality]
            try:
                approved = before_provider_call(provider)
            except Exception:
                approved = False
            if not approved:
                continue

            attempted.append(provider)
            response = self._try_generate_once(
                provider, model, messages, max_tokens, temperature
            )
            if not response:
                continue

            self.providers[provider].requests_today += 1
            self.providers[provider].consecutive_failures = 0
            self.providers[provider].last_request_time = time.time()
            self.usage_stats[provider]["requests"] += 1
            self.usage_stats[provider]["tokens"] += len(response.split())
            return GenerationResult(response, provider, model, tuple(attempted))

        return GenerationResult(None, None, None, tuple(attempted))

    def _try_generate_once(
        self,
        provider: str,
        model: str,
        messages: list,
        max_tokens: int,
        temperature: float,
    ) -> Optional[str]:
        """Use exactly one outbound transport for a permissioned attempt."""
        if provider == "google":
            return self._call_google_direct(
                model,
                messages,
                max_tokens,
                temperature,
                self._get_api_key(provider),
            )
        if provider == "ollama":
            return self._call_ollama(model, messages, max_tokens, temperature)
        if LITELLM_AVAILABLE:
            return self._call_litellm(
                provider, model, messages, max_tokens, temperature
            )
        return self._call_direct_api(
            provider, model, messages, max_tokens, temperature
        )

    def _try_generate(
        self,
        provider: str,
        model: str,
        messages: list,
        max_tokens: int,
        temperature: float,
    ) -> Optional[str]:
        # Ollama requires direct API call (not LiteLLM)
        if provider == "ollama":
            return self._call_ollama(model, messages, max_tokens, temperature)

        # Try LiteLLM first if available for other providers
        if LITELLM_AVAILABLE:
            response = self._call_litellm(
                provider, model, messages, max_tokens, temperature
            )
            if response:
                return response

        # Fall back to direct API calls
        return self._call_direct_api(provider, model, messages, max_tokens, temperature)

    def get_status(self) -> Dict[str, Any]:
        """Get current provider status"""
        status = {}
        for name, provider in self.providers.items():
            status[name] = {
                "available": provider.available,
                "requests_today": provider.requests_today,
                "consecutive_failures": provider.consecutive_failures,
                "last_error": provider.last_error,
            }
        return status

    def get_usage_stats(self) -> Dict[str, Dict]:
        return dict(self.usage_stats)

    def reset_usage(self):
        for provider in self.providers.values():
            provider.requests_today = 0
            provider.tokens_today = 0
            provider.consecutive_failures = 0
        self.usage_stats.clear()

    def get_routing_display(self, quality: str = "balanced") -> Dict[str, Any]:
        """Primary provider/model and configured fallbacks for CLI status panel."""
        provider = self._select_provider(quality)
        model = None
        if provider:
            model = PROVIDER_CONFIGS[provider]["models"].get(quality)

        fallback_labels: list[str] = []
        for name in FALLBACK_CHAIN:
            if name == provider:
                continue
            status = self.providers.get(name)
            if not status or not status.available:
                continue
            if status.consecutive_failures >= 3:
                continue
            fb_model = PROVIDER_CONFIGS[name]["models"].get(quality, "?")
            fallback_labels.append(f"{name}/{fb_model}")

        return {
            "provider": provider,
            "model": model,
            "fallback_labels": fallback_labels,
            "last_provider": self._last_provider,
            "last_model": self._last_model,
        }


# Singleton instance
_router_instance: Optional[AIRouter] = None


def get_router() -> AIRouter:
    global _router_instance
    if _router_instance is None:
        _router_instance = AIRouter()
    return _router_instance
