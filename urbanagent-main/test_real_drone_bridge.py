from urbanagent.real_drone_bridge import RealDroneBridge
from urbanagent.types import UrbanAction, Coordinate


def main():
    bridge = RealDroneBridge(drone_agent_url="http://127.0.0.1:8010")

    action = UrbanAction(
        kind="dispatch_drone",
        target_id="D-01",
        destination=Coordinate(x=0.0, y=0.0, z=0.0),
        parameters={
            "incident_id": "demo-incident-001",
            "role": "aerial_recon",
        },
        reason="Real DroneAgent bridge test.",
    )

    result = bridge.execute_action(action)
    print(result)


if __name__ == "__main__":
    main()
