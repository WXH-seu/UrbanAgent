"""Socket.IO adapter for CarlaBridge — Bridge × Agent Protocol v1.0.

Connects UrbanAgent (decision layer) to CarlaBridge (CARLA middleware) on the
``/agent`` Socket.IO namespace. Implements protocol v1.0 (see
``bridge-agent-protocol-v1.md``):

* handshake via ``hello`` RPC with version negotiation
* inbound: ``state_snapshot`` / ``command_status`` / ``scenario_event`` /
  ``event_log``
* outbound: ``agent.command`` RPC (8 command kinds: UAV_PATROL / UAV_GOTO /
  UAV_RTL / UAV_HOLD / UGV_GOTO / UGV_RTL / UGV_EXTINGUISH / UGV_STOP) plus
  optional ``event_log``

The class implements :class:`urbanagent.sandbox.SandboxClient` so existing
single-agent and multi-agent pipelines reuse it unchanged. ``send_action``
blocks until a terminal ``command_status`` (or ``ongoing``) arrives so callers
see Bridge-side completion, not just RPC acceptance.
"""
from __future__ import annotations

import asyncio
import logging
import math
import time
import uuid
from typing import Any

from urbanagent.errors import SandboxWireError
from urbanagent.fire_goto import apply_fire_goto_offset_to_action
from urbanagent.resource_policy import bridge_status_from_role_speed
from urbanagent.sandbox import SandboxClient
from urbanagent.types import (
    ActionResult,
    CityState,
    Coordinate,
    Incident,
    TrafficSignal,
    UrbanAction,
    UrbanResource,
)


PROTOCOL_VERSION = "1.0"
EXTINGUISH_RADIUS_M = 5.0
DEFAULT_UAV_CRUISE_SPEED = 8.0
DEFAULT_UGV_TARGET_SPEED = 25.0
_LOG = logging.getLogger(__name__)


