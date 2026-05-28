"""Strict JSON object parsing for multi-agent LLM stages."""
from __future__ import annotations

import json
import re
from typing import Any


def try_loads_json_object(text: str) -> dict[str, Any]:
    """Parse first JSON object in text; raises ValueError on failure."""
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```\s*$", "", cleaned)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end < start:
        raise ValueError("no JSON object found")
    core = cleaned[start : end + 1]
    loosened = core
    for _ in range(16):
        try:
            data = json.loads(loosened)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass
        nxt = re.sub(r",(\s*[}\]])", r"\1", loosened)
        if nxt == loosened:
            break
        loosened = nxt
    raise ValueError("invalid JSON object")
