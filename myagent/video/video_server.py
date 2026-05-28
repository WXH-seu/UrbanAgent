import cv2
import time
from flask import Flask, Response, jsonify


app = Flask(__name__)


CAMERA_INDEX = 0
FRAME_WIDTH = 640
FRAME_HEIGHT = 480
FPS_LIMIT = 20


def open_camera():
    cap = cv2.VideoCapture(CAMERA_INDEX)

    if not cap.isOpened():
        raise RuntimeError(f"无法打开摄像头 index={CAMERA_INDEX}")

    cap.set(cv2.CAP_PROP_FRAME_WIDTH, FRAME_WIDTH)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_HEIGHT)

    return cap


def generate_frames():
    cap = open_camera()
    last_time = 0

    try:
        while True:
            now = time.time()
            if now - last_time < 1.0 / FPS_LIMIT:
                time.sleep(0.005)
                continue

            last_time = now

            ok, frame = cap.read()
            if not ok:
                print("[video_server] 读取摄像头失败")
                time.sleep(0.1)
                continue

            ok, buffer = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
            if not ok:
                continue

            jpg_bytes = buffer.tobytes()

            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n\r\n" + jpg_bytes + b"\r\n"
            )

    finally:
        cap.release()


@app.route("/")
def index():
    return jsonify({
        "ok": True,
        "service": "transbot-video-server",
        "video_feed": "/video_feed"
    })


@app.route("/health")
def health():
    return jsonify({
        "ok": True,
        "camera_index": CAMERA_INDEX,
        "width": FRAME_WIDTH,
        "height": FRAME_HEIGHT,
        "fps_limit": FPS_LIMIT
    })


@app.route("/video_feed")
def video_feed():
    return Response(
        generate_frames(),
        mimetype="multipart/x-mixed-replace; boundary=frame"
    )


if __name__ == "__main__":
    print("[video_server] starting on 0.0.0.0:8000")
    app.run(host="0.0.0.0", port=8000, threaded=True)
