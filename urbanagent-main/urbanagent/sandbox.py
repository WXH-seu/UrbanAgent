"""Sandbox client interfaces for UrbanAgent.

3D 城市沙盘接入通过中间件 CarlaBridge 完成。生产环境使用
``CarlaBridgeSandboxClient`` 连接 Socket.IO ``/agent`` 命名空间；本模块保留
``MockSandboxClient`` 作为离线开发和单元测试用的内存仿真。
"""
from __future__ import annotations

import math
from abc import ABC, abstractmethod

from urbanagent.types import (
    ActionResult,
    CityState,
    Coordinate,
    DroneBase,
    FireStation,
    Incident,
    PoliceStation,
    RoadSegment,
    TrafficSignal,
    UrbanAction,
    UrbanResource,
)


class SandboxClient(ABC):
    """Minimal contract any 3D city sandbox integration must implement."""

    @abstractmethod
    async def get_state(self) -> CityState:
        """Return the latest city state visible to the agent."""

    @abstractmethod
    async def send_action(self, action: UrbanAction) -> ActionResult:
        """Apply one action to the sandbox and return execution feedback."""


class MockSandboxClient(SandboxClient):
    """Deterministic sandbox used to develop UrbanAgent before integration."""

    def __init__(self, initial_state: CityState | None = None) -> None:
        self._state = initial_state or build_single_fire_state()
        self._applied: list[ActionResult] = []
        self._patrol_anchors: dict[str, Coordinate] = {}
        self._patrol_step_m = 12.0

    @property
    def applied_results(self) -> list[ActionResult]:
        """A copy of all action results produced during this process."""

        return list(self._applied)

    async def close(self) -> None:
        """No-op hook; matches real sandbox adapters for uniform teardown."""

        return

    async def get_state(self) -> CityState:
        self._advance_patrol_positions()
        return self._state

    async def send_action(self, action: UrbanAction) -> ActionResult:
        if action.kind in {"dispatch_vehicle", "dispatch_drone"}:
            result = self._dispatch_mobile_resource(action)
        elif action.kind == "patrol_drone":
            result = self._patrol_drone(action)
        elif action.kind in {"return_drone", "return_vehicle"}:
            result = self._return_resource(action)
        elif action.kind in {"hold_drone", "stop_vehicle"}:
            if action.kind == "hold_drone":
                self._patrol_anchors.pop(action.target_id, None)
            result = self._instant_mobile_command(action)
        elif action.kind == "control_traffic_light":
            result = self._control_traffic_light(action)
        elif action.kind == "mark_incident":
            result = self._mark_incident(action)
        else:
            result = ActionResult(
                status="rejected",
                action=action,
                message=f"unsupported action kind: {action.kind}",
            )
        self._applied.append(result)
        return result

    def _dispatch_mobile_resource(self, action: UrbanAction) -> ActionResult:
        if action.destination is None:
            return ActionResult(
                status="rejected",
                action=action,
                message="dispatch action requires a destination",
            )
        resource = self._find_resource(action.target_id)
        if resource is None:
            return ActionResult(
                status="rejected",
                action=action,
                message=f"resource not found: {action.target_id}",
            )
        force_extinguish = bool(action.parameters.get("force_extinguish"))
        if resource.status != "available" and not force_extinguish:
            return ActionResult(
                status="rejected",
                action=action,
                message=f"resource {resource.id} is {resource.status}",
            )
        if action.kind == "dispatch_drone" and resource.kind != "drone":
            return ActionResult(
                status="rejected",
                action=action,
                message=f"resource {resource.id} is not a drone",
            )
        if action.kind == "dispatch_vehicle" and resource.kind == "drone":
            return ActionResult(
                status="rejected",
                action=action,
                message=f"resource {resource.id} is a drone; use dispatch_drone",
            )
        distance = _distance(resource.position, action.destination)
        resource.status = "dispatched"
        resource.position = action.destination
        resource.current_task_id = str(action.parameters.get("incident_id", ""))
        if resource.kind == "fire_truck" and resource.water_remaining is not None:
            resource.water_remaining = max(0.0, resource.water_remaining - 20.0)
        if resource.kind == "drone" and resource.battery_remaining is not None:
            resource.battery_remaining = max(0.0, resource.battery_remaining - 15.0)
        if resource.payload_remaining is not None:
            resource.payload_remaining = max(0.0, resource.payload_remaining - 5.0)
        if action.parameters.get("force_extinguish"):
            incident_id = str(action.parameters.get("incident_id", "") or "")
            incident = self._find_incident(incident_id)
            if incident is not None and incident.kind == "fire":
                incident.status = "resolved"
        return ActionResult(
            status="applied",
            action=action,
            message=(
                f"{resource.kind} {resource.id} dispatched to "
                f"({action.destination.x:.1f}, {action.destination.y:.1f}); "
                f"mock distance {distance:.1f}"
            ),
        )

    def _patrol_drone(self, action: UrbanAction) -> ActionResult:
        resource = self._find_resource(action.target_id)
        if resource is None:
            return ActionResult(
                status="rejected",
                action=action,
                message=f"resource not found: {action.target_id}",
            )
        if resource.kind != "drone":
            return ActionResult(
                status="rejected",
                action=action,
                message=f"resource {resource.id} is not a drone",
            )
        path = action.parameters.get("path") or []
        if not path:
            return ActionResult(
                status="rejected",
                action=action,
                message="patrol_drone requires a non-empty path",
            )
        anchor = action.parameters.get("fire_watch_anchor")
        if isinstance(anchor, Coordinate):
            self._patrol_anchors[resource.id] = anchor
        elif isinstance(anchor, dict):
            self._patrol_anchors[resource.id] = Coordinate(
                float(anchor["x"]),
                float(anchor["y"]),
                float(anchor.get("z", resource.position.z)),
            )
        resource.status = "available"
        resource.current_task_id = "patrol"
        if resource.battery_remaining is not None:
            resource.battery_remaining = max(0.0, resource.battery_remaining - 5.0)
        return ActionResult(
            status="applied",
            action=action,
            message=f"drone {resource.id} started patrol",
        )

    def _advance_patrol_positions(self) -> None:
        """Move mock patrol drones toward ``fire_watch_anchor`` each state poll."""
        for drone_id, anchor in list(self._patrol_anchors.items()):
            resource = self._find_resource(drone_id)
            if resource is None or resource.current_task_id != "patrol":
                self._patrol_anchors.pop(drone_id, None)
                continue
            remaining = _distance(resource.position, anchor)
            if remaining <= _PATROL_ARRIVAL_EPS_M:
                resource.position = anchor
                continue
            step = min(self._patrol_step_m, remaining)
            ratio = step / remaining
            resource.position = Coordinate(
                resource.position.x + (anchor.x - resource.position.x) * ratio,
                resource.position.y + (anchor.y - resource.position.y) * ratio,
                resource.position.z + (anchor.z - resource.position.z) * ratio,
            )

    def _return_resource(self, action: UrbanAction) -> ActionResult:
        resource = self._find_resource(action.target_id)
        if resource is None:
            return ActionResult(
                status="rejected",
                action=action,
                message=f"resource not found: {action.target_id}",
            )
        if action.kind == "return_drone" and resource.kind != "drone":
            return ActionResult(
                status="rejected",
                action=action,
                message=f"resource {resource.id} is not a drone",
            )
        if action.kind == "return_vehicle" and resource.kind == "drone":
            return ActionResult(
                status="rejected",
                action=action,
                message=f"resource {resource.id} is a drone; use return_drone",
            )
        home = self._home_position(resource)
        if home is not None:
            resource.position = home
        resource.status = "available"
        resource.current_task_id = None
        return ActionResult(
            status="applied",
            action=action,
            message=f"{resource.kind} {resource.id} returned to base",
        )

    def _instant_mobile_command(self, action: UrbanAction) -> ActionResult:
        resource = self._find_resource(action.target_id)
        if resource is None:
            return ActionResult(
                status="rejected",
                action=action,
                message=f"resource not found: {action.target_id}",
            )
        if action.kind == "hold_drone" and resource.kind != "drone":
            return ActionResult(
                status="rejected",
                action=action,
                message=f"resource {resource.id} is not a drone",
            )
        if action.kind == "stop_vehicle" and resource.kind == "drone":
            return ActionResult(
                status="rejected",
                action=action,
                message=f"resource {resource.id} is a drone; use hold_drone",
            )
        resource.status = "available"
        return ActionResult(
            status="applied",
            action=action,
            message=f"{action.kind} applied to {resource.id}",
        )

    def _control_traffic_light(self, action: UrbanAction) -> ActionResult:
        signal = self._find_signal(action.target_id)
        if signal is None:
            return ActionResult(
                status="rejected",
                action=action,
                message=f"traffic signal not found: {action.target_id}",
            )
        signal.mode = str(action.parameters.get("mode", "emergency_preemption"))
        return ActionResult(
            status="applied",
            action=action,
            message=f"traffic signal {signal.id} switched to {signal.mode}",
        )

    def _mark_incident(self, action: UrbanAction) -> ActionResult:
        incident = self._find_incident(action.target_id)
        if incident is None:
            return ActionResult(
                status="rejected",
                action=action,
                message=f"incident not found: {action.target_id}",
            )
        incident.status = str(action.parameters.get("status", "responding"))
        return ActionResult(
            status="applied",
            action=action,
            message=f"incident {incident.id} marked {incident.status}",
        )

    def _find_resource(self, resource_id: str) -> UrbanResource | None:
        return next((item for item in self._state.resources if item.id == resource_id), None)

    def _find_signal(self, signal_id: str) -> TrafficSignal | None:
        return next(
            (item for item in self._state.traffic_signals if item.id == signal_id),
            None,
        )

    def _find_incident(self, incident_id: str) -> Incident | None:
        return next((item for item in self._state.incidents if item.id == incident_id), None)

    def _home_position(self, resource: UrbanResource) -> Coordinate | None:
        if not resource.home_base_id:
            return None
        for base in (
            list(self._state.drone_bases)
            + list(self._state.fire_stations)
            + list(self._state.police_stations)
        ):
            if base.id == resource.home_base_id:
                return base.position
        return None


