"""
沙盘运动标定参数。

当前小车使用开环控制：v / w / t。
前进/后退启动瞬间可能存在左右轮响应不同步。
因此一格移动拆成：
1. 低速预启动段
2. 正常移动段
3. stop

后续微调运动效果时，优先只改这个文件。
"""

# =========================
# 前进一格：起步预启动段
# =========================

FORWARD_STARTUP_V = 0.06
FORWARD_STARTUP_W = -0.15
FORWARD_STARTUP_T = 0.20

# =========================
# 前进一格：正常直行段
# =========================

FORWARD_MAIN_V = 0.18
FORWARD_MAIN_W = 0.0
FORWARD_MAIN_T = 2.00

# =========================
# 后退一格：起步预启动段
# =========================

BACKWARD_STARTUP_V = -0.06
BACKWARD_STARTUP_W = 0.0
BACKWARD_STARTUP_T = 0.20

# =========================
# 后退一格：正常后退段
# =========================

BACKWARD_MAIN_V = -0.18
BACKWARD_MAIN_W = 0.0
BACKWARD_MAIN_T = 0.80

# =========================
# 原地转向 90 度
# =========================

TURN_W = 0.15
TURN_LEFT_90_T = 0.55
TURN_RIGHT_90_T = 0.55

# =========================
# 动作之间的停顿
# =========================

SHORT_SLEEP = 0.05
NORMAL_SLEEP = 0.20
STOP_SLEEP = 0.30


# =========================
# 坐标路线比例参数
# =========================

# 输入坐标单位到现实沙盘距离的比例。
# 例如：如果坐标差 1.0 对应沙盘 1 cm，可以先设为 1.0。
# 后续通过标定调整。
COORD_DISTANCE_SCALE = 1.0

# 每 1 个坐标距离单位，对应的前进主运动时间。
# 例如两个点距离为 10，FORWARD_T_PER_COORD_UNIT = 0.08，
# 那么主运动时间 = 10 * 0.08 = 0.8 秒。
FORWARD_T_PER_COORD_UNIT = 0.08

# 最小前进时间，避免太短导致小车不动
MIN_FORWARD_MAIN_T = 0.15

# 最大单段前进时间，避免异常坐标导致小车长时间冲出去
MAX_FORWARD_MAIN_T = 5.0


def clamp(value, low, high):
    return max(low, min(high, value))


def get_forward_main_t_by_distance(distance: float) -> float:
    """
    根据坐标距离计算前进主运动时间。
    """
    scaled_distance = distance * COORD_DISTANCE_SCALE
    t = scaled_distance * FORWARD_T_PER_COORD_UNIT
    return clamp(t, MIN_FORWARD_MAIN_T, MAX_FORWARD_MAIN_T)