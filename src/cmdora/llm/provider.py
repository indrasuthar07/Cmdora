"""Provider-neutral planning interface."""

from __future__ import annotations

from typing import Protocol

from cmdora.context import SafeContext
from cmdora.schemas import ActionPlan


class PlannerProvider(Protocol):
    """Interface implemented by any natural-language action planner."""

    def create_plan(self, request: str, context: SafeContext) -> ActionPlan:
        """Create a validated plan without executing any of its proposed actions."""
        ...


__all__ = ["PlannerProvider"]
