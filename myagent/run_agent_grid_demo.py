import asyncio
import json
import re
from collections import deque
from pathlib import Path

from DefenseAgent.agent import AgentConfig, ReActAgent
from myagent.robot_control.robot_executor import RobotExecutor


# =========================
# 基础配置
# =========================

# 改成你的小车真实局域网 IP
ROBOT_HOST = "192.168.43.160"
ROBOT_PORT = 9000

# False = 真实执行；True = 只打印，不控制小车
DRY_RUN = False

# 小车初始车头朝向。
# 当前地图中，如果小车从 B2 出发时车头朝 C2，就填 "down"。
# 可选值："up", "right", "down", "left"
INITIAL_HEADING = "down"

# 网格移动参数。先保守，跑通后再微调。
CELL_FORWARD_SPEED = 0.08
CELL_FORWARD_DURATION = 4.0

TURN_SPEED = 0.4
TURN_90_DURATION = 2.4

# 如果 Agent 没输出灭火动作，是否自动补一个 fire_extinguish
AUTO_APPEND_FIRE_EXTINGUISH = True

GRID_TEXT = """
A1 A2 A3 A4
B1 S  B3 B4
C1 C2 F  C4
"""


# =========================
# JSON 提取
# =========================

def extract_json_array(text: str):
    """
    从 Agent 输出中提取 JSON 数组。

    支持：
    1. 纯 JSON 数组
    2. ```json ... ``` 代码块
    3. 自然语言中夹带 JSON 数组
    """
    text = str(text).strip()

    if text.startswith("[") and text.endswith("]"):
        return json.loads(text)

    code_block_match = re.search(
        r"```(?:json)?\s*(\[.*?\])\s*```",
        text,
        flags=re.DOTALL
    )
    if code_block_match:
        return json.loads(code_block_match.group(1))

    array_match = re.search(
        r"(\[\s*\{.*\}\s*\])",
        text,
        flags=re.DOTALL
    )
    if array_match:
        return json.loads(array_match.group(1))

    raise ValueError(f"Agent 输出中没有找到 JSON 数组:\n{text}")


def get_agent_final_answer(result):
    """
    从 AgentResult 中提取最终回答。
    当前 DefenseAgent 使用 final_answer 字段。
    """
    if hasattr(result, "final_answer"):
        return result.final_answer

    if hasattr(result, "output"):
        return result.output

    return str(result)


# =========================
# 网格解析与路径校验
# =========================

def parse_grid(grid_text: str):
    rows = []
    start = None
    fire = None

    for r, line in enumerate(grid_text.strip().splitlines()):
        cells = line.split()
        rows.append(cells)

        for c, cell in enumerate(cells):
            if cell == "S":
                start = (r, c)
            elif cell == "F":
                fire = (r, c)

    if start is None:
        raise ValueError("地图中没有找到起点 S")

    if fire is None:
        raise ValueError("地图中没有找到火点 F")

    return rows, start, fire


def cell_name(pos):
    r, c = pos
    return f"{chr(ord('A') + r)}{c + 1}"


def direction_to_delta(direction: str):
    mapping = {
        "up": (-1, 0),
        "right": (0, 1),
        "down": (1, 0),
        "left": (0, -1),
    }

    if direction not in mapping:
        raise ValueError(f"非法方向: {direction}")

    return mapping[direction]


def delta_to_direction(a, b):
    dr = b[0] - a[0]
    dc = b[1] - a[1]

    if dr == -1 and dc == 0:
        return "up"
    if dr == 1 and dc == 0:
        return "down"
    if dr == 0 and dc == 1:
        return "right"
    if dr == 0 and dc == -1:
        return "left"

    raise ValueError(f"非法相邻移动: {a} -> {b}")


def shortest_path(rows, start, goal):
    h = len(rows)
    w = len(rows[0])

    # 优先 down/right，适配当前 B2 -> C2 -> C3 的演示路径
    directions = [
        (1, 0),
        (0, 1),
        (-1, 0),
        (0, -1),
    ]

    q = deque([start])
    parent = {start: None}

    while q:
        cur = q.popleft()

        if cur == goal:
            break

        for dr, dc in directions:
            nr, nc = cur[0] + dr, cur[1] + dc

            if not (0 <= nr < h and 0 <= nc < w):
                continue

            nxt = (nr, nc)

            if nxt not in parent:
                parent[nxt] = cur
                q.append(nxt)

    if goal not in parent:
        raise ValueError("没有找到从 S 到 F 的路径")

    path = []
    cur = goal

    while cur is not None:
        path.append(cur)
        cur = parent[cur]

    path.reverse()
    return path


