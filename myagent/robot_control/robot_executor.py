import time
from .transbot_client import TransbotClient


class RobotExecutor:
    """
    Agent 不直接控制硬件，而是把动作交给 RobotExecutor。
    这里负责：
    1. 动作白名单
    2. 速度限幅
    3. 机械臂姿态限制
    4. 异常时自动 stop
    """

    def __init__(self, robot_ip: str, port: int = 9000):
        self.bot = TransbotClient(robot_ip, port=port)

        # 这里请替换成你已经调好的机械臂姿态
        self.arm_poses = {
            "home": {"a7": 230, "a8": 60, "a9": 0, "run_time": 2000},
            "down": {"a7": 160, "a8": 150, "a9": 90, "run_time": 2000},
            "carry": {"a7": 120, "a8": 180, "a9": 170, "run_time": 3000},
        }

        self.gripper = {
            "open": 90,
            "close": 170,
        }

    @staticmethod
    def clamp(value, low, high):
        value = float(value)
        return max(low, min(high, value))

    def execute_step(self, step: dict, dry_run: bool = False):
        tool = step.get("tool")

        print(f"\n[RobotExecutor] step = {step}")

        if dry_run:
            print("[DRY RUN] 不实际执行硬件动作")
            return {"ok": True, "dry_run": True, "step": step}

        if tool == "beep":
            ms = int(self.clamp(step.get("ms", 100), 10, 1000))
            return self.bot.beep(ms)

        if tool == "stop":
            return self.bot.stop()

        if tool == "move":
            v = self.clamp(step.get("v", 0), -0.20, 0.20)
            w = self.clamp(step.get("w", 0), -1.00, 1.00)
            t = self.clamp(step.get("t", 0.5), 0.0, 5.0)
            return self.bot.move(v=v, w=w, t=t)

        if tool == "forward":
            speed = self.clamp(step.get("speed", 0.08), 0.0, 0.20)
            duration = self.clamp(step.get("duration", 1.0), 0.0, 5.0)
            return self.bot.move(v=speed, w=0.0, t=duration)

        if tool == "backward":
            speed = self.clamp(step.get("speed", 0.08), 0.0, 0.20)
            duration = self.clamp(step.get("duration", 1.0), 0.0, 5.0)
            return self.bot.move(v=-speed, w=0.0, t=duration)

        if tool == "turn_left":
            speed = self.clamp(step.get("speed", 0.4), 0.0, 1.00)
            duration = self.clamp(step.get("duration", 1.0), 0.0, 5.0)
            return self.bot.move(v=0.0, w=speed, t=duration)

        if tool == "turn_right":
            speed = self.clamp(step.get("speed", 0.4), 0.0, 1.00)
            duration = self.clamp(step.get("duration", 1.0), 0.0, 5.0)
            return self.bot.move(v=0.0, w=-speed, t=duration)

        if tool == "arm_pose":
            pose_name = step.get("pose", "home")

            if pose_name not in self.arm_poses:
                return {
                    "ok": False,
                    "error": f"unknown arm pose: {pose_name}",
                }

            pose = self.arm_poses[pose_name]
            return self.bot.arm(**pose)

        if tool == "gripper":
            state = step.get("state", "open")

            if state not in self.gripper:
                return {
                    "ok": False,
                    "error": f"unknown gripper state: {state}",
                }

            angle = self.gripper[state]
            return self.bot.servo(servo_id=9, angle=angle, run_time=800)

        return {
            "ok": False,
            "error": f"unknown tool: {tool}",
        }

    def execute_plan(self, plan: list, dry_run: bool = False):
        results = []

        try:
            for i, step in enumerate(plan, start=1):
                print(f"\n========== 执行动作 {i}/{len(plan)} ==========")
                result = self.execute_step(step, dry_run=dry_run)

                if isinstance(result, str):
                    try:
                        import json
                        result = json.loads(result)
                    except Exception:
                        result = {
                            "ok": False,
                            "error": result,
                        }
                results.append(result)

                if not result.get("ok", False):
                    print("[RobotExecutor] 动作失败，停止执行后续动作")
                    break

                time.sleep(float(step.get("sleep_after", 0.3)))

        finally:
            if not dry_run:
                print("\n[RobotExecutor] 最终安全停止")
                self.bot.stop()

        return results