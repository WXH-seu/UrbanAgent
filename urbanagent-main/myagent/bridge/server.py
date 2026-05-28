import math
import time
import threading
import uuid
from typing import Optional

import carla
import psutil
from flask import Flask, Response, request
from flask_socketio import SocketIO, emit

import cv2
import numpy as np

CARLA_HOST = "127.0.0.1"
CARLA_PORT = 2000

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
camera_actor = None
city_camera_actor = None
latest_city_frame = None
latest_ground_frame = None
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
    yaw = vehicle.get_transform().rotation.yaw
    return yaw % 360


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

    blueprint_library = world.get_blueprint_library()

    camera_bp = blueprint_library.find("sensor.camera.rgb")
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

        # CARLA raw_data 是 BGRA，取前三通道即可作为 BGR 给 OpenCV 编码
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

    blueprint_library = world.get_blueprint_library()

    camera_bp = blueprint_library.find("sensor.camera.rgb")
    camera_bp.set_attribute("image_size_x", "1280")
    camera_bp.set_attribute("image_size_y", "720")
    camera_bp.set_attribute("fov", "90")
    camera_bp.set_attribute("sensor_tick", "0.2")

    # 先用 Town10HD_Opt 上方的固定鸟瞰位置。
    # 后面可以根据 ego vehicle 位置动态跟随。
    camera_transform = carla.Transform(
        carla.Location(x=0.0, y=0.0, z=120.0),
        carla.Rotation(pitch=-90.0, yaw=0.0, roll=0.0),
    )

    city_camera_actor = world.spawn_actor(
        camera_bp,
        camera_transform,
    )

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
        "link": {
            "latency": 8,
            "quality": 0.96,
        },
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
            "alerts": 0,
        },
        "uav": {
            "id": "UAV-01",
            "altitude": 0.0,
            "speed": 0.0,
            "heading": 0.0,
            "battery": 100,
            "gps": {
                "lat": 31.2304,
                "lng": 121.4737,
            },
            "link": {
                "latency": 10,
                "quality": 0.95,
            },
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
    cmd_id = data.get("id") or str(uuid.uuid4())
    target = data.get("target", "carla")
    text = data.get("text", "")
    priority = data.get("priority", "normal")

    start = time.time()

    log_event("info", "COMMAND", f"收到指令 target={target}, priority={priority}, text={text}")

    try:
        vehicle = spawn_ego_vehicle()

        lowered = text.lower()

        if "stop" in lowered or "停车" in text or "停止" in text:
            vehicle.set_autopilot(False)
            vehicle.apply_control(carla.VehicleControl(throttle=0.0, brake=1.0))
            result = "车辆已停止"

        elif "auto" in lowered or "自动驾驶" in text or "巡航" in text:
            vehicle.set_autopilot(True)
            result = "车辆已进入自动驾驶"

        elif "forward" in lowered or "前进" in text or "移动" in text:
            vehicle.set_autopilot(False)
            vehicle.apply_control(carla.VehicleControl(throttle=0.35, steer=0.0))
            result = "车辆开始前进"

        else:
            vehicle.set_autopilot(True)
            result = "默认执行：生成车辆并开启自动驾驶"

        latency_ms = int((time.time() - start) * 1000)

        emit(
            "agent_ack",
            {
                "id": cmd_id,
                "target": target,
                "latency_ms": latency_ms,
            },
        )

        socketio.emit(
            "event_log",
            {
                "severity": "ok",
                "source": "CARLA",
                "message": result,
            },
        )

        socketio.emit("state_update", get_state_payload())

    except Exception as e:
        emit(
            "agent_reject",
            {
                "id": cmd_id,
                "target": target,
                "reason": str(e),
            },
        )

        socketio.emit(
            "event_log",
            {
                "severity": "danger",
                "source": "CARLA",
                "message": f"指令执行失败: {e}",
            },
        )


@app.route("/api/health")
def health():
    try:
        _, world = connect_carla()
        return {
            "ok": True,
            "carla": True,
            "map": world.get_map().name,
        }
    except Exception as e:
        return {
            "ok": False,
            "carla": False,
            "error": str(e),
        }, 500


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

    cv2.putText(
        img,
        title,
        (40, 90),
        cv2.FONT_HERSHEY_SIMPLEX,
        1.0,
        (255, 255, 255),
        2,
        cv2.LINE_AA,
    )

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
            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
            )
        time.sleep(0.1)
def mjpeg_ground_camera():
    attach_ground_camera()

    while True:
        with frame_lock:
            frame = latest_ground_frame

        if frame is None:
            frame = make_placeholder_frame("ground")

        yield (
            b"--frame\r\n"
            b"Content-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
        )

        time.sleep(0.05)
def mjpeg_city_camera():
    try:
        attach_city_camera()
    except Exception as e:
        print(f"[CARLA] city camera 挂载失败: {e}", flush=True)
        try:
            log_event("danger", "CARLA", f"city camera 挂载失败: {e}")
        except Exception:
            pass

    while True:
        with frame_lock:
            frame = latest_city_frame

        if frame is None:
            frame = make_placeholder_frame("city")

        yield (
            b"--frame\r\n"
            b"Content-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
        )

        time.sleep(0.1)
@app.route("/video_feed")
def video_feed():
    camera = request.args.get("camera", "ground")

    if camera == "ground":
        return Response(
            mjpeg_ground_camera(),
            mimetype="multipart/x-mixed-replace; boundary=frame",
        )

    if camera == "city":
        return Response(
            mjpeg_city_camera(),
            mimetype="multipart/x-mixed-replace; boundary=frame",
        )

    return Response(
        mjpeg_placeholder(camera),
        mimetype="multipart/x-mixed-replace; boundary=frame",
    )

def main():
    socketio.start_background_task(background_loop)
    socketio.run(
        app,
        host="0.0.0.0",
        port=5000,
        allow_unsafe_werkzeug=True,
    )



if __name__ == "__main__":
    main()
