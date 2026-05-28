import json
import socket


class TransbotClient:
    def __init__(self, ip: str, port: int = 9000, timeout: float = 5.0):
        self.ip = ip
        self.port = port
        self.timeout = timeout

    def send(self, cmd: dict) -> dict:
        data = json.dumps(cmd, ensure_ascii=False)

        with socket.create_connection((self.ip, self.port), timeout=self.timeout) as sock:
            sock.sendall(data.encode("utf-8"))
            raw = sock.recv(4096).decode("utf-8").strip()

        print("发送：", cmd)
        print("返回：", raw)

        try:
            result = json.loads(raw)
        except json.JSONDecodeError:
            result = {
                "ok": False,
                "error": raw,
            }

        return result

    def beep(self, ms: int = 100):
        return self.send({
            "action": "beep",
            "ms": ms,
        })

    def stop(self):
        return self.send({
            "action": "stop",
        })

    def move(self, v: float = 0.0, w: float = 0.0, t: float = 0.5):
        return self.send({
            "action": "move",
            "v": v,
            "w": w,
            "t": t,
        })

    def arm(self, a7: int, a8: int, a9: int, run_time: int = 1200):
        return self.send({
            "action": "arm",
            "a7": a7,
            "a8": a8,
            "a9": a9,
            "run_time": run_time,
        })

    def servo(self, servo_id: int, angle: int, run_time: int = 800):
        return self.send({
            "action": "servo",
            "id": servo_id,
            "angle": angle,
            "run_time": run_time,
        })