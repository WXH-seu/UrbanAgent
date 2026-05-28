"""UrbanAgent multi-agent MVP pipeline (meta + subagents + batch execution)."""
from __future__ import annotations

import asyncio
import copy
import math
import uuid
from pathlib import Path
from typing import cast

from urbanagent.dispatch import DispatchPolicy
from urbanagent.llm.llm import LLM
from urbanagent.multiagent.batch_runner import (
    execute_batch_ordered,
    execute_batch_parallel,
)
from urbanagent.multiagent.gating import run_gate
from urbanagent.multiagent.orchestrator import (
    build_committed_batch,
    deterministic_final_report,
    llm_cognition,
    llm_decompose,
    llm_final_report,
    rule_cognition,
    rule_decompose,
)
from urbanagent.multiagent.registry import SubAgentRegistry, build_default_mvp_registry
from urbanagent.multiagent.schemas import (
    BatchOutcome,
    PatrolFireResponseResult,
    SubAgentRole,
    UrbanMultiAgentResult,
)
from urbanagent.multiagent.toolkit import SubAgentToolkit
from urbanagent.multiagent.world_cache import WorldStateCache
from urbanagent.fire_goto import offset_fire_hold_goto_position
from urbanagent.sandbox import MockSandboxClient, SandboxClient
from urbanagent.types import CityState, Coordinate, Incident, UrbanAction, UrbanResource