class CarlaBridgeSandboxClient(SandboxClient):
    """CarlaBridge Socket.IO v4 ``/agent`` client (protocol v1.0)."""

    def __init__(
        self,
        url: str,
        *,
        namespace: str = "/agent",
        agent_id: str = "urban_agent_v1",
        connect_timeout: float = 30.0,
        ack_timeout: float = 2.0,
        state_timeout: float = 30.0,
        command_timeout: float = 60.0,
        extinguish_radius_m: float = EXTINGUISH_RADIUS_M,
        default_incidents: list[Incident] | None = None,
    ) -> None:
        self.url = url
        self.namespace = namespace
        self.agent_id = agent_id
        self.connect_timeout = connect_timeout
        self.ack_timeout = ack_timeout
        self.state_timeout = state_timeout
        self.command_timeout = command_timeout
        self.extinguish_radius_m = float(extinguish_radius_m)
        self.default_incidents = list(default_incidents or [])

        self._sio: Any | None = None
        self._connected = asyncio.Event()
        self._state_event = asyncio.Event()
        self._latest_state: CityState | None = None
        self._latest_frame: int | None = None
        self._latest_sim_time: float | None = None
        self._latest_in_flight: list[dict[str, Any]] = []
        self._known_entity_ids: set[str] = set()

        self._bridge_session_id: str | None = None
        self._run_id: int | None = None
        self._scenario: str | None = None

        self._in_flight_futures: dict[str, asyncio.Future[ActionResult]] = {}
        self._in_flight_actions: dict[str, UrbanAction] = {}
        self._events: list[dict[str, Any]] = []

    @property
    def event_logs(self) -> list[dict[str, Any]]:
        return list(self._events)

    @property
    def in_flight_commands_view(self) -> list[dict[str, Any]]:
        """Latest ``state_snapshot.in_flight_commands`` for debugging."""

        return list(self._latest_in_flight)

    @property
    def known_entity_ids(self) -> set[str]:
        """Entity IDs (UGV-* / UAV-*) seen in the most recent ``state_snapshot``."""

        return set(self._known_entity_ids)

    @property
    def bridge_session_id(self) -> str | None:
        return self._bridge_session_id

    @property
    def run_id(self) -> int | None:
        return self._run_id

    async def connect(self) -> None:
        if self._sio is not None and self._sio.connected:
            return
        try:
            import socketio
        except ImportError as exc:  # pragma: no cover - depends on environment
            raise RuntimeError(
                "CarlaBridgeSandboxClient requires python-socketio[asyncio_client]. "
                "Run `pip install -e .` after updating dependencies.",
            ) from exc

        self._connected.clear()
        self._sio = socketio.AsyncClient(reconnection=True)
        self._register_handlers()
        await self._sio.connect(
            self.url,
            namespaces=[self.namespace],
            wait_timeout=self.connect_timeout,
        )
        await asyncio.wait_for(self._connected.wait(), timeout=self.connect_timeout)
        await self._do_hello()

    async def close(self) -> None:
        if self._sio is not None and self._sio.connected:
            await self._sio.disconnect()
        self._sio = None
        self._fail_pending("CarlaBridge connection closed")

    async def __aenter__(self) -> CarlaBridgeSandboxClient:
        await self.connect()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def get_state(self) -> CityState:
        await self.connect()
        if self._latest_state is not None:
            return self._latest_state
        await asyncio.wait_for(self._state_event.wait(), timeout=self.state_timeout)
        if self._latest_state is None:
            raise SandboxWireError("CarlaBridge did not provide a state snapshot")
        return self._latest_state

    async def send_action(self, action: UrbanAction) -> ActionResult:
        await self.connect()

        if action.kind in ("control_traffic_light", "mark_incident"):
            message = f"protocol v1.0 does not support {action.kind}"
            _LOG.warning(message)
            await self._emit_event_log(severity="warn", message=message)
            return ActionResult(status="rejected", action=action, message=message)

        cmd_payload = self._action_to_agent_command(action)
        if cmd_payload is None:
            return ActionResult(
                status="rejected",
                action=action,
                message=f"action {action.kind} could not be mapped to protocol v1.0 command",
            )

        if self._known_entity_ids and cmd_payload["target"] not in self._known_entity_ids:
            message = (
                f"unknown_target: {cmd_payload['target']!r} not in latest Bridge fleet "
                f"{sorted(self._known_entity_ids)}"
            )
            _LOG.warning(message)
            await self._emit_event_log(severity="warn", message=message)
            return ActionResult(status="rejected", action=action, message=message)

        conflict = self._in_flight_conflict(cmd_payload)
        if conflict is not None:
            message = (
                f"target_in_flight: {cmd_payload['target']!r} already has command "
                f"{conflict.get('cmd_id') or conflict.get('id') or '<unknown>'}"
            )
            _LOG.warning(message)
            await self._emit_event_log(severity="warn", message=message)
            return ActionResult(status="rejected", action=action, message=message)

        cmd_id = cmd_payload["id"]
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[ActionResult] = loop.create_future()
        self._in_flight_futures[cmd_id] = fut
        self._in_flight_actions[cmd_id] = action

        envelope = self._wrap("agent.command", cmd_payload)
        try:
            try:
                ack = await self._sio.call(
                    "agent.command",
                    envelope,
                    namespace=self.namespace,
                    timeout=self.ack_timeout,
                )
            except asyncio.TimeoutError as exc:
                raise SandboxWireError(
                    f"agent.command RPC timeout (cmd_id={cmd_id})",
                ) from exc

            if not isinstance(ack, dict) or ack.get("status") != "accepted":
                reason = (
                    str(ack.get("reason", "rejected"))
                    if isinstance(ack, dict) else "rejected"
                )
                detail = ack.get("detail") if isinstance(ack, dict) else None
                message = f"rejected: {reason}"
                if detail:
                    message = f"{message} {detail}"
                return ActionResult(status="rejected", action=action, message=message)

            try:
                return await asyncio.wait_for(fut, timeout=self.command_timeout)
            except asyncio.TimeoutError:
                message = (
                    f"command_status timeout after {self.command_timeout:.1f}s "
                    f"(cmd_id={cmd_id}, kind={cmd_payload['kind']}, "
                    f"target={cmd_payload['target']})"
                )
                _LOG.warning(message)
                await self._emit_event_log(
                    severity="warn", message=message, cmd_id=cmd_id
                )
                return ActionResult(status="rejected", action=action, message=message)
        finally:
            self._in_flight_futures.pop(cmd_id, None)
            self._in_flight_actions.pop(cmd_id, None)

    async def send_event_log(
        self,
        message: str,
        *,
        severity: str = "info",
        cmd_id: str | None = None,
    ) -> None:
        await self.connect()
        await self._emit_event_log(severity=severity, message=message, cmd_id=cmd_id)

    def _in_flight_conflict(self, cmd_payload: dict[str, Any]) -> dict[str, Any] | None:
        target_id = str(cmd_payload.get("target", "") or "")
        new_kind = str(cmd_payload.get("kind", "") or "")
        terminal = {"completed", "failed", "cancelled"}
        for item in self._latest_in_flight:
            target = str(item.get("target", "") or "")
            if target != target_id:
                continue
            status = str(item.get("status", "") or "").lower()
            if status not in terminal:
                existing_kind = str(item.get("kind", "") or "")
                if existing_kind == "UAV_PATROL" and new_kind in {
                    "UAV_GOTO",
                    "UAV_RTL",
                    "UAV_HOLD",
                }:
                    continue
                return item
        return None

    def _register_handlers(self) -> None:
        assert self._sio is not None

        @self._sio.event(namespace=self.namespace)
        async def connect() -> None:  # type: ignore[no-redef]
            self._connected.set()

        @self._sio.event(namespace=self.namespace)
        async def disconnect() -> None:  # type: ignore[no-redef]
            self._connected.clear()

        @self._sio.on("state_snapshot", namespace=self.namespace)
        async def on_state_snapshot(data: Any) -> None:
            self._handle_state_snapshot(data)

        @self._sio.on("command_status", namespace=self.namespace)
        async def on_command_status(data: Any) -> None:
            self._handle_command_status(data)

        @self._sio.on("scenario_event", namespace=self.namespace)
        async def on_scenario_event(data: Any) -> None:
            self._handle_scenario_event(data)

        @self._sio.on("event_log", namespace=self.namespace)
        async def on_event_log(data: Any) -> None:
            payload = _unwrap_payload(data)
            self._events.append(payload)

    async def _do_hello(self) -> None:
        assert self._sio is not None
        try:
            resp = await self._sio.call(
                "hello",
                {"agent_id": self.agent_id, "version": PROTOCOL_VERSION},
                namespace=self.namespace,
                timeout=2.0,
            )
        except asyncio.TimeoutError as exc:
            raise SandboxWireError("hello RPC timed out") from exc
        if not isinstance(resp, dict):
            raise SandboxWireError(f"unexpected hello response: {resp!r}")
        self._bridge_session_id = str(resp.get("bridge_session_id", "") or "") or None
        self._scenario = str(resp.get("scenario", "") or "") or None
        server_version = str(resp.get("version", "") or "")
        if server_version and _major(server_version) != _major(PROTOCOL_VERSION):
            _LOG.warning(
                "protocol major version mismatch: bridge=%s agent=%s",
                server_version,
                PROTOCOL_VERSION,
            )

    def _handle_state_snapshot(self, data: Any) -> None:
        env = _as_envelope(data)
        payload = env.get("payload") or {}
        self._latest_frame = _maybe_int(env.get("frame"))
        self._latest_sim_time = _maybe_float(env.get("sim_time"))

        observed_session = str(payload.get("bridge_session_id", "") or "") or None
        observed_run = _maybe_int(payload.get("run_id"))

        if (
            observed_session
            and self._bridge_session_id
            and observed_session != self._bridge_session_id
        ):
            _LOG.warning(
                "bridge_session_id changed (%s -> %s); cancelling in-flight commands",
                self._bridge_session_id,
                observed_session,
            )
            self._fail_pending("bridge_restart")
            self._bridge_session_id = observed_session

        if (
            observed_run is not None
            and self._run_id is not None
            and observed_run != self._run_id
        ):
            _LOG.info(
                "run_id changed (%s -> %s) via state_snapshot; treating as reset",
                self._run_id,
                observed_run,
            )
            self._fail_pending("scenario_reset")

        if observed_session:
            self._bridge_session_id = observed_session
        if observed_run is not None:
            self._run_id = observed_run

        self._latest_in_flight = [
            dict(item) for item in (payload.get("in_flight_commands") or [])
            if isinstance(item, dict)
        ]

        ids: set[str] = set()
        for raw in payload.get("vehicles") or []:
            if isinstance(raw, dict) and raw.get("id"):
                ids.add(str(raw["id"]))
        for raw in payload.get("uavs") or []:
            if isinstance(raw, dict) and raw.get("id"):
                ids.add(str(raw["id"]))
        self._known_entity_ids = ids

        self._latest_state = carla_snapshot_to_city_state(
            dict(payload),
            timestamp=str(env.get("timestamp", time.time())),
            default_incidents=self.default_incidents,
        )
        self._state_event.set()

    def _handle_command_status(self, data: Any) -> None:
        env = _as_envelope(data)
        payload = env.get("payload") or {}
        cmd_id = str(payload.get("cmd_id", "") or "")
        if not cmd_id:
            return
        fut = self._in_flight_futures.get(cmd_id)
        if fut is None or fut.done():
            return
        action = self._in_flight_actions.get(cmd_id) or _placeholder_action(payload)
        status = str(payload.get("status", "") or "").lower()
        reason = payload.get("reason")
        detail = payload.get("detail")

        if status == "completed":
            result = ActionResult(status="applied", action=action, message="completed")
        elif status == "ongoing":
            result = ActionResult(status="applied", action=action, message="ongoing")
        elif status == "failed":
            message = f"failed: {reason or 'internal_error'}"
            if detail:
                message = f"{message} {detail}"
            result = ActionResult(status="rejected", action=action, message=message)
        elif status == "cancelled":
            message = f"cancelled: {reason or 'unknown'}"
            if detail:
                message = f"{message} {detail}"
            result = ActionResult(status="rejected", action=action, message=message)
        elif status == "accepted":
            # accepted is an intermediate state on the RPC ack path; some
            # bridges may also emit it as an event. Ignore so we keep waiting.
            return
        else:
            _LOG.warning("unknown command_status %r for cmd_id=%s", status, cmd_id)
            return

        self._latest_in_flight = [
            item
            for item in self._latest_in_flight
            if str(item.get("cmd_id") or item.get("id") or "") != cmd_id
        ]
        fut.set_result(result)

    def _handle_scenario_event(self, data: Any) -> None:
        env = _as_envelope(data)
        payload = env.get("payload") or {}
        event = str(payload.get("event", "") or "")
        run_id = _maybe_int(payload.get("run_id"))
        if event == "reset":
            _LOG.info("scenario_event:reset received (run_id=%s)", run_id)
            self._fail_pending("scenario_reset")
            if run_id is not None:
                self._run_id = run_id
        else:
            _LOG.debug("scenario_event %r ignored (not in v1.0)", event)

    def _fail_pending(self, message: str) -> None:
        if not self._in_flight_futures:
            return
        for cmd_id, fut in list(self._in_flight_futures.items()):
            if fut.done():
                continue
            action = self._in_flight_actions.get(cmd_id) or _placeholder_action(
                {"cmd_id": cmd_id}
            )
            fut.set_result(
                ActionResult(status="rejected", action=action, message=message)
            )
        self._in_flight_futures.clear()
        self._in_flight_actions.clear()

    def _wrap(self, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "version": PROTOCOL_VERSION,
            "msg_id": str(uuid.uuid4()),
            "type": event_type,
            "timestamp": time.time(),
            "frame": self._latest_frame,
            "sim_time": self._latest_sim_time,
            "sender": "agent",
            "payload": payload,
        }

    async def _emit_event_log(
        self,
        *,
        severity: str,
        message: str,
        cmd_id: str | None = None,
    ) -> None:
        if self._sio is None or not self._sio.connected:
            return
        payload: dict[str, Any] = {
            "severity": severity,
            "source": "AGENT",
            "message": message,
        }
        if cmd_id:
            payload["cmd_id"] = cmd_id
        await self._sio.emit(
            "event_log", self._wrap("event_log", payload), namespace=self.namespace
        )

    def _action_to_agent_command(self, action: UrbanAction) -> dict[str, Any] | None:
        cmd_id = f"urbanagent-{uuid.uuid4().hex[:12]}"
        priority = str(action.parameters.get("priority", "normal"))

        if action.kind == "patrol_drone":
            path = _patrol_path_data(action)
            if not path:
                return None
            return {
                "id": cmd_id,
                "kind": "UAV_PATROL",
                "target": action.target_id,
                "priority": priority,
                "params": {
                    "path": path,
                    "cruise_speed": float(
                        action.parameters.get("cruise_speed", DEFAULT_UAV_CRUISE_SPEED)
                    ),
                    "loop": bool(action.parameters.get("loop", True)),
                },
            }

        if action.kind == "return_drone":
            params: dict[str, Any] = {}
            if "cruise_speed" in action.parameters:
                params["cruise_speed"] = float(action.parameters["cruise_speed"])
            return {
                "id": cmd_id,
                "kind": "UAV_RTL",
                "target": action.target_id,
                "priority": priority,
                "params": params,
            }

        if action.kind == "hold_drone":
            return {
                "id": cmd_id,
                "kind": "UAV_HOLD",
                "target": action.target_id,
                "priority": priority,
                "params": {},
            }

        if action.kind == "return_vehicle":
            params = {}
            if "target_speed" in action.parameters:
                params["target_speed"] = float(action.parameters["target_speed"])
            return {
                "id": cmd_id,
                "kind": "UGV_RTL",
                "target": action.target_id,
                "priority": priority,
                "params": params,
            }

        if action.kind == "stop_vehicle":
            return {
                "id": cmd_id,
                "kind": "UGV_STOP",
                "target": action.target_id,
                "priority": priority,
                "params": {},
            }

        if action.kind == "dispatch_drone":
            action = apply_fire_goto_offset_to_action(action)
            if action.destination is None:
                return None
            params: dict[str, Any] = {
                "waypoint": _coord_data(action.destination),
                "cruise_speed": float(
                    action.parameters.get("cruise_speed", DEFAULT_UAV_CRUISE_SPEED)
                ),
            }
            return {
                "id": cmd_id,
                "kind": "UAV_GOTO",
                "target": action.target_id,
                "priority": priority,
                "params": params,
            }

        if action.kind == "dispatch_vehicle":
            if action.destination is None:
                return None
            incident_id = self._match_fire_incident(action)
            if incident_id is not None:
                return {
                    "id": cmd_id,
                    "kind": "UGV_EXTINGUISH",
                    "target": action.target_id,
                    "priority": priority,
                    "params": {"incident_id": incident_id},
                }
            params = {
                "dest": _coord_data(action.destination),
                "target_speed": float(
                    action.parameters.get("target_speed", DEFAULT_UGV_TARGET_SPEED)
                ),
            }

            # Optional extension for local CarAgent/Transbot integration.
            # Keep the original v1.0 `dest` field for CarlaBridge compatibility,
            # and additionally pass through precomputed routes when UrbanAgent
            # has them.
            route_nodes = action.parameters.get("route_nodes") or action.parameters.get("path")
            if route_nodes:
                params["route_nodes"] = [str(node) for node in route_nodes]

            route_waypoints = action.parameters.get("route_waypoints")
            if route_waypoints:
                params["route_waypoints"] = _route_waypoint_data(route_waypoints)

            route_source = action.parameters.get("route_source")
            if route_source:
                params["route_source"] = str(route_source)

            return {
                "id": cmd_id,
                "kind": "UGV_GOTO",
                "target": action.target_id,
                "priority": priority,
                "params": params,
            }

        return None

    def _match_fire_incident(self, action: UrbanAction) -> str | None:
        intent = str(action.parameters.get("intent", "") or "").lower()
        capability = str(action.parameters.get("capability", "") or "").lower()
        incident_id = str(action.parameters.get("incident_id", "") or "").strip()
        if action.parameters.get("force_extinguish") and incident_id:
            return incident_id
        reason = (action.reason or "").lower()
        wants_extinguish = (
            intent == "extinguish"
            or capability == "fire_suppression"
            or "extinguish" in reason
            or "灭火" in (action.reason or "")
        )
        if not wants_extinguish:
            return None
        state = self._latest_state
        if state is None:
            return None
        ugv = next(
            (r for r in state.resources if r.id == action.target_id),
            None,
        )
        if ugv is None:
            return None
        best_id: str | None = None
        best_dist = self.extinguish_radius_m
        for inc in state.incidents:
            if str(inc.kind).lower() != "fire":
                continue
            dist = _distance(inc.position, ugv.position)
            if dist <= best_dist:
                best_dist = dist
                best_id = inc.id
        return best_id


