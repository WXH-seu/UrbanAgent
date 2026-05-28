"""Dataclasses for the UrbanAgent multi-agent MVP (hub-spoke subagents + meta)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from urbanagent.types import ActionResult, CityState, Coordinate, UrbanAction

SubAgentRole = Literal["drone", "unmanned_vehicle", "police_car", "traffic_signal"]


@dataclass
class GateDecision:
    """G1: whether to run the full multi-agent pipeline."""

    should_intervene: bool
    priority: str = "normal"
    reason: str = ""
    trigger_kind: str = ""


@dataclass
class SubGoal:
    """G3 → sub agent: one role's task slice."""

    role: SubAgentRole
    incident_id: str
    narrative: str = ""
    allowed_resource_ids: list[str] = field(default_factory=list)
    allowed_signal_ids: list[str] = field(default_factory=list)
    hard_hints: list[str] = field(default_factory=list)
    soft_hints: list[str] = field(default_factory=list)
    entities: dict[str, Any] = field(default_factory=dict)


@dataclass
class ActionDraft:
    """S4: structured action before meta merge (must map to UrbanAction)."""

    kind: str
    target_id: str
    destination: Coordinate | None = None
    parameters: dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    ordering_hint: int = 0


@dataclass
class SubPlan:
    """Sub agent output (S0–S6), reported only to meta."""

    role: SubAgentRole
    status: Literal["ok", "need_clarification", "infeasible", "unsupported"] = "ok"
    rationale: str = ""
    action_drafts: list[ActionDraft] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    llm_stages: dict[str, Any] = field(default_factory=dict)


@dataclass
class CommittedBatch:
    """G5: ordered actions for 3D."""

    batch_id: str
    actions: list[UrbanAction]
    rationale: str = ""


@dataclass
class BatchOutcome:
    """G6–G7."""

    batch_id: str
    per_step_results: list[ActionResult]
    polling_iterations: int
    criteria_satisfied: bool
    final_state: CityState | None = None
    notes: list[str] = field(default_factory=list)


@dataclass
class UrbanMultiAgentResult:
    """End-to-end multi-agent run."""

    query: str
    gate: GateDecision
    urban_task: Any | None = None
    subgoals: dict[str, SubGoal] = field(default_factory=dict)
    sub_plans: list[SubPlan] = field(default_factory=list)
    committed: CommittedBatch | None = None
    batch_outcome: BatchOutcome | None = None
    final_report: str = ""
    initial_state: CityState | None = None
    llm_used: bool = False
    skipped_reason: str = ""


@dataclass
class PatrolFireResponseResult:
    """Patrol → fire detection → hold → dispatch → return-to-base workflow result."""

    patrol_outcome: BatchOutcome | None = None
    hold_outcome: BatchOutcome | None = None
    detected_incident_id: str | None = None
    detection_notes: list[str] = field(default_factory=list)
    response: UrbanMultiAgentResult | None = None
    return_outcome: BatchOutcome | None = None
    final_report: str = ""
