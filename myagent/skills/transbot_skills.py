from typing import Dict, Any, List
from typing import Dict, Any, List

from myagent.robot_control import motion_config as mc
from myagent.urban_sandbox.waypoint_route import (
    waypoints_to_segments,
    heading_to_angle_deg,
    turn_angle_from_to,
)
DEFAULT_MOVE_SPEED = 0.18
DEFAULT_CELL_DURATION = 1.0
DEFAULT_TURN_SPEED = 0.15
DEFAULT_TURN_DURATION = 0.55

def forward_one_cell_actions():
    """
    前进一格动作。

    两段式控制：
    1. 低速预启动，缓解左右轮启动不同步
    2. 正常直行
    3. 停止
    """
    return [
        {
            "tool": "move",
            "v": mc.FORWARD_STARTUP_V,
            "w": mc.FORWARD_STARTUP_W,
            "t": mc.FORWARD_STARTUP_T,
            "sleep_after": mc.SHORT_SLEEP,
        },
        {
            "tool": "move",
            "v": mc.FORWARD_MAIN_V,
            "w": mc.FORWARD_MAIN_W,
            "t": mc.FORWARD_MAIN_T,
            "sleep_after": mc.NORMAL_SLEEP,
        },
        {
            "tool": "stop",
            "sleep_after": mc.STOP_SLEEP,
        },
    ]


def backward_one_cell_actions():
    """
    后退一格动作。

    两段式控制：
    1. 低速预启动
    2. 正常后退
    3. 停止
    """
    return [
        {
            "tool": "move",
            "v": mc.BACKWARD_STARTUP_V,
            "w": mc.BACKWARD_STARTUP_W,
            "t": mc.BACKWARD_STARTUP_T,
            "sleep_after": mc.SHORT_SLEEP,
        },
        {
            "tool": "move",
            "v": mc.BACKWARD_MAIN_V,
            "w": mc.BACKWARD_MAIN_W,
            "t": mc.BACKWARD_MAIN_T,
            "sleep_after": mc.NORMAL_SLEEP,
        },
        {
            "tool": "stop",
            "sleep_after": mc.STOP_SLEEP,
        },
    ]


def turn_left_90_actions():
    """
    左转 90 度。
    """
    return [
        {
            "tool": "move",
            "v": 0.0,
            "w": mc.TURN_W,
            "t": mc.TURN_LEFT_90_T,
            "sleep_after": mc.NORMAL_SLEEP,
        },
        {
            "tool": "stop",
            "sleep_after": mc.STOP_SLEEP,
        },
    ]


def turn_right_90_actions():
    """
    右转 90 度。
    """
    return [
        {
            "tool": "move",
            "v": 0.0,
            "w": -mc.TURN_W,
            "t": mc.TURN_RIGHT_90_T,
            "sleep_after": mc.NORMAL_SLEEP,
        },
        {
            "tool": "stop",
            "sleep_after": mc.STOP_SLEEP,
        },
    ]
def emergency_stop(vehicle: str = "F-01", **kwargs) -> Dict[str, Any]:
    """
    紧急停止 skill。

    任何时候都应该可用。
    """
    actions = [
        {"tool": "stop"}
    ]

    return {
        "skill": "emergency_stop",
        "vehicle": vehicle,
        "actions": actions,
        "state_update": {
            "status": "idle"
        },
        "message": f"{vehicle} 已执行紧急停止"
    }


def move_one_cell(
    direction: str,
    vehicle: str = "F-01",
    speed: float = DEFAULT_MOVE_SPEED,
    duration: float = DEFAULT_CELL_DURATION,
    **kwargs
) -> Dict[str, Any]:
    """
    向指定方向移动一格。

    注意：
    这里暂时不处理小车朝向，只做最小测试版本。
    后续会升级成：
    1. 读取当前 heading
    2. 自动转向
    3. 前进一格
    4. 更新 node 和 heading
    """

    direction = direction.upper()

    if direction not in {"N", "E", "S", "W"}:
        raise ValueError(f"非法 direction: {direction}，只能是 N/E/S/W")

    # 当前阶段先用简单映射：
    # N/S/E/W 暂时只做动作演示，不更新真实网格坐标
    if direction == "N":
        actions = forward_one_cell_actions()

    elif direction == "S":
        actions = backward_one_cell_actions()

    elif direction == "E":
        actions = []
        actions.extend(turn_right_90_actions())
        actions.extend(forward_one_cell_actions())

    else:  # W
        actions = []
        actions.extend(turn_left_90_actions())
        actions.extend(forward_one_cell_actions())

    return {
        "skill": "move_one_cell",
        "vehicle": vehicle,
        "actions": actions,
        "state_update": {
            "status": "idle"
        },
        "message": f"{vehicle} 已尝试向 {direction} 移动一格"
    }