def carla_snapshot_to_city_state(
    payload: dict[str, Any],
    *,
    timestamp: str = "",
    default_incidents: list[Incident] | None = None,
) -> CityState:
    """Translate a protocol v1.0 ``state_snapshot.payload`` into ``CityState``."""

    resources: list[UrbanResource] = []
    for raw in payload.get("vehicles") or []:
        if not isinstance(raw, dict):
            continue
        if str(raw.get("role", "")).lower() == "civilian":
            continue
        resources.append(_vehicle_resource(raw))
    for raw in payload.get("uavs") or []:
        if isinstance(raw, dict):
            resources.append(_uav_resource(raw))
    signals = [
        _traffic_signal(raw)
        for raw in (payload.get("traffic_lights") or [])
        if isinstance(raw, dict)
    ]
    incidents = [
        _incident(raw)
        for raw in (payload.get("incidents") or [])
        if isinstance(raw, dict)
    ]
    if not incidents and default_incidents:
        incidents = list(default_incidents)
    return CityState(
        timestamp=timestamp,
        incidents=incidents,
        resources=resources,
        traffic_signals=signals,
    )


def _vehicle_resource(raw: dict[str, Any]) -> UrbanResource:
    rid = str(raw.get("id", ""))
    role = str(raw.get("role", "")).lower()
    if role == "dispatchable":
        kind = "unmanned_vehicle"
        caps = ["logistics_support", "perimeter_support", "fire_suppression"]
    else:
        kind = "ground_vehicle"
        caps = ["ground_mobility"]
    speed = _maybe_float(raw.get("speed")) or 0.0
    status = bridge_status_from_role_speed(role, speed)
    return UrbanResource(
        id=rid,
        kind=kind,  # type: ignore[arg-type]
        position=_coord_from_pose(raw.get("pose")),
        status=status,  # type: ignore[arg-type]
        speed=float(speed),
        battery_remaining=_maybe_float(raw.get("battery")),
        capabilities=caps,
        label=rid,
    )


