"""Public profile: refuse every tool call outside the guest allowlist.

The public profile already exposes only safe toolsets; this hook is the second line of defence against
misconfiguration, plugin/MCP tools and background forks. The allowlist comes from ``tier.json`` (written
by ``hermes salts-secrets sync``) and is always capped by ``CEILING``: nothing that can read the owner's
machine or act as the owner is ever allowed. Any internal error blocks the call.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from . import home

DEFAULT_ALLOWED_TOOLS = frozenset({
    "web_search", "web_extract", "clarify", "vision_analyze", "image_generate", "text_to_speech",
    "public_notes_lookup",
})
CEILING = DEFAULT_ALLOWED_TOOLS | frozenset({
    "x_search", "video_analyze", "video_generate", "xai_video_edit", "xai_video_extend", "todo_list",
    "memory", "session_search", "skills_list", "skill_view", "skill_manage",
})
TIER_FILE = "tier.json"


def allowed_tools(hermes_home: Optional[Path] = None) -> frozenset[str]:
    path = home.data_dir(hermes_home) / TIER_FILE
    if not path.exists():
        return DEFAULT_ALLOWED_TOOLS
    data = json.loads(path.read_text(encoding="utf-8"))
    return frozenset(t for t in data.get("allowed_tools") or [] if isinstance(t, str)) & CEILING


def _block(message: str) -> dict[str, str]:
    return {"action": "block", "message": message}


def _is_remote_url(value: Any) -> bool:
    return isinstance(value, str) and value.strip().lower().startswith(("http://", "https://"))


def check(tool_name: str, args: dict, hermes_home: Optional[Path] = None) -> Optional[dict[str, str]]:
    if tool_name not in allowed_tools(hermes_home):
        return _block(f"Tool '{tool_name}' is not available in a guest session.")
    if tool_name == "vision_analyze" and not _is_remote_url(args.get("image_url")):
        return _block("In a guest session vision_analyze only accepts http(s) image URLs.")
    return None


def on_pre_tool_call(tool_name: str = "", args: Any = None, **_: Any) -> Optional[dict[str, str]]:
    try:
        return check(str(tool_name or ""), args if isinstance(args, dict) else {})
    except Exception:  # noqa: BLE001
        return _block("Guest tool gate error; tool call refused.")


def register(ctx) -> None:
    ctx.register_hook("pre_tool_call", on_pre_tool_call)
