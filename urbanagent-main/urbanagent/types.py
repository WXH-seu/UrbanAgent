"""Domain types for UrbanAgent emergency dispatch."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Any, Literal


ResourceKind = Literal[
    "fire_truck",
    "police_car",
    "drone",
    "ground_vehicle",
    "unmanned_vehicle",
    "traffic_light",
]
ResourceStatus = Literal["available", "dispatched", "busy", "offline"]
IncidentKind = Literal["fire", "traffic_accident", "security", "medical", "other"]
IncidentSeverity = Literal["low", "medium", "high", "critical"]
IncidentStatus = Literal["open", "responding", "resolved"]
StationKind = Literal["fire_station", "police_station", "drone_base"]
ActionKind = Literal[
    "dispatch_vehicle",
    "dispatch_drone",
    "patrol_drone",
    "return_drone",
    "hold_drone",
    "return_vehicle",
    "stop_vehicle",
    "control_traffic_light",
    "mark_incident",
]
ActionStatus = Literal["accepted", "rejected", "applied"]


@dataclass
class Coordinate:
    """A sandbox-agnostic 2D coordinate.

    Real integrations can map this to Unity, Unreal, GIS, or grid coordinates
    inside the sandbox adapter.
    """

    x: float
    y: float
    z: float = 0.0


@dataclass
class UrbanResource:
    """A controllable city resource such as a fire truck, police car, or drone."""

    id: str
    kind: ResourceKind
    position: Coordinate
    status: ResourceStatus = "available"
    capacity: int = 1
    label: str = ""
    home_base_id: str = ""
    speed: float = 1.0
    current_task_id: str | None = None
    water_remaining: float | None = None
    payload_remaining: float | None = None
    battery_remaining: float | None = None
    capabilities: list[str] = field(default_factory=list)


@dataclass
class FireStation:
    """A fire station that owns fire trucks and keeps a local reserve."""

    id: str
    name: str
    position: Coordinate
    resource_ids: list[str] = field(default_factory=list)
    reserve_ratio: float = 0.3


@dataclass
class PoliceStation:
    """A police station that owns police cars and keeps a local reserve."""

    id: str
    name: str
    position: Coordinate
    resource_ids: list[str] = field(default_factory=list)
    reserve_ratio: float = 0.3


@dataclass
class DroneBase:
    """A drone base that owns aerial resources and keeps a local reserve."""

    id: str
    name: str
    position: Coordinate
    resource_ids: list[str] = field(default_factory=list)
    reserve_ratio: float = 0.3


@dataclass
class TrafficSignal:
    """A signalized intersection that may be controlled by the agent."""

    id: str
    position: Coordinate
    mode: str = "normal"
    status: ResourceStatus = "available"


@dataclass
class RoadSegment:
    """A lightweight road state record for routing-aware dispatch."""

    id: str
    from_node: str
    to_node: str
    from_position: Coordinate | None = None
    to_position: Coordinate | None = None
    length: float | None = None
    speed_limit: float = 1.0
    congestion: float = 0.0
    blocked: bool = False
    allowed_resource_kinds: list[ResourceKind] = field(default_factory=list)


@dataclass
class Incident:
    """An emergency event observed in the city sandbox."""

    id: str
    kind: IncidentKind
    position: Coordinate
    severity: IncidentSeverity = "medium"
    status: IncidentStatus = "open"
    description: str = ""


@dataclass
class UrbanAction:
    """One command the agent wants to apply to the city sandbox."""

    kind: ActionKind
    target_id: str
    destination: Coordinate | None = None
    parameters: dict[str, Any] = field(default_factory=dict)
    reason: str = ""


@dataclass
class ActionResult:
    """Sandbox feedback after an action was accepted or rejected."""

    status: ActionStatus
    action: UrbanAction
    message: str


@dataclass
class CityState:
    """A snapshot of the city sandbox visible to UrbanAgent."""

    incidents: list[Incident] = field(default_factory=list)
    resources: list[UrbanResource] = field(default_factory=list)
    fire_stations: list[FireStation] = field(default_factory=list)
    police_stations: list[PoliceStation] = field(default_factory=list)
    drone_bases: list[DroneBase] = field(default_factory=list)
    traffic_signals: list[TrafficSignal] = field(default_factory=list)
    roads: list[RoadSegment] = field(default_factory=list)
    timestamp: str = "mock-0000"


@dataclass
class RouteEstimate:
    """Estimated route cost between a resource and an incident."""

    distance: float
    travel_time: float
    path: list[str] = field(default_factory=list)
    congestion: float = 0.0
    source: str = "straight_line"


@dataclass
class CandidateScore:
    """Why one resource is a good or poor candidate for one incident role."""

    incident_id: str
    resource_id: str
    role: str
    score: float
    response_time: float
    congestion_penalty: float
    capability_penalty: float
    load_penalty: float
    reserve_penalty: float
    route: RouteEstimate
    reason: str = ""


@dataclass
class DispatchAssignment:
    """A selected resource assignment for one incident."""

    incident_id: str
    resource_id: str
    role: str
    action_kind: ActionKind
    destination: Coordinate
    score: CandidateScore
    reason: str = ""


@dataclass
class DispatchPlan:
    """A deterministic dispatch plan generated before sandbox actions are sent."""

    assignments: list[DispatchAssignment] = field(default_factory=list)
    candidate_scores: list[CandidateScore] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def to_plain_data(value: Any) -> Any:
    """Convert UrbanAgent dataclasses into JSON-serializable plain data."""

    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, list):
        return [to_plain_data(item) for item in value]
    if isinstance(value, dict):
        return {str(key): to_plain_data(item) for key, item in value.items()}
    return value


def to_json(value: Any) -> str:
    """Render a stable JSON string for tool results and demos."""

    return json.dumps(to_plain_data(value), ensure_ascii=False, indent=2, sort_keys=True)