def _uav_resource(raw: dict[str, Any]) -> UrbanResource:
    rid = str(raw.get("id", ""))
    role = str(raw.get("role", "")).lower()
    speed = _maybe_float(raw.get("speed")) or 0.0
    status = bridge_status_from_role_speed(role, speed, patrol_available=True)
    return UrbanResource(
        id=rid,
        kind="drone",
        position=_coord_from_pose(raw.get("pose")),
        status=status,  # type: ignore[arg-type]
        speed=float(speed),
        battery_remaining=_maybe_float(raw.get("battery")),
        capabilities=["aerial_recon", "thermal_imaging"],
        label=rid,
    )


def _traffic_signal(raw: dict[str, Any]) -> TrafficSignal:
    return TrafficSignal(
        id=str(raw.get("id", "")),
        position=_coord_from_pose(raw.get("pose")),
        mode=str(raw.get("phase", "unknown")),
        status="available",
    )


def _incident(raw: dict[str, Any]) -> Incident:
    return Incident(
        id=str(raw.get("id", "")),
        kind=str(raw.get("kind", "fire")),  # type: ignore[arg-type]
        position=_coord_from_position(raw.get("position")),
        severity=str(raw.get("severity", "high")),  # type: ignore[arg-type]
        status="open",
        description=str(raw.get("description", "")),
    )