# Demo / CARLA default fire-watch area (ground xy); patrol flies at patrol_altitude.
DEFAULT_FIRE_WATCH_XY = (25.3, 24.4)
# Match CarlaBridge s1_fire ``UAV_ARRIVAL_EPS_M``.
PATROL_ARRIVAL_EPS_M = 1
# Only this UAV must reach hold_anchor before hold dispatch (s1_fire primary watch).
PATROL_ARRIVAL_WATCH_DRONE_ID = "UAV-01"
class UrbanMultiAgentSystem:
    """Meta + four sub-agents (MVP one each); ordered batch to sandbox + post-batch poll."""

    ROLE_ORDER: tuple[SubAgentRole, ...] = (
        "traffic_signal",
        "police_car",
        "unmanned_vehicle",
        "drone",
    )

    def __init__(
        self,
        sandbox: SandboxClient | None = None,
        *,
        dispatch_policy: DispatchPolicy | None = None,
        registry: SubAgentRegistry | None = None,
        toolkit: SubAgentToolkit | None = None,
        dotenv_path: str | Path | None = None,
        use_llm: bool = True,
        use_llm_batch_rerank: bool = True,
    ) -> None:
        self.sandbox = sandbox or MockSandboxClient()
        self.dispatch_policy = dispatch_policy or DispatchPolicy()
        self.toolkit = toolkit or SubAgentToolkit()
        self.registry = registry or build_default_mvp_registry(self.toolkit)
        self.dotenv_path = str(dotenv_path) if dotenv_path is not None else None
        self.use_llm = use_llm
        self.use_llm_batch_rerank = use_llm_batch_rerank
        self.llm: LLM | None = None
        self.world_cache = WorldStateCache()

    def _ensure_llm(self) -> LLM | None:
        if not self.use_llm:
            return None
        if self.llm is None:
            self.llm = LLM.from_env(dotenv_path=self.dotenv_path)
        return self.llm

    async def run(self, query: str) -> UrbanMultiAgentResult:
        initial = copy.deepcopy(await self.sandbox.get_state())
        self.world_cache.ingest(initial, note="run_start")
        llm = self._ensure_llm()

        gate = await run_gate(query, initial, llm=llm, use_llm=self.use_llm)
        if not gate.should_intervene:
            return UrbanMultiAgentResult(
                query=query,
                gate=gate,
                initial_state=initial,
                skipped_reason=gate.reason or "gate declined",
                llm_used=llm is not None,
            )

        task = await self._cognition(query, initial, llm)
        if task.missing_information:
            return UrbanMultiAgentResult(
                query=query,
                gate=gate,
                urban_task=task,
                initial_state=initial,
                skipped_reason="missing_information:" + ",".join(task.missing_information),
                llm_used=llm is not None,
            )

        subgoals = await self._decompose(task, initial, llm)
        if not subgoals:
            return UrbanMultiAgentResult(
                query=query,
                gate=gate,
                urban_task=task,
                initial_state=initial,
                skipped_reason="decompose produced no subgoals",
                llm_used=llm is not None,
            )

        sub_plans = []
        for role in self.ROLE_ORDER:
            if role not in subgoals:
                continue
            agent = self.registry.primary(cast(SubAgentRole, role))
            sub_plans.append(
                await agent.run(subgoals[role], initial, llm=llm, use_llm=self.use_llm)
            )

        committed = await build_committed_batch(
            sub_plans,
            state=initial,
            task=task,
            dispatch_policy=self.dispatch_policy,
            llm=llm,
            use_llm=self.use_llm,
            use_llm_batch_rerank=self.use_llm_batch_rerank,
        )
        outcome = await execute_batch_ordered(
            self.sandbox,
            committed.batch_id,
            committed.actions,
        )

        if self.use_llm and llm is not None:
            try:
                report = await llm_final_report(
                    query, task, sub_plans, committed, outcome, llm
                )
            except Exception:
                report = deterministic_final_report(
                    query, task, sub_plans, committed, outcome
                )
        else:
            report = deterministic_final_report(
                query, task, sub_plans, committed, outcome
            )

        return UrbanMultiAgentResult(
            query=query,
            gate=gate,
            urban_task=task,
            subgoals=subgoals,
            sub_plans=sub_plans,
            committed=committed,
            batch_outcome=outcome,
            final_report=report,
            initial_state=initial,
            llm_used=llm is not None,
        )

    async def run_patrol_fire_response(
        self,
        *,
        patrol_waypoints: list[Coordinate] | None = None,
        fire_watch_point: Coordinate | None = None,
        patrol_altitude: float = 15.0,
        patrol_leg_m: float = 90.0,
        patrol_forward_axis: str = "x",
        max_patrol_drones: int = 3,
        detection_poll_interval_s: float = 0.5,
        max_detection_rounds: int = 60,
        arrival_poll_interval_s: float | None = None,
        max_arrival_rounds: int = 120,
        response_query_template: str = "{incident_id} 高严重度火情，请进行多智能体协同调度。",
        return_after_response: bool = True,
    ) -> PatrolFireResponseResult:
        """Run the full loop: patrol when idle, detect fire, hold above incident, dispatch, RTL.

        When ``patrol_waypoints`` is omitted, each UAV patrols a straight line
        from its spawn position: waypoint 1 = initial xy at ``patrol_altitude``,
        waypoint 2 = ``patrol_leg_m`` along ``patrol_forward_axis`` (+x or +y),
        then loop back. ``fire_watch_point`` is only used for post-detection hold.

        After fire appears in the snapshot, ``UAV-01`` is sent ``UAV_GOTO`` to the
        hold anchor above the fire, polled until arrival, then all patrol UAVs
        receive ``hold_drone``.
        """

        watch_xy = fire_watch_point or Coordinate(
            DEFAULT_FIRE_WATCH_XY[0],
            DEFAULT_FIRE_WATCH_XY[1],
            0.0,
        )
        notes: list[str] = []
        initial = copy.deepcopy(await self.sandbox.get_state())
        self.world_cache.ingest(initial, note="patrol_start")

        patrol_drones = self._select_patrol_drones(
            initial, max_patrol_drones=max_patrol_drones
        )
        patrol_drone_ids = [d.id for d in patrol_drones]

        detected = _first_open_fire(initial)
        patrol_outcome: BatchOutcome | None = None
        if detected is None:
            patrol_actions = self._build_patrol_actions(
                patrol_drones,
                patrol_waypoints=patrol_waypoints,
                fire_watch_point=watch_xy,
                patrol_altitude=patrol_altitude,
                patrol_leg_m=patrol_leg_m,
                patrol_forward_axis=patrol_forward_axis,
            )
            patrol_outcome = await execute_batch_ordered(
                self.sandbox,
                f"patrol-{uuid.uuid4().hex[:8]}",
                patrol_actions,
            )
            if patrol_actions:
                notes.append(f"patrol_started:{len(patrol_actions)}")
            else:
                notes.append("patrol_skipped:no_available_drone")
            detected = await self._wait_for_fire_detection(
                poll_interval_s=detection_poll_interval_s,
                max_rounds=max_detection_rounds,
            )
        else:
            notes.append(f"fire_already_present:{detected.id}")

        if detected is None:
            return PatrolFireResponseResult(
                patrol_outcome=patrol_outcome,
                detection_notes=notes + ["no_fire_detected"],
                final_report="巡逻完成：未发现火情，UrbanAgent 未进入应急调度。",
            )

        notes.append(f"fire_detected:{detected.id}")
        goto_anchor = _fire_hold_goto_anchor(detected, watch_xy, patrol_altitude)
        arrival_poll_s = (
            detection_poll_interval_s
            if arrival_poll_interval_s is None
            else arrival_poll_interval_s
        )
        hold_outcome: BatchOutcome | None = None
        if patrol_outcome is not None and patrol_drone_ids:
            hold_outcome, hold_notes = await self._execute_patrol_fire_hold(
                patrol_drone_ids=patrol_drone_ids,
                goto_anchor=goto_anchor,
                detected=detected,
                arrival_poll_s=arrival_poll_s,
                max_arrival_rounds=max_arrival_rounds,
            )
            notes.extend(hold_notes)
        else:
            notes.append("hold_skipped:no_active_patrol")
            hold_state = copy.deepcopy(await self.sandbox.get_state())
            fallback_actions = self._build_fire_hold_actions_goto_then_hold(
                hold_state,
                detected,
                patrol_drone_ids,
                hold_position=goto_anchor,
            )
            if fallback_actions:
                hold_outcome = await execute_batch_ordered(
                    self.sandbox,
                    f"hold-{uuid.uuid4().hex[:8]}",
                    fallback_actions,
                )
                notes.append(f"hold_fallback_goto:{len(fallback_actions)}")

        await self._emit_operator_notice(
            f"UAV hold above fire incident {detected.id}; UrbanAgent dispatch started."
        )

        response_query = response_query_template.format(incident_id=detected.id)
        response = await self.run(response_query)

        return_outcome: BatchOutcome | None = None
        if return_after_response:
            final_state = (
                response.batch_outcome.final_state
                if response.batch_outcome is not None
                and response.batch_outcome.final_state is not None
                else await self.sandbox.get_state()
            )
            return_actions = self._build_return_actions(response, final_state)
            if return_actions:
                return_outcome = await execute_batch_parallel(
                    self.sandbox,
                    f"return-{uuid.uuid4().hex[:8]}",
                    return_actions,
                )
                notes.append(f"return_started:{len(return_actions)}")
            else:
                notes.append("return_skipped:no_mobile_targets")

        return PatrolFireResponseResult(
            patrol_outcome=patrol_outcome,
            hold_outcome=hold_outcome,
            detected_incident_id=detected.id,
            detection_notes=notes,
            response=response,
            return_outcome=return_outcome,
            final_report=self._patrol_response_report(
                detected.id,
                hold_outcome,
                response,
                return_outcome,
            ),
        )

    async def _cognition(self, query: str, state, llm):
        if self.use_llm and llm is not None:
            try:
                return await llm_cognition(query, state, llm)
            except Exception:
                return rule_cognition(query, state)
        return rule_cognition(query, state)

    async def _decompose(self, task, state, llm):
        if self.use_llm and llm is not None:
            try:
                return await llm_decompose(task, state, llm, self.toolkit)
            except Exception:
                return rule_decompose(task, state, self.toolkit)
        return rule_decompose(task, state, self.toolkit)

    def _select_patrol_drones(
        self,
        state: CityState,
        *,
        max_patrol_drones: int,
    ) -> list[UrbanResource]:
        drones = [
            r
            for r in state.resources
            if r.kind == "drone"
            and r.status == "available"
            and "aerial_recon" in r.capabilities
        ]
        return drones[: max(0, max_patrol_drones)]

    def _build_patrol_actions(
        self,
        patrol_drones: list[UrbanResource],
        *,
        patrol_waypoints: list[Coordinate] | None,
        fire_watch_point: Coordinate,
        patrol_altitude: float,
        patrol_leg_m: float,
        patrol_forward_axis: str,
    ) -> list[UrbanAction]:
        actions: list[UrbanAction] = []
        anchor = Coordinate(
            fire_watch_point.x,
            fire_watch_point.y,
            max(float(patrol_altitude), float(fire_watch_point.z)),
        )
        for drone in patrol_drones:
            if patrol_waypoints:
                path = patrol_waypoints
            else:
                path = _linear_patrol_path_from_origin(
                    drone.position,
                    altitude=patrol_altitude,
                    leg_m=patrol_leg_m,
                    forward_axis=patrol_forward_axis,
                )
            actions.append(
                UrbanAction(
                    kind="patrol_drone",
                    target_id=drone.id,
                    parameters={
                        "path": path,
                        "loop": True,
                        "cruise_speed": 8.0,
                        "role": "fire_watch_patrol",
                        "fire_watch_anchor": anchor,
                    },
                    reason="idle fire-watch patrol over fire-watch area",
                )
            )
        return actions

    async def _execute_patrol_fire_hold(
        self,
        *,
        patrol_drone_ids: list[str],
        goto_anchor: Coordinate,
        detected: Incident,
        arrival_poll_s: float,
        max_arrival_rounds: int,
    ) -> tuple[BatchOutcome | None, list[str]]:
        """GOTO fire watch point with watch UAV (UAV-01), wait, then HOLD all patrol drones."""
        notes: list[str] = []
        watch_ids = _patrol_arrival_watch_drone_ids(patrol_drone_ids)
        hold_state = copy.deepcopy(await self.sandbox.get_state())
        goto_actions = self._build_fire_hold_goto_actions(
            hold_state,
            watch_ids,
            goto_anchor,
            detected.id,
            reason=f"goto above fire {detected.id} after patrol detection",
        )
        if goto_actions:
            goto_outcome = await execute_batch_ordered(
                self.sandbox,
                f"goto-{uuid.uuid4().hex[:8]}",
                goto_actions,
            )
            notes.append(f"watch_goto_started:{len(goto_actions)}")
            if not goto_outcome.criteria_satisfied:
                notes.extend(goto_outcome.notes)
            arrived = await self._wait_for_patrol_at_fire_anchor(
                [a.target_id for a in goto_actions],
                goto_anchor,
                poll_interval_s=arrival_poll_s,
                max_rounds=max_arrival_rounds,
            )
            if arrived:
                notes.append("watch_goto_arrived:fire_anchor")
            else:
                notes.append("watch_goto_arrival_timeout")
        else:
            notes.append("watch_goto_skipped:no_watch_uav")

        hold_actions = self._build_patrol_hold_actions(patrol_drone_ids)
        if not hold_actions:
            notes.append("hold_skipped:no_patrol_uav")
            return None, notes
        hold_outcome = await execute_batch_ordered(
            self.sandbox,
            f"hold-{uuid.uuid4().hex[:8]}",
            hold_actions,
        )
        notes.append(f"hold_started:{len(hold_actions)}")
        return hold_outcome, notes

    def _build_patrol_hold_actions(self, drone_ids: list[str]) -> list[UrbanAction]:
        """UAV_HOLD after watch UAV has reached the fire hold anchor."""
        return self._build_fire_hold_actions(drone_ids)

    def _build_fire_hold_goto_actions(
        self,
        state: CityState,
        drone_ids: list[str],
        hold_position: Coordinate,
        incident_id: str,
        *,
        reason: str | None = None,
    ) -> list[UrbanAction]:
        resources = {r.id: r for r in state.resources}
        goto_reason = reason or f"goto above fire {incident_id}"
        actions: list[UrbanAction] = []
        for drone_id in drone_ids:
            resource = resources.get(drone_id)
            if resource is None or resource.kind != "drone":
                continue
            actions.append(
                UrbanAction(
                    kind="dispatch_drone",
                    target_id=drone_id,
                    destination=hold_position,
                    parameters={
                        "cruise_speed": 8.0,
                        "role": "fire_confirmation_hover",
                    },
                    reason=goto_reason,
                )
            )
        return actions

    def _build_fire_hold_actions(self, drone_ids: list[str]) -> list[UrbanAction]:
        return [
            UrbanAction(
                kind="hold_drone",
                target_id=drone_id,
                parameters={"role": "fire_watch_hold"},
                reason="hold above fire after watch goto",
            )
            for drone_id in drone_ids
        ]

    def _build_fire_hold_actions_goto_then_hold(
        self,
        state: CityState,
        incident: Incident,
        drone_ids: list[str],
        *,
        hold_position: Coordinate | None = None,
        patrol_altitude: float | None = None,
    ) -> list[UrbanAction]:
        """Fallback when patrol was skipped: GOTO anchor, then HOLD (ordered batch)."""
        del patrol_altitude  # hold_position supersedes when provided by caller
        anchor = hold_position or _fire_hold_anchor(
            incident,
            Coordinate(incident.position.x, incident.position.y, incident.position.z),
            15.0,
        )
        goto_actions = self._build_fire_hold_goto_actions(
            state,
            drone_ids,
            anchor,
            incident.id,
            reason=f"goto above fire {incident.id} (no patrol leg)",
        )
        if not goto_actions:
            return []
        hold_actions = self._build_fire_hold_actions(
            [action.target_id for action in goto_actions]
        )
        return goto_actions + hold_actions

    async def _wait_for_patrol_at_fire_anchor(
        self,
        drone_ids: list[str],
        anchor: Coordinate,
        *,
        poll_interval_s: float,
        max_rounds: int,
    ) -> bool:
        """Poll snapshots until every patrol UAV is within arrival eps of anchor."""
        for round_idx in range(max(0, max_rounds) + 1):
            state = await self.sandbox.get_state()
            self.world_cache.ingest(state, note="patrol_arrival_poll")
            if _all_patrol_drones_at_anchor(state, drone_ids, anchor):
                return True
            if round_idx < max_rounds:
                await asyncio.sleep(max(0.0, poll_interval_s))
        return False

    async def _wait_for_fire_detection(
        self,
        *,
        poll_interval_s: float,
        max_rounds: int,
    ) -> Incident | None:
        for round_idx in range(max(0, max_rounds) + 1):
            state = await self.sandbox.get_state()
            self.world_cache.ingest(state, note="patrol_detection_poll")
            detected = _first_open_fire(state)
            if detected is not None:
                return detected
            if round_idx < max_rounds:
                await asyncio.sleep(max(0.0, poll_interval_s))
        return None

    async def _emit_operator_notice(self, message: str) -> None:
        sender = getattr(self.sandbox, "send_event_log", None)
        if sender is None:
            return
        try:
            await sender(message, severity="info")
        except Exception:
            return

    def _build_return_actions(
        self,
        response: UrbanMultiAgentResult,
        state: CityState,
    ) -> list[UrbanAction]:
        if response.committed is None:
            return []
        target_ids: list[str] = []
        for action in response.committed.actions:
            if action.kind not in {"dispatch_drone", "dispatch_vehicle"}:
                continue
            if action.target_id not in target_ids:
                target_ids.append(action.target_id)

        resources = {r.id: r for r in state.resources}
        return_actions: list[UrbanAction] = []
        for target_id in target_ids:
            resource = resources.get(target_id)
            if resource is None:
                continue
            if resource.kind == "drone":
                return_actions.append(
                    UrbanAction(
                        kind="return_drone",
                        target_id=target_id,
                        parameters={"role": "post_fire_return"},
                        reason="return to launch after fire response",
                    )
                )
            elif resource.kind in {"unmanned_vehicle", "ground_vehicle"}:
                return_actions.append(
                    UrbanAction(
                        kind="return_vehicle",
                        target_id=target_id,
                        parameters={"role": "post_fire_return"},
                        reason="return to base after fire response",
                    )
                )
        return return_actions

    def _patrol_response_report(
        self,
        incident_id: str,
        hold_outcome: BatchOutcome | None,
        response: UrbanMultiAgentResult,
        return_outcome: BatchOutcome | None,
    ) -> str:
        hold_ok = hold_outcome is not None and hold_outcome.criteria_satisfied
        response_ok = (
            response.batch_outcome is not None
            and response.batch_outcome.criteria_satisfied
        )
        return_ok = return_outcome is not None and return_outcome.criteria_satisfied
        return (
            f"巡逻发现火情 {incident_id}；"
            f"着火点悬停{'完成' if hold_ok else '未完成'}；"
            f"应急调度{'成功' if response_ok else '未完全成功'}；"
            f"返航{'已下发' if return_ok else '未完成或无可返航资源'}。"
        )


