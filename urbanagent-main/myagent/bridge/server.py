import math
import os
import re
import time
import threading
import uuid
from typing import Optional

import carla
import cv2
import numpy as np
import psutil
import requests
from flask import Flask, Response, jsonify, request
from flask_socketio import SocketIO, emit


CARLA_HOST = os.getenv("CARLA_HOST", "127.0.0.1")
CARLA_PORT = int(os.getenv("CARLA_PORT", "2000"))

# 外部接收端，逗号分隔，例如：
# export FIRE_TARGET_RECEIVERS="http://192.168.1.20:8000/fire_target,http://192.168.1.30:8000/fire_target"
FIRE_TARGET_RECEIVERS = os.getenv("FIRE_TARGET_RECEIVERS", "")

app = Flask(__name__)
socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    async_mode="threading",
    logger=True,
    engineio_logger=True,
)

carla_client: Optional[carla.Client] = None
carla_world: Optional[carla.World] = None
ego_vehicle: Optional[carla.Vehicle] = None

ground_camera_actor = None
city_camera_actor = None
aerial_camera_actor = None

latest_ground_frame = None
latest_city_frame = None
latest_aerial_frame = None
latest_fire_target = None

frame_lock = threading.Lock()


def log_event(severity: str, source: str, message: str):
    socketio.emit(
        "event_log",
        {
            "severity": severity,
            "source": source,
            "message": message,
        },
    )


def connect_carla():
    global carla_client, carla_world

    if carla_client is not None and carla_world is not None:
        return carla_client, carla_world

    client = carla.Client(CARLA_HOST, CARLA_PORT)
    client.set_timeout(10.0)
    world = client.get_world()

    carla_client = client
    carla_world = world

    return client, world


def get_speed_kmh(vehicle: carla.Vehicle) -> float:
    velocity = vehicle.get_velocity()
    return 3.6 * math.sqrt(
        velocity.x * velocity.x + velocity.y * velocity.y + velocity.z * velocity.z
    )


def get_heading_deg(vehicle: carla.Vehicle) -> float:
    return vehicle.get_transform().rotation.yaw % 360


def spawn_ego_vehicle():
    global ego_vehicle

    _, world = connect_carla()

    if ego_vehicle is not None and ego_vehicle.is_alive:
        return ego_vehicle

    blueprints = world.get_blueprint_library()
    vehicle_bp = blueprints.filter("vehicle.tesla.model3")[0]
    spawn_points = world.get_map().get_spawn_points()

    if not spawn_points:
        raise RuntimeError("CARLA 地图没有可用 spawn point")

    for spawn_point in spawn_points:
        vehicle = world.try_spawn_actor(vehicle_bp, spawn_point)
        if vehicle is not None:
            ego_vehicle = vehicle
            log_event("ok", "CARLA", f"已生成车辆 actor_id={vehicle.id}")
            return vehicle

    raise RuntimeError("车辆生成失败，所有 spawn point 都被占用")


def attach_ground_camera():
    global ground_camera_actor, latest_ground_frame

    _, world = connect_carla()
    vehicle = spawn_ego_vehicle()

    if ground_camera_actor is not None and ground_camera_actor.is_alive:
        return ground_camera_actor

    camera_bp = world.get_blueprint_library().find("sensor.camera.rgb")
    camera_bp.set_attribute("image_size_x", "640")
    camera_bp.set_attribute("image_size_y", "360")
    camera_bp.set_attribute("fov", "90")
    camera_bp.set_attribute("sensor_tick", "0.1")

    camera_transform = carla.Transform(
        carla.Location(x=1.6, z=1.7),
        carla.Rotation(pitch=-5),
    )

    ground_camera_actor = world.spawn_actor(
        camera_bp,
        camera_transform,
        attach_to=vehicle,
    )

    def on_image(image):
        global latest_ground_frame
        array = np.frombuffer(image.raw_data, dtype=np.uint8)
        array = array.reshape((image.height, image.width, 4))
        bgr = array[:, :, :3]
        ok, jpeg = cv2.imencode(".jpg", bgr)
        if ok:
            with frame_lock:
                latest_ground_frame = jpeg.tobytes()

    ground_camera_actor.listen(on_image)
    log_event("ok", "CARLA", f"已挂载 ground camera actor_id={ground_camera_actor.id}")
    return ground_camera_actor


