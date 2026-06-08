"""Neural-brain personal memory — generic names/places only."""

from core.brain import HikariBrain
from core.neural_memory.models import ContextPacket
from core.neural_memory.memory_compiler import MemoryCompiler
from core.neural_memory.models import MemoryNode, NodeType


class FakeNeural:
    def __init__(self, nodes=None):
        self.nodes = list(nodes or [])
        self.stored: list[str] = []

    def init_neural_memory(self):
        return True

    def smart_query(self, _query):
        return self.nodes

    def get_memory_context(self, query):
        return ContextPacket(
            query=query,
            relevant_nodes=self.nodes,
            confidence=0.82 if self.nodes else 0.0,
            retrieval_strategies_used=["fake_context"],
        )

    def learn_from_text(self, text):
        compiler = MemoryCompiler()
        compiler.storage = _NoopStorage(self)
        for node in compiler.extract_people_and_relationships(text, "test_user"):
            self._upsert(node)
        for node in compiler.extract_structured_personal_facts(text, "test_user"):
            self._upsert(node)
        self.stored.append(text)
        return {"success": True}

    def remember(self, user_msg, assistant_msg, metadata=None):
        self.stored.append(user_msg)
        return {"success": True}

    def _upsert(self, node: MemoryNode):
        key = (node.node_type, node.name.lower())
        for i, existing in enumerate(self.nodes):
            if (existing.node_type, existing.name.lower()) == key:
                self.nodes[i] = node
                return
        self.nodes.append(node)


class SearchBlindFakeNeural(FakeNeural):
    def smart_query(self, _query):
        return []


class _NoopStorage:
    """Compiler stub — capture nodes in FakeNeural instead of SQLite."""

    def __init__(self, fake: FakeNeural):
        self.fake = fake

    def upsert_node(self, node: MemoryNode) -> int:
        self.fake._upsert(node)
        return 1

    def get_node_by_name(self, *args, **kwargs):
        return None


class _MemoryStorageStub:
    def __init__(self):
        self.nodes: list[MemoryNode] = []

    def upsert_node(self, node: MemoryNode) -> int:
        node.id = len(self.nodes) + 1
        self.nodes.append(node)
        return node.id

    def get_node_by_name(self, name, node_type=None, user_id=None):
        for node in self.nodes:
            if node.name == name and (node_type is None or node.node_type == node_type):
                if user_id is None or node.user_id == user_id:
                    return node
        return None

    def upsert_edge(self, _edge):
        return 1


def _brain_with_text(*statements: str) -> HikariBrain:
    fake = FakeNeural()
    brain = HikariBrain(fake)
    for s in statements:
        brain.remember_fact(s)
    return brain


def test_permanent_vs_current_location():
    brain = _brain_with_text(
        "i live in River City because I study at North State University",
        "i am in Lake Town for summer break",
    )
    home = brain.answer("where do i live?")
    now = brain.answer("where am i now?")
    assert home and "River City" in home.text
    assert now and "Lake Town" in now.text
    assert "summer" in now.text.lower()


def test_dad_memory():
    brain = _brain_with_text("my dads name is Rowan and he lives in Lake Town")
    ans = brain.answer("dad?")
    assert ans and "Rowan" in ans.text and "Lake Town" in ans.text


def test_mom_with_dad_context():
    brain = _brain_with_text(
        "my dads name is Rowan and he lives in Lake Town",
        "my mom name is Lina and she lives with my dad",
    )
    ans = brain.answer("mom?")
    assert ans and "Lina" in ans.text


def test_partner_and_study():
    brain = _brain_with_text(
        "Priya is my girlfriend and she studies at East Medical College",
    )
    gf = brain.answer("gf?")
    study = brain.answer("where does my gf study?")
    assert gf and "Priya" in gf.text
    assert study and "East Medical" in study.text


def test_who_is_known_partner_blocks_research():
    from agents.research import ResearchAgent

    fake = FakeNeural()
    brain = HikariBrain(fake)
    brain.remember_fact("Priya is my girlfriend and she lives in South Town")
    agent = ResearchAgent(eager_legacy_brain=True)
    agent._brain = brain
    assert agent.can_handle("who is priya?") < 0.15
    assert agent.can_handle("who is Ada Lovelace?") >= 0.5


