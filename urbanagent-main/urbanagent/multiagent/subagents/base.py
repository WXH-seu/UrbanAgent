"""Hub-spoke subagents: report only to meta; extensible registry (MVP: one per role)."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, ClassVar

from urbanagent.multiagent.schemas import SubAgentRole, SubGoal, SubPlan
from urbanagent.multiagent.toolkit import SubAgentToolkit
from urbanagent.types import CityState

if TYPE_CHECKING:
    from urbanagent.llm.llm import LLM


class SubAgent(ABC):
    """One logical sub-agent type. MVP: single instance per role; extend registry for N>1."""

    role: ClassVar[SubAgentRole]

    def __init__(self, toolkit: SubAgentToolkit) -> None:
        self.tools = toolkit

    @abstractmethod
    async def run(
        self,
        goal: SubGoal,
        state: CityState,
        *,
        llm: LLM | None,
        use_llm: bool,
    ) -> SubPlan:
        """S0–S6 in one call; only SubPlan crosses the meta boundary."""
