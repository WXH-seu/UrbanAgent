from urbanagent.real_action_executor import RealActionExecutor
from urbanagent.types import UrbanAction, Coordinate


def main():
    executor = RealActionExecutor()

    actions = [
        UrbanAction(
            kind="dispatch_drone",
            target_id="D-01",
            destination=Coordinate(x=0.0, y=0.0, z=0.0),
            parameters={
                "incident_id": "demo-incident-001",
                "role": "aerial_recon",
            },
            reason="Real hardware execution test.",
        )
    ]

    results = executor.execute_batch(actions)

    for r in results:
        print(r)


if __name__ == "__main__":
    main()
