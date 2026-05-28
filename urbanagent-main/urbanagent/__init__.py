"""UrbanAgent: multi-agent emergency dispatch for CarlaBridge/CARLA city sandbox."""

from __future__ import annotations

from urbanagent.carla_bridge import CarlaBridgeSandboxClient
from urbanagent.dispatch import DispatchPolicy, assignment_to_action
from urbanagent.errors import SandboxWireError
from urbanagent.multiagent import (
    PatrolFireResponseResult,
    UrbanMultiAgentResult,
    UrbanMultiAgentSystem,
)
from urbanagent.routing import (
    ExternalMapRoutePlanner,
    LocalGraphRoutePlanner,
    RoutePlanner,
    StraightLineRoutePlanner,
)
from urbanagent.sandbox import (
    MockSandboxClient,
    SandboxClient,
    build_single_fire_state,
)
from urbanagent.schemas import (
    UrbanConstraint,
    UrbanTask,
)
from urbanagent.types import (
    ActionResult,
    CandidateScore,
    CityState,
    Coordinate,
    DispatchAssignment,
    DispatchPlan,
    DroneBase,
    FireStation,
    Incident,
    PoliceStation,
    RouteEstimate,
    TrafficSignal,
    UrbanAction,
    UrbanResource,
)

__all__ = [
    "SandboxWireError",
    "ActionResult",
    "CandidateScore",
    "CarlaBridgeSandboxClient",
    "CityState",
    "Coordinate",
    "DispatchAssignment",
    "DispatchPlan",
    "DispatchPolicy",
    "DroneBase",
    "ExternalMapRoutePlanner",
    "FireStation",
    "Incident",
    "LocalGraphRoutePlanner",
    "MockSandboxClient",
    "PoliceStation",
    "RouteEstimate",
    "RoutePlanner",
    "SandboxClient",
    "StraightLineRoutePlanner",
    "TrafficSignal",
    "UrbanAction",
    "UrbanConstraint",
    "UrbanMultiAgentResult",
    "UrbanMultiAgentSystem",
    "UrbanResource",
    "UrbanTask",
    "PatrolFireResponseResult",
    "assignment_to_action",
    "build_single_fire_state",
]
