"""Shared fire-scene UAV_GOTO hover offset (west of incident in CARLA +x east)."""
from __future__ import annotations

from urbanagent.types import Coordinate, UrbanAction

# GOTO hover point offset from incident xy (negative = -X).
FIRE_HOLD_GOTO_X_OFFSET_M = -10.0

# Roles whose destination is already adjusted before dispatch.
_PREOFFSET_ROLES = frozenset({"fire_confirmation_hover", "fire_watch_patrol"})


def offset_fire_hold_goto_position(position: Coordinate) -> Coordinate:
    return Coordinate(
        position.x + FIRE_HOLD_GOTO_X_OFFSET_M,
        position.y,
        position.z,
    )


def should_apply_fire_goto_offset(action: UrbanAction) -> bool:
    if action.kind != "dispatch_drone" or action.destination is None:
        return False
    if action.parameters.get("fire_goto_offset_applied"):
        return False
    role = str(action.parameters.get("role", ""))
    return role not in _PREOFFSET_ROLES


def apply_fire_goto_offset_to_action(action: UrbanAction) -> UrbanAction:
    if not should_apply_fire_goto_offset(action):
        return action
    params = dict(action.parameters)
    params["fire_goto_offset_applied"] = True
    return UrbanAction(
        kind=action.kind,
        target_id=action.target_id,
        destination=offset_fire_hold_goto_position(action.destination),
        parameters=params,
        reason=action.reason,
    )


def apply_fire_goto_offset_to_actions(actions: list[UrbanAction]) -> list[UrbanAction]:
    return [apply_fire_goto_offset_to_action(action) for action in actions]
