"""Tests for Bridge x Agent Protocol v1.0 adapter (CarlaBridgeSandboxClient)."""
from __future__ import annotations

import asyncio
import unittest
from typing import Any, Callable

from urbanagent.carla_bridge import (
    CarlaBridgeSandboxClient,
    PROTOCOL_VERSION,
    carla_snapshot_to_city_state,
)
from urbanagent.multiagent.batch_runner import batch_criteria_met
from urbanagent.types import ActionResult, Coordinate, Incident, UrbanAction


# --- helpers ----------------------------------------------------------------


class FakeAsyncClient:
    """Minimal stand-in for ``socketio.AsyncClient``."""

    def __init__(self) -> None:
        self.connected = False
        self.namespace = "/agent"
        self.handlers: dict[str, Callable[..., Any]] = {}
        self.connect_handlers: dict[str, Callable[..., Any]] = {}
        self.disconnect_handlers: dict[str, Callable[..., Any]] = {}
        self.emitted: list[tuple[str, Any]] = []
        self.calls: list[tuple[str, Any]] = []
        # Each entry is a function ``(event, payload) -> ack_value``.
        self.call_responder: Callable[[str, Any], Any] | None = None

    def event(self, namespace: str = "/"):
        def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            name = getattr(fn, "__name__", "")
            if name == "connect":
                self.connect_handlers[namespace] = fn
            elif name == "disconnect":
                self.disconnect_handlers[namespace] = fn
            return fn

        return decorator

    def on(self, event: str, namespace: str = "/"):
        def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            self.handlers[event] = fn
            return fn

        return decorator

    async def connect(self, url: str, namespaces: list[str], wait_timeout: float) -> None:
        del url, namespaces, wait_timeout
        self.connected = True
        for handler in self.connect_handlers.values():
            await handler()

    async def disconnect(self) -> None:
        self.connected = False
        for handler in self.disconnect_handlers.values():
            await handler()

    async def emit(self, event: str, payload: Any, namespace: str = "/") -> None:
        del namespace
        self.emitted.append((event, payload))

    async def call(
        self,
        event: str,
        payload: Any,
        namespace: str = "/",
        timeout: float = 0.0,
    ) -> Any:
        del namespace, timeout
        self.calls.append((event, payload))
        if self.call_responder is None:
            return None
        return self.call_responder(event, payload)

    async def deliver(self, event: str, data: Any) -> None:
        """Invoke a registered handler as if Bridge had pushed an event."""

        handler = self.handlers.get(event)
        if handler is not None:
            await handler(data)


def _envelope(event: str, payload: dict[str, Any], **extra: Any) -> dict[str, Any]:
    env = {
        "version": PROTOCOL_VERSION,
        "msg_id": "msg-test",
        "type": event,
        "timestamp": 0.0,
        "frame": None,
        "sim_time": None,
        "sender": "bridge",
        "payload": payload,
    }
    env.update(extra)
    return env


async def _connect_with_fake(
    client: CarlaBridgeSandboxClient,
    fake: FakeAsyncClient,
    *,
    hello_response: dict[str, Any] | None = None,
) -> None:
    """Wire ``fake`` into ``client`` and run the connect+hello sequence."""

    client._sio = fake  # type: ignore[attr-defined]
    client._register_handlers()
    fake.call_responder = lambda event, payload: (
        hello_response or {
            "server": "carlabridge",
            "version": PROTOCOL_VERSION,
            "bridge_session_id": "br-test-1",
            "scenario": "s1_fire",
        }
    ) if event == "hello" else None
    await fake.connect("http://test", ["/agent"], 1.0)
    await client._do_hello()  # type: ignore[attr-defined]


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# --- pure helpers -----------------------------------------------------------


