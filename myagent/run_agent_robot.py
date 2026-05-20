import asyncio
import json
import re
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


from DefenseAgent.agent import AgentConfig, ReActAgent
from myagent.robot_control.robot_executor import RobotExecutor


ROBOT_IP = "192.168.43.160"  # 改成小车真实 IP


def extract_json_array(text: str):
    """
    从 Agent 输出中提取 JSON 数组。
    允许 Agent 前后有少量解释文本，但推荐让它只输出 JSON。
    """
    text = str(text)

    match = re.search(r"\[\s*\{.*\}\s*\]", text, flags=re.DOTALL)
    if not match:
        raise ValueError(f"没有找到 JSON 动作数组。Agent 原始输出：\n{text}")

    return json.loads(match.group(0))


async def main():
    config = AgentConfig(
        profile=Path("./myagent/my_profile/profile.yaml"),
        use_tools=True,
        use_memory=False,
        use_compressor=False,
        use_rag=False,
    )

    user_task = """
现在进行一个 UrbanAgent 小车调度展示。

场景：
小车需要前进到目标点，使用机械臂完成一次夹取动作，然后后退返回。

你不能直接输出自然语言控制小车。
你只能输出 JSON 数组。
数组中的每一项是一个动作对象。

允许使用的动作 tool 只有：
1. beep
2. forward
3. backward
4. turn_left
5. turn_right
6. move
7. stop
8. arm_pose
9. gripper

动作格式示例：
[
  {"tool": "beep", "ms": 100},
  {"tool": "arm_pose", "pose": "home"},
  {"tool": "forward", "speed": 0.08, "duration": 1.0},
  {"tool": "arm_pose", "pose": "down"},
  {"tool": "gripper", "state": "close"},
  {"tool": "arm_pose", "pose": "carry"},
  {"tool": "backward", "speed": 0.08, "duration": 1.0},
  {"tool": "gripper", "state": "open"},
  {"tool": "stop"}
]

安全约束：
- speed 不要超过 0.12
- duration 单次不要超过 2.0
- 转向 speed 不要超过 0.5
- 必须以 stop 结束
- 不要输出解释文字
- 只输出 JSON 数组
"""

    async with ReActAgent(config) as agent:
        result = await agent.run(user_task)

    print("\n========== Agent 原始输出 ==========")
    print(result)

    answer = getattr(result, "final_answer", str(result))
  
    print("\n========== Agent final_answer ==========")
    print(answer)
    plan = extract_json_array(answer)

    print("\n========== 解析后的动作计划 ==========")
    print(json.dumps(plan, ensure_ascii=False, indent=2))

    executor = RobotExecutor(ROBOT_IP)

   
    executor.execute_plan(plan, dry_run=True)

    confirm = input("\n确认要实际执行吗？输入 y 执行：").strip().lower()
    if confirm == "y":
        executor.execute_plan(plan, dry_run=False)
    else:
        print("已取消实际执行。")


if __name__ == "__main__":
    asyncio.run(main())