def attach_city_camera():
    global city_camera_actor, latest_city_frame

    _, world = connect_carla()

    if city_camera_actor is not None and city_camera_actor.is_alive:
        return city_camera_actor

    camera_bp = world.get_blueprint_library().find("sensor.camera.rgb")
    camera_bp.set_attribute("image_size_x", "1280")
    camera_bp.set_attribute("image_size_y", "720")
    camera_bp.set_attribute("fov", "90")
    camera_bp.set_attribute("sensor_tick", "0.2")

    camera_transform = carla.Transform(
        carla.Location(x=0.0, y=0.0, z=120.0),
        carla.Rotation(pitch=-90.0, yaw=0.0, roll=0.0),
    )

    city_camera_actor = world.spawn_actor(camera_bp, camera_transform)

    def on_image(image):
        global latest_city_frame
        array = np.frombuffer(image.raw_data, dtype=np.uint8)
        array = array.reshape((image.height, image.width, 4))
        bgr = array[:, :, :3]
        ok, jpeg = cv2.imencode(".jpg", bgr)
        if ok:
            with frame_lock:
                latest_city_frame = jpeg.tobytes()

    city_camera_actor.listen(on_image)
    log_event("ok", "CARLA", f"已挂载 city camera actor_id={city_camera_actor.id}")
    return city_camera_actor


def attach_aerial_camera():
    """CARLA 仿真无人机视角：默认跟随 ego vehicle 上方俯视。"""
    global aerial_camera_actor, latest_aerial_frame

    _, world = connect_carla()
    vehicle = spawn_ego_vehicle()

    if aerial_camera_actor is not None and aerial_camera_actor.is_alive:
        return aerial_camera_actor

    camera_bp = world.get_blueprint_library().find("sensor.camera.rgb")
    camera_bp.set_attribute("image_size_x", "640")
    camera_bp.set_attribute("image_size_y", "360")
    camera_bp.set_attribute("fov", "80")
    camera_bp.set_attribute("sensor_tick", "0.1")

    loc = vehicle.get_transform().location
    camera_transform = carla.Transform(
        carla.Location(x=loc.x, y=loc.y, z=loc.z + 35.0),
        carla.Rotation(pitch=-90.0, yaw=0.0, roll=0.0),
    )

    aerial_camera_actor = world.spawn_actor(camera_bp, camera_transform)

    def on_image(image):
        global latest_aerial_frame
        array = np.frombuffer(image.raw_data, dtype=np.uint8)
        array = array.reshape((image.height, image.width, 4))
        bgr = array[:, :, :3]
        ok, jpeg = cv2.imencode(".jpg", bgr)
        if ok:
            with frame_lock:
                latest_aerial_frame = jpeg.tobytes()

    aerial_camera_actor.listen(on_image)
    log_event("ok", "CARLA", f"已挂载 aerial camera actor_id={aerial_camera_actor.id}")

    threading.Thread(target=aerial_camera_follow_loop, daemon=True).start()
    return aerial_camera_actor


def aerial_camera_follow_loop():
    global aerial_camera_actor
    while True:
        try:
            if (
                aerial_camera_actor is not None
                and aerial_camera_actor.is_alive
                and ego_vehicle is not None
                and ego_vehicle.is_alive
            ):
                loc = ego_vehicle.get_transform().location
                aerial_camera_actor.set_transform(
                    carla.Transform(
                        carla.Location(x=loc.x, y=loc.y, z=loc.z + 35.0),
                        carla.Rotation(pitch=-90.0, yaw=0.0, roll=0.0),
                    )
                )
        except Exception as e:
            try:
                log_event("warn", "CARLA", f"aerial camera 跟随失败: {e}")
            except Exception:
                pass
        time.sleep(0.2)


def get_state_payload():
    _, world = connect_carla()

    actors = world.get_actors()
    vehicles = actors.filter("vehicle.*")
    walkers = actors.filter("walker.pedestrian.*")

    ugv_payload = {
        "id": "CARLA-EGO-01",
        "speed": 0.0,
        "heading": 0.0,
        "road": world.get_map().name.split("/")[-1],
        "obstacle": "safe",
        "battery": 100,
        "link": {"latency": 8, "quality": 0.96},
    }

    if ego_vehicle is not None and ego_vehicle.is_alive:
        location = ego_vehicle.get_location()
        ugv_payload.update(
            {
                "speed": get_speed_kmh(ego_vehicle),
                "heading": get_heading_deg(ego_vehicle),
                "road": f"x={location.x:.1f}, y={location.y:.1f}",
            }
        )

    payload = {
        "ugv": ugv_payload,
        "city": {
            "vehicles": len(vehicles),
            "pedestrians": len(walkers),
            "intersections": "normal",
            "aqi": 35,
            "alerts": 1 if latest_fire_target else 0,
        },
        "uav": {
            "id": "UAV-01",
            "altitude": 0.0,
            "speed": 0.0,
            "heading": 0.0,
            "battery": 100,
            "gps": {"lat": 31.2304, "lng": 121.4737},
            "link": {"latency": 10, "quality": 0.95},
        },
    }

    return payload


