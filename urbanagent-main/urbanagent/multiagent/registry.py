"""Register multiple sub-agent instances per role (MVP: one per role)."""
from __future__ import annotations

from typing import Iterable

from urbanagent.multiagent.schemas import SubAgentRole
from urbanagent.multiagent.subagents.base import SubAgent
from urbanagent.multiagent.subagents.default_agents import (
    DroneSubAgent,
    PoliceSubAgent,
    TrafficSignalSubAgent,
    UnmannedVehicleSubAgent,
)
from urbanagent.multiagent.toolkit import SubAgentToolkit


class SubAgentRegistry:
    """Hub-spoke registry: extend via :meth:`register` for N agents per role."""

    def __init__(self) -> None:
        self._by_role: dict[SubAgentRole, list[SubAgent]] = {}

    def register(self, agent: SubAgent, *, position: int | None = None) -> None:
        role = agent.role
        self._by_role.setdefault(role, [])
        if position is None:
            self._by_role[role].append(agent)
        else:
            self._by_role[role].insert(max(0, position), agent)

    def primary(self, role: SubAgentRole) -> SubAgent:
        row = self._by_role.get(role)
        if not row:
            raise KeyError(f"no sub-agent registered for role {role!r}")
        return row[0]

    def all_for_role(self, role: SubAgentRole) -> list[SubAgent]:
        return list(self._by_role.get(role, []))

    def registered_roles(self) -> Iterable[SubAgentRole]:
        return tuple(self._by_role.keys())


def build_default_mvp_registry(toolkit: SubAgentToolkit) -> SubAgentRegistry:
    """One instance per role: traffic signal, police, unmanned ground, drone."""
    reg = SubAgentRegistry()
    reg.register(TrafficSignalSubAgent(toolkit))
    reg.register(PoliceSubAgent(toolkit))
    reg.register(UnmannedVehicleSubAgent(toolkit))
    reg.register(DroneSubAgent(toolkit))
    return reg
