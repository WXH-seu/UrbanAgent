import json

from myagent.skills.skill_registry import SkillRegistry
from myagent.skills.transbot_skills import register_transbot_skills

from myagent.robot_control.transbot_client import TransbotClient
from myagent.robot_control.robot_executor import RobotExecutor


ROBOT_HOST = "192.168.43.160"
ROBOT_PORT = 9000


def main():
    registry = SkillRegistry()
    register_transbot_skills(registry)

    print("========== 已注册 Skills ==========")
    for skill_name in registry.list_skills():
        print("-", skill_name)

    skill_call = {
        "skill": "move_one_cell",
        "vehicle": "F-01",
        "args": {
            "direction": "E"
        }
    }

    print("\n========== Skill Call ==========")
    print(json.dumps(skill_call, ensure_ascii=False, indent=2))

    result = registry.call(
        skill_call["skill"],
        vehicle=skill_call.get("vehicle", "F-01"),
        **skill_call.get("args", {})
    )

    print("\n========== Skill Result ==========")
    print(json.dumps(result, ensure_ascii=False, indent=2))

    actions = result["actions"]

    executor = RobotExecutor(ROBOT_HOST, port=ROBOT_PORT)

    print("\n========== Dry Run 执行 ==========")
    execute_results = executor.execute_plan(actions, dry_run=True)

    print("\n========== Execute Results ==========")
    print(json.dumps(execute_results, ensure_ascii=False, indent=2))
    print("\nSkill 测试完成")


if __name__ == "__main__":
    main()
