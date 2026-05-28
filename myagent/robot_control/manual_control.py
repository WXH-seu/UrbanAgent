import json
import socket


ROBOT_IP = "192.168.43.160"
ROBOT_PORT = 9000


def send(cmd):
    data = json.dumps(cmd, ensure_ascii=False)

    with socket.create_connection((ROBOT_IP, ROBOT_PORT), timeout=5) as sock:
        sock.sendall(data.encode("utf-8"))
        result = sock.recv(4096).decode("utf-8").strip()

    print("返回：", result)


def stop():
    send({"action": "stop"})


def forward():
    send({"action": "move", "v": 0.08, "w": 0, "t": 2})


def backward():
    send({"action": "move", "v": -0.08, "w": 0, "t": 2})


def left():
    send({"action": "move", "v": 0, "w": 0.4, "t": 1})


def right():
    send({"action": "move", "v": 0, "w": -0.4, "t": 1})


def arm_home():
    send({"action": "arm", "a7": 230, "a8": 60, "a9": 0, "run_time": 2000})


def arm_down():
    send({"action": "arm", "a7": 120, "a8": 180, "a9": 180, "run_time": 3000})


def gripper_open():
    send({"action": "servo", "id": 9, "angle": 90, "run_time": 800})


def gripper_close():
    send({"action": "servo", "id": 9, "angle": 170, "run_time": 800})


print("""
键盘控制：
w：前进
s：后退
a：左转
d：右转
x：停止

h：机械臂回初始姿态
j：机械臂下探
o：夹爪打开
p：夹爪夹紧

q：退出
""")


try:
    while True:
        key = input("请输入指令：").strip().lower()

        if key == "w":
            forward()
        elif key == "s":
            backward()
        elif key == "a":
            left()
        elif key == "d":
            right()
        elif key == "x":
            stop()
        elif key == "h":
            arm_home()
        elif key == "j":
            arm_down()
        elif key == "o":
            gripper_open()
        elif key == "p":
            gripper_close()
        elif key == "q":
            break
        else:
            print("未知指令")

finally:
    stop()