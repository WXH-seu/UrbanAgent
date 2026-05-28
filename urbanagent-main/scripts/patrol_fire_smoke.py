"""Offline smoke for UrbanMultiAgentSystem.run_patrol_fire_response.

Reproduces the closed loop without CarlaBridge:
  no fire -> patrol -> mock sandbox injects a fire after the first patrol
  command -> hold above incident -> dispatch -> return-to-base.

Run:
    python scripts/patrol_fire_smoke.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from urbanagent import UrbanMultiAgentSystem
from urbanagent.sandbox import MockSandboxClient
from urbanagent.types import (
    CityState,
    Coordinate,
    Incident,
    TrafficSignal,
    UrbanResource,
)


def _initial_state() -> CityState:
    return CityState(
        timestamp="smoke-t0",
        incidents=[],
        resources=[
            UrbanResource(
                id="UAV-01",
                kind="drone",
                position=Coordinate(0, 0, 60),
                battery_remaining=100.0,
                capabilities=["aerial_recon", "thermal_imaging"],
            ),
            UrbanResource(
                id="UGV-01",
                kind="unmanned_vehicle",
                position=Coordinate(0, 0, 0),
                battery_remaining=100.0,
                capabilities=["fire_suppression", "logistics_support"],
            ),
        ],
        traffic_signals=[
            TrafficSignal(id="signal-01", position=Coordinate(10, 10, 0)),
        ],
    )


class FireDiscoverySandbox(MockSandboxClient):
    """Mock that spawns a fire as soon as the first UAV patrol is issued."""

    async def send_action(self, action):
        result = await super().send_action(action)
        if action.kind == "patrol_drone" and not self._state.incidents:
            self._state.incidents.append(
                Incident(
                    id="incident-fire-001",
                    kind="fire",
                    severity="high",
                    position=Coordinate(25.3, 24.4, 0),
                )
            )
        return result


async def main() -> int:
    sandbox = FireDiscoverySandbox(_initial_state())
    system = UrbanMultiAgentSystem(
        sandbox=sandbox,
        use_llm=False,
        use_llm_batch_rerank=False,
    )

    result = await system.run_patrol_fire_response(
        detection_poll_interval_s=0.0,
        max_detection_rounds=2,
    )

    print("final_report:", result.final_report)
    print("detected_incident_id:", result.detected_incident_id)
    print("detection_notes:", result.detection_notes)

    applied = [r.action.kind for r in sandbox.applied_results]
    print("applied_kinds:", applied)
    assert "patrol_drone" in applied, applied
    assert "hold_drone" in applied, applied
    assert result.hold_outcome is not None
    assert result.hold_outcome.criteria_satisfied, result.hold_outcome.notes
    assert "dispatch_vehicle" in applied, applied
    assert "dispatch_drone" in applied, applied
    assert "return_vehicle" in applied, applied
    assert "return_drone" in applied, applied
    assert result.response is not None
    assert result.response.batch_outcome.criteria_satisfied, (
        result.response.batch_outcome.notes
    )
    assert result.return_outcome is not None
    assert result.return_outcome.criteria_satisfied, result.return_outcome.notes
    print("ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
