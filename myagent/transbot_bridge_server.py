from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any

import socketio
from aiohttp import web

from myagent.transbot_agent_skills import TransbotAgentSkills


PROTOCOL_VERSION = "1.0"
NAMESPACE = "/agent"


class TransbotBridgeServer:
    """
    CarlaBridge v1.0 最小兼容服务端。

    UrbanAgent 会以为自己连的是 CarlaBridge。
    实际上这里会把 UGV_* 命令转给 TransbotAgentSkills。
    """

    def __init__(self) -> None:
        self.sio = socketio.AsyncServer(
            async_mode="aiohttp",
            cors_allowed_origins="*",
            logger=False,
            engineio_logger=False,
        )
        self.app = web.Application()
        self.sio.attach(self.app)

        self.bridge_session_id = f"transbot-{uuid.uuid4().hex[:8]}"
        self.run_id = 1
        self.frame = 0
        self.sim_time = 0.0

        self.skills = TransbotAgentSkills()

        self.in_flight_commands: list[dict[str, Any]] = []

        self.incidents: dict[str, dict[str, Any]] = {
            "incident-fire-001": {
                "id": "incident-fire-001",
                "kind": "fire",
                "position": {"x": 20.0, "y": 20.0, "z": 0.0},
                "severity": "high",
                "since_sim_time": 0.0,
            }
        }

        self._register_handlers()

    def _wrap(self, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "version": PROTOCOL_VERSION,
            "msg_id": str(uuid.uuid4()),
            "type": event_type,
            "timestamp": time.time(),
            "frame": self.frame,
            "sim_time": self.sim_time,
            "sender": "bridge",
            "payload": payload,
        }

    def _unwrap_payload(self, data: Any) -> dict[str, Any]:
        if isinstance(data, dict):
            inner = data.get("payload")
            if isinstance(inner, dict):
                return inner
            return data
        return {}

    def _state_payload(self) -> dict[str, Any]:
        return {
            "sim_time": round(self.sim_time, 3),
            "run_id": self.run_id,
            "bridge_session_id": self.bridge_session_id,
            "traffic_lights": [],
            "vehicles": [
                {
                    "id": "UGV-01",
                    "role": "dispatchable",
                    "pose": self.skills.get_pose_array(),
                    "yaw": 0.0,
                    "speed": 0.0,
                    "heading": 0.0,
                    "battery": 100.0,
                }
            ],
            "uavs": [],
            "incidents": list(self.incidents.values()),
            "in_flight_commands": list(self.in_flight_commands),
        }

    def _register_handlers(self) -> None:
        @self.sio.event(namespace=NAMESPACE)
        async def connect(sid, environ, auth=None):
            print(f"[Bridge] UrbanAgent connected: {sid}")

            await self.sio.emit(
                "state_snapshot",
                self._wrap("state_snapshot", self._state_payload()),
                to=sid,
                namespace=NAMESPACE,
            )

        @self.sio.event(namespace=NAMESPACE)
        async def disconnect(sid):
            print(f"[Bridge] UrbanAgent disconnected: {sid}")

        @self.sio.on("hello", namespace=NAMESPACE)
        async def hello(sid, data):
            print(f"[Bridge] hello: {data}")

            return {
                "server": "transbot-bridge",
                "version": PROTOCOL_VERSION,
                "bridge_session_id": self.bridge_session_id,
                "scenario": "physical-grid-fire-demo",
            }

        @self.sio.on("agent.command", namespace=NAMESPACE)
        async def agent_command(sid, envelope):
            payload = self._unwrap_payload(envelope)

            cmd_id = str(payload.get("id", ""))
            kind = str(payload.get("kind", ""))
            target = str(payload.get("target", ""))
            params = payload.get("params", {}) or {}

            print(
                f"[Bridge] agent.command "
                f"id={cmd_id}, kind={kind}, target={target}, params={params}"
            )

            if not cmd_id:
                return {
                    "status": "rejected",
                    "cmd_id": "",
                    "reason": "parse_error",
                    "detail": {"message": "missing id"},
                }

            if target not in {"UGV-01", "F-01"}:
                return {
                    "status": "rejected",
                    "cmd_id": cmd_id,
                    "reason": "unknown_target",
                    "detail": {"target": target},
                }

            if kind not in {"UGV_GOTO", "UGV_EXTINGUISH", "UGV_RTL", "UGV_STOP"}:
                return {
                    "status": "rejected",
                    "cmd_id": cmd_id,
                    "reason": "unsupported_command",
                    "detail": {"kind": kind},
                }

            self.in_flight_commands.append(
                {
                    "cmd_id": cmd_id,
                    "kind": kind,
                    "target": "UGV-01",
                    "accepted_at_sim_time": round(self.sim_time, 3),
                    "progress": None,
                    "awaiting": "physical_transbot",
                }
            )

            asyncio.create_task(self._execute_command(cmd_id, kind, params))

            return {
                "status": "accepted",
                "cmd_id": cmd_id,
                "queued_at_sim_time": round(self.sim_time, 3),
            }

        # 兼容飞书文档里的应用层心跳。
        # 当前 UrbanAgent 不一定会发，所以这里只支持，不强制。
        @self.sio.on("ping", namespace=NAMESPACE)
        async def ping(sid, data):
            await self.sio.emit(
                "pong",
                self._wrap("pong", {"ok": True}),
                to=sid,
                namespace=NAMESPACE,
            )

        @self.sio.on("event_log", namespace=NAMESPACE)
        async def event_log(sid, envelope):
            payload = self._unwrap_payload(envelope)
            print(f"[AgentLog] {payload}")

    async def _emit_command_status(
        self,
        cmd_id: str,
        kind: str,
        status: str,
        *,
        reason: str | None = None,
        detail: Any | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "cmd_id": cmd_id,
            "status": status,
            "kind": kind,
            "target": "UGV-01",
            "reason": reason,
            "detail": detail,
            "at_sim_time": round(self.sim_time, 3),
        }

        await self.sio.emit(
            "command_status",
            self._wrap("command_status", payload),
            namespace=NAMESPACE,
        )

    async def _execute_command(
        self,
        cmd_id: str,
        kind: str,
        params: dict[str, Any],
    ) -> None:
        try:
            # await self._emit_command_status(cmd_id, kind, "ongoing")

            if kind == "UGV_GOTO":
                dest = params.get("dest") or {}

                await self.skills.goto_xy(
                    float(dest.get("x", 0.0)),
                    float(dest.get("y", 0.0)),
                    float(dest.get("z", 0.0)),
                )

            elif kind == "UGV_EXTINGUISH":
                incident_id = str(params.get("incident_id", ""))

                await self.skills.extinguish()

                if incident_id in self.incidents:
                    self.incidents.pop(incident_id)

            elif kind == "UGV_RTL":
                await self.skills.return_home()

            elif kind == "UGV_STOP":
                await self.skills.stop()

            await self._emit_command_status(cmd_id, kind, "completed")

        except Exception as exc:
            await self._emit_command_status(
                cmd_id,
                kind,
                "failed",
                reason="skill_error",
                detail={"message": str(exc)},
            )

        finally:
            self.in_flight_commands = [
                c for c in self.in_flight_commands
                if c.get("cmd_id") != cmd_id
            ]

    async def publish_state_loop(self) -> None:
        while True:
            self.frame += 1
            self.sim_time += 0.1

            await self.sio.emit(
                "state_snapshot",
                self._wrap("state_snapshot", self._state_payload()),
                namespace=NAMESPACE,
            )

            await asyncio.sleep(0.1)

    async def start(self, host: str = "0.0.0.0", port: int = 5000) -> None:
        asyncio.create_task(self.publish_state_loop())

        runner = web.AppRunner(self.app)
        await runner.setup()

        site = web.TCPSite(runner, host, port)
        await site.start()

        print(f"[Bridge] listening on http://{host}:{port}{NAMESPACE}")
        print("[Bridge] waiting for UrbanAgent...")

        while True:
            await asyncio.sleep(3600)


async def main() -> None:
    server = TransbotBridgeServer()
    await server.start()


if __name__ == "__main__":
    asyncio.run(main())