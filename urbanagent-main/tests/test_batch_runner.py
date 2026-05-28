"""Tests for batch_runner parallel vs ordered execution."""
from __future__ import annotations

import asyncio
import unittest

from urbanagent.multiagent.batch_runner import execute_batch_parallel
from urbanagent.types import ActionResult, UrbanAction


class _ParallelTrackingSandbox:
    def __init__(self) -> None:
        self._in_flight = 0
        self._max_in_flight = 0

    async def get_state(self):
        return None

    async def send_action(self, action: UrbanAction) -> ActionResult:
        self._in_flight += 1
        self._max_in_flight = max(self._max_in_flight, self._in_flight)
        await asyncio.sleep(0.05)
        self._in_flight -= 1
        return ActionResult(status="applied", action=action, message="ok")


class BatchRunnerTests(unittest.IsolatedAsyncioTestCase):
    async def test_execute_batch_parallel_overlaps(self) -> None:
        sandbox = _ParallelTrackingSandbox()
        actions = [
            UrbanAction(kind="return_vehicle", target_id="UGV-01"),
            UrbanAction(kind="return_drone", target_id="UAV-01"),
        ]
        outcome = await execute_batch_parallel(sandbox, "batch-test", actions)
        self.assertTrue(outcome.criteria_satisfied)
        self.assertEqual(len(outcome.per_step_results), 2)
        self.assertGreaterEqual(sandbox._max_in_flight, 2)


if __name__ == "__main__":
    unittest.main()
