"""
HIKARI v3 - Main Orchestrator
Central brain that coordinates everything
"""

import os
import sys
import time
import asyncio
import re
import threading
from typing import TYPE_CHECKING, Optional, Dict, Any
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

# Import all core systems
from agents.base import BaseAgent
from agents.voice import VoiceAgent
from agents.code import CodeAgent
from agents.memory_agent import MemoryAgent

from core.router import AIRouter, get_router
from core.memory import MemorySystem, get_memory
from core.voice import VoiceSystem
from core.voice_memory import VoiceMemory
from core.user_profile import UserProfile
from core.knowledge_graph import KnowledgeGraph
from core.health_awareness import HealthAwareness
from core.semantic_memory import SemanticMemory
from core.task_planner import TaskPlanner, get_task_planner
from skills.skill_system import SkillRegistry, register_builtin_skills
from security.auth import CodenameAuth
from core.quiet import debug
from core.brain_statements import (
    classify_task_action_kind,
    is_declarative_memory_statement,
    is_task_or_action_statement,
)
from core.action_policy import Actor, ActorContext, validate_actor_context
from core.brain_service import BrainService
from core.brain_v2 import BrainV2Coordinator
from core.time_queries import answer_time_query

if TYPE_CHECKING:
    from core.brain import HikariBrain
from core.speaker_context import (
    get_speaker_context,
    is_guest_scoped_personal_question,
    is_guest_visit_recall_question,
    is_temporary_speaker_intro,
)
from core.brain_v2.memory_save_prompt import (
    PendingMemoryChoice,
    format_save_needs_review_reply,
    format_saved_to_memory_reply,
    format_saved_to_session_reply,
    is_save_to_memory_confirmation,
    is_session_only_confirmation,
)
from core.brain_v2.natural_replies import (
    format_guest_intro_reply,
    format_guest_visit_recall,
    format_identity_saved,
    format_memory_conflict_brief,
    format_owner_reset_reply,
    format_session_location_ack,
)
from core.brain_v2.owner_auto_trust import is_explicit_remember_command
from core.family_memory import (
    format_family_memory_confirmation,
    ingest_family_statement,
)

# Import new systems
from core.personality import get_personality, get_emotional_iq

# Wake words that activate HIKARI
WAKE_WORDS = ["hikari", "hey hikari", "okay hikari", "hi hikari"]