def path_to_skill_calls(path):
    calls = []

    for a, b in zip(path, path[1:]):
        direction = delta_to_direction(a, b)
        calls.append({
            "type": "skill_call",
            "skill": "move_one_cell",
            "vehicle": "F-01",
            "args": {
                "direction": direction
            }
        })

    if AUTO_APPEND_FIRE_EXTINGUISH:
        calls.append({
            "type": "skill_call",
            "skill": "fire_extinguish",
            "vehicle": "F-01",
            "args": {
                "target": "F"
            }
        })

    return calls


def simulate_skill_calls(rows, start, goal, skill_calls):
    """
    校验 Agent 给出的 move_one_cell 序列是否能从 S 到 F。
    只校验移动，不执行硬件。
    """
    h = len(rows)
    w = len(rows[0])
    pos = start

    has_move = False

    for call in skill_calls:
        skill = call.get("skill")
        args = call.get("args", {}) or {}

        if skill == "move_one_cell":
            has_move = True
            direction = args.get("direction")

            if direction not in ["up", "right", "down", "left"]:
                return False, pos, f"非法方向: {direction}"

            dr, dc = direction_to_delta(direction)
            nxt = (pos[0] + dr, pos[1] + dc)

            if not (0 <= nxt[0] < h and 0 <= nxt[1] < w):
                return False, pos, f"移动越界: {cell_name(pos)} -> {direction}"

            pos = nxt

        elif skill in ["fire_extinguish", "emergency_stop"]:
            continue

        else:
            return False, pos, f"未知 skill: {skill}"

    if not has_move:
        return False, pos, "没有移动动作"

    if pos != goal:
        return False, pos, f"Agent 路径未到达 F，当前位置: {cell_name(pos)}"

    return True, pos, "路径有效"


def repair_skill_calls_by_grid(skill_calls):
    """
    如果 Agent 给出的路径有效，就使用 Agent 路径。
    如果无效，自动回退到程序 BFS 最短路径。
    """
    rows, start, fire = parse_grid(GRID_TEXT)

    ok, end_pos, message = simulate_skill_calls(rows, start, fire, skill_calls)

    if ok:
        print("\n========== 路径校验 ==========")
        print(f"Agent 路径有效，终点: {cell_name(end_pos)}")

        has_fire_action = any(call.get("skill") == "fire_extinguish" for call in skill_calls)

        if AUTO_APPEND_FIRE_EXTINGUISH and not has_fire_action:
            print("Agent 未输出 fire_extinguish，已自动补充灭火/夹取动作。")
            skill_calls.append({
                "type": "skill_call",
                "skill": "fire_extinguish",
                "vehicle": "F-01",
                "args": {
                    "target": "F"
                }
            })

        return skill_calls

    print("\n========== 路径校验 ==========")
    print(f"Agent 路径无效，原因: {message}")
    print("自动使用程序 BFS 最短路径替代。")

    path = shortest_path(rows, start, fire)

    print("BFS 路径:", " -> ".join(cell_name(p) for p in path))

    return path_to_skill_calls(path)


# =========================
# Skill Call 规范化
# =========================

def normalize_direction(direction):
    """
    兼容中英文方向。
    """
    mapping = {
        "up": "up",
        "north": "up",
        "上": "up",
        "向上": "up",

        "right": "right",
        "east": "right",
        "右": "right",
        "向右": "right",

        "down": "down",
        "south": "down",
        "下": "down",
        "向下": "down",

        "left": "left",
        "west": "left",
        "左": "left",
        "向左": "left",
    }

    if direction is None:
        return None

    return mapping.get(str(direction).strip().lower(), direction)