class SnapshotTranslationTest(unittest.TestCase):
    def test_pose_array_and_position_object(self) -> None:
        state = carla_snapshot_to_city_state(
            {
                "traffic_lights": [
                    {"id": "TL-01", "pose": [1, 2, 3], "phase": "green", "remaining_s": 5.0},
                ],
                "vehicles": [
                    {
                        "id": "UGV-01",
                        "role": "dispatchable",
                        "pose": [10, 20, 0],
                        "speed": 2.0,
                        "battery": 88,
                    },
                    {
                        "id": "UGV-02",
                        "role": "mission",
                        "pose": [11, 21, 0],
                        "speed": 0.0,
                        "battery": 80,
                    },
                    {"id": "VEH-99", "role": "civilian", "pose": [9, 9, 0]},
                ],
                "uavs": [
                    {
                        "id": "UAV-01",
                        "role": "patrol",
                        "pose": [5, 6, 30],
                        "speed": 3.0,
                        "battery": 70,
                    },
                    {
                        "id": "UAV-02",
                        "role": "tasked",
                        "pose": [6, 7, 30],
                        "speed": 0.0,
                        "battery": 70,
                    }
                ],
                "incidents": [
                    {
                        "id": "fire-001",
                        "kind": "fire",
                        "position": {"x": 90.0, "y": 0.0, "z": 0.0},
                        "severity": "high",
                    }
                ],
            }
        )

        # civilian vehicle filtered out; dispatchable UGV becomes unmanned_vehicle
        kinds = [r.kind for r in state.resources]
        self.assertEqual(kinds, ["unmanned_vehicle", "ground_vehicle", "drone", "drone"])
        self.assertEqual(state.resources[0].position, Coordinate(10.0, 20.0, 0.0))
        self.assertEqual(state.resources[1].status, "busy")
        self.assertEqual(state.resources[2].status, "available")
        self.assertEqual(state.resources[3].status, "busy")
        self.assertEqual(state.traffic_signals[0].mode, "green")
        self.assertEqual(state.traffic_signals[0].position, Coordinate(1.0, 2.0, 3.0))
        self.assertEqual(state.incidents[0].position, Coordinate(90.0, 0.0, 0.0))
        self.assertEqual(state.incidents[0].status, "open")

    def test_default_incidents_when_snapshot_empty(self) -> None:
        state = carla_snapshot_to_city_state(
            {"vehicles": [], "uavs": [], "traffic_lights": [], "incidents": []},
            default_incidents=[
                Incident(
                    id="fallback",
                    kind="fire",
                    position=Coordinate(0, 0, 0),
                    severity="high",
                )
            ],
        )
        self.assertEqual([inc.id for inc in state.incidents], ["fallback"])


class BatchCriteriaTest(unittest.TestCase):
    def test_all_applied_passes(self) -> None:
        action = UrbanAction(
            kind="dispatch_vehicle",
            target_id="UGV-01",
            destination=Coordinate(1, 1, 0),
        )
        results = [ActionResult(status="applied", action=action, message="completed")]
        self.assertTrue(batch_criteria_met(None, [action], results))  # type: ignore[arg-type]

    def test_rejected_fails(self) -> None:
        action = UrbanAction(
            kind="dispatch_vehicle",
            target_id="UGV-01",
            destination=Coordinate(1, 1, 0),
        )
        results = [ActionResult(status="rejected", action=action, message="boom")]
        self.assertFalse(batch_criteria_met(None, [action], results))  # type: ignore[arg-type]


# --- end-to-end with fake socketio -----------------------------------------


