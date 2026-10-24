"""Opt-in Gemini smoke test; ordinary pytest runs never contact Gemini."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from cmdora.context import SafeContext
from cmdora.llm.gemini import GeminiPlanner
from cmdora.schemas import ActionPlan

pytestmark = pytest.mark.live


def test_live_gemini_returns_a_valid_plan() -> None:
    if os.environ.get("CMDORA_LIVE_GEMINI") != "1":
        pytest.skip("Set CMDORA_LIVE_GEMINI=1 to opt in to a live Gemini request.")
    if not os.environ.get("GEMINI_API_KEY") and not (Path.cwd() / ".env").is_file():
        pytest.skip("A Gemini API key is required for the opt-in smoke test.")

    context = SafeContext(
        workspace_basename="evaluation",
        is_git_repository=False,
        git_branch=None,
        git_status="",
        supported_actions=["inspect", "list_modified_today", "clarification"],
        autonomy_mode="off",
    )
    plan = GeminiPlanner().create_plan("Show files modified today", context)

    assert isinstance(plan, ActionPlan)
    assert plan.steps or plan.needs_clarification
