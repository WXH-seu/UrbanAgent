"""G6–G7: submit to sandbox; collect outcomes.

Bridge × Agent Protocol v1.0 has `command_status` events drive completion, so
``sandbox.send_action`` already blocks until a terminal status (or ``ongoing``).
There is no longer a need to poll ``CityState`` to confirm that an action took
effect — terminal status implies the world matches.

Use ``execute_batch_ordered`` when steps must not overlap (e.g. GOTO then
extinguish on the same UGV). Use ``execute_batch_parallel`` when each step
targets a different asset (e.g. post-response RTL).
"""
from __future__ import annotations

import asyncio

from urbanagent.multiagent.schemas import BatchOutcome
from urbanagent.sandbox import SandboxClient
from urbanagent.types import ActionResult, CityState, UrbanAction


def batch_criteria_met(
    state: CityState,
    actions: list[UrbanAction],
    results: list[ActionResult],
) -> bool:
    """All steps reached a non-rejected terminal status."""

    del state  # unused under protocol v1.0; kept for signature compatibility
    if not actions:
        return False
    if len(results) != len(actions):
        return False
    return all(r.status in {"accepted", "applied"} for r in results)


async def execute_batch_ordered(
    sandbox: SandboxClient,
    batch_id: str,
    actions: list[UrbanAction],
    *,
    max_poll_rounds: int = 0,
    poll_interval_s: float = 0.0,
) -> BatchOutcome:
    del max_poll_rounds, poll_interval_s  # protocol v1.0 confirms via command_status
    per_step: list[ActionResult] = []
    notes: list[str] = []
    if not actions:
        final_state = await sandbox.get_state()
        return BatchOutcome(
            batch_id=batch_id,
            per_step_results=[],
            polling_iterations=0,
            criteria_satisfied=False,
            final_state=final_state,
            notes=["no executable actions produced by multi-agent planning"],
        )
    for action in actions:
        result = await sandbox.send_action(action)
        per_step.append(result)
        if result.status == "rejected":
            notes.append(
                f"rejected at {action.kind} target={action.target_id}: {result.message}"
            )
            break

    final_state = await sandbox.get_state()
    ok = batch_criteria_met(final_state, actions, per_step)
    if not ok and not notes:
        notes.append("not all actions reached a non-rejected terminal state")
    return BatchOutcome(
        batch_id=batch_id,
        per_step_results=per_step,
        polling_iterations=0,
        criteria_satisfied=ok,
        final_state=final_state,
        notes=notes,
    )


async def execute_batch_parallel(
    sandbox: SandboxClient,
    batch_id: str,
    actions: list[UrbanAction],
) -> BatchOutcome:
    """Submit all actions concurrently; wait for every terminal status."""
    notes: list[str] = []
    if not actions:
        final_state = await sandbox.get_state()
        return BatchOutcome(
            batch_id=batch_id,
            per_step_results=[],
            polling_iterations=0,
            criteria_satisfied=False,
            final_state=final_state,
            notes=["no executable actions produced by multi-agent planning"],
        )
    per_step = list(
        await asyncio.gather(*(sandbox.send_action(action) for action in actions))
    )
    for action, result in zip(actions, per_step):
        if result.status == "rejected":
            notes.append(
                f"rejected at {action.kind} target={action.target_id}: {result.message}"
            )

    final_state = await sandbox.get_state()
    ok = batch_criteria_met(final_state, actions, per_step)
    if not ok and not notes:
        notes.append("not all actions reached a non-rejected terminal state")
    return BatchOutcome(
        batch_id=batch_id,
        per_step_results=per_step,
        polling_iterations=0,
        criteria_satisfied=ok,
        final_state=final_state,
        notes=notes,
    )