def background_loop():
    while True:
        try:
            socketio.emit("state_update", get_state_payload())
            socketio.emit(
                "system_metrics",
                {
                    "cpu": psutil.cpu_percent(),
                    "gpu": 0,
                    "mem": psutil.virtual_memory().percent,
                    "net": 0,
                    "fps": 10,
                },
            )
        except Exception as e:
            socketio.emit(
                "event_log",
                {
                    "severity": "warn",
                    "source": "BRIDGE",
                    "message": f"状态推送失败: {e}",
                },
            )
        socketio.sleep(1)


def parse_goal_from_text(text: str):
    """从文本中解析 CARLA 世界坐标：x=... y=... z=...。"""
    text = text or ""

    def extract(axis: str, default=None):
        patterns = [
            rf"{axis}\s*=\s*(-?\d+(?:\.\d+)?)",
            rf"{axis}\s*:\s*(-?\d+(?:\.\d+)?)",
            rf"{axis}\s+(-?\d+(?:\.\d+)?)",
        ]
        for pat in patterns:
            m = re.search(pat, text, flags=re.IGNORECASE)
            if m:
                return float(m.group(1))
        return default

    x = extract("x")
    y = extract("y")
    z = extract("z", 0.0)

    if x is None or y is None:
        return None

    return {"x": float(x), "y": float(y), "z": float(z)}


def push_fire_target_to_receivers(payload: dict):
    """
    主动把 CARLA 火点坐标推送给外部接收端。
    FIRE_TARGET_RECEIVERS 格式：
    http://ip1:port/fire_target,http://ip2:port/fire_target
    """
    if not FIRE_TARGET_RECEIVERS:
        return []

    results = []
    urls = [u.strip() for u in FIRE_TARGET_RECEIVERS.split(",") if u.strip()]

    for url in urls:
        try:
            resp = requests.post(url, json=payload, timeout=3)
            results.append(
                {
                    "url": url,
                    "ok": resp.ok,
                    "status_code": resp.status_code,
                    "text": resp.text[:200],
                }
            )
        except Exception as e:
            results.append({"url": url, "ok": False, "error": str(e)})

    return results


def publish_fire_target(carla_point: dict, severity: str = "medium", fire_id: str | None = None):
    """发布 CARLA 世界坐标系下的起火点，不做任何现实坐标转换。"""
    global latest_fire_target

    x = float(carla_point["x"])
    y = float(carla_point["y"])
    z = float(carla_point.get("z", 0.0))
    fire_id = fire_id or f"FIRE-{int(time.time())}"

    latest_fire_target = {
        "type": "fire_target",
        "fire_id": fire_id,
        "frame": "carla_world",
        "position": {"x": x, "y": y, "z": z},
        "severity": severity,
        "source": "UrbanAgent",
        "timestamp": time.time(),
    }

    push_results = push_fire_target_to_receivers(latest_fire_target)

    socketio.emit(
        "event_log",
        {
            "severity": "ok",
            "source": "URBAN",
            "message": f"火点 CARLA 坐标已发布 x={x:.2f}, y={y:.2f}, z={z:.2f}",
        },
    )
    socketio.emit("fire_target", latest_fire_target)

    return latest_fire_target, push_results


@socketio.on("connect")
def on_connect():
    emit(
        "event_log",
        {
            "severity": "ok",
            "source": "BRIDGE",
            "message": "前端已连接 Python Bridge",
        },
    )

    try:
        connect_carla()
        emit(
            "event_log",
            {
                "severity": "ok",
                "source": "CARLA",
                "message": "CARLA Server 连接成功",
            },
        )
        emit("state_update", get_state_payload())
    except Exception as e:
        emit(
            "event_log",
            {
                "severity": "danger",
                "source": "CARLA",
                "message": f"CARLA 连接失败: {e}",
            },
        )