def register_transbot_skills(registry):
    registry.register("emergency_stop", emergency_stop)
    registry.register("move_one_cell", move_one_cell)

def turn_by_angle_actions(angle_deg: float):
    """
    根据角度生成转向动作。

    angle_deg:
      正数 = 左转
      负数 = 右转

    使用 90 度标定时间按比例换算。
    """
    if abs(angle_deg) < 5.0:
        return []

    actions = []

    if angle_deg > 0:
        t = mc.TURN_LEFT_90_T * abs(angle_deg) / 90.0
        actions.append({
            "tool": "move",
            "v": 0.0,
            "w": mc.TURN_W,
            "t": t,
            "sleep_after": mc.NORMAL_SLEEP,
        })
    else:
        t = mc.TURN_RIGHT_90_T * abs(angle_deg) / 90.0
        actions.append({
            "tool": "move",
            "v": 0.0,
            "w": -mc.TURN_W,
            "t": t,
            "sleep_after": mc.NORMAL_SLEEP,
        })

    actions.append({
        "tool": "stop",
        "sleep_after": mc.STOP_SLEEP,
    })

    return actions

def forward_by_distance_actions(distance: float):
    """
    根据坐标距离生成前进动作。
    """
    return [
        {
            "tool": "move",
            "v": mc.FORWARD_STARTUP_V,
            "w": mc.FORWARD_STARTUP_W,
            "t": mc.FORWARD_STARTUP_T,
            "sleep_after": mc.SHORT_SLEEP,
        },
        {
            "tool": "move",
            "v": mc.FORWARD_MAIN_V,
            "w": mc.FORWARD_MAIN_W,
            "t": mc.get_forward_main_t_by_distance(distance),
            "sleep_after": mc.NORMAL_SLEEP,
        },
        {
            "tool": "stop",
            "sleep_after": mc.STOP_SLEEP,
        },
    ]

def follow_waypoints(
    waypoints,
    vehicle: str = "F-01",
    start_heading: str = "N",
    y_axis_down: bool = False,
    **kwargs
):
    """
    按连续坐标 waypoint 路线执行。

    输入示例：
    {
      "skill": "follow_waypoints",
      "vehicle": "F-01",
      "args": {
        "waypoints": [
          {"x": 17.525, "y": 22.474, "z": 0.0},
          {"x": 25.000, "y": 22.474, "z": 0.0},
          {"x": 25.000, "y": 35.000, "z": 0.0}
        ],
        "start_heading": "E"
      }
    }

    说明：
    - waypoints 已经包含所有转弯点。
    - 本 skill 不规划路径，只按输入顺序执行。
    - 每段：转向到目标角度，然后按距离前进。
    """
    segments = waypoints_to_segments(
        waypoints,
        y_axis_down=y_axis_down
    )

    current_angle = heading_to_angle_deg(start_heading)

    actions = []

    for segment in segments:
        target_angle = segment["angle_deg"]
        distance = segment["distance"]

        turn_angle = turn_angle_from_to(
            current_angle=current_angle,
            target_angle=target_angle
        )

        actions.extend(turn_by_angle_actions(turn_angle))
        actions.extend(forward_by_distance_actions(distance))

        current_angle = target_angle

    return {
        "skill": "follow_waypoints",
        "vehicle": vehicle,
        "actions": actions,
        "state_update": {
            "last_waypoint": waypoints[-1],
            "heading_angle_deg": current_angle,
            "status": "idle"
        },
        "message": f"{vehicle} 已生成 waypoint 路线动作，共 {len(segments)} 段",
    }


def register_transbot_skills(registry):
    registry.register("emergency_stop", emergency_stop)
    registry.register("move_one_cell", move_one_cell)
    registry.register("follow_waypoints", follow_waypoints)