def test_compiler_extracts_structured_metadata():
    compiler = MemoryCompiler()
    nodes = compiler.extract_structured_personal_facts(
        "i live in River City because i study at North State University", "u1"
    )
    kinds = {(n.metadata or {}).get("kind") for n in nodes}
    assert "permanent_home" in kinds
    assert "education" in kinds

    current = compiler.extract_structured_personal_facts(
        "i am in Lake Town for summer break", "u1"
    )
    assert any((n.metadata or {}).get("kind") == "current_location" for n in current)


def test_low_confidence_returns_none():
    brain = HikariBrain(FakeNeural([]))
    assert brain.answer("where am i now?") is None


def test_dad_name_parser_excludes_and():
    compiler = MemoryCompiler()
    nodes = compiler.extract_structured_personal_facts(
        "my dads name is Rowan and he lives in Lake Town", "u1"
    )
    father = next(
        n for n in nodes if (n.metadata or {}).get("relation") == "father"
    )
    assert father.metadata["person_name"] == "Rowan"
    assert "And" not in father.metadata["person_name"]


def test_sister_full_name_update_and_recall():
    brain = _brain_with_text(
        "Maya is my sister and she studies at North State University",
        "my sisters full name is Maya Beth",
    )
    ans = brain.answer("whats my sisters full name?")
    assert ans and "Maya Beth" in ans.text


def test_mom_shares_dad_location_and_current():
    brain = _brain_with_text(
        "my dads name is Rowan and he lives in Lake Town",
        "my mom name is Lina and she also lives in Lake Town with my dad and im here with them now",
    )
    mom = brain.answer("mom?")
    now = brain.answer("where am i now?")
    assert mom and "Lina" in mom.text and "Lake Town" in mom.text
    assert now and "Lake Town" in now.text


def test_return_flight_recall():
    brain = _brain_with_text(
        "i have a return flight on july 3 early morning and i will reach River City on july 4 early morning local time",
    )
    ans = brain.answer("whens my return?")
    assert ans
    assert "July 3" in ans.text or "july 3" in ans.text.lower()
    assert "River City" in ans.text
    assert "July 4" in ans.text or "july 4" in ans.text.lower()


def test_weather_outside_uses_current_location(monkeypatch):
    from agents.research import ResearchAgent

    fake = FakeNeural()
    brain = HikariBrain(fake)
    brain.remember_fact("i am in Lake Town for summer break")
    agent = ResearchAgent(eager_legacy_brain=True)
    agent._brain = brain
    captured = {}

    def fake_weather(loc):
        captured["loc"] = loc
        return f"Weather in {loc}: sunny"

    monkeypatch.setattr(agent, "get_weather", fake_weather)
    out = agent.handle("whats the weather outside?")
    assert out == "Weather in Lake Town: sunny"
    assert captured["loc"] == "Lake Town"


def test_whoami_summary_filters_unrelated_person_nodes():
    nodes = [
        MemoryNode(
            node_type=NodeType.PERSON.value,
            name="Stale Test",
            content="mentioned in old QA",
            salience=0.4,
        ),
        MemoryNode(
            node_type=NodeType.LOCATION.value,
            name="current_location",
            content="User is currently in Lake Town.",
            metadata={"kind": "current_location", "location": "Lake Town"},
            salience=0.97,
        ),
        MemoryNode(
            node_type=NodeType.FACT.value,
            name="user:father",
            content="User's father is Rowan and lives in Lake Town.",
            metadata={
                "kind": "relation",
                "relation": "father",
                "person_name": "Rowan",
                "location": "Lake Town",
            },
            salience=0.96,
        ),
    ]
    summary = HikariBrain(FakeNeural(nodes)).summarize_user()
    assert summary
    assert "Rowan" in summary
    assert "Lake Town" in summary
    assert "Stale Test" not in summary


