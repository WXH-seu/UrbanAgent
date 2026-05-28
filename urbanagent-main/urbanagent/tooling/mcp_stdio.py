"""MCP stdio client: one subprocess server → prefixed planner tool names."""
from __future__ import annotations

import re
from contextlib import AsyncExitStack
from typing import Any

import mcp.types as mcp_types
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from urbanagent.tooling.builtin_names import BUILTIN_ENV_TOOL_NAMES


def _safe_token(part: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_]", "_", part)


def planner_tool_name(alias: str, mcp_tool_name: str) -> str:
    return f"mcp_{_safe_token(alias)}_{_safe_token(mcp_tool_name)}"


def _serialize_call_tool_result(result: mcp_types.CallToolResult) -> dict[str, Any]:
    if result.isError:
        parts: list[str] = []
        for block in result.content:
            if isinstance(block, mcp_types.TextContent):
                parts.append(block.text)
        raise RuntimeError(
            "MCP tool returned error: " + ("\n".join(parts) if parts else "no message")
        )
    texts: list[str] = []
    for block in result.content:
        if isinstance(block, mcp_types.TextContent):
            texts.append(block.text)
    out: dict[str, Any] = {"text": "\n".join(texts).strip()}
    if result.structuredContent is not None:
        out["structured"] = result.structuredContent
    return out


class McpStdioToolBackend:
    """Connects to one MCP server over stdio; exposes tools under `mcp_<alias>_*` names."""

    def __init__(self, alias: str, params: StdioServerParameters) -> None:
        self.alias = alias
        self.params = params
        self._stack: AsyncExitStack | None = None
        self._session: ClientSession | None = None
        self._mcp_by_planner: dict[str, str] = {}

    async def __aenter__(self) -> McpStdioToolBackend:
        self._stack = AsyncExitStack()
        await self._stack.__aenter__()
        read_write = await self._stack.enter_async_context(stdio_client(self.params))
        read, write = read_write
        session = await self._stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        self._session = session

        tools: list[mcp_types.Tool] = []
        cursor: str | None = None
        while True:
            page = await session.list_tools() if cursor is None else await session.list_tools(cursor=cursor)
            tools.extend(page.tools)
            nxt = page.nextCursor
            if not nxt:
                break
            cursor = str(nxt)

        self._metadata: list[dict[str, Any]] = []
        self._mcp_by_planner = {}
        for t in tools:
            pname = planner_tool_name(self.alias, t.name)
            if pname in BUILTIN_ENV_TOOL_NAMES:
                raise ValueError(
                    f"MCP tool planner name {pname!r} conflicts with a built-in "
                    "environment operation; change server alias or tool name."
                )
            if pname in self._mcp_by_planner:
                raise ValueError(f"duplicate MCP planner tool name after sanitizing: {pname}")
            self._mcp_by_planner[pname] = t.name
            schema = t.inputSchema if isinstance(t.inputSchema, dict) else {}
            self._metadata.append(
                {
                    "name": pname,
                    "description": (t.description or "").strip(),
                    "args_schema": schema,
                    "returns": "MCP tool result (text and optional structured JSON)",
                    "source": "mcp",
                    "mcp_server": self.alias,
                    "mcp_tool": t.name,
                }
            )
        return self

    async def __aexit__(self, *exc: object) -> bool | None:
        self._session = None
        if self._stack is None:
            return None
        try:
            return await self._stack.__aexit__(*exc)
        finally:
            self._stack = None

    def planner_metadata(self) -> list[dict[str, Any]]:
        return list(self._metadata)

    def owns(self, name: str) -> bool:
        return name in self._mcp_by_planner

    async def invoke(self, name: str, arguments: dict[str, Any]) -> Any:
        if not self._session:
            raise RuntimeError("MCP session not initialized")
        mcp_name = self._mcp_by_planner[name]
        result = await self._session.call_tool(mcp_name, arguments or None)
        return _serialize_call_tool_result(result)
