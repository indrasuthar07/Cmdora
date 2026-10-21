"""Offline planner fakes shared by unit tests."""

from __future__ import annotations

from cmdora.context import SafeContext
from cmdora.schemas import ActionPlan


class FakePlanner:
    """A configurable provider fake that never uses the network or Gemini SDK."""

    def __init__(self, plan: ActionPlan | Exception) -> None:
        self.plan = plan
        self.calls: list[tuple[str, SafeContext]] = []

    def create_plan(self, request: str, context: SafeContext) -> ActionPlan:
        self.calls.append((request, context))
        if isinstance(self.plan, Exception):
            raise self.plan
        return self.plan.model_copy(deep=True)


class FakeGeminiModels:
    """Return or raise queued SDK-like results for GeminiPlanner unit tests."""

    def __init__(self, *outcomes: object) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[dict[str, object]] = []

    def generate_content(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        if not self.outcomes:
            raise AssertionError("FakeGeminiModels has no queued response")
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class FakeGeminiClient:
    """Minimal client shape used by GeminiPlanner tests."""

    def __init__(self, *outcomes: object) -> None:
        self.models = FakeGeminiModels(*outcomes)
