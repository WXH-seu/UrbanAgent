from __future__ import annotations

from typing import Any

import requests

from urbanagent.types import UrbanAction


class RealDroneBridge:
    """
    将 UrbanAgent 的 UrbanAction(kind='dispatch_drone') 映射到真实 DroneAgent。
    注意：
    - 不接管 CarlaBridge
    - 不改 DroneSubAgent 的规划逻辑
    - 只负责真实硬件执行
    """

    def __init__(
        self,
        drone_agent_url: str = "http://127.0.0.1:8010",
    ) -> None:
        self.drone_agent_url = drone_agent_url.rstrip("/")

    def _call_drone_skill(
        self,
        skill: str,
        vehicle: str,
        args: dict[str, Any] | None = None,
        timeout: int = 120,
    ) -> dict[str, Any]:
        payload = {
            "skill": skill,
            "vehicle": vehicle,
            "args": args or {},
        }

        try:
            resp = requests.post(
                f"{self.drone_agent_url}/skill",
                json=payload,
                timeout=timeout,
            )
            resp.raise_for_status()
            return resp.json()

        except Exception as e:
            return {
                "ok": False,
                "action": skill,
                "result": None,
                "error": str(e),
            }

    def execute_action(self, action: UrbanAction) -> dict[str, Any]:
        """
        执行单个 UrbanAction。
        第一版只处理 dispatch_drone。
        """

        if action.kind != "dispatch_drone":
            return {
                "ok": False,
                "action": str(action.kind),
                "result": None,
                "error": f"RealDroneBridge does not support action kind: {action.kind}",
            }

        vehicle = action.target_id

        role = str(action.parameters.get("role", "aerial_recon"))
        incident_id = action.parameters.get("incident_id")

        # 先做状态检查
        status = self._call_drone_skill(
            skill="drone_status",
            vehicle=vehicle,
            args={},
            timeout=30,
        )

        if not status.get("ok"):
            return {
                "ok": False,
                "action": "dispatch_drone",
                "result": {
                    "status": status,
                },
                "error": "drone_status failed; abort dispatch_drone",
            }

        # 第一版：dispatch_drone = 低空本地安全巡检
        # 不直接使用 UrbanAgent 的 destination 作为无人机坐标。
        # 因为 Crazyflie Flow deck 坐标是相对起飞点，不是城市全局坐标。
        if role == "aerial_recon":
            flight = self._call_drone_skill(
                skill="drone_square_route",
                vehicle=vehicle,
                args={
                    "height": 0.20,
                    "size": 0.15,
                    "velocity": 0.08,
                    "hold_time": 1.0,
                    "incident_id": incident_id,
                },
                timeout=120,
            )
        else:
            flight = self._call_drone_skill(
                skill="drone_takeoff_land",
                vehicle=vehicle,
                args={
                    "height": 0.20,
                    "hover_time": 1.5,
                    "incident_id": incident_id,
                },
                timeout=90,
            )

        return {
            "ok": bool(flight.get("ok")),
            "action": "dispatch_drone",
            "target_id": vehicle,
            "urban_action": {
                "kind": action.kind,
                "target_id": action.target_id,
                "destination": (
                    {
                        "x": action.destination.x,
                        "y": action.destination.y,
                        "z": action.destination.z,
                    }
                    if action.destination is not None
                    else None
                ),
                "parameters": dict(action.parameters),
                "reason": action.reason,
            },
            "result": {
                "status": status,
                "flight": flight,
            },
            "error": flight.get("error"),
        }

    def emergency_stop(self, vehicle: str = "D-01") -> dict[str, Any]:
        return self._call_drone_skill(
            skill="drone_emergency_stop",
            vehicle=vehicle,
            args={},
            timeout=20,
        )
