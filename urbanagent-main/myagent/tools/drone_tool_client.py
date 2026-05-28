import requests
from typing import Any, Dict


DRONE_AGENT_URL = "http://127.0.0.1:8010"


def call_drone_skill(
    skill: str,
    vehicle: str = "D-01",
    args: Dict[str, Any] | None = None,
    timeout: int = 60,
) -> Dict[str, Any]:
    if args is None:
        args = {}

    payload = {
        "skill": skill,
        "vehicle": vehicle,
        "args": args,
    }

    try:
        response = requests.post(
            f"{DRONE_AGENT_URL}/skill",
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


if __name__ == "__main__":
    print(call_drone_skill("drone_status", "D-01", {}))
