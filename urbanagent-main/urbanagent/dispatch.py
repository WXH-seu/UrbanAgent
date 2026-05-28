"""Deterministic dispatch policy for UrbanAgent."""
from __future__ import annotations

import math
from dataclasses import dataclass

from urbanagent.routing import LocalGraphRoutePlanner, RoutePlanner
from urbanagent.resource_policy import is_dispatchable_resource, resource_block_reason
from urbanagent.types import (
    ActionKind,
    CandidateScore,
    CityState,
    Coordinate,
    DispatchAssignment,
    DispatchPlan,
    Incident,
    ResourceKind,
    UrbanAction,
    UrbanResource,
)
import json
from pathlib import Path

DEFAULT_FIRE_LANE_OFFSET_M = 3.5


def lane_offset_destination(
    start: Coordinate,
    target: Coordinate,
    *,
    offset_m: float = DEFAULT_FIRE_LANE_OFFSET_M,
) -> Coordinate:
    """Shift ``target`` one lane to the right of the approach direction.

    CARLA uses a left-handed XY ground plane (+X east, +Y south, +Z up); when
    a vehicle heads along the forward unit ``(fx, fy)``, its right is
    ``(-fy, fx)``. We keep z unchanged so the UGV stops alongside the incident
    instead of driving into it.
    """

    dx = target.x - start.x
    dy = target.y - start.y
    length = math.hypot(dx, dy)
    if length < 1e-6:
        return Coordinate(target.x, target.y + offset_m, target.z)
    fx = dx / length
    fy = dy / length
    return Coordinate(
        target.x + (-fy) * offset_m,
        target.y + fx * offset_m,
        target.z,
    )


@dataclass(frozen=True)
class RoleSpec:
    """Resource requirement for one incident-response role."""

    role: str
    resource_kinds: tuple[ResourceKind, ...]
    required_capability: str
    action_kind: ActionKind


ROLE_SPECS: dict[str, RoleSpec] = {
    "fire_suppression": RoleSpec(
        role="fire_suppression",
        resource_kinds=("fire_truck", "unmanned_vehicle", "ground_vehicle"),
        required_capability="fire_suppression",
        action_kind="dispatch_vehicle",
    ),
    "police_control": RoleSpec(
        role="police_control",
        resource_kinds=("police_car",),
        required_capability="traffic_control",
        action_kind="dispatch_vehicle",
    ),
    "aerial_recon": RoleSpec(
        role="aerial_recon",
        resource_kinds=("drone",),
        required_capability="aerial_recon",
        action_kind="dispatch_drone",
    ),
}


