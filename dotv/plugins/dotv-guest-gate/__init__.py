"""dotv tier gate (#527): second line of defence for every non-owner profile (Admin/User/Guest).

Each tier profile already exposes only its toolsets via ``platform_toolsets``; this hook refuses any
other tool call (mis-configuration, plugin/MCP tools, background forks). The per-tier allowlist comes
from ``$HERMES_HOME/dotv/tier.json`` (written by the owner's permission board) and is always capped by
``CEILING``: tools that can read the owner's machine or act as the owner are never allowed. Without a
tier file the Guest default applies. Install only into non-owner profiles; plugin managers are per
HERMES_HOME. Any internal error blocks the call (fail-closed).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

DEFAULT_ALLOWED_TOOLS = frozenset({
    "web_search", "web_extract", "clarify", "vision_analyze", "image_generate", "text_to_speech",
    "dotv_public_lookup",
})
CEILING = DEFAULT_ALLOWED_TOOLS | frozenset({
    "x_search", "video_analyze", "video_generate", "xai_video_edit", "xai_video_extend", "todo_list",
    "memory", "session_search", "skills_list", "skill_view", "skill_manage",
})
ENV_ALLOWED = "DOTV_GUEST_ALLOWED_TOOLS"
TIER_FILE = "dotv/tier.json"


def _home() -> Path:
    from hermes_constants import get_hermes_home
    return Path(get_hermes_home())


def tier_tools(home: Optional[Path] = None) -> frozenset[str]:
    """Allowed tools from the profile's tier file (capped by CEILING), or the Guest default without one."""
    path = (home or _home()) / TIER_FILE
    if not path.exists():
        return DEFAULT_ALLOWED_TOOLS
    data = json.loads(path.read_text(encoding="utf-8"))
    return frozenset(t for t in data.get("allowed_tools") or [] if isinstance(t, str)) & CEILING


def allowed_tools(home: Optional[Path] = None) -> frozenset[str]:
    """Tier allowlist, optionally narrowed (never widened) by ``DOTV_GUEST_ALLOWED_TOOLS``."""
    base = tier_tools(home)
    raw = os.environ.get(ENV_ALLOWED)
    if raw is None:
        return base
    return frozenset({t.strip() for t in raw.split(",") if t.strip()} & base)


def _block(message: str) -> dict[str, str]:
    return {"action": "block", "message": message}


def _is_remote_url(value: Any) -> bool:
    return isinstance(value, str) and value.strip().lower().startswith(("http://", "https://"))


def check(tool_name: str, args: Any) -> Optional[dict[str, str]]:
    if tool_name not in allowed_tools():
        return _block(f"Tool '{tool_name}' is not available at your permission tier.")
    if tool_name == "vision_analyze" and not _is_remote_url((args or {}).get("image_url")):
        return _block("In a guest session vision_analyze only accepts http(s) image URLs.")
    return None


def on_pre_tool_call(tool_name: str = "", args: Any = None, **_: Any) -> Optional[dict[str, str]]:
    try:
        return check(str(tool_name or ""), args if isinstance(args, dict) else {})
    except Exception:
        return _block("Tier gate error; tool call refused.")


def register(ctx) -> None:
    ctx.register_hook("pre_tool_call", on_pre_tool_call)
