"""Errors raised by UrbanAgent integrations."""


class SandboxWireError(RuntimeError):
    """3D sandbox WebSocket wire protocol or payload decoding failed."""

    def __init__(self, message: str) -> None:
        super().__init__(f"[SandboxWire] {message}")
