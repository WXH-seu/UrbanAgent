"""Shared resource availability policy for dispatch decisions."""
from __future__ import annotations

from urbanagent.types import ResourceKind, ResourceStatus, UrbanResource


MIN_BATTERY_FOR_DISPATCH: dict[ResourceKind, float] = {
    "drone": 25.0,
    "unmanned_vehicle": 15.0,
    "ground_vehicle": 15.0,
    "police_car": 15.0,
    "fire_truck": 15.0,
}

_AVAILABLE_ROLES = {"", "available", "idle", "dispatchable", "standby", "ready", "patrol"}
_BUSY_ROLES = {
    "assigned",
    "busy",
    "dispatching",
    "enroute",
    "executing",
    "in_mission",
    "mission",
    "on_mission",
    "tasked",
    "working",
}
_OFFLINE_ROLES = {"offline", "disabled", "maintenance", "out_of_service"}


def bridge_status_from_role_speed(role: str, speed: float, *, patrol_available: bool = False) -> ResourceStatus:
    """Map CarlaBridge role/state strings into UrbanAgent resource availability."""

    normalized = role.strip().lower()
    if patrol_available and normalized == "patrol":
        return "available"
    if normalized in _OFFLINE_ROLES:
        return "offline"
    if normalized in _BUSY_ROLES:
        return "busy"
    if normalized in _AVAILABLE_ROLES:
        return "dispatched" if speed > 0.1 else "available"
    return "dispatched" if speed > 0.1 else "available"


def resource_block_reason(resource: UrbanResource) -> str | None:
    """Return a dispatch-blocking reason, or None when the resource can be assigned."""

    if resource.status != "available":
        return f"status={resource.status}"
    minimum = MIN_BATTERY_FOR_DISPATCH.get(resource.kind)
    if minimum is not None and resource.battery_remaining is not None:
        if resource.battery_remaining < minimum:
            return f"battery={resource.battery_remaining:.1f}<min={minimum:.1f}"
    if resource.kind == "fire_truck" and resource.water_remaining is not None:
        if resource.water_remaining <= 0:
            return "water_remaining=0"
    return None


def is_dispatchable_resource(resource: UrbanResource) -> bool:
    return resource_block_reason(resource) is None