class HIKARI_Orchestrator:
    """Central brain of HIKARI - coordinates everything"""

    def __init__(self):
        debug("[HIKARI] Initializing brain...")
        self.authenticated = False
        self.codename_auth = CodenameAuth()

        # Core memory
        self.memory = get_memory()
        self.semantic_memory = SemanticMemory()
        self.neural_memory = None
        self.neural_memory_enabled = False
        self.brain_v2_enabled = os.getenv("HIKARI_DISABLE_BRAIN_V2", "0") != "1"
        self.brain: Optional["HikariBrain"] = None
        if not self.brain_v2_enabled:
            self.brain = self._create_legacy_brain()
        self.brain_v2: Optional[BrainV2Coordinator] = None
        self.brain_service = BrainService()
        self._brain_v2_session: Optional[str] = None
        self._owner_session_current_location: Optional[tuple[str, str]] = None
        self._pending_memory_choice: Optional[PendingMemoryChoice] = None
        self.legacy_memory_enabled = os.getenv("HIKARI_ENABLE_LEGACY_MEMORY", "0") == "1"

        # Personality & emotions
        self.personality = get_personality()
        self.emotional_iq = get_emotional_iq()

        # User profile & knowledge
        self.user_profile = UserProfile()
        self.speaker = get_speaker_context()
        env_primary = (os.getenv("HIKARI_PRIMARY_USER") or "").strip()
        if env_primary:
            self.speaker.set_primary_user(env_primary)
        elif (
            not self.brain_v2_enabled
            and self.user_profile.get_name()
            and self.user_profile.get_name() != "user"
        ):
            self.speaker.set_primary_user(self.user_profile.get_name())
        if self.brain_v2_enabled:
            self.personality.quarantine_loaded_personal_prefs()
        self.knowledge_graph = KnowledgeGraph()
        self.health = HealthAwareness()

        # Voice system
        self.voice = VoiceSystem()
        self.voice_memory = VoiceMemory()

        # AI Router
        self.router = get_router()

        # Agents
        self.agents: Dict[str, BaseAgent] = {}
        self._init_agents()

        # Ungoverned legacy tool surfaces stay unreachable until migrated through policy.
        self.mac_control = None
        self.smart_home = None
        self.action_system = None
        self.desktop = None
        self.browser = None
        self.mac = None
        self.planner = get_task_planner()
        self.build_executor = None

        # Skills
        self.skill_registry = SkillRegistry()
        self._init_skills()

        # Scheduler
        self.scheduler = None
        self._init_scheduler()

        if self.brain_v2_enabled:
            self.neural_memory = None
            self.neural_memory_enabled = False
            debug("[HIKARI] Neural memory skipped (Brain v2 authority)")
        else:
            self._init_neural_memory()
        self._init_brain_v2()

        debug("[HIKARI] Brain initialized!")
        debug("[HIKARI] Memory:", len(self.memory.conversations), "conversations")
        debug("[HIKARI] Personality traits:", self.personality.traits)

    @staticmethod
    def _create_legacy_brain() -> "HikariBrain":
        from core.brain import HikariBrain

        return HikariBrain()

    def _get_legacy_brain(self) -> "HikariBrain":
        """Load legacy HikariBrain only when Brain v2 policy is off."""
        if self.brain is not None:
            return self.brain
        if self.brain_v2_enabled:
            raise RuntimeError(
                "legacy HikariBrain is not available while Brain v2 policy is enabled"
            )
        self.brain = self._create_legacy_brain()
        return self.brain

    def _init_neural_memory(self):
        """Initialize optional SQLite neural memory in the live brain directory."""
        try:
            from core import neural_memory_bridge

            if neural_memory_bridge.init_neural_memory():
                self.neural_memory = neural_memory_bridge
                self.neural_memory_enabled = True
                debug("[HIKARI] Neural memory connected")
            else:
                debug("[HIKARI] Neural memory unavailable")
        except Exception as e:
            self.neural_memory = None
            self.neural_memory_enabled = False
            debug(f"[HIKARI] Neural memory skipped: {e}")

    def _init_brain_v2(self):
        """Local episode pipeline (Omi-inspired); stored under the private brain brain_v2 tree."""
        if not self.brain_v2_enabled:
            debug("[HIKARI] Brain v2 disabled (HIKARI_DISABLE_BRAIN_V2=1)")
            return
        try:
            service = getattr(self, "brain_service", None) or BrainService()
            self.brain_service = service
            self.brain_v2 = service.initialize_owner(
                ActorContext("local-owner", Actor.OWNER, "startup", "orchestrator"),
                neural_bridge=None,
                allow_neural_procedural=False,
                allow_neural_conflict_reads=False,
            )
            self._brain_v2_session = self.brain_v2.start_session()
            self._register_brain_v2_session_hooks()
            debug("[HIKARI] Brain v2 episode pipeline ready (single memory authority)")
        except Exception as e:
            self.brain_v2 = None
            self._brain_v2_session = None
            debug(f"[HIKARI] Brain v2 skipped: {e}")

    def finalize_session(self) -> None:
        """Public hook to flush Brain v2 consolidation (e.g. text chat exit)."""
        try:
            self._finalize_brain_v2_session()
        except Exception as e:
            debug(f"[BRAIN_V2] finalize_session failed: {e}")

    def _register_brain_v2_session_hooks(self) -> None:
        from core.brain_v2.session_context import register_session_place_provider

        def _current_place() -> Optional[str]:
            if not self.brain_v2:
                return None
            data = self.brain_v2.working.get_current_location()
            return data[0] if data else None

        register_session_place_provider(
            _current_place,
            guest_is_active=lambda: self._is_active_guest_speaker(),
        )

    def _snapshot_owner_current_location(self) -> None:
        """Keep owner session location across a temporary guest interlude."""
        if not self.brain_v2:
            return
        try:
            data = self.brain_v2.working.get_current_location()
        except Exception:
            return
        if data:
            self._owner_session_current_location = data

    def _restore_owner_current_location(self) -> None:
        """Restore owner session location after returning from guest mode."""
        if not self.brain_v2:
            return
        data = getattr(self, "_owner_session_current_location", None)
        if not data:
            return
        loc, stmt = data
        try:
            self.brain_v2.working.note_current_location(loc, stmt)
        except Exception as e:
            debug(f"[BRAIN_V2] Owner current location restore failed: {e}")

    def _finalize_brain_v2_session(self) -> None:
        if not self.brain_v2 or not self._brain_v2_session:
            return
        try:
            self.brain_v2.close_and_consolidate(self._brain_v2_session)
        except KeyError:
            pass
        except Exception as e:
            debug(f"[BRAIN_V2] Session consolidate failed: {e}")
        self._begin_fresh_brain_v2_session()

    def _begin_fresh_brain_v2_session(self) -> None:
        """New session: clear guest/session speaker and working-memory turn context."""
        if self.brain_v2:
            self._brain_v2_session = self.brain_v2.start_session()
        else:
            self._brain_v2_session = None
        self._pending_memory_choice = None
        self.speaker.clear_current_speaker()

    def _is_active_guest_speaker(self) -> bool:
        """True when the current speaker must not read or write owner Brain v2 memory."""
        check = getattr(self.speaker, "is_guest_speaker", None)
        return bool(check()) if callable(check) else False

    def _reply_and_record_brain_v2_turn(
        self,
        user_input: str,
        response: str,
        source: str = "chat",
        *,
        metadata: Optional[dict] = None,
    ) -> str:
        """Return assistant text after persisting the turn (session context for recall)."""
        self._record_brain_v2_turn(user_input, response, source, metadata=metadata)
        return response

    def _record_brain_v2_turn(
        self,
        user_input: str,
        response: str,
        source: str = "chat",
        *,
        metadata: Optional[dict] = None,
    ) -> None:
        if not self.brain_v2 or not self._brain_v2_session:
            return
        if self._is_active_guest_speaker():
            return
        try:
            speaker = self.speaker.current_speaker or "user"
            meta: Dict[str, Any] = {"source": source, **(metadata or {})}
            if self.speaker.last_was_session_intro or is_temporary_speaker_intro(user_input):
                meta["session_speaker_intro"] = True
            service = getattr(self, "brain_service", None)
            if service is None or not service.owns(self.brain_v2):
                service = BrainService(self.brain_v2)
                self.brain_service = service
            service.record_turn(
                ActorContext(
                    "local-owner",
                    Actor.OWNER,
                    self._brain_v2_session,
                    "orchestrator",
                ),
                self._brain_v2_session,
                user_input,
                response or "",
                speaker_label=speaker,
                metadata=meta,
            )
            if self.speaker.current_speaker:
                self.brain_v2.working.note_speaker(self.speaker.current_speaker)
        except Exception as e:
            debug(f"[BRAIN_V2] Turn record failed: {e}")

    def _build_memory_first_context(self, user_input: str) -> str:
        """Retrieval order: working → speaker → task → semantic → episodic → procedural → neural."""
        parts = []
        speaker_ctx = {}
        if self.speaker.current_speaker:
            speaker_ctx["speaker"] = self.speaker.current_speaker
        if self.speaker.primary_user and not self._is_active_guest_speaker():
            speaker_ctx["household"] = f"primary={self.speaker.primary_user}"
        if self.speaker.last_contact_kind:
            speaker_ctx["family"] = self.speaker.last_contact_kind

        task_hint = None
        if self.planner and getattr(self.planner, "current_task", None):
            task_hint = str(self.planner.current_task)

        if self._brain_v2_runtime_ready() and not self._is_active_guest_speaker():
            try:
                prompt = self.brain_v2.build_prompt_context(
                    user_input,
                    speaker_context=speaker_ctx or None,
                    task_context=task_hint,
                )
                if prompt:
                    parts.append(prompt)
            except Exception as e:
                debug(f"[BRAIN_V2] Context packet failed: {e}")
        elif (
            not self._brain_v2_authority_enabled()
            and self.neural_memory_enabled
            and not self._is_active_guest_speaker()
        ):
            try:
                neural_prompt = self._get_legacy_brain().build_prompt_context(user_input)
                if neural_prompt:
                    parts.append(neural_prompt)
            except Exception as e:
                debug(f"[MEMORY] Neural recall failed: {e}")

        return "\n\n".join(parts)

    def _init_agents(self):
        """Initialize only agents without ungoverned file, network, or OS actions."""
        eager_legacy_brain = not self.brain_v2_enabled
        self.agents["voice"] = VoiceAgent()
        self.agents["code"] = CodeAgent()
        self.agents["memory"] = MemoryAgent(
            self.memory, eager_legacy_brain=eager_legacy_brain
        )

        debug(f"[HIKARI] Initialized {len(self.agents)} agents")

    def _init_skills(self):
        """Initialize built-in skills"""
        register_builtin_skills(self.skill_registry)
        debug(f"[HIKARI] Registered {len(self.skill_registry.skills)} skills")

    def _init_scheduler(self):
        """Keep autonomous legacy callbacks disabled until they have bounded grants."""
        self.scheduler = None
        debug("[HIKARI] Proactive scheduler quarantined pending policy migration")

    def _default_local_owner_context(self, source: str = "text") -> ActorContext:
        """Return a server-owned local-owner context for trusted local entrypoints."""
        return ActorContext(
            actor_id="local-owner",
            actor=Actor.OWNER,
            session_id="local",
            source=source,
        )

    def process_input(
        self,
        user_input: str,
        source: str = "text",
        *,
        context: Optional[ActorContext] = None,
    ) -> Optional[str]:
        """Main entry point - process any user input.

        Args:
            user_input: The user's text input.
            source: Channel identifier (e.g. "text", "device", "voice_remote").
            context: Optional server-derived actor context. If omitted, a local-owner
                default is used so existing CLI/direct callers remain trusted.
        """
        user_input = self._normalize_user_input_text(user_input)
        if not user_input:
            return None

        if context is None:
            context = self._default_local_owner_context(source)
        else:
            valid, _reason = validate_actor_context(context)
            if not valid:
                debug("[INPUT] source=%s actor=unknown outcome=denied", source)
                return "I cannot complete this request."

        if context.actor is Actor.UNKNOWN:
            debug(f"[INPUT] source={source} actor=unknown outcome=denied")
            return "I cannot complete this request."

        if not hasattr(self, "_pending_memory_choice"):
            self._pending_memory_choice = None

        # Remote/guest turns must not mutate the shared SpeakerContext.  Local CLI
        # turns continue to use SpeakerContext for backward compatibility.
        is_remote_guest = context.actor is Actor.GUEST
        if is_remote_guest:
            return self._remote_guest_reply(user_input)
        else:
            self.speaker.update_from_input(user_input)
            self.speaker.note_guest_relation_from_input(user_input)
            reset_check = getattr(self.speaker, "consume_speaker_reset", None)
            speaker_reset = reset_check() is True if callable(reset_check) else False
            if speaker_reset and self._brain_v2_runtime_ready():
                try:
                    self.brain_v2.working.clear()
                    self._restore_owner_current_location()
                except Exception as e:
                    debug(f"[BRAIN_V2] Working memory clear after speaker reset failed: {e}")
            guest = self.speaker.is_guest_speaker()
            if (
                guest
                and getattr(self.speaker, "last_was_session_intro", False)
                and self._brain_v2_runtime_ready()
            ):
                try:
                    self._snapshot_owner_current_location()
                    self.brain_v2.working.clear()
                except Exception as e:
                    debug(f"[BRAIN_V2] Working memory clear after guest intro failed: {e}")
        brain_memory_text = self._normalize_brain_memory_statement(user_input)

        if guest and getattr(self.speaker, "last_was_session_intro", False):
            guest_name = self.speaker.current_speaker or "guest"
            return format_guest_intro_reply(guest_name)

        if speaker_reset:
            return format_owner_reset_reply(self.speaker.primary_user)

        if not guest and is_guest_visit_recall_question(user_input):
            visit_reply = self._try_guest_visit_recall_answer(user_input)
            if visit_reply:
                return self._reply_and_record_brain_v2_turn(
                    user_input, visit_reply, source
                )

        if self._brain_v2_authority_enabled() and not guest:
            scope_reply = self._try_resolve_pending_memory_choice(user_input, source)
            if scope_reply is not None:
                return scope_reply

        if (
            guest
            and not getattr(self.speaker, "last_was_session_intro", False)
            and is_declarative_memory_statement(brain_memory_text)
        ):
            return self._guest_declarative_no_store_reply()
        if guest and self._guest_blocks_owner_personal_memory(user_input):
            guest_reply = self._guest_personal_no_memory_reply(user_input)
            self._record_brain_v2_turn(user_input, guest_reply, source)
            return guest_reply

        # Handle special commands (guest-aware for personal/profile/memory)
        response = self._handle_special_commands(user_input)
        if response:
            return response
        skip_owner_identity = self.speaker.should_skip_owner_identity_learning(user_input)

        # Detect and adapt to emotions
        emotions = self.emotional_iq.detect_emotion(user_input)
        dominant_emotion, emotion_score = self.emotional_iq.get_dominant_emotion(emotions)

        if emotion_score > 0.3:
            emotion_context = (
                "" if self._brain_v2_authority_enabled() else user_input
            )
            self.emotional_iq.log_emotion(
                dominant_emotion, emotion_score, emotion_context
            )

        # Style-only adaptation when Brain v2 owns personal memory (no legacy JSON facts).
        self.personality.learn_from_interaction(
            user_input,
            "",
            "",
            allow_name_update=not skip_owner_identity,
            store_personal_facts=not self._brain_v2_authority_enabled(),
        )
        if self._legacy_personal_learning_enabled():
            self.user_profile.extract_info_from_conversation(
                user_input, "", allow_primary_updates=not skip_owner_identity
            )
            self.knowledge_graph.extract_from_conversation(user_input, "")

        if not self._brain_v2_authority_enabled():
            self._check_health(user_input)

        # Strip wake words
        lowered = user_input.lower().strip()
        for wake in WAKE_WORDS:
            if lowered.startswith(wake):
                lowered = lowered.replace(wake, "", 1).strip()
                break

        if self._mentions_partner_context(lowered):
            self.speaker.note_contact_discussed("partner")

        if not lowered:
            greeting = self.personality.get_greeting()
            if guest and self.speaker.current_speaker:
                return f"Hi {self.speaker.current_speaker}! How can I help?"
            return greeting + "! How can I help?"

        if self._brain_v2_authority_enabled():
            from core.tasks.scheduling_commands import is_task_schedule_confirmation

            if is_task_schedule_confirmation(user_input):
                reply = self._handle_task_schedule_confirmation(source)
                return self._reply_and_record_brain_v2_turn(
                    user_input,
                    reply,
                    source,
                    metadata={"skip_candidate_extraction": True, "task_action": True},
                )

        if self._brain_v2_authority_enabled() and is_task_or_action_statement(user_input):
            task_meta = {"skip_candidate_extraction": True, "task_action": True}
            agent_response = self._route_to_agent(lowered)
            if agent_response:
                return self._reply_and_record_brain_v2_turn(
                    user_input,
                    agent_response,
                    source,
                    metadata=task_meta,
                )
            self._record_task_intent(user_input, source_channel=source)
            return self._reply_and_record_brain_v2_turn(
                user_input,
                self._task_action_no_memory_reply(user_input),
                source,
                metadata=task_meta,
            )

        # Memory policy router: silent bucket selection for owner utterances.
        if self._brain_v2_authority_enabled() and not skip_owner_identity:
            from core.brain_v2.memory_policy import MemoryPolicyRoute, route_owner_utterance

            decision = route_owner_utterance(
                brain_memory_text,
                guest=guest,
                skip_owner_identity=skip_owner_identity,
            )

            if decision.route == MemoryPolicyRoute.SESSION_MEMORY:
                from core.brain_v2.location_phrases import (
                    is_meta_or_deferred_location_phrase,
                )
                from core.brain_v2.session_context import get_session_current_place

                if is_meta_or_deferred_location_phrase(brain_memory_text):
                    session_place = get_session_current_place()
                    if session_place:
                        response = format_session_location_ack(session_place)
                    else:
                        response = (
                            "I don't have a city name for this session yet. "
                            'Tell me the city, for example: "I am in City A".'
                        )
                else:
                    place = ""
                    if decision.inferred and decision.inferred.metadata:
                        place = str(
                            decision.inferred.metadata.get("current_location") or ""
                        ).strip()
                    response = format_session_location_ack(place)
                return self._reply_and_record_brain_v2_turn(
                    user_input,
                    response,
                    source,
                    metadata={"skip_candidate_extraction": True},
                )

            if decision.route == MemoryPolicyRoute.EPISODE_ONLY and decision.reason in {
                "casual_filler",
                "quality_reject",
                "uncertain_hypothetical",
                "weak_fact",
            }:
                if decision.reason == "casual_filler":
                    response = "Got it."
                else:
                    response = self._route_to_agent(lowered)
                    if not response:
                        response = self._get_ai_response(
                            lowered, dominant_emotion, emotion_score
                        )
                    if response and emotion_score > 0.4:
                        response = self.emotional_iq.adapt_response(
                            response,
                            dominant_emotion,
                            emotion_score,
                            user_input=user_input,
                        )
                    if response:
                        response = self.personality.format_response(response)
                return self._reply_and_record_brain_v2_turn(
                    user_input,
                    response or "",
                    source,
                    metadata={
                        "skip_candidate_extraction": True,
                        "memory_policy_route": decision.route.value,
                        "memory_policy_reason": decision.reason,
                    },
                )

            if decision.route in (
                MemoryPolicyRoute.ACTIVE_MEMORY,
                MemoryPolicyRoute.REVIEW_QUEUE,
            ):
                if self._brain_v2_runtime_ready():
                    return self._reply_and_record_brain_v2_turn(
                        user_input,
                        self._commit_owner_memory_declaration(
                            user_input,
                            brain_memory_text,
                            source,
                            inferred=decision.inferred,
                        ),
                        source,
                        metadata={"skip_candidate_extraction": True},
                    )
                response = self._brain_v2_unavailable_message()
                return self._reply_and_record_brain_v2_turn(user_input, response, source)

        if (
            self._brain_v2_authority_enabled()
            and self._brain_v2_runtime_ready()
            and not skip_owner_identity
        ):
            from core.brain_v2.location_phrases import is_meta_or_deferred_location_phrase
            from core.brain_v2.session_context import get_session_current_place

            if is_meta_or_deferred_location_phrase(brain_memory_text):
                session_place = get_session_current_place()
                if session_place:
                    if re.search(r"\bweather\b", lowered):
                        research = self.agents.get("research")
                        if research and hasattr(research, "get_weather"):
                            weather_reply = research.get_weather(session_place)
                            if weather_reply:
                                return self._reply_and_record_brain_v2_turn(
                                    user_input, weather_reply, source
                                )
                    response = f"You're in {session_place} for this session."
                    return self._reply_and_record_brain_v2_turn(
                        user_input, response, source
                    )

            if re.search(r"\bweather\b", lowered) and re.search(
                r"\b(?:city|place|location)\s+i\s+live\s+in\b", lowered
            ):
                try:
                    home = self.brain_v2.retrieval.best_stable_home_place() if self.brain_v2 else None
                except Exception:
                    home = None
                if home:
                    research = self.agents.get("research")
                    if research and hasattr(research, "get_weather"):
                        weather_reply = research.get_weather(home)
                        if weather_reply:
                            return self._reply_and_record_brain_v2_turn(
                                user_input, weather_reply, source
                            )

        if self._brain_v2_authority_enabled() and self._requires_brain_v2_personal_answer(
            user_input
        ):
            v2_answer = self._brain_v2_personal_recall_answer(user_input)
            self._record_brain_v2_turn(user_input, v2_answer, source)
            return v2_answer

        # Legacy HikariBrain.answer() is quarantined whenever Brain v2 policy is on.
        if not self._brain_v2_authority_enabled():
            legacy_brain = self._get_legacy_brain()
            brain_answer = legacy_brain.answer(user_input)
            if brain_answer:
                self._record_brain_v2_turn(user_input, brain_answer.text, source)
                legacy_brain.remember_turn(
                    user_input,
                    brain_answer.text,
                    {"source": source, "path": "neural_brain_answer"},
                )
                return brain_answer.text

        if (
            not skip_owner_identity
            and is_declarative_memory_statement(brain_memory_text)
            and not self._brain_v2_authority_enabled()
        ):
            legacy_brain = self._get_legacy_brain()
            if legacy_brain.remember_fact(brain_memory_text):
                response = "Got it. I'll remember that."
                legacy_brain.remember_turn(
                    user_input,
                    response,
                    {"source": source, "path": "neural_memory_statement"},
                )
                self._record_brain_v2_turn(user_input, response, source)
                if not self.legacy_memory_enabled:
                    return response

        if not self.legacy_memory_enabled:
            if self._brain_v2_authority_enabled() and self._is_casual_greeting(
                lowered
            ):
                greeting = self.personality.get_greeting()
                if guest and self.speaker.current_speaker:
                    response = f"Hi {self.speaker.current_speaker}! How can I help?"
                else:
                    response = f"{greeting}! How can I help?"
                return self._reply_and_record_brain_v2_turn(
                    user_input, response, source
                )
            if self._brain_v2_authority_enabled() and self._should_use_brain_v2_recall(
                user_input
            ):
                v2_answer = self._brain_v2_personal_recall_answer(user_input)
                self._record_brain_v2_turn(user_input, v2_answer, source)
                return v2_answer
            response = self._route_to_agent(lowered)
            if not response:
                response = self._get_ai_response(lowered, dominant_emotion, emotion_score)
            if response and emotion_score > 0.4:
                response = self.emotional_iq.adapt_response(
                    response, dominant_emotion, emotion_score, user_input=user_input
                )
            if response:
                response = self.personality.format_response(response)
            self._record_brain_v2_turn(user_input, response or "", source)
            if not self._brain_v2_blocks_legacy_neural_writes():
                self._get_legacy_brain().remember_turn(
                    user_input, response or "", {"source": source}
                )
            return response

        # Legacy JSON family slots (optional); disabled when Brain v2 owns personal memory.
        rel = None
        if self._legacy_personal_learning_enabled():
            rel = ingest_family_statement(
                user_input,
                self.memory,
                self.neural_memory if self.neural_memory_enabled else None,
                last_relation=self.speaker.last_family_relation,
            )
            if rel:
                self.speaker.note_family_relation(rel)
                if is_declarative_memory_statement(user_input):
                    self._get_legacy_brain().remember_fact(brain_memory_text)
                family_answer = format_family_memory_confirmation(self.memory, rel)
                if self._may_persist_legacy_json_memory():
                    self.memory.add_conversation(user_input, family_answer)
                if self.neural_memory_enabled and self.neural_memory:
                    try:
                        self.neural_memory.remember(
                            user_input, family_answer, {"source": source}
                        )
                    except Exception:
                        pass
                return family_answer

        # Route to appropriate agent
        response = self._route_to_agent(lowered)

        # If no response, use AI
        if not response:
            response = self._get_ai_response(lowered, dominant_emotion, emotion_score)

        # Adapt response to emotions (skip for memory confirmations / family answers)
        memory_tone = response and (
            response.startswith(("I'll remember", "Got it.", "Your sister", "Your brother"))
            or response.startswith("Your ")
            or response.startswith(("From memory", "From saved memory", "You're in "))
            or "you asked about" in response.lower()
            or "studies" in response.lower()
            or ("School:" in response and "stud" in response.lower())
            or "Your flight is" in response
            or response.startswith("I don't have your flight")
        )
        if response and emotion_score > 0.4 and not memory_tone:
            response = self.emotional_iq.adapt_response(
                response, dominant_emotion, emotion_score, user_input=user_input
            )

        # Format based on personality
        if response:
            response = self.personality.format_response(response)

        if self._may_persist_legacy_json_memory():
            self.memory.add_conversation(user_input, response or "")
        self._record_brain_v2_turn(user_input, response or "", source)
        if (
            self.neural_memory_enabled
            and self.neural_memory
            and not self._brain_v2_blocks_legacy_neural_writes()
        ):
            try:
                meta = {"source": source}
                if self.speaker.current_speaker:
                    meta["speaker"] = self.speaker.current_speaker
                self.neural_memory.remember(user_input, response or "", meta)
            except Exception as e:
                debug(f"[MEMORY] Neural remember failed: {e}")

        return response

    def _handle_special_commands(self, user_input: str) -> Optional[str]:
        """Handle special system commands"""
        lowered = user_input.lower().strip()

        # Exit commands
        if any(w in lowered for w in ["exit", "quit", "goodbye", "bye"]):
            self._finalize_brain_v2_session()
            return "Goodbye! Call me when you need me. I'm always here."

        if self._is_self_identity_question(lowered):
            return "I'm HIKARI."

        if self._is_ai_runtime_question(lowered):
            return self._get_ai_runtime_summary()

        time_reply = answer_time_query(
            lowered,
            previous_was_time=getattr(self, "_last_special_intent", None) == "time",
        )
        if time_reply is not None:
            self._last_special_intent = "time"
            return time_reply

        if re.search(r"\b(?:today(?:'s)?\s+date|what(?:'s| is)\s+(?:today|the date)|date)\b", lowered):
            return f"Today is {datetime.now().strftime('%A, %B %-d, %Y')}."

        if "weather" in lowered:
            return "Live weather lookup is disabled until its network policy adapter is approved."

        if "news" in lowered or "headline" in lowered:
            return "News headlines are disabled until their network policy adapter is approved."

        if lowered.startswith(("search ", "find ", "look up ")):
            return "Web search is disabled until its network policy adapter is approved."

        # Status command
        if lowered in ["status", "hikari status", "system status"]:
            return self._get_status_report()

        # Codename authentication fallback
        configured_codename = os.getenv("CODENAME", "change-me").strip().lower()
        if lowered == configured_codename and self.codename_auth.verify(user_input):
            self.authenticated = True
            return "Authentication confirmed. I'm ready."

        # Who am I / profile commands
        if re.search(r"\bwho\s+am\s+i\b", lowered) or re.search(
            r"\bwhat\s+do\s+(?:you|u)\s+know\s+about\s+me\b", lowered
        ):
            return self._get_user_summary()

        if lowered in ("profile", "my profile", "show profile"):
            return self._get_user_summary()

        # Mood check (ephemeral emotion scores only; no legacy personal history)
        if any(w in lowered for w in ["how am i doing", "how have i been", "my mood"]):
            if self._brain_v2_authority_enabled():
                return self._brain_v2_safe_recall_summary(user_input)
            return self.emotional_iq.get_mood_summary()

        # Memory check (reviewed Brain v2 only when authority is enabled)
        if any(w in lowered for w in ["what do you remember", "what have we talked about"]):
            return self._get_brain_v2_safe_recall_summary(user_input)

        if lowered in (
            "memory status",
            "brain status",
            "memory-status",
            "brain-status",
        ):
            from core.memory_status import format_memory_status_report

            return format_memory_status_report(self)

        # Help
        if lowered in ["help", "what can you do", "commands"]:
            return self._get_help()

        return None

    def _guest_blocks_owner_personal_memory(self, user_input: str) -> bool:
        """Guest sessions must not read owner reviewed memories or legacy personal summaries."""
        if not self.speaker.is_guest_speaker():
            return False
        lowered = (user_input or "").lower().strip()
        if self._is_self_identity_question(lowered) or self._is_ai_runtime_question(
            lowered
        ):
            return False
        if lowered in ("help", "what can you do", "commands"):
            return False
        if lowered in (
            "memory status",
            "brain status",
            "memory-status",
            "brain-status",
            "status",
            "hikari status",
            "system status",
        ):
            return True
        if re.search(r"\bwho\s+am\s+i\b", lowered) or re.search(
            r"\bwhat\s+do\s+(?:you|u)\s+know\s+about\s+me\b", lowered
        ):
            return True
        if lowered in ("profile", "my profile", "show profile"):
            return True
        if any(
            phrase in lowered
            for phrase in (
                "what do you remember",
                "what have we talked about",
            )
        ):
            return True
        try:
            from core.brain_v2.recall_intent import is_personal_factual_question

            return is_personal_factual_question(user_input)
        except Exception:
            return is_guest_scoped_personal_question(user_input)

    def _guest_personal_no_memory_reply(self, user_input: str) -> str:
        guest = self.speaker.current_speaker or "the guest speaker"
        return (
            f"I do not have reviewed Brain v2 memories for {guest} yet. "
            "I will not use the household owner's personal memories while a guest is speaking."
        )

    def _guest_declarative_no_store_reply(self) -> str:
        return (
            "I will not store guest personal details in the household owner's "
            "Brain v2 memory."
        )

    def _remote_guest_reply(self, user_input: str) -> str:
        """Bounded generic reply for remote/guest turns.

        Remote guests cannot read owner memory, write durable memory, modify the
        speaker identity, access documents, execute side effects, or call providers.
        This method returns a safe generic response without exposing owner identity,
        memory contents, paths, provider credentials, internal exceptions, or policy
        internals.
        """
        lowered = (user_input or "").lower().strip()
        if not lowered:
            return "Hello. How can I help?"
        if any(
            lowered.startswith(wake) for wake in ["hi", "hello", "hey", "good morning", "good afternoon"]
        ):
            return "Hello. How can I help?"
        if any(phrase in lowered for phrase in ("who are you", "what is your name", "what can you do")):
            return "I'm HIKARI, a local assistant. I can answer general questions, but I cannot access household memory or documents from this device."
        if any(phrase in lowered for phrase in ("status", "memory", "remember", "profile", "document")):
            return "I cannot access that from this device. Please use the local computer for household memory, documents, or status."
        return "I cannot complete this request from this device."

    def _task_intent_service(self):
        if not hasattr(self, "_task_intents"):
            from core.tasks.factory import open_task_store
            from core.tasks.service import TaskIntentService

            self._task_intents = TaskIntentService(store=open_task_store())
        return self._task_intents

    def _task_record_context(self, source_channel: str):
        from core.tasks.context import TaskRecordContext

        guest = self.speaker.is_guest_speaker()
        label = self.speaker.current_speaker or self.speaker.primary_user or "owner"
        if guest:
            return TaskRecordContext(
                speaker_label=label,
                session_id=self._brain_v2_session,
                source="guest",
                is_guest=True,
            )
        return TaskRecordContext(
            speaker_label=label,
            session_id=self._brain_v2_session,
            source=source_channel,
            is_guest=False,
        )

    def _record_task_intent(self, user_input: str, *, source_channel: str = "text") -> None:
        try:
            self._task_intent_service().record_intent(
                user_input,
                context=self._task_record_context(source_channel),
            )
        except Exception as e:
            debug(f"[TASK] Intent record failed: {e}")

    def _handle_task_schedule_confirmation(self, source_channel: str) -> str:
        try:
            _task, reply = self._task_intent_service().schedule_latest_reminder(
                context=self._task_record_context(source_channel),
            )
            return reply
        except Exception as e:
            debug(f"[TASK] Schedule confirmation failed: {e}")
            return "I could not schedule that reminder right now."

    def _task_action_no_memory_reply(self, user_input: str) -> str:
        """Task-like phrasing is not durable memory; avoid pretending it was scheduled."""
        kind = classify_task_action_kind(user_input)
        if kind == "reminder":
            return (
                "I will not store that as a Brain v2 memory. "
                "I recorded it as a task intent (not scheduled yet). "
                "Say 'schedule that reminder' after enabling task scheduling if you want macOS Reminders."
            )
        if kind == "schedule":
            return (
                "I will not store that as a Brain v2 memory. "
                "Calendar scheduling is not wired up yet, so please create the event separately for now."
            )
        if kind == "code":
            return (
                "I will not store that as a Brain v2 memory. "
                "That is a coding task request, not a personal fact. "
                "I can help with code in chat, but durable task scheduling is not wired up yet."
            )
        return (
            "I will not store that as a Brain v2 memory. "
            "That sounds like a task request, not a personal fact."
        )

    def _guest_scoped_no_memory_reply(self, user_input: str) -> str:
        return self._guest_personal_no_memory_reply(user_input)

    def _brain_v2_authority_enabled(self) -> bool:
        """Brain v2 policy is on (fail-closed even when coordinator init failed)."""
        enabled = getattr(self, "brain_v2_enabled", None)
        if enabled is None:
            from core.brain_v2.status import is_brain_v2_enabled

            return is_brain_v2_enabled()
        return bool(enabled)

    def _brain_v2_runtime_ready(self) -> bool:
        """Episode pipeline initialized and usable."""
        if not self._brain_v2_authority_enabled():
            return False
        return bool(self.brain_v2 and getattr(self, "_brain_v2_session", None))

    def _brain_v2_blocks_legacy_neural_writes(self) -> bool:
        """Authority on: never write legacy neural for personal conversation evidence."""
        return self._brain_v2_authority_enabled()

    def _brain_v2_unavailable_message(self) -> str:
        from core.brain_v2.recall_intent import BRAIN_V2_UNAVAILABLE_MESSAGE

        return BRAIN_V2_UNAVAILABLE_MESSAGE

    def _brain_v2_no_reviewed_message(self, query: str = "") -> str:
        from core.brain_v2.no_reviewed_reply import format_no_reviewed_memory_reply

        if (query or "").strip():
            return format_no_reviewed_memory_reply(query)
        from core.brain_v2.recall_intent import BRAIN_V2_NO_REVIEWED_MEMORY_MESSAGE

        return BRAIN_V2_NO_REVIEWED_MEMORY_MESSAGE

    def _get_brain_v2_safe_recall_summary(self, user_input: str) -> str:
        """Memory-summary commands must not expose legacy conversation logs when authority is on."""
        if self.speaker.is_guest_speaker():
            return self._guest_personal_no_memory_reply(user_input)
        if not self._brain_v2_authority_enabled():
            return self._get_legacy_memory_summary()
        if not self._brain_v2_runtime_ready():
            return self._brain_v2_unavailable_message()
        try:
            profile = self.brain_v2.build_user_profile_answer()
            if profile and "do not have a reviewed memory" not in profile.lower():
                return profile
        except Exception as e:
            debug(f"[BRAIN_V2] Safe recall summary failed: {e}")
        return self._brain_v2_no_reviewed_message()

    def _get_legacy_memory_summary(self) -> str:
        """Legacy JSON conversation log (only when Brain v2 authority is disabled)."""
        recent = self.memory.get_recent_conversations(5)
        if not recent:
            return "We haven't talked much yet!"

        summary = "Recent conversations:\n"
        for i, conv in enumerate(recent, 1):
            user = conv.get("user", "")[:50]
            summary += f"{i}. You: {user}\n"

        return summary

    def _legacy_personal_learning_enabled(self) -> bool:
        """Legacy JSON/profile/family learning only when Brain v2 is not authoritative."""
        return bool(self.legacy_memory_enabled and not self._brain_v2_authority_enabled())

    def _may_persist_legacy_json_memory(self) -> bool:
        """Legacy JSON conversation log only when Brain v2 does not own personal memory."""
        return bool(self.legacy_memory_enabled and not self._brain_v2_authority_enabled())

    def _should_use_brain_v2_recall(self, user_input: str) -> bool:
        return self._requires_brain_v2_personal_answer(user_input)

    def _requires_brain_v2_personal_answer(self, user_input: str) -> bool:
        try:
            from core.brain_v2.recall_intent import is_personal_factual_question

            return is_personal_factual_question(user_input)
        except Exception:
            return False

    def _commit_owner_memory_declaration(
        self,
        user_input: str,
        brain_memory_text: str,
        source: str,
        *,
        inferred: Optional[Any] = None,
    ) -> str:
        from core.brain_v2.memory_type import infer_memory_type

        if not self._brain_v2_runtime_ready():
            return self._brain_v2_unavailable_message()

        inferred = inferred or infer_memory_type(brain_memory_text)
        outcome = self.brain_v2.ingest_trusted_owner_declaration(
            self._brain_v2_session,
            user_input,
        )
        if outcome.get("status") == "accepted":
            identity_meta = inferred.metadata or {}
            if inferred.candidate_type == "identity" and (
                identity_meta.get("preferred_name") or identity_meta.get("legal_name")
            ):
                return format_identity_saved(
                    legal=str(identity_meta.get("legal_name", "")),
                    preferred=str(identity_meta.get("preferred_name", "")),
                )
            if is_explicit_remember_command(brain_memory_text):
                return format_saved_to_memory_reply()
            return format_saved_to_memory_reply()
        if outcome.get("status") == "pending_conflict":
            return format_memory_conflict_brief()
        if outcome.get("status") == "pending_review":
            return format_save_needs_review_reply()
        return format_save_needs_review_reply()

    def _try_resolve_pending_memory_choice(
        self, user_input: str, source: str
    ) -> Optional[str]:
        pending = self._pending_memory_choice
        if not pending:
            return None
        if is_save_to_memory_confirmation(user_input):
            self._pending_memory_choice = None
            response = self._commit_owner_memory_declaration(
                pending.statement,
                pending.statement,
                source,
            )
            return self._reply_and_record_brain_v2_turn(
                user_input,
                response,
                source,
                metadata={"skip_candidate_extraction": True},
            )
        if is_session_only_confirmation(user_input):
            self._pending_memory_choice = None
            if self._brain_v2_runtime_ready():
                self.brain_v2.working.note_session_fact(pending.statement)
            return self._reply_and_record_brain_v2_turn(
                user_input,
                format_saved_to_session_reply(),
                source,
                metadata={"skip_candidate_extraction": True},
            )
        return None

    def _try_guest_visit_recall_answer(self, user_input: str) -> Optional[str]:
        from core.speaker_context import extract_guest_visit_relation_asked

        visit = self.speaker.last_guest_visit
        if not visit:
            return "No guest has checked in since you came back to owner mode."
        asked = extract_guest_visit_relation_asked(user_input)
        if asked and visit.relation and asked != visit.relation:
            return (
                f"I haven't had your {asked} visit as a guest. "
                f"{visit.guest_name} stopped by earlier."
            )
        return format_guest_visit_recall(
            visit.guest_name,
            relation=visit.relation,
            asked_relation=asked,
        )

    def _brain_v2_personal_recall_answer(self, user_input: str) -> str:
        """Personal recall under Brain v2 authority; never falls through to legacy neural."""
        if self.speaker.is_guest_speaker():
            return self._guest_personal_no_memory_reply(user_input)
        if not self._brain_v2_runtime_ready():
            return self._brain_v2_unavailable_message()
        visit_answer = self._try_guest_visit_recall_answer(user_input)
        if visit_answer and is_guest_visit_recall_question(user_input):
            return visit_answer
        v2_answer = self._try_brain_v2_recall_answer(user_input)
        if not self._is_brain_v2_authoritative_personal_recall_answer(v2_answer):
            return self._brain_v2_no_reviewed_message(user_input)
        return v2_answer or self._brain_v2_no_reviewed_message(user_input)

    def _try_brain_v2_recall_answer(self, user_input: str) -> Optional[str]:
        if not self.brain_v2:
            return None
        try:
            return self.brain_v2.try_answer_from_accepted_memories(user_input)
        except Exception as e:
            debug(f"[BRAIN_V2] Recall answer failed: {e}")
            return None

    @staticmethod
    def _is_positive_brain_v2_recall_answer(text: Optional[str]) -> bool:
        try:
            from core.brain_v2.recall_intent import is_positive_brain_v2_recall_answer

            return is_positive_brain_v2_recall_answer(text)
        except Exception:
            return bool(text and "from reviewed memory" in text.lower())

    @staticmethod
    def _is_brain_v2_conflict_review_answer(text: Optional[str]) -> bool:
        try:
            from core.brain_v2.recall_intent import is_brain_v2_conflict_review_answer

            return is_brain_v2_conflict_review_answer(text)
        except Exception:
            return False

    @staticmethod
    def _is_brain_v2_no_reviewed_memory_answer(text: Optional[str]) -> bool:
        try:
            from core.brain_v2.recall_intent import is_brain_v2_no_reviewed_memory_answer

            return is_brain_v2_no_reviewed_memory_answer(text)
        except Exception:
            return False

    @staticmethod
    def _is_brain_v2_authoritative_personal_recall_answer(text: Optional[str]) -> bool:
        try:
            from core.brain_v2.recall_intent import (
                is_brain_v2_authoritative_personal_recall_answer,
            )

            return is_brain_v2_authoritative_personal_recall_answer(text)
        except Exception:
            return False

    def _route_to_agent(self, user_input: str) -> Optional[str]:
        """Route input to best agent, but only for specific commands - not conversation"""
        scores = {}
        for name, agent in self.agents.items():
            scores[name] = agent.can_handle(user_input)

        if self._requires_brain_v2_personal_answer(user_input) and "research" in scores:
            scores["research"] = 0.0

        debug(f"[ROUTE] Agent scores: {scores}")

        best_agent = max(scores, key=scores.get)
        best_score = scores[best_agent]

        if best_agent == "memory" and (
            not self.legacy_memory_enabled or self._brain_v2_authority_enabled()
        ):
            return None

        # Only route to agent if confidence is high (> 0.6) - this is a specific command
        # Otherwise, let AI handle it (conversation goes to AI)
        if best_score < 0.5:
            return None

        try:
            response = self.agents[best_agent].handle(user_input)
            # If agent returns the same input (not a command), use AI instead
            if response == user_input.lower():
                return None
            return response
        except Exception as e:
            debug(f"[ROUTE] Agent error: {e}")
            return None

    def _is_self_identity_question(self, text: str) -> bool:
        q = (text or "").strip().lower().rstrip("?! .")
        return bool(
            re.search(
                r"\b(?:what(?:'s|s|\s+is)|who(?:'re|\s+are)|tell\s+me)\s+"
                r"(?:your|ur|u?r)\s+(?:name|nmae|identity)\b",
                q,
            )
            or re.search(r"\bwho\s+are\s+(?:you|u)\b", q)
            or q in {"your name", "ur name", "ur nmae", "name?", "who are you", "who r u"}
        )

    def _normalize_user_input_text(self, text: str) -> str:
        """Clean terminal prompt artifacts from pasted text before routing."""
        cleaned = (text or "").strip()
        while True:
            next_cleaned = re.sub(r"^\s*(?:you|user)\s*:\s*", "", cleaned, flags=re.I).strip()
            if next_cleaned == cleaned:
                return cleaned
            cleaned = next_cleaned

    def _is_ai_runtime_question(self, text: str) -> bool:
        q = (text or "").strip().lower().rstrip("?! .")
        provider_terms = r"(?:provider|ai\s+provider)"
        model_terms = r"(?:model|ai\s+model|llm)"
        fallback_terms = r"(?:fallback|fallbacks|backup\s+model|backup\s+models)"

        return bool(
            re.search(
                rf"\b(?:which|what)\s+(?:{provider_terms}|{model_terms}|{fallback_terms})\b",
                q,
            )
            or re.search(
                rf"\b(?:which|what)\s+(?:{provider_terms}|{model_terms})\s+"
                r"(?:are\s+you|are\s+u|r\s+u|u\s+r|do\s+you)\s+(?:using|usin|use)\b",
                q,
            )
            or re.search(
                rf"\b(?:{provider_terms}|{model_terms})\s+"
                r"(?:are\s+you|are\s+u|r\s+u|u\s+r)?\s*(?:using|usin|use)\b",
                q,
            )
            or re.search(
                r"\b(?:who|what)\s+(?:is|are)\s+(?:powering|running)\s+(?:you|hikari)\b",
                q,
            )
            or q in {
                "provider",
                "which provider",
                "current provider",
                "model",
                "which model",
                "current model",
                "fallbacks",
                "fallback models",
            }
        )

    def _get_ai_runtime_summary(self) -> str:
        router = getattr(self, "router", None) or get_router()

        try:
            info = router.get_routing_display()
        except Exception:
            info = {}

        provider = info.get("provider") or getattr(router, "_last_provider", None)
        model = info.get("model") or getattr(router, "_last_model", None)
        fallbacks = info.get("fallback_labels") or []
        last_provider = info.get("last_provider") or getattr(router, "_last_provider", None)
        last_model = info.get("last_model") or getattr(router, "_last_model", None)

        lines = [
            "I'm HIKARI.",
            f"Build: {self._get_build_id()}",
            f"Provider: {provider or 'unavailable'}",
            f"Model: {model or 'unavailable'}",
        ]

        if fallbacks:
            lines.append(f"Fallbacks: {', '.join(fallbacks)}")
        else:
            lines.append("Fallbacks: none available right now")

        if last_provider and last_model and (
            last_provider != provider or last_model != model
        ):
            lines.append(f"Last successful route: {last_provider}/{last_model}")

        return "\n".join(lines)

    def _get_build_id(self) -> str:
        try:
            from core.cli_status import get_build_id

            return get_build_id()
        except Exception:
            return "unknown"

    def _mentions_partner_context(self, text: str) -> bool:
        return bool(
            re.search(
                r"\b(?:gf|girlfriend|girl\s+friend|partner|boyfriend|boy\s+friend)\b",
                text or "",
                re.I,
            )
        )

    def _normalize_brain_memory_statement(self, user_input: str) -> str:
        """Resolve short pronoun/name partner statements before neural ingest.

        This keeps context in the orchestrator while the neural compiler stays
        mostly stateless.
        """
        text = (user_input or "").strip()
        if not text or self.speaker.last_contact_kind != "partner":
            return text
        if re.search(
            r"\b(?:my\s+)?(?:sister|brother|mom|mother|dad|father|parents?)\b",
            text,
            re.I,
        ):
            return text

        name_match = re.search(
            r"\b(?:her|his|their)\s+name\s+is\s+"
            r"(?P<name>[A-Za-z][a-zA-Z]+(?:\s+(?!she\b|he\b|they\b|shes\b|he's\b|she's\b|they're\b)[A-Za-z][a-zA-Z]+){0,3})",
            text,
            re.I,
        )
        bare_name_match = re.fullmatch(
            r"\s*(?P<name>[A-Za-z][a-zA-Z]+(?:\s+[A-Za-z][a-zA-Z]+){0,3})\s*[.!]?\s*",
            text,
            re.I,
        )

        name = ""
        if name_match:
            name = name_match.group("name").strip().title()
        elif bare_name_match and not self._looks_like_smalltalk_name(text):
            name = bare_name_match.group("name").strip().title()

        if name:
            pieces = [f"{name} is my girlfriend"]
            loc = self._extract_partner_location(text)
            school = self._extract_partner_school(text)
            if loc:
                pieces.append(f"she lives in {loc}")
            if school:
                pieces.append(f"she studies at {school}")
            return " and ".join(pieces)

        if re.search(r"\b(?:she|he|they|shes|he's|she's)\s+(?:is\s+)?my\s+girl\s*friend\b", text, re.I):
            return text.replace("girl friend", "girlfriend")
        return text

    def _extract_partner_location(self, text: str) -> str:
        m = re.search(
            r"\b(?:lives?\s+in|is\s+in|she'?s\s+in|he'?s\s+in|they'?re\s+in)\s+"
            r"(?:india\s+)?(?P<loc>[A-Za-z][a-zA-Z]+(?:\s+(?!studying\b|studing\b|studies\b|study\b|at\b|in\b)[A-Za-z][a-zA-Z]+)?)",
            text,
            re.I,
        )
        return m.group("loc").strip().title() if m else ""

    def _extract_partner_school(self, text: str) -> str:
        m = re.search(
            r"\bstud(?:y|ies|ying|ing)\s+(?:at|in)\s+"
            r"(?P<school>[A-Za-z][a-zA-Z]+(?:\s+(?!as\b|and\b|but\b|she\b|he\b|they\b)[A-Za-z][a-zA-Z]+){0,4})",
            text,
            re.I,
        )
        return m.group("school").strip().title() if m else ""

    def _is_casual_greeting(self, text: str) -> bool:
        from core.brain_v2.recall_intent import is_casual_greeting

        return is_casual_greeting(text)

    def _looks_like_smalltalk_name(self, text: str) -> bool:
        return self._is_casual_greeting(text) or (text or "").strip().lower() in {
            "yes",
            "yeah",
            "ok",
            "okay",
            "no",
            "bro",
        }

    def _get_ai_response(self, user_input: str, emotion: str = "neutral", emotion_score: float = 0.0) -> str:
        """Get AI response for general queries"""
        authority_on = self._brain_v2_authority_enabled()
        if authority_on and self._requires_brain_v2_personal_answer(user_input):
            return self._brain_v2_personal_recall_answer(user_input) or self._brain_v2_no_reviewed_message(
                user_input
            )

        # Build context (speaker-aware; avoid leaking primary-only prefs to guests)
        persona_ctx = self.personality.get_prompt_context(
            self.speaker,
            include_personal_facts=not authority_on,
            brain_v2_authority=authority_on,
        )
        if persona_ctx:
            context = f"User context: {persona_ctx}\n\n"
        else:
            context = ""

        memory_context = self._build_memory_first_context(user_input)
        if memory_context:
            context += f"{memory_context}\n\n"

        # Add emotion context
        if emotion != "neutral" and emotion_score > 0.4:
            context += f"User is feeling {emotion}. "

        # Build prompt
        system_prompt = f"""You are HIKARI, a helpful AI assistant.
Your assistant name is HIKARI. If asked your name or who you are, answer as HIKARI.
Do not answer self-identity questions with the underlying model provider or model name.
Adapt your responses to be:
- Formal level: {self.personality.traits['formality']:.0%} formal
- Verbose level: {self.personality.traits['verbosity']:.0%} detailed
- Humorous: {'yes' if self.personality.traits['humor'] > 0.5 else 'no'}
- Always helpful and friendly
- Treat the latest user message as controlling. Use older conversation context
  only to interpret follow-ups, and honor corrections instead of repeating the
  previous topic."""

        # Get AI response
        try:
            response = self.router.generate(
                user_input=user_input,
                system_prompt=system_prompt,
                context=context,
                max_tokens=500,
                temperature=0.7
            )
            return response if response else self._get_ai_unavailable_message()
        except Exception as e:
            debug(f"[AI] Error: {e}")
            return self._get_ai_unavailable_message()

    def _get_ai_unavailable_message(self) -> str:
        """Explain a missing or failed model route without reflecting errors."""
        try:
            info = self.router.get_routing_display()
        except Exception:
            info = {}
        provider = info.get("provider")
        if not provider or provider == "ollama":
            return (
                "No AI provider is available. Start and configure OmniRoute or "
                "9Router, configure a supported hosted provider, or start Ollama "
                "with an installed model."
            )
        return (
            "The configured AI provider is temporarily unavailable. Check the "
            "provider or local gateway and try again."
        )

    def run_voice_loop(self):
        """Run the menu-bar voice loop using the existing daemon service."""
        from services.hikari_service import HIKARI_Daemon

        HIKARI_Daemon().run()

    def _check_health(self, text: str):
        """Check for health indicators"""
        if self.emotional_iq.detect_emotion(text).get("sick", 0) > 0.5:
            self.voice_memory.is_sick_mode = True
            self.user_profile.log_mood("sick", 0.7, text)

    def _get_status_report(self) -> str:
        """Get system status"""
        memory_summary = self.memory.get_user_summary()

        status = f"""HIKARI Status
================
Agents: {len(self.agents)} active
Memory: {memory_summary.get('total_conversations', 0)} conversations
Facts: {memory_summary.get('facts_learned', 0)} learned
Neural memory: {"connected" if self.neural_memory_enabled else "not connected"}

Personality:
  Formal: {self.personality.traits['formality']:.0%}
  Verbose: {self.personality.traits['verbosity']:.0%}
  Humor: {self.personality.traits['humor']:.0%}
  Helpful: {self.personality.traits['helpfulness']:.0%}

Mood: {self.emotional_iq.current_mood}
"""
        if self.neural_memory_enabled and self.neural_memory:
            try:
                stats = self.neural_memory.get_memory_stats()
                status += (
                    f"Neural nodes: {stats.get('nodes', 0)}\n"
                    f"Neural edges: {stats.get('edges', 0)}\n"
                )
            except Exception as e:
                status += f"Neural memory stats unavailable: {e}\n"
        return status

    def _get_user_summary(self) -> str:
        """Get what HIKARI knows about the user (Brain v2 reviewed memories only by default)."""
        speaker = getattr(self, "speaker", None)
        if speaker and speaker.is_guest_speaker():
            return self._guest_personal_no_memory_reply("what do you know about me")
        if self._brain_v2_authority_enabled():
            if not self._brain_v2_runtime_ready():
                return self._brain_v2_unavailable_message()
            try:
                profile = self.brain_v2.build_user_profile_answer()
                if profile:
                    return profile
            except Exception as e:
                debug(f"[BRAIN_V2] Profile summary failed: {e}")
            return self._brain_v2_no_reviewed_message()

        neural_summary: Optional[str] = None
        try:
            neural_summary = self._get_legacy_brain().summarize_user()
        except Exception as e:
            debug(f"[MEMORY] Brain whoami failed: {e}")

        if neural_summary:
            return neural_summary

        name = self.personality.user_prefs.get("name") or self.user_profile.name or "you"
        prefs = self.personality.user_prefs

        summary = f"What I know about {name}:\n"

        if prefs.get("name"):
            summary += f"- Name: {prefs['name']}\n"
        if prefs.get("favorite_topics"):
            summary += f"- Interests: {', '.join(prefs['favorite_topics'][-3:])}\n"
        if prefs.get("health_concerns"):
            summary += f"- Health: {prefs['health_concerns'][-1]}\n"

        memory_count = len(self.memory.conversations)
        summary += f"- We've talked {memory_count} times\n"

        return summary

    def _get_memory_summary(self) -> str:
        """Get memory summary (legacy path; prefer _get_brain_v2_safe_recall_summary when authority on)."""
        return self._get_legacy_memory_summary()

    def _get_help(self) -> str:
        """Get help information"""
        return """HIKARI Commands
================
- "Remember that..." - Store facts
- "What do you know about me?" - User info
- Select a local text document in the Phase 1 client to request an explanation
- "Status" - System status
- Plus: Ask anything!

Legacy file, browser, scheduler, and Mac-control tools remain disabled until their
individual policy adapters and approval grants are complete."""


# Singleton
_orchestrator = None
_orchestrator_lock = threading.Lock()

# Backward-compatible name used by tests and older integrations.
Orchestrator = HIKARI_Orchestrator

def get_orchestrator() -> HIKARI_Orchestrator:
    global _orchestrator
    if _orchestrator is None:
        with _orchestrator_lock:
            if _orchestrator is None:
                _orchestrator = HIKARI_Orchestrator()
    return _orchestrator
