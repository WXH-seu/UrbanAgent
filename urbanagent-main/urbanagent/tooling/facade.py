"""Compose HTTP and MCP backends into one external tool facade (paper: T)."""
from __future__ import annotations

import json
from collections.abc import Sequence
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

from mcp.client.stdio import StdioServerParameters

from urbanagent.tooling.http_api import HttpApiToolBackend
from urbanagent.tooling.mcp_stdio import McpStdioToolBackend


class ExternalToolFacade:
    """Async context manager that connects external tool backends for one run."""

    def __init__(self, backends: Sequence[Any]) -> None:
        self._backends = [b for b in backends if b is not None]
        self._stack: AsyncExitStack | None = None
        self._active: list[Any] = []

    @property
    def enabled(self) -> bool:
        return bool(self._backends)

    async def __aenter__(self) -> ExternalToolFacade:
        self._stack = AsyncExitStack()
        await self._stack.__aenter__()
        self._active = []
        for b in self._backends:
            entered = await self._stack.enter_async_context(b)
            self._active.append(entered)
        seen: set[str] = set()
        for b in self._active:
            for row in b.planner_metadata():
                n = str(row.get("name", "")).strip()
                if not n:
                    raise ValueError("external tool metadata entry missing name")
                if n in seen:
                    raise ValueError(f"duplicate external tool name across backends: {n!r}")
                seen.add(n)
        return self

    async def __aexit__(self, *exc: object) -> bool | None:
        self._active = []
        if self._stack is None:
            return None
        try:
            return await self._stack.__aexit__(*exc)
        finally:
            self._stack = None

    def planner_metadata(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for b in self._active:
            rows.extend(b.planner_metadata())
        return rows

    async def invoke(self, name: str, arguments: dict[str, Any]) -> Any:
        for b in self._active:
            if b.owns(name):
                return await b.invoke(name, arguments)
        raise KeyError(name)


def build_external_tool_facade(
    *,
    http_tools_path: str | Path | None = None,
    mcp_servers_path: str | Path | None = None,
) -> ExternalToolFacade | None:
    """Build a facade from JSON config paths (either may be omitted). Returns None if nothing to load."""
    backends: list[Any] = []

    if http_tools_path:
        p = Path(http_tools_path)
        if p.is_file():
            http_b = HttpApiToolBackend.from_json_path(p)
            if http_b is not None:
                backends.append(http_b)

    if mcp_servers_path:
        p = Path(mcp_servers_path)
        if p.is_file():
            cfg = json.loads(p.read_text(encoding="utf-8"))
            for entry in cfg.get("servers", []):
                if not isinstance(entry, dict):
                    continue
                alias = str(entry.get("alias", "server")).strip() or "server"
                command = str(entry.get("command", "")).strip()
                if not command:
                    raise ValueError("MCP server entry requires non-empty command")
                args = [str(a) for a in entry.get("args", [])]
                env = entry.get("env")
                if env is not None and not isinstance(env, dict):
                    raise ValueError(f"MCP server {alias!r}: env must be an object or omitted")
                cwd = entry.get("cwd")
                params = StdioServerParameters(
                    command=command,
                    args=args,
                    env={str(k): str(v) for k, v in env.items()} if env else None,
                    cwd=cwd,
                )
                backends.append(McpStdioToolBackend(alias=alias, params=params))

    facade = ExternalToolFacade(backends)
    return facade if facade.enabled else None
