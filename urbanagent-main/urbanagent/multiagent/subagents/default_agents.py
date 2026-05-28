"""MVP subagents: one instance per role; S0–S2 via LLM (or rules); S3 via toolkit only."""
from __future__ import annotations

import json
import uuid
from typing import Any, ClassVar

from urbanagent.llm.types import Message
from urbanagent.multiagent.llm_json import try_loads_json_object
from urbanagent.multiagent.schemas import ActionDraft, SubAgentRole, SubGoal, SubPlan
from urbanagent.multiagent.subagents.base import SubAgent
from urbanagent.types import CityState, UrbanAction, UrbanResource


def _state_snippet(state: CityState, incident_id: str) -> dict[str, Any]:
    inc = next((i for i in state.incidents if i.id == incident_id), None)
    return {
        "incident": (
            {
                "id": inc.id,
                "kind": inc.kind,
                "severity": inc.severity,
                "status": inc.status,
                "position": {"x": inc.position.x, "y": inc.position.y, "z": inc.position.z},
            }
            if inc
            else None
        ),
        "resources": [
            {
                "id": r.id,
                "kind": r.kind,
                "status": r.status,
                "capabilities": list(r.capabilities),
            }
            for r in state.resources
        ],
        "signal_ids": [s.id for s in state.traffic_signals],
    }


async def _llm_s0_s1_s2(
    llm,
    *,
    role: str,
    goal: SubGoal,
    state: CityState,
    use_llm: bool,
) -> dict[str, Any]:
    if not use_llm or llm is None:
        return {
            "s0": {"aligned_summary": goal.narrative},
            "s1": {"capabilities_match": True, "notes": ""},
            "s2": {"observation_focus": _state_snippet(state, goal.incident_id)},
        }
    snippet = _state_snippet(state, goal.incident_id)
    user = (
        f"角色={role}\nSubGoal={json.dumps(goal.__dict__, default=str, ensure_ascii=False)}\n"
        f"状态摘要={json.dumps(snippet, ensure_ascii=False)}\n\n"
        "只输出一个 JSON 对象，键为 s0、s1、s2：\n"
        "s0: { aligned_summary: string }\n"
        "s1: { capabilities_match: boolean, notes: string }\n"
        "s2: { observation_focus: object } 须与状态摘要事实一致，勿编造 id。\n"
    )
    resp = await llm.chat(
        [Message(role="user", content=user)],
        temperature=0.1,
        max_tokens=1200,
        system="You are sub-stages S0–S2 for one urban emergency sub-agent. Output only valid JSON.",
    )
    data = try_loads_json_object(resp.content)
    if not all(k in data for k in ("s0", "s1", "s2")):
        raise ValueError("LLM S0-S2 missing keys")
    return data


async def _llm_s0_s1_s2_safe(
    llm,
    *,
    role: str,
    goal: SubGoal,
    state: CityState,
    use_llm: bool,
) -> dict[str, Any]:
    try:
        return await _llm_s0_s1_s2(
            llm, role=role, goal=goal, state=state, use_llm=use_llm
        )
    except Exception:
        return {
            "s0": {"aligned_summary": goal.narrative},
            "s1": {"capabilities_match": True, "notes": ""},
            "s2": {"observation_focus": _state_snippet(state, goal.incident_id)},
        }