class DispatchPolicy:
    """Create a constrained multi-event dispatch plan from a city state."""

    def __init__(
        self,
        route_planner: RoutePlanner | None = None,
        *,
        fire_lane_offset_m: float = DEFAULT_FIRE_LANE_OFFSET_M,
    ) -> None:
        self.route_planner = route_planner or LocalGraphRoutePlanner()
        self.fire_lane_offset_m = float(fire_lane_offset_m)

    def open_incidents(self, state: CityState) -> list[Incident]:
        return [
            incident
            for incident in state.incidents
            if incident.status in {"open", "responding"}
        ]

    def score_dispatch_candidates(self, state: CityState) -> list[CandidateScore]:
        return self._score_candidates(state, self.open_incidents(state))

    def build_plan_from_ordered_candidates(
        self,
        state: CityState,
        candidates: list[CandidateScore],
    ) -> DispatchPlan:
        open_incidents = self.open_incidents(state)
        assignments, notes = self._select_assignments(state, candidates)
        if not open_incidents:
            notes.append("No open incidents require dispatch.")
        for incident in open_incidents:
            selected_roles = {
                assignment.role
                for assignment in assignments
                if assignment.incident_id == incident.id
            }
            for role in self._required_roles(incident):
                if role not in selected_roles:
                    notes.append(f"No feasible {role} resource for incident {incident.id}.")
        return DispatchPlan(
            assignments=assignments,
            candidate_scores=candidates,
            notes=notes,
        )

    def create_plan(self, state: CityState) -> DispatchPlan:
        candidates = self.score_dispatch_candidates(state)
        return self.build_plan_from_ordered_candidates(state, candidates)

    def _score_candidates(
        self,
        state: CityState,
        incidents: list[Incident],
    ) -> list[CandidateScore]:
        candidates: list[CandidateScore] = []
        for incident in incidents:
            for role in self._required_roles(incident):
                spec = ROLE_SPECS[role]
                for resource in state.resources:
                    if not self._resource_matches(resource, spec):
                        continue
                    route = self.route_planner.estimate(
                        state=state,
                        start=resource.position,
                        end=incident.position,
                        resource_kind=resource.kind,
                        resource_speed=resource.speed,
                    )
                    capability_penalty = self._capability_penalty(resource, spec)
                    load_penalty = self._load_penalty(resource, role)
                    reserve_penalty = self._reserve_pressure_penalty(state, resource)
                    congestion_penalty = route.congestion * 10.0
                    score = (
                        route.travel_time
                        + congestion_penalty
                        + capability_penalty
                        + load_penalty
                        + reserve_penalty
                    )
                    candidates.append(
                        CandidateScore(
                            incident_id=incident.id,
                            resource_id=resource.id,
                            role=role,
                            score=score,
                            response_time=route.travel_time,
                            congestion_penalty=congestion_penalty,
                            capability_penalty=capability_penalty,
                            load_penalty=load_penalty,
                            reserve_penalty=reserve_penalty,
                            route=route,
                            reason=(
                                f"{resource.kind} candidate for {role}: "
                                f"time={route.travel_time:.2f}, score={score:.2f}"
                            ),
                        )
                    )
        return sorted(candidates, key=lambda item: item.score)

    def _select_assignments(
        self,
        state: CityState,
        candidates: list[CandidateScore],
    ) -> tuple[list[DispatchAssignment], list[str]]:
        assignments: list[DispatchAssignment] = []
        notes: list[str] = []
        selected_keys: set[tuple[str, str]] = set()
        selected_resources: set[str] = set()
        selected_by_base: dict[str, int] = {}
        resources_by_id = {resource.id: resource for resource in state.resources}
        incidents_by_id = {incident.id: incident for incident in state.incidents}

        for candidate in candidates:
            key = (candidate.incident_id, candidate.role)
            if key in selected_keys or candidate.resource_id in selected_resources:
                continue
            resource = resources_by_id.get(candidate.resource_id)
            incident = incidents_by_id.get(candidate.incident_id)
            if resource is None or incident is None:
                continue
            if not self._reserve_allows_dispatch(state, resource, selected_by_base):
                notes.append(
                    f"Skipped {resource.id}: dispatch would violate reserve ratio "
                    f"for base {resource.home_base_id}."
                )
                continue
            spec = ROLE_SPECS[candidate.role]
            destination = incident.position
            if (
                candidate.role == "fire_suppression"
                and resource.kind in {"unmanned_vehicle", "ground_vehicle"}
                and self.fire_lane_offset_m > 0.0
            ):
                destination = lane_offset_destination(
                    resource.position,
                    incident.position,
                    offset_m=self.fire_lane_offset_m,
                )
            assignment = DispatchAssignment(
                incident_id=incident.id,
                resource_id=resource.id,
                role=candidate.role,
                action_kind=spec.action_kind,
                destination=destination,
                score=candidate,
                reason=(
                    f"Selected {resource.id} for {incident.id} with score "
                    f"{candidate.score:.2f}; route source={candidate.route.source}."
                ),
            )
            assignments.append(assignment)
            selected_keys.add(key)
            selected_resources.add(resource.id)
            if resource.home_base_id:
                selected_by_base[resource.home_base_id] = (
                    selected_by_base.get(resource.home_base_id, 0) + 1
                )
        return assignments, notes

    def _required_roles(self, incident: Incident) -> list[str]:
        if incident.kind == "fire":
            roles = ["fire_suppression", "aerial_recon"]
            if incident.severity in {"high", "critical"}:
                roles.append("police_control")
            return roles
        if incident.kind == "traffic_accident":
            roles = ["police_control", "aerial_recon"]
            if incident.severity in {"high", "critical"}:
                roles.append("fire_suppression")
            return roles
        if incident.kind == "security":
            return ["police_control", "aerial_recon"]
        return ["aerial_recon"]

    def _resource_matches(self, resource: UrbanResource, spec: RoleSpec) -> bool:
        if not is_dispatchable_resource(resource):
            return False
        if resource.kind not in spec.resource_kinds:
            return False
        return spec.required_capability in resource.capabilities

    def _capability_penalty(self, resource: UrbanResource, spec: RoleSpec) -> float:
        return 0.0 if spec.required_capability in resource.capabilities else 50.0

    def _load_penalty(self, resource: UrbanResource, role: str) -> float:
        penalty = 0.0
        if resource_block_reason(resource) is not None:
            penalty += 1000.0
        if resource.current_task_id:
            penalty += 100.0
        if role == "fire_suppression":
            water = 100.0 if resource.water_remaining is None else resource.water_remaining
            penalty += max(0.0, 50.0 - water) * 0.5
        if role == "aerial_recon":
            battery = 100.0 if resource.battery_remaining is None else resource.battery_remaining
            penalty += max(0.0, 40.0 - battery) * 0.75
        payload = 100.0 if resource.payload_remaining is None else resource.payload_remaining
        penalty += max(0.0, 20.0 - payload) * 0.25
        return penalty

    def _reserve_pressure_penalty(self, state: CityState, resource: UrbanResource) -> float:
        base = _base_for_resource(state, resource)
        if base is None:
            return 0.0
        available = _available_count_for_base(state, base.id)
        reserve_min = _reserve_min(len(base.resource_ids), base.reserve_ratio)
        slack = available - reserve_min
        return 0.0 if slack > 1 else (2 - slack) * 5.0

    def _reserve_allows_dispatch(
        self,
        state: CityState,
        resource: UrbanResource,
        selected_by_base: dict[str, int],
    ) -> bool:
        base = _base_for_resource(state, resource)
        if base is None:
            return True
        available = _available_count_for_base(state, base.id)
        reserve_min = _reserve_min(len(base.resource_ids), base.reserve_ratio)
        already_selected = selected_by_base.get(base.id, 0)
        return available - already_selected - 1 >= reserve_min