def normalize_skill_call(raw_call: dict) -> dict:
    """
    将 Agent 输出的原始 skill call 规范化为内部 skill call。

    兼容以下格式：

    1. {"skill": "vehicle/move", "args": {"vehicle_id": "F-01", "direction": "down"}}
    2. {"skill": "move", "params": {"direction": "down"}}
    3. {"skill": "fire_extinguish", "params": {"target": "F"}}
    4. {"skill": "vehicle/fire_extinguish", "args": {"target": "F"}}
    """
    skill = raw_call.get("skill") or raw_call.get("tool")
    args = raw_call.get("args") or raw_call.get("params") or raw_call.get("arguments") or {}

    vehicle_id = (
        args.get("vehicle_id")
        or args.get("vehicle")
        or raw_call.get("vehicle_id")
        or raw_call.get("vehicle")
        or "F-01"
    )

    if skill in ["vehicle/move", "move"]:
        direction = normalize_direction(
            args.get("direction") or raw_call.get("direction")
        )

        if direction not in ["up", "down", "left", "right"]:
            return {
                "type": "skill_call",
                "skill": "emergency_stop",
                "vehicle": vehicle_id,
                "args": {
                    "reason": f"invalid direction: {direction}"
                }
            }

        return {
            "type": "skill_call",
            "skill": "move_one_cell",
            "vehicle": vehicle_id,
            "args": {
                "direction": direction
            }
        }

    if skill in ["vehicle/fire_extinguish", "fire_extinguish", "extinguish"]:
        return {
            "type": "skill_call",
            "skill": "fire_extinguish",
            "vehicle": vehicle_id,
            "args": {
                "target": args.get("target", raw_call.get("target", "F"))
            }
        }

    if skill in ["emergency_stop", "stop", "vehicle/stop"]:
        return {
            "type": "skill_call",
            "skill": "emergency_stop",
            "vehicle": vehicle_id,
            "args": {}
        }

    return {
        "type": "skill_call",
        "skill": "emergency_stop",
        "vehicle": vehicle_id,
        "args": {
            "reason": f"unknown skill: {skill}"
        }
    }


def normalize_skill_calls(raw_calls: list) -> list:
    return [normalize_skill_call(call) for call in raw_calls]


# =========================
# Skill Call 展开为 RobotExecutor 动作
# =========================

def turn_actions(current_heading: str, target_heading: str):
    """
    根据当前车头朝向和目标网格方向生成转向动作。
    坐标系：
    up -> right -> down -> left 为顺时针。
    """
    order = ["up", "right", "down", "left"]

    if current_heading not in order:
        raise ValueError(f"非法当前朝向: {current_heading}")

    if target_heading not in order:
        raise ValueError(f"非法目标朝向: {target_heading}")

    cur = order.index(current_heading)
    tar = order.index(target_heading)
    diff = (tar - cur) % 4

    if diff == 0:
        return [], target_heading

    if diff == 1:
        return [
            {"tool": "turn_right", "speed": TURN_SPEED, "duration": TURN_90_DURATION}
        ], target_heading

    if diff == 3:
        return [
            {"tool": "turn_left", "speed": TURN_SPEED, "duration": TURN_90_DURATION}
        ], target_heading

    return [
        {"tool": "turn_right", "speed": TURN_SPEED, "duration": TURN_90_DURATION},
        {"tool": "turn_right", "speed": TURN_SPEED, "duration": TURN_90_DURATION},
    ], target_heading


def expand_skill_call(skill_call: dict, state: dict) -> dict:
    skill = skill_call.get("skill")
    vehicle = skill_call.get("vehicle", "F-01")
    args = skill_call.get("args", {}) or {}

    if skill == "move_one_cell":
        direction = args.get("direction")
        heading = state.get("heading", INITIAL_HEADING)

        if direction not in ["up", "right", "down", "left"]:
            actions = [{"tool": "stop"}]
            message = f"非法移动方向: {direction}"
        else:
            turns, new_heading = turn_actions(heading, direction)
            state["heading"] = new_heading

            actions = []
            actions.extend(turns)
            actions.append({
                "tool": "forward",
                "speed": CELL_FORWARD_SPEED,
                "duration": CELL_FORWARD_DURATION
            })

            message = f"{vehicle} 从朝向 {heading} 调整到 {direction}，并前进一格"

        return {
            "skill": skill,
            "vehicle": vehicle,
            "actions": actions,
            "state_update": {
                "heading": state.get("heading", INITIAL_HEADING),
                "last_direction": direction
            },
            "message": message
        }

    if skill == "fire_extinguish":
        actions = [
            {"tool": "stop"},

            # 下探并夹取
            {"tool": "arm_pose", "pose": "down"},
            {"tool": "gripper", "state": "close"},
            {"tool": "arm_pose", "pose": "carry"},

            # 夹爪复原：先打开夹爪，再回 home
            {"tool": "gripper", "state": "open"},
            {"tool": "arm_pose", "pose": "home"},

            {"tool": "stop"},
        ]

        return {
            "skill": skill,
            "vehicle": vehicle,
            "actions": actions,
            "state_update": {
                "status": "fire_extinguished"
            },
            "message": f"{vehicle} 已在目标点执行灭火/夹取动作"
        }

    if skill == "emergency_stop":
        return {
            "skill": "emergency_stop",
            "vehicle": vehicle,
            "actions": [
                {"tool": "stop"}
            ],
            "state_update": {
                "status": "idle"
            },
            "message": f"{vehicle} 已执行紧急停止"
        }

    return {
        "skill": "emergency_stop",
        "vehicle": vehicle,
        "actions": [
            {"tool": "stop"}
        ],
        "state_update": {
            "status": "unknown_skill_stopped"
        },
        "message": f"未知 skill: {skill}，已停止"
    }