class DroneSubAgent(SubAgent):
    role: ClassVar[SubAgentRole] = "drone"
    def execute_drone_skill(
        self,
        skill: str,
        vehicle: str = "D-01",
        args: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        args = args or {}

        if skill == "drone_status":
            return self.tools.drone_status(vehicle)

        if skill == "drone_takeoff_land":
            return self.tools.drone_takeoff_land(
                vehicle=vehicle,
                height=args.get("height", 0.20),
                hover_time=args.get("hover_time", 1.5),
            )

        if skill == "drone_square_route":
            return self.tools.drone_square_route(
                vehicle=vehicle,
                height=args.get("height", 0.20),
                size=args.get("size", 0.15),
                velocity=args.get("velocity", 0.08),
                hold_time=args.get("hold_time", 1.0),
            )

        if skill == "drone_emergency_stop":
            return self.tools.drone_emergency_stop(vehicle)

        return {
            "ok": False,
            "action": skill,
            "result": None,
            "error": f"Unknown drone skill: {skill}",
        }
    async def run(
        self,
        goal: SubGoal,
        state: CityState,
        *,
        llm,
        use_llm: bool,
    ) -> SubPlan:
        stages = await _llm_s0_s1_s2_safe(llm, role="drone", goal=goal, state=state, use_llm=use_llm)
        if not stages.get("s1", {}).get("capabilities_match", True):
            return SubPlan(
                role="drone",
                status="infeasible",
                rationale=str(stages.get("s1", {}).get("notes", "capability mismatch")),
                llm_stages=stages,
            )
        incident = self.tools.find_incident(state, goal.incident_id)
        if incident is None:
            return SubPlan(role="drone", status="infeasible", rationale="incident not found")
        res = self.tools.first_available(
            state,
            kind="drone",
            capabilities=["aerial_recon"],
            allowed_ids=goal.allowed_resource_ids or None,
        )
        if res is None:
            return SubPlan(role="drone", status="infeasible", rationale="no available drone")
        rt = self.tools.estimate_route_for_resource(state, res, incident.position)
        draft = ActionDraft(
            kind="dispatch_drone",
            target_id=res.id,
            destination=incident.position,
            parameters={"incident_id": goal.incident_id, "role": "aerial_recon"},
            reason=f"toolkit route source={rt.route.source} eta={rt.route.travel_time:.2f}",
            ordering_hint=40,
        )
        return SubPlan(
            role="drone",
            status="ok",
            rationale=str(stages.get("s0", {}).get("aligned_summary", "")),
            action_drafts=[draft],
            llm_stages=stages,
        )


class UnmannedVehicleSubAgent(SubAgent):
    role: ClassVar[SubAgentRole] = "unmanned_vehicle"

    async def run(self, goal: SubGoal, state: CityState, *, llm, use_llm: bool) -> SubPlan:
        stages = await _llm_s0_s1_s2_safe(
            llm, role="unmanned_vehicle", goal=goal, state=state, use_llm=use_llm
        )
        if not stages.get("s1", {}).get("capabilities_match", True):
            return SubPlan(
                role="unmanned_vehicle",
                status="infeasible",
                rationale=str(stages.get("s1", {}).get("notes", "")),
                llm_stages=stages,
            )
        incident = self.tools.find_incident(state, goal.incident_id)
        if incident is None:
            return SubPlan(
                role="unmanned_vehicle",
                status="infeasible",
                rationale="incident not found",
            )
        res = self.tools.first_available(
            state,
            kind="unmanned_vehicle",
            allowed_ids=goal.allowed_resource_ids or None,
        )
        if res is None:
            return SubPlan(
                role="unmanned_vehicle",
                status="infeasible",
                rationale="no unmanned_vehicle resource",
            )
        rt = self.tools.estimate_route_for_resource(state, res, incident.position)
        draft = ActionDraft(
            kind="dispatch_vehicle",
            target_id=res.id,
            destination=incident.position,
            parameters={
                "incident_id": goal.incident_id,
                "role": "logistics_support",
            },
            reason=f"ugv route source={rt.route.source} eta={rt.route.travel_time:.2f}",
            ordering_hint=30,
        )
        return SubPlan(
            role="unmanned_vehicle",
            status="ok",
            rationale=str(stages.get("s0", {}).get("aligned_summary", "")),
            action_drafts=[draft],
            llm_stages=stages,
        )


class PoliceSubAgent(SubAgent):
    role: ClassVar[SubAgentRole] = "police_car"

    async def run(self, goal: SubGoal, state: CityState, *, llm, use_llm: bool) -> SubPlan:
        stages = await _llm_s0_s1_s2_safe(llm, role="police_car", goal=goal, state=state, use_llm=use_llm)
        if not stages.get("s1", {}).get("capabilities_match", True):
            return SubPlan(
                role="police_car",
                status="infeasible",
                rationale=str(stages.get("s1", {}).get("notes", "")),
                llm_stages=stages,
            )
        incident = self.tools.find_incident(state, goal.incident_id)
        if incident is None:
            return SubPlan(role="police_car", status="infeasible", rationale="incident not found")
        res = self.tools.first_available(
            state,
            kind="police_car",
            capabilities=["traffic_control"],
            allowed_ids=goal.allowed_resource_ids or None,
        )
        if res is None:
            return SubPlan(role="police_car", status="infeasible", rationale="no police car")
        rt = self.tools.estimate_route_for_resource(state, res, incident.position)
        draft = ActionDraft(
            kind="dispatch_vehicle",
            target_id=res.id,
            destination=incident.position,
            parameters={"incident_id": goal.incident_id, "role": "police_control"},
            reason=f"police route source={rt.route.source} eta={rt.route.travel_time:.2f}",
            ordering_hint=20,
        )
        return SubPlan(
            role="police_car",
            status="ok",
            rationale=str(stages.get("s0", {}).get("aligned_summary", "")),
            action_drafts=[draft],
            llm_stages=stages,
        )


class TrafficSignalSubAgent(SubAgent):
    role: ClassVar[SubAgentRole] = "traffic_signal"

    async def run(self, goal: SubGoal, state: CityState, *, llm, use_llm: bool) -> SubPlan:
        stages = await _llm_s0_s1_s2_safe(
            llm, role="traffic_signal", goal=goal, state=state, use_llm=use_llm
        )
        incident = self.tools.find_incident(state, goal.incident_id)
        if incident is None:
            return SubPlan(role="traffic_signal", status="infeasible", rationale="incident not found")
        sig = None
        if goal.allowed_signal_ids:
            for s in state.traffic_signals:
                if s.id in goal.allowed_signal_ids:
                    sig = s
                    break
        if sig is None:
            sig = self.tools.nearest_signal(state, incident.position)
        if sig is None:
            return SubPlan(role="traffic_signal", status="infeasible", rationale="no signal")
        draft = ActionDraft(
            kind="control_traffic_light",
            target_id=sig.id,
            destination=None,
            parameters={"mode": "emergency_preemption", "incident_id": goal.incident_id},
            reason="Emergency corridor for ground response.",
            ordering_hint=10,
        )
        return SubPlan(
            role="traffic_signal",
            status="ok",
            rationale=str(stages.get("s0", {}).get("aligned_summary", "")),
            action_drafts=[draft],
            llm_stages=stages,
        )


def draft_to_action(d: ActionDraft) -> UrbanAction:
    return UrbanAction(
        kind=d.kind,  # type: ignore[arg-type]
        target_id=d.target_id,
        destination=d.destination,
        parameters=dict(d.parameters),
        reason=d.reason,
    )


def _action_priority(action: UrbanAction) -> tuple[int, str]:
    if action.kind == "control_traffic_light":
        return (1, action.target_id)
    if action.kind == "dispatch_vehicle":
        role = str(action.parameters.get("role", ""))
        if role == "fire_suppression":
            return (2, action.target_id)
        if role == "police_control":
            return (3, action.target_id)
        if role in ("logistics_support", "perimeter_support"):
            return (4, action.target_id)
        return (5, action.target_id)
    if action.kind == "dispatch_drone":
        return (6, action.target_id)
    return (9, action.target_id)


def merge_fire_suppression_from_policy(state, dispatch_policy) -> list[UrbanAction]:
    """Meta supplements subagents with CarlaBridge-compatible fire response."""
    from urbanagent.dispatch import assignment_to_action

    plan = dispatch_policy.create_plan(state)
    resources = {r.id: r for r in state.resources}
    out: list[UrbanAction] = []
    for a in plan.assignments:
        if a.role == "fire_suppression":
            action = assignment_to_action(a)
            out.append(action)
            resource = resources.get(a.resource_id)
            if _needs_explicit_extinguish_step(resource):
                out.append(
                    UrbanAction(
                        kind="dispatch_vehicle",
                        target_id=a.resource_id,
                        destination=a.destination,
                        parameters={
                            "incident_id": a.incident_id,
                            "role": "fire_suppression",
                            "intent": "extinguish",
                            "capability": "fire_suppression",
                            "force_extinguish": True,
                        },
                        reason=(
                            "Follow-up extinguish after UGV_GOTO reaches "
                            f"incident {a.incident_id}."
                        ),
                    )
                )
    return out


def _needs_explicit_extinguish_step(resource: UrbanResource | None) -> bool:
    return resource is not None and resource.kind in {"unmanned_vehicle", "ground_vehicle"}


def _is_mobile_dispatch(action: UrbanAction) -> bool:
    return action.kind in {"dispatch_vehicle", "dispatch_drone"}


def _dedupe_mobile_targets(actions: list[UrbanAction]) -> list[UrbanAction]:
    out: list[UrbanAction] = []
    target_role: dict[str, str] = {}
    for action in actions:
        if not _is_mobile_dispatch(action):
            out.append(action)
            continue
        role = str(action.parameters.get("role", ""))
        existing = target_role.get(action.target_id)
        if existing == "fire_suppression" and role != "fire_suppression":
            continue
        if existing is not None and existing != "fire_suppression":
            if role == "fire_suppression":
                out = [
                    a
                    for a in out
                    if not (_is_mobile_dispatch(a) and a.target_id == action.target_id)
                ]
                target_role[action.target_id] = role
                out.append(action)
            continue
        target_role[action.target_id] = role
        out.append(action)
    return out


def integrate_actions_deterministic(
    sub_plans: list[SubPlan],
    *,
    state: CityState,
    dispatch_policy,
) -> tuple[list[UrbanAction], str]:
    """Merge sub drafts + policy fire_suppression; order by response role."""
    drafts: list[ActionDraft] = []
    blocked: list[str] = []
    for sp in sub_plans:
        if sp.status == "ok":
            drafts.extend(sp.action_drafts)
        else:
            blocked.append(f"{sp.role}:{sp.status}:{sp.rationale}")
    actions = [draft_to_action(d) for d in drafts]
    actions.extend(merge_fire_suppression_from_policy(state, dispatch_policy))
    actions = _dedupe_mobile_targets(actions)
    actions.sort(key=_action_priority)
    rationale = "deterministic merge: role priority + policy fire_suppression"
    if blocked:
        rationale += "; blocked=" + " | ".join(blocked)
    if not actions:
        rationale += "; no executable actions after hard constraints"
    return actions, rationale


def new_batch_id() -> str:
    return f"batch-{uuid.uuid4().hex[:12]}"