def test_name_parser_does_not_capture_but_and_keeps_official_name():
    brain = _brain_with_text(
        "my name is alex but offical name is alexander river"
    )
    preferred = brain.answer("whats my name?")
    official = brain.answer("what is my official name?")
    summary = brain.summarize_user()

    assert preferred and preferred.text == "Your name is Alex."
    assert official and official.text == "Your official name is Alexander River."
    assert summary and "Alex But" not in summary
    assert "Official name: Alexander River" in summary


def test_generic_self_intro_does_not_capture_but():
    compiler = MemoryCompiler()
    nodes = compiler.extract_people_and_relationships(
        "my name is alex but offical name is alexander river",
        "u1",
    )
    person_names = {
        n.name for n in nodes if n.node_type == NodeType.PERSON.value
    }
    assert "Alex" in person_names
    assert "Alex But" not in person_names


def test_compile_and_store_recreates_missing_user_anchor():
    compiler = MemoryCompiler()
    storage = _MemoryStorageStub()
    compiler.storage = storage

    compiler.compile_and_store(
        "i like local tools",
        "",
        {"user_id": "fresh_user"},
    )

    anchor = storage.get_node_by_name(
        "fresh_user", NodeType.PERSON.value, "fresh_user"
    )
    assert anchor is not None
    assert (anchor.metadata or {}).get("kind") == "identity_anchor"


def test_compile_and_store_does_not_learn_assistant_self_identity():
    compiler = MemoryCompiler()
    storage = _MemoryStorageStub()
    compiler.storage = storage

    compiler.compile_and_store(
        "hi",
        "I am HIKARI, your assistant.",
        {"user_id": "fresh_user"},
    )

    assert not storage.get_node_by_name("Hikari", NodeType.PERSON.value, "fresh_user")


def test_brain_context_packet_layers_and_scores_memory():
    nodes = [
        MemoryNode(
            node_type=NodeType.PERSON.value,
            name="Alex",
            content="User's brother",
            metadata={"kind": "relation", "relation": "brother", "person_name": "Alex"},
            salience=0.95,
        ),
        MemoryNode(
            node_type=NodeType.SKILL.value,
            name="Repair Flow",
            content="Run doctor, tests, and privacy scan before pushing.",
            salience=0.88,
        ),
        MemoryNode(
            node_type=NodeType.PERSON.value,
            name="Stale Low",
            content="old unstructured mention",
            salience=0.2,
        ),
    ]
    packet = HikariBrain(FakeNeural(nodes)).build_context_packet(
        "who is my brother?"
    )

    assert packet.confidence == 0.82
    assert packet.strategies == ["fake_context"]
    assert packet.items[0].name == "Alex"
    assert packet.items[0].layer == "semantic"
    assert any(item.layer == "procedural" for item in packet.items)
    assert all(item.name != "Stale Low" for item in packet.items)


def test_brain_prompt_context_is_compact_and_layered():
    brain = _brain_with_text(
        "Priya is my girlfriend and she studies at East Medical College",
        "i live in River City because i study at North State University",
    )

    prompt = brain.build_prompt_context("where does my girlfriend study?")

    assert "[Brain context]" in prompt
    assert "semantic:" in prompt
    assert "Priya" in prompt
    assert len(prompt.splitlines()) < 12


def test_question_words_are_not_stored_as_partner_names():
    compiler = MemoryCompiler()
    nodes = compiler.extract_structured_personal_facts("whos is my gf", "u1")
    assert not any(
        (n.metadata or {}).get("relation") == "partner" for n in nodes
    )


def test_orchestrator_normalizes_partner_pronoun_fact_without_full_init():
    from core.orchestrator import HIKARI_Orchestrator

    class Speaker:
        last_contact_kind = "partner"

    orch = HIKARI_Orchestrator.__new__(HIKARI_Orchestrator)
    orch.speaker = Speaker()

    normalized = orch._normalize_brain_memory_statement(
        "her name is Priya Shah she's in South Town studying in East Medical College as a medical student"
    )

    assert normalized.startswith("Priya Shah is my girlfriend")
    assert "she lives in South Town" in normalized
    assert "she studies at East Medical College" in normalized


