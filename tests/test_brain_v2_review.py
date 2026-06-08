"""Brain v2 guided review CLI — interactive flow and safe promotion."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core.brain_v2.cli import (
    PROMOTE_CONFIRM_TOKEN,
    ReviewAction,
    ReviewIO,
    ReviewStepResult,
    apply_review_action,
    confirm_promote,
    format_candidate_review_display,
    format_duplicate_markers,
    get_pending_review_candidates,
    normalize_review_action,
    run_interactive_review,
)
from core.brain_v2.coordinator import BrainV2Coordinator
from core.brain_v2.schemas import MemoryCandidateStatus
from core.path_literals import DOT_HIKARI, EPISODES_DB, HIKARI_MEMORY_DB

REPO_ROOT = Path(__file__).resolve().parent.parent


class ScriptedReviewIO(ReviewIO):
    """Collects output and serves scripted input lines (no real stdin)."""

    def __init__(self, inputs: list[str]):
        self._inputs = iter(inputs)
        self.lines: list[str] = []
        super().__init__(write=self._record_write, readline=self._read_scripted)

    def _record_write(self, text: str) -> None:
        self.lines.append(text)

    def _read_scripted(self, prompt: str) -> str:
        self.lines.append(prompt.rstrip())
        return next(self._inputs)


@pytest.fixture
def episode_db(tmp_path):
    from core.brain_v2 import EpisodeStore

    return EpisodeStore(db_path=tmp_path / "review_v2.db")


def _seed_candidate(
    episode_db,
    *,
    statement: str = "Owner A lives in City A.",
    metadata: dict | None = None,
):
    from core.brain_v2.consolidation_pipeline import EpisodeConsolidationPipeline

    episode_id = episode_db.create_episode("review-session")
    episode_db.add_turn(
        episode_id,
        f"Remember this: {statement}",
        is_user=True,
    )
    episode_db.add_turn(
        episode_id,
        "Got it. I'll remember that.",
        is_user=False,
        speaker_label="assistant",
    )
    candidates = EpisodeConsolidationPipeline(episode_db).process_episode(episode_id)[1]
    assert candidates
    cand = candidates[0]
    if metadata:
        merged = dict(cand.metadata or {})
        merged.update(metadata)
        episode_db.update_candidate_metadata(cand.candidate_id, merged)
        cand = episode_db.get_candidate(cand.candidate_id)
    return cand, episode_id


def test_normalize_review_action():
    assert normalize_review_action("a") == ReviewAction.ACCEPT
    assert normalize_review_action("P") == ReviewAction.ACCEPT_PROMOTE
    assert normalize_review_action("reject") == ReviewAction.REJECT
    assert normalize_review_action(" skip ") == ReviewAction.SKIP
    assert normalize_review_action("q") == ReviewAction.QUIT
    assert normalize_review_action("x") is None


def test_format_duplicate_markers():
    assert format_duplicate_markers({}) == "—"
    assert format_duplicate_markers({"duplicate_of": "abc"}) == "dup"
    assert (
        format_duplicate_markers(
            {"duplicate_of": "abc", "duplicate_of_existing_memory": "mem-1"}
        )
        == "dup, exists"
    )


def test_format_candidate_review_display_shows_source_segments(episode_db):
    cand, _episode_id = _seed_candidate(
        episode_db,
        statement="Guest B prefers tea over coffee.",
    )
    coord = BrainV2Coordinator(store=episode_db)
    text = format_candidate_review_display(coord, cand)
    assert cand.candidate_id in text
    assert cand.candidate_type in text
    assert "Guest B prefers tea over coffee." in text
    assert "[user]" in text
    assert "Remember this:" in text


def test_confirm_promote_requires_exact_token():
    io = ScriptedReviewIO(["promote", PROMOTE_CONFIRM_TOKEN])
    assert not confirm_promote(io)
    assert confirm_promote(io)
    assert PROMOTE_CONFIRM_TOKEN in " ".join(io.lines)


def test_confirm_promote_rejects_whitespace_padded_token():
    io = ScriptedReviewIO([f"  {PROMOTE_CONFIRM_TOKEN}  "])
    assert not confirm_promote(io)


def test_apply_review_action_accept_no_promote(episode_db):
    cand, _ = _seed_candidate(episode_db)
    coord = BrainV2Coordinator(store=episode_db)
    coord.promoter.promote = MagicMock(return_value="should-not-run")
    io = ScriptedReviewIO([])

    step = apply_review_action(coord, cand.candidate_id, ReviewAction.ACCEPT, io)

    assert step == ReviewStepResult.NEXT
    coord.promoter.promote.assert_not_called()
    stored = episode_db.get_candidate(cand.candidate_id)
    assert stored.review_status == MemoryCandidateStatus.ACCEPTED.value
    assert "without neural promotion" in " ".join(io.lines)


def test_apply_review_action_reject_with_prefix(episode_db):
    cand, _ = _seed_candidate(episode_db, statement="Owner A works in City A.")
    coord = BrainV2Coordinator(store=episode_db)
    prefix = cand.candidate_id[:8]
    io = ScriptedReviewIO([])

    step = apply_review_action(coord, prefix, ReviewAction.REJECT, io)

    assert step == ReviewStepResult.NEXT
    stored = episode_db.get_candidate(cand.candidate_id)
    assert stored.review_status == MemoryCandidateStatus.REJECTED.value


def test_apply_review_action_promote_cancelled_stays_pending(episode_db):
    cand, _ = _seed_candidate(episode_db)
    coord = BrainV2Coordinator(store=episode_db)
    coord.promoter.promote = MagicMock(return_value="brain_v2:city-a")
    io = ScriptedReviewIO(["NOPE"])

    step = apply_review_action(
        coord, cand.candidate_id, ReviewAction.ACCEPT_PROMOTE, io
    )

    assert step == ReviewStepResult.STAY
    coord.promoter.promote.assert_not_called()
    stored = episode_db.get_candidate(cand.candidate_id)
    assert stored.review_status == MemoryCandidateStatus.PENDING.value
    assert "Promotion cancelled" in " ".join(io.lines)


def test_apply_review_action_promote_with_confirmation(episode_db):
    from core.brain_v2 import cli as brain_cli

    cand, _ = _seed_candidate(episode_db, statement="Owner A lives in City A.")
    coord = BrainV2Coordinator(store=episode_db)
    coord.promoter.promote = MagicMock(return_value="brain_v2:city-a")
    io = ScriptedReviewIO([PROMOTE_CONFIRM_TOKEN])

    with patch.object(brain_cli, "_coordinator_promote", return_value=coord):
        step = apply_review_action(
            coord, cand.candidate_id, ReviewAction.ACCEPT_PROMOTE, io
        )

    assert step == ReviewStepResult.NEXT
    coord.promoter.promote.assert_called_once()
    stored = episode_db.get_candidate(cand.candidate_id)
    assert stored.review_status == MemoryCandidateStatus.ACCEPTED.value
    assert "Promoted to neural memory: brain_v2:city-a" in " ".join(io.lines)


def test_run_interactive_review_accept_skip_quit(episode_db):
    first, _ = _seed_candidate(episode_db, statement="Owner A lives in City A.")
    second, _ = _seed_candidate(episode_db, statement="Guest B visits City A often.")
    coord = BrainV2Coordinator(store=episode_db)
    coord.promoter.promote = MagicMock(return_value="unused")
    io = ScriptedReviewIO(["a", "s", "q"])

    rc = run_interactive_review(coordinator=coord, io=io)

    assert rc == 0
    statuses = {
        c.candidate_id: episode_db.get_candidate(c.candidate_id).review_status
        for c in (first, second)
    }
    assert MemoryCandidateStatus.ACCEPTED.value in statuses.values()
    assert MemoryCandidateStatus.PENDING.value in statuses.values()
    assert list(statuses.values()).count(MemoryCandidateStatus.ACCEPTED.value) == 1
    assert "Review complete." in " ".join(io.lines) or "Review session ended." in " ".join(
        io.lines
    )


def test_run_interactive_review_promote_requires_double_confirm(episode_db):
    cand, _ = _seed_candidate(episode_db)
    coord = BrainV2Coordinator(store=episode_db)
    coord.promoter.promote = MagicMock(return_value="brain_v2:safe")
    io = ScriptedReviewIO(["p", "nope", "a"])

    rc = run_interactive_review(coordinator=coord, io=io)

    assert rc == 0
    coord.promoter.promote.assert_not_called()
    stored = episode_db.get_candidate(cand.candidate_id)
    assert stored.review_status == MemoryCandidateStatus.ACCEPTED.value


def test_run_interactive_review_empty_pending(episode_db):
    coord = BrainV2Coordinator(store=episode_db)
    io = ScriptedReviewIO([])

    rc = run_interactive_review(coordinator=coord, io=io)

    assert rc == 0
    assert "No pending Brain v2 memory candidates." in io.lines[0]


def test_get_pending_review_candidates_filters_reject_queue(episode_db):
    keep, _ = _seed_candidate(episode_db, statement="Owner A lives in City A.")
    from core.brain_v2.candidate_quality import QUALITY_REJECT
    from core.brain_v2.schemas import MemoryCandidate
    import uuid

    rejected = MemoryCandidate(
        candidate_id=str(uuid.uuid4()),
        episode_id=keep.episode_id,
        statement="noise fragment",
        metadata={"quality_label": QUALITY_REJECT},
    )
    episode_db.save_candidates([rejected])
    coord = BrainV2Coordinator(store=episode_db)

    pending = get_pending_review_candidates(coord)

    assert len(pending) == 1
    assert pending[0].candidate_id == keep.candidate_id


def test_cmd_review_via_run_brain_v2_cli(episode_db):
    from core.brain_v2 import cli as brain_cli

    _seed_candidate(episode_db, statement="Owner A lives in City A.")
    coord = BrainV2Coordinator(store=episode_db)

    with patch.object(brain_cli, "_coordinator_readonly", return_value=coord):
        with patch.object(brain_cli, "run_interactive_review", return_value=0) as mock_review:
            assert brain_cli.run_brain_v2_cli("review") == 0
            mock_review.assert_called_once()


def test_cli_accept_without_confirm_does_not_promote(episode_db):
    from core.brain_v2 import cli as brain_cli
    from unittest.mock import MagicMock, patch

    cand, _ = _seed_candidate(episode_db)
    coord = BrainV2Coordinator(store=episode_db)
    coord.promoter.promote = MagicMock(return_value="should-not-run")

    def _fail_coordinator(*_args, **_kwargs):
        raise AssertionError("_coordinator_promote must not be called without PROMOTE token")

    with patch.object(brain_cli, "_coordinator_readonly", return_value=coord):
        with patch.object(brain_cli, "_coordinator_promote", side_effect=_fail_coordinator):
            assert brain_cli.run_brain_v2_cli("accept", cand.candidate_id) == 0
    coord.promoter.promote.assert_not_called()


def test_cli_accept_no_promote_explicit(episode_db):
    from core.brain_v2 import cli as brain_cli
    from unittest.mock import MagicMock, patch

    cand, _ = _seed_candidate(episode_db)
    coord = BrainV2Coordinator(store=episode_db)
    coord.promoter.promote = MagicMock(return_value="should-not-run")

    def _fail_coordinator(*_args, **_kwargs):
        raise AssertionError("_coordinator_promote must not be called for accept_no_promote")

    with patch.object(brain_cli, "_coordinator_readonly", return_value=coord):
        with patch.object(brain_cli, "_coordinator_promote", side_effect=_fail_coordinator):
            assert brain_cli.run_brain_v2_cli("accept_no_promote", cand.candidate_id) == 0
    coord.promoter.promote.assert_not_called()


def test_cli_accept_wrong_confirm_token_fails(episode_db, capsys):
    from core.brain_v2 import cli as brain_cli

    cand, _ = _seed_candidate(episode_db)
    coord = BrainV2Coordinator(store=episode_db)
    coord.promoter.promote = MagicMock(return_value="should-not-run")

    with patch.object(brain_cli, "_coordinator_readonly", return_value=coord):
        with patch.object(
            brain_cli,
            "_coordinator_promote",
            side_effect=AssertionError("_coordinator_promote must not run"),
        ):
            rc = brain_cli.run_brain_v2_cli(
                "accept", cand.candidate_id, confirm_promote="not-promote"
            )
    assert rc == 1
    coord.promoter.promote.assert_not_called()
    assert "Invalid --confirm-promote" in capsys.readouterr().err


def test_cli_accept_lowercase_promote_token_fails(episode_db):
    from core.brain_v2 import cli as brain_cli

    cand, _ = _seed_candidate(episode_db)
    coord = BrainV2Coordinator(store=episode_db)
    coord.promoter.promote = MagicMock(return_value="should-not-run")

    with patch.object(brain_cli, "_coordinator_readonly", return_value=coord):
        with patch.object(
            brain_cli,
            "_coordinator_promote",
            side_effect=AssertionError("_coordinator_promote must not run"),
        ):
            rc = brain_cli.run_brain_v2_cli(
                "accept", cand.candidate_id, confirm_promote="promote"
            )
    assert rc == 1
    coord.promoter.promote.assert_not_called()


def test_cli_accept_without_confirm_promote_never_promotes(episode_db):
    """Promote lock: accept without --confirm-promote must never call promoter."""
    from core.brain_v2 import cli as brain_cli

    cand, _ = _seed_candidate(episode_db)
    coord = BrainV2Coordinator(store=episode_db)
    coord.promoter.promote = MagicMock(return_value="should-not-run")

    def _fail_coordinator(*_args, **_kwargs):
        raise AssertionError("_coordinator_promote must not be called without PROMOTE token")

    with patch.object(brain_cli, "_coordinator_readonly", return_value=coord):
        with patch.object(brain_cli, "_coordinator_promote", side_effect=_fail_coordinator):
            assert brain_cli.run_brain_v2_cli("accept", cand.candidate_id) == 0
    coord.promoter.promote.assert_not_called()
    assert (
        episode_db.get_candidate(cand.candidate_id).review_status
        == MemoryCandidateStatus.ACCEPTED.value
    )


def test_cli_accept_with_exact_promote_confirms(episode_db, capsys):
    from core.brain_v2 import cli as brain_cli

    cand, _ = _seed_candidate(episode_db)
    coord = BrainV2Coordinator(store=episode_db)
    coord.promoter.promote = MagicMock(return_value="brain_v2:city-a")

    with patch.object(brain_cli, "_coordinator_promote", return_value=coord):
        assert (
            brain_cli.run_brain_v2_cli(
                "accept",
                cand.candidate_id,
                confirm_promote=PROMOTE_CONFIRM_TOKEN,
            )
            == 0
        )
    coord.promoter.promote.assert_called_once()
    assert "Promoted to neural memory" in capsys.readouterr().out


def test_accept_no_promote_never_calls_coordinator_promote(episode_db):
    """accept_no_promote must use readonly coordinator only."""
    from core.brain_v2 import cli as brain_cli

    cand, _ = _seed_candidate(episode_db)
    coord = BrainV2Coordinator(store=episode_db)
    coord.promoter.promote = MagicMock(return_value="should-not-run")

    def _fail_promote(*_args, **_kwargs):
        raise AssertionError("_coordinator_promote must not be called")

    with patch.object(brain_cli, "_coordinator_readonly", return_value=coord) as mock_ro:
        with patch.object(brain_cli, "_coordinator_promote", side_effect=_fail_promote):
            assert brain_cli.cmd_accept(cand.candidate_id, promote=False) == 0
    mock_ro.assert_called_once()
    coord.promoter.promote.assert_not_called()


def test_cmd_accept_bare_never_inits_neural(episode_db):
    """Bare accept (promote=False) must not call init_neural_memory."""
    from core.brain_v2 import cli as brain_cli

    cand, _ = _seed_candidate(episode_db)
    coord = BrainV2Coordinator(store=episode_db)

    with patch.object(brain_cli, "_coordinator_readonly", return_value=coord):
        with patch.object(brain_cli, "_coordinator_promote") as mock_promote:
            with patch(
                "core.neural_memory_bridge.init_neural_memory",
                side_effect=AssertionError("init_neural_memory must not run"),
            ):
                assert brain_cli.cmd_accept(cand.candidate_id, promote=False) == 0
    mock_promote.assert_not_called()


def test_cmd_accept_with_promote_may_init_neural(episode_db):
    """Promote=True path may initialize neural memory via _coordinator_promote."""
    from core.brain_v2 import cli as brain_cli

    cand, _ = _seed_candidate(episode_db)
    coord = BrainV2Coordinator(store=episode_db)
    coord.promoter.promote = MagicMock(return_value="brain_v2:ok")
    init_calls: list[bool] = []

    def _promote_with_init(*_args, **_kwargs):
        init_calls.append(True)
        return coord

    with patch.object(brain_cli, "_coordinator_promote", side_effect=_promote_with_init):
        assert brain_cli.cmd_accept(cand.candidate_id, promote=True) == 0
    assert init_calls
    coord.promoter.promote.assert_called_once()


def _seed_pending_candidate_subprocess(db_root: Path) -> str:
    """Seed one pending candidate under db_root (not under HOME/.hikari)."""
    import os
    import subprocess
    import sys

    db_root.mkdir(parents=True, exist_ok=True)
    db_path = db_root / "brain_v2" / EPISODES_DB
    env = {
        **dict(os.environ),
        "HIKARI_BRAIN_V2_EPISODES_DB": str(db_path),
    }
    code = """
