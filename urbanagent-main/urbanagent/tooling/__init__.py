"""External tool layer: MCP (stdio) and HTTP APIs (paper toolset T)."""

from __future__ import annotations

from urbanagent.tooling.builtin_names import BUILTIN_ENV_TOOL_NAMES
from urbanagent.tooling.facade import ExternalToolFacade, build_external_tool_facade
from urbanagent.tooling.http_api import HttpApiToolBackend
from urbanagent.tooling.mcp_stdio import McpStdioToolBackend, planner_tool_name

__all__ = [
    "BUILTIN_ENV_TOOL_NAMES",
    "ExternalToolFacade",
    "HttpApiToolBackend",
    "McpStdioToolBackend",
    "build_external_tool_facade",
    "planner_tool_name",
]