@socketio.on("agent_command")
def on_agent_command(data):
    """
    新主流程：前端/UrbanAgent 输入火点 CARLA 坐标后，只发布坐标。
    不再调用 CarAgent / DroneAgent，不再做现实坐标转换。
    """
    cmd_id = data.get("id") or str(uuid.uuid4())
    target = data.get("target", "fire_target")
    text = data.get("text", "")
    priority = data.get("priority", "normal")
    start = time.time()

    log_event("info", "COMMAND", f"收到指令 target={target}, priority={priority}, text={text}")

    try:
        goal = parse_goal_from_text(text)
        if goal is None:
            raise ValueError("未解析到 CARLA 坐标，请输入 x=... y=... z=...")

        severity = "medium"
        if "高" in text or "high" in text.lower():
            severity = "high"
        elif "低" in text or "low" in text.lower():
            severity = "low"

        fire, push_results = publish_fire_target(carla_point=goal, severity=severity)
        latency_ms = int((time.time() - start) * 1000)

        emit(
            "agent_ack",
            {"id": cmd_id, "target": "fire_target", "latency_ms": latency_ms},
        )
        socketio.emit(
            "event_log",
            {
                "severity": "info",
                "source": "AGENT",
                "message": (
                    f"accept target=fire_target kind=publish "
                    f"x={goal['x']} y={goal['y']} z={goal['z']} "
                    f"receivers={len(push_results)}"
                ),
            },
        )
        socketio.emit("state_update", get_state_payload())

    except Exception as e:
        emit("agent_reject", {"id": cmd_id, "target": target, "reason": str(e)})
        socketio.emit(
            "event_log",
            {
                "severity": "danger",
                "source": "URBAN",
                "message": f"火点坐标发布失败: {e}",
            },
        )


@app.route("/api/health")
def health():
    try:
        _, world = connect_carla()
        return {"ok": True, "carla": True, "map": world.get_map().name}
    except Exception as e:
        return {"ok": False, "carla": False, "error": str(e)}, 500


@app.route("/api/fire/report", methods=["POST"])
def api_fire_report():
    try:
        data = request.get_json(force=True) or {}
        pos = data.get("position", data)

        carla_point = {
            "x": float(pos["x"]),
            "y": float(pos["y"]),
            "z": float(pos.get("z", 0.0)),
        }
        severity = data.get("severity", "medium")
        fire_id = data.get("fire_id")

        fire, push_results = publish_fire_target(
            carla_point=carla_point,
            severity=severity,
            fire_id=fire_id,
        )

        return jsonify({"ok": True, "fire": fire, "push_results": push_results})

    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@app.route("/api/fire/latest")
def api_fire_latest():
    if latest_fire_target is None:
        return jsonify({"ok": False, "error": "no fire target yet"}), 404
    return jsonify({"ok": True, "fire": latest_fire_target})


def parse_float_arg(name, default=None):
    value = request.args.get(name, default)
    if value is None:
        return None
    return float(value)


def draw_carla_marker(x, y, z=0.0, label=None, life_time=60.0):
    _, world = connect_carla()

    loc = carla.Location(x=float(x), y=float(y), z=float(z) + 0.5)
    top = carla.Location(x=float(x), y=float(y), z=float(z) + 15.0)

    world.debug.draw_line(
        loc,
        top,
        thickness=0.15,
        color=carla.Color(255, 0, 0),
        life_time=life_time,
    )
    world.debug.draw_point(
        loc,
        size=0.25,
        color=carla.Color(0, 255, 0),
        life_time=life_time,
    )

    text = label or f"POINT x={float(x):.2f}, y={float(y):.2f}, z={float(z):.2f}"
    world.debug.draw_string(
        top,
        text,
        draw_shadow=True,
        color=carla.Color(255, 255, 0),
        life_time=life_time,
        persistent_lines=False,
    )

    return {
        "ok": True,
        "x": float(x),
        "y": float(y),
        "z": float(z),
        "label": text,
        "life_time": life_time,
    }


def move_spectator_to_point(x, y, z=0.0, height=80.0):
    global city_camera_actor

    _, world = connect_carla()
    target_transform = carla.Transform(
        carla.Location(x=float(x), y=float(y), z=float(z) + float(height)),
        carla.Rotation(pitch=-90.0, yaw=0.0, roll=0.0),
    )

    spectator = world.get_spectator()
    spectator.set_transform(target_transform)

    try:
        if city_camera_actor is not None and city_camera_actor.is_alive:
            city_camera_actor.set_transform(target_transform)
    except Exception as e:
        log_event("warn", "CARLA", f"city camera 跳转失败: {e}")

    return {"ok": True, "x": float(x), "y": float(y), "z": float(z), "height": float(height)}


