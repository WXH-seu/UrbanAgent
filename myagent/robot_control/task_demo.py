import time
from transbot_client import TransbotClient


ROBOT_IP = "192.168.43.160"  # 改成你的小车实际 IP


bot = TransbotClient(ROBOT_IP)


try:
    print("任务开始")

    bot.beep()
    time.sleep(0.5)

    print("机械臂回初始姿态")
    bot.arm_home()
    time.sleep(0.5)

    print("小车前进")
    bot.forward(speed=0.08, duration=1.5)
    time.sleep(0.5)

    print("机械臂下探")
    bot.arm_down()
    time.sleep(0.5)

    print("夹爪夹紧")
    bot.gripper_close()
    time.sleep(0.5)

    print("机械臂抬起")
    bot.arm_home()
    time.sleep(0.5)

    print("小车后退")
    bot.backward(speed=0.08, duration=1.5)
    time.sleep(0.5)

    print("夹爪松开")
    bot.gripper_open()
    time.sleep(0.5)

    print("任务完成")

finally:
    bot.stop()