from core.brain import HikariBrain
from core.neural_memory.models import MemoryNode, NodeType


class FakeNeural:
    def __init__(self, nodes):
        self.nodes = nodes

    def init_neural_memory(self):
        return True

    def smart_query(self, _query):
        return self.nodes

    def remember(self, *_args, **_kwargs):
        return {"success": True}


def test_brain_filters_question_artifacts_and_prefers_full_relative_name():
    nodes = [
        MemoryNode(
            node_type=NodeType.FACT.value,
            name="user's sister is Who",
            content="who is my sister? Your sister is Maya. School: North City University.",
            salience=0.99,
        ),
        MemoryNode(
            node_type=NodeType.PERSON.value,
            name="Maya Beth",
            content="User's sister",
            metadata={
                "kind": "relation",
                "relation": "sister",
                "person_name": "Maya Beth",
            },
            salience=0.9,
        ),
        MemoryNode(
            node_type=NodeType.FACT.value,
            name="user's sister is Maya",
            content="maya is my sister and she studies cs at East State University",
            salience=0.91,
        ),
    ]

    answer = HikariBrain(FakeNeural(nodes)).answer("whats my sisters name?")

    assert answer
    assert "Maya Beth" in answer.text
    assert "Who" not in answer.text
    assert "North City" not in answer.text


def test_brain_partner_answer_does_not_steal_sister_school():
    nodes = [
        MemoryNode(
            node_type=NodeType.PERSON.value,
            name="Priya Rao",
            content="User's partner",
            metadata={
                "kind": "relation",
                "relation": "partner",
                "person_name": "Priya Rao",
                "location": "South Town",
            },
            salience=0.9,
        ),
        MemoryNode(
            node_type=NodeType.FACT.value,
            name="user:partner",
            content="User's partner is Priya Rao and lives in South Town.",
            metadata={
                "kind": "relation",
                "relation": "partner",
                "person_name": "Priya Rao",
                "location": "South Town",
            },
            salience=0.96,
        ),
        MemoryNode(
            node_type=NodeType.FACT.value,
            name="relative studies cs",
            content="studies CS at East State University",
            salience=0.9,
        ),
    ]

    answer = HikariBrain(FakeNeural(nodes)).answer("whos my gf?")

    assert answer
    assert "Priya Rao" in answer.text
    assert "South Town" in answer.text
    assert "East State" not in answer.text


def test_brain_does_not_use_missing_parent_json_fallback():
    brain = HikariBrain(FakeNeural([]))
    assert brain.is_personal_memory_question("do u know my dad?")
    answer = brain.answer("do u know my dad?")
    assert answer
    assert "don't have your father saved yet" in answer.text


def test_brain_does_not_treat_parent_statement_as_question():
    brain = HikariBrain(FakeNeural([]))

    assert brain.is_memory_statement("my dads name is Rowan and he lives in Lakeside")
    assert not brain.is_personal_memory_question(
        "my dads name is Rowan and he lives in Lakeside"
    )
    assert brain.is_personal_memory_question("whos my dad")