class SendActionTest(unittest.IsolatedAsyncioTestCase):
    async def test_dispatch_drone_maps_to_uav_goto_and_waits_for_completed(self) -> None:
        client = CarlaBridgeSandboxClient("http://test")
        fake = FakeAsyncClient()
        await _connect_with_fake(client, fake)

        def respond(event: str, payload: Any) -> Any:
            if event == "hello":
                return {
                    "server": "carlabridge",
                    "version": PROTOCOL_VERSION,
                    "bridge_session_id": "br-test-1",
                    "scenario": "s1_fire",
                }
            if event == "agent.command":
                inner = payload["payload"]
                return {
                    "status": "accepted",
                    "cmd_id": inner["id"],
                    "queued_at_sim_time": 0.0,
                }
            return None

        fake.call_responder = respond

        action = UrbanAction(
            kind="dispatch_drone",
            target_id="UAV-01",
            destination=Coordinate(10, 20, 85),
            parameters={"cruise_speed": 8.0},
        )

        async def push_completed_after_ack() -> None:
            await asyncio.sleep(0)  # let send_action submit its RPC
            # the latest 'agent.command' call carries the cmd_id we should ack
            event, payload = fake.calls[-1]
            self.assertEqual(event, "agent.command")
            inner = payload["payload"]
            self.assertEqual(inner["kind"], "UAV_GOTO")
            self.assertEqual(inner["target"], "UAV-01")
            self.assertEqual(
                inner["params"]["waypoint"],
                {"x": -3.0, "y": 20.0, "z": 85.0},
            )
            self.assertEqual(inner["params"]["cruise_speed"], 8.0)
            await fake.deliver(
                "command_status",
                _envelope(
                    "command_status",
                    {
                        "cmd_id": inner["id"],
                        "status": "completed",
                        "kind": "UAV_GOTO",
                        "target": "UAV-01",
                    },
                ),
            )

        result, _ = await asyncio.gather(client.send_action(action), push_completed_after_ack())

        self.assertEqual(result.status, "applied")
        self.assertIn("completed", result.message)

    async def test_dispatch_drone_adds_default_cruise_speed(self) -> None:
        client = CarlaBridgeSandboxClient("http://test")
        fake = FakeAsyncClient()
        await _connect_with_fake(client, fake)

        fake.call_responder = lambda event, payload: (
            {"status": "accepted", "cmd_id": payload["payload"]["id"], "queued_at_sim_time": 0.0}
            if event == "agent.command" else None
        )

        action = UrbanAction(
            kind="dispatch_drone",
            target_id="UAV-01",
            destination=Coordinate(10, 20, 85),
        )

        async def push_completed_after_ack() -> None:
            await asyncio.sleep(0)
            inner = fake.calls[-1][1]["payload"]
            self.assertEqual(inner["kind"], "UAV_GOTO")
            self.assertEqual(inner["params"]["cruise_speed"], 8.0)
            await fake.deliver(
                "command_status",
                _envelope(
                    "command_status",
                    {
                        "cmd_id": inner["id"],
                        "status": "completed",
                        "kind": "UAV_GOTO",
                        "target": "UAV-01",
                    },
                ),
            )

        result, _ = await asyncio.gather(client.send_action(action), push_completed_after_ack())
        self.assertEqual(result.status, "applied")

    async def test_patrol_drone_maps_to_uav_patrol(self) -> None:
        client = CarlaBridgeSandboxClient("http://test")
        fake = FakeAsyncClient()
        await _connect_with_fake(client, fake)

        fake.call_responder = lambda event, payload: (
            {"status": "accepted", "cmd_id": payload["payload"]["id"], "queued_at_sim_time": 0.0}
            if event == "agent.command" else None
        )
        action = UrbanAction(
            kind="patrol_drone",
            target_id="UAV-01",
            parameters={
                "path": [Coordinate(0, 0, 60), Coordinate(10, 0, 60)],
                "loop": True,
            },
        )

        async def push_ongoing() -> None:
            await asyncio.sleep(0)
            inner = fake.calls[-1][1]["payload"]
            self.assertEqual(inner["kind"], "UAV_PATROL")
            self.assertEqual(
                inner["params"]["path"],
                [
                    {"x": 0, "y": 0, "z": 60},
                    {"x": 10, "y": 0, "z": 60},
                ],
            )
            self.assertEqual(inner["params"]["cruise_speed"], 8.0)
            self.assertTrue(inner["params"]["loop"])
            await fake.deliver(
                "command_status",
                _envelope(
                    "command_status",
                    {
                        "cmd_id": inner["id"],
                        "status": "ongoing",
                        "kind": "UAV_PATROL",
                        "target": "UAV-01",
                    },
                ),
            )

        result, _ = await asyncio.gather(client.send_action(action), push_ongoing())
        self.assertEqual(result.status, "applied")
        self.assertIn("ongoing", result.message)

    async def test_return_actions_map_to_rtl_commands(self) -> None:
        client = CarlaBridgeSandboxClient("http://test")
        fake = FakeAsyncClient()
        await _connect_with_fake(client, fake)

        fake.call_responder = lambda event, payload: (
            {"status": "accepted", "cmd_id": payload["payload"]["id"], "queued_at_sim_time": 0.0}
            if event == "agent.command" else None
        )

        async def send_and_complete(action: UrbanAction, expected_kind: str) -> ActionResult:
            async def push_completed() -> None:
                await asyncio.sleep(0)
                inner = fake.calls[-1][1]["payload"]
                self.assertEqual(inner["kind"], expected_kind)
                await fake.deliver(
                    "command_status",
                    _envelope(
                        "command_status",
                        {
                            "cmd_id": inner["id"],
                            "status": "completed",
                            "kind": expected_kind,
                            "target": action.target_id,
                        },
                    ),
                )

            result, _ = await asyncio.gather(client.send_action(action), push_completed())
            return result

        drone_result = await send_and_complete(
            UrbanAction(kind="return_drone", target_id="UAV-01"),
            "UAV_RTL",
        )
        vehicle_result = await send_and_complete(
            UrbanAction(kind="return_vehicle", target_id="UGV-01"),
            "UGV_RTL",
        )

        self.assertEqual(drone_result.status, "applied")
        self.assertEqual(vehicle_result.status, "applied")

    async def test_dispatch_vehicle_upgrades_to_extinguish(self) -> None:
        client = CarlaBridgeSandboxClient("http://test")
        fake = FakeAsyncClient()
        await _connect_with_fake(client, fake)

        # Seed latest_state via state_snapshot
        await fake.deliver(
            "state_snapshot",
            _envelope(
                "state_snapshot",
                {
                    "sim_time": 1.0,
                    "run_id": 1,
                    "bridge_session_id": "br-test-1",
                    "vehicles": [
                        {
                            "id": "UGV-01",
                            "role": "dispatchable",
                            "pose": [9, 20, 0],
                            "speed": 0.0,
                        }
                    ],
                    "uavs": [],
                    "traffic_lights": [],
                    "incidents": [
                        {
                            "id": "fire-001",
                            "kind": "fire",
                            "position": {"x": 10.0, "y": 20.0, "z": 0.0},
                            "severity": "high",
                        }
                    ],
                    "in_flight_commands": [],
                },
            ),
        )

        def respond(event: str, payload: Any) -> Any:
            if event == "agent.command":
                inner = payload["payload"]
                return {
                    "status": "accepted",
                    "cmd_id": inner["id"],
                    "queued_at_sim_time": 0.0,
                }
            return {
                "server": "carlabridge",
                "version": PROTOCOL_VERSION,
                "bridge_session_id": "br-test-1",
                "scenario": "s1_fire",
            }

        fake.call_responder = respond

        action = UrbanAction(
            kind="dispatch_vehicle",
            target_id="UGV-01",
            destination=Coordinate(11, 20, 0),  # ~1m from fire-001
            parameters={"intent": "extinguish"},
        )

        async def push_terminal() -> None:
            await asyncio.sleep(0)
            event, payload = fake.calls[-1]
            inner = payload["payload"]
            self.assertEqual(inner["kind"], "UGV_EXTINGUISH")
            self.assertEqual(inner["params"], {"incident_id": "fire-001"})
            await fake.deliver(
                "command_status",
                _envelope(
                    "command_status",
                    {
                        "cmd_id": inner["id"],
                        "status": "completed",
                        "kind": "UGV_EXTINGUISH",
                        "target": "UGV-01",
                    },
                ),
            )

        result, _ = await asyncio.gather(client.send_action(action), push_terminal())
        self.assertEqual(result.status, "applied")

    async def test_force_extinguish_uses_incident_id_without_local_range_check(self) -> None:
        client = CarlaBridgeSandboxClient("http://test")
        fake = FakeAsyncClient()
        await _connect_with_fake(client, fake)

        await fake.deliver(
            "state_snapshot",
            _envelope(
                "state_snapshot",
                {
                    "vehicles": [
                        {"id": "UGV-01", "role": "dispatchable", "pose": [0, 0, 0]}
                    ],
                    "uavs": [],
                    "incidents": [
                        {
                            "id": "fire-001",
                            "kind": "fire",
                            "position": {"x": 100.0, "y": 0.0, "z": 0.0},
                        }
                    ],
                },
            ),
        )
        fake.call_responder = lambda event, payload: (
            {"status": "accepted", "cmd_id": payload["payload"]["id"], "queued_at_sim_time": 0.0}
            if event == "agent.command" else None
        )
        action = UrbanAction(
            kind="dispatch_vehicle",
            target_id="UGV-01",
            destination=Coordinate(100, 0, 0),
            parameters={
                "incident_id": "fire-001",
                "intent": "extinguish",
                "force_extinguish": True,
            },
        )

        async def push_terminal() -> None:
            await asyncio.sleep(0)
            inner = fake.calls[-1][1]["payload"]
            self.assertEqual(inner["kind"], "UGV_EXTINGUISH")
            self.assertEqual(inner["params"], {"incident_id": "fire-001"})
            await fake.deliver(
                "command_status",
                _envelope(
                    "command_status",
                    {
                        "cmd_id": inner["id"],
                        "status": "completed",
                        "kind": "UGV_EXTINGUISH",
                        "target": "UGV-01",
                    },
                ),
            )

        result, _ = await asyncio.gather(client.send_action(action), push_terminal())
        self.assertEqual(result.status, "applied")

    async def test_dispatch_vehicle_without_intent_stays_ugv_goto(self) -> None:
        client = CarlaBridgeSandboxClient("http://test")
        fake = FakeAsyncClient()
        await _connect_with_fake(client, fake)

        # Fire is right at destination, but intent is not extinguish
        await fake.deliver(
            "state_snapshot",
            _envelope(
                "state_snapshot",
                {
                    "incidents": [
                        {
                            "id": "fire-001",
                            "kind": "fire",
                            "position": {"x": 10.0, "y": 20.0, "z": 0.0},
                        }
                    ]
                },
            ),
        )

        fake.call_responder = lambda event, payload: (
            {"status": "accepted", "cmd_id": payload["payload"]["id"], "queued_at_sim_time": 0.0}
            if event == "agent.command" else None
        )

        action = UrbanAction(
            kind="dispatch_vehicle",
            target_id="UGV-01",
            destination=Coordinate(10, 20, 0),
            # no intent
        )

        async def push_terminal() -> None:
            await asyncio.sleep(0)
            inner = fake.calls[-1][1]["payload"]
            self.assertEqual(inner["kind"], "UGV_GOTO")
            self.assertEqual(inner["params"]["dest"], {"x": 10.0, "y": 20.0, "z": 0.0})
            await fake.deliver(
                "command_status",
                _envelope(
                    "command_status",
                    {"cmd_id": inner["id"], "status": "completed", "kind": "UGV_GOTO",
                     "target": "UGV-01"},
                ),
            )

        result, _ = await asyncio.gather(client.send_action(action), push_terminal())
        self.assertEqual(result.status, "applied")

    async def test_rejection_path(self) -> None:
        client = CarlaBridgeSandboxClient("http://test")
        fake = FakeAsyncClient()
        await _connect_with_fake(client, fake)

        fake.call_responder = lambda event, payload: (
            {
                "status": "rejected",
                "cmd_id": payload["payload"]["id"],
                "reason": "not_in_range",
                "detail": {"distance_m": 18.7, "max_m": 5.0},
            }
            if event == "agent.command" else None
        )

        action = UrbanAction(
            kind="dispatch_vehicle",
            target_id="UGV-01",
            destination=Coordinate(100, 100, 0),
        )
        result = await client.send_action(action)
        self.assertEqual(result.status, "rejected")
        self.assertIn("not_in_range", result.message)

    async def test_failed_command_status_returns_rejected(self) -> None:
        client = CarlaBridgeSandboxClient("http://test")
        fake = FakeAsyncClient()
        await _connect_with_fake(client, fake)

        fake.call_responder = lambda event, payload: (
            {"status": "accepted", "cmd_id": payload["payload"]["id"], "queued_at_sim_time": 0.0}
            if event == "agent.command" else None
        )

        action = UrbanAction(
            kind="dispatch_drone",
            target_id="UAV-01",
            destination=Coordinate(1, 1, 1),
        )

        async def push_failed() -> None:
            await asyncio.sleep(0)
            inner = fake.calls[-1][1]["payload"]
            await fake.deliver(
                "command_status",
                _envelope(
                    "command_status",
                    {
                        "cmd_id": inner["id"],
                        "status": "failed",
                        "kind": "UAV_GOTO",
                        "target": "UAV-01",
                        "reason": "follower_error",
                    },
                ),
            )

        result, _ = await asyncio.gather(client.send_action(action), push_failed())
        self.assertEqual(result.status, "rejected")
        self.assertIn("follower_error", result.message)

    async def test_scenario_reset_cancels_pending(self) -> None:
        client = CarlaBridgeSandboxClient("http://test")
        fake = FakeAsyncClient()
        await _connect_with_fake(client, fake)

        fake.call_responder = lambda event, payload: (
            {"status": "accepted", "cmd_id": payload["payload"]["id"], "queued_at_sim_time": 0.0}
            if event == "agent.command" else None
        )

        action = UrbanAction(
            kind="dispatch_drone",
            target_id="UAV-01",
            destination=Coordinate(1, 1, 1),
        )

        async def push_reset() -> None:
            await asyncio.sleep(0)
            await fake.deliver(
                "scenario_event",
                _envelope("scenario_event", {"event": "reset", "run_id": 2, "trigger": "http"}),
            )

        result, _ = await asyncio.gather(client.send_action(action), push_reset())
        self.assertEqual(result.status, "rejected")
        self.assertIn("scenario_reset", result.message)
        self.assertEqual(client.run_id, 2)

    async def test_traffic_light_is_noop_rejected_without_rpc(self) -> None:
        client = CarlaBridgeSandboxClient("http://test")
        fake = FakeAsyncClient()
        await _connect_with_fake(client, fake)

        action = UrbanAction(
            kind="control_traffic_light",
            target_id="signal-01",
            parameters={"mode": "emergency_preemption"},
        )
        result = await client.send_action(action)
        self.assertEqual(result.status, "rejected")
        self.assertIn("protocol v1.0 does not support", result.message)
        # No agent.command RPC went out (only the hello call)
        self.assertEqual([e for e, _ in fake.calls if e == "agent.command"], [])
        # But event_log warning was emitted
        self.assertTrue(any(e == "event_log" for e, _ in fake.emitted))

    async def test_mark_incident_is_noop_rejected(self) -> None:
        client = CarlaBridgeSandboxClient("http://test")
        fake = FakeAsyncClient()
        await _connect_with_fake(client, fake)

        action = UrbanAction(
            kind="mark_incident",
            target_id="fire-001",
            parameters={"status": "responding"},
        )
        result = await client.send_action(action)
        self.assertEqual(result.status, "rejected")
        self.assertEqual([e for e, _ in fake.calls if e == "agent.command"], [])

    async def test_unknown_target_rejected_before_rpc(self) -> None:
        client = CarlaBridgeSandboxClient("http://test")
        fake = FakeAsyncClient()
        await _connect_with_fake(client, fake)

        # Seed a snapshot that only knows UGV-01; targeting a hallucinated id
        # should be rejected locally without going to Bridge.
        await fake.deliver(
            "state_snapshot",
            _envelope(
                "state_snapshot",
                {
                    "vehicles": [
                        {"id": "UGV-01", "role": "dispatchable", "pose": [0, 0, 0]}
                    ],
                    "uavs": [],
                },
            ),
        )

        action = UrbanAction(
            kind="dispatch_drone",
            target_id="drone-99",  # not in Bridge fleet
            destination=Coordinate(1, 1, 1),
        )
        result = await client.send_action(action)
        self.assertEqual(result.status, "rejected")
        self.assertIn("unknown_target", result.message)
        self.assertEqual([e for e, _ in fake.calls if e == "agent.command"], [])

    async def test_in_flight_target_rejected_before_rpc(self) -> None:
        client = CarlaBridgeSandboxClient("http://test")
        fake = FakeAsyncClient()
        await _connect_with_fake(client, fake)

        await fake.deliver(
            "state_snapshot",
            _envelope(
                "state_snapshot",
                {
                    "vehicles": [
                        {"id": "UGV-01", "role": "dispatchable", "pose": [0, 0, 0]}
                    ],
                    "uavs": [],
                    "in_flight_commands": [
                        {"cmd_id": "cmd-existing", "target": "UGV-01", "status": "ongoing"}
                    ],
                },
            ),
        )

        action = UrbanAction(
            kind="dispatch_vehicle",
            target_id="UGV-01",
            destination=Coordinate(1, 1, 0),
        )
        result = await client.send_action(action)
        self.assertEqual(result.status, "rejected")
        self.assertIn("target_in_flight", result.message)
        self.assertEqual([e for e, _ in fake.calls if e == "agent.command"], [])

    async def test_extinguish_uses_ugv_position_not_destination(self) -> None:
        """UGV far from fire should fall back to UGV_GOTO even with extinguish intent."""

        client = CarlaBridgeSandboxClient("http://test")
        fake = FakeAsyncClient()
        await _connect_with_fake(client, fake)

        # UGV-01 is 100m away from the fire; destination is at the fire.
        await fake.deliver(
            "state_snapshot",
            _envelope(
                "state_snapshot",
                {
                    "vehicles": [
                        {"id": "UGV-01", "role": "dispatchable", "pose": [0, 0, 0]}
                    ],
                    "uavs": [],
                    "incidents": [
                        {
                            "id": "fire-001",
                            "kind": "fire",
                            "position": {"x": 100.0, "y": 0.0, "z": 0.0},
                        }
                    ],
                },
            ),
        )

        fake.call_responder = lambda event, payload: (
            {"status": "accepted", "cmd_id": payload["payload"]["id"], "queued_at_sim_time": 0.0}
            if event == "agent.command" else None
        )

        action = UrbanAction(
            kind="dispatch_vehicle",
            target_id="UGV-01",
            destination=Coordinate(100, 0, 0),
            parameters={"intent": "extinguish"},
        )

        async def push_terminal() -> None:
            await asyncio.sleep(0)
            inner = fake.calls[-1][1]["payload"]
            # UGV is 100m from the fire, not within 5m, so adapter must NOT upgrade.
            self.assertEqual(inner["kind"], "UGV_GOTO")
            self.assertEqual(inner["params"]["target_speed"], 25.0)
            await fake.deliver(
                "command_status",
                _envelope(
                    "command_status",
                    {
                        "cmd_id": inner["id"],
                        "status": "completed",
                        "kind": "UGV_GOTO",
                        "target": "UGV-01",
                    },
                ),
            )

        result, _ = await asyncio.gather(client.send_action(action), push_terminal())
        self.assertEqual(result.status, "applied")

    async def test_hello_handshake_records_session(self) -> None:
        client = CarlaBridgeSandboxClient("http://test")
        fake = FakeAsyncClient()
        await _connect_with_fake(
            client,
            fake,
            hello_response={
                "server": "carlabridge",
                "version": PROTOCOL_VERSION,
                "bridge_session_id": "br-xyz",
                "scenario": "s1_fire",
            },
        )
        self.assertEqual(client.bridge_session_id, "br-xyz")


if __name__ == "__main__":
    unittest.main()