def test_orchestrator_partner_location_stops_before_studing_typo():
    from core.orchestrator import HIKARI_Orchestrator

    class Speaker:
        last_contact_kind = "partner"

    orch = HIKARI_Orchestrator.__new__(HIKARI_Orchestrator)
    orch.speaker = Speaker()

    normalized = orch._normalize_brain_memory_statement(
        "her name is Priya she's in india Lakeville studing in East Medical College"
    )

    assert "she lives in Lakeville" in normalized
    assert "Lakeville Studing" not in normalized


def test_orchestrator_does_not_rewrite_family_statement_as_partner():
    from core.orchestrator import HIKARI_Orchestrator

    class Speaker:
        last_contact_kind = "partner"

    orch = HIKARI_Orchestrator.__new__(HIKARI_Orchestrator)
    orch.speaker = Speaker()

    text = "my sister her name is Maya and she studies in North Valley University"

    assert orch._normalize_brain_memory_statement(text) == text


def test_loose_sister_name_statement_with_school_recall():
    brain = _brain_with_text(
        "okay my sister her name is Maya she studies in North Valley University she is studing CSE"
    )

    sister = brain.answer("who is my sister?")
    study = brain.answer("where does my sister study?")

    assert sister and "Maya" in sister.text
    assert "North Valley University" in sister.text
    assert study and "North Valley University" in study.text


def test_location_parser_stops_before_travel_and_study_words():
    brain = _brain_with_text(
        "i live in River City studing in North State University",
        "now i am in Lake Town i flew from River City on may 14",
    )

    home = brain.answer("where do i live?")
    now = brain.answer("where am i now?")

    assert home and home.text == "You live in River City."
    assert now and now.text.startswith("You're currently in Lake Town")
    assert "Flew" not in now.text


def test_location_correction_replaces_bad_current_location():
    brain = _brain_with_text(
        "now i am in Lake Town i flew from River City on may 14",
        "no it's just called Lake Town",
    )

    now = brain.answer("where am i now?")

    assert now and now.text == "You're currently in Lake Town."


def test_weather_outside_cleans_stale_location(monkeypatch):
    from agents.research import ResearchAgent

    node = MemoryNode(
        node_type=NodeType.LOCATION.value,
        name="current_location",
        content="User is currently in Lake Town Flew.",
        metadata={"kind": "current_location", "location": "Lake Town Flew"},
        salience=0.97,
    )
    agent = ResearchAgent(eager_legacy_brain=True)
    agent._brain = HikariBrain(FakeNeural([node]))
    captured = {}

    def fake_weather(loc):
        captured["loc"] = loc
        return f"Weather in {loc}: sunny"

    monkeypatch.setattr(agent, "get_weather", fake_weather)
    out = agent.handle("whats the weather outside?")

    assert out == "Weather in Lake Town: sunny"
    assert captured["loc"] == "Lake Town"


def test_bare_relation_fragment_is_question_not_memory_statement():
    brain = HikariBrain(FakeNeural())

    assert not brain.is_memory_statement("my sister")
    ans = brain.answer("my sister")

    assert ans
    assert "don't have your sister saved yet" in ans.text


def test_relation_recall_uses_context_packet_when_fts_misses():
    fake = SearchBlindFakeNeural()
    brain = HikariBrain(fake)
    brain.remember_fact(
        "okay i will give u more information my sister her name is Maya and she used to live in Lake Town with my parents but now she went to university so she is in River City studing CSE in North Valley University"
    )

    sister = brain.answer("do u know my sister?")
    summary = brain.summarize_user()

    assert sister and "Maya" in sister.text
    assert "North Valley University" in sister.text
    assert summary and "Sister: Maya" in summary


def test_partner_event_question_asks_for_event_after_identifying_partner():
    brain = _brain_with_text("Priya Rao is my girlfriend")

    ans = brain.answer("do u know what my gf did?")

    assert ans
    assert "Priya Rao" in ans.text
    assert "What did she do" in ans.text
