import math
from typing import List, Dict, Any


def normalize_angle_deg(angle: float) -> float:
    """
    归一化角度到 [-180, 180)。
    """
    while angle >= 180:
        angle -= 360
    while angle < -180:
        angle += 360
    return angle


def heading_to_angle_deg(heading: str) -> float:
    """
    将离散朝向转换成角度。

    约定：
      E = 0 度
      N = 90 度
      W = 180 度
      S = -90 度

    注意：
      这里采用数学坐标系：x 向右，y 向上。
      如果你的地图 y 轴向下，需要在 segment_angle_deg 里取反 dy。
    """
    heading = heading.upper()

    mapping = {
        "E": 0.0,
        "N": 90.0,
        "W": 180.0,
        "S": -90.0,
    }

    if heading not in mapping:
        raise ValueError(f"非法 heading: {heading}")

    return mapping[heading]


def angle_to_nearest_heading(angle: float) -> str:
    """
    将角度近似为 N/E/S/W。
    如果你的路线严格是横平竖直，这个函数会很稳定。
    """
    angle = normalize_angle_deg(angle)

    candidates = {
        "E": 0.0,
        "N": 90.0,
        "W": 180.0,
        "S": -90.0,
    }

    best_heading = None
    best_error = 999.0

    for heading, candidate_angle in candidates.items():
        error = abs(normalize_angle_deg(angle - candidate_angle))
        if error < best_error:
            best_error = error
            best_heading = heading

    return best_heading


def waypoint_to_xy(point: Dict[str, Any]):
    """
    从 {'x': ..., 'y': ..., 'z': ...} 中取 x/y。
    """
    if not isinstance(point, dict):
        raise ValueError(f"waypoint 必须是 dict: {point}")

    if "x" not in point or "y" not in point:
        raise ValueError(f"waypoint 缺少 x/y: {point}")

    return float(point["x"]), float(point["y"])


def segment_from_waypoints(p1: Dict[str, Any], p2: Dict[str, Any], y_axis_down: bool = False):
    """
    根据两个连续 waypoint 生成一段移动信息。

    返回：
    {
      "from": {...},
      "to": {...},
      "dx": ...,
      "dy": ...,
      "distance": ...,
      "angle_deg": ...,
      "heading": "N/E/S/W"
    }

    y_axis_down:
      False: y 越大表示越上方，数学坐标系
      True: y 越大表示越下方，图像坐标系/屏幕坐标系
    """
    x1, y1 = waypoint_to_xy(p1)
    x2, y2 = waypoint_to_xy(p2)

    dx = x2 - x1
    dy = y2 - y1

    if y_axis_down:
        dy_for_angle = -dy
    else:
        dy_for_angle = dy

    distance = math.hypot(dx, dy)

    if distance <= 1e-6:
        raise ValueError(f"重复 waypoint，无需移动: {p1} -> {p2}")

    angle_deg = math.degrees(math.atan2(dy_for_angle, dx))
    heading = angle_to_nearest_heading(angle_deg)

    return {
        "from": p1,
        "to": p2,
        "dx": dx,
        "dy": dy,
        "distance": distance,
        "angle_deg": angle_deg,
        "heading": heading,
    }


def waypoints_to_segments(waypoints: List[Dict[str, Any]], y_axis_down: bool = False):
    """
    将 waypoint 列表转换成移动段。
    """
    if not isinstance(waypoints, list):
        raise ValueError("waypoints 必须是 list")

    if len(waypoints) < 2:
        raise ValueError("waypoints 至少需要两个点")

    segments = []

    for i in range(len(waypoints) - 1):
        segments.append(
            segment_from_waypoints(
                waypoints[i],
                waypoints[i + 1],
                y_axis_down=y_axis_down
            )
        )

    return segments


def turn_angle_from_to(current_angle: float, target_angle: float) -> float:
    """
    计算从当前角度转到目标角度的最短转角。
    正数表示左转，负数表示右转。
    """
    return normalize_angle_deg(target_angle - current_angle)