@app.route("/api/carla/ego_pose")
def api_carla_ego_pose():
    try:
        vehicle = spawn_ego_vehicle()
        tf = vehicle.get_transform()
        loc = tf.location
        rot = tf.rotation

        return jsonify(
            {
                "ok": True,
                "id": vehicle.id,
                "location": {"x": loc.x, "y": loc.y, "z": loc.z},
                "rotation": {"pitch": rot.pitch, "yaw": rot.yaw, "roll": rot.roll},
            }
        )
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/carla/mark")
def api_carla_mark():
    try:
        x = parse_float_arg("x")
        y = parse_float_arg("y")
        z = parse_float_arg("z", 0.0)
        life_time = parse_float_arg("life_time", 60.0)
        label = request.args.get("label")

        if x is None or y is None:
            return jsonify({"ok": False, "error": "missing x/y"}), 400

        result = draw_carla_marker(x=x, y=y, z=z, label=label, life_time=life_time)
        log_event("ok", "CARLA", f"已标记 CARLA 坐标 x={x:.2f}, y={y:.2f}, z={z:.2f}")
        return jsonify(result)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/carla/goto_view")
def api_carla_goto_view():
    try:
        x = parse_float_arg("x")
        y = parse_float_arg("y")
        z = parse_float_arg("z", 0.0)
        height = parse_float_arg("height", 80.0)

        if x is None or y is None:
            return jsonify({"ok": False, "error": "missing x/y"}), 400

        marker_result = draw_carla_marker(
            x=x,
            y=y,
            z=z,
            label=f"VIEW x={x:.2f}, y={y:.2f}, z={z:.2f}",
            life_time=120.0,
        )
        view_result = move_spectator_to_point(x=x, y=y, z=z, height=height)
        log_event("ok", "CARLA", f"视角已跳转到 CARLA 坐标 x={x:.2f}, y={y:.2f}, z={z:.2f}")
        return jsonify({"ok": True, "marker": marker_result, "view": view_result})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


def make_placeholder_frame(camera: str):
    img = np.zeros((360, 640, 3), dtype=np.uint8)

    if camera == "city":
        title = "CARLA CITY VIEW"
    elif camera == "ground":
        title = "CARLA GROUND VIEW"
    elif camera == "aerial":
        title = "UAV AERIAL VIEW"
    else:
        title = f"CAMERA: {camera}"

    cv2.putText(img, title, (40, 90), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2, cv2.LINE_AA)
    cv2.putText(
        img,
        time.strftime("%H:%M:%S"),
        (40, 150),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.8,
        (180, 180, 180),
        2,
        cv2.LINE_AA,
    )

    ok, jpeg = cv2.imencode(".jpg", img)
    if not ok:
        return None
    return jpeg.tobytes()


def mjpeg_placeholder(camera: str):
    while True:
        frame = make_placeholder_frame(camera)
        if frame is not None:
            yield b"--frame\r\n" b"Content-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
        time.sleep(0.1)


def mjpeg_ground_camera():
    try:
        attach_ground_camera()
    except Exception as e:
        log_event("danger", "CARLA", f"ground camera 挂载失败: {e}")

    while True:
        with frame_lock:
            frame = latest_ground_frame
        if frame is None:
            frame = make_placeholder_frame("ground")
        yield b"--frame\r\n" b"Content-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
        time.sleep(0.05)


def mjpeg_city_camera():
    try:
        attach_city_camera()
    except Exception as e:
        log_event("danger", "CARLA", f"city camera 挂载失败: {e}")

    while True:
        with frame_lock:
            frame = latest_city_frame
        if frame is None:
            frame = make_placeholder_frame("city")
        yield b"--frame\r\n" b"Content-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
        time.sleep(0.1)


def mjpeg_aerial_camera():
    try:
        attach_aerial_camera()
    except Exception as e:
        log_event("danger", "CARLA", f"aerial camera 挂载失败: {e}")

    while True:
        with frame_lock:
            frame = latest_aerial_frame
        if frame is None:
            frame = make_placeholder_frame("aerial")
        yield b"--frame\r\n" b"Content-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
        time.sleep(0.1)


@app.route("/video_feed")
def video_feed():
    camera = request.args.get("camera", "ground")

    if camera == "ground":
        return Response(mjpeg_ground_camera(), mimetype="multipart/x-mixed-replace; boundary=frame")
    if camera == "city":
        return Response(mjpeg_city_camera(), mimetype="multipart/x-mixed-replace; boundary=frame")
    if camera == "aerial":
        return Response(mjpeg_aerial_camera(), mimetype="multipart/x-mixed-replace; boundary=frame")

    return Response(mjpeg_placeholder(camera), mimetype="multipart/x-mixed-replace; boundary=frame")


def main():
    socketio.start_background_task(background_loop)
    socketio.run(app, host="0.0.0.0", port=5000, allow_unsafe_werkzeug=True)


if __name__ == "__main__":
    main()
