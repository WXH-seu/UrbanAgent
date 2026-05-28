"""Multi-agent MVP tests (no LLM / no network)."""
from __future__ import annotations

import unittest

from urbanagent import UrbanMultiAgentSystem
from urbanagent.dispatch import DispatchPolicy
from urbanagent.multiagent.subagents.default_agents import integrate_actions_deterministic
from urbanagent.sandbox import MockSandboxClient
from urbanagent.types import CityState, Coordinate, Incident, TrafficSignal, UrbanResource


class MultiAgentMVPTest(unittest.IsolatedAsyncioTestCase):
    async def test_run_no_llm_succeeds(self) -> None:
        sys = UrbanMultiAgentSystem(use_llm=False, use_llm_batch_rerank=False)
        r = await sys.run("incident-fire-001 高严重度火情")
        self.assertTrue(r.gate.should_intervene)
        self.assertIsNotNone(r.committed)
        self.assertIsNotNone(r.batch_outcome)
        self.assertTrue(r.batch_outcome.criteria_satisfied, msg=str(r.batch_outcome.notes))
        self.assertGreater(len(r.committed.actions), 0)

    async def test_gate_skips_when_no_trigger(self) -> None:
        empty = CityState(incidents=[], resources=[], timestamp="t0")
        sys = UrbanMultiAgentSystem(sandbox=MockSandboxClient(empty), use_llm=False)
        r = await sys.run("hello world no emergency")
        self.assertFalse(r.gate.should_intervene)

    async def test_no_dispatchable_resources_degrades_hard(self) -> None:
        state = CityState(
            timestamp="t0",
            incidents=[
                Incident(
                    id="incident-fire-001",
                    kind="fire",
                    severity="high",
                    position=Coordinate(10, 10, 0),
                )
            ],
            resources=[
                UrbanResource(
                    id="fire-low",
                    kind="fire_truck",
                    position=Coordinate(0, 0, 0),
                    battery_remaining=1.0,
                    water_remaining=100.0,
                    capabilities=["fire_suppression"],
                ),
                UrbanResource(
                    id="drone-low",
                    kind="drone",
                    position=Coordinate(0, 0, 10),
                    battery_remaining=1.0,
                    capabilities=["aerial_recon"],
                ),
                UrbanResource(
                    id="ugv-low",
                    kind="unmanned_vehicle",
                    position=Coordinate(0, 0, 0),
                    battery_remaining=1.0,
                    capabilities=["logistics_support"],
                ),
                UrbanResource(
                    id="police-low",
                    kind="police_car",
                    position=Coordinate(0, 0, 0),
                    battery_remaining=1.0,
                    capabilities=["traffic_control"],
                ),
            ],
            traffic_signals=[
                TrafficSignal(id="signal-01", position=Coordinate(9, 9, 0)),
            ],
        )
        sys = UrbanMultiAgentSystem(
            sandbox=MockSandboxClient(state),
            use_llm=False,
            use_llm_batch_rerank=False,
        )
        r = await sys.run("incident-fire-001 高严重度火情")
        self.assertIsNotNone(r.committed)
        self.assertEqual(r.committed.actions, [])
        self.assertIn("no dispatchable mobile resources", r.committed.rationale)
        self.assertIsNotNone(r.batch_outcome)
        self.assertFalse(r.batch_outcome.criteria_satisfied)
        self.assertIn("no executable actions", " ".join(r.batch_outcome.notes))

    async def test_carla_bridge_fire_ugv_gets_goto_then_extinguish(self) -> None:
        state = CityState(
            timestamp="t0",
            incidents=[
                Incident(
                    id="incident-fire-001",
                    kind="fire",
                    severity="high",
                    position=Coordinate(10, 10, 0),
                )
            ],
            resources=[
                UrbanResource(
                    id="UGV-01",
                    kind="unmanned_vehicle",
                    position=Coordinate(0, 0, 0),
                    capabilities=["fire_suppression", "logistics_support"],
                ),
                UrbanResource(
                    id="UAV-01",
                    kind="drone",
                    position=Coordinate(0, 0, 10),
                    battery_remaining=100.0,
                    capabilities=["aerial_recon"],
                ),
            ],
        )
        actions, rationale = integrate_actions_deterministic(
            [],
            state=state,
            dispatch_policy=DispatchPolicy(),
        )
        self.assertNotIn("no executable", rationale)
        ugv_actions = [a for a in actions if a.target_id == "UGV-01"]
        self.assertEqual(len(ugv_actions), 2)
        self.assertTrue(
            all(a.parameters.get("intent") == "extinguish" for a in ugv_actions)
        )
        self.assertFalse(ugv_actions[0].parameters.get("force_extinguish", False))
        self.assertTrue(ugv_actions[1].parameters.get("force_extinguish"))

    async def test_fire_ugv_destination_offset_right_one_lane(self) -> None:
        state = CityState(
            timestamp="t0",
            incidents=[
                Incident(
                    id="incident-fire-001",
                    kind="fire",
                    severity="high",
                    position=Coordinate(100, 0, 0),
                )
            ],
            resources=[
                UrbanResource(
                    id="UGV-01",
                    kind="unmanned_vehicle",
                    position=Coordinate(0, 0, 0),
                    capabilities=["fire_suppression", "logistics_support"],
                ),
            ],
        )
        actions, _ = integrate_actions_deterministic(
            [],
            state=state,
            dispatch_policy=DispatchPolicy(fire_lane_offset_m=3.5),
        )
        ugv_actions = [a for a in actions if a.target_id == "UGV-01"]
        self.assertEqual(len(ugv_actions), 2)
        for action in ugv_actions:
            self.assertAlmostEqual(action.destination.x, 100.0, places=3)
            self.assertAlmostEqual(action.destination.y, 3.5, places=3)
            self.assertAlmostEqual(action.destination.z, 0.0, places=3)

    async def test_fire_ugv_offset_disabled_when_zero(self) -> None:
        state = CityState(
            timestamp="t0",
            incidents=[
                Incident(
                    id="incident-fire-001",
                    kind="fire",
                    severity="high",
                    position=Coordinate(0, 50, 0),
                )
            ],
            resources=[
                UrbanResource(
                    id="UGV-01",
                    kind="unmanned_vehicle",
                    position=Coordinate(0, 0, 0),
                    capabilities=["fire_suppression"],
                ),
            ],
        )
        actions, _ = integrate_actions_deterministic(
            [],
            state=state,
            dispatch_policy=DispatchPolicy(fire_lane_offset_m=0.0),
        )
        goto = next(
            a
            for a in actions
            if a.target_id == "UGV-01" and not a.parameters.get("force_extinguish")
        )
        self.assertEqual(goto.destination, Coordinate(0, 50, 0))

    async def test_patrol_detects_fire_dispatches_and_returns(self) -> None:
        state = CityState(
            timestamp="t0",
            incidents=[],
            resources=[
                UrbanResource(
                    id="UAV-01",
                    kind="drone",
                    position=Coordinate(0, 0, 60),
                    battery_remaining=100.0,
                    capabilities=["aerial_recon"],
                ),
                UrbanResource(
                    id="UGV-01",
                    kind="unmanned_vehicle",
                    position=Coordinate(0, 0, 0),
                    capabilities=["fire_suppression", "logistics_support"],
                ),
            ],
            traffic_signals=[
                TrafficSignal(id="signal-01", position=Coordinate(10, 10, 0)),
            ],
        )

        class FireDiscoverySandbox(MockSandboxClient):
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

        sandbox = FireDiscoverySandbox(state)
        sys = UrbanMultiAgentSystem(
            sandbox=sandbox,
            use_llm=False,
            use_llm_batch_rerank=False,
        )

        result = await sys.run_patrol_fire_response(
            detection_poll_interval_s=0.0,
            max_detection_rounds=1,
        )

        self.assertEqual(result.detected_incident_id, "incident-fire-001")
        self.assertIsNotNone(result.patrol_outcome)
        self.assertTrue(result.patrol_outcome.criteria_satisfied)
        self.assertIsNotNone(result.hold_outcome)
        self.assertTrue(result.hold_outcome.criteria_satisfied)
        self.assertIsNotNone(result.response)
        self.assertTrue(result.response.batch_outcome.criteria_satisfied)
        self.assertIsNotNone(result.return_outcome)
        self.assertTrue(result.return_outcome.criteria_satisfied)

        applied_kinds = [r.action.kind for r in sandbox.applied_results]
        self.assertIn("patrol_drone", applied_kinds)
        self.assertIn("hold_drone", applied_kinds)
        self.assertIn("dispatch_vehicle", applied_kinds)
        self.assertIn("dispatch_drone", applied_kinds)
        self.assertIn("return_vehicle", applied_kinds)
        self.assertIn("return_drone", applied_kinds)
        self.assertEqual(state.incidents[0].status, "resolved")


if __name__ == "__main__":
    unittest.main()
