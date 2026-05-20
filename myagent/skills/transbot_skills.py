from typing import Dict, Any, List


DEFAULT_MOVE_SPEED = 0.18
DEFAULT_CELL_DURATION = 1.0
DEFAULT_TURN_SPEED = 0.15
DEFAULT_TURN_DURATION = 0.55


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
        actions = [
            {"tool": "forward", "speed": speed, "duration": duration},
            {"tool": "stop"}
        ]
    elif direction == "S":
        actions = [
            {"tool": "backward", "speed": speed, "duration": duration},
            {"tool": "stop"}
        ]
    elif direction == "E":
        actions = [
            {"tool": "turn_right", "speed": DEFAULT_TURN_SPEED, "duration": DEFAULT_TURN_DURATION},
            {"tool": "forward", "speed": speed, "duration": duration},
            {"tool": "stop"}
        ]
    else:  # W
        actions = [
            {"tool": "turn_left", "speed": DEFAULT_TURN_SPEED, "duration": DEFAULT_TURN_DURATION},
            {"tool": "forward", "speed": speed, "duration": duration},
            {"tool": "stop"}
        ]

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
