import json
import socket
import time


ROBOT_IP = "192.168.43.160"
ROBOT_PORT = 9000


def send(cmd):
    data = json.dumps(cmd, ensure_ascii=False)

    with socket.create_connection((ROBOT_IP, ROBOT_PORT), timeout=5) as sock:
        sock.sendall(data.encode("utf-8"))
        result = sock.recv(4096).decode("utf-8").strip()

    print("发送：", cmd)
    
    print("返回：", result)
    return result


def stop():
    send({
        "action": "stop"
    })


def move_forward(speed=0.08, duration=1.0):
    send({
        "action": "move",
        "v": speed,
        "w": 0,
        "t": duration
    })


def move_backward(speed=0.08, duration=1.0):
    send({
        "action": "move",
        "v": -speed,
        "w": 0,
        "t": duration
    })


def turn_left(speed=0.4, duration=1.0):
    send({
        "action": "move",
        "v": 0,
        "w": speed,
        "t": duration
    })


def turn_right(speed=0.4, duration=1.0):
    send({
        "action": "move",
        "v": 0,
        "w": -speed,
        "t": duration
    })


def arm_home():
    send({
        "action": "arm",
        "a7": 180,
        "a8": 180,
        "a9": 90,
        "run_time": 1200
    })


def arm_down():
    send({
        "action": "arm",
        "a7": 160,
        "a8": 150,
        "a9": 90,
        "run_time": 1200
    })


def gripper_open():
    send({
        "action": "servo",
        "id": 9,
        "angle": 90,
        "run_time": 800
    })


def gripper_close():
    send({
        "action": "servo",
        "id": 9,
        "angle": 170,
        "run_time": 800
    })


try:
    # 连接测试
    send({
        "action": "beep",
        "ms": 100
    })

    time.sleep(0.5)

    # 小车移动测试
    move_forward(0.08, 1.0)
    time.sleep(0.5)

    move_backward(0.08, 1.0)
    time.sleep(0.5)

    turn_left(0.4, 1.0)
    time.sleep(0.5)

    turn_right(0.4, 1.0)
    time.sleep(0.5)

    # 机械臂测试
    arm_home()
    time.sleep(0.5)

    arm_down()
    time.sleep(0.5)

    gripper_close()
    time.sleep(0.5)

    arm_home()
    time.sleep(0.5)

    gripper_open()

finally:
    stop()