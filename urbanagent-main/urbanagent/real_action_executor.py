from __future__ import annotations

from typing import Any

from urbanagent.real_drone_bridge import RealDroneBridge
from urbanagent.types import UrbanAction


class RealActionExecutor:
    """
    真实硬件动作执行器。
    第一版只执行 dispatch_drone。
    后续可以加入：
    - dispatch_vehicle -> CarAgent
    - stop_vehicle -> CarAgent
    - control_traffic_light -> TrafficSignalAgent
    """

    def __init__(self) -> None:
        self.drone_bridge = RealDroneBridge()

    def execute(self, action: UrbanAction) -> dict[str, Any]:
        if action.kind == "dispatch_drone":
            return self.drone_bridge.execute_action(action)

        return {
            "ok": False,
            "action": str(action.kind),
            "target_id": action.target_id,
            "result": None,
            "error": f"No real executor registered for action kind: {action.kind}",
        }

    def execute_batch(self, actions: list[UrbanAction]) -> list[dict[str, Any]]:
        results = []

        for action in actions:
            result = self.execute(action)
            results.append(result)

            if not result.get("ok") and action.kind == "dispatch_drone":
                stop = self.drone_bridge.emergency_stop(action.target_id)
                results.append({
                    "ok": bool(stop.get("ok")),
                    "action": "drone_emergency_stop",
                    "target_id": action.target_id,
                    "result": stop,
                    "error": stop.get("error"),
                })

        return results

