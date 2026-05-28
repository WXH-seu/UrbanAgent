"""Declarative HTTP tools (REST) for the external tool layer T."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import httpx

from urbanagent.tooling.builtin_names import BUILTIN_ENV_TOOL_NAMES


_PLACEHOLDER = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


def _substitute(template: str, args: dict[str, Any]) -> str:
    def repl(m: re.Match[str]) -> str:
        key = m.group(1)
        if key not in args:
            raise ValueError(f"missing argument {key!r} for HTTP tool template")
        return str(args[key])

    out, n = _PLACEHOLDER.subn(repl, template)
    if _PLACEHOLDER.search(out):
        raise ValueError(f"unresolved placeholders in template: {template!r}")
    return out


def _substitute_json(obj: Any, args: dict[str, Any]) -> Any:
    if isinstance(obj, str):
        return _substitute(obj, args)
    if isinstance(obj, dict):
        return {k: _substitute_json(v, args) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_substitute_json(v, args) for v in obj]
    return obj


class HttpApiToolBackend:
    """Loads tool specs from JSON and invokes them via httpx."""

    def __init__(self, tools: list[dict[str, Any]]) -> None:
        seen: set[str] = set()
        self._specs: dict[str, dict[str, Any]] = {}
        for raw in tools:
            if not isinstance(raw, dict):
                continue
            name = str(raw.get("name", "")).strip()
            if not name:
                raise ValueError("HTTP tool entry missing non-empty name")
            if name in seen:
                raise ValueError(f"duplicate HTTP tool name: {name}")
            if name in BUILTIN_ENV_TOOL_NAMES:
                raise ValueError(
                    f"HTTP tool name {name!r} conflicts with a built-in environment "
                    "operation; choose another name."
                )
            seen.add(name)
            method = str(raw.get("method", "GET")).upper()
            url_t = raw.get("url") or raw.get("url_template")
            if not url_t:
                raise ValueError(f"HTTP tool {name!r} requires url or url_template")
            self._specs[name] = {
                "name": name,
                "description": str(raw.get("description", "")).strip(),
                "method": method,
                "url_template": str(url_t),
                "headers": dict(raw.get("headers") or {}),
                "body_template": raw.get("body"),
                "args_schema": raw.get("args_schema") or {},
            }

    @classmethod
    def from_json_path(cls, path: Path) -> HttpApiToolBackend | None:
        data = json.loads(path.read_text(encoding="utf-8"))
        tools = data.get("tools", [])
        if not tools:
            return None
        return cls(tools)

    async def __aenter__(self) -> HttpApiToolBackend:
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(60.0))
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._client.aclose()

    def planner_metadata(self) -> list[dict[str, Any]]:
        return [
            {
                "name": s["name"],
                "description": s["description"],
                "args_schema": s["args_schema"],
                "returns": "HTTP response (JSON or text)",
                "source": "http_api",
            }
            for s in self._specs.values()
        ]

    def owns(self, name: str) -> bool:
        return name in self._specs

    async def invoke(self, name: str, arguments: dict[str, Any]) -> Any:
        spec = self._specs[name]
        args = dict(arguments or {})
        url = _substitute(spec["url_template"], args)
        headers = {k: _substitute(str(v), args) for k, v in spec["headers"].items()}
        method = spec["method"]
        body_t = spec["body_template"]

        req_kw: dict[str, Any] = {"headers": headers}
        if method in {"POST", "PUT", "PATCH"} and body_t is not None:
            payload = _substitute_json(body_t, args)
            req_kw["json"] = payload
        response = await self._client.request(method, url, **req_kw)
        try:
            return response.json()
        except Exception:
            return {"status_code": response.status_code, "text": response.text}