def assignment_to_action(assignment: DispatchAssignment) -> UrbanAction:
    """Convert a dispatch assignment into a sandbox action."""

    route = assignment.score.route

    parameters = {
        "incident_id": assignment.incident_id,
        "role": assignment.role,
        "score": round(assignment.score.score, 4),
        "route_source": route.source,
        "route_nodes": list(route.path),
        "route_distance": route.distance,
        "route_travel_time": route.travel_time,
        "route_congestion": route.congestion,
    }

    route_file = Path("route_waypoints.json")

    if route_file.exists():
        data = json.loads(route_file.read_text(encoding="utf-8"))
        route_waypoints = data.get("route_waypoints") or []

        if route_waypoints:
            parameters["route_source"] = "carla_global_route"
            parameters["route_waypoints"] = route_waypoints
    parameters["route_source"] = "carla_global_route"
    parameters["route_waypoints"] = [
        {"x": 10.0, "y": 10.0, "z": 0.0},
        {"x": 12.0, "y": 10.0, "z": 0.0},
        {"x": 14.0, "y": 10.0, "z": 0.0},
        {"x": 20.0, "y": 20.0, "z": 0.0},
    ]


    if assignment.role == "fire_suppression":
        parameters["intent"] = "extinguish"
        parameters["capability"] = "fire_suppression"

    return UrbanAction(
        kind=assignment.action_kind,
        target_id=assignment.resource_id,
        destination=assignment.destination,
        parameters=parameters,
        reason=assignment.reason,
    )


def _base_for_resource(state: CityState, resource: UrbanResource):
    if not resource.home_base_id:
        return None
    for base in [*state.fire_stations, *state.police_stations, *state.drone_bases]:
        if base.id == resource.home_base_id:
            return base
    return None


def _available_count_for_base(state: CityState, base_id: str) -> int:
    return sum(
        1
        for resource in state.resources
        if resource.home_base_id == base_id and resource.status == "available"
    )


def _reserve_min(total: int, reserve_ratio: float) -> int:
    if total <= 0:
        return 0
    ratio = min(max(reserve_ratio, 0.0), 1.0)
    return math.ceil(total * ratio)
