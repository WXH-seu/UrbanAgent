"""Deterministic tools for sub-agent S3 (no LLM)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import requests

from urbanagent.resource_policy import is_dispatchable_resource
from urbanagent.routing import LocalGraphRoutePlanner
from urbanagent.types import CityState, Coordinate, Incident, ResourceKind, RouteEstimate, TrafficSignal, UrbanResource


@dataclass
class RouteToolResult:
    resource_id: str
    route: RouteEstimate
    destination: Coordinate


class SubAgentToolkit:
    """Shared routing / lookup helpers for all sub-agent kinds."""

    def __init__(
    	self,
    	route_planner: LocalGraphRoutePlanner | None = None,
    	drone_agent_url: str = "http://127.0.0.1:8010",
    ) -> None:
    	self._planner = route_planner or LocalGraphRoutePlanner()
    	self.drone_agent_url = drone_agent_url.rstrip("/")

    def find_incident(self, state: CityState, incident_id: str) -> Incident | None:
        return next((i for i in state.incidents if i.id == incident_id), None)

    def resources_by_ids(self, state: CityState, ids: list[str]) -> list[UrbanResource]:
        id_set = set(ids)
        return [r for r in state.resources if r.id in id_set]

    def first_available(
        self,
        state: CityState,
        *,
        kind: ResourceKind | None = None,
        kinds: tuple[ResourceKind, ...] | None = None,
        capabilities: list[str] | None = None,
        allowed_ids: list[str] | None = None,
    ) -> UrbanResource | None:
        caps = capabilities or []
        for r in state.resources:
            if not is_dispatchable_resource(r):
                continue
            if allowed_ids is not None and r.id not in allowed_ids:
                continue
            if kind is not None and r.kind != kind:
                continue
            if kinds is not None and r.kind not in kinds:
                continue
            if caps and not all(c in r.capabilities for c in caps):
                continue
            return r
        return None

    def estimate_route_for_resource(
        self,
        state: CityState,
        resource: UrbanResource,
        destination: Coordinate,
    ) -> RouteToolResult:
        route = self._planner.estimate(
            state=state,
            start=resource.position,
            end=destination,
            resource_kind=resource.kind,
            resource_speed=resource.speed,
        )
        return RouteToolResult(
            resource_id=resource.id,
            route=route,
            destination=destination,
        )

    def nearest_signal(self, state: CityState, position: Coordinate) -> TrafficSignal | None:
        if not state.traffic_signals:
            return None
        def dist(sig: TrafficSignal) -> float:
            dx = sig.position.x - position.x
            dy = sig.position.y - position.y
            dz = sig.position.z - position.z
            return (dx * dx + dy * dy + dz * dz) ** 0.5

        return min(state.traffic_signals, key=dist)
    def call_drone_skill(
        self,
        skill: str,
        vehicle: str = "D-01",
        args: dict[str, Any] | None = None,
        timeout: int = 60,
    ) -> dict[str, Any]:
        if args is None:
            args = {}

        payload = {
            "skill": skill,
            "vehicle": vehicle,
            "args": args,
        }

        try:
            response = requests.post(
                f"{self.drone_agent_url}/skill",
                json=payload,
                timeout=timeout,
            )
            response.raise_for_status()
            return response.json()

        except Exception as e:
            return {
                "ok": False,
                "action": skill,
                "result": None,
                "error": str(e),
            }

    def drone_status(self, vehicle: str = "D-01") -> dict[str, Any]:
        return self.call_drone_skill(
            skill="drone_status",
            vehicle=vehicle,
            args={},
            timeout=30,
        )

    def drone_takeoff_land(
        self,
        vehicle: str = "D-01",
        height: float = 0.20,
        hover_time: float = 1.5,
    ) -> dict[str, Any]:
        return self.call_drone_skill(
            skill="drone_takeoff_land",
            vehicle=vehicle,
            args={
                "height": height,
                "hover_time": hover_time,
            },
            timeout=90,
        )

    def drone_square_route(
        self,
        vehicle: str = "D-01",
        height: float = 0.20,
        size: float = 0.15,
        velocity: float = 0.08,
        hold_time: float = 1.0,
    ) -> dict[str, Any]:
        return self.call_drone_skill(
            skill="drone_square_route",
            vehicle=vehicle,
            args={
                "height": height,
                "size": size,
                "velocity": velocity,
                "hold_time": hold_time,
            },
            timeout=120,
        )

    def drone_emergency_stop(self, vehicle: str = "D-01") -> dict[str, Any]:
        return self.call_drone_skill(
            skill="drone_emergency_stop",
            vehicle=vehicle,
            args={},
            timeout=20,
        )