def execute_skill_calls(skill_calls: list):
    """
    将规范化后的 Skill Calls 展开并交给 RobotExecutor 执行。
    """
    executor = RobotExecutor(ROBOT_HOST, port=ROBOT_PORT)
    final_results = []

    state = {
        "heading": INITIAL_HEADING
    }

    for i, skill_call in enumerate(skill_calls, start=1):
        print(f"\n========== 执行 Skill Call {i}/{len(skill_calls)} ==========")
        print(json.dumps(skill_call, ensure_ascii=False, indent=2))

        skill_result = expand_skill_call(skill_call, state)

        print("\n========== Skill 展开结果 ==========")
        print(json.dumps(skill_result, ensure_ascii=False, indent=2))

        actions = skill_result.get("actions", [])

        print("\n========== RobotExecutor 执行 ==========")
        execute_result = executor.execute_plan(actions, dry_run=DRY_RUN)

        final_results.append({
            "skill_call": skill_call,
            "skill_result": skill_result,
            "execute_result": execute_result,
        })

    return final_results


# =========================
# 主流程
# =========================

async def main():
    config = AgentConfig(
        profile=Path("./myagent/my_profile/profile_grid_demo.yaml"),
        use_tools=False,
        use_memory=False,
        use_compressor=False,
        use_rag=False,
    )

    user_task = f"""
现在进行城市沙盘消防调度展示。

地图如下：

{GRID_TEXT}

说明：
S 表示小车 F-01 当前起点。
F 表示着火点。
城市道路是规则网格。
小车只能上下左右移动。

请调度 F-01 前往着火点，并在到达 F 后执行一次灭火/夹取动作。

输出要求：
你只能输出 JSON 数组，不要输出解释文字。
数组中的每一项是一个 skill call。

允许的格式只有：

1. 移动一格：
{{"skill": "move", "params": {{"direction": "down"}}}}

direction 只能是：
up, down, left, right

2. 到达 F 后灭火/夹取：
{{"skill": "fire_extinguish", "params": {{"target": "F"}}}}

当前地图中，S 在 B2，F 在 C3。
最短路径应该是 B2 -> C2 -> C3。
因此合理方向是 down -> right。
"""

    async with ReActAgent(config) as agent:
        result = await agent.run(user_task)

    print("\n========== Agent 原始输出 ==========")
    print(result)

    raw_output = get_agent_final_answer(result)

    print("\n========== Agent final_answer ==========")
    print(raw_output)

    raw_skill_calls = extract_json_array(raw_output)

    print("\n========== Agent 原始 JSON Calls ==========")
    print(json.dumps(raw_skill_calls, ensure_ascii=False, indent=2))

    skill_calls = normalize_skill_calls(raw_skill_calls)

    print("\n========== 规范化后的 Skill Calls ==========")
    print(json.dumps(skill_calls, ensure_ascii=False, indent=2))

    skill_calls = repair_skill_calls_by_grid(skill_calls)

    print("\n========== 路径修正后的 Skill Calls ==========")
    print(json.dumps(skill_calls, ensure_ascii=False, indent=2))

    final_results = execute_skill_calls(skill_calls)

    print("\n========== 最终执行结果 ==========")
    print(json.dumps(final_results, ensure_ascii=False, indent=2))

    print("\nUrban Grid Demo 执行完成")


if __name__ == "__main__":
    asyncio.run(main())