"""Provider envelopes derived from Colly's canonical tool descriptor."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Optional, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
DESCRIPTOR_PATH = REPO_ROOT / ".agents" / "tools" / "colly-collect.tool.json"


def load_tool_descriptor() -> Dict[str, Any]:
    return json.loads(DESCRIPTOR_PATH.read_text(encoding="utf-8"))


def _load_input_schema(descriptor: Dict[str, Any]) -> Dict[str, Any]:
    schema_path = (DESCRIPTOR_PATH.parent / descriptor["inputSchema"]).resolve()
    schema_path.relative_to(REPO_ROOT)
    return json.loads(schema_path.read_text(encoding="utf-8"))


def build_openai_responses_tool(
    allowed_callers: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    descriptor = load_tool_descriptor()
    tool = {
        "type": "function",
        "name": descriptor["name"],
        "description": descriptor["description"],
        "parameters": deepcopy(_load_input_schema(descriptor)),
        "strict": True,
    }
    if allowed_callers is not None:
        invalid = sorted(set(allowed_callers) - {"direct", "programmatic"})
        if invalid:
            raise ValueError(f"unsupported Responses caller: {invalid[0]}")
        tool["allowed_callers"] = list(allowed_callers)
    return tool


def build_codex_ptc_tools() -> list[Dict[str, Any]]:
    """Return the Responses tools needed for programmatic tool calling."""
    return [
        {"type": "programmatic_tool_calling"},
        build_openai_responses_tool(allowed_callers=["programmatic"]),
    ]


def build_hermes_tool() -> Dict[str, Any]:
    responses_tool = build_openai_responses_tool()
    return {
        "type": "function",
        "function": {
            "name": responses_tool["name"],
            "description": responses_tool["description"],
            "parameters": responses_tool["parameters"],
        },
    }