import os
from pathlib import Path
from core.brain_v2.episode_store import EpisodeStore
from core.brain_v2.consolidation_pipeline import EpisodeConsolidationPipeline

db_path = Path(os.environ["HIKARI_BRAIN_V2_EPISODES_DB"])
store = EpisodeStore(db_path=db_path, create_dirs=True)
episode_id = store.create_episode("subproc-seed")
store.add_turn(episode_id, "Remember this: subprocess safe accept.", is_user=True)
store.add_turn(
    episode_id,
    "Got it.",
    is_user=False,
    speaker_label="assistant",
)
candidates = EpisodeConsolidationPipeline(store).process_episode(episode_id)[1]
print(candidates[0].candidate_id)
"""
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


@pytest.mark.parametrize(
    "cli_args",
    [
        ["--brain-v2-accept-no-promote"],
        ["--brain-v2-accept"],
        ["--brain-v2-accept", "--confirm-promote", "promote"],
        ["--brain-v2-accept", "--confirm-promote", "PROMOTE"],
    ],
)
def test_subprocess_accept_paths_hikari_isolation(tmp_path, cli_args):
    """Safe accept paths must not create HOME/.hikari; only exact PROMOTE may touch neural."""
    import os
    import subprocess
    import sys

    from core.path_literals import DOT_HIKARI, HIKARI_MEMORY_DB

    db_root = tmp_path / "external_brain_v2_db"
    home = tmp_path / "fresh_home"
    home.mkdir()
    cand_id = _seed_pending_candidate_subprocess(db_root)
    db_path = db_root / "brain_v2" / EPISODES_DB
    env = {
        **dict(os.environ),
        "HOME": str(home),
        "HIKARI_BRAIN_V2_EPISODES_DB": str(db_path),
    }
    hikari_dir = home / DOT_HIKARI.lstrip("~/")
    neural_db = hikari_dir / "brain" / HIKARI_MEMORY_DB
    assert not hikari_dir.exists()

    if "--confirm-promote" in cli_args:
        token = cli_args[cli_args.index("--confirm-promote") + 1]
        argv = [
            sys.executable,
            str(REPO_ROOT / "hikari.py"),
            "--brain-v2-accept",
            cand_id,
            "--confirm-promote",
            token,
        ]
    else:
        argv = [sys.executable, str(REPO_ROOT / "hikari.py"), *cli_args, cand_id]

    proc = subprocess.run(
        argv,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    promote_confirmed = (
        "--confirm-promote" in cli_args
        and cli_args[cli_args.index("--confirm-promote") + 1] == "PROMOTE"
    )
    if promote_confirmed:
        assert proc.returncode == 0, proc.stderr
    else:
        if "--confirm-promote" in cli_args:
            assert proc.returncode == 1, proc.stderr
        else:
            assert proc.returncode == 0, proc.stderr
        assert not hikari_dir.exists(), f"safe path must not create {hikari_dir}"
        assert not neural_db.exists()