def _fire_hold_anchor(
    incident: Incident,
    fire_watch: Coordinate,
    patrol_altitude: float,
) -> Coordinate:
    hold_z = max(float(patrol_altitude), float(incident.position.z))
    return Coordinate(incident.position.x, incident.position.y, hold_z)


def _fire_hold_goto_anchor(
    incident: Incident,
    fire_watch: Coordinate,
    patrol_altitude: float,
) -> Coordinate:
    anchor = _fire_hold_anchor(incident, fire_watch, patrol_altitude)
    return offset_fire_hold_goto_position(anchor)


def _coordinate_distance_3d(left: Coordinate, right: Coordinate) -> float:
    return math.sqrt(
        (left.x - right.x) ** 2
        + (left.y - right.y) ** 2
        + (left.z - right.z) ** 2
    )


def _patrol_arrival_watch_drone_ids(patrol_drone_ids: list[str]) -> list[str]:
    """Return the single drone ID used for post-fire arrival polling."""
    watch = PATROL_ARRIVAL_WATCH_DRONE_ID
    watch_key = watch.lower().replace("-", "")
    for drone_id in patrol_drone_ids:
        if drone_id.lower().replace("-", "") == watch_key:
            return [drone_id]
    return [watch]


def _drone_at_fire_anchor(
    drone: UrbanResource,
    anchor: Coordinate,
    *,
    eps_m: float = PATROL_ARRIVAL_EPS_M,
) -> bool:
    return _coordinate_distance_3d(drone.position, anchor) <= eps_m