def _coord_from_pose(value: Any) -> Coordinate:
    """Decode ``pose: [x, y, z]`` (protocol §3.2 array form)."""

    if isinstance(value, (list, tuple)):
        x = float(value[0]) if len(value) > 0 else 0.0
        y = float(value[1]) if len(value) > 1 else 0.0
        z = float(value[2]) if len(value) > 2 else 0.0
        return Coordinate(x=x, y=y, z=z)
    if isinstance(value, dict):
        return _coord_from_position(value)
    return Coordinate(x=0.0, y=0.0, z=0.0)


def _coord_from_position(value: Any) -> Coordinate:
    """Decode ``position: {x, y, z}`` (protocol §3.2 object form)."""

    d = value if isinstance(value, dict) else {}
    return Coordinate(
        x=float(d.get("x", 0.0) or 0.0),
        y=float(d.get("y", 0.0) or 0.0),
        z=float(d.get("z", 0.0) or 0.0),
    )


def _coord_data(coord: Coordinate) -> dict[str, float]:
    return {"x": coord.x, "y": coord.y, "z": coord.z}


def _route_waypoint_data(value: Any) -> list[dict[str, float]]:
    """Normalize a CARLA waypoint route into protocol-safe coordinate dicts.

    Accepts either:
    - list[Coordinate]
    - list[{"x": ..., "y": ..., "z": ...}]

    Invalid shapes return an empty list so malformed optional route data does
    not break the base UGV_GOTO command, which still carries `dest`.
    """

    if not isinstance(value, list):
        return []

    waypoints: list[dict[str, float]] = []
    for item in value:
        if isinstance(item, Coordinate):
            waypoints.append(_coord_data(item))
        elif isinstance(item, dict):
            if not {"x", "y", "z"}.issubset(item):
                return []
            waypoints.append(
                {
                    "x": float(item["x"]),
                    "y": float(item["y"]),
                    "z": float(item["z"]),
                }
            )
        else:
            return []

    return waypoints