def build_single_fire_state() -> CityState:
    """Build a small emergency-dispatch scene matching the first demo target."""

    return CityState(
        timestamp="mock-2026-05-08T20:00:00Z",
        incidents=[
            Incident(
                id="incident-fire-001",
                kind="fire",
                severity="high",
                position=Coordinate(x=42.0, y=18.0),
                description="Smoke and flame reported near the central plaza.",
            )
        ],
        fire_stations=[
            FireStation(
                id="fire-station-north",
                name="North Fire Station",
                position=Coordinate(x=10.0, y=15.0),
                resource_ids=["fire-truck-01", "fire-truck-02"],
                reserve_ratio=0.5,
            ),
            FireStation(
                id="fire-station-east",
                name="East Fire Station",
                position=Coordinate(x=68.0, y=21.0),
                resource_ids=["fire-truck-03", "fire-truck-04"],
                reserve_ratio=0.5,
            ),
        ],
        police_stations=[
            PoliceStation(
                id="police-station-central",
                name="Central Police Station",
                position=Coordinate(x=36.0, y=12.0),
                resource_ids=["police-car-01", "police-car-02"],
                reserve_ratio=0.5,
            )
        ],
        drone_bases=[
            DroneBase(
                id="drone-base-west",
                name="West Drone Base",
                position=Coordinate(x=20.0, y=30.0, z=12.0),
                resource_ids=["drone-01", "drone-02"],
                reserve_ratio=0.5,
            )
        ],
        resources=[
            UrbanResource(
                id="fire-truck-01",
                kind="fire_truck",
                label="North station fire truck",
                position=Coordinate(x=10.0, y=15.0),
                home_base_id="fire-station-north",
                speed=1.15,
                water_remaining=100.0,
                payload_remaining=90.0,
                capabilities=["fire_suppression", "rescue"],
            ),
            UrbanResource(
                id="fire-truck-02",
                kind="fire_truck",
                label="North station reserve fire truck",
                position=Coordinate(x=11.0, y=15.0),
                home_base_id="fire-station-north",
                speed=1.05,
                water_remaining=95.0,
                payload_remaining=85.0,
                capabilities=["fire_suppression"],
            ),
            UrbanResource(
                id="fire-truck-03",
                kind="fire_truck",
                label="East station fire truck",
                position=Coordinate(x=68.0, y=21.0),
                home_base_id="fire-station-east",
                speed=1.2,
                water_remaining=100.0,
                payload_remaining=80.0,
                capabilities=["fire_suppression", "hazmat"],
            ),
            UrbanResource(
                id="fire-truck-04",
                kind="fire_truck",
                label="East station reserve fire truck",
                position=Coordinate(x=69.0, y=20.0),
                home_base_id="fire-station-east",
                speed=1.0,
                water_remaining=90.0,
                payload_remaining=80.0,
                capabilities=["fire_suppression"],
            ),
            UrbanResource(
                id="police-car-01",
                kind="police_car",
                label="Traffic police patrol",
                position=Coordinate(x=36.0, y=12.0),
                home_base_id="police-station-central",
                speed=1.35,
                payload_remaining=70.0,
                capabilities=["traffic_control", "perimeter_control"],
            ),
            UrbanResource(
                id="police-car-02",
                kind="police_car",
                label="Central reserve police car",
                position=Coordinate(x=37.0, y=12.0),
                home_base_id="police-station-central",
                speed=1.25,
                payload_remaining=80.0,
                capabilities=["traffic_control"],
            ),
            UrbanResource(
                id="drone-01",
                kind="drone",
                label="Aerial reconnaissance drone",
                position=Coordinate(x=20.0, y=30.0, z=12.0),
                home_base_id="drone-base-west",
                speed=2.4,
                battery_remaining=92.0,
                payload_remaining=60.0,
                capabilities=["aerial_recon", "thermal_imaging"],
            ),
            UrbanResource(
                id="drone-02",
                kind="drone",
                label="Reserve reconnaissance drone",
                position=Coordinate(x=21.0, y=29.0, z=12.0),
                home_base_id="drone-base-west",
                speed=2.1,
                battery_remaining=78.0,
                payload_remaining=55.0,
                capabilities=["aerial_recon"],
            ),
            UrbanResource(
                id="ugv-01",
                kind="unmanned_vehicle",
                label="Unmanned ground vehicle support unit",
                position=Coordinate(x=18.0, y=28.0),
                home_base_id="drone-base-west",
                speed=1.6,
                payload_remaining=75.0,
                capabilities=["logistics_support", "perimeter_support"],
            ),
        ],
        traffic_signals=[
            TrafficSignal(id="signal-avenue-01", position=Coordinate(x=35.0, y=15.0)),
        ],
        roads=[
            RoadSegment(
                id="road-fire-north-to-avenue",
                from_node="fire_north",
                to_node="avenue_west",
                from_position=Coordinate(x=10.0, y=15.0),
                to_position=Coordinate(x=28.0, y=15.0),
                speed_limit=1.3,
                congestion=0.15,
                allowed_resource_kinds=[
                    "fire_truck",
                    "police_car",
                    "ground_vehicle",
                    "unmanned_vehicle",
                ],
            ),
            RoadSegment(
                id="road-police-to-avenue",
                from_node="police_central",
                to_node="avenue_west",
                from_position=Coordinate(x=36.0, y=12.0),
                to_position=Coordinate(x=28.0, y=15.0),
                speed_limit=1.4,
                congestion=0.05,
                allowed_resource_kinds=[
                    "fire_truck",
                    "police_car",
                    "ground_vehicle",
                    "unmanned_vehicle",
                ],
            ),
            RoadSegment(
                id="road-avenue-to-plaza",
                from_node="avenue_west",
                to_node="central_plaza",
                from_position=Coordinate(x=28.0, y=15.0),
                to_position=Coordinate(x=42.0, y=18.0),
                speed_limit=1.1,
                congestion=0.35,
                allowed_resource_kinds=[
                    "fire_truck",
                    "police_car",
                    "ground_vehicle",
                    "unmanned_vehicle",
                ],
            ),
            RoadSegment(
                id="road-fire-east-to-plaza",
                from_node="fire_east",
                to_node="central_plaza",
                from_position=Coordinate(x=68.0, y=21.0),
                to_position=Coordinate(x=42.0, y=18.0),
                speed_limit=1.2,
                congestion=0.2,
                allowed_resource_kinds=[
                    "fire_truck",
                    "police_car",
                    "ground_vehicle",
                    "unmanned_vehicle",
                ],
            ),
            RoadSegment(
                id="air-corridor-drone-west",
                from_node="drone_west",
                to_node="central_plaza",
                from_position=Coordinate(x=20.0, y=30.0, z=12.0),
                to_position=Coordinate(x=42.0, y=18.0, z=12.0),
                speed_limit=2.5,
                congestion=0.0,
                allowed_resource_kinds=["drone"],
            ),
        ],
    )


def _distance(left: Coordinate, right: Coordinate) -> float:
    return math.sqrt(
        (left.x - right.x) ** 2
        + (left.y - right.y) ** 2
        + (left.z - right.z) ** 2
    )


_PATROL_ARRIVAL_EPS_M = 0.5
