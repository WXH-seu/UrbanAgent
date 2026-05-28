from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import Any

from myagent.robot_control.robot_executor import RobotExecutor
from myagent.skills.transbot_skills import follow_waypoints

@dataclass
class GridPose:
    cell: str
    x: float
    y: float
    z: float = 0.0


class TransbotAgentSkills:
    """
    UrbanAgent UGV 命令 -> CarAgent 真实执行器 的适配层。

    第一阶段：
    - UrbanAgent 下发 UGV_GOTO / UGV_EXTINGUISH / UGV_RTL / UGV_STOP
    - 本类转换成 RobotExecutor 能执行的 plan
    - RobotExecutor 再调用 TransbotClient 控制真实小车

    环境变量：
    - TRANSBOT_IP: 小车主板 IP，默认 127.0.0.1
    - TRANSBOT_PORT: 小车控制端口，默认 9000
    - TRANSBOT_DRY_RUN: 是否只打印不执行，默认 1
    """

    def __init__(self) -> None:
        self.current_cell = "B2"
        self.home_cell = "B2"

        self.grid_step_m = 10.0

        self.grid: dict[str, GridPose] = {
            "A1": GridPose("A1", 0, 0),
            "A2": GridPose("A2", 10, 0),
            "A3": GridPose("A3", 20, 0),
            "A4": GridPose("A4", 30, 0),

            "B1": GridPose("B1", 0, 10),
            "B2": GridPose("B2", 10, 10),
            "B3": GridPose("B3", 20, 10),
            "B4": GridPose("B4", 30, 10),

            "C1": GridPose("C1", 0, 20),
            "C2": GridPose("C2", 10, 20),
            "C3": GridPose("C3", 20, 20),
            "C4": GridPose("C4", 30, 20),
        }

        robot_ip = os.getenv("TRANSBOT_IP", "127.0.0.1")
        robot_port = int(os.getenv("TRANSBOT_PORT", "9000"))
        self.dry_run = os.getenv("TRANSBOT_DRY_RUN", "1") != "0"

        print(
            f"[TransbotAgentSkills] robot_ip={robot_ip}, "
            f"port={robot_port}, dry_run={self.dry_run}"
        )

        self.executor = RobotExecutor(robot_ip=robot_ip, port=robot_port)

        # 你之前要求：前后行进指令比原来更久。
        # RobotExecutor 里单步最多 clamp 到 5.0 秒，所以这里先设 4.0。
        self.move_speed = 0.18
        self.move_duration = 4.0

        # 你之前要求：转向指令需多执行一次。
        # 这里通过连续两个 turn 实现。
        self.turn_speed = 0.15
        self.turn_duration = 0.55

    def get_pose_array(self) -> list[float]:
        pose = self.grid[self.current_cell]
        return [pose.x, pose.y, pose.z]

    def coord_to_cell(self, x: float, y: float) -> str:
        best_cell = self.current_cell
        best_dist = float("inf")

        for cell, pose in self.grid.items():
            dist = (pose.x - x) ** 2 + (pose.y - y) ** 2
            if dist < best_dist:
                best_dist = dist
                best_cell = cell

        return best_cell

    async def goto_route_waypoints(
        self,
        route_waypoints: list[dict[str, Any]],
        *,
        start_heading: str = "E",
        y_axis_down: bool = False,
    ) -> None:
        """
        执行 UrbanAgent / CARLA GlobalRoutePlanner 输出的 waypoint 路径。

        route_waypoints:
        [
            {"x": 10.0, "y": 10.0, "z": 0.0},
            {"x": 12.0, "y": 10.0, "z": 0.0},
            ...
        ]

        当前实现：
        1. 规范化 waypoint 坐标
        2. 调用 follow_waypoints() 生成 RobotExecutor action plan
        3. 调用 RobotExecutor 执行动作
        """
        print(
            f"[Transbot] follow route_waypoints: "
            f"{len(route_waypoints)} points"
        )

        normalized_waypoints: list[dict[str, float]] = []

        for i, wp in enumerate(route_waypoints, start=1):
            if not isinstance(wp, dict):
                raise ValueError(f"invalid waypoint at index {i}: {wp!r}")

            point = {
                "x": float(wp.get("x", 0.0)),
                "y": float(wp.get("y", 0.0)),
                "z": float(wp.get("z", 0.0)),
            }
            normalized_waypoints.append(point)

            print(f"[Transbot] waypoint {i}: {point}")

        if len(normalized_waypoints) < 2:
            print("[Transbot] route_waypoints 少于 2 个点，只执行 stop")
            await self.stop()
            return

        skill_result = follow_waypoints(
            waypoints=normalized_waypoints,
            vehicle="UGV-01",
            start_heading=start_heading,
            y_axis_down=y_axis_down,
        )

        plan = skill_result.get("actions", [])

        print(
            f"[Transbot] waypoint route converted to "
            f"{len(plan)} RobotExecutor actions"
        )

        if not plan:
            print("[Transbot] waypoint route 生成的 plan 为空，只执行 stop")
            await self.stop()
            return

        await self._execute_plan_async(plan)

        last = normalized_waypoints[-1]
        self.current_cell = self.coord_to_cell(last["x"], last["y"])

    def plan_path(self, start: str, target: str) -> list[str]:
        start_pose = self.grid[start]
        target_pose = self.grid[target]

        dx_cells = round((target_pose.x - start_pose.x) / self.grid_step_m)
        dy_cells = round((target_pose.y - start_pose.y) / self.grid_step_m)

        steps: list[str] = []

        if dy_cells > 0:
            steps.extend(["down"] * dy_cells)
        elif dy_cells < 0:
            steps.extend(["up"] * abs(dy_cells))

        if dx_cells > 0:
            steps.extend(["right"] * dx_cells)
        elif dx_cells < 0:
            steps.extend(["left"] * abs(dx_cells))

        return steps

    async def goto_xy(self, x: float, y: float, z: float = 0.0) -> None:
        target_cell = self.coord_to_cell(x, y)
        await self.goto_cell(target_cell)

    async def goto_cell(self, target_cell: str) -> None:
        path = self.plan_path(self.current_cell, target_cell)

        print(f"[Transbot] goto {self.current_cell} -> {target_cell}, path={path}")

        for step in path:
            await self.execute_step(step)

        self.current_cell = target_cell
        await self.stop()

    async def execute_step(self, direction: str) -> None:
        print(f"[Transbot] execute step: {direction}")

        if direction == "up":
            await self.move_up()
        elif direction == "down":
            await self.move_down()
        elif direction == "left":
            await self.move_left()
        elif direction == "right":
            await self.move_right()
        else:
            raise ValueError(f"unknown direction: {direction}")

    async def _execute_plan_async(self, plan: list[dict[str, Any]]) -> None:
        """
        RobotExecutor 是同步阻塞执行。
        这里用 asyncio.to_thread，避免阻塞 Socket.IO 事件循环。
        """
        results = await asyncio.to_thread(
            self.executor.execute_plan,
            plan,
            self.dry_run,
        )

        failed = [r for r in results if isinstance(r, dict) and not r.get("ok", False)]
        if failed:
            raise RuntimeError(f"robot execution failed: {failed}")

    async def move_down(self) -> None:
        """
        沙盘 down：默认映射为小车前进一格。
        """
        plan = [
            {"tool": "forward", "speed": self.move_speed, "duration": self.move_duration},
            {"tool": "stop"},
        ]
        await self._execute_plan_async(plan)

    async def move_up(self) -> None:
        """
        沙盘 up：默认映射为小车后退一格。
        """
        plan = [
            {"tool": "backward", "speed": self.move_speed, "duration": self.move_duration},
            {"tool": "stop"},
        ]
        await self._execute_plan_async(plan)

    async def move_right(self) -> None:
        """
        沙盘 right：右转两次，然后前进一格。
        你之前要求转向指令多执行一次，所以这里有两个 turn_right。
        """
        plan = [
            {"tool": "turn_right", "speed": self.turn_speed, "duration": self.turn_duration},
            {"tool": "turn_right", "speed": self.turn_speed, "duration": self.turn_duration},
            {"tool": "forward", "speed": self.move_speed, "duration": self.move_duration},
            {"tool": "stop"},
        ]
        await self._execute_plan_async(plan)

    async def move_left(self) -> None:
        """
        沙盘 left：左转两次，然后前进一格。
        """
        plan = [
            {"tool": "turn_left", "speed": self.turn_speed, "duration": self.turn_duration},
            {"tool": "turn_left", "speed": self.turn_speed, "duration": self.turn_duration},
            {"tool": "forward", "speed": self.move_speed, "duration": self.move_duration},
            {"tool": "stop"},
        ]
        await self._execute_plan_async(plan)

    async def stop(self) -> None:
        plan = [
            {"tool": "stop"},
        ]
        await self._execute_plan_async(plan)

    async def extinguish(self) -> None:
        """
        当前先用机械臂/夹爪动作模拟灭火：
        1. 机械臂 down
        2. 夹爪 close
        3. 机械臂 carry
        4. 夹爪 open
        5. gripper_reset
        """
        plan = [
            {"tool": "arm_pose", "pose": "down", "sleep_after": 0.5},
            {"tool": "gripper", "state": "close", "sleep_after": 0.5},
            {"tool": "arm_pose", "pose": "carry", "sleep_after": 0.5},
            {"tool": "gripper", "state": "open", "sleep_after": 0.5},
        ]
        await self._execute_plan_async(plan)

        # 你之前要求：最后夹爪要复原
        await self.gripper_reset()

    async def gripper_reset(self) -> None:
        plan = [
            {"tool": "gripper", "state": "open", "sleep_after": 0.3},
            {"tool": "arm_pose", "pose": "home", "sleep_after": 0.3},
        ]
        await self._execute_plan_async(plan)

    async def return_home(self) -> None:
        print(f"[Transbot] return home: {self.home_cell}")
        await self.goto_cell(self.home_cell)
        await self.gripper_reset()