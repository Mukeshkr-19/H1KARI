"""Build planning must retain and safely consume its active request."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from core.build_executor import BuildExecutor


def test_planning_flow_stores_request_for_the_answer():
    executor = BuildExecutor()

    result = asyncio.run(executor.start_build_flow("build a dashboard for invoices"))

    assert result["type"] == "planning"
    assert executor.get_active_plan() == {
        "request": "build a dashboard for invoices",
        "task_type": "build",
        "answers": {},
    }


def test_answer_without_active_plan_is_safe_even_when_it_says_bypass():
    executor = BuildExecutor()

    with patch.object(executor, "_execute_build", new_callable=AsyncMock) as execute:
        result = asyncio.run(executor.process_answer("just do it"))

    assert result == {"type": "chat", "message": "No active build plan."}
    execute.assert_not_awaited()