def _all_patrol_drones_at_anchor(
    state: CityState,
    drone_ids: list[str],
    anchor: Coordinate,
) -> bool:
    if not drone_ids:
        return False
    resources = {r.id: r for r in state.resources}
    for drone_id in drone_ids:
        resource = resources.get(drone_id)
        if resource is None or resource.kind != "drone":
            return False
        if not _drone_at_fire_anchor(resource, anchor):
            return False
    return True


def _first_open_fire(state: CityState) -> Incident | None:
    return next(
        (
            incident
            for incident in state.incidents
            if incident.kind == "fire" and incident.status in {"open", "responding"}
        ),
        None,
    )


def _linear_patrol_path_from_origin(
    origin: Coordinate,
    *,
    altitude: float,
    leg_m: float,
    forward_axis: str = "x",
) -> list[Coordinate]:
    """Two-point straight patrol: initial pose, then ``leg_m`` along +x or +y."""
    z = max(float(altitude), origin.z)
    start = Coordinate(origin.x, origin.y, z)
    leg = float(leg_m)
    axis = forward_axis.lower()
    if axis == "y":
        forward = Coordinate(start.x, start.y + leg, z)
    elif axis != "x":
        raise ValueError(f"patrol_forward_axis must be 'x' or 'y', got {forward_axis!r}")
    else:
        forward = Coordinate(start.x + leg, start.y, z)
    return [start, forward]