def _patrol_path_data(action: UrbanAction) -> list[dict[str, float]]:
    raw_path = action.parameters.get("path")
    if raw_path is None and action.destination is not None:
        raw_path = [action.destination]
    if not isinstance(raw_path, list):
        return []
    path: list[dict[str, float]] = []
    for item in raw_path:
        if isinstance(item, Coordinate):
            path.append(_coord_data(item))
        elif isinstance(item, dict):
            if not {"x", "y", "z"}.issubset(item):
                return []
            path.append(
                {
                    "x": float(item["x"]),
                    "y": float(item["y"]),
                    "z": float(item["z"]),
                }
            )
        else:
            return []
    return path


def _distance(left: Coordinate, right: Coordinate) -> float:
    return math.sqrt(
        (left.x - right.x) ** 2
        + (left.y - right.y) ** 2
        + (left.z - right.z) ** 2
    )


def _maybe_int(value: Any) -> int | None:
    try:
        return None if value is None else int(value)
    except (TypeError, ValueError):
        return None


def _maybe_float(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _as_envelope(data: Any) -> dict[str, Any]:
    if isinstance(data, dict) and "payload" in data:
        return data
    return {
        "version": PROTOCOL_VERSION,
        "msg_id": "",
        "type": "",
        "timestamp": time.time(),
        "frame": None,
        "sim_time": None,
        "sender": "bridge",
        "payload": data if isinstance(data, dict) else {},
    }


def _unwrap_payload(data: Any) -> dict[str, Any]:
    env = _as_envelope(data)
    payload = env.get("payload")
    return dict(payload) if isinstance(payload, dict) else {}


def _major(version: str) -> str:
    return version.split(".", 1)[0]


def _placeholder_action(payload: dict[str, Any]) -> UrbanAction:
    return UrbanAction(
        kind="dispatch_vehicle",
        target_id=str(payload.get("target", "") or ""),
        parameters={"cmd_id": str(payload.get("cmd_id", "") or "")},
        reason="command_status received without matching local action",
    )
