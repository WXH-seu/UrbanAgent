# myagent/skills/drone_skills.py

from myagent.tools.drone_tool_client import call_drone_skill


def drone_status(vehicle: str, args: dict):
    return call_drone_skill(
        skill="drone_status",
        vehicle=vehicle,
        args=args,
        timeout=30,
    )


def drone_takeoff_land(vehicle: str, args: dict):
    return call_drone_skill(
        skill="drone_takeoff_land",
        vehicle=vehicle,
        args=args,
        timeout=90,
    )


def drone_square_route(vehicle: str, args: dict):
    return call_drone_skill(
        skill="drone_square_route",
        vehicle=vehicle,
        args=args,
        timeout=120,
    )


def drone_emergency_stop(vehicle: str, args: dict):
    return call_drone_skill(
        skill="drone_emergency_stop",
        vehicle=vehicle,
        args=args,
        timeout=20,
    )
